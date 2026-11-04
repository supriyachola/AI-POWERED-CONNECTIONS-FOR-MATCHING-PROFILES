"""Call lifecycle state machine and service functions (sync, operate on a Session).

reserved -> ringing -> accepted -> connecting -> active -> ended
Side states: rejected, cancelled, expired, failed, disconnected (recoverable).
"""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from . import config
from .models import Block, Connection, DiscoveryAction, FriendRequest, InteractionHistory, User, utcnow
from .presence import presence

TERMINAL = {"ended", "rejected", "cancelled", "expired", "failed"}
LIVE = {"ringing", "accepted", "connecting", "active", "disconnected"}   # a call exists
ACTIVE_ALL = LIVE | {"reserved"}
JOINABLE = {"accepted", "connecting", "active", "disconnected"}          # media room may be entered

TRANSITIONS = {
    "reserved": {"ringing", "cancelled", "expired"},
    "ringing": {"accepted", "rejected", "cancelled", "expired"},
    "accepted": {"connecting", "ended", "failed", "cancelled", "expired"},
    "connecting": {"active", "failed", "ended", "disconnected"},
    "active": {"ended", "disconnected"},
    "disconnected": {"active", "connecting", "ended", "failed"},
}


@dataclass
class Change:
    connection_id: int
    requester_id: int
    receiver_id: int
    old: str
    new: str
    reason: str | None


def transition(conn: Connection, new: str, reason: str | None = None) -> Change:
    if new not in TRANSITIONS.get(conn.status, set()):
        raise HTTPException(409, f"Call cannot move from '{conn.status}' to '{new}'.")
    old, now = conn.status, utcnow()
    conn.status = new
    conn.updated_at = now
    if new == "accepted":
        conn.accepted_at = now
    if new == "active":
        conn.active_at = now
    if new in TERMINAL:
        conn.ended_at, conn.ended_reason = now, reason or new
    return Change(conn.id, conn.requester_id, conn.receiver_id, old, new, reason)


def other_party(conn: Connection, user_id: int) -> int:
    return conn.receiver_id if conn.requester_id == user_id else conn.requester_id


def get_for_participant(db: Session, user_id: int, connection_id: int, lock: bool = False) -> Connection:
    """404 (not 403) for non-participants so connection IDs cannot be probed."""
    q = select(Connection).where(Connection.id == connection_id)
    if lock:
        q = q.with_for_update()
    conn = db.scalar(q)
    if not conn or user_id not in (conn.requester_id, conn.receiver_id):
        raise HTTPException(404, "Call not found")
    return conn


def is_blocked(db: Session, a: int, b: int) -> bool:
    return bool(db.scalar(select(Block.id).where(or_(
        and_(Block.user_id == a, Block.blocked_user_id == b),
        and_(Block.user_id == b, Block.blocked_user_id == a)))))


def busy_call(db: Session, user_id: int) -> Connection | None:
    """The call that occupies this user. 'reserved' only occupies the browsing requester."""
    return db.scalar(select(Connection).where(or_(
        and_(Connection.status.in_(LIVE), or_(Connection.requester_id == user_id, Connection.receiver_id == user_id)),
        and_(Connection.status == "reserved", Connection.requester_id == user_id),
    )).order_by(desc(Connection.updated_at)))


def record_history(db: Session, user_id: int, other_id: int, action: str, connection_id: int | None = None):
    db.add(InteractionHistory(user_id=user_id, other_user_id=other_id, action=action, connection_id=connection_id))


def add_action(db: Session, user_id: int, candidate_id: int, action: str):
    db.add(DiscoveryAction(user_id=user_id, candidate_id=candidate_id, action=action))


