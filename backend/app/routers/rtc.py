"""ICE server configuration. TURN credentials are minted per request (coturn `use-auth-secret` scheme)."""
import base64
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Depends

from ..deps import current_user_id

router = APIRouter()
TURN_TTL_SECONDS = 3600


def build_ice_servers(user_id: int, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    stun = [u.strip() for u in os.getenv("STUN_URLS", "stun:stun.l.google.com:19302").split(",") if u.strip()]
    servers = [{"urls": stun}]
    turn_urls = [u.strip() for u in os.getenv("TURN_URLS", "").split(",") if u.strip()]
    secret = os.getenv("TURN_SHARED_SECRET", "")
    turn_ready = bool(turn_urls and secret)
    if turn_ready:
        username = f"{int(now) + TURN_TTL_SECONDS}:{user_id}"
        credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({"urls": turn_urls, "username": username, "credential": credential})
    return {"iceServers": servers, "turn_configured": turn_ready, "ttl": TURN_TTL_SECONDS}


@router.get("/rtc/config")
async def rtc_config(uid: int = Depends(current_user_id)):
    return build_ice_servers(uid)
