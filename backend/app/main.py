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

app = FastAPI(title="Affinity Plus API", version="0.10.0")
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
    excluded.update({
        row.receiver_id if row.requester_id == user.id else row.requester_id
        for row in db.scalars(select(Connection).where(or_(Connection.requester_id == user.id, Connection.receiver_id == user.id))).all()
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
    items = ranked(user, db, gender, "district", state)
    return {
        "found": bool(items),
        "person": items[0] if items else None,
        "message": None if items else "No one is available right now. Please try again soon."
    }

@app.post("/discover/{candidate_id}/skip")
def skip(candidate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if candidate_id == user.id or not db.get(User, candidate_id): raise HTTPException(404, "Person not found")
    if is_blocked(db, user.id, candidate_id): raise HTTPException(403, "This person is blocked.")
    if not db.scalar(select(DiscoveryAction).where(DiscoveryAction.user_id == user.id, DiscoveryAction.candidate_id == candidate_id)):
        db.add(DiscoveryAction(user_id=user.id, candidate_id=candidate_id, action="skip")); db.commit()
    return {"status": "skipped"}

@app.post("/connections")
def connect(data: ConnectionRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Start (or resume) a call session with someone.

    This intentionally does NOT make the two people friends. Starting a call
    just opens a private room; becoming friends is a separate, explicit
    action (see /connections/{id}/add-friend) so people are never added to
    each other's friends list without choosing to.
    """
    if data.receiver_id == user.id or not db.get(User, data.receiver_id): raise HTTPException(404, "Person not found")
    # A call can only be started while the other person is currently online.
    # Discovery is online-first and this second check prevents a stale result from opening an offline call.
    presence.sweep()
    if not presence.is_online(data.receiver_id):
        raise HTTPException(409, "This person is no longer online. Find another match.")
    if is_blocked(db, user.id, data.receiver_id): raise HTTPException(403, "You cannot connect with a blocked person.")
    existing = db.scalar(select(Connection).where(or_(and_(Connection.requester_id == user.id, Connection.receiver_id == data.receiver_id), and_(Connection.requester_id == data.receiver_id, Connection.receiver_id == user.id))))
    if existing:
        if existing.status == "blocked": raise HTTPException(403, "This connection is blocked.")
        if existing.status == "removed": existing.status = "calling"
        record_history(db, user.id, data.receiver_id, "call_started", existing.id); db.commit()
        return {"message": "Reconnected", "status": existing.status, "connection_id": existing.id, "is_friend": bool(existing.status == "connected" or accepted_friend_request(db, user.id, data.receiver_id))}
    connection = Connection(requester_id=user.id, receiver_id=data.receiver_id, status="calling")
    db.add(connection); db.flush()
    db.add(DiscoveryAction(user_id=user.id, candidate_id=data.receiver_id, action="connected"))
    record_history(db, user.id, data.receiver_id, "call_started", connection.id); record_history(db, data.receiver_id, user.id, "call_started", connection.id); db.commit()
    return {"message": "Call started", "status": "calling", "connection_id": connection.id, "is_friend": bool(accepted_friend_request(db, user.id, data.receiver_id))}

def accepted_friend_request(db: Session, a: int, b: int):
    return db.scalar(select(FriendRequest).where(
        FriendRequest.status == "accepted",
        or_(
            and_(FriendRequest.requester_id == a, FriendRequest.receiver_id == b),
            and_(FriendRequest.requester_id == b, FriendRequest.receiver_id == a),
        ),
    ))


def pending_friend_request(db: Session, a: int, b: int):
    return db.scalar(select(FriendRequest).where(
        FriendRequest.status == "pending",
        or_(
            and_(FriendRequest.requester_id == a, FriendRequest.receiver_id == b),
            and_(FriendRequest.requester_id == b, FriendRequest.receiver_id == a),
        ),
    ))


@app.post("/connections/{connection_id}/friend-request")
def send_friend_request(connection_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Connection, connection_id)
    if not row or user.id not in (row.requester_id, row.receiver_id):
        raise HTTPException(404, "Connection not found")
    other_id = row.receiver_id if row.requester_id == user.id else row.requester_id
    if is_blocked(db, user.id, other_id):
        raise HTTPException(403, "You cannot send a request to a blocked person.")
    if accepted_friend_request(db, user.id, other_id):
        return {"status": "accepted", "message": "You are already friends."}
    existing = pending_friend_request(db, user.id, other_id)
    if existing:
        if existing.requester_id == user.id:
            return {"status": "pending", "message": "Friend request already sent.", "request_id": existing.id}
        raise HTTPException(409, "This person has already sent you a friend request. Open Requests to accept it.")
    request = FriendRequest(requester_id=user.id, receiver_id=other_id, status="pending")
    db.add(request)
    db.commit(); db.refresh(request)
    return {"status": "pending", "message": "Friend request sent. They need to accept it.", "request_id": request.id}


@app.get("/friend-requests")
def friend_requests(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(FriendRequest).where(
        or_(FriendRequest.receiver_id == user.id, FriendRequest.requester_id == user.id),
        FriendRequest.status == "pending",
    ).order_by(desc(FriendRequest.created_at))).all()
    incoming, outgoing = [], []
    for r in rows:
        other_id = r.requester_id if r.receiver_id == user.id else r.receiver_id
        other = db.get(User, other_id)
        profile = db.scalar(select(Profile).where(Profile.user_id == other_id)) if other else None
        if not other: continue
        item = {"request_id": r.id, "user_id": other.id, "username": other.username, "is_online": presence.is_online(other.id), "created_at": r.created_at.isoformat(), **profile_payload(profile)}
        (incoming if r.receiver_id == user.id else outgoing).append(item)
    return {"incoming": incoming, "outgoing": outgoing, "incoming_count": len(incoming)}


@app.post("/friend-requests/{request_id}/accept")
def accept_friend_request(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    request = db.get(FriendRequest, request_id)
    if not request or request.receiver_id != user.id or request.status != "pending":
        raise HTTPException(404, "Friend request not found")
    if is_blocked(db, user.id, request.requester_id):
        raise HTTPException(403, "Unblock this person before accepting the request.")
    request.status = "accepted"
    request.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "accepted", "message": "Friend request accepted."}


@app.post("/friend-requests/{request_id}/reject")
def reject_friend_request(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    request = db.get(FriendRequest, request_id)
    if not request or request.receiver_id != user.id or request.status != "pending":
        raise HTTPException(404, "Friend request not found")
    request.status = "rejected"
    db.commit()
    return {"status": "rejected"}


@app.delete("/friend-requests/{request_id}")
def cancel_friend_request(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    request = db.get(FriendRequest, request_id)
    if not request or request.requester_id != user.id or request.status != "pending":
        raise HTTPException(404, "Friend request not found")
    request.status = "cancelled"
    db.commit()
    return {"status": "cancelled"}


@app.get("/connections")
def connections(user: User = Depends(current_user), db: Session = Depends(get_db)):
    result, seen = [], set()
    # Legacy/explicitly connected records remain compatible. New friendships
    # come only from an accepted FriendRequest.
    rows = db.scalars(select(Connection).where(or_(Connection.requester_id == user.id, Connection.receiver_id == user.id), Connection.status == "connected").order_by(desc(Connection.updated_at))).all()
    for row in rows:
        other_id = row.receiver_id if row.requester_id == user.id else row.requester_id
        if other_id in seen or is_blocked(db, user.id, other_id): continue
        other = db.get(User, other_id); profile = db.scalar(select(Profile).where(Profile.user_id == other_id)) if other else None
        if other:
            seen.add(other_id); result.append({"connection_id": row.id, "friend_request_id": None, **public_person(other, profile), "status": "connected", "connected_at": row.created_at.isoformat()})
    accepted = db.scalars(select(FriendRequest).where(
        FriendRequest.status == "accepted",
        or_(FriendRequest.requester_id == user.id, FriendRequest.receiver_id == user.id),
    ).order_by(desc(FriendRequest.updated_at))).all()
    for fr in accepted:
        other_id = fr.receiver_id if fr.requester_id == user.id else fr.requester_id
        if other_id in seen or is_blocked(db, user.id, other_id): continue
        other = db.get(User, other_id); profile = db.scalar(select(Profile).where(Profile.user_id == other_id)) if other else None
        if other:
            seen.add(other_id); result.append({"connection_id": None, "friend_request_id": fr.id, **public_person(other, profile), "status": "accepted", "connected_at": fr.updated_at.isoformat()})
    return {"connections": result}

@app.delete("/connections/{connection_id}")
def remove_connection(connection_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Connection, connection_id)
    if row and user.id in (row.requester_id, row.receiver_id):
        other_id = row.receiver_id if row.requester_id == user.id else row.requester_id
        row.status = "removed"
        db.commit()
        return {"status": "removed"}
    raise HTTPException(404, "Connection not found")


@app.delete("/friendships/{friend_request_id}")
def remove_friendship(friend_request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    request = db.get(FriendRequest, friend_request_id)
    if not request or user.id not in (request.requester_id, request.receiver_id) or request.status != "accepted":
        raise HTTPException(404, "Friendship not found")
    request.status = "removed"
    db.commit()
    return {"status": "removed"}


@app.get("/history")
def history(limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 100)
    rows = db.scalars(
        select(InteractionHistory)
        .where(
            InteractionHistory.user_id == user.id,
            InteractionHistory.action == "call_started",
        )
        .order_by(desc(InteractionHistory.created_at))
        .limit(limit)
    ).all()

    result = []
    seen = set()
    for row in rows:
        # A call can create multiple call_started records; show the most recent
        # call per person so History is a clean chronological list of people.
        if row.other_user_id in seen:
            continue
        seen.add(row.other_user_id)
        other = db.get(User, row.other_user_id)
        profile = db.scalar(select(Profile).where(Profile.user_id == row.other_user_id)) if other else None
        connection = db.get(Connection, row.connection_id) if row.connection_id else None
        is_friend = bool((connection and connection.status == "connected") or accepted_friend_request(db, user.id, row.other_user_id))
        if other:
            result.append({
                "id": row.id,
                "user_id": other.id,
                "username": other.username,
                "connection_id": row.connection_id,
                "friend_request_id": (accepted_friend_request(db, user.id, row.other_user_id).id if accepted_friend_request(db, user.id, row.other_user_id) else None),
                "created_at": row.created_at.isoformat(),
                "is_online": presence.is_online(other.id),
                "is_friend": is_friend,
                "is_blocked": is_blocked(db, user.id, other.id),
                **profile_payload(profile),
            })
    return {"history": result}

@app.get("/blocked")
def blocked(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Block).where(Block.user_id == user.id).order_by(desc(Block.created_at))).all()
    return {"blocked": [{"user_id": r.blocked_user_id, "username": db.get(User, r.blocked_user_id).username if db.get(User, r.blocked_user_id) else "", "created_at": r.created_at.isoformat()} for r in rows]}

@app.post("/users/{target_id}/block")
def block_user(target_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if target_id == user.id or not db.get(User, target_id): raise HTTPException(404, "Person not found")
    if not db.scalar(select(Block).where(Block.user_id == user.id, Block.blocked_user_id == target_id)):
        db.add(Block(user_id=user.id, blocked_user_id=target_id))
    connection = db.scalar(select(Connection).where(or_(and_(Connection.requester_id == user.id, Connection.receiver_id == target_id), and_(Connection.requester_id == target_id, Connection.receiver_id == user.id))))
    if connection: connection.status = "blocked"
    db.commit()
    return {"status": "blocked"}

@app.delete("/users/{target_id}/block")
def unblock_user(target_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Block).where(Block.user_id == user.id, Block.blocked_user_id == target_id))
    if row: db.delete(row)
    connection = db.scalar(select(Connection).where(or_(and_(Connection.requester_id == user.id, Connection.receiver_id == target_id), and_(Connection.requester_id == target_id, Connection.receiver_id == user.id))))
    if connection and connection.status == "blocked": connection.status = "removed"
    db.commit()
    return {"status": "unblocked"}

@app.post("/users/{target_id}/report")
def report_user(target_id: int, data: ReportRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if target_id == user.id or not db.get(User, target_id): raise HTTPException(404, "Person not found")
    db.add(Report(reporter_id=user.id, reported_user_id=target_id, reason=data.reason.strip(), details=data.details.strip()))
    db.commit()
    return {"status": "reported", "message": "Thanks. Your report has been recorded."}

# ---------------- Real-time WebRTC signaling + chat ----------------
class ConnectionManager:
    def __init__(self): self.rooms: Dict[int, Dict[int, WebSocket]] = {}
    async def connect(self, connection_id, user_id, websocket):
        await websocket.accept(); room = self.rooms.setdefault(connection_id, {}); peers = list(room.keys()); room[user_id] = websocket; return peers
    def disconnect(self, connection_id, user_id):
        room = self.rooms.get(connection_id)
        if not room: return
        room.pop(user_id, None)
        if not room: self.rooms.pop(connection_id, None)
    async def send_to_peer(self, connection_id, sender_id, payload):
        for uid, socket in list(self.rooms.get(connection_id, {}).items()):
            if uid != sender_id:
                try: await socket.send_json(payload)
                except Exception: pass
    async def send_to_all(self, connection_id, payload):
        for socket in list(self.rooms.get(connection_id, {}).values()):
            try: await socket.send_json(payload)
            except Exception: pass
manager = ConnectionManager()

def websocket_user(token: str, db: Session):
    try: return db.get(User, decode_token(token))
    except HTTPException: return None

@app.websocket("/ws/connection/{connection_id}")
async def connection_socket(websocket: WebSocket, connection_id: int):
    token = websocket.query_params.get("token")
    if not token: await websocket.close(code=1008); return
    db = next(get_db())
    try:
        user = websocket_user(token, db); connection = db.get(Connection, connection_id)
        if not user or not connection or user.id not in (connection.requester_id, connection.receiver_id) or connection.status not in ("connected", "calling"): await websocket.close(code=1008); return
        peer_id = connection.receiver_id if connection.requester_id == user.id else connection.requester_id
        if is_blocked(db, user.id, peer_id): await websocket.close(code=1008); return
        peers = await manager.connect(connection_id, user.id, websocket)
        presence.touch(user.id)
        await websocket.send_json({"type": "room_state", "user_id": user.id, "peer_id": peer_id, "peers": peers})
        if peers: await manager.send_to_peer(connection_id, user.id, {"type": "peer_joined", "user_id": user.id})
        while True:
            message = await websocket.receive_json(); msg_type = message.get("type")
            presence.touch(user.id)
            if msg_type in {"offer", "answer", "ice-candidate"}:
                await manager.send_to_peer(connection_id, user.id, {"type": msg_type, "from": user.id, "data": message.get("data")})
            elif msg_type == "chat":
                text = str(message.get("text", "")).strip()
                if text and len(text) <= 1000: await manager.send_to_all(connection_id, {"type": "chat", "from": user.id, "text": text})
            elif msg_type == "hangup":
                record_history(db, user.id, peer_id, "call_ended", connection.id); db.commit()
                await manager.send_to_peer(connection_id, user.id, {"type": "hangup", "from": user.id})
    except WebSocketDisconnect:
        manager.disconnect(connection_id, user.id if 'user' in locals() and user else -1)
        if 'user' in locals() and user: await manager.send_to_all(connection_id, {"type": "peer_left", "user_id": user.id})
    except Exception:
        if 'user' in locals() and user: manager.disconnect(connection_id, user.id)
    finally: db.close()
