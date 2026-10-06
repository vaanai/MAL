"""PumpSwap virtual quote reserve V: per-pool map, fetched once, cached.

Every PumpSwap pool carries V (about 17.58 SOL on migrated pump.fun pools) in its pool account
(`tools.pumpswap_tx.parse_pool_account(...)["virtual_quote_reserves"]`). The swap math prices on
`quote_vault + V`; the tape's `quote_reserve` is the vault only. This module builds and loads the
pool -> V map. It never prints the RPC URL (`redact_rpc_url`); the key is read inside Python from the
walker env file.

A pool whose account is closed, too short, or has a V outside i64 has V = None in the map and is listed by
`missing_pools`. It is never silently 0.

The map file is JSON: {"fetched_utc": ..., "n_pools": ..., "calls": ..., "v": {pool: int|null}}.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

DEFAULT_MAP_PATH = Path("/data/mal/pumpswap-virtual/pool_v.json")
DEFAULT_ENV_FILE = "/var/lib/mal/backfill/helius.env"  # key stays there; read in Python only
BATCH = 100  # getMultipleAccounts limit
MAX_RPS = 5.0


V_OFFSET = 245  # pool account: V is a signed value at 245..261 (i128 LE); equals i64 at 245..253 today
PENDING_A_OFFSET, PENDING_B_OFFSET, DETAIL_MIN_LEN = 271, 279, 287  # two u64 counters A, B (meaning is a guess: pending fees / cashback)
_I64_MIN, _I64_MAX = -(2**63), 2**63 - 1


def parse_virtual_detail(data: bytes) -> dict[str, int | None]:
    """{"v", "pending", "v_base"} from raw pool-account bytes.

    v is the stored signed V: i128 LE at 245..261 when the account has those bytes, else i64 at 245..253;
    None only when the account is too short or the value does not fit in i64. A and B (u64 at 271..279 and
    279..287) are read only when the account is at least 287 bytes; pending = A + B and v_base = v + A + B.
    On every account seen (561), stored V = V0 - A - B: V0 (= v_base) is constant per pool, v is not.
    Negative V is a real value (the 321 'null' pools are V ~ 0 pools), not an unreadable account."""
    out: dict[str, int | None] = {"v": None, "pending": None, "v_base": None}
    if len(data) >= V_OFFSET + 16:
        v = int.from_bytes(data[V_OFFSET : V_OFFSET + 16], "little", signed=True)
    elif len(data) >= V_OFFSET + 8:
        v = int.from_bytes(data[V_OFFSET : V_OFFSET + 8], "little", signed=True)
    else:
        return out
    if not _I64_MIN <= v <= _I64_MAX:
        return out
    out["v"] = v
    if len(data) >= DETAIL_MIN_LEN:
        pending = int.from_bytes(data[PENDING_A_OFFSET : PENDING_A_OFFSET + 8], "little") + int.from_bytes(data[PENDING_B_OFFSET : PENDING_B_OFFSET + 8], "little")
        out["pending"] = pending
        out["v_base"] = v + pending
    return out


def parse_virtual(data: bytes) -> int | None:
    """Signed V from raw pool-account bytes (see `parse_virtual_detail`), or None when too short / out of i64."""
    return parse_virtual_detail(data)["v"]


def _rpc_url(env_file: str = DEFAULT_ENV_FILE) -> str:
    from tools import pumpswap_simulate as sim

    return sim.load_rpc_url(None, env_file)


def fetch_batch(url: str, pools: list[str]) -> list[int | None]:
    """getMultipleAccounts for up to BATCH pools. Null account / non-pool data -> None."""
    from tools.pump_history_backfill import redact_rpc_url

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [pools, {"encoding": "base64"}]}).encode()
    last: Exception | None = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            resp = json.load(urllib.request.urlopen(req, timeout=60))
            if "error" in resp:
                raise RuntimeError(str(resp["error"])[:200])
            out: list[int | None] = []
            for acc in resp["result"]["value"]:
                out.append(None if not acc else parse_virtual(base64.b64decode(acc["data"][0])))
            return out
        except Exception as exc:  # noqa: BLE001 -- the URL must never leak
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(redact_rpc_url(f"getMultipleAccounts failed: {type(last).__name__}: {last}"))


def build_map(pools: Iterable[str], *, existing: Mapping[str, Any] | None = None, fetch: Callable[[list[str]], list[int | None]] | None = None, rps: float = MAX_RPS, log: Callable[[str], None] | None = None) -> tuple[dict[str, int | None], int]:
    """Fetch V for every pool not already in `existing` (null entries are retried). Returns (map, calls).
    `fetch` is injectable for tests; the default talks to Helius at <= `rps` calls per second."""
    assert rps <= MAX_RPS, "walkers share Helius: keep rps <= 5"
    out: dict[str, int | None] = dict(existing or {})
    todo = sorted({p for p in pools if p and out.get(p) is None})
    if fetch is None:
        url = _rpc_url()
        fetch = lambda chunk: fetch_batch(url, chunk)  # noqa: E731
    calls = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i : i + BATCH]
        got = fetch(chunk)
        calls += 1
        if len(got) != len(chunk):
            raise ValueError("fetch returned a different number of results than pools")
        for p, v in zip(chunk, got):
            out[p] = v
        if log and calls % 10 == 0:
            log(f"fetched {i + len(chunk)}/{len(todo)} pools in {calls} calls")
        time.sleep(1.0 / rps)
    return out, calls


def save_map(path: Path, vmap: Mapping[str, int | None], calls: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "n_pools": len(vmap), "calls": calls, "n_null": sum(1 for v in vmap.values() if v is None), "v": dict(sorted(vmap.items()))}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_map(path: Path = DEFAULT_MAP_PATH) -> dict[str, int | None]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: (None if v is None else int(v)) for k, v in doc["v"].items()}


def missing_pools(vmap: Mapping[str, int | None], pools: Iterable[str] | None = None) -> list[str]:
    keys = vmap.keys() if pools is None else pools
    return sorted(p for p in keys if vmap.get(p) is None)
