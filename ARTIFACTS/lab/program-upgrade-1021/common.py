"""Shared helpers for the 2026-10-21 pump / PumpSwap program-upgrade review scripts (precedent: ../program-upgrade-1014/common.py).

Public RPC only (api.mainnet-beta.solana.com). No Helius key, no key file, no sealed ledger is read. Every real network
attempt (retries included) is appended to RAW/rpc_calls.log. Counts and structure only: nothing here reads a fill, exit,
P&L, shadow, forward-paper or walk row, and no price path is saved.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import tools.pump_structure_monitor as m  # noqa: E402  (read-only import of the monitor)

RAW = Path(os.environ.get("UPGRADE_RAW", "/data2/mal/scratch/a3-repin-1021/raw"))
OUT = Path(__file__).resolve().parent / "out"
RAW.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
CALL_LOG = RAW / "rpc_calls.log"
RPC_URL = m.DEFAULT_RPC
assert "helius" not in RPC_URL.lower()
TAG = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "py"


def iso_ms(t: float | None = None) -> str:
    t = time.time() if t is None else t
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def logging_transport(url: str, body: bytes, timeout: float):
    method = json.loads(body).get("method")
    status, headers, payload = m.urllib_transport(url, body, timeout)
    with open(CALL_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{iso_ms()} {TAG} {method} http={status} bytes={len(payload)}\n")
    return status, headers, payload


class LClient(m.RpcClient):
    def __init__(self, max_calls: int = 200, min_interval: float = 0.6) -> None:
        super().__init__(RPC_URL, min_interval=min_interval, max_calls=max_calls, timeout=120.0, transport=logging_transport)
