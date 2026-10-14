"""Shared helpers for the 2026-10-14 pump program-upgrade review scripts.

Public RPC only (api.mainnet-beta.solana.com). No Helius key, no key file, no sealed data is read.
Every real network attempt (retries included) is appended to RAW/rpc_calls.log with its instant. A hard stop refuses
any attempt at or after DEADLINE_UTC, and refuses to start while the boost_slice_watch job is running.
Counts and code facts only: nothing here reads a fill, exit, P&L, shadow, forward-paper or walk row.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]  # the worktree this file sits in
sys.path.insert(0, str(REPO))

import tools.pump_structure_monitor as m  # noqa: E402  (read-only import of the monitor)

RAW = Path(os.environ.get("UPGRADE_RAW", "/data/mal/program-upgrade-1014/raw"))
OUT = Path(__file__).resolve().parent / "out"
RAW.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
CALL_LOG = RAW / "rpc_calls.log"
DEADLINE_UTC = calendar.timegm((2026, 10, 14, 9, 25, 0))  # all public-RPC calls must be finished by 09:25Z
RPC_URL = m.DEFAULT_RPC
assert "helius" not in RPC_URL.lower()

DEPLOY_SLOT = 456494698  # pump program, from the 2026-10-14T07:11:10Z A3 run (tools/pump_structure_monitor.py reading)
DEPLOY_UTC = "2026-10-13T15:40:41Z"

TAG = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "py"


class DeadlinePassed(RuntimeError):
    pass


def iso_ms(t: float | None = None) -> str:
    t = time.time() if t is None else t
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def slice_watch_running() -> bool:
    """True if a `python -m tools.boost_slice_watch` process exists (scans /proc; a shell line that merely mentions the name does not count)."""
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if len(argv) >= 3 and b"python" in os.path.basename(argv[0]) and argv[1] == b"-m" and argv[2] == b"tools.boost_slice_watch":
            return True
    return False


def _guard() -> None:
    if time.time() >= DEADLINE_UTC:
        raise DeadlinePassed(f"public-RPC deadline {time.strftime('%H:%M:%SZ', time.gmtime(DEADLINE_UTC))} passed; no call made")


def logging_transport(url: str, body: bytes, timeout: float):
    """urllib_transport plus the deadline guard and one log line per real attempt."""
    _guard()
    method = json.loads(body).get("method")
    status, headers, payload = m.urllib_transport(url, body, timeout)
    with open(CALL_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{iso_ms()} {TAG} {method} http={status} bytes={len(payload)}\n")
    return status, headers, payload


class LClient(m.RpcClient):
    """The monitor's RpcClient, public RPC, logged and deadline-guarded. No cache."""

    def __init__(self, max_calls: int = 200, min_interval: float = 0.6) -> None:
        super().__init__(RPC_URL, min_interval=min_interval, max_calls=max_calls, timeout=120.0, transport=logging_transport)


class CClient(LClient):
    """LClient with an on-disk cache keyed by method + params (a cache hit makes no network attempt)."""

    def call(self, method: str, params):  # type: ignore[override]
        key = hashlib.sha1(json.dumps([method, list(params)], sort_keys=True).encode()).hexdigest()
        p = RAW / "cache" / f"{key}.json"
        if p.exists():
            return json.loads(p.read_text())["r"]
        r = super().call(method, params)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"method": method, "params": list(params), "r": r}))
        return r


def client(max_calls: int = 200, cached: bool = True):
    _guard()
    if slice_watch_running():
        raise SystemExit("refused: tools.boost_slice_watch is still running on the public RPC")
    return (CClient if cached else LClient)(max_calls=max_calls)


def acct(c, addr: str, **opts):
    """getAccountInfo (base64, finalized). Returns (bytes | None, value, ctx_slot)."""
    import base64

    o = {"encoding": "base64", "commitment": "finalized"}
    o.update(opts)
    r = c.call("getAccountInfo", [addr, o])
    v = (r or {}).get("value")
    if not v:
        return None, None, (r or {}).get("context", {}).get("slot")
    return base64.b64decode(v["data"][0]), v, r["context"]["slot"]


def write_json(name: str, obj: Any) -> None:
    (OUT / name).write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
