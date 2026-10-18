import csv
import io

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from core.authz import role_required
from core.db import get_db, log_activity, now
from core.ops import OpError, change_warehouse

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

REASONS = {"receive": "Received (no PO)", "adjust": "Stock count correction", "damage": "Damaged / expired",
           "return": "Customer or shop return", "sample": "Used as sample / internal use"}


@bp.route("/")
def index():
    con = get_db()
    qs, args = [], []
    term = request.args.get("q", "").strip()
    cat = request.args.get("cat", "")
    zone = request.args.get("zone", "")
    view = request.args.get("view", "all")
    if term:
        qs.append("(p.name LIKE ? OR p.sku LIKE ? OR p.bin LIKE ?)")
        args += [f"%{term}%"] * 3
    if cat:
        qs.append("p.category=?")
        args.append(cat)
    if zone:
        qs.append("z.code=?")
        args.append(zone)
    if view == "low":
        qs.append("p.qty<=p.reorder_level")
    elif view == "out":
        qs.append("p.qty=0")
    active = 0 if view == "archived" else 1
    sort = {"name": "p.name", "qty": "p.qty", "value": "p.qty*p.unit_cost DESC", "sku": "p.sku", "health": "p.qty*1.0/p.reorder_level"}.get(request.args.get("sort", "sku"), "p.sku")
    rows = con.execute(f"""SELECT p.*, z.code AS zone, s.name AS supplier,
                           (SELECT COALESCE(SUM(qty),0) FROM shop_stock WHERE product_id=p.id) AS in_shops
                           FROM products p LEFT JOIN zones z ON z.id=p.zone_id LEFT JOIN suppliers s ON s.id=p.supplier_id
                           WHERE p.active={active} {('AND ' + ' AND '.join(qs)) if qs else ''} ORDER BY {sort}""", args).fetchall()
    cats = [r[0] for r in con.execute("SELECT DISTINCT category FROM products ORDER BY 1")]
    zones = con.execute("SELECT code, name FROM zones ORDER BY code").fetchall()
    totals = {"units": sum(r["qty"] for r in rows), "value": sum(r["qty"] * r["unit_cost"] for r in rows)}
    return render_template("inventory/index.html", rows=rows, cats=cats, zones=zones, totals=totals,
                           f={"q": term, "cat": cat, "zone": zone, "view": view, "sort": request.args.get("sort", "sku")})


def _form_lists(con):
    return (con.execute("SELECT id, code, name FROM zones ORDER BY code").fetchall(),
            con.execute("SELECT id, name FROM suppliers WHERE active=1 ORDER BY name").fetchall(),
            [r[0] for r in con.execute("SELECT DISTINCT category FROM products ORDER BY 1")])


def _read_product_form(f):
    try:
        d = {k: f.get(k, "").strip() for k in ("sku", "name", "category", "unit", "bin")}
        d["zone_id"] = int(f.get("zone_id") or 0) or None
        d["supplier_id"] = int(f.get("supplier_id") or 0) or None
        for k in ("reorder_level", "reorder_qty"):
            d[k] = int(f.get(k) or 0)
        for k in ("unit_cost", "unit_price"):
            d[k] = float(f.get(k) or 0)
    except ValueError:
        raise OpError("Numbers only in the quantity and price boxes")
    if not d["sku"] or not d["name"] or not d["category"]:
        raise OpError("SKU, name and category are required")
    if d["unit_price"] and d["unit_price"] < d["unit_cost"]:
        flash("Heads up: selling price is lower than cost.", "info")
    return d


