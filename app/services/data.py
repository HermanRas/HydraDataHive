"""File ingest + chunking + reassembly.

Local flow (``scan_input_folder`` on the 5-min tick):

1. Scan ``data/input/``.
2. For each new file: SHA-256, split into 64 MB chunks → base64-encode → write
   to ``datastore/<id>/NNN.b64``.
3. Insert ``files`` + ``chunks`` rows signed by this node's key.
4. Audit ``file.add``.
5. Move original out of ``input/`` (to ``out/<ts>_``).

Pull flow (neighbor sync) reuses :func:`ingest_payload` with the bytes
already downloaded.
"""

from __future__ import annotations

import base64
import logging
import math
import mimetypes
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.config import Settings
from app.crypto import (
    Keypair,
    chunk_sha256s,
    sha256_file,
    sign_file,
    sign_hex,
    verify_file,
    verify_hex,
)
from app.db import get_conn, transaction

log = logging.getLogger(__name__)

CHUNK_BIN_BYTES = 64 * 1024 * 1024  # 64 MiB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _guess_mime(name: str) -> str:
    mt, _ = mimetypes.guess_type(name)
    return mt or "application/octet-stream"


def _ext_of(name: str) -> str:
    i = name.rfind(".")
    return name[i + 1 :].lower() if i >= 0 else ""


# ---------------------------------------------------------------------------
# Local ingest
# ---------------------------------------------------------------------------

def ingest_file(
    settings: Settings,
    actor: str,
    src_path: Path,
    original_mtime: Optional[float] = None,
    keypair: Optional[Keypair] = None,
) -> int:
    """Chunk and persist a local file. Returns the new ``files.id``.

    The caller is responsible for moving the original out of ``input/`` once
    this returns successfully.
    """
    if keypair is None:
        from app.crypto import load_or_generate

        keypair = load_or_generate(settings.key_path, settings.pub_path)

    src_path = Path(src_path)
    size = src_path.stat().st_size
    if size > settings.max_file_size_mb * 1024 * 1024:
        raise ValueError(
            f"File too large: {size} bytes > max {settings.max_file_size_mb} MB"
        )

    sha = sha256_file(src_path)
    sig = sign_file(keypair, src_path)
    mime = _guess_mime(src_path.name)
    ext = _ext_of(src_path.name)
    chunk_count = max(1, math.ceil(size / CHUNK_BIN_BYTES))
    now = _now()

    with transaction() as conn:
        # Dedup by sha256.
        existing = conn.execute(
            "SELECT id FROM files WHERE sha256 = ?", (sha,)
        ).fetchone()
        if existing:
            return int(existing["id"])

        cur = conn.execute(
            """
            INSERT INTO files(name, extension, mime_type, size_bytes, sha256,
                              chunk_count, uploaded_by, signer_pubkey, signature,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                src_path.name,
                ext,
                mime,
                size,
                sha,
                chunk_count,
                actor,
                keypair.pub_hex,
                sig,
                now,
                now,
            ),
        )
        file_id = int(cur.lastrowid)
        file_dir = settings.datastore_dir / str(file_id)
        file_dir.mkdir(parents=True, exist_ok=True)

        # Stream chunks → base64 → .b64 files.
        # We store the on-disk (.b64) size because that is what a peer must
        # verify on receipt.
        with open(src_path, "rb") as fh:
            for idx in range(chunk_count):
                buf = fh.read(CHUNK_BIN_BYTES)
                if not buf:
                    break
                encoded = base64.b64encode(buf).decode("ascii")
                chunk_path = file_dir / f"{idx:03d}.b64"
                chunk_path.write_text(encoded)
                conn.execute(
                    "INSERT INTO chunks(file_id, chunk_index, path, size_bytes) "
                    "VALUES (?, ?, ?, ?)",
                    (file_id, idx, str(chunk_path), len(encoded)),
                )

    # Audit outside the transaction so an audit failure doesn't roll back.
    from app.services import audit

    audit.append(actor=actor, action="file.add", target=str(file_id), details={
        "name": src_path.name,
        "size": size,
        "sha256": sha,
        "chunks": chunk_count,
    })
    return file_id


def archive_original(src_path: Path, out_dir: Path) -> None:
    """Move a processed file out of ``input/`` into ``out/`` with a ts prefix."""
    if not src_path.exists():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dst = out_dir / f"{ts}_{src_path.name}"
    shutil.move(str(src_path), str(dst))


def scan_input_folder(settings: Settings, actor: str) -> List[int]:
    """Ingest every file currently in ``data/input/``. Returns new file_ids."""
    ingested: List[int] = []
    for path in sorted(settings.input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        try:
            mtime = path.stat().st_mtime
            fid = ingest_file(settings, actor=actor, src_path=path, original_mtime=mtime)
            archive_original(path, settings.out_dir)
            ingested.append(fid)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ingest failed for %s: %s", path, exc)
    return ingested


# ---------------------------------------------------------------------------
# Pull flow
# ---------------------------------------------------------------------------

def ingest_payload(
    settings: Settings,
    actor: str,
    *,
    name: str,
    size: int,
    sha256: str,
    chunk_count: int,
    chunks: List[Dict],
    signer_pubkey: str,
    signature: str,
    mime_type: Optional[str] = None,
) -> int:
    """Persist a file that was fetched from a neighbor.

    ``chunks`` is a list of dicts with keys: ``chunk_index``, ``path`` (str),
    ``size_bytes`` (int). The .b64 files must already be on disk; we verify
    their SHA-256s match before insert and verify the file signature last.
    """
    # Sanity-check chunk files exist + checksums match.
    for c in chunks:
        cpath = Path(c["path"])
        if not cpath.exists():
            raise FileNotFoundError(cpath)
        with open(cpath, "rb") as fh:
            actual_size = len(fh.read())
        if actual_size != int(c["size_bytes"]):
            raise ValueError(f"chunk {c['chunk_index']} size mismatch")

    # Verify the signature over the file SHA-256 using the origin's pubkey.
    if not verify_hex(signer_pubkey, sha256.encode("ascii"), signature):
        raise ValueError("signature verification failed")

    # Verify the on-disk concatenation matches sha256.
    computed = _hash_concat_chunks([Path(c["path"]) for c in chunks], chunk_count, sha256)
    if not computed:
        raise ValueError("file hash mismatch after assembly")

    now = _now()
    mime = mime_type or _guess_mime(name)
    ext = _ext_of(name)

    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM files WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if existing:
            return int(existing["id"])

        cur = conn.execute(
            """
            INSERT INTO files(name, extension, mime_type, size_bytes, sha256,
                              chunk_count, uploaded_by, signer_pubkey, signature,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, ext, mime, size, sha256, chunk_count, actor,
             signer_pubkey, signature, now, now),
        )
        new_id = int(cur.lastrowid)
        # Move chunks from staging into final datastore/<new_id>/.
        final_dir = settings.datastore_dir / str(new_id)
        final_dir.mkdir(parents=True, exist_ok=True)
        for c in chunks:
            src = Path(c["path"])
            idx = int(c["chunk_index"])
            dst = final_dir / f"{idx:03d}.b64"
            if src != dst:
                shutil.move(str(src), str(dst))
            conn.execute(
                "INSERT INTO chunks(file_id, chunk_index, path, size_bytes) "
                "VALUES (?, ?, ?, ?)",
                (new_id, idx, str(dst), int(c["size_bytes"])),
            )

    from app.services import audit
    audit.append(actor=actor, action="sync.pull", target=str(new_id), details={
        "name": name, "size": size, "sha256": sha256,
    })
    return new_id


