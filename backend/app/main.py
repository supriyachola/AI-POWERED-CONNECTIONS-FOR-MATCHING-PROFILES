import json
import os
import secrets
import string
import time
from datetime import datetime, timezone
from typing import Dict

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.orm import Session

from .database import Base, engine, get_db, migrate_schema
from .models import User, Profile, Connection, FriendRequest, DiscoveryAction, InteractionHistory, Block, Report
from .schemas import RegisterRequest, LoginRequest, ProfileRequest, ConnectionRequest, LocationRequest, ReportRequest, PREFERRED_GENDERS
from .auth import hash_password, verify_password, create_token, decode_token
from .matching import normalize_interests, score_matches

migrate_schema()

app = FastAPI(title="Affinity Plus API", version="0.11.0")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,https://affinityplus.vercel.app").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBearer()

# ---------------- Authentication ----------------
@app.post("/auth/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    username = data.username.strip()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "Username already exists")
    user = User(username=username, password_hash=hash_password(data.password), account_type="registered")
    db.add(user)
    db.flush()
    profile = Profile(user_id=user.id, age=data.age, gender=data.gender)
    db.add(profile)
    db.commit()
    db.refresh(user)
    presence.touch(user.id)
    return {"token": create_token(user.id), "user": public_user(user)}

@app.post("/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    username = data.username.strip()
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.password_hash or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    presence.touch(user.id)
    return {"token": create_token(user.id), "user": public_user(user)}

@app.post("/auth/guest")
def guest_login(db: Session = Depends(get_db)):
    user = User(username=make_guest_username(db), account_type="guest", guest_token=secrets.token_urlsafe(32))
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, age=None, gender="prefer_not_to_say"))
    db.commit()
    db.refresh(user)
    presence.touch(user.id)
    return {"token": create_token(user.id), "user": public_user(user)}

# ---------------- Live presence tracking ----------------
# Lightweight in-memory presence so the frontend can show "X people online"
# and so matching can prefer people who are actually online right now.
# A user counts as online if we've heard from them (any API call, heartbeat,
# or open websocket) within ONLINE_WINDOW_SECONDS.
ONLINE_WINDOW_SECONDS = 45


class PresenceManager:
    def __init__(self):
        self._last_seen: Dict[int, float] = {}

    def touch(self, user_id: int) -> None:
        self._last_seen[user_id] = time.time()

    def mark_offline(self, user_id: int) -> None:
        self._last_seen.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        ts = self._last_seen.get(user_id)
        return bool(ts and (time.time() - ts) <= ONLINE_WINDOW_SECONDS)

    def online_count(self) -> int:
        cutoff = time.time() - ONLINE_WINDOW_SECONDS
        return sum(1 for ts in self._last_seen.values() if ts >= cutoff)

    def sweep(self) -> None:
        cutoff = time.time() - ONLINE_WINDOW_SECONDS * 6
        for uid in [u for u, ts in self._last_seen.items() if ts < cutoff]:
            self._last_seen.pop(uid, None)


presence = PresenceManager()

INDIA_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"
]


def current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    user = db.get(User, decode_token(credentials.credentials))
    if not user:
        raise HTTPException(401, "User not found")
    presence.touch(user.id)
    return user


def parse_interests(value):
    try:
        return json.loads(value or "[]")
    except Exception:
        return []


def profile_payload(profile):
    if not profile:
        return {"bio": "", "looking_for": "", "interests": [], "age": None, "gender": "prefer_not_to_say", "country": "", "state": "", "city": "", "district": "", "vibe": "", "preferred_gender": "any", "preferred_country": "any", "preferred_state": "any", "min_age": 18, "max_age": 100}
    return {
        "bio": profile.bio or "", "looking_for": profile.looking_for or "", "interests": parse_interests(profile.interests),
        "age": profile.age, "gender": profile.gender or "prefer_not_to_say", "country": profile.country or "", "state": profile.state or "", "city": profile.city or "", "district": profile.district or "", "vibe": profile.vibe or "",
        "preferred_gender": profile.preferred_gender or "any", "preferred_country": profile.preferred_country or "any", "preferred_state": profile.preferred_state or "any", "min_age": profile.min_age or 18, "max_age": profile.max_age or 100,
    }


def public_user(user):
    return {"id": user.id, "username": user.username, "account_type": user.account_type}


def public_person(user, profile):
    return {"user_id": user.id, "username": user.username, "is_online": presence.is_online(user.id), **profile_payload(profile)}


def make_guest_username(db: Session) -> str:
    alphabet = string.ascii_lowercase + string.digits
    while True:
        username = "guest_" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.scalar(select(User).where(User.username == username)):
            return username


