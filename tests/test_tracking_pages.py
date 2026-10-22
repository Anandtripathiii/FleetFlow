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
