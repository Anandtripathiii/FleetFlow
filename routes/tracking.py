"""Live tracking pages: the driver page (/drive), the manager live map and trip history."""
from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user

from core import tracking as tr
from core.db import get_db

bp = Blueprint("tracking", __name__)


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