def is_blocked(db: Session, a: int, b: int) -> bool:
    return bool(db.scalar(select(Block).where(or_(and_(Block.user_id == a, Block.blocked_user_id == b), and_(Block.user_id == b, Block.blocked_user_id == a)))))


def record_history(db: Session, user_id: int, other_user_id: int, action: str, connection_id: int | None = None):
    db.add(InteractionHistory(user_id=user_id, other_user_id=other_user_id, action=action, connection_id=connection_id))


def rolling_window(age: int) -> tuple[int, int]:
    return max(18, age - 3), min(100, age + 2)


def gender_allowed(me: Profile, other: Profile, session_gender: str) -> bool:
    if session_gender not in PREFERRED_GENDERS:
        session_gender = "any"
    if session_gender != "any" and other.gender != session_gender:
        return False
    if other.preferred_gender and other.preferred_gender != "any" and me.gender != other.preferred_gender:
        return False
    return True


def age_allowed(me: Profile, other: Profile, strict_age: bool = True) -> bool:
    if not strict_age:
        return True
    if me.age is None or other.age is None:
        return False
    me_low, me_high = rolling_window(me.age)
    other_low, other_high = rolling_window(other.age)
    return me_low <= other.age <= me_high and other_low <= me.age <= other_high


def location_allowed(me: Profile, other: Profile, scope: str) -> bool:
    if scope == "district":
        return bool(me.district and other.district and me.district.casefold() == other.district.casefold())
    if scope == "state":
        return bool(me.state and other.state and me.state.casefold() == other.state.casefold() and me.country.casefold() == other.country.casefold())
    return True


def matches_preference(me: Profile, other: Profile, session_gender: str = "any", location_scope: str = "district", strict_age: bool = True) -> bool:
    return gender_allowed(me, other, session_gender) and age_allowed(me, other, strict_age) and location_allowed(me, other, location_scope)


CALL_ACTIVE_STATUSES = {"reserved", "calling", "active"}
CALL_RESERVATION_TIMEOUT_SECONDS = 20

def cleanup_stale_calls(db: Session) -> None:
    """Release abandoned discovery/call reservations so users never get stuck."""
    cutoff = datetime.now(timezone.utc).timestamp() - CALL_RESERVATION_TIMEOUT_SECONDS
    rows = db.scalars(select(Connection).where(Connection.status.in_(["reserved", "calling"]))).all()
    changed = False
    for row in rows:
        created = row.updated_at or row.created_at
        if created and created.timestamp() < cutoff:
            row.status = "ended"
            changed = True
    if changed:
        db.flush()

def active_call_for_user(db: Session, user_id: int):
    """Return the one active call/reservation belonging to a user, if any."""
    return db.scalar(select(Connection).where(
        Connection.status.in_(CALL_ACTIVE_STATUSES),
        or_(Connection.requester_id == user_id, Connection.receiver_id == user_id),
    ).order_by(desc(Connection.updated_at)))


