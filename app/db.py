"""SQLite access layer.

Single connection per request is overkill for v1; we use a thread-safe
``get_conn()`` that lazily creates connections with WAL mode and a sane
``row_factory``.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Schema (kept in one place; applied by ``init_schema``)
# ---------------------------------------------------------------------------

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS files (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      name            TEXT NOT NULL,
      extension       TEXT,
      mime_type       TEXT,
      size_bytes      INTEGER NOT NULL,
      sha256          TEXT NOT NULL UNIQUE,
      chunk_count     INTEGER NOT NULL,
      uploaded_by     TEXT NOT NULL,
      signer_pubkey   TEXT NOT NULL,
      signature       TEXT NOT NULL,
      created_at      TEXT NOT NULL,
      updated_at      TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_files_updated ON files(updated_at);",
    """
    CREATE TABLE IF NOT EXISTS chunks (
      file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
      chunk_index     INTEGER NOT NULL,
      path            TEXT NOT NULL,
      size_bytes      INTEGER NOT NULL,
      PRIMARY KEY (file_id, chunk_index)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS neighbors (
      ip              TEXT PRIMARY KEY,
      port            INTEGER NOT NULL DEFAULT 8080,
      node_name       TEXT,
      public_key      TEXT,
      approved        INTEGER NOT NULL DEFAULT 0,
      first_seen      TEXT NOT NULL,
      last_online     TEXT,
      latency_ms      INTEGER,
      hello_sent      INTEGER NOT NULL DEFAULT 0,
      hello_failures  INTEGER NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_neighbors_approved ON neighbors(approved);",
    "CREATE INDEX IF NOT EXISTS idx_neighbors_hello_pending ON neighbors(hello_sent) WHERE hello_sent = 0;",
    """
    CREATE TABLE IF NOT EXISTS audit_log (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      ts              TEXT NOT NULL,
      actor           TEXT NOT NULL,
      action          TEXT NOT NULL,
      target          TEXT,
      details         TEXT,
      prev_hash       TEXT,
      entry_hash      TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);",
    """
    CREATE TABLE IF NOT EXISTS sync_state (
      neighbor_ip     TEXT NOT NULL,
      last_pulled_at  TEXT NOT NULL,
      last_index_ts   TEXT NOT NULL,
      PRIMARY KEY (neighbor_ip)
    );
    """,
    "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);",
]


_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_db_path: Optional[Path] = None


def configure(db_path: Path) -> None:
    """Configure the global DB path (called once at app boot)."""
    global _db_path
    _db_path = Path(db_path)


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("db.configure(path) must be called before use")
    conn = sqlite3.connect(
        _db_path,
        isolation_level=None,  # autocommit; we use explicit BEGIN where needed
        check_same_thread=False,
        timeout=30.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def get_conn() -> sqlite3.Connection:
    """Return a process-wide shared connection (thread-safe via lock)."""
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                _conn = _connect()
    return _conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Explicit transaction helper."""
    conn = get_conn()
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_schema() -> None:
    """Create tables if missing and stamp schema version."""
    conn = get_conn()
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (1,))