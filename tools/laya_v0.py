#!/usr/bin/env python3
"""LAYA v0: paper entry and exit models on the pump trade tape.

LightGBM (sklearn HistGradientBoosting if the wheel is missing) scores a
numeric packet at fixed times after create. Features read only tape rows
with t_recv_ms <= the decision time, plus the create payload. Labels are
the PR #76 paper fill at decision time + 1s under the exit grid. The
primary exit rule is chosen on each training fold only.

Walk-forward splits are time-ordered. The daily job retrains on the tape
it can see and writes a scoreboard. It does not touch the recorder.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    DEFAULT_SLIPPAGE_CAP,
    INITIAL_REAL_TOKEN_UI,
    LAMPORTS_PER_SOL,
    PRIORITY_FEE_LAMPORTS,
    TOKEN_ACCOUNT_RENT_LAMPORTS,
    TOKEN_SCALE,
    bonding_real_tokens,
    market_cap_sol,
    quote_sell,
)
from tools.paper_price_path import (
    CreateSignal,
    MintPath,
    ScanStats,
    TapePrint,
    load_creates,
    open_text,
    print_from_trade_row,
    state_as_of,
)
from tools.paper_tape_scoreboard import (
    EXIT_RULES,
    ExitRule,
    _plan_exit,
    filter_window,
    simulate_exit,
    try_entry,
)

SCHEMA = "laya_v0"
DECISION_OFFSETS_MS = (5_000, 15_000, 30_000, 60_000, 120_000)
ENTRY_LATENCY_MS = 1_000
TRADE_TRIGGER_BUYERS = 8
TRADE_TRIGGER_NEAR_MS = 2_000
CURVE_LEVELS = (0.20, 0.40, 0.60, 0.80)
BUYER_TRIGGER_NS = (5, 10, 20)
BARRIER_HORIZON_MS = 1_800_000
# Upside before downside, within 30 minutes of the fill. Label only.
BARRIERS = (
    ("hit_100_30", 1.00, 0.30, "ladder_2x_t30"),
    ("hit_50_25", 0.50, 0.25, "ladder_1_5x_t25"),
)
VELOCITY_MS = 5_000
SNIPER_SLOT_DELTA = 2
CREATOR_HORIZON_MS = 30_000
CREATOR_RUG_RATIO = 0.5
CREATOR_PRIOR_CAP = 20
EXIT_TICK_OFFSETS_MS = (5_000, 15_000, 30_000, 60_000, 90_000, 120_000)
EXIT_TICK_CAP = 8
MIN_RULE_N = 30
SCORE_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)
TOP_FRACTIONS = (0.01, 0.05, 0.10, 0.20, 1.0)
EXIT_TAKE_P = 0.5
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1
WINSOR_P = 0.01
QUOTE_WSOL_LIVE_AT = "2026-09-25T08:37:00Z"
PROMOTION_MIN_N = 100
PROMOTION_MIN_DAYS = 5
PROMOTION_DROP_N = 3
# Exploratory search through the 15:18Z scoreboard is not a holdout.
# Ranked candidates are fit only on decisions at or before this instant.
CANDIDATE_FREEZE_AT = "2026-09-25T15:30:00Z"
CANDIDATE_FREEZE_MS = 1_790_350_200_000
PROMOTION_RULE = (
    f"at least {PROMOTION_MIN_N} out-of-sample trades, "
    f"at least {PROMOTION_MIN_DAYS} distinct UTC days with a majority of those days positive, "
    "lower 90% CI bound of mean SOL per trade > 0, "
    f"and total SOL still positive after removing the top {PROMOTION_DROP_N} trades"
)
LATENCY_DRAW_SEED = 1
CHAIN_LAG_MIN_MS = -5_000
CHAIN_LAG_MAX_MS = 120_000
# Pre-registered after the 8.3h search. fraction 1 is the whole point, not a top slice.
FROZEN_CANDIDATES = (
    {"id": "buyers_8_top5_ladder_2x", "point": "buyers_8", "fraction": 0.05, "label": "hit_100_30", "pnl_rule": "ladder_2x_t30"},
    {"id": "t30_top1_hold_30s", "point": "30", "fraction": 0.01, "label": "pnl", "pnl_rule": "hold_30s"},
    {"id": "migrate_hold_30s", "point": "migrate", "fraction": 1.0, "label": "none", "pnl_rule": "hold_30s"},
)

BOT_MIN_BUYS = 25
BOT_MIN_MINTS = 8
BOT_SNIPER_SHARE = 0.45
BOT_MEAN_GAP_MS = 2_000
BOT_MIN_GAPS = 20
SNIPER_WALLET_MIN_BUYS = 8
SNIPER_WALLET_SHARE = 0.50
LEADER_MIN_CLOSED = 3
LEADER_MIN_WR = 0.30
LEADER_MAX_WR = 0.75
LEADER_MIN_HOLD_MS = 15_000
LEADER_MAX_HOLD_MS = 30 * 60 * 1000
LEADER_MAX_MINT_SHARE = 0.70
LEADER_MIN_INVESTED = 50_000_000
DUST_TOKEN_RAW = 1

NAN = float("nan")

FEATURE_NAMES: tuple[str, ...] = (
    "f_n_buy",
    "f_n_sell",
    "f_buy_sol",
    "f_sell_sol",
    "f_net_sol",
    "f_unique_buyers",
    "f_unique_sellers",
    "f_unique_wallets",
    "f_buy_n_5s",
    "f_buy_n_prev_5s",
    "f_buy_accel_5s",
    "f_buy_sol_5s",
    "f_buy_sol_prev_5s",
    "f_buy_sol_accel_5s",
    "f_curve_progress",
    "f_migrated",
    "f_ms_from_create",
    "f_sig_n",
    "f_sig_window_ms",
    "f_sig_unique_wallets",
    "f_sig_sniper_share",
    "f_sig_creator_prior_rugs",
    "f_sig_delta_slot_from_create",
    "f_sig_wallet_win_rate",
    "f_sig_wallet_median_hold_ms",
    "f_sig_copyable",
    "f_quote_sol",
    "f_base_ui",
    "f_price_sol",
    "f_price_chg",
    "f_mcap_sol",
    "f_top1_holder_share",
    "f_top5_holder_share",
    "f_sniper_buy_sol_share",
    "f_bundle_buy_sol_share",
    "f_creator_prior_mints",
    "f_creator_prior_scored",
    "f_creator_prior_rug_frac",
    "f_creator_prior_median_ret",
    "f_bot_buy_sol_share",
    "f_veto_buy_sol_share",
    "f_leader_present",
    "f_leader_buyers",
    "f_leader_buy_sol_share",
    "f_hour_sin",
    "f_hour_cos",
    "f_offset_s",
    "f_trigger_grid",
    "f_create_sol",
    "f_create_mcap_sol",
)

EXIT_FEATURE_NAMES: tuple[str, ...] = (
    "x_time_in_trade_s",
    "x_unrealized_ret",
    "x_peak_ret",
    "x_drawdown",
    "x_buy_sol_since",
    "x_sell_sol_since",
    "x_unique_buyers_since",
    "x_curve_progress",
    "x_price_chg_since_entry",
    "x_offset_s",
    "x_entry_unique_buyers",
    "x_entry_top1",
    "x_entry_price_chg",
)


@dataclass(frozen=True, slots=True)
class FlowPrint:
    t_recv_ms: int
    slot: int
    event_index: int
    venue: str
    side: str
    sol_lamports: int
    token_raw: int
    trader: str | None
    quote_reserve: int
    base_reserve: int
    price_sol: float
    market_cap_sol: float

    def to_tape(self) -> TapePrint:
        return TapePrint(
            t_recv_ms=self.t_recv_ms,
            slot=self.slot,
            event_index=self.event_index,
            venue=self.venue,
            side=self.side,
            sol_lamports=self.sol_lamports,
            quote_reserve=self.quote_reserve,
            base_reserve=self.base_reserve,
            price_sol=self.price_sol,
            market_cap_sol=self.market_cap_sol,
        )


@dataclass
class MintBook:
    create: CreateSignal
    flow: list[FlowPrint]
    path: MintPath = field(init=False)

    def __post_init__(self) -> None:
        self.path = MintPath(create=self.create, prints=[p.to_tape() for p in self.flow])


@dataclass
class DecisionRow:
    mint: str
    creator: str | None
    create_t_ms: int
    decision_t_ms: int
    trigger: str
    features: dict[str, float]
    pnl_by_rule: dict[str, int | None] = field(default_factory=dict)
    exit_status_by_rule: dict[str, str] = field(default_factory=dict)
    exit_t_by_rule: dict[str, int | None] = field(default_factory=dict)
    entry_status: str = ""
    entry_t_ms: int = 0
    barrier: dict[str, int | None] = field(default_factory=dict)

    def point_id(self) -> str:
        if self.trigger == "grid":
            return str(int(round((self.decision_t_ms - self.create_t_ms) / 1000)))
        return self.trigger


@dataclass(frozen=True)
class LadderRule:
    """Scale out at a spot multiple, then trail the rest. Hard stop and a 30-minute cap."""

    rule_id: str
    scale_ret: float
    scale_frac: float
    trail: float
    hard_sl: float
    max_hold_ms: int = BARRIER_HORIZON_MS


LADDER_RULES: tuple[LadderRule, ...] = (
    LadderRule("ladder_2x_t30", scale_ret=1.00, scale_frac=0.5, trail=0.30, hard_sl=0.30),
    LadderRule("ladder_1_5x_t25", scale_ret=0.50, scale_frac=0.5, trail=0.25, hard_sl=0.25),
)


@dataclass
class ExitTick:
    mint: str
    decision_t_ms: int
    tick_t_ms: int
    features: dict[str, float]
    pnl_now: int | None


def _sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


def _is_nan(value: float) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _median(values: Sequence[int]) -> float:
    return float(statistics.median(values))


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def flow_as_of(flow: Sequence[FlowPrint], t_ms: int) -> list[FlowPrint]:
    """Prints with receive time <= t. `flow` is sorted by receive time."""
    lo, hi = 0, len(flow)
    while lo < hi:
        mid = (lo + hi) // 2
        if flow[mid].t_recv_ms <= t_ms:
            lo = mid + 1
        else:
            hi = mid
    return list(flow[:lo])


def flow_from_row(row: dict[str, Any]) -> tuple[str, FlowPrint] | None:
    parsed = print_from_trade_row(row)
    if parsed is None:
        return None
    mint, tape = parsed
    trader = row.get("trader")
    if not isinstance(trader, str) or not trader or trader == "UNK":
        trader = None
    try:
        token_raw = int(row.get("token_raw") or 0)
    except (TypeError, ValueError):
        token_raw = 0
    return mint, FlowPrint(
        t_recv_ms=tape.t_recv_ms,
        slot=tape.slot,
        event_index=tape.event_index,
        venue=tape.venue,
        side=tape.side,
        sol_lamports=tape.sol_lamports,
        token_raw=max(0, token_raw),
        trader=trader,
        quote_reserve=tape.quote_reserve,
        base_reserve=tape.base_reserve,
        price_sol=tape.price_sol,
        market_cap_sol=tape.market_cap_sol,
    )


def _dedupe_key(pr: FlowPrint) -> tuple[Any, ...]:
    return (
        pr.t_recv_ms,
        pr.slot,
        pr.event_index,
        pr.venue,
        pr.trader,
        pr.side,
        pr.sol_lamports,
        pr.token_raw,
        pr.quote_reserve,
        pr.base_reserve,
    )


def _dedupe_sorted(prints: list[FlowPrint]) -> list[FlowPrint]:
    prints.sort(key=lambda p: (p.t_recv_ms, p.slot, p.event_index, p.trader or "", p.side))
    out: list[FlowPrint] = []
    prev: tuple[Any, ...] | None = None
    for pr in prints:
        key = _dedupe_key(pr)
        if key == prev:
            continue
        prev = key
        out.append(pr)
    return out


def chain_lag_ms(row: dict[str, Any]) -> int | None:
    """chain→receive, same bounds as the forward paper meter. None if event_ts is missing."""
    t_raw = row.get("t_recv_ms")
    raw = row.get("event_ts")
    if isinstance(t_raw, bool) or not isinstance(t_raw, (int, float)):
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    whole = int(raw)
    if whole < 1_000_000_000:
        return None
    lag = int(t_raw) - whole * 1000
    if lag < CHAIN_LAG_MIN_MS or lag > CHAIN_LAG_MAX_MS:
        return None
    return lag


def load_books(
    creates: dict[str, CreateSignal],
    tape_paths: Iterable[Path],
) -> tuple[dict[str, MintBook], ScanStats]:
    buckets: dict[str, list[FlowPrint]] = {mint: [] for mint in creates}
    stats = ScanStats()
    lags: list[int] = []
    for path in tape_paths:
        with open_text(path) as fh:
            for line in fh:
                stats.lines += 1
                if stats.lines % 250_000 == 0:
                    print(f"tape_lines={stats.lines} kept={stats.kept}", file=sys.stderr)
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    stats.bad_json += 1
                    continue
                if not isinstance(row, dict):
                    stats.bad_json += 1
                    continue
                t_raw = row.get("t_recv_ms")
                if isinstance(t_raw, int):
                    stats.observe_t(t_raw)
                lag = chain_lag_ms(row)
                if lag is not None:
                    lags.append(lag)
                if row.get("quote_is_wsol") is False:
                    stats.non_wsol += 1
                    continue
                mint = row.get("mint")
                if mint not in buckets:
                    if not mint or row.get("mint_source") == "unresolved":
                        stats.unresolved += 1
                    else:
                        stats.other_mint += 1
                    continue
                parsed = flow_from_row(row)
                if parsed is None:
                    stats.skipped_kept_mint += 1
                    continue
                _, pr = parsed
                buckets[mint].append(pr)
                stats.kept += 1
    books: dict[str, MintBook] = {}
    for mint, create in creates.items():
        books[mint] = MintBook(create=create, flow=_dedupe_sorted(buckets[mint]))
    stats.chain_lags_ms = lags  # type: ignore[attr-defined]
    return books, stats


def decision_times(book: MintBook, *, tape_end_ms: int, offsets_ms: Sequence[int]) -> list[tuple[int, str]]:
    t0 = book.create.t_signal_ms
    out: list[tuple[int, str]] = []
    grid: list[int] = []
    for off in offsets_ms:
        t_ms = t0 + off
        if t_ms <= tape_end_ms:
            out.append((t_ms, "grid"))
            grid.append(t_ms)
    buyers: set[str] = set()
    for pr in book.flow:
        if pr.t_recv_ms > tape_end_ms:
            break
        if pr.side != "buy" or not pr.trader:
            continue
        buyers.add(pr.trader)
        if len(buyers) < TRADE_TRIGGER_BUYERS:
            continue
        t_ms = pr.t_recv_ms
        if t_ms < t0 + offsets_ms[0]:
            continue
        if t_ms > t0 + offsets_ms[-1]:
            break
        if all(abs(t_ms - g) > TRADE_TRIGGER_NEAR_MS for g in grid):
            out.append((t_ms, "buyers_8"))
        break
    for pr in book.flow:
        if pr.t_recv_ms > tape_end_ms:
            break
        if pr.venue == "pumpswap" and pr.t_recv_ms >= t0:
            out.append((pr.t_recv_ms, "migrate"))
            break
    out.extend(_curve_decision_times(book, tape_end_ms=tape_end_ms))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _curve_decision_times(book: MintBook, *, tape_end_ms: int) -> list[tuple[int, str]]:
    """First receive time at which curve progress is at or above each level."""
    t0 = book.create.t_signal_ms
    crossed: set[int] = set()
    out: list[tuple[int, str]] = []
    points: list[tuple[int, str, int]] = []
    anchor = book.path.anchor()
    if anchor is not None and anchor.t_recv_ms <= t0:
        points.append((t0, anchor.venue, anchor.base_reserve))
    for pr in book.flow:
        if pr.t_recv_ms > tape_end_ms:
            break
        if pr.t_recv_ms < t0:
            continue
        points.append((pr.t_recv_ms, pr.venue, pr.base_reserve))
    for t_ms, venue, base in points:
        progress = _curve_progress(venue, base)
        if progress != progress:
            continue
        for level in CURVE_LEVELS:
            mark = int(round(level * 100))
            if mark in crossed or progress < level:
                continue
            crossed.add(mark)
            out.append((t_ms, f"curve_{mark}"))
    return out


def causal_buyer_triggers(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    ns: Sequence[int] = BUYER_TRIGGER_NS,
) -> dict[str, list[tuple[int, str]]]:
    """First time unique buyers who are not vetoed as of that print reach each N.

    Veto matches the feature: bot, sniper wallet, or creator, updated only by
    prints at or before this one.
    """
    events: list[tuple[int, int, str, Any]] = []
    for mint, book in books.items():
        for pr in book.flow:
            if pr.t_recv_ms > tape_end_ms:
                continue
            events.append((pr.t_recv_ms, 0, mint, pr))
        if book.create.creator:
            events.append((book.create.t_signal_ms, -1, mint, book.create.creator))
    events.sort(key=lambda item: (item[0], item[1], item[2]))
    wallets: dict[str, _Wallet] = {}
    bots: set[str] = set()
    snipers: set[str] = set()
    creators: set[str] = set()
    first_print_slot: dict[str, int] = {}
    seen: dict[str, set[str]] = defaultdict(set)
    fired: dict[str, set[int]] = defaultdict(set)
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    levels = tuple(sorted(ns))
    for t_ms, kind, mint, payload in events:
        if kind == -1:
            creators.add(payload)
            continue
        pr: FlowPrint = payload
        if mint not in first_print_slot:
            first_print_slot[mint] = pr.slot
        if not pr.trader:
            continue
        wallet = wallets.get(pr.trader)
        if wallet is None:
            wallet = _Wallet()
            wallets[pr.trader] = wallet
        was_bot, was_sniper = wallet.is_bot, wallet.is_sniper
        wallet.observe(
            mint=mint,
            side=pr.side,
            sol=pr.sol_lamports,
            token_raw=pr.token_raw,
            t_ms=t_ms,
            slot=pr.slot,
            first_slot=first_print_slot[mint],
        )
        _move(bots, pr.trader, was_bot, wallet.is_bot)
        _move(snipers, pr.trader, was_sniper, wallet.is_sniper)
        if pr.side != "buy":
            continue
        if pr.trader in bots or pr.trader in snipers or pr.trader in creators:
            continue
        if pr.trader in seen[mint]:
            continue
        seen[mint].add(pr.trader)
        n_clean = len(seen[mint])
        for level in levels:
            if level in fired[mint] or n_clean < level:
                continue
            fired[mint].add(level)
            out[mint].append((t_ms, f"buyers_nv{level}"))
    return out


def _window_buys(flow: Sequence[FlowPrint], t0: int, t1: int) -> tuple[int, int]:
    """Buys with t in (t0, t1]."""
    n = 0
    sol = 0
    for pr in flow:
        if pr.t_recv_ms <= t0:
            continue
        if pr.t_recv_ms > t1:
            break
        if pr.side == "buy":
            n += 1
            sol += pr.sol_lamports
    return n, sol


def _curve_progress(venue: str, base_raw: int) -> float:
    if venue == "pumpswap":
        return 1.0
    initial = INITIAL_REAL_TOKEN_UI * TOKEN_SCALE
    if initial <= 0:
        return NAN
    real = bonding_real_tokens(base_raw)
    return max(0.0, min(1.0, 1.0 - (real / initial)))


def _state_at(book: MintBook, t_ms: int) -> tuple[float, int, int, str, float] | None:
    seen = flow_as_of(book.flow, t_ms)
    if seen:
        last = seen[-1]
        return last.price_sol, last.quote_reserve, last.base_reserve, last.venue, last.market_cap_sol
    anchor = book.path.anchor()
    if anchor is not None and anchor.t_recv_ms <= t_ms:
        return anchor.price_sol, anchor.quote_reserve, anchor.base_reserve, anchor.venue, anchor.market_cap_sol
    return None


def local_features(book: MintBook, t_ms: int, trigger: str) -> dict[str, float]:
    """Packet from this mint only. Ignores prints after t_ms."""
    seen = flow_as_of(book.flow, t_ms)
    n_buy = n_sell = 0
    buy_sol = sell_sol = 0
    buyers: set[str] = set()
    sellers: set[str] = set()
    wallets: set[str] = set()
    bal: dict[str, int] = defaultdict(int)
    slot_buyers: dict[int, set[str]] = defaultdict(set)
    slot_buy_sol: dict[int, int] = defaultdict(int)
    first_slot: int | None = None
    first_price: float | None = None
    creator = book.create.creator
    nc_buy_sol = 0
    nc_sniper_sol = 0
    organic_n = 0
    organic_wallets: set[str] = set()
    for pr in seen:
        if first_slot is None:
            first_slot = pr.slot
        if first_price is None and pr.price_sol > 0:
            first_price = pr.price_sol
        if pr.trader:
            wallets.add(pr.trader)
        if pr.side == "sell":
            n_sell += 1
            sell_sol += pr.sol_lamports
            if pr.trader:
                sellers.add(pr.trader)
                bal[pr.trader] -= pr.token_raw
        else:
            n_buy += 1
            buy_sol += pr.sol_lamports
            sniper = first_slot is not None and pr.slot <= first_slot + SNIPER_SLOT_DELTA
            own = bool(creator) and pr.trader == creator
            if not own:
                nc_buy_sol += pr.sol_lamports
                if sniper:
                    nc_sniper_sol += pr.sol_lamports
            if pr.t_recv_ms > t_ms - VELOCITY_MS and not sniper and not own:
                organic_n += 1
                if pr.trader:
                    organic_wallets.add(pr.trader)
            if pr.trader:
                buyers.add(pr.trader)
                bal[pr.trader] += pr.token_raw
                slot_buyers[pr.slot].add(pr.trader)
                slot_buy_sol[pr.slot] += pr.sol_lamports
    buy_n_5, buy_sol_5 = _window_buys(seen, t_ms - VELOCITY_MS, t_ms)
    buy_n_prev, buy_sol_prev = _window_buys(seen, t_ms - 2 * VELOCITY_MS, t_ms - VELOCITY_MS)
    state = _state_at(book, t_ms)
    migrated = 0.0
    if state is None:
        price = quote_sol = base_ui = mcap = curve = NAN
        price_chg = NAN
    else:
        price, quote, base, venue, mcap = state
        migrated = 1.0 if venue == "pumpswap" else 0.0
        quote_sol = _sol(quote)
        base_ui = base / TOKEN_SCALE
        curve = _curve_progress(venue, base)
        start = first_price
        if start is None or start <= 0:
            anchor = book.path.anchor()
            if anchor is not None and anchor.price_sol > 0:
                start = anchor.price_sol
        price_chg = NAN if start is None or start <= 0 or price <= 0 else (price / start - 1.0)
    positive = sorted((v for v in bal.values() if v > 0), reverse=True)
    if positive:
        total_pos = float(sum(positive))
        top1 = positive[0] / total_pos
        top5 = sum(positive[:5]) / total_pos
    else:
        top1 = top5 = NAN
    if buy_sol > 0 and first_slot is not None:
        sniper_sol = 0
        bundle_sol = 0
        for slot, sol in slot_buy_sol.items():
            if slot <= first_slot + SNIPER_SLOT_DELTA:
                sniper_sol += sol
                if len(slot_buyers[slot]) >= 2:
                    bundle_sol += sol
        sniper_share = sniper_sol / buy_sol
        bundle_share = bundle_sol / buy_sol
    else:
        sniper_share = bundle_share = NAN
    sod = (t_ms // 1000) % 86400
    ang = 2.0 * math.pi * sod / 86400
    create = book.create
    return {
        "f_n_buy": float(n_buy),
        "f_n_sell": float(n_sell),
        "f_buy_sol": _sol(buy_sol),
        "f_sell_sol": _sol(sell_sol),
        "f_net_sol": _sol(buy_sol - sell_sol),
        "f_unique_buyers": float(len(buyers)),
        "f_unique_sellers": float(len(sellers)),
        "f_unique_wallets": float(len(wallets)),
        "f_buy_n_5s": float(buy_n_5),
        "f_buy_n_prev_5s": float(buy_n_prev),
        "f_buy_accel_5s": float(buy_n_5 - buy_n_prev),
        "f_buy_sol_5s": _sol(buy_sol_5),
        "f_buy_sol_prev_5s": _sol(buy_sol_prev),
        "f_buy_sol_accel_5s": _sol(buy_sol_5 - buy_sol_prev),
        "f_curve_progress": curve,
        "f_migrated": migrated,
        "f_ms_from_create": float(t_ms - create.t_signal_ms),
        "f_sig_n": float(organic_n),
        "f_sig_window_ms": float(VELOCITY_MS),
        "f_sig_unique_wallets": float(len(organic_wallets)),
        "f_sig_sniper_share": NAN if nc_buy_sol <= 0 else nc_sniper_sol / nc_buy_sol,
        "f_sig_delta_slot_from_create": NAN,
        "f_sig_wallet_win_rate": NAN,
        "f_sig_wallet_median_hold_ms": NAN,
        "f_sig_copyable": 0.0,
        "f_quote_sol": quote_sol,
        "f_base_ui": base_ui,
        "f_price_sol": price,
        "f_price_chg": price_chg,
        "f_mcap_sol": mcap,
        "f_top1_holder_share": top1,
        "f_top5_holder_share": top5,
        "f_sniper_buy_sol_share": sniper_share,
        "f_bundle_buy_sol_share": bundle_share,
        "f_hour_sin": math.sin(ang),
        "f_hour_cos": math.cos(ang),
        "f_offset_s": (t_ms - create.t_signal_ms) / 1000.0,
        "f_trigger_grid": 1.0 if trigger == "grid" else 0.0,
        "f_create_sol": NAN if create.sol_amount is None else float(create.sol_amount),
        "f_create_mcap_sol": NAN if create.mcap_sol is None else float(create.mcap_sol),
    }


def _price_at(book: MintBook, t_ms: int) -> float | None:
    state = _state_at(book, t_ms)
    if state is None or state[0] <= 0:
        return None
    return state[0]


def creator_features(
    book: MintBook,
    t_ms: int,
    by_creator: dict[str, list[MintBook]],
) -> dict[str, float]:
    """Earlier mints by this creator. Outcomes only after their horizon has elapsed."""
    creator = book.create.creator
    empty = {
        "f_creator_prior_mints": 0.0,
        "f_creator_prior_scored": 0.0,
        "f_creator_prior_rug_frac": NAN,
        "f_creator_prior_median_ret": NAN,
        "f_sig_creator_prior_rugs": 0.0,
    }
    if not creator:
        return empty
    priors = [
        other
        for other in by_creator.get(creator, ())
        if other.create.mint != book.create.mint and other.create.t_signal_ms < t_ms
    ]
    if not priors:
        return empty
    priors = priors[-CREATOR_PRIOR_CAP:]
    rets: list[float] = []
    rugs = 0
    for other in priors:
        horizon = other.create.t_signal_ms + CREATOR_HORIZON_MS
        if horizon > t_ms:
            continue
        start = _price_at(other, other.create.t_signal_ms)
        end = _price_at(other, horizon)
        if start is None or end is None or start <= 0:
            continue
        ret = end / start - 1.0
        rets.append(ret)
        if end / start < CREATOR_RUG_RATIO:
            rugs += 1
    scored = len(rets)
    return {
        "f_creator_prior_mints": float(len(priors)),
        "f_creator_prior_scored": float(scored),
        "f_creator_prior_rug_frac": NAN if scored == 0 else rugs / scored,
        "f_creator_prior_median_ret": NAN if scored == 0 else float(statistics.median(rets)),
        "f_sig_creator_prior_rugs": float(rugs),
    }


class _Wallet:
    __slots__ = (
        "n_buys",
        "n_sells",
        "mints",
        "sniper_buys",
        "gap_sum_ms",
        "gap_n",
        "last_t",
        "pos_tokens",
        "pos_cost",
        "pos_open_t",
        "pos_invested",
        "mint_pnl",
        "closed",
        "wins",
        "holds",
        "invested_closed",
        "is_bot",
        "is_sniper",
        "is_leader",
    )

    def __init__(self) -> None:
        self.n_buys = 0
        self.n_sells = 0
        self.mints: set[str] = set()
        self.sniper_buys = 0
        self.gap_sum_ms = 0
        self.gap_n = 0
        self.last_t: int | None = None
        self.pos_tokens: dict[str, int] = {}
        self.pos_cost: dict[str, int] = {}
        self.pos_open_t: dict[str, int] = {}
        self.pos_invested: dict[str, int] = {}
        self.mint_pnl: dict[str, int] = {}
        self.closed = 0
        self.wins = 0
        self.holds: list[int] = []
        self.invested_closed = 0
        self.is_bot = False
        self.is_sniper = False
        self.is_leader = False

    def observe(
        self,
        *,
        mint: str,
        side: str,
        sol: int,
        token_raw: int,
        t_ms: int,
        slot: int,
        first_slot: int,
    ) -> None:
        if self.last_t is not None and t_ms >= self.last_t:
            self.gap_sum_ms += t_ms - self.last_t
            self.gap_n += 1
        self.last_t = t_ms
        self.mints.add(mint)
        if side == "buy":
            self.n_buys += 1
            if slot <= first_slot + SNIPER_SLOT_DELTA:
                self.sniper_buys += 1
            if token_raw > 0:
                self.pos_tokens[mint] = self.pos_tokens.get(mint, 0) + token_raw
                self.pos_cost[mint] = self.pos_cost.get(mint, 0) + sol
                self.pos_invested[mint] = self.pos_invested.get(mint, 0) + sol
                if mint not in self.pos_open_t:
                    self.pos_open_t[mint] = t_ms
        else:
            self.n_sells += 1
            self._on_sell(mint, sol, token_raw, t_ms)
        self._refresh_flags()

    def _on_sell(self, mint: str, sol: int, token_raw: int, t_ms: int) -> None:
        held = self.pos_tokens.get(mint, 0)
        if held <= DUST_TOKEN_RAW or token_raw <= 0:
            return
        sold = min(token_raw, held)
        cost = self.pos_cost.get(mint, 0)
        cost_out = cost * sold // held
        proceeds = sol * sold // token_raw
        pnl = proceeds - cost_out
        self.pos_tokens[mint] = held - sold
        self.pos_cost[mint] = cost - cost_out
        self.mint_pnl[mint] = self.mint_pnl.get(mint, 0) + pnl
        if self.pos_tokens[mint] <= DUST_TOKEN_RAW:
            self.closed += 1
            if self.mint_pnl[mint] > 0:
                self.wins += 1
            self.holds.append(max(0, t_ms - self.pos_open_t.get(mint, t_ms)))
            self.invested_closed += self.pos_invested.get(mint, 0)
            self.pos_tokens.pop(mint, None)
            self.pos_cost.pop(mint, None)
            self.pos_open_t.pop(mint, None)
            self.pos_invested.pop(mint, None)

    def _refresh_flags(self) -> None:
        sniper_share = (self.sniper_buys / self.n_buys) if self.n_buys else 0.0
        mean_gap = (self.gap_sum_ms / self.gap_n) if self.gap_n else 1e12
        self.is_sniper = self.n_buys >= SNIPER_WALLET_MIN_BUYS and sniper_share >= SNIPER_WALLET_SHARE
        fast = self.gap_n >= BOT_MIN_GAPS and mean_gap <= BOT_MEAN_GAP_MS
        snipy = sniper_share >= BOT_SNIPER_SHARE
        self.is_bot = self.n_buys >= BOT_MIN_BUYS and len(self.mints) >= BOT_MIN_MINTS and (fast or snipy)
        self.is_leader = self._leader_ok()

    def _leader_ok(self) -> bool:
        if self.closed < LEADER_MIN_CLOSED or not self.holds:
            return False
        wr = self.wins / self.closed
        if wr < LEADER_MIN_WR or wr > LEADER_MAX_WR:
            return False
        med_hold = statistics.median(self.holds)
        if med_hold < LEADER_MIN_HOLD_MS or med_hold > LEADER_MAX_HOLD_MS:
            return False
        if self.invested_closed < LEADER_MIN_INVESTED:
            return False
        # Open lots are not round-trips. Only mints already closed (no remaining tokens) count.
        positive = [v for mint, v in self.mint_pnl.items() if v > 0 and mint not in self.pos_tokens]
        if not positive:
            return False
        if max(positive) / sum(positive) > LEADER_MAX_MINT_SHARE:
            return False
        return True


class WalletState:
    """Causal wallet flags. The batch scorer and the forward service share this.

    Prints are applied in time order. A decision reads the sets as they stand
    after every print at or before that decision and after no later print.
    """

    def __init__(self) -> None:
        self.wallets: dict[str, _Wallet] = {}
        self.bots: set[str] = set()
        self.snipers: set[str] = set()
        self.leaders: set[str] = set()
        self.creators: set[str] = set()
        self.first_print_slot: dict[str, int] = {}
        self.leaders_peak = 0

    def note_creator(self, creator: str | None) -> None:
        if creator:
            self.creators.add(creator)

    def observe_print(self, mint: str, pr: FlowPrint) -> None:
        if mint not in self.first_print_slot:
            self.first_print_slot[mint] = pr.slot
        if not pr.trader:
            return
        wallet = self.wallets.get(pr.trader)
        if wallet is None:
            wallet = _Wallet()
            self.wallets[pr.trader] = wallet
        was_bot, was_sniper, was_leader = wallet.is_bot, wallet.is_sniper, wallet.is_leader
        wallet.observe(
            mint=mint,
            side=pr.side,
            sol=pr.sol_lamports,
            token_raw=pr.token_raw,
            t_ms=pr.t_recv_ms,
            slot=pr.slot,
            first_slot=self.first_print_slot[mint],
        )
        _move(self.bots, pr.trader, was_bot, wallet.is_bot)
        _move(self.snipers, pr.trader, was_sniper, wallet.is_sniper)
        _move(self.leaders, pr.trader, was_leader, wallet.is_leader)
        if len(self.leaders) > self.leaders_peak:
            self.leaders_peak = len(self.leaders)

    def fill_features(self, book: MintBook, t_ms: int, feats: dict[str, float]) -> None:
        """Write the wallet columns. `book.flow` must already include prints at t_ms."""
        seen = flow_as_of(book.flow, t_ms)
        buy_sol = bot_sol = veto_sol = leader_sol = 0
        leader_buyers: set[str] = set()
        first_leader: tuple[int, int, str] | None = None
        first_leader_slot0 = 0
        for pr in seen:
            if pr.side != "buy":
                continue
            buy_sol += pr.sol_lamports
            if not pr.trader:
                continue
            veto = pr.trader in self.bots or pr.trader in self.snipers or pr.trader in self.creators
            if pr.trader in self.bots:
                bot_sol += pr.sol_lamports
            if veto:
                veto_sol += pr.sol_lamports
            if pr.trader in self.leaders:
                leader_sol += pr.sol_lamports
                leader_buyers.add(pr.trader)
                slot0 = self.first_print_slot.get(book.create.mint, pr.slot)
                if first_leader is None or (pr.t_recv_ms, pr.slot) < (first_leader[0], first_leader[1]):
                    first_leader = (pr.t_recv_ms, pr.slot, pr.trader)
                    first_leader_slot0 = slot0
        if first_leader is None:
            feats["f_sig_delta_slot_from_create"] = NAN
            feats["f_sig_wallet_win_rate"] = NAN
            feats["f_sig_wallet_median_hold_ms"] = NAN
            feats["f_sig_copyable"] = 0.0
        else:
            _t_buy, slot, trader = first_leader
            delta = slot - first_leader_slot0
            wallet = self.wallets[trader]
            feats["f_sig_delta_slot_from_create"] = float(delta)
            feats["f_sig_wallet_win_rate"] = NAN if wallet.closed == 0 else wallet.wins / wallet.closed
            feats["f_sig_wallet_median_hold_ms"] = NAN if not wallet.holds else float(statistics.median(wallet.holds))
            feats["f_sig_copyable"] = 1.0 if delta > SNIPER_SLOT_DELTA else 0.0
        if buy_sol > 0:
            feats["f_bot_buy_sol_share"] = bot_sol / buy_sol
            feats["f_veto_buy_sol_share"] = veto_sol / buy_sol
            feats["f_leader_buy_sol_share"] = leader_sol / buy_sol
        else:
            feats["f_bot_buy_sol_share"] = NAN
            feats["f_veto_buy_sol_share"] = NAN
            feats["f_leader_buy_sol_share"] = NAN
        feats["f_leader_buyers"] = float(len(leader_buyers))
        feats["f_leader_present"] = 1.0 if leader_buyers else 0.0

    def diag(self) -> dict[str, int]:
        return {
            "wallets": len(self.wallets),
            "bots_end": len(self.bots),
            "sniper_wallets_end": len(self.snipers),
            "leaders_end": len(self.leaders),
            "leaders_peak": self.leaders_peak,
            "creators_end": len(self.creators),
        }


def packet_at(
    book: MintBook,
    t_ms: int,
    trigger: str,
    by_creator: dict[str, list[MintBook]],
    wallets: WalletState,
) -> dict[str, float]:
    """Full LAYA v0 packet at one decision. Same columns as the offline builder."""
    feats = local_features(book, t_ms, trigger)
    feats.update(creator_features(book, t_ms, by_creator))
    wallets.fill_features(book, t_ms, feats)
    return feats


def apply_wallet_features(books: dict[str, MintBook], rows: list[DecisionRow]) -> dict[str, int]:
    """Fill bot / veto / leader shares from trades at or before each decision."""
    events: list[tuple[int, int, Any]] = []
    for mint, book in books.items():
        for pr in book.flow:
            events.append((pr.t_recv_ms, 0, (mint, pr)))
        creator = book.create.creator
        if creator:
            events.append((book.create.t_signal_ms, -1, creator))
    for index, row in enumerate(rows):
        events.append((row.decision_t_ms, 1, index))
    events.sort(key=lambda item: (item[0], item[1]))

    state = WalletState()
    for _t_ms, kind, payload in events:
        if kind == -1:
            state.note_creator(payload)
            continue
        if kind == 0:
            mint, pr = payload
            state.observe_print(mint, pr)
            continue
        row = rows[payload]
        state.fill_features(books[row.mint], row.decision_t_ms, row.features)
    return state.diag()


def _move(bucket: set[str], wallet: str, before: bool, after: bool) -> None:
    if after and not before:
        bucket.add(wallet)
    elif before and not after:
        bucket.discard(wallet)


def index_creators(books: dict[str, MintBook]) -> dict[str, list[MintBook]]:
    by: dict[str, list[MintBook]] = defaultdict(list)
    for book in books.values():
        if book.create.creator:
            by[book.create.creator].append(book)
    for lst in by.values():
        lst.sort(key=lambda b: (b.create.t_signal_ms, b.create.mint))
    return by


def build_feature_rows(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
) -> tuple[list[DecisionRow], dict[str, int]]:
    by_creator = index_creators(books)
    buyer_marks = causal_buyer_triggers(books, tape_end_ms=tape_end_ms)
    rows: list[DecisionRow] = []
    for book in books.values():
        marks = list(decision_times(book, tape_end_ms=tape_end_ms, offsets_ms=offsets_ms))
        marks.extend(buyer_marks.get(book.create.mint, []))
        seen_marks: set[tuple[int, str]] = set()
        for t_ms, trigger in sorted(marks):
            if (t_ms, trigger) in seen_marks:
                continue
            seen_marks.add((t_ms, trigger))
            feats = local_features(book, t_ms, trigger)
            feats.update(creator_features(book, t_ms, by_creator))
            rows.append(
                DecisionRow(
                    mint=book.create.mint,
                    creator=book.create.creator,
                    create_t_ms=book.create.t_signal_ms,
                    decision_t_ms=t_ms,
                    trigger=trigger,
                    features=feats,
                )
            )
    wallet_diag = apply_wallet_features(books, rows)
    return rows, wallet_diag


def _ref_feats(row: DecisionRow) -> dict[str, Any]:
    price = row.features.get("f_price_sol")
    if price is None or _is_nan(float(price)) or price <= 0:
        return {"f_tape_last_price_sol": None}
    return {"f_tape_last_price_sol": float(price)}


def barrier_outcome(
    prints: Sequence[TapePrint],
    *,
    entry_t_ms: int,
    entry_spot: float,
    tp: float,
    sl: float,
    horizon_ms: int,
    tape_end_ms: int,
) -> int | None:
    """1 if spot hits +tp before −sl within the horizon. None if the tape ends first."""
    if entry_spot <= 0:
        return None
    deadline = entry_t_ms + horizon_ms
    for pr in prints:
        if pr.t_recv_ms <= entry_t_ms:
            continue
        if pr.t_recv_ms > deadline or pr.t_recv_ms > tape_end_ms:
            break
        if pr.price_sol <= 0:
            continue
        ret = pr.price_sol / entry_spot - 1.0
        if ret >= tp:
            return 1
        if ret <= -sl:
            return 0
    if tape_end_ms < deadline:
        return None
    return 0


def _ladder_legs(
    prints: Sequence[TapePrint],
    *,
    entry_t_ms: int,
    entry_spot: float,
    tokens: int,
    rule: LadderRule,
) -> list[tuple[int, int]]:
    """(fill_time, tokens) for each paper sell. The position is fully scheduled."""
    remaining = tokens
    peak = entry_spot
    scaled = False
    scale_tokens = int(tokens * rule.scale_frac)
    if 0 < scale_tokens < tokens:
        pass
    else:
        scale_tokens = tokens // 2 if tokens >= 2 else tokens
    deadline = entry_t_ms + rule.max_hold_ms
    legs: list[tuple[int, int]] = []
    for pr in prints:
        if pr.t_recv_ms <= entry_t_ms:
            continue
        if pr.t_recv_ms > deadline:
            break
        spot = pr.price_sol
        if spot <= 0:
            continue
        if spot > peak:
            peak = spot
        ret = spot / entry_spot - 1.0
        if not scaled and ret >= rule.scale_ret and remaining > scale_tokens:
            legs.append((pr.t_recv_ms, scale_tokens))
            remaining -= scale_tokens
            scaled = True
        if remaining <= 0:
            break
        if ret <= -rule.hard_sl or (scaled and spot <= peak * (1.0 - rule.trail)):
            legs.append((pr.t_recv_ms, remaining))
            remaining = 0
            break
    if remaining > 0:
        legs.append((deadline, remaining))
    return legs


def _sell_tokens(path: MintPath, entry: Any, t_fill: int, tokens: int) -> int | None:
    state = state_as_of(path, t_fill, allow_anchor=True)
    if state is None or entry.venue is None or tokens <= 0:
        return None
    same = (
        state.t_recv_ms == entry.state_t_ms
        and state.venue == entry.venue
        and state.quote_reserve == entry.quote_reserve
        and state.base_reserve == entry.base_reserve
    )
    if same:
        quote, base_raw, venue = entry.quote_after, entry.base_after, entry.venue
    else:
        quote, base_raw, venue = state.quote_reserve, state.base_reserve, state.venue
    return quote_sell(
        venue=venue,
        tokens_raw=tokens,
        quote_lamports=quote,
        base_raw=base_raw,
        market_cap=market_cap_sol(quote, base_raw),
    )


def simulate_ladder(
    path: MintPath,
    entry: Any,
    rule: LadderRule,
    *,
    latency_ms: int,
    tape_end_ms: int,
    size_lamports: int,
) -> dict[str, Any]:
    """Paper PnL for a scale-out. Legs use the tape reserves at each fill; our trade is not in the tape."""
    empty = {
        "exit_status": "not_entered",
        "trigger": None,
        "exit_t_ms": None,
        "pnl_lamports": None,
        "legs": 0,
    }
    if entry.status != "filled" or not entry.spot_sol or entry.spot_sol <= 0 or entry.tokens_raw <= 0:
        return empty
    planned = _ladder_legs(
        path.prints,
        entry_t_ms=entry.t_entry_ms,
        entry_spot=float(entry.spot_sol),
        tokens=int(entry.tokens_raw),
        rule=rule,
    )
    fills = [(t_ms + latency_ms, tokens) for t_ms, tokens in planned]
    if any(t_fill > tape_end_ms for t_fill, _tokens in fills):
        return {
            "exit_status": "censored",
            "trigger": "censored",
            "exit_t_ms": fills[-1][0] if fills else None,
            "pnl_lamports": None,
            "legs": len(fills),
        }
    sol_out = 0
    failed = False
    for t_fill, tokens in fills:
        got = _sell_tokens(path, entry, t_fill, tokens)
        if got is None:
            failed = True
            continue
        sol_out += got
    attempts = len(fills)
    pnl = sol_out - size_lamports - PRIORITY_FEE_LAMPORTS * (1 + attempts)
    if failed:
        pnl -= TOKEN_ACCOUNT_RENT_LAMPORTS
        status = "no_exit_liquidity" if sol_out == 0 else "realized"
    else:
        status = "realized"
    return {
        "exit_status": status,
        "trigger": rule.rule_id,
        "exit_t_ms": fills[-1][0] if fills else None,
        "pnl_lamports": pnl,
        "legs": attempts,
    }


def sample_entry_latencies(n: int, lags_ms: Sequence[int], *, seed: int = LATENCY_DRAW_SEED) -> list[int]:
    """One chain→receive draw per decision. Falls back to the 1s seed when the tape has no event_ts."""
    if n <= 0:
        return []
    if not lags_ms:
        return [ENTRY_LATENCY_MS] * n
    rng = random.Random(seed)
    return [int(lags_ms[rng.randrange(len(lags_ms))]) for _ in range(n)]


def latency_percentiles(lags_ms: Sequence[int]) -> dict[str, Any]:
    if not lags_ms:
        return {
            "n": 0,
            "p50_ms": ENTRY_LATENCY_MS,
            "p90_ms": ENTRY_LATENCY_MS,
            "source": "no event_ts on the tape; labels use the 1s seed",
        }
    ordered = sorted(int(v) for v in lags_ms)
    return {
        "n": len(ordered),
        "p50_ms": int(round(_pct([float(v) for v in ordered], 0.50))),
        "p90_ms": int(round(_pct([float(v) for v in ordered], 0.90))),
        "source": "tape chain→receive (t_recv_ms - event_ts*1000). recv→decision is not on the sealed tape.",
    }


def attach_labels(
    books: dict[str, MintBook],
    rows: list[DecisionRow],
    *,
    tape_end_ms: int,
    latency_ms: int = ENTRY_LATENCY_MS,
    latency_draws: Sequence[int] | None = None,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
    rules: Sequence[ExitRule] = EXIT_RULES,
) -> list[ExitTick]:
    """Paper PnL for an entry at decision+latency. Exit ticks carry their own features."""
    ticks: list[ExitTick] = []
    for index, row in enumerate(rows):
        book = books[row.mint]
        lag = int(latency_draws[index]) if latency_draws is not None else latency_ms
        t_entry = row.decision_t_ms + lag
        row.entry_t_ms = t_entry
        entry = try_entry(
            book.path,
            t_entry_ms=t_entry,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            feats=_ref_feats(row),
        )
        row.entry_status = entry.status
        for rule in rules:
            part = simulate_exit(
                book.path,
                entry,
                rule,
                latency_ms=lag,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            status = str(part["exit_status"])
            pnl = part.get("pnl_lamports")
            row.exit_status_by_rule[rule.rule_id] = status
            row.exit_t_by_rule[rule.rule_id] = part.get("exit_t_ms")
            if status in ("realized", "no_exit_liquidity") and isinstance(pnl, int):
                row.pnl_by_rule[rule.rule_id] = pnl
            else:
                row.pnl_by_rule[rule.rule_id] = None
        spot = float(entry.spot_sol or 0.0)
        for name, tp, sl, _ladder_id in BARRIERS:
            if entry.status != "filled":
                row.barrier[name] = None
                continue
            row.barrier[name] = barrier_outcome(
                book.path.prints,
                entry_t_ms=t_entry,
                entry_spot=spot,
                tp=tp,
                sl=sl,
                horizon_ms=BARRIER_HORIZON_MS,
                tape_end_ms=tape_end_ms,
            )
        for ladder in LADDER_RULES:
            part = simulate_ladder(
                book.path,
                entry,
                ladder,
                latency_ms=lag,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            status = str(part["exit_status"])
            pnl = part.get("pnl_lamports")
            row.exit_status_by_rule[ladder.rule_id] = status
            row.exit_t_by_rule[ladder.rule_id] = part.get("exit_t_ms")
            if status in ("realized", "no_exit_liquidity") and isinstance(pnl, int):
                row.pnl_by_rule[ladder.rule_id] = pnl
            else:
                row.pnl_by_rule[ladder.rule_id] = None
        if entry.status == "filled":
            ticks.extend(
                _exit_ticks(
                    book,
                    row,
                    entry=entry,
                    latency_ms=lag,
                    tape_end_ms=tape_end_ms,
                    size_lamports=size_lamports,
                )
            )
    return ticks


def _exit_ticks(
    book: MintBook,
    row: DecisionRow,
    *,
    entry: Any,
    latency_ms: int,
    tape_end_ms: int,
    size_lamports: int,
) -> list[ExitTick]:
    entry_spot = float(entry.spot_sol or 0.0)
    entry_t_ms = int(entry.t_entry_ms)
    if entry_spot <= 0:
        return []
    candidates = [entry_t_ms + off for off in EXIT_TICK_OFFSETS_MS]
    last_tick = entry_t_ms
    for pr in book.flow:
        if pr.t_recv_ms <= entry_t_ms:
            continue
        if pr.t_recv_ms >= entry_t_ms + EXIT_TICK_OFFSETS_MS[-1]:
            break
        if pr.t_recv_ms - last_tick >= VELOCITY_MS:
            candidates.append(pr.t_recv_ms)
            last_tick = pr.t_recv_ms
    picked: list[int] = []
    for t_tick in sorted(set(candidates)):
        if t_tick <= entry_t_ms or t_tick > tape_end_ms:
            continue
        if t_tick + latency_ms > tape_end_ms:
            continue
        picked.append(t_tick)
        if len(picked) >= EXIT_TICK_CAP:
            break
    entry_buyers = row.features.get("f_unique_buyers", NAN)
    entry_top1 = row.features.get("f_top1_holder_share", NAN)
    entry_chg = row.features.get("f_price_chg", NAN)
    out: list[ExitTick] = []
    peak = entry_spot
    for t_tick in picked:
        seen = [pr for pr in book.flow if entry_t_ms < pr.t_recv_ms <= t_tick]
        buy_sol = sell_sol = 0
        buyers: set[str] = set()
        for pr in seen:
            if pr.price_sol > peak:
                peak = pr.price_sol
            if pr.side == "sell":
                sell_sol += pr.sol_lamports
            else:
                buy_sol += pr.sol_lamports
                if pr.trader:
                    buyers.add(pr.trader)
        state = _state_at(book, t_tick)
        if state is None:
            continue
        price, _quote, base, venue, _mcap = state
        unreal = price / entry_spot - 1.0 if price > 0 else NAN
        peak_ret = peak / entry_spot - 1.0
        drawdown = NAN if peak <= 0 or price <= 0 else (peak - price) / peak
        hold_ms = t_tick + latency_ms - entry_t_ms
        now_rule = ExitRule("now", "hold", hold_ms=hold_ms)
        part = simulate_exit(
            book.path,
            entry,
            now_rule,
            latency_ms=latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=size_lamports,
        )
        pnl = part.get("pnl_lamports") if part.get("exit_status") in ("realized", "no_exit_liquidity") else None
        out.append(
            ExitTick(
                mint=row.mint,
                decision_t_ms=row.decision_t_ms,
                tick_t_ms=t_tick,
                pnl_now=pnl if isinstance(pnl, int) else None,
                features={
                    "x_time_in_trade_s": (t_tick - entry_t_ms) / 1000.0,
                    "x_unrealized_ret": unreal,
                    "x_peak_ret": peak_ret,
                    "x_drawdown": drawdown,
                    "x_buy_sol_since": _sol(buy_sol),
                    "x_sell_sol_since": _sol(sell_sol),
                    "x_unique_buyers_since": float(len(buyers)),
                    "x_curve_progress": _curve_progress(venue, base),
                    "x_price_chg_since_entry": unreal,
                    "x_offset_s": row.features.get("f_offset_s", NAN),
                    "x_entry_unique_buyers": entry_buyers,
                    "x_entry_top1": entry_top1,
                    "x_entry_price_chg": entry_chg,
                },
            )
        )
    return out


def choose_rule(rows: Sequence[DecisionRow], *, min_n: int = MIN_RULE_N, rules: Sequence[ExitRule] = EXIT_RULES) -> str:
    """Highest median realized PnL on this set. Ties break to total, then grid order."""
    ranked: list[tuple[float, int, int, str, int]] = []
    for index, rule in enumerate(rules):
        vals = [row.pnl_by_rule[rule.rule_id] for row in rows if row.pnl_by_rule.get(rule.rule_id) is not None]
        vals_i = [v for v in vals if v is not None]
        if not vals_i:
            continue
        ranked.append((_median(vals_i), sum(vals_i), -index, rule.rule_id, len(vals_i)))
    if not ranked:
        return rules[0].rule_id
    eligible = [item for item in ranked if item[4] >= min_n]
    pool = eligible or ranked
    pool.sort(reverse=True)
    return pool[0][3]


def walk_forward(times: Sequence[int], n_folds: int = 4) -> list[tuple[list[int], list[int]]]:
    """Expanding train prefix, later test slice. Equal timestamps stay in train."""
    n = len(times)
    if n < 8 or n_folds < 1:
        return []
    folds = n_folds
    while folds > 1 and (n // 2) // folds < 2:
        folds -= 1
    order = sorted(range(n), key=lambda i: (times[i], i))
    test_span = n // 2
    start = n - test_span
    size = test_span // folds
    if size < 1:
        return []
    out: list[tuple[list[int], list[int]]] = []
    for k in range(folds):
        a = start + k * size
        b = n if k == folds - 1 else start + (k + 1) * size
        train = order[:a]
        test = order[a:b]
        if not train or not test:
            continue
        cut = times[train[-1]]
        while test and times[test[0]] <= cut:
            train.append(test.pop(0))
        if train and test and times[test[0]] > times[train[-1]]:
            out.append((train, test))
    return out


def vector(features: dict[str, float], names: Sequence[str]) -> list[float]:
    out: list[float] = []
    for name in names:
        value = features.get(name)
        if value is None:
            out.append(NAN)
        else:
            out.append(float(value))
    return out


class Booster:
    def __init__(self, backend: str, model: Any, names: Sequence[str]) -> None:
        self.backend = backend
        self.model = model
        self.names = list(names)

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        if not rows:
            return []
        if self.backend == "lightgbm":
            import numpy as np

            pred = self.model.predict(np.asarray(rows, dtype=np.float64))
            return [float(v) for v in pred]
        import numpy as np

        proba = self.model.predict_proba(np.asarray(rows, dtype=np.float64))
        return [float(v) for v in proba[:, 1]]

    def predict_one(self, row: Sequence[float]) -> float:
        return self.predict([row])[0]

    def importance(self, sample: Sequence[Sequence[float]] | None = None, labels: Sequence[int] | None = None) -> list[dict[str, float | str]]:
        if self.backend == "lightgbm":
            gain = self.model.feature_importance(importance_type="gain")
            pairs = list(zip(self.names, (float(v) for v in gain)))
        else:
            pairs = list(zip(self.names, _sklearn_importance(self, sample or [], labels or [])))
        pairs.sort(key=lambda item: item[1], reverse=True)
        return [{"name": name, "gain": gain} for name, gain in pairs]

    def save(self, path: Path) -> str:
        if self.backend == "lightgbm":
            text_path = path.with_suffix(".txt")
            self.model.save_model(str(text_path))
            return text_path.name
        import pickle

        bin_path = path.with_suffix(".pkl")
        with bin_path.open("wb") as fh:
            pickle.dump({"backend": self.backend, "names": self.names, "model": self.model}, fh)
        return bin_path.name

    @classmethod
    def load(cls, path: Path) -> Booster:
        """Hot-reload a deploy model. LightGBM text or the sklearn pickle."""
        if path.suffix == ".txt":
            import lightgbm as lgb

            model = lgb.Booster(model_file=str(path))
            names = [str(name) for name in model.feature_name()]
            return cls("lightgbm", model, names)
        import pickle

        with path.open("rb") as fh:
            blob = pickle.load(fh)
        if not isinstance(blob, dict) or "model" not in blob or "names" not in blob:
            raise ValueError("model file is not a LAYA booster")
        return cls(str(blob.get("backend") or "sklearn"), blob["model"], list(blob["names"]))


def available_backend(prefer: str | None = None) -> str:
    if prefer == "sklearn":
        return "sklearn"
    if prefer == "lightgbm":
        import lightgbm  # noqa: F401

        return "lightgbm"
    try:
        import lightgbm  # noqa: F401

        return "lightgbm"
    except ImportError:
        return "sklearn"


def fit_booster(
    x_rows: Sequence[Sequence[float]],
    labels: Sequence[int],
    names: Sequence[str],
    *,
    backend: str | None = None,
) -> Booster | None:
    if len(x_rows) < 8 or len(set(labels)) < 2:
        return None
    which = available_backend(backend)
    n = len(x_rows)
    leaves = 7 if n < 500 else 15
    min_leaf = 5 if n < 200 else 20
    rounds = 40 if n < 500 else 80
    pos = sum(labels)
    neg = n - pos
    scale = (neg / pos) if pos else 1.0
    import numpy as np

    x = np.asarray(x_rows, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int32)
    if which == "lightgbm":
        import lightgbm as lgb

        train = lgb.Dataset(x, label=y, feature_name=list(names))
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "num_threads": 1,
            "verbosity": -1,
            "learning_rate": 0.05,
            "num_leaves": leaves,
            "min_data_in_leaf": min_leaf,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.9,
            "bagging_freq": 1,
            "scale_pos_weight": scale,
            "seed": 1,
            "deterministic": True,
            "force_row_wise": True,
        }
        model = lgb.train(params, train, num_boost_round=rounds)
        return Booster("lightgbm", model, names)
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(
        max_iter=rounds,
        learning_rate=0.05,
        max_leaf_nodes=leaves,
        min_samples_leaf=min_leaf,
        random_state=1,
        early_stopping=False,
        class_weight="balanced",
    )
    model.fit(x, y)
    return Booster("sklearn", model, names)


def _sklearn_importance(booster: Booster, sample: Sequence[Sequence[float]], labels: Sequence[int]) -> list[float]:
    if len(sample) < 8 or len(set(labels)) < 2:
        return [0.0] * len(booster.names)
    import numpy as np
    from sklearn.inspection import permutation_importance

    cap = min(400, len(sample))
    x = np.asarray(sample[:cap], dtype=np.float64)
    y = np.asarray(labels[:cap], dtype=np.int32)
    result = permutation_importance(booster.model, x, y, n_repeats=2, random_state=1, scoring="accuracy")
    return [float(v) for v in result.importances_mean]


def _trainable(rows: Sequence[DecisionRow], rule_id: str) -> list[DecisionRow]:
    return [row for row in rows if row.pnl_by_rule.get(rule_id) is not None]


def _labels_for(rows: Sequence[DecisionRow], rule_id: str) -> tuple[list[list[float]], list[int]]:
    xs: list[list[float]] = []
    ys: list[int] = []
    for row in rows:
        pnl = row.pnl_by_rule.get(rule_id)
        if pnl is None:
            continue
        xs.append(vector(row.features, FEATURE_NAMES))
        ys.append(1 if pnl > 0 else 0)
    return xs, ys


def _pnl_summary(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median_sol": None, "mean_sol": None, "p10_sol": None, "p90_sol": None, "win_rate": None, "total_sol": 0.0, "rugs": 0}
    ordered = sorted(values)
    n = len(ordered)
    # Rug stuck-loss is the same lamport constant on every no-exit. Count the left tail
    # at or below one fee-sized loss only as a reported extra, not as a filter.
    return {
        "n": n,
        "median_sol": statistics.median(ordered) / LAMPORTS_PER_SOL,
        "mean_sol": (sum(ordered) / n) / LAMPORTS_PER_SOL,
        "p10_sol": _pct(ordered, 0.10) / LAMPORTS_PER_SOL,
        "p90_sol": _pct(ordered, 0.90) / LAMPORTS_PER_SOL,
        "win_rate": sum(1 for v in ordered if v > 0) / n,
        "total_sol": sum(ordered) / LAMPORTS_PER_SOL,
    }


def _pct(ordered: Sequence[float], p: float) -> float:
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    w = k - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


@dataclass(frozen=True)
class BookTrade:
    mint: str
    t_ms: int
    pnl: int


def _utc_day(t_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t_ms / 1000.0))


def _winsorized_mean_lamports(values: Sequence[int], p: float = WINSOR_P) -> float | None:
    """Mean after capping each value at the p and 1-p percentiles of this book."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    lo = _pct(ordered, p)
    hi = _pct(ordered, 1.0 - p)
    capped = [min(hi, max(lo, float(v))) for v in ordered]
    return sum(capped) / len(capped)


