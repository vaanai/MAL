#!/usr/bin/env python3
"""EXP-012 operating point under V pricing, paired against the frozen point. **EXPLORATION, BEST-OF-N, NOT A
PROMOTE, NOT GATE EVIDENCE.**

Question: with the live probe's costs (per-side fee 505k lamports; entry lands about k = 5-6 slots after migrate),
does moving the entry-score threshold or the entry latency k change SOL per trade, relative to the frozen point?

Design (narrow, after the quant-proof review of the first version)
  - Primary grid, size 0.05 SOL, fee 505k: thresholds {0.8031 frozen, 0.82, 0.85} x k {6, 8, 10, 12} = 12 cells.
    Below 0.803 already failed DEC-017 (c) (pressure -0.00312, CI90 [-0.01022, +0.00426]); 0.90 keeps 4 mints.
  - The k here counts from the first PumpSwap print (the latency tool's entry_land_k). The live a25eb17 entries land
    k(migrate) = 5-6; that is notebook evidence from jobs #170/#171, not measured by this tool.
  - Fee 155k is a sensitivity column at the same k (fee and k are coupled live, DEC-019 Am.1 stop rule; the pressure
    curve is calibrated at 500k).
  - Size is NEVER selected across. Sizes 0.1 / 0.25 / 0.5 are a separate sensitivity table at the frozen threshold,
    labelled: missing size-proportional costs (exit lag, sell shortfall, MEV); does not support any live size above
    0.05 SOL.
  - No entry-drift rule (it used the landing state, and turned fee-paid misses into free skips).
  - Nested leave-one-day-out selection is over the THRESHOLD only, at each fixed k, with the frozen threshold among the
    candidates (DEC-017 section 5 approach, tools.exp012_exit_sensitivity.nested_lodo). It reports the PAIRED
    held-out difference against the frozen threshold on the common set of mints (the frozen cell's mints; a
    candidate that does not enter a mint scores 0 on it), CI90 under both fail models, and positive held-out days.
  - OOF caveat: the freeze stored one set of 9-fold LODO scores, no inner OOF scores per training fold, so the
    training days' selection scores come from models that saw the held-out day: the nested number is an UPPER BOUND.

Pass: one tape pass (reusing the latency tool's three-pool layout, guarded roots, the V adapter, eem.score_one with the
frozen tp50_sl30 exit) caches, for each migration with OOF score >= the frozen threshold, per k and size, the exit
outcome (net0, sides, status, p_press). Fees are applied offline with mixed_net. Cells come from the cached rows;
CIs are the gate's cluster bootstrap (1,000 draws, seed 1) under flat 15% and pressure (slope scale 1).

Every computed cell (primary, fee sensitivity, size sensitivity) is appended to the tries log. Only the ENTRY is
delayed per k; the exit delay stays at the frozen k=1 'start'. Never reads the EXP-012 holdout, the backup block, the
EXP-011 block or the forward walk (guarded_roots from tools.exp012_latency_sensitivity).

Full run (see the PR body):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_operating_point --verify-view \
    --out-dir /data/mal/exp012-opoint --tries-log /data/mal/ops/tries-exp012-opoint.jsonl \
    --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
    --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
    --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import tools.exploration_entry_model as eem
from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp012_exit_sensitivity import paired_side, side  # noqa: F401
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, guarded_roots, load_oof, write_rows
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.exploration_entry_model import iter_rows_jsonl
from tools.exploration_exits import build_specs
from tools.latency_curve import FLAT_FAIL, mixed_net

LAMPORTS = 1_000_000_000

# --- the grid (declared before any data is read) ---------------------------------------------------------
FROZEN_THRESHOLD = 0.8030766588450794
THRESHOLDS = (FROZEN_THRESHOLD, 0.82, 0.85)
KS = (6, 8, 10, 12)
PRIMARY_SIZE_SOL = 0.05
SENS_SIZES_SOL = (0.1, 0.25, 0.5)
PRIMARY_FEE = 505_000
SENS_FEE = 155_000
REF_FEE = PRIMARY_FEE  # fee used when calling score_one (net0 does not depend on it)
FROZEN_K = 6
FROZEN_CELL = {"threshold": FROZEN_THRESHOLD, "k": FROZEN_K, "size_sol": PRIMARY_SIZE_SOL, "fee": PRIMARY_FEE}
SCRATCH_ROWS = "opoint_rows.jsonl"
TRIES_MARKER = "tries_logged.marker"
TOOL = "tools.exp012_operating_point"
NESTED_MIN_TRAIN_TRADES = 100
EXISTING_TRIES_ON_POOL = 18  # data/tries.jsonl lines on the 9-day exploration pool before this tool
ENV_ARTIFACT = "MAL_OPOINT_ARTIFACT"
ENV_TMIN = "MAL_OPOINT_TMIN"
BANNER = "EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE"
SIZE_LABEL = "missing size-proportional costs (exit lag, sell shortfall, MEV); does not support any live size above 0.05 SOL"
BELOW_FROZEN_NOTE = "thresholds below 0.803 already failed DEC-017 (c): pressure -0.00312, CI90 [-0.01022, +0.00426]; 0.90 keeps 4 mints"


def size_lamports(sol: float) -> int:
    return int(round(sol * LAMPORTS))


def cell_id(c: Mapping[str, Any]) -> str:
    return f"t{c['threshold']:.4f}_k{c['k']}_s{c['size_sol']}_f{c['fee']}"


def primary_cells() -> list[dict[str, Any]]:
    return [{"threshold": t, "k": k, "size_sol": PRIMARY_SIZE_SOL, "fee": PRIMARY_FEE} for k in KS for t in THRESHOLDS]


def fee_sens_cells() -> list[dict[str, Any]]:
    return [dict(c, fee=SENS_FEE) for c in primary_cells()]


def size_sens_cells() -> list[dict[str, Any]]:
    return [{"threshold": FROZEN_THRESHOLD, "k": k, "size_sol": s, "fee": PRIMARY_FEE} for k in KS for s in SENS_SIZES_SOL]


def all_cells() -> list[dict[str, Any]]:
    """Every cell computed (and logged): 12 primary + 12 fee sensitivity + 12 size sensitivity."""
    return primary_cells() + fee_sens_cells() + size_sens_cells()


def cell_axis(c: Mapping[str, Any]) -> str:
    if c["size_sol"] != PRIMARY_SIZE_SOL:
        return "size_sensitivity"
    return "primary" if c["fee"] == PRIMARY_FEE else "fee_sensitivity"


SIZES_SOL = (PRIMARY_SIZE_SOL,) + SENS_SIZES_SOL


# --- tape pass: multi-cell score_one (runs inside the pool workers) ----------------------------------------


@contextlib.contextmanager
def multi_cell_patch(scores: Mapping[str, float], t_min: float, ks: Sequence[int] = KS, sizes_sol: Sequence[float] = SIZES_SOL, combos: Sequence[tuple[int, float, int]] | None = None) -> Iterator[None]:
    """`combos` (optional, used by tools.exp012_backcheck): explicit (k, size_sol, exit_lag) triples replacing the
    ks x sizes_sol product; exit_lag > 0 passes exit_land_k to score_one. Default None = unchanged behaviour.
    Replace eem.score_one so each selected migration (OOF score >= t_min) is scored once per (k, size) with
    the frozen tp50_sl30 exit, and emits one cached row per (k, size). Not-selected migrations emit nothing.
    Restores everything on exit."""
    spec = [s for s in build_specs() if s["id"] == TARGET_SPEC_ID]
    if len(spec) != 1:
        raise SystemExit("tp50_sl30 spec not found")
    orig_score, orig_fills, orig_mixed = eem.score_one, eem._fills_for, eem.mixed_net
    cap: dict[str, Any] = {"fills": None, "calls": []}

    def fills_for(*a: Any, **kw: Any) -> Any:
        res = orig_fills(*a, **kw)
        cap["fills"] = res[0]
        return res

    def mixed(net0: int, pri_sides: int, status: int, priority: int, p_fail: float) -> float:
        cap["calls"].append((net0, pri_sides, status, p_fail))
        return orig_mixed(net0, pri_sides, status, priority, p_fail)

    def multi(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
        if any(v is not None for v in kw.values()):
            raise RuntimeError(f"score_one was called with arguments this pass does not model: {sorted(kw)}")
        sc = scores.get(mint_id)
        if sc is None or sc < t_min:
            return []
        out: list[dict[str, Any]] = []
        todo = list(combos) if combos is not None else [(k, sol, 0) for k in ks for sol in sizes_sol]
        for k, sol, lag in todo:
            cap["calls"] = []
            cap["fills"] = None
            extra = {"exit_land_k": lag} if lag else {}
            rows = orig_score(mint_id, mint, feat, curve, through_ms, creator_hist, specs=spec, size=size_lamports(sol), priority=REF_FEE, entry_land_k=k, **extra)
            if cap["fills"] is None:
                continue
            if not rows:
                # exit deadline past the tape end: censored, kept as a marker so n is honest
                out.append({"mint": mint_id, "spec": TARGET_SPEC_ID, "score": sc, "k": k, "size": size_lamports(sol), "censored": True, "day": _day_of(mint), **({"exit_lag": lag} if lag else {})})
                continue
            r = rows[0]
            calls = cap["calls"]
            net0, sides, status, _ = calls[0]
            p_press = calls[1][3] if len(calls) > 1 else 0.0
            if abs(orig_mixed(net0, sides, status, REF_FEE, FLAT_FAIL) - r["flat"]) > 1e-6 or abs(orig_mixed(net0, sides, status, REF_FEE, p_press) - r["press"]) > 1e-6:
                raise RuntimeError(f"capture mismatch for {mint_id} k={k} size={sol}: {calls} vs flat={r['flat']} press={r['press']}")
            out.append(
                {"mint": mint_id, "spec": TARGET_SPEC_ID, "day": r["day"], "score": sc, "k": k, "size": size_lamports(sol), "censored": False, "filled": bool(r["filled"]),
                 "status": status, "gross": r["gross"], "net0": net0, "sides": sides, "p_press": p_press, **({"exit": r["exit"]} if "exit" in r else {}), **({"exit_lag": lag} if lag else {})}
            )
        return out

    eem.score_one, eem._fills_for, eem.mixed_net = multi, fills_for, mixed
    try:
        yield
    finally:
        eem.score_one, eem._fills_for, eem.mixed_net = orig_score, orig_fills, orig_mixed


def _day_of(mint: Any) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(mint.mig_ms / 1000.0))


def _worker(which: str, *args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    scores, _thr, _doc, _days = load_oof(Path(os.environ[ENV_ARTIFACT]))
    with multi_cell_patch(scores, float(os.environ[ENV_TMIN])):
        return getattr(ad, f"worker_{which}")(*args, **kw)


# Module-level so a spawn pool can pickle them by reference.
def op_worker_a(*args: Any, **kw: Any) -> Any:
    return _worker("a", *args, **kw)


def op_worker_b(*args: Any, **kw: Any) -> Any:
    return _worker("b", *args, **kw)


def op_worker_c(*args: Any, **kw: Any) -> Any:
    return _worker("c", *args, **kw)


@contextlib.contextmanager
def patched_op_workers() -> Iterator[None]:
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
    eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = op_worker_a, op_worker_b, op_worker_c
    try:
        yield
    finally:
        eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = saved


def collect_rows(fast: Path, insample: Path, live: Path, scratch: Path, artifact_dir: Path, vmap: str, t_min: float, max_workers: int = 2, buffer_hours: int = 24, max_home_hours: int | None = 12) -> list[dict[str, Any]]:
    from tools.exploration_entry_model_b2 import run_all_features_b
    from tools.exploration_entry_model_b3 import run_all_features_c

    if max_workers > 2:
        raise SystemExit("keep max-workers <= 2")
    set_env(vmap, scratch / "counts_virtual")
    os.environ[ENV_ARTIFACT], os.environ[ENV_TMIN] = str(artifact_dir), repr(t_min)
    common = dict(max_workers=max_workers, buffer_hours=buffer_hours, max_home_hours=max_home_hours)
    passes = (
        ("A", lambda: eem.run_all_features(out_dir=scratch / "poolA", backfill=fast, **common)),
        ("C", lambda: run_all_features_c(out_dir=scratch / "poolC", root=insample, **common)),
        ("B", lambda: run_all_features_b(out_dir=scratch / "poolB", root=live, **common)),
    )
    out: list[dict[str, Any]] = []
    with patched_op_workers():
        for pool, fn in passes:
            print(f"operating-point pass: pool {pool}...", file=sys.stderr, flush=True)
            for r in fn():
                if r.get("spec") == TARGET_SPEC_ID:
                    r["pool"] = pool
                    out.append(r)
    out.sort(key=lambda r: (r["k"], r["size"], r["day"] or "", r["mint"]))
    return out


# --- analysis (pure; tested on fixtures) ----------------------------------------------------------------------


def index_rows(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[int, int], list[Mapping[str, Any]]]:
    idx: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    seen: set[tuple[str, int, int]] = set()
    for r in rows:
        key = (r["mint"], int(r["k"]), int(r["size"]))
        if key in seen:
            raise SystemExit(f"integrity: duplicate row {key}")
        seen.add(key)
        idx.setdefault((int(r["k"]), int(r["size"])), []).append(r)
    return idx


def cell_trades(idx: Mapping[tuple[int, int], Sequence[Mapping[str, Any]]], c: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """(trades, n_censored) for one cell, trades at the cell's fee. A cell is one (k, size): rows of other sizes
    are never touched."""
    trades: list[dict[str, Any]] = []
    censored = 0
    for r in idx.get((int(c["k"]), size_lamports(c["size_sol"])), ()):
        if r["score"] < c["threshold"]:
            continue
        if r.get("censored"):
            censored += 1
            continue
        flat = mixed_net(r["net0"], r["sides"], r["status"], c["fee"], FLAT_FAIL)
        press = mixed_net(r["net0"], r["sides"], r["status"], c["fee"], r["p_press"])
        trades.append({"mint": r["mint"], "day": r["day"], "filled": r["filled"], "status": r["status"], "gross": r["gross"], "flat": flat, "press": press, "pool": r.get("pool")})
    return trades, censored


def cell_stats(trades: Sequence[dict[str, Any]], c: Mapping[str, Any], n_days: int, censored: int = 0) -> dict[str, Any]:
    s = side(trades)
    out: dict[str, Any] = {"id": cell_id(c), "axis": cell_axis(c), **{k: c[k] for k in ("threshold", "k", "size_sol", "fee")}, "n": s["n"], "n_censored": censored, "n_days_total": n_days}
    out["trades_per_day"] = s["n"] / n_days if n_days else None
    out["fill_rate"] = s["fill_rate"]
    for name in ("flat", "press"):
        leg = s[name]
        if leg is None:
            out[name] = None
            continue
        out[name] = {
            "mean_sol": leg["mean_sol"],
            "mean_bps_of_size": leg["mean_sol"] / c["size_sol"] * 1e4,
            "ci90_sol": leg["ci90_sol"],
            "sol_per_day": leg["total_sol"] / n_days,
            "total_sol": leg["total_sol"],
            "ex_top3_sol": leg["ex_top3_sol"],
            "days_positive": leg["days_positive"],
            "days_with_trades": leg["n_days"],
        }
    return out


def compute_grid(rows: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]], days: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    idx = index_rows(rows)
    stats, per_cell = [], {}
    for c in cells:
        tr, cen = cell_trades(idx, c)
        per_cell[cell_id(c)] = tr
        stats.append(cell_stats(tr, c, len(days), cen))
    return stats, per_cell


def paired_vs_reference(ref_trades: Sequence[Mapping[str, Any]], cand_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-mint paired difference (candidate - reference, lamports) on the COMMON set of mints = the reference's
    mints. A mint the candidate does not enter contributes 0 for the candidate, so dropping a trade is charged
    what it would have earned."""
    cand = {t["mint"]: t for t in cand_trades}
    out = []
    for t in ref_trades:
        c = cand.get(t["mint"])
        out.append({"mint": t["mint"], "day": t["day"], "pool": t.get("pool"), "dflat": (c["flat"] if c else 0.0) - t["flat"], "dpress": (c["press"] if c else 0.0) - t["press"], "entered": c is not None})
    return out