def expire_stale(db: Session) -> list[Change]:
    """Move timed-out calls to a terminal state. Returns the changes so callers can notify clients."""
    now = utcnow()
    changes: list[Change] = []
    rows = db.scalars(select(Connection).where(Connection.status.in_(ACTIVE_ALL))).all()
    for c in rows:
        age = (now - (c.updated_at or c.created_at)).total_seconds()
        if c.status == "reserved" and age > config.RESERVED_TTL:
            changes.append(transition(c, "expired", "reservation_timeout"))
            add_action(db, c.requester_id, c.receiver_id, "expired")
        elif c.status == "ringing" and age > config.RINGING_TTL:
            changes.append(transition(c, "expired", "ring_timeout"))
            add_action(db, c.requester_id, c.receiver_id, "expired")
        elif c.status == "accepted" and age > config.ACCEPTED_TTL:
            changes.append(transition(c, "expired", "join_timeout"))
        elif c.status == "connecting" and age > config.CONNECTING_TTL:
            changes.append(transition(c, "failed", "media_timeout"))
        elif c.status == "disconnected" and age > config.DISCONNECTED_GRACE:
            changes.append(transition(c, "ended", "disconnect_timeout"))
        elif c.status in {"active", "connecting"} and age > config.ABANDONED_AFTER and not (
                presence.is_online(c.requester_id) or presence.is_online(c.receiver_id)):
            changes.append(transition(c, "ended" if c.status == "active" else "failed", "abandoned"))
    return changes


def _lock_users(db: Session, *ids: int):
    db.execute(select(User.id).where(User.id.in_(sorted(set(ids)))).order_by(User.id).with_for_update()).all()


def reserve(db: Session, user: User, candidate_id: int) -> Connection:
    """Create the browsing reservation used by /discover/next. Raises 409 if either side is occupied."""
    _lock_users(db, user.id, candidate_id)
    expire_stale(db)
    if busy_call(db, user.id):
        raise HTTPException(409, "You are already connected to someone. End that call before finding another match.")
    if busy_call(db, candidate_id):
        raise HTTPException(409, "That person is busy.")
    pair = _pair_row(db, user.id, candidate_id)
    if pair and pair.status not in TERMINAL:
        raise HTTPException(409, "That person is busy.")
    if pair:  # reuse the single row allowed per direction
        pair.requester_id, pair.receiver_id = user.id, candidate_id
        pair.status, pair.ended_reason, pair.ended_at = "reserved", None, None
        pair.accepted_at = pair.active_at = None
        pair.updated_at = utcnow()
        conn = pair
    else:
        conn = Connection(requester_id=user.id, receiver_id=candidate_id, status="reserved")
        db.add(conn)
        db.flush()
    add_action(db, user.id, candidate_id, "reserved")
    return conn


def _pair_row(db: Session, a: int, b: int) -> Connection | None:
    return db.scalar(select(Connection).where(or_(
        and_(Connection.requester_id == a, Connection.receiver_id == b),
        and_(Connection.requester_id == b, Connection.receiver_id == a))))


def invite(db: Session, user: User, receiver_id: int) -> tuple[Connection, list[Change]]:
    """Requester rings the receiver. Nothing media-related starts until the receiver accepts."""
    if receiver_id == user.id or not db.get(User, receiver_id):
        raise HTTPException(404, "Person not found")
    _lock_users(db, user.id, receiver_id)
    changes = expire_stale(db)
    if is_blocked(db, user.id, receiver_id):
        raise HTTPException(403, "You cannot connect with this person.")
    if not presence.is_online(receiver_id):
        raise HTTPException(409, "This person is no longer online. Find another match.")
    pair = _pair_row(db, user.id, receiver_id)
    mine, theirs = busy_call(db, user.id), busy_call(db, receiver_id)
    if pair and pair.status in LIVE and pair.requester_id == user.id:
        return pair, changes                      # idempotent re-invite
    if mine and (not pair or mine.id != pair.id):
        raise HTTPException(409, "You are already connected to someone. End the call before finding another match.")
    if theirs and (not pair or theirs.id != pair.id):
        raise HTTPException(409, "This person is already talking to someone else. Find another match.")
    if pair and pair.status in LIVE | {"reserved"} and pair.requester_id != user.id:
        raise HTTPException(409, "This person is already calling or reserving you. Check your incoming calls.")
    if pair and pair.status in TERMINAL:
        pair.requester_id, pair.receiver_id = user.id, receiver_id
        pair.status, pair.ended_reason, pair.ended_at = "reserved", None, None
        pair.accepted_at = pair.active_at = None
        pair.updated_at = utcnow()
    elif not pair:
        pair = Connection(requester_id=user.id, receiver_id=receiver_id, status="reserved")
        db.add(pair)
        db.flush()
    changes.append(transition(pair, "ringing"))
    return pair, changes


