#!/usr/bin/env python3
"""Tape L2 smart-wallet leaderboard (paper-only).

Builds a ranked, veto-filtered wallet list from sealed trade-tape JSONL
(observe.trade_tape). FIFO realized PnL is on-chain cash flow (curve fees
already in buy spend / sell proceeds) minus a small per-trade tx haircut.
Copy-trade portal fees (~0.5%/side; ~3.5% round trip with curve) are a
feature for the follow backtest, not the rank of *their* fills.

Does not write to the recorder, its unit, or LAB_STATE. Designed for a
rolling 7–30 day window; short tapes are labeled noisy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

LAMPORTS_PER_SOL = 1_000_000_000
TX_FEE_LAMPORTS = 5_000
# Portal Local 0.5%/side on top of curve fees already in the tape.
PORTAL_BPS_PER_SIDE = 50
# Documented copy round-trip before impact (curve 1.25%*2 + portal 0.5%*2).
COPY_ROUND_TRIP_BPS = 350
DUST_TOKEN_RAW = 1
SNIPER_MAX_DELTA_SLOTS = 2
ORGANIC_MIN_DELTA_SLOTS = 3
ORGANIC_MAX_DELTA_SLOTS = 150
DEFAULT_CAP = 50
FOLLOW_WINDOWS_MS = (400, 1_000, 2_000, 5_000, 10_000, 30_000)

VETO_SNIPER = "sniper_bundler"
VETO_CREATOR = "creator"
VETO_CREATOR_LINKED = "creator_linked"
VETO_BOT = "bot"
VETO_WASH = "wash"
VETO_FARM = "follower_farm"
VETO_TRANSFER_IN = "transfer_in"
VETO_ONE_HIT = "one_hit"
VETO_WR_EXTREME = "win_rate_extreme"

ALL_VETOES = (
    VETO_SNIPER,
    VETO_CREATOR,
    VETO_CREATOR_LINKED,
    VETO_BOT,
    VETO_WASH,
    VETO_FARM,
    VETO_TRANSFER_IN,
    VETO_ONE_HIT,
    VETO_WR_EXTREME,
)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Eligibility for a ranked copy list. Research v0; all fields CLI-tunable."""

    min_closed_mints: int = 20
    min_win_rate: float = 0.35
    max_win_rate: float = 0.65
    min_median_hold_ms: int = 2 * 60 * 1000
    max_median_hold_ms: int = 30 * 60 * 1000
    max_single_mint_pnl_share: float = 0.40
    min_realized_pnl_sol: float = 0.0
    min_invested_sol: float = 1.0
    cap: int = DEFAULT_CAP
    name: str = "strict"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "min_closed_mints": self.min_closed_mints,
            "min_win_rate": self.min_win_rate,
            "max_win_rate": self.max_win_rate,
            "min_median_hold_ms": self.min_median_hold_ms,
            "max_median_hold_ms": self.max_median_hold_ms,
            "max_single_mint_pnl_share": self.max_single_mint_pnl_share,
            "min_realized_pnl_sol": self.min_realized_pnl_sol,
            "min_invested_sol": self.min_invested_sol,
            "cap": self.cap,
        }


# Short tapes cannot meet 30d research floors. Still publish a labeled board.
NOISY_V0 = Thresholds(
    min_closed_mints=3,
    min_win_rate=0.30,
    max_win_rate=0.75,
    min_median_hold_ms=15_000,
    max_median_hold_ms=30 * 60 * 1000,
    max_single_mint_pnl_share=0.70,
    min_realized_pnl_sol=0.0,
    min_invested_sol=0.05,
    cap=DEFAULT_CAP,
    name="noisy_v0",
)

STRICT = Thresholds(name="strict")


@dataclass(slots=True)
class Trade:
    t_ms: int
    slot: int
    mint: str
    trader: str
    side: str
    sol_lamports: int
    token_raw: int
    signature: str
    venue: str
    event_index: int
    price_sol: float | None = None
    market_cap_sol: float | None = None


@dataclass(slots=True)
class Lot:
    token_raw: int
    cost_lamports: int
    t_ms: int
    slot: int
    signature: str = ""


@dataclass(slots=True)
class RoundTrip:
    mint: str
    pnl_lamports: int
    cost_lamports: int
    proceeds_lamports: int
    hold_ms: int
    first_buy_ms: int
    last_sell_ms: int
    first_buy_slot: int
    last_sell_slot: int
    buyer_rank: int
    delta_slot: int | None
    sniper: bool
    organic_early: bool


@dataclass(slots=True)
class MintMeta:
    first_slot: int
    first_t_ms: int
    first_sig: str
    first_trader: str
    first_venue: str
    create_slot: int | None = None
    create_sig: str | None = None
    creator: str | None = None
    buyer_order: list[str] = field(default_factory=list)
    buyer_rank: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class WalletAcc:
    trader: str
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    buy_sol_lamports: int = 0
    sell_sol_lamports: int = 0
    tx_fees_lamports: int = 0
    unmatched_sell_lamports: int = 0
    unmatched_sell_count: int = 0
    lots: dict[str, deque[Lot]] = field(default_factory=dict)
    pos_first_buy_ms: dict[str, int] = field(default_factory=dict)
    pos_first_buy_slot: dict[str, int] = field(default_factory=dict)
    pos_cost_lamports: dict[str, int] = field(default_factory=dict)
    pos_proceeds_lamports: dict[str, int] = field(default_factory=dict)
    pos_pnl_lamports: dict[str, int] = field(default_factory=dict)
    mint_trade_count: dict[str, int] = field(default_factory=dict)
    trips: list[RoundTrip] = field(default_factory=list)
    lot_hold_ms: list[int] = field(default_factory=list)
    sniper_buys: int = 0
    sniper_buy_sol: int = 0
    organic_early_buys: int = 0
    first_print_mints: int = 0
    same_create_tx_buys: int = 0
    subsecond_lot_holds: int = 0
    same_tx_roundtrips: int = 0
    first_buy_ms: int | None = None
    last_t_ms: int | None = None
    signatures_approx: int = 0


