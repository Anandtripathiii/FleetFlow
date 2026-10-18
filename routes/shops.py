from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from core.authz import role_required
from core.db import get_db, log_activity
from core.ops import OpError, change_shop, incoming_to_shops

bp = Blueprint("shops", __name__, url_prefix="/shops")


@bp.route("/")
def index():
    con = get_db()
    rows = con.execute("""SELECT s.*, z.code AS zone,
                          COUNT(ss.product_id) AS items,
                          SUM(CASE WHEN ss.qty < ss.min_qty THEN 1 ELSE 0 END) AS low,
                          SUM(CASE WHEN ss.qty = 0 THEN 1 ELSE 0 END) AS empty,
                          COALESCE(SUM(ss.qty * p.unit_price),0) AS shelf_value,
                          (SELECT COUNT(*) FROM transfers t WHERE t.to_shop_id=s.id AND t.status IN ('pending','picking','in_transit')) AS incoming
                          FROM shops s LEFT JOIN zones z ON z.id=s.zone_id
                          LEFT JOIN shop_stock ss ON ss.shop_id=s.id LEFT JOIN products p ON p.id=ss.product_id
                          GROUP BY s.id ORDER BY s.active DESC, s.name""").fetchall()
    return render_template("shops/index.html", rows=rows)


def _save(con, sid=None):
    f = request.form
    vals = (f.get("code", "").strip().upper(), f.get("name", "").strip(), f.get("area", "").strip(), f.get("manager", "").strip(),
            f.get("phone", "").strip(), f.get("address", "").strip(), int(f.get("zone_id") or 0) or None)
    if not vals[0] or not vals[1]:
        raise OpError("Shop code and name are required")
    clash = con.execute("SELECT id FROM shops WHERE code=? AND id<>?", (vals[0], sid or 0)).fetchone()
    if clash:
        raise OpError("Another shop already uses that code")
    if sid:
        con.execute("UPDATE shops SET code=?, name=?, area=?, manager=?, phone=?, address=?, zone_id=? WHERE id=?", vals + (sid,))
        return sid
    con.execute("INSERT INTO shops (code, name, area, manager, phone, address, zone_id) VALUES (?,?,?,?,?,?,?)", vals)
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


@bp.route("/new", methods=["GET", "POST"])
@role_required("manager")
def new():
    con = get_db()
    if request.method == "POST":
        try:
            sid = _save(con)
            log_activity(con, current_user.id, "Shop added", request.form.get("name"))
            con.commit()
            flash("Shop added. Now add the products it sells.", "success")
            return redirect(url_for("shops.detail", sid=sid))
        except OpError as e:
            flash(str(e), "error")
    zones = con.execute("SELECT id, code, name FROM zones ORDER BY code").fetchall()
    return render_template("shops/form.html", s=request.form if request.method == "POST" else {}, zones=zones, new=True)


@bp.route("/<int:sid>/edit", methods=["GET", "POST"])
@role_required("manager")
def edit(sid):
    con = get_db()
    s = con.execute("SELECT * FROM shops WHERE id=?", (sid,)).fetchone()
    if request.method == "POST":
        try:
            _save(con, sid)
            con.commit()
            flash("Saved.", "success")
            return redirect(url_for("shops.detail", sid=sid))
        except OpError as e:
            flash(str(e), "error")
    zones = con.execute("SELECT id, code, name FROM zones ORDER BY code").fetchall()
    return render_template("shops/form.html", s=s, zones=zones, new=False)


