#!/usr/bin/env python3
"""EXP-011 Phase A: build the per-migration feature + label table once,
streaming to disk, and write it to /home/claude/data/exp011/table.jsonl.

This is the ONE heavy pass over pools A + C + B (tools/exp011_freeze.py's
load_tp50_rows, unchanged feature set / whitelists / assertions -- see that
module's own docstring and the holdout fence it asserts at import time).
Phase B (tools/exp011_freeze.py's freeze()) is meant to read this table
back off disk and never touch the raw tape again -- train, LODO, and the
nested fixed-threshold LODO are all "light" once this file exists.

Memory: each pool's run_all_features_* streams its workers' rows straight
to a scratch dir (out_dir/poolA|poolC|poolB/*.jsonl) instead of holding
them in memory (tools.exploration_entry_model.run_worker_features's
rows_out_path). Keep --max-workers <= 2 on this box (mal-fast-0, 22 GB,
no swap, user-1002.slice MemoryMax=15G) -- see docs/HOSTS.md and the
fast-box-memory-budget note. Per-worker RSS is printed to stderr by the
streaming worker itself (rss_mb=... on every hour boundary); run this
under a wrapper that tees stderr to a log and kills the process tree if
any worker's RSS exceeds 5 GB (see tools/exp011_rss_monitor.sh).

Run: nice -n 19 python3 -m tools.exp011_build_table --max-workers 2
[--out /home/claude/data/exp011/table.jsonl] [--scratch-dir /home/claude/data/exp011/scratch]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from tools.exp011_freeze import TARGET_SPEC_ID, load_tp50_rows


def _md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_table(
    out_path: Path, scratch_dir: Path, max_workers: int = 2, buffer_hours: int = 24, max_home_hours: int | None = 12
) -> dict[str, Any]:
    t0 = time.time()
    rows, manifest = load_tp50_rows(
        max_workers=max_workers, buffer_hours=buffer_hours, out_dir=scratch_dir, max_home_hours=max_home_hours
    )
    wall_s = time.time() - t0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    row_counts_pool: dict[str, int] = {}
    row_counts_day: dict[str, int] = {}
    for r in rows:
        row_counts_pool[r["pool"]] = row_counts_pool.get(r["pool"], 0) + 1
        row_counts_day[r["day"]] = row_counts_day.get(r["day"], 0) + 1

    table_md5 = _md5_of_file(out_path)
    (out_path.with_suffix(".md5")).write_text(table_md5 + "\n", encoding="utf-8")

    counts_doc = {
        "schema": "exp011_table_row_counts_v1",
        "target_spec": TARGET_SPEC_ID,
        "n_rows_total": len(rows),
        "by_pool": row_counts_pool,
        "by_day": dict(sorted(row_counts_day.items())),
        "manifest": manifest,
        "table_md5": table_md5,
        "wall_s": wall_s,
        "max_workers": max_workers,
        "buffer_hours": buffer_hours,
        "max_home_hours": max_home_hours,
    }
    (out_path.parent / "row_counts.json").write_text(json.dumps(counts_doc, indent=2) + "\n", encoding="utf-8")
    print(f"exp011_build_table: wrote {out_path} n_rows={len(rows)} md5={table_md5} wall_s={wall_s:.1f}", file=sys.stderr, flush=True)
    return counts_doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/home/claude/data/exp011/table.jsonl")
    ap.add_argument("--scratch-dir", default="/home/claude/data/exp011/scratch")
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument(
        "--buffer-hours",
        type=int,
        default=24,
        help="hours each chunk keeps reading past its home window; 24 h covers 98.7%% of create->migrate lags (pool A, 2026-09-30)",
    )
    ap.add_argument("--max-home-hours", type=int, default=12, help="max home hours per chunk (bounds watch/hot memory); 0 = old per-worker windows")
    args = ap.parse_args()
    assert args.max_workers <= 2, "keep max-workers <= 2 for this Phase A run -- see tools/exp011_build_table.py's docstring"
    build_table(
        Path(args.out),
        Path(args.scratch_dir),
        max_workers=args.max_workers,
        buffer_hours=args.buffer_hours,
        max_home_hours=(args.max_home_hours or None),
    )


if __name__ == "__main__":
    main()
