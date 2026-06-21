"""Entrypoint: ``python -m app``.

Boots config, DB, crypto keypair, optional welcome seed, scheduler, then the
Flask app.
"""

from __future__ import annotations

import logging
import sys

from app import create_app
from app.config import load_settings
from app.crypto import ensure_keypair
from app.db import configure as db_configure, init_schema
from app.scheduler import start_scheduler
from app.services.seeding import seed_welcome_if_master


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    log = logging.getLogger("hydra")

    settings = load_settings()
    if not settings.admin_password:
        log.error("ADMIN_PASSWORD env var is required. Refusing to start.")
        return 2

    db_configure(settings.db_path)
    init_schema()
    ensure_keypair(settings)
    seed_welcome_if_master(settings)

    start_scheduler(settings)

    app = create_app(settings)
    log.info(
        "Hydra node '%s' listening on :%s (master=%s, neighbors=%s)",
        settings.node_name,
        settings.http_port,
        settings.is_master,
        settings.neighbors_mode,
    )
    app.run(host="0.0.0.0", port=settings.http_port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())