#!/usr/bin/env python3
"""Paper PnL versus landing slot. Scoring only. No live fills.

The grid, fees, and fail models are pre-registered. This module does not
fit a threshold and does not import the forward runner or promotion gate.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import math
import struct
import subprocess
import sys
import time
from array import array
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools import tape_lines
from tools.paper_curve_math import (
    DEFAULT_SLIPPAGE_CAP,
    LAMPORTS_PER_SOL,
    TOKEN_ACCOUNT_RENT_LAMPORTS,
    market_cap_sol,
    quote_buy,
    quote_sell,
    reserves_with_our_buy,
    spot_sol_per_ui,
)
from tools.paper_price_path import TapePrint, TxOrder, collapse_fillable, print_from_trade_row

# Same declared curve as tools/paper_fail_pressure.py. Inlined so a host tree
# without that module still scores the pre-registered scale-1 gate.
B_SLOT = 0.8
B_SOL = 0.35
TARGET_FAIL_RATE = 0.289


class Pressure:
    __slots__ = ("same_slot_buys", "nearby_buy_lamports")

    def __init__(self, same_slot_buys: int, nearby_buy_lamports: int) -> None:
        self.same_slot_buys = int(same_slot_buys)
        self.nearby_buy_lamports = int(nearby_buy_lamports)


class FailCurve:
    __slots__ = ("intercept", "b_slot", "b_sol", "scale")

    def __init__(self, intercept: float, b_slot: float, b_sol: float, scale: float) -> None:
        self.intercept = float(intercept)
        self.b_slot = float(b_slot)
        self.b_sol = float(b_sol)
        self.scale = float(scale)

    def p(self, pressure: Pressure) -> float:
        z = (
            self.intercept
            + self.b_slot * math.log1p(pressure.same_slot_buys)
            + self.b_sol * math.log1p(pressure.nearby_buy_lamports / LAMPORTS_PER_SOL)
        )
        return _sigmoid(z)


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _fit_intercept(pressures: Sequence[Pressure], b_slot: float, b_sol: float) -> float:
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        curve = FailCurve(mid, b_slot, b_sol, 0.0)
        mean = sum(curve.p(pr) for pr in pressures) / len(pressures)
        if mean > TARGET_FAIL_RATE:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def fit_curve(pressures: Sequence[Pressure]) -> FailCurve:
    """Scale-1 slopes, intercept set so mean p on these sends is 0.289."""
    b_slot = B_SLOT * 1.0
    b_sol = B_SOL * 1.0
    return FailCurve(_fit_intercept(pressures, b_slot, b_sol), b_slot, b_sol, 1.0)

# Locked in internal/latency-curve-prereg.md before the holdout run.
MEASURED_MS = 1_387
SLOT_MS = 400
WINDOW_MS = 32 * 60 * 1000
HOLD_MS = 30_000
MAX_HOLD_MS = 30 * 60 * 1000
SLIPPAGE_CAP = DEFAULT_SLIPPAGE_CAP
FLAT_FAIL = 0.15
PRIORITIES = (1_000_000, 300_000, 100_000)
WSOL = "So11111111111111111111111111111111111111112"
HOLDOUT_END = "2026-09-25T06:58:00Z"
RUG_MIN_SCORED = 2
RUG_FRAC = 0.50
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1

# (index, name, bound, slot offset). bound "clock" is the measured receive.
LANDS: tuple[tuple[int, str, str, int], ...] = (
    (0, "slot+1", "start", 1),
    (1, "slot+1", "end", 1),
    (2, "slot+2", "start", 2),
    (3, "slot+2", "end", 2),
    (4, "slot+3", "start", 3),
    (5, "slot+3", "end", 3),
    (6, "slot+4", "start", 4),
    (7, "slot+4", "end", 4),
    (8, "measured", "clock", 0),
)
EXITS = ("hold_30s", "tp50_sl30", "ladder_2x_t30")
ROUTES = (("direct", 0), ("portal", 5_000))
SIZES = (50_000_000, 500_000_000)
STRATS = ("buy_all", "skip_fresh_rug", "migrate")

# strategy, land, exit, route, size, status, pri_sides, day, net0, gross, buys, nearby
REC = struct.Struct("<BBBBBBBB qq i q")
MISS = 0
SEND = 1


def skip_decision(fresh: float | None, scored: float | None, rug_frac: float | None) -> bool:
    """True only when a visible feature says skip. None is unknown and does not skip."""
    if fresh == 1:
        return True
    if (
        scored is not None
        and rug_frac is not None
        and scored >= RUG_MIN_SCORED
        and rug_frac >= RUG_FRAC
    ):
        return True
    return False


def _state_index(fills: Sequence[TapePrint], slot: int, bound: str) -> int:
    idx = -1
    if bound == "start":
        for i, pr in enumerate(fills):
            if pr.slot < slot:
                idx = i
            else:
                break
    else:
        for i, pr in enumerate(fills):
            if pr.slot <= slot:
                idx = i
            else:
                break
    return idx


def _state_at(fills: Sequence[TapePrint], t_ms: int) -> int:
    idx = -1
    for i, pr in enumerate(fills):
        if pr.t_recv_ms <= t_ms:
            idx = i
        else:
            break
    return idx


def _slot_time(fills: Sequence[TapePrint], slot: int, fallback: int) -> int:
    for pr in fills:
        if pr.slot == slot:
            return pr.t_recv_ms
        if pr.slot > slot:
            break
    return fallback


def _pressure(fills: Sequence[TapePrint], end_idx: int, entry_slot: int, landing_ms: int) -> tuple[int, int]:
    buys = 0
    nearby = 0
    start = landing_ms - 2_000
    stop = end_idx + 1
    for i in range(stop):
        pr = fills[i]
        if pr.side != "buy":
            continue
        if pr.slot == entry_slot:
            buys += 1
        if start <= pr.t_recv_ms <= landing_ms:
            nearby += pr.sol_lamports
    return buys, nearby


def _sell(
    state: TapePrint,
    *,
    venue: str,
    tokens: int,
    held: int,
    extra_quote: int,
    portal_ppm: int,
) -> tuple[int, int] | None:
    book = reserves_with_our_buy(
        quote_lamports=state.quote_reserve,
        base_raw=state.base_reserve,
        net_in_lamports=extra_quote,
        tokens_raw=held,
        same_venue=state.venue == venue,
    )
    if book is None:
        return None
    quote, base = book
    if base + tokens <= 0:
        return None
    sell_gross = tokens * quote // (base + tokens)
    sol_out = quote_sell(
        venue=state.venue,
        tokens_raw=tokens,
        quote_lamports=quote,
        base_raw=base,
        market_cap=market_cap_sol(quote, base),
        payable_quote_lamports=state.quote_reserve if state.venue == "pump_bonding" else None,
        portal_fee_ppm=portal_ppm,
    )
    if sol_out is None:
        return None
    return sol_out, sell_gross


def _gross(net0: int, size: int, net_in: int, sol_out: int, sell_gross: int, failed: bool) -> int:
    rent = TOKEN_ACCOUNT_RENT_LAMPORTS if failed else 0
    fee_wo = (size - net_in) + (sell_gross - sol_out) + rent
    return net0 + fee_wo


def _try_buy(
    state: TapePrint,
    size: int,
    portal_ppm: int,
    ref: float | None,
) -> Any:
    spot = state.price_sol
    if ref and ref > 0 and spot > ref * (1.0 + SLIPPAGE_CAP):
        return None
    mcap = state.market_cap_sol if state.market_cap_sol > 0 else market_cap_sol(state.quote_reserve, state.base_reserve)
    buy = quote_buy(
        venue=state.venue,
        size_lamports=size,
        quote_lamports=state.quote_reserve,
        base_raw=state.base_reserve,
        market_cap=mcap,
        portal_fee_ppm=portal_ppm,
    )
    if buy is None:
        return None
    if ref and ref > 0 and buy.tokens_raw > 0:
        executable = (buy.net_in_lamports / LAMPORTS_PER_SOL) / (buy.tokens_raw / 1_000_000)
        if executable > ref * (1.0 + SLIPPAGE_CAP):
            return None
    return buy


def _delayed(fills: Sequence[TapePrint], pr: TapePrint, k: int, bound: str, mode: str) -> tuple[int, int]:
    """Return (state index, exit chain ms). Index -1 if there is no state."""
    if mode == "clock":
        t_exit = pr.t_recv_ms + MEASURED_MS
        return _state_at(fills, t_exit), t_exit
    target = pr.slot + k
    return _state_index(fills, target, bound), pr.t_recv_ms + k * SLOT_MS


def _one_sell_close(
    fills: Sequence[TapePrint],
    state_idx: int,
    buy: Any,
    venue: str,
    size: int,
    portal_ppm: int,
) -> tuple[int, int, int, int] | None:
    """(net0, gross, pri_sides, status) or None if the state is missing."""
    if state_idx < 0:
        net0 = -(size + TOKEN_ACCOUNT_RENT_LAMPORTS)
        gross = _gross(net0, size, buy.net_in_lamports, 0, 0, True)
        return net0, gross, 2, SEND
    sold = _sell(
        fills[state_idx],
        venue=venue,
        tokens=buy.tokens_raw,
        held=buy.tokens_raw,
        extra_quote=buy.net_in_lamports,
        portal_ppm=portal_ppm,
    )
    if sold is None:
        net0 = -(size + TOKEN_ACCOUNT_RENT_LAMPORTS)
        gross = _gross(net0, size, buy.net_in_lamports, 0, 0, True)
        return net0, gross, 2, SEND
    sol_out, sell_gross = sold
    net0 = sol_out - size
    gross = _gross(net0, size, buy.net_in_lamports, sol_out, sell_gross, False)
    return net0, gross, 2, SEND


def _hold(
    fills: Sequence[TapePrint],
    buy: Any,
    venue: str,
    size: int,
    portal_ppm: int,
    landing_ms: int,
    tape_through_ms: int,
) -> tuple[int, int, int, int] | None:
    deadline = landing_ms + HOLD_MS
    if deadline > tape_through_ms:
        return None
    return _one_sell_close(fills, _state_at(fills, deadline), buy, venue, size, portal_ppm)


def _tpsl(
    fills: Sequence[TapePrint],
    entry_idx: int,
    buy: Any,
    venue: str,
    size: int,
    portal_ppm: int,
    landing_ms: int,
    tape_through_ms: int,
    k: int,
    bound: str,
    mode: str,
) -> tuple[int, int, int, int] | None:
    mark = spot_sol_per_ui(buy.quote_after, buy.base_after)
    if mark <= 0:
        return None
    deadline = landing_ms + MAX_HOLD_MS
    hit: TapePrint | None = None
    for pr in fills[entry_idx + 1 :]:
        if pr.t_recv_ms > deadline:
            break
        book = reserves_with_our_buy(
            quote_lamports=pr.quote_reserve,
            base_raw=pr.base_reserve,
            net_in_lamports=buy.net_in_lamports,
            tokens_raw=buy.tokens_raw,
            same_venue=pr.venue == venue,
        )
        if book is None:
            continue
        spot = spot_sol_per_ui(book[0], book[1])
        if spot <= 0:
            continue
        ret = spot / mark - 1.0
        if ret >= 0.50 or ret <= -0.30:
            hit = pr
            break
    if hit is None:
        if deadline > tape_through_ms:
            return None
        return _one_sell_close(fills, _state_at(fills, deadline), buy, venue, size, portal_ppm)
    state_idx, t_exit = _delayed(fills, hit, k, bound, mode)
    if t_exit > tape_through_ms:
        return None
    return _one_sell_close(fills, state_idx, buy, venue, size, portal_ppm)


def _ladder(
    fills: Sequence[TapePrint],
    entry_idx: int,
    entry_spot: float,
    buy: Any,
    venue: str,
    size: int,
    portal_ppm: int,
    landing_ms: int,
    tape_through_ms: int,
    k: int,
    bound: str,
    mode: str,
) -> tuple[int, int, int, int] | None:
    if entry_spot <= 0 or buy.tokens_raw <= 0:
        return None
    tokens = buy.tokens_raw
    scale_tokens = int(tokens * 0.5)
    if not (0 < scale_tokens < tokens):
        scale_tokens = tokens // 2 if tokens >= 2 else tokens
    remaining = tokens
    held = tokens
    extra = buy.net_in_lamports
    peak = entry_spot
    scaled = False
    sol_out = 0
    sell_gross_sum = 0
    attempts = 0
    failed = False
    deadline = landing_ms + MAX_HOLD_MS

    def attempt(state_idx: int, n_tokens: int, t_exit: int) -> bool:
        nonlocal remaining, held, extra, sol_out, sell_gross_sum, attempts, failed
        if t_exit > tape_through_ms:
            return False
        attempts += 1
        if state_idx < 0:
            failed = True
            return True
        sold = _sell(
            fills[state_idx],
            venue=venue,
            tokens=n_tokens,
            held=held,
            extra_quote=extra,
            portal_ppm=portal_ppm,
        )
        if sold is None:
            failed = True
            return True
        got, gross_leg = sold
        sol_out += got
        sell_gross_sum += gross_leg
        remaining -= n_tokens
        held -= n_tokens
        extra = max(0, extra - got)
        return True

    for pr in fills[entry_idx + 1 :]:
        if pr.t_recv_ms > deadline:
            break
        spot = pr.price_sol
        if spot <= 0:
            continue
        if spot > peak:
            peak = spot
        ret = spot / entry_spot - 1.0
        if not scaled and ret >= 1.0 and remaining > scale_tokens:
            state_idx, t_exit = _delayed(fills, pr, k, bound, mode)
            if not attempt(state_idx, scale_tokens, t_exit):
                return None
            scaled = True
        if remaining <= 0:
            break
        if ret <= -0.30 or (scaled and spot <= peak * (1.0 - 0.30)):
            state_idx, t_exit = _delayed(fills, pr, k, bound, mode)
            if not attempt(state_idx, remaining, t_exit):
                return None
            remaining = 0
            break
    if remaining > 0:
        if deadline > tape_through_ms:
            return None
        if not attempt(_state_at(fills, deadline), remaining, deadline):
            return None
    if attempts == 0:
        return None
    rent = TOKEN_ACCOUNT_RENT_LAMPORTS if failed else 0
    net0 = sol_out - size - rent
    gross = _gross(net0, size, buy.net_in_lamports, sol_out, sell_gross_sum, failed)
    return net0, gross, 1 + attempts, SEND


def evaluate_path(
    fills: Sequence[TapePrint],
    *,
    trigger_slot: int,
    trigger_block_ms: int,
    ref_price: float | None,
    tape_through_ms: int,
) -> list[tuple[int, int, int, int, int, int, int, int, int, int]]:
    """One trigger. Rows are (land, exit, route, size, status, pri_sides, net0, gross, buys, nearby).

    Censored exits are omitted. Misses stay, one per exit.
    """
    rows: list[tuple[int, int, int, int, int, int, int, int, int, int]] = []
    ordered = fills
    for land_i, _name, bound, k in LANDS:
        if bound == "clock":
            t_entry = trigger_block_ms + MEASURED_MS
            idx = _state_at(ordered, t_entry)
            landing_ms = t_entry
        else:
            target = trigger_slot + k
            idx = _state_index(ordered, target, bound)
            fallback = ordered[idx].t_recv_ms if idx >= 0 else trigger_block_ms
            landing_ms = _slot_time(ordered, target, fallback)
        state = ordered[idx] if idx >= 0 else None
        buys, nearby = (0, 0)
        if state is not None:
            buys, nearby = _pressure(ordered, idx, state.slot, landing_ms)
        for route_i, (_route, portal) in enumerate(ROUTES):
            for size_i, size in enumerate(SIZES):
                buy = None if state is None else _try_buy(state, size, portal, ref_price)
                if buy is None:
                    for exit_i in range(len(EXITS)):
                        rows.append((land_i, exit_i, route_i, size_i, MISS, 1, 0, 0, buys, nearby))
                    continue
                venue = state.venue
                assert state is not None
                closed = [
                    _hold(ordered, buy, venue, size, portal, landing_ms, tape_through_ms),
                    _tpsl(
                        ordered, idx, buy, venue, size, portal, landing_ms, tape_through_ms, k, bound, bound
                    ),
                    _ladder(
                        ordered,
                        idx,
                        state.price_sol,
                        buy,
                        venue,
                        size,
                        portal,
                        landing_ms,
                        tape_through_ms,
                        k,
                        bound,
                        bound,
                    ),
                ]
                for exit_i, result in enumerate(closed):
                    if result is None:
                        continue
                    net0, gross, sides, status = result
                    rows.append((land_i, exit_i, route_i, size_i, status, sides, net0, gross, buys, nearby))
    return rows


def prepare_fills(prints: Sequence[TapePrint]) -> list[TapePrint]:
    """#97 order, then one state per signature after its last inner event."""
    order = TxOrder()
    stamped = [order.stamp(pr) for pr in prints]
    return collapse_fillable(stamped)


