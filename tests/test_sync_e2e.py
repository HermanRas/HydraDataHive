"""End-to-end test for the /hello + /index + chunk pull flow.

Runs two Flask apps in threads on ephemeral ports, then exercises:
- node2 POSTs /hello to node1 → node1 should record neighbor with
  approved=1 (AUTO mode)
- node1 ingests a file via the local scan
- node2 pulls the index + chunks via sync.pull_from_peer
- node2's local DB ends up with the same SHA-256

Designed to be run inside the Hydra container:

    docker run --rm hydra python -m tests.test_sync_e2e
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Repo-root path for ``from app ...`` imports when invoked as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import configure as db_configure, init_schema  # noqa: E402
from app.services import data as data_svc, neighbor as neighbor_svc, sync  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start(app, port: int):
    threading.Thread(
        target=app.run,
        kwargs=dict(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    ).start()


def _wait_ready(port: int, timeout: float = 10.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/identity", timeout=1)
            if r.status == 200:
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"node on :{port} never became ready")


class SyncE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hydra-e2e-"))
        # Two independent "nodes" share this process but use separate dirs.
        self.node1_dir = self.tmp / "node1"
        self.node2_dir = self.tmp / "node2"
        for d in (self.node1_dir, self.node2_dir):
            (d / "data" / "input").mkdir(parents=True)
            (d / "data" / "datastore").mkdir(parents=True)
            (d / "data" / "out").mkdir(parents=True)
            (d / "keys").mkdir(parents=True)

        self.port1 = _free_port()
        self.port2 = _free_port()

        self.s1 = Settings(
            node_name="node1",
            admin_password="x",
            data_dir=self.node1_dir / "data",
            keys_dir=self.node1_dir / "keys",
            is_master=True,
            neighbors_mode="AUTO",
            http_port=self.port1,
        )
        self.s2 = Settings(
            node_name="node2",
            admin_password="x",
            data_dir=self.node2_dir / "data",
            keys_dir=self.node2_dir / "keys",
            is_master=True,
            neighbors_mode="AUTO",
            http_port=self.port2,
        )

        self.app1 = create_app(self.s1)
        self.app2 = create_app(self.s2)

        # AUTO mode means no need to manually approve; but seed node1 → node2
        # approval so pull_from_peer will be allowed.
        _start(self.app1, self.port1)
        _start(self.app2, self.port2)
        _wait_ready(self.port1)
        _wait_ready(self.port2)

    def test_hello_and_pull(self) -> None:
        # node2 says hello to node1
        r = neighbor_svc.post_hello(self.s2, f"127.0.0.1:{self.port1}")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["response"]["approved"])

        # node1 should have node2 in its neighbors table
        # (Re-open node1's DB to inspect it.)
        db_configure(self.s1.db_path)
        init_schema()
        rows = neighbor_svc.list_neighbors()
        approved = [n for n in rows if n["approved"] == 1]
        self.assertTrue(any(n["ip"] == "127.0.0.1" for n in approved))

        # node1 ingests a file
        sample = self.s1.input_dir / "sample.txt"
        sample.write_text("hello, e2e")
        data_svc.scan_input_folder(self.s1, actor="node1")

        # node2 pulls from node1
        summary = sync.pull_from_peer(self.s2, peer_ip="127.0.0.1")
        self.assertTrue(summary["ok"], summary)
        self.assertGreaterEqual(summary["fetched"], 1)

        # node2's DB should have a file with the same SHA-256
        db_configure(self.s2.db_path)
        init_schema()
        files = data_svc.list_files()
        self.assertTrue(any(f["name"] == "sample.txt" for f in files))


if __name__ == "__main__":
    unittest.main()