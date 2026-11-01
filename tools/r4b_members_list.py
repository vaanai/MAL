#!/usr/bin/env python3
"""EXP-027 post-E member list for the mint-account fetch (EXP-027 section 5.2 (iii) item 4; `EXP027_MINT_ACCOUNTS`). OUTCOME-BLIND, structure only.

What it does. Runs the same structure-only layout build the read's own pre-check runs (`tools/r4b_layout.build(..., structure_only=True, trade_end=TO)`;
`tools/r4b_read.structure_precheck`) over [FROM, TO) of the walk dir into a private scratch dir, takes the sorted distinct `mint` of the members table
(`meta.parquet`), writes them one per line to OUT (the input format of `tools/mint_account_fetch.py members --mints OUT`), prints a one-line JSON summary
(mints, sha256 of OUT, hours, build stats counts) and deletes the scratch dir. Nothing else is written.

What it reads and never reads. The structure-only build parses no side, sol_lamports, token_raw, quote_reserve, base_reserve or trader column
(`r4b_layout.TRADE_COLS_STRUCT`); the V0 value stays inside the build's own tables. OUT holds mint addresses only: no pool, slot, time, V or count per mint.
It computes no feature, score, rank, attempt, fill, exit or P&L. Hours must be sealed and strict-verified (the build refuses otherwise, exit 2).

For EXP-027 (manager job, after hour E + 2 h = 2026-11-07T05 is sealed):
  /data/mal/venv/bin/python -m tools.r4b_members_list --walk-dir /data/mal/blocks/forward-1016 --from-hour 2026-10-16T03 --to-hour 2026-11-07T06 --out DIR/members.txt
  (PYTHONPATH must reach duckdb, as for r4b-count.sh; run it under a 12G MemoryMax scope.)

Exit: 0 written, 2 refused (bad arguments, OUT exists or sits inside the walk dir, a layout refusal); nothing is written on a refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from tools import r4b_layout as rl

STATS_KEYS = ("members", "n_hours", "trade_rows", "pda_err")


class Refused(Exception):
    pass


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def run(walk_dir: Path, h0: str, h1: str, out: Path, *, canonical_fn: Any = None, memory_limit: str = "6GB", threads: int = 4,
        scratch_parent: Path | None = None) -> dict[str, Any]:
    """Build, write OUT (mints, sorted, one per line, LF), return the summary. Raises Refused or rl.LayoutRefused; OUT is written only on success."""
    import duckdb

    walk_dir, out = Path(walk_dir), Path(out)
    if out.exists():
        raise Refused(f"{out} exists: write-once, pick a fresh path")
    if _inside(out, walk_dir):
        raise Refused(f"{out} is inside the walk dir {walk_dir}")
    if not rl.hour_range(h0, h1):
        raise Refused(f"empty hour range [{h0}, {h1})")
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="r4b-members-", dir=str(scratch_parent or out.parent)))
    try:
        if _inside(scratch, walk_dir):
            raise Refused("scratch dir is inside the walk dir")
        stats = rl.build(walk_dir, h0, h1, scratch / "build", v_source="event", canonical_fn=canonical_fn, memory_limit=memory_limit, threads=threads,
                         tmp_dir=scratch / "tmp", structure_only=True, trade_end=h1)
        con = duckdb.connect()
        try:
            mints = [r[0] for r in con.sql(f"select distinct mint from read_parquet('{scratch / 'build' / 'meta.parquet'}') order by mint").fetchall()]
        finally:
            con.close()
        body = "".join(f"{m}\n" for m in mints).encode("ascii")
        tmp = out.with_name(out.name + ".part")
        tmp.write_bytes(body)
        tmp.replace(out)
        return {"out": str(out), "sha256": hashlib.sha256(body).hexdigest(), "mints": len(mints), "from_hour": h0, "to_hour": h1,
                "hours": len(rl.hour_range(h0, h1)), "build": {k: stats.get(k) for k in STATS_KEYS if k in stats}}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--from-hour", required=True, help="first hour, YYYY-MM-DDTHH (inclusive); EXP-027: 2026-10-16T03 (H0)")
    ap.add_argument("--to-hour", required=True, help="end hour, YYYY-MM-DDTHH (exclusive); EXP-027: 2026-11-07T06 (E + 3 h)")
    ap.add_argument("--out", required=True, help="members file to create (must not exist)")
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    try:
        summ = run(Path(a.walk_dir), a.from_hour, a.to_hour, Path(a.out), memory_limit=a.memory_limit, threads=a.threads)
    except (Refused, rl.LayoutRefused) as exc:
        reasons = getattr(exc, "reasons", None) or [str(exc)]
        print("REFUSED: " + " | ".join(str(r) for r in reasons), file=sys.stderr)
        return 2
    print(json.dumps(summ, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
