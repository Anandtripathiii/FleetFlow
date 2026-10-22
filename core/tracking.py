"""Live GPS tracking: trips, locations, issues, arrival and manual receipt.

Shared by the JSON API (routes/tracking_api.py) and the pages. The API takes the same payloads from the
browser, a native app or GPS hardware (field "source"), so those can be added later without changes here.
Rules we never break: no fake positions (no permission = no point stored, the trip is marked 'denied'),
and GPS never confirms receipt on its own. A manager confirms it by hand.
"""
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta

from .db import now
from .ops import OpError, advance_transfer, next_code

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "tracking.json")
ISSUE_KINDS = {"breakdown": "Vehicle breakdown", "accident": "Accident", "traffic": "Heavy traffic",
               "address": "Cannot find address", "shop_closed": "Shop closed", "other": "Other problem"}
GPS_ERRORS = {"permission_denied": "denied", "position_unavailable": "unavailable", "timeout": "unavailable"}
OPEN = ("active", "arrived")


class TrackingError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def config():
    with open(CONFIG_PATH) as f:
        c = json.load(f)
    if "TRACKING_OSRM_URL" in os.environ:          # "" turns routing off (tests, offline demos)
        c["osrm_url"] = os.environ["TRACKING_OSRM_URL"]
    return c


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") if ts else None


def haversine_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def eta_seconds(lat1, lng1, lat2, lng2, cfg=None):
    """(seconds, source). Road time from public OSRM if it answers fast, else straight line at average speed."""
    cfg = cfg or config()
    if cfg.get("osrm_url"):
        url = f"{cfg['osrm_url']}/route/v1/driving/{lng1},{lat1};{lng2},{lat2}?overview=false"
        try:
            with urllib.request.urlopen(url, timeout=cfg["osrm_timeout_s"]) as r:
                return float(json.load(r)["routes"][0]["duration"]), "osrm"
        except Exception:
            pass                                    # OSRM is a free shared server: fall back, label it approximate
    return haversine_m(lat1, lng1, lat2, lng2) / (cfg["avg_speed_kmh"] / 3.6), "approximate"


# ------------------------------------------------------------------ lookups
def driver_of(con, user_id):
    return con.execute("SELECT * FROM drivers WHERE user_id=? AND active=1", (user_id,)).fetchone()


def get_trip(con, tid):
    t = con.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone()
    if t is None:
        raise TrackingError("Trip not found", 404)
    return t


def own_trip(con, tid, user_id):
    """The trip, if this user is its driver. Other drivers get 403."""
    d, t = driver_of(con, user_id), get_trip(con, tid)
    if d is None or t["driver_id"] != d["id"]:
        raise TrackingError("This is not your trip", 403)
    return t


def event(con, tid, kind, detail="", user_id=None):
    con.execute("INSERT INTO trip_events (trip_id, kind, detail, user_id, created_at) VALUES (?,?,?,?,?)",
                (tid, kind, detail, user_id, now()))


def available_transfers(con):
    """Transfers on the road with no open trip: what a driver can start."""
    return con.execute("""SELECT t.id, t.code, t.priority, s.name AS to_name, s.area FROM transfers t JOIN shops s ON s.id=t.to_shop_id
                          WHERE t.status='in_transit' AND NOT EXISTS
                          (SELECT 1 FROM trips p WHERE p.transfer_id=t.id AND p.status IN ('active','arrived'))
                          ORDER BY CASE t.priority WHEN 'urgent' THEN 0 ELSE 1 END, t.id""").fetchall()


# ------------------------------------------------------------------ actions
def start_trip(con, user_id, transfer_id, vehicle_id=None):
    d = driver_of(con, user_id)
    if d is None:
        raise TrackingError("Only drivers can start trips", 403)
    if con.execute("SELECT 1 FROM trips WHERE driver_id=? AND status='active'", (d["id"],)).fetchone():
        raise TrackingError("You already have an active trip", 409)
    t = con.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if t is None:
        raise TrackingError("Transfer not found", 404)
    if t["status"] != "in_transit":
        raise TrackingError("Only dispatched transfers (on the road) can be tracked", 409)
    if con.execute("SELECT 1 FROM trips WHERE transfer_id=? AND status IN ('active','arrived')", (transfer_id,)).fetchone():
        raise TrackingError("This transfer already has an open trip", 409)
    if vehicle_id is not None and not con.execute("SELECT 1 FROM vehicles WHERE id=? AND active=1", (vehicle_id,)).fetchone():
        raise TrackingError("Vehicle not found", 404)
    dest = con.execute("SELECT * FROM geofences WHERE shop_id=?", (t["to_shop_id"],)).fetchone()
    origin = con.execute("SELECT * FROM geofences WHERE shop_id IS ?" if t["from_shop_id"] is None else
                         "SELECT * FROM geofences WHERE shop_id=?", (t["from_shop_id"],)).fetchone()
    eta_at = eta_src = None
    if dest and origin:
        secs, eta_src = eta_seconds(origin["lat"], origin["lng"], dest["lat"], dest["lng"])
        eta_at = now(datetime.now() + timedelta(seconds=secs))
    code = next_code(con, "trips", "TRIP")
    cur = con.execute("""INSERT INTO trips (code, transfer_id, driver_id, vehicle_id, dest_lat, dest_lng, eta_at, eta_source, started_at)
                         VALUES (?,?,?,?,?,?,?,?,?)""", (code, transfer_id, d["id"], vehicle_id, dest and dest["lat"],
                                                         dest and dest["lng"], eta_at, eta_src, now()))
    tid = cur.lastrowid
    event(con, tid, "started", f"Trip for {t['code']} started" + (f", ETA {eta_at[11:16]} ({eta_src})" if eta_at else ""), user_id)
    return get_trip(con, tid)


