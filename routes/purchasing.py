from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from core.authz import role_required
from core.db import get_db, log_activity
from core.ops import OpError, advance_po, create_po, reorder_plan

bp = Blueprint("purchasing", __name__, url_prefix="/purchasing")


@bp.route("/")
def index():
    con = get_db()
    status = request.args.get("status", "open")
    where = "WHERE po.status IN ('draft','ordered')" if status == "open" else ("" if status == "all" else "WHERE po.status=?")
    args = () if status in ("open", "all") else (status,)
    rows = con.execute(f"""SELECT po.*, s.name AS supplier, u.name AS by_name,
                           (SELECT COUNT(*) FROM po_items WHERE po_id=po.id) lines,
                           (SELECT SUM(qty*unit_cost) FROM po_items WHERE po_id=po.id) total
                           FROM purchase_orders po LEFT JOIN suppliers s ON s.id=po.supplier_id LEFT JOIN users u ON u.id=po.created_by
                           {where} ORDER BY po.id DESC""", args).fetchall()
    counts = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM purchase_orders GROUP BY status")}
    return render_template("purchasing/index.html", rows=rows, status=status, counts=counts, n_reorder=len(reorder_plan(con)))


@bp.route("/new", methods=["GET", "POST"])
@role_required("manager")
def new():
    con = get_db()
    if request.method == "POST":
        f = request.form
        items = list(zip(f.getlist("product_id"), f.getlist("qty"), f.getlist("unit_cost")))
        try:
            po, code = create_po(con, int(f.get("supplier_id") or 0), items, current_user.id, note=f.get("note", "").strip())
            con.commit()
            flash(f"{code} saved as a draft.", "success")
            return redirect(url_for("purchasing.detail", po_id=po))
        except (OpError, ValueError) as e:
            con.rollback()
            flash(str(e) if isinstance(e, OpError) else "Check the supplier, quantities and costs.", "error")
    sups = con.execute("SELECT id, name, lead_days FROM suppliers WHERE active=1 ORDER BY name").fetchall()
    products = con.execute("SELECT id, sku, name, unit_cost, supplier_id, qty, reorder_qty FROM products WHERE active=1 ORDER BY name").fetchall()
    return render_template("purchasing/new.html", sups=sups, products=products, pre_sup=request.args.get("supplier", ""))


@bp.route("/<int:po_id>")
def detail(po_id):
    con = get_db()
    po = con.execute("""SELECT po.*, s.name AS supplier, s.contact, s.phone, s.email, u.name AS by_name
                        FROM purchase_orders po LEFT JOIN suppliers s ON s.id=po.supplier_id LEFT JOIN users u ON u.id=po.created_by
                        WHERE po.id=?""", (po_id,)).fetchone()
    if po is None:
        flash("Purchase order not found.", "error")
        return redirect(url_for("purchasing.index"))
    items = con.execute("""SELECT pi.*, p.sku, p.name, p.unit, p.qty AS stock FROM po_items pi JOIN products p ON p.id=pi.product_id
                           WHERE po_id=? ORDER BY p.sku""", (po_id,)).fetchall()
    return render_template("purchasing/detail.html", po=po, items=items, total=sum(i["qty"] * i["unit_cost"] for i in items))


@bp.route("/<int:po_id>/<action>", methods=["POST"])
@role_required("manager")
def act(po_id, action):
    con = get_db()
    try:
        advance_po(con, po_id, action, current_user.id)
        con.commit()
        flash({"order": "Marked as sent to the supplier.", "receive": "Goods received. Warehouse stock updated.",
               "cancel": "Order cancelled."}.get(action, "Done."), "success")
    except OpError as e:
        con.rollback()
        flash(str(e), "error")
    return redirect(url_for("purchasing.detail", po_id=po_id))


@bp.route("/reorder", methods=["GET", "POST"])
def reorder():
    con = get_db()
    plan = reorder_plan(con)
    if request.method == "POST":
        if not current_user.can("manager"):
            flash("You need manager access to create purchase orders.", "error")
            return redirect(url_for("purchasing.reorder"))
        chosen = set(request.form.getlist("pick"))
        groups = defaultdict(list)
        for r in plan:
            if str(r["id"]) in chosen and r["supplier_id"]:
                qty = int(request.form.get(f"qty_{r['id']}") or r["suggest"])
                groups[r["supplier_id"]].append((r["id"], qty, r["cost"]))
        made = []
        for sup, items in groups.items():
            _, code = create_po(con, sup, items, current_user.id, note="Created from reorder suggestions")
            made.append(code)
        con.commit()
        flash(f"Draft purchase orders created: {', '.join(made)}" if made else "Nothing selected.", "success" if made else "info")
        return redirect(url_for("purchasing.index"))
    return render_template("purchasing/reorder.html", plan=plan)


@bp.route("/suppliers", methods=["GET", "POST"])
def suppliers():
    con = get_db()
    if request.method == "POST":
        if not current_user.can("manager"):
            flash("You need manager access to edit suppliers.", "error")
            return redirect(url_for("purchasing.suppliers"))
        f = request.form
        vals = (f.get("name", "").strip(), f.get("contact", "").strip(), f.get("phone", "").strip(), f.get("email", "").strip(),
                int(f.get("lead_days") or 3), f.get("website", "").strip())
        if not vals[0]:
            flash("Supplier name is required.", "error")
        elif f.get("sid"):
            con.execute("UPDATE suppliers SET name=?, contact=?, phone=?, email=?, lead_days=?, website=? WHERE id=?", vals + (int(f["sid"]),))
            flash("Supplier updated.", "success")
        else:
            con.execute("INSERT INTO suppliers (name, contact, phone, email, lead_days, website) VALUES (?,?,?,?,?,?)", vals)
            log_activity(con, current_user.id, "Supplier added", vals[0])
            flash("Supplier added.", "success")
        con.commit()
        return redirect(url_for("purchasing.suppliers"))
    rows = con.execute("""SELECT s.*, (SELECT COUNT(*) FROM products WHERE supplier_id=s.id AND active=1) products,
                          (SELECT COUNT(*) FROM purchase_orders WHERE supplier_id=s.id) pos,
                          (SELECT AVG(julianday(received_at)-julianday(created_at)) FROM purchase_orders WHERE supplier_id=s.id AND status='received') real_lead
                          FROM suppliers s ORDER BY s.name""").fetchall()
    edit = None
    if request.args.get("edit"):
        edit = con.execute("SELECT * FROM suppliers WHERE id=?", (request.args["edit"],)).fetchone()
    return render_template("purchasing/suppliers.html", rows=rows, edit=edit)
