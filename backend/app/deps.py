import json
from typing import Callable

from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import decode_token
from .database import SessionLocal, get_db
from .models import Profile, User
from .presence import presence

security = HTTPBearer()

INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]


def current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> User:
    user = db.get(User, decode_token(credentials.credentials))
    if not user:
        raise HTTPException(401, "User not found")
    presence.touch(user.id)
    return user


def current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """Token-only dependency for async routes that do their DB work through db_call()."""
    uid = decode_token(credentials.credentials)
    presence.touch(uid)
    return uid


async def db_call(fn: Callable, *args, **kwargs):
    """Run a sync DB function in the threadpool with its own session; commit on success."""
    def run():
        db = SessionLocal()
        try:
            result = fn(db, *args, **kwargs)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return await run_in_threadpool(run)


def require_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return user


def parse_interests(value):
    try:
        out = json.loads(value or "[]")
        return out if isinstance(out, list) else []
    except Exception:
        return []


DEFAULT_PROFILE = {"bio": "", "looking_for": "", "interests": [], "age": None, "gender": "prefer_not_to_say",
                   "country": "", "state": "", "city": "", "district": "", "vibe": "",
                   "preferred_gender": "any", "preferred_country": "any", "preferred_state": "any",
                   "min_age": 18, "max_age": 100}


def profile_payload(p: Profile | None) -> dict:
    if not p:
        return dict(DEFAULT_PROFILE)
    return {
        "bio": p.bio or "", "looking_for": p.looking_for or "", "interests": parse_interests(p.interests),
        "age": p.age, "gender": p.gender or "prefer_not_to_say", "country": p.country or "",
        "state": p.state or "", "city": p.city or "", "district": p.district or "", "vibe": p.vibe or "",
        "preferred_gender": p.preferred_gender or "any", "preferred_country": p.preferred_country or "any",
        "preferred_state": p.preferred_state or "any", "min_age": p.min_age or 18, "max_age": p.max_age or 100,
    }


def public_user(user: User) -> dict:
    return {"id": user.id, "username": user.username, "account_type": user.account_type}


def public_person(user: User, profile: Profile | None) -> dict:
    # Only fields meant for other users: preferences (preferred_*, min/max age) stay private.
    full = profile_payload(profile)
    for private in ("preferred_gender", "preferred_country", "preferred_state", "min_age", "max_age"):
        full.pop(private, None)
    return {"user_id": user.id, "username": user.username, "is_online": presence.is_online(user.id), **full}


def people_by_id(db: Session, ids: set[int]) -> dict[int, dict]:
    """Bulk-load public person payloads (avoids N+1 queries)."""
    if not ids:
        return {}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()}
    profiles = {p.user_id: p for p in db.scalars(select(Profile).where(Profile.user_id.in_(ids))).all()}
    return {uid: public_person(u, profiles.get(uid)) for uid, u in users.items()}
