"""Alert engine: stale/offline, delay 10/30, stoppage 10/20/30, approach, no repeats, re-open, in-app bell."""
from datetime import datetime, timedelta

import pytest

from conftest import DRIVER, MANAGER, STAFF, dispatched_transfer, login
from core import alert_engine as ae
from core import db as dbm
from core.tracking import config

T0 = datetime(2026, 10, 22, 10, 0, 0)
ts = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def trip(tapp):
    """An active trip far from its shop, last fix and last move at T0, ETA well in the future."""
    m = login(tapp, MANAGER)
    r = login(tapp, DRIVER).post("/api/trips", json={"transfer_id": dispatched_transfer(tapp)})
    tid = r.get_json()["trip"]["id"]
    con = dbm.connect()
    con.execute("""UPDATE trips SET gps_state='ok', last_lat=28.40, last_lng=77.10, last_seen_at=?, last_moved_at=?,
                   started_at=?, eta_at=? WHERE id=?""", (ts(T0), ts(T0), ts(T0), ts(T0 + timedelta(hours=3)), tid))
    con.commit()
    yield con, tid
    con.close()
    m.post(f"/api/trips/{tid}/cancel", json={})


def run(con, tid, at, **cfg):
    c = {**config(), **cfg}
    out = ae.check_trip(con, con.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone(), c, at)
    con.commit()
    return out


def open_kinds(con, tid):
    return {r["kind"]: r["level"] for r in con.execute("SELECT kind, level FROM tracking_alerts WHERE trip_id=? AND resolved_at IS NULL", (tid,))}


def test_fresh_fix_no_alerts(trip):
    con, tid = trip
    assert run(con, tid, T0 + timedelta(seconds=10)) == [] and open_kinds(con, tid) == {}


def test_stale_then_offline_then_back(trip):
    con, tid = trip
    assert run(con, tid, T0 + timedelta(seconds=35)) == ["gps_stale"]
    assert open_kinds(con, tid) == {"gps_stale": "warn"}
    assert run(con, tid, T0 + timedelta(seconds=65)) == ["gps_offline"]
    assert open_kinds(con, tid) == {"gps_offline": "danger"}            # offline replaces stale
    con.execute("UPDATE trips SET last_seen_at=? WHERE id=?", (ts(T0 + timedelta(seconds=70)), tid))
    assert run(con, tid, T0 + timedelta(seconds=75)) == [] and open_kinds(con, tid) == {}


def test_no_repeats_but_reopens(trip):
    con, tid = trip
    at = T0 + timedelta(seconds=40)
    assert run(con, tid, at) == ["gps_stale"]
    assert run(con, tid, at + timedelta(seconds=5)) == []               # same problem: no second alert
    con.execute("UPDATE trips SET last_seen_at=? WHERE id=?", (ts(at), tid))
    run(con, tid, at + timedelta(seconds=1))                            # fix is back: resolved
    assert run(con, tid, at + timedelta(seconds=40)) == ["gps_stale"]   # stale again: re-opened
    assert con.execute("SELECT COUNT(*) FROM tracking_alerts WHERE trip_id=? AND kind='gps_stale'", (tid,)).fetchone()[0] == 1


def test_thresholds_come_from_config(trip):
    con, tid = trip
    assert run(con, tid, T0 + timedelta(seconds=10), stale_after_s=5) == ["gps_stale"]


@pytest.mark.parametrize("minutes,expect", [(9, {}), (11, {"stop_10": "warn"}),
                                            (21, {"stop_10": "warn", "stop_20": "warn"}),
                                            (31, {"stop_10": "warn", "stop_20": "warn", "stop_30": "danger"})])
def test_stoppage_steps(trip, minutes, expect):
    con, tid = trip
    at = T0 + timedelta(minutes=minutes)
    con.execute("UPDATE trips SET last_seen_at=? WHERE id=?", (ts(at - timedelta(seconds=5)), tid))   # fresh fixes, same spot
    run(con, tid, at)
    assert open_kinds(con, tid) == expect


def test_no_stoppage_judgement_while_offline(trip):
    con, tid = trip
    run(con, tid, T0 + timedelta(minutes=25))
    assert set(open_kinds(con, tid)) == {"gps_offline"}


@pytest.mark.parametrize("late,expect", [(5, {}), (15, {"delay_10": "warn"}), (40, {"delay_10": "warn", "delay_30": "danger"})])
def test_delay_steps(trip, late, expect):
    con, tid = trip
    at = T0 + timedelta(seconds=5)
    con.execute("UPDATE trips SET last_lat=dest_lat, last_lng=dest_lng+0.006, eta_at=? WHERE id=?",   # ~600 m out, ~2 min left
                (ts(at - timedelta(minutes=late)), tid))
    run(con, tid, at)
    assert {k: v for k, v in open_kinds(con, tid).items() if k.startswith("delay")} == expect


def test_geofence_approach_once(trip):
    con, tid = trip
    con.execute("UPDATE trips SET last_lat=dest_lat+0.002, last_lng=dest_lng WHERE id=?", (tid,))  # ~220 m
    at = T0 + timedelta(seconds=5)
    assert run(con, tid, at) == ["approach"] and open_kinds(con, tid)["approach"] == "info"
    assert run(con, tid, at + timedelta(seconds=1)) == []


def test_denied_gps_alert(trip):
    con, tid = trip
    con.execute("UPDATE trips SET gps_state='denied' WHERE id=?", (tid,))
    assert run(con, tid, T0 + timedelta(seconds=5)) == ["gps_denied"]


def test_arrived_trips_get_no_road_alerts(trip):
    con, tid = trip
    con.execute("UPDATE trips SET status='arrived' WHERE id=?", (tid,))
    assert run(con, tid, T0 + timedelta(hours=2)) == []


def test_notify_hook_called_once_and_failures_ignored(trip, monkeypatch):
    con, tid = trip
    seen = []
    monkeypatch.setattr(ae, "EXTRA_CHANNELS", [seen.append, lambda a: 1 / 0])
    run(con, tid, T0 + timedelta(seconds=35))
    run(con, tid, T0 + timedelta(seconds=36))
    assert [a["kind"] for a in seen] == ["gps_stale"]


def test_in_app_bell_for_managers_only(tapp, trip):
    con, tid = trip
    run(con, tid, T0 + timedelta(seconds=65))                           # opens gps_offline
    assert "no location" in login(tapp, MANAGER).get("/alerts").get_data(as_text=True)
    assert "no location" not in login(tapp, STAFF).get("/alerts").get_data(as_text=True)
