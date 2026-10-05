#!/usr/bin/env python3
"""EXP-012 operating-point grid under V pricing. **EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE.**

Question: given what the live probe measured (entry lands k = 5-6 slots after migrate, per-side fee 505k
lamports, sell fills -11..-16 bps, entry vs quote +-300 bps), which operating point maximizes expected SOL per
trade and per day after costs? Knobs: entry score threshold, entry latency k, position size, per-side fee, and an
"entry drift" skip rule (skip when the pool price at landing is more than X% above the price at slot+1).

One tape pass, then everything offline.
  1. The pass reuses the latency tool's pool runners (tools.exp012_latency_sensitivity's three-pool layout and
     guarded roots) and the PumpSwap virtual-reserve (V) adapter, so execution is priced on vault + V. For every
     migration whose stored OOF score is >= the lowest threshold (so higher thresholds are subsets) it scores
     the frozen tp50_sl30 exit (tools.exploration_exits via eem.score_one) at every k and every size, and records
     the score, the entry state, the drift, and the exit outcome as (net0, sides, status, p_press). The
     per-side fee does not change the tape walk, only mixed_net, so both fees are computed offline from those
     numbers (the wrapper checks the capture against score_one's own flat leg at the reference fee).
  2. The grid (threshold x k x size x fee x drift rule) is computed from the cached rows, each cell through the
     gate's cluster bootstrap (1,000 draws, seed 1; tools.paper_attention_promote.book_stats), both fail models
     (flat 15%, pressure at slope scale 1).
  3. A NESTED leave-one-day-out estimate of the selection procedure (DEC-017 section 5 style: pick the best cell
     on 8 days, score it on the 9th, pool) is reported next to the in-sample best.
  4. Every cell tried is appended to data/tries.jsonl (role "exploration").

Winner's curse: N cells are tried on the same 9 days the model was frozen on. The best cell is a selection, not
a result. Only the ENTRY is delayed per k; the exit delay stays at the frozen k=1 'start' (live exit lags too).
Selection is the stored OOF score (computed at k=1 timing), not recomputed per k.

Never reads the EXP-012 holdout, the backup block, the EXP-011 block or the forward walk (2026-10-02 onwards):
the roots go through tools.exp012_latency_sensitivity.guarded_roots (forbidden prefixes, realpath, VIEW.sha256).

Full run (see the PR body):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_operating_point --verify-view \
    --out-dir /data/mal/exp012-opoint \
    --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
    --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
    --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import tools.exploration_entry_model as eem
from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp012_exit_sensitivity import side
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, guarded_roots, load_oof, write_rows
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.exploration_entry_model import iter_rows_jsonl
from tools.exploration_exits import build_specs
from tools.latency_curve import FLAT_FAIL, mixed_net

LAMPORTS = 1_000_000_000

# --- the grid (declared before any data is read) ---------------------------------------------------------
FROZEN_THRESHOLD = 0.8030766588450794
THRESHOLDS = (0.70, 0.75, FROZEN_THRESHOLD, 0.85, 0.90)
KS = (4, 5, 6, 8)
REF_K = 1  # slot+1: the drift reference price. Recorded, not a grid cell.
SIZES_SOL = (0.05, 0.1, 0.25, 0.5)
FEES = (155_000, 505_000)  # per-side lamports
DRIFTS = (None, 0.10, 0.20, 0.30)  # skip if landing price > (1 + X) * price at slot+1
REF_FEE = 505_000  # fee used when calling score_one (net0 does not depend on it)
FROZEN_CELL = {"threshold": FROZEN_THRESHOLD, "k": 6, "size_sol": 0.05, "fee": 505_000, "drift": None}
SCRATCH_ROWS = "opoint_rows.jsonl"
TRIES_MARKER = "tries_logged.marker"
TOOL = "tools.exp012_operating_point"
NESTED_MIN_TRAIN_TRADES = 20
ENV_ARTIFACT = "MAL_OPOINT_ARTIFACT"
ENV_TMIN = "MAL_OPOINT_TMIN"
BANNER = "EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE"


def size_lamports(sol: float) -> int:
    return int(round(sol * LAMPORTS))


def cell_id(c: Mapping[str, Any]) -> str:
    d = "none" if c["drift"] is None else f"{int(round(c['drift'] * 100))}pct"
    return f"t{c['threshold']:.4f}_k{c['k']}_s{c['size_sol']}_f{c['fee']}_d{d}"


def all_cells() -> list[dict[str, Any]]:
    return [
        {"threshold": t, "k": k, "size_sol": s, "fee": f, "drift": d}
        for t, k, s, f, d in itertools.product(THRESHOLDS, KS, SIZES_SOL, FEES, DRIFTS)
    ]


# --- tape pass: multi-cell score_one (runs inside the pool workers) ----------------------------------------


@contextlib.contextmanager
def multi_cell_patch(scores: Mapping[str, float], t_min: float, ks: Sequence[int] = (REF_K,) + KS, sizes_sol: Sequence[float] = SIZES_SOL) -> Iterator[None]:
    """Replace eem.score_one so each selected migration (OOF score >= t_min) is scored once per (k, size) with
    the frozen tp50_sl30 exit, and emits one cached row per (k, size). Not-selected migrations emit nothing.
    Restores everything on exit."""
    spec = [s for s in build_specs() if s["id"] == TARGET_SPEC_ID]
    if len(spec) != 1:
        raise SystemExit("tp50_sl30 spec not found")
    orig_score, orig_fills, orig_mixed = eem.score_one, eem._fills_for, eem.mixed_net
    cap: dict[str, Any] = {"fills": None, "slot": None, "calls": []}

    def fills_for(*a: Any, **kw: Any) -> Any:
        res = orig_fills(*a, **kw)
        cap["fills"], cap["slot"] = res[0], res[1]
        return res

    def mixed(net0: int, pri_sides: int, status: int, priority: int, p_fail: float) -> float:
        cap["calls"].append((net0, pri_sides, status, p_fail))
        return orig_mixed(net0, pri_sides, status, priority, p_fail)

    def landing_price(k: int) -> float | None:
        fills, slot = cap["fills"], cap["slot"]
        if not fills or slot is None:
            return None
        idx = eem._state_index(fills, slot + k, eem.ENTRY_BOUND)
        return float(fills[idx].price_sol) if idx >= 0 else None

    def multi(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
        if any(v is not None for v in kw.values()):
            raise RuntimeError(f"score_one was called with arguments this pass does not model: {sorted(kw)}")
        sc = scores.get(mint_id)
        if sc is None or sc < t_min:
            return []
        out: list[dict[str, Any]] = []
        p_ref: float | None = None
        for k in ks:
            p_k: float | None = None
            for sol in sizes_sol:
                cap["calls"] = []
                cap["fills"] = cap["slot"] = None
                rows = orig_score(mint_id, mint, feat, curve, through_ms, creator_hist, specs=spec, size=size_lamports(sol), priority=REF_FEE, entry_land_k=k)
                if cap["fills"] is None:
                    continue
                if p_k is None:
                    p_k = landing_price(k)
                    if k == REF_K:
                        p_ref = p_k
                drift = None if (p_k is None or not p_ref) else p_k / p_ref - 1.0
                day = rows[0]["day"] if rows else None
                if not rows:
                    # exit deadline past the tape end: censored, kept as a marker so n is honest
                    out.append({"mint": mint_id, "spec": TARGET_SPEC_ID, "score": sc, "k": k, "size": size_lamports(sol), "censored": True, "drift": drift, "day": _day_of(mint)})
                    continue
                r = rows[0]
                calls = cap["calls"]
                net0, sides, status, _ = calls[0]
                p_press = calls[1][3] if len(calls) > 1 else 0.0
                if abs(orig_mixed(net0, sides, status, REF_FEE, FLAT_FAIL) - r["flat"]) > 1e-6 or abs(orig_mixed(net0, sides, status, REF_FEE, p_press) - r["press"]) > 1e-6:
                    raise RuntimeError(f"capture mismatch for {mint_id} k={k} size={sol}: {calls} vs flat={r['flat']} press={r['press']}")
                out.append(
                    {"mint": mint_id, "spec": TARGET_SPEC_ID, "day": day, "score": sc, "k": k, "size": size_lamports(sol), "censored": False, "filled": bool(r["filled"]),
                     "status": status, "gross": r["gross"], "net0": net0, "sides": sides, "p_press": p_press, "drift": drift}
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


def cell_trades(idx: Mapping[tuple[int, int], Sequence[Mapping[str, Any]]], c: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    """(trades, n_censored, n_drift_skipped) for one cell. A trade carries flat/press lamports at the cell's fee.
    Drift skips drop the trade entirely (no fee: the entry is never sent); a row with no landing price is not skipped."""
    trades: list[dict[str, Any]] = []
    censored = skipped = 0
    for r in idx.get((int(c["k"]), size_lamports(c["size_sol"])), ()):
        if r["score"] < c["threshold"]:
            continue
        if r.get("censored"):
            censored += 1
            continue
        if c["drift"] is not None and r["drift"] is not None and r["drift"] > c["drift"]:
            skipped += 1
            continue
        flat = mixed_net(r["net0"], r["sides"], r["status"], c["fee"], FLAT_FAIL)
        press = mixed_net(r["net0"], r["sides"], r["status"], c["fee"], r["p_press"])
        trades.append({"mint": r["mint"], "day": r["day"], "filled": r["filled"], "status": r["status"], "gross": r["gross"], "flat": flat, "press": press, "pool": r.get("pool")})
    return trades, censored, skipped


def cell_stats(trades: Sequence[dict[str, Any]], c: Mapping[str, Any], n_days: int, censored: int = 0, skipped: int = 0) -> dict[str, Any]:
    s = side(trades)
    out: dict[str, Any] = {"id": cell_id(c), **{k: c[k] for k in ("threshold", "k", "size_sol", "fee", "drift")}, "n": s["n"], "n_censored": censored, "n_drift_skipped": skipped, "n_days_total": n_days}
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
        tr, cen, skp = cell_trades(idx, c)
        per_cell[cell_id(c)] = tr
        stats.append(cell_stats(tr, c, len(days), cen, skp))
    return stats, per_cell


def nested_lodo(per_cell: Mapping[str, Sequence[Mapping[str, Any]]], order: Sequence[str], days: Sequence[str], objective: str, min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    """Nested leave-one-day-out selection of the CELL. For each held-out day d, choose the cell with the best
    pooled PRESSURE objective over the other days (objective 'per_day': total SOL / training days; 'per_trade':
    mean SOL per trade, needing >= min_train_trades training trades), then score that cell on day d. Held-out
    trades are pooled across days and reported with the gate's bootstrap under both fail models. Ties go to the
    earlier cell in `order`. Same shape as tools.exp012_exit_sensitivity.nested_lodo (DEC-017 section 5)."""
    if objective not in ("per_day", "per_trade"):
        raise ValueError(objective)
    days = sorted(days)
    if len(days) < 2:
        return {"available": False, "reason": "needs at least 2 days"}
    held: list[dict[str, Any]] = []
    choices: list[dict[str, Any]] = []
    for d in days:
        best, best_v = order[0], float("-inf")
        for cid in order:
            train = [t["press"] for t in per_cell[cid] if t["day"] != d]
            if objective == "per_day":
                v = sum(train) / (len(days) - 1)
            else:
                v = (sum(train) / len(train)) if len(train) >= min_train_trades else float("-inf")
            if v > best_v:
                best, best_v = cid, v
        day_trades = [t for t in per_cell[best] if t["day"] == d]
        choices.append({"day": d, "chosen": best, "train_objective_lamports": None if best_v == float("-inf") else best_v, "n_held_out": len(day_trades)})
        held.extend(day_trades)
    s = side(held)
    return {
        "available": True,
        "objective": objective,
        "selection_rule": f"per held-out day: cell with the best pooled pressure {objective} on the other days; scored on the held-out day",
        "min_train_trades": min_train_trades if objective == "per_trade" else None,
        "n_cells": len(order),
        "n_days": len(days),
        "choices_by_day": choices,
        "n_distinct_cells_chosen": len({c["chosen"] for c in choices}),
        "pooled": {"n": s["n"], "trades_per_day": s["n"] / len(days), "flat": s["flat"], "press": s["press"],
                   "press_sol_per_day": (s["press"]["total_sol"] / len(days)) if s["press"] else None,
                   "flat_sol_per_day": (s["flat"]["total_sol"] / len(days)) if s["flat"] else None},
    }


def one_knob_deviations(stats_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The frozen point and every single-knob deviation from it that is on the grid."""
    out = []
    for knob, values in (("threshold", THRESHOLDS), ("k", KS), ("size_sol", SIZES_SOL), ("fee", FEES), ("drift", DRIFTS)):
        for v in values:
            c = dict(FROZEN_CELL, **{knob: v})
            s = stats_by_id.get(cell_id(c))
            if s is not None:
                out.append({"knob": knob, "value": v, "cell": s})
    return out


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


