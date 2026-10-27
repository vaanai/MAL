#!/usr/bin/env python3
"""hv7_p0_count: the OUTCOME-BLIND P0 counter for HV7 (DESIGN sections 2 and 6, kill lines). Prints COUNTS only.

For each pre-clock day (2026-10-21..23 only, the same real-layout loader as the replay) it runs the runner's OWN screen (`Hv7Runner.screen`: hv6_cards.scan, the
vol_60m filter, cards through hv6_cards.build_card with the fake chain, the impact veto, screen_set with N_MIN = 3) at the hourly instants H:02:00 and, in the same
pass, at the half-hour instants H:32:00. It counts the periods whose screen holds at least N_MIN = 3 candidates (so a period is a trade in every arm). There is no
engine, no model call, no entry, no exit, no fill and no price: no outcome exists in this pass. It never prints a pool, mint, price or per-pool row.

Output (JSON): per day: hours, hours_ge3 (hourly periods with >= 3 screened candidates), half_hours_ge3 (periods of a 30-min schedule), and the same counts with
raw candidates >= 3 (before the vol/card/impact steps). Totals, mean per day, minimum day, and the DESIGN rule evaluated as booleans:
  hourly_mean_lt_10_7        -> DESIGN: below 10.7/day the registration switches to 30-min periods
  hourly_any_day_lt_5        -> kill line: any P0 day under 5 attempts
  chosen_period / chosen_mean / chosen_min_day / chosen_meets_10 / chosen_any_day_lt_5   (chosen = hourly unless hourly_mean_lt_10_7, else 30-min)
Pools created before the first day are unknown to the pass (the loader starts at 2026-10-21T00Z), so the first hours of the first day undercount. Declared BEFORE
the count: the FULL-day mean (all days, first day included; conservative, it biases toward the 30-min switch and the kill line) decides; hourly_mean_excl_first_day
is report-only. DESIGN names tools/hv7_count.py for the B/E1 decision-timestamp count and slot histogram; that is not in this tool.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import hv6_agents as A  # noqa: E402
from tools import hv6_cards as H  # noqa: E402
from tools import hv7_rank as RK  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.drill_hv6_item6 import FakeChain  # noqa: E402
from tools.drill_hv7_real_layout import ALLOWED_DAYS, DRILL_ROOT, END_EXCLUSIVE, ID_RE, Refused, day_ms, hour_scan, is_drill_root, parse_days  # noqa: E402

REAL_TAPE = H.TAPE_ROOT
HALF_MS = R.HOUR_MS // 2
HOURLY_SWITCH_BELOW = 10.7      # DESIGN section 2: 24 x q below this -> 30-min periods
KILL_DAY_BELOW = 5
TARGET_PER_DAY = 10.0


class NullEngine:
    """No pricing at all: P0 is a structure count. It takes rows and ignores them."""

    def on_row(self, row: dict) -> None:
        return None


class NoModel:
    def complete(self, request, now_ms):  # never called: _ask is replaced below
        raise AssertionError("P0 makes no model call")


def count_days(days: list[str], root: Path, tape: Path) -> dict[str, Any]:
    """The pass. `root` is a scratch decision-log dir under HV7_ROOT/drill (the runner's log needs one; nothing in it is read back)."""
    start, end = day_ms(days[0]), day_ms(days[-1]) + 86_400_000
    guard = R.Hv7Guard(root / "v7")
    run = R.Hv7Runner(R.Hv7Config(root=str(root / "v7"), frozen_sha="p0count0000000", clock_start_ms=start, phantom_filter=True), ArenaState(tape, start_ms=0),
                      NullEngine(), NoModel(), FakeChain(), guard)
    run._ask = lambda rr, T: None           # type: ignore[method-assign]  # P0: no model call of any kind
    want = frozenset(days)
    orig = run.state._hour_files
    run.state._hour_files = lambda: [t for t in orig() if t[0][:10] in want and t[0] < END_EXCLUSIVE]
    per_day: dict[str, dict[str, int]] = {d: {"hours": 0, "hours_ge3": 0, "hours_ge3_raw": 0, "half_hours": 0, "half_hours_ge3": 0, "half_hours_ge3_raw": 0} for d in days}
    from tools.arena_declog import utc_day

    T = start + R.T_D_OFFSET_MS
    while T < end:
        run.poll_to(T)
        run.sim_now = max(run.sim_now, T)
        run.pending = None
        run.screen(T)
        ge3 = run.pending is not None
        raw = 0
        rows = run.log.rows("SCAN")
        if rows and rows[-1].get("kind") == "period":
            raw = int(rows[-1].get("n_candidates", 0))
        d = per_day[utc_day(T)]
        hourly = T % R.HOUR_MS == R.T_D_OFFSET_MS
        if hourly:
            d["hours"] += 1
            d["hours_ge3"] += 1 if ge3 else 0
            d["hours_ge3_raw"] += 1 if raw >= RK.N_MIN else 0
        d["half_hours"] += 1
        d["half_hours_ge3"] += 1 if ge3 else 0
        d["half_hours_ge3_raw"] += 1 if raw >= RK.N_MIN else 0
        T += HALF_MS
    return {"per_day": per_day, "counters": dict(sorted(run.counters.items()))}


def rule(per_day: dict[str, dict[str, int]]) -> dict[str, Any]:
    days = sorted(per_day)
    hourly = [per_day[d]["hours_ge3"] for d in days]
    half = [per_day[d]["half_hours_ge3"] for d in days]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    h_mean, half_mean = mean(hourly), mean(half)
    h_tail = mean(hourly[1:])       # without the first day (its early hours have no pool history in this pass)
    switch = h_mean is not None and h_mean < HOURLY_SWITCH_BELOW
    chosen = "30min" if switch else "hourly"
    c = half if switch else hourly
    return {"days": len(days), "hourly_total": sum(hourly), "hourly_mean": h_mean, "hourly_min_day": min(hourly) if hourly else None, "hourly_mean_excl_first_day": h_tail,
            "half_hour_total": sum(half), "half_hour_mean": half_mean, "half_hour_min_day": min(half) if half else None,
            "hourly_mean_lt_10_7": switch, "hourly_any_day_lt_5": any(x < KILL_DAY_BELOW for x in hourly), "chosen_period": chosen,
            "chosen_mean": mean(c), "chosen_min_day": min(c) if c else None, "chosen_meets_10": bool(c) and mean(c) >= TARGET_PER_DAY,
            "chosen_any_day_lt_5": any(x < KILL_DAY_BELOW for x in c)}


def run_p0(days: list[str], drill_id: str, *, tape_dir: str | None = None, root: Path | None = None) -> dict[str, Any]:
    tape_dir = REAL_TAPE if tape_dir is None else tape_dir
    if tape_dir != REAL_TAPE:
        raise Refused("tape_dir_not_allowed")
    if not ID_RE.fullmatch(drill_id):
        raise Refused("drill_id")
    root = Path(root) if root is not None else DRILL_ROOT / drill_id
    if not is_drill_root(root):
        raise Refused("not_a_drill_root")
    R.check_path(root / "x", "w")
    root.mkdir(parents=True, exist_ok=True)
    info = hour_scan(Path(tape_dir), days)
    if info["hours_present"] == 0:
        raise Refused("no_hour_files_for_the_days")
    res = count_days(days, root, Path(tape_dir))
    return {"hv7_p0": "counts", **info, "first_day": days[0], "last_day": days[-1], "outcome_free": True, "per_day": res["per_day"], "rule": rule(res["per_day"]),
            "screen_counters": res["counters"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 P0: hours (and half-hours) with >= 3 screened candidates, per pre-clock day. Counts only; no outcome exists in this pass.")
    ap.add_argument("--days", default=",".join(ALLOWED_DAYS))
    ap.add_argument("--drill-id", required=True)
    a = ap.parse_args(argv)
    try:
        res = run_p0(parse_days(a.days), a.drill_id)
    except Refused as e:
        print(json.dumps({"hv7_p0": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only
        print(json.dumps({"hv7_p0": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
