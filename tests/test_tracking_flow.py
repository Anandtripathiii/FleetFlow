"""End to end: start trip -> 10 points -> manager sees it -> issue -> alert -> arrived -> manual receipt -> completed.
Plus: history access control, transfer page sync, and the existing site still works."""
import pytest

from conftest import DRIVER, DRIVER2, MANAGER, STAFF, dispatched_transfer, login
from core import db as dbm


def test_full_delivery_flow(tapp):
    d, m = login(tapp, DRIVER), login(tapp, MANAGER)
    tid = dispatched_transfer(tapp)
    con = dbm.connect()
    shop, prod, qty = con.execute("""SELECT t.to_shop_id, i.product_id, i.qty FROM transfers t
                                     JOIN transfer_items i ON i.transfer_id=t.id WHERE t.id=?""", (tid,)).fetchone()
    stock = lambda: (con.execute("SELECT qty FROM shop_stock WHERE shop_id=? AND product_id=?", (shop, prod)).fetchone() or [0])[0]
    before = stock()
    dest = con.execute("SELECT lat, lng FROM geofences WHERE shop_id=?", (shop,)).fetchone()

    # 1. driver starts the trip
    trip = d.post("/api/trips", json={"transfer_id": tid, "vehicle_id": 1}).get_json()["trip"]
    # 2. ten coordinates, ending ~100 m from the shop (test data, posted like a GPS device would)
    start = (28.5355, 77.2732)
    for i in range(10):
        k = (i + 1) / 10 * 0.985
        p = {"lat": start[0] + (dest["lat"] - start[0]) * k, "lng": start[1] + (dest["lng"] - start[1]) * k, "accuracy": 10}
        assert d.post(f"/api/trips/{trip['id']}/locations", json=p).get_json() == {"stored": 1}
    # 3. manager sees the live update
    live = m.get("/api/fleet/live").get_json()
    me = next(x for x in live["trips"] if x["id"] == trip["id"])
    assert me["signal"] == "live" and me["last_lat"] == pytest.approx(p["lat"]) and me["dest_m"] < 500
    assert len(m.get(f"/api/trips/{trip['id']}").get_json()["track"]) == 10
    assert any(a["trip_id"] == trip["id"] and a["kind"] == "approach" for a in live["alerts"])   # geofence alert
    # 4. driver reports an issue -> 5. manager gets the alert (live map and bell)
    d.post(f"/api/trips/{trip['id']}/issues", json={"kind": "shop_closed", "note": "Shutter down"})
    alerts = m.get("/api/fleet/live").get_json()["alerts"]
    assert any("Shutter down" in a["message"] for a in alerts)
    assert "Shutter down" in m.get("/alerts").get_data(as_text=True)
    # 6. driver marks arrived: nothing is confirmed yet
    d.post(f"/api/trips/{trip['id']}/arrived", json={})
    assert con.execute("SELECT status FROM transfers WHERE id=?", (tid,)).fetchone()[0] == "in_transit"
    assert stock() == before
    # 7. manager confirms receipt -> 8. trip completes, transfer delivered, stock moved, alerts closed
    assert m.post(f"/api/trips/{trip['id']}/confirm", json={}).get_json() == {"status": "completed"}
    det = m.get(f"/api/trips/{trip['id']}").get_json()
    assert det["trip"]["status"] == "completed" and det["trip"]["open_alerts"] == 0
    assert con.execute("SELECT status FROM transfers WHERE id=?", (tid,)).fetchone()[0] == "delivered"
    assert stock() == before + qty
    kinds = [e["kind"] for e in det["events"]]
    assert kinds == ["started", "issue", "arrived", "received"]
    assert m.post(f"/api/trips/{trip['id']}/confirm", json={}).status_code == 409   # only once
    # history page: manager and own driver yes, staff and other drivers no
    for c in (m, d):
        html = c.get(f"/tracking/trips/{trip['id']}").get_data(as_text=True)
        assert "Receipt confirmed" in html and "10 points" in html
    for who in (STAFF, DRIVER2):
        r = login(tapp, who).get(f"/tracking/trips/{trip['id']}")
        assert r.status_code == 302 and "/tracking/trips" not in r.headers["Location"]
    assert trip["code"] in m.get("/tracking/trips").get_data(as_text=True)
    assert login(tapp, DRIVER).get("/tracking/trips").status_code == 302                  # list is manager only
    assert f"/tracking/trips/{trip['id']}" in m.get(f"/transfers/{tid}").get_data(as_text=True)
    con.close()


def test_mark_delivered_on_transfer_page_closes_trip(tapp):
    d, m = login(tapp, DRIVER), login(tapp, MANAGER)
    tid = dispatched_transfer(tapp)
    trip = d.post("/api/trips", json={"transfer_id": tid}).get_json()["trip"]
    m.post(f"/transfers/{tid}/deliver")                                                  # the existing manual button
    m.get("/api/fleet/live")                                                             # next poll syncs
    det = m.get(f"/api/trips/{trip['id']}").get_json()
    assert det["trip"]["status"] == "completed" and det["events"][-1]["detail"].startswith("Transfer marked delivered")


@pytest.mark.parametrize("who", [("admin@fleetflow.in", "admin123"), MANAGER, STAFF])
def test_existing_site_still_works(tapp, who):
    c = login(tapp, who)
    for path in ("/", "/inventory/", "/transfers/", "/fleet/", "/fleet/lab", "/alerts"):
        assert c.get(path).status_code == 200, path
