#!/usr/bin/env python3
"""EXP-011: freeze the migrate entry model (leakage-ablated S2).

Lane B3 (#156, tools/exploration_entry_model_b3.py, ARTIFACTS/lab/
exploration-entry-model-b3-2026-09-28.md) found that S2 (the lgb_medium
classifier on P(pressure net > 0)), taking the top 10% of migrate entries
with exit tp50_sl30, survives a leakage ablation. The manager+quant-proof
audit on PR #156 found `same_slot_buys` and `nearby_buy_sol` are computed
at the slot+1 landing (tools/latency_curve.py::_pressure), i.e. AFTER the
migrate decision time T -- lookahead the no-lookahead test never covered.
Removed, the result survives: flat +6.22% (CI lo +3.85%), pressure +3.59%
(CI lo +2.06%), ex-top-3 +24.97/+14.17 SOL, 9/9 days positive under both
fail models, over the 9-day LODO.

This script FREEZES that ablated model:
  - Feature set: tools.exploration_entry_model.FEATURE_NAMES minus
    ("same_slot_buys", "nearby_buy_sol") -- FROZEN_FEATURE_NAMES below.
    Every remaining feature is asserted to come from compute_features()
    alone, fed causal_events() output (strictly t_recv_ms < T) -- see the
    module-level assertion right after FROZEN_FEATURE_NAMES, and the
    no-lookahead perturbation test in tools/test_exp011_freeze.py.
  - Training data: all 9 exploration days (pools A + C + B, B3's own
    loaders and whitelists, reused unchanged -- tools.exploration_exits
    pool A, tools.oracle_insample_adapter pool C, tools.oracle_live_adapter
    pool B via tools.exploration_entry_model_b2.run_all_features_b).
  - Model: exactly S2's lgb_medium params (num_leaves=15,
    min_data_in_leaf=20, learning_rate=0.05, rounds=100), objective=binary
    on y = 1{pressure net > 0} under the frozen execution (migrate
    trigger, slot+1, direct, 0.0005 SOL/side, 0.5 SOL, exit tp50_sl30),
    plus deterministic=True, num_threads=1, seed=SEED (all already true of
    S2's own settings in tools.exploration_entry_model -- restated here,
    not changed).
  - Threshold: the ablated S2 LODO is recomputed on the frozen feature set
    (9 folds, same settings, deterministic). The pooled out-of-fold scores
    (every scored tpsl_tp50_sl30 row -- filled AND miss, DEC-007, no
    survivorship) set the threshold at their 90th percentile.

EXPLORATION -> FREEZE only. This script does not touch the forward runner,
the promotion gate, or any live book. The frozen artifacts it writes
(ARTIFACTS/exp011/) are read once, later, by tools/exp011_score.py (a
follow-up PR) against the reserved fresh holdout block -- see
EXP/EXP-011-migrate-entry-model-prereg.md.

Holdout fence (hard): this script never reads either fast-box holdout
walker -- walker B (/var/lib/mal/backfill-fast-b, now covering
[2026-09-12T12, 2026-09-15T12)) or walker C (/var/lib/mal/backfill-fast-c,
covering [2026-09-09T12, 2026-09-12T12)) -- or any hour in
[2026-09-09T12, 2026-09-15T12) -- EXP-011's own reserved, unread holdout,
split across those two walkers to halve the wall-clock wait but otherwise
unchanged (docs/HOLDOUT_LEDGER.md). It only ever touches the three
pool-A/C/B hour lists below (exploration pools, not to be confused with
the holdout walkers B/C above), all of which are asserted (at import time,
before any file is opened) to fall entirely outside that range.

Run: nice -n 19 python3 -m tools.exp011_freeze [--out-dir ARTIFACTS/exp011]
[--max-workers 3] [--buffer-hours 2]. Keep max-workers <= 3 -- two backfill
walkers share this box.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_entry_model import FEATURE_NAMES, SEED
from tools.exploration_entry_model import run_all_features as run_all_features_a
from tools.exploration_entry_model_b2 import _ex_top3_sol, run_all_features_b
from tools.exploration_entry_model_b3 import DAYS_ALL, _ci_lo, run_all_features_c
from tools.exploration_entry_model import compute_features
from tools.exploration_exits import ENTRY_SIZE, POOL_HOURS as POOL_A_HOURS
from tools.oracle_insample_adapter import POOL_C_HOURS
from tools.oracle_live_adapter import POOL_B_HOURS
from tools.paper_curve_math import LAMPORTS_PER_SOL

TARGET_SPEC_ID = "tpsl_tp50_sl30"

# --- Frozen feature set: B3's FEATURE_NAMES minus the two lookahead ones --

_DROPPED_LOOKAHEAD_FEATURES = ("same_slot_buys", "nearby_buy_sol")
FROZEN_FEATURE_NAMES: list[str] = [f for f in FEATURE_NAMES if f not in _DROPPED_LOOKAHEAD_FEATURES]
assert len(FROZEN_FEATURE_NAMES) == len(FEATURE_NAMES) - 2, "expected exactly two features dropped"
assert set(_DROPPED_LOOKAHEAD_FEATURES).isdisjoint(FROZEN_FEATURE_NAMES)

# Every frozen feature must come from compute_features() alone -- the pure
# aggregation over causal_events()'s strictly-before-T output -- and never
# from tools.latency_curve._pressure() (the slot+1 landing state that
# produces same_slot_buys/nearby_buy_sol). Proved here structurally: a
# throwaway call's key set must equal FROZEN_FEATURE_NAMES exactly, with no
# extra and no missing name. tools/test_exp011_freeze.py additionally
# proves it behaviorally (perturbing landing-slot data never moves these
# features) and per-feature (every one of the 18 names is individually
# exercised by a no-lookahead assertion).
_PROBE_FEATS = compute_features([], create_ms=0, first_price=None, mig_ms=1, creator_prior_mints_24h=0)
assert set(FROZEN_FEATURE_NAMES) == set(_PROBE_FEATS.keys()), (
    "FROZEN_FEATURE_NAMES must equal compute_features()'s own causal output exactly"
)

# --- Holdout fence: EXP-011's reserved, unread block ------------------------

HOLDOUT_START = "2026-09-09T12"
HOLDOUT_END = "2026-09-15T12"


def _assert_never_holdout(hours: Sequence[str], label: str) -> None:
    for h in hours:
        assert not (HOLDOUT_START <= h < HOLDOUT_END), f"{label} hour {h!r} falls inside EXP-011's reserved holdout [{HOLDOUT_START}, {HOLDOUT_END})"


_assert_never_holdout(POOL_A_HOURS, "pool A")
_assert_never_holdout(POOL_C_HOURS, "pool C")
_assert_never_holdout(POOL_B_HOURS, "pool B")
# Belt and suspenders: every pool hour this script can ever touch is newer
# than the holdout's own end, so no pool can ever reach into it even if the
# holdout window were misdeclared above.
assert all(h >= HOLDOUT_END for h in POOL_A_HOURS), "pool A reaches into or before the holdout"
assert all(h >= HOLDOUT_END for h in POOL_C_HOURS), "pool C reaches into or before the holdout"
assert all(h >= HOLDOUT_END for h in POOL_B_HOURS), "pool B reaches into or before the holdout"

# --- Frozen S2 lgb_medium hyperparameters -----------------------------------

LGB_PARAMS = {
    "num_leaves": 15,
    "min_data_in_leaf": 20,
    "learning_rate": 0.05,
    "rounds": 100,
}


def _vector(feats: dict[str, float]) -> list[float]:
    return [float(feats.get(name, 0.0)) for name in FROZEN_FEATURE_NAMES]


def _fit(x: Sequence[Sequence[float]], y: Sequence[int]) -> Any:
    import lightgbm as lgb
    import numpy as np

    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.int32)
    pos = int(ya.sum())
    neg = len(ya) - pos
    scale = (neg / pos) if pos else 1.0
    train = lgb.Dataset(xa, label=ya, feature_name=list(FROZEN_FEATURE_NAMES))
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_threads": 1,
        "verbosity": -1,
        "learning_rate": LGB_PARAMS["learning_rate"],
        "num_leaves": LGB_PARAMS["num_leaves"],
        "min_data_in_leaf": LGB_PARAMS["min_data_in_leaf"],
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "scale_pos_weight": scale,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
    }
    return lgb.train(params, train, num_boost_round=LGB_PARAMS["rounds"])


def _predict(model: Any, x: Sequence[Sequence[float]]) -> list[float]:
    import numpy as np

    xa = np.asarray(x, dtype=np.float64)
    return [float(v) for v in model.predict(xa)]


def _label(rows: Sequence[dict[str, Any]]) -> list[int]:
    return [1 if r["press"] > 0 else 0 for r in rows]


# --- Data loading: pools A + C + B, tp50_sl30 rows only, sorted -----------


def load_tp50_rows(max_workers: int = 3, buffer_hours: int = 2) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest: dict[str, Any] = {"pools": {}}
    print("EXP-011 freeze: loading pool A (fast-box backfill)...", file=sys.stderr, flush=True)
    rows_a = [r for r in run_all_features_a(max_workers=max_workers, buffer_hours=buffer_hours) if r["spec"] == TARGET_SPEC_ID]
    for r in rows_a:
        r["pool"] = "A"
    manifest["pools"]["A"] = {"start": POOL_A_HOURS[0], "end": POOL_A_HOURS[-1], "n_hours": len(POOL_A_HOURS), "n_rows": len(rows_a)}
    print(f"pool A: {len(rows_a)} {TARGET_SPEC_ID} rows", file=sys.stderr, flush=True)

    print("EXP-011 freeze: loading pool C (Oracle in-sample backfill)...", file=sys.stderr, flush=True)
    rows_c = [r for r in run_all_features_c(max_workers=max_workers, buffer_hours=buffer_hours) if r["spec"] == TARGET_SPEC_ID]
    for r in rows_c:
        r["pool"] = "C"
    manifest["pools"]["C"] = {"start": POOL_C_HOURS[0], "end": POOL_C_HOURS[-1], "n_hours": len(POOL_C_HOURS), "n_rows": len(rows_c)}
    print(f"pool C: {len(rows_c)} {TARGET_SPEC_ID} rows", file=sys.stderr, flush=True)

    print("EXP-011 freeze: loading pool B (Oracle live tape)...", file=sys.stderr, flush=True)
    rows_b = [r for r in run_all_features_b(max_workers=max_workers, buffer_hours=buffer_hours) if r["spec"] == TARGET_SPEC_ID]
    for r in rows_b:
        r["pool"] = "B"
    manifest["pools"]["B"] = {"start": POOL_B_HOURS[0], "end": POOL_B_HOURS[-1], "n_hours": len(POOL_B_HOURS), "n_rows": len(rows_b)}
    print(f"pool B: {len(rows_b)} {TARGET_SPEC_ID} rows", file=sys.stderr, flush=True)

    rows = rows_a + rows_c + rows_b
    # Deterministic order: independent of worker scheduling/process timing,
    # so two runs over the same sealed data always fit on the same row
    # order -- required for the md5 reproducibility check.
    rows.sort(key=lambda r: (r["day"], r["mint"], r["spec"]))
    manifest["n_rows_total"] = len(rows)
    manifest["days"] = sorted({r["day"] for r in rows})
    return rows, manifest


# --- LODO over the 9 exploration days: pooled out-of-fold scores ----------


def leave_one_day_out_oof(rows: Sequence[dict[str, Any]], days: Sequence[str] = DAYS_ALL) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for r in rows:
        if r["day"] in by_day:
            by_day[r["day"]].append(r)
    oof: list[dict[str, Any]] = []
    for held_out in days:
        train = [r for d in days if d != held_out for r in by_day.get(d, [])]
        test = by_day.get(held_out, [])
        if len(train) < 20 or len(test) == 0:
            continue
        x_train = [_vector(r["features"]) for r in train]
        y_train = _label(train)
        if len(set(y_train)) < 2:
            continue
        model = _fit(x_train, y_train)
        x_test = [_vector(r["features"]) for r in test]
        scores = _predict(model, x_test)
        for r, score in zip(test, scores):
            oof.append(
                {
                    "day": r["day"],
                    "mint": r["mint"],
                    "score": score,
                    "label": 1 if r["press"] > 0 else 0,
                    "filled": bool(r["filled"]),
                }
            )
    return oof


def _percentile(sorted_vals: Sequence[float], p: float) -> float:
    """Same non-interpolating percentile tools.exploration_entry_model_b3
    uses for its bootstrap CI (`_pct`): index = round(p * (n-1)) into the
    sorted array. Reimplemented here (not imported) so this module has no
    import-time dependency on b3's private helper; identical algorithm."""
    if not sorted_vals:
        return 0.0
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def compute_threshold(oof: Sequence[dict[str, Any]], pct: float = 0.90) -> dict[str, Any]:
    """THE frozen threshold used by tools/exp011_score.py against the
    holdout (§3/§B of the pre-registration): the 90th percentile of the
    OUTER 9-fold LODO out-of-fold scores -- each day's rows scored by the
    single model trained on the other 8 days (leave_one_day_out_oof
    above), pooled across all 9 days, before any threshold is chosen. This
    is NOT the nested/fixed-threshold report in tools.exp011_freeze's
    nested_fixed_threshold_lodo (report-only, a different, per-fold
    threshold, never used for the frozen holdout entry rule)."""
    scores = sorted(r["score"] for r in oof)
    threshold = _percentile(scores, pct)
    n = len(scores)
    n_selected = sum(1 for s in scores if s >= threshold)
    return {
        "role": "frozen holdout threshold -- the single number tools/exp011_score.py uses for 'enter iff score(T) >= threshold' on the reserved fast-box holdout",
        "threshold": threshold,
        "percentile_definition": "pooled OUTER out-of-fold scores (9-fold LODO -- each day scored by the model trained on the other 8 days), non-interpolating index=round(p*(n-1)) into the sorted array (same convention as tools.exploration_entry_model_b3._pct)",
        "percentile": pct,
        "n_oof": n,
        "n_selected_at_or_above_threshold": n_selected,
        "selected_fraction": (n_selected / n) if n else None,
    }


