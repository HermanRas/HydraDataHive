"""Flask app factory.

Wires together:
- DB init
- crypto keypair
- auth
- web routes (HTML)
- API routes (JSON under /api/v1)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from flask import Flask

from app.config import Settings, load_settings
from app.crypto import load_or_generate
from app.db import configure as db_configure, init_schema
from app.services.seeding import seed_welcome_if_master

log = logging.getLogger(__name__)


def create_app(settings: Optional[Settings] = None) -> Flask:
    settings = settings or load_settings()
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["HYDRA_SETTINGS"] = settings
    app.config["HYDRA_ADMIN_PASSWORD"] = settings.admin_password
    app.secret_key = os.environ.get("FLASK_SECRET", settings.admin_password or "hydra")

    # ---- bring up DB + keypair + welcome seed (idempotent) ----
    db_configure(settings.db_path)
    init_schema()
    kp = load_or_generate(settings.key_path, settings.pub_path)
    app.config["HYDRA_KEYPAIR"] = kp
    seed_welcome_if_master(settings)

    # ---- blueprints ----
    from app.routes.api import bp as api_bp
    from app.routes.web import bp as web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.context_processor
    def inject_globals():
        return {
            "node_name": settings.node_name,
            "is_master": settings.is_master,
            "public_key_hex": kp.pub_hex,
        }

    return app