@bp.route("/new", methods=["GET", "POST"])
@role_required("manager")
def new():
    con = get_db()
    if request.method == "POST":
        try:
            d = _read_product_form(request.form)
            if con.execute("SELECT 1 FROM products WHERE sku=?", (d["sku"],)).fetchone():
                raise OpError("That SKU already exists")
            opening = int(request.form.get("opening") or 0)
            con.execute("""INSERT INTO products (sku, name, category, unit, zone_id, bin, qty, reorder_level, reorder_qty, unit_cost,
                           unit_price, supplier_id, created_at) VALUES (?,?,?,?,?,?,0,?,?,?,?,?,?)""",
                        (d["sku"], d["name"], d["category"], d["unit"] or "pcs", d["zone_id"], d["bin"], d["reorder_level"],
                         d["reorder_qty"], d["unit_cost"], d["unit_price"], d["supplier_id"], now()))
            pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            if opening > 0:
                change_warehouse(con, pid, opening, "opening", "New product", current_user.id)
            log_activity(con, current_user.id, "Product added", d["sku"])
            con.commit()
            flash(f"{d['name']} added.", "success")
            return redirect(url_for("inventory.detail", pid=pid))
        except OpError as e:
            con.rollback()
            flash(str(e), "error")
    zones, sups, cats = _form_lists(con)
    return render_template("inventory/form.html", p=request.form if request.method == "POST" else {}, zones=zones, sups=sups, cats=cats, new=True)


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
@role_required("manager")
def edit(pid):
    con = get_db()
    p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if p is None:
        return redirect(url_for("inventory.index"))
    if request.method == "POST":
        try:
            d = _read_product_form(request.form)
            if con.execute("SELECT 1 FROM products WHERE sku=? AND id<>?", (d["sku"], pid)).fetchone():
                raise OpError("Another product already uses that SKU")
            con.execute("""UPDATE products SET sku=?, name=?, category=?, unit=?, zone_id=?, bin=?, reorder_level=?, reorder_qty=?,
                           unit_cost=?, unit_price=?, supplier_id=? WHERE id=?""",
                        (d["sku"], d["name"], d["category"], d["unit"] or "pcs", d["zone_id"], d["bin"], d["reorder_level"],
                         d["reorder_qty"], d["unit_cost"], d["unit_price"], d["supplier_id"], pid))
            log_activity(con, current_user.id, "Product edited", d["sku"])
            con.commit()
            flash("Saved.", "success")
            return redirect(url_for("inventory.detail", pid=pid))
        except OpError as e:
            flash(str(e), "error")
    zones, sups, cats = _form_lists(con)
    return render_template("inventory/form.html", p=p, zones=zones, sups=sups, cats=cats, new=False)


@bp.route("/<int:pid>")
def detail(pid):
    con = get_db()
    p = con.execute("""SELECT p.*, z.code AS zone, z.name AS zone_name, s.name AS supplier, s.id AS sup_id, s.lead_days
                       FROM products p LEFT JOIN zones z ON z.id=p.zone_id LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?""", (pid,)).fetchone()
    if p is None:
        flash("Product not found.", "error")
        return redirect(url_for("inventory.index"))
    moves = con.execute("""SELECT m.*, u.name AS user FROM movements m LEFT JOIN users u ON u.id=m.user_id
                           WHERE product_id=? ORDER BY m.id DESC LIMIT 60""", (pid,)).fetchall()
    shops = con.execute("""SELECT ss.*, s.name, s.code FROM shop_stock ss JOIN shops s ON s.id=ss.shop_id
                           WHERE product_id=? ORDER BY s.name""", (pid,)).fetchall()
    out30 = con.execute("""SELECT COALESCE(-SUM(change),0) FROM movements WHERE product_id=? AND reason='dispatch'
                           AND created_at >= datetime('now','localtime','-30 days')""", (pid,)).fetchone()[0]
    per_day = out30 / 30.0
    cover = (p["qty"] / per_day) if per_day > 0 else None
    open_po = con.execute("""SELECT po.id, po.code, po.status, pi.qty, po.expected_at FROM po_items pi JOIN purchase_orders po ON po.id=pi.po_id
                             WHERE pi.product_id=? AND po.status IN ('draft','ordered')""", (pid,)).fetchall()
    return render_template("inventory/detail.html", p=p, moves=moves, shops=shops, out30=out30, per_day=per_day,
                           cover=cover, open_po=open_po, reasons=REASONS)


