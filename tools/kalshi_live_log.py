"""JSONL event log for the Kalshi LIP live lane (OFFLINE core, no network, no key).

Every refusal, cancel, dry-run request and send goes here as one JSON object per line.

The log must never carry credential material.  Three layers:

1. field names that denote credential material (key id, signature, private key, authorization) are
   replaced by ``"[redacted]"`` whatever their value;
2. values registered with :meth:`EventLog.add_forbidden` (the real key id, every signature the client
   produced) are scrubbed out of every string, and out of the serialised line a second time;
3. anything that looks like a PEM block is replaced.

The file is created 0600 and opened append-only.

Not wired to any network code.  MAL is paper-only today: this module is not to be merged before a DEC
amendment approved by the owner allows a Kalshi key on a host.
"""

from __future__ import annotations

import json
import os
import threading
import time
from decimal import Decimal
from pathlib import Path

REDACTED = "[redacted]"
MIN_FORBIDDEN_LEN = 8  # shorter values would mangle ordinary text

# Normalised (lower case, '_' -> '-') field names that are always redacted.
_REDACT_KEYS = frozenset({
    "signature", "kalshi-access-signature", "kalshi-access-key", "kalshi-access-timestamp-signature",
    "key-id", "keyid", "access-key", "api-key", "private-key", "pem", "authorization", "kalshi-key-id",
})


def _norm_key(k: str) -> str:
    return str(k).lower().replace("_", "-")


class EventLog:
    def __init__(self, path, clock=time.time, forbidden=()):
        self.path = Path(path)
        self.clock = clock
        self._forbidden: list[str] = []
        self._lock = threading.Lock()
        self.add_forbidden(*forbidden)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add_forbidden(self, *values) -> None:
        for v in values:
            if isinstance(v, str) and len(v) >= MIN_FORBIDDEN_LEN and v not in self._forbidden:
                self._forbidden.append(v)

    def _scrub_str(self, s: str) -> str:
        if "-----BEGIN" in s:
            return REDACTED
        for f in self._forbidden:
            if f in s:
                s = s.replace(f, REDACTED)
        return s

    def _scrub(self, o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                ks = self._scrub_str(str(k))
                out[ks] = REDACTED if _norm_key(k) in _REDACT_KEYS else self._scrub(v)
            return out
        if isinstance(o, (list, tuple, set, frozenset)):
            return [self._scrub(v) for v in o]
        if isinstance(o, str):
            return self._scrub_str(o)
        if isinstance(o, (bytes, bytearray)):
            return f"<{len(o)} bytes>"
        if isinstance(o, Decimal):
            return str(o)
        if o is None or isinstance(o, (bool, int, float)):
            return o
        return self._scrub_str(str(o))

    def emit(self, event: str, **fields) -> dict:
        rec = {"ts": round(float(self.clock()), 3), "event": str(event)}
        rec.update(self._scrub(fields))
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)
        for f in self._forbidden:  # second pass on the serialised line
            if f in line:
                line = line.replace(f, REDACTED)
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        return json.loads(line)

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]
