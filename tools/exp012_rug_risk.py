#!/usr/bin/env python3
"""EXP-012 rug-risk exploration: can information known AT DECISION TIME (bonding-curve history up to the migration)
predict a large one-step dump in the first ~15 minutes after entry? **EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE
EVIDENCE.**

Motivation: the biggest single live loss (C71Lk8Ko) went from above -30% to -69% between two 400 ms polls, then sold
at -0.8957. Hypothesis: rugs come from concentrated holders.

Frozen point: OOF threshold 0.8030766588450794, k = 6, 0.05 SOL, per-side fee 505,000 lamports, tp50_sl30, V pricing
(the 881 OOF-selected migrations). Only the 9-day exploration pool is read, through the same guarded roots as
tools.exp012_operating_point. Never the EXP-012 holdout, the backup block, the EXP-011 block or 2026-10-02 onwards.

PRE-DECLARED FEATURES (6, written before any data was read). All come from pump_bonding trade events with receive
time strictly before the migration trigger (tools.exploration_entry_model.causal_events). A PumpSwap print never
enters them. Supply = 1e9 tokens = 1e15 raw units. Balances are buys minus sells by trader on the bonding curve.
  creator_share        creator's net token balance / supply (creator wallet = the create row's creator), floored at 0
  top1_share           largest non-creator net balance / supply
  top3_share           sum of the three largest non-creator net balances / supply
  n_buyers             distinct buyer wallets
  top_wallet_vol_share largest single wallet's bonding buy SOL / total bonding buy SOL
  creator_prior_mints_24h  creator's mints in the trailing 24h in this pool (existing G1 feature). This is the only
                       creator-history proxy available without leakage; a creator PRIOR RUG COUNT is NOT computed
                       (no outcome labels are knowable at decision time on this pool without leakage).

PRE-DECLARED RULES (6, thresholds fixed before any data was read, frozen always a candidate; a rule vetoes the entry):
  creator_share_ge_5pct   creator_share >= 0.05
  top1_share_ge_10pct     top1_share >= 0.10
  top3_share_ge_25pct     top3_share >= 0.25
  n_buyers_lt_20          n_buyers < 20
  top_wallet_vol_ge_30pct top_wallet_vol_share >= 0.30
  creator_prior_ge_2      creator_prior_mints_24h >= 2

RUG LABEL (an outcome, never a feature). From the entry state's V-priced price P0 (the state the buy fills against,
k = 6) over PumpSwap prints landing_ms < t <= landing_ms + 15 min:
  one_step  a SELL print whose price is <= -30% of the immediately preceding print's price (the first preceding is P0)
  crash50   a print priced below 0.5 * P0 before any print >= 1.5 * P0 (the tp)
  rug = one_step or crash50. A migration with no entry state has no label (None).

MISSES. The simulator's entry can MISS (no state, or _try_buy SLIPPAGE_CAP). A rule that would veto a MISS does NOT
gain: the miss keeps its fee in BOTH arms (difference 0). Only vetoes of FILLED trades change the score, and a vetoed
filled trade is charged what it would have earned. The paired scoring runs over FILLED frozen trades only; misses are
counted separately.

Selection: nested leave-one-day-out over the rule family (pick on 8 days by pooled pressure paired mean over FILLED
trades, training floor 100 entered filled trades, frozen first and wins ties, unavailable folds not defaulted).
UPPER BOUND: the stored OOF scores saw the other days. CIs: gate cluster bootstrap (1,000 draws, seed 1).

Run:
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_rug_risk --verify-view \
    --out-dir /data/mal/exp012-rug --tries-log /data/mal/ops/tries-exp012-rug.jsonl \
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
from typing import Any, Callable, Iterator, Mapping, Sequence

import tools.exploration_entry_model as eem
import tools.exp012_operating_point as op
from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp012_exit_sensitivity import paired_side
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, guarded_roots, load_oof, write_rows
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.exploration_entry_model import iter_rows_jsonl

LAMPORTS = 1_000_000_000
SUPPLY_RAW = 1_000_000_000_000_000
FROZEN_THRESHOLD = op.FROZEN_THRESHOLD
K = op.FROZEN_K
SIZE_SOL = op.PRIMARY_SIZE_SOL
FEE = op.PRIMARY_FEE
FROZEN_CELL = op.FROZEN_CELL
WINDOW_MS = 15 * 60 * 1000
ONE_STEP_DROP = -0.30
CRASH_FRAC = 0.5
TP_MULT = 1.5
NESTED_MIN_TRAIN_TRADES = 100
EXISTING_TRIES_ON_POOL = 68  # 54 in data/tries.jsonl + 14 from the exit and veto studies
N_BINS = 5
SCRATCH_ROWS = "rug_rows.jsonl"
TRIES_MARKER = "tries_logged.marker"
TOOL = "tools.exp012_rug_risk"
BANNER = "EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE"
ENV_ARTIFACT = "MAL_RUG_ARTIFACT"
ENV_TMIN = "MAL_RUG_TMIN"
FROZEN_ID = "frozen"
FEATURES = ("creator_share", "top1_share", "top3_share", "n_buyers", "top_wallet_vol_share", "creator_prior_mints_24h")

RULES: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "creator_share_ge_5pct": lambda f: f["creator_share"] >= 0.05,
    "top1_share_ge_10pct": lambda f: f["top1_share"] >= 0.10,
    "top3_share_ge_25pct": lambda f: f["top3_share"] >= 0.25,
    "n_buyers_lt_20": lambda f: f["n_buyers"] < 20,
    "top_wallet_vol_ge_30pct": lambda f: f["top_wallet_vol_share"] >= 0.30,
    "creator_prior_ge_2": lambda f: f["creator_prior_mints_24h"] >= 2,
}
RULE_IDS = tuple(RULES)


def vetoed(rule: str, feats: Mapping[str, Any] | None) -> bool:
    """A mint with no feature record is never vetoed (no information)."""
    return bool(feats) and RULES[rule](feats)


# --- decision-time features (pure) -----------------------------------------------------------------------------


def rug_features(events: Sequence[tuple], creator: str | None, mig_ms: int, creator_prior_mints_24h: int = 0) -> dict[str, float]:
    """Only events with receive time strictly before mig_ms are read (eem.causal_events), whatever the caller passes.
    Event = (t_ms, side, trader_or_None, sol_lamports, token_raw, price_or_None)."""
    bal: dict[str, int] = {}
    buy_sol: dict[str, int] = {}
    total_buy = 0
    for _t, side, trader, sol, tok, _p in eem.causal_events(events, mig_ms):
        if side == "buy":
            total_buy += sol
            if trader:
                bal[trader] = bal.get(trader, 0) + tok
                buy_sol[trader] = buy_sol.get(trader, 0) + sol
        elif side == "sell" and trader:
            bal[trader] = bal.get(trader, 0) - tok
    others = sorted((v for w, v in bal.items() if w != creator and v > 0), reverse=True)
    return {
        "creator_share": max(bal.get(creator, 0), 0) / SUPPLY_RAW if creator else 0.0,
        "top1_share": (others[0] / SUPPLY_RAW) if others else 0.0,
        "top3_share": sum(others[:3]) / SUPPLY_RAW,
        "n_buyers": float(len(buy_sol)),
        "top_wallet_vol_share": (max(buy_sol.values()) / total_buy) if buy_sol and total_buy > 0 else 0.0,
        "creator_prior_mints_24h": float(creator_prior_mints_24h),
    }


# --- outcome label (pure; never a feature) -----------------------------------------------------------------------


def rug_label(fills: Sequence[Any], idx: int, landing_ms: int, window_ms: int = WINDOW_MS) -> dict[str, Any] | None:
    """None when there is no entry state (idx < 0). Reads PumpSwap prints after the entry state within the window."""
    if idx < 0:
        return None
    p0 = fills[idx].price_sol
    prev = p0
    one_step = crash = tp_seen = False
    n_after = 0
    worst_step = 0.0
    for p in fills[idx + 1 :]:
        if p.venue != "pumpswap" or p.t_recv_ms <= landing_ms:
            continue
        if p.t_recv_ms > landing_ms + window_ms:
            break
        n_after += 1
        if prev > 0:
            step = p.price_sol / prev - 1.0
            if p.side == "sell":
                worst_step = min(worst_step, step)
                if step <= ONE_STEP_DROP:
                    one_step = True
        if p.price_sol >= TP_MULT * p0:
            tp_seen = True
        if not tp_seen and p.price_sol < CRASH_FRAC * p0:
            crash = True
        prev = p.price_sol
    return {"one_step": one_step, "crash50": crash, "rug": one_step or crash, "n_after": n_after, "worst_sell_step": worst_step}


# --- tape pass -----------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def rug_patch(scores: Mapping[str, float], t_min: float) -> Iterator[None]:
    """The operating-point patch at ONE cell (k=6, 0.05 SOL) plus decision-time features and the outcome label
    attached to each cached row. Restores everything on exit."""
    with op.multi_cell_patch(scores, t_min, ks=(K,), sizes_sol=(SIZE_SOL,)):
        inner = eem.score_one

        def with_rug(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
            rows = inner(mint_id, mint, feat, curve, through_ms, creator_hist, **kw)
            if not rows:
                return rows
            fills, trig_slot, trig_ms, _ref = eem._fills_for(mint, migrate=True)
            prior = eem.count_prior_creates(creator_hist, feat.creator, feat.create_ms)
            f = rug_features(feat.events, getattr(feat, "creator", None), mint.mig_ms, prior)
            target = trig_slot + K
            idx = eem._state_index(fills, target, eem.ENTRY_BOUND)
            fallback = fills[idx].t_recv_ms if idx >= 0 else trig_ms
            lab = rug_label(fills, idx, eem._slot_time(fills, target, fallback))
            for r in rows:
                r["rugf"], r["rug"] = f, lab
            return rows

        eem.score_one = with_rug
        try:
            yield
        finally:
            eem.score_one = inner


def _worker(which: str, *args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    scores, _thr, _doc, _days = load_oof(Path(os.environ[ENV_ARTIFACT]))
    with rug_patch(scores, float(os.environ[ENV_TMIN])):
        return getattr(ad, f"worker_{which}")(*args, **kw)


def rug_worker_a(*args: Any, **kw: Any) -> Any:
    return _worker("a", *args, **kw)


def rug_worker_b(*args: Any, **kw: Any) -> Any:
    return _worker("b", *args, **kw)


def rug_worker_c(*args: Any, **kw: Any) -> Any:
    return _worker("c", *args, **kw)


@contextlib.contextmanager
def patched_rug_workers() -> Iterator[None]:
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
    eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = rug_worker_a, rug_worker_b, rug_worker_c
    try:
        yield
    finally:
        eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = saved


def collect_rows(fast: Path, insample: Path, live: Path, scratch: Path, artifact_dir: Path, vmap: str, max_workers: int = 2, buffer_hours: int = 24, max_home_hours: int | None = 12) -> list[dict[str, Any]]:
    from tools.exploration_entry_model_b2 import run_all_features_b
    from tools.exploration_entry_model_b3 import run_all_features_c

    if max_workers > 2:
        raise SystemExit("keep max-workers <= 2")
    set_env(vmap, scratch / "counts_virtual")
    os.environ[ENV_ARTIFACT], os.environ[ENV_TMIN] = str(artifact_dir), repr(FROZEN_THRESHOLD)
    common = dict(max_workers=max_workers, buffer_hours=buffer_hours, max_home_hours=max_home_hours)
    passes = (
        ("A", lambda: eem.run_all_features(out_dir=scratch / "poolA", backfill=fast, **common)),
        ("C", lambda: run_all_features_c(out_dir=scratch / "poolC", root=insample, **common)),
        ("B", lambda: run_all_features_b(out_dir=scratch / "poolB", root=live, **common)),
    )
    out: list[dict[str, Any]] = []
    with patched_rug_workers():
        for pool, fn in passes:
            print(f"rug-risk pass: pool {pool}...", file=sys.stderr, flush=True)
            for r in fn():
                if r.get("spec") == TARGET_SPEC_ID:
                    r["pool"] = pool
                    out.append(r)
    out.sort(key=lambda r: (r["day"] or "", r["mint"]))
    return out


# --- analysis (pure; tested on fixtures) --------------------------------------------------------------------------


def frozen_trades(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any] | None], dict[str, Mapping[str, Any] | None], int]:
    """(frozen trades, features by mint, rug label by mint, n_censored)."""
    idx = op.index_rows(rows)
    trades, cen = op.cell_trades(idx, FROZEN_CELL)
    size = op.size_lamports(SIZE_SOL)
    sel = [r for r in rows if int(r["k"]) == K and int(r["size"]) == size]
    return trades, {r["mint"]: r.get("rugf") for r in sel}, {r["mint"]: r.get("rug") for r in sel}, cen


def paired_filled(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], rule: str | None) -> list[dict[str, Any]]:
    """Paired difference (rule - frozen, lamports) over FILLED frozen trades only. A vetoed filled trade scores 0 (no
    tx sent), so the difference is minus what it earned. A vetoed MISS is not a row here: the miss stays at its fee in
    both arms (difference 0), and it is reported separately by `miss_counts`."""
    out = []
    for t in trades:
        if not t["filled"]:
            continue
        gone = rule is not None and vetoed(rule, feats.get(t["mint"]))
        out.append({"mint": t["mint"], "day": t["day"], "pool": t.get("pool"), "dflat": -t["flat"] if gone else 0.0, "dpress": -t["press"] if gone else 0.0, "entered": not gone})
    return out


def miss_counts(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], rule: str) -> dict[str, int]:
    """Misses a rule would have vetoed. Scored as 0 difference in both arms: never a gain."""
    misses = [t for t in trades if not t["filled"]]
    return {"n_misses": len(misses), "n_misses_vetoed": sum(1 for t in misses if vetoed(rule, feats.get(t["mint"])))}


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) / LAMPORTS if xs else None


def _ex_top3(diff: Sequence[Mapping[str, Any]], key: str) -> float | None:
    if not diff:
        return None
    vals = sorted((r[key] for r in diff), reverse=True)
    return (sum(vals) - sum(vals[:3])) / LAMPORTS


def rule_stats(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], labels: Mapping[str, Any], rule: str) -> dict[str, Any]:
    filled = [t for t in trades if t["filled"]]
    gone = [t for t in filled if vetoed(rule, feats.get(t["mint"]))]
    d = paired_filled(trades, feats, rule)
    rugs = [t for t in gone if (labels.get(t["mint"]) or {}).get("rug")]
    return {
        "rule": rule,
        "n_filled": len(filled),
        "n_vetoed_filled": len(gone),
        "n_vetoed_rug_of_filled": len(rugs),
        "vetoed_press_mean_sol": _mean([t["press"] for t in gone]),
        "vetoed_flat_mean_sol": _mean([t["flat"] for t in gone]),
        **miss_counts(trades, feats, rule),
        "paired_vs_frozen": paired_side(d),
        "ex_top3_paired_press_sol": _ex_top3(d, "dpress"),
        "ex_top3_paired_flat_sol": _ex_top3(d, "dflat"),
    }


def nested_lodo(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], rules: Sequence[str], days: Sequence[str], min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    ids = [FROZEN_ID, *rules]
    diffs = {FROZEN_ID: paired_filled(trades, feats, None)}
    for r in rules:
        diffs[r] = paired_filled(trades, feats, r)
    days = sorted(days)
    held: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for d in days:
        best, best_v = None, float("-inf")
        for i in ids:
            train = [r for r in diffs[i] if r["day"] != d]
            if not train or sum(1 for r in train if r["entered"]) < min_train_trades:
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
    insample = {i: (sum(r["dpress"] for r in diffs[i]) / len(diffs[i]) if diffs[i] else float("-inf")) for i in ids}
    best_in = max(ids, key=lambda i: (insample[i], -ids.index(i)))
    pooled = paired_side(held)
    return {
        "available": n_unavail < len(days),
        "candidates": ids,
        "min_train_trades": min_train_trades,
        "n_days": len(days),
        "n_unavailable_folds": n_unavail,
        "folds": folds,
        "times_chosen": {i: sum(1 for f in folds if f["chosen"] == i) for i in ids},
        "heldout_paired_vs_frozen": pooled,
        "insample_best": best_in,
        "insample_best_paired_press_mean_sol": None if insample[best_in] == float("-inf") else insample[best_in] / LAMPORTS,
        "optimism_gap_press_sol": op._gap(insample[best_in], pooled),
    }


def quantile_bins(values: Sequence[float], n_bins: int = N_BINS) -> list[int]:
    """Bin index per value by rank; equal values share the bin of the first occurrence, so a feature that is mostly 0
    collapses into fewer bins instead of splitting identical values."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    out = [0] * n
    first_bin: dict[float, int] = {}
    for rank, i in enumerate(order):
        v = values[i]
        if v not in first_bin:
            first_bin[v] = min(n_bins - 1, rank * n_bins // n)
        out[i] = first_bin[v]
    return out


def descriptive(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], labels: Mapping[str, Any]) -> dict[str, Any]:
    """Per feature, per quantile bin over all selected trades with a feature record: n, labelled n, rug rate, one-step
    rate, pressure and flat mean (misses included at their fee), filled n."""
    use = [t for t in trades if feats.get(t["mint"])]
    out: dict[str, Any] = {}
    for name in FEATURES:
        vals = [float(feats[t["mint"]][name]) for t in use]
        bins = quantile_bins(vals)
        rows = []
        for b in sorted(set(bins)):
            grp = [(t, v) for t, v, bb in zip(use, vals, bins) if bb == b]
            lab = [x for x in (labels.get(t["mint"]) for t, _ in grp) if x]
            rows.append(
                {
                    "bin": b,
                    "lo": min(v for _, v in grp),
                    "hi": max(v for _, v in grp),
                    "n": len(grp),
                    "n_filled": sum(1 for t, _ in grp if t["filled"]),
                    "n_labelled": len(lab),
                    "rug_rate": (sum(1 for x in lab if x["rug"]) / len(lab)) if lab else None,
                    "one_step_rate": (sum(1 for x in lab if x["one_step"]) / len(lab)) if lab else None,
                    "press_mean_sol": _mean([t["press"] for t, _ in grp]),
                    "flat_mean_sol": _mean([t["flat"] for t, _ in grp]),
                }
            )
        out[name] = rows
    return out


