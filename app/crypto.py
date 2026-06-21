"""Ed25519 keypair management and file signing.

The private key lives at ``keys/node.key`` (PEM, 0600). The public key is
written alongside as hex to ``keys/node.pub`` so it's easy to copy/paste
between nodes without PEM parsing.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@dataclass
class Keypair:
    private: Ed25519PrivateKey
    public: Ed25519PublicKey
    pub_hex: str


def _ensure_perms(path: Path) -> None:
    try:
        os.chmod(path, 0o600 if path.name.endswith(".key") else 0o644)
    except OSError:
        # Best-effort; don't fail boot on permission issues (e.g. FAT volumes).
        pass


def load_or_generate(key_path: Path, pub_path: Path) -> Keypair:
    if key_path.exists():
        pem = key_path.read_bytes()
        priv = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            raise RuntimeError(f"{key_path} is not an Ed25519 key")
        pub = priv.public_key()
    else:
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_path.write_bytes(pem)
        _ensure_perms(key_path)

    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    pub_path.write_text(pub_hex + "\n")
    _ensure_perms(pub_path)
    return Keypair(private=priv, public=pub, pub_hex=pub_hex)


def ensure_keypair(settings) -> Keypair:
    """Convenience wrapper used at boot."""
    return load_or_generate(settings.key_path, settings.pub_path)


# ---------------------------------------------------------------------------
# File signing
# ---------------------------------------------------------------------------

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign_hex(kp: Keypair, data: bytes) -> str:
    sig = kp.private.sign(data)
    return sig.hex()


def sign_file(kp: Keypair, path: Path) -> str:
    """Sign the SHA-256 of a file; returns hex signature."""
    digest = sha256_file(path).encode("ascii")
    return sign_hex(kp, digest)


def verify_hex(pubkey_hex: str, data: bytes, signature_hex: str) -> bool:
    try:
        pub_bytes = bytes.fromhex(pubkey_hex)
        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


def verify_file(pubkey_hex: str, path: Path, signature_hex: str) -> bool:
    return verify_hex(pubkey_hex, sha256_file(path).encode("ascii"), signature_hex)


def chunk_sha256s(path: Path, chunk_size_bytes: int) -> Iterable[tuple[int, str, int]]:
    """Yield (index, sha256_hex, size_bytes) for each chunk of ``path``."""
    with open(path, "rb") as fh:
        idx = 0
        while True:
            buf = fh.read(chunk_size_bytes)
            if not buf:
                return
            yield idx, hashlib.sha256(buf).hexdigest(), len(buf)
            idx += 1