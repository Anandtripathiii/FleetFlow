"""Tracking pages: who can open them, and the GPS limits notice is shown."""
from conftest import DRIVER, MANAGER, STAFF, login


def test_driver_page_for_drivers_only(tapp):
    r = login(tapp, DRIVER).get("/drive")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Keep this page open" in html and "My trip" in html
    for who in (STAFF, MANAGER):
        r = login(tapp, who).get("/drive")
        assert r.status_code == 302 and "/drive" not in r.headers["Location"]


def test_driver_page_needs_login(tapp):
    r = tapp.test_client().get("/drive")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_my_trip_link_only_for_drivers(tapp):
    assert 'href="/drive"' in login(tapp, DRIVER).get("/").get_data(as_text=True)
    assert 'href="/drive"' not in login(tapp, MANAGER).get("/").get_data(as_text=True)


def test_live_map_for_managers_only(tapp):
    r = login(tapp, MANAGER).get("/tracking")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'id="map"' in html and "leaflet.js" in html and "never guessed" in html
    for who in (STAFF, DRIVER):
        assert login(tapp, who).get("/tracking").status_code == 302


def test_live_tracking_link_only_for_managers(tapp):
    assert 'href="/tracking"' in login(tapp, MANAGER).get("/").get_data(as_text=True)
    assert 'href="/tracking"' not in login(tapp, STAFF).get("/").get_data(as_text=True)


def test_live_api_signal_states(tapp):
    from conftest import dispatched_transfer
    d = login(tapp, DRIVER)
    t = d.post("/api/trips", json={"transfer_id": dispatched_transfer(tapp)}).get_json()["trip"]
    m = login(tapp, MANAGER)
    sig = lambda: next(x for x in m.get("/api/fleet/live").get_json()["trips"] if x["id"] == t["id"])["signal"]
    assert sig() == "waiting"                               # started, no fix yet
    d.post(f"/api/trips/{t['id']}/locations", json={"lat": 28.53, "lng": 77.27})
    assert sig() == "live"
    d.post(f"/api/trips/{t['id']}/locations", json={"error": "position_unavailable"})
    assert sig() == "unavailable"
    m.post(f"/api/trips/{t['id']}/cancel", json={})