def analyze(rows: Sequence[Mapping[str, Any]], scores: Mapping[str, float], thr_doc: Mapping[str, Any] | None = None, oof_days: Mapping[str, str] | None = None, min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    op.check_integrity(rows, scores, thr_doc, oof_days)
    days = sorted(set(oof_days.values()) if oof_days else {r["day"] for r in rows if r.get("day")})
    trades, feats, labels, cen = frozen_trades(rows)
    ref = op.cell_stats(trades, FROZEN_CELL, len(days), cen)
    lab_all = [x for x in (labels.get(t["mint"]) for t in trades) if x]
    return {
        "schema": "exp012_rug_risk_v1",
        "status": BANNER,
        "n_rules_tried": len(RULE_IDS),
        "cumulative_tries_on_pool": EXISTING_TRIES_ON_POOL + len(RULE_IDS),
        "existing_tries_on_pool": EXISTING_TRIES_ON_POOL,
        "n_days": len(days),
        "days": days,
        "frozen_point": {"threshold": FROZEN_THRESHOLD, "k": K, "size_sol": SIZE_SOL, "fee": FEE},
        "n_selected": len(trades),
        "n_filled": sum(1 for t in trades if t["filled"]),
        "n_misses": sum(1 for t in trades if not t["filled"]),
        "n_without_features": sum(1 for t in trades if not feats.get(t["mint"])),
        "base_rug_rate": (sum(1 for x in lab_all if x["rug"]) / len(lab_all)) if lab_all else None,
        "n_labelled": len(lab_all),
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats)",
        "caveats": [
            "exploration pool, the same 9 days the model was frozen on: best-of-N, winner's curse applies; not a promote, not gate evidence",
            "features use bonding-curve events strictly before the migration trigger only; thresholds were fixed before any data was read",
            "a vetoed MISS is NOT a gain: the miss keeps its fee in both arms; scoring runs over FILLED trades only",
            "a vetoed filled trade is assumed to cost nothing (no tx sent)",
            "the rug label uses V-priced PumpSwap prints, a print-by-print sell step (not the live 400 ms poll), and the entry state price as P0",
            "the nested number is an UPPER BOUND: stored OOF scores saw the other days",
            "creator prior RUG count is not computed; creator_prior_mints_24h is a left-censored proxy",
            "only the ENTRY slot is delayed (k=6); the live sell shortfall and entry noise are not added",
        ],
        "frozen_stats": ref,
        "descriptive": descriptive(trades, feats, labels),
        "rules": [rule_stats(trades, feats, labels, r) for r in RULE_IDS],
        "nested_lodo": nested_lodo(trades, feats, RULE_IDS, days, min_train_trades=min_train_trades),
    }