# --- Part A (owner scope addition, 2026-09-29): fixed-threshold nested ----
# --- LODO, REPORT ONLY. Never gating; never used to change the threshold, --
# --- features, or params of the frozen model above. ------------------------


def _mean_pct(vals: Sequence[float]) -> float | None:
    return (sum(vals) / len(vals) / ENTRY_SIZE * 100.0) if vals else None


def _ci_lo_pct(vals: Sequence[float]) -> float | None:
    return (_ci_lo(list(vals)) / ENTRY_SIZE * 100.0) if vals else None


def nested_fixed_threshold_lodo(rows: Sequence[dict[str, Any]], days: Sequence[str] = DAYS_ALL) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """For each outer held-out day d (of 9): train the outer fold model on
    the other 8 days (frozen spec: ablated features, S2 params,
    deterministic). Choose that fold's threshold using ONLY those 8 days,
    via an INNER 8-fold LODO over them (8 inner models, never touching day
    d): the 90th percentile of the pooled inner out-of-fold scores. Apply
    that one fixed threshold to day d's rows, scored by the outer fold
    model -- enter iff score >= threshold. This never sees the reserved
    holdout, and it never feeds back into the frozen threshold/features/
    params in compute_threshold/FROZEN_FEATURE_NAMES/LGB_PARAMS above --
    report only (nested_lodo_report). Returns (entries, fold_info):
    entries are the rows that would have entered under this scheme
    (original row dict, plus 'score' and 'threshold'); fold_info is one
    dict per outer day with its threshold and selection counts.
    """
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for r in rows:
        if r["day"] in by_day:
            by_day[r["day"]].append(r)

    entries: list[dict[str, Any]] = []
    fold_info: list[dict[str, Any]] = []
    for outer_day in days:
        inner_days = [d for d in days if d != outer_day]
        inner_oof_scores: list[float] = []
        for inner_held_out in inner_days:
            inner_train_days = [d for d in inner_days if d != inner_held_out]
            inner_train = [r for d in inner_train_days for r in by_day.get(d, [])]
            inner_test = by_day.get(inner_held_out, [])
            if len(inner_train) < 20 or not inner_test:
                continue
            y_inner = _label(inner_train)
            if len(set(y_inner)) < 2:
                continue
            inner_model = _fit([_vector(r["features"]) for r in inner_train], y_inner)
            inner_oof_scores.extend(_predict(inner_model, [_vector(r["features"]) for r in inner_test]))

        outer_train = [r for d in inner_days for r in by_day.get(d, [])]
        outer_test = by_day.get(outer_day, [])
        threshold = _percentile(sorted(inner_oof_scores), 0.90) if inner_oof_scores else None
        trained = False
        n_entered = 0
        if threshold is not None and len(outer_train) >= 20 and outer_test:
            y_outer = _label(outer_train)
            if len(set(y_outer)) >= 2:
                outer_model = _fit([_vector(r["features"]) for r in outer_train], y_outer)
                outer_scores = _predict(outer_model, [_vector(r["features"]) for r in outer_test])
                trained = True
                for r, score in zip(outer_test, outer_scores):
                    if score >= threshold:
                        n_entered += 1
                        entry = dict(r)
                        entry["score"] = score
                        entry["threshold"] = threshold
                        entries.append(entry)
        fold_info.append(
            {
                "outer_day": outer_day,
                "trained": trained,
                "threshold": threshold,
                "n_inner_oof": len(inner_oof_scores),
                "n_test": len(outer_test),
                "n_entered": n_entered,
                "selected_fraction": (n_entered / len(outer_test)) if outer_test else None,
            }
        )
    return entries, fold_info