def _cluster_bootstrap(trades: Sequence[BookTrade]) -> tuple[list[float] | None, list[float] | None]:
    """90% CI of mean and total SOL. Resample whole tokens with replacement."""
    if not trades:
        return None, None
    clusters: dict[str, list[int]] = defaultdict(list)
    for trade in trades:
        clusters[trade.mint].append(trade.pnl)
    groups = [clusters[mint] for mint in sorted(clusters)]
    n_mints = len(groups)
    rng = random.Random(BOOTSTRAP_SEED)
    means: list[float] = []
    totals: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        drawn = [groups[rng.randrange(n_mints)] for _ in range(n_mints)]
        n = 0
        total = 0
        for group in drawn:
            n += len(group)
            total += sum(group)
        means.append(total / n)
        totals.append(float(total))
    means.sort()
    totals.sort()
    mean_ci = [_pct(means, 0.05) / LAMPORTS_PER_SOL, _pct(means, 0.95) / LAMPORTS_PER_SOL]
    total_ci = [_pct(totals, 0.05) / LAMPORTS_PER_SOL, _pct(totals, 0.95) / LAMPORTS_PER_SOL]
    return mean_ci, total_ci


def _total_ex_top_sol(pnls: Sequence[int], k: int) -> float | None:
    """Sum after removing the k largest trades. None when there is nothing left."""
    if len(pnls) <= k:
        return None
    dropped = sum(sorted(pnls, reverse=True)[:k])
    return (sum(pnls) - dropped) / LAMPORTS_PER_SOL


