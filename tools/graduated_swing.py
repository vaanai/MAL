#!/usr/bin/env python3
"""Post-migration PumpSwap swing book. Paper only.

Decision clocks are migration + 1/5/15/30/60 minutes, plus genuine attention
first-seen on tokens that have already graduated. Fills use the tape
simulator (own impact, misses kept, flat 15% and pressure fail models).
Promotion is tools.laya_v0.book_stats.

Backfill rows (source=backfill, null t_recv_ms) get a synthetic receive
time: block_time plus a draw from the live tape's chain→receive lags.
Block time alone is not a receive time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from observe.attention import is_genuine_arrival, load_poller_start_ms, load_snapshot_keys
from tools.funding_graph import FundingGraph, empty_funding_features, fill_funding_features
from tools.laya_v0 import (
    ENTRY_LATENCY_MS,
    LATENCY_DRAW_SEED,
    NAN,
    PROMOTION_DROP_N,
    PROMOTION_MIN_DAYS,
    PROMOTION_MIN_N,
    PROMOTION_RULE,
    SNIPER_SLOT_DELTA,
    BookTrade,
    DecisionRow,
    LadderRule,
    MintBook,
    book_stats,
    causal_take_indices,
    chain_lag_ms,
    fit_booster,
    flow_from_row,
    vector,
    walk_forward,
)
from tools.laya_v0 import choose_rule as _choose_rule
from tools.paper_curve_math import (
    INITIAL_VIRTUAL_SOL_LAMPORTS,
    INITIAL_VIRTUAL_TOKEN_UI,
    LAMPORTS_PER_SOL,
    PORTAL_FEE_PPM,
    PRIORITY_FEE_LAMPORTS,
    TOKEN_SCALE,
    TOKEN_UI_OFFSET,
    market_cap_sol,
    pumpswap_sol_fee_ppm,
    pumpswap_sol_fee_split,
    quote_buy,
    quote_sell,
)
from tools.paper_fail_pressure import (
    HEADLINE_SCALE,
    Attempt,
    FailCurve,
    Pressure,
    fit_curve,
    headline_pnl,
    pressure_from_prints,
)
from tools.paper_price_path import CreateSignal, open_text
from tools.paper_tape_scoreboard import (
    DEFAULT_FAIL_RATE,
    DEFAULT_SLIPPAGE_CAP,
    MISS_STATUSES,
    ExitRule,
    simulate_exit,
    try_entry,
)
from tools.laya_v0 import simulate_ladder

SCHEMA = "graduated_swing_v1"
FEE_PAGE = "https://pump.fun/docs/fees"
FEE_PAGE_UPDATED = "2026-05-20"
FEE_REREAD = "2026-09-25"
WINDOW_START_ISO = "2026-09-25T07:00:00Z"
WINDOW_START_MS = 1_790_319_600_000  # 2026-09-25 07:00:00 UTC. 1790319600000.
# Live chain→receive reservoir. Sequential draws match sample_entry_latencies.
LAG_RESERVOIR = 100_000
# Sizes the fee table is quoted at. The scored book is 0.05 SOL (the live cap).
# 0.5 SOL is a fee-economics hold book, not a second model.
SIZE_LAMPORTS = 50_000_000
SIZE_LAMPORTS_LARGE = 500_000_000
MAX_HOLD_MS = 4 * 60 * 60 * 1000
DECISION_OFFSETS_MS: tuple[tuple[int, str], ...] = (
    (60_000, "mig_1"),
    (300_000, "mig_5"),
    (900_000, "mig_15"),
    (1_800_000, "mig_30"),
    (3_600_000, "mig_60"),
)
NEAR_MISS_MIN_N = 14

# Graduation virtual quote: k / remaining virtual tokens. Market cap is that
# quote over the remaining UI supply, about 411 SOL, still inside 0–420.
_GRAD_VIRTUAL_QUOTE = INITIAL_VIRTUAL_SOL_LAMPORTS * INITIAL_VIRTUAL_TOKEN_UI // TOKEN_UI_OFFSET
GRADUATION_MCAP_SOL = _GRAD_VIRTUAL_QUOTE / TOKEN_UI_OFFSET
GRADUATION_REAL_SOL_LAMPORTS = _GRAD_VIRTUAL_QUOTE - INITIAL_VIRTUAL_SOL_LAMPORTS
# Pool seeded with the real SOL at the curve's completion price.
_GRAD_BASE_RAW = GRADUATION_REAL_SOL_LAMPORTS * (TOKEN_UI_OFFSET * TOKEN_SCALE) // _GRAD_VIRTUAL_QUOTE

SWING_EXITS: tuple[ExitRule, ...] = (
    ExitRule("hold_5m", "hold", hold_ms=300_000, max_hold_ms=MAX_HOLD_MS),
    ExitRule("hold_15m", "hold", hold_ms=900_000, max_hold_ms=MAX_HOLD_MS),
    ExitRule("hold_30m", "hold", hold_ms=1_800_000, max_hold_ms=MAX_HOLD_MS),
    ExitRule("hold_60m", "hold", hold_ms=3_600_000, max_hold_ms=MAX_HOLD_MS),
    ExitRule("hold_120m", "hold", hold_ms=7_200_000, max_hold_ms=MAX_HOLD_MS),
    ExitRule("hold_240m", "hold", hold_ms=14_400_000, max_hold_ms=MAX_HOLD_MS),
    ExitRule("tp50_sl30", "tpsl", tp=0.50, sl=0.30, max_hold_ms=MAX_HOLD_MS),
    ExitRule("tp100_sl50", "tpsl", tp=1.00, sl=0.50, max_hold_ms=MAX_HOLD_MS),
    ExitRule("tp200_sl50", "tpsl", tp=2.00, sl=0.50, max_hold_ms=MAX_HOLD_MS),
    ExitRule("trail30", "trail", trail=0.30, max_hold_ms=MAX_HOLD_MS),
    ExitRule("trail50", "trail", trail=0.50, max_hold_ms=MAX_HOLD_MS),
)
SWING_LADDERS: tuple[LadderRule, ...] = (
    LadderRule("ladder_2x_t240", scale_ret=1.00, scale_frac=0.5, trail=0.30, hard_sl=0.30, max_hold_ms=MAX_HOLD_MS),
    LadderRule("ladder_1_5x_t240", scale_ret=0.50, scale_frac=0.5, trail=0.25, hard_sl=0.25, max_hold_ms=MAX_HOLD_MS),
)
ALL_RULE_IDS: tuple[str, ...] = tuple(rule.rule_id for rule in SWING_EXITS) + tuple(
    rule.rule_id for rule in SWING_LADDERS
)

SWING_FEATURES: tuple[str, ...] = (
    "f_pm_n_buy",
    "f_pm_n_sell",
    "f_pm_buy_sol",
    "f_pm_sell_sol",
    "f_pm_net_sol",
    "f_pm_unique_buyers",
    "f_pm_unique_sellers",
    "f_pm_price_vs_mig",
    "f_minutes_since_mig",
    "f_quote_sol",
    "f_mcap_sol",
    "f_venue_fee_ppm",
    "f_creator_sell_sol",
    "f_creator_sell_n",
    "f_creator_sell_share",
    "f_pm_creator_sell_sol",
    "f_top1_holder_share",
    "f_top5_holder_share",
    "f_sniper_buy_sol_share",
    "f_attn_n",
    "f_attn_dex",
    "f_attn_pump",
    "f_attn_gecko",
    "f_attn_min_rank",
    "f_attn_genuine",
    "f_is_attention",
    "f_funder_known",
    "f_funder_cluster_hash",
    "f_funder_prior_creates",
    "f_funder_prior_scored",
    "f_funder_prior_rug_frac",
    "f_funder_prior_median_peak",
    "f_creator_funded_by_exchange",
    "f_creator_fresh_wallet",
    "f_creator_wallet_age_s",
    "f_same_funder_early_n",
    "f_funder_creator_buyer_loop",
)


def _iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def _sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


def fee_schedule_config() -> dict[str, Any]:
    """Verified PumpSwap economics the simulator charges. Not a fitted number."""
    creator, protocol, lp = pumpswap_sol_fee_split(GRADUATION_MCAP_SOL)
    return {
        "source": FEE_PAGE,
        "page_updated": FEE_PAGE_UPDATED,
        "reread": FEE_REREAD,
        "canonical": (
            "Migrated pump.fun pools are canonical. Fee is creator + protocol + LP "
            "by SOL market cap. LP stays in the pool. Protocol and creator do not."
        ),
        "non_canonical_not_used": "Flat 0.30% (creator 0, protocol 0.05%, LP 0.25%) is not applied.",
        "graduation_mcap_sol": GRADUATION_MCAP_SOL,
        "graduation_tier": "0-420 SOL",
        "graduation_creator_ppm": creator,
        "graduation_protocol_ppm": protocol,
        "graduation_lp_ppm": lp,
        "graduation_total_ppm": pumpswap_sol_fee_ppm(GRADUATION_MCAP_SOL),
        "bonding_total_ppm": 12_500,
        "portal_local_ppm_per_side": PORTAL_FEE_PPM,
        "direct_portal_ppm": 0,
        "priority_lamports_per_side": PRIORITY_FEE_LAMPORTS,
        "planning_pool_quote_lamports": GRADUATION_REAL_SOL_LAMPORTS,
        "planning_pool_base_raw": _GRAD_BASE_RAW,
        "note": (
            "At graduation the canonical total is 1.25%, the same as the bonding curve. "
            "PumpPortal Local adds 0.5% per side. Direct instructions drop that 0.5%. "
            "The tier steps down only after 420 SOL of market cap."
        ),
    }


def immediate_round_trip(
    *,
    size_lamports: int,
    quote_lamports: int,
    base_raw: int,
    portal_fee_ppm: int,
) -> dict[str, Any] | None:
    """Buy then sell into the post-buy pool. Impact is in the quote; fees use the tier."""
    if size_lamports <= 0 or quote_lamports <= 0 or base_raw <= 0:
        return None
    mcap = market_cap_sol(quote_lamports, base_raw)
    buy = quote_buy(
        venue="pumpswap",
        size_lamports=size_lamports,
        quote_lamports=quote_lamports,
        base_raw=base_raw,
        market_cap=mcap,
        portal_fee_ppm=portal_fee_ppm,
    )
    if buy is None:
        return None
    out = quote_sell(
        venue="pumpswap",
        tokens_raw=buy.tokens_raw,
        quote_lamports=buy.quote_after,
        base_raw=buy.base_after,
        market_cap=market_cap_sol(buy.quote_after, buy.base_after),
        portal_fee_ppm=portal_fee_ppm,
    )
    if out is None:
        return None
    gross_loss = size_lamports - out
    with_priority = gross_loss + 2 * PRIORITY_FEE_LAMPORTS
    # Constant product: selling the tokens back into the post-buy pool returns the
    # net quote. Round-trip loss vs that book is fees. Entry impact vs the pre-trade
    # spot is net/quote, and that is the price impact of the size.
    entry_impact = buy.net_in_lamports / quote_lamports if quote_lamports else None
    return {
        "size_sol": _sol(size_lamports),
        "quote_sol": _sol(quote_lamports),
        "mcap_sol": mcap,
        "venue_fee_ppm": buy.venue_fee_ppm,
        "portal_fee_ppm": portal_fee_ppm,
        "sol_out": _sol(out),
        "entry_impact_frac": entry_impact,
        "loss_sol_fees_and_impact": _sol(gross_loss),
        "loss_frac_fees_and_impact": gross_loss / size_lamports,
        "loss_sol_plus_priority": _sol(with_priority),
        "loss_frac_plus_priority": with_priority / size_lamports,
    }


def fee_only_round_trip(*, size_lamports: int, mcap_sol: float, portal_fee_ppm: int) -> dict[str, Any] | None:
    """Same fees, reserves large enough that price impact is dust."""
    quote = 1_000_000 * LAMPORTS_PER_SOL
    # Keep the requested mcap: base_raw = quote / (mcap/1e9) / 1000, from spot*1e9 = mcap.
    if mcap_sol <= 0:
        return None
    base = int(quote / (mcap_sol / 1_000_000_000) / 1000)
    if base <= 0:
        return None
    row = immediate_round_trip(
        size_lamports=size_lamports,
        quote_lamports=quote,
        base_raw=base,
        portal_fee_ppm=portal_fee_ppm,
    )
    return row


def planning_fee_table() -> dict[str, Any]:
    """Round trip at the graduation pool and at infinite reserves, both routes, both sizes."""
    rows = []
    for size in (SIZE_LAMPORTS, SIZE_LAMPORTS_LARGE):
        for name, portal in (("portal_local", PORTAL_FEE_PPM), ("direct", 0)):
            fee_only = fee_only_round_trip(
                size_lamports=size,
                mcap_sol=GRADUATION_MCAP_SOL,
                portal_fee_ppm=portal,
            )
            impacted = immediate_round_trip(
                size_lamports=size,
                quote_lamports=GRADUATION_REAL_SOL_LAMPORTS,
                base_raw=_GRAD_BASE_RAW,
                portal_fee_ppm=portal,
            )
            rows.append({"route": name, "fee_only": fee_only, "graduation_pool": impacted})
    return {"config": fee_schedule_config(), "round_trips": rows}


def synthetic_recv_ms(block_time_s: int, lag_ms: int) -> int:
    """block_time is unix seconds. The lag is a live chain→receive draw, in ms."""
    return int(block_time_s) * 1000 + int(lag_ms)


class _LagReservoir:
    def __init__(self, cap: int, seed: int) -> None:
        self.cap = cap
        self.rng = random.Random(seed)
        self.n = 0
        self.data: list[int] = []

    def add(self, lag_ms: int) -> None:
        self.n += 1
        if len(self.data) < self.cap:
            self.data.append(int(lag_ms))
            return
        j = self.rng.randrange(self.n)
        if j < self.cap:
            self.data[j] = int(lag_ms)

    def draw(self, rng: random.Random) -> int:
        if not self.data:
            return ENTRY_LATENCY_MS
        return int(self.data[rng.randrange(len(self.data))])


def _block_time_s(row: dict[str, Any]) -> int | None:
    for key in ("block_time", "event_ts"):
        raw = row.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        whole = int(raw)
        if whole >= 1_000_000_000_000:
            whole //= 1000
        if whole >= 1_000_000_000:
            return whole
    return None


def _is_backfill(row: dict[str, Any]) -> bool:
    return row.get("source") == "backfill"


def _dedupe_key(row: dict[str, Any]) -> int | None:
    """Stable 64-bit key. Live and backfill copies of one signature collapse."""
    try:
        event_index = int(row.get("event_index") or 0)
    except (TypeError, ValueError):
        event_index = 0
    sig = row.get("signature")
    if isinstance(sig, str) and sig and sig != "UNK":
        raw = f"{sig}|{event_index}".encode()
    else:
        mint = row.get("mint")
        if not isinstance(mint, str):
            return None
        raw = "|".join(
            (
                mint,
                str(row.get("slot")),
                str(event_index),
                str(row.get("trader")),
                str(row.get("side")),
                str(row.get("sol_lamports")),
                str(row.get("venue")),
            )
        ).encode()
    return int.from_bytes(hashlib.blake2s(raw, digest_size=8).digest(), "little")


def _iter_jsonl(paths: Iterable[Path]) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in paths:
        with open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield path, row


def _trade_paths(directory: Path) -> list[Path]:
    found: list[Path] = []
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        name = path.name
        if not name.startswith("trades-"):
            continue
        if not path.is_file():
            continue
        if name.endswith(".jsonl") or name.endswith(".jsonl.zst") or name.endswith(".jsonl.gz"):
            found.append(path)
    return sorted(found)


def _stamp_backfill_recv(row: dict[str, Any], reservoir: _LagReservoir, rng: random.Random) -> dict[str, Any] | None:
    """Copy with t_recv_ms = block_time + sampled live lag. None if the row cannot be placed."""
    if not _is_backfill(row):
        return row
    block = _block_time_s(row)
    if block is None:
        return None
    stamped = dict(row)
    stamped["t_recv_ms"] = synthetic_recv_ms(block, reservoir.draw(rng))
    stamped["recv_synthetic"] = True
    return stamped


@dataclass
class ScanStats:
    lines: int = 0
    live_lines: int = 0
    backfill_lines: int = 0
    kept: int = 0
    dupes: int = 0
    bad_backfill_clock: int = 0
    lags_seen: int = 0
    graduated: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "live_lines": self.live_lines,
            "backfill_lines": self.backfill_lines,
            "kept": self.kept,
            "dupes": self.dupes,
            "bad_backfill_clock": self.bad_backfill_clock,
            "lags_seen": self.lags_seen,
            "graduated": self.graduated,
        }


def _pumpswap_wsol(row: dict[str, Any]) -> bool:
    return row.get("venue") == "pumpswap" and row.get("quote_is_wsol") is True and isinstance(row.get("mint"), str)


def load_graduated_books(
    live_paths: Sequence[Path],
    backfill_paths: Sequence[Path],
    creates: dict[str, CreateSignal],
    *,
    window_start_ms: int,
) -> tuple[dict[str, MintBook], dict[str, int], ScanStats, _LagReservoir]:
    """Two passes. Live receive times win on a duplicate signature.

    Migration time is the first PumpSwap wSOL print with synthetic-or-live
    receive time at or after the window. Prints before that still enter the
    book when their receive time is at or before a later decision.
    """
    stats = ScanStats()
    reservoir = _LagReservoir(LAG_RESERVOIR, LATENCY_DRAW_SEED)
    first_bond: dict[str, int] = {}
    first_swap: dict[str, int] = {}

    def _note_migration(row: dict[str, Any]) -> None:
        """Remember the earliest curve print and the earliest PumpSwap print.

        A later PumpSwap trade on a pool that was already migrated before this
        tape is not a migration. The clock is the first PumpSwap print, and
        only when a bonding print is strictly earlier.
        """
        mint = row.get("mint")
        t_raw = row.get("t_recv_ms")
        if not isinstance(mint, str) or not mint or not isinstance(t_raw, int):
            return
        if row.get("quote_is_wsol") is False:
            return
        venue = row.get("venue")
        if venue == "pump_bonding":
            prev = first_bond.get(mint)
            if prev is None or t_raw < prev:
                first_bond[mint] = t_raw
            return
        if not _pumpswap_wsol(row):
            return
        prev = first_swap.get(mint)
        if prev is None or t_raw < prev:
            first_swap[mint] = t_raw

    def _scan_lags_and_migrations(paths: Sequence[Path], *, backfill: bool, rng: random.Random) -> None:
        for _path, row in _iter_jsonl(paths):
            stats.lines += 1
            if stats.lines % 500_000 == 0:
                print(
                    f"pass1 lines={stats.lines} swaps={len(first_swap)} bonds={len(first_bond)} lags={reservoir.n}",
                    file=sys.stderr,
                )
            if backfill:
                stats.backfill_lines += 1
                stamped = _stamp_backfill_recv(row, reservoir, rng)
                if stamped is None:
                    stats.bad_backfill_clock += 1
                    continue
                _note_migration(stamped)
                continue
            stats.live_lines += 1
            lag = chain_lag_ms(row)
            if lag is not None:
                reservoir.add(lag)
                stats.lags_seen += 1
            _note_migration(row)

    # Lags come from the live tape only. Backfill is stamped on the second walk
    # of pass 1 so the reservoir already holds live draws.
    _scan_lags_and_migrations(live_paths, backfill=False, rng=random.Random(0))
    _scan_lags_and_migrations(backfill_paths, backfill=True, rng=random.Random(LATENCY_DRAW_SEED))
    migration: dict[str, int] = {}
    for mint, swap_t in first_swap.items():
        bond_t = first_bond.get(mint)
        if bond_t is None or bond_t >= swap_t or swap_t < window_start_ms:
            continue
        migration[mint] = swap_t
    stats.graduated = len(migration)
    print(
        f"graduated_mints={stats.graduated} swap_mints={len(first_swap)} "
        f"lag_reservoir={len(reservoir.data)}",
        file=sys.stderr,
    )

    buckets: dict[str, list[Any]] = {mint: [] for mint in migration}
    seen: set[int] = set()
    draw = random.Random(LATENCY_DRAW_SEED)

    def _keep(row: dict[str, Any]) -> None:
        mint = row.get("mint")
        if mint not in buckets:
            return
        parsed = flow_from_row(row)
        if parsed is None:
            return
        key = _dedupe_key(row)
        if key is not None:
            if key in seen:
                stats.dupes += 1
                return
            seen.add(key)
        _mint, pr = parsed
        buckets[mint].append(pr)
        stats.kept += 1

    def _scan_keep(paths: Sequence[Path], *, backfill: bool) -> None:
        n = 0
        for _path, row in _iter_jsonl(paths):
            n += 1
            if n % 500_000 == 0:
                print(f"pass2 lines={n} kept={stats.kept}", file=sys.stderr)
            if backfill:
                stamped = _stamp_backfill_recv(row, reservoir, draw)
                if stamped is None:
                    continue
                _keep(stamped)
            else:
                _keep(row)

    _scan_keep(live_paths, backfill=False)
    _scan_keep(backfill_paths, backfill=True)

    books: dict[str, MintBook] = {}
    for mint, mig_t in migration.items():
        create = creates.get(mint)
        if create is None:
            create = CreateSignal(
                mint=mint,
                t_signal_ms=mig_t,
                creator=None,
                signature=None,
                v_sol=None,
                v_token_ui=None,
                mcap_sol=None,
                initial_buy_ui=None,
                sol_amount=None,
            )
        flow = buckets[mint]
        flow.sort(key=lambda p: (p.t_recv_ms, p.slot, p.event_index, p.trader or "", p.side))
        deduped = []
        prev = None
        for pr in flow:
            key = (pr.t_recv_ms, pr.slot, pr.event_index, pr.venue, pr.trader, pr.side, pr.sol_lamports, pr.token_raw)
            if key == prev:
                continue
            prev = key
            deduped.append(pr)
        books[mint] = MintBook(create=create, flow=deduped)
    # Migration clock is the first kept PumpSwap print. Live receive time wins
    # a duplicate, so this can be later than a backfill stamp that was dropped.
    kept_migration: dict[str, int] = {}
    for mint, book in books.items():
        for pr in book.flow:
            if pr.venue == "pumpswap" and pr.t_recv_ms >= window_start_ms:
                kept_migration[mint] = pr.t_recv_ms
                break
    books = {mint: books[mint] for mint in kept_migration}
    stats.graduated = len(books)
    return books, kept_migration, stats, reservoir


def create_from_backfill_row(row: dict[str, Any], *, lag_ms: int) -> CreateSignal | None:
    if row.get("type") != "create":
        return None
    mint = row.get("mint")
    if not isinstance(mint, str) or not mint:
        return None
    block = _block_time_s(row)
    if block is None:
        return None
    creator = row.get("creator") or row.get("trader")
    if not isinstance(creator, str) or not creator:
        creator = None
    quote = row.get("quote_reserve")
    base = row.get("base_reserve")
    v_sol = None
    v_token = None
    try:
        if quote is not None:
            v_sol = int(quote) / LAMPORTS_PER_SOL
        if base is not None:
            v_token = int(base) / TOKEN_SCALE
    except (TypeError, ValueError):
        v_sol = None
        v_token = None
    return CreateSignal(
        mint=mint,
        t_signal_ms=synthetic_recv_ms(block, lag_ms),
        creator=creator,
        signature=row.get("signature") if isinstance(row.get("signature"), str) else None,
        v_sol=v_sol,
        v_token_ui=v_token,
        mcap_sol=(v_sol / v_token) * 1_000_000_000 if v_sol and v_token else None,
        initial_buy_ui=None,
        sol_amount=None,
    )


def load_create_map(
    observe_paths: Sequence[Path],
    backfill_create_paths: Sequence[Path],
    *,
    lag_ms: int,
) -> dict[str, CreateSignal]:
    """Observe creates keep their real t_ws. Backfill creates use block_time + lag."""
    from tools.paper_price_path import create_from_observe_row, load_creates

    found = load_creates(observe_paths)
    for _path, row in _iter_jsonl(backfill_create_paths):
        create = create_from_backfill_row(row, lag_ms=lag_ms)
        if create is None:
            parsed = create_from_observe_row(row)
            create = parsed
        if create is None:
            continue
        prev = found.get(create.mint)
        if prev is None:
            found[create.mint] = create
    return found


@dataclass
class AttentionEvent:
    mint: str
    kind: str
    t_ms: int
    rank: int | None
    genuine: bool


def load_attention(directory: Path) -> list[AttentionEvent]:
    if not directory.is_dir():
        return []
    t_start = load_poller_start_ms(directory) or 0
    keys = load_snapshot_keys(directory)
    paths = sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.name.startswith("attention-") and (p.name.endswith(".jsonl") or p.name.endswith(".jsonl.zst"))
    )
    best: dict[tuple[str, str], AttentionEvent] = {}
    for _path, row in _iter_jsonl(paths):
        mint = row.get("mint")
        kind = row.get("kind")
        t_ms = row.get("t_first_ms")
        if not isinstance(mint, str) or not isinstance(kind, str) or not isinstance(t_ms, int):
            continue
        genuine = is_genuine_arrival(row, t_start_ms=t_start, snapshot_keys=keys)
        rank = row.get("rank")
        rank_i = int(rank) if isinstance(rank, int) else None
        ev = AttentionEvent(mint=mint, kind=kind, t_ms=t_ms, rank=rank_i, genuine=genuine)
        prev = best.get((kind, mint))
        if prev is None or ev.t_ms < prev.t_ms:
            best[(kind, mint)] = ev
    return sorted(best.values(), key=lambda ev: (ev.t_ms, ev.mint, ev.kind))


def _attn_flags(events: Sequence[AttentionEvent], t_ms: int) -> dict[str, float]:
    seen = [ev for ev in events if ev.t_ms <= t_ms]
    dex = pump = gecko = 0
    genuine = 0
    ranks: list[int] = []
    for ev in seen:
        if ev.kind.startswith("dex_"):
            dex += 1
        elif ev.kind.startswith("gecko"):
            gecko += 1
        elif ev.kind.startswith("pump_"):
            pump += 1
        if ev.genuine:
            genuine += 1
        if ev.rank is not None:
            ranks.append(ev.rank)
    return {
        "f_attn_n": float(len(seen)),
        "f_attn_dex": float(dex),
        "f_attn_pump": float(pump),
        "f_attn_gecko": float(gecko),
        "f_attn_min_rank": float(min(ranks)) if ranks else NAN,
        "f_attn_genuine": float(genuine),
    }


def features_at(
    book: MintBook,
    *,
    decision_t_ms: int,
    migration_t_ms: int,
    migration_price: float,
    attention: Sequence[AttentionEvent],
    is_attention: bool,
) -> dict[str, float]:
    """Prints and attention with time <= decision. Nothing later."""
    pm_buy = pm_sell = 0
    pm_buy_sol = pm_sell_sol = 0
    pm_buyers: set[str] = set()
    pm_sellers: set[str] = set()
    bal: dict[str, int] = defaultdict(int)
    buy_sol = 0
    sniper_sol = 0
    first_slot: int | None = None
    creator = book.create.creator
    creator_sell_sol = 0
    creator_sell_n = 0
    pm_creator_sell = 0
    last_price = NAN
    last_quote = 0
    last_base = 0
    for pr in book.flow:
        if pr.t_recv_ms > decision_t_ms:
            break
        if first_slot is None:
            first_slot = pr.slot
        if pr.side == "sell":
            if pr.trader:
                bal[pr.trader] -= pr.token_raw
            if creator and pr.trader == creator:
                creator_sell_n += 1
                creator_sell_sol += pr.sol_lamports
                if pr.t_recv_ms > migration_t_ms:
                    pm_creator_sell += pr.sol_lamports
        else:
            buy_sol += pr.sol_lamports
            if first_slot is not None and pr.slot <= first_slot + SNIPER_SLOT_DELTA:
                sniper_sol += pr.sol_lamports
            if pr.trader:
                bal[pr.trader] += pr.token_raw
        if pr.t_recv_ms > migration_t_ms:
            if pr.side == "sell":
                pm_sell += 1
                pm_sell_sol += pr.sol_lamports
                if pr.trader:
                    pm_sellers.add(pr.trader)
            else:
                pm_buy += 1
                pm_buy_sol += pr.sol_lamports
                if pr.trader:
                    pm_buyers.add(pr.trader)
        if pr.price_sol > 0:
            last_price = pr.price_sol
            last_quote = pr.quote_reserve
            last_base = pr.base_reserve
    positive = sorted((v for v in bal.values() if v > 0), reverse=True)
    if positive:
        total = float(sum(positive))
        top1 = positive[0] / total
        top5 = sum(positive[:5]) / total
    else:
        top1 = top5 = NAN
    mcap = market_cap_sol(last_quote, last_base) if last_quote > 0 and last_base > 0 else NAN
    price_vs = NAN
    if migration_price > 0 and isinstance(last_price, float) and last_price > 0:
        price_vs = last_price / migration_price - 1.0
    feats = {
        "f_pm_n_buy": float(pm_buy),
        "f_pm_n_sell": float(pm_sell),
        "f_pm_buy_sol": _sol(pm_buy_sol),
        "f_pm_sell_sol": _sol(pm_sell_sol),
        "f_pm_net_sol": _sol(pm_buy_sol - pm_sell_sol),
        "f_pm_unique_buyers": float(len(pm_buyers)),
        "f_pm_unique_sellers": float(len(pm_sellers)),
        "f_pm_price_vs_mig": price_vs,
        "f_minutes_since_mig": (decision_t_ms - migration_t_ms) / 60_000.0,
        "f_quote_sol": _sol(last_quote) if last_quote else NAN,
        "f_mcap_sol": mcap,
        "f_venue_fee_ppm": float(pumpswap_sol_fee_ppm(mcap)) if mcap == mcap else NAN,
        "f_creator_sell_sol": _sol(creator_sell_sol),
        "f_creator_sell_n": float(creator_sell_n),
        "f_creator_sell_share": (creator_sell_sol / buy_sol) if buy_sol > 0 else NAN,
        "f_pm_creator_sell_sol": _sol(pm_creator_sell),
        "f_top1_holder_share": top1,
        "f_top5_holder_share": top5,
        "f_sniper_buy_sol_share": (sniper_sol / buy_sol) if buy_sol > 0 else NAN,
        "f_price_sol": last_price,
        "f_is_attention": 1.0 if is_attention else 0.0,
    }
    feats.update(_attn_flags(attention, decision_t_ms))
    feats.update(empty_funding_features())
    return feats


def decision_points(
    book: MintBook,
    migration_t_ms: int,
    attention: Sequence[AttentionEvent],
    *,
    tape_end_ms: int,
) -> list[tuple[int, str, bool]]:
    """(t_ms, trigger, is_attention). Attention only after this mint has migrated."""
    out: list[tuple[int, str, bool]] = []
    for offset, name in DECISION_OFFSETS_MS:
        t_ms = migration_t_ms + offset
        if migration_t_ms <= t_ms <= tape_end_ms:
            out.append((t_ms, name, False))
    seen_kind: set[str] = set()
    for ev in attention:
        if ev.mint != book.create.mint or not ev.genuine:
            continue
        if ev.kind in seen_kind:
            continue
        if ev.t_ms < migration_t_ms or ev.t_ms > tape_end_ms:
            continue
        seen_kind.add(ev.kind)
        out.append((ev.t_ms, f"attn:{ev.kind}", True))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _migration_price(book: MintBook, migration_t_ms: int) -> tuple[float, int]:
    price = 0.0
    quote = 0
    for pr in book.flow:
        if pr.t_recv_ms > migration_t_ms:
            break
        if pr.venue == "pumpswap" and pr.t_recv_ms == migration_t_ms and pr.price_sol > 0:
            price = pr.price_sol
            quote = pr.quote_reserve
    if price <= 0:
        for pr in book.flow:
            if pr.t_recv_ms > migration_t_ms:
                break
            if pr.venue == "pumpswap" and pr.price_sol > 0:
                price = pr.price_sol
                quote = pr.quote_reserve
    return price, quote


def build_rows(
    books: dict[str, MintBook],
    migration: dict[str, int],
    attention: Sequence[AttentionEvent],
    *,
    tape_end_ms: int,
    graph: FundingGraph | None,
) -> tuple[list[DecisionRow], list[int]]:
    by_mint: dict[str, list[AttentionEvent]] = defaultdict(list)
    for ev in attention:
        by_mint[ev.mint].append(ev)
    rows: list[DecisionRow] = []
    mig_quotes: list[int] = []
    for mint, book in books.items():
        mig_t = migration[mint]
        price, quote = _migration_price(book, mig_t)
        if quote > 0:
            mig_quotes.append(quote)
        events = by_mint.get(mint, ())
        for t_ms, trigger, is_attn in decision_points(book, mig_t, events, tape_end_ms=tape_end_ms):
            feats = features_at(
                book,
                decision_t_ms=t_ms,
                migration_t_ms=mig_t,
                migration_price=price,
                attention=events,
                is_attention=is_attn,
            )
            fill_funding_features(book, t_ms, feats, None, graph, books)
            rows.append(
                DecisionRow(
                    mint=mint,
                    creator=book.create.creator,
                    create_t_ms=book.create.t_signal_ms,
                    decision_t_ms=t_ms,
                    trigger=trigger,
                    features=feats,
                )
            )
    rows.sort(key=lambda row: (row.decision_t_ms, row.mint, row.trigger))
    return rows, mig_quotes


def _ref_feats(row: DecisionRow) -> dict[str, Any]:
    price = row.features.get("f_price_sol")
    if price is None or (isinstance(price, float) and (math.isnan(price) or price <= 0)):
        return {"f_tape_last_price_sol": None}
    return {"f_tape_last_price_sol": float(price)}


def _entry_slot(book: MintBook, t_ms: int) -> int | None:
    slot = None
    for pr in book.flow:
        if pr.t_recv_ms > t_ms:
            break
        slot = pr.slot
    return slot


def label_rows(
    books: dict[str, MintBook],
    rows: list[DecisionRow],
    *,
    tape_end_ms: int,
    size_lamports: int,
    slippage_cap: float,
    latency_ms: int = ENTRY_LATENCY_MS,
) -> None:
    """Honest fills. A non-PumpSwap book is a miss: this lane does not buy the curve."""
    for index, row in enumerate(rows):
        if index and index % 2000 == 0:
            print(f"labeled={index}/{len(rows)}", file=sys.stderr)
        book = books[row.mint]
        t_entry = row.decision_t_ms + latency_ms
        row.entry_t_ms = t_entry
        entry = try_entry(
            book.path,
            t_entry_ms=t_entry,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            feats=_ref_feats(row),
        )
        if entry.status == "filled" and entry.venue != "pumpswap":
            entry.status = "missed_no_liquidity"
            entry.tokens_raw = 0
        row.entry_status = entry.status
        slot = _entry_slot(book, t_entry)
        row.pressure = pressure_from_prints(  # type: ignore[attr-defined]
            book.flow,
            t_entry_ms=t_entry,
            entry_slot=slot,
        )
        for rule in SWING_EXITS:
            part = simulate_exit(
                book.path,
                entry,
                rule,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            status = str(part["exit_status"])
            pnl = part.get("pnl_lamports")
            row.exit_status_by_rule[rule.rule_id] = status
            row.exit_t_by_rule[rule.rule_id] = part.get("exit_t_ms")
            if status in ("realized", "no_exit_liquidity", "not_entered") and isinstance(pnl, int):
                row.pnl_by_rule[rule.rule_id] = pnl
            else:
                row.pnl_by_rule[rule.rule_id] = None
        for ladder in SWING_LADDERS:
            part = simulate_ladder(
                book.path,
                entry,
                ladder,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            status = str(part["exit_status"])
            pnl = part.get("pnl_lamports")
            row.exit_status_by_rule[ladder.rule_id] = status
            row.exit_t_by_rule[ladder.rule_id] = part.get("exit_t_ms")
            if status in ("realized", "no_exit_liquidity") and isinstance(pnl, int):
                row.pnl_by_rule[ladder.rule_id] = pnl
            elif entry.status in MISS_STATUSES:
                row.pnl_by_rule[ladder.rule_id] = -PRIORITY_FEE_LAMPORTS
                row.exit_status_by_rule[ladder.rule_id] = "not_entered"
            else:
                row.pnl_by_rule[ladder.rule_id] = None


def flat_mix(pnl: int, entry_status: str, fail_rate: float = DEFAULT_FAIL_RATE) -> int:
    """Misses already cost the priority fee. Sends replace that fraction with a priority burn."""
    if entry_status in MISS_STATUSES or fail_rate <= 0:
        return int(pnl)
    mixed = (1.0 - fail_rate) * float(pnl) + fail_rate * float(-PRIORITY_FEE_LAMPORTS)
    return int(round(mixed))


def _point(row: DecisionRow) -> str:
    if row.trigger.startswith("attn:"):
        return "attn"
    return row.trigger


def _trades(rows: Sequence[DecisionRow], rule_id: str, *, mode: str, curve: FailCurve | None) -> list[BookTrade]:
    out: list[BookTrade] = []
    for row in rows:
        raw = row.pnl_by_rule.get(rule_id)
        if raw is None:
            continue
        if mode == "raw":
            pnl = int(raw)
        elif mode == "flat":
            pnl = flat_mix(int(raw), row.entry_status)
        elif mode == "pressure":
            if curve is None:
                continue
            attempt = Attempt(
                entry_status=row.entry_status,
                exit_status=row.exit_status_by_rule.get(rule_id, ""),
                pnl_lamports=int(raw),
                pressure=getattr(row, "pressure", Pressure(0, 0)),
            )
            mixed = headline_pnl(attempt, curve)
            if mixed is None:
                continue
            pnl = mixed
        else:
            raise ValueError(mode)
        out.append(BookTrade(row.mint, row.decision_t_ms, pnl))
    return out


def _blockers(summary: dict[str, Any]) -> list[str]:
    blocked = []
    if int(summary.get("n") or 0) < PROMOTION_MIN_N:
        blocked.append("n")
    if int(summary.get("n_days") or 0) < PROMOTION_MIN_DAYS:
        blocked.append("days")
    if not summary.get("majority_days_positive"):
        blocked.append("majority_days")
    ci = summary.get("mean_ci90_sol")
    if not isinstance(ci, list) or len(ci) < 1 or not isinstance(ci[0], (int, float)) or ci[0] <= 0:
        blocked.append("mean_ci90")
    tail = summary.get("total_ex_top3_sol")
    if not isinstance(tail, (int, float)) or tail <= 0:
        blocked.append("drop_top3")
    return blocked


def _brief(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": summary.get("n"),
        "n_days": summary.get("n_days"),
        "days_positive": summary.get("days_positive"),
        "median_sol": summary.get("median_sol"),
        "mean_sol": summary.get("mean_sol"),
        "mean_ci90_sol": summary.get("mean_ci90_sol"),
        "win_rate": summary.get("win_rate"),
        "total_sol": summary.get("total_sol"),
        "total_ex_top3_sol": summary.get("total_ex_top3_sol"),
        "promote": summary.get("promote"),
        "blockers": _blockers(summary),
    }


def buy_all_table(rows: Sequence[DecisionRow], *, curve: FailCurve | None) -> list[dict[str, Any]]:
    points = sorted({_point(row) for row in rows})
    table = []
    for point in points:
        subset = [row for row in rows if _point(row) == point]
        for rule_id in ALL_RULE_IDS:
            raw = book_stats(_trades(subset, rule_id, mode="raw", curve=None))
            flat = book_stats(_trades(subset, rule_id, mode="flat", curve=None))
            pressure = book_stats(_trades(subset, rule_id, mode="pressure", curve=curve)) if curve else None
            table.append(
                {
                    "point": point,
                    "rule": rule_id,
                    "raw": _brief(raw),
                    "flat_15": _brief(flat),
                    "pressure": None if pressure is None else _brief(pressure),
                }
            )
    return table


def _fit_pressure(rows: Sequence[DecisionRow]) -> FailCurve | None:
    pressures: list[Pressure] = []
    for row in rows:
        if row.entry_status != "filled":
            continue
        if row.trigger != "mig_1":
            continue
        status = row.exit_status_by_rule.get("hold_30m")
        if status not in ("realized", "no_exit_liquidity"):
            continue
        pr = getattr(row, "pressure", None)
        if isinstance(pr, Pressure):
            pressures.append(pr)
    if len(pressures) < 8:
        return None
    return fit_curve(pressures, scale=HEADLINE_SCALE)


def evaluate_model(
    rows: list[DecisionRow],
    *,
    n_folds: int,
    curve: FailCurve | None,
    backend: str | None,
) -> dict[str, Any]:
    """Walk-forward LightGBM (or the LAYA fallback). Top-k is RankWindow, causal."""
    folds = walk_forward([row.decision_t_ms for row in rows], n_folds=n_folds)
    rules = tuple(SWING_EXITS) + tuple(SWING_LADDERS)
    oos: list[dict[str, Any]] = []
    fold_meta = []
    for fold_i, (train_idx, test_idx) in enumerate(folds):
        train_rows = [rows[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]
        # Train on the flat 15% book so the label matches the headline mix.
        shadowed = []
        for row in train_rows:
            copy = DecisionRow(
                mint=row.mint,
                creator=row.creator,
                create_t_ms=row.create_t_ms,
                decision_t_ms=row.decision_t_ms,
                trigger=row.trigger,
                features=row.features,
                pnl_by_rule={
                    rule_id: None
                    if row.pnl_by_rule.get(rule_id) is None
                    else flat_mix(int(row.pnl_by_rule[rule_id]), row.entry_status)
                    for rule_id in ALL_RULE_IDS
                },
            )
            shadowed.append(copy)
        rule_id = _choose_rule(shadowed, rules=rules)
        xs: list[list[float]] = []
        ys: list[int] = []
        for row in shadowed:
            pnl = row.pnl_by_rule.get(rule_id)
            if pnl is None:
                continue
            xs.append(vector(row.features, SWING_FEATURES))
            ys.append(1 if pnl > 0 else 0)
        model = fit_booster(xs, ys, SWING_FEATURES, backend=backend)
        meta: dict[str, Any] = {
            "fold": fold_i,
            "rule_id": rule_id,
            "train_n": len(train_idx),
            "test_n": len(test_idx),
            "train_labeled": len(ys),
            "model": None if model is None else model.backend,
        }
        if model is None:
            meta["skipped"] = "one_class_or_too_small"
            fold_meta.append(meta)
            continue
        test_x = []
        test_keep: list[DecisionRow] = []
        for row in test_rows:
            if row.pnl_by_rule.get(rule_id) is None:
                continue
            test_x.append(vector(row.features, SWING_FEATURES))
            test_keep.append(row)
        probs = model.predict(test_x) if test_x else []
        for row, prob in zip(test_keep, probs):
            raw = row.pnl_by_rule[rule_id]
            assert raw is not None
            oos.append(
                {
                    "fold": fold_i,
                    "point": _point(row),
                    "trigger": row.trigger,
                    "score": float(prob),
                    "pnl_raw": int(raw),
                    "pnl_flat": flat_mix(int(raw), row.entry_status),
                    "rule_id": rule_id,
                    "mint": row.mint,
                    "decision_t_ms": row.decision_t_ms,
                    "pressure": getattr(row, "pressure", Pressure(0, 0)),
                    "entry_status": row.entry_status,
                    "exit_status": row.exit_status_by_rule.get(rule_id, ""),
                }
            )
        fold_meta.append(meta)
    return {"folds": fold_meta, "oos": oos, "curve": curve}


def _oos_books(oos: Sequence[dict[str, Any]], *, curve: FailCurve | None) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    points = sorted({row["point"] for row in oos})
    fractions = (0.01, 0.05, 0.10, 0.20, 1.0)
    by_fold_point: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in oos:
        by_fold_point[(int(row["fold"]), str(row["point"]))].append(row)
    table = []
    for point in points:
        for frac in fractions:
            bag_raw: list[BookTrade] = []
            bag_flat: list[BookTrade] = []
            bag_p: list[BookTrade] = []
            for (fold, grp_point), rows in by_fold_point.items():
                if grp_point != point:
                    continue
                ordered = sorted(rows, key=lambda r: (r["decision_t_ms"], r["mint"]))
                chosen = causal_take_indices([float(r["score"]) for r in ordered], frac)
                for i in chosen:
                    row = ordered[i]
                    bag_raw.append(BookTrade(row["mint"], row["decision_t_ms"], int(row["pnl_raw"])))
                    bag_flat.append(BookTrade(row["mint"], row["decision_t_ms"], int(row["pnl_flat"])))
                    if curve is not None:
                        attempt = Attempt(
                            entry_status=str(row["entry_status"]),
                            exit_status=str(row["exit_status"]),
                            pnl_lamports=int(row["pnl_raw"]),
                            pressure=row["pressure"],
                        )
                        mixed = headline_pnl(attempt, curve)
                        if mixed is not None:
                            bag_p.append(BookTrade(row["mint"], row["decision_t_ms"], mixed))
            grouped[(point, frac)] = []
            table.append(
                {
                    "point": point,
                    "fraction": frac,
                    "raw": _brief(book_stats(bag_raw)),
                    "flat_15": _brief(book_stats(bag_flat)),
                    "pressure": _brief(book_stats(bag_p)) if curve is not None else None,
                }
            )
    return table


def nearest_misses(books: Sequence[dict[str, Any]], *, key: str = "flat_15") -> list[dict[str, Any]]:
    """Highest mean among books with n >= 14 that do not promote. One UTC day cannot clear days."""
    ranked = []
    for row in books:
        summary = row.get(key) or {}
        n = int(summary.get("n") or 0)
        mean = summary.get("mean_sol")
        if n < NEAR_MISS_MIN_N or not isinstance(mean, (int, float)):
            continue
        if summary.get("promote"):
            continue
        ranked.append((float(mean), n, row))
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    out = []
    for mean, n, row in ranked[:8]:
        summary = row.get(key) or {}
        out.append(
            {
                "point": row.get("point"),
                "rule": row.get("rule") or row.get("fraction"),
                "n": n,
                "mean_sol": mean,
                "median_sol": summary.get("median_sol"),
                "mean_ci90_sol": summary.get("mean_ci90_sol"),
                "total_sol": summary.get("total_sol"),
                "total_ex_top3_sol": summary.get("total_ex_top3_sol"),
                "win_rate": summary.get("win_rate"),
                "blockers": summary.get("blockers"),
            }
        )
    return out


def measured_impact(quotes: Sequence[int]) -> dict[str, Any]:
    if not quotes:
        return {"n": 0}
    ordered = sorted(quotes)
    def pct(p: float) -> int:
        if len(ordered) == 1:
            return ordered[0]
        k = (len(ordered) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(ordered) - 1)
        w = k - lo
        return int(round(ordered[lo] * (1.0 - w) + ordered[hi] * w))
    levels = {"p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90)}
    table = []
    for name, quote in levels.items():
        # Base from the graduation price so the tier is the pool's own mcap only if we
        # had base. Quote alone does not set mcap. Use a base that prices the pool at
        # the graduation mcap when the quote is the planning size, and scale base with
        # quote so spot (and therefore mcap) stays at graduation. Deeper SOL at the
        # same price is the typical "more liquidity" case. Impact then scales with size/quote.
        base = max(1, _GRAD_BASE_RAW * quote // max(1, GRADUATION_REAL_SOL_LAMPORTS))
        for size in (SIZE_LAMPORTS, SIZE_LAMPORTS_LARGE):
            for route, portal in (("portal_local", PORTAL_FEE_PPM), ("direct", 0)):
                trip = immediate_round_trip(
                    size_lamports=size,
                    quote_lamports=quote,
                    base_raw=base,
                    portal_fee_ppm=portal,
                )
                table.append({"pool": name, "route": route, "trip": trip})
    return {
        "n": len(ordered),
        "quote_sol_p10": _sol(levels["p10"]),
        "quote_sol_p50": _sol(levels["p50"]),
        "quote_sol_p90": _sol(levels["p90"]),
        "round_trips_at_graduation_mcap": table,
    }


def run(
    *,
    live_tape: Sequence[Path],
    backfill_tape: Sequence[Path],
    observe_creates: Sequence[Path],
    backfill_creates: Sequence[Path],
    attention_dir: Path | None,
    graph_dir: Path | None,
    output_dir: Path,
    n_folds: int,
    backend: str | None,
    window_start_ms: int,
) -> dict[str, Any]:
    out_text = str(output_dir)
    if "/sealed/trades" in out_text:
        raise SystemExit("refusing to write into the trade tape directory")
    lag_guess = ENTRY_LATENCY_MS
    creates = load_create_map(observe_creates, backfill_creates, lag_ms=lag_guess)
    print(f"creates={len(creates)}", file=sys.stderr)
    books, migration, stats, reservoir = load_graduated_books(
        live_tape,
        backfill_tape,
        creates,
        window_start_ms=window_start_ms,
    )
    tape_end = 0
    for book in books.values():
        if book.flow:
            tape_end = max(tape_end, book.flow[-1].t_recv_ms)
    attention = load_attention(attention_dir) if attention_dir is not None else []
    graph = FundingGraph.load(graph_dir) if graph_dir is not None else None
    print(f"attention_events={len(attention)} graph={0 if graph is None else len(graph)}", file=sys.stderr)
    rows, mig_quotes = build_rows(books, migration, attention, tape_end_ms=tape_end, graph=graph)
    print(f"decisions={len(rows)} tape_end={_iso(tape_end) if tape_end else None}", file=sys.stderr)
    label_rows(
        books,
        rows,
        tape_end_ms=tape_end,
        size_lamports=SIZE_LAMPORTS,
        slippage_cap=DEFAULT_SLIPPAGE_CAP,
    )
    curve = _fit_pressure(rows)
    baselines = buy_all_table(rows, curve=curve)
    # 0.5 SOL hold book on the migration clocks only. Same fills machinery, fewer rules.
    large_rows = [
        DecisionRow(
            mint=row.mint,
            creator=row.creator,
            create_t_ms=row.create_t_ms,
            decision_t_ms=row.decision_t_ms,
            trigger=row.trigger,
            features=row.features,
        )
        for row in rows
        if row.trigger in {name for _off, name in DECISION_OFFSETS_MS}
    ]
    hold_only = [rule for rule in SWING_EXITS if rule.kind == "hold"]
    # Label large size with the hold rules by temporarily swapping the exit tuple via direct calls.
    _label_holds(books, large_rows, tape_end_ms=tape_end, size_lamports=SIZE_LAMPORTS_LARGE, rules=hold_only)
    large_table = []
    for point in sorted({row.trigger for row in large_rows}):
        subset = [row for row in large_rows if row.trigger == point]
        for rule in hold_only:
            large_table.append(
                {
                    "point": point,
                    "rule": rule.rule_id,
                    "size_sol": 0.5,
                    "raw": _brief(book_stats(_trades(subset, rule.rule_id, mode="raw", curve=None))),
                    "flat_15": _brief(book_stats(_trades(subset, rule.rule_id, mode="flat", curve=None))),
                }
            )
    modeled = evaluate_model(rows, n_folds=n_folds, curve=curve, backend=backend)
    oos_table = _oos_books(modeled["oos"], curve=curve)
    misses = nearest_misses(baselines) + [
        {**row, "book": "model"} for row in nearest_misses(oos_table)
    ]
    misses.sort(key=lambda row: (-(row.get("mean_sol") or -1e9), -(row.get("n") or 0)))
    any_promote = any((row.get("flat_15") or {}).get("promote") for row in baselines + oos_table)
    fee = planning_fee_table()
    impact = measured_impact(mig_quotes)
    board = {
        "schema": SCHEMA,
        "promotion_rule": PROMOTION_RULE,
        "promote": bool(any_promote),
        "window_start": _iso(window_start_ms),
        "tape_end": _iso(tape_end) if tape_end else None,
        "graduated": len(books),
        "decisions": len(rows),
        "attention_events": len(attention),
        "attention_genuine": sum(1 for ev in attention if ev.genuine),
        "entry_latency_ms": ENTRY_LATENCY_MS,
        "size_lamports": SIZE_LAMPORTS,
        "fail_flat": DEFAULT_FAIL_RATE,
        "fail_pressure": None if curve is None else curve.as_dict(),
        "lag_reservoir_n": len(reservoir.data),
        "lags_seen": stats.lags_seen,
        "scan": stats.as_dict(),
        "fee": fee,
        "measured_pools": impact,
        "buy_all": baselines,
        "buy_all_0_5sol_holds": large_table,
        "model_folds": modeled["folds"],
        "model_topk": oos_table,
        "nearest_misses": misses[:12],
        "model_backend": next((m.get("model") for m in modeled["folds"] if m.get("model")), None),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scoreboard.json").write_text(json.dumps(_json_safe(board), indent=2) + "\n", encoding="utf-8")
    (output_dir / "scoreboard.md").write_text(format_markdown(board), encoding="utf-8")
    print(f"wrote {output_dir}/scoreboard.json promote={board['promote']}", file=sys.stderr)
    return board


def _label_holds(
    books: dict[str, MintBook],
    rows: list[DecisionRow],
    *,
    tape_end_ms: int,
    size_lamports: int,
    rules: Sequence[ExitRule],
) -> None:
    for row in rows:
        book = books[row.mint]
        t_entry = row.decision_t_ms + ENTRY_LATENCY_MS
        row.entry_t_ms = t_entry
        entry = try_entry(
            book.path,
            t_entry_ms=t_entry,
            size_lamports=size_lamports,
            slippage_cap=DEFAULT_SLIPPAGE_CAP,
            feats=_ref_feats(row),
        )
        if entry.status == "filled" and entry.venue != "pumpswap":
            entry.status = "missed_no_liquidity"
        row.entry_status = entry.status
        for rule in rules:
            part = simulate_exit(
                book.path,
                entry,
                rule,
                latency_ms=ENTRY_LATENCY_MS,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            status = str(part["exit_status"])
            pnl = part.get("pnl_lamports")
            row.exit_status_by_rule[rule.rule_id] = status
            if status in ("realized", "no_exit_liquidity", "not_entered") and isinstance(pnl, int):
                row.pnl_by_rule[rule.rule_id] = pnl
            else:
                row.pnl_by_rule[rule.rule_id] = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Pressure):
        return {"same_slot_buys": value.same_slot_buys, "nearby_buy_lamports": value.nearby_buy_lamports}
    return value


def _fmt(summary: dict[str, Any] | None) -> str:
    if not summary:
        return ""
    med = summary.get("median_sol")
    mean = summary.get("mean_sol")
    med_s = "" if med is None else f"{med:.6f}"
    mean_s = "" if mean is None else f"{mean:.6f}"
    return (
        f"n={summary.get('n')} med={med_s} mean={mean_s} "
        f"total={summary.get('total_sol')} win={summary.get('win_rate')} "
        f"promote={summary.get('promote')} blockers={summary.get('blockers')}"
    )


def format_markdown(board: dict[str, Any]) -> str:
    lines = [
        "# Graduated PumpSwap swing",
        "",
        f"Window {board.get('window_start')} → {board.get('tape_end')}. "
        f"Graduated {board.get('graduated')}. Decisions {board.get('decisions')}. "
        f"Promote **{board.get('promote')}**.",
        "",
        f"Promotion: {board.get('promotion_rule')}.",
        "",
        "## Fee economics",
        "",
    ]
    cfg = (board.get("fee") or {}).get("config") or {}
    lines.append(
        f"Graduation mcap {cfg.get('graduation_mcap_sol')} SOL, tier {cfg.get('graduation_tier')}, "
        f"creator {cfg.get('graduation_creator_ppm')} ppm, protocol {cfg.get('graduation_protocol_ppm')} ppm, "
        f"LP {cfg.get('graduation_lp_ppm')} ppm, total {cfg.get('graduation_total_ppm')} ppm. "
        f"{cfg.get('note')}"
    )
    lines.append("")
    lines.append("| route | size | fee-only loss | entry impact vs spot | with priority |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for row in (board.get("fee") or {}).get("round_trips") or []:
        fee = row.get("fee_only") or {}
        pool = row.get("graduation_pool") or {}
        lines.append(
            f"| {row.get('route')} | {fee.get('size_sol')} | {fee.get('loss_frac_fees_and_impact')} | "
            f"{pool.get('entry_impact_frac')} | {pool.get('loss_frac_plus_priority')} |"
        )
    pools = board.get("measured_pools") or {}
    lines.append("")
    lines.append(
        f"Measured migration-print quote SOL p10/p50/p90: "
        f"{pools.get('quote_sol_p10')} / {pools.get('quote_sol_p50')} / {pools.get('quote_sol_p90')} "
        f"(n={pools.get('n')})."
    )
    lines.append("")
    lines.append("## Buy-all graduated, 0.05 SOL, flat 15%")
    lines.append("")
    lines.append("| point | rule | n | median | mean | total | win | promote |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in board.get("buy_all") or []:
        flat = row.get("flat_15") or {}
        if row.get("rule") not in ("hold_15m", "hold_30m", "hold_60m", "hold_240m", "tp50_sl30", "trail30", "ladder_2x_t240"):
            continue
        lines.append(
            f"| {row.get('point')} | {row.get('rule')} | {flat.get('n')} | {flat.get('median_sol')} | "
            f"{flat.get('mean_sol')} | {flat.get('total_sol')} | {flat.get('win_rate')} | {flat.get('promote')} |"
        )
    lines.append("")
    lines.append("## Model top-k (causal RankWindow, flat 15%)")
    lines.append("")
    lines.append(f"Backend `{board.get('model_backend')}`.")
    lines.append("")
    lines.append("| point | frac | n | mean | total | promote | blockers |")
    lines.append("| --- | ---: | ---: | ---: | ---: | --- | --- |")
    for row in board.get("model_topk") or []:
        flat = row.get("flat_15") or {}
        lines.append(
            f"| {row.get('point')} | {row.get('fraction')} | {flat.get('n')} | {flat.get('mean_sol')} | "
            f"{flat.get('total_sol')} | {flat.get('promote')} | {flat.get('blockers')} |"
        )
    lines.append("")
    lines.append("## Nearest misses")
    lines.append("")
    for row in board.get("nearest_misses") or []:
        lines.append(
            f"- {row.get('book') or 'buy-all'} {row.get('point')} {row.get('rule')}: "
            f"n={row.get('n')} mean={row.get('mean_sol')} median={row.get('median_sol')} "
            f"CI={row.get('mean_ci90_sol')} ex_top3={row.get('total_ex_top3_sol')} "
            f"blockers={row.get('blockers')}"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def _discover_creates(directory: Path | None) -> list[Path]:
    if directory is None or not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and (p.name.startswith("observe-") or p.name.startswith("creates-"))
        and (p.name.endswith(".jsonl") or p.name.endswith(".jsonl.zst") or p.name.endswith(".jsonl.gz"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper PumpSwap swing book after migration")
    parser.add_argument("--tape-dir", type=Path, required=True)
    parser.add_argument("--backfill-dir", type=Path, required=True)
    parser.add_argument("--creates-dir", type=Path, required=True)
    parser.add_argument("--attention-dir", type=Path)
    parser.add_argument("--graph-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--backend", choices=("lightgbm", "sklearn"))
    parser.add_argument("--window-start-ms", type=int, default=WINDOW_START_MS)
    args = parser.parse_args(argv)
    live = _trade_paths(args.tape_dir)
    backfill = _trade_paths(args.backfill_dir / "trades") if (args.backfill_dir / "trades").is_dir() else _trade_paths(args.backfill_dir)
    creates = _discover_creates(args.creates_dir)
    bf_creates = _discover_creates(args.backfill_dir / "creates")
    if not live and not backfill:
        raise SystemExit("no trade files")
    run(
        live_tape=live,
        backfill_tape=backfill,
        observe_creates=creates,
        backfill_creates=bf_creates,
        attention_dir=args.attention_dir,
        graph_dir=args.graph_dir,
        output_dir=args.output_dir,
        n_folds=args.folds,
        backend=args.backend,
        window_start_ms=args.window_start_ms,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
