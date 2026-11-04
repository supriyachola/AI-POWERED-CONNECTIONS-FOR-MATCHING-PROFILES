from datetime import timedelta


from app import calls as svc
from app.database import SessionLocal
from app.models import Connection, utcnow
from .conftest import accepted_call


def status_of(cid):
    db = SessionLocal()
    try:
        return db.get(Connection, cid).status
    finally:
        db.close()


def age_call(cid, seconds):
    db = SessionLocal()
    try:
        db.get(Connection, cid).updated_at = utcnow() - timedelta(seconds=seconds)
        db.commit()
    finally:
        db.close()


def sweep():
    db = SessionLocal()
    try:
        ch = svc.expire_stale(db)
        db.commit()
        return ch
    finally:
        db.close()


def test_invite_rings_and_does_not_start_media(mk):
    a, b = mk("alice"), mk("bob")
    r = a.post("/connections", json={"receiver_id": b.id})
    assert r.status_code == 200 and r.json()["status"] == "ringing"
    cid = r.json()["connection_id"]
    # neither side may get a room ticket before acceptance
    assert a.post(f"/connections/{cid}/ws-ticket").status_code == 409
    assert b.post(f"/connections/{cid}/ws-ticket").status_code == 409
    inc = b.get("/calls/incoming").json()["incoming"]
    assert inc["connection_id"] == cid and inc["person"]["username"] == "alice"
    assert b.post("/presence/heartbeat").json()["incoming_call"]["connection_id"] == cid


def test_only_receiver_can_accept_or_reject(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    cid = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    assert a.post(f"/connections/{cid}/accept").status_code == 403
    assert c.post(f"/connections/{cid}/accept").status_code == 404   # outsiders can't even see it
    assert c.post(f"/connections/{cid}/ws-ticket").status_code == 404
    assert b.post(f"/connections/{cid}/accept").status_code == 200
    assert b.post(f"/connections/{cid}/ws-ticket").status_code == 200
    assert a.post(f"/connections/{cid}/accept").status_code == 403


def test_reject_and_cancel(mk):
    a, b = mk("alice"), mk("bob")
    cid = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    assert b.post(f"/connections/{cid}/reject").json()["status"] == "rejected"
    assert status_of(cid) == "rejected"
    assert b.post(f"/connections/{cid}/accept").status_code == 409   # can't revive a finished call
    cid2 = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    assert b.post(f"/connections/{cid2}/cancel").status_code == 403
    assert a.post(f"/connections/{cid2}/cancel").json()["status"] == "cancelled"


def test_ring_timeout_expires_and_nobody_is_excluded_forever(mk):
    a, b = mk("alice", age=28), mk("bob", age=28)
    cid = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    age_call(cid, 31)
    changes = sweep()
    assert [c.new for c in changes] == ["expired"] and status_of(cid) == "expired"
    assert b.post(f"/connections/{cid}/accept").status_code == 409
    # expired must NOT hide Bob from Alice's discovery
    assert a.get("/discover/next").json()["found"] is True


def test_no_second_call_while_busy(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    accepted_call(a, b)
    assert a.post("/connections", json={"receiver_id": c.id}).status_code == 409
    assert c.post("/connections", json={"receiver_id": b.id}).status_code == 409
    assert a.get("/discover/next").status_code == 409


def test_duplicate_invite_is_idempotent(mk):
    a, b = mk("alice"), mk("bob")
    c1 = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    c2 = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    assert c1 == c2
    # reverse invite while ringing is refused rather than creating a second call
    assert b.post("/connections", json={"receiver_id": a.id}).status_code == 409


def test_invite_requires_online_receiver(mk):
    a, b = mk("alice"), mk("bob")
    from app.presence import presence
    presence.mark_offline(b.id)
    assert a.post("/connections", json={"receiver_id": b.id}).status_code == 409


def test_end_is_idempotent_and_frees_both_users(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    cid = accepted_call(a, b)
    assert b.post(f"/connections/{cid}/end").json()["status"] == "ended"
    assert a.post(f"/connections/{cid}/end").json()["status"] == "ended"
    assert a.post("/connections", json={"receiver_id": c.id}).status_code == 200


def test_accepted_but_nobody_joins_expires(mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    age_call(cid, 31)
    sweep()
    assert status_of(cid) == "expired"


def test_calls_cannot_become_active_without_acceptance():
    from fastapi import HTTPException
    import pytest
    conn = Connection(requester_id=1, receiver_id=2, status="ringing")
    with pytest.raises(HTTPException):
        svc.transition(conn, "active")
    with pytest.raises(HTTPException):
        svc.transition(conn, "connecting")


def test_status_endpoint_is_participant_only(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    cid = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    assert a.get(f"/connections/{cid}/status").json()["role"] == "caller"
    assert b.get(f"/connections/{cid}/status").json()["role"] == "callee"
    assert c.get(f"/connections/{cid}/status").status_code == 404
