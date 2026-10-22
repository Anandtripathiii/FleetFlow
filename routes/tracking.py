"""Live tracking pages: the driver page (/drive), the manager live map and trip history."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from core import tracking as tr
from core.authz import role_required
from core.db import get_db

bp = Blueprint("tracking", __name__)


@bp.cli.command("check")
def check_cmd():
    """Run the alert checks once: `flask tracking check` (e.g. every minute from cron)."""
    from core.alert_engine import check_all
    from core.db import connect
    con = connect()
    out = check_all(con)
    con.commit()
    print({k: v for k, v in out.items() if v} or "no new alerts")


@bp.route("/tracking")
@role_required("manager")
def live():
    return render_template("tracking/live.html", cfg=tr.config())


@bp.route("/drive")
def drive():
    con = get_db()
    d = tr.driver_of(con, current_user.id)
    if d is None:
        flash("The driver page is only for drivers.", "error")
        return redirect(url_for("main.dashboard"))
    trip = con.execute(tr.TRIP_SQL + " WHERE p.driver_id=? AND p.status IN ('active','arrived') ORDER BY p.id DESC LIMIT 1",
                       (d["id"],)).fetchone()
    vehicles = con.execute("SELECT id, code, plate FROM vehicles WHERE active=1 ORDER BY code").fetchall()
    return render_template("tracking/drive.html", trip=trip, available=tr.available_transfers(con), vehicles=vehicles,
                           kinds=tr.ISSUE_KINDS)


TRIP_STATUS = {"active": ("On the way", "b-info"), "arrived": ("Arrived", "b-warn"),
               "completed": ("Completed", "b-ok"), "cancelled": ("Cancelled", "")}


@bp.route("/tracking/trips")
@role_required("manager")
def trips():
    status = request.args.get("status", "")
    where, args = ("WHERE p.status=?", (status,)) if status in TRIP_STATUS else ("", ())
    rows = get_db().execute(tr.TRIP_SQL + f" {where} ORDER BY p.id DESC LIMIT 200", args).fetchall()
    return render_template("tracking/trips.html", rows=rows, status=status, labels=TRIP_STATUS)


@bp.route("/tracking/trips/<int:tid>")
def trip(tid):
    con = get_db()
    try:
        if not current_user.can("manager"):
            tr.own_trip(con, tid, current_user.id)       # drivers see only their own trips
        d = tr.trip_detail(con, tid)
    except tr.TrackingError as e:
        flash(str(e) if e.status == 404 else "You can only see your own trips.", "error")
        return redirect(url_for("main.dashboard"))
    pts = d["track"]
    km = sum(tr.haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])) / 1000
    timeline = sorted([(e["created_at"], "event", e) for e in d["events"]] +
                      [(a["created_at"], "alert", a) for a in d["alerts"]], key=lambda x: x[0])
    fence = con.execute("""SELECT g.* FROM geofences g JOIN transfers t ON t.to_shop_id=g.shop_id
                           WHERE t.id=?""", (d["trip"]["transfer_id"],)).fetchone()
    return render_template("tracking/trip.html", d=d, t=d["trip"], km=km, timeline=timeline, labels=TRIP_STATUS,
                           fence=dict(fence) if fence else None)