# --- rendering ---------------------------------------------------------------------------------------------------


def _f(v: float | None, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


def _leg(g: Mapping[str, Any] | None) -> str:
    return "n/a | n/a | n/a" if not g else f"{_f(g['mean_sol'])} | {_ci(g['ci90_sol'])} | {g['days_positive']}/{g['n_days']}"


def render_md(rep: Mapping[str, Any]) -> str:
    fs = rep["frozen_stats"]
    lines = [
        "# EXP-012 rug-risk features at the frozen point (decision-time information only)",
        "",
        f"## **BEST-OF-N: N = {rep['n_rules_tried']} rules; cumulative tries on these {rep['n_days']} days = {rep['existing_tries_on_pool']} existing + {rep['n_rules_tried']} new = {rep['cumulative_tries_on_pool']}. {rep['status']}.**",
        "",
        f"CI: {rep['ci']}.",
        f"Frozen point: {rep['frozen_point']}. Selected {rep['n_selected']}, filled {rep['n_filled']}, simulator MISSES {rep['n_misses']} (reported separately; never a gain). Base rug rate {_f(rep['base_rug_rate'], 4)} over {rep['n_labelled']} labelled.",
        (f"Frozen: pressure mean {_f(fs['press']['mean_sol'])} {_ci(fs['press']['ci90_sol'])}, flat mean {_f(fs['flat']['mean_sol'])} {_ci(fs['flat']['ci90_sol'])}" if fs.get("press") and fs.get("flat") else "Frozen: no trades"),
        "",
        "Caveats:",
        *[f"- {c}" for c in rep["caveats"]],
        "",
        "## (a) Descriptive: rug rate and pressure mean by quantile bin (all selected, misses at their fee)",
    ]
    for name, bins in rep["descriptive"].items():
        lines += ["", f"### {name}", "", "| bin | range | n | filled | labelled | rug rate | one-step rate | press mean | flat mean |", "| " + " | ".join(["---"] * 9) + " |"]
        for b in bins:
            lines.append(f"| {b['bin']} | [{b['lo']:.4g}, {b['hi']:.4g}] | {b['n']} | {b['n_filled']} | {b['n_labelled']} | {_f(b['rug_rate'], 3)} | {_f(b['one_step_rate'], 3)} | {_f(b['press_mean_sol'])} | {_f(b['flat_mean_sol'])} |")
    lines += [
        "",
        "## (b) Per rule, paired vs frozen over FILLED trades (vetoed filled trade scores 0; difference SOL per filled trade)",
        "",
        "| rule | vetoed filled | of which rug | misses vetoed / misses | vetoed press mean | paired press | press CI90 | press days+ | ex-top3 press | paired flat | flat CI90 | flat days+ | ex-top3 flat |",
        "| " + " | ".join(["---"] * 13) + " |",
    ]
    for r in rep["rules"]:
        p = r["paired_vs_frozen"]
        lines.append(
            f"| {r['rule']} | {r['n_vetoed_filled']} | {r['n_vetoed_rug_of_filled']} | {r['n_misses_vetoed']} / {r['n_misses']} | {_f(r['vetoed_press_mean_sol'])} | {_leg(p.get('press'))} | {_f(r['ex_top3_paired_press_sol'])} | {_leg(p.get('flat'))} | {_f(r['ex_top3_paired_flat_sol'])} |"
        )
    n = rep["nested_lodo"]
    lines += ["", "## Nested leave-one-day-out over the rule family (frozen always a candidate), paired vs frozen, filled trades", ""]
    if not n["available"]:
        lines.append("- unavailable: no fold met the training floor")
    else:
        p = n["heldout_paired_vs_frozen"]
        lines.append(f"- held-out n {p['n']}, unavailable folds {n['n_unavailable_folds']}/{n['n_days']}, training floor {n['min_train_trades']}, times chosen {n['times_chosen']}")
        for name in ("press", "flat"):
            g = p.get(name)
            lines.append(f"  - {name}: " + ("no held-out rows" if not g else f"paired mean {_f(g['mean_sol'])} SOL/trade, CI90 {_ci(g['ci90_sol'])}, held-out days positive {g['days_positive']}/{g['n_days']}"))
        lines.append(f"- optimism gap (in-sample best `{n['insample_best']}` {_f(n['insample_best_paired_press_mean_sol'])} minus nested held-out): {_f(n['optimism_gap_press_sol'])} SOL/trade")
    lines.append("")
    return "\n".join(lines)


# --- tries log -----------------------------------------------------------------------------------------------------


def _already_in_log(log: Path, rule: str, result_path: Path) -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("tool") == TOOL and rec.get("config", {}).get("rule") == rule and rec.get("result_path") == str(result_path):
            return True
    return False


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 tries line per rule (role exploration). Idempotent per out dir (marker plus a log scan)."""
    from tools import mal_result
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker
    from tools.exp012_support import exploration_pool_blocks

    marker = out_dir / TRIES_MARKER
    done = _read_marker(marker)
    if "*" in done:
        return 0
    result_path = out_dir / "rug_risk.json"
    blocks = exploration_pool_blocks()
    n = 0
    for r in rep["rules"]:
        if r["rule"] in done:
            continue
        if not _already_in_log(Path(tries_log), r["rule"], result_path):
            mal_result.append_try(
                tries_log,
                tool=TOOL,
                config={"experiment": "EXP-012 rug risk", "rule": r["rule"], "threshold": FROZEN_THRESHOLD, "k": K, "size_sol": SIZE_SOL, "fee_lamports": FEE, "selection": "OOF", "pricing": "V", "n_rules": rep["n_rules_tried"]},
                data_blocks=blocks,
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(r["rule"])
    _write_marker(marker, done)
    return n


# --- CLI -------------------------------------------------------------------------------------------------------------


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
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.out_dir / SCRATCH_ROWS
    t0 = time.time()
    if args.reuse_rows and rows_path.is_file():
        rows = list(iter_rows_jsonl(rows_path))
    else:
        roots = guarded_roots(args)
        rows = collect_rows(roots["fast"], roots["insample"], roots["live"], args.out_dir / "scratch", args.artifact_dir, args.vmap,
                            max_workers=args.max_workers, buffer_hours=args.buffer_hours, max_home_hours=(args.max_home_hours or None))
        write_rows(rows_path, rows)
    rep = analyze(rows, scores, thr_doc, oof_days)
    rep["wall_s"] = time.time() - t0
    rep["tries"] = {"logged": log_tries(rep, args.out_dir, tries_path), "log_path": str(tries_path)}
    (args.out_dir / "rug_risk.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "rug_risk.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