def mixed_net(net0: int, pri_sides: int, status: int, priority: int, p_fail: float) -> float:
    net = net0 - priority * pri_sides
    if status == MISS:
        return float(net)
    return (1.0 - p_fail) * net + p_fail * float(-priority)


def _parse_time(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def sealed_hours(backfill: Path, end_s: int) -> list[dict[str, Any]]:
    found = []
    for path in sorted(backfill.glob("stats-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        start = data.get("block_time_start")
        end = data.get("block_time_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start >= end_s or end > end_s:
            continue
        key = path.name[len("stats-") : -len(".json")]
        trade = _hour_file(backfill / "trades", "trades", key)
        if trade is None:
            continue
        found.append(
            {
                "hour": key,
                "day": key[:10],
                "start": start,
                "end": end,
                "trade": trade,
                "create": _hour_file(backfill / "creates", "creates", key),
            }
        )
    return found


def _hour_file(directory: Path, prefix: str, key: str) -> Path | None:
    if not directory.is_dir():
        return None
    for name in (f"{prefix}-{key}.jsonl.zst", f"{prefix}-{key}.jsonl", f"{prefix}-{key}.jsonl.gz"):
        path = directory / name
        if path.is_file():
            return path
    return None


def _open_text(path: Path) -> Iterable[str]:
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")
        finally:
            proc.stdout.close()
            proc.wait()
        return
    if path.name.endswith(".gz"):
        import gzip

        with gzip.open(path, "rt", encoding="utf-8") as fh:
            yield from fh
        return
    with path.open("r", encoding="utf-8") as fh:
        yield from fh


def _anchor(row: dict[str, Any], slot: int, block_ms: int, signature: str | None) -> TapePrint | None:
    try:
        quote = int(row["quote_reserve"])
        base = int(row["base_reserve"])
    except (KeyError, TypeError, ValueError):
        return None
    if quote <= 0 or base <= 0:
        return None
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=block_ms,
        slot=slot,
        event_index=-1,
        venue="pump_bonding",
        side="create",
        sol_lamports=0,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=signature,
        tx_index=-1,
    )


# One fillable print, signature dropped. 58 bytes. market cap is recomputed.
_PACK = struct.Struct("<qqiiBBqqqd")
_VENUE_CODE = {"pump_bonding": 0, "pumpswap": 1}
_CODE_VENUE = ("pump_bonding", "pumpswap")
_SIDE_CODE = {"buy": 0, "sell": 1, "create": 2}
_CODE_SIDE = ("buy", "sell", "create")


def _pack_print(pr: TapePrint) -> bytes:
    return _PACK.pack(
        int(pr.t_recv_ms),
        int(pr.slot),
        int(pr.tx_index),
        int(pr.event_index),
        _VENUE_CODE.get(pr.venue, 0),
        _SIDE_CODE.get(pr.side, 0),
        int(pr.sol_lamports),
        int(pr.quote_reserve),
        int(pr.base_reserve),
        float(pr.price_sol),
    )


def _unpack_print(raw: bytes) -> TapePrint:
    t_ms, slot, tx_index, event_index, venue, side, sol, quote, base, price = _PACK.unpack(raw)
    return TapePrint(
        t_recv_ms=int(t_ms),
        slot=int(slot),
        event_index=int(event_index),
        venue=_CODE_VENUE[venue] if venue < len(_CODE_VENUE) else "pump_bonding",
        side=_CODE_SIDE[side] if side < len(_CODE_SIDE) else "buy",
        sol_lamports=int(sol),
        quote_reserve=int(quote),
        base_reserve=int(base),
        price_sol=float(price),
        market_cap_sol=float(price) * 1_000_000_000,
        signature=None,
        tx_index=int(tx_index),
    )


def _later_event(a: TapePrint, b: TapePrint) -> TapePrint:
    """Last inner event, visible at the later receive time. Same rule as collapse_fillable."""
    winner = a if (a.tx_index, a.event_index) >= (b.tx_index, b.event_index) else b
    visible = a.t_recv_ms if a.t_recv_ms >= b.t_recv_ms else b.t_recv_ms
    if winner.t_recv_ms == visible:
        return winner
    return replace(winner, t_recv_ms=visible)


class _Mint:
    """Hot path keeps one slot of signatures, then a packed fillable tape.

    Collapse matches tools.paper_price_path.collapse_fillable. The create
    anchor joins its signature's group when that slot closes, then the
    signature string is dropped.
    """

    __slots__ = (
        "slot",
        "block_ms",
        "day",
        "anchor",
        "packed",
        "order",
        "had_bond",
        "mig_slot",
        "mig_ms",
        "create_done",
        "mig_done",
        "_open_slot",
        "_groups",
        "_solos",
        "_anchor_merged",
    )

    def __init__(self, slot: int, block_ms: int, day: int, anchor: TapePrint | None) -> None:
        self.slot = slot
        self.block_ms = block_ms
        self.day = day
        self.anchor = anchor
        self.packed = bytearray()
        self.order = TxOrder()
        self.had_bond = False
        self.mig_slot: int | None = None
        self.mig_ms: int | None = None
        self.create_done = False
        self.mig_done = False
        self._open_slot: int | None = None
        self._groups: dict[str, TapePrint] = {}
        self._solos: list[TapePrint] = []
        self._anchor_merged = False

    def add(self, pr: TapePrint) -> None:
        self._open(pr.slot)
        stamped = self.order.stamp(pr)
        if stamped.venue == "pump_bonding":
            self.had_bond = True
        elif stamped.venue == "pumpswap" and self.had_bond and self.mig_slot is None:
            self.mig_slot = stamped.slot
            self.mig_ms = stamped.t_recv_ms
        sig = stamped.signature
        if not sig:
            self._solos.append(stamped)
            return
        prev = self._groups.get(sig)
        self._groups[sig] = stamped if prev is None else _later_event(prev, stamped)

    def _open(self, slot: int) -> None:
        if self._open_slot is None:
            self._open_slot = slot
            return
        if slot == self._open_slot:
            return
        self._close_slot()
        self._open_slot = slot

    def _close_slot(self) -> None:
        slot = self._open_slot
        groups = self._groups
        if (
            not self._anchor_merged
            and self.anchor is not None
            and self.anchor.signature
            and slot is not None
            and self.anchor.slot == slot
            and self.anchor.signature in groups
        ):
            groups[self.anchor.signature] = _later_event(groups[self.anchor.signature], self.anchor)
            self._anchor_merged = True
        events = list(groups.values())
        events.extend(self._solos)
        events.sort(key=lambda pr: (pr.t_recv_ms, pr.slot, pr.tx_index, pr.event_index))
        for pr in events:
            self.packed += _pack_print(pr)
        if slot is not None:
            seen = self.order._seen
            for sig in groups:
                seen.pop((slot, sig), None)
        groups.clear()
        self._solos.clear()

    def finish(self) -> None:
        if self._open_slot is not None or self._groups or self._solos:
            self._close_slot()
            self._open_slot = None

    def release(self) -> None:
        """Drop the tape after the create is scored and no migration is open."""
        self.finish()
        self.packed = bytearray()
        self._groups.clear()
        self._solos.clear()
        self.order = TxOrder()

    def drop_before(self, slot: int) -> None:
        """Keep fillable prints at or after `slot` (migration trigger onward)."""
        self.finish()
        kept = bytearray()
        step = _PACK.size
        raw = self.packed
        for i in range(0, len(raw), step):
            if _PACK.unpack_from(raw, i)[1] >= slot:
                kept += raw[i : i + step]
        self.packed = kept

    def fillable(self, *, migrate: bool) -> list[TapePrint]:
        self.finish()
        step = _PACK.size
        raw = self.packed
        fills = [_unpack_print(raw[i : i + step]) for i in range(0, len(raw), step)]
        if not migrate and self.anchor is not None and not self._anchor_merged:
            fills.append(self.anchor)
        fills.sort(key=lambda pr: (pr.t_recv_ms, pr.slot, pr.tx_index, pr.event_index))
        return fills


def _ref_from_anchor(anchor: TapePrint | None) -> float | None:
    if anchor is None or anchor.price_sol <= 0:
        return None
    return anchor.price_sol


def _fills_for(mint: _Mint, *, migrate: bool) -> tuple[list[TapePrint], int, int, float | None]:
    fills = mint.fillable(migrate=migrate)
    if migrate:
        assert mint.mig_slot is not None and mint.mig_ms is not None
        ref = None
        for pr in fills:
            if pr.slot == mint.mig_slot and pr.price_sol > 0:
                ref = pr.price_sol
                break
        return fills, mint.mig_slot, mint.mig_ms, ref
    ref = _ref_from_anchor(mint.anchor)
    if ref is None:
        for pr in fills:
            if pr.slot == mint.slot and pr.price_sol > 0 and pr.event_index >= 0:
                ref = pr.price_sol
                break
    return fills, mint.slot, mint.block_ms, ref


def _write_rows(fh: Any, strategy: int, day: int, rows: Sequence[tuple[int, ...]]) -> None:
    for land, exit_i, route, size_i, status, sides, net0, gross, buys, nearby in rows:
        fh.write(REC.pack(strategy, land, exit_i, route, size_i, status, sides, day, net0, gross, buys, nearby))


def _rss_mb() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _trim_heap() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        return


def _lag(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    lag = data.get("lag_ms")
    if isinstance(lag, bool) or not isinstance(lag, int):
        return None
    return lag


def wait_for_lag(path: Path) -> int:
    while True:
        lag = _lag(path)
        if lag is None:
            print("lag_unknown pause", file=sys.stderr, flush=True)
            time.sleep(30)
            continue
        if lag <= 5000:
            return lag
        print(f"pause lag_ms={lag}", file=sys.stderr, flush=True)
        time.sleep(30)


def _min_first_seen(graph_dir: Path) -> int | None:
    earliest: int | None = None
    if not graph_dir.is_dir():
        return None
    for path in sorted(graph_dir.glob("funding-*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen = row.get("first_seen_ms")
                if isinstance(seen, int):
                    if earliest is None or seen < earliest:
                        earliest = seen
                    break
    return earliest


def _load_creates(hours: Sequence[dict[str, Any]]) -> dict[str, _Mint]:
    found: dict[str, _Mint] = {}
    day_codes: dict[str, int] = {}
    for hour in hours:
        path = hour.get("create")
        if path is None:
            continue
        day_s = str(hour["day"])
        if day_s not in day_codes:
            day_codes[day_s] = len(day_codes)
        day = day_codes[day_s]
        for line in _open_text(path):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("type") != "create":
                continue
            mint = row.get("mint")
            slot = row.get("slot")
            block = row.get("block_time")
            if not isinstance(mint, str) or not isinstance(slot, int) or not isinstance(block, int):
                continue
            quote_mint = row.get("quote_mint")
            if isinstance(quote_mint, str) and quote_mint and quote_mint != WSOL:
                continue
            block_ms = block * 1000
            prev = found.get(mint)
            if prev is not None and prev.block_ms <= block_ms:
                continue
            sig = row.get("signature") if isinstance(row.get("signature"), str) else None
            found[mint] = _Mint(slot, block_ms, day, _anchor(row, slot, block_ms, sig))
    return found


def _iter_trades(path: Path, strict: bool = False) -> Iterable[dict[str, Any]]:
    """Rows of one tape file. A line that is not JSON is skipped, as always.

    `strict=True` (an explicit parameter; nothing in the environment switches it) makes the reader count lines
    that contain NUL, are not JSON even with strict=False, or are not objects, and raise
    tools.tape_lines.BadLinesError (file and count) once the file is exhausted. Rows yielded are unchanged."""
    lines: Iterable[str] = _open_text(path)
    if strict:
        lines = tape_lines.strict_lines(lines, path)  # type: ignore[assignment]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


def run_holdout(
    *,
    backfill: Path,
    out_json: Path,
    attempts_path: Path,
    lag_path: Path,
    graph_dir: Path | None,
    end_iso: str,
) -> dict[str, Any]:
    end_s = _parse_time(end_iso)
    end_ms = end_s * 1000
    lag = wait_for_lag(lag_path)
    print(f"lag_ms={lag} before", file=sys.stderr, flush=True)
    hours = sealed_hours(backfill, end_s)
    if not hours:
        raise SystemExit("no sealed holdout hour")
    print(f"sealed_hours={len(hours)}", file=sys.stderr, flush=True)
    creates = _load_creates(hours)
    print(f"creates={len(creates)}", file=sys.stderr, flush=True)
    first_seen = _min_first_seen(graph_dir) if graph_dir else None
    graph_visible = first_seen is not None and first_seen <= end_ms
    # A row written after the holdout cut cannot be visible at a holdout landing.
    print(f"graph_min_first_seen={first_seen} visible_in_holdout={graph_visible}", file=sys.stderr, flush=True)
    if graph_visible:
        raise SystemExit("funding graph overlaps the holdout; this pass does not join features")

    hot: dict[str, _Mint] = dict(creates)
    watch: dict[str, _Mint] = {}
    attempts_path.parent.mkdir(parents=True, exist_ok=True)
    last_lag = time.time()
    lines = 0
    create_rows = 0
    mig_rows = 0
    with attempts_path.open("wb") as fh:
        def score_create(mint: _Mint, through_ms: int) -> None:
            nonlocal create_rows
            fills, slot, block_ms, ref = _fills_for(mint, migrate=False)
            rows = evaluate_path(
                fills,
                trigger_slot=slot,
                trigger_block_ms=block_ms,
                ref_price=ref,
                tape_through_ms=through_ms,
            )
            _write_rows(fh, 0, mint.day, rows)
            create_rows += 1
            mint.create_done = True

        def score_mig(mint: _Mint, through_ms: int) -> None:
            nonlocal mig_rows
            if mint.mig_slot is None or mint.mig_done:
                return
            fills, slot, block_ms, ref = _fills_for(mint, migrate=True)
            rows = evaluate_path(
                fills,
                trigger_slot=slot,
                trigger_block_ms=block_ms,
                ref_price=ref,
                tape_through_ms=through_ms,
            )
            _write_rows(fh, 2, mint.day, rows)
            mig_rows += 1
            mint.mig_done = True

        def flush(now_ms: int, final: bool) -> None:
            for mint_id, mint in list(hot.items()):
                if not mint.create_done and (final or now_ms >= mint.block_ms + WINDOW_MS):
                    through = now_ms
                    if final and now_ms < mint.block_ms + WINDOW_MS:
                        score_create(mint, mint.block_ms)  # deadline in the future -> censored holds
                    else:
                        score_create(mint, through)
                    if mint.mig_slot is None:
                        mint.release()
                        watch[mint_id] = mint
                        hot.pop(mint_id, None)
                    elif final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS:
                        score_mig(mint, through if not final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS else mint.mig_ms or 0)
                        hot.pop(mint_id, None)
                    else:
                        mint.drop_before(mint.mig_slot)
                elif mint.create_done and mint.mig_slot is not None and not mint.mig_done:
                    if final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS:
                        through = now_ms if now_ms >= (mint.mig_ms or 0) + WINDOW_MS else (mint.mig_ms or 0)
                        score_mig(mint, through)
                        hot.pop(mint_id, None)
            if final:
                for mint in list(watch.values()):
                    if mint.mig_slot is not None and not mint.mig_done:
                        score_mig(mint, mint.mig_ms or 0)

        now_ms = 0
        for hour in hours:
            wait_ok = time.time() - last_lag
            if wait_ok >= 600:
                lag = wait_for_lag(lag_path)
                last_lag = time.time()
                print(f"lag_ms={lag} hour={hour['hour']}", file=sys.stderr, flush=True)
            rss_mb = _rss_mb()
            print(
                f"hour={hour['hour']} hot={len(hot)} watch={len(watch)} creates_scored={create_rows} rss_mb={rss_mb}",
                file=sys.stderr,
                flush=True,
            )
            for row in _iter_trades(hour["trade"]):
                lines += 1
                if lines % 2_000_000 == 0:
                    if time.time() - last_lag >= 600:
                        lag = wait_for_lag(lag_path)
                        last_lag = time.time()
                        print(f"lag_ms={lag} lines={lines}", file=sys.stderr, flush=True)
                    print(f"lines={lines} scored={create_rows} mig={mig_rows}", file=sys.stderr, flush=True)
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
                parsed = print_from_trade_row(row)
                if parsed is None:
                    continue
                _mint_name, pr = parsed
                now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
                if watching:
                    if pr.venue != "pumpswap" or not mint.had_bond or mint.mig_done:
                        continue
                    mint.add(pr)
                    watch.pop(mint_id, None)
                    hot[mint_id] = mint
                else:
                    mint.add(pr)
                if lines % 200_000 == 0:
                    flush(now_ms, False)
            flush(max(now_ms, int(hour["end"]) * 1000), False)
            _trim_heap()
        flush(now_ms, True)
    print(f"done lines={lines} create_rows={create_rows} mig_rows={mig_rows}", file=sys.stderr, flush=True)
    summary = aggregate(attempts_path, graph_visible=graph_visible)
    summary["measured_ms"] = MEASURED_MS
    summary["chain_to_recv_p50_ms"] = 1349
    summary["hop_p50_ms"] = 38
    summary["holdout_end"] = end_iso
    summary["hours"] = [hour["hour"] for hour in hours]
    summary["creates"] = len(creates)
    summary["create_rows"] = create_rows
    summary["migrate_rows"] = mig_rows
    summary["trade_lines"] = lines
    summary["graph_min_first_seen_ms"] = first_seen
    summary["graph_visible_in_holdout"] = graph_visible
    summary["slice"] = "holdout"
    days = sorted({hour["day"] for hour in hours})
    summary["days_present"] = days
    summary["complete_days"] = _complete_days(hours, end_s)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out_json}", file=sys.stderr, flush=True)
    return summary


def _complete_days(hours: Sequence[dict[str, Any]], end_s: int) -> list[str]:
    by_day: dict[str, set[str]] = {}
    for hour in hours:
        by_day.setdefault(str(hour["day"]), set()).add(str(hour["hour"]))
    done = []
    for day in sorted(by_day):
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_s = int(start.timestamp())
        expected = []
        for hour in range(24):
            ts = start_s + hour * 3600
            if ts >= end_s:
                break
            expected.append(datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H"))
        if expected and all(key in by_day[day] for key in expected):
            done.append(day)
    return done


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


class _Head:
    __slots__ = ("day", "flat", "press", "gross")

    def __init__(self) -> None:
        self.day = array("B")
        self.flat = array("d")
        self.press = array("d")
        self.gross = array("d")


def aggregate(path: Path, *, graph_visible: bool) -> dict[str, Any]:
    """Means for every fee cell. Headline rows kept for the CI and the ex-top-3."""
    curve = _fit_pressure(path)
    headline: dict[tuple[int, int, int], _Head] = {}
    sums: dict[tuple[int, int, int, int, int], list[float]] = {}

    def slot_for(key: tuple[int, int, int, int, int]) -> list[float]:
        bucket = sums.get(key)
        if bucket is None:
            # n, gross, then 3 flat nets, 3 pressure nets
            bucket = [0.0] * 8
            sums[key] = bucket
        return bucket

    with path.open("rb") as fh:
        while True:
            raw = fh.read(REC.size)
            if len(raw) < REC.size:
                break
            strategy, land, exit_i, route, size_i, status, sides, day, net0, gross, buys, nearby = REC.unpack(raw)
            if strategy == 1:
                continue
            pressure = curve.p(Pressure(int(buys), int(nearby))) if status == SEND else 0.0
            for pri_i, priority in enumerate(PRIORITIES):
                flat = mixed_net(net0, sides, status, priority, FLAT_FAIL if status == SEND else 0.0)
                # mixed_net already applies p only for sends; pass FLAT only for sends
                if status == MISS:
                    flat = mixed_net(net0, sides, status, priority, 0.0)
                else:
                    flat = mixed_net(net0, sides, status, priority, FLAT_FAIL)
                pressed = mixed_net(net0, sides, status, priority, pressure if status == SEND else 0.0)
                bucket = slot_for((strategy, land, exit_i, route, size_i))
                if pri_i == 0:
                    bucket[0] += 1
                    bucket[1] += gross
                bucket[2 + pri_i] += flat
                bucket[5 + pri_i] += pressed
            if route == 0 and size_i == 0 and strategy in (0, 2):
                flat0 = mixed_net(net0, sides, status, PRIORITIES[0], FLAT_FAIL if status == SEND else 0.0)
                press0 = mixed_net(net0, sides, status, PRIORITIES[0], pressure if status == SEND else 0.0)
                head = headline.get((strategy, land, exit_i))
                if head is None:
                    head = _Head()
                    headline[(strategy, land, exit_i)] = head
                head.day.append(day)
                head.flat.append(flat0)
                head.press.append(press0)
                head.gross.append(gross)
    if not graph_visible:
        # skip_fresh_rug is buy_all when no feature is visible. Share the rows.
        for key, bucket in list(sums.items()):
            if key[0] != 0:
                continue
            copied = list(bucket)
            sums[(1, key[1], key[2], key[3], key[4])] = copied
        for key, rows in list(headline.items()):
            if key[0] == 0:
                headline[(1, key[1], key[2])] = rows

    cells: dict[str, Any] = {}
    best: dict[str, Any] | None = None
    positive: list[str] = []
    for key, bucket in sums.items():
        strategy, land, exit_i, route, size_i = key
        n = int(bucket[0])
        if n <= 0:
            continue
        size = SIZES[size_i]
        gross_pct = (bucket[1] / n) / size * 100.0
        for pri_i, priority in enumerate(PRIORITIES):
            flat_pct = (bucket[2 + pri_i] / n) / size * 100.0
            press_pct = (bucket[5 + pri_i] / n) / size * 100.0
            both = flat_pct > 0 and press_pct > 0
            cell_id = _cell_id(strategy, land, exit_i, route, size_i, priority)
            row = {
                "n": n,
                "mean_net_flat_pct": flat_pct,
                "mean_net_pressure_pct": press_pct,
                "mean_gross_pct": gross_pct,
                "positive_both": both,
            }
            cells[cell_id] = row
            if both:
                positive.append(cell_id)
            headline_cell = route == 0 and size_i == 0 and priority == PRIORITIES[0]
            score = flat_pct
            if best is None or score > best["mean_net_flat_pct"]:
                if headline_cell:
                    best = {"id": cell_id, **row, "strategy": strategy, "land": land, "exit": exit_i}

    # CI for headline cells and any positive headline-sized cell. Reuse stored lists.
    for (strategy, land, exit_i), rows in headline.items():
        cell_id = _cell_id(strategy, land, exit_i, 0, 0, PRIORITIES[0])
        cell = cells.get(cell_id)
        if cell is None or not rows:
            continue
        _attach_ci(cell, rows, SIZES[0])
    # Positive cells outside the stored lists need another pass.
    extra = [cell_id for cell_id in positive if "ci_lo_flat_pct" not in cells[cell_id]]
    if extra:
        _attach_extra_ci(path, cells, extra, curve)
    return {
        "curve_intercept": curve.intercept,
        "curve_scale": curve.scale,
        "cells": cells,
        "positive_both": positive,
        "best_headline": best,
        "skip_equals_buy_all": not graph_visible,
    }


def _fit_pressure(path: Path) -> FailCurve:
    pressures: list[Pressure] = []
    with path.open("rb") as fh:
        while True:
            raw = fh.read(REC.size)
            if len(raw) < REC.size:
                break
            strategy, land, exit_i, route, size_i, status, _sides, _day, _net0, _gross, buys, nearby = REC.unpack(raw)
            if strategy == 0 and land == 8 and exit_i == 0 and route == 0 and size_i == 0 and status == SEND:
                pressures.append(Pressure(int(buys), int(nearby)))
    if not pressures:
        raise SystemExit("no measured hold_30s sends to calibrate the pressure curve")
    return fit_curve(pressures)


def _cell_id(strategy: int, land: int, exit_i: int, route: int, size_i: int, priority: int) -> str:
    _i, name, bound, _k = LANDS[land]
    pri = f"{priority / LAMPORTS_PER_SOL:.4f}".rstrip("0").rstrip(".")
    return f"{STRATS[strategy]}|{name}|{bound}|{EXITS[exit_i]}|{ROUTES[route][0]}|{SIZES[size_i] / LAMPORTS_PER_SOL}|{pri}"


def _attach_ci(cell: dict[str, Any], rows: _Head, size: int) -> None:
    days: dict[int, float] = {}
    for day, flat in zip(rows.day, rows.flat):
        days[int(day)] = days.get(int(day), 0.0) + flat
    cell["days"] = len(days)
    cell["days_positive"] = sum(1 for total in days.values() if total > 0)
    cell["ci_lo_flat_pct"] = _ci_lo(rows.flat) / size * 100.0
    cell["ci_lo_pressure_pct"] = _ci_lo(rows.press) / size * 100.0
    ex = _ex_top(rows.flat, 3)
    cell["ex_top3_flat_sol"] = None if ex is None else ex / LAMPORTS_PER_SOL
    cell["total_flat_sol"] = sum(rows.flat) / LAMPORTS_PER_SOL


def _ci_lo(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    import random

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


def _ex_top(values: Sequence[float], k: int) -> float | None:
    if len(values) <= k:
        return None
    ordered = sorted(values, reverse=True)
    return sum(values) - sum(ordered[:k])


def _attach_extra_ci(path: Path, cells: dict[str, Any], wanted: list[str], curve: FailCurve) -> None:
    want = set(wanted)
    grouped: dict[str, list[float]] = {key: [] for key in want}
    days: dict[str, dict[int, float]] = {key: {} for key in want}
    with path.open("rb") as fh:
        while True:
            raw = fh.read(REC.size)
            if len(raw) < REC.size:
                break
            strategy, land, exit_i, route, size_i, status, sides, day, net0, gross, buys, nearby = REC.unpack(raw)
            if strategy == 1:
                continue
            pressure = curve.p(Pressure(int(buys), int(nearby))) if status == SEND else 0.0
            for priority in PRIORITIES:
                cell_id = _cell_id(strategy, land, exit_i, route, size_i, priority)
                if cell_id not in want:
                    continue
                flat = mixed_net(net0, sides, status, priority, FLAT_FAIL if status == SEND else 0.0)
                grouped[cell_id].append(flat)
                days[cell_id][day] = days[cell_id].get(day, 0.0) + flat
    for cell_id, values in grouped.items():
        cell = cells[cell_id]
        size = _size_from_id(cell_id)
        cell["days"] = len(days[cell_id])
        cell["days_positive"] = sum(1 for total in days[cell_id].values() if total > 0)
        cell["ci_lo_flat_pct"] = _ci_lo(values) / size * 100.0
        cell["ex_top3_flat_sol"] = None if _ex_top(values, 3) is None else _ex_top(values, 3) / LAMPORTS_PER_SOL
        cell["total_flat_sol"] = sum(values) / LAMPORTS_PER_SOL


def _size_from_id(cell_id: str) -> int:
    parts = cell_id.split("|")
    sol = float(parts[5])
    return int(round(sol * LAMPORTS_PER_SOL))


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper latency curve on sealed backfill")
    parser.add_argument("--backfill", type=Path, default=Path("/var/lib/mal/backfill"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, required=True)
    parser.add_argument("--lag-file", type=Path, default=Path("/var/lib/mal/paper/forward-paper/runner-status.json"))
    parser.add_argument("--graph-dir", type=Path, default=Path("/var/lib/mal/graph"))
    parser.add_argument("--holdout-end", default=HOLDOUT_END)
    args = parser.parse_args()
    run_holdout(
        backfill=args.backfill,
        out_json=args.out,
        attempts_path=args.attempts,
        lag_path=args.lag_file,
        graph_dir=args.graph_dir,
        end_iso=args.holdout_end,
    )


if __name__ == "__main__":
    main()
