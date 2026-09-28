#!/usr/bin/env python3
"""Exploration: a learned migrate entry filter (leave-one-day-out).

EXPLORATION ONLY. This produces a candidate for a later pre-registered
test. It never promotes and it does not touch the forward runner, the
promotion gate, or any live book. See
ARTIFACTS/lab/exploration-entry-model-2026-09-28.md.

Idea: instead of hand-tuning entry rules, learn which migrate attempts are
good from the FULL book -- winners, losers, and misses, all attempts, no
survivorship (DEC-007) -- using only features knowable at decision time T
(the migrate trigger). Then check whether the model's top-ranked trades
make money on a UTC day it was not trained on (leave-one-day-out over the
pool's 3 days).

Reuse, not rebuild: this module imports the fenced exploration pool, the
create/trade loaders, the frozen entry execution constants, and the exit
evaluator from tools.exploration_exits (#139), and scores exactly the two
frozen exits named in the brief: tp50_sl30 (spec id tpsl_tp50_sl30) and
trail_30_act20. Only the entry SELECTION is new; the entry execution and
exit mechanics are untouched.

Data fence (hard, inherited from tools.exploration_exits): sealed fast-box
backfill hours 2026-09-19T01 through 2026-09-21T23 only, in
/var/lib/mal/backfill-fast (read-only). Never an hour older than
2026-09-19T01 (EXP-009's holdout). Never forward-paper output.

No future info: every feature is computed from pump_bonding trade events
strictly before the migrate trigger's receive time (the accumulation is
only ever fed rows while a mint has not yet migrated -- see
run_worker_features below -- and tools/test_exploration_entry_model.py
proves the pure feature function ignores events at or after the cutoff
even when they are shuffled into the input). The one deliberate exception
is "same-slot buy pressure at T", which reuses the frozen pressure-fail
model's own input (tools.latency_curve._pressure): it looks at competing
buys up to the order's own slot+1 landing, the same causal boundary that
model already uses and that this repo has already reviewed. That is
documented, not hidden.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_exits import (
    ENTRY_BOUND,
    ENTRY_LAND_K,
    ENTRY_PORTAL_PPM,
    ENTRY_PRIORITY_LAMPORTS,
    ENTRY_SIZE,
    POOL_END,
    POOL_HOURS,
    POOL_START,
    PRESSURE_INTERCEPT,
    _curve,
    _hour_info,
    build_specs,
    eval_spec,
    plan_workers,
)
from tools.latency_curve import (
    FLAT_FAIL,
    MISS,
    WINDOW_MS,
    WSOL,
    FailCurve,
    Pressure,
    _Mint,
    _anchor,
    _fills_for,
    _iter_trades,
    _pressure,
    _rss_mb,
    _slot_time,
    _state_index,
    _trim_heap,
    _try_buy,
    mixed_net,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import print_from_trade_row

# --- The two frozen exits named in the brief, reused unchanged from #139 ---

_ALL_SPECS = {s["id"]: s for s in build_specs()}
TARGET_SPEC_IDS = ("tpsl_tp50_sl30", "trail_30_act20")
TARGET_SPECS = [_ALL_SPECS[sid] for sid in TARGET_SPEC_IDS]
assert len(TARGET_SPECS) == 2, "expected exactly tp50_sl30 and trail_30_act20 from #139's grid"

DAYS = ("2026-09-19", "2026-09-20", "2026-09-21")
SNIPER_WINDOW_MS = 3_000
CREATOR_LOOKBACK_MS = 24 * 3_600 * 1000
SEED = 1

FEATURE_NAMES = [
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
    "same_slot_buys",
    "nearby_buy_sol",
    "hour_of_day",
    "hour_sin",
    "hour_cos",
    "creator_prior_mints_24h",
]

# --- Causal per-mint pre-migration feature accumulation ---------------------


def causal_events(events: Sequence[tuple], t_cutoff_ms: int) -> list[tuple]:
    """Keep only events with t_recv_ms strictly before t_cutoff_ms, ordered
    by receive time.

    This is the single causal boundary a decision-time feature must respect.
    It does not trust its caller's ordering (the sort makes it robust to
    out-of-order input, not just to future input) -- tools/
    test_exploration_entry_model.py shuffles extra events at or after
    t_cutoff_ms into the input, in arbitrary order, and checks the filtered
    result -- and so the features built from it -- never changes.
    """
    kept = [e for e in events if e[0] < t_cutoff_ms]
    kept.sort(key=lambda e: e[0])
    return kept


def compute_features(
    events: Sequence[tuple],
    *,
    create_ms: int,
    first_price: float | None,
    mig_ms: int,
    creator_prior_mints_24h: int,
) -> dict[str, float]:
    """Pure aggregation over pre-cutoff pump_bonding buy/sell events.

    `events` must already be causal (see causal_events); this function does
    not look at wall-clock time itself, only at what it is handed.
    Each event is (t_ms, side, trader_or_None, sol_lamports, token_raw,
    price_sol_or_None).
    """
    n_buys = n_sells = 0
    buyers: set[str] = set()
    sellers: set[str] = set()
    buy_sol_l = sell_sol_l = 0
    sniper_sol_l = 0
    bal: dict[str, int] = {}
    last_price = first_price
    for t_ms, side, trader, sol_l, tok, price in events:
        if price is not None and price > 0:
            last_price = price
        if side == "buy":
            n_buys += 1
            if trader:
                buyers.add(trader)
                bal[trader] = bal.get(trader, 0) + tok
            buy_sol_l += sol_l
            if t_ms - create_ms <= SNIPER_WINDOW_MS:
                sniper_sol_l += sol_l
        elif side == "sell":
            n_sells += 1
            if trader:
                sellers.add(trader)
                bal[trader] = bal.get(trader, 0) - tok
            sell_sol_l += sol_l
    buy_sol = buy_sol_l / LAMPORTS_PER_SOL
    sell_sol = sell_sol_l / LAMPORTS_PER_SOL
    top_bal = max(bal.values()) if bal else 0
    pos_sum = sum(v for v in bal.values() if v > 0)
    top_share = (top_bal / pos_sum) if pos_sum > 0 and top_bal > 0 else 0.0
    price_ret = (last_price / first_price - 1.0) if first_price and first_price > 0 and last_price else 0.0
    hour = time.gmtime(mig_ms / 1000.0).tm_hour
    ang = 2.0 * math.pi * hour / 24.0
    return {
        "time_to_migrate_s": (mig_ms - create_ms) / 1000.0,
        "n_bonding_trades": float(n_buys + n_sells),
        "n_buys": float(n_buys),
        "n_sells": float(n_sells),
        "n_buyers": float(len(buyers)),
        "n_sellers": float(len(sellers)),
        "buy_sol": buy_sol,
        "sell_sol": sell_sol,
        "net_flow_sol": buy_sol - sell_sol,
        "buy_sell_ratio": buy_sol / (sell_sol + 1e-6),
        "sniper_buy_share": (sniper_sol_l / buy_sol_l) if buy_sol_l > 0 else 0.0,
        "top_holder_share": top_share,
        "price_return_pre": price_ret,
        "mcap_at_t_sol": (last_price or 0.0) * 1_000_000_000.0,
        "hour_of_day": float(hour),
        "hour_sin": math.sin(ang),
        "hour_cos": math.cos(ang),
        "creator_prior_mints_24h": float(creator_prior_mints_24h),
    }


def count_prior_creates(creator_hist: dict[str, list[int]], creator: str | None, create_ms: int) -> int:
    """Creator's prior mints in the trailing 24h, within the exploration pool
    only (EXP-009's G1 definition, recomputed here for this pool). Left-
    censored at the pool start (2026-09-19T01): a mint created early in the
    pool sees an undercount because a creator's earlier mints, if any, may
    be outside the pool. Documented, not hidden.
    """
    if not creator:
        return 0
    times = creator_hist.get(creator)
    if not times:
        return 0
    lo = bisect.bisect_left(times, create_ms - CREATOR_LOOKBACK_MS)
    hi = bisect.bisect_left(times, create_ms)  # excludes create_ms itself
    return hi - lo


class _Feat:
    __slots__ = ("creator", "create_ms", "first_price", "events")

    def __init__(self, creator: str, create_ms: int, first_price: float | None) -> None:
        self.creator = creator
        self.create_ms = create_ms
        self.first_price = first_price
        self.events: list[tuple] = []

    def record(self, row: dict[str, Any], t_ms: int) -> None:
        side = row.get("side")
        if side not in ("buy", "sell"):
            return
        trader = row.get("trader")
        sol = row.get("sol_lamports")
        tok = row.get("token_raw")
        price = row.get("price_sol")
        self.events.append(
            (
                t_ms,
                side,
                trader if isinstance(trader, str) else None,
                sol if isinstance(sol, int) else 0,
                tok if isinstance(tok, int) else 0,
                price if isinstance(price, (int, float)) and price > 0 else None,
            )
        )


# --- Creator prior-mint history, whole pool, precomputed once --------------


def build_creator_history() -> dict[str, list[int]]:
    hist: dict[str, list[int]] = {}
    for key in POOL_HOURS:
        hour = _hour_info(key)
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


# --- Load pool creates with both a _Mint (for exit scoring) and a _Feat ----


def _load_creates_full(hours: Sequence[dict[str, Any]]) -> dict[str, tuple[_Mint, _Feat]]:
    found: dict[str, tuple[_Mint, _Feat]] = {}
    for hour in hours:
        path = hour.get("create")
        if path is None:
            continue
        for row in _iter_trades(path):
            if row.get("type") != "create":
                continue
            mint_id = row.get("mint")
            slot = row.get("slot")
            block = row.get("block_time")
            if not isinstance(mint_id, str) or not isinstance(slot, int) or not isinstance(block, int):
                continue
            quote_mint = row.get("quote_mint")
            if isinstance(quote_mint, str) and quote_mint and quote_mint != WSOL:
                continue
            block_ms = block * 1000
            prev = found.get(mint_id)
            if prev is not None and prev[0].block_ms <= block_ms:
                continue
            sig = row.get("signature") if isinstance(row.get("signature"), str) else None
            anchor = _anchor(row, slot, block_ms, sig)
            m = _Mint(slot, block_ms, 0, anchor)
            creator = row.get("creator")
            first_price = anchor.price_sol if anchor is not None else None
            f = _Feat(creator if isinstance(creator, str) else "", block_ms, first_price)
            found[mint_id] = (m, f)
    return found


# --- Per-migration scoring: features + the two frozen exits ----------------


def _utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def _row(mint_id: str, spec_id: str, day: str, status: int, filled: bool, gross: int, flat: float, press: float, feats: dict[str, float]) -> dict[str, Any]:
    return {
        "mint": mint_id,
        "spec": spec_id,
        "day": day,
        "status": status,
        "filled": filled,
        "gross": gross,
        "flat": flat,
        "press": press,
        "features": feats,
    }


def score_one(
    mint_id: str,
    mint: _Mint,
    feat: _Feat | None,
    curve: FailCurve,
    tape_through_ms: int,
    creator_hist: dict[str, list[int]],
) -> list[dict[str, Any]]:
    if mint.mig_slot is None or mint.mig_ms is None or feat is None:
        return []
    fills, trigger_slot, trigger_block_ms, ref = _fills_for(mint, migrate=True)
    if not fills:
        return []
    day = _utc_day(mint.mig_ms)
    target = trigger_slot + ENTRY_LAND_K
    idx = _state_index(fills, target, ENTRY_BOUND)
    fallback = fills[idx].t_recv_ms if idx >= 0 else trigger_block_ms
    landing_ms = _slot_time(fills, target, fallback)
    state = fills[idx] if idx >= 0 else None
    buys, nearby = (0, 0)
    if state is not None:
        buys, nearby = _pressure(fills, idx, state.slot, landing_ms)
    prior = count_prior_creates(creator_hist, feat.creator, feat.create_ms)
    causal = causal_events(feat.events, mint.mig_ms)
    feats = compute_features(
        causal,
        create_ms=feat.create_ms,
        first_price=feat.first_price,
        mig_ms=mint.mig_ms,
        creator_prior_mints_24h=prior,
    )
    feats["same_slot_buys"] = float(buys)
    feats["nearby_buy_sol"] = nearby / LAMPORTS_PER_SOL
    out: list[dict[str, Any]] = []
    if state is None:
        flat = mixed_net(0, 1, MISS, ENTRY_PRIORITY_LAMPORTS, 0.0)
        for spec in TARGET_SPECS:
            out.append(_row(mint_id, spec["id"], day, MISS, False, 0, flat, flat, feats))
        return out
    buy = _try_buy(state, ENTRY_SIZE, ENTRY_PORTAL_PPM, ref)
    if buy is None:
        flat = mixed_net(0, 1, MISS, ENTRY_PRIORITY_LAMPORTS, 0.0)
        for spec in TARGET_SPECS:
            out.append(_row(mint_id, spec["id"], day, MISS, False, 0, flat, flat, feats))
        return out
    venue = state.venue
    p_press = curve.p(Pressure(buys, nearby))
    for spec in TARGET_SPECS:
        result = eval_spec(spec, fills, idx, state.price_sol, buy, venue, landing_ms, tape_through_ms)
        if result is None:
            continue
        net0, gross, sides, status = result
        flat = mixed_net(net0, sides, status, ENTRY_PRIORITY_LAMPORTS, FLAT_FAIL)
        press = mixed_net(net0, sides, status, ENTRY_PRIORITY_LAMPORTS, p_press)
        out.append(_row(mint_id, spec["id"], day, status, True, gross, flat, press, feats))
    return out


# --- Streaming worker (mirrors exploration_exits.run_worker; adds features) -


def run_worker_features(
    worker_id: int,
    home_keys: list[str],
    buffer_keys: list[str],
    creator_hist: dict[str, list[int]],
    *,
    hour_info_fn: Any = _hour_info,
    row_iter_fn: Any = _iter_trades,
    creates_override: dict[str, tuple[_Mint, _Feat]] | None = None,
) -> list[dict[str, Any]]:
    """`hour_info_fn`/`row_iter_fn`/`creates_override` let a second pool with a
    different on-disk layout (e.g. the Oracle live tape in
    tools.oracle_live_adapter / tools.exploration_entry_model_b2) reuse this
    exact streaming worker and score_one unchanged. Every default reproduces
    the original fast-box-only behavior exactly, so the existing pool-A
    tests and reports are untouched.
    """
    os.nice(19)
    home_hours = [hour_info_fn(k) for k in home_keys]
    buffer_hours = [hour_info_fn(k) for k in buffer_keys]
    hours = home_hours + buffer_hours
    creates = creates_override if creates_override is not None else _load_creates_full(home_hours)
    hot: dict[str, _Mint] = {mid: m for mid, (m, _f) in creates.items()}
    feat: dict[str, _Feat] = {mid: f for mid, (_m, f) in creates.items()}
    watch: dict[str, _Mint] = {}
    curve = _curve()
    out: list[dict[str, Any]] = []
    now_ms = 0
    scored = 0

    def score_and_collect(mint_id: str, mint: _Mint, through_ms: int) -> None:
        nonlocal scored
        if mint.mig_slot is None or mint.mig_done:
            return
        out.extend(score_one(mint_id, mint, feat.get(mint_id), curve, through_ms, creator_hist))
        mint.mig_done = True
        scored += 1

    def flush(now_ms_local: int, final: bool) -> None:
        for mint_id, mint in list(hot.items()):
            if mint.mig_slot is not None:
                if final:
                    continue
                mint.drop_before(mint.mig_slot)
                continue
            if final or now_ms_local >= mint.block_ms + WINDOW_MS:
                mint.release()
                watch[mint_id] = mint
                hot.pop(mint_id, None)
        if final:
            for mint_id, mint in list(hot.items()):
                if mint.mig_slot is not None and not mint.mig_done:
                    score_and_collect(mint_id, mint, now_ms_local)
            for mint_id, mint in list(watch.items()):
                if mint.mig_slot is not None and not mint.mig_done:
                    score_and_collect(mint_id, mint, now_ms_local)

    lines = 0
    for hour in hours:
        print(
            f"[w{worker_id}] hour={hour['hour']} hot={len(hot)} watch={len(watch)} scored={scored} rss_mb={_rss_mb()}",
            file=sys.stderr,
            flush=True,
        )
        for row in row_iter_fn(hour["trade"]):
            lines += 1
            mint_id = row.get("mint")
            if not isinstance(mint_id, str):
                continue
            mint = hot.get(mint_id)
            watching = False
            if mint is None:
                mint = watch.get(mint_id)
                watching = mint is not None
            if mint is None:
                continue
            block = row.get("block_time")
            if not isinstance(block, int):
                continue
            if row.get("t_recv_ms") is None:
                row["t_recv_ms"] = block * 1000
            t_ms = row["t_recv_ms"]
            # Feature accumulation: only while this mint has not migrated yet
            # (mig_slot is None) and never for a watched mint (matches the
            # exit-scoring stream below, which likewise only feeds pumpswap
            # prints to a watched mint) -- so every event that ever reaches
            # a _Feat is, by construction of this forward-time stream,
            # strictly before the eventual migrate trigger.
            if not watching and mint.mig_slot is None and row.get("venue") == "pump_bonding":
                f = feat.get(mint_id)
                if f is not None:
                    f.record(row, t_ms)
            parsed = print_from_trade_row(row)
            if parsed is None:
                continue
            _name, pr = parsed
            now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
            if watching:
                if pr.venue != "pumpswap" or not mint.had_bond or mint.mig_done:
                    continue
                mint.add(pr)
                watch.pop(mint_id, None)
                hot[mint_id] = mint
            else:
                mint.add(pr)
            if lines % 300_000 == 0:
                flush(now_ms, False)
        flush(max(now_ms, int(hour["end"]) * 1000), False)
        _trim_heap()
    flush(now_ms, True)
    print(f"[w{worker_id}] done lines={lines} scored={scored} rows={len(out)}", file=sys.stderr, flush=True)
    return out


def run_all_features(max_workers: int = 3, buffer_hours: int = 2) -> list[dict[str, Any]]:
    print("building creator prior-mint history over the whole pool...", file=sys.stderr, flush=True)
    creator_hist = build_creator_history()
    print(f"creator_history creators={len(creator_hist)}", file=sys.stderr, flush=True)
    plan = plan_workers(max_workers, buffer_hours)
    print(f"hours_read={POOL_HOURS}", file=sys.stderr, flush=True)
    print(f"worker_plan={[(i, h[0], h[-1], b) for i, h, b in plan]}", file=sys.stderr, flush=True)
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for worker_id, home, buf in plan:
            rows.extend(run_worker_features(worker_id, home, buf, creator_hist))
        return rows
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=len(plan)) as pool:
        results = pool.starmap(run_worker_features, [(i, h, b, creator_hist) for i, h, b in plan])
    for part in results:
        rows.extend(part)
    return rows


# --- Model training: leave-one-day-out, <=3 fixed settings -----------------

SETTINGS = [
    {"id": "lgb_shallow", "kind": "lightgbm", "num_leaves": 7, "min_data_in_leaf": 40, "learning_rate": 0.05, "rounds": 50},
    {"id": "lgb_medium", "kind": "lightgbm", "num_leaves": 15, "min_data_in_leaf": 20, "learning_rate": 0.05, "rounds": 100},
    {"id": "logreg_l2", "kind": "logreg", "C": 0.5},
]
assert len(SETTINGS) <= 3


def _vector(feats: dict[str, float]) -> list[float]:
    return [float(feats.get(name, 0.0)) for name in FEATURE_NAMES]


def _train_lightgbm(x: Sequence[Sequence[float]], y: Sequence[int], setting: dict[str, Any]) -> Any:
    import lightgbm as lgb
    import numpy as np

    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.int32)
    pos = int(ya.sum())
    neg = len(ya) - pos
    scale = (neg / pos) if pos else 1.0
    train = lgb.Dataset(xa, label=ya, feature_name=list(FEATURE_NAMES))
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_threads": 1,
        "verbosity": -1,
        "learning_rate": setting["learning_rate"],
        "num_leaves": setting["num_leaves"],
        "min_data_in_leaf": setting["min_data_in_leaf"],
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "scale_pos_weight": scale,
        "seed": SEED,
        "deterministic": True,
        "force_row_wise": True,
    }
    return lgb.train(params, train, num_boost_round=setting["rounds"])


def _train_logreg(x: Sequence[Sequence[float]], y: Sequence[int], setting: dict[str, Any]) -> tuple[Any, Any, Any]:
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    xa = np.asarray(x, dtype=np.float64)
    mean = xa.mean(axis=0)
    std = xa.std(axis=0)
    std[std == 0] = 1.0
    xs = (xa - mean) / std
    model = LogisticRegression(C=setting["C"], penalty="l2", class_weight="balanced", max_iter=2000, random_state=SEED)
    model.fit(xs, np.asarray(y, dtype=np.int32))
    return model, mean, std


def fit_setting(x: Sequence[Sequence[float]], y: Sequence[int], setting: dict[str, Any]) -> dict[str, Any]:
    if setting["kind"] == "lightgbm":
        model = _train_lightgbm(x, y, setting)
        return {"kind": "lightgbm", "model": model}
    model, mean, std = _train_logreg(x, y, setting)
    return {"kind": "logreg", "model": model, "mean": mean, "std": std}


def predict_setting(fit: dict[str, Any], x: Sequence[Sequence[float]]) -> list[float]:
    import numpy as np

    xa = np.asarray(x, dtype=np.float64)
    if fit["kind"] == "lightgbm":
        return [float(v) for v in fit["model"].predict(xa)]
    xs = (xa - fit["mean"]) / fit["std"]
    proba = fit["model"].predict_proba(xs)
    return [float(v) for v in proba[:, 1]]


def importance_setting(fit: dict[str, Any]) -> list[tuple[str, float]]:
    if fit["kind"] == "lightgbm":
        gain = fit["model"].feature_importance(importance_type="gain")
        pairs = list(zip(FEATURE_NAMES, (float(v) for v in gain)))
    else:
        coef = fit["model"].coef_[0]
        pairs = list(zip(FEATURE_NAMES, (abs(float(v)) for v in coef)))
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    return pairs


# --- Cohort stats and the leave-one-day-out report --------------------------


def _cohort_stats(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "gross_mean_pct": None, "flat_net_mean_pct": None, "press_net_mean_pct": None}
    gross = sum(r["gross"] for r in rows) / n / ENTRY_SIZE * 100.0
    flat = sum(r["flat"] for r in rows) / n / ENTRY_SIZE * 100.0
    press = sum(r["press"] for r in rows) / n / ENTRY_SIZE * 100.0
    return {"n": n, "gross_mean_pct": gross, "flat_net_mean_pct": flat, "press_net_mean_pct": press}


def evaluate_fold(rows_train: Sequence[dict[str, Any]], rows_test: Sequence[dict[str, Any]], setting: dict[str, Any]) -> dict[str, Any]:
    x_train = [_vector(r["features"]) for r in rows_train]
    y_train = [1 if r["press"] > 0 else 0 for r in rows_train]
    if len(set(y_train)) < 2 or len(x_train) < 20:
        return {"trained": False}
    fit = fit_setting(x_train, y_train, setting)
    x_test = [_vector(r["features"]) for r in rows_test]
    scores = predict_setting(fit, x_test)
    order = sorted(range(len(rows_test)), key=lambda i: scores[i], reverse=True)
    ranked = [rows_test[i] for i in order]
    n = len(ranked)
    cohorts = {"all": _cohort_stats(ranked)}
    for pct in (10, 20, 30):
        k = max(1, round(n * pct / 100.0))
        cohorts[f"top{pct}"] = _cohort_stats(ranked[:k])
    return {
        "trained": True,
        "n_train": len(rows_train),
        "n_test": n,
        "cohorts": cohorts,
        "importance": importance_setting(fit)[:8],
    }


def leave_one_day_out(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """rows: one exit's scored attempts, MISS included (DEC-007, no survivorship)."""
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in DAYS}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    out: dict[str, Any] = {}
    for setting in SETTINGS:
        out[setting["id"]] = {}
        for held_out in DAYS:
            train = [r for d in DAYS if d != held_out for r in by_day.get(d, [])]
            test = by_day.get(held_out, [])
            out[setting["id"]][held_out] = evaluate_fold(train, test, setting)
    return out


