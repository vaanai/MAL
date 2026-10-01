"""Synthetic-tape builders shared by the EXP-012 tests (tools/test_exp012_*.py).

Test support only: nothing in the production path imports this module. It
writes small, deterministic, fully synthetic fast-format tapes under a caller
supplied temp dir. It never refers to a real host path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence

T0_BASE_S = 1_790_000_000  # arbitrary synthetic epoch seconds; only differences matter


def write_zst_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write rows as JSONL and zstd-compress them to `path` (a `.zst` name)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = path.with_name(path.name[: -len(".zst")]) if path.name.endswith(".zst") else path
    with plain.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    if path.name.endswith(".zst"):
        subprocess.run(["zstd", "-q", "-f", "--rm", str(plain), "-o", str(path)], check=True)


def mint_tape(mint: str, block_s: int, *, creator: str = "creatorZ", slot0: int = 1000, n_bond: int = 3, later_after_s: int = 1900) -> tuple[list[dict], list[dict]]:
    """(create_rows, trade_rows) for one mint that creates, trades on the bonding
    curve, migrates to pumpswap, and prints again `later_after_s` after the
    migrate (past the 30-minute exit cap, so the exits resolve)."""
    quote0, base0 = 30_000_000_000, 1_000_000_000_000_000
    create = {
        "type": "create",
        "mint": mint,
        "slot": slot0,
        "block_time": block_s,
        "creator": creator,
        "signature": f"sig-create-{mint}",
        "quote_reserve": quote0,
        "base_reserve": base0,
    }
    trades: list[dict] = []
    for i in range(n_bond):
        trades.append(
            {
                "type": "trade",
                "venue": "pump_bonding",
                "side": "buy" if i % 3 != 2 else "sell",
                "quote_reserve": quote0 + (i + 1) * 5_000_000_000,
                "base_reserve": base0 - (i + 1) * 20_000_000_000_000,
                "slot": slot0 + 1 + i,
                "event_index": 1,
                "sol_lamports": 1_000_000_000 + i * 100_000_000,
                "trader": f"wallet{i}",
                "token_raw": 500_000 + i,
                "mint": mint,
                "t_recv_ms": (block_s + 1 + i) * 1000,
                "block_time": block_s + 1 + i,
                "signature": f"sig-b{i}-{mint}",
            }
        )
    mig_s = block_s + 10
    mig = {
        **trades[-1],
        "venue": "pumpswap",
        "side": "buy",
        "slot": slot0 + 50,
        "t_recv_ms": mig_s * 1000,
        "block_time": mig_s,
        "quote_is_wsol": True,
        "signature": f"sig-mig-{mint}",
        "trader": "walletM",
    }
    after = {
        **mig,
        "slot": slot0 + 60,
        "t_recv_ms": (mig_s + 1) * 1000,
        "block_time": mig_s + 1,
        "signature": f"sig-after-{mint}",
        "quote_reserve": int(mig["quote_reserve"] * 1.2),
        "trader": "walletN",
    }
    later = {
        **mig,
        "slot": slot0 + 90,
        "t_recv_ms": (mig_s + later_after_s) * 1000,
        "block_time": mig_s + later_after_s,
        "signature": f"sig-later-{mint}",
        "quote_reserve": int(mig["quote_reserve"] * 1.1),
        "trader": "walletO",
    }
    trades.extend([mig, after, later])
    return [create], trades


def hour_start_s(hour_key: str) -> int:
    from datetime import datetime, timezone

    return int(datetime.strptime(hour_key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def write_fast_format_root(root: Path, hours: Sequence[str], populated: dict[str, list[str]]) -> None:
    """A fast-format data root (trades/creates/migrations `<sub>-<hour>.jsonl.zst`)
    holding every whitelisted hour. `populated` maps hour key -> mint names to
    put in that hour (each created 60 s into the hour); all other hours are
    present but empty (a quiet hour still has its file)."""
    for h in hours:
        creates: list[dict] = []
        trades: list[dict] = []
        for k, mint in enumerate(populated.get(h, [])):
            c, t = mint_tape(mint, hour_start_s(h) + 60 + 5 * k, creator=f"creator-{mint}", slot0=1000 + 1000 * k)
            creates.extend(c)
            trades.extend(t)
        trades.sort(key=lambda r: (r["t_recv_ms"], r["slot"]))
        write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", trades)
        write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", creates)
        write_zst_jsonl(root / "migrations" / f"migrations-{h}.jsonl.zst", [])


def synthetic_table_rows(seed: int = 7, per_day: int = 60) -> list[dict[str, Any]]:
    """Deterministic stand-in for tools/exp011_build_table.py's table rows:
    9 UTC days (tools.exploration_entry_model_b3.DAYS_ALL) x `per_day` rows,
    features keyed by FROZEN_FEATURE_NAMES, one informative feature so the
    LODO has something to learn. Same row shape the freeze reads."""
    import random

    from tools.exp011_freeze import FROZEN_FEATURE_NAMES, TARGET_SPEC_ID
    from tools.exploration_entry_model_b3 import DAYS_ALL

    rng = random.Random(seed)
    pools = ("A", "C", "B")
    rows: list[dict[str, Any]] = []
    for di, day in enumerate(DAYS_ALL):
        for i in range(per_day):
            feats = {name: rng.random() for name in FROZEN_FEATURE_NAMES}
            signal = feats["buy_sol"] + 0.5 * rng.random()
            press = int(round((signal - 0.9) * 40_000_000 + rng.gauss(0, 15_000_000)))
            flat = int(round(press * 1.3 - 2_000_000))
            rows.append(
                {
                    "mint": f"m{di}-{i:03d}",
                    "spec": TARGET_SPEC_ID,
                    "day": day,
                    "status": 0,
                    "filled": rng.random() < 0.6,
                    "gross": press,
                    "flat": flat,
                    "press": press,
                    "features": feats,
                    "pool": pools[di % 3],
                }
            )
    rows.sort(key=lambda r: (r["day"], r["mint"], r["spec"]))
    return rows


def write_table_fixture(table_path: Path, rows: Sequence[dict[str, Any]]) -> None:
    """table.jsonl + table.md5 + row_counts.json, the trio exp011_freeze.load_table reads."""
    import hashlib

    table_path.parent.mkdir(parents=True, exist_ok=True)
    with table_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    md5 = hashlib.md5(table_path.read_bytes()).hexdigest()
    table_path.with_suffix(".md5").write_text(md5 + "\n", encoding="utf-8")
    manifest = {"pools": {}, "n_rows_total": len(rows), "days": sorted({r["day"] for r in rows})}
    (table_path.parent / "row_counts.json").write_text(json.dumps({"manifest": manifest, "table_md5": md5}) + "\n", encoding="utf-8")
