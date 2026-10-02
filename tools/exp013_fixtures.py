"""Fixture helpers for the EXP-013 tests (tools/test_exp013_*.py). Test support only; synthetic data."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any, Sequence


def write_view_sha256(root: Path) -> Path:
    """`VIEW.sha256` (sha256sum format, `./`-relative, sorted) over every file under `root`."""
    lines = []
    for p in sorted(q for q in root.rglob("*") if q.is_file() and q.name != "VIEW.sha256"):
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root).as_posix()}")
    out = root / "VIEW.sha256"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def hours_between(first: str, last: str) -> list[str]:
    """Inclusive list of UTC hour keys `YYYY-MM-DDTHH`."""
    from tools.exp013_pool import _hour_range

    return _hour_range(first, last)


def synthetic_extra_rows(days: Sequence[str], seed: int = 11, per_day: int = 60, pool: str = "X") -> list[dict[str, Any]]:
    """Same row shape as tools.exp012_fixtures.synthetic_table_rows, for extra days (pool "X")."""
    from tools.exp011_freeze import FROZEN_FEATURE_NAMES, TARGET_SPEC_ID

    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for di, day in enumerate(days):
        for i in range(per_day):
            feats = {name: rng.random() for name in FROZEN_FEATURE_NAMES}
            signal = feats["buy_sol"] + 0.5 * rng.random()
            press = int(round((signal - 0.9) * 40_000_000 + rng.gauss(0, 15_000_000)))
            rows.append(
                {
                    "mint": f"x{di}-{i:03d}",
                    "spec": TARGET_SPEC_ID,
                    "day": day,
                    "status": 0,
                    "filled": rng.random() < 0.6,
                    "gross": press,
                    "flat": int(round(press * 1.3 - 2_000_000)),
                    "press": press,
                    "features": feats,
                    "pool": pool,
                }
            )
    return rows
