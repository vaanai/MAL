"""Test helpers for the Kalshi live core.  Generates THROWAWAY key material at test time inside a
caller-supplied tmp dir; nothing here holds a key, and nothing is read from /var/lib/mal."""

from __future__ import annotations

import os
from pathlib import Path

FAKE_KEY_ID = "test-key-id-00000000-aaaa-bbbb-cccc-111111111111"
FAKE_BASE_URL = "https://kalshi-offline.invalid/trade-api/v2"  # .invalid never resolves


def make_rsa_key(bits: int = 2048):
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def pem_bytes(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def make_key_dir(root: Path, key=None, key_id: str = FAKE_KEY_ID, base_url: str = FAKE_BASE_URL,
                 env: str = "demo") -> tuple[Path, object]:
    """Write ``root/kalshi.env`` and ``root/kalshi-private.pem`` (dir 0700, files 0600)."""
    key = key or make_rsa_key()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    envp, pemp = root / "kalshi.env", root / "kalshi-private.pem"
    envp.write_text(f"KALSHI_KEY_ID={key_id}\nKALSHI_BASE_URL={base_url}\nKALSHI_ENV={env}\n")
    pemp.write_bytes(pem_bytes(key))
    os.chmod(envp, 0o600)
    os.chmod(pemp, 0o600)
    return root, key
