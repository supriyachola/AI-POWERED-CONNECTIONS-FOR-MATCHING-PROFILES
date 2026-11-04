"""Candidate discovery. Hard constraints are applied in SQL; ranking happens only on the survivors."""
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import config
from .calls import ACTIVE_ALL
from .deps import people_by_id
from .matching import score_matches
from .models import Block, Connection, DiscoveryAction, FriendRequest, Profile, User, utcnow
from .presence import presence

PREFERRED_GENDERS = {"any", "male", "female", "non_binary"}
LONG_COOLDOWN_ACTIONS = ("skip", "rejected")        # SKIP_COOLDOWN_HOURS
SHORT_COOLDOWN_ACTIONS = ("connected", "completed")  # REPEAT_COOLDOWN_HOURS
# 'reserved' and 'expired' never exclude anyone: an abandoned reservation must not hide a person.


def rolling_window(age: int) -> tuple[int, int]:
    return max(18, age - 3), min(100, age + 2)


def _in_rolling_window(me: Profile, other: Profile) -> bool:
    lo, hi = rolling_window(me.age)
    olo, ohi = rolling_window(other.age)
    return lo <= other.age <= hi and olo <= me.age <= ohi


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.casefold() == b.casefold())


def _location_ok(me: Profile, other: Profile, scope: str) -> bool:
    if scope == "district":
        return _same(me.district, other.district)
    if scope == "state":
        return _same(me.state, other.state) and _same(me.country, other.country)
    return True


def excluded_ids(db: Session, user_id: int) -> set[int]:
    now = utcnow()
    ex = {user_id}
    for a, b in db.execute(select(Block.user_id, Block.blocked_user_id).where(
            or_(Block.user_id == user_id, Block.blocked_user_id == user_id))).all():
        ex.add(b if a == user_id else a)
    long_cut = now - timedelta(hours=config.SKIP_COOLDOWN_HOURS)
    short_cut = now - timedelta(hours=config.REPEAT_COOLDOWN_HOURS)
    for cid, action, ts in db.execute(select(DiscoveryAction.candidate_id, DiscoveryAction.action, DiscoveryAction.created_at)
                                      .where(DiscoveryAction.user_id == user_id,
                                             DiscoveryAction.action.in_(LONG_COOLDOWN_ACTIONS + SHORT_COOLDOWN_ACTIONS))).all():
        if (action in LONG_COOLDOWN_ACTIONS and ts >= long_cut) or (action in SHORT_COOLDOWN_ACTIONS and ts >= short_cut):
            ex.add(cid)
    # Anyone currently in (or being held by) a call/reservation is unavailable.
    for a, b in db.execute(select(Connection.requester_id, Connection.receiver_id)
                           .where(Connection.status.in_(ACTIVE_ALL))).all():
        ex.add(a)
        ex.add(b)
    for a, b in db.execute(select(FriendRequest.requester_id, FriendRequest.receiver_id).where(
            FriendRequest.status.in_(["pending", "accepted"]),
            or_(FriendRequest.requester_id == user_id, FriendRequest.receiver_id == user_id))).all():
        ex.add(b if a == user_id else a)
    ex.discard(user_id)
    ex.add(user_id)
    return ex


def candidate_query(me: Profile, user_id: int, session_gender: str, state_filter: str, excluded: set[int]):
    """SQL-level hard constraints: online, adult, mutual age range, mutual gender/country/state preferences."""
    online = presence.online_ids() - excluded
    q = (select(Profile, User).join(User, User.id == Profile.user_id)
         .where(Profile.user_id.in_(online), Profile.age.is_not(None), Profile.age >= 18,
                Profile.age.between(me.min_age or 18, me.max_age or 100),
                Profile.min_age <= me.age, Profile.max_age >= me.age))
    wanted = [g for g in (session_gender, me.preferred_gender) if g and g != "any"]
    for g in wanted:
        q = q.where(Profile.gender == g)
    # The other person's gender preference must accept me (prefer_not_to_say never satisfies a specific one).
    q = q.where(or_(Profile.preferred_gender.is_(None), Profile.preferred_gender.in_(("", "any")),
                    Profile.preferred_gender == (me.gender or "")))
    for field, pref in ((Profile.country, me.preferred_country), (Profile.state, me.preferred_state)):
        if pref and pref.lower() != "any":
            q = q.where(func.lower(field) == pref.lower())
    for pref_field, mine in ((Profile.preferred_country, me.country), (Profile.preferred_state, me.state)):
        q = q.where(or_(pref_field.is_(None), func.lower(pref_field).in_(("", "any")),
                        func.lower(pref_field) == (mine or "").lower()))
    if state_filter:
        q = q.where(func.lower(Profile.state) == state_filter.lower(), func.lower(Profile.country) == "india")
    return q.limit(300)


def ranked(user: User, db: Session, session_gender: str = "any", location_scope: str = "district", state_filter: str = ""):
    me = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if not me or me.age is None:
        return []
    if session_gender not in PREFERRED_GENDERS:
        session_gender = "any"
    if location_scope not in {"district", "state", "anywhere"}:
        location_scope = "district"

    rows = db.execute(candidate_query(me, user.id, session_gender, state_filter, excluded_ids(db, user.id))).all()
    pool = [(p, u) for p, u in rows if presence.is_online(u.id)]
    if not pool:
        return []

    # Location may widen step by step; the age range, gender, country/state preferences never do.
    # Inside the user's own min/max range we still prefer the tighter rolling age window first.
    scopes = {"district": ["district", "state", "anywhere"], "state": ["state", "anywhere"], "anywhere": ["anywhere"]}[location_scope]
    tiers = [(s, True) for s in scopes] + [("anywhere", False)]
    people = people_by_id(db, {p.user_id for p, _ in pool})
    from .deps import profile_payload  # local import keeps module graph simple
    target = profile_payload(me)
    seen_tiers = set()
    for scope, strict_age in tiers:
        if (scope, strict_age) in seen_tiers:
            continue
        seen_tiers.add((scope, strict_age))
        tier = [p for p, _ in pool if _location_ok(me, p, scope) and (not strict_age or _in_rolling_window(me, p))]
        if tier:
            label = f"{'same ' + scope if scope != 'anywhere' else 'anywhere'} · {'age matched' if strict_age else 'within your age range'} · online"
            scored = score_matches(target, [people[p.user_id] for p in tier])
            for c in scored:
                c["match_tier"] = label
            return scored
    return []
