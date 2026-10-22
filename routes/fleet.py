import json
import os
import threading
from functools import lru_cache

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from core.arbiter import Arbiter, DecisionLog
from core.authz import role_required
from core.db import get_db, log_activity, now
from core.ops import twin_advice, zone_pressure
from core.policy import get_policy
from core.sim import N_ROBOTS, SCENARIOS, ZONE_NAMES, ZONES, make_plan, run_episode

bp = Blueprint("fleet", __name__, url_prefix="/fleet")
STATUS_LABEL = {"idle": "Idle", "busy": "Picking", "charging": "Charging", "maintenance": "In maintenance"}


@bp.route("/")
def index():
    con = get_db()
    robots = con.execute("""SELECT r.*, z.code AS zone, h.code AS home,
                            (SELECT code FROM transfers WHERE robot_id=r.id AND status='picking' ORDER BY id DESC LIMIT 1) AS job
                            FROM robots r JOIN zones z ON z.id=r.zone_id JOIN zones h ON h.id=r.home_zone_id ORDER BY r.id""").fetchall()
    policy, kind = get_policy()
    pr, moves, fc = twin_advice(con, policy)
    history = con.execute("""SELECT m.*, r.code, u.name FROM robot_moves m JOIN robots r ON r.id=m.robot_id
                             LEFT JOIN users u ON u.id=m.user_id ORDER BY m.id DESC LIMIT 10""").fetchall()
    return render_template("fleet/index.html", robots=robots, pr=pr, moves=moves, kind=kind, history=history,
                           labels=STATUS_LABEL, zones=ZONES, fc=fc)


@bp.route("/apply", methods=["POST"])
@role_required("manager")
def apply():
    con = get_db()
    policy, kind = get_policy()
    _, moves, _ = twin_advice(con, policy)
    chosen = set(request.form.getlist("move"))
    done = 0
    for i, m in enumerate(moves):
        if str(i) not in chosen:
            continue
        zid = con.execute("SELECT id FROM zones WHERE code=?", (m["to"],)).fetchone()["id"]
        con.execute("UPDATE robots SET zone_id=? WHERE id=?", (zid, m["robot_id"]))
        con.execute("INSERT INTO robot_moves (robot_id, from_zone, to_zone, reason, source, user_id, created_at) VALUES (?,?,?,?,?,?,?)",
                    (m["robot_id"], m["from"], m["to"], m["why"], "Twin planner", current_user.id, now()))
        done += 1
    log_activity(con, current_user.id, "Fleet rebalanced", f"{done} robot move(s)")
    con.commit()
    flash(f"{done} robot move(s) applied." if done else "No moves selected.", "success" if done else "info")
    return redirect(url_for("fleet.index"))


@bp.route("/robot/<int:rid>", methods=["POST"])
def robot_action(rid):
    con = get_db()
    act = request.form.get("action") or ("move" if request.form.get("zone") else None)
    r = con.execute("SELECT r.*, z.code AS zone FROM robots r JOIN zones z ON z.id=r.zone_id WHERE r.id=?", (rid,)).fetchone()
    if r is None:
        return redirect(url_for("fleet.index"))
    if act == "maintenance":
        new = "idle" if r["status"] == "maintenance" else "maintenance"
        con.execute("UPDATE robots SET status=?, last_service=CASE WHEN ?='idle' THEN ? ELSE last_service END WHERE id=?",
                    (new, new, now(), rid))
        flash(f"{r['code']} {'is back on the floor' if new == 'idle' else 'sent to maintenance'}.", "success")
    elif act == "charge":
        con.execute("UPDATE robots SET status='charging' WHERE id=?", (rid,))
        flash(f"{r['code']} sent to charge.", "success")
    elif act == "charged":
        con.execute("UPDATE robots SET status='idle', battery=100 WHERE id=?", (rid,))
        flash(f"{r['code']} is fully charged.", "success")
    elif act == "move":
        if not current_user.can("manager"):
            flash("You need manager access to move robots.", "error")
            return redirect(url_for("fleet.index"))
        to = request.form.get("zone")
        z = con.execute("SELECT id FROM zones WHERE code=?", (to,)).fetchone()
        if z and to != r["zone"]:
            con.execute("UPDATE robots SET zone_id=? WHERE id=?", (z["id"], rid))
            con.execute("INSERT INTO robot_moves (robot_id, from_zone, to_zone, reason, source, user_id, created_at) VALUES (?,?,?,?,?,?,?)",
                        (rid, r["zone"], to, "Moved by hand", "Manual", current_user.id, now()))
            flash(f"{r['code']} moved to Zone {to}.", "success")
    elif act == "home":
        con.execute("UPDATE robots SET zone_id=home_zone_id WHERE id=?", (rid,))
        flash(f"{r['code']} sent back to its home zone.", "success")
    log_activity(con, current_user.id, f"Robot {act}", r["code"])
    con.commit()
    return redirect(url_for("fleet.index"))


@bp.route("/all-home", methods=["POST"])
@role_required("manager")
def all_home():
    con = get_db()
    con.execute("UPDATE robots SET zone_id=home_zone_id")
    log_activity(con, current_user.id, "All robots sent home")
    con.commit()
    flash("Every robot is back in its home zone.", "success")
    return redirect(url_for("fleet.index"))


# ------------------------------------------------------------- simulation lab
@bp.route("/lab")
def lab():
    _, kind = get_policy()
    ev = None
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "core", "eval.json")
    if os.path.exists(p):
        with open(p) as f:
            ev = json.load(f)
    return render_template("fleet/lab.html", scenarios=SCENARIOS, kind=kind, ev=ev, zone_names=ZONE_NAMES)


@lru_cache(maxsize=64)
def _run(scenario, seed):
    policy, kind = get_policy()
    import numpy as np
    plan = make_plan(scenario, rng=np.random.default_rng(seed))
    ra, rb = DecisionLog(None, "rules"), DecisionLog(policy, "rl")       # only records; same runs as before
    a = run_episode(plan, seed, ra)
    b = run_episode(plan, seed, rb)
    arb = Arbiter(policy)
    c = run_episode(plan, seed, arb)
    return {"scenario": scenario, "seed": seed, "kind": kind, "length": plan.length, "robots": N_ROBOTS, "zones": ZONES,
            "zone_names": ZONE_NAMES,
            "rules": {"metrics": a.metrics(), "history": a.history, "log": a.log},
            "rl": {"metrics": b.metrics(), "history": b.history, "log": b.log},
            "arbiter": {"metrics": c.metrics(), "history": c.history, "decisions": arb.decisions, "counts": arb.counts()},
            "rules_decisions": ra.decisions, "rl_decisions": rb.decisions}


_warmed = threading.Event()


@bp.before_app_request
def warm_lab():
    """The first request in each worker computes the Lab's default run (spike, seed 1) in the background,
    so the Simulation Lab opens without a ~5 s wait. Results are deterministic, so the cache never goes stale."""
    if not _warmed.is_set() and os.environ.get("LAB_WARMUP") != "0":
        _warmed.set()
        threading.Thread(target=_run, args=("spike", 1), daemon=True).start()


@bp.route("/api/sim")
def api_sim():
    sc = request.args.get("scenario", "spike")
    if sc not in SCENARIOS:
        return jsonify({"error": "unknown scenario"}), 400
    try:
        seed = max(0, min(9999, int(request.args.get("seed", 1))))
    except ValueError:
        seed = 1
    return jsonify(_run(sc, seed))


@bp.route("/api/pressure")
def api_pressure():
    return jsonify(zone_pressure(get_db()))