def nested_lodo_report(entries: Sequence[dict[str, Any]], fold_info: Sequence[dict[str, Any]], days: Sequence[str] = DAYS_ALL) -> dict[str, Any]:
    """Report-only summary of nested_fixed_threshold_lodo's pooled entered
    trades: n, mean %, CI lo, total SOL, ex-top-3 SOL under both fail
    models; per-day selected fraction and mean; fill-conditional net
    (entered-and-filled vs all entered); and the source-pool split. NOT
    gating -- see the promotion gate in EXP-011's pre-registration, scored
    only on the reserved holdout by tools/exp011_score.py."""
    by_day_entries: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for e in entries:
        if e["day"] in by_day_entries:
            by_day_entries[e["day"]].append(e)

    def _fail_cohort(vals: Sequence[float]) -> dict[str, Any]:
        return {
            "n": len(vals),
            "mean_pct": _mean_pct(vals),
            "ci_lo_pct": _ci_lo_pct(vals),
            "total_sol": (sum(vals) / LAMPORTS_PER_SOL) if vals else None,
            "ex_top3_sol": _ex_top3_sol(vals),
        }

    flat_vals_all = [e["flat"] for e in entries]
    press_vals_all = [e["press"] for e in entries]
    flat_stats = _fail_cohort(flat_vals_all)
    press_stats = _fail_cohort(press_vals_all)

    per_day: list[dict[str, Any]] = []
    n_days_total = 0
    n_days_flat_pos = 0
    n_days_press_pos = 0
    fold_by_day = {f["outer_day"]: f for f in fold_info}
    for d in days:
        day_entries = by_day_entries.get(d, [])
        fold = fold_by_day.get(d, {})
        row: dict[str, Any] = {
            "day": d,
            "threshold": fold.get("threshold"),
            "n_test": fold.get("n_test", 0),
            "n_entered": len(day_entries),
            "selected_fraction": fold.get("selected_fraction"),
            "flat_mean_pct": None,
            "press_mean_pct": None,
        }
        if day_entries:
            n_days_total += 1
            flat_mean = _mean_pct([e["flat"] for e in day_entries])
            press_mean = _mean_pct([e["press"] for e in day_entries])
            row["flat_mean_pct"] = flat_mean
            row["press_mean_pct"] = press_mean
            if flat_mean is not None and flat_mean > 0:
                n_days_flat_pos += 1
            if press_mean is not None and press_mean > 0:
                n_days_press_pos += 1
        per_day.append(row)

    filled_entries = [e for e in entries if e.get("filled")]
    fill_conditional = {
        "all_entered": {"n": len(entries), "flat_mean_pct": _mean_pct(flat_vals_all), "press_mean_pct": _mean_pct(press_vals_all)},
        "filled_only": {
            "n": len(filled_entries),
            "flat_mean_pct": _mean_pct([e["flat"] for e in filled_entries]),
            "press_mean_pct": _mean_pct([e["press"] for e in filled_entries]),
        },
    }

    by_pool: dict[str, list[dict[str, Any]]] = {"A": [], "C": [], "B": []}
    for e in entries:
        by_pool.setdefault(e.get("pool", "?"), []).append(e)
    source_split = {
        name: {"n": len(rs), "flat_mean_pct": _mean_pct([r["flat"] for r in rs]), "press_mean_pct": _mean_pct([r["press"] for r in rs])}
        for name, rs in by_pool.items()
    }

    return {
        "schema": "exp011_nested_fixed_threshold_lodo_v1",
        "note": "REPORT ONLY. Not gating. Must not be used to change the frozen threshold, features, or params.",
        "n_days_total": n_days_total,
        "n_days_flat_positive": n_days_flat_pos,
        "n_days_press_positive": n_days_press_pos,
        "flat": flat_stats,
        "press": press_stats,
        "per_day": per_day,
        "fill_conditional": fill_conditional,
        "source_split": source_split,
        "fold_info": list(fold_info),
    }


