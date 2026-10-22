"""Tracking API: auth required, role checks, happy paths, GPS denied, bad input."""
import pytest

from conftest import DRIVER, DRIVER2, MANAGER, STAFF, dispatched_transfer, login

POSTS = ["/api/trips", "/api/trips/1/locations", "/api/trips/1/issues", "/api/trips/1/arrived",
         "/api/trips/1/confirm", "/api/trips/1/cancel"]


@pytest.fixture()
def trip(tapp):
    """A started trip and the driver's client."""
    d = login(tapp, DRIVER)
    r = d.post("/api/trips", json={"transfer_id": dispatched_transfer(tapp), "vehicle_id": 1})
    assert r.status_code == 200, r.get_json()
    yield r.get_json()["trip"], d
    login(tapp, MANAGER).post(f"/api/trips/{r.get_json()['trip']['id']}/cancel", json={})   # leave no active trip


@pytest.mark.parametrize("url", POSTS)
def test_post_needs_login(tapp, url):
    assert tapp.test_client().post(url, json={}).status_code == 401


@pytest.mark.parametrize("url", ["/api/fleet/live", "/api/trips/1"])
def test_get_needs_login(tapp, url):
    assert tapp.test_client().get(url).status_code == 401


def test_only_drivers_start_trips(tapp):
    tid = dispatched_transfer(tapp)
    for who in (STAFF, MANAGER):
        r = login(tapp, who).post("/api/trips", json={"transfer_id": tid})
        assert r.status_code == 403 and "drivers" in r.get_json()["error"]


def test_manager_endpoints_reject_drivers_and_staff(tapp, trip):
    t, d = trip
    for c in (d, login(tapp, STAFF)):
        assert c.get("/api/fleet/live").status_code == 403
        assert c.post(f"/api/trips/{t['id']}/confirm", json={}).status_code == 403


def test_other_driver_cannot_touch_trip(tapp, trip):
    t, _ = trip
    o = login(tapp, DRIVER2)
    assert o.post(f"/api/trips/{t['id']}/locations", json={"lat": 28.5, "lng": 77.2}).status_code == 403
    assert o.post(f"/api/trips/{t['id']}/issues", json={"kind": "traffic"}).status_code == 403
    assert o.post(f"/api/trips/{t['id']}/arrived", json={}).status_code == 403
    assert o.get(f"/api/trips/{t['id']}").status_code == 403
    assert login(tapp, STAFF).get(f"/api/trips/{t['id']}").status_code == 403


def test_start_trip_happy_path(trip):
    t, _ = trip
    assert t["status"] == "active" and t["code"].startswith("TRIP-") and t["eta_source"] == "approximate"
    assert t["dest_lat"] and t["eta_at"]


def test_start_twice_and_wrong_transfer(tapp, trip):
    t, d = trip
    assert d.post("/api/trips", json={"transfer_id": dispatched_transfer(tapp)}).status_code == 409   # one active trip per driver
    o = login(tapp, DRIVER2)
    assert o.post("/api/trips", json={"transfer_id": t["transfer_id"]}).status_code == 409           # transfer already tracked
    assert o.post("/api/trips", json={"transfer_id": 999999}).status_code == 404
    assert o.post("/api/trips", json={"transfer_id": "1"}).status_code == 400


def test_post_location_single_and_batch(tapp, trip):
    t, d = trip
    assert d.post(f"/api/trips/{t['id']}/locations", json={"lat": 28.53, "lng": 77.27, "accuracy": 8}).get_json() == {"stored": 1}
    pts = [{"lat": 28.53 + i / 1000, "lng": 77.27, "timestamp": 1_700_000_000_000 + i} for i in range(5)]
    assert d.post(f"/api/trips/{t['id']}/locations", json={"points": pts, "source": "device"}).get_json() == {"stored": 5}
    live = login(tapp, MANAGER).get("/api/fleet/live").get_json()
    me = next(x for x in live["trips"] if x["id"] == t["id"])
    assert me["gps_state"] == "ok" and me["last_lat"] == pytest.approx(28.534) and me["age_s"] is not None


def test_gps_denied_stores_no_point(tapp, trip):
    t, d = trip
    r = d.post(f"/api/trips/{t['id']}/locations", json={"error": "permission_denied"})
    assert r.status_code == 200 and r.get_json() == {"stored": 0}
    det = d.get(f"/api/trips/{t['id']}").get_json()
    assert det["trip"]["gps_state"] == "denied" and det["track"] == [] and det["trip"]["last_lat"] is None
    assert any(e["kind"] == "gps_denied" for e in det["events"])
    assert d.post(f"/api/trips/{t['id']}/locations", json={"error": "made_up"}).status_code == 400


@pytest.mark.parametrize("bad", [{"lat": 91, "lng": 0}, {"lat": "x", "lng": 0}, {"lat": 1}, {"lat": float("nan"), "lng": 0},
                                 {"points": []}, {"points": [1, 2]}, {"lat": 1, "lng": 1, "source": "fake"}])
def test_bad_locations_rejected(trip, bad):
    t, d = trip
    assert d.post(f"/api/trips/{t['id']}/locations", json=bad).status_code == 400


def test_json_only(trip):
    t, d = trip
    assert d.post(f"/api/trips/{t['id']}/locations", data={"lat": 1, "lng": 1}).status_code == 415


def test_issue_happy_path_creates_alert(tapp, trip):
    t, d = trip
    r = d.post(f"/api/trips/{t['id']}/issues", json={"kind": "breakdown", "note": "Flat tyre"})
    assert r.status_code == 200
    alerts = login(tapp, MANAGER).get("/api/fleet/live").get_json()["alerts"]
    assert any(a["trip_id"] == t["id"] and "Flat tyre" in a["message"] for a in alerts)
    assert d.post(f"/api/trips/{t['id']}/issues", json={"kind": "aliens"}).status_code == 400


def test_arrived_then_manager_confirms(tapp, trip):
    t, d = trip
    assert d.post(f"/api/trips/{t['id']}/arrived", json={}).get_json() == {"status": "arrived"}
    assert d.post(f"/api/trips/{t['id']}/locations", json={"lat": 1, "lng": 1}).status_code == 409
    det = d.get(f"/api/trips/{t['id']}").get_json()
    assert det["trip"]["status"] == "arrived"                       # GPS/arrival never confirms receipt by itself
    assert login(tapp, MANAGER).post(f"/api/trips/{t['id']}/confirm", json={}).get_json() == {"status": "completed"}