def ranked(user, db, session_gender: str = "any", location_scope: str = "district", state_filter: str = ""):
    me = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not me or me.age is None:
        return []
    if location_scope not in {"district", "state", "anywhere"}:
        location_scope = "district"

    excluded = {x.candidate_id for x in db.scalars(select(DiscoveryAction).where(DiscoveryAction.user_id == user.id)).all()}
    excluded.update({x.blocked_user_id for x in db.scalars(select(Block).where(Block.user_id == user.id)).all()})
    excluded.update({x.user_id for x in db.scalars(select(Block).where(Block.blocked_user_id == user.id)).all()})
    excluded.add(user.id)
    cleanup_stale_calls(db)
    active_rows = db.scalars(select(Connection).where(Connection.status.in_(CALL_ACTIVE_STATUSES))).all()
    # A person can participate in exactly one discovery reservation/call at a time.
    for row in active_rows:
        if row.requester_id != user.id:
            excluded.add(row.requester_id)
        if row.receiver_id != user.id:
            excluded.add(row.receiver_id)
    excluded.update({
        row.receiver_id if row.requester_id == user.id else row.requester_id
        for row in db.scalars(select(Connection).where(or_(Connection.requester_id == user.id, Connection.receiver_id == user.id))).all()
        if row.status in CALL_ACTIVE_STATUSES
    })
    excluded.update({
        row.receiver_id if row.requester_id == user.id else row.requester_id
        for row in db.scalars(select(FriendRequest).where(
            FriendRequest.status.in_(["pending", "accepted"]),
            or_(FriendRequest.requester_id == user.id, FriendRequest.receiver_id == user.id),
        )).all()
    })

    candidates = []
    for p in db.scalars(select(Profile).where(Profile.user_id != user.id)).all():
        if p.user_id in excluded or not gender_allowed(me, p, session_gender):
            continue
        if state_filter and (not p.state or p.state.casefold() != state_filter.casefold() or not p.country or p.country.casefold() != "india"):
            continue
        other = db.get(User, p.user_id)
        if other:
            candidates.append((other, p, public_person(other, p)))

    # Quiet fallback ladder. We only move to the next tier when the current
    # tier has no candidate, so matching never dead-ends on a strict filter.
    online = lambda item: presence.is_online(item[0].id)
    age_match = lambda item: age_allowed(me, item[1], True)
    district_match = lambda item: location_allowed(me, item[1], "district")
    state_match = lambda item: location_allowed(me, item[1], "state")

    if location_scope == "district":
        tiers = [
            ("same district · age matched · online", lambda x: age_match(x) and district_match(x) and online(x)),
            ("same state · age matched · online", lambda x: age_match(x) and state_match(x) and online(x)),
            ("anywhere · age matched · online", lambda x: age_match(x) and online(x)),
            ("anywhere · any age · online", lambda x: online(x)),
            ("anywhere · any age · online", lambda x: online(x)),
        ]
    elif location_scope == "state":
        tiers = [
            ("same state · age matched · online", lambda x: age_match(x) and state_match(x) and online(x)),
            ("anywhere · age matched · online", lambda x: age_match(x) and online(x)),
            ("anywhere · any age · online", lambda x: online(x)),
            ("anywhere · any age · online", lambda x: online(x)),
        ]
    else:
        tiers = [
            ("anywhere · age matched · online", lambda x: age_match(x) and online(x)),
            ("anywhere · any age · online", lambda x: online(x)),
            ("anywhere · any age · online", lambda x: online(x)),
        ]

    target = profile_payload(me)
    for tier_name, predicate in tiers:
        tier = [item for item in candidates if predicate(item)]
        if tier:
            scored = score_matches(target, [item[2] for item in tier])
            for candidate in scored:
                candidate["match_tier"] = tier_name
            return sorted(scored, key=lambda c: -c.get("score", 0))
    return []


@app.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    return {**public_user(user), "profile": profile_payload(profile)}

@app.put("/me/profile")
def update_profile(data: ProfileRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)
    profile.bio = data.bio.strip()
    profile.looking_for = data.looking_for.strip()
    profile.interests = json.dumps(normalize_interests(data.interests))
    if data.age is not None:
        profile.age = data.age
    profile.gender = data.gender
    profile.country = data.country.strip()
    profile.state = data.state.strip()
    profile.city = data.city.strip()
    profile.district = data.district.strip()
    profile.vibe = data.vibe
    profile.preferred_gender = data.preferred_gender
    profile.preferred_country = data.preferred_country.strip()
    profile.preferred_state = data.preferred_state.strip()
    profile.min_age = data.min_age
    profile.max_age = data.max_age
    db.commit(); db.refresh(profile)
    return profile_payload(profile)

@app.put("/me/location")
def update_location(data: LocationRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)
    profile.city = data.city.strip()
    profile.district = data.district.strip()
    profile.state = data.state.strip()
    profile.country = data.country.strip()
    db.commit(); db.refresh(profile)
    return profile_payload(profile)

@app.post("/presence/heartbeat")
def heartbeat(user: User = Depends(current_user), db: Session = Depends(get_db)):
    presence.touch(user.id)
    presence.sweep()
    return {"online_count": presence.online_count()}