def _max_drawdown_lamports(trades: Sequence[BookTrade]) -> int | None:
    """Peak-to-trough of cumulative PnL. Peak starts at 0. Independent fills, not a bankroll."""
    if not trades:
        return None
    cum = 0
    peak = 0
    worst = 0
    for trade in sorted(trades, key=lambda item: (item.t_ms, item.mint)):
        cum += trade.pnl
        if cum > peak:
            peak = cum
        drop = peak - cum
        if drop > worst:
            worst = drop
    return worst


def book_stats(trades: Sequence[BookTrade]) -> dict[str, Any]:
    """Simple PnL summary plus heavy-tail yardsticks and the promotion flag."""
    summary = _pnl_summary([trade.pnl for trade in trades])
    pnls = [trade.pnl for trade in trades]
    mean_ci, total_ci = _cluster_bootstrap(trades)
    if len(pnls) < 2:
        total_ex_best = None
    else:
        total_ex_best = (sum(pnls) - max(pnls)) / LAMPORTS_PER_SOL
    total_ex_top = _total_ex_top_sol(pnls, PROMOTION_DROP_N)
    winsor = _winsorized_mean_lamports(pnls)
    by_day: dict[str, list[int]] = defaultdict(list)
    for trade in trades:
        by_day[_utc_day(trade.t_ms)].append(trade.pnl)
    days = []
    for day in sorted(by_day):
        values = by_day[day]
        days.append(
            {
                "day": day,
                "n": len(values),
                "total_sol": sum(values) / LAMPORTS_PER_SOL,
                "mean_sol": (sum(values) / len(values)) / LAMPORTS_PER_SOL,
            }
        )
    days_positive = sum(1 for row in days if row["total_sol"] > 0)
    n_days = len(days)
    majority = n_days > 0 and days_positive * 2 > n_days
    drawdown = _max_drawdown_lamports(trades)
    promote = bool(
        summary["n"] >= PROMOTION_MIN_N
        and n_days >= PROMOTION_MIN_DAYS
        and majority
        and mean_ci is not None
        and mean_ci[0] > 0
        and total_ex_top is not None
        and total_ex_top > 0
    )
    summary.update(
        {
            "mean_ci90_sol": mean_ci,
            "total_ci90_sol": total_ci,
            "winsorized_mean_sol": None if winsor is None else winsor / LAMPORTS_PER_SOL,
            "total_ex_best_sol": total_ex_best,
            "total_ex_top3_sol": total_ex_top,
            "days": days,
            "days_positive": days_positive,
            "n_days": n_days,
            "majority_days_positive": majority,
            "max_drawdown_sol": None if drawdown is None else drawdown / LAMPORTS_PER_SOL,
            "promote": promote,
        }
    )
    return summary


