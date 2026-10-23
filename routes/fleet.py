import json
import os
import threading
import time
from functools import lru_cache

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from core.arbiter import Arbiter, DecisionLog
from core.authz import role_required
from core.db import connect, get_db, log_activity, now
from core.ops import twin_advice, zone_pressure
from core.policy import get_policy
from core.sim import N_ROBOTS, SCENARIOS, ZONE_NAMES, ZONES, make_plan, run_episode

bp = Blueprint("fleet", __name__, url_prefix="/fleet")
STATUS_LABEL = {"idle": "Idle", "busy": "Picking", "charging": "Charging", "maintenance": "In maintenance"}
_twin_busy = threading.Lock()


def _twin_bg():
    try:
        con = connect()
        twin_advice(con, get_policy()[0])
        con.close()
    finally:
        _twin_busy.release()


def _kick_twin():
    """Plan the current floor in a background thread (one at a time); the result lands in ops._TWIN_CACHE."""
    if _twin_busy.acquire(blocking=False):
        threading.Thread(target=_twin_bg, daemon=True).start()


def _advice(con):
    """(pressure, moves, forecast) from the cached plan for this floor state, or forecast None while it is computed."""
    res = twin_advice(con, get_policy()[0], cached_only=True)
    if res is None:
        _kick_twin()
        return zone_pressure(con), [], None
    return res


@bp.route("/")
def index():
    con = get_db()
    robots = con.execute("""SELECT r.*, z.code AS zone, h.code AS home,
                            (SELECT code FROM transfers WHERE robot_id=r.id AND status='picking' ORDER BY id DESC LIMIT 1) AS job
                            FROM robots r JOIN zones z ON z.id=r.zone_id JOIN zones h ON h.id=r.home_zone_id ORDER BY r.id""").fetchall()
    _, kind = get_policy()
    pr, moves, fc = _advice(con)
    history = con.execute("""SELECT m.*, r.code, u.name FROM robot_moves m JOIN robots r ON r.id=m.robot_id
                             LEFT JOIN users u ON u.id=m.user_id ORDER BY m.id DESC LIMIT 10""").fetchall()
    return render_template("fleet/index.html", robots=robots, pr=pr, moves=moves, kind=kind, history=history,
                           labels=STATUS_LABEL, zones=ZONES, fc=fc)


@bp.route("/api/advice")
def api_advice():
    """The AI advice card as HTML once the twin has planned the current floor; {"html": null} while it works."""
    con = get_db()
    _, moves, fc = _advice(con)
    if fc is None:
        return jsonify({"html": None})
    return jsonify({"html": render_template("fleet/_advice.html", moves=moves, fc=fc, kind=get_policy()[1], zones=ZONES)})


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
    out = {"scenario": scenario, "seed": seed, "kind": kind, "length": plan.length, "robots": N_ROBOTS, "zones": ZONES,
           "zone_names": ZONE_NAMES,
           "rules": {"metrics": a.metrics(), "history": a.history, "log": a.log},
           "rl": {"metrics": b.metrics(), "history": b.history, "log": b.log},
           "arbiter": {"history": [], "decisions": []},
           "rules_decisions": ra.decisions, "rl_decisions": rb.decisions}

    def watch(obs, sim):            # same action as arb; only exposes the growing arbiter lane to ?partial=1
        out["arbiter"]["history"] = sim.history
        return arb(obs, sim)
    if (scenario, seed) in _live:   # a background run started by _start: publish progress while the arbiter plays
        _live[(scenario, seed)] = out
    c = run_episode(plan, seed, watch)
    return {**out, "arbiter": {"metrics": c.metrics(), "history": c.history, "decisions": arb.decisions, "counts": arb.counts()}}


_live = {}                          # (scenario, seed) -> payload so far while a background _run computes it
_live_lock = threading.Lock()


def _bg(scenario, seed):
    try:
        _run(scenario, seed)
    finally:
        _live.pop((scenario, seed), None)     # after _run returned, so the lru cache already holds the result


def _start(scenario, seed):
    """Compute (or fetch from cache) one Lab run in a background thread, once per key at a time."""
    with _live_lock:
        if (scenario, seed) in _live:
            return
        _live[(scenario, seed)] = None
    threading.Thread(target=_bg, args=(scenario, seed), daemon=True).start()


_warmed = threading.Event()


@bp.before_app_request
def warm_lab():
    """Each worker computes the Lab's default run (spike, seed 1) and the Fleet page's twin plan in the
    background, at fork or on its first request. Lab results are deterministic, so the cache never goes stale."""
    if not _warmed.is_set() and os.environ.get("LAB_WARMUP") != "0":
        _warmed.set()
        _kick_twin()
        _start("spike", 1)


if hasattr(os, "register_at_fork"):   # gunicorn --preload forks workers from the loaded app: warm each at birth
    os.register_at_fork(after_in_child=warm_lab)


@bp.route("/api/sim")
def api_sim():
    sc = request.args.get("scenario", "spike")
    if sc not in SCENARIOS:
        return jsonify({"error": "unknown scenario"}), 400
    try:
        seed = max(0, min(9999, int(request.args.get("seed", 1))))
    except ValueError:
        seed = 1
    if request.args.get("partial"):
        # rules + RL lanes come back at once; the slow arbiter lane is fetched in pieces with ?since=<frames held>
        key = (sc, seed)
        _start(sc, seed)
        if request.args.get("prefetch"):
            return jsonify({"ok": True})
        for _ in range(25):                    # rules + RL take ~0.1 s; a finished or cached run frees the key
            live = _live.get(key)
            if live is not None or key not in _live:
                break
            time.sleep(0.02)
        if live is not None:
            h = live["arbiter"]["history"][:]
            try:
                since = max(0, int(request.args.get("since", 0)))
            except ValueError:
                since = 0
            if since:
                return jsonify({"done": False, "arbiter_history": h[since:]})
            return jsonify({**live, "arbiter": {"history": h, "decisions": []}, "done": False})
        if key in _live:
            return jsonify({"done": False, "pending": True})
    return jsonify({**_run(sc, seed), "done": True})


@bp.route("/api/pressure")
def api_pressure():
    return jsonify(zone_pressure(get_db()))
