#!/usr/bin/env python3
"""Exploration lane B2: the learned migrate entry filter (tools/
exploration_entry_model.py, #142 / ARTIFACTS/lab/exploration-entry-model-
2026-09-28.md) evaluated over 5.7 UTC days instead of 3, by adding the
Oracle live tape (pool B) alongside the fast-box backfill pool (pool A).

EXPLORATION ONLY. Produces a candidate for a later pre-registered test on a
fresh block. Never a promote. See ARTIFACTS/lab/exploration-entry-model-b2-
2026-09-28.md.

Why: ARTIFACTS/lab/exploration-exits-2026-09-28.md (+ its addendum) found
migrate entries lose on the typical trade -- trailing exits only harvest
rare moonshots, and the book is negative once the top 3 trades are removed.
Profit has to come from better ENTRY selection, not a better exit. Lane B's
entry model (tools/exploration_entry_model.py) found stable top features
(pre-migration buy pressure, net flow, price momentum) but the top-decile
lift was not consistent across only 3 held-out days. This module adds pool
B's 3 more UTC days (2026-09-25 partial from 07Z, 09-26, 09-27) without
touching the frozen execution, the feature list, or the model settings.

Data:
  Pool A: sealed fast-box backfill, 2026-09-19T01 - 2026-09-21T23 (71
    hours), already wired in tools/exploration_exits.py / tools/
    exploration_entry_model.py. Untouched here.
  Pool B: Oracle live tape, 2026-09-25T07 - 2026-09-27T23 (65 hours) plus
    PumpPortal creates through 2026-09-27, and 2026-09-28 creates strictly
    before 2026-09-28T00:00:00Z. tools/oracle_live_adapter.py fences and
    adapts it onto the same loaders. Never a promote input; see the
    "Oracle live tape, pre-clean-clock" row in docs/HOLDOUT_LEDGER.md.

B has REAL t_recv_ms (a live listener's receive time); A's are synthetic
(post-hoc getBlock timestamps). Reported for A and B separately as well as
together -- never blended silently -- and no "which pool" flag is ever fed
to the model (FEATURE_NAMES, reused unchanged from tools/
exploration_entry_model.py, has no such feature; each row's "pool" key is
report-only metadata that `_vector()` never reads).

Model: unchanged from tools/exploration_entry_model.py -- same features, the
same no-lookahead causal boundary, the same frozen entry execution (migrate
trigger, slot+1, direct, 0.0005 SOL/side, 0.5 SOL), the same two exits
(tpsl_tp50_sl30, trail_30_act20). No new hyperparameter search: only
`lgb_medium` (the earlier module's own winner) and `logreg_l2` (second
opinion) are fit here; `lgb_shallow` is skipped to keep the 6-fold run
inside the box's budget.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_entry_model import (
    SETTINGS,
    TARGET_SPEC_IDS,
    _Feat,
    _cohort_stats,
    _fmt_pct,
    fit_setting,
    importance_setting,
    predict_setting,
    run_all_features as run_all_features_a,
    run_worker_features,
    _vector,
)
from tools.latency_curve import _Mint, _rss_mb
from tools.oracle_live_adapter import (
    POOL_B_CREATE_DAYS,
    POOL_B_CUTOFF_ISO,
    POOL_B_END,
    POOL_B_HOURS,
    POOL_B_START,
    _hour_info_b,
    iter_adapted_creates,
    iter_trade_rows_sorted,
    load_creates_b,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL

SETTINGS_B2 = [s for s in SETTINGS if s["id"] in ("lgb_medium", "logreg_l2")]
assert {s["id"] for s in SETTINGS_B2} == {"lgb_medium", "logreg_l2"}
PRIMARY_SETTING = "lgb_medium"

DAYS_A = ("2026-09-19", "2026-09-20", "2026-09-21")
DAYS_B = ("2026-09-25", "2026-09-26", "2026-09-27")
DAYS_ALL = DAYS_A + DAYS_B

# --- Pool B: streaming worker orchestration (reuses run_worker_features) ---


def build_creator_history_b() -> dict[str, list[int]]:
    hist: dict[str, list[int]] = {}
    for row in iter_adapted_creates():
        creator = row.get("creator")
        block = row.get("block_time")
        if not isinstance(creator, str) or not creator or not isinstance(block, int):
            continue
        hist.setdefault(creator, []).append(block * 1000)
    for times in hist.values():
        times.sort()
    return hist


def diagnose_row_order(sample_hours: Sequence[str]) -> dict[str, Any]:
    """Say so, don't assume: for each sampled hour, count how many rows in
    the on-disk file were already out of (slot, t_recv_ms, event_index)
    order before iter_trade_rows_sorted's defensive re-sort. A count of 0
    across every sampled hour means the live listener's append-only file
    order already matched the causal ordering entry/exit scoring assumes;
    a nonzero count means the re-sort actually changed something, and this
    is where that gets reported instead of silently assumed away.
    """
    from tools.latency_curve import _iter_trades
    from tools.oracle_live_adapter import _row_sort_key

    rows_sampled = 0
    out_of_order = 0
    for key in sample_hours:
        info = _hour_info_b(key)
        prev = None
        n = 0
        for row in _iter_trades(info["trade"]):
            key_now = _row_sort_key(row)
            if prev is not None and key_now < prev:
                out_of_order += 1
            prev = key_now
            n += 1
        rows_sampled += n
    return {"hours_sampled": len(sample_hours), "hours": list(sample_hours), "rows_sampled": rows_sampled, "out_of_order": out_of_order}


def _chunk(keys: list[str], n: int) -> list[list[str]]:
    k, m = divmod(len(keys), n)
    chunks = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(keys[start : start + size])
        start += size
    return [c for c in chunks if c]


def plan_workers_b(max_workers: int = 3, buffer_hours: int = 2) -> list[tuple[int, list[str], list[str]]]:
    chunks = _chunk(POOL_B_HOURS, max_workers)
    plan = []
    for i, home in enumerate(chunks):
        idx = POOL_B_HOURS.index(home[-1])
        buf = POOL_B_HOURS[idx + 1 : idx + 1 + buffer_hours]
        plan.append((i, home, buf))
    return plan


def _worker_home_window_ms(home_keys: list[str]) -> tuple[int, int]:
    start = int(datetime.strptime(home_keys[0], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) * 1000
    end = _hour_info_b(home_keys[-1])["end"] * 1000
    return start, end


def run_worker_b(
    worker_id: int,
    home_keys: list[str],
    buffer_keys: list[str],
    all_creates: dict[str, tuple[_Mint, _Feat]],
    creator_hist: dict[str, list[int]],
) -> list[dict[str, Any]]:
    """Partition the pre-loaded pool-B creates by this worker's home-hour
    window (Oracle creates are day-granular, loaded once in the parent, not
    per-hour like pool A), then run the exact same streaming worker pool A
    uses (tools.exploration_entry_model.run_worker_features), pointed at
    Oracle's hour resolver and the defensive sorted row iterator.
    """
    home_start_ms, home_end_ms = _worker_home_window_ms(home_keys)
    home_creates = {mid: (m, f) for mid, (m, f) in all_creates.items() if home_start_ms <= m.block_ms < home_end_ms}
    print(
        f"[w{worker_id}] pool=B home={home_keys[0]}..{home_keys[-1]} creates={len(home_creates)} rss_mb={_rss_mb()}",
        file=sys.stderr,
        flush=True,
    )
    return run_worker_features(
        worker_id,
        home_keys,
        buffer_keys,
        creator_hist,
        hour_info_fn=_hour_info_b,
        row_iter_fn=iter_trade_rows_sorted,
        creates_override=home_creates,
    )


def run_all_features_b(max_workers: int = 3, buffer_hours: int = 2) -> list[dict[str, Any]]:
    print(f"pool B trade hours: {POOL_B_START} .. {POOL_B_END} ({len(POOL_B_HOURS)})", file=sys.stderr, flush=True)
    print("loading pool B creates (Oracle observe day files)...", file=sys.stderr, flush=True)
    all_creates = load_creates_b()
    print(f"pool B creates: {len(all_creates)}", file=sys.stderr, flush=True)
    creator_hist = build_creator_history_b()
    print(f"pool B creator_history creators={len(creator_hist)}", file=sys.stderr, flush=True)
    plan = plan_workers_b(max_workers, buffer_hours)
    print(f"pool B worker_plan={[(i, h[0], h[-1], b) for i, h, b in plan]}", file=sys.stderr, flush=True)
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for worker_id, home, buf in plan:
            rows.extend(run_worker_b(worker_id, home, buf, all_creates, creator_hist))
        return rows
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=len(plan)) as pool:
        results = pool.starmap(run_worker_b, [(i, h, b, all_creates, creator_hist) for i, h, b in plan])
    for part in results:
        rows.extend(part)
    return rows


# --- Reporting: cohorts with the ex-top-3 concentration check --------------


def _ex_top3_sol(press_vals: Sequence[float]) -> float | None:
    """Total pressure-model net SOL for the cohort, excluding its 3 largest
    individual winners -- the promotion gate's own "still positive after
    removing the top 3 trades" check, applied here to an exploration
    cohort. None if the cohort is empty."""
    if not press_vals:
        return None
    ordered = sorted(press_vals, reverse=True)
    kept = ordered[3:]
    return sum(kept) / LAMPORTS_PER_SOL


def _cohort_stats_b2(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    base = _cohort_stats(rows)
    press_vals = [r["press"] for r in rows]
    base["press_net_total_sol"] = (sum(press_vals) / LAMPORTS_PER_SOL) if press_vals else None
    base["ex_top3_press_net_sol"] = _ex_top3_sol(press_vals)
    return base


def evaluate_fold_b2(rows_train: Sequence[dict[str, Any]], rows_test: Sequence[dict[str, Any]], setting: dict[str, Any]) -> dict[str, Any]:
    x_train = [_vector(r["features"]) for r in rows_train]
    y_train = [1 if r["press"] > 0 else 0 for r in rows_train]
    if len(set(y_train)) < 2 or len(x_train) < 20:
        return {"trained": False, "n_train": len(rows_train), "n_test": len(rows_test)}
    fit = fit_setting(x_train, y_train, setting)
    x_test = [_vector(r["features"]) for r in rows_test]
    scores = predict_setting(fit, x_test)
    order = sorted(range(len(rows_test)), key=lambda i: scores[i], reverse=True)
    ranked = [rows_test[i] for i in order]
    n = len(ranked)
    cohorts = {"all": _cohort_stats_b2(ranked)}
    for pct in (10, 20):
        k = max(1, round(n * pct / 100.0))
        cohorts[f"top{pct}"] = _cohort_stats_b2(ranked[:k])
    return {
        "trained": True,
        "n_train": len(rows_train),
        "n_test": n,
        "cohorts": cohorts,
        "importance": importance_setting(fit)[:8],
    }


def leave_one_day_out_days(rows: Sequence[dict[str, Any]], days: Sequence[str], setting: dict[str, Any]) -> dict[str, Any]:
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for r in rows:
        if r["day"] in by_day:
            by_day[r["day"]].append(r)
    out: dict[str, Any] = {}
    for held_out in days:
        train = [r for d in days if d != held_out for r in by_day.get(d, [])]
        test = by_day.get(held_out, [])
        out[held_out] = evaluate_fold_b2(train, test, setting)
    return out


def consistency_counts(lodo: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Over a {day: fold} LODO for one exit/setting: how many held-out days
    have top10 pressure-net > all-trades pressure-net, and how many have
    top10 pressure-net > 0 with ex-top-3 SOL > 0 too."""
    n_days = 0
    n_lift = 0
    n_both_positive = 0
    for fold in lodo.values():
        if not fold.get("trained"):
            continue
        n_days += 1
        top10 = fold["cohorts"]["top10"]
        allc = fold["cohorts"]["all"]
        if top10["press_net_mean_pct"] is not None and allc["press_net_mean_pct"] is not None:
            if top10["press_net_mean_pct"] > allc["press_net_mean_pct"]:
                n_lift += 1
        if (
            top10["press_net_mean_pct"] is not None
            and top10["press_net_mean_pct"] > 0
            and top10["ex_top3_press_net_sol"] is not None
            and top10["ex_top3_press_net_sol"] > 0
        ):
            n_both_positive += 1
    return {"n_days": n_days, "n_top10_beats_all": n_lift, "n_top10_positive_and_ex_top3_positive": n_both_positive}