def accept(db: Session, user: User, connection_id: int) -> Change:
    conn = get_for_participant(db, user.id, connection_id, lock=True)
    if conn.receiver_id != user.id:
        raise HTTPException(403, "Only the person being called can accept.")
    expire_stale(db)
    if is_blocked(db, conn.requester_id, conn.receiver_id):
        raise HTTPException(403, "You cannot connect with this person.")
    ch = transition(conn, "accepted")
    add_action(db, conn.requester_id, conn.receiver_id, "connected")
    record_history(db, conn.requester_id, conn.receiver_id, "call_started", conn.id)
    record_history(db, conn.receiver_id, conn.requester_id, "call_started", conn.id)
    return ch


def reject(db: Session, user: User, connection_id: int) -> Change:
    conn = get_for_participant(db, user.id, connection_id, lock=True)
    if conn.receiver_id != user.id:
        raise HTTPException(403, "Only the person being called can decline.")
    ch = transition(conn, "rejected", "declined")
    add_action(db, conn.requester_id, conn.receiver_id, "rejected")
    return ch


def end(db: Session, user: User, connection_id: int, reason: str = "hangup") -> Change | None:
    """Either participant ends the call. Idempotent for already-finished calls."""
    conn = get_for_participant(db, user.id, connection_id, lock=True)
    if conn.status in TERMINAL:
        return None
    if conn.status == "reserved":
        return transition(conn, "cancelled", "left_reservation")
    if conn.status == "ringing":
        if conn.requester_id == user.id:
            return transition(conn, "cancelled", "cancelled")
        ch = transition(conn, "rejected", "declined")
        add_action(db, conn.requester_id, conn.receiver_id, "rejected")
        return ch
    if conn.status == "disconnected":
        ch = transition(conn, "ended", reason)
    elif conn.status in {"accepted", "connecting", "active"}:
        ch = transition(conn, "ended", reason)
    else:
        raise HTTPException(409, "Call cannot be ended from its current state.")
    if conn.active_at:
        add_action(db, conn.requester_id, conn.receiver_id, "completed")
    return ch


def end_between(db: Session, a: int, b: int, reason: str) -> list[Change]:
    """Force-end any non-terminal call between two users (used by block)."""
    out = []
    for c in db.scalars(select(Connection).where(Connection.status.in_(ACTIVE_ALL), or_(
            and_(Connection.requester_id == a, Connection.receiver_id == b),
            and_(Connection.requester_id == b, Connection.receiver_id == a)))).all():
        new = "cancelled" if c.status in {"reserved", "ringing"} else "ended"
        out.append(transition(c, new, reason))
    return out


def incoming_for(db: Session, user_id: int) -> Connection | None:
    return db.scalar(select(Connection).where(Connection.receiver_id == user_id, Connection.status == "ringing")
                     .order_by(desc(Connection.updated_at)))


def friendship(db: Session, a: int, b: int) -> FriendRequest | None:
    return db.scalar(select(FriendRequest).where(or_(
        and_(FriendRequest.requester_id == a, FriendRequest.receiver_id == b),
        and_(FriendRequest.requester_id == b, FriendRequest.receiver_id == a))))


def accepted_friend_request(db: Session, a: int, b: int) -> FriendRequest | None:
    fr = friendship(db, a, b)
    return fr if fr and fr.status == "accepted" else None