@bp.route("/<int:pid>/adjust", methods=["POST"])
def adjust(pid):
    con = get_db()
    try:
        qty = int(request.form.get("qty") or 0)
        direction = request.form.get("direction", "in")
        reason = request.form.get("reason", "adjust")
        note = request.form.get("note", "").strip()
        if qty <= 0:
            raise OpError("Enter a quantity above zero")
        if direction == "set":
            cur = con.execute("SELECT qty FROM products WHERE id=?", (pid,)).fetchone()["qty"]
            change, reason = qty - cur, "count"
            if change == 0:
                raise OpError("Count matches the system, nothing to change")
        else:
            change = qty if direction == "in" else -qty
        new = change_warehouse(con, pid, change, reason, note or REASONS.get(reason, reason), current_user.id)
        log_activity(con, current_user.id, "Stock adjusted", f"product {pid}: {change:+d} ({reason})")
        con.commit()
        flash(f"Stock updated. New balance: {new}.", "success")
    except (OpError, ValueError) as e:
        con.rollback()
        flash(str(e) if isinstance(e, OpError) else "Enter a whole number.", "error")
    return redirect(url_for("inventory.detail", pid=pid))


@bp.route("/<int:pid>/archive", methods=["POST"])
@role_required("manager")
def archive(pid):
    con = get_db()
    con.execute("UPDATE products SET active=1-active WHERE id=?", (pid,))
    log_activity(con, current_user.id, "Product archived/restored", str(pid))
    con.commit()
    flash("Product updated.", "success")
    return redirect(url_for("inventory.detail", pid=pid))


@bp.route("/import", methods=["GET", "POST"])
@role_required("manager")
def import_csv():
    con = get_db()
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"):
            flash("Choose a .csv file.", "error")
            return redirect(url_for("inventory.import_csv"))
        rows = list(csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))))
        zones = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM zones")}
        added = updated = 0
        errors = []
        for i, r in enumerate(rows, start=2):
            try:
                sku = (r.get("sku") or "").strip()
                if not sku:
                    raise ValueError("missing sku")
                ex = con.execute("SELECT id, qty FROM products WHERE sku=?", (sku,)).fetchone()
                vals = dict(name=(r.get("name") or sku).strip(), category=(r.get("category") or "General").strip(),
                            unit=(r.get("unit") or "pcs").strip(), zone_id=zones.get((r.get("zone") or "").strip().upper()),
                            bin=(r.get("bin") or "").strip(), reorder_level=int(r.get("reorder_level") or 10),
                            reorder_qty=int(r.get("reorder_qty") or 20), unit_cost=float(r.get("unit_cost") or 0),
                            unit_price=float(r.get("unit_price") or 0))
                qty = int(r.get("qty") or 0)
                if ex:
                    con.execute("""UPDATE products SET name=:name, category=:category, unit=:unit, zone_id=:zone_id, bin=:bin,
                                   reorder_level=:reorder_level, reorder_qty=:reorder_qty, unit_cost=:unit_cost, unit_price=:unit_price
                                   WHERE id=:id""", dict(vals, id=ex["id"]))
                    if r.get("qty") not in (None, "") and qty != ex["qty"]:
                        change_warehouse(con, ex["id"], qty - ex["qty"], "count", "CSV import", current_user.id)
                    updated += 1
                else:
                    con.execute("""INSERT INTO products (sku, name, category, unit, zone_id, bin, qty, reorder_level, reorder_qty, unit_cost,
                                   unit_price, created_at) VALUES (:sku,:name,:category,:unit,:zone_id,:bin,0,:reorder_level,:reorder_qty,
                                   :unit_cost,:unit_price,:created)""", dict(vals, sku=sku, created=now()))
                    pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                    if qty > 0:
                        change_warehouse(con, pid, qty, "opening", "CSV import", current_user.id)
                    added += 1
            except (ValueError, OpError) as e:
                errors.append(f"Row {i}: {e}")
        log_activity(con, current_user.id, "CSV import", f"{added} added, {updated} updated")
        con.commit()
        flash(f"Import done: {added} added, {updated} updated." + (f" {len(errors)} row(s) skipped." if errors else ""), "success")
        for e in errors[:5]:
            flash(e, "error")
        return redirect(url_for("inventory.index"))
    return render_template("inventory/import.html")
