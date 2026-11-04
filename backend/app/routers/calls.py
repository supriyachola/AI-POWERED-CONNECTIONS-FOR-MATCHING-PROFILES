import json
import logging
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from .. import calls as svc
from .. import config
from ..auth import consume_ws_ticket, create_ws_ticket
from ..deps import current_user_id, db_call, people_by_id, require_user
from ..models import Connection
from ..presence import presence
from ..ratelimit import limiter
from ..realtime import dispatch, hub
from ..schemas import ConnectionRequest

log = logging.getLogger("affinity.calls")
router = APIRouter()

MAX_WS_BYTES = 64 * 1024


def _call_payload(db, conn: Connection, uid: int) -> dict:
    other = svc.other_party(conn, uid)
    return {"connection_id": conn.id, "status": conn.status, "reason": conn.ended_reason,
            "role": "caller" if conn.requester_id == uid else "callee",
            "person": people_by_id(db, {other}).get(other)}


# ------------------------------ REST: call lifecycle ------------------------------
@router.post("/connections")
async def invite(data: ConnectionRequest, uid: int = Depends(current_user_id)):
    """Ring another person. The call stays 'ringing' until THEY accept; no media starts before that."""
    def work(db):
        user = require_user(db, uid)
        conn, changes = svc.invite(db, user, data.receiver_id)
        caller = people_by_id(db, {uid}).get(uid)
        return (conn.id, conn.status, bool(svc.accepted_friend_request(db, uid, data.receiver_id)), changes,
                {"from": caller, "expires_in": config.RINGING_TTL})
    cid, status, is_friend, changes, caller_info = await db_call(work)
    await dispatch(changes, {cid: caller_info})
    return {"message": "Ringing", "status": status, "connection_id": cid, "is_friend": is_friend,
            "expires_in": config.RINGING_TTL}


@router.get("/calls/incoming")
async def incoming(uid: int = Depends(current_user_id)):
    """Polling fallback for clients without a live user socket."""
    def work(db):
        svc.expire_stale(db)
        conn = svc.incoming_for(db, uid)
        return None if not conn else {**_call_payload(db, conn, uid), "expires_in": config.RINGING_TTL}
    return {"incoming": await db_call(work)}


