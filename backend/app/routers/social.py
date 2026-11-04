from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, desc, or_, select
from sqlalchemy.orm import Session

from .. import calls as svc
from ..deps import current_user_id, db_call, people_by_id, require_user
from ..models import Block, Connection, FriendRequest, InteractionHistory, Report, User, utcnow
from ..ratelimit import limiter
from ..realtime import dispatch, hub
from ..schemas import ReportRequest

router = APIRouter()
REREQUEST_AFTER_DECLINE = timedelta(days=7)


def _ensure_user(db: Session, user_id: int) -> User:
    other = db.get(User, user_id)
    if not other:
        raise HTTPException(404, "Person not found")
    return other


# ------------------------------ friends ------------------------------
@router.post("/connections/{connection_id}/friend-request")
async def send_friend_request(connection_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        require_user(db, uid)
        conn = svc.get_for_participant(db, uid, connection_id)
        other = svc.other_party(conn, uid)
        if svc.is_blocked(db, uid, other):
            raise HTTPException(403, "You cannot send a request to this person.")
        fr = svc.friendship(db, uid, other)
        notify = False
        if fr is None:
            fr = FriendRequest(requester_id=uid, receiver_id=other, status="pending")
            db.add(fr)
            db.flush()
            notify = True
        elif fr.status == "accepted":
            return fr.id, "accepted", "You're already friends.", other, False
        elif fr.status == "pending" and fr.receiver_id == uid:   # they asked first: accept
            fr.status = "accepted"
            return fr.id, "accepted", "You're now friends!", other, True
        elif fr.status == "pending":
            return fr.id, "pending", "Friend request already sent.", other, False
        else:  # previously declined
            if fr.requester_id == uid and utcnow() - fr.updated_at < REREQUEST_AFTER_DECLINE:
                raise HTTPException(409, "They declined your earlier request. Try again later.")
            fr.requester_id, fr.receiver_id, fr.status = uid, other, "pending"
            notify = True
        return fr.id, "pending", "Friend request sent. They need to accept it.", other, notify
    rid, status, message, other, notify = await db_call(work)
    if notify:
        await hub.notify(other, {"type": "friend_request" if status == "pending" else "friend_accepted", "request_id": rid})
    return {"status": status, "request_id": rid, "message": message}


def _req_rows(db: Session, uid: int, incoming: bool):
    col, other_col = (FriendRequest.receiver_id, FriendRequest.requester_id) if incoming else (FriendRequest.requester_id, FriendRequest.receiver_id)
    rows = db.scalars(select(FriendRequest).where(col == uid, FriendRequest.status == "pending")
                      .order_by(desc(FriendRequest.created_at)).limit(100)).all()
    people = people_by_id(db, {getattr(r, other_col.key) for r in rows})
    return [{"request_id": r.id, **people[getattr(r, other_col.key)], "created_at": r.created_at.isoformat()}
            for r in rows if getattr(r, other_col.key) in people and not svc.is_blocked(db, uid, getattr(r, other_col.key))]


@router.get("/friend-requests")
async def list_friend_requests(uid: int = Depends(current_user_id)):
    return await db_call(lambda db: {"incoming": _req_rows(db, uid, True), "outgoing": _req_rows(db, uid, False)})


def _get_request(db: Session, uid: int, request_id: int) -> FriendRequest:
    fr = db.scalar(select(FriendRequest).where(FriendRequest.id == request_id).with_for_update())
    if not fr or uid not in (fr.requester_id, fr.receiver_id):
        raise HTTPException(404, "Request not found")
    return fr


@router.post("/friend-requests/{request_id}/accept")
async def accept_request(request_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        fr = _get_request(db, uid, request_id)
        if fr.receiver_id != uid:
            raise HTTPException(403, "Only the receiver can accept.")
        if svc.is_blocked(db, fr.requester_id, fr.receiver_id):
            raise HTTPException(403, "You cannot connect with this person.")
        if fr.status != "pending":
            raise HTTPException(409, f"Request is already {fr.status}.")
        fr.status = "accepted"
        return fr.requester_id
    other = await db_call(work)
    await hub.notify(other, {"type": "friend_accepted", "request_id": request_id})
    return {"status": "accepted"}


@router.post("/friend-requests/{request_id}/reject")
async def reject_request(request_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        fr = _get_request(db, uid, request_id)
        if fr.receiver_id != uid:
            raise HTTPException(403, "Only the receiver can decline.")
        if fr.status != "pending":
            raise HTTPException(409, f"Request is already {fr.status}.")
        fr.status = "rejected"
        fr.updated_at = utcnow()
    await db_call(work)
    return {"status": "rejected"}


@router.delete("/friend-requests/{request_id}")
async def cancel_request(request_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        fr = _get_request(db, uid, request_id)
        if fr.requester_id != uid or fr.status != "pending":
            raise HTTPException(409, "Only your own pending request can be cancelled.")
        db.delete(fr)
    await db_call(work)
    return {"status": "cancelled"}


@router.delete("/friendships/{request_id}")
async def remove_friendship(request_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        fr = _get_request(db, uid, request_id)
        if fr.status != "accepted":
            raise HTTPException(409, "You are not friends with this person.")
        db.delete(fr)
    await db_call(work)
    return {"status": "removed"}


@router.delete("/connections/{connection_id}")
async def remove_friend_by_connection(connection_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        conn = svc.get_for_participant(db, uid, connection_id)
        fr = svc.accepted_friend_request(db, uid, svc.other_party(conn, uid))
        if not fr:
            raise HTTPException(409, "You are not friends with this person.")
        db.delete(fr)
    await db_call(work)
    return {"status": "removed"}


@router.get("/connections")
async def list_friends(uid: int = Depends(current_user_id)):
    """Accepted friends (not live calls). `connection_id` is the pair's call row, if any, so the UI can call them."""
    def work(db):
        frs = db.scalars(select(FriendRequest).where(FriendRequest.status == "accepted",
                         or_(FriendRequest.requester_id == uid, FriendRequest.receiver_id == uid))
                         .order_by(desc(FriendRequest.updated_at)).limit(500)).all()
        ids = {fr.receiver_id if fr.requester_id == uid else fr.requester_id for fr in frs}
        people = people_by_id(db, ids)
        pair_rows = {}
        for c in db.scalars(select(Connection).where(or_(
                and_(Connection.requester_id == uid, Connection.receiver_id.in_(ids)),
                and_(Connection.receiver_id == uid, Connection.requester_id.in_(ids))))).all():
            pair_rows[svc.other_party(c, uid)] = c.id
        out = []
        for fr in frs:
            oid = fr.receiver_id if fr.requester_id == uid else fr.requester_id
            if oid in people and not svc.is_blocked(db, uid, oid):
                out.append({**people[oid], "friend_request_id": fr.id, "connection_id": pair_rows.get(oid)})
        return {"connections": out}
    return await db_call(work)


# ------------------------------ history ------------------------------
@router.get("/history")
async def history(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=5000),
                  uid: int = Depends(current_user_id)):
    """One entry per person (most recent call), newest first, paginated."""
    def work(db):
        rows = db.scalars(select(InteractionHistory).where(InteractionHistory.user_id == uid,
                          InteractionHistory.action == "call_started")
                          .order_by(desc(InteractionHistory.created_at)).limit(2000)).all()
        seen, latest = set(), []
        for r in rows:
            if r.other_user_id not in seen:
                seen.add(r.other_user_id)
                latest.append(r)
        page = latest[offset: offset + limit]
        ids = {r.other_user_id for r in page}
        people = people_by_id(db, ids)
        blocked = {b for (b,) in db.execute(select(Block.blocked_user_id).where(Block.user_id == uid, Block.blocked_user_id.in_(ids))).all()}
        friends = {fr.receiver_id if fr.requester_id == uid else fr.requester_id for fr in db.scalars(select(FriendRequest).where(
            FriendRequest.status == "accepted", or_(FriendRequest.requester_id == uid, FriendRequest.receiver_id == uid))).all()}
        out = []
        for r in page:
            p = people.get(r.other_user_id)
            if p:
                out.append({**p, "id": r.id, "connection_id": r.connection_id, "action": r.action,
                            "created_at": r.created_at.isoformat(), "is_friend": r.other_user_id in friends,
                            "is_blocked": r.other_user_id in blocked})
        return {"history": out, "total": len(latest), "limit": limit, "offset": offset}
    return await db_call(work)


# ------------------------------ block / report ------------------------------
@router.get("/blocked")
async def list_blocked(uid: int = Depends(current_user_id)):
    def work(db):
        rows = db.scalars(select(Block).where(Block.user_id == uid).order_by(desc(Block.created_at)).limit(500)).all()
        people = people_by_id(db, {b.blocked_user_id for b in rows})
        return {"blocked": [{"user_id": b.blocked_user_id, "username": people[b.blocked_user_id]["username"],
                             "created_at": b.created_at.isoformat()} for b in rows if b.blocked_user_id in people]}
    return await db_call(work)


@router.post("/users/{target_id}/block")
async def block_user(target_id: int, uid: int = Depends(current_user_id)):
    """Block takes effect immediately: any call between the two is ended and sockets are closed."""
    def work(db):
        require_user(db, uid)
        if target_id == uid:
            raise HTTPException(400, "You cannot block yourself.")
        _ensure_user(db, target_id)
        if not db.scalar(select(Block.id).where(Block.user_id == uid, Block.blocked_user_id == target_id)):
            db.add(Block(user_id=uid, blocked_user_id=target_id))
        changes = svc.end_between(db, uid, target_id, "blocked")
        for fr in db.scalars(select(FriendRequest).where(or_(
                and_(FriendRequest.requester_id == uid, FriendRequest.receiver_id == target_id),
                and_(FriendRequest.requester_id == target_id, FriendRequest.receiver_id == uid)))).all():
            db.delete(fr)
        return changes
    changes = await db_call(work)
    await dispatch(changes)
    return {"status": "blocked"}


@router.delete("/users/{target_id}/block")
async def unblock_user(target_id: int, uid: int = Depends(current_user_id)):
    def work(db):
        db.execute(delete(Block).where(Block.user_id == uid, Block.blocked_user_id == target_id))
    await db_call(work)
    return {"status": "unblocked"}


@router.post("/users/{target_id}/report")
async def report_user(target_id: int, data: ReportRequest, uid: int = Depends(current_user_id)):
    if not limiter.allow(f"report:{uid}", 10, 3600):
        raise HTTPException(429, "Too many reports. Please try again later.")

    def work(db):
        require_user(db, uid)
        if target_id == uid:
            raise HTTPException(400, "You cannot report yourself.")
        _ensure_user(db, target_id)
        recent = db.scalar(select(Report.id).where(Report.reporter_id == uid, Report.reported_user_id == target_id,
                                                   Report.reason == data.reason,
                                                   Report.created_at > utcnow() - timedelta(hours=24)))
        if recent:
            return recent
        r = Report(reporter_id=uid, reported_user_id=target_id, reason=data.reason, details=data.details.strip())
        db.add(r)
        db.flush()
        return r.id
    rid = await db_call(work)
    return {"status": "received", "report_id": rid}
