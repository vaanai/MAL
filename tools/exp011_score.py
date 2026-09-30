#!/usr/bin/env python3
"""EXP-011: the single, one-shot scorer for the reserved fast-box holdout.

Read exactly once, per EXP-011's pre-registration
(EXP/EXP-011-migrate-entry-model-prereg.md, section 4) and
docs/HOLDOUT_LEDGER.md's "Fast [2026-09-09T12, 2026-09-15T12)" row. This
module is built and tested here against synthetic fixtures only -- nobody
points it at either holdout walker before both walkers have sealed every
one of their hours and the manager runs the one real command (see this
file's own `main`).

Holdout fence (hard, whitelisted): exactly the 144 hours
`[2026-09-09T12, 2026-09-15T12)`, split across two backward backfill
walkers on the fast box -- walker B, `/var/lib/mal/backfill-fast-b`,
covering `[2026-09-12T12, 2026-09-15T12)` (72h), and walker C,
`/var/lib/mal/backfill-fast-c`, covering `[2026-09-09T12, 2026-09-12T12)`
(72h). `_hour_info_holdout` asserts every hour key it is ever asked for is
in `HOLDOUT_HOURS_SET` before opening anything -- the same pattern
`tools.oracle_insample_adapter._hour_info_c` uses for pool C -- so a bug
that tried to read one hour outside the whitelist would raise instead of
silently reading it. `load_holdout_rows` below only ever calls it with
keys from `HOLDOUT_HOURS` (via `tools.exploration_exits.chunk_plan`), so
by construction no other hour file is ever opened.

Reuse, not rebuild:
  - `tools.exploration_entry_model.run_worker_features` / `score_one` /
    `causal_events` / `compute_features` (the SAME code path
    `tools/exp011_freeze.py` trained against) -- unchanged, pointed at
    this module's own `_hour_info_holdout` via `hour_info_fn`. The fast
    backfill schema is identical to pool A's (`tools.exploration_exits`),
    so no schema adapter is needed here, unlike pool B's PumpPortal-derived
    live tape (`tools.oracle_live_adapter`) -- see
    `tools.oracle_insample_adapter`'s docstring for the same argument
    applied to pool C.
  - `tools.exploration_exits.chunk_plan` for bounded (`max_home_hours`,
    `buffer_hours`) worker chunks -- the EXP-011 Phase A memory fix,
    unchanged. A chunk's buffer read is sliced out of `HOLDOUT_HOURS`
    itself (`chunk_plan`'s own `pool_hours[idx+1:idx+1+buffer_hours]`), so
    the last chunk's buffer is automatically truncated at the block's own
    end and never reads past `HOLDOUT_END`.
  - `tools.paper_attention_promote.BookTrade` / `book_stats` for the base
    four-part promotion gate under both fail models -- the exact function
    the project-wide promotion rule already uses (see `tools/kill_review.py`'s
    own docstring for the same reuse argument), so this scorer's gate
    numbers agree with it by construction, not by reimplementing the
    bootstrap arithmetic a second time.
  - `tools.exp011_freeze`'s `FROZEN_FEATURE_NAMES`, `TARGET_SPEC_ID`,
    `_mean_pct`, `_ci_lo_pct`, `_md5_of_file`, `_git_commit`, `HOLDOUT_START`,
    `HOLDOUT_END` -- the frozen spec this script scores against, and the
    same holdout-fence string constants `tools/exp011_freeze.py` already
    asserts its training pools never reach into.

Single-read lock (DEC-014 pattern, `tools/kill_review.py`'s own review
instant): before reading any holdout hour, this script writes
`/home/claude/data/exp011/HOLDOUT_READ.lock` (UTC time, git SHA, model
md5, command line) with `O_CREAT | O_EXCL` -- a second invocation while the
lock exists refuses immediately (`check_preconditions`), and a race
between two invocations starting at once still lets only one past the
`os.open` call. `--dry-run-preconditions` checks everything below and
exits 0 (or 2) without writing the lock or reading a single holdout row.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.exp011_freeze import (
    FROZEN_FEATURE_NAMES,
    HOLDOUT_END,
    HOLDOUT_START,
    TARGET_SPEC_ID,
    _ci_lo_pct,
    _git_commit,
    _md5_of_file,
    _mean_pct,
)
from tools.exploration_entry_model import (
    _rows_out_path,
    iter_rows_jsonl,
    run_worker_features,
)
from tools.exploration_entry_model_b2 import _ex_top3_sol
from tools.exploration_exits import chunk_plan
from tools.latency_curve import _hour_file, _iter_trades
from tools.paper_attention_promote import LAMPORTS_PER_SOL, BookTrade, book_stats

SCHEMA_HOLDOUT_REPORT = "exp011_holdout_report_v1"

# --- Holdout fence: two walkers, one contiguous 144h block ------------------

WALKER_B_DIR = Path("/var/lib/mal/backfill-fast-b")
WALKER_B_START = "2026-09-12T12"
WALKER_B_END = "2026-09-15T12"  # exclusive

WALKER_C_DIR = Path("/var/lib/mal/backfill-fast-c")
WALKER_C_START = "2026-09-09T12"
WALKER_C_END = "2026-09-12T12"  # exclusive

DEFAULT_LOCK_PATH = Path("/home/claude/data/exp011/HOLDOUT_READ.lock")
DEFAULT_OUT_DIR = Path("/home/claude/data/exp011")
DEFAULT_EXP011_DIR = Path("ARTIFACTS/exp011")


def _hours_range(start: str, end: str) -> list[str]:
    """Hourly keys in [start, end), end EXCLUSIVE -- unlike
    tools.exploration_exits.pool_hours / tools.oracle_insample_adapter.
    pool_c_hours, which are both inclusive-end. The holdout ledger states
    each walker's own range as a half-open interval; this matches that."""
    s = datetime.strptime(start, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    e = datetime.strptime(end, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out: list[str] = []
    cur = s
    while cur < e:
        out.append(cur.strftime("%Y-%m-%dT%H"))
        cur += timedelta(hours=1)
    return out


WALKER_B_HOURS = _hours_range(WALKER_B_START, WALKER_B_END)
WALKER_C_HOURS = _hours_range(WALKER_C_START, WALKER_C_END)
assert len(WALKER_B_HOURS) == 72, len(WALKER_B_HOURS)
assert len(WALKER_C_HOURS) == 72, len(WALKER_C_HOURS)
WALKER_B_HOURS_SET = frozenset(WALKER_B_HOURS)
WALKER_C_HOURS_SET = frozenset(WALKER_C_HOURS)
assert WALKER_B_HOURS_SET.isdisjoint(WALKER_C_HOURS_SET), "walker B/C hour ranges must not overlap"

HOLDOUT_HOURS = sorted(WALKER_B_HOURS + WALKER_C_HOURS)
HOLDOUT_HOURS_SET = frozenset(HOLDOUT_HOURS)
assert len(HOLDOUT_HOURS) == 144, len(HOLDOUT_HOURS)
assert HOLDOUT_HOURS == _hours_range(HOLDOUT_START, HOLDOUT_END), "walker B + C hours must form one contiguous 144h block == [HOLDOUT_START, HOLDOUT_END)"


def _hour_info_holdout(key: str) -> dict[str, Any]:
    """Whitelist-checked hour loader across both holdout walkers. Never
    opens a trade/create file for a key outside HOLDOUT_HOURS_SET -- the
    single mechanism that keeps `load_holdout_rows` inside the 144-hour
    fence no matter how it is called."""
    assert key in HOLDOUT_HOURS_SET, f"hour {key!r} is outside EXP-011's reserved holdout whitelist [{HOLDOUT_START}, {HOLDOUT_END})"
    root = WALKER_B_DIR if key in WALKER_B_HOURS_SET else WALKER_C_DIR
    trade = _hour_file(root / "trades", "trades", key)
    if trade is None:
        raise SystemExit(f"missing holdout trade file for whitelisted hour {key} under {root}")
    create = _hour_file(root / "creates", "creates", key)
    start_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
    return {"hour": key, "day": key[:10], "end": start_s + 3600, "trade": trade, "create": create}


# --- Preconditions (refuse loudly; never partial-run) ------------------------


def _check_walker_checkpoint(dir_path: Path, hours: Sequence[str], label: str) -> list[str]:
    cp_path = dir_path / "checkpoint.json"
    if not cp_path.exists():
        return [f"{label} checkpoint missing: {cp_path}"]
    try:
        data = json.loads(cp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{label} checkpoint unreadable ({cp_path}): {exc}"]
    hours_map = data.get("hours") if isinstance(data, dict) else None
    if not isinstance(hours_map, dict):
        return [f"{label} checkpoint has no 'hours' map: {cp_path}"]
    missing = [h for h in hours if not isinstance(hours_map.get(h), dict) or hours_map[h].get("status") != "sealed"]
    if missing:
        return [f"{label} checkpoint is missing sealed status for {len(missing)}/{len(hours)} hour(s), e.g. {missing[0]}"]
    return []


def check_preconditions(exp011_dir: Path, b_dir: Path, c_dir: Path, lock_path: Path) -> list[str]:
    """Every refusal reason this scorer will ever raise before writing the
    lock or reading a holdout hour. Empty list == clear to proceed."""
    errors: list[str] = []

    model_path = exp011_dir / "model.txt"
    md5_path = exp011_dir / "model.md5"
    if not model_path.exists():
        errors.append(f"missing {model_path}")
    elif not md5_path.exists():
        errors.append(f"missing {md5_path}")
    else:
        want = md5_path.read_text(encoding="utf-8").strip()
        got = _md5_of_file(model_path)
        if got != want:
            errors.append(f"model md5 mismatch: {model_path} hashes to {got}, {md5_path} says {want}")

    threshold_path = exp011_dir / "threshold.json"
    if not threshold_path.exists():
        errors.append(f"missing {threshold_path}")

    features_path = exp011_dir / "features.json"
    if not features_path.exists():
        errors.append(f"missing {features_path}")
    else:
        try:
            features_doc = json.loads(features_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{features_path} unreadable: {exc}")
        else:
            names = features_doc.get("frozen_feature_names")
            if names != FROZEN_FEATURE_NAMES:
                errors.append(f"{features_path}'s frozen_feature_names does not match tools.exp011_freeze.FROZEN_FEATURE_NAMES")

    errors.extend(_check_walker_checkpoint(b_dir, WALKER_B_HOURS, "walker B"))
    errors.extend(_check_walker_checkpoint(c_dir, WALKER_C_HOURS, "walker C"))

    if lock_path.exists():
        errors.append(f"holdout read lock already exists at {lock_path} -- this holdout has already been read (or is being read); refusing a second read")

    return errors


def write_lock(lock_path: Path, *, model_md5: str, command_line: str) -> None:
    """O_CREAT | O_EXCL: a concurrent second caller loses the race and gets
    FileExistsError instead of a silent double-read."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "exp011_holdout_read_lock_v1",
        "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_commit(),
        "model_md5": model_md5,
        "command_line": command_line,
    }
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2) + "\n")


# --- Data loading: the SAME feature/label code path exp011_freeze trained --
# --- against, pointed at the two holdout walkers instead of pools A/C/B. ---


def _build_creator_history_holdout() -> dict[str, list[int]]:
    """Mirrors tools.exploration_entry_model.build_creator_history /
    tools.exploration_entry_model_b2.build_creator_history_b -- each pool
    (including this one) builds its own creator-prior-mints history over
    its own hour whitelist; this is that pattern for the holdout."""
    hist: dict[str, list[int]] = {}
    for key in HOLDOUT_HOURS:
        hour = _hour_info_holdout(key)
        path = hour.get("create")
        if path is None:
            continue
        for row in _iter_trades(path):
            if row.get("type") != "create":
                continue
            creator = row.get("creator")
            block = row.get("block_time")
            if not isinstance(creator, str) or not isinstance(block, int):
                continue
            hist.setdefault(creator, []).append(block * 1000)
    for times in hist.values():
        times.sort()
    return hist


def _run_worker_holdout(
    worker_id: int, home_keys: list[str], buffer_keys: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None = None
) -> list[dict[str, Any]]:
    """Fixed-positional wrapper (mirrors run_worker_a / run_worker_b) so
    this is picklable for `multiprocessing`'s spawn context."""
    return run_worker_features(worker_id, home_keys, buffer_keys, creator_hist, hour_info_fn=_hour_info_holdout, rows_out_path=rows_out_path)


def load_holdout_rows(max_workers: int = 2, buffer_hours: int = 24, max_home_hours: int | None = 12, out_dir: Path | None = None) -> list[dict[str, Any]]:
    """The one heavy pass over the 144-hour holdout. Returns tpsl_tp50_sl30
    rows only (filled AND miss -- DEC-007), same shape as
    tools.exp011_freeze.load_tp50_rows's per-pool rows."""
    print("EXP-011 score: building creator prior-mint history over the holdout...", file=sys.stderr, flush=True)
    creator_hist = _build_creator_history_holdout()
    print(f"EXP-011 score: holdout creator_history creators={len(creator_hist)}", file=sys.stderr, flush=True)
    plan = chunk_plan(HOLDOUT_HOURS, max_workers, buffer_hours, max_home_hours)
    print(f"EXP-011 score: hours_read={HOLDOUT_HOURS[0]}..{HOLDOUT_HOURS[-1]} (n={len(HOLDOUT_HOURS)})", file=sys.stderr, flush=True)
    print(f"EXP-011 score: worker_plan={[(i, h[0], h[-1], b) for i, h, b in plan]}", file=sys.stderr, flush=True)
    paths = [_rows_out_path(out_dir, "HOLDOUT", i) for i, _h, _b in plan]
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for (worker_id, home, buf), path in zip(plan, paths):
            rows.extend(_run_worker_holdout(worker_id, home, buf, creator_hist, path))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=min(max_workers, len(plan))) as pool:
            results = pool.starmap(_run_worker_holdout, [(i, h, b, creator_hist, p) for (i, h, b), p in zip(plan, paths)])
        for part in results:
            rows.extend(part)
    if out_dir is not None:
        for path in paths:
            rows.extend(iter_rows_jsonl(path))
    return [r for r in rows if r["spec"] == TARGET_SPEC_ID]


# --- Frozen model + threshold + scoring --------------------------------------


def load_frozen_spec(exp011_dir: Path) -> tuple[Any, float, list[str]]:
    import lightgbm as lgb

    model = lgb.Booster(model_file=str(exp011_dir / "model.txt"))
    threshold_info = json.loads((exp011_dir / "threshold.json").read_text(encoding="utf-8"))
    features_doc = json.loads((exp011_dir / "features.json").read_text(encoding="utf-8"))
    feature_names = list(features_doc["frozen_feature_names"])
    return model, float(threshold_info["threshold"]), feature_names


def score_rows(model: Any, rows: Sequence[dict[str, Any]], feature_names: Sequence[str]) -> None:
    """Mutates each row in place, adding row['score'] = model.predict(...)
    at num_threads=1 (frozen spec, §4 of the pre-registration)."""
    import numpy as np

    if not rows:
        return
    x = [[float(r["features"].get(name, 0.0)) for name in feature_names] for r in rows]
    xa = np.asarray(x, dtype=np.float64)
    scores = model.predict(xa, num_threads=1)
    for r, s in zip(rows, scores):
        r["score"] = float(s)


# --- Gate (reused from tools.paper_attention_promote.book_stats, unchanged) -


def _t_ms_for_day(day: str) -> int:
    """Midday UTC of the row's own 'day' string -- score_one's rows carry a
    UTC day but not a decision_t_ms; book_stats only needs day-bucketing
    granularity (BookTrade.t_ms -> _utc_day), so any ms within that UTC day
    reproduces it exactly."""
    dt = datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _book_trades(rows: Sequence[dict[str, Any]], pnl_key: str) -> list[BookTrade]:
    return [BookTrade(mint=r["mint"], t_ms=_t_ms_for_day(r["day"]), pnl=int(round(r[pnl_key]))) for r in rows]


def compute_gate(entered_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The unchanged project-wide promotion gate, both fail models, via
    tools.paper_attention_promote.book_stats -- result['promote'] is True
    only if BOTH the flat and pressure legs clear all four gate conditions
    (book_stats ANDs them internally)."""
    flat_trades = _book_trades(entered_rows, "flat")
    press_trades = _book_trades(entered_rows, "press")
    return book_stats(flat_trades, pressure_scale_1=press_trades)


# --- Non-gating report sections ----------------------------------------------


def _cohort_stats(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    flat_vals = [r["flat"] for r in rows]
    press_vals = [r["press"] for r in rows]
    return {
        "n": len(rows),
        "flat_mean_pct": _mean_pct(flat_vals),
        "flat_ci_lo_pct": _ci_lo_pct(flat_vals),
        "flat_total_sol": (sum(flat_vals) / LAMPORTS_PER_SOL) if flat_vals else None,
        "flat_ex_top3_sol": _ex_top3_sol(flat_vals),
        "press_mean_pct": _mean_pct(press_vals),
        "press_ci_lo_pct": _ci_lo_pct(press_vals),
        "press_total_sol": (sum(press_vals) / LAMPORTS_PER_SOL) if press_vals else None,
        "press_ex_top3_sol": _ex_top3_sol(press_vals),
    }


def per_day_table(rows_all: Sequence[dict[str, Any]], entered: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day_all: dict[str, list[dict[str, Any]]] = {}
    by_day_entered: dict[str, list[dict[str, Any]]] = {}
    for r in rows_all:
        by_day_all.setdefault(r["day"], []).append(r)
    for r in entered:
        by_day_entered.setdefault(r["day"], []).append(r)
    out: list[dict[str, Any]] = []
    for day in sorted(by_day_all):
        all_d = by_day_all[day]
        ent_d = by_day_entered.get(day, [])
        out.append(
            {
                "day": day,
                "n_test": len(all_d),
                "n_entered": len(ent_d),
                "selected_fraction": (len(ent_d) / len(all_d)) if all_d else None,
                "flat_mean_pct": _mean_pct([r["flat"] for r in ent_d]) if ent_d else None,
                "press_mean_pct": _mean_pct([r["press"] for r in ent_d]) if ent_d else None,
            }
        )
    return out


def fill_conditional_stats(rows_all: Sequence[dict[str, Any]], entered: Sequence[dict[str, Any]]) -> dict[str, Any]:
    all_filled = [r for r in rows_all if r.get("filled")]
    entered_filled = [r for r in entered if r.get("filled")]
    return {
        "entered_all": _cohort_stats(entered),
        "entered_filled_only": _cohort_stats(entered_filled),
        "all_filled_baseline": _cohort_stats(all_filled),
        "all_attempts_baseline_no_filter": _cohort_stats(rows_all),
    }


def build_report(rows_all: Sequence[dict[str, Any]], entered: Sequence[dict[str, Any]], threshold: float) -> dict[str, Any]:
    gate = compute_gate(entered)
    verdict = "PASS" if gate.get("promote") else "FAIL"
    n_all = len(rows_all)
    return {
        "schema": SCHEMA_HOLDOUT_REPORT,
        "verdict": verdict,
        "threshold": threshold,
        "n_holdout_rows": n_all,
        "n_entered": len(entered),
        "selected_fraction_overall": (len(entered) / n_all) if n_all else None,
        "gate": gate,
        "per_day": per_day_table(rows_all, entered),
        "fill_conditional": fill_conditional_stats(rows_all, entered),
        "source_note": "holdout is a single fast-box block split across two walkers; no cross-source split is possible here, only within-block day variation",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": _git_commit(),
        "holdout_hours": {"start": HOLDOUT_START, "end": HOLDOUT_END, "n_hours": len(HOLDOUT_HOURS)},
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"VERDICT: {report['verdict']}", ""]
    lines.append(f"# EXP-011 holdout report ({report['generated_at_utc']})")
    lines.append("")
    lines.append(f"threshold={report['threshold']!r} n_holdout_rows={report['n_holdout_rows']} n_entered={report['n_entered']} "
                 f"selected_fraction_overall={report['selected_fraction_overall']!r}")
    lines.append("")
    gate = report["gate"]
    lines.append(f"gate: n={gate.get('n')} promote={gate.get('promote')} blockers={gate.get('promote_blockers')}")
    lines.append(f"flat: mean_sol={gate.get('mean_sol')} total_sol={gate.get('total_sol')} total_ex_top3_sol={gate.get('total_ex_top3_sol')}")
    p1 = gate.get("pressure_scale_1") or {}
    lines.append(f"pressure_scale_1: mean_sol={p1.get('mean_sol')} total_sol={p1.get('total_sol')} total_ex_top3_sol={p1.get('total_ex_top3_sol')} promote={p1.get('promote')}")
    lines.append("")
    lines.append("## Per-day")
    lines.append("| day | n_test | n_entered | selected_fraction | flat_mean_pct | press_mean_pct |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in report["per_day"]:
        lines.append(
            f"| {row['day']} | {row['n_test']} | {row['n_entered']} | {row['selected_fraction']!r} | {row['flat_mean_pct']!r} | {row['press_mean_pct']!r} |"
        )
    lines.append("")
    lines.append("## Fill-conditional / baseline")
    for key, cohort in report["fill_conditional"].items():
        lines.append(f"- {key}: n={cohort['n']} flat_mean_pct={cohort['flat_mean_pct']!r} press_mean_pct={cohort['press_mean_pct']!r}")
    lines.append("")
    lines.append(report["source_note"])
    return "\n".join(lines) + "\n"


def write_report(out_dir: Path, report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "holdout_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    (out_dir / "holdout_report.md").write_text(render_markdown(report), encoding="utf-8")


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exp011-dir", default=str(DEFAULT_EXP011_DIR))
    ap.add_argument("--b-dir", default=str(WALKER_B_DIR))
    ap.add_argument("--c-dir", default=str(WALKER_C_DIR))
    ap.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--buffer-hours", type=int, default=24)
    ap.add_argument("--max-home-hours", type=int, default=12)
    ap.add_argument("--dry-run-preconditions", action="store_true", help="check preconditions and exit; never writes the lock or reads a holdout row")
    args = ap.parse_args(argv)

    exp011_dir = Path(args.exp011_dir)
    b_dir = Path(args.b_dir)
    c_dir = Path(args.c_dir)
    lock_path = Path(args.lock_path)

    errors = check_preconditions(exp011_dir, b_dir, c_dir, lock_path)
    for e in errors:
        print(f"REFUSED: {e}", file=sys.stderr)
    if errors:
        return 2
    if args.dry_run_preconditions:
        print("preconditions OK (dry run -- no lock written, no holdout hour read)", file=sys.stderr)
        return 0

    model, threshold, feature_names = load_frozen_spec(exp011_dir)
    model_md5 = (exp011_dir / "model.md5").read_text(encoding="utf-8").strip()
    write_lock(lock_path, model_md5=model_md5, command_line=" ".join(sys.argv))

    rows = load_holdout_rows(
        max_workers=args.max_workers,
        buffer_hours=args.buffer_hours,
        max_home_hours=(args.max_home_hours or None),
        out_dir=(Path(args.out_dir) / "scratch"),
    )
    score_rows(model, rows, feature_names)
    entered = [r for r in rows if r["score"] >= threshold]
    report = build_report(rows, entered, threshold)
    write_report(Path(args.out_dir), report)
    print(f"VERDICT: {report['verdict']}", file=sys.stderr, flush=True)
    print(f"EXP-011 score: wrote {args.out_dir}/holdout_report.{{md,json}}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