@dataclass(slots=True)
class WalletReport:
    wallet: str
    trade_count: int
    buy_count: int
    sell_count: int
    closed_mints: int
    open_mints: int
    win_count: int
    loss_count: int
    win_rate: float | None
    realized_pnl_sol: float
    copy_haircut_pnl_sol: float
    invested_sol: float
    proceeds_sol: float
    unmatched_sell_sol: float
    top_mint: str | None
    top_mint_pnl_sol: float
    top_mint_pnl_share: float | None
    median_hold_ms: int | None
    p25_hold_ms: int | None
    p75_hold_ms: int | None
    median_lot_hold_ms: int | None
    profit_factor: float | None
    avg_r: float | None
    sniper_buy_frac: float
    organic_early_frac: float
    first_print_mints: int
    same_create_tx_buys: int
    subsecond_lot_hold_frac: float
    farm_closed_frac: float
    wash_closed_frac: float
    buyer_rank_median: float | None
    median_delta_slot: float | None
    vetoes: list[str]
    score: float
    reasons: list[str]
    hold_ms_n: int
    window_span_ms: int | None
    trades_per_min: float | None


def lamports_to_sol(lamports: int | float) -> float:
    return float(lamports) / LAMPORTS_PER_SOL


def _pct(xs: Sequence[int], p: float) -> int | None:
    if not xs:
        return None
    ordered = sorted(xs)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * (p / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return ordered[lo]
    frac = idx - lo
    return int(round(ordered[lo] * (1.0 - frac) + ordered[hi] * frac))


def _median(xs: Sequence[int]) -> int | None:
    return _pct(xs, 50.0)


def parse_trade(row: Mapping[str, Any]) -> Trade | None:
    if row.get("type") not in (None, "trade"):
        return None
    mint = row.get("mint")
    trader = row.get("trader")
    side = row.get("side")
    if not isinstance(mint, str) or not mint:
        return None
    if not isinstance(trader, str) or not trader:
        return None
    if side not in ("buy", "sell"):
        return None
    if row.get("mint_source") == "unresolved":
        return None
    venue = row.get("venue") or ""
    # Same quote rule as the fill simulator: PumpSwap counts only when the flag is true.
    if str(venue) == "pumpswap" and row.get("quote_is_wsol") is not True:
        return None
    if row.get("quote_is_wsol") is False:
        return None
    try:
        t_ms = int(row["t_recv_ms"])
        slot = int(row["slot"])
        sol_lamports = int(row["sol_lamports"])
        token_raw = int(row["token_raw"])
        event_index = int(row.get("event_index") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if sol_lamports <= 0 or token_raw <= 0:
        return None
    sig = row.get("signature")
    venue = row.get("venue") or ""
    if not isinstance(sig, str) or not sig:
        return None
    price = row.get("price_sol")
    mcap = row.get("market_cap_sol")
    return Trade(
        t_ms=t_ms,
        slot=slot,
        mint=mint,
        trader=trader,
        side=side,
        sol_lamports=sol_lamports,
        token_raw=token_raw,
        signature=sig,
        venue=str(venue),
        event_index=event_index,
        price_sol=float(price) if isinstance(price, (int, float)) else None,
        market_cap_sol=float(mcap) if isinstance(mcap, (int, float)) else None,
    )


def iter_jsonl(paths: Sequence[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row


def resolve_inputs(raw: Sequence[str]) -> list[Path]:
    out: list[Path] = []
    for item in raw:
        path = Path(item)
        if path.is_dir():
            found = sorted(path.glob("trades-*.jsonl"))
            if not found:
                found = sorted(path.glob("*.jsonl"))
            out.extend(found)
        elif path.is_file():
            out.append(path)
        else:
            matched = sorted(Path().glob(item))
            if not matched:
                raise SystemExit(f"input not found: {item}")
            out.extend(matched)
    # unique, keep order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for path in out:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        uniq.append(path)
    if not uniq:
        raise SystemExit("no trade JSONL inputs")
    return uniq


def load_creates(path: Path | None) -> dict[str, dict[str, Any]]:
    """Optional mint -> {creator, slot, signature, t_ms} overlay."""
    if path is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl([path]):
        mint = row.get("mint") or row.get("token")
        if not isinstance(mint, str) or not mint:
            continue
        creator = (
            row.get("creator")
            or row.get("user")
            or row.get("traderPublicKey")
            or row.get("trader")
        )
        rec: dict[str, Any] = {}
        if isinstance(creator, str) and creator:
            rec["creator"] = creator
        slot = row.get("slot")
        sig = row.get("signature") or row.get("create_signature")
        t_ms = row.get("t_ms") or row.get("t_recv_ms")
        if isinstance(slot, int):
            rec["slot"] = slot
        elif isinstance(slot, str) and slot.isdigit():
            rec["slot"] = int(slot)
        if isinstance(sig, str) and sig:
            rec["signature"] = sig
        if isinstance(t_ms, (int, float)):
            rec["t_ms"] = int(t_ms)
        if rec:
            out[mint] = rec
    return out


def remaining_tokens(lots: deque[Lot]) -> int:
    return sum(lot.token_raw for lot in lots)


def match_sell(
    lots: deque[Lot],
    token_raw: int,
    sol_lamports: int,
    sell_t_ms: int,
    sell_sig: str,
) -> tuple[int, int, int, list[int], int, int]:
    """FIFO match. Returns pnl, cost, proceeds, holds_ms, unmatched_sol, same_tx_rt."""
    if token_raw <= 0 or sol_lamports <= 0:
        return 0, 0, 0, [], 0, 0
    inv = remaining_tokens(lots)
    if inv <= 0:
        return 0, 0, 0, [], sol_lamports, 0
    sell_tokens = token_raw
    sell_sol = sol_lamports
    unmatched_sol = 0
    if sell_tokens > inv:
        matched_frac = inv / sell_tokens
        unmatched_sol = int(round(sell_sol * (1.0 - matched_frac)))
        sell_sol -= unmatched_sol
        sell_tokens = inv
    left = sell_tokens
    proceeds_left = sell_sol
    pnl = 0
    matched_cost = 0
    matched_proceeds = 0
    holds: list[int] = []
    same_tx = 0
    while left > 0 and lots:
        lot = lots[0]
        take = min(lot.token_raw, left)
        orig_tokens = lot.token_raw
        orig_cost = lot.cost_lamports
        take_cost = (orig_cost * take) // orig_tokens if orig_tokens else 0
        take_proceeds = (proceeds_left * take) // left if left else 0
        pnl += take_proceeds - take_cost
        matched_cost += take_cost
        matched_proceeds += take_proceeds
        holds.append(max(0, sell_t_ms - lot.t_ms))
        if lot.signature and lot.signature == sell_sig:
            same_tx += 1
        lot.token_raw -= take
        lot.cost_lamports -= take_cost
        left -= take
        proceeds_left -= take_proceeds
        if lot.token_raw <= DUST_TOKEN_RAW:
            lots.popleft()
    return pnl, matched_cost, matched_proceeds, holds, unmatched_sol, same_tx


def note_buy_rank(meta: MintMeta, trader: str) -> int:
    rank = meta.buyer_rank.get(trader)
    if rank is not None:
        return rank
    rank = len(meta.buyer_order) + 1
    meta.buyer_order.append(trader)
    meta.buyer_rank[trader] = rank
    return rank


def build_mint_meta(
    trades: Sequence[Trade],
    creates: Mapping[str, Mapping[str, Any]],
) -> dict[str, MintMeta]:
    metas: dict[str, MintMeta] = {}
    for tr in trades:
        meta = metas.get(tr.mint)
        if meta is None:
            meta = MintMeta(
                first_slot=tr.slot,
                first_t_ms=tr.t_ms,
                first_sig=tr.signature,
                first_trader=tr.trader,
                first_venue=tr.venue,
            )
            overlay = creates.get(tr.mint)
            if overlay:
                if isinstance(overlay.get("creator"), str):
                    meta.creator = overlay["creator"]
                if isinstance(overlay.get("slot"), int):
                    meta.create_slot = overlay["slot"]
                if isinstance(overlay.get("signature"), str):
                    meta.create_sig = overlay["signature"]
            if meta.create_slot is None:
                meta.create_slot = tr.slot
            if meta.create_sig is None:
                meta.create_sig = tr.signature
            if meta.creator is None and tr.venue == "pump_bonding":
                meta.creator = tr.trader
            metas[tr.mint] = meta
        else:
            if tr.slot < meta.first_slot or (
                tr.slot == meta.first_slot and tr.t_ms < meta.first_t_ms
            ):
                meta.first_slot = tr.slot
                meta.first_t_ms = tr.t_ms
                meta.first_sig = tr.signature
                meta.first_trader = tr.trader
                meta.first_venue = tr.venue
                if tr.mint not in creates:
                    meta.create_slot = tr.slot
                    meta.create_sig = tr.signature
                    if tr.venue == "pump_bonding":
                        meta.creator = tr.trader
        if tr.side == "buy":
            note_buy_rank(meta, tr.trader)
    return metas


def consume_trades(
    trades: Sequence[Trade],
    metas: Mapping[str, MintMeta],
) -> dict[str, WalletAcc]:
    wallets: dict[str, WalletAcc] = {}
    for tr in trades:
        acc = wallets.get(tr.trader)
        if acc is None:
            acc = WalletAcc(trader=tr.trader)
            wallets[tr.trader] = acc
        acc.trade_count += 1
        acc.tx_fees_lamports += TX_FEE_LAMPORTS
        acc.signatures_approx += 1 if tr.event_index == 0 else 0
        acc.mint_trade_count[tr.mint] = acc.mint_trade_count.get(tr.mint, 0) + 1
        if acc.first_buy_ms is None and tr.side == "buy":
            acc.first_buy_ms = tr.t_ms
        acc.last_t_ms = tr.t_ms
        meta = metas[tr.mint]
        create_slot = meta.create_slot if meta.create_slot is not None else meta.first_slot
        delta_slot = tr.slot - create_slot
        create_sig = meta.create_sig or meta.first_sig
        if tr.side == "buy":
            acc.buy_count += 1
            acc.buy_sol_lamports += tr.sol_lamports
            rank = meta.buyer_rank.get(tr.trader, 0)
            sniper = delta_slot <= SNIPER_MAX_DELTA_SLOTS or tr.signature == create_sig
            if sniper:
                acc.sniper_buys += 1
                acc.sniper_buy_sol += tr.sol_lamports
            if ORGANIC_MIN_DELTA_SLOTS <= delta_slot <= ORGANIC_MAX_DELTA_SLOTS:
                acc.organic_early_buys += 1
            if tr.trader == meta.first_trader and rank == 1:
                # counted once per mint via first_print below
                pass
            if tr.signature == create_sig:
                acc.same_create_tx_buys += 1
            lots = acc.lots.get(tr.mint)
            if lots is None:
                lots = deque()
                acc.lots[tr.mint] = lots
            if remaining_tokens(lots) <= DUST_TOKEN_RAW:
                acc.pos_first_buy_ms[tr.mint] = tr.t_ms
                acc.pos_first_buy_slot[tr.mint] = tr.slot
            lots.append(
                Lot(
                    token_raw=tr.token_raw,
                    cost_lamports=tr.sol_lamports,
                    t_ms=tr.t_ms,
                    slot=tr.slot,
                    signature=tr.signature,
                )
            )
        else:
            acc.sell_count += 1
            acc.sell_sol_lamports += tr.sol_lamports
            lots = acc.lots.get(tr.mint)
            if lots is None:
                lots = deque()
                acc.lots[tr.mint] = lots
            pnl, cost, proceeds, holds, unmatched, same_tx = match_sell(
                lots, tr.token_raw, tr.sol_lamports, tr.t_ms, tr.signature
            )
            acc.unmatched_sell_lamports += unmatched
            if unmatched:
                acc.unmatched_sell_count += 1
            acc.pos_pnl_lamports[tr.mint] = acc.pos_pnl_lamports.get(tr.mint, 0) + pnl
            acc.pos_cost_lamports[tr.mint] = acc.pos_cost_lamports.get(tr.mint, 0) + cost
            acc.pos_proceeds_lamports[tr.mint] = acc.pos_proceeds_lamports.get(tr.mint, 0) + proceeds
            acc.lot_hold_ms.extend(holds)
            acc.same_tx_roundtrips += same_tx
            for hold in holds:
                if hold < 1_000:
                    acc.subsecond_lot_holds += 1
            if remaining_tokens(lots) <= DUST_TOKEN_RAW and cost > 0:
                first_ms = acc.pos_first_buy_ms.get(tr.mint, tr.t_ms)
                first_slot = acc.pos_first_buy_slot.get(tr.mint, tr.slot)
                trip_delta = first_slot - create_slot
                rank = meta.buyer_rank.get(tr.trader, 0)
                sniper = trip_delta <= SNIPER_MAX_DELTA_SLOTS
                organic = ORGANIC_MIN_DELTA_SLOTS <= trip_delta <= ORGANIC_MAX_DELTA_SLOTS
                acc.trips.append(
                    RoundTrip(
                        mint=tr.mint,
                        pnl_lamports=acc.pos_pnl_lamports.get(tr.mint, 0),
                        cost_lamports=acc.pos_cost_lamports.get(tr.mint, 0),
                        proceeds_lamports=acc.pos_proceeds_lamports.get(tr.mint, 0),
                        hold_ms=max(0, tr.t_ms - first_ms),
                        first_buy_ms=first_ms,
                        last_sell_ms=tr.t_ms,
                        first_buy_slot=first_slot,
                        last_sell_slot=tr.slot,
                        buyer_rank=rank,
                        delta_slot=trip_delta,
                        sniper=sniper,
                        organic_early=organic,
                    )
                )
                acc.pos_pnl_lamports[tr.mint] = 0
                acc.pos_cost_lamports[tr.mint] = 0
                acc.pos_proceeds_lamports[tr.mint] = 0
                acc.pos_first_buy_ms.pop(tr.mint, None)
                acc.pos_first_buy_slot.pop(tr.mint, None)
    for _mint, meta in metas.items():
        acc = wallets.get(meta.first_trader)
        if acc is not None:
            acc.first_print_mints += 1
        if meta.creator:
            cacc = wallets.get(meta.creator)
            if cacc is not None and meta.creator != meta.first_trader:
                cacc.first_print_mints += 1
    return wallets


def _farm_and_wash(
    acc: WalletAcc,
    mint_buys: Mapping[str, Sequence[tuple[int, str]]],
) -> tuple[float, float, dict[str, bool]]:
    """Return (farm_frac, wash_frac, mint->farm)."""
    if not acc.trips:
        return 0.0, 0.0, {}
    farm_n = 0
    wash_n = 0
    farm_mints: dict[str, bool] = {}
    for trip in acc.trips:
        buys = mint_buys.get(trip.mint) or ()
        followers: set[str] = set()
        for t_ms, trader in buys:
            if trader == acc.trader:
                continue
            if trip.first_buy_ms < t_ms <= trip.last_sell_ms + 2_000:
                followers.add(trader)
        dump_into_wave = (
            trip.organic_early
            and trip.buyer_rank <= 5
            and trip.pnl_lamports > 0
            and len(followers) >= 8
        )
        farm_mints[trip.mint] = dump_into_wave
        if dump_into_wave:
            farm_n += 1
        if trip.cost_lamports > 0:
            ratio = abs(trip.proceeds_lamports - trip.cost_lamports) / trip.cost_lamports
            if ratio <= 0.02 and trip.hold_ms < 5_000:
                wash_n += 1
    n = len(acc.trips)
    return farm_n / n, wash_n / n, farm_mints


def veto_flags(
    acc: WalletAcc,
    realized_pnl_lamports: int,
    top_share: float | None,
    win_rate: float | None,
    median_hold_ms: int | None,
    median_lot_hold_ms: int | None,
    farm_frac: float,
    wash_frac: float,
    creator_wallets: set[str],
    creator_tx_wallets: set[str],
) -> list[str]:
    flags: list[str] = []
    buys = max(acc.buy_count, 1)
    sniper_frac = acc.sniper_buys / buys
    if sniper_frac >= 0.50 or acc.same_create_tx_buys / buys >= 0.40:
        flags.append(VETO_SNIPER)
    if acc.trader in creator_wallets or acc.first_print_mints >= 2:
        flags.append(VETO_CREATOR)
    if acc.trader in creator_tx_wallets and VETO_CREATOR not in flags:
        flags.append(VETO_CREATOR_LINKED)
    lot_n = max(len(acc.lot_hold_ms), 1)
    sub_frac = acc.subsecond_lot_holds / lot_n
    span = None
    if acc.first_buy_ms is not None and acc.last_t_ms is not None:
        span = max(1, acc.last_t_ms - acc.first_buy_ms)
    tpm = None
    if span:
        tpm = acc.trade_count / (span / 60_000)
    bot = (
        (median_lot_hold_ms is not None and median_lot_hold_ms < 10_000)
        or (median_hold_ms is not None and median_hold_ms < 10_000)
        or sub_frac >= 0.50
        or (tpm is not None and tpm >= 10.0)
        or acc.same_tx_roundtrips >= 3
    )
    if bot:
        flags.append(VETO_BOT)
    if wash_frac >= 0.40:
        flags.append(VETO_WASH)
    if farm_frac >= 0.40:
        flags.append(VETO_FARM)
    if acc.unmatched_sell_lamports > 0 and (
        acc.buy_sol_lamports == 0
        or acc.unmatched_sell_lamports >= int(0.5 * max(acc.buy_sol_lamports, 1))
    ):
        flags.append(VETO_TRANSFER_IN)
    if top_share is not None and top_share > 0.70 and realized_pnl_lamports > 0:
        flags.append(VETO_ONE_HIT)
    if win_rate is not None and win_rate > 0.80 and (
        (median_hold_ms is not None and median_hold_ms < 30_000)
        or (acc.trips and (sum(max(0, t.pnl_lamports) for t in acc.trips) / max(1, sum(t.cost_lamports for t in acc.trips))) < 0.10)
    ):
        flags.append(VETO_WR_EXTREME)
    # unique preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        ordered.append(flag)
    return ordered


def _profit_factor(trips: Sequence[RoundTrip]) -> float | None:
    wins = sum(t.pnl_lamports for t in trips if t.pnl_lamports > 0)
    losses = sum(-t.pnl_lamports for t in trips if t.pnl_lamports < 0)
    if losses <= 0 and wins > 0:
        return 10.0
    if losses <= 0:
        return None
    return min(10.0, wins / losses)


def _avg_r(trips: Sequence[RoundTrip]) -> float | None:
    rs: list[float] = []
    for t in trips:
        if t.cost_lamports <= 0:
            continue
        rs.append(t.pnl_lamports / t.cost_lamports)
    if not rs:
        return None
    return sum(rs) / len(rs)


def report_wallet(
    acc: WalletAcc,
    mint_buys: Mapping[str, Sequence[tuple[int, str]]],
    creator_wallets: set[str],
    creator_tx_wallets: set[str],
) -> WalletReport:
    farm_frac, wash_frac, _ = _farm_and_wash(acc, mint_buys)
    trip_pnl = defaultdict(int)
    for t in acc.trips:
        trip_pnl[t.mint] += t.pnl_lamports
    realized_gross = sum(trip_pnl.values())
    realized = realized_gross - acc.tx_fees_lamports
    # still-open inventory is not ranked (unrealized)
    invested = sum(t.cost_lamports for t in acc.trips)
    proceeds = sum(t.proceeds_lamports for t in acc.trips)
    portal = (invested + proceeds) * PORTAL_BPS_PER_SIDE // 10_000
    copy_pnl = realized - portal
    closed = len(trip_pnl)
    open_mints = sum(1 for mint, lots in acc.lots.items() if remaining_tokens(lots) > DUST_TOKEN_RAW)
    wins = sum(1 for pnl in trip_pnl.values() if pnl > 0)
    losses = sum(1 for pnl in trip_pnl.values() if pnl < 0)
    wr = (wins / closed) if closed else None
    holds = [t.hold_ms for t in acc.trips]
    med_hold = _median(holds)
    top_mint = None
    top_pnl = 0
    for mint, pnl in trip_pnl.items():
        if abs(pnl) > abs(top_pnl) or (abs(pnl) == abs(top_pnl) and pnl > top_pnl):
            top_mint = mint
            top_pnl = pnl
    share = None
    if realized_gross > 0 and top_pnl > 0:
        share = min(1.0, top_pnl / realized_gross)
    elif realized_gross < 0 and top_pnl < 0:
        share = min(1.0, abs(top_pnl) / abs(realized_gross))
    med_lot = _median(acc.lot_hold_ms)
    pf = _profit_factor(acc.trips)
    avg_r = _avg_r(acc.trips)
    buys = max(acc.buy_count, 1)
    ranks = [t.buyer_rank for t in acc.trips if t.buyer_rank > 0]
    deltas = [t.delta_slot for t in acc.trips if t.delta_slot is not None]
    span = None
    tpm = None
    if acc.first_buy_ms is not None and acc.last_t_ms is not None:
        span = max(0, acc.last_t_ms - acc.first_buy_ms)
        if span > 0:
            tpm = acc.trade_count / (span / 60_000)
    vetoes = veto_flags(
        acc,
        realized,
        share,
        wr,
        med_hold,
        med_lot,
        farm_frac,
        wash_frac,
        creator_wallets,
        creator_tx_wallets,
    )
    conc_pen = 1.0 - min(0.99, share or 0.0)
    n = math.sqrt(max(closed, 0))
    pf_term = min(pf or 1.0, 5.0)
    score = lamports_to_sol(realized) * n * conc_pen * pf_term
    reasons = [
        f"closed_mints={closed}",
        f"win_rate={wr * 100:.1f}%" if wr is not None else "win_rate=n/a",
        f"median_hold_ms={med_hold}" if med_hold is not None else "median_hold=n/a",
        f"realized_pnl_sol={lamports_to_sol(realized):+.4f}",
        f"top_mint_share={share * 100:.1f}%" if share is not None else "top_mint_share=n/a",
        f"profit_factor={pf:.2f}" if pf is not None else "profit_factor=n/a",
        f"organic_early={acc.organic_early_buys / buys * 100:.0f}%",
        f"sniper_buys={sniper_frac_str(acc.sniper_buys, buys)}",
    ]
    if vetoes:
        reasons.append("veto=" + ",".join(vetoes))
    return WalletReport(
        wallet=acc.trader,
        trade_count=acc.trade_count,
        buy_count=acc.buy_count,
        sell_count=acc.sell_count,
        closed_mints=closed,
        open_mints=open_mints,
        win_count=wins,
        loss_count=losses,
        win_rate=wr,
        realized_pnl_sol=lamports_to_sol(realized),
        copy_haircut_pnl_sol=lamports_to_sol(copy_pnl),
        invested_sol=lamports_to_sol(invested),
        proceeds_sol=lamports_to_sol(proceeds),
        unmatched_sell_sol=lamports_to_sol(acc.unmatched_sell_lamports),
        top_mint=top_mint,
        top_mint_pnl_sol=lamports_to_sol(top_pnl),
        top_mint_pnl_share=share,
        median_hold_ms=med_hold,
        p25_hold_ms=_pct(holds, 25.0),
        p75_hold_ms=_pct(holds, 75.0),
        median_lot_hold_ms=med_lot,
        profit_factor=pf,
        avg_r=avg_r,
        sniper_buy_frac=acc.sniper_buys / buys,
        organic_early_frac=acc.organic_early_buys / buys,
        first_print_mints=acc.first_print_mints,
        same_create_tx_buys=acc.same_create_tx_buys,
        subsecond_lot_hold_frac=acc.subsecond_lot_holds / max(len(acc.lot_hold_ms), 1),
        farm_closed_frac=farm_frac,
        wash_closed_frac=wash_frac,
        buyer_rank_median=(sorted(ranks)[len(ranks) // 2] if ranks else None),
        median_delta_slot=(float(sorted(deltas)[len(deltas) // 2]) if deltas else None),
        vetoes=vetoes,
        score=score,
        reasons=reasons,
        hold_ms_n=len(holds),
        window_span_ms=span,
        trades_per_min=tpm,
    )


def sniper_frac_str(n: int, buys: int) -> str:
    return f"{n / buys * 100:.0f}%"


def eligible(rep: WalletReport, th: Thresholds) -> tuple[bool, list[str]]:
    misses: list[str] = []
    if rep.closed_mints < th.min_closed_mints:
        misses.append(f"closed_mints<{th.min_closed_mints}")
    if rep.win_rate is None:
        misses.append("win_rate=n/a")
    else:
        if rep.win_rate < th.min_win_rate:
            misses.append(f"win_rate<{th.min_win_rate}")
        if rep.win_rate > th.max_win_rate:
            misses.append(f"win_rate>{th.max_win_rate}")
    if rep.median_hold_ms is None:
        misses.append("median_hold=n/a")
    else:
        if rep.median_hold_ms < th.min_median_hold_ms:
            misses.append("median_hold_short")
        if rep.median_hold_ms > th.max_median_hold_ms:
            misses.append("median_hold_long")
    if rep.top_mint_pnl_share is not None and rep.top_mint_pnl_share > th.max_single_mint_pnl_share:
        misses.append("one_mint_share")
    if rep.realized_pnl_sol <= th.min_realized_pnl_sol:
        misses.append("pnl_not_positive")
    if rep.invested_sol < th.min_invested_sol:
        misses.append("invested_low")
    if rep.vetoes:
        misses.append("veto")
    return (not misses, misses)


def rank_board(reports: Sequence[WalletReport], th: Thresholds) -> list[dict[str, Any]]:
    scored: list[tuple[WalletReport, list[str]]] = []
    for rep in reports:
        ok, _misses = eligible(rep, th)
        if ok:
            scored.append((rep, []))
    scored.sort(key=lambda pair: (pair[0].score, pair[0].realized_pnl_sol, pair[0].closed_mints), reverse=True)
    out: list[dict[str, Any]] = []
    for i, (rep, _) in enumerate(scored[: th.cap], start=1):
        row = report_to_dict(rep)
        row["rank"] = i
        row["board"] = th.name
        out.append(row)
    return out


def report_to_dict(rep: WalletReport) -> dict[str, Any]:
    return {
        "wallet": rep.wallet,
        "trade_count": rep.trade_count,
        "buy_count": rep.buy_count,
        "sell_count": rep.sell_count,
        "closed_mints": rep.closed_mints,
        "open_mints": rep.open_mints,
        "win_count": rep.win_count,
        "loss_count": rep.loss_count,
        "win_rate": None if rep.win_rate is None else round(rep.win_rate, 4),
        "realized_pnl_sol": round(rep.realized_pnl_sol, 6),
        "copy_haircut_pnl_sol": round(rep.copy_haircut_pnl_sol, 6),
        "invested_sol": round(rep.invested_sol, 6),
        "proceeds_sol": round(rep.proceeds_sol, 6),
        "unmatched_sell_sol": round(rep.unmatched_sell_sol, 6),
        "top_mint": rep.top_mint,
        "top_mint_pnl_sol": round(rep.top_mint_pnl_sol, 6),
        "top_mint_pnl_share": None if rep.top_mint_pnl_share is None else round(rep.top_mint_pnl_share, 4),
        "median_hold_ms": rep.median_hold_ms,
        "p25_hold_ms": rep.p25_hold_ms,
        "p75_hold_ms": rep.p75_hold_ms,
        "median_lot_hold_ms": rep.median_lot_hold_ms,
        "profit_factor": None if rep.profit_factor is None else round(rep.profit_factor, 4),
        "avg_r": None if rep.avg_r is None else round(rep.avg_r, 4),
        "sniper_buy_frac": round(rep.sniper_buy_frac, 4),
        "organic_early_frac": round(rep.organic_early_frac, 4),
        "first_print_mints": rep.first_print_mints,
        "same_create_tx_buys": rep.same_create_tx_buys,
        "subsecond_lot_hold_frac": round(rep.subsecond_lot_hold_frac, 4),
        "farm_closed_frac": round(rep.farm_closed_frac, 4),
        "wash_closed_frac": round(rep.wash_closed_frac, 4),
        "buyer_rank_median": rep.buyer_rank_median,
        "median_delta_slot": rep.median_delta_slot,
        "vetoes": list(rep.vetoes),
        "score": round(rep.score, 6),
        "reasons": list(rep.reasons),
        "hold_ms_n": rep.hold_ms_n,
        "window_span_ms": rep.window_span_ms,
        "trades_per_min": None if rep.trades_per_min is None else round(rep.trades_per_min, 4),
        "copy_round_trip_bps": COPY_ROUND_TRIP_BPS,
    }


def index_mint_buys(trades: Sequence[Trade]) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for tr in trades:
        if tr.side == "buy":
            out[tr.mint].append((tr.t_ms, tr.trader))
    return out


def index_mint_buy_full(trades: Sequence[Trade]) -> dict[str, list[Trade]]:
    out: dict[str, list[Trade]] = defaultdict(list)
    for tr in trades:
        if tr.side == "buy":
            out[tr.mint].append(tr)
    return out


def follower_wave(leader: Trade, later: Sequence[Trade]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    sols: dict[str, int] = {}
    first_dt: int | None = None
    seen: dict[str, set[str]] = {str(w): set() for w in FOLLOW_WINDOWS_MS}
    for tr in later:
        if tr.trader == leader.trader:
            continue
        if tr.mint != leader.mint or tr.side != "buy":
            continue
        if tr.t_ms < leader.t_ms:
            continue
        dt = tr.t_ms - leader.t_ms
        if first_dt is None:
            first_dt = dt
        for window in FOLLOW_WINDOWS_MS:
            if dt <= window:
                key = str(window)
                wallets = seen[key]
                if tr.trader not in wallets:
                    wallets.add(tr.trader)
                    counts[key] = counts.get(key, 0) + 1
                    sols[key] = sols.get(key, 0) + tr.sol_lamports
    return {
        "first_follower_dt_ms": first_dt,
        "unique_wallets_by_ms": {str(w): counts.get(str(w), 0) for w in FOLLOW_WINDOWS_MS},
        "sol_lamports_by_ms": {str(w): sols.get(str(w), 0) for w in FOLLOW_WINDOWS_MS},
    }


def iter_entry_buys(trades: Sequence[Trade], leaders: set[str]) -> Iterator[Trade]:
    seen: set[tuple[str, str]] = set()
    for tr in trades:
        if tr.side != "buy" or tr.trader not in leaders:
            continue
        key = (tr.trader, tr.mint)
        if key in seen:
            continue
        seen.add(key)
        yield tr


def follow_signals(
    trades: Sequence[Trade],
    metas: Mapping[str, MintMeta],
    reports: Mapping[str, WalletReport],
    board_rows: Sequence[Mapping[str, Any]],
    mint_buys: Mapping[str, Sequence[Trade]],
    board_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    leaders = {row["wallet"] for row in board_rows}
    signals: list[dict[str, Any]] = []
    waves: list[dict[str, Any]] = []
    for tr in iter_entry_buys(trades, leaders):
        meta = metas[tr.mint]
        create_slot = meta.create_slot if meta.create_slot is not None else meta.first_slot
        rep = reports[tr.trader]
        features = {
            "venue": tr.venue,
            "sol": lamports_to_sol(tr.sol_lamports),
            "token_raw": tr.token_raw,
            "buyer_rank": meta.buyer_rank.get(tr.trader),
            "delta_slot_from_create": tr.slot - create_slot,
            "delta_ms_from_create": tr.t_ms - meta.first_t_ms,
            "price_sol": tr.price_sol,
            "market_cap_sol": tr.market_cap_sol,
            "create_proxy_venue": meta.first_venue,
            "wallet_closed_mints": rep.closed_mints,
            "wallet_win_rate": rep.win_rate,
            "wallet_realized_pnl_sol": rep.realized_pnl_sol,
            "wallet_median_hold_ms": rep.median_hold_ms,
            "wallet_score": rep.score,
            "vetoes": list(rep.vetoes),
            "board": board_name,
            "signal_kind": "entry",
        }
        sig = {
            "v": 1,
            "type": "follow_signal",
            "mint": tr.mint,
            "signal_t_ms": tr.t_ms,
            "slot": tr.slot,
            "wallet": tr.trader,
            "signature": tr.signature,
            "features": features,
        }
        signals.append(sig)
        buys = mint_buys.get(tr.mint) or ()
        # later buys only; list is time-sorted with trades
        wave = follower_wave(tr, buys)
        waves.append(
            {
                "v": 1,
                "type": "follower_wave",
                "mint": tr.mint,
                "signal_t_ms": tr.t_ms,
                "slot": tr.slot,
                "wallet": tr.trader,
                **wave,
            }
        )
    return signals, waves


def summarize_waves(waves: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not waves:
        return {"n": 0}
    firsts = [w["first_follower_dt_ms"] for w in waves if w.get("first_follower_dt_ms") is not None]
    by_ms: dict[str, list[int]] = {str(x): [] for x in FOLLOW_WINDOWS_MS}
    sol_by_ms: dict[str, list[int]] = {str(x): [] for x in FOLLOW_WINDOWS_MS}
    for w in waves:
        uw = w.get("unique_wallets_by_ms") or {}
        sl = w.get("sol_lamports_by_ms") or {}
        for key in by_ms:
            by_ms[key].append(int(uw.get(key, 0)))
            sol_by_ms[key].append(int(sl.get(key, 0)))

    def avg(xs: Sequence[int]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "n": len(waves),
        "median_first_follower_dt_ms": _median(firsts) if firsts else None,
        "mean_unique_wallets_by_ms": {k: round(avg(v), 3) for k, v in by_ms.items()},
        "mean_sol_by_ms": {k: round(lamports_to_sol(avg(v)), 6) for k, v in sol_by_ms.items()},
        "p50_unique_wallets_2s": _median(by_ms["2000"]),
        "p50_unique_wallets_10s": _median(by_ms["10000"]),
    }


def load_trades(paths: Sequence[Path]) -> list[Trade]:
    trades: list[Trade] = []
    for row in iter_jsonl(paths):
        parsed = parse_trade(row)
        if parsed is not None:
            trades.append(parsed)
    trades.sort(key=lambda t: (t.t_ms, t.slot, t.signature, t.event_index))
    return trades


def run(
    paths: Sequence[Path],
    *,
    creates_path: Path | None = None,
    thresholds: Sequence[Thresholds] = (STRICT, NOISY_V0),
) -> dict[str, Any]:
    trades = load_trades(paths)
    creates = load_creates(creates_path)
    metas = build_mint_meta(trades, creates)
    wallets = consume_trades(trades, metas)
    mint_buys_light = index_mint_buys(trades)
    mint_buys_full = index_mint_buy_full(trades)
    creator_wallets = {m.creator for m in metas.values() if m.creator}
    creator_tx_wallets: set[str] = set()
    sig_traders: dict[str, set[str]] = defaultdict(set)
    for tr in trades:
        sig_traders[tr.signature].add(tr.trader)
    for meta in metas.values():
        sig = meta.create_sig or meta.first_sig
        if meta.creator:
            for other in sig_traders.get(sig, ()):
                if other != meta.creator:
                    creator_tx_wallets.add(other)
    reports = [
        report_wallet(acc, mint_buys_light, creator_wallets, creator_tx_wallets)
        for acc in wallets.values()
    ]
    reports.sort(key=lambda r: (r.score, r.realized_pnl_sol), reverse=True)
    by_wallet = {r.wallet: r for r in reports}
    t_min = trades[0].t_ms if trades else None
    t_max = trades[-1].t_ms if trades else None
    span_ms = (t_max - t_min) if t_min is not None and t_max is not None else 0
    span_days = span_ms / 86_400_000 if span_ms else 0.0
    noisy = span_days < 7.0
    boards: dict[str, list[dict[str, Any]]] = {}
    signals_out: dict[str, list[dict[str, Any]]] = {}
    waves_out: dict[str, list[dict[str, Any]]] = {}
    wave_summary: dict[str, Any] = {}
    for th in thresholds:
        board = rank_board(reports, th)
        boards[th.name] = board
        sigs, waves = follow_signals(trades, metas, by_wallet, board, mint_buys_full, th.name)
        signals_out[th.name] = sigs
        waves_out[th.name] = waves
        wave_summary[th.name] = summarize_waves(waves)
    closed_wallets = sum(1 for r in reports if r.closed_mints > 0)
    veto_counts: dict[str, int] = {k: 0 for k in ALL_VETOES}
    for r in reports:
        for v in r.vetoes:
            veto_counts[v] = veto_counts.get(v, 0) + 1
    return {
        "paper_only": True,
        "inputs": [str(p) for p in paths],
        "trades_n": len(trades),
        "wallets_n": len(reports),
        "wallets_with_closed_mints": closed_wallets,
        "mints_n": len(metas),
        "t_recv_ms_min": t_min,
        "t_recv_ms_max": t_max,
        "window_ms": span_ms,
        "window_days": round(span_days, 4),
        "noisy_short_window": noisy,
        "create_overlay_n": len(creates),
        "fee_model": {
            "tape_cashflow": "buy sol spent / sell sol received (curve fees inside)",
            "tx_fee_lamports_per_trade": TX_FEE_LAMPORTS,
            "portal_bps_per_side_copy_haircut_only": PORTAL_BPS_PER_SIDE,
            "copy_round_trip_bps_documented": COPY_ROUND_TRIP_BPS,
            "unrealized_ranked": False,
        },
        "thresholds": {th.name: th.as_dict() for th in thresholds},
        "veto_counts_all_wallets": veto_counts,
        "boards": boards,
        "board_sizes": {name: len(rows) for name, rows in boards.items()},
        "follow_signals": signals_out,
        "follower_waves": waves_out,
        "follower_wave_summary": wave_summary,
        "top_unfiltered_by_score": [report_to_dict(r) for r in reports[:20]],
        "honesty": [
            "CreateEvent is not on this tape. create_slot is the first print of the mint (bonding preferred) unless --creates overlay is passed.",
            "SOL transfers are not on this tape. creator_linked is same-signature as a create-proxy, not a funding graph.",
            "Strict thresholds need ~7–30d. Short windows are noisy_v0 and may be empty on strict.",
            "Do not blind-copy. Follow signals are a hook for a paper fill sim that must still pay ~3.5% round trip + lag.",
        ],
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in result.items() if k not in ("follow_signals", "follower_waves")}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for name, rows in result["boards"].items():
        board_path = output_dir / f"leaderboard-{name}.json"
        board_path.write_text(json.dumps({"board": name, "n": len(rows), "rows": rows}, indent=2) + "\n", encoding="utf-8")
        jsonl = output_dir / f"leaderboard-{name}.jsonl"
        with jsonl.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        sig_path = output_dir / f"follow-signals-{name}.jsonl"
        with sig_path.open("w", encoding="utf-8") as fh:
            for row in result["follow_signals"].get(name, []):
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        wave_path = output_dir / f"follower-waves-{name}.jsonl"
        with wave_path.open("w", encoding="utf-8") as fh:
            for row in result["follower_waves"].get(name, []):
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Tape L2 smart-wallet leaderboard (paper-only)")
    p.add_argument("--input", action="append", default=[], help="Trade JSONL file, dir, or glob. Repeatable.")
    p.add_argument("--creates", default=None, help="Optional mint/creator overlay JSONL")
    p.add_argument("--output-dir", default="data/observe/_wallet_leaderboard")
    p.add_argument(
        "--profile",
        choices=("strict", "noisy_v0", "both"),
        default="both",
        help="strict = research 30d floors; noisy_v0 = short-tape floors; both = default",
    )
    p.add_argument("--min-closed-mints", type=int, default=None)
    p.add_argument("--min-win-rate", type=float, default=None)
    p.add_argument("--max-win-rate", type=float, default=None)
    p.add_argument("--min-median-hold-ms", type=int, default=None)
    p.add_argument("--max-median-hold-ms", type=int, default=None)
    p.add_argument("--max-single-mint-pnl-share", type=float, default=None)
    p.add_argument("--min-realized-pnl-sol", type=float, default=None)
    p.add_argument("--min-invested-sol", type=float, default=None)
    p.add_argument("--cap", type=int, default=None)
    return p


def _apply_overrides(th: Thresholds, args: argparse.Namespace) -> Thresholds:
    kw = th.as_dict()
    mapping = {
        "min_closed_mints": args.min_closed_mints,
        "min_win_rate": args.min_win_rate,
        "max_win_rate": args.max_win_rate,
        "min_median_hold_ms": args.min_median_hold_ms,
        "max_median_hold_ms": args.max_median_hold_ms,
        "max_single_mint_pnl_share": args.max_single_mint_pnl_share,
        "min_realized_pnl_sol": args.min_realized_pnl_sol,
        "min_invested_sol": args.min_invested_sol,
        "cap": args.cap,
    }
    for key, val in mapping.items():
        if val is not None:
            kw[key] = val
    return Thresholds(**kw)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input:
        print("wallet_leaderboard: pass --input", file=sys.stderr)
        return 2
    paths = resolve_inputs(args.input)
    profiles: list[Thresholds]
    if args.profile == "strict":
        profiles = [_apply_overrides(STRICT, args)]
    elif args.profile == "noisy_v0":
        profiles = [_apply_overrides(NOISY_V0, args)]
    else:
        profiles = [_apply_overrides(STRICT, args), _apply_overrides(NOISY_V0, args)]
    creates = Path(args.creates) if args.creates else None
    result = run(paths, creates_path=creates, thresholds=tuple(profiles))
    out = Path(args.output_dir)
    write_outputs(result, out)
    sizes = result["board_sizes"]
    print(
        json.dumps(
            {
                "output_dir": str(out),
                "trades_n": result["trades_n"],
                "wallets_n": result["wallets_n"],
                "mints_n": result["mints_n"],
                "window_days": result["window_days"],
                "noisy_short_window": result["noisy_short_window"],
                "board_sizes": sizes,
                "follower_wave_summary": result["follower_wave_summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