@router.get("/connections/{connection_id}/status")
async def call_status(connection_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        svc.expire_stale(db)
        return _call_payload(db, svc.get_for_participant(db, uid, connection_id), uid)
    return await db_call(work)


@router.post("/connections/{connection_id}/accept")
async def accept(connection_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        ch = svc.accept(db, require_user(db, uid), connection_id)
        return ch, _call_payload(db, svc.get_for_participant(db, uid, connection_id), uid)
    ch, payload = await db_call(work)
    await dispatch([ch])
    return payload


@router.post("/connections/{connection_id}/reject")
async def reject(connection_id: int, uid: int = Depends(current_user_id)):
    ch = await db_call(lambda db: svc.reject(db, require_user(db, uid), connection_id))
    await dispatch([ch])
    return {"status": ch.new}


@router.post("/connections/{connection_id}/cancel")
async def cancel(connection_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        conn = svc.get_for_participant(db, uid, connection_id, lock=True)
        if conn.requester_id != uid:
            raise HTTPException(403, "Only the caller can cancel.")
        if conn.status not in {"reserved", "ringing"}:
            raise HTTPException(409, "Call cannot be cancelled in its current state.")
        return svc.transition(conn, "cancelled", "cancelled")
    ch = await db_call(work)
    await dispatch([ch])
    return {"status": ch.new}


@router.post("/connections/{connection_id}/end")
async def end(connection_id: int, uid: int = Depends(current_user_id)):
    ch = await db_call(lambda db: svc.end(db, require_user(db, uid), connection_id))
    if ch:
        await dispatch([ch])
    return {"status": ch.new if ch else "ended"}


@router.post("/connections/{connection_id}/ws-ticket")
async def room_ticket(connection_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        svc.expire_stale(db)
        conn = svc.get_for_participant(db, uid, connection_id)
        if conn.status not in svc.JOINABLE:
            raise HTTPException(409, "The call has not been accepted yet." if conn.status in {"reserved", "ringing"}
                                else "This call has ended.")
        if svc.is_blocked(db, conn.requester_id, conn.receiver_id):
            raise HTTPException(403, "You cannot connect with this person.")
        return True
    await db_call(work)
    return {"ticket": create_ws_ticket(uid, "room", connection_id), "expires_in": config.WS_TICKET_SECONDS}


@router.post("/auth/ws-ticket")
async def user_ticket(uid: int = Depends(current_user_id)):
    return {"ticket": create_ws_ticket(uid, "user"), "expires_in": config.WS_TICKET_SECONDS}


# ------------------------------ WebSocket: strict message schema ------------------------------
class WsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["offer", "answer", "ice-candidate", "chat", "media_connected", "hangup", "ping"]
    data: dict | None = None
    text: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def check_payload(self):
        t, d = self.type, self.data
        if t in {"offer", "answer"}:
            if not d or set(d) - {"type", "sdp"} or d.get("type") != t or not isinstance(d.get("sdp"), str) \
                    or not (0 < len(d["sdp"]) <= 40_000):
                raise ValueError("invalid session description")
        elif t == "ice-candidate":
            allowed = {"candidate", "sdpMid", "sdpMLineIndex", "usernameFragment"}
            if d is None or set(d) - allowed or not isinstance(d.get("candidate"), str) or len(d["candidate"]) > 2000 \
                    or not (d.get("sdpMid") is None or isinstance(d["sdpMid"], str)) \
                    or not (d.get("sdpMLineIndex") is None or isinstance(d["sdpMLineIndex"], int)) \
                    or not (d.get("usernameFragment") is None or isinstance(d["usernameFragment"], str)):
                raise ValueError("invalid ICE candidate")
        elif t == "chat":
            if not self.text or not self.text.strip():
                raise ValueError("empty chat message")
            self.text = self.text.strip()
        elif d is not None or self.text is not None:
            raise ValueError("unexpected payload")
        return self


def origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if origin is None:
        return not config.IS_PRODUCTION  # browsers always send Origin; only tooling omits it
    return origin in config.CORS_ORIGINS


def _join_work(db, uid: int, cid: int):
    svc.expire_stale(db)
    conn = svc.get_for_participant(db, uid, cid)
    if conn.status not in svc.JOINABLE:
        raise HTTPException(409, "call not joinable")
    if svc.is_blocked(db, conn.requester_id, conn.receiver_id):
        raise HTTPException(403, "blocked")
    return svc.other_party(conn, uid), conn.status


def _to_connecting(db, cid: int):
    conn = db.scalar(select(Connection).where(Connection.id == cid).with_for_update())
    if conn and conn.status in {"accepted", "disconnected"}:
        return svc.transition(conn, "connecting")
    return None


def _to_active(db, cid: int):
    conn = db.scalar(select(Connection).where(Connection.id == cid).with_for_update())
    if conn and conn.status in {"connecting", "disconnected"}:
        return svc.transition(conn, "active")
    return None


def _mark_disconnected(db, cid: int):
    conn = db.scalar(select(Connection).where(Connection.id == cid).with_for_update())
    if conn and conn.status in {"connecting", "active"}:
        return svc.transition(conn, "disconnected", "peer_dropped")
    return None


@router.websocket("/ws/connection/{connection_id}")
async def ws_connection(ws: WebSocket, connection_id: int, ticket: str = Query(default="")):
    if not origin_allowed(ws):
        await ws.close(code=1008)
        return
    uid = consume_ws_ticket(ticket, "room", connection_id)
    if uid is None:
        await ws.close(code=1008)
        return
    try:
        peer_id, _status = await db_call(_join_work, uid, connection_id)
    except HTTPException:
        await ws.close(code=1008)   # unknown call, not a participant, not accepted, blocked or ended
        return
    await ws.accept()
    await hub.join(connection_id, uid, ws)
    presence.touch(uid)
    try:
        if peer_id in hub.peers(connection_id):
            ch = await db_call(_to_connecting, connection_id)
            await hub.room_send(connection_id, uid, {"type": "room_state", "peers": [peer_id], "peer_id": peer_id})
            await hub.room_send(connection_id, peer_id, {"type": "peer_joined", "user_id": uid})
        else:
            await hub.room_send(connection_id, uid, {"type": "room_state", "peers": [], "peer_id": peer_id})
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_WS_BYTES:
                await ws.close(code=1009)
                return
            presence.touch(uid)
            if not limiter.allow(f"ws:{uid}:{connection_id}", 200, 10):
                await ws.close(code=1008)
                return
            try:
                msg = WsIn.model_validate(json.loads(raw))
            except (ValueError, ValidationError):
                await hub.room_send(connection_id, uid, {"type": "error", "message": "invalid message"})
                continue
            if msg.type == "ping":
                await hub.room_send(connection_id, uid, {"type": "pong"})
            elif msg.type == "chat":
                if not limiter.allow(f"chat:{uid}:{connection_id}", 10, 10):
                    await hub.room_send(connection_id, uid, {"type": "error", "message": "slow down"})
                    continue
                out = {"type": "chat", "from": uid, "text": msg.text}
                for target in (uid, peer_id):
                    await hub.room_send(connection_id, target, out)
            elif msg.type in {"offer", "answer", "ice-candidate"}:
                await hub.room_send(connection_id, peer_id, {"type": msg.type, "from": uid, "data": msg.data})
            elif msg.type == "media_connected":
                hub.confirmed.setdefault(connection_id, set()).add(uid)
                if {uid, peer_id} <= hub.confirmed[connection_id]:
                    ch = await db_call(_to_active, connection_id)
                    if ch:
                        await dispatch([ch])
            elif msg.type == "hangup":
                ch = await db_call(lambda db: svc.end(db, require_user(db, uid), connection_id))
                await hub.room_send(connection_id, peer_id, {"type": "hangup", "from": uid})
                if ch:
                    await dispatch([ch])
                return
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws room error")
    finally:
        # Shielded: the server cancels this task when the client vanishes, and cleanup must still finish
        # (otherwise the call is marked disconnected but the peer is never told).
        with anyio.CancelScope(shield=True):
            if hub.leave(connection_id, uid, ws):
                try:
                    ch = await db_call(_mark_disconnected, connection_id)
                    await hub.room_send(connection_id, peer_id, {"type": "peer_left", "user_id": uid})
                    if ch:
                        await hub.notify(peer_id, {"type": "peer_disconnected", "connection_id": connection_id,
                                                   "grace_seconds": config.DISCONNECTED_GRACE})
                except Exception:
                    log.exception("ws cleanup error")


@router.websocket("/ws/user")
async def ws_user(ws: WebSocket, ticket: str = Query(default="")):
    """Push channel for incoming calls and call-state changes."""
    if not origin_allowed(ws):
        await ws.close(code=1008)
        return
    uid = consume_ws_ticket(ticket, "user", None)
    if uid is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    hub.add_user(uid, ws)
    presence.touch(uid)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 1024:
                await ws.close(code=1009)
                return
            presence.touch(uid)
            if raw.strip() and json.loads(raw).get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        hub.remove_user(uid, ws)
