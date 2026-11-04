from datetime import timedelta

from app.database import SessionLocal
from app.models import DiscoveryAction, utcnow
from .conftest import accepted_call


def nxt(u, **params):
    return u.get("/discover/next", params=params).json()


def test_explicit_age_range_is_never_relaxed(mk):
    me = mk("alice", age=30, min_age=28, max_age=34)
    mk("young", age=21)
    mk("old", age=60)
    assert nxt(me)["found"] is False
    ok = mk("peer", age=33)
    assert nxt(me)["person"]["user_id"] == ok.id


def test_age_preference_is_mutual(mk):
    me = mk("alice", age=30)
    mk("picky", age=30, min_age=40, max_age=60)       # picky wants 40+; I'm 30
    assert nxt(me)["found"] is False


def test_own_gender_preference_and_session_gender(mk):
    me = mk("alice", gender="female", preferred_gender="male")
    mk("fran", gender="female")
    assert nxt(me)["found"] is False
    m = mk("mark", gender="male")
    assert nxt(me)["person"]["user_id"] == m.id
    me.post(f"/discover/{m.id}/skip")
    other = mk("sam", gender="male")
    assert nxt(me, gender="female")["found"] is False          # session filter AND saved preference both hold
    assert nxt(me, gender="male")["person"]["user_id"] == other.id


def test_other_persons_gender_preference_is_respected(mk):
    me = mk("alice", gender="female")
    mk("carl", gender="male", preferred_gender="male")
    assert nxt(me)["found"] is False


def test_preferred_state_is_hard(mk):
    me = mk("alice", preferred_state="Kerala")
    mk("blr", state="Karnataka")
    assert nxt(me)["found"] is False
    k = mk("kochi", state="Kerala", district="Ernakulam")
    assert nxt(me)["person"]["user_id"] == k.id


def test_location_widens_but_never_beyond_age_range(mk):
    me = mk("alice", age=30, district="Bengaluru Urban", min_age=25, max_age=40)
    far = mk("mumbai", age=38, state="Maharashtra", district="Mumbai")  # outside +2 window, inside 25-40
    got = nxt(me)["person"]
    assert got["user_id"] == far.id and "within your age range" in got["match_tier"]


def test_abandoned_reservation_does_not_exclude_candidate(client, mk):
    a, b = mk("alice"), mk("bob")
    first = nxt(a)["person"]["user_id"]
    assert first == b.id
    # alice walks away; reservation lapses
    from app.main import svc
    from app.models import Connection
    db = SessionLocal()
    db.get(Connection, nxt_conn(db)).updated_at = utcnow() - timedelta(seconds=120)
    db.commit(); svc.expire_stale(db); db.commit(); db.close()
    assert nxt(a)["person"]["user_id"] == b.id


def nxt_conn(db):
    from sqlalchemy import select
    from app.models import Connection
    return db.scalar(select(Connection.id))


def test_skip_excludes_but_reservation_alone_does_not(mk):
    a, b = mk("alice"), mk("bob")
    assert nxt(a)["person"]["user_id"] == b.id
    a.post(f"/discover/{b.id}/skip")
    assert nxt(a)["found"] is False


def test_skip_cooldown_expires(mk):
    a, b = mk("alice"), mk("bob")
    a.post(f"/discover/{b.id}/skip")
    db = SessionLocal()
    for row in db.query(DiscoveryAction).filter_by(action="skip"):
        row.created_at = utcnow() - timedelta(days=8)
    db.commit(); db.close()
    assert nxt(a)["person"]["user_id"] == b.id


def test_reserved_candidate_unavailable_to_others_and_busy_user_not_matched(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    accepted_call(a, b)
    assert nxt(c)["found"] is False        # everyone else is in a call


def test_offline_users_are_not_matched(mk):
    from app.presence import presence
    a, b = mk("alice"), mk("bob")
    presence.mark_offline(b.id)
    assert nxt(a)["found"] is False


def test_adult_only_and_age_required(client, mk):
    r = client.post("/auth/register", json={"username": "kid", "password": "Passw0rd!x", "age": 17, "gender": "male"})
    assert r.status_code == 422
    g = client.post("/auth/guest").json()
    h = {"Authorization": f"Bearer {g['token']}"}
    assert client.get("/discover/next", headers=h).status_code == 409   # guest must provide age first