def choose_best_setting(lodo: dict[str, dict[str, dict[str, Any]]]) -> tuple[str, float]:
    """Mean(top10 press% - all press%) across the exits' held-out days.
    Deterministic; documented in the report. Ties broken by setting order.
    """
    best_id, best_lift = None, float("-inf")
    for setting_id, by_exit in lodo.items():
        lifts = []
        for exit_lodo in by_exit.values():
            for fold in exit_lodo.values():
                if not fold.get("trained"):
                    continue
                top10 = fold["cohorts"]["top10"]["press_net_mean_pct"]
                allc = fold["cohorts"]["all"]["press_net_mean_pct"]
                if top10 is None or allc is None:
                    continue
                lifts.append(top10 - allc)
        mean_lift = sum(lifts) / len(lifts) if lifts else float("-inf")
        if mean_lift > best_lift:
            best_id, best_lift = setting_id, mean_lift
    return best_id, best_lift


# --- Report -------------------------------------------------------------


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%"


def write_report(combined: dict[str, Any], out_md: Path, out_json: Path, wall_s: float, n_rows_by_spec: dict[str, int]) -> None:
    """combined: {spec_id: {setting_id: {day: fold}}}, already computed by
    leave_one_day_out per exit (see main()). choose_best_setting wants the
    transpose, {setting_id: {spec_id: {day: fold}}}.
    """
    by_setting = {s["id"]: {spec_id: combined[spec_id][s["id"]] for spec_id in TARGET_SPEC_IDS} for s in SETTINGS}
    best_id, best_lift = choose_best_setting(by_setting)
    out_json.write_text(
        json.dumps(
            {
                "schema": "exploration_entry_model_v1",
                "pool_start": POOL_START,
                "pool_end": POOL_END,
                "hours_read": POOL_HOURS,
                "target_specs": TARGET_SPEC_IDS,
                "feature_names": FEATURE_NAMES,
                "settings": SETTINGS,
                "n_rows_by_spec": n_rows_by_spec,
                "lodo": combined,
                "best_setting": {"id": best_id, "mean_top10_lift_pct": best_lift},
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
    lines.append('  subagentId: "exploration-entry-model-2026-09-28"')
    lines.append("---")
    lines.append("")
    lines.append("# Exploration: learned migrate entry filter (leave-one-day-out)")
    lines.append("")
    lines.append(
        "**Exploration, not a pre-registered test.** This is a candidate for a later "
        "pre-registered test, never a promote. Three UTC days is a small-sample "
        "warning by itself -- leave-one-day-out here means 3 folds, and the model "
        "picking rule and the feature list were chosen by looking at all three days' "
        "results together, so even the 'held-out' day numbers below carry some of the "
        "same winner's-curse risk as the exit-family scan in "
        "`ARTIFACTS/lab/exploration-exits-2026-09-28.md`. None of this clears the "
        "promotion gate (`ARTIFACTS/lab/migrate-direct-prereg.md`) and none of it is "
        "scored against forward paper."
    )
    lines.append("")
    lines.append("## Data fence and reuse")
    lines.append("")
    lines.append(
        f"Sealed fast-box backfill hours **{POOL_START} through {POOL_END}** ({len(POOL_HOURS)} hours), "
        "the same fence as `tools/exploration_exits.py` (#139), enforced by the same whitelist. "
        "Entry execution (migrate trigger, slot+1, direct, 0.0005 SOL/side, 0.5 SOL) and the two "
        "exit rules (`tpsl_tp50_sl30`, `trail_30_act20`) are reused unchanged from that module -- "
        "only the entry SELECTION (which migrations to take) is new here."
    )
    lines.append("")
    lines.append("## Features (knowable at decision time T, the migrate trigger)")
    lines.append("")
    lines.append(
        "Pre-migration curve stats are a running, causal accumulation over pump_bonding buy/sell "
        "events strictly before the migrate trigger's receive time -- see `compute_features` and "
        "`causal_events` in `tools/exploration_entry_model.py`, and the no-lookahead test in "
        "`tools/test_exploration_entry_model.py`, which shuffles extra events at or after the "
        "cutoff into the input and checks the features never move. `same_slot_buys` / "
        "`nearby_buy_sol` are the one deliberate exception: they reuse the frozen pressure-fail "
        "model's own input (`tools.latency_curve._pressure`), which looks at competing buys up to "
        "the order's own slot+1 landing -- the same causal boundary that model already uses and "
        "this repo has already reviewed. `creator_prior_mints_24h` is EXP-009's G1 definition, "
        "recomputed here for this pool only and left-censored at the pool start. `top_holder_share` "
        "nets buy/sell token amounts per wallet seen on the DEX tape; it has no visibility into "
        "wallet-to-wallet transfers, so it is a proxy, not a resolved cap table."
    )
    lines.append("")
    lines.append("Feature list: `" + "`, `".join(FEATURE_NAMES) + "`")
    lines.append("")
    lines.append("## Settings (<=3, all reported; no other hyperparameter search)")
    lines.append("")
    for s in SETTINGS:
        lines.append(f"- `{s['id']}`: {json.dumps({k: v for k, v in s.items() if k != 'id'})}")
    lines.append("")
    lines.append(
        f"Rows per exit (full pool, MISS included, no survivorship / DEC-007): "
        + ", ".join(f"`{sid}`={n_rows_by_spec.get(sid, 0)}" for sid in TARGET_SPEC_IDS)
    )
    lines.append("")
    lines.append(
        f"**Best setting by the mean(top10% press-net% - all press-net%) lift across both exits' "
        f"3 held-out days:** `{best_id}` (mean lift {best_lift:+.2f} pct pts). This selection rule "
        "is fixed in code (`choose_best_setting`), not picked after seeing which one looked best."
    )
    lines.append("")
    for spec_id in TARGET_SPEC_IDS:
        lines.append(f"## `{spec_id}`: held-out-day table, setting `{best_id}`")
        lines.append("")
        lines.append("| Held-out day | Cohort | n | Gross % | Flat net % | Pressure net % |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        by_exit = combined[spec_id][best_id]
        for day in DAYS:
            fold = by_exit.get(day, {})
            if not fold.get("trained"):
                lines.append(f"| {day} | (not trained -- too few rows) |  |  |  |  |")
                continue
            for cohort_name in ("all", "top10", "top20", "top30"):
                c = fold["cohorts"][cohort_name]
                lines.append(
                    f"| {day} | {cohort_name} | {c['n']} | {_fmt_pct(c['gross_mean_pct'])} | "
                    f"{_fmt_pct(c['flat_net_mean_pct'])} | {_fmt_pct(c['press_net_mean_pct'])} |"
                )
        lines.append("")
        lines.append(f"### `{spec_id}` top features, setting `{best_id}` (mean gain/coef across the 3 folds)")
        lines.append("")
        agg: dict[str, list[float]] = {}
        top5_by_day: dict[str, list[str]] = {}
        for day in DAYS:
            fold = by_exit.get(day, {})
            if not fold.get("trained"):
                continue
            names = [name for name, _v in fold["importance"]]
            top5_by_day[day] = names[:5]
            for name, val in fold["importance"]:
                agg.setdefault(name, []).append(val)
        ranked = sorted(agg.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        lines.append("| Feature | Mean importance | Days in its top-5 |")
        lines.append("| --- | ---: | --- |")
        for name, vals in ranked[:8]:
            days_in_top5 = [d for d, top5 in top5_by_day.items() if name in top5]
            lines.append(f"| `{name}` | {sum(vals) / len(vals):.3g} | {', '.join(days_in_top5) if days_in_top5 else '-'} |")
        lines.append("")
    lines.append("## All settings, all exits (for comparison)")
    lines.append("")
    for spec_id in TARGET_SPEC_IDS:
        for setting in SETTINGS:
            sid = setting["id"]
            lines.append(f"### `{spec_id}` / `{sid}`")
            lines.append("")
            lines.append("| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
            for day in DAYS:
                fold = combined[spec_id][sid].get(day, {})
                if not fold.get("trained"):
                    lines.append(f"| {day} | not trained |  |  |  |  |")
                    continue
                c = fold["cohorts"]
                lines.append(
                    f"| {day} | {c['all']['n']} | {_fmt_pct(c['all']['press_net_mean_pct'])} | "
                    f"{_fmt_pct(c['top10']['press_net_mean_pct'])} | {_fmt_pct(c['top20']['press_net_mean_pct'])} | "
                    f"{_fmt_pct(c['top30']['press_net_mean_pct'])} |"
                )
            lines.append("")
    lines.append(f"Wall time: {wall_s:.0f}s. Full grid: `exploration-entry-model-2026-09-28.json`.")
    lines.append("")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Exploration: learned migrate entry filter (leave-one-day-out)")
    parser.add_argument("--out-md", type=Path, default=Path("ARTIFACTS/lab/exploration-entry-model-2026-09-28.md"))
    parser.add_argument("--out-json", type=Path, default=Path("ARTIFACTS/lab/exploration-entry-model-2026-09-28.json"))
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--buffer-hours", type=int, default=2)
    args = parser.parse_args()
    t0 = time.time()
    rows = run_all_features(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    by_spec: dict[str, list[dict[str, Any]]] = {sid: [] for sid in TARGET_SPEC_IDS}
    for r in rows:
        by_spec.setdefault(r["spec"], []).append(r)
    n_rows_by_spec = {sid: len(by_spec.get(sid, [])) for sid in TARGET_SPEC_IDS}
    combined = {sid: leave_one_day_out(by_spec.get(sid, [])) for sid in TARGET_SPEC_IDS}
    wall_s = time.time() - t0
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    write_report(combined, args.out_md, args.out_json, wall_s, n_rows_by_spec)
    print(f"wrote {args.out_md} and {args.out_json} in {wall_s:.0f}s", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
