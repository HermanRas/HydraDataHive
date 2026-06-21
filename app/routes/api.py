"""HTTP API blueprint — ``/api/v1/*``.

All endpoints are open per spec (cluster trusts the LAN). Admin web UI uses
session cookies for /login.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, abort, current_app, jsonify, request, send_file

from app.crypto import sha256_bytes, verify_hex
from app.services import audit, data

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__)


def _settings():
    return current_app.config["HYDRA_SETTINGS"]


def _kp():
    return current_app.config["HYDRA_KEYPAIR"]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@bp.get("/identity")
def identity():
    s = _settings()
    kp = _kp()
    return jsonify(
        {
            "node_name": s.node_name,
            "is_master": s.is_master,
            "neighbors_mode": s.neighbors_mode,
            "http_port": s.http_port,
            "public_key": kp.pub_hex,
        }
    )


# ---------------------------------------------------------------------------
# Hello — imported lazily so we can wire neighbor logic after api is loaded
# ---------------------------------------------------------------------------


@bp.post("/hello")
def hello():
    from app.services import neighbor as neighbor_svc

    return neighbor_svc.handle_hello(request)


@bp.get("/neighbors")
def neighbors():
    from app.services import neighbor as neighbor_svc

    return jsonify(neighbor_svc.list_neighbors())


# ---------------------------------------------------------------------------
# Files index / manifest / chunk
# ---------------------------------------------------------------------------


@bp.get("/index")
def index():
    since = request.args.get("since")
    rows = data.files_index_since(since)
    return jsonify({"files": rows, "count": len(rows)})


@bp.get("/files/<int:file_id>/manifest")
def manifest(file_id: int):
    meta = data.get_file(file_id)
    if not meta:
        abort(404, "file not found")
    chunks = data.get_chunks(file_id)
    # Compute per-chunk SHA-256 from disk so receivers can verify.
    enriched = []
    for c in chunks:
        p = Path(c["path"])
        sha = sha256_bytes(p.read_bytes()) if p.exists() else None
        enriched.append(
            {
                "chunk_index": c["chunk_index"],
                "size_bytes": c["size_bytes"],
                "sha256": sha,
                "path": c["path"],
            }
        )
    return jsonify({"file": meta, "chunks": enriched})


@bp.get("/files/<int:file_id>/chunk/<int:idx>")
def chunk(file_id: int, idx: int):
    chunks = data.get_chunks(file_id)
    target = next((c for c in chunks if c["chunk_index"] == idx), None)
    if not target:
        abort(404, "chunk not found")
    p = Path(target["path"])
    if not p.exists():
        abort(404, "chunk file missing on disk")
    return send_file(str(p), mimetype="text/plain", download_name=p.name)


@bp.get("/files/<int:file_id>/download")
def download(file_id: int):
    """Reassemble a file and stream it to the caller."""
    meta = data.get_file(file_id)
    if not meta:
        abort(404, "file not found")
    tmp_dir = _settings().out_dir / "_downloads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / meta["name"]
    data.assemble_to_disk(file_id, dst)
    return send_file(str(dst), as_attachment=True, download_name=meta["name"])


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@bp.get("/audit")
def audit_tail():
    since = request.args.get("since")
    rows = audit.since(since)
    return jsonify({"entries": rows, "count": len(rows)})


@bp.get("/audit/verify")
def audit_verify():
    return jsonify(audit.verify_chain())