def nested_threshold_lodo(per_cell: Mapping[str, Sequence[Mapping[str, Any]]], k: int, thresholds: Sequence[float], days: Sequence[str], size_sol: float = PRIMARY_SIZE_SOL, fee: int = PRIMARY_FEE, min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    """Nested leave-one-day-out selection of the THRESHOLD at one fixed (k, size, fee). Candidates = `thresholds`
    with thresholds[0] the frozen reference (its paired difference is 0, DEC-017 section 5). For each held-out day:
    pick the candidate with the best pooled PRESSURE paired mean (vs the reference, common mints) over the other days,
    among candidates entering >= min_train_trades training trades. If none qualifies the fold is UNAVAILABLE (no
    fallback to the reference); its day is excluded from the held-out pool. Held-out paired differences are pooled
    with the gate's bootstrap under both fail models. Ties go to the earlier candidate."""
    ids = [cell_id({"threshold": t, "k": k, "size_sol": size_sol, "fee": fee}) for t in thresholds]
    ref_trades = per_cell[ids[0]]
    diffs = {i: paired_vs_reference(ref_trades, per_cell[i]) for i in ids}
    days = sorted(days)
    if len(days) < 2:
        return {"available": False, "reason": "needs at least 2 days", "k": k}
    held: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for d in days:
        best, best_v = None, float("-inf")
        for i in ids:
            train = [r for r in diffs[i] if r["day"] != d]
            n_train_entered = sum(1 for r in train if r["entered"])
            if n_train_entered < min_train_trades or not train:
                continue
            v = sum(r["dpress"] for r in train) / len(train)
            if v > best_v:
                best, best_v = i, v
        if best is None:
            folds.append({"day": d, "chosen": None, "available": False, "n_held_out": 0})
            continue
        day_rows = [dict(r, chosen=best) for r in diffs[best] if r["day"] == d]
        held.extend(day_rows)
        folds.append({"day": d, "chosen": best, "available": True, "n_held_out": len(day_rows)})
    n_unavail = sum(1 for f in folds if not f["available"])
    # in-sample best threshold: the candidate with the best paired pressure mean over ALL days (what picking on these 9 days gives)
    insample = {i: (sum(r["dpress"] for r in diffs[i]) / len(diffs[i]) if diffs[i] else float("-inf")) for i in ids}
    best_in = max(ids, key=lambda i: (insample[i], -ids.index(i)))
    pooled = paired_side(held)
    return {
        "available": n_unavail < len(days),
        "k": k,
        "size_sol": size_sol,
        "fee": fee,
        "candidates": ids,
        "reference": ids[0],
        "min_train_trades": min_train_trades,
        "n_days": len(days),
        "n_unavailable_folds": n_unavail,
        "folds": folds,
        "times_chosen": {i: sum(1 for f in folds if f["chosen"] == i) for i in ids},
        "common_set_size": len(ref_trades),
        "heldout_paired_vs_frozen": pooled,
        "insample_best": best_in,
        "insample_best_paired_press_mean_sol": None if insample[best_in] == float("-inf") else insample[best_in] / LAMPORTS,
        "insample_paired_press_mean_by_candidate_sol": {i: (None if v == float("-inf") else v / LAMPORTS) for i, v in insample.items()},
        "optimism_gap_press_sol": _gap(insample[best_in], pooled),
    }


def _gap(insample_best: float, pooled: Mapping[str, Any]) -> float | None:
    """In-sample best paired pressure mean minus the nested held-out paired pressure mean (SOL per mint on the common set)."""
    if insample_best == float("-inf") or not pooled.get("press"):
        return None
    return insample_best / LAMPORTS - pooled["press"]["mean_sol"]


def check_integrity(rows: Sequence[Mapping[str, Any]], scores: Mapping[str, float], thr_doc: Mapping[str, Any] | None, oof_days: Mapping[str, str] | None) -> None:
    """Every row's score is the stored OOF score and its day the OOF day; the distinct mints at or above the
    frozen threshold equal the freeze's n_selected_at_or_above_threshold."""
    sel: set[str] = set()
    for r in rows:
        m = r["mint"]
        if m not in scores or abs(scores[m] - r["score"]) > 1e-12:
            raise SystemExit(f"integrity: row score for {m} is not the stored OOF score")
        if oof_days is not None and r.get("day") is not None and oof_days.get(m) != r["day"]:
            raise SystemExit(f"integrity: mint {m} day {r['day']} != OOF day {oof_days.get(m)}")
        if r["score"] >= FROZEN_THRESHOLD:
            sel.add(m)
    if thr_doc is not None and len(sel) != int(thr_doc["n_selected_at_or_above_threshold"]):
        raise SystemExit(f"integrity: {len(sel)} mints at/above the frozen threshold in the rows, threshold file says {thr_doc['n_selected_at_or_above_threshold']}")


def analyze(rows: Sequence[Mapping[str, Any]], scores: Mapping[str, float], thr_doc: Mapping[str, Any] | None = None, oof_days: Mapping[str, str] | None = None, cells: Sequence[Mapping[str, Any]] | None = None, min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    check_integrity(rows, scores, thr_doc, oof_days)
    cells = list(all_cells() if cells is None else cells)
    days = sorted(set(oof_days.values()) if oof_days else {r["day"] for r in rows if r.get("day")})
    stats, per_cell = compute_grid(rows, cells, days)
    by_id = {s["id"]: s for s in stats}
    ref = by_id.get(cell_id(FROZEN_CELL))
    n_primary = sum(1 for c in cells if cell_axis(c) == "primary")
    nested = {}
    for k in sorted({c["k"] for c in cells if cell_axis(c) == "primary"}):
        ths = [t for t in THRESHOLDS if cell_id({"threshold": t, "k": k, "size_sol": PRIMARY_SIZE_SOL, "fee": PRIMARY_FEE}) in per_cell]
        if ths and ths[0] == FROZEN_THRESHOLD and len(ths) > 1:
            nested[str(k)] = nested_threshold_lodo(per_cell, k, ths, days, min_train_trades=min_train_trades)
    return {
        "schema": "exp012_operating_point_v2",
        "status": BANNER,
        "n_cells_tried": len(cells),
        "n_primary_cells": n_primary,
        "cumulative_tries_on_pool": EXISTING_TRIES_ON_POOL + len(cells),
        "existing_tries_on_pool": EXISTING_TRIES_ON_POOL,
        "n_days": len(days),
        "days": days,
        "grid": {"thresholds": list(THRESHOLDS), "ks": list(KS), "primary_size_sol": PRIMARY_SIZE_SOL, "primary_fee": PRIMARY_FEE, "sens_fee": SENS_FEE, "sens_sizes_sol": list(SENS_SIZES_SOL)},
        "selection": "OUT-OF-FOLD stored LODO scores; enter iff score >= threshold (scored at k=1 timing, not recomputed per k)",
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats)",
        "notes": {"below_frozen": BELOW_FROZEN_NOTE, "k_definition": "k counts from the first PumpSwap print (entry_land_k); live a25eb17 entries land k(migrate) = 5-6, notebook evidence from jobs #170/#171, not measured here",
                  "fee_coupling": "fee 155k is a sensitivity at the same k; fee and k are coupled live (DEC-019 Am.1 stop rule) and the pressure curve is calibrated at 500k", "size_label": SIZE_LABEL,
                  "oof_upper_bound": "no inner OOF scores are stored by the freeze; training-day selection used scores from models that saw the held-out day, so the nested number is an UPPER BOUND"},
        "caveats": [
            "exploration pool, the same 9 days the model was frozen on: best-of-N, winner's curse applies; not a promote, not gate evidence",
            "only the ENTRY slot is delayed per k; the exit delay stays at the frozen k=1 'start' (a live exit lags too)",
            "a missed fill (no state, slippage cap) costs one per-side fee and stays in n",
            "V-priced (vault + V) execution; a pool with no V is left unchanged and counted, never V=0 silently",
            "exit sell fills are quoted by the frozen exec model; the live probe's -11..-16 bps sell shortfall and +-300 bps entry noise are NOT added",
        ],
        "reference_frozen": {"cell": FROZEN_CELL, "stats": ref},
        "cells": stats,
        "nested_threshold_lodo_by_k": nested,
    }


# --- rendering ---------------------------------------------------------------------------------------------------


def _f(v: float | None, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


HEAD = [
    "| thr | k | size | fee | n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |",
    "| " + " | ".join(["---"] * 16) + " |",
]


def _row(s: Mapping[str, Any], nd: int) -> str:
    def leg(name: str) -> str:
        g = s[name]
        if g is None:
            return "n/a | n/a | n/a | n/a | n/a"
        return f"{_f(g['mean_sol'])} | {_ci(g['ci90_sol'])} | {_f(g['sol_per_day'], 4)} | {_f(g['ex_top3_sol'], 3)} | {g['days_positive']}/{nd}"

    return f"| {s['threshold']:.4f} | {s['k']} | {s['size_sol']} | {s['fee']} | {s['n']} | {_f(s['trades_per_day'], 2)} | {leg('flat')} | {leg('press')} |"


def _paired_leg_md(name: str, g: Mapping[str, Any] | None) -> str:
    if not g:
        return f"  - {name}: no held-out rows"
    return f"  - {name}: paired mean {_f(g['mean_sol'])} SOL/mint, CI90 {_ci(g['ci90_sol'])}, held-out days positive {g['days_positive']}/{g['n_days']}"


def render_md(rep: Mapping[str, Any]) -> str:
    nd = rep["n_days"]
    by = {s["id"]: s for s in rep["cells"]}
    g = rep["grid"]
    primary = [s for s in rep["cells"] if s["axis"] == "primary"]
    lines = [
        "# EXP-012 operating point (V-priced, 9-day exploration pool), paired against the frozen point",
        "",
        f"**{rep['status']}.** N = {rep['n_cells_tried']} cells computed and logged ({rep['n_primary_cells']} primary, rest sensitivity); "
        f"cumulative tries on these {nd} days: {rep['existing_tries_on_pool']} existing + {rep['n_cells_tried']} new = {rep['cumulative_tries_on_pool']}. CI: {rep['ci']}.",
        "",
        f"- Thresholds: {BELOW_FROZEN_NOTE}.",
        f"- {rep['notes']['k_definition']}.",
        f"- {rep['notes']['fee_coupling']}.",
        f"- {rep['notes']['oof_upper_bound']}.",
        "",
        "Caveats:",
        *[f"- {c}" for c in rep["caveats"]],
        "",
        "## Frozen operating point (reference: threshold 0.8031, k=6, 0.05 SOL, fee 505k)",
        "",
        *HEAD,
        *([_row(rep["reference_frozen"]["stats"], nd)] if rep["reference_frozen"]["stats"] else []),
        "",
        "## Primary grid (size 0.05 SOL, fee 505k)",
        "",
        *HEAD,
        *[_row(s, nd) for s in primary],
        "",
        "## Nested leave-one-day-out over the THRESHOLD, per fixed k, paired vs the frozen threshold",
        "",
        f"Candidates {g['thresholds']} (frozen first = reference, paired difference 0), size {g['primary_size_sol']}, fee {g['primary_fee']}. Pick on 8 days by pooled pressure paired mean, score on the 9th; "
        f"training floor {NESTED_MIN_TRAIN_TRADES} entered trades, a fold with no qualifying candidate is UNAVAILABLE (not defaulted). Common set = the frozen threshold's mints at that k; "
        "a candidate that skips a mint scores 0 on it. Upper bound (see above).",
        "",
    ]
    for k, n in rep["nested_threshold_lodo_by_k"].items():
        ref = by.get(n["reference"])
        lines.append(f"### k = {k}")
        if ref and ref["press"]:
            lines.append(f"- frozen-threshold level: n {ref['n']}, flat mean {_f(ref['flat']['mean_sol'])} {_ci(ref['flat']['ci90_sol'])}, pressure mean {_f(ref['press']['mean_sol'])} {_ci(ref['press']['ci90_sol'])}")
        if not n.get("available"):
            lines.append(f"- nested: unavailable ({n.get('reason', 'no fold met the training floor')})")
            continue
        p = n["heldout_paired_vs_frozen"]
        lines.append(f"- nested threshold choice: held-out n {p['n']}, unavailable folds {n['n_unavailable_folds']}/{n['n_days']}, times chosen {n['times_chosen']}")
        lines.append(_paired_leg_md("flat", p.get("flat")))
        lines.append(_paired_leg_md("pressure", p.get("press")))
        lines.append(f"- optimism gap (in-sample best `{n['insample_best']}` paired pressure mean {_f(n['insample_best_paired_press_mean_sol'])} minus nested held-out {_f(p['press']['mean_sol'] if p.get('press') else None)}): {_f(n['optimism_gap_press_sol'])} SOL/mint")
        lines.append("")
    fee_s = [s for s in rep["cells"] if s["axis"] == "fee_sensitivity"]
    size_s = [s for s in rep["cells"] if s["axis"] == "size_sensitivity"]
    lines += [
        f"## Fee sensitivity: {SENS_FEE} per side, same cells (not a recommendation; fee and k are coupled live)",
        "",
        *HEAD,
        *[_row(s, nd) for s in fee_s],
        "",
        f"## Size sensitivity at the frozen threshold, fee {PRIMARY_FEE} (NOT a selection)",
        "",
        f"**{SIZE_LABEL}.**",
        "",
        *HEAD,
        *[_row(s, nd) for s in size_s],
        "",
    ]
    return "\n".join(lines)


# --- tries log ---------------------------------------------------------------------------------------------------


def _already_in_log_op(log: Path, cell: str, result_path: Path) -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("tool") == TOOL and rec.get("config", {}).get("cell") == cell and rec.get("result_path") == str(result_path):
            return True
    return False


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 tries line per cell computed (role exploration). Idempotent per out dir via a marker and a
    scan of the log for (tool, cell, result_path), so lines from an earlier attempt on the same out dir are not doubled."""
    from tools import mal_result
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker
    from tools.exp012_support import exploration_pool_blocks

    marker = out_dir / TRIES_MARKER
    done = _read_marker(marker)
    if "*" in done:
        return 0
    result_path = out_dir / "operating_point.json"
    blocks = exploration_pool_blocks()
    n = 0
    for s in rep["cells"]:
        if s["id"] in done:
            continue
        if not _already_in_log_op(Path(tries_log), s["id"], result_path):
            mal_result.append_try(
                tries_log,
                tool=TOOL,
                config={"experiment": "EXP-012 operating point", "cell": s["id"], "axis": s["axis"], "threshold": s["threshold"], "k": s["k"], "size_sol": s["size_sol"], "fee_lamports": s["fee"],
                        "reference": s["id"] == cell_id(FROZEN_CELL), "selection": "OOF", "pricing": "V", "n_cells": rep["n_cells_tried"]},
                data_blocks=blocks,
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(s["id"])
    _write_marker(marker, done)
    return n


# --- CLI ---------------------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_root_args(ap)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--vmap", default=DEFAULT_VMAP)
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--buffer-hours", type=int, default=24)
    ap.add_argument("--max-home-hours", type=int, default=12)
    ap.add_argument("--tries-log", default=None, help="absolute tries-log path (default: MAL_TRIES_LOG, else data/tries.jsonl)")
    ap.add_argument("--reuse-rows", action="store_true", help=f"skip the tape pass and analyze OUT_DIR/{SCRATCH_ROWS} if it exists")
    args = ap.parse_args(argv)
    if args.max_workers > 2:
        raise SystemExit("keep --max-workers <= 2")
    tries_path = resolve_tries_path(args.tries_log)
    scores, threshold, thr_doc, oof_days = load_oof(args.artifact_dir)
    if abs(threshold - FROZEN_THRESHOLD) > 1e-15:
        raise SystemExit(f"threshold.json threshold {threshold!r} != the frozen {FROZEN_THRESHOLD!r}")
    rows_path = args.out_dir / SCRATCH_ROWS
    t0 = time.time()
    if args.reuse_rows and rows_path.is_file():
        rows = list(iter_rows_jsonl(rows_path))
    else:
        roots = guarded_roots(args)
        rows = collect_rows(roots["fast"], roots["insample"], roots["live"], args.out_dir / "scratch", args.artifact_dir, args.vmap, min(THRESHOLDS),
                            max_workers=args.max_workers, buffer_hours=args.buffer_hours, max_home_hours=(args.max_home_hours or None))
        write_rows(rows_path, rows)
    rep = analyze(rows, scores, thr_doc, oof_days)
    rep["wall_s"] = time.time() - t0
    rep["tries"] = {"logged": log_tries(rep, args.out_dir, tries_path), "log_path": str(tries_path)}
    (args.out_dir / "operating_point.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "operating_point.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
