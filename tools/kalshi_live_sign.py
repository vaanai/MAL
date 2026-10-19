"""Kalshi request signing (OFFLINE core: no key is read at import, no network).

Per Kalshi's public API documentation, "API Keys / Signing requests":
    https://docs.kalshi.com/getting_started/api_keys
(read from the plan in LIVE-READINESS-DRAFT.md section 2; the doc page itself was NOT fetched while
building this PR, no kalshi.com host is contacted by this repo's tests.  Re-check the three header
names and the salt length against the page before the demo run.)

    message   = timestamp_ms_as_string + HTTP_METHOD + path
                path = the full request path INCLUDING the ``/trade-api/v2`` prefix, WITHOUT the query string
    signature = base64( RSA-PSS( SHA-256, MGF1(SHA-256), salt_length = digest length (32) ) over message )
    headers   = KALSHI-ACCESS-KEY (key id), KALSHI-ACCESS-TIMESTAMP (ms), KALSHI-ACCESS-SIGNATURE (base64)

The ``cryptography`` package is imported lazily so the caps / kill-switch / gate modules import without it.
Nothing here logs; callers register the key id and each signature with ``kalshi_live_log.EventLog``.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

DOC_URL = "https://docs.kalshi.com/getting_started/api_keys"
HDR_KEY = "KALSHI-ACCESS-KEY"
HDR_TS = "KALSHI-ACCESS-TIMESTAMP"
HDR_SIG = "KALSHI-ACCESS-SIGNATURE"
AUTH_HEADER_NAMES = (HDR_KEY, HDR_TS, HDR_SIG)
SALT_LEN = 32  # SHA-256 digest length = padding.PSS.DIGEST_LENGTH
MIN_RSA_BITS = 2048
METHODS = ("GET", "POST", "DELETE", "PUT")


class SignError(Exception):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as e:  # pragma: no cover - environment dependent
        raise SignError("the 'cryptography' package is required for signing") from e
    return hashes, serialization, padding, rsa


def sign_path(path: str) -> str:
    """The path as signed: query string stripped, must be absolute."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise SignError(f"signing path must start with '/': {path!r}")
    return path.split("?", 1)[0]


def signing_message(ts_ms: int, method: str, path: str) -> bytes:
    m = str(method).upper()
    if m not in METHODS:
        raise SignError(f"unsupported method {method!r}")
    if isinstance(ts_ms, bool) or not isinstance(ts_ms, int) or ts_ms <= 0:
        raise SignError("timestamp must be a positive integer (milliseconds)")
    return (str(ts_ms) + m + sign_path(path)).encode("ascii")


def load_private_key(pem_path):
    """Load an RSA private key PEM.  Refuses a symlink, a non-regular file, or any group/other permission."""
    p = Path(pem_path)
    st = os.lstat(p)
    if not stat.S_ISREG(st.st_mode):
        raise SignError("private key path is not a regular file")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise SignError("private key file must be 0600 (no group/other access)")
    _, serialization, _, rsa = _crypto()
    try:
        key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    except (ValueError, TypeError) as e:
        raise SignError("private key file is not a readable unencrypted PEM") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise SignError("only RSA keys are supported (RSA-PSS)")
    if key.key_size < MIN_RSA_BITS:
        raise SignError(f"RSA key below {MIN_RSA_BITS} bits")
    return key


def sign_message(private_key, message: bytes) -> str:
    hashes, _, padding, _ = _crypto()
    sig = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=SALT_LEN),
        hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def verify_message(public_key, message: bytes, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    hashes, _, padding, _ = _crypto()
    try:
        public_key.verify(
            base64.b64decode(signature_b64, validate=True), message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=SALT_LEN),
            hashes.SHA256())
        return True
    except (InvalidSignature, ValueError):
        return False


def auth_headers(key_id: str, private_key, method: str, path: str, ts_ms: int) -> dict:
    """The three documented auth headers for one request.  The timestamp is part of the signed message,
    so every retry must call this again with a fresh ``ts_ms``."""
    if not key_id or not isinstance(key_id, str):
        raise SignError("key id missing")
    sig = sign_message(private_key, signing_message(ts_ms, method, path))
    return {HDR_KEY: key_id, HDR_TS: str(ts_ms), HDR_SIG: sig}
