"""Speed-ups keep pages the same: compression, hashed static URLs with long cache, twin plan cache."""
import re

from conftest import MANAGER, login


def test_static_urls_hashed_and_long_cached(tapp):
    html = login(tapp, MANAGER).get("/fleet/lab").get_data(as_text=True)
    url = re.search(r'/static/vendor/chart\.umd\.js\?h=[0-9a-f]{10}', html).group(0)
    r = tapp.test_client().get(url)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert "?v=" not in html


def test_unhashed_static_not_long_cached(tapp):
    assert "immutable" not in tapp.test_client().get("/static/css/app.css").headers.get("Cache-Control", "")


def test_html_json_and_static_are_compressed(tapp):
    c = login(tapp, MANAGER)
    for url in ("/fleet/lab", "/api/fleet/live"):
        assert c.get(url, headers={"Accept-Encoding": "gzip"}).headers.get("Content-Encoding") == "gzip", url
    # static files are streamed: flask-compress uses brotli for those (all current browsers send br)
    r = c.get("/static/vendor/leaflet/leaflet.js", headers={"Accept-Encoding": "gzip, br"})
    assert r.headers.get("Content-Encoding") == "br" and len(r.data) < 60_000
    plain = c.get("/fleet/lab")
    assert "Content-Encoding" not in plain.headers and b"Simulation lab" in plain.data      # same page without gzip


def test_twin_plan_cache_gives_same_answer(tapp):
    from core import db as dbm, ops
    from core.policy import get_policy
    con, pol = dbm.connect(), get_policy()[0]
    ops._TWIN_CACHE.clear()
    fresh = ops.twin_advice(con, pol)
    assert ops.twin_advice(con, pol) == fresh and len(ops._TWIN_CACHE) == 1
    con.close()
