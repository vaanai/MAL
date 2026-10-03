#!/usr/bin/env python3
"""EXP-014 migration + 15 min PumpSwap selector: table builder. EXPLORATION, NOT EVIDENCE.

One tape pass over the 9-day exploration pool (pools A / C / B, the clean views the EXP-012
freeze read) plus the verified expansion views (pool X, `--extra-fast-view`). For every mint
that migrates (the first pumpswap print after a bonding print) it records 27 features at
T = mig_t + 15 min on the block clock and scores a PumpSwap buy at the start of global slot
S_T + d for d in 1, 4, 8, with tp50 / sl30 / 30 minute cap exits (tools/exp014_m15_trigger.py).

Output, under `<out-root>/<run-id>/` (default out-root /data/mal/exp014-m15):
  table.jsonl, table.md5       one row per (mint, d) that was scored (all rows; `excluded_by_time`
                               is a flag, the screen applies it)
  censored.jsonl               one record per (mint, d) that was not (reason)
  trigger_counts.json          triggers per T day (every migration, censored or not), per day x d:
                               rows, fills, outcomes, excluded_by_time, censored; per pool: dropped
                               other-pool prints, rows with no pool, rows with no side, null
                               trader share on buys; edge-day report
  manifest.json                schema exp014_m15_table_v1; input shas, view mtimes, code shas, settings
  scratch/                     per-worker streams (kept, small)

Guards, all before any data file is opened (VIEW.sha256 files excepted), reused from
tools.exp013_grad_table / tools.exp013_pool by import:
  1. Roots: realpath, then the forbidden prefixes (the sealed backup / EXP-012 / EXP-011 / forward
     blocks, the 0808 and 0828 backup blocks, their clean copies and views); all three roots and
     --verify-view are required, no defaults; VIEW.sha256 checked and pinned.
  2. Extra views: forbidden realpaths, VIEW.sha256 verified, hour fence to the expansion block,
     contiguity, no overlap; and the VIEW.sha256 mtime must be <= 2026-10-05T12:00:00Z (plan item 13).
  3. Hour fence: every hour read is inside the pool whitelists or the extra views, and outside every
     owned/reserved block and the forward period.
  4. Settings: 2 workers at most, 24 h buffer, 12 h home chunks (the recipe's).
  5. Output: realpath outside the forbidden locations and ARTIFACTS/; the run directory must not
     already hold files. A duplicate (mint, d) key is refused as it is appended.

Run (see the PR body for the MiScusi command):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp014_m15_table --verify-view --run-id <id> \\
    --fast-dir ... --oracle-insample-dir ... --oracle-live-dir ... [--extra-fast-view DIR ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_freeze as fz
import tools.exp013_grad_table as g13
import tools.exp014_m15_trigger as mt
from tools.exp011_build_table import FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS, FORCED_MAX_WORKERS, _md5_of_file, _sha256_of_file
from tools.exp012_latency_sensitivity import guarded_roots
from tools.exp013_grad_table import (  # noqa: F401  (re-exported guards)
    assert_builtin_roots_allowed,
    assert_builtin_views_complete,
    assert_hour_fence,
    assert_out_dir_allowed,
    assert_recipe_settings,
    edge_flags,
    pool_runs,
    pools_hours,
)

DEFAULT_OUT_ROOT = Path("/data/mal/exp014-m15")
SCHEMA = "exp014_m15_table_v1"
# Plan item 13: only views whose VIEW.sha256 mtime is at or before this are in the exploration pool.
VIEW_MTIME_CUTOFF = "2026-10-05T12:00:00Z"


def _cutoff_s() -> int:
    return int(datetime.strptime(VIEW_MTIME_CUTOFF, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def view_mtimes(extra_views: Sequence[Any] | None) -> list[dict[str, Any]]:
    out = []
    for v in extra_views or []:
        m = (Path(v.root) / "VIEW.sha256").stat().st_mtime
        out.append({"root": str(v.root), "view_sha256_mtime_utc": datetime.fromtimestamp(m, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mtime_s": m})
    return out


def assert_view_mtimes(extra_views: Sequence[Any] | None) -> list[dict[str, Any]]:
    """Plan item 13: refuse an extra view whose VIEW.sha256 is newer than the cutoff."""
    info = view_mtimes(extra_views)
    for i in info:
        if i["mtime_s"] > _cutoff_s():
            raise SystemExit(f"extra view {i['root']!r}: VIEW.sha256 mtime {i['view_sha256_mtime_utc']} is after the pool cutoff {VIEW_MTIME_CUTOFF}")
    return info


# --- worker plumbing ----------------------------------------------------------------


def _worker(spec: dict[str, Any]) -> dict[str, Any]:
    """Pickle-friendly wrapper (Pool.map takes one argument)."""
    out = mt.run_worker_m15(
        spec["worker_id"], spec["home"], spec["buf"], spec["creator_hist"], spec["rows_path"], spec["cens_path"], spec["hour_info_fn"],
        row_iter_fn=spec["row_iter_fn"], creates_override=spec["creates"], ks=spec["ks"], pool_tag=spec["tag"],
        pool_end_ms=spec.get("pool_end_ms"), pool_gap_starts_ms=spec.get("pool_gap_starts_ms"),
    )
    out["tag"] = spec["tag"]
    out.pop("rows", None)
    out.pop("censored", None)
    return out


def plan_pools(roots: dict[str, Path], extra_views: Sequence[Any] | None, scratch: Path, ks: Sequence[int]) -> list[dict[str, Any]]:
    """EXP-013's chunk plan (A, C, B, X) with the pool run end and the pool's missing hours added
    to every spec, for the item-11 exclusion flag."""
    specs = g13.plan_pools(roots, extra_views, scratch, ks)
    ph = pools_hours(extra_views)
    for s in specs:
        hours = ph[s["tag"]]
        s["pool_end_ms"] = g13._hour_ms(hours[-1]) + 3_600_000
        s["pool_gap_starts_ms"] = mt.missing_hour_starts_ms(hours)
    return specs


# --- aggregation ------------------------------------------------------------------------


def summarize(rows: Sequence[dict[str, Any]], censored: Sequence[dict[str, Any]], triggers_by_day: dict[str, int]) -> dict[str, Any]:
    """Triggers per T day (every migration), and per day x d: scored rows, fills, outcomes,
    excluded_by_time and censored reasons. Scored + censored = triggers at every d."""
    days = sorted(set(triggers_by_day) | {r["day"] for r in rows} | {c["day"] for c in censored})
    ds = sorted({r["entry_land_k"] for r in rows} | {c["entry_land_k"] for c in censored})
    by_day: dict[str, Any] = {}
    for day in days:
        entry: dict[str, Any] = {"triggers": triggers_by_day.get(day, 0), "by_d": {}}
        for d in ds:
            rk = [r for r in rows if r["day"] == day and r["entry_land_k"] == d]
            ck = [c for c in censored if c["day"] == day and c["entry_land_k"] == d]
            outcomes: dict[str, int] = {}
            for r in rk:
                outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
            reasons: dict[str, int] = {}
            for c in ck:
                reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
            entry["by_d"][str(d)] = {
                "scored": len(rk), "filled": sum(1 for r in rk if r["filled"]), "label_press_pos": sum(r["label"] for r in rk),
                "excluded_by_time": sum(1 for r in rk if r["excluded_by_time"]), "outcomes": dict(sorted(outcomes.items())),
                "censored": len(ck), "censored_reasons": dict(sorted(reasons.items())),
            }
        by_day[day] = entry
    return {"triggers_total": sum(triggers_by_day.values()), "by_day": by_day}


def edge_report(runs: dict[str, list[tuple[str, str]]], triggers: dict[str, dict[str, int]], rows: Sequence[dict[str, Any]], censored: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per pool run, per edge day (plan item 12): triggers (by T day), rows scored, censored by reason."""
    out = []
    for tag, rr in runs.items():
        for first, last in rr:
            for role, day in (("left", first[:10]), ("right", last[:10])):
                rk = [r for r in rows if r.get("pool") == tag and r["day"] == day]
                ck = [c for c in censored if c.get("pool") == tag and c["day"] == day]
                reasons: dict[str, int] = {}
                for c in ck:
                    reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
                out.append(
                    {
                        "pool": tag, "run": [first, last], "role": role, "day": day, "triggers": triggers.get(tag, {}).get(day, 0),
                        "rows_scored": len(rk), "censored": len(ck), "censored_reasons": dict(sorted(reasons.items())),
                    }
                )
    return out


