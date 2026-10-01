"""Tmp-dir fixture for the three exploration pools (A fast, C Oracle in-sample,
B Oracle live) -- test support only, no real data. Used by
tools/test_exploration_entry_filter_cell.py to prove the default B3 grid path is
decision-equivalent and to run the single-cell entry point end to end.

Every whitelisted hour of every pool gets a (mostly empty) file so the default
loaders (which require every hour) work; a deterministic set of synthetic mints
lives in the hours listed by `HOURS_OF_DAY`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.exploration_exits import POOL_HOURS as POOL_A_HOURS
from tools.oracle_insample_adapter import POOL_C_HOURS
from tools.oracle_live_adapter import POOL_B_CREATE_DAYS, POOL_B_HOURS

DAYS = ("2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27")
# Create hours (UTC hour-of-day) used per day; every one is a valid pool hour of the
# pool that owns the day (day 09-25 hours < 07 are pool C, >= 07 are pool B).
HOURS_OF_DAY = {
    "2026-09-19": (3, 8, 13, 18, 21),
    "2026-09-20": (2, 6, 11, 16, 20),
    "2026-09-21": (1, 5, 9, 14, 19),
    "2026-09-22": (2, 7, 12, 17, 22),
    "2026-09-23": (1, 6, 10, 15, 20),
    "2026-09-24": (3, 8, 13, 18, 23),
    "2026-09-25": (2, 5, 9, 15, 20),
    "2026-09-26": (1, 7, 12, 17, 22),
    "2026-09-27": (4, 9, 13, 18, 21),
}
MINTS_PER_HOUR = 2
BASE_RESERVE = 400_000_000_000_000


def epoch(hour: str) -> int:
    return int(datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def _bond(mint: str, slot: int, t_ms: int, quote: int, base: int, side: str, trader: str, sol: int) -> dict[str, Any]:
    return {
        "type": "trade", "mint": mint, "venue": "pump_bonding", "slot": slot, "t_recv_ms": t_ms,
        "block_time": t_ms // 1000, "quote_reserve": quote, "base_reserve": base,
        "sol_lamports": sol, "token_raw": sol * 30, "price_sol": quote / (base * 1000), "side": side,
        "trader": trader, "event_index": 1, "signature": f"b-{mint}-{slot}", "quote_is_wsol": True,
    }


def _swap(mint: str, slot: int, t_ms: int, quote: int, base: int) -> dict[str, Any]:
    row = _bond(mint, slot, t_ms, quote, base, "buy", "swapper", 1_000_000)
    row.update(venue="pumpswap", quote_is_wsol=True, signature=f"s-{mint}-{slot}")
    return row


def mint_specs() -> list[dict[str, Any]]:
    """Deterministic synthetic mints."""
    out: list[dict[str, Any]] = []
    k = 0
    for day in DAYS:
        for hod in HOURS_OF_DAY[day]:
            for j in range(MINTS_PER_HOUR):
                out.append({
                    "mint": f"M{k:03d}", "day": day, "hour": f"{day}T{hod:02d}", "j": j, "creator": f"cr{k % 6}",
                    "n_bond": 2 + (k * 7) % 5, "up": (k * 5 + j) % 3 != 0, "slot0": 100_000 + k * 1000,
                })
                k += 1
    return out


def mint_rows(spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """(create row, trade rows, create time ms)."""
    mint, slot0, base = spec["mint"], spec["slot0"], BASE_RESERVE
    t0 = (epoch(spec["hour"]) + 60 + 400 * spec["j"]) * 1000
    create = {"type": "create", "mint": mint, "slot": slot0, "block_time": t0 // 1000, "quote_reserve": 30_000_000_000,
              "base_reserve": base, "creator": spec["creator"], "signature": f"c-{mint}"}
    trades = []
    for i in range(spec["n_bond"]):
        side = "buy" if (i % 3 != 2 or spec["up"]) else "sell"
        trades.append(_bond(mint, slot0 + 1 + i, t0 + 1_000 + 500 * i, 30_000_000_000 + 1_000_000_000 * i, base,
                            side, f"t{(i + slot0) % 4}", 400_000_000 + 100_000_000 * i))
    s = slot0 + 100
    trades += [_swap(mint, s, t0 + 40_000, 80_000_000_000, base),
               _swap(mint, s + 1, t0 + 40_400, 80_000_000_000, base),
               _swap(mint, s + 2, t0 + 40_800, 80_000_000_000, base)]
    q = 128_000_000_000 if spec["up"] else 50_000_000_000
    for i in range(1, 6):
        trades.append(_swap(mint, slot0 + 110 + i, t0 + 60_000 + i * 20_000, q, base))
    return create, trades, t0


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _iso(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_pools(tmp: Path) -> tuple[Path, Path, Path]:
    """Return (fast, insample, live) roots under tmp, every whitelisted hour present."""
    fast, insample, live = tmp / "fast", tmp / "insample", tmp / "live"
    t_a: dict[str, list] = {h: [] for h in POOL_A_HOURS}
    c_a: dict[str, list] = {h: [] for h in POOL_A_HOURS}
    t_c: dict[str, list] = {h: [] for h in POOL_C_HOURS}
    c_c: dict[str, list] = {h: [] for h in POOL_C_HOURS}
    t_b: dict[str, list] = {h: [] for h in POOL_B_HOURS}
    obs: dict[str, list] = {d: [] for d in POOL_B_CREATE_DAYS}
    for spec in mint_specs():
        create, trades, t0 = mint_rows(spec)
        h = spec["hour"]
        if h in t_a:
            c_a[h].append(create)
            t_a[h].extend(trades)
        elif h in t_c:
            c_c[h].append(create)
            t_c[h].extend(trades)
        else:
            assert h in t_b, h
            obs[spec["day"]].append({
                "stream": "subscribeNewToken", "txType": "create", "mint": spec["mint"], "t_ws": _iso(t0),
                "traderPublicKey": spec["creator"], "signature": create["signature"],
                "vSolInBondingCurve": 30.0, "vTokensInBondingCurve": BASE_RESERVE / 1e6,
            })
            for r in trades:  # live tape: no block_time, pumpswap without quote_is_wsol
                r = dict(r)
                r.pop("block_time")
                if r["venue"] == "pumpswap":
                    r.pop("quote_is_wsol")
                t_b[h].append(r)
    for root, tt, cc in ((fast, t_a, c_a), (insample, t_c, c_c)):
        for h in tt:
            write_jsonl(root / "trades" / f"trades-{h}.jsonl", tt[h])
            write_jsonl(root / "creates" / f"creates-{h}.jsonl", cc[h])
    for h in t_b:
        write_jsonl(live / "trades" / f"trades-{h}.jsonl", t_b[h])
    for d, rows in obs.items():
        write_jsonl(live / "creates" / f"observe-{d}.jsonl", rows)
    return fast, insample, live
