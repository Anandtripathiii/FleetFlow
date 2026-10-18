import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.security import check_password_hash, generate_password_hash

from core import db as dbm
from core.db import get_db, log_activity, now, set_setting, setting
from core.ops import alerts, kpis, zone_pressure

bp = Blueprint("main", __name__)


from core.authz import role_required as _role


@bp.route("/")
def dashboard():
    con = get_db()
    k = kpis(con)
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    out = {r["d"]: r["n"] for r in con.execute(
        "SELECT substr(created_at,1,10) d, -SUM(change) n FROM movements WHERE reason='dispatch' AND created_at>=? GROUP BY d", (days[0],))}
    inn = {r["d"]: r["n"] for r in con.execute(
        "SELECT substr(created_at,1,10) d, SUM(change) n FROM movements WHERE reason='receive' AND created_at>=? GROUP BY d", (days[0],))}
    flow = {"labels": [datetime.strptime(d, "%Y-%m-%d").strftime("%d %b") for d in days],
            "out": [out.get(d, 0) for d in days], "in": [inn.get(d, 0) for d in days]}
    by_cat = con.execute("""SELECT category, SUM(qty*unit_cost) v FROM products WHERE active=1 GROUP BY category ORDER BY v DESC""").fetchall()
    open_tr = con.execute("""SELECT t.*, s.name AS shop, (SELECT COUNT(*) FROM transfer_items WHERE transfer_id=t.id) AS lines
                             FROM transfers t JOIN shops s ON s.id=t.to_shop_id WHERE t.status IN ('pending','picking','in_transit')
                             ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, t.created_at LIMIT 6""").fetchall()
    low = con.execute("""SELECT id, sku, name, qty, reorder_level, unit FROM products WHERE active=1 AND qty<=reorder_level
                         ORDER BY qty*1.0/reorder_level LIMIT 6""").fetchall()
    pr = zone_pressure(con)
    acts = con.execute("""SELECT a.*, u.name FROM activity a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 7""").fetchall()
    return render_template("dashboard.html", k=k, flow=flow, by_cat=by_cat, open_tr=open_tr, low=low, pr=pr,
                           acts=acts, al=alerts(con))


@bp.route("/alerts")
def alerts_page():
    return render_template("alerts.html", al=alerts(get_db()))


@bp.route("/search")
def search():
    term = request.args.get("q", "").strip()
    con = get_db()
    res = {"products": [], "shops": [], "transfers": [], "pos": []}
    if term:
        like = f"%{term}%"
        res["products"] = con.execute("SELECT id, sku, name, qty FROM products WHERE (name LIKE ? OR sku LIKE ? OR category LIKE ?) LIMIT 20", (like, like, like)).fetchall()
        res["shops"] = con.execute("SELECT id, code, name, area FROM shops WHERE name LIKE ? OR code LIKE ? OR area LIKE ?", (like, like, like)).fetchall()
        res["transfers"] = con.execute("SELECT id, code, status FROM transfers WHERE code LIKE ? ORDER BY id DESC LIMIT 10", (like,)).fetchall()
        res["pos"] = con.execute("SELECT id, code, status FROM purchase_orders WHERE code LIKE ? ORDER BY id DESC LIMIT 10", (like,)).fetchall()
        if sum(len(v) for v in res.values()) == 1:
            if res["products"]:
                return redirect(url_for("inventory.detail", pid=res["products"][0]["id"]))
            if res["transfers"]:
                return redirect(url_for("transfers.detail", tid=res["transfers"][0]["id"]))
    return render_template("search.html", term=term, res=res)


@bp.route("/reports")
def reports():
    con = get_db()
    days = int(request.args.get("days", 30))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    top = con.execute("""SELECT p.id, p.sku, p.name, p.category, -SUM(m.change) units, -SUM(m.change)*p.unit_price revenue
                         FROM movements m JOIN products p ON p.id=m.product_id
                         WHERE m.reason='dispatch' AND m.created_at>=? GROUP BY p.id ORDER BY units DESC LIMIT 10""", (since,)).fetchall()
    slow = con.execute("""SELECT p.id, p.sku, p.name, p.qty, p.qty*p.unit_cost value,
                          COALESCE((SELECT -SUM(change) FROM movements WHERE product_id=p.id AND reason='dispatch' AND created_at>=?),0) units
                          FROM products p WHERE p.active=1 ORDER BY units ASC, value DESC LIMIT 8""", (since,)).fetchall()
    shops = con.execute("""SELECT s.id, s.name, COUNT(ss.product_id) items,
                           SUM(CASE WHEN ss.qty>=ss.min_qty THEN 1 ELSE 0 END) ok,
                           (SELECT COUNT(*) FROM transfers t WHERE t.to_shop_id=s.id AND t.status='delivered' AND t.delivered_at>=?) deliveries,
                           (SELECT AVG((julianday(delivered_at)-julianday(created_at))*24) FROM transfers t WHERE t.to_shop_id=s.id AND t.status='delivered' AND t.delivered_at>=?) lead
                           FROM shops s LEFT JOIN shop_stock ss ON ss.shop_id=s.id WHERE s.active=1 GROUP BY s.id ORDER BY s.name""", (since, since)).fetchall()
    cats = con.execute("""SELECT category, COUNT(*) n, SUM(qty) units, SUM(qty*unit_cost) cost, SUM(qty*unit_price) retail
                          FROM products WHERE active=1 GROUP BY category ORDER BY cost DESC""").fetchall()
    robots = con.execute("SELECT code, tasks_done, battery, status FROM robots ORDER BY tasks_done DESC").fetchall()
    return render_template("reports.html", top=top, slow=slow, shops=shops, cats=cats, robots=robots, days=days)


