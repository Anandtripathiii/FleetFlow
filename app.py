"""FleetFlow: warehouse stock, shop transfers and AI robot rebalancing.

Run locally:   pip install -r requirements.txt  &&  python app.py   ->  http://127.0.0.1:5000
"""
import hashlib
import os
from datetime import datetime
from functools import wraps

from flask import Flask, redirect, request, url_for
from flask_compress import Compress
from flask_login import LoginManager, current_user

from core import db as dbm
from core.authz import User

APP_NAME = "FleetFlow"


def static_speedups(app):
    """gzip/brotli for HTML, CSS, JS and JSON, and long browser caching for static files.

    url_for('static', ...) adds ?h=<content hash>, so a changed file gets a new URL and the
    one-year immutable cache can never serve an old version."""
    Compress(app)
    hashes = {}

    @app.url_defaults
    def static_hash(endpoint, values):
        if endpoint == "static" and "filename" in values:
            path = os.path.join(app.static_folder, values["filename"])
            try:
                key = (path, os.path.getmtime(path))
            except OSError:
                return
            if key not in hashes:
                with open(path, "rb") as f:
                    hashes[key] = hashlib.md5(f.read()).hexdigest()[:10]
            values["h"] = hashes[key]

    @app.after_request
    def static_cache(resp):                     # runs before Compress, which skips passthrough responses
        if request.endpoint == "static" and resp.status_code == 200:
            resp.direct_passthrough = False
            if request.args.get("h"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["APP_NAME"] = APP_NAME
    if os.environ.get("SPACE_ID"):              # running on Hugging Face Spaces (shown inside an iframe)
        app.config.update(SESSION_COOKIE_SAMESITE="None", SESSION_COOKIE_SECURE=True,
                          REMEMBER_COOKIE_SAMESITE="None", REMEMBER_COOKIE_SECURE=True)
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    con = dbm.connect()
    dbm.init_db(con, demo=os.environ.get("NO_DEMO_DATA") != "1")
    con.close()
    app.teardown_appcontext(dbm.close_db)
    static_speedups(app)

    lm = LoginManager(app)
    lm.login_view = "auth.login"
    lm.login_message = "Please log in to continue."
    lm.login_message_category = "info"

    @lm.user_loader
    def load_user(uid):
        r = dbm.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        return User(r) if r else None

    from routes.auth import bp as auth_bp
    from routes.main import bp as main_bp
    from routes.inventory import bp as inv_bp
    from routes.shops import bp as shops_bp
    from routes.transfers import bp as tr_bp
    from routes.purchasing import bp as po_bp
    from routes.fleet import bp as fleet_bp
    from routes.signals import bp as sig_bp
    from routes.tracking_api import bp as trk_api_bp
    from routes.tracking import bp as trk_bp
    for b in (auth_bp, main_bp, inv_bp, shops_bp, tr_bp, po_bp, fleet_bp, sig_bp, trk_api_bp, trk_bp):
        app.register_blueprint(b)

    @app.before_request
    def require_login():
        open_paths = ("/login", "/register", "/static/", "/healthz", "/api/")   # /api/ answers 401 JSON itself
        public_pages = ("/", "/about")         # landing page
        if not current_user.is_authenticated and not request.path.startswith(open_paths) and request.path not in public_pages:
            return redirect(url_for("auth.login", next=request.path))

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.context_processor
    def globals_():
        ctx = {"APP_NAME": APP_NAME, "now": datetime.now()}
        if current_user.is_authenticated:
            from core.ops import alerts
            try:
                ctx["nav_alerts"] = alerts(dbm.get_db(), tracking=current_user.can("manager"))
                from core.tracking import driver_of
                ctx["is_driver"] = driver_of(dbm.get_db(), current_user.id) is not None
            except Exception:
                ctx["nav_alerts"] = []
        return ctx

    @app.template_filter("inr")
    def inr(v, dec=0):
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            return v
        neg = v < 0
        v = abs(v)
        s = f"{v:,.{dec}f}"
        whole, _, frac = s.partition(".")
        whole = whole.replace(",", "")
        if len(whole) > 3:                      # Indian grouping: 12,34,567
            head, tail = whole[:-3], whole[-3:]
            parts = []
            while len(head) > 2:
                parts.insert(0, head[-2:])
                head = head[:-2]
            if head:
                parts.insert(0, head)
            whole = ",".join(parts + [tail])
        return ("-" if neg else "") + "₹" + whole + ("." + frac if frac else "")

    @app.template_filter("num")
    def num(v):
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return v

    @app.template_filter("when")
    def when(s, fmt="%d %b, %H:%M"):
        if not s:
            return "—"
        try:
            return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").strftime(fmt)
        except ValueError:
            return s

    @app.template_filter("ago")
    def ago(s):
        if not s:
            return "—"
        try:
            d = datetime.now() - datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return s
        m = int(d.total_seconds() // 60)
        if m < 1:
            return "just now"
        if m < 60:
            return f"{m} min ago"
        if m < 1440:
            return f"{m // 60} h ago"
        return f"{m // 1440} d ago"

    @app.errorhandler(404)
    def nf(_e):
        from flask import render_template
        return render_template("error.html", code=404, msg="That page does not exist."), 404

    @app.errorhandler(403)
    def forb(_e):
        from flask import render_template
        return render_template("error.html", code=403, msg="You do not have access to that page."), 403

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
