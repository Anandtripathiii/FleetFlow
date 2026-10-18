from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from core.db import get_db, log_activity
from core.ops import OpError, advance_transfer, create_transfer, restock_plan, transfer_items

bp = Blueprint("transfers", __name__, url_prefix="/transfers")

STATUSES = [("pending", "Pending"), ("picking", "Picking"), ("in_transit", "On the road"), ("delivered", "Delivered"), ("cancelled", "Cancelled")]


@bp.route("/")
def index():
    con = get_db()
    status = request.args.get("status", "open")
    shop = request.args.get("shop", "")
    where, args = [], []
    if status == "open":
        where.append("t.status IN ('pending','picking','in_transit')")
    elif status != "all":
        where.append("t.status=?")
        args.append(status)
    if shop:
        where.append("(t.to_shop_id=? OR t.from_shop_id=?)")
        args += [shop, shop]
    rows = con.execute(f"""SELECT t.*, s.name AS shop, COALESCE(f.name,'Warehouse') AS src, r.code AS robot, u.name AS by_name,
                           (SELECT COUNT(*) FROM transfer_items WHERE transfer_id=t.id) AS lines,
                           (SELECT SUM(qty) FROM transfer_items WHERE transfer_id=t.id) AS units
                           FROM transfers t JOIN shops s ON s.id=t.to_shop_id LEFT JOIN shops f ON f.id=t.from_shop_id
                           LEFT JOIN robots r ON r.id=t.robot_id LEFT JOIN users u ON u.id=t.created_by
                           {('WHERE ' + ' AND '.join(where)) if where else ''}
                           ORDER BY CASE t.status WHEN 'pending' THEN 0 WHEN 'picking' THEN 1 WHEN 'in_transit' THEN 2 ELSE 3 END,
                                    CASE t.priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, t.id DESC LIMIT 200""", args).fetchall()
    counts = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM transfers GROUP BY status")}
    shops = con.execute("SELECT id, name FROM shops ORDER BY name").fetchall()
    return render_template("transfers/index.html", rows=rows, counts=counts, statuses=STATUSES, status=status, shop=shop, shops=shops)


@bp.route("/new", methods=["GET", "POST"])
def new():
    con = get_db()
    if request.method == "POST":
        f = request.form
        items = list(zip(f.getlist("product_id"), f.getlist("qty")))
        try:
            tid, code = create_transfer(con, int(f.get("to_shop_id") or 0), items, current_user.id,
                                        from_shop=int(f.get("from_shop_id") or 0) or None,
                                        priority=f.get("priority", "normal"), note=f.get("note", "").strip())
            con.commit()
            flash(f"Transfer {code} created.", "success")
            return redirect(url_for("transfers.detail", tid=tid))
        except (OpError, ValueError) as e:
            con.rollback()
            flash(str(e) if isinstance(e, OpError) else "Check the shop and quantities.", "error")
    shops = con.execute("SELECT id, name, area FROM shops WHERE active=1 ORDER BY name").fetchall()
    products = con.execute("""SELECT p.id, p.sku, p.name, p.qty, p.unit, z.code AS zone FROM products p LEFT JOIN zones z ON z.id=p.zone_id
                              WHERE p.active=1 ORDER BY p.name""").fetchall()
    return render_template("transfers/new.html", shops=shops, products=products,
                           pre_to=request.args.get("to", ""), pre_product=request.args.get("product", ""))


@bp.route("/<int:tid>")
def detail(tid):
    con = get_db()
    t = con.execute("""SELECT t.*, s.name AS shop, s.area, s.manager, s.phone, COALESCE(f.name,'Main warehouse') AS src,
                       r.code AS robot, u.name AS by_name FROM transfers t JOIN shops s ON s.id=t.to_shop_id
                       LEFT JOIN shops f ON f.id=t.from_shop_id LEFT JOIN robots r ON r.id=t.robot_id
                       LEFT JOIN users u ON u.id=t.created_by WHERE t.id=?""", (tid,)).fetchone()
    if t is None:
        flash("Transfer not found.", "error")
        return redirect(url_for("transfers.index"))
    items = transfer_items(con, tid)
    if t["from_shop_id"]:
        src_qty = {r["product_id"]: r["qty"] for r in con.execute("SELECT product_id, qty FROM shop_stock WHERE shop_id=?", (t["from_shop_id"],))}
    else:
        src_qty = {i["product_id"]: i["wh_qty"] for i in items}
    return render_template("transfers/detail.html", t=t, items=items, src_qty=src_qty)


@bp.route("/<int:tid>/<action>", methods=["POST"])
def act(tid, action):
    con = get_db()
    try:
        advance_transfer(con, tid, action, current_user.id)
        con.commit()
        flash({"pick": "Picking started. A robot has been assigned.", "dispatch": "Dispatched. Stock has left the source.",
               "deliver": "Delivered. Shop stock updated.", "cancel": "Transfer cancelled."}.get(action, "Done."), "success")
    except OpError as e:
        con.rollback()
        flash(str(e), "error")
    return redirect(request.referrer or url_for("transfers.detail", tid=tid))


@bp.route("/<int:tid>/picklist")
def picklist(tid):
    con = get_db()
    t = con.execute("""SELECT t.*, s.name AS shop, s.address, COALESCE(f.name,'Main warehouse') AS src, r.code AS robot
                       FROM transfers t JOIN shops s ON s.id=t.to_shop_id LEFT JOIN shops f ON f.id=t.from_shop_id
                       LEFT JOIN robots r ON r.id=t.robot_id WHERE t.id=?""", (tid,)).fetchone()
    items = sorted(transfer_items(con, tid), key=lambda i: (i["zone"] or "", i["bin"] or ""))
    return render_template("transfers/picklist.html", t=t, items=items)


@bp.route("/planner", methods=["GET", "POST"])
def planner():
    con = get_db()
    plan = restock_plan(con)
    if request.method == "POST":
        chosen = set(request.form.getlist("pick"))
        groups = defaultdict(list)
        for i, row in enumerate(plan):
            if str(i) in chosen and row["qty"] > 0:
                groups[(row["shop_id"], row["from_id"])].append((row["product_id"], row["qty"]))
        made = []
        try:
            for (to, src), items in groups.items():
                _, code = create_transfer(con, to, items, current_user.id, from_shop=src,
                                          priority=request.form.get("priority", "normal"), note="Created by restock planner")
                made.append(code)
            log_activity(con, current_user.id, "Restock planner used", f"{len(made)} transfers")
            con.commit()
            flash(f"Created {len(made)} transfer(s): {', '.join(made)}" if made else "Nothing selected.", "success" if made else "info")
        except OpError as e:
            con.rollback()
            flash(str(e), "error")
        return redirect(url_for("transfers.index") if made else url_for("transfers.planner"))
    return render_template("transfers/planner.html", plan=plan)