# --- Final frozen model: fit once on ALL 9 exploration days ---------------


def fit_frozen_model(rows: Sequence[dict[str, Any]]) -> Any:
    x = [_vector(r["features"]) for r in rows]
    y = _label(rows)
    assert len(set(y)) == 2, "frozen training pool must have both classes"
    return _fit(x, y)


def _git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parents[1]), stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return "unknown"


def _md5_of_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def write_outputs(
    out_dir: Path,
    model: Any,
    threshold_info: dict[str, Any],
    oof: Sequence[dict[str, Any]],
    manifest: dict[str, Any],
    wall_s: float,
    nested_report: dict[str, Any] | None = None,
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.txt"
    model.save_model(str(model_path))
    model_md5 = _md5_of_file(model_path)
    (out_dir / "model.md5").write_text(model_md5 + "\n", encoding="utf-8")

    (out_dir / "threshold.json").write_text(json.dumps(threshold_info, indent=2) + "\n", encoding="utf-8")

    features_doc = {
        "schema": "exp011_features_v1",
        "frozen_feature_names": FROZEN_FEATURE_NAMES,
        "dropped_lookahead_features": list(_DROPPED_LOOKAHEAD_FEATURES),
        "source_feature_names": FEATURE_NAMES,
        "source": "tools.exploration_entry_model.FEATURE_NAMES minus the two slot+1-landing pressure features (see PR #156 audit comment)",
    }
    (out_dir / "features.json").write_text(json.dumps(features_doc, indent=2) + "\n", encoding="utf-8")

    manifest_out = dict(manifest)
    manifest_out.update(
        {
            "schema": "exp011_train_manifest_v1",
            "target_spec": TARGET_SPEC_ID,
            "label": "pressure net > 0 under the frozen execution (migrate, slot+1, direct, 0.0005 SOL/side, 0.5 SOL, exit tp50_sl30)",
            "lgb_params": LGB_PARAMS,
            "seed": SEED,
            "deterministic": True,
            "num_threads": 1,
            "code_commit": _git_commit(),
            "trained_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wall_s": wall_s,
            "model_md5": model_md5,
            "holdout_fence": {"start": HOLDOUT_START, "end": HOLDOUT_END, "note": "never read by this script; asserted at import time"},
        }
    )
    (out_dir / "train_manifest.json").write_text(json.dumps(manifest_out, indent=2, default=str) + "\n", encoding="utf-8")

    oof_doc = {
        "schema": "exp011_oof_scores_v1",
        "n_oof": len(oof),
        "days": DAYS_ALL,
        "rows": list(oof),
    }
    (out_dir / "oof_scores.json").write_text(json.dumps(oof_doc, indent=2) + "\n", encoding="utf-8")

    if nested_report is not None:
        (out_dir / "nested_fixed_threshold_lodo.json").write_text(json.dumps(nested_report, indent=2, default=str) + "\n", encoding="utf-8")

    return {"model_md5": model_md5}


def freeze(
    max_workers: int = 3, buffer_hours: int = 2, run_nested_lodo: bool = True
) -> tuple[Any, dict[str, Any], list[dict[str, Any]], dict[str, Any], float, dict[str, Any] | None]:
    t0 = time.time()
    rows, manifest = load_tp50_rows(max_workers=max_workers, buffer_hours=buffer_hours)
    print(f"EXP-011 freeze: {len(rows)} {TARGET_SPEC_ID} rows over {len(manifest['days'])} days; computing ablated S2 LODO...", file=sys.stderr, flush=True)
    oof = leave_one_day_out_oof(rows)
    threshold_info = compute_threshold(oof)
    print(f"EXP-011 freeze: threshold={threshold_info['threshold']:.6f} n_oof={threshold_info['n_oof']} selected_fraction={threshold_info['selected_fraction']:.4f}", file=sys.stderr, flush=True)

    nested_report: dict[str, Any] | None = None
    if run_nested_lodo:
        print("EXP-011 freeze: running the nested fixed-threshold LODO (report only, 9 outer x 8 inner fits)...", file=sys.stderr, flush=True)
        entries, fold_info = nested_fixed_threshold_lodo(rows)
        nested_report = nested_lodo_report(entries, fold_info)
        print(
            f"EXP-011 freeze: nested LODO n={nested_report['flat']['n']} flat_mean={nested_report['flat']['mean_pct']} press_mean={nested_report['press']['mean_pct']}",
            file=sys.stderr,
            flush=True,
        )

    print("EXP-011 freeze: fitting the frozen model on all 9 days...", file=sys.stderr, flush=True)
    model = fit_frozen_model(rows)
    wall_s = time.time() - t0
    return model, threshold_info, oof, manifest, wall_s, nested_report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="ARTIFACTS/exp011")
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--buffer-hours", type=int, default=2)
    ap.add_argument("--skip-nested-lodo", action="store_true", help="skip the report-only nested fixed-threshold LODO (Part A)")
    args = ap.parse_args()
    assert args.max_workers <= 3, "keep max-workers <= 3 -- two backfill walkers share this box"

    model, threshold_info, oof, manifest, wall_s, nested_report = freeze(
        max_workers=args.max_workers, buffer_hours=args.buffer_hours, run_nested_lodo=not args.skip_nested_lodo
    )
    out = write_outputs(Path(args.out_dir), model, threshold_info, oof, manifest, wall_s, nested_report=nested_report)
    print(f"EXP-011 freeze: wrote {args.out_dir} model_md5={out['model_md5']} wall_s={wall_s:.1f}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
