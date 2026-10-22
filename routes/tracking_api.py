"""JSON API for live GPS tracking. Every endpoint needs a login (401) and the right role or trip owner (403).

Driver (a staff user with a drivers row, only on their own trip):
  POST /api/trips                      {"transfer_id", "vehicle_id"?}            start a trip
  POST /api/trips/<id>/locations       {"lat","lng","accuracy"?,"speed"?,"heading"?,"timestamp"?}
                                       or {"points": [...], "source": "browser|native|device"}
                                       or {"error": "permission_denied|position_unavailable|timeout"}
  POST /api/trips/<id>/issues          {"kind","note"?,"lat"?,"lng"?}
  POST /api/trips/<id>/arrived         end of the drive (receipt is still manual)
Manager or admin:
  POST /api/trips/<id>/confirm         confirm receipt: delivers the transfer, completes the trip
  POST /api/trips/<id>/cancel          {"reason"?}  (the trip's driver may also cancel)
  GET  /api/fleet/live                 live positions, geofences, open alerts
Manager, admin or the trip's driver:
  GET  /api/trips/<id>                 details, timeline, issues, alerts, GPS track
"""
from functools import wraps

from flask import Blueprint, jsonify, request
from flask_login import current_user

from core import tracking as tr
from core.alert_engine import check_all, check_trip
from core.db import get_db, log_activity

bp = Blueprint("tracking_api", __name__, url_prefix="/api")


def api(manager=False):
    """Login + JSON body + role check, TrackingError -> JSON error, commit on success."""
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not current_user.is_authenticated:
                return jsonify(error="Login required"), 401
            if manager and not current_user.can("manager"):
                return jsonify(error="Manager access required"), 403
            if request.method == "POST" and not request.is_json:
                return jsonify(error="Send JSON (Content-Type: application/json)"), 415
            con = get_db()
            try:
                out = fn(con, *a, **kw)
                con.commit()
                return jsonify(out)
            except tr.TrackingError as e:
                con.rollback()
                return jsonify(error=str(e)), e.status
        return inner
    return deco


def body():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        raise tr.TrackingError("Body must be a JSON object")
    return b


def int_field(b, key, required=True):
    v = b.get(key)
    if v is None and not required:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise tr.TrackingError(f"'{key}' must be a whole number")
    return v


@bp.post("/trips")
@api()
def start(con):
    b = body()
    t = tr.start_trip(con, current_user.id, int_field(b, "transfer_id"), int_field(b, "vehicle_id", False))
    log_activity(con, current_user.id, "Trip started", t["code"])
    return {"trip": dict(t)}


@bp.post("/trips/<int:tid>/locations")
@api()
def locations(con, tid):
    n = tr.add_locations(con, tr.own_trip(con, tid, current_user.id), body(), current_user.id)
    check_trip(con, tr.get_trip(con, tid))
    return {"stored": n}


@bp.post("/trips/<int:tid>/issues")
@api()
def issue(con, tid):
    b = body()
    iid = tr.report_issue(con, tr.own_trip(con, tid, current_user.id), current_user.id, b.get("kind"),
                          b.get("note"), b.get("lat"), b.get("lng"))
    return {"issue_id": iid}


@bp.post("/trips/<int:tid>/arrived")
@api()
def arrived(con, tid):
    tr.mark_arrived(con, tr.own_trip(con, tid, current_user.id), current_user.id)
    return {"status": "arrived"}


@bp.post("/trips/<int:tid>/confirm")
@api(manager=True)
def confirm(con, tid):
    t = tr.get_trip(con, tid)
    tr.confirm_receipt(con, t, current_user.id)
    log_activity(con, current_user.id, "Receipt confirmed", t["code"])
    return {"status": "completed"}


@bp.post("/trips/<int:tid>/cancel")
@api()
def cancel(con, tid):
    t = tr.get_trip(con, tid) if current_user.can("manager") else tr.own_trip(con, tid, current_user.id)
    tr.cancel_trip(con, t, current_user.id, str(body().get("reason") or ""))
    return {"status": "cancelled"}


@bp.get("/fleet/live")
@api(manager=True)
def live(con):
    check_all(con)                                  # each manager poll also runs the alert checks
    return tr.live_fleet(con)


@bp.get("/trips/<int:tid>")
@api()
def detail(con, tid):
    if not current_user.can("manager"):
        tr.own_trip(con, tid, current_user.id)
    return tr.trip_detail(con, tid)
