#!/usr/bin/env python3
"""Exploration lane B3: the learned migrate entry filter (tools/
exploration_entry_model.py, tools/exploration_entry_model_b2.py) evaluated
over 9 UTC days instead of 5.7, by adding the Oracle in-sample backfill
("pool C", tools/oracle_insample_adapter.py) between pool A (fast-box) and
pool B (Oracle live tape), and by running three pre-fixed model settings
instead of the earlier "classifier + logreg" pair.

EXPLORATION ONLY. Produces at most one candidate for a later pre-registered
test on a fresh, never-read block. Never a promote. See ARTIFACTS/lab/
exploration-entry-model-b3-2026-09-28.md.

Data (9 UTC days, ~215 hours total, no overlap, each hour whitelisted in its
own adapter module):
  Pool A: sealed fast-box backfill, 2026-09-19T01 - 2026-09-21T23 (71 hours).
    tools/exploration_exits.py / tools/exploration_entry_model.py. Untouched.
  Pool C: Oracle in-sample backfill, 2026-09-22T00 - 2026-09-25T06 (79
    hours). tools/oracle_insample_adapter.py. New in this module. Same
    schema as pool A (verified on-disk, not assumed) so it reuses the fast
    pool's loaders unchanged, just pointed at a different root.
  Pool B: Oracle live tape, 2026-09-25T07 - 2026-09-27T23 (65 hours).
    tools/oracle_live_adapter.py. Untouched, reused via tools/
    exploration_entry_model_b2.py's run_all_features_b.

No dataset-source feature is ever fed to the model (FEATURE_NAMES, reused
unchanged from tools.exploration_entry_model, has no such feature; each
row's "pool" key is report-only metadata _vector() never reads).

Model: same features, same no-lookahead causal boundary (tools.
exploration_entry_model.causal_events / compute_features, unchanged), same
frozen entry execution (migrate trigger, slot+1, direct, 0.0005 SOL/side,
0.5 SOL), same two exits (tpsl_tp50_sl30, trail_30_act20). Exactly three
settings, all using the earlier module's own "lgb_medium" hyperparameters
(num_leaves=15, min_data_in_leaf=20, learning_rate=0.05, rounds=100) so the
only thing that varies is the training TARGET, not the tree shape:

  S1 (`s1_reg`):        LightGBM regression, y = per-trade pressure-model
                         net %, raw (no transform).
  S2 (`s2_clf`):         LightGBM binary classifier, y = 1{pressure net>0}
                         (this is the earlier modules' own setup).
  S3 (`s3_reg_winsor`):  LightGBM regression, y = per-trade pressure-model
                         net %, winsorized at the TRAINING fold's own
                         1st/99th percentiles (computed on that fold's
                         training rows only, never on the held-out day, so
                         this cannot leak). Evaluation of the resulting
                         portfolio always uses raw, unwinsorized pnl -- the
                         winsorization only ever touches the label a tree
                         is fit against, never a reported number.

Leave-one-day-out over all 9 UTC days (2026-09-19 .. 2026-09-27). One LODO
per (exit, setting): 9 folds, each held-out day's model trained on the
other 8 days pooled across all three source pools.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_entry_model import (
    FEATURE_NAMES,
    SEED,
    SETTINGS,
    TARGET_SPEC_IDS,
    _cohort_stats,
    _rows_out_path,
    _vector,
    fit_setting,
    importance_setting,
    iter_rows_jsonl,
    predict_setting,
    run_all_features as run_all_features_a,
    run_worker_features,
)
from tools.exploration_entry_model_b2 import _ex_top3_sol, run_all_features_b
from tools.exploration_exits import ENTRY_SIZE, POOL_END as POOL_A_END, POOL_HOURS as POOL_A_HOURS, POOL_START as POOL_A_START, _chunk
from tools.latency_curve import _iter_trades, _rss_mb
from tools.oracle_insample_adapter import BACKFILL_C, POOL_C_END, POOL_C_HOURS, POOL_C_START, _hour_info_c
from tools.oracle_live_adapter import (
    POOL_B_CUTOFF_ISO,
    POOL_B_END,
    POOL_B_HOURS,
    POOL_B_START,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL

# --- Days: 9 UTC days, in order, one per source-pool boundary ---------------

DAYS_A = ("2026-09-19", "2026-09-20", "2026-09-21")
DAYS_C_ONLY = ("2026-09-22", "2026-09-23", "2026-09-24")  # 09-25 is split C(00-06)/B(07-23), tagged by day below
DAYS_B_ONLY = ("2026-09-26", "2026-09-27")
DAYS_ALL = ("2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27")
assert len(DAYS_ALL) == 9
assert DAYS_ALL == DAYS_A + DAYS_C_ONLY + ("2026-09-25",) + DAYS_B_ONLY

BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1

# --- Pool C: streaming worker orchestration (mirrors pool A exactly; the ---
# --- schema is identical, so this reuses run_worker_features unchanged, ---
# --- just pointed at a different root via hour_info_fn.) -------------------


def build_creator_history_c() -> dict[str, list[int]]:
    hist: dict[str, list[int]] = {}
    for key in POOL_C_HOURS:
        hour = _hour_info_c(key)
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


def plan_workers_c(max_workers: int = 3, buffer_hours: int = 2) -> list[tuple[int, list[str], list[str]]]:
    chunks = _chunk(POOL_C_HOURS, max_workers)
    plan = []
    for i, home in enumerate(chunks):
        idx = POOL_C_HOURS.index(home[-1])
        buf = POOL_C_HOURS[idx + 1 : idx + 1 + buffer_hours]
        plan.append((i, home, buf))
    return plan


def run_worker_c(worker_id: int, home_keys: list[str], buffer_keys: list[str], creator_hist: dict[str, list[int]]) -> list[dict[str, Any]]:
    return run_worker_features(worker_id, home_keys, buffer_keys, creator_hist, hour_info_fn=_hour_info_c)


def run_all_features_c(max_workers: int = 3, buffer_hours: int = 2) -> list[dict[str, Any]]:
    print(f"pool C trade hours: {POOL_C_START} .. {POOL_C_END} ({len(POOL_C_HOURS)})", file=sys.stderr, flush=True)
    print(f"pool C root: {BACKFILL_C}", file=sys.stderr, flush=True)
    creator_hist = build_creator_history_c()
    print(f"pool C creator_history creators={len(creator_hist)}", file=sys.stderr, flush=True)
    plan = plan_workers_c(max_workers, buffer_hours)
    print(f"pool C worker_plan={[(i, h[0], h[-1], b) for i, h, b in plan]}", file=sys.stderr, flush=True)
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for worker_id, home, buf in plan:
            rows.extend(run_worker_c(worker_id, home, buf, creator_hist))
        return rows
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=len(plan)) as pool:
        results = pool.starmap(run_worker_c, [(i, h, b, creator_hist) for i, h, b in plan])
    for part in results:
        rows.extend(part)
    return rows


# --- Settings: same lgb_medium tree shape, three different targets --------

_LGB_MEDIUM = next(s for s in SETTINGS if s["id"] == "lgb_medium")
assert _LGB_MEDIUM["kind"] == "lightgbm"

SETTINGS_B3 = [
    {
        "id": "s1_reg",
        "label": "S1: lgb_medium regression on per-trade pressure net",
        "kind": "lightgbm_reg",
        "num_leaves": _LGB_MEDIUM["num_leaves"],
        "min_data_in_leaf": _LGB_MEDIUM["min_data_in_leaf"],
        "learning_rate": _LGB_MEDIUM["learning_rate"],
        "rounds": _LGB_MEDIUM["rounds"],
    },
    {
        "id": "s2_clf",
        "label": "S2: lgb_medium classifier on P(pressure net > 0)",
        "kind": "lightgbm",
        "num_leaves": _LGB_MEDIUM["num_leaves"],
        "min_data_in_leaf": _LGB_MEDIUM["min_data_in_leaf"],
        "learning_rate": _LGB_MEDIUM["learning_rate"],
        "rounds": _LGB_MEDIUM["rounds"],
    },
    {
        "id": "s3_reg_winsor",
        "label": "S3: lgb_medium regression on pressure net, labels winsorized at the training fold's 1st/99th pct",
        "kind": "lightgbm_reg_winsor",
        "num_leaves": _LGB_MEDIUM["num_leaves"],
        "min_data_in_leaf": _LGB_MEDIUM["min_data_in_leaf"],
        "learning_rate": _LGB_MEDIUM["learning_rate"],
        "rounds": _LGB_MEDIUM["rounds"],
    },
]
assert len(SETTINGS_B3) == 3
assert {s["kind"] for s in SETTINGS_B3} == {"lightgbm_reg", "lightgbm", "lightgbm_reg_winsor"}


def _train_lightgbm_reg(x: Sequence[Sequence[float]], y: Sequence[float], setting: dict[str, Any]) -> Any:
    import lightgbm as lgb
    import numpy as np

    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    train = lgb.Dataset(xa, label=ya, feature_name=list(FEATURE_NAMES))
    params = {
        "objective": "regression",
        "metric": "l2",
        "num_threads": 1,
        "verbosity": -1,
        "learning_rate": setting["learning_rate"],
        "num_leaves": setting["num_leaves"],
        "min_data_in_leaf": setting["min_data_in_leaf"],
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
    }
    return lgb.train(params, train, num_boost_round=setting["rounds"])


def winsorize_train_labels(y_raw: Sequence[float]) -> tuple[list[float], float, float]:
    """Clip y_raw to its own 1st/99th percentile, computed on y_raw itself.
    The caller must pass only TRAINING-fold labels -- never the held-out
    day's -- so this cannot leak held-out information into the clip bounds.
    Pure and side-effect-free so it is testable without lightgbm."""
    import numpy as np

    arr = np.asarray(y_raw, dtype=np.float64)
    if arr.size == 0:
        return [], 0.0, 0.0
    p1, p99 = np.percentile(arr, [1, 99])
    clipped = np.clip(arr, p1, p99)
    return [float(v) for v in clipped], float(p1), float(p99)


def _press_pct_labels(rows: Sequence[dict[str, Any]]) -> list[float]:
    return [r["press"] / ENTRY_SIZE * 100.0 for r in rows]


def fit_setting_b3(x_train: Sequence[Sequence[float]], rows_train: Sequence[dict[str, Any]], setting: dict[str, Any]) -> dict[str, Any] | None:
    kind = setting["kind"]
    if kind == "lightgbm":
        y = [1 if r["press"] > 0 else 0 for r in rows_train]
        if len(set(y)) < 2:
            return None
        return {"kind": "lightgbm", "fit": fit_setting(x_train, y, setting)}
    y_raw = _press_pct_labels(rows_train)
    if kind == "lightgbm_reg":
        model = _train_lightgbm_reg(x_train, y_raw, setting)
        return {"kind": "lightgbm_reg", "model": model}
    if kind == "lightgbm_reg_winsor":
        y_clipped, p1, p99 = winsorize_train_labels(y_raw)
        model = _train_lightgbm_reg(x_train, y_clipped, setting)
        return {"kind": "lightgbm_reg_winsor", "model": model, "train_p1_pct": p1, "train_p99_pct": p99}
    raise ValueError(f"unknown setting kind {kind!r}")


def predict_setting_b3(fit: dict[str, Any], x_test: Sequence[Sequence[float]]) -> list[float]:
    if fit["kind"] == "lightgbm":
        return predict_setting(fit["fit"], x_test)
    import numpy as np

    xa = np.asarray(x_test, dtype=np.float64)
    return [float(v) for v in fit["model"].predict(xa)]


def importance_setting_b3(fit: dict[str, Any]) -> list[tuple[str, float]]:
    if fit["kind"] == "lightgbm":
        return importance_setting(fit["fit"])[:8]
    gain = fit["model"].feature_importance(importance_type="gain")
    pairs = list(zip(FEATURE_NAMES, (float(v) for v in gain)))
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    return pairs[:8]


# --- Cohort stats: both fail models, total + ex-top-3 SOL for both ---------


def _cohort_stats_b3(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    base = _cohort_stats(rows)  # n, gross_mean_pct, flat_net_mean_pct, press_net_mean_pct
    flat_vals = [r["flat"] for r in rows]
    press_vals = [r["press"] for r in rows]
    base["flat_net_total_sol"] = (sum(flat_vals) / LAMPORTS_PER_SOL) if flat_vals else None
    base["flat_ex_top3_sol"] = _ex_top3_sol(flat_vals)
    base["press_net_total_sol"] = (sum(press_vals) / LAMPORTS_PER_SOL) if press_vals else None
    base["press_ex_top3_sol"] = _ex_top3_sol(press_vals)
    return base


def evaluate_fold_b3(rows_train: Sequence[dict[str, Any]], rows_test: Sequence[dict[str, Any]], setting: dict[str, Any]) -> dict[str, Any]:
    if len(rows_train) < 20:
        return {"trained": False, "n_train": len(rows_train), "n_test": len(rows_test)}
    x_train = [_vector(r["features"]) for r in rows_train]
    fit = fit_setting_b3(x_train, rows_train, setting)
    if fit is None:
        return {"trained": False, "n_train": len(rows_train), "n_test": len(rows_test)}
    x_test = [_vector(r["features"]) for r in rows_test]
    scores = predict_setting_b3(fit, x_test)
    order = sorted(range(len(rows_test)), key=lambda i: scores[i], reverse=True)
    ranked = [rows_test[i] for i in order]
    n = len(ranked)
    cohorts = {"all": _cohort_stats_b3(ranked)}
    top_rows: dict[str, list[dict[str, Any]]] = {}
    for pct in (10, 20):
        k = max(1, round(n * pct / 100.0))
        picked = ranked[:k]
        cohorts[f"top{pct}"] = _cohort_stats_b3(picked)
        top_rows[f"top{pct}_rows"] = picked
    out = {
        "trained": True,
        "n_train": len(rows_train),
        "n_test": n,
        "cohorts": cohorts,
        "importance": importance_setting_b3(fit),
        **top_rows,
    }
    if fit["kind"] == "lightgbm_reg_winsor":
        out["train_p1_pct"] = fit["train_p1_pct"]
        out["train_p99_pct"] = fit["train_p99_pct"]
    return out


def leave_one_day_out_b3(rows: Sequence[dict[str, Any]], days: Sequence[str], setting: dict[str, Any]) -> dict[str, Any]:
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for r in rows:
        if r["day"] in by_day:
            by_day[r["day"]].append(r)
    out: dict[str, Any] = {}
    for held_out in days:
        train = [r for d in days if d != held_out for r in by_day.get(d, [])]
        test = by_day.get(held_out, [])
        out[held_out] = evaluate_fold_b3(train, test, setting)
    return out


# --- Pooling over the 9 held-out days, and the pre-stated candidate screen -


def _pool_rows(lodo: dict[str, dict[str, Any]], cohort_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    pooled: list[dict[str, Any]] = []
    days_used: list[str] = []
    for day, fold in lodo.items():
        if not fold.get("trained"):
            continue
        pooled.extend(fold.get(f"{cohort_key}_rows", []))
        days_used.append(day)
    return pooled, days_used


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _ci_lo(values: Sequence[float]) -> float:
    """Bootstrap 90% CI lower bound on the mean: 1000 draws, seed 1, 5th
    percentile. Same procedure tools.exploration_exits._ci_lo uses,
    reimplemented here (not imported) so this module has no dependency on
    exploration_exits' random.Random call order -- the algorithm is
    identical and tools/test_exploration_entry_model_b3.py checks that."""
    import random

    if not values:
        return 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = []
    for _ in range(BOOTSTRAP_DRAWS):
        total = 0.0
        for _i in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return _pct(means, 0.05)


def pooled_cohort_report(lodo: dict[str, dict[str, Any]], cohort_key: str) -> dict[str, Any]:
    """Pool the given cohort ("top10"/"top20") across every trained held-out
    day, then report both fail models: n, mean net %, bootstrap 90% CI lower
    bound on the mean, total SOL, ex-top-3 SOL, and how many of the held-out
    days were individually positive under that fail model (that day's own
    cohort mean net, not the pooled one)."""
    pooled, days_used = _pool_rows(lodo, cohort_key)
    stats = _cohort_stats_b3(pooled)
    flat_vals = [r["flat"] for r in pooled]
    press_vals = [r["press"] for r in pooled]
    stats["flat_ci_lo_pct"] = (_ci_lo(flat_vals) / ENTRY_SIZE * 100.0) if flat_vals else None
    stats["press_ci_lo_pct"] = (_ci_lo(press_vals) / ENTRY_SIZE * 100.0) if press_vals else None
    n_days_total = 0
    n_days_flat_pos = 0
    n_days_press_pos = 0
    for day, fold in lodo.items():
        if not fold.get("trained"):
            continue
        c = fold["cohorts"][cohort_key]
        n_days_total += 1
        if c["flat_net_mean_pct"] is not None and c["flat_net_mean_pct"] > 0:
            n_days_flat_pos += 1
        if c["press_net_mean_pct"] is not None and c["press_net_mean_pct"] > 0:
            n_days_press_pos += 1
    stats["n_days_total"] = n_days_total
    stats["n_days_flat_positive"] = n_days_flat_pos
    stats["n_days_press_positive"] = n_days_press_pos
    stats["days_used"] = sorted(days_used)
    return stats


def pooled_split_by_source(lodo: dict[str, dict[str, Any]], cohort_key: str) -> dict[str, Any]:
    """The same pooled cohort, broken down by which source pool each
    selected trade came from -- report only, no retraining."""
    pooled, _days = _pool_rows(lodo, cohort_key)
    by_pool: dict[str, list[dict[str, Any]]] = {"A": [], "C": [], "B": []}
    for r in pooled:
        by_pool.setdefault(r.get("pool", "?"), []).append(r)
    return {name: _cohort_stats_b3(rows) for name, rows in by_pool.items()}


def screen_candidate(pooled_top10: dict[str, Any]) -> dict[str, Any]:
    """Pre-stated candidate screen (set before this run; do not change it):
    a (setting, exit, top-10%) cell is a CANDIDATE only if, under BOTH fail
    models, its pooled held-out mean > 0 AND its pooled ex-top-3 SOL > 0 AND
    more than half of the 9 held-out days are positive (that fail model's
    own per-day count)."""
    n_days_total = pooled_top10["n_days_total"]
    majority_needed = n_days_total / 2.0

    def _ok(mean_key: str, ex3_key: str, days_key: str) -> bool:
        mean_v = pooled_top10.get(mean_key)
        ex3_v = pooled_top10.get(ex3_key)
        days_v = pooled_top10.get(days_key)
        return mean_v is not None and mean_v > 0 and ex3_v is not None and ex3_v > 0 and days_v is not None and days_v > majority_needed

    flat_ok = _ok("flat_net_mean_pct", "flat_ex_top3_sol", "n_days_flat_positive")
    press_ok = _ok("press_net_mean_pct", "press_ex_top3_sol", "n_days_press_positive")
    return {"flat_ok": flat_ok, "press_ok": press_ok, "candidate": bool(flat_ok and press_ok), "n_days_total": n_days_total, "majority_needed": majority_needed}


def _strip_rows(lodo: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for day, fold in lodo.items():
        f = dict(fold)
        f.pop("top10_rows", None)
        f.pop("top20_rows", None)
        out[day] = f
    return out


# --- Report -------------------------------------------------------------


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%"


def _fmt_sol(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.4f}"


def _day_table(lodo: dict[str, dict[str, Any]], days: Sequence[str]) -> list[str]:
    lines = [
        "| Held-out day | Cohort | n | Flat net % | Flat total SOL | Flat ex-top-3 SOL | Press net % | Press total SOL | Press ex-top-3 SOL |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for day in days:
        fold = lodo.get(day, {})
        if not fold.get("trained"):
            lines.append(f"| {day} | (not trained -- n_train={fold.get('n_train', 0)}, n_test={fold.get('n_test', 0)}) |  |  |  |  |  |  |  |")
            continue
        for cohort_name in ("all", "top10", "top20"):
            c = fold["cohorts"][cohort_name]
            lines.append(
                f"| {day} | {cohort_name} | {c['n']} | {_fmt_pct(c['flat_net_mean_pct'])} | {_fmt_sol(c['flat_net_total_sol'])} | "
                f"{_fmt_sol(c['flat_ex_top3_sol'])} | {_fmt_pct(c['press_net_mean_pct'])} | {_fmt_sol(c['press_net_total_sol'])} | "
                f"{_fmt_sol(c['press_ex_top3_sol'])} |"
            )
    return lines


def _pooled_table_row(spec_id: str, setting_label: str, pooled_top10: dict[str, Any], screen: dict[str, Any]) -> str:
    verdict = "**CANDIDATE**" if screen["candidate"] else "no"
    return (
        f"| `{spec_id}` | {setting_label} | {pooled_top10['n']} | "
        f"{_fmt_pct(pooled_top10['flat_net_mean_pct'])} (CI lo {_fmt_pct(pooled_top10['flat_ci_lo_pct'])}) | "
        f"{_fmt_sol(pooled_top10['flat_net_total_sol'])} | {_fmt_sol(pooled_top10['flat_ex_top3_sol'])} | "
        f"{pooled_top10['n_days_flat_positive']}/{pooled_top10['n_days_total']} | "
        f"{_fmt_pct(pooled_top10['press_net_mean_pct'])} (CI lo {_fmt_pct(pooled_top10['press_ci_lo_pct'])}) | "
        f"{_fmt_sol(pooled_top10['press_net_total_sol'])} | {_fmt_sol(pooled_top10['press_ex_top3_sol'])} | "
        f"{pooled_top10['n_days_press_positive']}/{pooled_top10['n_days_total']} | {verdict} |"
    )


def write_report(
    out_md: Path,
    out_json: Path,
    combined_full: dict[str, Any],
    pooled: dict[str, Any],
    screens: dict[str, Any],
    n_rows: dict[str, Any],
    wall_s: float,
) -> None:
    combined_json = {
        spec_id: {sid: _strip_rows(lodo) for sid, lodo in by_setting.items()} for spec_id, by_setting in combined_full.items()
    }
    out_json.write_text(
        json.dumps(
            {
                "schema": "exploration_entry_model_b3_v1",
                "pool_a": {"start": POOL_A_START, "end": POOL_A_END, "hours": len(POOL_A_HOURS), "hours_read": POOL_A_HOURS},
                "pool_c": {"start": POOL_C_START, "end": POOL_C_END, "hours": len(POOL_C_HOURS), "hours_read": POOL_C_HOURS},
                "pool_b": {"start": POOL_B_START, "end": POOL_B_END, "hours": len(POOL_B_HOURS), "hours_read": POOL_B_HOURS, "cutoff_iso": POOL_B_CUTOFF_ISO},
                "days_all": DAYS_ALL,
                "settings": SETTINGS_B3,
                "target_specs": TARGET_SPEC_IDS,
                "feature_names": FEATURE_NAMES,
                "n_rows": n_rows,
                "results": combined_json,
                "pooled": pooled,
                "screens": screens,
                "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED, "pctile": 5},
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
    lines.append('  subagentId: "exploration-entry-model-b3-2026-09-28"')
    lines.append("---")
    lines.append("")
    lines.append("# Exploration: entry model B3 on 9 days (screen pre-stated)")
    lines.append("")
    lines.append(
        "**Exploration, not a pre-registered test.** This is lane B's third pass at a learned migrate "
        "entry filter, extending B2's 5.7 held-out days (`ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.md`) "
        "to the full 9-day exploration pool by adding the Oracle in-sample backfill (`tools/"
        "oracle_insample_adapter.py`) between the fast-box pool and the Oracle live tape. It is a candidate "
        "for a later pre-registered test on a fresh, never-read block -- never a promote -- and it does not "
        "touch the forward runner, the promotion gate, or any live book. The frozen execution, features, and "
        "causal no-lookahead boundary are all unchanged from `tools/exploration_entry_model.py`."
    )
    lines.append("")
    lines.append(
        "**Winner's-curse note.** 3 settings x 2 exits = 6 (setting, exit, top-10%) cells were screened "
        "below by one pre-stated rule, fixed by the manager before this run and never adjusted after seeing "
        "results. A cell passing this screen is still the best of 6 by construction and still needs its own "
        "pre-registration and its own out-of-sample block before it means anything for the promotion gate."
    )
    lines.append("")
    lines.append("## Data: three source pools, 9 UTC days, no overlap")
    lines.append("")
    lines.append(
        f"Pool A (fast-box backfill): `{POOL_A_START}` - `{POOL_A_END}` ({len(POOL_A_HOURS)} hours). "
        f"Pool C (Oracle in-sample backfill, new here): `{POOL_C_START}` - `{POOL_C_END}` ({len(POOL_C_HOURS)} hours), "
        "same schema as pool A verified on-disk (`tools/test_oracle_insample_adapter.py`), reused via the fast "
        "pool's own loaders pointed at a different root. Pool B (Oracle live tape): `{}` - `{}` ({} hours), real "
        "receive times, reused unchanged from lane B2. Hour 07 on 2026-09-25 belongs to pool B only -- pool C's "
        "fence stops at `{}` -- so no hour is read twice.".format(POOL_B_START, POOL_B_END, len(POOL_B_HOURS), POOL_C_END)
    )
    lines.append("")
    lines.append(
        f"Rows scored (full pool, MISS included, no survivorship / DEC-007): "
        + ", ".join(
            f"`{sid}`: A={n_rows['A'].get(sid, 0)}, C={n_rows['C'].get(sid, 0)}, B={n_rows['B'].get(sid, 0)}, total={n_rows['A'].get(sid, 0) + n_rows['C'].get(sid, 0) + n_rows['B'].get(sid, 0)}"
            for sid in TARGET_SPEC_IDS
        )
    )
    lines.append("")
    lines.append("## Settings (exactly three, fixed before this run)")
    lines.append("")
    for s in SETTINGS_B3:
        lines.append(f"- `{s['id']}` -- {s['label']} (num_leaves={s['num_leaves']}, min_data_in_leaf={s['min_data_in_leaf']}, learning_rate={s['learning_rate']}, rounds={s['rounds']})")
    lines.append("")
    lines.append(
        "Leave-one-day-out over all 9 UTC days: each held-out day's model is trained on the other 8 days, "
        "pooled across all three source pools (no source flag ever fed to the model)."
    )
    lines.append("")
    lines.append("## Pre-stated candidate screen, pooled top-10% over the 9 held-out days")
    lines.append("")
    lines.append(
        "A cell is a **CANDIDATE** only if, under BOTH fail models: pooled top-10% mean net % > 0, pooled "
        "ex-top-3 SOL > 0, and more than half of the 9 held-out days are individually positive (that day's "
        "own top-10% cohort mean, under that fail model)."
    )
    lines.append("")
    lines.append(
        "| Exit | Setting | n | Flat mean % (CI lo) | Flat total SOL | Flat ex-top-3 SOL | Flat days+ | "
        "Press mean % (CI lo) | Press total SOL | Press ex-top-3 SOL | Press days+ | Screen |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    any_candidate = False
    for spec_id in TARGET_SPEC_IDS:
        for setting in SETTINGS_B3:
            sid = setting["id"]
            p10 = pooled[spec_id][sid]["top10"]
            scr = screens[spec_id][sid]
            any_candidate = any_candidate or scr["candidate"]
            lines.append(_pooled_table_row(spec_id, sid, p10, scr))
    lines.append("")
    if any_candidate:
        passed = [f"`{spec_id}`/`{sid}`" for spec_id in TARGET_SPEC_IDS for sid in (s["id"] for s in SETTINGS_B3) if screens[spec_id][sid]["candidate"]]
        lines.append(f"**Cells passing the screen: {', '.join(passed)}.**")
    else:
        lines.append("**No cell passes the pre-stated screen.** None of the 6 (setting, exit) cells clears all three conditions under both fail models.")
    lines.append("")

    for spec_id in TARGET_SPEC_IDS:
        lines.append(f"## `{spec_id}`")
        lines.append("")
        for setting in SETTINGS_B3:
            sid = setting["id"]
            lodo = combined_full[spec_id][sid]
            lines.append(f"### `{spec_id}` / `{sid}` -- per held-out day (top10/top20 vs all)")
            lines.append("")
            lines.extend(_day_table(lodo, DAYS_ALL))
            lines.append("")
            lines.append(f"### `{spec_id}` / `{sid}` -- pooled over 9 held-out days")
            lines.append("")
            for cohort_key in ("top10", "top20"):
                c = pooled[spec_id][sid][cohort_key]
                lines.append(
                    f"- **{cohort_key}**: n={c['n']}, flat {_fmt_pct(c['flat_net_mean_pct'])} (CI lo {_fmt_pct(c['flat_ci_lo_pct'])}, "
                    f"total {_fmt_sol(c['flat_net_total_sol'])} SOL, ex-top-3 {_fmt_sol(c['flat_ex_top3_sol'])} SOL, "
                    f"{c['n_days_flat_positive']}/{c['n_days_total']} days+), press {_fmt_pct(c['press_net_mean_pct'])} "
                    f"(CI lo {_fmt_pct(c['press_ci_lo_pct'])}, total {_fmt_sol(c['press_net_total_sol'])} SOL, "
                    f"ex-top-3 {_fmt_sol(c['press_ex_top3_sol'])} SOL, {c['n_days_press_positive']}/{c['n_days_total']} days+)"
                )
            lines.append("")
            lines.append(f"### `{spec_id}` / `{sid}` -- pooled top10%, split by source")
            lines.append("")
            split = pooled_split_by_source(lodo, "top10")
            lines.append("| Source | n | Flat net % | Press net % |")
            lines.append("| --- | ---: | ---: | ---: |")
            for pool_name, label in (("A", "fast"), ("C", "Oracle in-sample"), ("B", "Oracle live")):
                s = split.get(pool_name, {"n": 0, "flat_net_mean_pct": None, "press_net_mean_pct": None})
                lines.append(f"| {label} | {s['n']} | {_fmt_pct(s['flat_net_mean_pct'])} | {_fmt_pct(s['press_net_mean_pct'])} |")
            lines.append("")
            # Feature importances + per-fold stability
            agg: dict[str, list[float]] = {}
            top5_by_day: dict[str, list[str]] = {}
            for day in DAYS_ALL:
                fold = lodo.get(day, {})
                if not fold.get("trained"):
                    continue
                names = [name for name, _v in fold["importance"]]
                top5_by_day[day] = names[:5]
                for name, val in fold["importance"]:
                    agg.setdefault(name, []).append(val)
            ranked = sorted(agg.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
            lines.append(f"### `{spec_id}` / `{sid}` -- top features, mean importance across trained folds, and per-fold stability")
            lines.append("")
            lines.append("| Feature | Mean importance | Held-out days in its top-5 |")
            lines.append("| --- | ---: | --- |")
            for name, vals in ranked[:8]:
                days_in_top5 = [d for d, top5 in top5_by_day.items() if name in top5]
                lines.append(f"| `{name}` | {sum(vals) / len(vals):.3g} | {len(days_in_top5)}/9: {', '.join(days_in_top5) if days_in_top5 else '-'} |")
            lines.append("")

    lines.append(f"Wall time: {wall_s:.0f}s. Full grid: `exploration-entry-model-b3-2026-09-28.json`.")
    lines.append("")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exploration lane B3: entry model on 9 days (screen pre-stated)")
    parser.add_argument("--out-md", type=Path, default=Path("ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md"))
    parser.add_argument("--out-json", type=Path, default=Path("ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.json"))
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--buffer-hours", type=int, default=2)
    args = parser.parse_args()
    t0 = time.time()

    print(f"hours_read_pool_A={POOL_A_HOURS}", file=sys.stderr, flush=True)
    print(f"hours_read_pool_C={POOL_C_HOURS}", file=sys.stderr, flush=True)
    print(f"hours_read_pool_B={POOL_B_HOURS}", file=sys.stderr, flush=True)

    print("=== pool A (fast-box) ===", file=sys.stderr, flush=True)
    rows_a = run_all_features_a(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    print("=== pool C (Oracle in-sample) ===", file=sys.stderr, flush=True)
    rows_c = run_all_features_c(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    print("=== pool B (Oracle live) ===", file=sys.stderr, flush=True)
    rows_b = run_all_features_b(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    for r in rows_a:
        r["pool"] = "A"
    for r in rows_c:
        r["pool"] = "C"
    for r in rows_b:
        r["pool"] = "B"
    rows_all = rows_a + rows_c + rows_b

    by_spec_a: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    by_spec_c: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    by_spec_b: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    by_spec_all: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    for r in rows_a:
        by_spec_a.setdefault(r["spec"], []).append(r)
        by_spec_all.setdefault(r["spec"], []).append(r)
    for r in rows_c:
        by_spec_c.setdefault(r["spec"], []).append(r)
        by_spec_all.setdefault(r["spec"], []).append(r)
    for r in rows_b:
        by_spec_b.setdefault(r["spec"], []).append(r)
        by_spec_all.setdefault(r["spec"], []).append(r)
    n_rows = {
        "A": {sid: len(by_spec_a.get(sid, [])) for sid in TARGET_SPEC_IDS},
        "C": {sid: len(by_spec_c.get(sid, [])) for sid in TARGET_SPEC_IDS},
        "B": {sid: len(by_spec_b.get(sid, [])) for sid in TARGET_SPEC_IDS},
    }

    combined_full: dict[str, Any] = {sid: {} for sid in TARGET_SPEC_IDS}
    pooled: dict[str, Any] = {sid: {} for sid in TARGET_SPEC_IDS}
    screens: dict[str, Any] = {sid: {} for sid in TARGET_SPEC_IDS}
    for spec_id in TARGET_SPEC_IDS:
        rows = by_spec_all.get(spec_id, [])
        for setting in SETTINGS_B3:
            sid = setting["id"]
            print(f"=== LODO {spec_id} / {sid} ===", file=sys.stderr, flush=True)
            lodo = leave_one_day_out_b3(rows, DAYS_ALL, setting)
            combined_full[spec_id][sid] = lodo
            pooled[spec_id][sid] = {
                "top10": pooled_cohort_report(lodo, "top10"),
                "top20": pooled_cohort_report(lodo, "top20"),
            }
            screens[spec_id][sid] = screen_candidate(pooled[spec_id][sid]["top10"])
            print(f"rss_mb={_rss_mb()}", file=sys.stderr, flush=True)

    wall_s = time.time() - t0
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.out_md, args.out_json, combined_full, pooled, screens, n_rows, wall_s)
    print(f"wrote {args.out_md} and {args.out_json} in {wall_s:.0f}s", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
