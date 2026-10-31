"""Arming gate for the Kalshi LIP live lane (OFFLINE core).

The client may SEND only if ALL of these hold; otherwise the default is dry-run (logs the request it
would send, signs nothing, opens no connection):

1. an explicit ``--live`` flag;
2. a ``LIVE-ARMED`` file whose single hash line names the sha256 of the given DECLARE-LIVE declaration
   file, and that hash equals the sha256 of that file's bytes;
3. a key directory (default ``/var/lib/mal/kalshi-live/``) that is a real directory owned by the
   caller, mode 0700, holding ``kalshi.env`` and ``kalshi-private.pem`` as regular files, mode 0600.

``arm()`` returns an :class:`ArmedContext` or raises :class:`Refused` (machine-readable ``code``).
Error text never includes a value read from the key directory.  The private key is NOT read here.

MAL is paper-only today.  This module is not to be merged before a DEC amendment approved by the owner
allows a Kalshi key on a host.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

KEY_DIR_DEFAULT = Path("/var/lib/mal/kalshi-live")
ENV_FILE = "kalshi.env"
PEM_FILE = "kalshi-private.pem"
ARMED_NAME = "LIVE-ARMED"
DECLARATION_PREFIX = "DECLARE-LIVE"
ENVS = ("demo", "prod")
_HEX64 = re.compile(r"^(?:DECLARE-LIVE-SHA256[ \t:=]+)?([0-9a-f]{64})$")


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ArmedContext:
    key_id: str = field(repr=False)
    base_url: str
    env: str
    key_path: Path
    declaration_sha256: str


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _lstat(path, code: str, what: str):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        raise Refused(code, f"{what} is missing") from None
    except OSError:
        raise Refused(code, f"{what} cannot be inspected") from None


def read_armed_sha(armed_path) -> str:
    st = _lstat(armed_path, "no_armed_file", "LIVE-ARMED file")
    if not stat.S_ISREG(st.st_mode):
        raise Refused("armed_not_regular", "LIVE-ARMED is not a regular file")
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise Refused("armed_perms", "LIVE-ARMED must not be group/other writable")
    lines = [ln.strip() for ln in Path(armed_path).read_text().splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if len(lines) != 1:
        raise Refused("armed_malformed", "LIVE-ARMED must hold exactly one hash line")
    m = _HEX64.match(lines[0])
    if not m:
        raise Refused("armed_malformed", "LIVE-ARMED hash line is not 'DECLARE-LIVE-SHA256 <64 hex>'")
    return m.group(1)


def check_declaration(declaration_path) -> str:
    if declaration_path is None:
        raise Refused("no_declaration", "a DECLARE-LIVE declaration file is required")
    p = Path(declaration_path)
    if not p.name.startswith(DECLARATION_PREFIX):
        raise Refused("bad_declaration_name", f"declaration file name must start with {DECLARATION_PREFIX}")
    st = _lstat(p, "no_declaration", "declaration file")
    if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
        raise Refused("declaration_empty", "declaration must be a non-empty regular file")
    return file_sha256(p)


def parse_env(env_path) -> dict:
    out = {}
    for ln in Path(env_path).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def check_key_dir(key_dir) -> tuple[Path, Path]:
    d = Path(key_dir)
    st = _lstat(d, "key_dir_missing", "key directory")
    if not stat.S_ISDIR(st.st_mode):
        raise Refused("key_dir_missing", "key directory is not a real directory")
    if st.st_uid != os.geteuid():
        raise Refused("key_dir_owner", "key directory is not owned by the caller")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise Refused("key_dir_perms", "key directory must be 0700")
    paths = []
    for name in (ENV_FILE, PEM_FILE):
        fp = d / name
        fst = _lstat(fp, "key_file_missing", name)
        if not stat.S_ISREG(fst.st_mode):
            raise Refused("key_file_missing", f"{name} is not a regular file")
        if fst.st_uid != os.geteuid():
            raise Refused("key_file_owner", f"{name} is not owned by the caller")
        if stat.S_IMODE(fst.st_mode) != 0o600:
            raise Refused("key_file_perms", f"{name} must be mode 0600")
        paths.append(fp)
    return paths[0], paths[1]


def arm(live: bool, declaration, armed_file=None, key_dir=KEY_DIR_DEFAULT) -> ArmedContext:
    """All three conditions or :class:`Refused`.  ``live=False`` is always a refusal here: callers that
    want dry-run simply do not call ``arm`` (dry-run needs no key and sends nothing)."""
    if live is not True:
        raise Refused("no_live_flag", "--live was not given")
    key_dir = Path(key_dir)
    declared = check_declaration(declaration)
    armed = read_armed_sha(armed_file if armed_file is not None else key_dir / ARMED_NAME)
    if not hmac.compare_digest(declared, armed):
        raise Refused("sha_mismatch", "LIVE-ARMED hash does not match the declaration file")
    env_path, pem_path = check_key_dir(key_dir)
    env = parse_env(env_path)
    key_id, base_url, kenv = env.get("KALSHI_KEY_ID", ""), env.get("KALSHI_BASE_URL", ""), env.get("KALSHI_ENV", "")
    if not key_id:
        raise Refused("env_bad", "KALSHI_KEY_ID missing in kalshi.env")
    if kenv not in ENVS:
        raise Refused("env_bad", f"KALSHI_ENV must be one of {ENVS}")
    u = urlparse(base_url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise Refused("env_bad", "KALSHI_BASE_URL must be a plain https URL")
    return ArmedContext(key_id=key_id, base_url=base_url.rstrip("/"), env=kenv, key_path=pem_path,
                        declaration_sha256=declared)