@bp.route("/<int:sid>")
def detail(sid):
    con = get_db()
    s = con.execute("SELECT s.*, z.code AS zone, z.name AS zone_name FROM shops s LEFT JOIN zones z ON z.id=s.zone_id WHERE s.id=?", (sid,)).fetchone()
    if s is None:
        flash("Shop not found.", "error")
        return redirect(url_for("shops.index"))
    view = request.args.get("view", "all")
    incoming = incoming_to_shops(con)
    stock = con.execute("""SELECT ss.*, p.name, p.sku, p.unit, p.category, p.unit_price, p.qty AS wh_qty
                           FROM shop_stock ss JOIN products p ON p.id=ss.product_id WHERE ss.shop_id=?
                           ORDER BY (ss.qty*1.0/ss.min_qty), p.name""", (sid,)).fetchall()
    stock = [dict(r, on_way=incoming.get((sid, r["product_id"]), 0)) for r in stock]
    summary = {"items": len(stock), "low": sum(1 for r in stock if r["qty"] < r["min_qty"]),
               "empty": sum(1 for r in stock if r["qty"] == 0), "on_way": sum(r["on_way"] for r in stock)}
    if view == "low":
        stock = [r for r in stock if r["qty"] < r["min_qty"]]
    transfers = con.execute("""SELECT t.*, COALESCE(f.name,'Warehouse') AS src FROM transfers t LEFT JOIN shops f ON f.id=t.from_shop_id
                               WHERE t.to_shop_id=? OR t.from_shop_id=? ORDER BY t.id DESC LIMIT 12""", (sid, sid)).fetchall()
    others = con.execute("""SELECT id, sku, name FROM products WHERE active=1 AND id NOT IN (SELECT product_id FROM shop_stock WHERE shop_id=?)
                            ORDER BY name""", (sid,)).fetchall()
    sales7 = con.execute("""SELECT COALESCE(-SUM(change),0) FROM movements WHERE location=? AND reason='sale'
                            AND created_at >= datetime('now','localtime','-7 days')""", (s["code"],)).fetchone()[0]
    return render_template("shops/detail.html", s=s, stock=stock, transfers=transfers, others=others, view=view, sales7=sales7, sm=summary)


@bp.route("/<int:sid>/stock", methods=["POST"])
def stock_action(sid):
    con = get_db()
    act = request.form.get("action")
    try:
        pid = int(request.form.get("product_id") or 0)
        qty = int(request.form.get("qty") or 0)
        if act == "sale":
            if qty <= 0:
                raise OpError("Enter how many were sold")
            change_shop(con, sid, pid, -qty, "sale", "Counter sale", current_user.id, allow_create=False)
            msg = f"Recorded sale of {qty}."
        elif act == "count":
            row = con.execute("SELECT qty FROM shop_stock WHERE shop_id=? AND product_id=?", (sid, pid)).fetchone()
            if row is None or qty < 0:
                raise OpError("Enter the counted quantity")
            if qty != row["qty"]:
                change_shop(con, sid, pid, qty - row["qty"], "count", "Shelf count", current_user.id)
            msg = "Shelf count saved."
        elif act == "min":
            if qty < 0:
                raise OpError("Minimum cannot be negative")
            con.execute("UPDATE shop_stock SET min_qty=? WHERE shop_id=? AND product_id=?", (qty, sid, pid))
            msg = "Minimum level updated."
        elif act == "add":
            if not pid:
                raise OpError("Pick a product")
            mn = int(request.form.get("min_qty") or 5)
            con.execute("INSERT OR IGNORE INTO shop_stock (shop_id, product_id, qty, min_qty) VALUES (?,?,0,?)", (sid, pid, mn))
            if qty > 0:
                change_shop(con, sid, pid, qty, "count", "Opening shelf stock", current_user.id)
            msg = "Product added to this shop."
        elif act == "remove":
            row = con.execute("SELECT qty FROM shop_stock WHERE shop_id=? AND product_id=?", (sid, pid)).fetchone()
            if row and row["qty"] > 0:
                raise OpError("Move or sell the remaining stock before removing this product")
            con.execute("DELETE FROM shop_stock WHERE shop_id=? AND product_id=?", (sid, pid))
            msg = "Removed from this shop."
        else:
            raise OpError("Unknown action")
        con.commit()
        flash(msg, "success")
    except (OpError, ValueError) as e:
        con.rollback()
        flash(str(e) if isinstance(e, OpError) else "Enter a whole number.", "error")
    return redirect(request.referrer or url_for("shops.detail", sid=sid))


@bp.route("/<int:sid>/toggle", methods=["POST"])
@role_required("manager")
def toggle(sid):
    con = get_db()
    con.execute("UPDATE shops SET active=1-active WHERE id=?", (sid,))
    con.commit()
    flash("Shop status changed.", "success")
    return redirect(url_for("shops.detail", sid=sid))
