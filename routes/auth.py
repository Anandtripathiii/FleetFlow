from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from core.authz import User
from core.db import get_db, log_activity, now, setting

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        con = get_db()
        email = request.form.get("email", "").strip().lower()
        row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row is None or not check_password_hash(row["password_hash"], request.form.get("password", "")):
            flash("Wrong email or password.", "error")
        elif not row["active"]:
            flash("This account is switched off. Ask an admin.", "error")
        else:
            login_user(User(row), remember=bool(request.form.get("remember")))
            con.execute("UPDATE users SET last_login=? WHERE id=?", (now(), row["id"]))
            log_activity(con, row["id"], "Logged in")
            con.commit()
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
    con = get_db()
    allow = setting(con, "allow_signup", "1") == "1"
    return render_template("auth/login.html", allow_signup=allow)


@bp.route("/register", methods=["GET", "POST"])
def register():
    con = get_db()
    first = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    if setting(con, "allow_signup", "1") != "1" and not first:
        flash("New sign-ups are closed. Ask an admin to add you.", "info")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        err = None
        if len(name) < 2:
            err = "Please enter your name."
        elif "@" not in email or "." not in email.split("@")[-1]:
            err = "Please enter a valid email."
        elif len(pw) < 6:
            err = "Password must be at least 6 characters."
        elif pw != request.form.get("confirm", ""):
            err = "Passwords do not match."
        elif con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            err = "An account with this email already exists."
        if err:
            flash(err, "error")
            return render_template("auth/register.html", form=request.form)
        role = "admin" if first else "staff"
        con.execute("INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                    (name, email, generate_password_hash(pw), role, now()))
        log_activity(con, None, "Account created", f"{email} ({role})")
        con.commit()
        flash("Account created. You can log in now." + ("" if first else " An admin can give you more access."), "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form={})


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