@bp.route("/reports/export/<kind>.csv")
def export(kind):
    con = get_db()
    queries = {
        "stock": ("SELECT p.sku, p.name, p.category, z.code AS zone, p.bin, p.qty, p.unit, p.reorder_level, p.reorder_qty, p.unit_cost, p.unit_price, ROUND(p.qty*p.unit_cost,2) AS stock_value FROM products p LEFT JOIN zones z ON z.id=p.zone_id WHERE p.active=1 ORDER BY p.sku", ()),
        "movements": ("SELECT m.created_at, p.sku, p.name, m.location, m.change, m.balance, m.reason, m.ref, u.name AS user FROM movements m JOIN products p ON p.id=m.product_id LEFT JOIN users u ON u.id=m.user_id ORDER BY m.id DESC", ()),
        "transfers": ("SELECT t.code, COALESCE(f.name,'Warehouse') AS from_place, s.name AS to_shop, t.status, t.priority, t.created_at, t.dispatched_at, t.delivered_at FROM transfers t JOIN shops s ON s.id=t.to_shop_id LEFT JOIN shops f ON f.id=t.from_shop_id ORDER BY t.id DESC", ()),
        "shop_stock": ("SELECT s.name AS shop, p.sku, p.name, ss.qty, ss.min_qty FROM shop_stock ss JOIN shops s ON s.id=ss.shop_id JOIN products p ON p.id=ss.product_id ORDER BY s.name, p.sku", ()),
    }
    if kind not in queries:
        return redirect(url_for("main.reports"))
    cur = con.execute(*queries[kind])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([d[0] for d in cur.description])
    w.writerows(cur.fetchall())
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={kind}_{datetime.now():%Y%m%d}.csv"})


@bp.route("/activity")
def activity():
    con = get_db()
    rows = con.execute("SELECT a.*, u.name FROM activity a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300").fetchall()
    return render_template("activity.html", rows=rows)


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    con = get_db()
    if request.method == "POST":
        row = con.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
        if not check_password_hash(row["password_hash"], request.form.get("current", "")):
            flash("Your current password is wrong.", "error")
        elif len(request.form.get("new", "")) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("New passwords do not match.", "error")
        else:
            con.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(request.form["new"]), current_user.id))
            log_activity(con, current_user.id, "Changed password")
            con.commit()
            flash("Password updated.", "success")
        return redirect(url_for("main.profile"))
    return render_template("profile.html")


@bp.route("/users", methods=["GET", "POST"])
@_role("admin")
def users():
    con = get_db()
    if request.method == "POST":
        act = request.form.get("action")
        if act == "add":
            email = request.form.get("email", "").strip().lower()
            if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                flash("That email already has an account.", "error")
            elif len(request.form.get("password", "")) < 6 or "@" not in email:
                flash("Enter a valid email and a password of 6+ characters.", "error")
            else:
                con.execute("INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                            (request.form.get("name", "").strip() or email, email, generate_password_hash(request.form["password"]),
                             request.form.get("role", "staff"), now()))
                log_activity(con, current_user.id, "User added", email)
                flash("User added.", "success")
        else:
            uid = int(request.form.get("uid", 0))
            if uid == current_user.id:
                flash("You cannot change your own role or switch yourself off.", "error")
            elif act == "role":
                con.execute("UPDATE users SET role=? WHERE id=?", (request.form.get("role"), uid))
                flash("Role updated.", "success")
            elif act == "toggle":
                con.execute("UPDATE users SET active=1-active WHERE id=?", (uid,))
                flash("Account updated.", "success")
            elif act == "reset":
                con.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash("welcome123"), uid))
                flash("Password reset to welcome123. Ask them to change it.", "success")
        con.commit()
        return redirect(url_for("main.users"))
    rows = con.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    return render_template("users.html", rows=rows)


@bp.route("/settings", methods=["GET", "POST"])
@_role("admin")
def settings():
    con = get_db()
    import os
    if request.method == "POST":
        act = request.form.get("action")
        if act == "save":
            for key in ("company", "city"):
                set_setting(con, key, request.form.get(key, "").strip())
            set_setting(con, "allow_signup", "1" if request.form.get("allow_signup") else "0")
            key = request.form.get("tinyfish_key", "").strip()
            if key:
                set_setting(con, "tinyfish_key", key)
            if request.form.get("clear_key"):
                set_setting(con, "tinyfish_key", "")
            log_activity(con, current_user.id, "Settings saved")
            con.commit()
            flash("Settings saved.", "success")
        elif act == "reset_demo":
            if request.form.get("confirm") != "RESET":
                flash("Type RESET to confirm.", "error")
            else:
                con.commit()
                con.execute("PRAGMA foreign_keys = OFF")
                for t in ["activity", "signals", "robot_moves", "transfer_items", "transfers", "po_items", "purchase_orders",
                          "movements", "shop_stock", "robots", "shops", "products", "suppliers", "zones", "settings"]:
                    con.execute(f"DELETE FROM {t}")
                con.execute("PRAGMA foreign_keys = ON")
                dbm.seed(con)
                con.commit()
                flash("Demo data reloaded.", "success")
        return redirect(url_for("main.settings"))
    env_key = bool(os.environ.get("TINYFISH_API_KEY"))
    db_key = setting(con, "tinyfish_key", "") or ""
    return render_template("settings.html", company=setting(con, "company", ""), city=setting(con, "city", "New Delhi"),
                           allow_signup=setting(con, "allow_signup", "1") == "1", env_key=env_key,
                           db_key_hint=(db_key[:4] + "…" + db_key[-4:]) if len(db_key) > 8 else "")
