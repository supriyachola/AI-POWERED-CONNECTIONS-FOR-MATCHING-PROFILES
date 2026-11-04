import asyncio
import json
import logging
import secrets
import string
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import calls as svc
from . import config
from .auth import create_token, hash_password, verify_password
from .database import SessionLocal, engine, get_db, migrate_schema
from .deps import INDIA_STATES, current_user, public_user, profile_payload
from .discovery import PREFERRED_GENDERS, ranked
from .matching import normalize_interests
from .models import Profile, User
from .presence import presence
from .ratelimit import limiter, rate_limit
from .realtime import dispatch
from .routers import calls as calls_router, rtc as rtc_router, social as social_router
from .schemas import LocationRequest, LoginRequest, ProfileRequest, RegisterRequest

log = logging.getLogger("affinity")
SWEEP_INTERVAL_SECONDS = 5
LOGIN_FAILURE_LIMIT, LOGIN_FAILURE_WINDOW = 5, 15 * 60
_DUMMY_HASH = hash_password("not-a-real-password")  # equalises timing for unknown usernames

migrate_schema()  # TODO(phase 2): replace with Alembic migrations run as an explicit release step


async def _sweeper():
    """Expire stale reservations/rings/joins and tell the affected clients."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            def run():
                db = SessionLocal()
                try:
                    changes = svc.expire_stale(db)
                    db.commit()
                    return changes
                finally:
                    db.close()
            changes = await asyncio.to_thread(run)
            if changes:
                await dispatch(changes)
            presence.sweep()
        except Exception:
            log.exception("sweeper error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_sweeper())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Affinity Plus API", version="0.12.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(calls_router.router)
app.include_router(social_router.router)
app.include_router(rtc_router.router)


# ---------------- Health ----------------
@app.get("/")
def root():
    return {"status": "ok", "service": "affinity-plus-api"}  # warm-up ping target


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Database unavailable")
    return {"status": "ready"}


# ---------------- Authentication ----------------
def make_guest_username(db: Session) -> str:
    alphabet = string.ascii_lowercase + string.digits
    while True:
        username = "guest_" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.scalar(select(User.id).where(User.username == username)):
            return username


@app.post("/auth/register", dependencies=[Depends(rate_limit("register", 10, 3600))])
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    username = data.username.strip()
    if db.scalar(select(User.id).where(User.username == username)):
        raise HTTPException(409, "Username already exists")
    user = User(username=username, password_hash=hash_password(data.password), account_type="registered")
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username already exists")
    db.add(Profile(user_id=user.id, age=data.age, gender=data.gender))
    db.commit()
    db.refresh(user)
    presence.touch(user.id)
    return {"token": create_token(user.id), "user": public_user(user)}


@app.post("/auth/login", dependencies=[Depends(rate_limit("login", 30, 60))])
def login(data: LoginRequest, db: Session = Depends(get_db)):
    username = data.username.strip()
    fail_key = f"loginfail:{username.casefold()}"
    if limiter.count(fail_key, LOGIN_FAILURE_WINDOW) >= LOGIN_FAILURE_LIMIT:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(data.password, user.password_hash if user and user.password_hash else _DUMMY_HASH)
    if not user or not user.password_hash or not ok:
        limiter.record(fail_key)
        raise HTTPException(401, "Invalid username or password")
    limiter.clear(fail_key)
    presence.touch(user.id)
    return {"token": create_token(user.id), "user": public_user(user)}


@app.post("/auth/guest", dependencies=[Depends(rate_limit("guest", 20, 3600))])
def guest_login(db: Session = Depends(get_db)):
    user = User(username=make_guest_username(db), account_type="guest", guest_token=secrets.token_urlsafe(32))
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, age=None, gender="prefer_not_to_say"))
    db.commit()
    db.refresh(user)
    presence.touch(user.id)
    return {"token": create_token(user.id), "user": public_user(user)}


# ---------------- Profile ----------------
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
    db.commit()
    db.refresh(profile)
    return profile_payload(profile)


@app.put("/me/location")
def update_location(data: LocationRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not profile:
        profile = Profile(user_id=user.id)
        db.add(profile)
    profile.city, profile.district = data.city.strip(), data.district.strip()
    profile.state, profile.country = data.state.strip(), data.country.strip()
    db.commit()
    db.refresh(profile)
    return profile_payload(profile)


@app.post("/presence/heartbeat")
def heartbeat(user: User = Depends(current_user), db: Session = Depends(get_db)):
    presence.touch(user.id)
    presence.sweep()
    incoming = svc.incoming_for(db, user.id)  # fallback for clients without the push socket
    payload = None
    if incoming:
        from .routers.calls import _call_payload
        payload = _call_payload(db, incoming, user.id)
    return {"online_count": presence.online_count(), "incoming_call": payload}


# ---------------- Discovery ----------------
@app.get("/discover/next")
def discover_next(gender: str = "any", state: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if gender not in PREFERRED_GENDERS:
        raise HTTPException(422, "Invalid session gender preference.")
    if state and state not in INDIA_STATES:
        raise HTTPException(422, "Invalid India state filter.")
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not profile or profile.age is None:
        raise HTTPException(409, "Complete your 18+ age before matching.")

    svc.expire_stale(db)
    if svc.busy_call(db, user.id):
        db.commit()
        raise HTTPException(409, "You are already connected to someone. End that call before finding another match.")

    for item in ranked(user, db, gender, "district", state):
        try:
            reservation = svc.reserve(db, user, item["user_id"])
            db.commit()
        except HTTPException:
            db.rollback()  # candidate got taken in the meantime: try the next one
            continue
        item["reservation_id"] = reservation.id
        item["is_reserved"] = True
        return {"found": True, "person": item, "reservation_id": reservation.id, "message": None}
    db.commit()
    return {"found": False, "person": None, "reservation_id": None,
            "message": "No one is available right now. Please try again soon."}


@app.post("/discover/{candidate_id}/skip")
def skip(candidate_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if candidate_id == user.id or not db.get(User, candidate_id):
        raise HTTPException(404, "Person not found")
    if svc.is_blocked(db, user.id, candidate_id):
        raise HTTPException(403, "This person is blocked.")
    row = db.scalar(select(svc.Connection).where(
        svc.Connection.requester_id == user.id, svc.Connection.receiver_id == candidate_id,
        svc.Connection.status == "reserved"))
    if row:
        svc.transition(row, "cancelled", "skipped")
    svc.add_action(db, user.id, candidate_id, "skip")
    db.commit()
    return {"status": "skipped"}
