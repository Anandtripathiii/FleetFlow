"""Business rules: stock, transfers, purchase orders, suggestions, alerts and live fleet advice."""
import time
from datetime import datetime, timedelta

import numpy as np

from .db import log_activity, now
from .sim import MOVES, N_ROBOTS, ZONES

_TWIN_CACHE = {}         # floor state -> twin plan; the plan is deterministic for a state, so it never expires


class OpError(Exception):
    """A rule was broken (e.g. not enough stock). Message is shown to the user."""


# ------------------------------------------------------------------ stock moves
def change_warehouse(con, pid, change, reason, ref, uid):
    p = con.execute("SELECT qty, name FROM products WHERE id=?", (pid,)).fetchone()
    if p is None:
        raise OpError("Product not found")
    new = p["qty"] + change
    if new < 0:
        raise OpError(f"Not enough stock of {p['name']} in the warehouse (have {p['qty']}, need {-change})")
    con.execute("UPDATE products SET qty=? WHERE id=?", (new, pid))
    con.execute("INSERT INTO movements (product_id, location, change, balance, reason, ref, user_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, "warehouse", change, new, reason, ref, uid, now()))
    return new


def change_shop(con, shop_id, pid, change, reason, ref, uid, allow_create=True):
    sh = con.execute("SELECT code, name FROM shops WHERE id=?", (shop_id,)).fetchone()
    row = con.execute("SELECT qty FROM shop_stock WHERE shop_id=? AND product_id=?", (shop_id, pid)).fetchone()
    if row is None:
        if not allow_create or change < 0:
            raise OpError(f"{sh['name']} does not stock this product")
        con.execute("INSERT INTO shop_stock (shop_id, product_id, qty, min_qty) VALUES (?,?,0,5)", (shop_id, pid))
        cur = 0
    else:
        cur = row["qty"]
    new = cur + change
    if new < 0:
        name = con.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()["name"]
        raise OpError(f"{sh['name']} only has {cur} of {name}")
    con.execute("UPDATE shop_stock SET qty=? WHERE shop_id=? AND product_id=?", (new, shop_id, pid))
    con.execute("INSERT INTO movements (product_id, location, change, balance, reason, ref, user_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, sh["code"], change, new, reason, ref, uid, now()))
    return new


# -------------------------------------------------------------------- transfers
def next_code(con, table, prefix):
    r = con.execute(f"SELECT code FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    n = int(r["code"].split("-")[1]) + 1 if r else 1
    return f"{prefix}-{n:04d}"


def create_transfer(con, to_shop, items, uid, from_shop=None, priority="normal", note=""):
    items = [(int(p), int(q)) for p, q in items if p and q and int(q) > 0]
    if not items:
        raise OpError("Add at least one product with a quantity")
    if from_shop and int(from_shop) == int(to_shop):
        raise OpError("A shop cannot send stock to itself")
    code = next_code(con, "transfers", "TR")
    con.execute("""INSERT INTO transfers (code, from_shop_id, to_shop_id, status, priority, note, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""", (code, from_shop or None, to_shop, "pending", priority, note, uid, now()))
    tid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.executemany("INSERT INTO transfer_items (transfer_id, product_id, qty) VALUES (?,?,?)", [(tid, p, q) for p, q in items])
    log_activity(con, uid, "Transfer created", f"{code} with {len(items)} item(s)")
    return tid, code


def transfer_items(con, tid):
    return con.execute("""SELECT ti.*, p.name, p.sku, p.unit, p.qty AS wh_qty, z.code AS zone, p.bin
                          FROM transfer_items ti JOIN products p ON p.id=ti.product_id
                          LEFT JOIN zones z ON z.id=p.zone_id WHERE transfer_id=? ORDER BY p.sku""", (tid,)).fetchall()


def advance_transfer(con, tid, action, uid):
    t = con.execute("SELECT * FROM transfers WHERE id=?", (tid,)).fetchone()
    if t is None:
        raise OpError("Transfer not found")
    st, items = t["status"], transfer_items(con, tid)
    if action == "pick":
        if st != "pending":
            raise OpError("Only pending transfers can start picking")
        if t["from_shop_id"] is None:
            short = [i for i in items if i["wh_qty"] < i["qty"]]
            if short:
                raise OpError("Not enough warehouse stock for: " + ", ".join(f"{i['name']} (have {i['wh_qty']}, need {i['qty']})" for i in short))
        robot = pick_robot(con, items)
        con.execute("UPDATE transfers SET status='picking', picked_at=?, robot_id=? WHERE id=?", (now(), robot, tid))
        if robot:
            con.execute("UPDATE robots SET status='busy' WHERE id=?", (robot,))
    elif action == "dispatch":
        if st != "picking":
            raise OpError("Pick the items before dispatching")
        for i in items:
            if t["from_shop_id"] is None:
                change_warehouse(con, i["product_id"], -i["qty"], "dispatch", t["code"], uid)
            else:
                change_shop(con, t["from_shop_id"], i["product_id"], -i["qty"], "transfer out", t["code"], uid, allow_create=False)
        con.execute("UPDATE transfers SET status='in_transit', dispatched_at=? WHERE id=?", (now(), tid))
        if t["robot_id"]:
            con.execute("UPDATE robots SET status='idle', tasks_done=tasks_done+1 WHERE id=?", (t["robot_id"],))
    elif action == "deliver":
        if st != "in_transit":
            raise OpError("Only transfers on the road can be delivered")
        for i in items:
            change_shop(con, t["to_shop_id"], i["product_id"], i["qty"], "transfer in", t["code"], uid)
        con.execute("UPDATE transfers SET status='delivered', delivered_at=? WHERE id=?", (now(), tid))
    elif action == "cancel":
        if st in ("delivered", "cancelled"):
            raise OpError("This transfer is already closed")
        if st == "in_transit":                 # stock already left: put it back where it came from
            for i in items:
                if t["from_shop_id"] is None:
                    change_warehouse(con, i["product_id"], i["qty"], "return", t["code"], uid)
                else:
                    change_shop(con, t["from_shop_id"], i["product_id"], i["qty"], "return", t["code"], uid)
        if t["robot_id"] and st == "picking":
            con.execute("UPDATE robots SET status='idle' WHERE id=?", (t["robot_id"],))
        con.execute("UPDATE transfers SET status='cancelled' WHERE id=?", (tid,))
    else:
        raise OpError("Unknown action")
    log_activity(con, uid, f"Transfer {action}", t["code"])


def pick_robot(con, items):
    """Choose a free robot in the zone that holds most of the items."""
    zones = {}
    for i in items:
        zones[i["zone"]] = zones.get(i["zone"], 0) + 1
    order = sorted(zones, key=lambda z: -zones[z])
    for z in order + ZONES:
        r = con.execute("""SELECT r.id FROM robots r JOIN zones z ON z.id=r.zone_id
                           WHERE z.code=? AND r.status='idle' AND r.battery>20 ORDER BY r.battery DESC LIMIT 1""", (z,)).fetchone()
        if r:
            return r["id"]
    return None


# ---------------------------------------------------------------- purchase orders
def create_po(con, supplier_id, items, uid, note="", expected_days=None):
    items = [(int(p), int(q), float(c)) for p, q, c in items if p and q and int(q) > 0]
    if not items:
        raise OpError("Add at least one product with a quantity")
    sup = con.execute("SELECT lead_days FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    days = expected_days if expected_days is not None else (sup["lead_days"] if sup else 3)
    code = next_code(con, "purchase_orders", "PO")
    con.execute("""INSERT INTO purchase_orders (code, supplier_id, status, created_by, created_at, expected_at, note)
                   VALUES (?,?,?,?,?,?,?)""", (code, supplier_id, "draft", uid, now(), now(datetime.now() + timedelta(days=days)), note))
    po = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.executemany("INSERT INTO po_items (po_id, product_id, qty, unit_cost) VALUES (?,?,?,?)", [(po, p, q, c) for p, q, c in items])
    log_activity(con, uid, "Purchase order created", code)
    return po, code


def advance_po(con, po_id, action, uid):
    po = con.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if po is None:
        raise OpError("Purchase order not found")
    if action == "order":
        if po["status"] != "draft":
            raise OpError("Only drafts can be sent to the supplier")
        con.execute("UPDATE purchase_orders SET status='ordered' WHERE id=?", (po_id,))
    elif action == "receive":
        if po["status"] != "ordered":
            raise OpError("Send the order to the supplier first")
        for it in con.execute("SELECT * FROM po_items WHERE po_id=?", (po_id,)).fetchall():
            change_warehouse(con, it["product_id"], it["qty"], "receive", po["code"], uid)
            con.execute("UPDATE products SET unit_cost=? WHERE id=?", (it["unit_cost"], it["product_id"]))
        con.execute("UPDATE purchase_orders SET status='received', received_at=? WHERE id=?", (now(), po_id))
    elif action == "cancel":
        if po["status"] in ("received", "cancelled"):
            raise OpError("This order is already closed")
        con.execute("UPDATE purchase_orders SET status='cancelled' WHERE id=?", (po_id,))
    else:
        raise OpError("Unknown action")
    log_activity(con, uid, f"PO {action}", po["code"])


# -------------------------------------------------------------------- suggestions
def incoming_to_shops(con):
    """Qty already on the way to each (shop, product), so we do not suggest it twice."""
    rows = con.execute("""SELECT t.to_shop_id AS shop_id, ti.product_id, SUM(ti.qty) AS q FROM transfers t
                          JOIN transfer_items ti ON ti.transfer_id=t.id
                          WHERE t.status IN ('pending','picking','in_transit') GROUP BY 1,2""").fetchall()
    return {(r["shop_id"], r["product_id"]): r["q"] for r in rows}


def restock_plan(con):
    """For every shop item below its minimum: send from the warehouse, or from a shop with spare stock."""
    incoming = incoming_to_shops(con)
    rows = con.execute("""SELECT ss.*, s.name AS shop, s.code AS shop_code, p.name, p.sku, p.qty AS wh_qty, p.reorder_level,
                                 z.code AS zone
                          FROM shop_stock ss JOIN shops s ON s.id=ss.shop_id JOIN products p ON p.id=ss.product_id
                          LEFT JOIN zones z ON z.id=p.zone_id
                          WHERE ss.qty < ss.min_qty AND s.active=1 AND p.active=1
                          ORDER BY (ss.qty * 1.0 / ss.min_qty), s.name""").fetchall()
    plan, wh_left = [], {}
    for r in rows:
        on_way = incoming.get((r["shop_id"], r["product_id"]), 0)
        target = r["min_qty"] * 2
        need = target - r["qty"] - on_way
        if need <= 0:
            continue
        wh = wh_left.setdefault(r["product_id"], r["wh_qty"])
        spare_wh = max(0, wh - r["reorder_level"] // 2)       # keep a safety buffer in the warehouse
        src, src_name, qty, why = None, "Warehouse", 0, ""
        if spare_wh >= need:
            qty, why = need, f"warehouse has {wh}"
            wh_left[r["product_id"]] = wh - need
        else:
            donor = con.execute("""SELECT ss.shop_id, ss.qty, ss.min_qty, s.name FROM shop_stock ss JOIN shops s ON s.id=ss.shop_id
                                   WHERE ss.product_id=? AND ss.shop_id<>? AND ss.qty - ss.min_qty*1.5 >= ?
                                   ORDER BY ss.qty - ss.min_qty DESC LIMIT 1""", (r["product_id"], r["shop_id"], min(need, 4))).fetchone()
            if donor:
                qty = int(min(need, donor["qty"] - donor["min_qty"] * 1.5))
                src, src_name = donor["shop_id"], donor["name"]
                why = f"warehouse low ({wh}); {donor['name']} has spare ({donor['qty']} vs min {donor['min_qty']})"
            elif spare_wh > 0:
                qty, why = spare_wh, f"only {spare_wh} spare in warehouse, partial refill"
                wh_left[r["product_id"]] = wh - spare_wh
            else:
                why = "no spare stock anywhere: reorder from supplier"
        plan.append({"shop_id": r["shop_id"], "shop": r["shop"], "product_id": r["product_id"], "name": r["name"], "sku": r["sku"],
                     "zone": r["zone"], "have": r["qty"], "min": r["min_qty"], "on_way": on_way, "qty": int(qty),
                     "from_id": src, "from": src_name, "why": why})
    return plan


def reorder_plan(con):
    """Warehouse items at or below reorder level, grouped by supplier."""
    on_order = {r["product_id"]: r["q"] for r in con.execute(
        """SELECT pi.product_id, SUM(pi.qty) q FROM po_items pi JOIN purchase_orders po ON po.id=pi.po_id
           WHERE po.status IN ('draft','ordered') GROUP BY 1""")}
    rows = con.execute("""SELECT p.*, s.name AS supplier, s.lead_days FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
                          WHERE p.active=1 AND p.qty <= p.reorder_level ORDER BY s.name, p.qty*1.0/p.reorder_level""").fetchall()
    out = []
    for r in rows:
        oo = on_order.get(r["id"], 0)
        need = r["reorder_qty"] - oo
        if need > 0:
            out.append({"id": r["id"], "sku": r["sku"], "name": r["name"], "qty": r["qty"], "level": r["reorder_level"],
                        "on_order": oo, "suggest": need, "cost": r["unit_cost"], "supplier_id": r["supplier_id"],
                        "supplier": r["supplier"] or "No supplier", "lead": r["lead_days"]})
    return out


# ---------------------------------------------------------------------- alerts
def alerts(con, tracking=False):
    a = []
    for r in con.execute("SELECT id, name, qty, reorder_level FROM products WHERE active=1 AND qty=0"):
        a.append(("danger", f"{r['name']} is out of stock in the warehouse", f"/inventory/{r['id']}"))
    n = con.execute("SELECT COUNT(*) FROM products WHERE active=1 AND qty>0 AND qty<=reorder_level").fetchone()[0]
    if n:
        a.append(("warn", f"{n} product{'s' if n > 1 else ''} at or below reorder level", "/purchasing/reorder"))
    n = con.execute("SELECT COUNT(*) FROM shop_stock ss JOIN shops s ON s.id=ss.shop_id WHERE ss.qty < ss.min_qty AND s.active=1").fetchone()[0]
    if n:
        a.append(("warn", f"{n} shop shelf item{'s' if n > 1 else ''} below minimum", "/transfers/planner"))
    for r in con.execute("SELECT code, battery FROM robots WHERE battery < 25 AND status <> 'maintenance'"):
        a.append(("warn", f"Robot {r['code']} battery at {r['battery']:.0f}%", "/fleet"))
    n = con.execute("SELECT COUNT(*) FROM transfers WHERE status='pending' AND priority='urgent'").fetchone()[0]
    if n:
        a.append(("danger", f"{n} urgent transfer{'s' if n > 1 else ''} not started yet", "/transfers?status=pending"))
    late = con.execute("SELECT COUNT(*) FROM purchase_orders WHERE status='ordered' AND expected_at < ?", (now(),)).fetchone()[0]
    if late:
        a.append(("warn", f"{late} purchase order{'s are' if late > 1 else ' is'} late", "/purchasing"))
    for r in con.execute("SELECT s.title, z.code, s.multiplier FROM signals s JOIN zones z ON z.id=s.zone_id WHERE s.active=1 AND (s.expires_at IS NULL OR s.expires_at > ?) AND s.multiplier>=2", (now(),)):
        a.append(("info", f"Demand signal Zone {r['code']} x{r['multiplier']:.1f}: {r['title']}", "/signals"))
    if tracking:                                    # live GPS alerts, for managers only
        for r in con.execute("SELECT level, message FROM tracking_alerts WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 20"):
            a.append(({"danger": "danger", "warn": "warn"}.get(r["level"], "info"), r["message"], "/tracking"))
    return a


# ------------------------------------------------------------------- live fleet
def zone_pressure(con):
    """Live numbers per zone in the same shape the RL agent was trained on."""
    zones = con.execute("SELECT id, code, name FROM zones ORDER BY code").fetchall()
    queue = {z["code"]: 0 for z in zones}
    for r in con.execute("""SELECT z.code, COUNT(*) n FROM transfers t JOIN transfer_items ti ON ti.transfer_id=t.id
                            JOIN products p ON p.id=ti.product_id JOIN zones z ON z.id=p.zone_id
                            WHERE t.status IN ('pending','picking') AND t.from_shop_id IS NULL GROUP BY z.code"""):
        queue[r["code"]] = r["n"]
    since = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    recent = {z["code"]: 0 for z in zones}
    for r in con.execute("""SELECT z.code, COUNT(*) n FROM transfers t JOIN transfer_items ti ON ti.transfer_id=t.id
                            JOIN products p ON p.id=ti.product_id JOIN zones z ON z.id=p.zone_id
                            WHERE t.created_at >= ? GROUP BY z.code""", (since,)):
        recent[r["code"]] = r["n"]
    robots = {z["code"]: 0 for z in zones}
    for r in con.execute("""SELECT z.code, COUNT(*) n FROM robots r JOIN zones z ON z.id=r.zone_id
                            WHERE r.status <> 'maintenance' GROUP BY z.code"""):
        robots[r["code"]] = r["n"]
    signal = {z["code"]: 1.0 for z in zones}
    for r in con.execute("""SELECT z.code, MAX(s.multiplier) m FROM signals s JOIN zones z ON z.id=s.zone_id
                            WHERE s.active=1 AND (s.expires_at IS NULL OR s.expires_at > ?) GROUP BY z.code""", (now(),)):
        signal[r["code"]] = r["m"]
    return {"zones": [dict(z) for z in zones], "queue": queue, "recent": recent, "robots": robots, "signal": signal}


def _rollout(plan, seed, init, rates, sig, moves=(), policy=None):
    from .sim import DECISION_S, WarehouseSim
    sim = WarehouseSim(plan, seed, init=init, rates=rates, signal=sig)
    for s, d in moves:
        sim.move_robot(s, d)
    while sim.t < plan.length:
        if policy is not None and sim.t % DECISION_S == 0:
            sim.apply_action(policy(sim.observation(), sim))
        sim.step_second()
    return sim


def twin_advice(con, policy, horizon=900, seeds=(11, 12, 13), max_moves=3, cached_only=False):
    """Digital twin + planning.
    1. Copy today's floor (robots, open pick lines, recent demand, web signals) into the simulator.
    2. Try every single robot move, play the next 15 minutes for each, keep the best; repeat up to 3 moves.
    3. Also let the RL agent drive the same 15 minutes, so both can be compared.
    Only moves that cut predicted waiting time are suggested.
    cached_only=True returns None instead of planning when this floor state has no plan yet."""
    from .sim import BASE_RATE, MOVES, SERVICE_S, Plan
    pr = zone_pressure(con)
    zi = {z: i for i, z in enumerate(ZONES)}
    robots = con.execute("""SELECT r.id, r.code, r.status, r.battery, z.code AS zone FROM robots r JOIN zones z ON z.id=r.zone_id
                            ORDER BY r.id LIMIT ?""", (N_ROBOTS,)).fetchall()
    st_map = {"idle": 0, "busy": 0, "charging": 3, "maintenance": 4}
    init = {"zone": [zi[r["zone"]] for r in robots], "status": [st_map.get(r["status"], 0) for r in robots],
            "battery": [r["battery"] if r["status"] == "charging" else max(30.0, r["battery"]) for r in robots],
            "queues": [pr["queue"][z] * 3 for z in ZONES]}          # one open pick line ~ 3 orders of work
    recent = np.array([pr["recent"][z] for z in ZONES], dtype=float)
    share = (recent + 2.0) / (recent + 2.0).sum()                  # smoothed share of recent demand
    sig = np.array([pr["signal"][z] for z in ZONES], dtype=float)
    rates = BASE_RATE.sum() * share * sig                          # orders/min per zone, next 15 minutes
    plan = Plan(length=horizon)

    def score(moves=(), pol=None):
        return float(np.mean([_rollout(plan, sd, init, rates, sig, moves, pol).metrics()["avg_wait_s"] for sd in seeds]))

    alive = [init["zone"][i] for i in range(len(robots)) if init["status"][i] in (0, 1)]
    count = [alive.count(z) for z in range(len(ZONES))]
    # the plan only depends on these inputs (seeds are fixed), so the same floor state gives the same answer
    key = (repr(init), rates.round(6).tobytes(), sig.tobytes(), horizon, tuple(seeds), max_moves, type(policy).__name__)
    hit = _TWIN_CACHE.get(key)
    if hit:
        base, chosen, best, rl = hit[1]
        chosen = list(chosen)
    elif cached_only:
        return None
    else:
        base = score()
        chosen, best = [], base
        for _ in range(max_moves):
            trial = []
            for s, d in MOVES:
                if count[s] - sum(1 for m in chosen if m[0] == s) + sum(1 for m in chosen if m[1] == s) <= 1:
                    continue                                       # every zone keeps at least one robot
                trial.append((score(chosen + [(s, d)]), (s, d)))
            if not trial:
                break
            val, mv = min(trial)
            if val > best * 0.98:                                  # needs at least a 2% gain to be worth a trip
                break
            chosen.append(mv)
            best = val
        rl = score(pol=policy) if policy is not None else None
        if len(_TWIN_CACHE) > 32:
            _TWIN_CACHE.clear()
        _TWIN_CACHE[key] = hit = (time.time(), (base, tuple(chosen), best, rl))

    per_robot = 60.0 / SERVICE_S                                   # orders/min one robot can handle
    rob_now = list(count)
    moves, used = [], set()
    for s, d in chosen:
        row = next((r for r in sorted(robots, key=lambda r: (r["status"] != "idle", -r["battery"]))
                    if r["zone"] == ZONES[s] and r["id"] not in used and r["status"] in ("idle", "busy")), None)
        if row is None:
            continue
        used.add(row["id"])
        why = (f"Zone {ZONES[d]} expects {rates[d]:.0f} orders/min"
               + (f" (web signal x{sig[d]:.1f})" if sig[d] > 1.05 else "")
               + f" and has {pr['queue'][ZONES[d]]} open lines for {rob_now[d]} robot(s), needs about {rates[d] / per_robot:.0f}. "
               + f"Zone {ZONES[s]} expects {rates[s]:.0f} orders/min for {rob_now[s]} robots, needs about {rates[s] / per_robot:.0f}.")
        rob_now[s] -= 1
        rob_now[d] += 1
        moves.append({"robot_id": row["id"], "robot": row["code"], "from": ZONES[s], "to": ZONES[d],
                      "battery": row["battery"], "why": why})
    forecast = {"base": round(base), "plan": round(best), "rl": round(rl) if rl is not None else None,
                "gain": round(100 * (1 - best / max(base, 1e-6))), "rl_gain": round(100 * (1 - rl / max(base, 1e-6))) if rl is not None else None,
                "rates": [round(float(x), 1) for x in rates], "horizon_min": horizon // 60, "updated": hit[0]}
    return pr, moves, forecast


def kpis(con):
    k = {}
    k["skus"] = con.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
    k["units"] = con.execute("SELECT COALESCE(SUM(qty),0) FROM products WHERE active=1").fetchone()[0]
    k["value"] = con.execute("SELECT COALESCE(SUM(qty*unit_cost),0) FROM products WHERE active=1").fetchone()[0]
    k["shop_value"] = con.execute("SELECT COALESCE(SUM(ss.qty*p.unit_cost),0) FROM shop_stock ss JOIN products p ON p.id=ss.product_id").fetchone()[0]
    k["low"] = con.execute("SELECT COUNT(*) FROM products WHERE active=1 AND qty<=reorder_level").fetchone()[0]
    k["shop_low"] = con.execute("SELECT COUNT(*) FROM shop_stock WHERE qty<min_qty").fetchone()[0]
    k["open_tr"] = con.execute("SELECT COUNT(*) FROM transfers WHERE status IN ('pending','picking','in_transit')").fetchone()[0]
    k["urgent"] = con.execute("SELECT COUNT(*) FROM transfers WHERE status IN ('pending','picking') AND priority='urgent'").fetchone()[0]
    k["robots_active"] = con.execute("SELECT COUNT(*) FROM robots WHERE status IN ('busy')").fetchone()[0]
    k["robots_ok"] = con.execute("SELECT COUNT(*) FROM robots WHERE status <> 'maintenance'").fetchone()[0]
    k["po_open"] = con.execute("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('draft','ordered')").fetchone()[0]
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    r = con.execute("""SELECT AVG((julianday(delivered_at)-julianday(created_at))*24) h, COUNT(*) n FROM transfers
                       WHERE status='delivered' AND delivered_at >= ?""", (since,)).fetchone()
    k["lead_h"] = round(r["h"] or 0, 1)
    k["delivered_7d"] = r["n"]
    return k
