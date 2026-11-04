import json

import pytest
from starlette.websockets import WebSocketDisconnect

from .conftest import accepted_call
from .test_calls import status_of

SDP = {"type": "offer", "sdp": "v=0\r\n"}


def room(user, cid, client):
    t = user.post(f"/connections/{cid}/ws-ticket").json()["ticket"]
    return client.websocket_connect(f"/ws/connection/{cid}?ticket={t}")


def expect_rejected(client, url):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url) as ws:
            ws.receive_json()


def test_full_signalling_flow_reaches_active_only_after_both_confirm(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    with room(a, cid, client) as wa:
        assert wa.receive_json() == {"type": "room_state", "peers": [], "peer_id": b.id}
        with room(b, cid, client) as wb:
            assert wb.receive_json()["peers"] == [a.id]
            assert wa.receive_json() == {"type": "peer_joined", "user_id": b.id}
            assert status_of(cid) == "connecting"
            wa.send_json({"type": "offer", "data": SDP})
            m = wb.receive_json()
            assert m["type"] == "offer" and m["from"] == a.id and m["data"] == SDP
            wb.send_json({"type": "answer", "data": {"type": "answer", "sdp": "v=0\r\n"}})
            assert wa.receive_json()["type"] == "answer"
            wa.send_json({"type": "ice-candidate", "data": {"candidate": "candidate:1 1 udp 1 1.2.3.4 5 typ host", "sdpMid": "0", "sdpMLineIndex": 0}})
            assert wb.receive_json()["type"] == "ice-candidate"
            # one confirmation is not enough
            wa.send_json({"type": "media_connected"})
            wa.send_json({"type": "ping"})
            assert wa.receive_json() == {"type": "pong"}
            assert status_of(cid) == "connecting"
            wb.send_json({"type": "media_connected"})
            wb.send_json({"type": "ping"})
            assert wb.receive_json() == {"type": "pong"}
            assert status_of(cid) == "active"
            wa.send_json({"type": "chat", "text": "  hello  "})
            assert wb.receive_json() == {"type": "chat", "from": a.id, "text": "hello"}
            assert wa.receive_json()["text"] == "hello"
            wa.send_json({"type": "hangup"})
            assert wb.receive_json()["type"] == "hangup"
    assert status_of(cid) == "ended"


def test_outsider_cannot_join_even_with_valid_ticket_for_themselves(client, mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    cid = accepted_call(a, b)
    from app.auth import create_ws_ticket
    t = create_ws_ticket(c.id, "room", cid)       # a perfectly valid ticket, but carol isn't a participant
    expect_rejected(client, f"/ws/connection/{cid}?ticket={t}")


def test_ticket_bound_to_room_single_use_and_not_an_api_token(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    cid_other = accepted_call(mk("xavier"), mk("xena"))
    t = a.post(f"/connections/{cid}/ws-ticket").json()["ticket"]
    expect_rejected(client, f"/ws/connection/{cid_other}?ticket={t}")      # wrong room
    with client.websocket_connect(f"/ws/connection/{cid}?ticket={t}") as ws:
        ws.receive_json()
    expect_rejected(client, f"/ws/connection/{cid}?ticket={t}")            # replay
    assert client.get("/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401
    expect_rejected(client, f"/ws/connection/{cid}?ticket={a.token}")      # access token is not a ticket
    expect_rejected(client, f"/ws/connection/{cid}")                        # no ticket


def test_cannot_join_ringing_call_or_after_it_ended(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
    from app.auth import create_ws_ticket
    expect_rejected(client, f"/ws/connection/{cid}?ticket={create_ws_ticket(a.id, 'room', cid)}")
    b.post(f"/connections/{cid}/accept")
    b.post(f"/connections/{cid}/end")
    expect_rejected(client, f"/ws/connection/{cid}?ticket={create_ws_ticket(a.id, 'room', cid)}")


@pytest.mark.parametrize("bad", [
    {"type": "offer"},
    {"type": "offer", "data": {"type": "answer", "sdp": "x"}},
    {"type": "offer", "data": {"type": "offer", "sdp": "x" * 50000}},
    {"type": "ice-candidate", "data": {"candidate": 5}},
    {"type": "ice-candidate", "data": {"candidate": "c", "evil": 1}},
    {"type": "chat", "text": "   "},
    {"type": "chat", "text": "x" * 1001},
    {"type": "ping", "text": "nope"},
    {"type": "join_other_room", "room": 9},
    {"type": "hangup", "extra": 1},
])
def test_invalid_messages_rejected_and_not_relayed(client, mk, bad):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    with room(a, cid, client) as wa, room(b, cid, client) as wb:
        wa.receive_json(); wb.receive_json(); wa.receive_json()
        wa.send_json(bad)
        assert wa.receive_json()["type"] == "error"
        wa.send_json({"type": "ping"})
        assert wa.receive_json()["type"] == "pong"
        wb.send_json({"type": "ping"})
        assert wb.receive_json()["type"] == "pong"   # peer received nothing in between
    assert status_of(cid) in {"disconnected", "connecting", "accepted", "ended"}


def test_garbage_and_oversize_frames(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    with room(a, cid, client) as wa:
        wa.receive_json()
        wa.send_text("not json")
        assert wa.receive_json()["type"] == "error"
        wa.send_text(json.dumps({"type": "chat", "text": "x" * 70000}))
        with pytest.raises(WebSocketDisconnect):
            wa.receive_json()


def test_chat_rate_limit(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    with room(a, cid, client) as wa:
        wa.receive_json()
        for _ in range(10):
            wa.send_json({"type": "chat", "text": "hi"})
            assert wa.receive_json()["type"] == "chat"
        wa.send_json({"type": "chat", "text": "hi"})
        assert wa.receive_json() == {"type": "error", "message": "slow down"}


def test_drop_marks_disconnected_then_rejoin_recovers(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    with room(a, cid, client) as wa:
        wa.receive_json()
        with room(b, cid, client) as wb:
            wb.receive_json(); wa.receive_json()
            wa.send_json({"type": "media_connected"}); wb.send_json({"type": "media_connected"})
            wa.send_json({"type": "ping"}); wa.receive_json()
            wb.send_json({"type": "ping"}); wb.receive_json()
            assert status_of(cid) == "active"
        # Bob's socket dropped
        assert wa.receive_json() == {"type": "peer_left", "user_id": b.id}
        assert status_of(cid) == "disconnected"
        with room(b, cid, client) as wb2:                       # reconnection (e.g. page refresh)
            assert wb2.receive_json()["peers"] == [a.id]
            assert wa.receive_json()["type"] == "peer_joined"
            assert status_of(cid) == "connecting"


def test_block_ends_call_and_closes_the_room_immediately(client, mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    with room(b, cid, client) as wb:
        wb.receive_json()
        assert a.post(f"/users/{b.id}/block").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            wb.receive_json()
    assert status_of(cid) == "ended"
    assert b.post(f"/connections/{cid}/ws-ticket").status_code == 409


def test_user_channel_pushes_incoming_call(client, mk):
    a, b = mk("alice"), mk("bob")
    t = b.post("/auth/ws-ticket").json()["ticket"]
    with client.websocket_connect(f"/ws/user?ticket={t}") as ws:
        cid = a.post("/connections", json={"receiver_id": b.id}).json()["connection_id"]
        m = ws.receive_json()
        assert m["type"] == "incoming_call" and m["connection_id"] == cid and m["from"]["username"] == "alice"
        a.post(f"/connections/{cid}/cancel")
        assert ws.receive_json()["type"] == "call_ended"


def test_origin_enforced_when_present(client, mk, monkeypatch):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    t = a.post(f"/connections/{cid}/ws-ticket").json()["ticket"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/connection/{cid}?ticket={t}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
