#!/usr/bin/env python3
"""N random graduated mints from the EXPLORATION tape (/data/mal/audit-1008/tape/migrations, September 2026 hours). Mint identities only: reads columns
type and mint, nothing else; never opens /data/mal/blocks/*. Graduated = type in ('complete', 'migration'). Distinct mints, sorted, then sampled with a
seeded RNG (seed 1, N 300). Output: one mint per line. Needs duckdb:

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python scripts/research/mint_census_input.py --out FILE
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

TAPE = Path("/data/mal/audit-1008/tape/migrations")
PREFIX = "2026-09"


def pick(mints: list[str], n: int, seed: int) -> list[str]:
    pool = sorted(set(mints))
    if n > len(pool):
        raise ValueError(f"only {len(pool)} distinct mints, wanted {n}")
    return sorted(random.Random(seed).sample(pool, n))


def load_mints(tape: Path = TAPE, prefix: str = PREFIX) -> list[str]:
    import duckdb
    files = sorted(str(p) for p in Path(tape).glob(f"{prefix}*.parquet"))
    if not files:
        raise SystemExit("no tape files")
    rows = duckdb.execute("select distinct mint from read_parquet(?) where type in ('complete','migration')", [files]).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tape", type=Path, default=TAPE)
    a = ap.parse_args()
    chosen = pick(load_mints(a.tape), a.n, a.seed)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(chosen) + "\n")
    print(f"wrote {len(chosen)} mints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