def _trades_from_scored(rows: Sequence[Scored]) -> list[BookTrade]:
    return [BookTrade(row.mint, row.decision_t_ms, row.pnl) for row in rows]


def _topk(indexed: list[tuple[float, int]], frac: float) -> list[int]:
    """Pool-wide percentile. Not a tradable book: it ranks against later scores."""
    if not indexed or frac <= 0:
        return []
    if frac >= 1:
        return [i for _s, i in indexed]
    k = int(round(frac * len(indexed)))
    if k < 1:
        k = 1
    ordered = sorted(indexed, key=lambda item: (-item[0], item[1]))
    return [i for _s, i in ordered[:k]]


class RankWindow:
    """Causal top-k. The forward paper service uses this same window.

    A score is taken when it sits in the top fraction of recent scores at this
    point. The window includes the score just observed. Until it holds at least
    1/fraction scores, nothing is taken.
    """

    def __init__(self, frac: float, cap: int = 500) -> None:
        self.frac = frac
        self.cap = cap
        self.scores: list[float] = []

    def consider(self, score: float) -> str:
        self.scores.append(score)
        if len(self.scores) > self.cap:
            del self.scores[0]
        need = max(1, math.ceil(1.0 / self.frac))
        if len(self.scores) < need:
            return "warmup"
        k = int(round(self.frac * len(self.scores)))
        if k < 1:
            k = 1
        higher = sum(1 for prior in self.scores if prior > score)
        return "take" if higher < k else "below"


