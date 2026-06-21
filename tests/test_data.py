"""Unit tests for the data ingest service.

Run with: ``python -m pytest tests/`` (or ``python tests/test_data.py``).
"""

from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Allow running as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.crypto import ensure_keypair, sha256_file  # noqa: E402
from app.db import configure as db_configure, init_schema, get_conn  # noqa: E402
from app.services import data as data_svc  # noqa: E402


def _make_settings(tmp: Path) -> Settings:
    s = Settings(
        node_name="test-node",
        admin_password="x",
        data_dir=tmp / "data",
        keys_dir=tmp / "keys",
    )
    for d in (s.input_dir, s.datastore_dir, s.out_dir, s.keys_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


class DataIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hydra-test-"))
        self.settings = _make_settings(self.tmp)
        db_configure(self.settings.db_path)
        init_schema()
        self.kp = ensure_keypair(self.settings)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_input(self, name: str, payload: bytes) -> Path:
        p = self.settings.input_dir / name
        p.write_bytes(payload)
        return p

    def test_ingest_small_file(self) -> None:
        p = self._write_input("hello.txt", b"hello, hydra")
        fid = data_svc.ingest_file(
            self.settings, actor=self.settings.node_name, src_path=p, keypair=self.kp
        )
        meta = data_svc.get_file(fid)
        self.assertEqual(meta["name"], "hello.txt")
        self.assertEqual(meta["size_bytes"], len(b"hello, hydra"))
        self.assertEqual(meta["sha256"], sha256_file(p))
        self.assertEqual(meta["chunk_count"], 1)

        # Chunks on disk.
        chunks = data_svc.get_chunks(fid)
        self.assertEqual(len(chunks), 1)
        decoded = base64.b64decode(Path(chunks[0]["path"]).read_text())
        self.assertEqual(decoded, b"hello, hydra")

        # Audit row.
        from app.services import audit
        tail = audit.tail(5)
        self.assertTrue(any(r["action"] == "file.add" for r in tail))

    def test_ingest_dedupes_by_sha(self) -> None:
        p1 = self._write_input("a.bin", b"abc")
        p2 = self._write_input("b.bin", b"abc")
        f1 = data_svc.ingest_file(self.settings, actor="t", src_path=p1, keypair=self.kp)
        f2 = data_svc.ingest_file(self.settings, actor="t", src_path=p2, keypair=self.kp)
        self.assertEqual(f1, f2)

    def test_scan_input_archives_processed(self) -> None:
        self._write_input("one.txt", b"one")
        self._write_input("two.txt", b"two")
        ids = data_svc.scan_input_folder(self.settings, actor=self.settings.node_name)
        self.assertEqual(len(ids), 2)
        # input/ should be empty.
        leftovers = [p for p in self.settings.input_dir.iterdir() if p.is_file()]
        self.assertEqual(leftovers, [])
        # out/ should have the two files.
        archived = list(self.settings.out_dir.iterdir())
        self.assertEqual(len(archived), 2)

    def test_multi_chunk_round_trip(self) -> None:
        # Force the chunk size down so we exercise >1 chunk.
        original = data_svc.CHUNK_BIN_BYTES
        data_svc.CHUNK_BIN_BYTES = 8  # bytes, for test
        try:
            blob = b"".join(bytes([i % 256]) for i in range(40))
            p = self._write_input("multi.bin", blob)
            fid = data_svc.ingest_file(self.settings, actor="t", src_path=p, keypair=self.kp)
            meta = data_svc.get_file(fid)
            self.assertEqual(meta["chunk_count"], 5)
            chunks = data_svc.get_chunks(fid)
            self.assertEqual(len(chunks), 5)
            # Reassemble and verify.
            out = self.tmp / "assembled.bin"
            data_svc.assemble_to_disk(fid, out)
            self.assertEqual(out.read_bytes(), blob)
        finally:
            data_svc.CHUNK_BIN_BYTES = original


if __name__ == "__main__":
    unittest.main()