def row_edge_flags(tag: str, item: dict[str, Any], runs: dict[str, list[tuple[str, str]]]) -> dict[str, Any]:
    """Plan item 12 (report-only). `edge_left` / `edge_right`: the day of T is the first / last day
    of a run of the pool. `edge_left_mig` / `edge_right_mig`: the same for the migration day.
    `secs_cap_s`: on a left-edge migration day, the seconds from the run start to the migration (a
    mint created at or after the run start cannot have a longer `time_to_migrate_s`)."""
    t_flags = edge_flags(tag, item["day"], item.get("trigger_ms"), runs)
    m_flags = edge_flags(tag, item.get("mig_day", item["day"]), item.get("mig_ms"), runs)
    return {
        "edge_left": t_flags["edge_left"], "edge_right": t_flags["edge_right"],
        "edge_left_mig": m_flags["edge_left"], "edge_right_mig": m_flags["edge_right"], "secs_cap_s": m_flags["secs_cap_s"],
    }


def _code_shas() -> dict[str, str]:
    here = Path(__file__).resolve().parent
    names = (
        "exp014_m15_trigger.py", "exp014_m15_table.py", "exp013_grad_table.py", "exp013_grad_trigger.py", "exp013_pool.py",
        "latency_curve.py", "paper_curve_math.py", "paper_price_path.py", "exploration_exits.py", "exploration_entry_model.py",
    )
    return {n: _sha256_of_file(here / n) for n in names}