def analyze(rows: Sequence[Mapping[str, Any]], scores: Mapping[str, float], thr_doc: Mapping[str, Any] | None = None, oof_days: Mapping[str, str] | None = None, cells: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    check_integrity(rows, scores, thr_doc, oof_days)
    cells = list(all_cells() if cells is None else cells)
    days = sorted(set(oof_days.values()) if oof_days else {r["day"] for r in rows if r.get("day")})
    stats, per_cell = compute_grid(rows, cells, days)
    order = [cell_id(c) for c in cells]
    by_id = {s["id"]: s for s in stats}
    ref = by_id.get(cell_id(FROZEN_CELL))
    live = [s for s in stats if s["press"] is not None]
    by_day = sorted(live, key=lambda s: -s["press"]["sol_per_day"])
    by_trade = sorted([s for s in live if s["n"] >= NESTED_MIN_TRAIN_TRADES], key=lambda s: -s["press"]["mean_sol"])
    return {
        "schema": "exp012_operating_point_v1",
        "status": BANNER,
        "n_cells_tried": len(cells),
        "n_days": len(days),
        "days": days,
        "grid": {"thresholds": list(THRESHOLDS), "ks": list(KS), "sizes_sol": list(SIZES_SOL), "fees": list(FEES), "drifts": list(DRIFTS)},
        "selection": "OUT-OF-FOLD stored LODO scores; enter iff score >= threshold (scored at k=1 timing, not recomputed per k)",
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats)",
        "caveats": [
            "exploration pool, the same 9 days the model was frozen on: best-of-N, winner's curse applies; not a promote, not gate evidence",
            "only the ENTRY slot is delayed per k; the exit delay stays at the frozen k=1 'start' (a live exit lags too)",
            "per-trade means scale with size; compare sizes by bps of size and by SOL/day",
            "a drift-skipped entry costs nothing (never sent); a missed fill (no state, slippage cap) costs one per-side fee",
            "V-priced (vault + V) execution; a pool with no V is left unchanged and counted, never V=0 silently",
            "exit sell fills are quoted by the frozen exec model; the live probe's -11..-16 bps sell shortfall and +-300 bps entry noise are NOT added",
        ],
        "reference_frozen": {"cell": FROZEN_CELL, "stats": ref},
        "cells": stats,
        "top_by_press_sol_per_day": [s["id"] for s in by_day[:15]],
        "top_by_press_mean_per_trade": [s["id"] for s in by_trade[:15]],
        "best_in_sample_press_sol_per_day": by_day[0] if by_day else None,
        "one_knob_from_frozen": one_knob_deviations(by_id),
        "nested_lodo": {
            "per_day": nested_lodo(per_cell, order, days, "per_day"),
            "per_trade": nested_lodo(per_cell, order, days, "per_trade"),
        },
    }


