"""hv7_count: an OUTCOME-BLIND attempt counter for HV7 (E1, B and the 10% rule). Prints COUNTS only.

Reads the hash-chained decision logs under <root>/decisions/<SCAN|L|R|S|U>/<day>.jsonl and looks at three row kinds and nothing else:
  * `entry`            (arms L, R, S, U): one attempt. Counted per arm and per UTC day. Fill, price, mark, est_ret and sell rows are never read.
  * `rank` (arm L)     : one Haiku rank row per period. A FALLBACK is a rank row with fallback true and excluded not true. Fallbacks come from these rows,
                         NOT from the runner's late_reply counter (which is a diagnostic, lost on a restart, and counts a late reply only).
  * `period_excluded` / rank rows with excluded true: periods that got no entry in any arm (restart after t_e, in-flight shutdown, rebuild mismatch).
Output keys: attempts per arm, rank_rows, fallbacks, excluded_periods, fallback_share (fallbacks / non-excluded rank rows), fallback_share_gt_10pct,
nonzero_days (UTC days with at least one entry, per arm and any arm), torn_lines. No P&L, no price, no pool id, no sha.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools import hv7_runner as R
from tools.arena_declog import utc_day

FALLBACK_SHARE_LIMIT = 0.10


def _rows(root: Path, name: str):
    """Yield (row, torn) pairs for one stream. An unparseable line (a torn last line) is counted, never raised."""
    d = root / "decisions" / name
    for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
        for line in f.read_bytes().split(b"\n"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                yield None, True
                continue
            yield (row if isinstance(row, dict) else None), not isinstance(row, dict)


def count(root: str | Path, *, clock_start_ms: int = 0) -> dict[str, Any]:
    root = Path(R.check_path(root, "r"))
    torn = 0
    attempts = {a: 0 for a in R.ARM_ORDER}
    days: dict[str, set] = {a: set() for a in R.ARM_ORDER}
    rank_rows = fallbacks = excluded = 0
    for arm in R.ARM_ORDER:
        for row, bad in _rows(root, arm):
            if bad:
                torn += 1
                continue
            ms = row.get("ms")
            if not isinstance(ms, int) or isinstance(ms, bool) or ms < clock_start_ms:
                continue
            kind = row.get("kind")
            if kind == "entry":
                attempts[arm] += 1
                days[arm].add(utc_day(ms))
            elif arm == "L" and kind == "rank":
                if row.get("excluded") is True:
                    excluded += 1
                else:
                    rank_rows += 1
                    fallbacks += 1 if row.get("fallback") is True else 0
    share = (fallbacks / rank_rows) if rank_rows else None
    union = set().union(*days.values())
    return {"attempts": attempts, "rank_rows": rank_rows, "fallbacks": fallbacks, "excluded_periods": excluded, "fallback_share": share,
            "fallback_share_gt_10pct": (share > FALLBACK_SHARE_LIMIT) if share is not None else None,
            "nonzero_days": {"any": len(union), **{a: len(days[a]) for a in R.ARM_ORDER}}, "torn_lines": torn, "clock_start_ms": clock_start_ms}


def main(argv: list[str] | None = None, *, out=None) -> int:
    out = out if out is not None else sys.stdout
    ap = argparse.ArgumentParser(prog="hv7_count")
    ap.add_argument("--root", default=R.HV7_ROOT)
    ap.add_argument("--clock-start-ms", type=int, default=0, help="count only rows at or after this instant")
    a = ap.parse_args(argv)
    try:
        res = count(a.root, clock_start_ms=a.clock_start_ms)
    except Exception as e:  # noqa: BLE001 - class name only
        print(f"refused {type(e).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(res, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
