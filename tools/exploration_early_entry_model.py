#!/usr/bin/env python3
"""Exploration lane D: a learned filter on EARLY bonding-curve entries.

EXPLORATION ONLY. Produces at most a candidate for a later pre-registered
test on a fresh, never-read block. Never a promote. Does not touch the
forward runner, the promotion gate, or any live book.

Idea (lane B3, #156, found a profitable learned filter on MIGRATE entries;
this module asks the same question of a different, much worse base rate):
instead of waiting for a migration, decide at T = create_ms + Delta seconds
whether to buy directly on the bonding curve. Buying every create loses
about -32% gross per trade on average (`buy_all` in
ARTIFACTS/lab/latency-curve-2026-09-27.md) -- the question is whether a
learned top slice is profitable even though the average trade is not.

Reuse, not rebuild:
  - Pools, whitelists, hour loaders: tools.exploration_exits (pool A, fast),
    tools.oracle_insample_adapter (pool C), tools.oracle_live_adapter
    (pool B) -- the exact same 9-day, three-source exploration pool lane B3
    used, same hard fences.
  - Frozen fill/fee/fail code: tools.latency_curve._try_buy, _one_sell_close
    (via tools.exploration_exits._eval_tpsl / tools.latency_curve._hold),
    mixed_net, FailCurve, Pressure -- all unchanged.
  - Causal feature boundary: tools.exploration_entry_model.causal_events,
    compute_features, count_prior_creates, build_creator_history,
    _load_creates_full, _Feat -- all unchanged. compute_features's `mig_ms`
    parameter is fed this module's decision time T (not a migrate
    timestamp); the returned "time_to_migrate_s" key is therefore really
    "seconds from create to decision" -- constant within one Delta's rows,
    kept for parity with the migrate model's own feature name rather than
    invented anew.

No-lookahead: every feature comes from `causal_events`, gated strictly
before T (`t_recv_ms < T`), exactly as the migrate model. This module goes
one step further than the reviewed migrate model: `same_slot_buys` /
`nearby_buy_sol` (the pressure-fail model's own competing-buys-at-landing
input) are the lookahead lane B3's audit found in #156 -- see the PR #156
review comment quoting `tools/exploration_entry_model.py:354-371`. This
module's FEATURE_NAMES excludes both; they are never computed as a feature
here at all (the pressure-fail model itself still uses them, via `_pressure`
at the entry's landing slot, same as every other frozen cell in this repo --
that is EXECUTION, scored after the decision is already made, not a model
INPUT). tools/test_exploration_early_entry_model.py extends the no-lookahead
test with a direct perturbation of landing-slot tape data (extra prints at
and after the entry's landing slot) and checks the feature vector never
moves, on top of the inherited causal_events shuffle test.

Fixed by the manager, no search beyond this (EXP-D brief):
  - Decision times Delta in {5s, 15s, 30s} after the create's receive time
    (`feat.create_ms`, the same block time / receive time the create loader
    already uses). Entry lands at the next slot after the decision (K=1,
    "start" bound -- exactly the migrate cell's own landing rule, just
    anchored to a computed decision slot instead of a given trigger slot).
  - Direct route, 0.0005 SOL/side priority (ENTRY_PRIORITY_LAMPORTS, reused
    unchanged from tools.exploration_exits), size 0.1 SOL (ENTRY_SIZE_EARLY
    -- smaller than the migrate cell's 0.5 SOL because bonding-curve
    slippage is larger at the same size).
  - Exits: hold_30s (tools.latency_curve._hold), tpsl_tp50_sl30 and
    tpsl_tp100_sl40 (both present in tools.exploration_exits.build_specs()'s
    tp/sl grid -- the code supports tp100_sl40, so all three named exits in
    the brief are scored, not two).
  - Model: exactly the S2 spec -- lgb_medium LightGBM classifier on
    P(pressure net > 0), num_leaves=15, min_data_in_leaf=20,
    learning_rate=0.05, rounds=100, deterministic, one thread. Reused
    unchanged from tools.exploration_entry_model.SETTINGS.
  - Evaluation: leave-one-day-out over the same 9 UTC days lane B3 used.
    Two selections are reported for every (Delta, exit) cell: (i) the naive
    per-day top 10% (rank the held-out day's own scores, take the top
    decile of that day) and (ii) the NESTED fixed-threshold version -- for
    each held-out day D, an inner LODO over the other 8 days produces an
    out-of-fold score for every row in those 8 days (day d's inner model is
    trained on the other 7, excluding both D and d); the 90th percentile of
    that pooled inner out-of-fold score distribution is a threshold fixed
    before D is ever scored, then applied as-is to D's outer-model scores.
    The naive top-10% picks a fraction of D using D's OWN score
    distribution; the nested version never looks at D's distribution to
    pick the cutoff, only at the other 8 days'.
  - Pre-stated screen (nested fixed-threshold version ONLY, under BOTH fail
    models): pooled mean > 0 AND ex-top-3 SOL > 0 AND more than half of the
    9 held-out days positive. 3 Deltas x 3 exits = 9 cells screened.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_entry_model import (
    FEATURE_NAMES as _FEATURE_NAMES_MIGRATE,
    SEED,
    SETTINGS,
    _Feat,
    _load_creates_full,
    build_creator_history,
    causal_events,
    compute_features,
    count_prior_creates,
)
from tools.exploration_entry_model_b3 import _ci_lo as _ci_lo_b3
from tools.exploration_entry_model_b2 import _ex_top3_sol
from tools.exploration_exits import (
    ENTRY_BOUND,
    ENTRY_LAND_K,
    ENTRY_PORTAL_PPM,
    ENTRY_PRIORITY_LAMPORTS,
    POOL_END as POOL_A_END,
    POOL_HOURS as POOL_A_HOURS,
    POOL_START as POOL_A_START,
    _chunk,
    _curve,
    _eval_tpsl,
    _hour_info,
    build_specs,
)
from tools.latency_curve import (
    FLAT_FAIL,
    MISS,
    WINDOW_MS,
    FailCurve,
    Pressure,
    _Mint,
    _fills_for,
    _hold,
    _iter_trades,
    _pressure,
    _rss_mb,
    _slot_time,
    _state_at,
    _state_index,
    _trim_heap,
    _try_buy,
    mixed_net,
)
from tools.oracle_insample_adapter import BACKFILL_C, POOL_C_END, POOL_C_HOURS, POOL_C_START, _hour_info_c
from tools.oracle_live_adapter import (
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

# --- Fixed entry (manager brief, EXP-D) --------------------------------------

ENTRY_SIZE_EARLY = 100_000_000  # 0.1 SOL -- smaller than the migrate cell's
# 0.5 SOL because bonding-curve slippage is larger at the same size.
DELTAS_S = (5, 15, 30)

# Features: the migrate model's own list, minus the two lookahead features
# the #156 audit found (same_slot_buys, nearby_buy_sol come from the
# pressure-fail model's own landing-slot input -- execution, not a feature).
FEATURE_NAMES = [n for n in _FEATURE_NAMES_MIGRATE if n not in ("same_slot_buys", "nearby_buy_sol")]
assert FEATURE_NAMES == [
    "time_to_migrate_s",
    "n_bonding_trades",
    "n_buys",
    "n_sells",
    "n_buyers",
    "n_sellers",
    "buy_sol",
    "sell_sol",
    "net_flow_sol",
    "buy_sell_ratio",
    "sniper_buy_share",
    "top_holder_share",
    "price_return_pre",
    "mcap_at_t_sol",
    "hour_of_day",
    "hour_sin",
    "hour_cos",
    "creator_prior_mints_24h",
]
assert "same_slot_buys" not in FEATURE_NAMES and "nearby_buy_sol" not in FEATURE_NAMES

# S2 spec only (fixed by the manager -- no setting search here).
S2_SETTING = next(s for s in SETTINGS if s["id"] == "lgb_medium")
assert S2_SETTING["kind"] == "lightgbm"


def _fit_s2(x_train: Sequence[Sequence[float]], y_train: Sequence[int]) -> Any:
    """S2 (lgb_medium classifier), fit against THIS module's own 18-name
    FEATURE_NAMES. Deliberately not `tools.exploration_entry_model.fit_setting`:
    that function's `_train_lightgbm` hardcodes the MIGRATE model's own
    20-name FEATURE_NAMES for `lgb.Dataset`'s `feature_name=`, regardless of
    the width of the `x` it is actually given -- calling it here (18-wide
    vectors, two lookahead features dropped) raises a feature-count
    mismatch. Same hyperparameters and scale_pos_weight balancing as the
    migrate model's own `_train_lightgbm`, just re-pointed at this module's
    feature list.
    """
    import lightgbm as lgb
    import numpy as np

    xa = np.asarray(x_train, dtype=np.float64)
    ya = np.asarray(y_train, dtype=np.int32)
    pos = int(ya.sum())
    neg = len(ya) - pos
    scale = (neg / pos) if pos else 1.0
    train = lgb.Dataset(xa, label=ya, feature_name=list(FEATURE_NAMES))
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_threads": 1,
        "verbosity": -1,
        "learning_rate": S2_SETTING["learning_rate"],
        "num_leaves": S2_SETTING["num_leaves"],
        "min_data_in_leaf": S2_SETTING["min_data_in_leaf"],
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "scale_pos_weight": scale,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
    }
    return lgb.train(params, train, num_boost_round=S2_SETTING["rounds"])


def _predict_s2(model: Any, x_test: Sequence[Sequence[float]]) -> list[float]:
    import numpy as np

    xa = np.asarray(x_test, dtype=np.float64)
    return [float(v) for v in model.predict(xa)]


def _importance_s2(model: Any) -> list[tuple[str, float]]:
    gain = model.feature_importance(importance_type="gain")
    pairs = list(zip(FEATURE_NAMES, (float(v) for v in gain)))
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    return pairs

# Exits: hold_30s (custom, tools.latency_curve._hold) plus the two named
# tp/sl cells, both present in tools.exploration_exits.build_specs()'s grid.
_ALL_EXIT_SPECS = {s["id"]: s for s in build_specs()}
EXIT_IDS = ("hold_30s", "tpsl_tp50_sl30", "tpsl_tp100_sl40")
assert "tpsl_tp50_sl30" in _ALL_EXIT_SPECS and "tpsl_tp100_sl40" in _ALL_EXIT_SPECS, (
    "the code does not support tp100_sl40 -- brief says fall back to two exits, unused here"
)

DAYS_A = ("2026-09-19", "2026-09-20", "2026-09-21")
DAYS_C = ("2026-09-22", "2026-09-23", "2026-09-24")
DAYS_B = ("2026-09-26", "2026-09-27")
DAYS_ALL = ("2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27")
assert len(DAYS_ALL) == 9
assert DAYS_ALL == DAYS_A + DAYS_C + ("2026-09-25",) + DAYS_B

BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1
NESTED_THRESHOLD_PCT = 0.90


# --- Decision-time entry: T = create_ms + Delta_s * 1000 --------------------


def _utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def decision_landing(fills: Sequence[Any], mint_slot: int, decision_ms: int) -> tuple[int, int, int]:
    """(entry_idx, decision_slot, landing_ms) for a decision made at
    decision_ms. The decision slot is read off the market state visible at
    or before decision_ms (the slot of the last print with t_recv_ms <=
    decision_ms); if no print exists yet (thin early trading), the create's
    own slot is the base, matching `_fills_for`'s own fallback for a create
    trigger. Entry lands at decision_slot + ENTRY_LAND_K ("start" bound),
    the same next-slot rule the frozen migrate cell uses -- just anchored to
    a computed decision slot instead of a given trigger slot.
    """
    idx0 = _state_at(fills, decision_ms)
    decision_slot = fills[idx0].slot if idx0 >= 0 else mint_slot
    target = decision_slot + ENTRY_LAND_K
    idx = _state_index(fills, target, ENTRY_BOUND)
    fallback = fills[idx].t_recv_ms if idx >= 0 else decision_ms
    landing_ms = _slot_time(fills, target, fallback)
    return idx, decision_slot, landing_ms


def eval_exit_early(
    exit_id: str,
    fills: Sequence[Any],
    entry_idx: int,
    buy: Any,
    venue: str,
    landing_ms: int,
    tape_through_ms: int,
) -> tuple[int, int, int, int] | None:
    """Dispatch to the frozen exit evaluators at ENTRY_SIZE_EARLY. Not
    tools.exploration_exits.eval_spec: that function bakes in the migrate
    cell's own 0.5 SOL ENTRY_SIZE as the sell-side size, which would net a
    trade bought at 0.1 SOL against the wrong close size. `_eval_tpsl` and
    `_hold` both take size as an explicit argument, so they are reused
    directly instead, at this module's own size.
    """
    if exit_id == "hold_30s":
        return _hold(fills, buy, venue, ENTRY_SIZE_EARLY, ENTRY_PORTAL_PPM, landing_ms, tape_through_ms)
    spec = _ALL_EXIT_SPECS[exit_id]
    return _eval_tpsl(
        fills, entry_idx, buy, venue, ENTRY_SIZE_EARLY, landing_ms, tape_through_ms, spec["tp"], spec["sl"], spec["cap_ms"]
    )


def _row(mint_id: str, delta_s: int, spec_id: str, day: str, status: int, filled: bool, gross: int, flat: float, press: float, feats: dict[str, float]) -> dict[str, Any]:
    return {
        "mint": mint_id,
        "delta_s": delta_s,
        "spec": spec_id,
        "day": day,
        "status": status,
        "filled": filled,
        "gross": gross,
        "flat": flat,
        "press": press,
        "features": feats,
    }


def score_one_early(
    mint_id: str,
    mint: _Mint,
    feat: _Feat | None,
    curve: FailCurve,
    tape_through_ms: int,
    creator_hist: dict[str, list[int]],
) -> list[dict[str, Any]]:
    """One create. One row per (Delta, exit): 3 x 3 = 9, plus a shared MISS
    row per (Delta, exit) when the entry itself does not fill. Never gated
    on migration -- this is a bonding-curve entry, taken or not regardless
    of whether the mint ever migrates. `mint.fillable(migrate=False)` (via
    `_fills_for(..., migrate=False)`) already carries the tape from create
    onward, including any post-migration pumpswap prints seen within the
    scoring window, so an exit can still track price through a migration
    that happens to land inside its hold.
    """
    if feat is None:
        return []
    fills, mint_slot, _mint_block_ms, ref = _fills_for(mint, migrate=False)
    if not fills:
        return []
    day = _utc_day(feat.create_ms)
    prior = count_prior_creates(creator_hist, feat.creator, feat.create_ms)
    out: list[dict[str, Any]] = []
    for delta_s in DELTAS_S:
        decision_ms = feat.create_ms + delta_s * 1000
        idx, _decision_slot, landing_ms = decision_landing(fills, mint_slot, decision_ms)
        state = fills[idx] if idx >= 0 else None
        causal = causal_events(feat.events, decision_ms)
        feats = compute_features(
            causal,
            create_ms=feat.create_ms,
            first_price=feat.first_price,
            mig_ms=decision_ms,
            creator_prior_mints_24h=prior,
        )
        if state is None:
            flat = mixed_net(0, 1, MISS, ENTRY_PRIORITY_LAMPORTS, 0.0)
            for exit_id in EXIT_IDS:
                out.append(_row(mint_id, delta_s, exit_id, day, MISS, False, 0, flat, flat, feats))
            continue
        buy = _try_buy(state, ENTRY_SIZE_EARLY, ENTRY_PORTAL_PPM, ref)
        if buy is None:
            flat = mixed_net(0, 1, MISS, ENTRY_PRIORITY_LAMPORTS, 0.0)
            for exit_id in EXIT_IDS:
                out.append(_row(mint_id, delta_s, exit_id, day, MISS, False, 0, flat, flat, feats))
            continue
        venue = state.venue
        buys, nearby = _pressure(fills, idx, state.slot, landing_ms)  # execution only, never a feature
        p_press = curve.p(Pressure(buys, nearby))
        for exit_id in EXIT_IDS:
            result = eval_exit_early(exit_id, fills, idx, buy, venue, landing_ms, tape_through_ms)
            if result is None:
                continue
            net0, gross, sides, status = result
            flat = mixed_net(net0, sides, status, ENTRY_PRIORITY_LAMPORTS, FLAT_FAIL)
            press = mixed_net(net0, sides, status, ENTRY_PRIORITY_LAMPORTS, p_press)
            out.append(_row(mint_id, delta_s, exit_id, day, status, True, gross, flat, press, feats))
    return out


# --- Streaming worker: create-triggered, no migrate tracking needed --------
#
# Unlike the migrate model, this scorer never needs to wait for a migration
# or watch a released mint -- the decision (and every exit) is already
# resolved from the tape between create_ms and create_ms + WINDOW_MS, so a
# mint is simply tracked from its create until that window elapses (or the
# input runs out, which censors any exit whose deadline runs past it -- the
# same censoring rule tools.latency_curve's own evaluators already apply).


def run_worker_early(
    worker_id: int,
    home_keys: list[str],
    buffer_keys: list[str],
    creator_hist: dict[str, list[int]],
    *,
    hour_info_fn: Any = _hour_info,
    row_iter_fn: Any = _iter_trades,
    creates_override: dict[str, tuple[_Mint, _Feat]] | None = None,
) -> list[dict[str, Any]]:
    os.nice(19)
    home_hours = [hour_info_fn(k) for k in home_keys]
    buffer_hours = [hour_info_fn(k) for k in buffer_keys]
    hours = home_hours + buffer_hours
    creates = creates_override if creates_override is not None else _load_creates_full(home_hours)
    hot: dict[str, tuple[_Mint, _Feat]] = dict(creates)
    curve = _curve()
    out: list[dict[str, Any]] = []
    now_ms = 0
    scored = 0

    def score_and_collect(mint_id: str, mint: _Mint, feat: _Feat, through_ms: int) -> None:
        nonlocal scored
        out.extend(score_one_early(mint_id, mint, feat, curve, through_ms, creator_hist))
        scored += 1

    def flush(now_ms_local: int, final: bool) -> None:
        for mint_id in list(hot.keys()):
            mint, feat = hot[mint_id]
            if final or now_ms_local >= mint.block_ms + WINDOW_MS:
                score_and_collect(mint_id, mint, feat, now_ms_local)
                hot.pop(mint_id, None)

    lines = 0
    for hour in hours:
        print(
            f"[w{worker_id}] hour={hour['hour']} hot={len(hot)} scored={scored} rss_mb={_rss_mb()}",
            file=sys.stderr,
            flush=True,
        )
        for row in row_iter_fn(hour["trade"]):
            lines += 1
            mint_id = row.get("mint")
            if not isinstance(mint_id, str):
                continue
            entry = hot.get(mint_id)
            if entry is None:
                continue
            mint, feat = entry
            block = row.get("block_time")
            if not isinstance(block, int):
                continue
            if row.get("t_recv_ms") is None:
                row["t_recv_ms"] = block * 1000
            t_ms = row["t_recv_ms"]
            if row.get("venue") == "pump_bonding":
                feat.record(row, t_ms)
            from tools.paper_price_path import print_from_trade_row

            parsed = print_from_trade_row(row)
            if parsed is None:
                continue
            _name, pr = parsed
            now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
            mint.add(pr)
            if lines % 300_000 == 0:
                flush(now_ms, False)
        flush(max(now_ms, int(hour["end"]) * 1000), False)
        _trim_heap()
    flush(now_ms, True)
    print(f"[w{worker_id}] done lines={lines} scored={scored} rows={len(out)}", file=sys.stderr, flush=True)
    return out


# --- Pool A (fast-box) --------------------------------------------------


def plan_workers_a(max_workers: int = 3, buffer_hours: int = 2) -> list[tuple[int, list[str], list[str]]]:
    chunks = _chunk(list(POOL_A_HOURS), max_workers)
    plan = []
    for i, home in enumerate(chunks):
        idx = POOL_A_HOURS.index(home[-1])
        buf = POOL_A_HOURS[idx + 1 : idx + 1 + buffer_hours]
        plan.append((i, home, buf))
    return plan


def run_all_features_a(max_workers: int = 3, buffer_hours: int = 2) -> list[dict[str, Any]]:
    print(f"pool A trade hours: {POOL_A_START} .. {POOL_A_END} ({len(POOL_A_HOURS)})", file=sys.stderr, flush=True)
    creator_hist = build_creator_history()
    plan = plan_workers_a(max_workers, buffer_hours)
    print(f"pool A worker_plan={[(i, h[0], h[-1], b) for i, h, b in plan]}", file=sys.stderr, flush=True)
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for worker_id, home, buf in plan:
            rows.extend(run_worker_early(worker_id, home, buf, creator_hist))
        return rows
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=len(plan)) as pool:
        results = pool.starmap(run_worker_early, [(i, h, b, creator_hist) for i, h, b in plan])
    for part in results:
        rows.extend(part)
    return rows


# --- Pool C (Oracle in-sample) ------------------------------------------


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
    chunks = _chunk(list(POOL_C_HOURS), max_workers)
    plan = []
    for i, home in enumerate(chunks):
        idx = POOL_C_HOURS.index(home[-1])
        buf = POOL_C_HOURS[idx + 1 : idx + 1 + buffer_hours]
        plan.append((i, home, buf))
    return plan


def run_worker_c(worker_id: int, home_keys: list[str], buffer_keys: list[str], creator_hist: dict[str, list[int]]) -> list[dict[str, Any]]:
    return run_worker_early(worker_id, home_keys, buffer_keys, creator_hist, hour_info_fn=_hour_info_c)


def run_all_features_c(max_workers: int = 3, buffer_hours: int = 2) -> list[dict[str, Any]]:
    print(f"pool C trade hours: {POOL_C_START} .. {POOL_C_END} ({len(POOL_C_HOURS)})", file=sys.stderr, flush=True)
    print(f"pool C root: {BACKFILL_C}", file=sys.stderr, flush=True)
    creator_hist = build_creator_history_c()
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


# --- Pool B (Oracle live) -----------------------------------------------


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


def plan_workers_b(max_workers: int = 3, buffer_hours: int = 2) -> list[tuple[int, list[str], list[str]]]:
    chunks = _chunk(list(POOL_B_HOURS), max_workers)
    plan = []
    for i, home in enumerate(chunks):
        idx = POOL_B_HOURS.index(home[-1])
        buf = POOL_B_HOURS[idx + 1 : idx + 1 + buffer_hours]
        plan.append((i, home, buf))
    return plan


def _worker_home_window_ms(home_keys: list[str]) -> tuple[int, int]:
    from datetime import datetime, timezone

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
    home_start_ms, home_end_ms = _worker_home_window_ms(home_keys)
    home_creates = {mid: (m, f) for mid, (m, f) in all_creates.items() if home_start_ms <= m.block_ms < home_end_ms}
    print(
        f"[w{worker_id}] pool=B home={home_keys[0]}..{home_keys[-1]} creates={len(home_creates)} rss_mb={_rss_mb()}",
        file=sys.stderr,
        flush=True,
    )
    return run_worker_early(
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
    all_creates = load_creates_b()
    print(f"pool B creates: {len(all_creates)}", file=sys.stderr, flush=True)
    creator_hist = build_creator_history_b()
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


# --- Model: S2 only, LODO + nested fixed-threshold --------------------------


def _vector(feats: dict[str, float]) -> list[float]:
    return [float(feats.get(name, 0.0)) for name in FEATURE_NAMES]


def _cohort_stats(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "flat_net_mean_pct": None,
            "press_net_mean_pct": None,
            "flat_net_total_sol": None,
            "flat_ex_top3_sol": None,
            "press_net_total_sol": None,
            "press_ex_top3_sol": None,
        }
    flat_vals = [r["flat"] for r in rows]
    press_vals = [r["press"] for r in rows]
    flat = sum(flat_vals) / n / ENTRY_SIZE_EARLY * 100.0
    press = sum(press_vals) / n / ENTRY_SIZE_EARLY * 100.0
    return {
        "n": n,
        "flat_net_mean_pct": flat,
        "press_net_mean_pct": press,
        "flat_net_total_sol": sum(flat_vals) / LAMPORTS_PER_SOL,
        "flat_ex_top3_sol": _ex_top3_sol(flat_vals),
        "press_net_total_sol": sum(press_vals) / LAMPORTS_PER_SOL,
        "press_ex_top3_sol": _ex_top3_sol(press_vals),
    }


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _ci_lo(values: Sequence[float]) -> float:
    """Bootstrap 90% CI lower bound on the mean: 1000 draws, seed 1, 5th
    percentile. Delegates to lane B3's own reimplementation (algorithm
    identical, no cross-module random.Random call-order coupling)."""
    return _ci_lo_b3(values)


def fit_and_score(
    rows_train: Sequence[dict[str, Any]], rows_test: Sequence[dict[str, Any]]
) -> tuple[list[float], Any] | None:
    """Fit S2 on rows_train, return (scores for rows_test, the fitted
    model), or None if the fold cannot train (too few rows or a single
    class)."""
    if len(rows_train) < 20:
        return None
    y_train = [1 if r["press"] > 0 else 0 for r in rows_train]
    if len(set(y_train)) < 2:
        return None
    x_train = [_vector(r["features"]) for r in rows_train]
    model = _fit_s2(x_train, y_train)
    x_test = [_vector(r["features"]) for r in rows_test]
    return _predict_s2(model, x_test), model


def evaluate_cell(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> dict[str, Any]:
    """LODO for one (Delta, exit) cell. Per held-out day: the naive top-10%
    (rank the day's own outer scores) and the nested fixed-threshold
    selection (a cutoff fixed from an inner 8-day LODO on the OTHER 8 days,
    never touching the held-out day's own scores)."""
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for r in rows:
        if r["day"] in by_day:
            by_day[r["day"]].append(r)

    out: dict[str, Any] = {}
    for held_out in days:
        outer_train = [r for d in days if d != held_out for r in by_day.get(d, [])]
        outer_test = by_day.get(held_out, [])
        outer_fit = fit_and_score(outer_train, outer_test)
        if outer_fit is None:
            out[held_out] = {"trained": False, "n_train": len(outer_train), "n_test": len(outer_test)}
            continue
        outer_scores, outer_model = outer_fit

        order = sorted(range(len(outer_test)), key=lambda i: outer_scores[i], reverse=True)
        ranked = [outer_test[i] for i in order]
        k = max(1, round(len(ranked) * 10 / 100.0))
        naive_top10 = ranked[:k]

        other_days = [d for d in days if d != held_out]
        inner_scores_pooled: list[float] = []
        inner_folds_trained = 0
        for inner_day in other_days:
            inner_train = [r for d in other_days if d != inner_day for r in by_day.get(d, [])]
            inner_test = by_day.get(inner_day, [])
            inner_fit = fit_and_score(inner_train, inner_test)
            if inner_fit is None:
                continue
            inner_scores, _inner_model = inner_fit
            inner_scores_pooled.extend(inner_scores)
            inner_folds_trained += 1
        threshold = _pct(sorted(inner_scores_pooled), NESTED_THRESHOLD_PCT) if inner_scores_pooled else None
        nested_selected = (
            [r for r, s in zip(outer_test, outer_scores) if s >= threshold] if threshold is not None else []
        )

        out[held_out] = {
            "trained": True,
            "n_train": len(outer_train),
            "n_test": len(outer_test),
            "cohorts": {
                "all": _cohort_stats(outer_test),
                "naive_top10": _cohort_stats(naive_top10),
                "nested_fixed_threshold": _cohort_stats(nested_selected),
            },
            "nested_threshold_score": threshold,
            "inner_folds_trained": inner_folds_trained,
            "nested_top10_rows": naive_top10,
            "nested_selected_rows": nested_selected,
            "importance": _importance_s2(outer_model)[:8],
        }
    return out


# --- Pooling and the pre-stated screen (nested version only) ---------------


def _pool_rows(lodo: dict[str, dict[str, Any]], row_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    pooled: list[dict[str, Any]] = []
    days_used: list[str] = []
    for day, fold in lodo.items():
        if not fold.get("trained"):
            continue
        pooled.extend(fold.get(row_key, []))
        days_used.append(day)
    return pooled, days_used


def pooled_cohort_report(lodo: dict[str, dict[str, Any]], cohort_key: str, row_key: str) -> dict[str, Any]:
    pooled, days_used = _pool_rows(lodo, row_key)
    stats = _cohort_stats(pooled)
    flat_vals = [r["flat"] for r in pooled]
    press_vals = [r["press"] for r in pooled]
    stats["flat_ci_lo_pct"] = (_ci_lo(flat_vals) / ENTRY_SIZE_EARLY * 100.0) if flat_vals else None
    stats["press_ci_lo_pct"] = (_ci_lo(press_vals) / ENTRY_SIZE_EARLY * 100.0) if press_vals else None
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


def pooled_split_by_source(lodo: dict[str, dict[str, Any]], row_key: str) -> dict[str, Any]:
    pooled, _days = _pool_rows(lodo, row_key)
    by_pool: dict[str, list[dict[str, Any]]] = {"A": [], "C": [], "B": []}
    for r in pooled:
        by_pool.setdefault(r.get("pool", "?"), []).append(r)
    return {name: _cohort_stats(rows) for name, rows in by_pool.items()}


def screen_candidate(pooled_nested: dict[str, Any]) -> dict[str, Any]:
    """Pre-stated screen (fixed before this run, applied to the nested
    fixed-threshold cohort only): under BOTH fail models, pooled mean > 0
    AND ex-top-3 SOL > 0 AND more than half of the 9 held-out days
    positive."""
    n_days_total = pooled_nested["n_days_total"]
    majority_needed = n_days_total / 2.0

    def _ok(mean_key: str, ex3_key: str, days_key: str) -> bool:
        mean_v = pooled_nested.get(mean_key)
        ex3_v = pooled_nested.get(ex3_key)
        days_v = pooled_nested.get(days_key)
        return mean_v is not None and mean_v > 0 and ex3_v is not None and ex3_v > 0 and days_v is not None and days_v > majority_needed

    flat_ok = _ok("flat_net_mean_pct", "flat_ex_top3_sol", "n_days_flat_positive")
    press_ok = _ok("press_net_mean_pct", "press_ex_top3_sol", "n_days_press_positive")
    return {"flat_ok": flat_ok, "press_ok": press_ok, "candidate": bool(flat_ok and press_ok), "n_days_total": n_days_total, "majority_needed": majority_needed}


def _strip_rows(lodo: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for day, fold in lodo.items():
        f = dict(fold)
        f.pop("nested_top10_rows", None)
        f.pop("nested_selected_rows", None)
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


def cell_id(delta_s: int, exit_id: str) -> str:
    return f"delta{delta_s}s__{exit_id}"


def write_report(
    out_md: Path,
    out_json: Path,
    combined_full: dict[str, dict[str, Any]],
    pooled: dict[str, dict[str, Any]],
    screens: dict[str, dict[str, Any]],
    n_rows: dict[str, Any],
    wall_s: float,
) -> None:
    combined_json = {cid: _strip_rows(lodo) for cid, lodo in combined_full.items()}
    cells_screened = len(DELTAS_S) * len(EXIT_IDS)
    out_json.write_text(
        json.dumps(
            {
                "schema": "exploration_early_entry_model_v1",
                "pool_a": {"start": POOL_A_START, "end": POOL_A_END, "hours": len(POOL_A_HOURS)},
                "pool_c": {"start": POOL_C_START, "end": POOL_C_END, "hours": len(POOL_C_HOURS)},
                "pool_b": {"start": POOL_B_START, "end": POOL_B_END, "hours": len(POOL_B_HOURS), "cutoff_iso": POOL_B_CUTOFF_ISO},
                "days_all": DAYS_ALL,
                "deltas_s": DELTAS_S,
                "exit_ids": EXIT_IDS,
                "entry_size_lamports": ENTRY_SIZE_EARLY,
                "priority_lamports": ENTRY_PRIORITY_LAMPORTS,
                "setting": S2_SETTING,
                "feature_names": FEATURE_NAMES,
                "nested_threshold_pct": NESTED_THRESHOLD_PCT,
                "cells_screened": cells_screened,
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
    lines.append('  subagentId: "exploration-early-entry-model-2026-09-29"')
    lines.append("---")
    lines.append("")
    lines.append("# Exploration: learned filter on early bonding-curve entries (screen pre-stated)")
    lines.append("")
    lines.append(
        "**Exploration, not a pre-registered test.** Lane B3 (#156) found a learned filter that picks "
        "profitable MIGRATE entries. This asks the same question of EARLY bonding-curve entries -- buy "
        "at T = create + Delta seconds, before any migration -- where the unconditional base rate is "
        "much worse (`buy_all` averages about -32% gross per trade, `ARTIFACTS/lab/latency-curve-2026-09-27.md`). "
        "A cell passing the screen below is a candidate for a later pre-registered test on a fresh, "
        "never-read block -- never a promote -- and none of this touches the forward runner, the "
        "promotion gate, or any live book."
    )
    lines.append("")
    lines.append(
        f"**Winner's-curse note.** {cells_screened} cells (Delta x exit = {len(DELTAS_S)} x {len(EXIT_IDS)}) "
        "were screened below by one pre-stated rule, applied to the nested fixed-threshold cohort only, "
        "fixed by the manager before this run and never adjusted after seeing results. A cell passing "
        "this screen is still the best of "
        f"{cells_screened} by construction and still needs its own pre-registration and its own "
        "out-of-sample block before it means anything for the promotion gate."
    )
    lines.append("")
    lines.append("## Data: three source pools, 9 UTC days, no overlap (same pool lane B3 used)")
    lines.append("")
    lines.append(
        f"Pool A (fast-box backfill): `{POOL_A_START}` - `{POOL_A_END}` ({len(POOL_A_HOURS)} hours). "
        f"Pool C (Oracle in-sample backfill): `{POOL_C_START}` - `{POOL_C_END}` ({len(POOL_C_HOURS)} hours). "
        f"Pool B (Oracle live tape): `{POOL_B_START}` - `{POOL_B_END}` ({len(POOL_B_HOURS)} hours). "
        "Hard fences, explicit hour whitelists, and assertions reused unchanged from "
        "`tools/exploration_exits.py` / `tools/oracle_insample_adapter.py` / `tools/oracle_live_adapter.py`."
    )
    lines.append("")
    lines.append(
        "Rows scored (full pool, MISS included, no survivorship / DEC-007), by (Delta, exit) cell -- "
        "fast-box (A) reported separately from Oracle in-sample (C) and Oracle live (B):"
    )
    lines.append("")
    lines.append("| Cell | A (fast) | C (Oracle in-sample) | B (Oracle live) | Total |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for delta_s in DELTAS_S:
        for exit_id in EXIT_IDS:
            cid = cell_id(delta_s, exit_id)
            a, c, b = n_rows["A"].get(cid, 0), n_rows["C"].get(cid, 0), n_rows["B"].get(cid, 0)
            lines.append(f"| `{cid}` | {a} | {c} | {b} | {a + c + b} |")
    lines.append("")
    lines.append("## Fixed entry and model (manager brief, no search)")
    lines.append("")
    lines.append(
        f"Decision at T = create + Delta seconds, Delta in `{DELTAS_S}`. Entry lands at the next slot "
        "after T (K=1, start bound), direct route, priority 0.0005 SOL/side "
        f"({ENTRY_PRIORITY_LAMPORTS} lamports), size 0.1 SOL ({ENTRY_SIZE_EARLY} lamports). Exits: "
        f"`{'`, `'.join(EXIT_IDS)}` (all three named in the brief -- the code supports tp100_sl40). "
        f"Model: `{S2_SETTING['id']}` only (num_leaves={S2_SETTING['num_leaves']}, "
        f"min_data_in_leaf={S2_SETTING['min_data_in_leaf']}, learning_rate={S2_SETTING['learning_rate']}, "
        f"rounds={S2_SETTING['rounds']}), no setting search."
    )
    lines.append("")
    lines.append("Feature list (`same_slot_buys` / `nearby_buy_sol` excluded -- see module docstring):")
    lines.append("")
    lines.append("`" + "`, `".join(FEATURE_NAMES) + "`")
    lines.append("")
    lines.append("## Pre-stated candidate screen, nested fixed-threshold cohort, pooled over the 9 held-out days")
    lines.append("")
    lines.append(
        "A cell is a **CANDIDATE** only if, under BOTH fail models: pooled nested-fixed-threshold mean "
        "net % > 0, pooled ex-top-3 SOL > 0, and more than half of the 9 held-out days are individually "
        "positive (that day's own nested-threshold cohort mean, under that fail model)."
    )
    lines.append("")
    lines.append(
        "| Delta | Exit | n | Flat mean % (CI lo) | Flat total SOL | Flat ex-top-3 SOL | Flat days+ | "
        "Press mean % (CI lo) | Press total SOL | Press ex-top-3 SOL | Press days+ | Screen |"
    )
    lines.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    any_candidate = False
    for delta_s in DELTAS_S:
        for exit_id in EXIT_IDS:
            cid = cell_id(delta_s, exit_id)
            p = pooled[cid]["nested_fixed_threshold"]
            scr = screens[cid]
            any_candidate = any_candidate or scr["candidate"]
            verdict = "**CANDIDATE**" if scr["candidate"] else "no"
            lines.append(
                f"| {delta_s}s | `{exit_id}` | {p['n']} | "
                f"{_fmt_pct(p['flat_net_mean_pct'])} (CI lo {_fmt_pct(p['flat_ci_lo_pct'])}) | "
                f"{_fmt_sol(p['flat_net_total_sol'])} | {_fmt_sol(p['flat_ex_top3_sol'])} | "
                f"{p['n_days_flat_positive']}/{p['n_days_total']} | "
                f"{_fmt_pct(p['press_net_mean_pct'])} (CI lo {_fmt_pct(p['press_ci_lo_pct'])}) | "
                f"{_fmt_sol(p['press_net_total_sol'])} | {_fmt_sol(p['press_ex_top3_sol'])} | "
                f"{p['n_days_press_positive']}/{p['n_days_total']} | {verdict} |"
            )
    lines.append("")
    if any_candidate:
        passed = [
            f"Delta={delta_s}s/`{exit_id}`"
            for delta_s in DELTAS_S
            for exit_id in EXIT_IDS
            if screens[cell_id(delta_s, exit_id)]["candidate"]
        ]
        lines.append(f"**Cells passing the screen: {', '.join(passed)}.**")
    else:
        lines.append(
            f"**No cell passes the pre-stated screen.** None of the {cells_screened} (Delta, exit) cells "
            "clears all three conditions under both fail models on the nested fixed-threshold cohort."
        )
    lines.append("")
    lines.append("## Naive per-day top-10% (for comparison only -- not screened)")
    lines.append("")
    lines.append("| Delta | Exit | n | Flat mean % (CI lo) | Press mean % (CI lo) |")
    lines.append("| ---: | --- | ---: | ---: | ---: |")
    for delta_s in DELTAS_S:
        for exit_id in EXIT_IDS:
            cid = cell_id(delta_s, exit_id)
            p = pooled[cid]["naive_top10"]
            lines.append(
                f"| {delta_s}s | `{exit_id}` | {p['n']} | "
                f"{_fmt_pct(p['flat_net_mean_pct'])} (CI lo {_fmt_pct(p['flat_ci_lo_pct'])}) | "
                f"{_fmt_pct(p['press_net_mean_pct'])} (CI lo {_fmt_pct(p['press_ci_lo_pct'])}) |"
            )
    lines.append("")

    for delta_s in DELTAS_S:
        for exit_id in EXIT_IDS:
            cid = cell_id(delta_s, exit_id)
            lodo = combined_full[cid]
            lines.append(f"## `{cid}`")
            lines.append("")
            lines.append(
                "| Held-out day | Cohort | n | Flat net % | Press net % | Nested threshold score | Inner folds trained |"
            )
            lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
            for day in DAYS_ALL:
                fold = lodo.get(day, {})
                if not fold.get("trained"):
                    lines.append(f"| {day} | (not trained -- n_train={fold.get('n_train', 0)}, n_test={fold.get('n_test', 0)}) |  |  |  |  |  |")
                    continue
                thr = fold.get("nested_threshold_score")
                thr_s = "n/a" if thr is None else f"{thr:.4f}"
                for cohort_name in ("all", "naive_top10", "nested_fixed_threshold"):
                    c = fold["cohorts"][cohort_name]
                    lines.append(
                        f"| {day} | {cohort_name} | {c['n']} | {_fmt_pct(c['flat_net_mean_pct'])} | "
                        f"{_fmt_pct(c['press_net_mean_pct'])} | {thr_s} | {fold.get('inner_folds_trained', 0)} |"
                    )
            lines.append("")
            lines.append(f"### `{cid}` -- nested fixed-threshold, pooled top10%, split by source")
            lines.append("")
            split = pooled_split_by_source(lodo, "nested_selected_rows")
            lines.append("| Source | n | Flat net % | Press net % |")
            lines.append("| --- | ---: | ---: | ---: |")
            for pool_name, label in (("A", "fast"), ("C", "Oracle in-sample"), ("B", "Oracle live")):
                s = split.get(pool_name, {"n": 0, "flat_net_mean_pct": None, "press_net_mean_pct": None})
                lines.append(f"| {label} | {s['n']} | {_fmt_pct(s['flat_net_mean_pct'])} | {_fmt_pct(s['press_net_mean_pct'])} |")
            lines.append("")
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
            lines.append(f"### `{cid}` -- top features (outer-fold models), mean importance and per-fold stability")
            lines.append("")
            lines.append("| Feature | Mean importance | Held-out days in its top-5 |")
            lines.append("| --- | ---: | --- |")
            for name, vals in ranked[:8]:
                days_in_top5 = [d for d, top5 in top5_by_day.items() if name in top5]
                lines.append(f"| `{name}` | {sum(vals) / len(vals):.3g} | {len(days_in_top5)}/9: {', '.join(days_in_top5) if days_in_top5 else '-'} |")
            lines.append("")

    lines.append(f"Wall time: {wall_s:.0f}s. Full grid: `exploration-early-entry-model-2026-09-29.json`.")
    lines.append("")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exploration lane D: learned filter on early bonding-curve entries")
    parser.add_argument("--out-md", type=Path, default=Path("ARTIFACTS/lab/exploration-early-entry-model-2026-09-29.md"))
    parser.add_argument("--out-json", type=Path, default=Path("ARTIFACTS/lab/exploration-early-entry-model-2026-09-29.json"))
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--buffer-hours", type=int, default=2)
    args = parser.parse_args()
    t0 = time.time()

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

    by_cell_a: dict[str, list[dict[str, Any]]] = {}
    by_cell_c: dict[str, list[dict[str, Any]]] = {}
    by_cell_b: dict[str, list[dict[str, Any]]] = {}
    by_cell_all: dict[str, list[dict[str, Any]]] = {}
    for r in rows_a:
        cid = cell_id(r["delta_s"], r["spec"])
        by_cell_a.setdefault(cid, []).append(r)
        by_cell_all.setdefault(cid, []).append(r)
    for r in rows_c:
        cid = cell_id(r["delta_s"], r["spec"])
        by_cell_c.setdefault(cid, []).append(r)
        by_cell_all.setdefault(cid, []).append(r)
    for r in rows_b:
        cid = cell_id(r["delta_s"], r["spec"])
        by_cell_b.setdefault(cid, []).append(r)
        by_cell_all.setdefault(cid, []).append(r)

    all_cids = [cell_id(d, e) for d in DELTAS_S for e in EXIT_IDS]
    n_rows = {
        "A": {cid: len(by_cell_a.get(cid, [])) for cid in all_cids},
        "C": {cid: len(by_cell_c.get(cid, [])) for cid in all_cids},
        "B": {cid: len(by_cell_b.get(cid, [])) for cid in all_cids},
    }

    combined_full: dict[str, dict[str, Any]] = {}
    pooled: dict[str, dict[str, Any]] = {}
    screens: dict[str, dict[str, Any]] = {}
    for cid in all_cids:
        print(f"=== LODO {cid} ===", file=sys.stderr, flush=True)
        lodo = evaluate_cell(by_cell_all.get(cid, []), DAYS_ALL)
        combined_full[cid] = lodo
        pooled[cid] = {
            "naive_top10": pooled_cohort_report(lodo, "naive_top10", "nested_top10_rows"),
            "nested_fixed_threshold": pooled_cohort_report(lodo, "nested_fixed_threshold", "nested_selected_rows"),
        }
        screens[cid] = screen_candidate(pooled[cid]["nested_fixed_threshold"])
        print(f"rss_mb={_rss_mb()}", file=sys.stderr, flush=True)

    wall_s = time.time() - t0
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.out_md, args.out_json, combined_full, pooled, screens, n_rows, wall_s)
    print(f"wrote {args.out_md} and {args.out_json} in {wall_s:.0f}s", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
