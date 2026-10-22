"""Shared test setup: a temporary database for every app, no internet calls."""
import os
import tempfile

import pytest
from werkzeug.security import generate_password_hash

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")   # never the dev DB in instance/
os.environ["TRACKING_OSRM_URL"] = ""                                   # straight-line ETA, no OSRM call

DRIVER = ("driver@fleetflow.in", "driver123")
MANAGER = ("manager@fleetflow.in", "manager123")
STAFF = ("staff@fleetflow.in", "staff123")
DRIVER2 = ("driver2@fleetflow.in", "driver2pw")


@pytest.fixture(scope="module")
def tapp():
    """A fresh app + database with a second driver, for the tracking tests."""
    from app import create_app
    from core import db as dbm
    old = dbm.DB_PATH
    dbm.DB_PATH = os.path.join(tempfile.mkdtemp(), "tracking.db")
    app = create_app()
    con = dbm.connect()
    con.execute("INSERT INTO users (name, email, password_hash, role, created_at) VALUES ('Second Driver', ?, ?, 'staff', ?)",
                (DRIVER2[0], generate_password_hash(DRIVER2[1]), dbm.now()))
    con.execute("INSERT INTO drivers (user_id) SELECT id FROM users WHERE email=?", (DRIVER2[0],))
    con.commit()
    con.close()
    yield app
    dbm.DB_PATH = old


def login(app, who):
    c = app.test_client()
    r = c.post("/login", data={"email": who[0], "password": who[1]})
    assert r.status_code == 302 and "/login" not in r.headers["Location"]
    return c


def dispatched_transfer(app):
    """A real transfer taken through pick and dispatch, so it is on the road."""
    from core import db as dbm
    from core.ops import advance_transfer, create_transfer
    con = dbm.connect()
    shop = con.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()["id"]
    prod = con.execute("SELECT id FROM products WHERE qty >= 5 ORDER BY id LIMIT 1").fetchone()["id"]
    tid, _ = create_transfer(con, shop, [(prod, 1)], 1)
    advance_transfer(con, tid, "pick", 1)
    advance_transfer(con, tid, "dispatch", 1)
    con.commit()
    con.close()
    return tid
