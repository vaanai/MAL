"""hv7_count: an OUTCOME-BLIND attempt counter for HV7 (E1, B and the 10% rules). Prints COUNTS only.

Reads ONLY the arm logs <root>/decisions/<L|R|S|U>/<day>.jsonl (not the SCAN log), rows with clock_start_ms <= ms < clock_end_ms, and looks at two row kinds:
  * `entry` (arms L, R, S, U): one attempt. Counted per arm and per UTC day. Fill, price, mark, est_ret and sell rows are never read.
  * `rank` (arm L): one row per period.
      - a FALLBACK is a rank row with fallback true and excluded not true. This includes an L call cut off by a crash or shutdown (cut_off true). Fallbacks
        come from these rows, NOT from the runner's late_reply counter (a diagnostic, lost on a restart, counting late replies only).
      - an EXCLUDED period is a rank row with excluded true (restart after t_e, rebuild mismatch): no entry in any arm.
Output: attempts per arm, a_l (L attempts; excluded periods have no entry so they are already out of it) and a_l_lt_20 (E1), rank_rows (non-excluded),
fallbacks, fallback_share and fallback_share_gt_10pct (the L fallback rule), excluded_periods, excluded_share and excluded_gt_10pct (the separate exclusion
cap; over it every arm is NOT EVALUABLE: not_evaluable), nonzero_days per arm and for any arm, torn_lines. No P&L, price, pool id or sha.
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
EXCLUDED_SHARE_LIMIT = 0.10
E1_MIN_A_L = 20


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


def count(root: str | Path, *, clock_start_ms: int, clock_end_ms: int) -> dict[str, Any]:
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
            if not isinstance(ms, int) or isinstance(ms, bool) or ms < clock_start_ms or ms >= clock_end_ms:
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
    periods = rank_rows + excluded
    xshare = (excluded / periods) if periods else None
    xover = (xshare > EXCLUDED_SHARE_LIMIT) if xshare is not None else None
    union = set().union(*days.values())
    return {"attempts": attempts, "a_l": attempts["L"], "a_l_lt_20": attempts["L"] < E1_MIN_A_L, "rank_rows": rank_rows, "fallbacks": fallbacks,
            "fallback_share": share, "fallback_share_gt_10pct": (share > FALLBACK_SHARE_LIMIT) if share is not None else None,
            "excluded_periods": excluded, "excluded_share": xshare, "excluded_gt_10pct": xover, "not_evaluable": bool(xover),
            "nonzero_days": {"any": len(union), **{a: len(days[a]) for a in R.ARM_ORDER}}, "torn_lines": torn,
            "clock_start_ms": clock_start_ms, "clock_end_ms": clock_end_ms}


def main(argv: list[str] | None = None, *, out=None) -> int:
    out = out if out is not None else sys.stdout
    ap = argparse.ArgumentParser(prog="hv7_count")
    ap.add_argument("--root", default=R.HV7_ROOT)
    ap.add_argument("--clock-start-ms", type=int, default=0, help="count only rows at or after this instant")
    ap.add_argument("--clock-end-ms", type=int, required=True, help="count only rows before this instant (required)")
    a = ap.parse_args(argv)
    try:
        res = count(a.root, clock_start_ms=a.clock_start_ms, clock_end_ms=a.clock_end_ms)
    except Exception as e:  # noqa: BLE001 - class name only
        print(f"refused {type(e).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(res, sort_keys=True), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
