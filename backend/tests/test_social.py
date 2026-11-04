from .conftest import accepted_call


def test_block_report_flow(mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    assert a.post(f"/users/{b.id}/block").json()["status"] == "blocked"
    assert a.get("/blocked").json()["blocked"][0]["user_id"] == b.id
    # blocked in both directions: no new contact, no discovery
    assert b.post("/connections", json={"receiver_id": a.id}).status_code == 403
    assert a.post("/connections", json={"receiver_id": b.id}).status_code == 403
    assert a.get("/discover/next").json()["found"] is False and b.get("/discover/next").json()["found"] is False
    assert b.post(f"/connections/{cid}/friend-request").status_code == 403
    assert a.delete(f"/users/{b.id}/block").status_code == 200
    assert a.get("/blocked").json()["blocked"] == []
    assert a.post(f"/users/{a.id}/block").status_code == 400
    assert a.post("/users/99999/block").status_code == 404


def test_report_validation_dedupe_and_auth(client, mk):
    a, b = mk("alice"), mk("bob")
    ok = a.post(f"/users/{b.id}/report", json={"reason": "Spam or scam", "details": "ads"})
    assert ok.status_code == 200
    again = a.post(f"/users/{b.id}/report", json={"reason": "Spam or scam", "details": "ads"})
    assert again.json()["report_id"] == ok.json()["report_id"]
    assert a.post(f"/users/{b.id}/report", json={"reason": "made up"}).status_code == 422
    assert a.post(f"/users/{a.id}/report", json={"reason": "Other"}).status_code == 400
    assert a.post("/users/99999/report", json={"reason": "Other"}).status_code == 404
    assert client.post(f"/users/{b.id}/report", json={"reason": "Other"}).status_code in (401, 403)


def test_friend_request_lifecycle(mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    r = a.post(f"/connections/{cid}/friend-request").json()
    assert r["status"] == "pending"
    assert a.post(f"/connections/{cid}/friend-request").json()["status"] == "pending"   # idempotent
    reqs = b.get("/friend-requests").json()
    assert reqs["incoming"][0]["username"] == "alice" and a.get("/friend-requests").json()["outgoing"][0]["username"] == "bob"
    rid = reqs["incoming"][0]["request_id"]
    assert a.post(f"/friend-requests/{rid}/accept").status_code == 403    # requester can't self-accept
    assert b.post(f"/friend-requests/{rid}/accept").status_code == 200
    assert b.post(f"/friend-requests/{rid}/accept").status_code == 409
    assert [c["username"] for c in a.get("/connections").json()["connections"]] == ["bob"]
    assert a.post(f"/connections/{cid}/friend-request").json()["status"] == "accepted"
    assert b.delete(f"/connections/{cid}").status_code == 200
    assert a.get("/connections").json()["connections"] == []


def test_reject_cancel_and_cooldown(mk):
    a, b = mk("alice"), mk("bob")
    cid = accepted_call(a, b)
    rid = a.post(f"/connections/{cid}/friend-request").json()["request_id"]
    assert b.post(f"/friend-requests/{rid}/reject").status_code == 200
    assert a.post(f"/connections/{cid}/friend-request").status_code == 409   # can't nag after a decline
    # the person who declined may still ask, which re-opens it from their side
    assert b.post(f"/connections/{cid}/friend-request").json()["status"] == "pending"
    rid2 = b.get("/friend-requests").json()["outgoing"][0]["request_id"]
    assert a.delete(f"/friend-requests/{rid2}").status_code == 409           # only the sender cancels
    assert b.delete(f"/friend-requests/{rid2}").status_code == 200


def test_object_level_authorisation(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    cid = accepted_call(a, b)
    rid = a.post(f"/connections/{cid}/friend-request").json()["request_id"]
    for call in (lambda: c.post(f"/connections/{cid}/friend-request"), lambda: c.delete(f"/connections/{cid}"),
                 lambda: c.post(f"/connections/{cid}/end"), lambda: c.post(f"/friend-requests/{rid}/accept"),
                 lambda: c.post(f"/friend-requests/{rid}/reject"), lambda: c.delete(f"/friend-requests/{rid}"),
                 lambda: c.delete(f"/friendships/{rid}")):
        assert call().status_code == 404
    assert c.get("/friend-requests").json() == {"incoming": [], "outgoing": []}


def test_history_dedupes_paginates_and_flags(mk):
    a, b, c = mk("alice"), mk("bob"), mk("carol")
    for other in (b, c, b):
        cid = accepted_call(a, other)
        a.post(f"/connections/{cid}/end")
    h = a.get("/history", params={"limit": 10}).json()
    assert [x["username"] for x in h["history"]] == ["bob", "carol"] and h["total"] == 2
    assert len(a.get("/history", params={"limit": 1, "offset": 1}).json()["history"]) == 1
    assert a.get("/history", params={"limit": 1000}).status_code == 422
    a.post(f"/users/{c.id}/block")
    assert next(x for x in a.get("/history").json()["history"] if x["username"] == "carol")["is_blocked"] is True
    assert c.get("/history").json()["history"][0]["username"] == "alice"


def test_public_person_hides_private_preferences(mk):
    a, b = mk("alice", min_age=20, max_age=40, preferred_gender="male"), mk("bob")
    person = b.get("/discover/next").json()
    assert person["found"] is False  # alice prefers men, bob is female-registered
    p = a.get("/me").json()["profile"]
    assert p["min_age"] == 20
    c = mk("carl", gender="male")
    found = a.get("/discover/next").json()["person"]
    assert "min_age" not in found and "preferred_gender" not in found and c.id == found["user_id"]