# --- Report -----------------------------------------------------------------


def _day_table(lodo: dict[str, dict[str, Any]], days: Sequence[str]) -> list[str]:
    lines = ["| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for day in days:
        fold = lodo.get(day, {})
        if not fold.get("trained"):
            lines.append(f"| {day} | (not trained -- n_train={fold.get('n_train', 0)}, n_test={fold.get('n_test', 0)}) |  |  |  |  |  |")
            continue
        for cohort_name in ("all", "top10", "top20"):
            c = fold["cohorts"][cohort_name]
            ex3 = c["ex_top3_press_net_sol"]
            tot = c["press_net_total_sol"]
            lines.append(
                f"| {day} | {cohort_name} | {c['n']} | {_fmt_pct(c['flat_net_mean_pct'])} | "
                f"{_fmt_pct(c['press_net_mean_pct'])} | {'n/a' if tot is None else f'{tot:+.4f}'} | "
                f"{'n/a' if ex3 is None else f'{ex3:+.4f}'} |"
            )
    return lines


def write_report(
    out_md: Path,
    out_json: Path,
    combined: dict[str, Any],
    n_rows: dict[str, Any],
    wall_s: float,
    pool_b_diag: dict[str, Any],
) -> None:
    out_json.write_text(
        json.dumps(
            {
                "schema": "exploration_entry_model_b2_v1",
                "pool_a": {"start": "2026-09-19T01", "end": "2026-09-21T23", "hours": 71},
                "pool_b": {
                    "trade_start": POOL_B_START,
                    "trade_end": POOL_B_END,
                    "trade_hours": len(POOL_B_HOURS),
                    "create_days": POOL_B_CREATE_DAYS,
                    "cutoff_iso": POOL_B_CUTOFF_ISO,
                },
                "days_a": DAYS_A,
                "days_b": DAYS_B,
                "settings": SETTINGS_B2,
                "primary_setting": PRIMARY_SETTING,
                "n_rows": n_rows,
                "results": combined,
                "pool_b_diagnostics": pool_b_diag,
                "wall_s": wall_s,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("---")
    lines.append("cursor:")
    lines.append('  subagentId: "exploration-entry-model-b2-2026-09-28"')
    lines.append("---")
    lines.append("")
    lines.append("# Exploration: entry model on 5.7 days (fast + Oracle live, pre-clean-clock)")
    lines.append("")
    lines.append(
        "**Exploration, not a pre-registered test.** This lane adds pool B (the Oracle live tape, "
        "2026-09-25T07 through 2026-09-27T23, real receive times) to pool A (the fast-box backfill, "
        "2026-09-19T01 through 2026-09-21T23, synthetic receive times) behind the exact reuse "
        "described in `tools/oracle_live_adapter.py` and `tools/exploration_entry_model_b2.py`. "
        "The frozen execution, features, and settings are unchanged from "
        "`ARTIFACTS/lab/exploration-entry-model-2026-09-28.md`. It is a candidate for a later "
        "pre-registered test on a fresh, never-read block -- never a promote, and it does not "
        "touch the forward runner, the promotion gate, or any live book. Results are reported for "
        "A and B separately as well as together; no dataset-source feature is ever fed to the model."
    )
    lines.append("")
    lines.append("## Data")
    lines.append("")
    lines.append(
        f"Pool A: sealed fast-box backfill, 2026-09-19T01 - 2026-09-21T23 (71 hours), reused unchanged. "
        f"Pool B: Oracle live tape, {POOL_B_START} - {POOL_B_END} ({len(POOL_B_HOURS)} hours) plus PumpPortal "
        f"creates through 2026-09-27 and 2026-09-28 strictly before {POOL_B_CUTOFF_ISO} (enforced in "
        "`tools/oracle_live_adapter.py` by an explicit hour/day whitelist and a hard per-row cutoff -- "
        "see `tools/test_oracle_live_adapter.py`). Not a confirmation holdout: the 'Oracle live tape, "
        "pre-clean-clock' row in `docs/HOLDOUT_LEDGER.md` marks it exploration pool only."
    )
    lines.append("")
    lines.append(
        f"Rows scored (full pool, MISS included, no survivorship / DEC-007): "
        + ", ".join(f"`{sid}`: A={n_rows['A'].get(sid, 0)}, B={n_rows['B'].get(sid, 0)}" for sid in TARGET_SPEC_IDS)
    )
    lines.append("")
    if pool_b_diag:
        lines.append(
            f"Pool B ordering diagnostic (`tools/oracle_live_adapter.iter_trade_rows_sorted`, sampled "
            f"{pool_b_diag.get('hours_sampled', 0)} hours): {pool_b_diag.get('rows_sampled', 0)} rows read, "
            f"{pool_b_diag.get('out_of_order', 0)} were out of (slot, t_recv_ms, event_index) order in the "
            "on-disk file before the defensive re-sort (0 means the live listener's file order already matched)."
        )
        lines.append("")
    lines.append("## Settings run here (no new hyperparameter search)")
    lines.append("")
    for s in SETTINGS_B2:
        lines.append(f"- `{s['id']}`: {json.dumps({k: v for k, v in s.items() if k != 'id'})}")
    lines.append("")
    lines.append(
        f"`{PRIMARY_SETTING}` is primary (the prior module's own winner by its fixed selection rule); "
        "`logreg_l2` is reported as a second opinion. `lgb_shallow` is skipped here to keep the 6-fold "
        "run inside the box's time budget -- it is unchanged and still reported in the original 3-day file."
    )
    lines.append("")

    total_days_checked = 0
    total_lift = 0
    total_both = 0
    for spec_id in TARGET_SPEC_IDS:
        lines.append(f"## `{spec_id}`")
        lines.append("")
        for pool_name, days in (("A only (3 days)", DAYS_A), ("B only (3 days)", DAYS_B), ("A + B together (6 days)", DAYS_ALL)):
            lodo = combined[spec_id][PRIMARY_SETTING][pool_name]
            lines.append(f"### {pool_name}, setting `{PRIMARY_SETTING}`")
            lines.append("")
            lines.extend(_day_table(lodo, days))
            lines.append("")
            if pool_name == "A + B together (6 days)":
                counts = consistency_counts(lodo)
                total_days_checked += counts["n_days"]
                total_lift += counts["n_top10_beats_all"]
                total_both += counts["n_top10_positive_and_ex_top3_positive"]
                lines.append(
                    f"Consistency over the {counts['n_days']} held-out days: top10 pressure-net beats "
                    f"all-trades pressure-net on **{counts['n_top10_beats_all']}/{counts['n_days']}** days; "
                    f"top10 pressure-net > 0 AND ex-top-3 SOL > 0 on "
                    f"**{counts['n_top10_positive_and_ex_top3_positive']}/{counts['n_days']}** days."
                )
                lines.append("")
        # Top features (6-day combined, primary setting)
        lodo6 = combined[spec_id][PRIMARY_SETTING]["A + B together (6 days)"]
        agg: dict[str, list[float]] = {}
        top5_by_day: dict[str, list[str]] = {}
        for day in DAYS_ALL:
            fold = lodo6.get(day, {})
            if not fold.get("trained"):
                continue
            names = [name for name, _v in fold["importance"]]
            top5_by_day[day] = names[:5]
            for name, val in fold["importance"]:
                agg.setdefault(name, []).append(val)
        ranked = sorted(agg.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        lines.append(f"### `{spec_id}` top features, setting `{PRIMARY_SETTING}`, 6-day combined LODO")
        lines.append("")
        lines.append("| Feature | Mean importance | Days in its top-5 |")
        lines.append("| --- | ---: | --- |")
        for name, vals in ranked[:8]:
            days_in_top5 = [d for d, top5 in top5_by_day.items() if name in top5]
            lines.append(f"| `{name}` | {sum(vals) / len(vals):.3g} | {', '.join(days_in_top5) if days_in_top5 else '-'} |")
        lines.append("")
        lines.append(f"### `{spec_id}`, second opinion: `logreg_l2`, A + B together (6 days)")
        lines.append("")
        lines.extend(_day_table(combined[spec_id]["logreg_l2"]["A + B together (6 days)"], DAYS_ALL))
        lines.append("")

    lines.append("## Overall day-consistency (both exits, 6-day combined LODO, primary setting)")
    lines.append("")
    lines.append(
        f"Across both exits' 6 held-out-day folds ({total_days_checked} fold-days total): top10 pressure-net "
        f"beat all-trades pressure-net on **{total_lift}/{total_days_checked}**; top10 pressure-net > 0 with "
        f"ex-top-3 SOL > 0 on **{total_both}/{total_days_checked}**. This is the number that answers whether "
        "3 more days changed the earlier module's inconsistent-lift finding -- read it next to "
        "`ARTIFACTS/lab/exploration-entry-model-2026-09-28.md`'s 3-day numbers, not instead of them."
    )
    lines.append("")
    lines.append(f"Wall time: {wall_s:.0f}s. Full grid: `exploration-entry-model-b2-2026-09-28.json`.")
    lines.append("")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exploration lane B2: entry model on 5.7 days")
    parser.add_argument("--out-md", type=Path, default=Path("ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.md"))
    parser.add_argument("--out-json", type=Path, default=Path("ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.json"))
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--buffer-hours", type=int, default=2)
    args = parser.parse_args()
    t0 = time.time()

    print("=== pool B row-order diagnostic (say so, don't assume) ===", file=sys.stderr, flush=True)
    sample_hours = [POOL_B_HOURS[0], POOL_B_HOURS[len(POOL_B_HOURS) // 2], POOL_B_HOURS[-1]]
    pool_b_diag = diagnose_row_order(sample_hours)
    print(f"pool_b_diagnostics={pool_b_diag}", file=sys.stderr, flush=True)

    print("=== pool A ===", file=sys.stderr, flush=True)
    rows_a = run_all_features_a(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    print("=== pool B ===", file=sys.stderr, flush=True)
    rows_b = run_all_features_b(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    for r in rows_a:
        r["pool"] = "A"
    for r in rows_b:
        r["pool"] = "B"

    by_spec_a: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    by_spec_b: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    for r in rows_a:
        by_spec_a.setdefault(r["spec"], []).append(r)
    for r in rows_b:
        by_spec_b.setdefault(r["spec"], []).append(r)
    n_rows = {
        "A": {sid: len(by_spec_a.get(sid, [])) for sid in TARGET_SPEC_IDS},
        "B": {sid: len(by_spec_b.get(sid, [])) for sid in TARGET_SPEC_IDS},
    }

    combined: dict[str, Any] = {sid: {} for sid in TARGET_SPEC_IDS}
    for spec_id in TARGET_SPEC_IDS:
        rows_all = by_spec_a.get(spec_id, []) + by_spec_b.get(spec_id, [])
        for setting in SETTINGS_B2:
            combined[spec_id][setting["id"]] = {
                "A only (3 days)": leave_one_day_out_days(by_spec_a.get(spec_id, []), DAYS_A, setting),
                "B only (3 days)": leave_one_day_out_days(by_spec_b.get(spec_id, []), DAYS_B, setting),
                "A + B together (6 days)": leave_one_day_out_days(rows_all, DAYS_ALL, setting),
            }

    wall_s = time.time() - t0
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.out_md, args.out_json, combined, n_rows, wall_s, pool_b_diag)
    print(f"wrote {args.out_md} and {args.out_json} in {wall_s:.0f}s", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
