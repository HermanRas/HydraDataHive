"""Hash-chained append-only audit log.

Each row stores ``prev_hash`` (the previous row's ``entry_hash``) and its own
``entry_hash = sha256(prev_hash || ts || actor || action || target || details)``.

The web UI can recompute the chain on demand to verify integrity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.db import get_conn, transaction

GENESIS_HASH = "0" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(actor: str, action: str, target: Optional[str], details: Optional[str]) -> bytes:
    payload = f"{actor}|{action}|{target or ''}|{details or ''}"
    return payload.encode("utf-8")


def append(
    actor: str,
    action: str,
    target: Optional[str] = None,
    details: Optional[Dict[str, Any] | str] = None,
    *,
    _conn=None,
) -> int:
    """Append an audit entry; returns the new row id.

    If ``_conn`` is supplied (caller already in a transaction), reuse it
    instead of opening a nested transaction.
    """
    if not isinstance(details, str):
        details = json.dumps(details or {}, separators=(",", ":"), sort_keys=True)
    ts = _now()
    if _conn is not None:
        conn = _conn
        _write_row(conn, ts, actor, action, target, details, prev_lookup=True)
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    with transaction() as conn:
        _write_row(conn, ts, actor, action, target, details, prev_lookup=True)
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _write_row(conn, ts, actor, action, target, details, prev_lookup: bool):
    prev_hash = GENESIS_HASH
    if prev_lookup:
        prev = conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if prev:
            prev_hash = prev["entry_hash"]
    entry_hash = hashlib.sha256(
        b"|".join(
            [
                prev_hash.encode("ascii"),
                ts.encode("utf-8"),
                _canonical(actor, action, target, details),
            ]
        )
    ).hexdigest()
    conn.execute(
        "INSERT INTO audit_log(ts, actor, action, target, details, prev_hash, entry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, actor, action, target, details, prev_hash, entry_hash),
    )


def tail(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, ts, actor, action, target, details, prev_hash, entry_hash "
        "FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def since(ts: Optional[str]) -> List[Dict[str, Any]]:
    conn = get_conn()
    if ts:
        rows = conn.execute(
            "SELECT id, ts, actor, action, target, details, prev_hash, entry_hash "
            "FROM audit_log WHERE ts > ? ORDER BY id ASC",
            (ts,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, ts, actor, action, target, details, prev_hash, entry_hash "
            "FROM audit_log ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def verify_chain() -> Dict[str, Any]:
    """Recompute every entry's hash and check the prev_hash links."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, ts, actor, action, target, details, prev_hash, entry_hash "
        "FROM audit_log ORDER BY id ASC"
    ).fetchall()
    expected_prev = GENESIS_HASH
    bad: List[int] = []
    checked = 0
    for r in rows:
        checked += 1
        # Recompute this row's hash.
        computed = hashlib.sha256(
            b"|".join(
                [
                    expected_prev.encode("ascii"),
                    r["ts"].encode("utf-8"),
                    _canonical(r["actor"], r["action"], r["target"], r["details"]),
                ]
            )
        ).hexdigest()
        if r["prev_hash"] != expected_prev or r["entry_hash"] != computed:
            bad.append(r["id"])
        expected_prev = r["entry_hash"]
    return {"ok": not bad, "checked": checked, "bad_ids": bad}


def iter_all() -> Iterable[Dict[str, Any]]:
    conn = get_conn()
    for r in conn.execute(
        "SELECT id, ts, actor, action, target, details, prev_hash, entry_hash "
        "FROM audit_log ORDER BY id ASC"
    ):
        yield dict(r)