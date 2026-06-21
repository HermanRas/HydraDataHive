"""Neighbor hello handshake, approval, and mesh discovery.

A 'master' node handles inbound hellos (POST /api/v1/hello); a 'neighbor'
node calls :func:`post_hello` to its configured master and to discovered
peers (mesh expansion).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Request

from app.config import Settings
from app.crypto import load_or_generate
from app.db import get_conn, transaction

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 5.0
HELLO_MAX_FAILURES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _peer_key(spec: str) -> Tuple[str, int]:
    if ":" in spec:
        host, port = spec.rsplit(":", 1)
        return host, int(port)
    return spec, 8080


# ---------------------------------------------------------------------------
# Inbound hello (called from /api/v1/hello route)
# ---------------------------------------------------------------------------


def handle_hello(req: Request):
    """Process an inbound hello from another node."""
    from app.services import audit

    body = req.get_json(silent=True) or {}
    ip = req.headers.get("X-Forwarded-For", req.remote_addr or "0.0.0.0").split(",")[0].strip()
    node_name = body.get("node_name", "")
    pubkey = body.get("public_key", "")
    port = int(body.get("port", 8080))
    known_peers = body.get("known_peers", []) or []

    now = _now()
    auto_approve = (
        current_settings().neighbors_mode.upper() == "AUTO"
    )

    with transaction() as conn:
        row = conn.execute("SELECT * FROM neighbors WHERE ip = ?", (ip,)).fetchone()
        if row is None:
            approved = 1 if auto_approve else 0
            conn.execute(
                """
                INSERT INTO neighbors(ip, port, node_name, public_key, approved,
                                     first_seen, last_online, hello_sent, hello_failures)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)
                """,
                (ip, port, node_name, pubkey, approved, now, now),
            )
            audit.append(
                actor=node_name or "unknown",
                action="neighbor.hello",
                target=ip,
                details={"auto_approved": bool(auto_approve), "pubkey": pubkey[:16] + "…"},
                _conn=conn,
            )
        else:
            # Update last_online, name, pubkey if changed.
            conn.execute(
                """
                UPDATE neighbors
                SET last_online = ?, node_name = COALESCE(NULLIF(?, ''), node_name),
                    public_key = COALESCE(NULLIF(?, ''), public_key),
                    hello_sent = 1,
                    hello_failures = 0
                WHERE ip = ?
                """,
                (now, node_name, pubkey, ip),
            )

    # Persist any peer IPs the master itself didn't know about, so they get
    # hello'd in the next tick (mesh expansion).
    for peer in known_peers:
        try:
            host, pport = _peer_key(peer)
        except Exception:
            continue
        upsert_peer(host, port=pport)

    response = build_hello_response(include_audit=True)
    response["approved"] = bool(auto_approve) or is_approved(ip)
    return _json(response)


def _json(payload: Dict[str, Any]):
    from flask import jsonify
    return jsonify(payload)


def current_settings() -> Settings:
    from flask import current_app
    return current_app.config["HYDRA_SETTINGS"]


# ---------------------------------------------------------------------------
# Outbound hello
# ---------------------------------------------------------------------------


def post_hello(settings: Settings, peer_spec: str) -> Dict[str, Any]:
    """POST /hello to a peer; upsert the row + return the parsed response.

    On failure, increment the row's hello_failures counter and auto-pause
    (``hello_sent = -1``) after HELLO_MAX_FAILURES consecutive failures.
    """
    host, port = _peer_key(peer_spec)
    kp = load_or_generate(settings.key_path, settings.pub_path)
    body = {
        "node_name": settings.node_name,
        "public_key": kp.pub_hex,
        "port": settings.http_port,
        "known_peers": list(settings.seed_peers),
    }
    url = f"http://{host}:{port}/api/v1/hello"
    t0 = time.time()
    try:
        r = requests.post(url, json=body, timeout=HELLO_TIMEOUT)
        latency_ms = int((time.time() - t0) * 1000)
        r.raise_for_status()
        resp = r.json()
    except Exception as exc:  # noqa: BLE001
        latency_ms = None
        resp = None
        err = str(exc)

    now = _now()
    with transaction() as conn:
        row = conn.execute("SELECT * FROM neighbors WHERE ip = ?", (host,)).fetchone()
        if resp is not None:
            failures = 0
            conn.execute(
                """
                UPDATE neighbors
                SET port = ?, node_name = COALESCE(NULLIF(?, ''), node_name),
                    public_key = COALESCE(NULLIF(?, ''), public_key),
                    last_online = ?, latency_ms = ?, hello_sent = 1, hello_failures = 0
                WHERE ip = ?
                """,
                (port, resp.get("node_name", ""), resp.get("public_key", ""),
                 now, latency_ms, host),
            )
            # Insert new peers discovered in this response.
            for n in resp.get("neighbors", []) or []:
                try:
                    upsert_peer(n["ip"], port=int(n.get("port", 8080)))
                except Exception:
                    pass
            return {"ok": True, "peer": peer_spec, "response": resp, "latency_ms": latency_ms}
        else:
            new_failures = (row["hello_failures"] + 1) if row else 1
            paused = -1 if new_failures >= HELLO_MAX_FAILURES else 0
            conn.execute(
                """
                INSERT INTO neighbors(ip, port, node_name, public_key, approved,
                                       first_seen, last_online, hello_sent, hello_failures)
                VALUES (?, ?, NULL, NULL, 0, ?, NULL, ?, ?)
                ON CONFLICT(ip) DO UPDATE SET
                  hello_sent = excluded.hello_sent,
                  hello_failures = excluded.hello_failures
                """,
                (host, port, now, paused, new_failures),
            )
            return {"ok": False, "peer": peer_spec, "error": err, "failures": new_failures, "paused": paused == -1}


def upsert_peer(ip: str, port: int = 8080) -> None:
    """Insert a peer row with hello_sent=0 so the scheduler will greet it."""
    now = _now()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO neighbors(ip, port, node_name, public_key, approved,
                                  first_seen, last_online, hello_sent, hello_failures)
            VALUES (?, ?, NULL, NULL, 0, ?, NULL, 0, 0)
            ON CONFLICT(ip) DO NOTHING
            """,
            (ip, port, now),
        )


