"""Bootstrap seeding: drop a welcome.md on master boot if the datastore is empty."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.db import get_conn
from app.services.data import ingest_file

log = logging.getLogger(__name__)

WELCOME_BODY = """# Welcome to HydraDataHive

This file was seeded automatically because this node started with `MASTER=TRUE`.

- Drop new files into `data/input/` and they will be ingested within 5 minutes.
- Open `/data` in the web UI to see the datastore.
- Open `/neighbors` to approve incoming nodes.
- Open `/audit` to inspect the hash-chained audit log.

Happy replicating. 🐙
"""


def seed_welcome_if_master(settings: Settings) -> None:
    if not settings.is_master:
        return

    # Skip if any file already exists.
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"] > 0:
        return

    welcome_path = settings.input_dir / "welcome.md"
    if not welcome_path.exists():
        welcome_path.write_text(WELCOME_BODY, encoding="utf-8")

    try:
        file_id = ingest_file(
            settings,
            actor=settings.node_name,
            src_path=welcome_path,
            original_mtime=None,
        )
        log.info("Seeded welcome.md as file_id=%s", file_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to seed welcome.md: %s", exc)
        # Don't leave a half-ingested welcome file behind.
        try:
            if welcome_path.exists():
                welcome_path.unlink()
        except OSError:
            pass