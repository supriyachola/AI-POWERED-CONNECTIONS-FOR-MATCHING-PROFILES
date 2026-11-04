from app.auth import create_token, create_ws_ticket, decode_token
from app.routers.rtc import build_ice_servers
import base64
import hashlib
import hmac


def test_login_lockout_after_repeated_failures(client, mk):
    mk("alice")
    for _ in range(5):
        assert client.post("/auth/login", json={"username": "alice", "password": "wrong-pass"}).status_code == 401
    assert client.post("/auth/login", json={"username": "alice", "password": "Passw0rd!x"}).status_code == 429


def test_successful_login_resets_failures(client, mk):
    mk("alice")
    for _ in range(3):
        client.post("/auth/login", json={"username": "alice", "password": "nope-nope"})
    assert client.post("/auth/login", json={"username": "alice", "password": "Passw0rd!x"}).status_code == 200
    for _ in range(4):
        client.post("/auth/login", json={"username": "alice", "password": "nope-nope"})
    assert client.post("/auth/login", json={"username": "alice", "password": "Passw0rd!x"}).status_code == 200


def test_unknown_user_and_wrong_password_same_response(client, mk):
    mk("alice")
    a = client.post("/auth/login", json={"username": "alice", "password": "bad-password"})
    b = client.post("/auth/login", json={"username": "ghost", "password": "bad-password"})
    assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_register_and_guest_rate_limited(client):
    codes = [client.post("/auth/guest").status_code for _ in range(22)]
    assert codes[:20] == [200] * 20 and 429 in codes[20:]


def test_duplicate_username_rejected(client, mk):
    mk("alice")
    r = client.post("/auth/register", json={"username": "alice", "password": "Passw0rd!x", "age": 30, "gender": "male"})
    assert r.status_code == 409


def test_ws_ticket_not_accepted_as_bearer_and_garbage_rejected(client, mk):
    u = mk("alice")
    assert client.get("/me", headers={"Authorization": f"Bearer {create_ws_ticket(u.id, 'user')}"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/me").status_code in (401, 403)
    assert decode_token(create_token(u.id)) == u.id


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").json()["status"] == "ok"
    assert client.get("/ready").json() == {"status": "ready"}


def test_every_authenticated_route_rejects_anonymous(client):
    open_paths = {"/", "/health", "/ready", "/auth/register", "/auth/login", "/auth/guest", "/openapi.json", "/docs",
                  "/docs/oauth2-redirect", "/redoc"}
    from app.main import app
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods or route.path in open_paths:
            continue
        path = route.path.replace("{connection_id}", "1").replace("{target_id}", "1").replace("{request_id}", "1").replace("{candidate_id}", "1")
        for m in methods - {"HEAD", "OPTIONS"}:
            r = client.request(m, path, json={})
            assert r.status_code in (401, 403), f"{m} {route.path} reachable without auth ({r.status_code})"


def test_turn_credentials_are_temporary_and_verifiable(monkeypatch):
    monkeypatch.setenv("TURN_URLS", "turn:turn.example.com:3478,turns:turn.example.com:5349")
    monkeypatch.setenv("TURN_SHARED_SECRET", "s" * 32)
    out = build_ice_servers(7, now=1_000_000)
    turn = out["iceServers"][1]
    assert out["turn_configured"] and turn["username"] == "1003600:7"
    expect = base64.b64encode(hmac.new(b"s" * 32, b"1003600:7", hashlib.sha1).digest()).decode()
    assert turn["credential"] == expect and "s" * 32 not in str(out)


def test_rtc_config_requires_auth_and_degrades_to_stun(client, mk, monkeypatch):
    monkeypatch.delenv("TURN_URLS", raising=False)
    monkeypatch.delenv("TURN_SHARED_SECRET", raising=False)
    assert client.get("/rtc/config").status_code in (401, 403)
    cfg = mk("alice").get("/rtc/config").json()
    assert cfg["turn_configured"] is False and len(cfg["iceServers"]) == 1