# ---------------------------------------------------------------------------
# Hello response payload
# ---------------------------------------------------------------------------


def build_hello_response(include_audit: bool = True) -> Dict[str, Any]:
    from app.services import audit, data

    conn = get_conn()
    nrows = conn.execute(
        "SELECT ip, port, node_name, public_key, approved, hello_sent, last_online "
        "FROM neighbors ORDER BY first_seen ASC"
    ).fetchall()
    files = data.files_index_since(None)
    payload: Dict[str, Any] = {
        "node_name": current_settings().node_name,
        "public_key": _kp_pub(),
        "neighbors": [dict(r) for r in nrows],
        "files_index": files,
    }
    if include_audit:
        payload["audit_tail"] = audit.tail(50)
    return payload


def _kp_pub() -> str:
    from flask import current_app
    return current_app.config["HYDRA_KEYPAIR"].pub_hex


# ---------------------------------------------------------------------------
# Approval / removal (used by web UI + CLI)
# ---------------------------------------------------------------------------


def approve(ip: str, actor: str = "admin") -> bool:
    from app.services import audit

    with transaction() as conn:
        row = conn.execute("SELECT ip FROM neighbors WHERE ip = ?", (ip,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE neighbors SET approved = 1 WHERE ip = ?", (ip,))
    audit.append(actor=actor, action="neighbor.approve", target=ip)
    return True


def reject(ip: str, actor: str = "admin") -> bool:
    """Reject = set approved=0 but keep the row (so future hellos get re-handled)."""
    from app.services import audit

    with transaction() as conn:
        row = conn.execute("SELECT ip FROM neighbors WHERE ip = ?", (ip,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE neighbors SET approved = 0 WHERE ip = ?", (ip,))
    audit.append(actor=actor, action="neighbor.reject", target=ip)
    return True


def remove(ip: str, actor: str = "admin") -> bool:
    from app.services import audit

    with transaction() as conn:
        row = conn.execute("SELECT ip FROM neighbors WHERE ip = ?", (ip,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM neighbors WHERE ip = ?", (ip,))
    audit.append(actor=actor, action="neighbor.remove", target=ip)
    return True


def reset_hello(ip: str, actor: str = "admin") -> bool:
    from app.services import audit

    with transaction() as conn:
        row = conn.execute("SELECT ip FROM neighbors WHERE ip = ?", (ip,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE neighbors SET hello_sent = 0, hello_failures = 0 WHERE ip = ?",
            (ip,),
        )
    audit.append(actor=actor, action="neighbor.reset_hello", target=ip)
    return True


def is_approved(ip: str) -> bool:
    row = get_conn().execute(
        "SELECT approved FROM neighbors WHERE ip = ?", (ip,)
    ).fetchone()
    return bool(row and row["approved"])


# ---------------------------------------------------------------------------
# Listing helpers
# ---------------------------------------------------------------------------


def list_neighbors() -> List[Dict]:
    rows = get_conn().execute(
        "SELECT ip, port, node_name, public_key, approved, hello_sent, hello_failures, "
        "first_seen, last_online, latency_ms "
        "FROM neighbors ORDER BY first_seen ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def list_neighbors_for_ui() -> List[Dict]:
    return list_neighbors()


def approved_ips() -> List[str]:
    rows = get_conn().execute("SELECT ip FROM neighbors WHERE approved = 1").fetchall()
    return [r["ip"] for r in rows]


def pending_hello_peers() -> List[Dict]:
    rows = get_conn().execute(
        "SELECT ip, port FROM neighbors WHERE hello_sent = 0"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Mesh discovery loop (called by scheduler)
# ---------------------------------------------------------------------------


def discover_mesh_peers(settings: Settings) -> int:
    """For every approved neighbor, fetch a fresh hello response so we learn
    about new peers; insert them as hello_sent=0 rows.
    """
    count = 0
    for ip in approved_ips():
        try:
            res = post_hello(settings, f"{ip}:{_port_for(ip)}")
            if res.get("ok"):
                count += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("discover_mesh_peers: hello to %s failed: %s", ip, exc)
    return count


def _port_for(ip: str) -> int:
    row = get_conn().execute("SELECT port FROM neighbors WHERE ip = ?", (ip,)).fetchone()
    return int(row["port"]) if row else 8080


def emit_hello_if_pending(settings: Settings) -> int:
    """POST /hello to every neighbor row where hello_sent = 0."""
    sent = 0
    for row in pending_hello_peers():
        try:
            res = post_hello(settings, f"{row['ip']}:{row['port']}")
            if res.get("ok"):
                sent += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("emit_hello: %s failed: %s", row["ip"], exc)
    return sent


def seed_peer_rows(settings: Settings) -> int:
    """Insert SEED_PEERS / MASTER_PEERS rows so the scheduler will hello them."""
    n = 0
    for spec in settings.seed_peers + settings.master_peers:
        try:
            host, port = _peer_key(spec)
            upsert_peer(host, port=port)
            n += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("seed_peer_rows: bad spec %r: %s", spec, exc)
    return n