def _num(p, key, lo, hi, required=True):
    v = p.get(key)
    if v is None and not required:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise TrackingError(f"'{key}' must be a number")
    if not math.isfinite(v) or not lo <= v <= hi:
        raise TrackingError(f"'{key}' out of range")
    return v


def add_locations(con, trip, payload, user_id):
    """One point, a batch ({"points": [...]}) or a GPS error ({"error": "permission_denied"})."""
    if trip["status"] != "active":
        raise TrackingError("Trip is not active", 409)
    if "error" in payload:                          # no position: say so, never invent one
        state = GPS_ERRORS.get(payload["error"])
        if state is None:
            raise TrackingError("Unknown GPS error")
        if trip["gps_state"] != state:
            event(con, trip["id"], "gps_" + state, {"denied": "Driver did not allow location access",
                                                    "unavailable": "Phone could not get a GPS signal"}[state], user_id)
        con.execute("UPDATE trips SET gps_state=? WHERE id=?", (state, trip["id"]))
        return 0
    points = payload.get("points", [payload])
    cfg = config()
    if not isinstance(points, list) or not points or len(points) > cfg["max_batch"]:
        raise TrackingError(f"Send 1 to {cfg['max_batch']} points")
    source = payload.get("source", "browser")
    if source not in ("browser", "native", "device"):
        raise TrackingError("Unknown source")
    rows, received = [], now()
    for p in points:
        if not isinstance(p, dict):
            raise TrackingError("Each point must be an object")
        lat, lng = _num(p, "lat", -90, 90), _num(p, "lng", -180, 180)
        acc, spd, hdg = _num(p, "accuracy", 0, 100000, False), _num(p, "speed", 0, 100, False), _num(p, "heading", 0, 360, False)
        ts = p.get("timestamp")                     # device time in ms; keep server time if missing or in the future
        rec = received
        if ts is not None:
            try:
                dt = datetime.fromtimestamp(float(ts) / 1000)
            except (TypeError, ValueError, OverflowError, OSError):
                raise TrackingError("'timestamp' must be epoch milliseconds")
            rec = now(min(dt, datetime.now()))
        rows.append((trip["id"], lat, lng, acc, spd, hdg, rec, received, source))
    con.executemany("""INSERT INTO trip_locations (trip_id, lat, lng, accuracy_m, speed_mps, heading, recorded_at, received_at, source)
                       VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    last = rows[-1]
    moved = trip["last_lat"] is None or haversine_m(trip["last_lat"], trip["last_lng"], last[1], last[2]) >= cfg["moved_m"]
    con.execute("""UPDATE trips SET last_lat=?, last_lng=?, last_speed=?, last_seen_at=?, gps_state='ok',
                   last_moved_at=CASE WHEN ? THEN ? ELSE COALESCE(last_moved_at, ?) END WHERE id=?""",
                (last[1], last[2], last[4], received, moved, received, received, trip["id"]))
    if trip["gps_state"] in ("denied", "unavailable"):
        event(con, trip["id"], "gps_ok", "GPS signal is back", user_id)
    return len(rows)


def report_issue(con, trip, user_id, kind, note="", lat=None, lng=None):
    if trip["status"] not in OPEN:
        raise TrackingError("Trip is closed", 409)
    if kind not in ISSUE_KINDS:
        raise TrackingError("Unknown issue type")
    note = (note or "").strip()[:500]
    if lat is not None or lng is not None:
        lat, lng = _num({"lat": lat}, "lat", -90, 90), _num({"lng": lng}, "lng", -180, 180)
    cur = con.execute("INSERT INTO driver_issues (trip_id, driver_id, kind, note, lat, lng, created_at) VALUES (?,?,?,?,?,?,?)",
                      (trip["id"], trip["driver_id"], kind, note, lat, lng, now()))
    msg = f"{ISSUE_KINDS[kind]}" + (f": {note}" if note else "")
    event(con, trip["id"], "issue", msg, user_id)
    con.execute("INSERT OR IGNORE INTO tracking_alerts (trip_id, kind, level, message, created_at) VALUES (?,?,?,?,?)",
                (trip["id"], f"issue:{cur.lastrowid}", "danger", f"Driver reported: {msg}", now()))
    return cur.lastrowid


def mark_arrived(con, trip, user_id):
    """Driver says they are there. Receipt still needs a manager (GPS alone never confirms it)."""
    if trip["status"] != "active":
        raise TrackingError("Trip is not active", 409)
    where = "no GPS fix"
    if trip["last_lat"] is not None and trip["dest_lat"] is not None:
        where = f"{haversine_m(trip['last_lat'], trip['last_lng'], trip['dest_lat'], trip['dest_lng']):.0f} m from the shop by GPS"
    con.execute("UPDATE trips SET status='arrived', arrived_at=? WHERE id=?", (now(), trip["id"]))
    event(con, trip["id"], "arrived", f"Driver marked arrived ({where}). Waiting for receipt.", user_id)


def confirm_receipt(con, trip, user_id):
    """Manager confirms the shop received the goods: delivers the transfer and closes the trip."""
    if trip["status"] not in OPEN:
        raise TrackingError("Trip is already closed", 409)
    try:
        advance_transfer(con, trip["transfer_id"], "deliver", user_id)
    except OpError as e:
        raise TrackingError(str(e), 409)
    con.execute("UPDATE trips SET status='completed', completed_at=?, confirmed_by=? WHERE id=?", (now(), user_id, trip["id"]))
    con.execute("UPDATE tracking_alerts SET resolved_at=? WHERE trip_id=? AND resolved_at IS NULL", (now(), trip["id"]))
    event(con, trip["id"], "received", "Manager confirmed receipt. Transfer delivered.", user_id)


def cancel_trip(con, trip, user_id, reason=""):
    if trip["status"] not in OPEN:
        raise TrackingError("Trip is already closed", 409)
    con.execute("UPDATE trips SET status='cancelled', completed_at=? WHERE id=?", (now(), trip["id"]))
    con.execute("UPDATE tracking_alerts SET resolved_at=? WHERE trip_id=? AND resolved_at IS NULL", (now(), trip["id"]))
    event(con, trip["id"], "cancelled", "Trip ended without delivery" + (f": {reason[:200]}" if reason else ""), user_id)


# ------------------------------------------------------------------ reads
TRIP_SQL = """SELECT p.*, t.code AS transfer_code, t.priority, s.name AS to_name, u.name AS driver_name,
              v.code AS vehicle_code, v.plate,
              (SELECT COUNT(*) FROM tracking_alerts a WHERE a.trip_id=p.id AND a.resolved_at IS NULL) AS open_alerts
              FROM trips p JOIN transfers t ON t.id=p.transfer_id JOIN shops s ON s.id=t.to_shop_id
              JOIN drivers d ON d.id=p.driver_id JOIN users u ON u.id=d.user_id LEFT JOIN vehicles v ON v.id=p.vehicle_id"""


def signal(trip, age_s, cfg):
    """What the manager should believe about the position: live, stale, offline, denied, unavailable or waiting."""
    if trip["gps_state"] in ("denied", "unavailable"):
        return trip["gps_state"]
    if age_s is None:
        return "waiting"
    return "live" if age_s < cfg["stale_after_s"] else "stale" if age_s < cfg["offline_after_s"] else "offline"


def trip_json(r, cfg=None):
    d = dict(r)
    seen = parse(r["last_seen_at"])
    d["age_s"] = int((datetime.now() - seen).total_seconds()) if seen else None
    d["signal"] = signal(r, d["age_s"], cfg or config())
    if r["last_lat"] is not None and r["dest_lat"] is not None:
        d["dest_m"] = round(haversine_m(r["last_lat"], r["last_lng"], r["dest_lat"], r["dest_lng"]))
    return d


def live_fleet(con):
    cfg = config()
    trips = [trip_json(r, cfg) for r in con.execute(TRIP_SQL + " WHERE p.status IN ('active','arrived') ORDER BY p.id")]
    fences = [dict(r) for r in con.execute("SELECT id, name, kind, lat, lng, radius_m FROM geofences")]
    alerts = [dict(r) for r in con.execute("""SELECT a.*, p.code AS trip_code FROM tracking_alerts a JOIN trips p ON p.id=a.trip_id
                                              WHERE a.resolved_at IS NULL ORDER BY a.id DESC LIMIT 50""")]
    return {"server_time": now(), "trips": trips, "geofences": fences, "alerts": alerts}


def trip_detail(con, tid):
    r = con.execute(TRIP_SQL + " WHERE p.id=?", (tid,)).fetchone()
    if r is None:
        raise TrackingError("Trip not found", 404)
    return {"trip": trip_json(r),
            "events": [dict(e) for e in con.execute("SELECT * FROM trip_events WHERE trip_id=? ORDER BY id", (tid,))],
            "issues": [dict(e) for e in con.execute("SELECT * FROM driver_issues WHERE trip_id=? ORDER BY id", (tid,))],
            "alerts": [dict(e) for e in con.execute("SELECT * FROM tracking_alerts WHERE trip_id=? ORDER BY id", (tid,))],
            "track": [[p["lat"], p["lng"], p["recorded_at"]] for p in
                      con.execute("SELECT lat, lng, recorded_at FROM trip_locations WHERE trip_id=? ORDER BY id", (tid,))]}
