"""Web UI authentication: env password → Flask session."""

from __future__ import annotations

import hmac
from functools import wraps

from flask import current_app, flash, redirect, request, session, url_for


def check_password(submitted: str) -> bool:
    expected = current_app.config["HYDRA_ADMIN_PASSWORD"]
    if not expected:
        return False
    return hmac.compare_digest(submitted or "", expected)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("hydra_user"):
            return redirect(url_for("web.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper