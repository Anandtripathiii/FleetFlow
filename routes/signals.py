from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from core import tinyfish as tf
from core.db import get_db, log_activity, now, set_setting, setting

bp = Blueprint("signals", __name__, url_prefix="/signals")


@bp.route("/")
def index():
    con = get_db()
    rows = con.execute("""SELECT s.*, z.code AS zone, z.name AS zone_name FROM signals s JOIN zones z ON z.id=s.zone_id
                          ORDER BY s.active DESC, s.id DESC LIMIT 80""").fetchall()
    zones = con.execute("SELECT id, code, name FROM zones ORDER BY code").fetchall()
    effect = {z["code"]: 1.0 for z in zones}
    for r in rows:
        if r["active"] and (not r["expires_at"] or r["expires_at"] > now()):
            effect[r["zone"]] = max(effect[r["zone"]], r["multiplier"])
    last = setting(con, "last_signal_fetch", "")
    checks = con.execute("SELECT * FROM activity WHERE action='Supplier price check' ORDER BY id DESC LIMIT 6").fetchall()
    return render_template("signals/index.html", rows=rows, zones=zones, effect=effect, has_key=bool(tf.api_key(con)),
                           city=setting(con, "city", "New Delhi"), last=last, rules=tf.RULES, queries=tf.DEFAULT_QUERIES,
                           checks=checks, now_s=now())


@bp.route("/fetch", methods=["POST"])
def fetch():
    con = get_db()
    key = tf.api_key(con)
    city = request.form.get("city", "").strip() or setting(con, "city", "New Delhi")
    set_setting(con, "city", city)
    if not key:
        flash("No TinyFish API key yet. Add it in Settings (or set TINYFISH_API_KEY) to fetch live signals.", "error")
        con.commit()
        return redirect(url_for("signals.index"))
    try:
        found = tf.demand_signals(key, city)
    except tf.TinyFishError as e:
        flash(f"TinyFish could not fetch signals: {e}. Your saved signals are still in use.", "error")
        return redirect(url_for("signals.index"))
    zid = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM zones")}
    added = 0
    for s in found:
        if con.execute("SELECT 1 FROM signals WHERE title=? AND created_at >= ?", (s["title"], (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))).fetchone():
            continue
        con.execute("""INSERT INTO signals (zone_id, source, title, detail, url, multiplier, starts_at, expires_at, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""", (zid[s["zone"]], "TinyFish Search", s["title"], s["detail"], s["url"], s["multiplier"],
                                                      now(), (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"), now()))
        added += 1
    set_setting(con, "last_signal_fetch", now())
    log_activity(con, current_user.id, "Fetched TinyFish signals", f"{city}: {added} new")
    con.commit()
    flash(f"TinyFish checked the web for {city}: {added} new signal(s)." if added else "TinyFish found nothing new that matches a demand rule.", "success")
    return redirect(url_for("signals.index"))


@bp.route("/add", methods=["POST"])
def add():
    con = get_db()
    f = request.form
    try:
        mult = float(f.get("multiplier") or 1)
        hours = int(f.get("hours") or 24)
        if not (0.5 <= mult <= 5):
            raise ValueError
    except ValueError:
        flash("Multiplier must be between 0.5 and 5.", "error")
        return redirect(url_for("signals.index"))
    if not f.get("title", "").strip():
        flash("Give the signal a short title.", "error")
        return redirect(url_for("signals.index"))
    con.execute("""INSERT INTO signals (zone_id, source, title, detail, multiplier, starts_at, expires_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""", (int(f["zone_id"]), f"Added by {current_user.name}", f["title"].strip()[:160],
                                                  f.get("detail", "").strip(), mult, now(),
                                                  (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S"), now()))
    log_activity(con, current_user.id, "Signal added", f["title"][:80])
    con.commit()
    flash("Signal added. The fleet advice now takes it into account.", "success")
    return redirect(url_for("signals.index"))


@bp.route("/<int:sid>/toggle", methods=["POST"])
def toggle(sid):
    con = get_db()
    con.execute("UPDATE signals SET active=1-active WHERE id=?", (sid,))
    con.commit()
    return redirect(url_for("signals.index"))


@bp.route("/price-check", methods=["POST"])
def price_check():
    con = get_db()
    key = tf.api_key(con)
    url = request.form.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        flash("Paste a full product link starting with https://", "error")
        return redirect(url_for("signals.index") + "#price")
    if not key:
        flash("Add a TinyFish API key in Settings to run the Web Agent.", "error")
        return redirect(url_for("signals.index") + "#price")
    try:
        result, run_id = tf.run_agent(key, url, tf.PRICE_GOAL)
    except tf.TinyFishError as e:
        flash(f"TinyFish Web Agent could not read that page: {e}", "error")
        return redirect(url_for("signals.index") + "#price")
    if isinstance(result, dict):
        txt = ", ".join(f"{k}: {v}" for k, v in result.items())
    else:
        txt = str(result)[:400]
    log_activity(con, current_user.id, "Supplier price check", f"{url[:120]} -> {txt[:300]}")
    con.commit()
    flash(f"Web Agent result: {txt}", "success")
    return redirect(url_for("signals.index") + "#price")
