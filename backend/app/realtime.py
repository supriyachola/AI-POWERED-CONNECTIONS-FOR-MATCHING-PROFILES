"""In-process WebSocket hub (single worker). Rooms are keyed by call id; user channels by user id.

Scaling out to several workers requires a shared pub/sub (e.g. Redis) behind notify()/room_send().
"""
import logging

from fastapi import WebSocket

from .calls import TERMINAL, Change

log = logging.getLogger("affinity.realtime")


class Hub:
    def __init__(self):
        self.user_sockets: dict[int, set[WebSocket]] = {}
        self.rooms: dict[int, dict[int, WebSocket]] = {}
        self.confirmed: dict[int, set[int]] = {}  # users who reported media connected, per call

    # ---- user channel ----
    def add_user(self, uid: int, ws: WebSocket):
        self.user_sockets.setdefault(uid, set()).add(ws)

    def remove_user(self, uid: int, ws: WebSocket):
        s = self.user_sockets.get(uid)
        if s:
            s.discard(ws)
            if not s:
                self.user_sockets.pop(uid, None)

    async def notify(self, uid: int, payload: dict):
        for ws in list(self.user_sockets.get(uid, ())):
            await self._safe_send(ws, payload)

    # ---- call rooms ----
    def peers(self, cid: int) -> dict[int, WebSocket]:
        return self.rooms.get(cid, {})

    async def join(self, cid: int, uid: int, ws: WebSocket):
        old = self.rooms.setdefault(cid, {}).get(uid)
        self.rooms[cid][uid] = ws
        if old is not None and old is not ws:  # one socket per participant: newest wins
            try:
                await old.close(code=4009)
            except Exception:
                pass

    def leave(self, cid: int, uid: int, ws: WebSocket) -> bool:
        """Remove if this socket is still the registered one. True if it was."""
        room = self.rooms.get(cid)
        if room and room.get(uid) is ws:
            room.pop(uid, None)
            self.confirmed.get(cid, set()).discard(uid)
            if not room:
                self.rooms.pop(cid, None)
                self.confirmed.pop(cid, None)
            return True
        return False

    async def room_send(self, cid: int, to_uid: int, payload: dict):
        ws = self.rooms.get(cid, {}).get(to_uid)
        if ws:
            await self._safe_send(ws, payload)

    async def close_room(self, cid: int, code: int = 4000):
        for ws in list(self.rooms.pop(cid, {}).values()):
            try:
                await ws.close(code=code)
            except Exception:
                pass
        self.confirmed.pop(cid, None)

    @staticmethod
    async def _safe_send(ws: WebSocket, payload: dict):
        try:
            await ws.send_json(payload)
        except Exception:
            pass


hub = Hub()


async def dispatch(changes: list[Change], extra: dict | None = None):
    """Translate state changes into client notifications. `extra` maps connection_id -> payload for ringing."""
    for ch in changes:
        base = {"connection_id": ch.connection_id, "status": ch.new, "reason": ch.reason}
        if ch.new == "ringing":
            payload = {"type": "incoming_call", **base, **((extra or {}).get(ch.connection_id) or {})}
            await hub.notify(ch.receiver_id, payload)
        elif ch.new == "accepted":
            await hub.notify(ch.requester_id, {"type": "call_accepted", **base})
        elif ch.new == "active":
            for uid in (ch.requester_id, ch.receiver_id):
                await hub.notify(uid, {"type": "call_active", **base})
        elif ch.new in TERMINAL:
            kind = "call_rejected" if ch.new == "rejected" else "call_ended"
            for uid in (ch.requester_id, ch.receiver_id):
                await hub.notify(uid, {"type": kind, **base})
            await hub.close_room(ch.connection_id)