@app.get("/discover/next")
def discover_next(gender: str = "any", state: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if gender not in PREFERRED_GENDERS:
        raise HTTPException(422, "Invalid session gender preference.")
    if state and state not in INDIA_STATES:
        raise HTTPException(422, "Invalid India state filter.")
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not profile or profile.age is None:
        raise HTTPException(409, "Complete your 18+ age before matching.")

    cleanup_stale_calls(db)
    if active_call_for_user(db, user.id):
        raise HTTPException(409, "You are already connected to someone. End that call before finding another match.")

    items = ranked(user, db, gender, "district", state)
    # Reserve the first available person. Reservation is the important part:
    # once user A is shown user B, B cannot simultaneously be reserved by C.
    for item in items:
        candidate_id = item["user_id"]
        # Lock the two user rows in deterministic order so two simultaneous matches
        # cannot allocate the same person to different callers.
        ids = sorted([user.id, candidate_id])
        db.execute(select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()).all()
        cleanup_stale_calls(db)
        if active_call_for_user(db, user.id) or active_call_for_user(db, candidate_id):
            db.rollback()
            continue
        if not presence.is_online(candidate_id):
            db.rollback()
            continue
        reservation = Connection(requester_id=user.id, receiver_id=candidate_id, status="reserved")
        db.add(reservation)
        db.flush()
        db.add(DiscoveryAction(user_id=user.id, candidate_id=candidate_id, action="reserved"))
        db.commit()
        db.refresh(reservation)
        item["reservation_id"] = reservation.id
        item["is_reserved"] = True
        return {"found": True, "person": item, "reservation_id": reservation.id, "message": None}

    return {"found": False, "person": None, "reservation_id": None, "message": "No one is available right now. Please try again soon."}


@app.post("/discover/{candidate_id}/skip")
def skip(candidate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if candidate_id == user.id or not db.get(User, candidate_id):
        raise HTTPException(404, "Person not found")
    if is_blocked(db, user.id, candidate_id):
        raise HTTPException(403, "This person is blocked.")
    row = db.scalar(select(Connection).where(
        Connection.requester_id == user.id,
        Connection.receiver_id == candidate_id,
        Connection.status == "reserved",
    ))
    if row:
        row.status = "ended"
    if not db.scalar(select(DiscoveryAction).where(DiscoveryAction.user_id == user.id, DiscoveryAction.candidate_id == candidate_id)):
        db.add(DiscoveryAction(user_id=user.id, candidate_id=candidate_id, action="skip"))
    db.commit()
    return {"status": "skipped"}


@app.post("/connections")
def connect(data: ConnectionRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Convert a reserved match into one private two-person call.

    Exactly one active call is allowed per user. The target must still be online,
    and the pair is locked in a single transaction before the call is created.
    """
    if data.receiver_id == user.id or not db.get(User, data.receiver_id):
        raise HTTPException(404, "Person not found")
    candidate_id = data.receiver_id
    presence.sweep()
    cleanup_stale_calls(db)

    # Lock both user rows in deterministic order. This closes the race where
    # users 1 and 3 both try to connect to user 2 at nearly the same moment.
    ids = sorted([user.id, candidate_id])
    db.execute(select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()).all()
    cleanup_stale_calls(db)

    if not presence.is_online(candidate_id):
        db.rollback()
        raise HTTPException(409, "This person is no longer online. Find another match.")
    if is_blocked(db, user.id, candidate_id):
        db.rollback()
        raise HTTPException(403, "You cannot connect with a blocked person.")

    pair = db.scalar(select(Connection).where(or_(
        and_(Connection.requester_id == user.id, Connection.receiver_id == candidate_id),
        and_(Connection.requester_id == candidate_id, Connection.receiver_id == user.id),
    )))
    active_for_me = db.scalar(select(Connection).where(
        Connection.status.in_(CALL_ACTIVE_STATUSES),
        or_(Connection.requester_id == user.id, Connection.receiver_id == user.id),
    ))
    active_for_candidate = db.scalar(select(Connection).where(
        Connection.status.in_(CALL_ACTIVE_STATUSES),
        or_(Connection.requester_id == candidate_id, Connection.receiver_id == candidate_id),
    ))

    if active_for_me and (not pair or active_for_me.id != pair.id):
        db.rollback()
        raise HTTPException(409, "You are already connected to someone. End the call before finding another match.")
    if active_for_candidate and (not pair or active_for_candidate.id != pair.id):
        db.rollback()
        raise HTTPException(409, "This person is already talking to someone else. Find another match.")

    if pair and pair.status in CALL_ACTIVE_STATUSES:
        if pair.status == "reserved" and pair.requester_id == user.id:
            pair.status = "calling"
        record_history(db, user.id, candidate_id, "call_started", pair.id)
        record_history(db, candidate_id, user.id, "call_started", pair.id)
        db.commit()
        return {"message": "Call started", "status": pair.status, "connection_id": pair.id, "is_friend": bool(accepted_friend_request(db, user.id, candidate_id))}

    if pair:
        pair.status = "calling"
        connection = pair
    else:
        connection = Connection(requester_id=user.id, receiver_id=candidate_id, status="calling")
        db.add(connection)
        db.flush()

    db.add(DiscoveryAction(user_id=user.id, candidate_id=candidate_id, action="connected"))
    record_history(db, user.id, candidate_id, "call_started", connection.id)
    record_history(db, candidate_id, user.id, "call_started", connection.id)
    db.commit()
    return {"message": "Call started", "status": "calling", "connection_id": connection.id, "is_friend": bool(accepted_friend_request(db, user.id, candidate_id))}



