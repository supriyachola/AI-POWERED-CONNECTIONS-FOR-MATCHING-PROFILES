import time
from collections import defaultdict, deque
from fastapi import HTTPException, Request


class SlidingWindow:
    """Small in-memory limiter (single worker). Swap for a shared store when scaling out."""

    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self.hits) > 20000:  # bound memory
            for k in [k for k, v in self.hits.items() if not v][:5000]:
                self.hits.pop(k, None)
        return True


    def count(self, key: str, window: float) -> int:
        now = time.monotonic()
        q = self.hits.get(key)
        if not q:
            return 0
        while q and q[0] <= now - window:
            q.popleft()
        return len(q)

    def record(self, key: str) -> None:
        self.hits[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        self.hits.pop(key, None)


limiter = SlidingWindow()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")  # Render sits behind a proxy
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def rate_limit(name: str, limit: int, window: float):
    def dep(request: Request):
        if not limiter.allow(f"{name}:{client_ip(request)}", limit, window):
            raise HTTPException(429, "Too many requests. Please slow down and try again shortly.")
    return dep