def _hash_concat_chunks(paths: List[Path], expected_chunk_count: int, expected_sha: str) -> bool:
    import hashlib

    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as fh:
            h.update(base64.b64decode(fh.read()))
    return h.hexdigest() == expected_sha


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def list_files(limit: int = 500) -> List[Dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, extension, mime_type, size_bytes, sha256, chunk_count, "
        "uploaded_by, signer_pubkey, signature, created_at, updated_at "
        "FROM files ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def files_index_since(since_iso: Optional[str]) -> List[Dict]:
    conn = get_conn()
    if since_iso:
        rows = conn.execute(
            "SELECT id, name, size_bytes, sha256, uploaded_by, updated_at "
            "FROM files WHERE updated_at > ? ORDER BY updated_at ASC",
            (since_iso,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, size_bytes, sha256, uploaded_by, updated_at "
            "FROM files ORDER BY updated_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_file(file_id: int) -> Optional[Dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name, extension, mime_type, size_bytes, sha256, chunk_count, "
        "uploaded_by, signer_pubkey, signature, created_at, updated_at "
        "FROM files WHERE id = ?",
        (file_id,),
    ).fetchone()
    return dict(row) if row else None


def get_chunks(file_id: int) -> List[Dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT chunk_index, path, size_bytes FROM chunks WHERE file_id = ? ORDER BY chunk_index",
        (file_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_file(
    file_id: int,
    actor: str,
    action: str = "file.delete",
    extra_details: Optional[Dict] = None,
) -> bool:
    """Remove a file row + its chunks. Returns True if anything was deleted.

    ``action`` lets the caller emit a different audit event (e.g.
    ``file.replace`` when the deletion is part of a version swap). The
    emitted audit ``details`` always include ``{"name": ...}`` and are
    merged with ``extra_details`` if provided.
    """
    with transaction() as conn:
        row = conn.execute("SELECT name FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            return False
        name = row["name"]
        chunk_rows = conn.execute(
            "SELECT path FROM chunks WHERE file_id = ?", (file_id,)
        ).fetchall()
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    # Remove chunk files from disk after the DB commit.
    for c in chunk_rows:
        try:
            os.remove(c["path"])
        except OSError:
            pass
    # Clean up empty dir.
    from app.config import Settings  # local to avoid cycles in some importers
    try:
        for d in {Path(c["path"]).parent for c in chunk_rows}:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
    except OSError:
        pass

    from app.services import audit
    details: Dict = {"name": name}
    if extra_details:
        details.update(extra_details)
    audit.append(actor=actor, action=action, target=str(file_id), details=details)
    return True


def assemble_to_disk(file_id: int, dst: Path) -> Path:
    """Reassemble all chunks into a single file at ``dst``."""
    meta = get_file(file_id)
    if not meta:
        raise FileNotFoundError(file_id)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as out:
        for c in get_chunks(file_id):
            with open(c["path"], "rb") as fh:
                out.write(base64.b64decode(fh.read()))
    # Sanity check.
    if sha256_file(dst) != meta["sha256"]:
        dst.unlink(missing_ok=True)
        raise ValueError("assembled file hash mismatch")
    return dst