def settings_doc(ks: Sequence[int]) -> dict[str, Any]:
    return {
        "offset_ms": mt.OFFSET_MS, "ks": list(ks), "primary_d": mt.PRIMARY_K, "take_profit": mt.TAKE_PROFIT, "stop_loss": mt.STOP_LOSS, "cap_ms": mt.CAP_MS,
        "size_lamports": mt.ENTRY_SIZE, "priority_lamports": mt.ENTRY_PRIORITY_LAMPORTS, "portal_ppm": mt.ENTRY_PORTAL_PPM, "flat_fail": mt.FLAT_FAIL,
        "pressure_intercept": mt._curve().intercept, "spec": mt.SPEC_ID, "feature_names": mt.FEATURE_NAMES, "exclusion_tail_ms": mt.EXCL_TAIL_MS,
        "clock": "block_time*1000", "view_mtime_cutoff": VIEW_MTIME_CUTOFF,
        "max_workers": FORCED_MAX_WORKERS, "buffer_hours": FORCED_BUFFER_HOURS, "max_home_hours": FORCED_MAX_HOME_HOURS,
    }


def _merge_counters(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in results:
        cell = out.setdefault(r["tag"], {"by_day": {n: {} for n in mt.COUNTER_NAMES}, "buys": 0, "null_trader_buys": 0})
        c = r["counters"]
        cell["buys"] += c["buys"]
        cell["null_trader_buys"] += c["null_trader_buys"]
        for name, days in c["by_day"].items():
            for day, n in days.items():
                cell["by_day"][name][day] = cell["by_day"][name].get(day, 0) + n
    for cell in out.values():
        cell["null_trader_share_of_buys"] = (cell["null_trader_buys"] / cell["buys"]) if cell["buys"] else None
        for name in cell["by_day"]:
            cell["by_day"][name] = dict(sorted(cell["by_day"][name].items()))
    return dict(sorted(out.items()))


def build_table(
    out_dir: Path,
    roots: dict[str, Path],
    extra_views: Sequence[Any] | None = None,
    max_workers: int = FORCED_MAX_WORKERS,
    ks: Sequence[int] = mt.KS,
    run_id: str = "",
    sha_of_roots: dict[str, str | None] | None = None,
    mtimes: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The heavy pass. Call only after the guards (main does)."""
    assert_recipe_settings(max_workers, FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS)
    n_hours = assert_hour_fence(pools_hours(extra_views))
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = out_dir / "scratch"
    scratch.mkdir(exist_ok=True)
    t0 = time.time()
    specs = plan_pools(roots, extra_views, scratch, ks)
    print(f"EXP-014 m15: {n_hours} hours, {len(specs)} chunks, max_workers={max_workers}", file=sys.stderr, flush=True)
    if max_workers <= 1 or len(specs) <= 1:
        results = [_worker(s) for s in specs]
    else:
        with mp.get_context("spawn").Pool(processes=min(max_workers, len(specs))) as pool:
            results = pool.map(_worker, specs, chunksize=1)
    triggers_by_day: dict[str, int] = {}
    triggers_by_tag: dict[str, dict[str, int]] = {}
    for r in results:
        for d, n in r["triggers_by_day"].items():
            triggers_by_day[d] = triggers_by_day.get(d, 0) + n
            triggers_by_tag.setdefault(r["tag"], {})[d] = triggers_by_tag.get(r["tag"], {}).get(d, 0) + n
    runs = pool_runs(extra_views)
    rows: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for s in specs:
        for target, path in ((rows, s["rows_path"]), (censored, s["cens_path"])):
            for item in g13._read_jsonl(path):
                ident = (item["mint"], item["entry_land_k"])
                if ident in seen:  # detected as each item is appended
                    raise SystemExit(f"duplicate (mint, d) {ident} (pool {s['tag']}, chunk {s['worker_id']})")
                seen.add(ident)
                item.update(row_edge_flags(s["tag"], item, runs))
                target.append(item)
    key = lambda r: (r["day"], r["mint"], r["entry_land_k"])  # noqa: E731
    rows.sort(key=key)
    censored.sort(key=key)
    table = out_dir / "table.jsonl"
    with table.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    md5 = _md5_of_file(table)
    table.with_suffix(".md5").write_text(md5 + "\n", encoding="utf-8")
    with (out_dir / "censored.jsonl").open("w", encoding="utf-8") as fh:
        for c in censored:
            fh.write(json.dumps(c) + "\n")
    counts = summarize(rows, censored, triggers_by_day)
    counts["pool_prints"] = _merge_counters(results)
    counts["edge_days"] = edge_report(runs, triggers_by_tag, rows, censored)
    (out_dir / "trigger_counts.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema": SCHEMA, "run_id": run_id, "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "git_head": g13._git_head(),
        "code_sha256": _code_shas(), "settings": settings_doc(ks), "roots": {k: str(v) for k, v in roots.items()},
        "view_sha256_file_sha256": sha_of_roots or {},
        "pinned_view_sha256": {t: fz.VIEW_PIN_BY_POOL[t] for t in ("A", "C", "B")},
        "extra_views": [v.describe() for v in (extra_views or [])], "extra_view_mtimes": list(mtimes or []),
        "n_hours": n_hours, "n_rows": len(rows), "n_censored": len(censored), "n_triggers": counts["triggers_total"],
        "n_excluded_by_time": sum(1 for r in rows if r["excluded_by_time"]),
        "table_md5": md5, "wall_s": time.time() - t0,
        "tape_through_ms_by_chunk": [{"pool": r["tag"], "tape_through_ms": r["tape_through_ms"]} for r in results],
        "edge_days": counts["edge_days"], "pool_runs": {t: [list(x) for x in v] for t, v in runs.items()},
        "n_gap_hours_in_chunks": sum(r.get("gap_hours", 0) for r in results), "pool_prints": counts["pool_prints"],
        "label": "1{press > 0}; the primary row is d=4 (rows carry entry_land_k = d)",
        "censoring_note": (
            "A (mint, d) whose entry landing, tp/sl sell or 30-minute cap sell runs past the end of the tape read, or touches a chunk gap, is in censored.jsonl with a reason, never in table.jsonl. "
            "`excluded_by_time` rows stay in the table, flagged by trigger time alone (plan item 11); the screen applies the flag."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"EXP-014 m15: wrote {table} rows={len(rows)} censored={len(censored)} triggers={counts['triggers_total']} md5={md5}", file=sys.stderr, flush=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    fz.add_root_args(ap)
    fz.add_extra_view_arg(ap)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--max-workers", type=int, default=FORCED_MAX_WORKERS)
    ap.add_argument("--dry-run", action="store_true", help="run every guard and print the plan, read no tape")
    args = ap.parse_args(argv)
    assert_recipe_settings(args.max_workers, FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS)
    out = assert_out_dir_allowed(args.out_root, args.run_id)
    assert_builtin_roots_allowed(args)
    roots = guarded_roots(args)
    assert_builtin_views_complete(roots)
    extra_views = None
    if args.extra_fast_view:
        from tools.exp013_pool import load_extra_views

        extra_views = load_extra_views(args.extra_fast_view, fz.DAYS_ALL, allow_gap=args.allow_gap)
    mtimes = assert_view_mtimes(extra_views)
    n_hours = assert_hour_fence(pools_hours(extra_views))
    shas = {k: (_sha256_of_file(r / "VIEW.sha256") if r is not None else None) for k, r in roots.items()}
    if args.dry_run:
        print(
            json.dumps(
                {
                    "out_dir": str(out), "n_hours": n_hours, "roots": {k: str(v) for k, v in roots.items()}, "view_sha256_file_sha256": shas,
                    "extra_views": [v.describe() for v in (extra_views or [])], "extra_view_mtimes": mtimes, "view_mtime_cutoff": VIEW_MTIME_CUTOFF,
                    "pool_runs": {t: [list(x) for x in v] for t, v in pool_runs(extra_views).items()},
                },
                indent=2,
            )
        )
        return 0
    build_table(out, roots, extra_views, max_workers=args.max_workers, run_id=args.run_id, sha_of_roots=shas, mtimes=mtimes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
