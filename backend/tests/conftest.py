import os
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./test_affinity.db"
os.environ["JWT_SECRET"] = "t" * 48

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from app.presence import presence
from app.ratelimit import limiter
from app.realtime import hub


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    presence._last_seen.clear()
    limiter.hits.clear()
    hub.user_sockets.clear(); hub.rooms.clear(); hub.confirmed.clear()
    with TestClient(app) as c:
        yield c


class User:
    def __init__(self, client, name, age=28, gender="female", **profile):
        self.c, self.name = client, name
        r = client.post("/auth/register", json={"username": name, "password": "Passw0rd!x", "age": age, "gender": gender})
        assert r.status_code == 200, r.text
        self.token, self.id = r.json()["token"], r.json()["user"]["id"]
        if profile:
            self.profile(**profile)

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def profile(self, **kw):
        base = {"bio": "", "looking_for": "", "interests": ["music"], "gender": "female", "country": "India", "state": "Karnataka",
                "city": "Bengaluru", "district": "Bengaluru Urban", "vibe": "", "preferred_gender": "any",
                "preferred_country": "any", "preferred_state": "any", "min_age": 18, "max_age": 100}
        base.update(kw)
        r = self.c.put("/me/profile", json=base, headers=self.h)
        assert r.status_code == 200, r.text

    def get(self, p, **kw): return self.c.get(p, headers=self.h, **kw)
    def post(self, p, **kw): return self.c.post(p, headers=self.h, **kw)
    def delete(self, p, **kw): return self.c.delete(p, headers=self.h, **kw)


@pytest.fixture()
def mk(client):
    def make(name, **kw):
        u = User(client, name, **kw)
        presence.touch(u.id)
        return u
    return make


def accepted_call(a, b):
    """a calls b, b accepts. Returns connection id."""
    r = a.post("/connections", json={"receiver_id": b.id})
    assert r.status_code == 200, r.text
    cid = r.json()["connection_id"]
    assert b.post(f"/connections/{cid}/accept").status_code == 200
    return cid
