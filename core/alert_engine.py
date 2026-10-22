"""Tracking alert engine: stale / offline GPS, delay, stoppage and geofence approach.

Thresholds come from config/tracking.json. The engine runs when a driver posts a location, when a
manager's live map polls, and from `flask tracking check` (for a cron job). We do not use a background
thread: gunicorn runs several workers and the free host sleeps, so a thread would run twice or not at all.

Each alert is one row per trip and kind (e.g. stop_20). When the cause goes away it is marked resolved;
if it comes back, the same row is opened again. So a manager never gets the same alert twice in a row.
"""
from datetime import datetime

from .db import now
from .tracking import config, haversine_m, parse, sync_with_transfers

# Out of scope this round: SMS / WhatsApp / email / push. Add a sender here once its key is in .env.
# Each sender gets the alert dict. In-app needs nothing: the bell and the live map read tracking_alerts.
EXTRA_CHANNELS = []


def notify(alert):
    for send in EXTRA_CHANNELS:
        try:
            send(alert)
        except Exception:
            pass                                    # a failed SMS must never stop tracking


def _open(con, trip_id, kind, level, message, at):
    """Open (or re-open) one alert. Returns True if it is new for the manager."""
    cur = con.execute("""INSERT INTO tracking_alerts (trip_id, kind, level, message, created_at) VALUES (?,?,?,?,?)
                         ON CONFLICT(trip_id, kind, level) DO UPDATE SET resolved_at=NULL, message=excluded.message,
                         created_at=excluded.created_at WHERE tracking_alerts.resolved_at IS NOT NULL""",
                      (trip_id, kind, level, message, at))
    if cur.rowcount:
        notify({"trip_id": trip_id, "kind": kind, "level": level, "message": message, "created_at": at})
    return bool(cur.rowcount)


def _close(con, trip_id, kinds, at):
    con.execute(f"UPDATE tracking_alerts SET resolved_at=? WHERE trip_id=? AND resolved_at IS NULL "
                f"AND kind IN ({','.join('?' * len(kinds))})", (at, trip_id, *kinds))


def check_trip(con, t, cfg=None, at=None):
    """Check one trip. `at` (a datetime) lets tests move the clock. Returns the kinds of newly opened alerts."""
    cfg, at = cfg or config(), at or datetime.now()
    stamp, tid, new = now(at), t["id"], []
    who = f"{t['code']}"

    def open_(kind, level, msg):
        if _open(con, tid, kind, level, f"{who}: {msg}", stamp):
            new.append(kind)

    if t["status"] != "active":
        return new                                  # arrived trips wait for receipt; no road alerts

    # --- GPS: stale / offline / no permission / no signal
    seen = parse(t["last_seen_at"])
    age = (at - (seen or parse(t["started_at"]))).total_seconds()
    gps = ["gps_stale", "gps_offline", "gps_denied", "gps_unavailable"]
    if t["gps_state"] == "denied":
        open_("gps_denied", "warn", "driver has not allowed location access")
        keep = "gps_denied"
    elif t["gps_state"] == "unavailable":
        open_("gps_unavailable", "warn", "driver's phone has no GPS signal")
        keep = "gps_unavailable"
    elif age >= cfg["offline_after_s"]:
        open_("gps_offline", "danger", f"no location for {int(age // 60)} min {int(age % 60)} s" if seen else "no GPS fix since the trip started")
        keep = "gps_offline"
    elif age >= cfg["stale_after_s"] and seen:
        open_("gps_stale", "warn", f"location is {int(age)} s old")
        keep = "gps_stale"
    else:
        keep = None
    _close(con, tid, [k for k in gps if k != keep], stamp)

    # --- delay: predicted arrival (now + straight-line time left) vs the ETA given at start
    if t["eta_at"] and t["dest_lat"] is not None:
        left_s = 0.0
        if t["last_lat"] is not None:
            left_s = haversine_m(t["last_lat"], t["last_lng"], t["dest_lat"], t["dest_lng"]) / (cfg["avg_speed_kmh"] / 3.6)
        late_min = ((at - parse(t["eta_at"])).total_seconds() + left_s) / 60
        steps = sorted(cfg["delay_min"])
        for i, m in enumerate(steps):
            if late_min >= m:
                open_(f"delay_{m}", "danger" if i == len(steps) - 1 else "warn", f"about {int(late_min)} min late (approximate)")
        _close(con, tid, [f"delay_{m}" for m in steps if late_min < m], stamp)

    # --- stoppage: only judged while fixes are fresh (offline means we do not know)
    moved = parse(t["last_moved_at"])
    if moved and seen and (at - seen).total_seconds() < cfg["offline_after_s"]:
        still_min = (at - moved).total_seconds() / 60
        steps = sorted(cfg["stoppage_min"])
        for i, m in enumerate(steps):
            if still_min >= m:
                open_(f"stop_{m}", "danger" if i == len(steps) - 1 else "warn", f"vehicle has not moved for {int(still_min)} min")
        _close(con, tid, [f"stop_{m}" for m in steps if still_min < m], stamp)

    # --- geofence approach: once per trip
    if t["last_lat"] is not None and t["dest_lat"] is not None:
        d = haversine_m(t["last_lat"], t["last_lng"], t["dest_lat"], t["dest_lng"])
        if d <= cfg["approach_m"]:
            open_("approach", "info", f"{int(d)} m from the shop, arriving soon")
    return new


def check_all(con, at=None):
    sync_with_transfers(con)                        # trips closed by hand on the Transfers page get no alerts
    cfg = config()
    return {t["code"]: check_trip(con, t, cfg, at) for t in con.execute("SELECT * FROM trips WHERE status='active'")}
