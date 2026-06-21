"""Centralized environment-variable driven config.

All env-var parsing lives here so the rest of the app can import a typed
``settings`` object rather than scattering ``os.environ`` lookups.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


def _truthy(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().upper() in {"TRUE", "1", "YES", "ON"}


def _split_list(val: str | None) -> List[str]:
    if not val:
        return []
    return [p.strip() for p in val.split(",") if p.strip()]


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "hydra-node"


@dataclass
class Settings:
    # Identity / networking
    node_name: str = field(default_factory=_hostname)
    http_port: int = 8080

    # Master behavior
    is_master: bool = True
    neighbors_mode: str = "MANUAL"  # "MANUAL" | "AUTO"

    # Auth (required)
    admin_password: str = ""

    # Storage paths
    data_dir: Path = field(default_factory=lambda: Path("/data"))
    keys_dir: Path = field(default_factory=lambda: Path("/keys"))

    # Peer bootstrap
    seed_peers: List[str] = field(default_factory=list)
    master_peers: List[str] = field(default_factory=list)

    # Sync behavior
    delete_local: bool = False
    max_file_size_mb: int = 2048

    # Constants from spec
    chunk_size_mb: int = 64  # 64 MB binary chunks (base64-encoded on disk)

    # Derived paths
    input_dir: Path = field(init=False)
    datastore_dir: Path = field(init=False)
    out_dir: Path = field(init=False)
    db_path: Path = field(init=False)
    key_path: Path = field(init=False)
    pub_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.keys_dir = Path(self.keys_dir)
        self.input_dir = self.data_dir / "input"
        self.datastore_dir = self.data_dir / "datastore"
        self.out_dir = self.data_dir / "out"
        self.db_path = self.data_dir / "hydra.db"
        self.key_path = self.keys_dir / "node.key"
        self.pub_path = self.keys_dir / "node.pub"


def load_settings() -> Settings:
    """Build a Settings object from process environment."""
    s = Settings(
        node_name=os.environ.get("NODE_NAME") or _hostname(),
        http_port=int(os.environ.get("HTTP_PORT", "8080")),
        is_master=_truthy(os.environ.get("MASTER"), default=True),
        neighbors_mode=(os.environ.get("NEIGHBORS", "MANUAL") or "MANUAL").upper(),
        admin_password=os.environ.get("ADMIN_PASSWORD", "") or "",
        data_dir=os.environ.get("DATA_DIR", "/data"),
        keys_dir=os.environ.get("KEYS_DIR", "/keys"),
        seed_peers=_split_list(os.environ.get("SEED_PEERS")),
        master_peers=_split_list(os.environ.get("MASTER_PEERS")),
        delete_local=_truthy(os.environ.get("DELETE_LOCAL"), default=False),
        max_file_size_mb=int(os.environ.get("MAX_FILE_SIZE_MB", "2048")),
    )

    # Ensure required dirs exist early.
    for d in (s.input_dir, s.datastore_dir, s.out_dir, s.keys_dir):
        d.mkdir(parents=True, exist_ok=True)

    return s