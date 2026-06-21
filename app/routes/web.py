"""Web UI blueprint (Jinja2 pages)."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from app.auth import check_password, login_required
from app.services import audit, data, neighbor as neighbor_svc

log = logging.getLogger(__name__)

bp = Blueprint("web", __name__)


def _settings():
    return current_app.config["HYDRA_SETTINGS"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@bp.get("/login")
def login():
    if session.get("hydra_user"):
        return redirect(url_for("web.dashboard"))
    return render_template("login.html", next=request.args.get("next", ""))


@bp.post("/login")
def login_post():
    pw = request.form.get("password", "")
    if check_password(pw):
        session["hydra_user"] = "admin"
        nxt = request.form.get("next") or url_for("web.dashboard")
        return redirect(nxt)
    flash("Invalid password.", "error")
    return redirect(url_for("web.login"))


@bp.get("/logout")
def logout():
    session.pop("hydra_user", None)
    flash("Logged out.", "info")
    return redirect(url_for("web.login"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@bp.get("/")
@login_required
def dashboard():
    files = data.list_files(50)
    neighbors = neighbor_svc.list_neighbors_for_ui()
    audit_tail = audit.tail(10)
    return render_template(
        "dashboard.html",
        files=files,
        neighbors=neighbors,
        audit_tail=audit_tail,
        chain_status=audit.verify_chain(),
    )


@bp.get("/data")
@login_required
def data_page():
    files = data.list_files(1000)
    return render_template("data.html", files=files)


@bp.get("/data/download/<int:file_id>")
@login_required
def data_download(file_id: int):
    from app.services.data import assemble_to_disk

    meta = data.get_file(file_id)
    if not meta:
        abort(404)
    tmp_dir = current_app.config["HYDRA_SETTINGS"].out_dir / "_downloads"
    dst = assemble_to_disk(file_id, tmp_dir / meta["name"])
    return send_file(str(dst), as_attachment=True, download_name=meta["name"])


@bp.post("/data/delete/<int:file_id>")
@login_required
def data_delete(file_id: int):
    s = _settings()
    actor = session.get("hydra_user", "admin")
    ok = data.delete_file(file_id, actor=actor)
    flash("File deleted." if ok else "File not found.", "success" if ok else "error")
    return redirect(url_for("web.data_page"))


@bp.get("/neighbors")
@login_required
def neighbors_page():
    rows = neighbor_svc.list_neighbors_for_ui()
    return render_template("neighbors.html", neighbors=rows)


@bp.post("/neighbors/<ip>/approve")
@login_required
def neighbors_approve(ip: str):
    actor = session.get("hydra_user", "admin")
    neighbor_svc.approve(ip, actor=actor)
    flash(f"Approved {ip}.", "success")
    return redirect(url_for("web.neighbors_page"))


@bp.post("/neighbors/<ip>/reject")
@login_required
def neighbors_reject(ip: str):
    actor = session.get("hydra_user", "admin")
    neighbor_svc.reject(ip, actor=actor)
    flash(f"Rejected {ip}.", "info")
    return redirect(url_for("web.neighbors_page"))


@bp.post("/neighbors/<ip>/remove")
@login_required
def neighbors_remove(ip: str):
    actor = session.get("hydra_user", "admin")
    neighbor_svc.remove(ip, actor=actor)
    flash(f"Removed {ip}.", "info")
    return redirect(url_for("web.neighbors_page"))


@bp.post("/neighbors/<ip>/reset-hello")
@login_required
def neighbors_reset(ip: str):
    actor = session.get("hydra_user", "admin")
    neighbor_svc.reset_hello(ip, actor=actor)
    flash(f"Reset hello for {ip}.", "info")
    return redirect(url_for("web.neighbors_page"))


@bp.get("/audit")
@login_required
def audit_page():
    rows = audit.tail(500)
    chain = audit.verify_chain()
    return render_template("audit.html", rows=rows, chain_status=chain)


@bp.post("/audit/verify")
@login_required
def audit_verify_post():
    return redirect(url_for("web.audit_page"))


@bp.get("/identity")
@login_required
def identity_page():
    kp = current_app.config["HYDRA_KEYPAIR"]
    s = _settings()
    return render_template(
        "identity.html",
        public_key=kp.pub_hex,
        settings=s,
    )