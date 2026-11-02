"""Launcher for the DEC-035 lane shadow with Helius websockets (paper only; reads no pinned file for writing).

The Helius api-key must never reach argv (visible in ps and /proc/<pid>/cmdline), a record, a status json or a log.
This launcher reads the key INSIDE the process from a mode-600 env file, builds the wss URLs in memory, calls
tools.h5_lane_shadow.main(argv) in-process (so the process argv stays key-free), and wraps stdout/stderr and the
logging record factory so any accidental occurrence of the key is replaced by REDACTED.

Layout: sockets i uses url i mod n; urls = [helius, public, helius] gives 2 Helius + 1 public socket with --sockets 3.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Sequence

from tools import h5_lane_shadow as ls

DEFAULT_ENV_FILE = "/var/lib/mal/fast-listener/helius.env"
KEY_NAMES = ("HELIUS_API_KEY", "HELIUS_KEY")
HELIUS_WS = "wss://mainnet.helius-rpc.com/?api-key="
PUBLIC_WS = "wss://api.mainnet-beta.solana.com"
REDACTED = "REDACTED"


def read_key(path: str) -> str:
    """Return the key from KEY=value lines (optional quotes / 'export '); never echoes the content on error."""
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            name, sep, val = line.partition("=")
            if sep and name.strip() in KEY_NAMES:
                val = val.strip().strip("'\"")
                if val:
                    return val
    raise ValueError(f"no {'/'.join(KEY_NAMES)} in {path}")


def build_ws_urls(key: str) -> list[str]:
    return [HELIUS_WS + key, PUBLIC_WS, HELIUS_WS + key]


def redact(text: str, key: str) -> str:
    return text.replace(key, REDACTED) if key else text


class RedactingStream:
    def __init__(self, inner, key: str) -> None:
        self._inner, self._key = inner, key

    def write(self, s):
        return self._inner.write(redact(s, self._key) if isinstance(s, str) else s)

    def flush(self):
        return self._inner.flush()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def install_redaction(key: str) -> None:
    sys.stdout = RedactingStream(sys.stdout, key)
    sys.stderr = RedactingStream(sys.stderr, key)
    old = logging.getLogRecordFactory()

    def factory(*a, **kw):
        rec = old(*a, **kw)
        try:
            rec.msg = redact(rec.getMessage(), key)
            rec.args = ()
        except Exception:
            rec.msg, rec.args = "log record redacted after format error", ()
        return rec

    logging.setLogRecordFactory(factory)


def main(argv: Sequence[str] | None = None) -> int:
    """argv: lane options only (--out-dir, --sockets, --syn-sockets, --h5-look2-observed, --max-seconds ...). --ws-url is refused."""
    rest = list(sys.argv[1:] if argv is None else argv)
    if any(a == "--ws-url" or a.startswith("--ws-url=") for a in rest):
        print("h5_lane_helius: --ws-url is built inside the launcher; refused on argv", file=sys.stderr)
        return 2
    path = os.environ.get("H5_HELIUS_ENV_FILE", DEFAULT_ENV_FILE)
    try:
        key = read_key(path)
    except (OSError, ValueError) as exc:
        print(f"h5_lane_helius: cannot read key ({type(exc).__name__})", file=sys.stderr)
        return 3
    install_redaction(key)
    full = ["--lane", ls.LANE_REF, *rest]
    for u in build_ws_urls(key):
        full += ["--ws-url", u]
    return ls.main(full)


if __name__ == "__main__":
    sys.exit(main())
