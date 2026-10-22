"""App regression: starts, the 3 demo logins work, Fleet and Simulation Lab return 200."""
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")      # never touch instance/fleetflow.db

from app import create_app  # noqa: E402

DEMO = [("admin@fleetflow.in", "admin123"), ("manager@fleetflow.in", "manager123"), ("staff@fleetflow.in", "staff123")]


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.mark.parametrize("email,password", DEMO)
def test_demo_login_and_fleet_pages(app, email, password):
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": password})
    assert r.status_code == 302 and "/login" not in r.headers["Location"]
    for path in ("/fleet/", "/fleet/lab", "/fleet/api/sim?scenario=mixed&seed=1"):
        assert c.get(path).status_code == 200, path
    sim = c.get("/fleet/api/sim?scenario=mixed&seed=1").get_json()
    assert {"rules", "rl", "arbiter", "rl_decisions", "rules_decisions"} <= set(sim)
    assert len(sim["arbiter"]["history"]) == len(sim["rl"]["history"])
    assert len(sim["rl_decisions"]) == len(sim["rules_decisions"]) == len(sim["arbiter"]["decisions"])


def test_wrong_password_stays_on_login(app):
    r = app.test_client().post("/login", data={"email": DEMO[0][0], "password": "nope"})
    assert r.status_code == 200
