"""Logged-in user object and role checks (staff < manager < admin < owner)."""
from functools import wraps

from flask import flash, redirect, request, url_for
from flask_login import UserMixin, current_user

ROLE_RANK = {"staff": 1, "manager": 2, "admin": 3, "owner": 4}   # owner can do everything an admin can

class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.name = row["name"]
        self.email = row["email"]
        self.role = row["role"]
        self.active_flag = bool(row["active"])

    @property
    def is_active(self):
        return self.active_flag

    def can(self, role):
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK[role]

    @property
    def initials(self):
        return "".join(p[0] for p in self.name.split()[:2]).upper()

def role_required(role):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.path))
            if not current_user.can(role):
                flash(f"You need {role} access for that.", "error")
                return redirect(request.referrer or url_for("main.dashboard"))
            return fn(*a, **kw)
        return inner
    return deco

