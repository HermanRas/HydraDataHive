"""Mesh sync: pull from approved neighbors on the 5-min tick.

For each approved peer:
1. GET /index?since=<last_index_ts> → list of new/updated files.
2. For each file: GET /files/<id>/manifest → chunk list + checksums.
3. GET /files/<id>/chunk/<idx> for each chunk → base64, verify SHA-256.
4. Reassemble locally; verify file signature with signer's pubkey.
5. Insert into files/chunks; audit ``sync.pull``.

New-version replacement: if we already have a file with the same name from
the same signer but older ``updated_at``, delete the old one after the new
copy is verified and assembled.

Conflict resolution: latest ``updated_at`` wins; ties → larger ``sha256`` wins.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from app.config import Settings
from app.crypto import sha256_file, verify_hex
from app.db import get_conn
from app.services import audit, data

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _split_host(spec: str) -> Tuple[str, int]:
    if ":" in spec:
        host, port = spec.rsplit(":", 1)
        return host, int(port)
    return spec, 8080


def _last_index_ts(peer_ip: str) -> Optional[str]:
    row = get_conn().execute(
        "SELECT last_index_ts FROM sync_state WHERE neighbor_ip = ?", (peer_ip,)
    ).fetchone()
    return row["last_index_ts"] if row else None


def _set_last_index_ts(peer_ip: str, ts: str) -> None:
    get_conn().execute(
        """
        INSERT INTO sync_state(neighbor_ip, last_pulled_at, last_index_ts)
        VALUES (?, ?, ?)
        ON CONFLICT(neighbor_ip) DO UPDATE SET
          last_pulled_at = excluded.last_pulled_at,
          last_index_ts  = excluded.last_index_ts
        """,
        (peer_ip, _now(), ts),
    )


def _last_audit_ts(peer_ip: str) -> str:
    row = get_conn().execute(
        "SELECT last_audit_ts FROM sync_state WHERE neighbor_ip = ?", (peer_ip,)
    ).fetchone()
    return row["last_audit_ts"] if row and row["last_audit_ts"] else ""


def _set_last_audit_ts(peer_ip: str, ts: str) -> None:
    get_conn().execute(
        """
        INSERT INTO sync_state(neighbor_ip, last_pulled_at, last_index_ts, last_audit_ts)
        VALUES (?, '', '', ?)
        ON CONFLICT(neighbor_ip) DO UPDATE SET
          last_audit_ts = excluded.last_audit_ts
        """,
        (peer_ip, ts),
    )


def fetch_index(peer: str, since: Optional[str] = None) -> Dict:
    host, port = _split_host(peer)
    url = f"http://{host}:{port}/api/v1/index"
    params = {"since": since} if since else {}
    r = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_audit(peer: str, since: Optional[str] = None) -> Dict:
    host, port = _split_host(peer)
    url = f"http://{host}:{port}/api/v1/audit"
    params = {"since": since} if since else {}
    r = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_manifest(peer: str, file_id: int) -> Dict:
    host, port = _split_host(peer)
    r = requests.get(
        f"http://{host}:{port}/api/v1/files/{file_id}/manifest",
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_chunk(peer: str, file_id: int, idx: int, dst_path: Path) -> None:
    host, port = _split_host(peer)
    r = requests.get(
        f"http://{host}:{port}/api/v1/files/{file_id}/chunk/{idx}",
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    dst_path.write_bytes(r.content)


def _download_one(settings: Settings, peer: str, fmeta: Dict) -> Optional[int]:
    """Pull a single file from a peer. Returns new local file_id or None on skip."""
    fid = int(fmeta["id"])
    sha = fmeta["sha256"]
    name = fmeta["name"]
    updated_at = fmeta["updated_at"]

    # If we already have the exact same sha, nothing to do.
    existing = get_conn().execute(
        "SELECT id, size_bytes FROM files WHERE sha256 = ?", (sha,)
    ).fetchone()
    if existing:
        return int(existing["id"])

    # Manifest
    manifest = fetch_manifest(peer, fid)
    fmeta_full = manifest["file"]
    chunks = manifest["chunks"]

    # Download chunks to a temp staging area keyed by remote file_id.
    staging = settings.datastore_dir / "_staging" / f"{fid}"
    staging.mkdir(parents=True, exist_ok=True)
    downloaded: List[Dict] = []
    try:
        for c in chunks:
            dst = staging / f"{int(c['chunk_index']):03d}.b64"
            fetch_chunk(peer, fid, int(c["chunk_index"]), dst)
            # Verify chunk SHA-256.
            chunk_sha = sha256_file(dst)
            if c.get("sha256") and chunk_sha != c["sha256"]:
                raise ValueError(
                    f"chunk {c['chunk_index']} sha256 mismatch "
                    f"(got {chunk_sha}, expected {c['sha256']})"
                )
            downloaded.append(
                {
                    "chunk_index": int(c["chunk_index"]),
                    "path": str(dst),
                    "size_bytes": int(c["size_bytes"]),
                }
            )

        new_id = data.ingest_payload(
            settings,
            actor=fmeta_full.get("uploaded_by") or peer,
            name=fmeta_full["name"],
            size=int(fmeta_full["size_bytes"]),
            sha256=fmeta_full["sha256"],
            chunk_count=int(fmeta_full["chunk_count"]),
            chunks=downloaded,
            signer_pubkey=fmeta_full["signer_pubkey"],
            signature=fmeta_full["signature"],
            mime_type=fmeta_full.get("mime_type"),
        )
        return new_id
    except Exception as exc:  # noqa: BLE001
        log.warning("Pull of file id=%s from %s failed: %s", fid, peer, exc)
        # Best-effort cleanup of staged chunks.
        for c in downloaded:
            try:
                Path(c["path"]).unlink()
            except OSError:
                pass
        return None


def _replace_old_version(name: str, signer: str, new_updated_at: str) -> None:
    """Delete local files with the same (name, uploaded_by) whose updated_at
    is older than the freshly-pulled one. Emits ``file.replace`` (not
    ``file.delete``) so peers don't cascade a redundant deletion.
    """
    rows = get_conn().execute(
        "SELECT id, updated_at FROM files WHERE name = ? AND uploaded_by = ?",
        (name, signer),
    ).fetchall()
    for r in rows:
        if r["updated_at"] >= new_updated_at:
            continue
        try:
            data.delete_file(
                int(r["id"]),
                actor="sync",
                action="file.replace",
                extra_details={"replaced_by_updated_at": new_updated_at},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to delete old version file_id=%s: %s", r["id"], exc)


def _process_remote_deletions(settings: Settings, peer: str, peer_ip: str) -> List[int]:
    """Fetch the peer's audit entries since ``last_audit_ts`` and apply
    ``file.delete`` events locally. Returns the list of locally-deleted file
    ids. Honours ``settings.delete_local`` — when False, we still note the
    deletion in a local audit entry but keep the file (orphan-marked).

    On the very first sync for a peer (no ``last_audit_ts`` yet), we seed
    ``last_audit_ts`` to "now" before processing so we don't try to replay
    the entire historical audit log.
    """
    since = _last_audit_ts(peer_ip)
    if not since:
        # First sync with this peer — anchor to current time and skip history.
        from datetime import datetime, timezone
        seed_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _set_last_audit_ts(peer_ip, seed_ts)
        return []
    try:
        resp = fetch_audit(peer, since=since)
    except Exception as exc:  # noqa: BLE001
        log.warning("Audit fetch from %s failed: %s", peer, exc)
        return []

    entries = resp.get("entries", [])
    deleted: List[int] = []
    max_ts = since or ""
    for e in entries:
        if e.get("action") != "file.delete":
            continue
        try:
            remote_id = int(e.get("target") or 0)
        except (TypeError, ValueError):
            continue
        if not remote_id:
            continue
        if not settings.delete_local:
            # Log the refusal locally but do not delete.
            audit.append(
                actor="sync",
                action="file.delete.skip",
                target=str(remote_id),
                details={"reason": "DELETE_LOCAL=FALSE", "from": peer_ip},
            )
            if e["ts"] > max_ts:
                max_ts = e["ts"]
            continue
        # Map the remote file id onto whatever local rows match by
        # (name, uploaded_by). The remote file id is informational only —
        # our local id will differ because of autoincrement.
        try:
            details = json.loads(e.get("details") or "{}")
        except Exception:
            details = {}
        name = details.get("name")
        if name:
            rows = get_conn().execute(
                "SELECT id, uploaded_by FROM files WHERE name = ?", (name,)
            ).fetchall()
            for r in rows:
                try:
                    if data.delete_file(int(r["id"]), actor=f"sync:{peer_ip}"):
                        deleted.append(int(r["id"]))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Local delete of file_id=%s failed: %s", r["id"], exc)
        if e["ts"] > max_ts:
            max_ts = e["ts"]

    if max_ts:
        _set_last_audit_ts(peer_ip, max_ts)
    return deleted


def pull_from_peer(settings: Settings, peer_ip: str) -> Dict:
    """Pull the delta from one peer; returns a summary dict."""
    peer_spec = _peer_spec(peer_ip)
    # Apply remote deletions first so a freshly-pulled copy in the same tick
    # doesn't reintroduce what we just removed.
    deleted_ids = _process_remote_deletions(settings, peer_spec, peer_ip)

    since = _last_index_ts(peer_ip)
    try:
        idx = fetch_index(peer_spec, since=since)
    except Exception as exc:  # noqa: BLE001
        log.warning("Index fetch from %s failed: %s", peer_ip, exc)
        return {"peer": peer_ip, "ok": False, "error": str(exc), "deleted_local": deleted_ids}

    files = idx.get("files", [])
    max_ts = since or ""
    new_ids: List[int] = []
    for f in files:
        nid = _download_one(settings, peer_spec, f)
        if nid is not None:
            new_ids.append(nid)
            if f["updated_at"] > max_ts:
                max_ts = f["updated_at"]
            _replace_old_version(f["name"], f.get("uploaded_by", peer_ip), f["updated_at"])

    if max_ts:
        _set_last_index_ts(peer_ip, max_ts)

    return {
        "peer": peer_ip,
        "ok": True,
        "fetched": len(files),
        "new_local": new_ids,
        "deleted_local": deleted_ids,
    }


def _peer_spec(peer_ip: str) -> str:
    """Return ``ip:port`` for a stored neighbor (looks up port in DB)."""
    row = get_conn().execute(
        "SELECT port FROM neighbors WHERE ip = ?", (peer_ip,)
    ).fetchone()
    port = int(row["port"]) if row else 8080
    return f"{peer_ip}:{port}"


def pull_from_approved_peers(settings: Settings) -> List[Dict]:
    rows = get_conn().execute(
        "SELECT ip FROM neighbors WHERE approved = 1"
    ).fetchall()
    results: List[Dict] = []
    for r in rows:
        results.append(pull_from_peer(settings, r["ip"]))
    return results