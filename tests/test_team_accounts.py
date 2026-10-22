"""The 4 team accounts: they log in, roles are right, owner can do everything, admins cannot touch owners."""
import pytest

from conftest import TEAM_PASSWORDS, login
from core import db as dbm

TEAM = {"anand@fleetflow.in": ("t-anand-1", "owner"), "akshat@fleetflow.in": ("t-akshat-1", "manager"),
        "saavya@fleetflow.in": ("t-saavya-1", "manager"), "anurag@fleetflow.in": ("t-anurag-1", "staff")}
ADMIN = ("admin@fleetflow.in", "admin123")


@pytest.mark.parametrize("email", TEAM)
def test_team_member_logs_in_and_sees_dashboard(tapp, email):
    pw, role = TEAM[email]
    c = login(tapp, (email, pw))
    html = c.get("/").get_data(as_text=True)
    assert c.get("/").status_code == 200 and f"· {role}" in html


def test_existing_demo_logins_untouched(tapp):
    for who in (ADMIN, ("manager@fleetflow.in", "manager123"), ("staff@fleetflow.in", "staff123")):
        assert login(tapp, who).get("/").status_code == 200


def test_owner_has_admin_and_manager_screens(tapp):
    c = login(tapp, ("anand@fleetflow.in", "t-anand-1"))
    for path in ("/users", "/settings", "/tracking", "/tracking/trips", "/fleet/"):
        assert c.get(path).status_code == 200, path
    assert '<option value="owner"' in c.get("/users").get_data(as_text=True)


def test_staff_and_managers_keep_their_limits(tapp):
    assert login(tapp, ("anurag@fleetflow.in", "t-anurag-1")).get("/users").status_code == 302
    m = login(tapp, ("akshat@fleetflow.in", "t-akshat-1"))
    assert m.get("/users").status_code == 302 and m.get("/tracking").status_code == 200


def test_admin_cannot_touch_owner_or_make_owners(tapp):
    con = dbm.connect()
    owner = con.execute("SELECT id FROM users WHERE email='anand@fleetflow.in'").fetchone()["id"]
    a = login(tapp, ADMIN)
    assert '<option value="owner"' not in a.get("/users").get_data(as_text=True)
    for form in ({"action": "role", "uid": owner, "role": "staff"}, {"action": "reset", "uid": owner},
                 {"action": "toggle", "uid": owner}):
        a.post("/users", data=form)
    a.post("/users", data={"action": "add", "email": "x@fleetflow.in", "password": "secret1", "role": "owner"})
    r = con.execute("SELECT role, active FROM users WHERE id=?", (owner,)).fetchone()
    assert (r["role"], r["active"]) == ("owner", 1)
    assert con.execute("SELECT 1 FROM users WHERE email='x@fleetflow.in'").fetchone() is None
    assert login(tapp, ("anand@fleetflow.in", "t-anand-1"))                     # password not reset
    con.close()


def test_passwords_come_from_env_or_dotenv_and_missing_means_skip(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SECRET_KEY=x\nTEAM_PW_ANAND=from-file\n")
    monkeypatch.setattr(dbm, "ENV_PATH", str(env))
    for k in TEAM_PASSWORDS:
        monkeypatch.delenv(k)
    assert dbm.team_password("TEAM_PW_ANAND") == "from-file" and dbm.team_password("TEAM_PW_AKSHAT") is None
    monkeypatch.setenv("TEAM_PW_ANAND", "from-env")
    assert dbm.team_password("TEAM_PW_ANAND") == "from-env"
    con = dbm.connect(str(tmp_path / "t.db"))
    con.executescript(dbm.SCHEMA)
    dbm.seed_team(con)
    assert [r[0] for r in con.execute("SELECT email FROM users")] == ["anand@fleetflow.in"]   # no password -> not created
