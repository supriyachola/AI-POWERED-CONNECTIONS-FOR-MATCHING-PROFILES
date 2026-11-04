import time
from typing import Dict

# A user is online if we heard from them (API call, heartbeat, open socket) within this window.
ONLINE_WINDOW_SECONDS = 45


class PresenceManager:
    """In-process presence. Single-worker only: replace with a shared TTL store before scaling out."""

    def __init__(self):
        self._last_seen: Dict[int, float] = {}

    def touch(self, user_id: int) -> None:
        self._last_seen[user_id] = time.time()

    def mark_offline(self, user_id: int) -> None:
        self._last_seen.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        ts = self._last_seen.get(user_id)
        return bool(ts and (time.time() - ts) <= ONLINE_WINDOW_SECONDS)

    def online_ids(self) -> set[int]:
        cutoff = time.time() - ONLINE_WINDOW_SECONDS
        return {u for u, ts in self._last_seen.items() if ts >= cutoff}

    def online_count(self) -> int:
        return len(self.online_ids())

    def sweep(self) -> None:
        cutoff = time.time() - ONLINE_WINDOW_SECONDS * 6
        for uid in [u for u, ts in self._last_seen.items() if ts < cutoff]:
            self._last_seen.pop(uid, None)


presence = PresenceManager()
