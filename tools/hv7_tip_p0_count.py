#!/usr/bin/env python3
"""hv7_tip_p0_count: the OUTCOME-BLIND P0 counter for HV7 on the fast-0 TIP source (QP-RULING-1101-arena-tip-reader section 4; DEC-031 Amendment 23 item 8, "P0").
A NEW pinned file. `tools/hv7_p0_count.py` and `tools/drill_hv7_real_layout.py` keep their bytes and their public-tape refusals (10-21..23); this variant is the tip version.

It runs the runner's OWN screen (`Hv7Runner.screen`, so rule U2 of `tools/hv7_tip_guard.py` is the same function, not a copy) over the derived copy's tape at the hourly instants H:02:00
and the half-hour instants H:32:00 and counts the periods whose screen holds at least N_MIN = 3 candidates. The state is built FROM H0, the first hour of the derived copy's continuous
history (QP-RULING-1101-arena-tip-reader section 4: "reads the live derived copy from H0, so its history equals the live book's"), not from the first counted day: a pool migrated before
day 1 is known from the history, and loading only the counted days would refuse every such pool under U2 (reviewer B1 on PR #1172). Only the three days are SCREENED; the hours from H0
to the first day are state only. No engine, no model call, no entry, no exit, no fill, no price: no outcome
exists in this pass. COUNTS ONLY: it never prints a pool, mint, signature, price or per-pool row, and it reads no file but the hour files of ONE directory (see the refusals).

Refusals (all tested; each is a `Refused` with a reason, exit 2):
  * `tape_dir` must be the research-0 receiver copy (`hv7_runner.TIP_FEED_ROOT`) or, for tests only, a directory under the system temp dir or `/data2/mal/scratch`. The sealed fast-0
    tip dir, the tip-tape archive, the public arena tape, the mirror and any other path are refused. A `.writer` component is refused.
  * `--h0` (required, `YYYY-MM-DDTHH`): not before 2026-10-16T01 (the D2/D3 seal; nothing before it is ever read) and before day 1. Every hour file from H0 to the end of day 3 must exist
    (a listing of names only); a missing one is `hours_missing`, not a quiet undercount.
  * Days: an explicit list of exactly 3 contiguous ascending FULL CLOSED UTC days (the latest 3 before the registration; for T0 2026-11-07: 11-03, 11-04, 11-05), all >= 2026-11-01 (decisions
    only on hours >= 2026-11-01T00Z; nothing before 2026-10-16T01 is ever read), and a non-empty `--notebook-ref` (the days are fixed in a notebook entry BEFORE the job).
  * A day with fewer than 24 hour files is refused (`hours_missing`): a hole is the writer's gap, not an undercount to report quietly.
  * The output dir must be under the drill root.

Output (JSON, counts only): per day hours, hours_ge3, hours_ge3_raw, half_hours, half_hours_ge3, half_hours_ge3_raw; the runner's screen counters (they include `u2_age_unknown`);
the rule: hourly mean and min day, `pass_hourly` (mean >= 150/14 = 10.714285714285714 a day, Amendment 4 item 2.2's H = 14 for 150 attempts), `any_day_lt_5` (kill line), the same for 30-min
periods, and `u2_undercount_days` (always present): the days that start before H0 + 7 d (those days count a subset of the universe, a declared undercount in the direction the rule accepts:
the FULL-day mean decides; an EMPTY list means no day is an undercount). The pass line is evaluated here as a boolean only; the verdict is the manager's and QP's.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import hv7_rank as RK  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools.arena_declog import utc_day  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.drill_hv6_item6 import FakeChain  # noqa: E402
from tools.drill_hv7_real_layout import DAY_RE, DRILL_ROOT, ID_RE, Refused, day_ms, is_drill_root  # noqa: E402
from tools.hv7_p0_count import NoModel, NullEngine  # noqa: E402

FIRST_DECISION_DAY = "2026-11-01"        # Amendment 23 item 7: decisions only on rows of hours >= 2026-11-01T00Z
H0_FLOOR = "2026-10-16T01"               # D2/D3: no hour before it is ever read (Amendment 23 item 7)
H0_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}")
N_DAYS = 3
HALF_MS = R.HOUR_MS // 2
PASS_PER_DAY = 150 / 14                  # 10.714285714285714: 150 attempts in H = 14 days
KILL_DAY_BELOW = 5
HISTORY_DAYS = 7                         # L for HV7: H0 + 7 d (Amendment 23 item 7)
HOUR_FILE = re.compile(r"tape-(\d{4}-\d{2}-\d{2})T(\d{2})\.jsonl(?:\.zst)?")
TEST_TMP_ROOTS = (tempfile.gettempdir().rstrip("/") + "/", "/data2/mal/scratch/")


def check_tape_dir(tape_dir: str | os.PathLike) -> Path:
    p = os.fspath(tape_dir)
    if not p or "\x00" in p or not os.path.isabs(p):
        raise Refused("tape_dir_not_absolute")
    real = os.path.realpath(p)
    if real.split(os.sep).count(R.TIP_WRITER_DIR):
        raise Refused("tip_writer_state")
    if real == os.path.realpath(R.TIP_FEED_ROOT):
        return Path(real)
    if any(real.startswith(os.path.realpath(r.rstrip("/")) + os.sep) for r in TEST_TMP_ROOTS) and not any(tok in real.lower() for tok in ("sealed", "tip-tape-archive")):
        return Path(real)
    raise Refused("tape_dir_not_allowed")


def parse_days(arg: str, *, now_ms: int) -> list[str]:
    days = [d.strip() for d in arg.split(",") if d.strip()]
    if len(days) != N_DAYS or len(set(days)) != N_DAYS:
        raise Refused("need_exactly_3_distinct_days")
    for d in days:
        if not DAY_RE.fullmatch(d):
            raise Refused("day_format")
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise Refused("day_format") from None
        if d < FIRST_DECISION_DAY:
            raise Refused("day_before_2026_11_01")
        if day_ms(d) + 86_400_000 > now_ms:
            raise Refused("day_not_closed")
    dts = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    if dts != sorted(dts) or any((b - a) != timedelta(days=1) for a, b in zip(dts, dts[1:])):
        raise Refused("days_not_contiguous_ascending")
    return days


def parse_h0(arg: Any, days: list[str]) -> tuple[str, int]:
    if not isinstance(arg, str) or not H0_RE.fullmatch(arg):
        raise Refused("h0_format")
    try:
        t = datetime.strptime(arg, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Refused("h0_format") from None
    if arg < H0_FLOOR:
        raise Refused("h0_before_2026_10_16T01")
    ms = int(t.timestamp() * 1000)
    if ms >= day_ms(days[0]):
        raise Refused("h0_not_before_first_day")
    return arg, ms


def hour_names(src: Path, h0_key: str, days: list[str]) -> dict[str, int]:
    """Names only (a directory listing): every hour file from H0 to the end of the last day must exist. No file is opened."""
    present = set()
    for p in sorted(src.iterdir()):
        m = HOUR_FILE.fullmatch(p.name)
        if m:
            present.add(f"{m.group(1)}T{m.group(2)}")
    t = datetime.strptime(h0_key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    want = []
    while t < end:
        want.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    if any(k not in present for k in want):
        raise Refused("hours_missing")
    return {"hours_present": len(want), "hours_expected": len(want), "hours_missing": 0, "history_hours_before_day_1": len(want) - 24 * len(days)}


def count_days(days: list[str], root: Path, tape: Path, h0_key: str) -> dict[str, Any]:
    start, end = day_ms(days[0]), day_ms(days[-1]) + 86_400_000
    guard = R.Hv7Guard(root / "v7")
    run = R.Hv7Runner(R.Hv7Config(root=str(root / "v7"), frozen_sha="p0tipcount000", clock_start_ms=start, phantom_filter=True), ArenaState(tape, start_ms=0),
                      NullEngine(), NoModel(), FakeChain(), guard)
    run._ask = lambda rr, T: None           # type: ignore[method-assign]  # P0: no model call of any kind
    last, orig = days[-1], run.state._hour_files
    run.state._hour_files = lambda: [t for t in orig() if h0_key <= t[0] and t[0][:10] <= last]      # the history from H0 is loaded; only the three days are screened
    keys = ("hours", "hours_ge3", "hours_ge3_raw", "half_hours", "half_hours_ge3", "half_hours_ge3_raw")
    per_day: dict[str, dict[str, int]] = {d: {k: 0 for k in keys} for d in days}
    T = start + R.T_D_OFFSET_MS
    while T < end:
        run.poll_to(T)
        run.sim_now = max(run.sim_now, T)
        run.pending = None
        run.screen(T)
        ge3 = run.pending is not None
        rows = run.log.rows("SCAN")
        raw = int(rows[-1].get("n_candidates", 0)) if rows and rows[-1].get("kind") == "period" else 0
        d = per_day[utc_day(T)]
        if T % R.HOUR_MS == R.T_D_OFFSET_MS:
            d["hours"] += 1
            d["hours_ge3"] += 1 if ge3 else 0
            d["hours_ge3_raw"] += 1 if raw >= RK.N_MIN else 0
        d["half_hours"] += 1
        d["half_hours_ge3"] += 1 if ge3 else 0
        d["half_hours_ge3_raw"] += 1 if raw >= RK.N_MIN else 0
        T += HALF_MS
    return {"per_day": per_day, "counters": dict(sorted(run.counters.items()))}


def rule(per_day: dict[str, dict[str, int]], days: list[str], h0_ms: int) -> dict[str, Any]:
    hourly = [per_day[d]["hours_ge3"] for d in days]
    half = [per_day[d]["half_hours_ge3"] for d in days]
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    out = {"days": len(days), "hourly_total": sum(hourly), "hourly_mean": mean(hourly), "hourly_min_day": min(hourly), "pass_line_per_day": PASS_PER_DAY,
           "pass_hourly": mean(hourly) >= PASS_PER_DAY, "hourly_any_day_lt_5": any(x < KILL_DAY_BELOW for x in hourly),
           "half_hour_total": sum(half), "half_hour_mean": mean(half), "half_hour_min_day": min(half), "pass_half_hour": mean(half) >= PASS_PER_DAY,
           "half_hour_any_day_lt_5": any(x < KILL_DAY_BELOW for x in half),
           "u2_undercount_days": [d for d in days if day_ms(d) < h0_ms + HISTORY_DAYS * 86_400_000]}
    return out


def run_p0(days_arg: str, drill_id: str, notebook_ref: str, h0: str, *, tape_dir: str | os.PathLike = R.TIP_FEED_ROOT, root: Path | None = None,
           now_ms: int | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if not (isinstance(notebook_ref, str) and notebook_ref.strip()):
        raise Refused("notebook_ref_required")
    tape = check_tape_dir(tape_dir)
    days = parse_days(days_arg, now_ms=now)
    h0_key, h0_ms = parse_h0(h0, days)
    if not ID_RE.fullmatch(drill_id):
        raise Refused("drill_id")
    root = Path(root) if root is not None else DRILL_ROOT / drill_id
    if not is_drill_root(root):
        raise Refused("not_a_drill_root")
    R.check_path(root / "x", "w")
    info = hour_names(tape, h0_key, days)
    root.mkdir(parents=True, exist_ok=True)
    res = count_days(days, root, tape, h0_key)
    return {"hv7_tip_p0": "counts", **info, "first_day": days[0], "last_day": days[-1], "h0": h0_key, "notebook_ref": notebook_ref, "outcome_free": True, "per_day": res["per_day"],
            "rule": rule(res["per_day"], days, h0_ms), "screen_counters": res["counters"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 tip-source P0: hours (and half-hours) with >= 3 screened candidates, on 3 closed days of the derived copy. Counts only; no outcome exists.")
    ap.add_argument("--days", required=True, help="three contiguous closed UTC days >= 2026-11-01, fixed in a notebook entry first")
    ap.add_argument("--notebook-ref", required=True)
    ap.add_argument("--drill-id", required=True)
    ap.add_argument("--h0", required=True, help="H0, the first hour of the derived copy's continuous history, YYYY-MM-DDTHH (UTC); the state is built from it")
    a = ap.parse_args(argv)
    try:
        res = run_p0(a.days, a.drill_id, a.notebook_ref, a.h0)
    except Refused as e:
        print(json.dumps({"hv7_tip_p0": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only
        print(json.dumps({"hv7_tip_p0": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