# --- rendering ---------------------------------------------------------------------------------------------------


def _f(v: float | None, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


def _row(s: Mapping[str, Any], nd: int) -> str:
    def leg(name: str) -> str:
        g = s[name]
        if g is None:
            return "n/a | n/a | n/a | n/a | n/a"
        return f"{_f(g['mean_sol'])} | {_ci(g['ci90_sol'])} | {_f(g['sol_per_day'], 4)} | {_f(g['ex_top3_sol'], 3)} | {g['days_positive']}/{nd}"
    return f"| {s['threshold']:.4f} | {s['k']} | {s['size_sol']} | {s['fee']} | {s['drift']} | {s['n']} | {_f(s['trades_per_day'], 2)} | {leg('flat')} | {leg('press')} |"


HEAD = [
    "| thr | k | size | fee | drift | n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |",
    "| " + " | ".join(["---"] * 17) + " |",
]


def _nested_md(n: Mapping[str, Any]) -> list[str]:
    if not n.get("available"):
        return [f"- unavailable: {n.get('reason')}"]
    p = n["pooled"]
    out = [f"- objective `{n['objective']}`, {n['n_cells']} candidate cells, {n['n_distinct_cells_chosen']} distinct cells chosen over {n['n_days']} folds; held-out n = {p['n']} ({p['trades_per_day']:.2f}/day)"]
    for name in ("flat", "press"):
        g = p[name]
        if g is None:
            out.append(f"  - {name}: no held-out trades")
        else:
            out.append(f"  - {name}: mean {_f(g['mean_sol'])} CI90 {_ci(g['ci90_sol'])}, SOL/day {_f(g['total_sol'] / n['n_days'], 4)}, ex-top3 {_f(g['ex_top3_sol'], 3)}, days+ {g['days_positive']}/{n['n_days']}")
    out.append("  - choices: " + "; ".join(f"{c['day'][5:]}->{c['chosen']}" for c in n["choices_by_day"]))
    return out


def render_md(rep: Mapping[str, Any]) -> str:
    nd = rep["n_days"]
    by_id = {s["id"]: s for s in rep["cells"]}
    lines = [
        "# EXP-012 operating point (V-priced, 9-day exploration pool)",
        "",
        f"**{rep['status']}.** N = {rep['n_cells_tried']} cells tried on {nd} days. Selection: {rep['selection']}. CI: {rep['ci']}.",
        "",
        "Caveats:",
        *[f"- {c}" for c in rep["caveats"]],
        "",
        "## Frozen operating point (reference: threshold 0.803, k=6, 0.05 SOL, fee 505k, no drift skip)",
        "",
        *HEAD,
        *([_row(rep["reference_frozen"]["stats"], nd)] if rep["reference_frozen"]["stats"] else []),
        "",
        f"## Nested leave-one-day-out estimate of the selection procedure (over all {rep['n_cells_tried']} cells)",
        "",
        "Pick the best cell on 8 days, score it on the 9th, pool (DEC-017 section 5 style). This, not the in-sample table below, is the honest estimate of what picking a cell yields.",
        "",
        *_nested_md(rep["nested_lodo"]["per_day"]),
        *_nested_md(rep["nested_lodo"]["per_trade"]),
        "",
        "## One knob at a time from the frozen point",
        "",
        *HEAD,
        *[_row(d["cell"], nd) for d in rep["one_knob_from_frozen"]],
        "",
        f"## In-sample top 15 by pressure SOL/day (best-of-{rep['n_cells_tried']}: optimistic by construction)",
        "",
        *HEAD,
        *[_row(by_id[i], nd) for i in rep["top_by_press_sol_per_day"]],
        "",
        f"## In-sample top 15 by pressure mean SOL/trade, n >= {NESTED_MIN_TRAIN_TRADES} (best-of-{rep['n_cells_tried']}: optimistic by construction)",
        "",
        *HEAD,
        *[_row(by_id[i], nd) for i in rep["top_by_press_mean_per_trade"]],
        "",
    ]
    return "\n".join(lines)


# --- tries log ---------------------------------------------------------------------------------------------------


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 tries line per cell (role exploration). Idempotent per out dir via a marker of cell ids."""
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
                config={"experiment": "EXP-012 operating point", "cell": s["id"], "threshold": s["threshold"], "k": s["k"], "size_sol": s["size_sol"], "fee_lamports": s["fee"],
                        "drift_skip": s["drift"], "reference": s["id"] == cell_id(FROZEN_CELL), "selection": "OOF", "pricing": "V", "n_cells": rep["n_cells_tried"]},
                data_blocks=blocks,
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(s["id"])
    _write_marker(marker, done)
    return n


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
    assert args.max_workers <= 2, "keep max-workers <= 2"
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
