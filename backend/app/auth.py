import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash

from . import config

password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"
_UNAUTH = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id), "typ": "access", "jti": secrets.token_urlsafe(8),
        "iat": now, "exp": now + timedelta(minutes=config.ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[ALGORITHM])
        if payload.get("typ", "access") != "access":  # WS tickets must never work as API tokens
            raise ValueError("wrong token type")
        return int(payload["sub"])
    except Exception:
        raise _UNAUTH


# ---- Short-lived, single-use WebSocket tickets (keeps long-lived tokens out of URLs) ----
_used_tickets: dict[str, float] = {}
_ticket_lock = threading.Lock()


def create_ws_ticket(user_id: int, scope: str, connection_id: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id), "typ": "wst", "scope": scope, "cid": connection_id,
        "jti": secrets.token_urlsafe(12), "iat": now,
        "exp": now + timedelta(seconds=config.WS_TICKET_SECONDS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def consume_ws_ticket(ticket: str, scope: str, connection_id: int | None = None) -> int | None:
    """Return the user id if the ticket is valid, unexpired, unused and bound to this scope/room."""
    try:
        p = jwt.decode(ticket, config.JWT_SECRET, algorithms=[ALGORITHM])
        if p.get("typ") != "wst" or p.get("scope") != scope or p.get("cid") != connection_id:
            return None
        jti, exp = p["jti"], float(p["exp"])
        user_id = int(p["sub"])
    except Exception:
        return None
    with _ticket_lock:
        now = time.time()
        for k in [k for k, e in _used_tickets.items() if e < now]:
            _used_tickets.pop(k, None)
        if jti in _used_tickets:
            return None
        _used_tickets[jti] = exp
    return user_id
