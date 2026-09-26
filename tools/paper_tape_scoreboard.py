#!/usr/bin/env python3
"""Paper scoreboard on the pump.fun trade tape.

Buy every new create at T+L (default L=1s) and a seeded random subsample.
Per-token per-exit SOL PnL is written as JSONL with features knowable at T.
Rugs and no-exit sells stay in the realized book. Censored holds (the tape
ended first) are counted and are not given a fake zero.

This is not a bankroll path: total SOL is the sum of independent 0.05 SOL
trades, including names that would have overlapped.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    DEFAULT_SLIPPAGE_CAP,
    LAMPORTS_PER_SOL,
    PRIORITY_FEE_LAMPORTS,
    TOKEN_ACCOUNT_RENT_LAMPORTS,
    TOKEN_RAW_OFFSET,
    market_cap_sol,
    quote_buy,
    quote_sell,
    reserves_with_our_buy,
    spot_sol_per_ui,
)
from tools.paper_price_path import (
    MintPath,
    TapePrint,
    iter_price_path_jsonl,
    fillable_prints,
    load_creates,
    peek_trade_bounds,
    state_as_of,
    stream_paths,
)

SCHEMA_LABEL = "paper_exit_label_v1"
SCHEMA_BOARD = "paper_tape_scoreboard_v1"
DEFAULT_LATENCIES = (0.5, 1.0, 2.0, 5.0)
HEADLINE_LATENCY = 1.0
# 15% is inside the 10–20% band. The public tape's 28.9% err!=null share is an
# upper bound (other people's reverted swaps, not our sends). execution-stack
# says score 0/10/30 until measured and not to pick the rate that flatters;
# the headline is the middle of the requested band, and 0/10/25% are always
# reported beside it. 0% is the unflattered tape. 25% flatters a negative book
# more than 15% because a failed entry only burns the priority fee.
DEFAULT_FAIL_RATE = 0.15
FAIL_RATES = (0.0, 0.10, 0.25)
MISS_STATUSES = (
    "missed_slippage",
    "missed_curve_complete",
    "missed_no_liquidity",
    "missed_no_state",
)
RANDOM_FRACTION = 0.20
RANDOM_SEED = 1
MAX_HOLD_MS = 30 * 60 * 1000

ASSUMPTIONS: dict[str, Any] = {
    "clock": "create signal is observe t_ws; tape state uses t_recv_ms. event_ts is not a decision clock.",
    "entry": "reserves after the last print with t<=T+L. Create-payload reserves only if the tape has no print yet.",
    "pumpswap_reserves": (
        "PumpSwap reserves on the tape are the pool before that trade. Base moves by token_raw. "
        "Quote moves by the pool-net amount: decoded events add quote_in+lp_fee on a buy and "
        "remove the CP gross on a sell. Tape rows that only have user_quote use the canonical "
        "venue fee, not the gross user amount. Bonding-curve reserves are already post-trade."
    ),
    "own_impact": (
        "Our virtual buy stays in the same-venue reserves used for later prints and the exit. "
        "A later tape print does not erase it. A migrated pool is a different book."
    ),
    "real_sol_cap": (
        "When bonding virtual quote is at least 30 SOL, a sell that asks for more than "
        "virtual-30 reverts. When the tape's virtual quote is already below 30 SOL, that "
        "reserve is the cap: those curves are still paying sells. The cap is the tape "
        "reserve, not the reserve after our paper buy is added back."
    ),
    "fees": "PumpPortal 0.5% then venue fee, sequential. Buys: fees out of input. Sells: fees out of SOL output.",
    "bonding_fee": "1.25% flat (pump.fun fees page, 20 May 2026), not the 95 bps protocol slice alone.",
    "pumpswap_fee": "Canonical SOL market-cap tiers from that page. Migrated creates are treated as canonical.",
    "priority_lamports": PRIORITY_FEE_LAMPORTS,
    "rent_lamports": TOKEN_ACCOUNT_RENT_LAMPORTS,
    "rent_policy": "Recovered when the sell lands. Stuck when the sell cannot be sent.",
    "no_partial_fills": True,
    "slippage": (
        "Miss if marginal spot or pre-fee executable price is more than the cap above the T quote. "
        "The miss stays in n and costs the priority fee. It is not dropped and it is not a filled loss of the size."
    ),
    "rugs": "no_exit_liquidity is a realized loss (size + both priority fees + rent) and is inside n, median, mean, and total.",
    "censored": "Exit time after the last tape timestamp has null pnl and is outside the realized n.",
    "fail_rates": (
        f"Headline fail rate is {DEFAULT_FAIL_RATE:.0%}. A failed entry stays in n and costs the "
        "priority fee only, which flatters a negative book versus the landed trade. "
        "Sensitivity is always reported at 0, 10, and 25 percent. 0 percent is the unflattered tape."
    ),
    "priority_fee_why": (
        "0.001 SOL per side. PumpPortal examples are 0.00001–0.00005 SOL. "
        "50k lamports is that tutorial ceiling, not a launch where 28.9% of public-RPC trade logs already failed."
    ),
    "non_wsol": (
        "Prints with quote_is_wsol not true (false, or a PumpSwap row with the flag missing) "
        "are excluded from SOL PnL and counted as non_wsol."
    ),
    "random_baseline": f"fraction {RANDOM_FRACTION} of in-window creates, Random seed {RANDOM_SEED}, mints sorted before sample.",
    "notional": "Sum of per-trade pnl. Not a 1 SOL float and not a concurrency cap.",
    "features": "f_* columns use only the create payload and tape prints with t_recv_ms <= T.",
}


@dataclass(frozen=True, slots=True)
class ExitRule:
    rule_id: str
    kind: str  # hold, tpsl, trail
    hold_ms: int | None = None
    tp: float | None = None
    sl: float | None = None
    trail: float | None = None
    max_hold_ms: int = MAX_HOLD_MS


# Fixed holds plus a small documented grid. Not searched to improve the baseline.
EXIT_RULES: tuple[ExitRule, ...] = (
    ExitRule("hold_30s", "hold", hold_ms=30_000),
    ExitRule("hold_1m", "hold", hold_ms=60_000),
    ExitRule("hold_2m", "hold", hold_ms=120_000),
    ExitRule("hold_5m", "hold", hold_ms=300_000),
    ExitRule("hold_10m", "hold", hold_ms=600_000),
    ExitRule("hold_15m", "hold", hold_ms=900_000),
    ExitRule("hold_30m", "hold", hold_ms=1_800_000),
    ExitRule("tp50_sl30", "tpsl", tp=0.50, sl=0.30),
    ExitRule("tp100_sl50", "tpsl", tp=1.00, sl=0.50),
    ExitRule("tp200_sl50", "tpsl", tp=2.00, sl=0.50),
    ExitRule("trail30", "trail", trail=0.30),
    ExitRule("trail50", "trail", trail=0.50),
)


@dataclass
class EntryFill:
    status: str
    t_entry_ms: int
    venue: str | None = None
    spot_sol: float | None = None
    tokens_raw: int = 0
    net_in_lamports: int = 0
    quote_reserve: int = 0
    base_reserve: int = 0
    quote_after: int = 0
    base_after: int = 0
    fee_ppm: int | None = None
    state_t_ms: int | None = None


def _sol(lamports: int | None) -> float | None:
    if lamports is None:
        return None
    return lamports / LAMPORTS_PER_SOL


def features_at_t(path: MintPath) -> dict[str, Any]:
    """Knowable at the create signal. Prints after T are not read."""
    t_ms = path.create.t_signal_ms
    n = buy_n = sell_n = 0
    vol = 0
    last: TapePrint | None = None
    for pr in path.prints:
        if pr.t_recv_ms > t_ms:
            break
        n += 1
        vol += pr.sol_lamports
        if pr.side == "sell":
            sell_n += 1
        else:
            buy_n += 1
        last = pr
    c = path.create
    return {
        "f_create_v_sol": c.v_sol,
        "f_create_v_token_ui": c.v_token_ui,
        "f_create_mcap_sol": c.mcap_sol,
        "f_create_initial_buy_ui": c.initial_buy_ui,
        "f_create_sol_amount": c.sol_amount,
        "f_tape_n": n,
        "f_tape_buy_n": buy_n,
        "f_tape_sell_n": sell_n,
        "f_tape_vol_lamports": vol,
        "f_tape_last_price_sol": None if last is None else last.price_sol,
        "f_tape_last_mcap_sol": None if last is None else last.market_cap_sol,
        "f_tape_last_quote_lamports": None if last is None else last.quote_reserve,
        "f_tape_last_base_raw": None if last is None else last.base_reserve,
        "f_tape_last_venue": None if last is None else last.venue,
    }


def reference_spot(path: MintPath, feats: dict[str, Any]) -> float | None:
    price = feats.get("f_tape_last_price_sol")
    if isinstance(price, (int, float)) and price > 0:
        return float(price)
    if path.create.v_sol and path.create.v_token_ui and path.create.v_token_ui > 0:
        return path.create.v_sol / path.create.v_token_ui
    if path.create.mcap_sol and path.create.mcap_sol > 0:
        return path.create.mcap_sol / 1_000_000_000
    return None


def try_entry(
    path: MintPath,
    *,
    t_entry_ms: int,
    size_lamports: int,
    slippage_cap: float,
    feats: dict[str, Any],
) -> EntryFill:
    state = state_as_of(path, t_entry_ms, allow_anchor=True)
    if state is None:
        return EntryFill(status="missed_no_state", t_entry_ms=t_entry_ms)
    ref = reference_spot(path, feats)
    spot = state.price_sol
    if ref and ref > 0 and spot > ref * (1.0 + slippage_cap):
        return EntryFill(status="missed_slippage", t_entry_ms=t_entry_ms, venue=state.venue, spot_sol=spot, state_t_ms=state.t_recv_ms)
    mcap = state.market_cap_sol or market_cap_sol(state.quote_reserve, state.base_reserve)
    buy = quote_buy(
        venue=state.venue,
        size_lamports=size_lamports,
        quote_lamports=state.quote_reserve,
        base_raw=state.base_reserve,
        market_cap=mcap,
    )
    if buy is None:
        real_closed = state.venue == "pump_bonding" and state.base_reserve <= TOKEN_RAW_OFFSET
        status = "missed_curve_complete" if real_closed else "missed_no_liquidity"
        return EntryFill(status=status, t_entry_ms=t_entry_ms, venue=state.venue, spot_sol=spot, state_t_ms=state.t_recv_ms)
    if ref and ref > 0 and buy.tokens_raw > 0:
        tokens_ui = buy.tokens_raw / 1_000_000
        net_sol = buy.net_in_lamports / LAMPORTS_PER_SOL
        executable = net_sol / tokens_ui
        if executable > ref * (1.0 + slippage_cap):
            return EntryFill(
                status="missed_slippage",
                t_entry_ms=t_entry_ms,
                venue=state.venue,
                spot_sol=spot,
                state_t_ms=state.t_recv_ms,
            )
    return EntryFill(
        status="filled",
        t_entry_ms=t_entry_ms,
        venue=state.venue,
        spot_sol=spot,
        tokens_raw=buy.tokens_raw,
        net_in_lamports=buy.net_in_lamports,
        quote_reserve=state.quote_reserve,
        base_reserve=state.base_reserve,
        quote_after=buy.quote_after,
        base_after=buy.base_after,
        fee_ppm=buy.venue_fee_ppm,
        state_t_ms=state.t_recv_ms,
    )


def _plan_exit(path: MintPath, entry: EntryFill, rule: ExitRule, latency_ms: int) -> tuple[str, int]:
    assert rule.kind == "hold" or (entry.spot_sol is not None and entry.spot_sol > 0)
    if rule.kind == "hold":
        assert rule.hold_ms is not None
        return "hold", entry.t_entry_ms + rule.hold_ms
    assert entry.spot_sol is not None and entry.spot_sol > 0
    mark = _entry_mark(entry)
    peak = mark
    deadline = entry.t_entry_ms + rule.max_hold_ms
    for pr in fillable_prints(path):
        if pr.t_recv_ms <= entry.t_entry_ms:
            continue
        if pr.t_recv_ms > deadline:
            break
        spot = _spot_with_our_buy(entry, pr)
        if spot is None or spot <= 0:
            continue
        if rule.kind == "trail":
            assert rule.trail is not None
            if spot > peak:
                peak = spot
                continue
            if spot <= peak * (1.0 - rule.trail):
                return "trail", pr.t_recv_ms + latency_ms
        elif rule.kind == "tpsl":
            assert rule.tp is not None and rule.sl is not None
            ret = spot / mark - 1.0
            if ret >= rule.tp:
                return "tp", pr.t_recv_ms + latency_ms
            if ret <= -rule.sl:
                return "sl", pr.t_recv_ms + latency_ms
    return "time_stop", deadline


def _entry_mark(entry: EntryFill) -> float:
    """Spot of the book after our buy. Stops are measured from that, not the pre-buy tape."""
    if entry.quote_after > 0 and entry.base_after > 0:
        spot = spot_sol_per_ui(entry.quote_after, entry.base_after)
        if spot > 0:
            return spot
    return float(entry.spot_sol or 0.0)


def _spot_with_our_buy(entry: EntryFill, pr: TapePrint) -> float | None:
    book = reserves_with_our_buy(
        quote_lamports=pr.quote_reserve,
        base_raw=pr.base_reserve,
        net_in_lamports=entry.net_in_lamports,
        tokens_raw=entry.tokens_raw,
        same_venue=pr.venue == entry.venue,
    )
    if book is None:
        return None
    spot = spot_sol_per_ui(book[0], book[1])
    return spot if spot > 0 else None


def _book_for_exit(entry: EntryFill, state: TapePrint) -> tuple[int, int, str] | None:
    book = reserves_with_our_buy(
        quote_lamports=state.quote_reserve,
        base_raw=state.base_reserve,
        net_in_lamports=entry.net_in_lamports,
        tokens_raw=entry.tokens_raw,
        same_venue=state.venue == entry.venue,
    )
    if book is None:
        return None
    return book[0], book[1], state.venue


def simulate_exit(
    path: MintPath,
    entry: EntryFill,
    rule: ExitRule,
    *,
    latency_ms: int,
    tape_end_ms: int,
    size_lamports: int,
) -> dict[str, Any]:
    if entry.status != "filled":
        # A send that does not fill still burns the priority fee when it is included.
        # Count it. Do not drop it from n.
        cost = -PRIORITY_FEE_LAMPORTS if entry.status in MISS_STATUSES else None
        return {
            "exit_status": "not_entered",
            "trigger": None,
            "exit_t_ms": None,
            "exit_venue": None,
            "exit_spot_sol": None,
            "exit_sol_lamports": None,
            "pnl_lamports": cost,
            "attempt_cost_lamports": cost,
        }
    trigger, t_fill = _plan_exit(path, entry, rule, latency_ms)
    base = {
        "trigger": trigger,
        "exit_t_ms": t_fill,
        "exit_venue": None,
        "exit_spot_sol": None,
        "exit_sol_lamports": None,
    }
    if t_fill > tape_end_ms:
        return {**base, "exit_status": "censored", "pnl_lamports": None}
    state = state_as_of(path, t_fill, allow_anchor=True)
    if state is None or entry.venue is None:
        return {**base, "exit_status": "no_exit_liquidity", "pnl_lamports": _stuck_loss(size_lamports)}
    booked = _book_for_exit(entry, state)
    if booked is None:
        return {**base, "exit_status": "no_exit_liquidity", "pnl_lamports": _stuck_loss(size_lamports)}
    quote, base_raw, venue = booked
    mcap = market_cap_sol(quote, base_raw)
    spot = spot_sol_per_ui(quote, base_raw)
    sol_out = quote_sell(
        venue=venue,
        tokens_raw=entry.tokens_raw,
        quote_lamports=quote,
        base_raw=base_raw,
        market_cap=mcap,
        payable_quote_lamports=state.quote_reserve if venue == "pump_bonding" else None,
    )
    base.update({"exit_venue": venue, "exit_spot_sol": spot})
    if sol_out is None:
        return {**base, "exit_status": "no_exit_liquidity", "pnl_lamports": _stuck_loss(size_lamports)}
    # Rent paid at entry comes back when the sell lands. Priority paid both sides.
    pnl = sol_out - size_lamports - 2 * PRIORITY_FEE_LAMPORTS
    return {**base, "exit_status": "realized", "exit_sol_lamports": sol_out, "pnl_lamports": pnl}


def _stuck_loss(size_lamports: int) -> int:
    """Tried to exit, transaction cannot land. Rent stays locked."""
    return -(size_lamports + 2 * PRIORITY_FEE_LAMPORTS + TOKEN_ACCOUNT_RENT_LAMPORTS)


def label_row(
    path: MintPath,
    feats: dict[str, Any],
    entry: EntryFill,
    exit_part: dict[str, Any],
    *,
    rule_id: str,
    latency_s: float,
    size_lamports: int,
) -> dict[str, Any]:
    pnl = exit_part.get("pnl_lamports")
    row: dict[str, Any] = {
        "schema": SCHEMA_LABEL,
        "mint": path.create.mint,
        "creator": path.create.creator,
        "create_signature": path.create.signature,
        "t_signal_ms": path.create.t_signal_ms,
        "latency_s": latency_s,
        "size_lamports": size_lamports,
        "exit_rule": rule_id,
        "entry_status": entry.status,
        "exit_status": exit_part["exit_status"],
        "trigger": exit_part.get("trigger"),
        "pnl_lamports": pnl,
        "pnl_sol": _sol(pnl),
        "entry_t_ms": entry.t_entry_ms,
        "entry_venue": entry.venue,
        "entry_spot_sol": entry.spot_sol,
        "entry_tokens_raw": entry.tokens_raw if entry.status == "filled" else None,
        "entry_quote_reserve": entry.quote_reserve if entry.status == "filled" else None,
        "entry_base_reserve": entry.base_reserve if entry.status == "filled" else None,
        "entry_fee_ppm": entry.fee_ppm,
        "exit_t_ms": exit_part.get("exit_t_ms"),
        "exit_venue": exit_part.get("exit_venue"),
        "exit_spot_sol": exit_part.get("exit_spot_sol"),
        "exit_sol_lamports": exit_part.get("exit_sol_lamports"),
    }
    row.update(feats)
    return row


def simulate_path(
    path: MintPath,
    *,
    latencies: Sequence[float],
    rules: Sequence[ExitRule],
    tape_end_ms: int,
    size_lamports: int,
    slippage_cap: float,
) -> list[dict[str, Any]]:
    feats = features_at_t(path)
    rows: list[dict[str, Any]] = []
    for latency_s in latencies:
        latency_ms = int(round(latency_s * 1000))
        t_entry = path.create.t_signal_ms + latency_ms
        entry = try_entry(
            path,
            t_entry_ms=t_entry,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            feats=feats,
        )
        for rule in rules:
            exit_part = simulate_exit(
                path,
                entry,
                rule,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            rows.append(
                label_row(
                    path,
                    feats,
                    entry,
                    exit_part,
                    rule_id=rule.rule_id,
                    latency_s=latency_s,
                    size_lamports=size_lamports,
                )
            )
    return rows


def simulate_book(
    paths: Iterable[MintPath],
    *,
    latencies: Sequence[float] = DEFAULT_LATENCIES,
    rules: Sequence[ExitRule] = EXIT_RULES,
    tape_end_ms: int,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for path in paths:
        labels.extend(
            simulate_path(
                path,
                latencies=latencies,
                rules=rules,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
                slippage_cap=slippage_cap,
            )
        )
    return labels


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    w = k - lo
    return sorted_vals[lo] * (1.0 - w) + sorted_vals[hi] * w


def _stats_from_lamports(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "median_sol": None,
            "mean_sol": None,
            "p10_sol": None,
            "p90_sol": None,
            "win_rate": None,
            "total_sol": 0.0,
            "total_lamports": 0,
        }
    ordered = sorted(values)
    n = len(ordered)
    total = sum(ordered)
    return {
        "n": n,
        "median_sol": _pct([float(v) for v in ordered], 0.50) / LAMPORTS_PER_SOL,
        "mean_sol": (total / n) / LAMPORTS_PER_SOL,
        "p10_sol": _pct([float(v) for v in ordered], 0.10) / LAMPORTS_PER_SOL,
        "p90_sol": _pct([float(v) for v in ordered], 0.90) / LAMPORTS_PER_SOL,
        "win_rate": sum(1 for v in ordered if v > 0) / n,
        "total_sol": total / LAMPORTS_PER_SOL,
        "total_lamports": total,
    }


def _headline_pnl(row: dict[str, Any], fail_rate: float, size_lamports: int) -> int | None:
    """One attempt, in lamports. Misses and failed entries stay in the sum.

    A slippage or no-fill attempt costs the priority fee. A failed entry
    (the fail rate) replaces the landed pnl with that same priority-fee cost
    for that fraction of attempts. Censored holds stay out.
    """
    del size_lamports  # stuck loss is already on the row
    entry = row.get("entry_status")
    pnl = row.get("pnl_lamports")
    if entry in MISS_STATUSES:
        return int(pnl) if isinstance(pnl, int) else None
    if entry != "filled" or not isinstance(pnl, int):
        return None
    if row.get("exit_status") not in ("realized", "no_exit_liquidity"):
        return None
    if fail_rate <= 0:
        return int(pnl)
    mixed = (1.0 - fail_rate) * float(pnl) + fail_rate * float(-PRIORITY_FEE_LAMPORTS)
    return int(round(mixed))


def _symmetric_ev(row: dict[str, Any], fail_rate: float, size_lamports: int) -> float | None:
    """Expected lamports. Failed entry pays priority only (can skip a loser)."""
    if row.get("entry_status") != "filled":
        return None
    status = row.get("exit_status")
    pnl = row.get("pnl_lamports")
    if status not in ("realized", "no_exit_liquidity") or pnl is None:
        return None
    if fail_rate <= 0:
        return float(pnl)
    p = fail_rate
    entry_fail = float(-PRIORITY_FEE_LAMPORTS)
    exit_fail = float(_stuck_loss(size_lamports))
    if status == "no_exit_liquidity":
        return (1.0 - p) * float(pnl) + p * entry_fail
    return p * entry_fail + (1.0 - p) * p * exit_fail + (1.0 - p) * (1.0 - p) * float(pnl)


def _exit_fail_only_ev(row: dict[str, Any], fail_rate: float, size_lamports: int) -> float | None:
    """Entry is kept. With probability p the exit reverts into a stuck loss."""
    if row.get("entry_status") != "filled":
        return None
    status = row.get("exit_status")
    pnl = row.get("pnl_lamports")
    if status not in ("realized", "no_exit_liquidity") or pnl is None:
        return None
    if fail_rate <= 0 or status == "no_exit_liquidity":
        return float(pnl)
    p = fail_rate
    return (1.0 - p) * float(pnl) + p * float(_stuck_loss(size_lamports))


def aggregate_rule(
    rows: Sequence[dict[str, Any]],
    *,
    size_lamports: int,
    fail_rates: Sequence[float],
    count_attempts: bool = False,
    headline_fail_rate: float = 0.0,
) -> dict[str, Any]:
    realized: list[int] = []
    censored_n = no_exit_n = miss_n = filled_n = 0
    rate_for_row = headline_fail_rate if count_attempts else 0.0
    for row in rows:
        entry = row.get("entry_status")
        status = row.get("exit_status")
        if entry != "filled":
            miss_n += 1
            if count_attempts:
                missed = _headline_pnl(row, 0.0, size_lamports)
                if missed is not None:
                    realized.append(missed)
            continue
        filled_n += 1
        if status == "censored":
            censored_n += 1
        elif status == "no_exit_liquidity":
            no_exit_n += 1
            landed = _headline_pnl(row, rate_for_row, size_lamports)
            if landed is not None:
                realized.append(landed)
        elif status == "realized":
            landed = _headline_pnl(row, rate_for_row, size_lamports)
            if landed is not None:
                realized.append(landed)
    stats = _stats_from_lamports(realized)
    stats["filled_n"] = filled_n
    stats["miss_n"] = miss_n
    stats["censored_n"] = censored_n
    stats["no_exit_n"] = no_exit_n
    stats["notional_sol"] = stats["n"] * size_lamports / LAMPORTS_PER_SOL
    if stats["mean_sol"] is not None and size_lamports:
        stats["mean_return_on_size"] = stats["mean_sol"] / (size_lamports / LAMPORTS_PER_SOL)
    else:
        stats["mean_return_on_size"] = None
    sensitivity: dict[str, Any] = {}
    for rate in fail_rates:
        key = f"{rate:g}"
        if count_attempts:
            sym_i = [v for row in rows if (v := _headline_pnl(row, rate, size_lamports)) is not None]
            sym = [float(v) for v in sym_i]
        else:
            sym = [v for row in rows if (v := _symmetric_ev(row, rate, size_lamports)) is not None]
        exo = [v for row in rows if (v := _exit_fail_only_ev(row, rate, size_lamports)) is not None]
        sensitivity[key] = {
            "n": len(sym),
            "symmetric_mean_sol": (sum(sym) / len(sym) / LAMPORTS_PER_SOL) if sym else None,
            "symmetric_total_sol": sum(sym) / LAMPORTS_PER_SOL if sym else 0.0,
            "exit_fail_only_mean_sol": (sum(exo) / len(exo) / LAMPORTS_PER_SOL) if exo else None,
            "exit_fail_only_total_sol": sum(exo) / LAMPORTS_PER_SOL if exo else 0.0,
        }
    stats["fail_sensitivity"] = sensitivity
    return stats


def score_labels(
    labels: Sequence[dict[str, Any]],
    *,
    book: str,
    latency_s: float,
    mints: set[str] | None,
    size_lamports: int,
    fail_rates: Sequence[float] = FAIL_RATES,
    rules: Sequence[ExitRule] = EXIT_RULES,
    count_attempts: bool = False,
    headline_fail_rate: float = 0.0,
) -> dict[str, Any]:
    by_rule: dict[str, Any] = {}
    for rule in rules:
        rows = [
            row
            for row in labels
            if row.get("exit_rule") == rule.rule_id
            and row.get("latency_s") == latency_s
            and (mints is None or row.get("mint") in mints)
        ]
        by_rule[rule.rule_id] = aggregate_rule(
            rows,
            size_lamports=size_lamports,
            fail_rates=fail_rates,
            count_attempts=count_attempts,
            headline_fail_rate=headline_fail_rate,
        )
    return {"book": book, "latency_s": latency_s, "by_exit": by_rule}


def random_mints(mints: Sequence[str], *, fraction: float, seed: int) -> list[str]:
    ordered = sorted(set(mints))
    if not ordered or fraction <= 0:
        return []
    k = int(round(len(ordered) * fraction))
    k = max(1, min(len(ordered), k)) if ordered else 0
    if fraction >= 1:
        return ordered
    return sorted(random.Random(seed).sample(ordered, k))


def build_scoreboard(
    labels: Sequence[dict[str, Any]],
    *,
    creates_n: int,
    tape_end_ms: int,
    tape_start_ms: int | None,
    size_lamports: int,
    slippage_cap: float,
    scan: dict[str, Any] | None = None,
    random_fraction: float = RANDOM_FRACTION,
    random_seed: int = RANDOM_SEED,
    latencies: Sequence[float] = DEFAULT_LATENCIES,
) -> dict[str, Any]:
    mints = sorted({row["mint"] for row in labels if row.get("latency_s") == HEADLINE_LATENCY and row.get("exit_rule") == EXIT_RULES[0].rule_id})
    # Fall back if headline latency was not simulated.
    if not mints:
        mints = sorted({row["mint"] for row in labels})
    sample = random_mints(mints, fraction=random_fraction, seed=random_seed)
    books = {
        "buy_every_create": score_labels(
            labels,
            book="buy_every_create",
            latency_s=HEADLINE_LATENCY,
            mints=None,
            size_lamports=size_lamports,
            count_attempts=True,
            headline_fail_rate=DEFAULT_FAIL_RATE,
        ),
        "random_subsample": score_labels(
            labels,
            book="random_subsample",
            latency_s=HEADLINE_LATENCY,
            mints=set(sample),
            size_lamports=size_lamports,
            count_attempts=True,
            headline_fail_rate=DEFAULT_FAIL_RATE,
        ),
    }
    latency_tables: dict[str, Any] = {}
    for latency in latencies:
        latency_tables[f"{latency:g}"] = score_labels(
            labels,
            book="buy_every_create",
            latency_s=latency,
            mints=None,
            size_lamports=size_lamports,
            count_attempts=True,
            headline_fail_rate=DEFAULT_FAIL_RATE,
        )["by_exit"]
    return {
        "schema": SCHEMA_BOARD,
        "assumptions": ASSUMPTIONS,
        "window": {
            "tape_start_ms": tape_start_ms,
            "tape_end_ms": tape_end_ms,
            "creates_in_window": creates_n,
            "headline_latency_s": HEADLINE_LATENCY,
            "headline_fail_rate": DEFAULT_FAIL_RATE,
            "priority_lamports": PRIORITY_FEE_LAMPORTS,
            "size_lamports": size_lamports,
            "slippage_cap": slippage_cap,
            "random_fraction": random_fraction,
            "random_seed": random_seed,
            "random_n": len(sample),
        },
        "scan": scan,
        "books": books,
        "latency_sensitivity": latency_tables,
    }


def format_markdown(board: dict[str, Any]) -> str:
    lines: list[str] = []
    window = board.get("window") or {}
    lines.append(
        f"Baseline buy-every-create at T+{window.get('headline_latency_s')}s, "
        f"size {window.get('size_lamports', 0) / LAMPORTS_PER_SOL:.2f} SOL, "
        f"fail rate {window.get('headline_fail_rate')}, "
        f"priority {window.get('priority_lamports')} lamports. "
        f"Misses are inside n. Sensitivity at 0/10/25% is below."
    )
    scan = board.get("scan") or {}
    if scan.get("non_wsol"):
        lines.append(
            f"Excluded from SOL PnL: {scan.get('non_wsol')} prints ({scan.get('non_wsol_reason')})."
        )
    lines.append("")
    lines.append("| book | exit | n | median | mean | p10 | p90 | win | total SOL | no-exit | censored | miss |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for book_name in ("buy_every_create", "random_subsample"):
        book = (board.get("books") or {}).get(book_name) or {}
        for rule_id, stats in (book.get("by_exit") or {}).items():
            lines.append(
                "| {book} | {rule} | {n} | {med} | {mean} | {p10} | {p90} | {win} | {total} | {rug} | {cens} | {miss} |".format(
                    book=book_name,
                    rule=rule_id,
                    n=stats.get("n"),
                    med=_fmt(stats.get("median_sol")),
                    mean=_fmt(stats.get("mean_sol")),
                    p10=_fmt(stats.get("p10_sol")),
                    p90=_fmt(stats.get("p90_sol")),
                    win=_fmt_rate(stats.get("win_rate")),
                    total=_fmt(stats.get("total_sol")),
                    rug=stats.get("no_exit_n"),
                    cens=stats.get("censored_n"),
                    miss=stats.get("miss_n"),
                )
            )
    lines.append("")
    lines.append(
        "Fail-rate sensitivity is on the buy-every-create book at T+1s. "
        f"Headline is {DEFAULT_FAIL_RATE:.0%}. The 0% row is the unflattered tape."
    )
    lines.append("")
    lines.append("| exit | rate | symmetric total | exit-fail-only total |")
    lines.append("| --- | ---: | ---: | ---: |")
    buy = ((board.get("books") or {}).get("buy_every_create") or {}).get("by_exit") or {}
    for rule_id, stats in buy.items():
        for rate, sens in (stats.get("fail_sensitivity") or {}).items():
            lines.append(
                f"| {rule_id} | {rate} | {_fmt(sens.get('symmetric_total_sol'))} | {_fmt(sens.get('exit_fail_only_total_sol'))} |"
            )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def _fmt_rate(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def headline(board: dict[str, Any], rule_id: str = "hold_30s") -> str:
    stats = (((board.get("books") or {}).get("buy_every_create") or {}).get("by_exit") or {}).get(rule_id) or {}
    return (
        f"buy every create T+1s {rule_id}: n={stats.get('n')} "
        f"median={_fmt(stats.get('median_sol'))} SOL mean={_fmt(stats.get('mean_sol'))} SOL "
        f"total={_fmt(stats.get('total_sol'))} SOL win={_fmt_rate(stats.get('win_rate'))} "
        f"no_exit={stats.get('no_exit_n')} censored={stats.get('censored_n')}"
    )


def filter_window(paths: dict[str, MintPath], t_min_ms: int, t_max_ms: int, pad_before_ms: int = 2_000) -> dict[str, MintPath]:
    lo = t_min_ms - pad_before_ms
    return {
        mint: path
        for mint, path in paths.items()
        if lo <= path.create.t_signal_ms <= t_max_ms
    }


def run_files(
    *,
    tape: Sequence[Path],
    creates: Sequence[Path],
    output_dir: Path,
    latencies: Sequence[float],
    size_lamports: int,
    slippage_cap: float,
    emit_paths: bool,
) -> dict[str, Any]:
    if not tape:
        raise SystemExit("no tape files")
    out_text = str(output_dir)
    if "/sealed/trades" in out_text or out_text.rstrip("/").endswith("/sealed/trades"):
        raise SystemExit("refusing to write into the trade tape directory")
    compressed = any(path.name.endswith(".zst") or path.suffix == ".gz" for path in tape)
    if compressed:
        # Bounds come from the scan. A peek cannot see inside zstd without a full read.
        t_min, t_max = None, None
    else:
        t_min, t_max = peek_trade_bounds(tape[0])
        for extra in tape[1:]:
            lo, hi = peek_trade_bounds(extra)
            if lo is not None and (t_min is None or lo < t_min):
                t_min = lo
            if hi is not None and (t_max is None or hi > t_max):
                t_max = hi
    # File may still be appending. Keep creates a few minutes past the peek.
    create_hi = None if t_max is None else t_max + 180_000
    create_map = load_creates(creates, t_min_ms=t_min, t_max_ms=create_hi)
    print(f"creates_loaded={len(create_map)} peek_t=({t_min},{t_max})", file=sys.stderr)
    paths, stats = stream_paths(create_map, tape)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("tape has no t_recv_ms")
    paths = filter_window(paths, stats.t_min_ms, stats.t_max_ms)
    print(
        f"creates_in_window={len(paths)} kept_prints={stats.kept} tape_lines={stats.lines}",
        file=sys.stderr,
    )
    labels = simulate_book(
        paths.values(),
        latencies=latencies,
        tape_end_ms=stats.t_max_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    board = build_scoreboard(
        labels,
        creates_n=len(paths),
        tape_end_ms=stats.t_max_ms,
        tape_start_ms=stats.t_min_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        scan=stats.as_dict(),
        latencies=latencies,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as fh:
        for row in labels:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    board_path = output_dir / "scoreboard.json"
    board_path.write_text(json.dumps(board, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    md_path = output_dir / "scoreboard.md"
    md_path.write_text(format_markdown(board), encoding="utf-8")
    if emit_paths:
        path_out = output_dir / "price_paths.jsonl"
        with path_out.open("w", encoding="utf-8") as fh:
            for line in iter_price_path_jsonl(paths.values()):
                fh.write(line + "\n")
    print(headline(board), file=sys.stderr)
    return board


def _parse_latencies(text: str) -> tuple[float, ...]:
    parts = tuple(float(p) for p in text.split(",") if p.strip())
    if HEADLINE_LATENCY not in parts:
        raise SystemExit("latencies must include 1 (the headline T+1s book)")
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper SOL scoreboard from the pump trade tape")
    parser.add_argument("--tape", nargs="+", required=True, type=Path, help="trades-*.jsonl files")
    parser.add_argument("--creates", nargs="+", required=True, type=Path, help="observe JSONL with subscribeNewToken")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--latencies", default="0.5,1,2,5")
    parser.add_argument("--size-sol", type=float, default=0.05)
    parser.add_argument("--slippage-cap", type=float, default=DEFAULT_SLIPPAGE_CAP)
    parser.add_argument("--emit-paths", action="store_true", help="also write per-print price path JSONL")
    args = parser.parse_args(argv)
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    size_lamports = int(round(args.size_sol * LAMPORTS_PER_SOL))
    board = run_files(
        tape=args.tape,
        creates=args.creates,
        output_dir=args.output_dir,
        latencies=_parse_latencies(args.latencies),
        size_lamports=size_lamports,
        slippage_cap=args.slippage_cap,
        emit_paths=args.emit_paths,
    )
    sys.stdout.write(headline(board) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