def causal_take_indices(scores: Sequence[float], frac: float) -> list[int]:
    """Indices taken by a fresh RankWindow walked in the given order."""
    if frac >= 1:
        return list(range(len(scores)))
    window = RankWindow(frac)
    return [i for i, score in enumerate(scores) if window.consider(score) == "take"]


@dataclass
class Scored:
    fold: int
    point: str
    score: float
    pnl: int
    rule_id: str
    mint: str
    decision_t_ms: int
    win: int


def evaluate_entry(
    rows: list[DecisionRow],
    *,
    n_folds: int = 4,
    min_rule_n: int = MIN_RULE_N,
    backend: str | None = None,
) -> dict[str, Any]:
    folds = walk_forward([row.decision_t_ms for row in rows], n_folds=n_folds)
    scored: list[Scored] = []
    fold_meta: list[dict[str, Any]] = []
    importances: list[list[dict[str, Any]]] = []
    for fold_i, (train_idx, test_idx) in enumerate(folds):
        train_rows = [rows[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]
        rule_id = choose_rule(train_rows, min_n=min_rule_n)
        xs, ys = _labels_for(train_rows, rule_id)
        model = fit_booster(xs, ys, FEATURE_NAMES, backend=backend)
        meta: dict[str, Any] = {
            "fold": fold_i,
            "rule_id": rule_id,
            "train_n": len(train_idx),
            "test_n": len(test_idx),
            "train_labeled": len(ys),
            "train_wins": int(sum(ys)),
            "train_t_max": train_rows[-1].decision_t_ms if train_rows else None,
            "test_t_min": test_rows[0].decision_t_ms if test_rows else None,
            "model": None if model is None else model.backend,
        }
        # The lists are not time-sorted; report actual bounds.
        meta["train_t_max"] = max(row.decision_t_ms for row in train_rows)
        meta["test_t_min"] = min(row.decision_t_ms for row in test_rows)
        meta["test_t_max"] = max(row.decision_t_ms for row in test_rows)
        if model is None:
            meta["skipped"] = "one_class_or_too_small"
            fold_meta.append(meta)
            continue
        importances.append(model.importance(xs, ys))
        test_x = []
        test_keep: list[DecisionRow] = []
        for row in test_rows:
            pnl = row.pnl_by_rule.get(rule_id)
            if pnl is None:
                continue
            test_x.append(vector(row.features, FEATURE_NAMES))
            test_keep.append(row)
        probs = model.predict(test_x) if test_x else []
        for row, prob in zip(test_keep, probs):
            pnl = row.pnl_by_rule[rule_id]
            assert pnl is not None
            scored.append(
                Scored(
                    fold=fold_i,
                    point=row.point_id(),
                    score=prob,
                    pnl=pnl,
                    rule_id=rule_id,
                    mint=row.mint,
                    decision_t_ms=row.decision_t_ms,
                    win=1 if pnl > 0 else 0,
                )
            )
        fold_meta.append(meta)
    return {
        "folds": fold_meta,
        "importance": _average_importance(importances),
        "by_point": _selection_table(scored),
        "thresholds": _threshold_table(scored),
        "oos_n": len(scored),
        "scored": scored,
        "migrate_predeclared": _migrate_predeclared(rows, n_folds=n_folds),
    }


def evaluate_barrier(
    rows: list[DecisionRow],
    *,
    target: str,
    pnl_rule: str,
    n_folds: int = 4,
    backend: str | None = None,
) -> dict[str, Any]:
    """Classifier trained on the barrier hit. The book is that model's top-k under the ladder PnL."""
    folds = walk_forward([row.decision_t_ms for row in rows], n_folds=n_folds)
    scored: list[Scored] = []
    fold_meta: list[dict[str, Any]] = []
    importances: list[list[dict[str, Any]]] = []
    for fold_i, (train_idx, test_idx) in enumerate(folds):
        train_rows = [rows[i] for i in train_idx if rows[i].barrier.get(target) is not None]
        xs: list[list[float]] = []
        ys: list[int] = []
        for row in train_rows:
            label = row.barrier.get(target)
            if label is None:
                continue
            xs.append(vector(row.features, FEATURE_NAMES))
            ys.append(int(label))
        model = fit_booster(xs, ys, FEATURE_NAMES, backend=backend)
        meta: dict[str, Any] = {
            "fold": fold_i,
            "target": target,
            "pnl_rule": pnl_rule,
            "train_labeled": len(ys),
            "train_hits": int(sum(ys)),
            "model": None if model is None else model.backend,
        }
        if model is None:
            meta["skipped"] = "one_class_or_too_small"
            fold_meta.append(meta)
            continue
        importances.append(model.importance(xs, ys))
        test_x = []
        test_keep: list[DecisionRow] = []
        for index in test_idx:
            row = rows[index]
            if row.barrier.get(target) is None:
                continue
            pnl = row.pnl_by_rule.get(pnl_rule)
            if pnl is None:
                continue
            test_x.append(vector(row.features, FEATURE_NAMES))
            test_keep.append(row)
        probs = model.predict(test_x) if test_x else []
        for row, prob in zip(test_keep, probs):
            pnl = row.pnl_by_rule[pnl_rule]
            assert pnl is not None
            scored.append(
                Scored(
                    fold=fold_i,
                    point=row.point_id(),
                    score=prob,
                    pnl=pnl,
                    rule_id=pnl_rule,
                    mint=row.mint,
                    decision_t_ms=row.decision_t_ms,
                    win=int(row.barrier.get(target) or 0),
                )
            )
        meta["test_n"] = len(test_keep)
        fold_meta.append(meta)
    return {
        "target": target,
        "pnl_rule": pnl_rule,
        "folds": fold_meta,
        "importance": _average_importance(importances),
        "by_point": _selection_table(scored),
        "thresholds": _threshold_table(scored),
        "oos_n": len(scored),
    }


def _migrate_predeclared(rows: Sequence[DecisionRow], *, n_folds: int) -> dict[str, Any]:
    """Signal-scan lead: enter at the first PumpSwap print, exit tp50_sl30.

    The exit is predeclared from that scan's training half. It is not re-picked
    on these test rows. hold_30s is the same clock for comparison.
    """
    folds = walk_forward([row.decision_t_ms for row in rows], n_folds=n_folds)
    oos_tp: list[BookTrade] = []
    oos_hold: list[BookTrade] = []
    for _train, test in folds:
        for index in test:
            row = rows[index]
            if row.trigger != "migrate":
                continue
            tp = row.pnl_by_rule.get("tp50_sl30")
            hold = row.pnl_by_rule.get("hold_30s")
            if tp is not None:
                oos_tp.append(BookTrade(row.mint, row.decision_t_ms, tp))
            if hold is not None:
                oos_hold.append(BookTrade(row.mint, row.decision_t_ms, hold))
    return {
        "rule": "tp50_sl30",
        "source": "signal-scan train pick, not re-chosen on this test",
        "oos_tp50_sl30": book_stats(oos_tp),
        "oos_hold_30s": book_stats(oos_hold),
    }


def _average_importance(folds: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not folds:
        return []
    acc: dict[str, float] = defaultdict(float)
    for rows in folds:
        for row in rows:
            acc[str(row["name"])] += float(row["gain"])
    total = sum(acc.values()) or 1.0
    ranked = sorted(acc.items(), key=lambda item: item[1], reverse=True)
    return [{"name": name, "gain": gain / len(folds), "share": gain / total} for name, gain in ranked]


def _selection_table(scored: Sequence[Scored]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str], list[Scored]] = defaultdict(list)
    for row in scored:
        groups[(row.fold, row.point)].append(row)
    pooled: dict[tuple[str, float], list[BookTrade]] = defaultdict(list)
    points = sorted({row.point for row in scored}, key=_point_sort)
    for point in points:
        for frac in TOP_FRACTIONS:
            bag: list[BookTrade] = []
            for (_fold, grp_point), rows in groups.items():
                if grp_point != point:
                    continue
                ordered = sorted(rows, key=lambda r: (r.decision_t_ms, r.mint))
                chosen = causal_take_indices([r.score for r in ordered], frac)
                bag.extend(BookTrade(ordered[i].mint, ordered[i].decision_t_ms, ordered[i].pnl) for i in chosen)
            pooled[(point, frac)] = bag
    table = []
    for point in points:
        base = pooled.get((point, 1.0), [])
        row: dict[str, Any] = {"point": point, "baseline": book_stats(base)}
        takes = []
        for frac in TOP_FRACTIONS:
            if frac >= 1:
                continue
            takes.append({"fraction": frac, **book_stats(pooled.get((point, frac), []))})
        row["top"] = takes
        table.append(row)
    return table


def _threshold_table(scored: Sequence[Scored]) -> list[dict[str, Any]]:
    out = []
    for threshold in SCORE_THRESHOLDS:
        kept = _trades_from_scored([row for row in scored if row.score >= threshold])
        summary = book_stats(kept)
        summary["threshold"] = threshold
        summary["precision"] = summary["win_rate"]
        out.append(summary)
    return out


def _point_sort(point: str) -> tuple[int, str]:
    try:
        return (0, f"{int(point):05d}")
    except ValueError:
        return (1, point)


def evaluate_exit(
    rows: list[DecisionRow],
    ticks: list[ExitTick],
    entry_eval: dict[str, Any],
    *,
    n_folds: int = 4,
    min_rule_n: int = MIN_RULE_N,
    backend: str | None = None,
) -> dict[str, Any]:
    """Second booster: exit now when it beats the train-chosen rule on the forward path."""
    by_key: dict[tuple[str, int], DecisionRow] = {(row.mint, row.decision_t_ms): row for row in rows}
    tick_groups: dict[tuple[str, int], list[ExitTick]] = defaultdict(list)
    for tick in ticks:
        tick_groups[(tick.mint, tick.decision_t_ms)].append(tick)
    folds = walk_forward([row.decision_t_ms for row in rows], n_folds=n_folds)
    fold_rules = {meta["fold"]: meta["rule_id"] for meta in entry_eval["folds"] if meta.get("rule_id")}
    policy_pnls: list[int] = []
    rule_pnls: list[int] = []
    fired = 0
    considered = 0
    importances: list[list[dict[str, Any]]] = []
    backend_name = None
    for fold_i, (train_idx, test_idx) in enumerate(folds):
        rule_id = fold_rules.get(fold_i) or choose_rule([rows[i] for i in train_idx], min_n=min_rule_n)
        xs: list[list[float]] = []
        ys: list[int] = []
        for index in train_idx:
            row = rows[index]
            pnl_rule = row.pnl_by_rule.get(rule_id)
            exit_t = row.exit_t_by_rule.get(rule_id)
            if pnl_rule is None or exit_t is None:
                continue
            row_lag = _decision_latency_ms(row)
            for tick in tick_groups.get((row.mint, row.decision_t_ms), ()):
                if tick.tick_t_ms + row_lag >= exit_t or tick.pnl_now is None:
                    continue
                xs.append(vector(tick.features, EXIT_FEATURE_NAMES))
                ys.append(1 if tick.pnl_now > pnl_rule else 0)
        model = fit_booster(xs, ys, EXIT_FEATURE_NAMES, backend=backend)
        if model is None:
            continue
        backend_name = model.backend
        importances.append(model.importance(xs, ys))
        for index in test_idx:
            row = rows[index]
            pnl_rule = row.pnl_by_rule.get(rule_id)
            exit_t = row.exit_t_by_rule.get(rule_id)
            if pnl_rule is None or exit_t is None:
                continue
            row_lag = _decision_latency_ms(row)
            usable = [
                tick
                for tick in tick_groups.get((row.mint, row.decision_t_ms), ())
                if tick.tick_t_ms + row_lag < exit_t and tick.pnl_now is not None
            ]
            if not usable:
                continue
            considered += 1
            usable.sort(key=lambda t: t.tick_t_ms)
            probs = model.predict([vector(tick.features, EXIT_FEATURE_NAMES) for tick in usable])
            taken = pnl_rule
            for tick, prob in zip(usable, probs):
                if prob >= EXIT_TAKE_P and tick.pnl_now is not None:
                    taken = tick.pnl_now
                    fired += 1
                    break
            policy_pnls.append(taken)
            rule_pnls.append(pnl_rule)
    return {
        "backend": backend_name,
        "positions": considered,
        "early_exits": fired,
        "policy": _pnl_summary(policy_pnls),
        "rule_hold": _pnl_summary(rule_pnls),
        "importance": _average_importance(importances),
    }


def measure_latency(model: Booster, sample: Sequence[float], repeats: int = 200) -> dict[str, float]:
    for _ in range(20):
        model.predict_one(sample)
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        model.predict_one(sample)
        times.append(time.perf_counter() - t0)
    times.sort()
    return {
        "n": float(repeats),
        "p50_us": times[len(times) // 2] * 1_000_000,
        "p90_us": times[int(len(times) * 0.90)] * 1_000_000,
        "mean_us": (sum(times) / len(times)) * 1_000_000,
    }


def data_needed_note(*, hours: float, creates: int, oos_n: int) -> str:
    rate = creates / hours if hours > 0 else 0.0
    # Half the tape is the OOS span; a 10% take is the book we would actually trade.
    per_hour_selected = rate * 0.5 * 0.10
    hours_for_1k = (1000 / per_hour_selected) if per_hour_selected > 0 else None
    hours_txt = "unknown" if hours_for_1k is None else f"{hours_for_1k:.1f}h"
    return (
        f"This window is {hours:.2f}h, {creates} creates, {oos_n} out-of-sample labeled decisions. "
        "The buy-every book wins about 18% and the left tail is a stuck rug near -0.052 SOL, so a "
        "top-10% median is noise until the selected out-of-sample book has on the order of 1,000 trades "
        "(several thousand before it can be told apart from the buy-every median). "
        f"At this create rate, about {hours_txt} of tape yields 1,000 selected out-of-sample trades at a 10% take, "
        "and a few days before walk-forward still has a later test slice big enough to trust. "
        "Treat this run as a pipeline measurement."
    )


def _label_matrix(
    rows: Sequence[DecisionRow], label: str, pnl_rule: str
) -> tuple[list[list[float]], list[int]]:
    """Rows that actually have the candidate's label. Holdout rows must not be passed in."""
    xs: list[list[float]] = []
    ys: list[int] = []
    for row in rows:
        if label == "pnl":
            pnl = row.pnl_by_rule.get(pnl_rule)
            if pnl is None:
                continue
            bit = 1 if pnl > 0 else 0
        else:
            raw = row.barrier.get(label)
            if raw is None:
                continue
            bit = int(raw)
        xs.append(vector(row.features, FEATURE_NAMES))
        ys.append(bit)
    return xs, ys


def score_frozen_candidates(
    rows: Sequence[DecisionRow],
    *,
    backend: str | None = None,
    freeze_ms: int = CANDIDATE_FREEZE_MS,
) -> dict[str, Any]:
    """Fit the pre-registered list on decisions at or before the freeze.

    Score only decisions strictly after it. Exploratory walk-forward is a
    different table and may still use the whole tape.
    """
    train = [row for row in rows if row.decision_t_ms <= freeze_ms]
    hold = [row for row in rows if row.decision_t_ms > freeze_ms]
    fitted: dict[tuple[str, str], tuple[Booster | None, int]] = {}
    candidates: list[dict[str, Any]] = []
    for spec in FROZEN_CANDIDATES:
        label = str(spec["label"])
        pnl_rule = str(spec["pnl_rule"])
        point = str(spec["point"])
        fraction = float(spec["fraction"])
        pool = [
            row
            for row in hold
            if row.point_id() == point and row.pnl_by_rule.get(pnl_rule) is not None
        ]
        model: Booster | None = None
        train_labeled = 0
        ranked = label != "none" and fraction < 1
        if ranked:
            key = (label, pnl_rule)
            if key not in fitted:
                xs, ys = _label_matrix(train, label, pnl_rule)
                fitted[key] = (fit_booster(xs, ys, FEATURE_NAMES, backend=backend), len(ys))
            model, train_labeled = fitted[key]
        if not ranked:
            chosen = pool
        elif model is None or not pool:
            chosen = []
        else:
            ordered = sorted(pool, key=lambda row: (row.decision_t_ms, row.mint))
            probs = model.predict([vector(row.features, FEATURE_NAMES) for row in ordered])
            chosen = [ordered[i] for i in causal_take_indices(probs, fraction)]
        trades = [
            BookTrade(row.mint, row.decision_t_ms, int(row.pnl_by_rule[pnl_rule]))
            for row in chosen
            if row.pnl_by_rule.get(pnl_rule) is not None
        ]
        stats = book_stats(trades)
        stats.update(
            {
                "id": spec["id"],
                "point": point,
                "fraction": fraction,
                "label": label,
                "pnl_rule": pnl_rule,
                "n_pool": len(pool),
                "train_labeled": train_labeled,
                "model": None if model is None else model.backend,
                "selected_mints": [row.mint for row in chosen],
                "selected_t_ms": [row.decision_t_ms for row in chosen],
            }
        )
        candidates.append(stats)
    return {
        "freeze_at": CANDIDATE_FREEZE_AT,
        "freeze_ms": freeze_ms,
        "train_n": len(train),
        "holdout_n": len(hold),
        "separate_from_exploratory_search": True,
        "note": (
            "Fit only on decisions at or before the freeze. "
            "Scored only on decisions strictly after it. Not the exploratory search."
        ),
        "candidates": candidates,
    }


def _decision_latency_ms(row: DecisionRow) -> int:
    if row.entry_t_ms > row.decision_t_ms:
        return row.entry_t_ms - row.decision_t_ms
    return ENTRY_LATENCY_MS


def hold_book_at_latency(
    books: dict[str, MintBook],
    rows: Sequence[DecisionRow],
    *,
    tape_end_ms: int,
    latency_ms: int,
    size_lamports: int,
    slippage_cap: float,
) -> dict[str, Any]:
    """hold_30s totals at one fixed entry delay. Does not rewrite the sampled labels."""
    rule = next(item for item in EXIT_RULES if item.rule_id == "hold_30s")
    values: list[int] = []
    for row in rows:
        book = books[row.mint]
        entry = try_entry(
            book.path,
            t_entry_ms=row.decision_t_ms + latency_ms,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            feats=_ref_feats(row),
        )
        part = simulate_exit(
            book.path,
            entry,
            rule,
            latency_ms=latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=size_lamports,
        )
        pnl = part.get("pnl_lamports")
        status = str(part["exit_status"])
        if status in ("realized", "no_exit_liquidity") and isinstance(pnl, int):
            values.append(pnl)
    summary = _pnl_summary(values)
    summary["latency_ms"] = latency_ms
    return summary


def entry_latency_report(
    books: dict[str, MintBook],
    rows: Sequence[DecisionRow],
    lags_ms: Sequence[int],
    *,
    tape_end_ms: int,
    size_lamports: int,
    slippage_cap: float,
) -> dict[str, Any]:
    measured = latency_percentiles(lags_ms)
    sampled = _pnl_summary(
        [int(row.pnl_by_rule["hold_30s"]) for row in rows if isinstance(row.pnl_by_rule.get("hold_30s"), int)]
    )
    return {
        "measured": measured,
        "sampled_hold_30s": sampled,
        "p50_hold_30s": hold_book_at_latency(
            books,
            rows,
            tape_end_ms=tape_end_ms,
            latency_ms=int(measured["p50_ms"]),
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
        ),
        "p90_hold_30s": hold_book_at_latency(
            books,
            rows,
            tape_end_ms=tape_end_ms,
            latency_ms=int(measured["p90_ms"]),
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
        ),
    }


def build_dataset(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
    latency_ms: int = ENTRY_LATENCY_MS,
    chain_lags_ms: Sequence[int] | None = None,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
) -> tuple[list[DecisionRow], list[ExitTick], dict[str, int]]:
    rows, wallet_diag = build_feature_rows(books, tape_end_ms=tape_end_ms, offsets_ms=offsets_ms)
    print(f"decisions={len(rows)}", file=sys.stderr)
    draws = None
    if chain_lags_ms is not None:
        draws = sample_entry_latencies(len(rows), chain_lags_ms)
    ticks = attach_labels(
        books,
        rows,
        tape_end_ms=tape_end_ms,
        latency_ms=latency_ms,
        latency_draws=draws,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    print(f"exit_ticks={len(ticks)}", file=sys.stderr)
    return rows, ticks, wallet_diag


def run_models(
    rows: list[DecisionRow],
    ticks: list[ExitTick],
    *,
    n_folds: int,
    min_rule_n: int,
    backend: str | None,
    output_dir: Path | None,
) -> dict[str, Any]:
    entry = evaluate_entry(rows, n_folds=n_folds, min_rule_n=min_rule_n, backend=backend)
    scored: list[Scored] = entry.pop("scored")
    exit_eval = evaluate_exit(rows, ticks, entry, n_folds=n_folds, min_rule_n=min_rule_n, backend=backend)
    # Deploy model sees every labeled row. Its fit is not the OOS table.
    deploy_rule = choose_rule(rows, min_n=min_rule_n)
    xs, ys = _labels_for(rows, deploy_rule)
    deploy = fit_booster(xs, ys, FEATURE_NAMES, backend=backend)
    latency = None
    model_file = None
    if deploy is not None and xs:
        latency = measure_latency(deploy, xs[0])
        if output_dir is not None:
            model_file = deploy.save(output_dir / "entry_model")
    entry["deploy"] = {
        "rule_id": deploy_rule,
        "labeled": len(ys),
        "wins": int(sum(ys)) if ys else 0,
        "backend": None if deploy is None else deploy.backend,
        "model_file": model_file,
        "predict_latency": latency,
        "in_sample_only": True,
    }
    entry["exit"] = exit_eval
    entry["barriers"] = [
        evaluate_barrier(rows, target=name, pnl_rule=ladder_id, n_folds=n_folds, backend=backend)
        for name, _tp, _sl, ladder_id in BARRIERS
    ]
    # Deploy fit for the pre-registered buyers_8 ladder book. Not the OOS table.
    barrier_file = None
    barrier_labeled = [row for row in rows if row.barrier.get("hit_100_30") is not None]
    barrier_x = [vector(row.features, FEATURE_NAMES) for row in barrier_labeled]
    barrier_y = [int(row.barrier["hit_100_30"]) for row in barrier_labeled]
    barrier_model = fit_booster(barrier_x, barrier_y, FEATURE_NAMES, backend=backend)
    if barrier_model is not None and output_dir is not None:
        barrier_file = barrier_model.save(output_dir / "barrier_hit_100_30")
    entry["barrier_deploy"] = {
        "target": "hit_100_30",
        "pnl_rule": "ladder_2x_t30",
        "labeled": len(barrier_y),
        "hits": int(sum(barrier_y)) if barrier_y else 0,
        "backend": None if barrier_model is None else barrier_model.backend,
        "model_file": barrier_file,
        "in_sample_only": True,
    }
    entry["frozen_candidates"] = score_frozen_candidates(rows, backend=backend)
    entry["oos_scores"] = [
        {
            "fold": s.fold,
            "point": s.point,
            "mint": s.mint,
            "decision_t_ms": s.decision_t_ms,
            "score": s.score,
            "pnl_lamports": s.pnl,
            "rule_id": s.rule_id,
            "win": s.win,
        }
        for s in scored
    ]
    return entry


def format_markdown(board: dict[str, Any]) -> str:
    lines = [
        "# LAYA v0 scoreboard",
        "",
        f"Schema `{board['schema']}`. Paper only. Entry delay is a draw from the tape's chain→receive lags when event_ts is present, otherwise {board['entry_latency_ms']} ms.",
        "Primary exit rule is chosen on each training fold (highest median realized SOL, rugs included).",
        "Scores in the tables are out of sample. The deploy model is fit on every row and is not those numbers.",
        "",
        board["data_needed"],
        "",
        "## Window",
        "",
        f"- Creates in window: {board['creates']}",
        f"- Decisions: {board['decisions']}",
        f"- OOS labeled decisions: {board['entry']['oos_n']}",
        f"- Tape lines: {board['scan']['lines']}, kept prints: {board['scan']['kept']}",
        f"- Backend (deploy): {board['entry']['deploy']['backend']}",
        "",
    ]
    latency = board.get("entry_latency") or {}
    measured = latency.get("measured") or {}
    if measured:
        lines.extend(
            [
                "## Entry latency",
                "",
                f"Measured chain→receive n={measured.get('n')}, p50 {measured.get('p50_ms')} ms, p90 {measured.get('p90_ms')} ms.",
                str(measured.get("source") or ""),
                "Training labels sample that distribution (seed 1). The rows below are hold_30s buy-all at the sample, and again at a flat p50 and p90, so the delay can be read without refitting.",
                "",
                "| book | n | median SOL | mean SOL | total SOL |",
                "| --- | ---: | ---: | ---: | ---: |",
                _md_latency_row("sampled", latency.get("sampled_hold_30s") or {}),
                _md_latency_row("p50", latency.get("p50_hold_30s") or {}),
                _md_latency_row("p90", latency.get("p90_hold_30s") or {}),
                "",
            ]
        )
    frozen = board["entry"].get("frozen_candidates")
    if frozen:
        lines.extend(
            [
                "## Pre-registered forward holdout",
                "",
                f"Frozen at {frozen.get('freeze_at')}. "
                "These three candidates were named before this slice of tape existed.",
                "Models are fit only on decisions at or before the freeze "
                f"({frozen.get('train_n')} decisions) and scored only on decisions strictly after it "
                f"({frozen.get('holdout_n')} decisions).",
                "This table is the forward holdout. The folds and point tables below are the exploratory search on the whole tape.",
                f"Promotion is the same rule: {PROMOTION_RULE}.",
                "",
                "| candidate | point | take | n | pool | mean | mean 90% CI | total | ex top 3 | days+ | promote |",
                "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for cand in frozen.get("candidates") or []:
            lines.append(_md_frozen(cand))
        lines.append("")
    lines.extend(
        [
        "## Folds",
        "",
        "| fold | rule | train labeled | train wins | test n |",
        "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for fold in board["entry"]["folds"]:
        lines.append(
            f"| {fold['fold']} | {fold.get('rule_id')} | {fold.get('train_labeled')} | {fold.get('train_wins')} | {fold.get('test_n')} |"
        )
    lines.extend(["", "## Out of sample vs buy-all at the same decision point", ""])
    lines.append("Baseline is every realized fill at that decision point (same latency, fold's rule), not a bankroll.")
    lines.append("")
    lines.append("| point | take | n | median SOL | total SOL | win |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for point in board["entry"]["by_point"]:
        base = point["baseline"]
        lines.append(_md_pnl(point["point"], "all", base))
        for take in point["top"]:
            lines.append(_md_pnl(point["point"], f"top {take['fraction']:.0%}", take))
    lines.extend(
        [
            "",
            "## Heavy-tail yardsticks",
            "",
            "Median often sits on the round-trip fee, so it is not the promotion yardstick. Raw totals move with one trade.",
            f"Mean and total 90% CIs resample tokens ({BOOTSTRAP_DRAWS} draws, seed {BOOTSTRAP_SEED}). "
            f"Winsorized mean caps each trade at the {WINSOR_P:.0%} and {1 - WINSOR_P:.0%} percentiles of that book.",
            "Max drawdown is the peak-to-trough of cumulative SOL on independent fills ordered by decision time. Peak starts at 0.",
            f"Promotion requires all of: {PROMOTION_RULE}.",
            "",
            "| point | take | n | mean | mean 90% CI | total | total 90% CI | winsor mean | ex top 3 | days+ | max DD | promote |",
            "| --- | --- | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for point in board["entry"]["by_point"]:
        lines.append(_md_robust(point["point"], "all", point["baseline"]))
        for take in point["top"]:
            lines.append(_md_robust(point["point"], f"top {take['fraction']:.0%}", take))
    lines.extend(["", "Per-day totals (UTC), same books.", "", "| point | take | day | n | total SOL |", "| --- | --- | --- | ---: | ---: |"])
    for point in board["entry"]["by_point"]:
        _md_days(lines, point["point"], "all", point["baseline"])
        for take in point["top"]:
            _md_days(lines, point["point"], f"top {take['fraction']:.0%}", take)
    mig = board["entry"].get("migrate_predeclared") or {}
    lines.extend(
        [
            "",
            "## Migration entry, predeclared tp50/sl30",
            "",
            "First PumpSwap print, fill +1s. Exit is the signal-scan training pick, not re-chosen here.",
            "Follow and crowd columns are on the packet; those scans lost out of sample as rules.",
            f"Hourly PumpSwap rows without `quote_is_wsol` are dropped. The fix has been live on the tape since {QUOTE_WSOL_LIVE_AT}, so migration decision points populate from prints after that.",
            "",
            "| exit | n | median SOL | total SOL | win |",
            "| --- | ---: | ---: | ---: | ---: |",
            _md_pnl("migrate", "tp50_sl30", mig.get("oos_tp50_sl30") or {"n": 0, "total_sol": 0}),
            _md_pnl("migrate", "hold_30s", mig.get("oos_hold_30s") or {"n": 0, "total_sol": 0}),
        ]
    )
    tp = mig.get("oos_tp50_sl30") or {}
    hold = mig.get("oos_hold_30s") or {}
    if tp.get("mean_ci90_sol") is not None or hold.get("mean_ci90_sol") is not None:
        lines.extend(
            [
                "",
                "| point | take | n | mean | mean 90% CI | total | total 90% CI | winsor mean | ex top 3 | days+ | max DD | promote |",
                "| --- | --- | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
                _md_robust("migrate", "tp50_sl30", tp),
                _md_robust("migrate", "hold_30s", hold),
            ]
        )
    lines.extend(["", "## Precision at score thresholds (pooled OOS)", ""])
    lines.append("| threshold | n | precision | median SOL | total SOL |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for row in board["entry"]["thresholds"]:
        prec = "" if row["precision"] is None else f"{row['precision'] * 100:.1f}%"
        med = "" if row["median_sol"] is None else f"{row['median_sol']:.6f}"
        lines.append(f"| {row['threshold']:.1f} | {row['n']} | {prec} | {med} | {row['total_sol']:.6f} |")
    lines.extend(
        [
            "",
            "Same promotion rule on the score cuts.",
            "",
            "| threshold | take | n | mean | mean 90% CI | total | total 90% CI | winsor mean | ex top 3 | days+ | max DD | promote |",
            "| --- | --- | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in board["entry"]["thresholds"]:
        lines.append(_md_robust(f"{row['threshold']:.1f}", "score", row))
    lines.extend(["", "| threshold | take | day | n | total SOL |", "| --- | --- | --- | ---: | ---: |"])
    for row in board["entry"]["thresholds"]:
        _md_days(lines, f"{row['threshold']:.1f}", "score", row)
    lines.extend(["", "## Barrier targets and ladder exits", ""])
    lines.append(
        "Hit +100% before −30%, and +50% before −25%, within 30 minutes of the fill. "
        "The classifier is trained on that bit. The book is the matching ladder: "
        "sell half at the upside, trail the rest, hard stop at the downside, 30-minute cap."
    )
    lines.append("Event clocks (curve 20/40/60/80, clean-buyer counts, migration) are included in the points above and here.")
    lines.append(f"Promotion is the same rule: {PROMOTION_RULE}.")
    barrier_deploy = board["entry"].get("barrier_deploy") or {}
    if barrier_deploy:
        lines.append(
            f"Deploy file `{barrier_deploy.get('model_file')}` is a fit on every labeled row "
            f"({barrier_deploy.get('hits')} hits / {barrier_deploy.get('labeled')}). "
            "The forward paper ladder book reloads that file. It is not the out-of-sample table."
        )
    for block in board["entry"].get("barriers") or []:
        lines.extend(["", f"### {block['target']} → {block['pnl_rule']}", ""])
        hits = sum(int(fold.get("train_hits") or 0) for fold in block.get("folds") or [])
        labeled = sum(int(fold.get("train_labeled") or 0) for fold in block.get("folds") or [])
        lines.append(f"OOS labeled {block.get('oos_n')}. Train hits {hits} / {labeled} (folds summed, so rows repeat).")
        lines.append("")
        lines.append("| point | take | n | mean | mean 90% CI | total | winsor mean | ex top 3 | days+ | max DD | promote |")
        lines.append("| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |")
        for point in block.get("by_point") or []:
            lines.append(_md_robust_short(point["point"], "all", point["baseline"]))
            for take in point["top"]:
                if take["fraction"] not in (0.01, 0.10):
                    continue
                lines.append(_md_robust_short(point["point"], f"top {take['fraction']:.0%}", take))
        lines.append("")
        lines.append("Top gain on this target:")
        for row in (block.get("importance") or [])[:8]:
            lines.append(f"- {row['name']} {row['share'] * 100:.1f}%")
    lines.extend(["", "## Feature importance (mean gain across OOS folds)", ""])
    lines.append("This list is the pnl>0 model (train-chosen grid rule), not the barrier classifiers.")
    lines.append("")
    lines.append("| feature | mean gain | share |")
    lines.append("| --- | ---: | ---: |")
    for row in board["entry"]["importance"][:15]:
        lines.append(f"| {row['name']} | {row['gain']:.4f} | {row['share'] * 100:.1f}% |")
    lat = board["entry"]["deploy"].get("predict_latency") or {}
    lines.extend(
        [
            "",
            "## Predict latency (one packet, deploy model)",
            "",
            f"p50 {lat.get('p50_us')} µs, p90 {lat.get('p90_us')} µs, n={lat.get('n')}.",
            "",
            "## Exit model",
            "",
        ]
    )
    exit_eval = board["entry"]["exit"]
    lines.append(
        f"Positions {exit_eval['positions']}, early exits {exit_eval['early_exits']}, backend {exit_eval['backend']}."
    )
    lines.append("")
    lines.append("| policy | n | median SOL | total SOL | win |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    lines.append(_md_pnl("exit", "model or rule", exit_eval["policy"]))
    lines.append(_md_pnl("exit", "rule only", exit_eval["rule_hold"]))
    lines.extend(["", "## Wallet flags at end of tape (diagnostic, not a feature)", ""])
    diag = board["wallet"]
    lines.append(
        f"Wallets {diag['wallets']}, bots {diag['bots_end']}, sniper wallets {diag['sniper_wallets_end']}, "
        f"leaders {diag['leaders_end']} (peak {diag['leaders_peak']}). "
        "Leader flags reuse the noisy_v0 shape (3 closed mints, win rate 30–75%, hold 15s–30m, "
        "≤70% of profit from one mint, ≥0.05 SOL invested) on average-cost tape PnL as of the decision. "
        "An end-of-tape leaderboard file is not joined: it would leak."
    )
    lines.append("")
    return "\n".join(lines)


def _md_latency_row(name: str, summary: dict[str, Any]) -> str:
    med = "" if summary.get("median_sol") is None else f"{summary['median_sol']:.6f}"
    mean = "" if summary.get("mean_sol") is None else f"{summary['mean_sol']:.6f}"
    return f"| {name} | {summary.get('n', 0)} | {med} | {mean} | {summary.get('total_sol', 0):.6f} |"


def _md_pnl(point: str, take: str, summary: dict[str, Any]) -> str:
    med = "" if summary.get("median_sol") is None else f"{summary['median_sol']:.6f}"
    win = "" if summary.get("win_rate") is None else f"{summary['win_rate'] * 100:.1f}%"
    return f"| {point} | {take} | {summary.get('n')} | {med} | {summary.get('total_sol', 0):.6f} | {win} |"


def _fmt_sol4(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.4f}"


def _fmt_ci(pair: Any) -> str:
    if not pair:
        return ""
    return f"{float(pair[0]):.4f} .. {float(pair[1]):.4f}"


def _md_robust(point: str, take: str, summary: dict[str, Any]) -> str:
    days_pos = summary.get("days_positive")
    n_days = summary.get("n_days")
    days = "" if days_pos is None or n_days is None else f"{days_pos}/{n_days}"
    promote = summary.get("promote")
    flag = "" if promote is None else ("yes" if promote else "no")
    return (
        f"| {point} | {take} | {summary.get('n')} | {_fmt_sol4(summary.get('mean_sol'))} | "
        f"{_fmt_ci(summary.get('mean_ci90_sol'))} | {_fmt_sol4(summary.get('total_sol'))} | "
        f"{_fmt_ci(summary.get('total_ci90_sol'))} | {_fmt_sol4(summary.get('winsorized_mean_sol'))} | "
        f"{_fmt_sol4(summary.get('total_ex_top3_sol'))} | {days} | "
        f"{_fmt_sol4(summary.get('max_drawdown_sol'))} | {flag} |"
    )


def _md_robust_short(point: str, take: str, summary: dict[str, Any]) -> str:
    days_pos = summary.get("days_positive")
    n_days = summary.get("n_days")
    days = "" if days_pos is None or n_days is None else f"{days_pos}/{n_days}"
    promote = summary.get("promote")
    flag = "" if promote is None else ("yes" if promote else "no")
    return (
        f"| {point} | {take} | {summary.get('n')} | {_fmt_sol4(summary.get('mean_sol'))} | "
        f"{_fmt_ci(summary.get('mean_ci90_sol'))} | {_fmt_sol4(summary.get('total_sol'))} | "
        f"{_fmt_sol4(summary.get('winsorized_mean_sol'))} | {_fmt_sol4(summary.get('total_ex_top3_sol'))} | "
        f"{days} | {_fmt_sol4(summary.get('max_drawdown_sol'))} | {flag} |"
    )


def _md_frozen(cand: dict[str, Any]) -> str:
    fraction = float(cand.get("fraction") or 0)
    take = "all" if fraction >= 1 else f"top {fraction:.0%}"
    days_pos = cand.get("days_positive")
    n_days = cand.get("n_days")
    days = "" if days_pos is None or n_days is None else f"{days_pos}/{n_days}"
    promote = cand.get("promote")
    flag = "" if promote is None else ("yes" if promote else "no")
    return (
        f"| {cand.get('id')} | {cand.get('point')} | {take} | {cand.get('n')} | {cand.get('n_pool')} | "
        f"{_fmt_sol4(cand.get('mean_sol'))} | {_fmt_ci(cand.get('mean_ci90_sol'))} | "
        f"{_fmt_sol4(cand.get('total_sol'))} | {_fmt_sol4(cand.get('total_ex_top3_sol'))} | {days} | {flag} |"
    )


def _md_days(lines: list[str], point: str, take: str, summary: dict[str, Any]) -> None:
    for day in summary.get("days") or []:
        lines.append(f"| {point} | {take} | {day['day']} | {day['n']} | {day['total_sol']:.4f} |")


def _discover(directory: Path, patterns: Sequence[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found.extend(directory.glob(pattern))
    return sorted({path.resolve() for path in found if path.is_file()})


def tape_day_tokens(paths: Sequence[Path]) -> set[str]:
    """Calendar days named in trades-YYYY-MM-DD files. Used to skip older observe logs."""
    out: set[str] = set()
    for path in paths:
        name = path.name
        if not name.startswith("trades-"):
            continue
        token = name[len("trades-") : len("trades-") + 10]
        if len(token) == 10 and token[4] == "-" and token[7] == "-":
            out.add(token)
    return out


def run_files(
    *,
    tape: Sequence[Path],
    creates: Sequence[Path],
    output_dir: Path,
    n_folds: int,
    min_rule_n: int,
    backend: str | None,
    offsets_ms: Sequence[int],
    size_lamports: int,
    slippage_cap: float,
) -> dict[str, Any]:
    if not tape:
        raise SystemExit("no tape files")
    out_text = str(output_dir)
    if "/sealed/trades" in out_text or out_text.rstrip("/").endswith("/sealed/trades"):
        raise SystemExit("refusing to write into the trade tape directory")
    compressed = any(path.name.endswith(".zst") or path.suffix == ".gz" for path in tape)
    t_min = t_max = None
    if not compressed:
        from tools.paper_price_path import peek_trade_bounds

        for path in tape:
            lo, hi = peek_trade_bounds(path)
            if lo is not None and (t_min is None or lo < t_min):
                t_min = lo
            if hi is not None and (t_max is None or hi > t_max):
                t_max = hi
    create_hi = None if t_max is None else t_max + 180_000
    create_map = load_creates(creates, t_min_ms=t_min, t_max_ms=create_hi)
    print(f"creates_loaded={len(create_map)}", file=sys.stderr)
    books, stats = load_books(create_map, tape)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("tape has no t_recv_ms")
    kept = filter_window({mint: book.path for mint, book in books.items()}, stats.t_min_ms, stats.t_max_ms)
    books = {mint: books[mint] for mint in kept}
    print(f"creates_in_window={len(books)} kept_prints={stats.kept}", file=sys.stderr)
    chain_lags = list(getattr(stats, "chain_lags_ms", []))
    rows, ticks, wallet_diag = build_dataset(
        books,
        tape_end_ms=stats.t_max_ms,
        offsets_ms=offsets_ms,
        chain_lags_ms=chain_lags,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    print("latency_sensitivity", file=sys.stderr)
    latency_report = entry_latency_report(
        books,
        rows,
        chain_lags,
        tape_end_ms=stats.t_max_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    modeled = run_models(
        rows,
        ticks,
        n_folds=n_folds,
        min_rule_n=min_rule_n,
        backend=backend,
        output_dir=output_dir,
    )
    hours = max(0.0, (stats.t_max_ms - stats.t_min_ms) / 3_600_000)
    note = data_needed_note(hours=hours, creates=len(books), oos_n=int(modeled["oos_n"]))
    board = {
        "schema": SCHEMA,
        "creates": len(books),
        "decisions": len(rows),
        "exit_ticks": len(ticks),
        "entry_latency_ms": ENTRY_LATENCY_MS,
        "entry_latency": latency_report,
        "offsets_ms": list(offsets_ms),
        "size_lamports": size_lamports,
        "slippage_cap": slippage_cap,
        "tape_start_ms": stats.t_min_ms,
        "tape_end_ms": stats.t_max_ms,
        "hours": hours,
        "scan": stats.as_dict(),
        "wallet": wallet_diag,
        "data_needed": note,
        "entry": {k: v for k, v in modeled.items() if k != "oos_scores"},
        "assumptions": {
            "no_lookahead": "Features use prints with t_recv_ms <= decision time, and wallet flags updated only by those prints.",
            "primary_rule": "Each walk-forward fold picks the exit rule with the best median realized PnL on its training rows.",
            "rugs": "no_exit_liquidity stays in the realized book.",
            "notional": "Totals sum independent 0.05 SOL trades. Not a bankroll.",
            "leaderboard": "Causal noisy_v0-shaped flags. The PR #75 end-of-tape board is not joined.",
            "threads": "num_threads=1. Daily job is nice/ionice, off the recorder process.",
            "heavy_tail": (
                "Median is not the promotion yardstick. Mean and total use a token bootstrap "
                f"({BOOTSTRAP_DRAWS} draws, seed {BOOTSTRAP_SEED}). Winsorized mean caps the "
                f"{WINSOR_P:.0%}/{1 - WINSOR_P:.0%} percentiles. Also reported: total without the "
                "best trade, UTC day totals, and max drawdown of the cumulative fill path."
            ),
            "promotion": PROMOTION_RULE,
            "quote_is_wsol": (
                "PumpSwap prints without quote_is_wsol are dropped. The tape fix has been live since "
                f"{QUOTE_WSOL_LIVE_AT}, so migration decision points populate from later prints."
            ),
            "event_clocks": (
                "Decision points also fire the first time curve progress crosses 20/40/60/80%, "
                "the first time unique non-vetoed buyers reach 5/10/20, and at the first PumpSwap print. "
                "Features use only prints with t_recv_ms <= that decision."
            ),
            "barrier": (
                "A second classifier is trained on reaching +100% before -30% within 30 minutes, "
                "and on +50% before -25%. Its book is the ladder PnL: sell half at the upside, "
                "trail the rest, hard stop at the downside, 30-minute cap. Promotion is unchanged."
            ),
            "frozen_holdout": (
                f"Three candidates were frozen at {CANDIDATE_FREEZE_AT}. "
                "buyers_8 top 5% uses the +100%/-30% model and the 2x ladder. "
                "T+30s top 1% uses the pnl>0 model and hold_30s. "
                "Migration is every post-freeze migrate fill under hold_30s, with no model. "
                "Fits use only decisions at or before the freeze. "
                "Scores use only decisions strictly after it. "
                "The exploratory walk-forward still uses the whole tape and is reported separately."
            ),
        },
    }
    (output_dir / "scoreboard.json").write_text(json.dumps(_json_safe(board), indent=2) + "\n", encoding="utf-8")
    (output_dir / "scoreboard.md").write_text(format_markdown(board), encoding="utf-8")
    with (output_dir / "oos.jsonl").open("w", encoding="utf-8") as fh:
        for row in modeled["oos_scores"]:
            fh.write(json.dumps(_json_safe(row), separators=(",", ":")) + "\n")
    print(note, file=sys.stderr)
    return board


def _parse_offsets(text: str) -> tuple[int, ...]:
    parts = tuple(int(float(p) * 1000) for p in text.split(",") if p.strip())
    if not parts or any(p <= 0 for p in parts):
        raise SystemExit("decision offsets must be positive seconds")
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LAYA v0 paper entry/exit model on the trade tape")
    parser.add_argument("--tape", nargs="*", type=Path, default=[])
    parser.add_argument("--creates", nargs="*", type=Path, default=[])
    parser.add_argument("--tape-dir", type=Path)
    parser.add_argument("--creates-dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--offsets", default="5,15,30,60,120", help="decision offsets in seconds")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-rule-n", type=int, default=MIN_RULE_N)
    parser.add_argument("--backend", choices=("lightgbm", "sklearn"))
    parser.add_argument("--size-sol", type=float, default=0.05)
    parser.add_argument("--slippage-cap", type=float, default=DEFAULT_SLIPPAGE_CAP)
    args = parser.parse_args(argv)
    tape = list(args.tape)
    creates = list(args.creates)
    if args.tape_dir:
        tape.extend(_discover(args.tape_dir, ("trades-*.jsonl", "trades-*.jsonl.zst", "trades-*.jsonl.gz")))
    if args.creates_dir:
        discovered = _discover(args.creates_dir, ("observe-*.jsonl", "observe-*.jsonl.zst"))
        days = tape_day_tokens(tape)
        if days:
            matched = [path for path in discovered if any(day in path.name for day in days)]
            discovered = matched or discovered
        creates.extend(discovered)
    # Stable, unique.
    tape = sorted({path.resolve() for path in tape})
    creates = sorted({path.resolve() for path in creates})
    print(f"tape_files={len(tape)} create_files={len(creates)}", file=sys.stderr)
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    board = run_files(
        tape=tape,
        creates=creates,
        output_dir=args.output_dir,
        n_folds=args.folds,
        min_rule_n=args.min_rule_n,
        backend=args.backend,
        offsets_ms=_parse_offsets(args.offsets),
        size_lamports=int(round(args.size_sol * LAMPORTS_PER_SOL)),
        slippage_cap=args.slippage_cap,
    )
    deploy = board["entry"]["deploy"]
    sys.stdout.write(
        f"laya_v0 decisions={board['decisions']} oos={board['entry']['oos_n']} "
        f"backend={deploy['backend']} rule={deploy['rule_id']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
