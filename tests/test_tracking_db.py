"""Tracking migration: new tables only, safe to run twice, demo driver and geofences."""
import os
import tempfile

from core import db as dbm
from core.tracking_db import SHOP_COORDS, migrate

NEW = {"drivers", "vehicles", "geofences", "trips", "trip_locations", "trip_events", "driver_issues", "tracking_alerts"}


def fresh():
    con = dbm.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    dbm.init_db(con)
    return con


def tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_tables_created_and_old_tables_unchanged():
    con = fresh()
    assert NEW <= tables(con)
    cols = [r[1] for r in con.execute("PRAGMA table_info(shops)")]
    assert "lat" not in cols and "lng" not in cols          # existing tables are not altered


def test_migration_runs_twice_without_duplicates():
    con = fresh()
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in NEW}
    migrate(con)
    migrate(con)
    assert before == {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in NEW}


def test_demo_driver_vehicles_and_geofences():
    con = fresh()
    d = con.execute("SELECT u.role FROM drivers d JOIN users u ON u.id=d.user_id WHERE u.email='driver@fleetflow.in'").fetchone()
    assert d["role"] == "staff"
    assert con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM geofences WHERE kind='shop'").fetchone()[0] == len(SHOP_COORDS)
    assert con.execute("SELECT COUNT(*) FROM geofences WHERE kind='warehouse'").fetchone()[0] == 1


def test_no_demo_data_when_disabled():
    con = dbm.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    dbm.init_db(con, demo=False)
    assert NEW <= tables(con) and con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 0
