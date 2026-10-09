"""H5-BOOSTFLOOR live executor (RULE H5-BOOSTFLOOR v1, frozen 2026-10-08).

Turns H5 trigger events into PumpSwap buys and slot-timed sells. DRY RUN is the default: it builds and simulates
transactions with a public payer and never signs, sends or touches a key. Live needs ALL of: config "mode": "live",
the --live flag, a LIVE_OK file the manager creates, EXP-024 Part 1 present in the deployed tree, an explicit end
instant, and the key from the systemd credential (tools.probe_live.load_probe_key; there is no path override).

This is a build, not a claim. It trades nothing by itself and is not gate evidence.

What is reused from the DEC-019 probe (nothing is copied; this module subclasses and calls it):
  tools.probe_live    LiveExecutor (write-ahead state, send, rebroadcast, confirm loop, getTransaction meta, fill and
                      sell bookkeeping, stuck handling), validate_message (pre-signing allowlist), parse_meta,
                      classify_failure, BlockhashCache, close_token_account, load_probe_key, harden_process
  tools.probe_executor Limits/State/FillLog, LimitedRpc/ProbeRpc, fetch_snapshot/Snapshot/StaticCache, entry_quote,
                      exit_check, fee_ppm_for, sell_probe_message, sim_message, STOP/HALT file checks
  tools.pumpswap_tx   build_buy/buy_instructions/sell_instructions, cp_buy_out, min_out_with_slippage, canonical_pool

Trigger interface. The shadow detector (tools/h5_shadow.py, PR #477) writes JSONL; this module only reads it and has no detection logic.
Primary input, #477's own records (intents_file may be a file or the detector's output directory; the newest hourly file is followed and
the previous hour is drained first on a roll):
  type "trigger"  variant "pv" only; mint, pool, s0, slot, sps, t_detect_ms, q_trigger_sol (post-trade Q, quote + the print's V), base_pre +
                  sell_token_raw (post-trade base), v_print, gap (this pool saw a feed gap -> refused)
  type "gap"      detector lost prints: buys refused for gap_hold_ms (20 s minimum) unless flags_pools is the literal false (a reconnect on a
                  redundant feed whose other sockets stayed up: ledgered as feed_reconnect_redundant, no hold); a missing key holds
  type "hb"       heartbeat (used when feed_heartbeat_max_age_ms is set)
  type "pool"     close record; boost_last_slice_s (else the block-time, else the wall-clock figure) feeds the BOOST halt rules, which judge the
                  UTC-day median over tracked pools (< 335 s, or < 337 s on two days), the share of our sells that landed after their pool's
                  BOOST finished (> 15% from 20 sells), and a structure floor (< 300 s on 3 pools in a day); never a single pool
Alternative flat rows, same meaning, for tests and other detectors:
  h5_intent_v1  mint, pool, s0_slot, sps, trigger_slot, q_lamports (post-trade real quote + V), base_reserve
                (post-trade raw base), v_lamports (per-print V), decision_ms (wall clock of the decision)
  h5_feed_v1    gap (bool), ms                 feed gap / heartbeat
  h5_boost_v1   mint, s0_slot, sps, last_slice_slot and/or last_slice_s   BOOST last-slice timing per tracked pool
  h5_watch_v1   mint, pool                     optional: pool first seen, lets us pre-fetch static accounts
The same entry points exist as callbacks: handle_trigger(H5Trigger), on_feed_status(gap), on_boost_row(...).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

RULE_ID = "H5-BOOSTFLOOR-v1"
TRIGGER_VARIANT = "pv"  # quote + the print's own V; the frozen rule's literal fixed V ("fv") is never traded
SCHEMA_INTENT, SCHEMA_FEED, SCHEMA_BOOST, SCHEMA_WATCH = "h5_intent_v1", "h5_feed_v1", "h5_boost_v1", "h5_watch_v1"
SCHEMA_LEDGER = "h5_ledger_v1"
DRYRUN, LIVE = "dryrun", "live"

# --- the frozen rule's own numbers (code constants, never config) ---------------------------------------------------
HOLD_S = 330.0  # exit trigger at s0 + round(330 s / sps)
RULE_TRIGGER_WINDOW_S = 300.0
RULE_Q_MAX_LAMPORTS = 40 * 10**9  # trigger needs post-trade Q = quote + V <= 40 SOL
SPS_MIN, SPS_MAX = 0.15, 0.6
SPS_MIN_SPAN_S = 120.0  # the detector's sps window must span at least this (it can be ready after ~8 s; a bootstrap sps sets a wrong exit)
BOOST_BUDGET_SOL, BOOST_DONE_FRAC = 17.585, 0.999  # the rule: BOOST spent < 0.999 x 17.585 SOL
ESCALATE_S = 345.0  # from here on a sell retry uses the escalated ladder level
EXP024_PART1 = "EXP/EXP-024-h5-boostfloor-part1-prereg.md"  # live is honoured only if this is in the deployed tree

# --- live-halt rules (coordinator brief 2026-10-08, PLAN.md section 5). Fixed in code. --------------------------------
# BOOST timing is judged on a UTC-day MEDIAN of the last-slice time over tracked pools, not on single pools (DEC-024 5.1, LIVE-PLAN 3, ROBUST
# S2): on the shadow feed 27% of single pools finish under 335 s while the median is 340 s, so a per-pool halt fires every day.
BOOST_MEDIAN_MIN_POOLS = 30  # a day's median counts once this many tracked pools have closed that day. 10 false-latched ~11.6% of days on the
# shadow feed's own distribution, 20: 3.6%, 30: 0.9%, 50: 0.2% (simulation, coordinator decision 2026-10-09).
BOOST_MEDIAN_HALT_S = 335.0  # day median below this -> halt (boost_median_lt_335)
BOOST_MEDIAN_TWICE_S = 337.0  # day median below this on two UTC days, consecutive or not -> halt (boost_median_lt_337_twice)
BOOST_ABSURD_S = 300.0  # a single pool this early is a structure change, not noise ...
BOOST_ABSURD_POOLS = 3  # ... when it happens on this many pools in one UTC day -> halt (boost_structure_lt_300_x3)
BOOST_BEFORE_SELL_FRAC = 0.15  # share of our landed sells whose pool's BOOST last slice was at or before the sell's landing -> halt above this
BOOST_BEFORE_SELL_MIN_SELLS = 20  # ... judged once this many landed sells have a known BOOST last slice (boost_before_sell_gt_15pct)
BOOST_SEEN_KEEP = 300  # pools remembered, to pair a pool's BOOST time with our sell on it

# --- exit timing and entry age (review of 86a224b) ----------------------------------------------------------------------------
SPS_TOLERANCE = 0.01  # a trigger whose sps is more than 1% from the one we measure is refused; an open plan is rebuilt past the same drift
SPS_WINDOW_MS = 300_000  # trailing window of our own getSlot observations
SPS_MIN_SPAN_MS = 60_000  # ... and the two points compared must be at least this far apart
HIST_KEEP_MS = 900_000
REPLAN_TOLERANCE = 0.001  # an open plan is rebuilt when the measured slot rate is more than 0.1% from the plan's (3.3 s over 330 s is the limit)
EARLY_TOLERANCE_MS = 1_000  # a slot-timed stage never fires more than this before its wall-clock time since s0
S0_RECV_LATE_MS = 1_500  # a detector s0 receive time later than our own mapping of s0_slot by more than this is not believed: refuse
SUPERSEDE_MS = 2_000  # a sell with no status this long after it was first sent is superseded by the next rung
BUY_REBROADCAST_MS = 3_000  # a buy is rebroadcast for this long after the decision, then left to expire
LATE_SELL_MIN_N = 1  # literal reading of "more than 5% of sells": judged from the first landed sell. A code constant, not config.
ENTRY_LATE_S = 5.0  # a buy landing later than this after the trigger print is out of the rule (the binding leg is 1.9 s)
LATE_SELL_S = 335.0  # a sell landing after s0 + this is late
LATE_SELL_FRAC = 0.05  # more than 5% of our landed sells late -> halt
LANDING_MEDIAN_MAX_S = 3.0  # median trigger-to-landing over the last LANDING_WINDOW fills above this -> halt
LANDING_WINDOW = 10

# --- EXP-022 seal guard ---------------------------------------------------------------------------------------------
SEAL_START_MS = 1792112400000  # 2026-10-16T01:00:00Z
ORACLE_EARLIEST_MS = 1792116000000  # 2026-10-16T02:00:00Z: the DEC-016 FINAL is written about now, not before
SEAL_END_DEFAULT_MS = 1793930400000  # 2026-11-06T02:00:00Z. Config may move the end later, never earlier.
SEAL_REASONS = frozenset({"seal_window_no_oracle", "seal_pick", "seal_oracle_error"})  # counted, never logged per mint

# --- limits: code maxima, config can only lower (floors: config can only raise) ---------------------------------------
H5_DEFAULT = {
    "stake_lamports": 20_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 80_000_000,
    "total_loss_lamports": 120_000_000, "max_attempts": 120, "max_days": 10, "buy_priority_lamports": 55_000,
    "sell_priority_lamports": 55_000, "escalated_priority_lamports": 150_000, "entry_tolerance_bps": 1500,
}
H5_MAX = dict(H5_DEFAULT)  # the defaults ARE the maxima (review of 86a224b): config can only tighten. Loosening is a reviewed code change.
MIN_WALLET_FLOOR_LAMPORTS = 50_000_000  # config may raise
DEFAULT_WALLET_FLOOR_LAMPORTS = 50_000_000
RENT_RESERVE_LAMPORTS = 2_100_000  # token ATA rent is paid up front on a buy and refunded on the closing sell
SELL_RESERVE_LAMPORTS = 1_000_000  # worst-case fees of one exit (escalated priority, a few attempts)
H5_MAX_RPS = 10.0
SELL_LADDER_SLIP_BPS = (1500, 1500, 3500)  # min_out = quote * (1 - slip): 0.85, 0.85 (fresh quote), 0.65
EMERGENCY_MIN_OUT = 1  # lamport. Never 0; emergency only, at most the position's value is exposed
MAX_ARM_AGE_MS = 8_000  # a precomputed sell older than this is rebuilt before it is sent
BALANCE_MAX_AGE_MS = 30_000


# --- small pure helpers -----------------------------------------------------------------------------------------------


def day_key(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def ceil_slots(seconds: float, sps: float) -> int:
    """Same rounding as the frozen scorer: math.ceil(x / sps - 1e-9)."""
    return max(0, math.ceil(seconds / sps - 1e-9))


@dataclass(frozen=True)
class H5Limits:
    stake_lamports: int
    max_open: int
    max_trades_per_day: int
    daily_loss_lamports: int
    total_loss_lamports: int
    max_attempts: int
    max_days: float
    buy_priority_lamports: int
    sell_priority_lamports: int
    escalated_priority_lamports: int
    entry_tolerance_bps: int
    wallet_floor_lamports: int = DEFAULT_WALLET_FLOOR_LAMPORTS
    send_lead_ms: int = 500  # measured send-to-land of a sell (probe: p50 about 480 ms); the sell is sent this early
    exit_land_offset_s: float = 0.0  # 0: land AT s0 + round(330/sps) (coordinator). The rule's sim lands 0.55 s later.
    deadline_s: float = 400.0  # never hold past s0 + this: emergency market sell
    arm_lead_ms: int = 2_000  # the sell is built and signed this long before it is sent
    rebroadcast_ms: int = 400
    status_poll_ms: int = 500
    slot_poll_ms: int = 200
    slot_resync_ms: int = 20_000
    max_trigger_age_ms: int = 10_000
    pool_cache_ttl_ms: int = 600_000
    buy_cu_limit: int = tx.DEFAULT_BUY_CU_LIMIT
    sell_cu_limit: int = tx.DEFAULT_SELL_CU_LIMIT
    track_volume: bool = True  # the probe's proven live shape; set false only after a dry-run simulate comparison
    gap_hold_ms: int = 20_000  # a detector `gap` record refuses buys for this long (#477 also flags the pools open at the time)
    dry_run_balance_lamports: int = 250_000_000  # dry run has no wallet: balance guard runs against this
    end_ms: int | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "H5Limits":
        def num(key: str, default: Any, lo: float, hi: float, as_int: bool = True) -> Any:
            v = cfg.get(key)
            v = default if v is None else v
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"{key} must be a finite number")
            if v < lo:
                raise ValueError(f"{key} must be >= {lo}")
            v = min(v, hi)  # config can lower a maximum, never raise it
            return int(v) if as_int else float(v)

        if cfg.get("trigger_variant") not in (None, TRIGGER_VARIANT):
            raise ValueError(f"trigger_variant is fixed at {TRIGGER_VARIANT!r}")
        if cfg.get("jito_enabled") or (cfg.get("jito_tip_lamports") or 0) != 0:
            raise ValueError("jito tips are not supported in this build (jito_enabled must be false, jito_tip_lamports 0)")
        fixed = ("late_sell_min_n", "sell_priority_lamports", "escalated_priority_lamports")  # code constants: no config at all
        for key in fixed:
            if key in cfg:
                raise ValueError(f"{key} is not configurable")
        kw: dict[str, Any] = {}
        for key, default in H5_DEFAULT.items():
            kw[key] = H5_DEFAULT[key] if key in fixed else num(key, default, 1, H5_MAX[key], as_int=(key != "max_days"))
        kw["max_days"] = num("max_days", H5_DEFAULT["max_days"], 0.01, H5_MAX["max_days"], as_int=False)
        kw["wallet_floor_lamports"] = int(max(MIN_WALLET_FLOOR_LAMPORTS, num("wallet_floor_lamports", DEFAULT_WALLET_FLOOR_LAMPORTS, 0, 10**12)))
        kw["send_lead_ms"] = num("send_lead_ms", 500, 0, 3_000)
        kw["exit_land_offset_s"] = num("exit_land_offset_s", 0.0, 0.0, 1.0, as_int=False)
        kw["deadline_s"] = num("deadline_s", 400.0, ESCALATE_S, 400.0, as_int=False)
        kw["arm_lead_ms"] = num("arm_lead_ms", 2_000, 500, 10_000)
        kw["rebroadcast_ms"] = num("rebroadcast_ms", 400, 200, 5_000)
        kw["status_poll_ms"] = num("status_poll_ms", 500, 250, 5_000)
        kw["slot_poll_ms"] = num("slot_poll_ms", 200, 100, 2_000)
        kw["slot_resync_ms"] = num("slot_resync_ms", 20_000, 1_000, 60_000)
        kw["max_trigger_age_ms"] = num("max_trigger_age_ms", 10_000, 1_000, 10_000)
        kw["pool_cache_ttl_ms"] = num("pool_cache_ttl_ms", 600_000, 60_000, 900_000)
        kw["buy_cu_limit"] = num("buy_cu_limit", tx.DEFAULT_BUY_CU_LIMIT, 50_000, 400_000)
        kw["sell_cu_limit"] = num("sell_cu_limit", tx.DEFAULT_SELL_CU_LIMIT, 50_000, 400_000)
        kw["gap_hold_ms"] = max(20_000, num("gap_hold_ms", 20_000, 0, 600_000))  # config may lengthen the hold, never shorten it
        kw["dry_run_balance_lamports"] = num("dry_run_balance_lamports", 250_000_000, 0, 10**12)
        tv = cfg.get("track_volume", True)
        if not isinstance(tv, bool):
            raise ValueError("track_volume must be a boolean")
        kw["track_volume"] = tv
        e = cfg.get("end_ms")
        if e is not None:
            if isinstance(e, bool) or not isinstance(e, (int, float)) or not math.isfinite(e) or e <= 0:
                raise ValueError("end_ms must be a finite positive number")
            kw["end_ms"] = int(e)
        return cls(**kw)


# --- trigger interface ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class H5Trigger:
    mint: str
    pool: str
    s0_slot: int
    sps: float
    trigger_slot: int
    q_lamports: int  # the rule's Q_i: post-trade real quote + V
    base_reserve: int  # post-trade raw base reserve
    v_lamports: int  # per-print V (logged; Q already contains it)
    decision_ms: int
    gap: bool = False  # the detector flagged this pool as having seen a feed gap
    s0_wall_ms: int | None = None  # wall clock at which the detector saw s0: the anchor of the 330 s exit if the slot rate moves
    block_time: int | None = None  # chain time (s) of the trigger print

    def public(self) -> dict[str, Any]:
        return asdict(self)

    def log_fields(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "mint"}  # the ledger row carries the mint itself


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_trigger(row: Any) -> tuple[H5Trigger | None, str | None]:
    """(trigger, None) for a good h5_intent_v1 row, (None, reason) for a malformed one, (None, None) for any other schema.
    Only the whitelisted fields are read; anything else in the row is dropped."""
    if not isinstance(row, dict) or row.get("schema") != SCHEMA_INTENT:
        return None, None
    try:
        mint, pool = row["mint"], row["pool"]
        if not (isinstance(mint, str) and isinstance(pool, str) and mint and pool):
            return None, "bad_intent:ids"
        for k in ("s0_slot", "trigger_slot", "q_lamports", "base_reserve", "v_lamports", "decision_ms"):
            if not _is_int(row[k]) or row[k] <= 0:
                return None, f"bad_intent:{k}"
        sps = row["sps"]
        if isinstance(sps, bool) or not isinstance(sps, (int, float)) or not math.isfinite(sps) or not (SPS_MIN < sps < SPS_MAX):
            return None, "bad_intent:sps"
        if row["trigger_slot"] < row["s0_slot"]:
            return None, "bad_intent:slot_order"
        if row["q_lamports"] < row["v_lamports"]:
            return None, "bad_intent:q_below_v"
        if "gap" in row and not isinstance(row["gap"], bool):
            return None, "bad_intent:gap"
    except KeyError as exc:
        return None, f"bad_intent:missing_{exc.args[0]}"
    return H5Trigger(mint, pool, row["s0_slot"], float(sps), row["trigger_slot"], row["q_lamports"], row["base_reserve"],
                     row["v_lamports"], row["decision_ms"], bool(row.get("gap")),
                     row["s0_wall_ms"] if _is_int(row.get("s0_wall_ms")) else None, row["block_time"] if _is_int(row.get("block_time")) else None), None


def parse_shadow_trigger(row: Any, variant: str = "pv") -> tuple[H5Trigger | None, str | None]:
    """A `type: "trigger"` record of tools/h5_shadow.py (PR #477) -> H5Trigger. Only the chosen Q variant is acted on ("pv": quote +
    the print's own V, the primary; "fv" is the rule's literal fixed V and is ignored here). Mapping, all from fields #477 writes:
      s0 -> s0_slot, slot -> trigger_slot, q_trigger_sol * 1e9 -> q_lamports (post-trade Q of that variant),
      base_pre + sell_token_raw -> base_reserve (a sell adds its tokens to the pool), v_print -> v_lamports, t_detect_ms -> decision_ms,
      gap -> gap. (None, None) for any other record or the other variant."""
    if not isinstance(row, dict) or row.get("type") != "trigger" or row.get("variant") != variant:
        return None, None
    try:
        q_sol, base_pre, tok = row["q_trigger_sol"], row["base_pre"], row["sell_token_raw"]
        v = row["v_print"]
        if isinstance(q_sol, bool) or not isinstance(q_sol, (int, float)) or not math.isfinite(q_sol) or q_sol <= 0:
            return None, "bad_intent:q_trigger_sol"
        if not _is_int(base_pre) or not _is_int(tok) or tok <= 0:
            return None, "bad_intent:base"
        if not _is_int(v) or v <= 0:
            return None, "bad_intent:v_print"
        # #477 never writes v_print null: with no event V it uses the pool's v0 and sets v_missing true. Only a literal False is tradable.
        if row.get("v_missing") is not False:
            return None, "bad_intent:v_missing"
        if not isinstance(row.get("gap"), bool):  # the key must be there and a bool; the default would be silent
            return None, "bad_intent:gap"
        if not _is_int(row.get("base_breaks")) or row["base_breaks"] != 0 or not _is_int(row.get("slot_regress")) or row["slot_regress"] != 0:
            return None, "bad_intent:base_breaks"  # missed or reordered prints in this pool's book
        span = row.get("sps_span_s")  # the detector's sps window; null only in a tape replay, which this executor never runs
        if isinstance(span, bool) or not isinstance(span, (int, float)) or not math.isfinite(span) or span < SPS_MIN_SPAN_S:
            return None, "bad_intent:sps_span"
        spent = row.get("boost_spent_sol")  # the rule's BOOST-remaining condition, re-checked
        if isinstance(spent, bool) or not isinstance(spent, (int, float)) or not math.isfinite(spent) or spent < 0 or spent >= BOOST_DONE_FRAC * BOOST_BUDGET_SOL:
            return None, "bad_intent:boost_spent"
        return parse_trigger({"schema": SCHEMA_INTENT, "mint": row["mint"], "pool": row["pool"], "s0_slot": row["s0"], "sps": row["sps"],
                              "trigger_slot": row["slot"], "q_lamports": int(round(q_sol * 1e9)), "base_reserve": base_pre + tok,
                              "v_lamports": v, "s0_wall_ms": row.get("s0_t_recv_ms"), "block_time": row.get("block_time"),
                              "decision_ms": row["t_detect_ms"] if not isinstance(row["t_detect_ms"], float) else int(row["t_detect_ms"]),
                              "gap": row.get("gap")})
    except KeyError as exc:
        return None, f"bad_intent:missing_{exc.args[0]}"


# --- slot arithmetic (the part the tests pin at 200 ms and 400 ms slots) --------------------------------------------


@dataclass(frozen=True)
class ExitPlan:
    s0_slot: int
    sps: float
    exit_slot: int  # s0 + round(330 / sps): the rule's exit trigger
    land_slot: int  # where the sell should LAND (exit_slot + the optional offset)
    send_slot: int  # land_slot minus the send-to-land lead: when the signed sell is sent
    arm_slot: int  # when the sell is built and signed
    escalate_slot: int  # s0 + round(345 / sps)
    deadline_slot: int  # s0 + round(400 / sps): emergency market sell, hard
    late_slot: int  # s0 + round(335 / sps): a sell landing after this is late
    # The same stages in wall time since s0 (s0_wall_ms + 330 s, ...). Slots follow the slot rate the detector measured; if that rate moves
    # (the 400 -> 200 ms switch) a slot plan lands tens of seconds from 330 s, so the wall stages bound it: no stage fires more than
    # EARLY_TOLERANCE_MS before its wall time, and every stage fires at its wall time even if the slot clock is unknown.
    s0_wall_ms: int | None = None
    arm_wall_ms: int | None = None
    send_wall_ms: int | None = None
    escalate_wall_ms: int | None = None
    deadline_wall_ms: int | None = None
    late_wall_ms: int | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


def exit_plan(s0_slot: int, sps: float, h5: H5Limits, s0_wall_ms: int | None = None) -> ExitPlan:
    exit_slot = s0_slot + int(round(HOLD_S / sps))  # python round, as the frozen scorer (s14_boostdip.py)
    land_slot = exit_slot + ceil_slots(h5.exit_land_offset_s, sps)
    send_slot = land_slot - ceil_slots(h5.send_lead_ms / 1000.0, sps)
    arm_slot = send_slot - ceil_slots(h5.arm_lead_ms / 1000.0, sps)
    wall: dict[str, int | None] = {}
    if s0_wall_ms is not None:
        send_wall = s0_wall_ms + int(HOLD_S * 1000) + int(h5.exit_land_offset_s * 1000) - h5.send_lead_ms
        wall = dict(s0_wall_ms=s0_wall_ms, send_wall_ms=send_wall, arm_wall_ms=send_wall - h5.arm_lead_ms,
                    escalate_wall_ms=s0_wall_ms + int(ESCALATE_S * 1000), deadline_wall_ms=s0_wall_ms + int(h5.deadline_s * 1000),
                    late_wall_ms=s0_wall_ms + int(LATE_SELL_S * 1000))
    return ExitPlan(s0_slot, sps, exit_slot, land_slot, send_slot, arm_slot, s0_slot + int(round(ESCALATE_S / sps)),
                    s0_slot + int(round(h5.deadline_s / sps)), s0_slot + int(round(LATE_SELL_S / sps)), **wall)


def entry_terms(q_lamports: int, base_reserve: int, stake: int, tol_bps: int) -> dict[str, Any]:
    """Expected tokens at the trigger's post-trade state and the min_out that refuses a fill if the price moved up by
    more than the tolerance: min_out = floor(expected / (1 + X)). Reuses probe_executor.entry_quote."""
    snap = pe.Snapshot(None, 0, q_lamports, base_reserve, 0)  # quote_priced = q_lamports + 0; ps is not used by entry_quote
    q = pe.entry_quote(snap, stake)
    return {**q, "expected_tokens": q["tokens"], "min_out": q["tokens"] * 10_000 // (10_000 + tol_bps)}


def balance_need(stake: int, buy_priority: int, n_open: int, pending_buy_stake: int, floor: int) -> int:
    """Lamports the wallet must hold before a buy. Open positions are already out of the balance (their cost left it),
    so assuming they all go to zero is already in the balance; what is added is everything still to be paid: this
    stake, this buy's fees and rent, the exit fees of every open position plus this one, stakes in flight, the floor."""
    return stake + buy_priority + tx.BASE_FEE_PER_SIGNATURE + RENT_RESERVE_LAMPORTS + (n_open + 1) * SELL_RESERVE_LAMPORTS + pending_buy_stake + floor


class SlotClock:
    """Dead-reckoned slot estimate anchored on getSlot (processed). Between polls it advances by wall time / sps."""

    def __init__(self) -> None:
        self.slot: int | None = None
        self.wall_ms = 0
        self.hist: list[tuple[int, int]] = []  # (wall_ms, slot), thinned to >= 2 s apart, trailing HIST_KEEP_MS

    def observe(self, slot: int, wall_ms: int) -> None:
        if self.slot is None or slot >= self.slot:
            self.slot, self.wall_ms = slot, wall_ms
            if not self.hist or wall_ms - self.hist[-1][0] >= 2_000:
                self.hist.append((wall_ms, slot))
                while self.hist and wall_ms - self.hist[0][0] > HIST_KEEP_MS:
                    self.hist.pop(0)

    def wall_of_slot(self, slot: int, sps_hint: float | None = None) -> int | None:
        """The wall time our own getSlot history maps a past slot to (linear between the two observations around it). Beyond the newest
        observation it extends with sps_hint; before the oldest it is unknown (None): history only covers what this process has seen."""
        if not self.hist or slot < self.hist[0][1]:
            return None
        prev = self.hist[0]
        for w, s in self.hist[1:]:
            if s >= slot:
                w0, s0 = prev
                return w0 if s == s0 else int(w0 + (w - w0) * (slot - s0) / (s - s0))
            prev = (w, s)
        if sps_hint is None:
            return None
        return int(prev[0] + (slot - prev[1]) * sps_hint * 1000)

    def measured_sps(self, now_ms: int) -> float | None:
        """Seconds per slot from OUR OWN getSlot observations: the newest point against the oldest within the trailing 300 s, and only when
        they are at least 60 s apart and the newest is under 30 s old. None when that does not hold (a fresh start has none for a minute)."""
        recent = [(w, s) for w, s in self.hist if now_ms - w <= SPS_WINDOW_MS]
        if len(recent) < 2 or now_ms - recent[-1][0] > 30_000:
            return None
        (w0, s0), (w1, s1) = recent[0], recent[-1]
        if w1 - w0 < SPS_MIN_SPAN_MS or s1 <= s0:
            return None
        return (w1 - w0) / 1000.0 / (s1 - s0)

    def est(self, now_ms: int, sps: float) -> int | None:
        if self.slot is None:
            return None
        return self.slot + max(0, int((now_ms - self.wall_ms) / (sps * 1000.0)))

    def age(self, now_ms: int) -> int:
        return 10**12 if self.slot is None else now_ms - self.wall_ms


# --- persistence, lock, ledger ------------------------------------------------------------------------------------------


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass
class H5Counters:
    """Persisted beside the probe State: per-day trade and realized counters, the latched halts, the rolling landing
    times, the late-sell tally, the BOOST last-slice sightings and the seal-skip count. A restart cannot reset them."""

    run_mode: str = DRYRUN
    days: dict[str, dict[str, Any]] = field(default_factory=dict)
    halts: dict[str, dict[str, Any]] = field(default_factory=dict)
    landing_s: list[float] = field(default_factory=list)
    sells_landed: int = 0
    sells_late: int = 0
    seal_skips: int = 0
    boost_seen_s: dict[str, float] = field(default_factory=dict)  # recent pool -> BOOST last slice, seconds after s0
    sell_land_s: dict[str, float] = field(default_factory=dict)  # our landed sells not yet paired: pool -> landing, seconds after s0
    bvs_n: int = 0  # landed sells paired with their pool's BOOST last slice
    bvs_before: int = 0  # ... of which BOOST's last slice came at or before our landing
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)  # open or in-flight position -> its exit plan and trigger, durable BEFORE the first send
    tail_path: str | None = None  # the intents file being read, so a restart finishes it before it moves to the newest hour

    def day(self, key: str) -> dict[str, Any]:
        d = self.days.setdefault(key, {"trades": 0, "realized": 0})
        d.setdefault("boost_s", {})  # pool -> last slice (s), one per pool, for the day's median
        d.setdefault("boost_lt300", [])
        d.setdefault("boost_median", None)  # the day's running median once BOOST_MEDIAN_MIN_POOLS pools closed
        return d

    def save(self, path: Path) -> None:
        for old in sorted(self.days)[:-14]:
            del self.days[old]
        for old in sorted(self.days)[:-3]:
            self.days[old].pop("boost_s", None)  # the raw list is only needed for the current day's median; the median stays
            self.days[old].pop("boost_lt300", None)
        _atomic_json(path, {"schema": "h5_counters_v1", **self.__dict__})

    @classmethod
    def load(cls, path: Path, run_mode: str) -> "H5Counters":
        if not path.exists():
            return cls(run_mode=run_mode)
        raw = json.loads(path.read_text())
        c = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        if c.run_mode != run_mode:
            raise SystemExit(f"counters file run_mode {c.run_mode!r} does not match {run_mode!r}")
        return c


def acquire_lock(path: Path) -> int:
    """Single instance: an exclusive non-blocking flock held for the life of the process (dry run and live share it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)  # a symlink in its place is refused, not followed and truncated
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SystemExit("h5_executor: another instance holds the lock; refusing to start") from None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


class H5Ledger(pe.FillLog):
    """The JSONL ledger: every decision, refusal, send, fill, exit and halt. Same writer as the probe's fill log."""

    def write(self, row: dict[str, Any]) -> None:
        super().write({"schema": SCHEMA_LEDGER, **row})


class PublicOnly:
    """Dry-run stand-in for a Keypair: a public key, no signing capability at all."""

    def __init__(self, pubkey: Pubkey):
        self._pk = pubkey

    def pubkey(self) -> Pubkey:
        return self._pk

    def __repr__(self) -> str:
        return f"PublicOnly({self._pk})"


# --- start conditions ---------------------------------------------------------------------------------------------------


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


LIVE_OK_PATH = Path("/etc/mal-h5/LIVE_OK")  # DEC-024 3: "The executor sends only while LIVE_OK exists. Helm creates it as root; the executor cannot."
LIVE_OK_UID = 0  # (module constants so a test can stand in for root)
LIVE_OK_GID = 0
LIVE_OK_MODE = 0o644
PINNED_PATH_KEYS = ("stop_file", "halt_file", "live_ok_file", "final_marker_file")  # no config override in live


def live_ok_valid() -> str | None:
    """None when LIVE_OK is a regular file at the fixed path, not a symlink, mode exactly 0644 root:root, in a directory that is owned by root and
    not group- or other-writable; all by lstat and O_NOFOLLOW (the same inode is checked that is opened). The executor cannot create it.
    'live_ok_missing' when absent, 'live_ok_unsafe' for anything else."""
    p = LIVE_OK_PATH
    try:
        dst, lst = os.lstat(p.parent), os.lstat(p)
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return "live_ok_missing"
    except OSError:  # ELOOP for a symlink, EACCES, ENOTDIR ...
        return "live_ok_unsafe"
    try:
        fst = os.fstat(fd)
    finally:
        os.close(fd)
    # Exactly 0644 root:root: the executor runs as mal-live and opens the file, so it must be world-readable, and it must not be writable by anyone else.
    ok = (stat.S_ISDIR(dst.st_mode) and dst.st_uid == LIVE_OK_UID and not dst.st_mode & 0o022
          and stat.S_ISREG(lst.st_mode) and stat.S_ISREG(fst.st_mode) and (lst.st_dev, lst.st_ino) == (fst.st_dev, fst.st_ino)
          and fst.st_uid == LIVE_OK_UID and fst.st_gid == LIVE_OK_GID and stat.S_IMODE(fst.st_mode) == LIVE_OK_MODE)
    return None if ok else "live_ok_unsafe"


def live_path_overrides(cfg: dict[str, Any]) -> list[str]:
    return [k for k in PINNED_PATH_KEYS if k in cfg]


def exp024_part1_present(root: Path) -> bool:
    """EXP-024 Part 1 is merged on main at the deployed commit: the pre-registration file is in the deployed tree. (The
    pinned installer deploys a main sha; this checks the tree it deployed, it does not shell out to git.)"""
    p = root / EXP024_PART1
    try:
        return p.is_file() and not p.is_symlink() and p.stat().st_size > 0
    except OSError:
        return False


def start_refusal(cfg: dict[str, Any], root: Path | None = None) -> str | None:
    """The live start conditions, keyless. None = may start live."""
    root = root or repo_root()
    if live_path_overrides(cfg):
        return "config_path_override:" + ",".join(live_path_overrides(cfg))  # STOP, HALT, LIVE_OK and FINAL_WRITTEN are pinned in live
    why = live_ok_valid()
    if why:
        return why
    if not exp024_part1_present(root):
        return "exp024_part1_missing"
    if cfg.get("end_ms") is None:
        return "end_ms_missing"
    return None


def cfg_path(cfg: dict[str, Any], key: str, name: str) -> Path:
    return Path(cfg[key]) if cfg.get(key) else Path(cfg["state_dir"]) / name


def run_path(cfg: dict[str, Any], key: str, name: str, live: bool) -> Path:
    """STOP, HALT and FINAL_WRITTEN: <state_dir>/<name> in live with no override; a dry run may point them elsewhere."""
    return Path(cfg["state_dir"]) / name if live else cfg_path(cfg, key, name)


def build_probe_cfg(cfg: dict[str, Any], h5: H5Limits, run_mode: str) -> dict[str, Any]:
    """The cfg the probe base classes want. State, ledger and counters live in <state_dir>/<mode>/ so a dry run can never
    consume the live budget, and nothing is shared with the probe's own files under /var/lib/mal-live."""
    sd = Path(cfg["state_dir"]) / run_mode
    pc: dict[str, Any] = {
        "signals_dir": str(Path(cfg["intents_file"]).parent), "state_dir": str(sd), "fill_log": str(sd / "h5-ledger.jsonl"),
        "stop_file": str(run_path(cfg, "stop_file", "STOP", run_mode == LIVE)), "halt_file": str(run_path(cfg, "halt_file", "HALT", run_mode == LIVE)),
        "mode": LIVE, "book": "h5_boostfloor_v1", "commitment": cfg.get("commitment", "confirmed"), "poll_s": float(cfg.get("poll_s", 5.0)),
        "size_lamports": h5.stake_lamports, "priority_lamports": h5.buy_priority_lamports, "max_attempts": h5.max_attempts,
        "max_open": h5.max_open, "loss_cap_lamports": h5.total_loss_lamports, "max_days": h5.max_days,
        "slippage_cap": h5.entry_tolerance_bps / 10_000.0, "sell_retries": max(1, min(int(cfg.get("sell_retries", 5)), pl.SELL_MAX_ATTEMPTS)),
        "signal_poll_ms": int(cfg.get("intent_poll_ms", 25)),
    }
    if h5.end_ms is not None:
        pc["end_ms"] = h5.end_ms
    if cfg.get("user"):
        pc["user"] = cfg["user"]
    return pc


# --- the pick oracle (EXP-022 seal): a boolean and nothing else ---------------------------------------------------------

_MINT_RE = re.compile(r'"mint"\s*:\s*"([1-9A-HJ-NP-Za-km-z]{32,44})"')
_PICK_RE = re.compile(r'"pick"\s*:\s*(true|false)')


class OracleUndecided(Exception):
    """The picks file has no row for the mint: undecided, so the executor refuses (fail closed)."""


class JsonlPickOracle:
    """pick_oracle(mint) -> bool over a boolean-only picks file: one row per gate decision, {"mint": ..., "pick": true|false}.
    The row is NOT parsed as JSON: two regexes pull the mint and the flag and nothing else of the line is read into a
    value, so a score, P&L or position field in a richer file is never touched. A mint with no row raises OracleUndecided."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._off = 0
        self._ino: int | None = None
        self._flags: dict[str, bool] = {}

    def _refresh(self) -> None:
        st = self.path.stat()
        if self._ino != st.st_ino or st.st_size < self._off:
            self._off, self._ino, self._flags = 0, st.st_ino, {}
        with self.path.open("rb") as fh:
            fh.seek(self._off)
            data = fh.read(pe.MAX_READ_BYTES * 8)
        end = data.rfind(b"\n")
        if end < 0:
            return
        for raw in data[: end + 1].splitlines():
            line = raw.decode("utf-8", "replace")
            m, f = _MINT_RE.findall(line), _PICK_RE.findall(line)
            if len(m) == 1 and len(f) == 1:
                self._flags[m[0]] = self._flags.get(m[0], False) or f[0] == "true"  # a pick is sticky: a later pick:false row never undoes it
        self._off += end + 1

    def __call__(self, mint: str) -> bool:
        self._refresh()
        if mint not in self._flags:
            raise OracleUndecided("undecided")
        return self._flags[mint]


# --- H5's own precheck ------------------------------------------------------------------------------------------------------


def h5_precheck(probe_cfg: dict[str, Any], mode: str) -> list[str]:
    """H5's replacement for probe_executor.profile_precheck, scoped to H5's own state_dir. The probe's version stats the probe's DEC-020
    state file under /var/lib/mal-live (mal-live 0700), which a dry run as another user cannot do, and H5 has no handover from the
    probe's profiles. Here: the limits must be valid, and H5's own state dir and files must be readable and writable. Anything else
    refuses (fail closed), in a dry run and in live. Returns [] (there is no probe `bought` list to seed)."""
    try:
        pe.Limits.from_config(probe_cfg)
    except ValueError as exc:
        raise SystemExit(f"limits refused: {exc}") from None
    sd = Path(probe_cfg["state_dir"])
    try:
        sd.mkdir(parents=True, exist_ok=True)
        if not os.access(sd, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError(sd)
        for f in (pe.state_path_for(sd, LIVE, pe.DEFAULT_PROFILE), sd / "h5-counters.json", sd / "h5-ledger.jsonl"):
            if f.exists() and not os.access(f, os.R_OK | os.W_OK):
                raise PermissionError(f)
    except OSError:
        raise SystemExit("h5 precheck refused: the H5 state dir or its files cannot be read and written (fail closed)") from None
    if mode == LIVE:
        # Live only (a dry run as another user cannot read these): the probe's cross-check, kept. The wallet is shared with the decommissioned
        # probe, so neither of its profiles may have a position open or in flight, and unreadable probe state fails closed.
        for prof in pe.PROFILES:
            try:
                counts = pe._live_state_open(pe.state_path_for(pe.LIVE_DIR, LIVE, prof))
            except BaseException:
                raise SystemExit(f"h5 precheck refused: the probe's {prof} live state cannot be read (fail closed)") from None
            if counts and any(counts):
                raise SystemExit(f"h5 precheck refused: the probe's {prof} live state has {counts[0]} open and {counts[1]} pending position(s)")
    return []


@contextlib.contextmanager
def _h5_precheck_scope(run_mode: str):
    """probe_executor.Executor.__init__ calls the module-global profile_precheck (always with mode "live"). For the duration of H5Executor's
    construction (one thread, restored in `finally`) that name is H5's own precheck, told the real run mode. Nothing of the probe's is edited,
    and the probe's executor, which never runs in this process, keeps its own."""
    orig = pe.profile_precheck
    pe.profile_precheck = lambda cfg, _mode: h5_precheck(cfg, run_mode)
    try:
        yield
    finally:
        pe.profile_precheck = orig


# --- the executor -------------------------------------------------------------------------------------------------------


class H5Executor(pl.LiveExecutor):
    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], keypair: Keypair | None, *,
                 now_ms: Callable[[], int] | None = None, pick_oracle: Callable[[str], bool] | None = None,
                 root: Path | None = None, rpc_label: str | None = None):
        self.h5 = H5Limits.from_config(cfg)
        self.dry_run = keypair is None
        self.run_mode = DRYRUN if self.dry_run else LIVE
        self.h5cfg = cfg
        self.root = root or repo_root()
        self.intents_path = Path(cfg["intents_file"])
        if not self.dry_run and live_path_overrides(cfg):
            raise SystemExit(f"live refused: {', '.join(live_path_overrides(cfg))} cannot be set in live (STOP, HALT, FINAL_WRITTEN are <state_dir>/ and "
                             f"LIVE_OK is {LIVE_OK_PATH})")
        probe_cfg = build_probe_cfg(cfg, self.h5, self.run_mode)
        kp = keypair if keypair is not None else PublicOnly(Pubkey.from_string(cfg.get("user") or sim.DEFAULT_USER))
        sd = Path(probe_cfg["state_dir"])
        self.counters_path = sd / "h5-counters.json"
        h5_precheck(probe_cfg, self.run_mode)  # first, before any H5 file is touched
        pe.guard_live_state(self.run_mode, self.counters_path, sd / "h5-ledger.jsonl")  # deleting the counters cannot reset the limits
        with _h5_precheck_scope(self.run_mode):
            super().__init__(rpc, probe_cfg, kp, now_ms=now_ms)  # type: ignore[arg-type]
        self.fills = H5Ledger(sd / "h5-ledger.jsonl", self.run_mode)
        self.decisions = self.intents_path  # base-class messages name the file we actually tail
        self.counters = H5Counters.load(self.counters_path, self.run_mode)
        self.final_marker = run_path(cfg, "final_marker_file", "FINAL_WRITTEN", not self.dry_run)
        self._tail_path = Path(self.counters.tail_path) if self.counters.tail_path else None  # a restart drains the file it was reading first
        seal_end = int(cfg.get("seal_end_ms") or SEAL_END_DEFAULT_MS)
        self.seal_end_ms = max(seal_end, SEAL_END_DEFAULT_MS)  # config may extend the seal, never shorten it
        self.pick_oracle = pick_oracle
        self.slots = SlotClock()
        self.pool_cache: dict[str, tuple[tx.PoolState, int]] = {}
        self.armed: dict[str, dict[str, Any]] = {}
        self.feed_gap = False
        self._gap_until_ms = 0
        self.no_sps_pools: set[str] = set()  # pools the detector skipped for want of an sps: never traded
        self._glob_path: Path | None = None
        self._glob_ms = 0
        self.trigger_variant = TRIGGER_VARIANT  # the traded rule is not a config value
        self.feed_last_ms: int | None = None
        self.heartbeat_max_age_ms = int(cfg.get("feed_heartbeat_max_age_ms") or 0)
        self.refusals: dict[str, int] = {}
        self._bal: tuple[int, int] | None = None
        self._last_adv = 0
        self._last_sell_try: dict[str, int] = {}
        self._slot_alert_ms = 0
        self._slot_fail_ms: int | None = None
        self._seal_logged = self.counters.seal_skips
        self._seal_logged_ms = 0
        self.save()  # state and counters exist on disk before the first ledger row, so the anti-reset guards hold from the first start
        self.counters.save(self.counters_path)
        self.paths = {"state_dir": str(Path(cfg["state_dir"])), "stop": str(run_path(cfg, "stop_file", "STOP", not self.dry_run)),
                      "halt": str(run_path(cfg, "halt_file", "HALT", not self.dry_run)), "final_marker": str(self.final_marker),
                      "live_ok": str(LIVE_OK_PATH) if not self.dry_run else None, "intents": str(self.intents_path),
                      "wallet_stop": str(Path(pe.LIVE_DIR) / "STOP"), "wallet_halt": str(Path(pe.LIVE_DIR) / "HALT")}
        self._log("start", "", rule=RULE_ID, run_mode=self.run_mode, limits=asdict(self.h5), user=str(self.user), paths=self.paths,
                  rpc=sim.redact_rpc_url(rpc_label) if rpc_label else None,
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), seal_end_ms=self.seal_end_ms)

    def __repr__(self) -> str:
        return f"H5Executor(mode={self.run_mode}, user={self.user})"

    # -- guards and kill switches --------------------------------------------------------------------------------------
    def _wallet_switch(self, name: str, fail_closed: bool) -> bool:
        """The wallet-wide STOP / HALT under the probe's directory (/var/lib/mal-live): the paths an operator already knows. Same semantics as
        the ones in <state_dir>. A directory this process cannot read counts as present for STOP in live (it only blocks buys) and as absent
        for HALT (it would freeze exits); a dry run, which cannot read it as another user, treats both as absent."""
        try:
            return (Path(pe.LIVE_DIR) / name).exists()
        except OSError:
            return fail_closed and not self.dry_run

    def _halt_present(self) -> bool:
        return pe.check_halt_file(self.limits) or self._wallet_switch("HALT", False)

    def _stop_present(self) -> bool:
        return pe.check_stop_file(self.limits) or self._wallet_switch("STOP", True)

    def _kill_reason(self) -> str | None:
        if self._halt_present():
            return "halt_file"
        if self._stop_present():
            return "stop_file"
        return None

    def _live_gate(self) -> str | None:
        """Checked before every live buy send: LIVE_OK present and EXP-024 Part 1 in the deployed tree."""
        if self.dry_run:
            return None
        why = live_ok_valid()  # fixed path, root-owned, re-checked by lstat / O_NOFOLLOW before every live buy send
        if why:
            return why
        if not exp024_part1_present(self.root):
            return "exp024_part1_missing"
        return None

    def _latch(self, name: str, **detail: Any) -> None:
        """Latch a live halt: new buys stop, the reason goes to the ledger, and it survives a restart. Cleared only by
        --clear-halt (a ledgered, manual step), never by the executor."""
        if name in self.counters.halts:
            return
        now = self.now_ms()
        self.counters.halts[name] = {"ts_ms": now, **detail}
        self.counters.save(self.counters_path)
        self._log("halt_latched", "", reason=name, **detail)
        self._alert(f"halt_{name}", "", **detail)

    def _seal_reason(self, trg: H5Trigger, now: int) -> str | None:
        if not (SEAL_START_MS <= now < self.seal_end_ms):
            return None
        if self.pick_oracle is None or now < ORACLE_EARLIEST_MS or not Path(self.final_marker).is_file():
            return "seal_window_no_oracle"  # the gate log may be read only after the FINAL; until then all buys refused
        try:
            picked = self.pick_oracle(trg.mint)
        except Exception:
            return "seal_oracle_error"
        if not isinstance(picked, bool):
            return "seal_oracle_error"
        return "seal_pick" if picked else None

    def _budget_stop(self, now: int) -> str | None:
        st, h5 = self.state, self.h5
        if self.counters.halts:
            return "halt_latched:" + ",".join(sorted(self.counters.halts))
        # Worst-case exposure, not realized alone: everything open or in flight, and this stake, is assumed lost.
        at_risk = (sum(int(p.get("spend") or 0) + int(p.get("extra_cost") or 0) for p in st.open.values())
                   + sum(int(p.get("spend") or 0) for p in st.pending.values() if p["kind"] == "buy") + h5.stake_lamports)
        if st.realized_lamports - at_risk <= -h5.total_loss_lamports:
            return "total_loss_stop"
        day = self.counters.day(day_key(now))
        if day["realized"] - at_risk <= -h5.daily_loss_lamports:
            return "daily_loss_stop"
        if day["trades"] >= h5.max_trades_per_day:
            return "max_trades_day"
        if st.attempts >= h5.max_attempts:
            return "max_attempts"
        if st.first_attempt_ms is not None and now - st.first_attempt_ms >= h5.max_days * 86_400_000:
            return "max_days"
        if h5.end_ms is not None and now >= h5.end_ms:
            return "end_instant"
        return None

    def _hard_refusal(self, trg: H5Trigger, now: int) -> str | None:
        """Refusals that hold in both modes."""
        why = self._kill_reason() or self._live_gate()
        if why:
            return why
        if now + pl.CLOCK_BACK_TOLERANCE_MS < self.state.max_seen_ms:
            return "clock_backwards"
        if self.feed_gap or trg.gap or now < self._gap_until_ms:
            return "feed_gap"
        if self.heartbeat_max_age_ms and (self.feed_last_ms is None or now - self.feed_last_ms > self.heartbeat_max_age_ms):
            return "feed_stale"
        if self.sell_stuck():
            return "sell_stuck"
        if now - trg.decision_ms > self.h5.max_trigger_age_ms:
            return "stale_trigger"
        if trg.block_time is not None and now / 1000.0 - trg.block_time > self.h5.max_trigger_age_ms / 1000.0 + 2.0:  # block_time is whole seconds
            return "stale_trigger"
        est = self.slots.est(now, trg.sps)  # the chain's own age of the trigger print, not the detector's clock
        if est is not None and est - trg.trigger_slot > ceil_slots(self.h5.max_trigger_age_ms / 1000.0, trg.sps):
            return "stale_trigger_chain"
        if (trg.trigger_slot - trg.s0_slot) * trg.sps > RULE_TRIGGER_WINDOW_S:  # the frozen window is [0, 300] s, no slack
            return "outside_rule_window"
        measured = self.slots.measured_sps(now)  # the exit is timed in slots from the detector's sps: it must be the rate we see
        if measured is None:
            return "sps_unmeasured"
        if abs(trg.sps / measured - 1.0) > SPS_TOLERANCE:
            return "sps_mismatch"
        why = self._s0_anchor(trg)[1]  # the wall anchor of the exit must be believable (see _s0_anchor)
        if why:
            return why
        if trg.q_lamports > RULE_Q_MAX_LAMPORTS:
            return "q_above_rule_max"
        if trg.mint in self.no_sps_pools:
            return "sps_skipped_pool"
        if trg.mint in self.state.open or trg.mint in self.state.pending or trg.mint in self.state.bought:
            return "already_bought"
        pend = sum(1 for p in self.state.pending.values() if p["kind"] == "buy")
        if len(self.state.open) + pend >= self.h5.max_open:
            return "max_open"
        return None

    def _s0_anchor(self, trg: H5Trigger) -> tuple[int | None, str | None]:
        """(wall ms of s0, refusal). The exit is timed from s0, so its wall anchor is OUR OWN mapping of s0_slot through the getSlot history we
        recorded, not the detector's word: a backlogged s0 print (received late) would otherwise delay the send, the escalation and the
        deadline by the same lag. Where the history does not reach s0 (just after a start) the trigger print's block time less its distance from
        s0 stands in (whole seconds, so coarse). If the detector claims s0 was received more than 1.5 s after that, the claim is false or the
        feed was backlogged: refuse. With no claim to check and nothing to map from, the old estimate from the decision time is used."""
        mapped = self.slots.wall_of_slot(trg.s0_slot, trg.sps)
        if mapped is None and trg.block_time is not None:
            mapped = trg.block_time * 1000 - int(round((trg.trigger_slot - trg.s0_slot) * trg.sps * 1000))
        if mapped is None:
            if trg.s0_wall_ms is not None:
                return None, "s0_unverifiable"
            mapped = trg.decision_ms - int(round((trg.trigger_slot - trg.s0_slot) * trg.sps * 1000))
        if trg.s0_wall_ms is not None and trg.s0_wall_ms - mapped > S0_RECV_LATE_MS:
            return mapped, "s0_recv_late"
        return mapped, None

    def _refuse(self, trg: H5Trigger, reason: str, **kw: Any) -> None:
        if reason in SEAL_REASONS:  # a count only: which mints the gate picked is never written down
            self.counters.seal_skips += 1
            self.counters.save(self.counters_path)
            return
        self.refusals[reason] = self.refusals.get(reason, 0) + 1
        self._skip({"mint": trg.mint, "decision_t_ms": trg.decision_ms}, reason, **kw)

    # -- balance -------------------------------------------------------------------------------------------------------
    def _balance_value(self, now: int) -> int | None:
        if self.dry_run:
            return self.h5.dry_run_balance_lamports
        if self._bal is not None and 0 <= now - self._bal[1] <= BALANCE_MAX_AGE_MS:
            return self._bal[0]
        v = self._balance()
        if v is not None:
            self._bal = (v, now)
        return v

    def _balance_refusal(self, now: int) -> str | None:
        bal = self._balance_value(now)
        if bal is None:
            return "balance_unreadable"
        pend_stake = sum(int(p.get("spend") or 0) for p in self.state.pending.values() if p["kind"] == "buy")
        need = balance_need(self.h5.stake_lamports, self.h5.buy_priority_lamports, len(self.state.open), pend_stake, self.h5.wallet_floor_lamports)
        return None if bal >= need else "balance_floor"

    # -- entry ---------------------------------------------------------------------------------------------------------
    def _static_for(self, trg: H5Trigger, now: int) -> tuple[tx.PoolState | None, str | None, pe.Snapshot | None]:
        ent = self.pool_cache.get(trg.mint)
        if ent is not None and 0 <= now - ent[1] <= self.h5.pool_cache_ttl_ms:
            ps = ent[0]
            if str(ps.pool) != trg.pool or str(ps.base_mint) != trg.mint:
                return None, "pool_mismatch", None
            return ps, None, None
        snap, pool, err = self._snapshot(trg.mint)  # the probe's two-call read; used only on a cache miss
        if snap is None:
            return None, err or "no_pool", None
        if snap.quote_priced is None:
            return None, "no_v", None
        if pool != trg.pool:
            return None, "pool_mismatch", None
        self.pool_cache[trg.mint] = (snap.ps, now)
        return snap.ps, None, snap

    def prefetch(self, mint: str, pool: str) -> None:
        """h5_watch_v1: read the static accounts when the pool first appears, so a trigger needs no RPC read."""
        now = self.now_ms()
        if mint in self.pool_cache or len(self.pool_cache) >= 200:
            return
        snap, p, _err = self._snapshot(mint)
        if snap is not None and p == pool:
            self.pool_cache[mint] = (snap.ps, now)

    def _buy_message(self, ps: tx.PoolState, user: Pubkey, min_out: int, blockhash: Hash | None) -> Message:
        ixs = tx.buy_instructions(ps, user, self.h5.stake_lamports, min_out, 0, priority_total_lamports=self.h5.buy_priority_lamports,
                                  cu_limit=self.h5.buy_cu_limit, exact_quote_in=True, track_volume=self.h5.track_volume)
        return Message.new_with_blockhash(ixs, user, blockhash or Hash.default())

    @pe.critical
    def handle_trigger(self, trg: H5Trigger, seen_ms: int | None = None) -> None:
        now = self.now_ms()
        why = self._hard_refusal(trg, now)
        would: str | None = None
        if not why:
            budget = self._budget_stop(now)
            if budget and not self.dry_run:
                why = budget
            elif budget:
                would = budget  # dry run: budget stops are recorded, not enforced
        if not why:
            why = self._seal_reason(trg, now)
        if not why:
            why = self._balance_refusal(now)
        if why:
            return self._refuse(trg, why)
        with self._prio():
            ps, err, snap = self._static_for(trg, now)
            if ps is None:
                return self._refuse(trg, err or "no_pool")
            terms = entry_terms(trg.q_lamports, trg.base_reserve, self.h5.stake_lamports, self.h5.entry_tolerance_bps)
            if terms["expected_tokens"] <= 0 or terms["min_out"] <= 0:
                return self._refuse(trg, "zero_quote")
            drift = None
            if snap is not None and snap.quote_priced:  # free: we already hold a fresh read on this path
                drift = (snap.quote_priced / snap.base_reserve) / (trg.q_lamports / trg.base_reserve) - 1.0
                if drift > self.h5.entry_tolerance_bps / 10_000.0:
                    return self._refuse(trg, "price_moved", drift_vs_trigger=drift)
            s0_wall = self._s0_anchor(trg)[0]
            plan = exit_plan(trg.s0_slot, trg.sps, self.h5, s0_wall)
            if self.dry_run:
                return self._dry_buy(trg, ps, terms, plan, now, would, drift)
            self._live_buy(trg, ps, terms, plan, now, drift, seen_ms)

    def _live_buy(self, trg: H5Trigger, ps: tx.PoolState, terms: dict[str, Any], plan: ExitPlan, now: int, drift: float | None,
                  seen_ms: int | None) -> None:
        try:
            bhash, lvbh = self.bh.get()
            msg = self._buy_message(ps, self.user, terms["min_out"], bhash)
            signature, tx_b64 = self._sign(msg, ps, trg.mint, cap=self.h5.buy_priority_lamports)
        except (Exception, SystemExit) as exc:
            if isinstance(exc, pl.UnsafeTx):
                self._alert("unsafe_tx_refused", trg.mint, why=exc.label)
            return self._refuse(trg, f"build_error:{pe.error_label(exc)}")
        t_built = self.now_ms()
        late = self._kill_reason() or self._live_gate()  # the kill switch, once more, immediately before the send
        if late:
            return self._refuse(trg, late)
        st = self.state
        st.attempts += 1
        st.bought.append(trg.mint)
        if st.first_attempt_ms is None:
            st.first_attempt_ms = now
        self.counters.day(day_key(now))["trades"] += 1
        self.counters.plans[trg.mint] = {"trigger": trg.public(), "plan": plan.public(), "min_out": terms["min_out"],  # durable before the send
                                         "tolerance_bps": self.h5.entry_tolerance_bps}
        spend = self.h5.stake_lamports
        st.pending[trg.mint] = {
            "kind": "buy", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": trg.pool, "snap_slot": trg.trigger_slot,
            "drift_vs_seed": None, "decision_t_ms": trg.decision_ms, "receive_ms": now, "score": None, "spend": spend,
            "q_tokens": terms["expected_tokens"], "q_net_in": terms["net_in"], "q_mark": terms["mark"], "fee_ppm": terms["fee_ppm"],
            "v_lamports": trg.v_lamports, "base_vault": str(ps.base_vault), "quote_vault": str(ps.quote_vault),
            "base_ata": str(tx.ata(self.user, ps.base_mint, ps.base_token_program)), "base_mint": str(ps.base_mint),
            "base_tp": str(ps.base_token_program), "sends": 0, "seen_ms": seen_ms, "state_ms": now, "state_slot": trg.trigger_slot,
            "built_ms": t_built, "runner_latency": None,
            "h5": {"trigger": trg.public(), "plan": plan.public(), "min_out": terms["min_out"], "tolerance_bps": self.h5.entry_tolerance_bps},
        }
        self.save()
        self.counters.save(self.counters_path)
        p = st.pending[trg.mint]
        self._send(p, trg.mint)
        self.save()
        if trg.mint not in st.pending:  # the kill switch fired between the gate and the send: nothing was sent
            return
        self._log("decision", trg.mint, **trg.log_fields(), stake_lamports=spend, expected_tokens=terms["expected_tokens"], min_out=terms["min_out"],
                  fee_ppm=terms["fee_ppm"], tolerance_bps=self.h5.entry_tolerance_bps, plan=plan.public(), signature=signature,
                  built_ms=t_built, sent_ms=p.get("first_send_ms"), ms_decision_to_send=(p["first_send_ms"] - trg.decision_ms) if p.get("first_send_ms") else None,
                  drift_vs_trigger=drift, priority_lamports=self.h5.buy_priority_lamports)
        self._after_send(trg, ps)

    def _after_send(self, trg: H5Trigger, ps: tx.PoolState) -> None:
        """Off the hot path: anchor the slot clock and keep the ATA/pool cache."""
        self.pool_cache[trg.mint] = (ps, self.now_ms())
        self.refresh_slot()

    def _dry_buy(self, trg: H5Trigger, ps: tx.PoolState, terms: dict[str, Any], plan: ExitPlan, now: int, would: str | None,
                 drift: float | None) -> None:
        msg = self._buy_message(ps, self.user, terms["min_out"], None)
        validate_err = None
        try:
            pl.validate_message(msg, ps, Pubkey.from_string(trg.mint), self.user, self.h5.buy_priority_lamports, self.h5.stake_lamports)
        except Exception as exc:
            validate_err = getattr(exc, "label", type(exc).__name__)
        t_built = self.now_ms()
        try:
            res = pe.sim_message(self.rpc, msg, self.user, [self.user, tx.ata(self.user, ps.base_mint, ps.base_token_program)])
        except (Exception, SystemExit) as exc:
            res = {"err": pe.error_label(exc)}
        sim_tokens = None
        accts = res.get("accounts") or [None, None]
        if res.get("err") is None and len(accts) > 1 and accts[1]:
            sim_tokens = sim.token_amount(sim._b64(accts[1]))
        self.state.attempts += 1
        self.state.bought.append(trg.mint)
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = now
        self.counters.day(day_key(now))["trades"] += 1
        self._log("decision", trg.mint, **trg.log_fields(), stake_lamports=self.h5.stake_lamports, expected_tokens=terms["expected_tokens"],
                  min_out=terms["min_out"], fee_ppm=terms["fee_ppm"], tolerance_bps=self.h5.entry_tolerance_bps, plan=plan.public(),
                  would_have_halted=would, live_validate_err=validate_err, sim_tokens=sim_tokens, drift_vs_trigger=drift,
                  err=res.get("err"), cu_used=res.get("unitsConsumed"), ms_decision_to_built=t_built - trg.decision_ms,
                  ms_built_to_sim=self.now_ms() - t_built, tx_bytes=tx.serialized_size(msg), sent=False)
        if res.get("err") is None:  # a virtual position, so the exit timer and max_open are exercised
            self.state.open[trg.mint] = {
                "mint": trg.mint, "pool": trg.pool, "t_entry_ms": now, "tokens": terms["expected_tokens"], "net_in": terms["net_in"],
                "mark": terms["mark"], "spend": self.h5.stake_lamports, "virtual": True, "h5": {"trigger": trg.public(), "plan": plan.public()},
                "base_mint": str(ps.base_mint), "base_ata": str(tx.ata(self.user, ps.base_mint, ps.base_token_program)),
                "base_tp": str(ps.base_token_program), "sell_attempts": 0,
            }
            self.pool_cache[trg.mint] = (ps, now)
        self.save()
        self.counters.save(self.counters_path)
        self.refresh_slot()

    # -- signing and sending (the only two places a key or the network is touched) ------------------------------------------
    def _sign(self, msg: Message, ps: tx.PoolState, mint: str, cap: int | None = None) -> tuple[str, str]:
        if self.dry_run:
            raise RuntimeError("dry run cannot sign")
        pl.validate_message(msg, ps, Pubkey.from_string(mint), self.user, cap if cap is not None else self.h5.escalated_priority_lamports,
                            self.limits.size_lamports)
        t = VersionedTransaction(msg, [self._kp])
        raw = bytes(t)
        if len(raw) > tx.TX_SIZE_LIMIT:
            raise ValueError("tx too large")
        return str(t.signatures[0]), base64.b64encode(raw).decode()

    def _send(self, p: dict[str, Any], mint: str) -> None:
        if self.dry_run:
            self._log("send_blocked_dry_run", mint, kind_=p.get("kind"))
            return
        if self._halt_present():
            self._log("send_blocked", mint, reason="halt_file", kind_=p.get("kind"))
            return
        if p["kind"] == "buy" and p.get("sends", 0) == 0:
            why = self._kill_reason() or self._live_gate()
            if why:
                self.state.pending.pop(mint, None)
                self._log("send_blocked", mint, reason=why, kind_="buy")
                return
        if p["kind"] == "buy" and p.get("sends", 0) >= 1:
            # A buy is rebroadcast only for BUY_REBROADCAST_MS after its first send, and never once STOP exists or LIVE_OK is gone; then it is
            # left to expire. This is the one place both rebroadcast paths (ours and the base class's 2 s one) pass through.
            if self._kill_reason() or self._live_gate() or self.now_ms() - int(p.get("decision_t_ms") or p.get("first_send_ms") or 0) > BUY_REBROADCAST_MS:
                return
        super()._send(p, mint)

    # -- feed rows -----------------------------------------------------------------------------------------------------------
    def on_feed_status(self, gap: bool) -> None:
        if gap and not self.feed_gap:
            self._log("feed_gap", "", gap=True)
        self.feed_gap = bool(gap)
        self.feed_last_ms = self.now_ms()

    def on_boost_row(self, mint: str, s0_slot: int | None, sps: float | None, last_slice_slot: int | None, last_slice_s: float | None) -> None:
        """BOOST last-slice timing of a closed tracked pool (seconds after s0). Every pool gets a ledger row; the halts judge the day, not the
        pool (a single early pool is noise: 27% of pools finish under 335 s while the median is 340 s):
          (a) UTC-day median over >= 10 pools: < 335 s -> boost_median_lt_335; < 337 s on two UTC days -> boost_median_lt_337_twice;
          (c) structure floor: last slice < 300 s on 3 pools in one UTC day -> boost_structure_lt_300_x3;
          (b) the share of our landed sells whose pool's BOOST had already finished is judged in _pair_boost_with_sell."""
        sec = last_slice_s
        if sec is None and last_slice_slot is not None and s0_slot is not None and sps:
            sec = (last_slice_slot - s0_slot) * sps
        if isinstance(sec, bool) or not isinstance(sec, (int, float)) or not math.isfinite(sec) or not (0 < sec < 2_000):
            return self._log("boost_row_ignored", mint, why="bad_seconds")
        sec = round(float(sec), 3)
        c = self.counters
        day = c.day(day_key(self.now_ms()))
        self._log("boost_last_slice", mint, seconds_after_s0=sec)
        c.boost_seen_s[mint] = sec
        while len(c.boost_seen_s) > BOOST_SEEN_KEEP:
            c.boost_seen_s.pop(next(iter(c.boost_seen_s)))
        if mint not in day["boost_s"]:  # a pool is counted once a day, however often it is re-reported
            day["boost_s"][mint] = sec
            if sec < BOOST_ABSURD_S:
                day["boost_lt300"].append(mint)
            if len(day["boost_s"]) >= BOOST_MEDIAN_MIN_POOLS:
                med = statistics.median(day["boost_s"].values())
                day["boost_median"] = round(med, 3)
                if med < BOOST_MEDIAN_HALT_S:
                    self._latch("boost_median_lt_335", median_s=day["boost_median"], pools=len(day["boost_s"]))
            if len(day["boost_lt300"]) >= BOOST_ABSURD_POOLS:
                self._latch("boost_structure_lt_300_x3", pools=len(day["boost_lt300"]))
        # "< 337 s on two days" is judged on COMPLETED UTC days only: today's running median dips and recovers, a finished day's does not.
        today = day_key(self.now_ms())
        low_days = sorted(k for k, d in c.days.items() if k < today and d.get("boost_median") is not None and d["boost_median"] < BOOST_MEDIAN_TWICE_S)
        if len(low_days) >= 2:
            self._latch("boost_median_lt_337_twice", days=low_days, medians=[c.days[k]["boost_median"] for k in low_days])
        c.save(self.counters_path)
        self._pair_boost_with_sell(mint)

    def _pair_boost_with_sell(self, mint: str) -> None:
        """Halt rule (b): over our own landed sells, the share whose pool's BOOST last slice came at or before our sell's landing (both
        in seconds after s0, the same clock). Judged from 20 paired sells, strictly above 15%. The running share is ledgered on every pair."""
        c = self.counters
        if mint not in c.sell_land_s or mint not in c.boost_seen_s:
            return  # the pool's close record comes after our usual 330 s exit, so either side may arrive first
        landing, boost = c.sell_land_s.pop(mint), c.boost_seen_s[mint]
        c.bvs_n += 1
        c.bvs_before += 1 if boost <= landing else 0
        share = c.bvs_before / c.bvs_n
        c.save(self.counters_path)
        self._log("boost_vs_sell", mint, before=boost <= landing, boost_s=boost, landing_s=round(landing, 3), n=c.bvs_n, before_n=c.bvs_before,
                  share=round(share, 4))
        if c.bvs_n >= BOOST_BEFORE_SELL_MIN_SELLS and share > BOOST_BEFORE_SELL_FRAC:
            self._latch("boost_before_sell_gt_15pct", n=c.bvs_n, before=c.bvs_before, share=round(share, 4))

    # -- intent file ---------------------------------------------------------------------------------------------------------
    def signal_tick(self) -> int:
        return self.intent_tick()

    def _resolve_intents(self) -> Path | None:
        """`intents_file` is a file, or the shadow detector's output directory (hourly h5-shadow-<UTC hour>.jsonl): then the newest hour."""
        p = self.intents_path
        if not p.is_dir():
            return p
        now = self.now_ms()
        if self._glob_path is None or now - self._glob_ms >= 1_000:
            self._glob_ms = now
            files = sorted(p.glob("h5-shadow-????-??-??T??.jsonl"))
            self._glob_path = files[-1] if files else None
        return self._glob_path

    def intent_tick(self) -> int:
        if self._crit:
            return 0
        path = self._resolve_intents()
        try:
            if path is None:
                raise FileNotFoundError
            s = path.stat()
        except FileNotFoundError:
            self._signals_absent()
            return 0
        st = self.state
        rolled = self._tail_path is not None and path != self._tail_path
        if not rolled and st.started and st.inode == s.st_ino and s.st_size == st.offset:
            return 0
        before = (st.started, st.offset, st.inode)
        lines: list[str] = []
        if rolled:  # the hour changed: finish the old file before the new one, so no row between the last look and the roll is lost
            while True:
                chunk = tail_lines(self._tail_path, st)  # type: ignore[arg-type]
                if not chunk:
                    break
                lines += chunk
            st.inode = None  # the new file is read from its start
        self._tail_path = path
        if self.counters.tail_path != str(path):
            self.counters.tail_path = str(path)
            self.counters.save(self.counters_path)
        lines += tail_lines(path, st)
        seen = self.now_ms()
        if (st.started, st.offset, st.inode) != before:
            self.save()  # the offset first: a crash mid-trigger must not replay it
        triggers: list[H5Trigger] = []
        watches: list[tuple[str, str]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            schema = row.get("schema")
            rtype = row.get("type")
            if rtype == "trigger":  # tools/h5_shadow.py (PR #477)
                trg, bad = parse_shadow_trigger(row, self.trigger_variant)
                if trg is not None:
                    triggers.append(trg)
                elif bad:
                    self._log("skip", str(row.get("mint") or ""), reason=bad)
            elif rtype == "gap":
                # The hold starts only when the record says coverage was lost: flags_pools true, or the key missing or anything but the
                # literal false (an unknown schema fails closed). A reconnect on a redundant feed whose other sockets stayed up says false.
                if row.get("flags_pools") is False:
                    self._log("feed_reconnect_redundant", "", kind_=row.get("kind"))
                else:
                    self._gap_until_ms = self.now_ms() + self.h5.gap_hold_ms
                    self._log("feed_gap", "", gap=True, kind_=row.get("kind"))
            elif rtype == "hb":
                self.feed_last_ms = self.now_ms()
            elif rtype == "skipped_no_sps":  # the detector could not time this pool: a later trigger on it is refused
                if isinstance(row.get("mint"), str):
                    self.no_sps_pools.add(row["mint"])
            elif rtype == "pool":  # per-pool close record: BOOST last-slice timing. The rule's own clock (slots x sps) first.
                # Only a pool that ran its full horizon, with no feed gap, and whose BOOST identity is the vault PDA or the event authority
                # (not the behavioural fallback) counts; a shutdown close, a gapped pool or a guessed BOOST wallet is ledgered and ignored.
                if row.get("reason") != "horizon" or row.get("gap") is not False or row.get("boost_src") not in ("pda", "event_authority"):
                    self._log("boost_row_ignored", str(row.get("mint") or ""), why="not_horizon_clean_pda", reason_=row.get("reason"),
                              gap=row.get("gap"), boost_src=row.get("boost_src"))
                    continue
                for k in ("boost_last_slice_s", "boost_last_slice_s_blocktime", "boost_last_slice_s_recv"):
                    v = row.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        self.on_boost_row(str(row.get("mint") or ""), None, None, None, float(v))
                        break
            elif schema == SCHEMA_FEED:
                self.on_feed_status(bool(row.get("gap")))
            elif schema == SCHEMA_BOOST:
                self.on_boost_row(str(row.get("mint") or ""), row.get("s0_slot") if _is_int(row.get("s0_slot")) else None,
                                  row.get("sps") if isinstance(row.get("sps"), (int, float)) else None,
                                  row.get("last_slice_slot") if _is_int(row.get("last_slice_slot")) else None,
                                  row.get("last_slice_s") if isinstance(row.get("last_slice_s"), (int, float)) else None)
            elif schema == SCHEMA_WATCH and isinstance(row.get("mint"), str) and isinstance(row.get("pool"), str):
                watches.append((row["mint"], row["pool"]))
            elif schema == SCHEMA_INTENT:
                trg, bad = parse_trigger(row)
                if trg is not None:
                    triggers.append(trg)
                elif bad:
                    self._log("skip", str(row.get("mint") or ""), reason=bad)
        for trg in triggers:  # triggers first: the buy path is the latency path
            self.handle_trigger(trg, seen)
        for mint, pool in watches:
            self.prefetch(mint, pool)
        return len(triggers)

    # -- exit scheduler ------------------------------------------------------------------------------------------------------
    def refresh_slot(self) -> None:
        t0 = self.now_ms()
        if self._slot_fail_ms is not None and 0 <= t0 - self._slot_fail_ms < 200:
            return  # a failing getSlot is retried at 5 per second at most, not every loop pass
        try:
            with self._prio():
                slot = int(self.rpc("getSlot", [{"commitment": "processed"}]))
        except (Exception, SystemExit) as exc:
            self._slot_fail_ms = t0
            if t0 - self._slot_alert_ms >= 60_000:
                self._slot_alert_ms = t0
                self._alert("slot_clock_error", "", label=pe.error_label(exc))
            return
        self._slot_fail_ms = None
        t1 = self.now_ms()
        self.slots.observe(slot, (t0 + t1) // 2)

    def _slot_fresh(self, now: int, plan: dict[str, Any]) -> None:
        """Poll getSlot at slot_poll_ms inside the final window before the send, at slot_resync_ms otherwise."""
        est = self.slots.est(now, plan["sps"])
        window = ceil_slots(5.0, plan["sps"])
        close = est is None or est >= plan["arm_slot"] - window
        limit = self.h5.slot_poll_ms if close else self.h5.slot_resync_ms
        if self.slots.age(now) >= limit:
            self.refresh_slot()

    @pe.critical
    def exit_tick(self, now: int) -> None:
        if self._halt_present():
            return  # HALT freezes everything, sells included
        for mint, pos in list(self.state.open.items()):
            if pos.get("abandoned"):
                continue
            if not (pos.get("h5") or {}).get("plan"):
                self._recover_plan(mint, pos)  # an open position with no plan is an incident, never a silent skip
            plan = pos["h5"]["plan"]
            self._slot_fresh(now, plan)
            now = self.now_ms()
            est = self.slots.est(now, plan["sps"])
            plan = self._maybe_replan(pos, now) or plan
            dead, esc = self._due(plan, "deadline", est, now), self._due(plan, "escalate", est, now)
            send = self._due(plan, "send", est, now) or bool(pos["h5"].get("immediate_exit"))
            pend = self.state.pending.get(mint)
            if pend is not None:
                if pend["kind"] == "sell":
                    self._maybe_supersede(mint, pos, pend, est, dead, esc)
            elif pos.get("stuck") and not esc and not dead and \
                    now - pos.get("last_sell_fail_ms", 0) < min(pl.STUCK_RETRY_MS * 2 ** max(0, pos["sell_attempts"] - self.sell_retries), pl.STUCK_RETRY_MAX_MS):
                pass  # the stuck back-off applies before the escalation, never after it
            elif dead:
                self._fire_sell(mint, pos, est, emergency=True)
            elif send:
                self._fire_sell(mint, pos, est, emergency=False)
            elif self._due(plan, "arm", est, now) and not self.dry_run:  # a dry run has nothing to precompute: it cannot sign
                armed = self.armed.get(mint)
                if armed is None or now - armed["armed_ms"] > MAX_ARM_AGE_MS:
                    if now - self._last_sell_try.get(mint, 0) >= 500:
                        self._last_sell_try[mint] = now
                        with self._prio():
                            self._arm_sell(mint, pos, self._level(pos, est, False), False, est=est)
            if dead and not pos.get("emergency_attempted"):
                # Not sold by s0 + 400 s, pending or not: the emergency sell has been attempted above; now the halt.
                pos["emergency_attempted"] = True
                self._latch("stuck_position", position_mint=mint, why="not_sold_by_deadline", est_slot=est, deadline_slot=plan["deadline_slot"])
                self.save()

    @staticmethod
    def _due(plan: dict[str, Any], stage: str, est: int | None, now: int) -> bool:
        """A stage is due at its wall time since s0 whatever the slot clock says (and when it is unknown), and at its slot only from
        EARLY_TOLERANCE_MS before that wall time, so a changed slot rate can neither make it late nor fire it minutes early."""
        slot_due = est is not None and est >= plan[f"{stage}_slot"]
        wall = plan.get(f"{stage}_wall_ms")
        if wall is None:
            return slot_due
        return now >= wall or (slot_due and now >= wall - EARLY_TOLERANCE_MS)

    def _recover_plan(self, mint: str, pos: dict[str, Any]) -> None:
        saved = self.counters.plans.get(mint)
        if saved and saved.get("plan"):
            pos["h5"] = {**saved, **(pos.get("h5") or {}), "plan": saved["plan"], "recovered": True}
            self._log("plan_recovered", mint)
        else:  # nothing durable: sell now at the escalated level (every stage due, the deadline far away)
            far = 10**15
            pos["h5"] = {**(pos.get("h5") or {}), "immediate_exit": True, "no_plan": True,
                         "plan": {"s0_slot": 0, "sps": 0.4, "exit_slot": 0, "land_slot": 0, "send_slot": 0, "arm_slot": 0, "escalate_slot": 0,
                                  "deadline_slot": far, "late_slot": far, "s0_wall_ms": None, "arm_wall_ms": 0, "send_wall_ms": 0,
                                  "escalate_wall_ms": 0, "deadline_wall_ms": far, "late_wall_ms": far}}
            self._alert("position_without_plan", mint)
        self.save()

    def _maybe_replan(self, pos: dict[str, Any], now: int) -> dict[str, Any] | None:
        """When the slot rate we measure has moved more than 1% from the plan's, rebuild the plan's slots from the measured rate, anchored
        on the same wall time since s0. The wall stages in the plan do not move."""
        plan = pos["h5"]["plan"]
        m = self.slots.measured_sps(now)
        if pos["h5"].get("no_plan") or m is None or not (SPS_MIN < m < SPS_MAX) or abs(m / plan["sps"] - 1.0) <= REPLAN_TOLERANCE:
            return None  # (a stand-in plan for a position that lost its own is not rebuilt: it has no s0 to anchor on)
        new = exit_plan(plan["s0_slot"], m, self.h5, plan.get("s0_wall_ms")).public()
        self._log("plan_recomputed", pos["mint"], old_sps=plan["sps"], new_sps=round(m, 5), send_slot=new["send_slot"], deadline_slot=new["deadline_slot"])
        pos["h5"]["plan"] = new
        self.save()
        return new

    def _maybe_supersede(self, mint: str, pos: dict[str, Any], p: dict[str, Any], est: int | None, dead: bool, esc: bool) -> None:
        """A pending sell no longer blocks the ladder. At the escalation, at the deadline, and when it has had no status for SUPERSEDE_MS, sign
        the next rung with a fresh blockhash and send it too. Every live signature stays in the pending record (`prior`); whichever lands
        is resolved, the others fail harmlessly (the winner closed the token account), at the cost of one fee each."""
        if self.dry_run:
            return
        h = p.get("h5") or {}
        lvl, emg = int(h.get("level", 0)), bool(h.get("emergency"))
        now = self.now_ms()
        timed = (now - int(h.get("new_ms") or p.get("first_send_ms") or now) >= SUPERSEDE_MS
                 and "confirm_seen_ms" not in p and "status_seen_ms" not in p)  # any status, processed included, ends the timed supersede
        top = len(SELL_LADDER_SLIP_BPS) - 1
        if emg:
            return
        if dead:
            new_lvl, new_emg = top, True
        elif esc and lvl < top:
            new_lvl, new_emg = top, False
        elif timed and lvl < top:
            new_lvl, new_emg = lvl + 1, False
        else:
            return
        if now - self._last_sell_try.get(mint, 0) < 250:
            return
        self._last_sell_try[mint] = now
        with self._prio():
            rec = self._arm_sell(mint, pos, new_lvl, new_emg, est=est, fresh=True)
        self.armed.pop(mint, None)
        if rec is None:
            return
        keep = {k: p[k] for k in ("signature", "tx_b64", "lvbh", "min_out", "q_out", "reason", "h5")}
        rec["prior"] = [*(p.get("prior") or []), keep][-4:]
        rec["h5"]["new_ms"] = now
        pos["exit_reason"] = rec["reason"]
        self.state.pending[mint] = rec
        self.save()
        with self._prio():
            self._send(rec, mint)
        self.save()
        plan = pos["h5"]["plan"]
        self._log("sell_sent", mint, level=rec["h5"]["level"], emergency=new_emg, superseding=True, est_slot=est, send_slot=plan["send_slot"],
                  land_slot=plan["land_slot"], deadline_slot=plan["deadline_slot"], min_out=rec["min_out"], priority_lamports=rec["h5"]["priority"],
                  signature=rec["signature"], sent_ms=rec.get("first_send_ms"), prior_signatures=[x["signature"] for x in rec["prior"]])

    def _level(self, pos: dict[str, Any], est: int | None, emergency: bool) -> int:
        lvl = min(int(pos.get("sell_attempts", 0)), len(SELL_LADDER_SLIP_BPS) - 1)
        if self._due(pos["h5"]["plan"], "escalate", est, self.now_ms()):
            lvl = len(SELL_LADDER_SLIP_BPS) - 1
        return lvl

    def _sell_balance(self, pos: dict[str, Any]) -> tuple[int | None, str]:
        known = pos.get("tokens")
        if (pos.get("sell_attempts", 0) == 0 and isinstance(known, int) and known > 0 and not pos.get("balance_pending")
                and pos.get("ata_pre_amount") == 0):
            return known, "buy_meta"
        return self._ata_balance(pos["base_ata"]), "rpc"

    def _arm_sell(self, mint: str, pos: dict[str, Any], level: int, emergency: bool, est: int | None = None, fresh: bool = False) -> dict[str, Any] | None:
        """Precompute the sell: balance, quote, min_out, blockhash, signature. Nothing is sent. Returns the pending record."""
        now = self.now_ms()
        bal, bsrc = self._sell_balance(pos)
        if not bal or bal <= 0:
            if not pos.get("zero_alerted"):
                pos["zero_alerted"] = True
                self._alert("zero_token_balance", mint)
            return None
        ent = self.pool_cache.get(mint)
        snap, _pool, _err = self._snapshot(mint, self.exit_commitment)
        ps = snap.ps if snap is not None else (ent[0] if ent else None)
        quote = ret = None
        if snap is not None and snap.quote_priced is not None:
            chk = pe.exit_check({**pos, "tokens": bal}, snap, now, own_trade_in_state=True)
            quote, ret = chk["quote_out"], chk["ret"]
            if quote and quote > 0:
                pos["last_quote_out"] = quote  # the reference for a floor when the pool cannot be read later
        if ps is None:
            return None
        if emergency:
            min_out = EMERGENCY_MIN_OUT  # a market sell: never 0, never blocked by a price floor
        elif quote and quote > 0:
            min_out = tx.min_out_with_slippage(quote, SELL_LADDER_SLIP_BPS[level])
        elif self._due(pos["h5"]["plan"], "escalate", est, now):
            # No readable quote, and we are past the escalation: do not wait for the deadline. Floor at 0.65 x the last good quote (else the
            # stake), at the escalated priority. Still never a blind min_out before the deadline.
            level = len(SELL_LADDER_SLIP_BPS) - 1
            min_out = tx.min_out_with_slippage(int(pos.get("last_quote_out") or pos["spend"]), SELL_LADDER_SLIP_BPS[level])
        else:
            if now - pos.get("unpriced_alert_ms", 0) >= 60_000:  # never sell blind outside the emergency
                pos["unpriced_alert_ms"] = now
                self._alert("unpriced_position", mint)
            return None
        prio = self.h5.escalated_priority_lamports if (level >= len(SELL_LADDER_SLIP_BPS) - 1 or emergency) else self.h5.sell_priority_lamports
        try:
            bhash, lvbh = self.bh.get(fresh=fresh or pos.get("sell_attempts", 0) > 0 or emergency)
            ixs = [*tx.sell_instructions(ps, self.user, bal, min_out, priority_total_lamports=prio, cu_limit=self.h5.sell_cu_limit),
                   pl.close_token_account(Pubkey.from_string(pos["base_ata"]), self.user, Pubkey.from_string(pos["base_tp"]))]
            signature, tx_b64 = self._sign(Message.new_with_blockhash(ixs, self.user, bhash), ps, mint, cap=prio)
            if signature in pos.setdefault("sell_sigs", []):
                raise pe.RpcError("duplicate_signature")
            pos["sell_sigs"].append(signature)
        except (Exception, SystemExit) as exc:
            self._alert("sell_build_error", mint, label=pe.error_label(exc))
            return None
        rec = {
            "kind": "sell", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": pos["pool"], "snap_slot": snap.slot if snap else None,
            "decision_t_ms": now, "receive_ms": now, "tokens": bal, "q_out": quote or 0, "min_out": min_out,
            "reason": "h5_emergency" if emergency else "h5_timed_exit", "ret": ret, "sends": 0, **self._exit_fields("full"),
            "balance_source": bsrc, "h5": {"level": level, "emergency": emergency, "priority": prio},
        }
        self.armed[mint] = {"p": rec, "armed_ms": now, "level": level, "emergency": emergency}
        return rec

    def _fire_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        now = self.now_ms()
        if self.now_ms() - self._last_sell_try.get(mint, 0) < 250 and mint not in self.armed:
            return
        if self.dry_run:
            return self._dry_sell(mint, pos, est, emergency)
        level = self._level(pos, est, emergency)
        armed = self.armed.get(mint)
        if armed and now - armed["armed_ms"] <= MAX_ARM_AGE_MS and armed["level"] == level and armed["emergency"] == emergency:
            rec = armed["p"]
        else:
            self._last_sell_try[mint] = now
            with self._prio():
                rec = self._arm_sell(mint, pos, level, emergency, est=est)
        self.armed.pop(mint, None)
        if rec is None:
            return  # exit_tick latches stuck_position at the deadline whether or not a sell could be built
        rec["decision_t_ms"] = rec["receive_ms"] = now
        pos["exit_reason"] = rec["reason"]
        self.state.pending[mint] = rec
        self.save()  # write-ahead
        with self._prio():  # the timed send goes ahead of the status polls and slot reads in the rate-limit queue
            self._send(rec, mint)
        self.save()
        plan = pos["h5"]["plan"]
        self._log("sell_sent", mint, level=rec["h5"]["level"], emergency=emergency, est_slot=est, send_slot=plan["send_slot"],
                  land_slot=plan["land_slot"], deadline_slot=plan["deadline_slot"], min_out=rec["min_out"], priority_lamports=rec["h5"]["priority"],
                  signature=rec["signature"], sent_ms=rec.get("first_send_ms"))

    def _dry_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        """Outcome-blind: simulate the sell shape against live state and log only err, CU, slot and timing. No quote, no P&L."""
        snap, _pool, _err = self._snapshot(mint, self.exit_commitment)
        row: dict[str, Any] = {"emergency": emergency, "est_slot": est, "send_slot": pos["h5"]["plan"]["send_slot"], "land_slot": pos["h5"]["plan"]["land_slot"],
                               "late_slots": est - pos["h5"]["plan"]["send_slot"], "sent": False}
        if snap is not None and snap.quote_priced is not None:
            msg, _tok, _min = pe.sell_probe_message(snap, self.user, pos["spend"], self.slip_bps, self.h5.sell_priority_lamports)
            try:
                res = pe.sim_message(self.rpc, msg, self.user, [self.user])
            except (Exception, SystemExit) as exc:
                res = {"err": pe.error_label(exc)}
            row.update(err=res.get("err"), cu_used=res.get("unitsConsumed"), pool_slot=snap.slot)
        else:
            row.update(err="no_snapshot")
        self._log("dry_sell", mint, **row)
        del self.state.open[mint]
        self.save()

    # -- landing hooks: daily counters and the halt rules ------------------------------------------------------------------
    def _book_realized(self, delta: int) -> None:
        if delta:
            self.counters.day(day_key(self.now_ms()))["realized"] += delta
            self.counters.save(self.counters_path)

    @pe.critical
    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        super()._finish_buy(mint, p, m)
        self._bal = None  # the balance moved
        self._book_realized(self.state.realized_lamports - before)
        pos = self.state.open.get(mint)
        h5 = p.get("h5") or self.counters.plans.get(mint)
        if pos is None:
            self.counters.plans.pop(mint, None)  # the buy failed: no position, no plan to keep
            self.counters.save(self.counters_path)
        if pos is None or h5 is None:
            return
        pos["h5"] = {**h5, "buy_landed_slot": m["slot"]}
        if m.get("slot"):
            trg, plan = h5["trigger"], h5["plan"]
            self._note_buy_landing(trg["trigger_slot"], m["slot"], trg["sps"])
            if m["slot"] > trg["trigger_slot"] + ceil_slots(ENTRY_LATE_S, trg["sps"]) or m["slot"] > plan["arm_slot"]:
                # A buy that lands this late is not the rule's entry (it was rebroadcast, or the network stalled): get out now at level 0
                # and stop buying until someone looks.
                pos["h5"]["immediate_exit"] = True
                self._log("out_of_rule_entry", mint, landed_slot=m["slot"], trigger_slot=trg["trigger_slot"], arm_slot=plan["arm_slot"],
                          slots_after_trigger=m["slot"] - trg["trigger_slot"])
                self._latch("out_of_rule_entry", landed_slot=m["slot"], slots_after_trigger=m["slot"] - trg["trigger_slot"])
        self.save()

    def _note_buy_landing(self, trigger_slot: int, landed_slot: int, sps: float) -> None:
        """Halt rule: median trigger-to-landing over the last 10 fills above 3.0 s (strictly above; fewer than 10 fills: no halt)."""
        sec = (landed_slot - trigger_slot) * sps  # trigger print to landing, in seconds of slot time
        self.counters.landing_s = [*self.counters.landing_s, round(sec, 4)][-50:]
        self.counters.save(self.counters_path)
        last = self.counters.landing_s[-LANDING_WINDOW:]
        if len(last) >= LANDING_WINDOW and statistics.median(last) > LANDING_MEDIAN_MAX_S:
            self._latch("landing_median_gt_3s", median_s=round(statistics.median(last), 4), n=len(last))

    @pe.critical
    def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        pos = self.state.open.get(mint)
        plan = (pos or {}).get("h5", {}).get("plan")
        priors = list(p.get("prior") or [])
        super()._finish_sell(mint, p, m)
        self._bal = None
        self._book_realized(self.state.realized_lamports - before)
        if priors:
            self._settle_priors(mint, priors)  # superseded signatures that landed with an error paid their fee
        if m.get("err") is None and plan is not None and mint not in self.state.open and m.get("slot"):
            self._note_sell_landing(mint, plan, m["slot"], bool(p.get("h5", {}).get("emergency")))

    def _note_sell_landing(self, mint: str, plan: dict[str, Any], landed_slot: int, emergency: bool) -> None:
        """Halt rule: more than 5% of our landed sells landing after s0 + 335 s (strictly more than 5%)."""
        c = self.counters
        c.plans.pop(mint, None)  # the position is closed
        c.sells_landed += 1
        # Time is judged in WALL time since s0, not in slots at the plan's sps (which, for minutes after a slot-rate change, is a blend): the
        # landed slot mapped through our own getSlot history against the s0 anchor. Without wall fields or a mapping it falls back to slots.
        wall0 = plan.get("s0_wall_ms")
        landing_wall = self.slots.wall_of_slot(landed_slot, plan["sps"]) if wall0 is not None else None
        if landing_wall is not None and plan.get("late_wall_ms") is not None:
            landing_s, late = (landing_wall - wall0) / 1000.0, landing_wall > plan["late_wall_ms"]
        else:
            landing_s, late = (landed_slot - plan["s0_slot"]) * plan["sps"], landed_slot > plan["late_slot"]
        c.sell_land_s[mint] = landing_s  # paired with the pool's BOOST last slice when that is known
        while len(c.sell_land_s) > BOOST_SEEN_KEEP:
            c.sell_land_s.pop(next(iter(c.sell_land_s)))
        c.sells_late += 1 if late else 0
        self._log("exit_landing", mint, landed_slot=landed_slot, exit_slot=plan["exit_slot"], land_slot=plan["land_slot"],
                  error_slots=landed_slot - plan["land_slot"], late=late, emergency=emergency, landing_s=round(landing_s, 3))
        c.save(self.counters_path)
        if c.sells_landed >= LATE_SELL_MIN_N and c.sells_late / c.sells_landed > LATE_SELL_FRAC:
            self._latch("late_sells_gt_5pct", late=c.sells_late, landed=c.sells_landed)
        self._pair_boost_with_sell(mint)

    @pe.critical
    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        super()._resolve_expired(mint, p)
        self._book_realized(self.state.realized_lamports - before)

    # -- loop ----------------------------------------------------------------------------------------------------------------
    def prewarm(self) -> None:
        super().prewarm()
        self.refresh_slot()  # our own slot-rate measurement needs observations whether or not a position is open
        now = self.now_ms()
        if not self.dry_run:
            v = self._balance()
            if v is not None:
                self._bal = (v, now)
        c = self.counters
        if c.seal_skips != self._seal_logged and now - self._seal_logged_ms >= 60_000:
            self._seal_logged, self._seal_logged_ms = c.seal_skips, now
            self._log("seal_count", "", seal_skips=c.seal_skips)  # a count and nothing else

    def housekeeping(self, now: int) -> None:
        if not self.state.pending:
            return
        if now - self._last_adv >= self.h5.status_poll_ms:
            self._last_adv = now
            self._poll_priors()
            self.advance_pending()
        if self._halt_present():
            return
        for mint, p in list(self.state.pending.items()):
            window = BUY_REBROADCAST_MS if p["kind"] == "buy" else 25_000
            since = int(p.get("decision_t_ms") or now) if p["kind"] == "buy" else p.get("first_send_ms", now)  # a buy's window runs from the decision
            if (p.get("sends", 0) >= 1 and "expired_seen_ms" not in p and now - p.get("last_send_ms", 0) >= self.h5.rebroadcast_ms
                    and now - since <= window):
                with self._prio():  # inside the exit window a rebroadcast must not queue behind the slot polls
                    self._send(p, mint)

    _SELL_KEYS = ("signature", "tx_b64", "lvbh", "min_out", "q_out", "reason", "h5")

    def _sell_fee(self, entry: dict[str, Any]) -> int:
        """The fee a sell transaction pays when it lands, even failed: the base fee plus its priority (an estimate; its meta is not fetched)."""
        return tx.BASE_FEE_PER_SIGNATURE + int((entry.get("h5") or {}).get("priority") or self.h5.sell_priority_lamports)

    def _book_fee(self, mint: str, fee: int, why: str) -> None:
        """A sell attempt that lost (landed with an error, or was superseded and failed) still paid its fee: it is realized loss now, and the
        position carries it as extra cost so the closing sell does not count it twice."""
        pos = self.state.open.get(mint)
        if pos is not None:
            pos["extra_cost"] = int(pos.get("extra_cost") or 0) + fee
        self.state.realized_lamports -= fee
        self._book_realized(-fee)
        self._log("sell_fee_booked", mint, fee_lamports=fee, why=why)

    def _poll_priors(self) -> None:
        """One getSignatureStatuses call for the current signature and every superseded one of each pending sell. ANY status on any of them
        (processed included) stops the timed supersede. A superseded sell that failed on chain has paid its fee: book it and drop it. One that
        landed is the sell to resolve: swap it in, keep the replaced current signature in `prior` (its status and fee are still tracked), and
        the base confirm loop then reads the new current signature."""
        items = []
        for m, p in self.state.pending.items():
            if p["kind"] == "sell":
                items.append((m, p, True))
                items.extend((m, pr, False) for pr in p.get("prior", []))
        if not items:
            return
        try:
            res = self.rpc("getSignatureStatuses", [[e["signature"] for _m, e, _c in items], {"searchTransactionHistory": True}])["value"]
        except (Exception, SystemExit):
            return
        now = self.now_ms()
        for (m, e, cur), st in zip(items, res):
            p = self.state.pending.get(m)
            if not st or p is None:
                continue
            p.setdefault("status_seen_ms", now)
            if cur or e["signature"] not in {x["signature"] for x in p.get("prior", [])}:
                continue
            if st.get("err") is not None:
                p["prior"] = [x for x in p["prior"] if x["signature"] != e["signature"]]
                self._book_fee(m, self._sell_fee(e), "superseded_sell_failed")
            elif st.get("confirmationStatus") in ("confirmed", "finalized"):
                demoted = {k: p[k] for k in self._SELL_KEYS}
                p["prior"] = [x for x in p["prior"] if x["signature"] != e["signature"]] + [demoted]
                p.update({k: e[k] for k in self._SELL_KEYS})
                p.pop("confirm_seen_ms", None)
                self._log("sell_superseded_landed", m, signature=e["signature"], level=e["h5"].get("level"))
        self.save()

    def _settle_priors(self, mint: str, priors: list[dict[str, Any]]) -> None:
        """The pending sell has resolved. Superseded signatures still in flight are looked at once: one that landed with an error paid its fee.
        One with no status yet may still land later and fail; that fee is not seen (ledgered as unresolved)."""
        try:
            res = self.rpc("getSignatureStatuses", [[x["signature"] for x in priors], {"searchTransactionHistory": True}])["value"]
        except (Exception, SystemExit):
            res = [None] * len(priors)
        for x, st in zip(priors, res):
            if st and st.get("err") is not None:
                self._book_fee(mint, self._sell_fee(x), "superseded_sell_failed")
            elif not st:
                self._log("sell_prior_unresolved", mint, signature=x["signature"])

    def _resolve_landed(self, mint: str, p: dict[str, Any], st: dict[str, Any]) -> None:
        if p.get("kind") == "sell" and st.get("err") is not None and p.get("prior"):
            # The current sell failed on chain while superseded ones are still live: book its fee and promote the newest of them. The
            # position is not finished (that would delete the pending record with the other signatures in it).
            self._book_fee(mint, self._sell_fee(p), "current_sell_failed_with_priors")
            self._log("sell_superseded_failed", mint, signature=p["signature"], promoted=p["prior"][-1]["signature"])
            newest = p["prior"].pop()
            p.update({k: newest[k] for k in self._SELL_KEYS})
            p.pop("confirm_seen_ms", None)
            self.save()
            return
        super()._resolve_landed(mint, p, st)
        if p.get("kind") == "sell" and st.get("err") is not None and p is self.state.pending.get(mint):
            # The status already says the sell failed, but its meta is not available yet: move to the next rung now on an estimated fee
            # (the base class would wait for getTransaction). The row is marked estimated.
            prio = int((p.get("h5") or {}).get("priority") or self.h5.sell_priority_lamports)
            fee = tx.BASE_FEE_PER_SIGNATURE + prio
            self._finish_sell(mint, p, {"slot": st.get("slot"), "fee": fee, "err": st["err"], "sol_delta": -fee, "token_delta": 0, "logs": [],
                                        "ata_rent_pre": 0, "ata_rent_post": 0, "estimated": True})

    def tick(self) -> int:
        now = self.now_ms()
        self._clock_note(now)
        n = self.intent_tick()
        now = self.now_ms()
        self.housekeeping(now)
        self.exit_tick(now)
        if self._last_slow is None or now - self._last_slow >= self.poll_ms:
            self._last_slow = now
            self.prewarm()
        return n

    def step(self) -> int:
        return self.tick()


def tail_lines(path: Path, st: pe.State) -> list[str]:
    """New complete lines since the persisted offset (the probe's tailer, minus its fixed schema parsing). First run starts
    at the end of the file so history is never replayed; rotation or truncation restarts from 0."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    if not st.started:
        st.started, st.offset, st.inode = True, stat.st_size, stat.st_ino
        return []
    if st.inode != stat.st_ino or stat.st_size < st.offset:
        st.offset, st.inode = 0, stat.st_ino
    with path.open("rb") as fh:
        fh.seek(st.offset)
        data = fh.read(pe.MAX_READ_BYTES)
    end = data.rfind(b"\n")
    if end < 0:
        if len(data) >= pe.MAX_READ_BYTES:
            st.offset += len(data)
        return []
    st.offset += end + 1
    return [raw.decode("utf-8", "replace") for raw in data[: end + 1].splitlines()]


# --- CLI ------------------------------------------------------------------------------------------------------------------


def url_label(url: str | None) -> str | None:
    """Only scheme://host of an RPC URL, for logs and the ledger: no path, query, userinfo or key (redact_rpc_url applied first as well)."""
    if not url:
        return None
    parts = urlsplit(sim.redact_rpc_url(url))
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else "rpc"


def status_report(cfg: dict[str, Any]) -> str:
    """Counters, limits and kill-file state from disk. No key, no RPC."""
    h5 = H5Limits.from_config(cfg)
    lines = [f"rule={RULE_ID} stake_sol={h5.stake_lamports / pe.LAMPORTS:.3f} max_open={h5.max_open}",
             f"stop_file={Path(cfg_path(cfg, 'stop_file', 'STOP')).exists()} halt_file={Path(cfg_path(cfg, 'halt_file', 'HALT')).exists()} "
             f"live_ok={live_ok_valid() or 'valid'} ({LIVE_OK_PATH}) exp024_part1={exp024_part1_present(repo_root())}"]
    for mode in (DRYRUN, LIVE):
        sd = Path(cfg["state_dir"]) / mode
        sp = pe.state_path_for(sd, LIVE)
        if not sp.exists():
            lines.append(f"[{mode}] no state")
            continue
        st = pe.State.load(sp, LIVE)
        c = H5Counters.load(sd / "h5-counters.json", mode) if (sd / "h5-counters.json").exists() else H5Counters(run_mode=mode)
        today = c.days.get(day_key(int(time.time() * 1000)), {})
        lines.append(f"[{mode}] attempts={st.attempts}/{h5.max_attempts} realized_sol={st.realized_lamports / pe.LAMPORTS:.6f} "
                     f"open={len(st.open)}/{h5.max_open} pending={len(st.pending)} today_trades={today.get('trades', 0)} "
                     f"today_realized_sol={today.get('realized', 0) / pe.LAMPORTS:.6f} halts={sorted(c.halts)} seal_skips={c.seal_skips} "
                     f"sells_landed={c.sells_landed} sells_late={c.sells_late}")
    return "\n".join(lines)


def _live_wallet(ledger_path: Path) -> str | None:
    """Our wallet's public key, from the newest live `start` row of OUR ledger (never from the command line)."""
    wallet = None
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            if '"kind":"start"' in line and '"run_mode":"live"' in line:
                try:
                    wallet = json.loads(line).get("user") or wallet
                except ValueError:
                    continue
    return wallet


def mark_closed(cfg: dict[str, Any], mint: str, sig: str, rpc: Callable[[str, list], Any], *, now_ms: int | None = None) -> int:
    """Offline: reconcile a position that Helm closed by hand (root sell-and-close). Needs the lock, so it is refused while the unit runs.
    It fetches the transaction and checks that OUR wallet signed it, that it sold THIS mint through PumpSwap, that the token account is
    now closed or empty, and that the position exists. Only then does it move the position from open to closed, book the realized P&L
    from the transaction meta (the loss stops read the same totals), and ledger `manual_close`. Any failed check refuses and changes nothing."""
    try:
        lock = acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")
    except SystemExit:
        raise SystemExit("h5_executor --mark-closed refused: the unit holds the lock (stop it first)") from None
    try:
        def refuse(why: str) -> int:
            print(f"h5_executor --mark-closed refused: {why}; nothing was changed", flush=True)
            return 1

        sd = Path(cfg["state_dir"]) / LIVE
        ledger_path, counters_path, state_path = sd / "h5-ledger.jsonl", sd / "h5-counters.json", pe.state_path_for(sd, LIVE)
        wallet = _live_wallet(ledger_path)
        if wallet is None or not state_path.exists():
            return refuse("no live state or no live start row for this wallet")
        st = pe.State.load(state_path, LIVE)
        pos = st.open.get(mint)
        if pos is None:
            return refuse("that mint has no open position")
        if not isinstance(pos.get("buy_cost_lamports"), int):
            return refuse("the position has no cost basis")
        try:
            res = rpc("getTransaction", [sig, {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
        except (Exception, SystemExit) as exc:
            return refuse(f"getTransaction failed ({pe.error_label(exc)})")
        if not res or not res.get("meta"):
            return refuse("the transaction was not found")
        try:
            msg, meta = res["transaction"]["message"], res["meta"]
            keys = [k if isinstance(k, str) else k["pubkey"] for k in msg["accountKeys"]]
            if (res["transaction"].get("signatures") or [None])[0] != sig:
                return refuse("the transaction's first signature is not the one given")
            if meta.get("err") is not None:
                return refuse("the transaction failed on chain")
            n_sign = int((msg.get("header") or {}).get("numRequiredSignatures", 1))
            if wallet not in keys[:n_sign]:
                return refuse("our wallet did not sign that transaction")
            if not any(keys[int(ix["programIdIndex"])] == str(tx.PUMPSWAP_PROGRAM) for ix in msg.get("instructions") or []):
                return refuse("the transaction does not call PumpSwap")
            if pos["base_ata"] not in keys:
                return refuse("our token account for that mint is not in the transaction")
            ai = keys.index(pos["base_ata"])

            def amount(rows: list | None) -> int:
                vals = [r for r in (rows or []) if r.get("accountIndex") == ai and r.get("mint") in (None, mint)]
                return int(vals[0]["uiTokenAmount"]["amount"]) if vals else 0

            pre_amt, post_amt = amount(meta.get("preTokenBalances")), amount(meta.get("postTokenBalances"))
            if pre_amt <= post_amt:
                return refuse("the transaction did not sell that mint (our token balance did not fall)")
            wi = keys.index(wallet)
            sol_delta = int(meta["postBalances"][wi]) - int(meta["preBalances"][wi])
            fee = int(meta["fee"])
            ata_rent = int(meta["preBalances"][ai])
        except (KeyError, ValueError, TypeError, IndexError):
            return refuse("the transaction is not in the shape of a sell")
        try:
            info = rpc("getAccountInfo", [pos["base_ata"], {"encoding": "base64", "commitment": "confirmed"}]).get("value")
            left = 0 if info is None else sim.token_amount(sim._b64(info))
        except (Exception, SystemExit) as exc:
            return refuse(f"could not read the token account ({pe.error_label(exc)})")
        if left != 0:
            return refuse(f"our token account still holds {left} tokens")
        # All checks passed. Realized = what the wallet gained in the sell less what the buy cost (the same arithmetic as a timed sell;
        # fees of earlier failed sell attempts were booked as they landed).
        extra = int(pos.get("extra_cost") or 0)
        realized = sol_delta - int(pos["buy_cost_lamports"]) - extra
        now = int(time.time() * 1000) if now_ms is None else now_ms
        when = int(res["blockTime"]) * 1000 if isinstance(res.get("blockTime"), int) else now
        counters = H5Counters.load(counters_path, LIVE)
        del st.open[mint]
        st.pending.pop(mint, None)
        st.realized_lamports += realized + extra
        st.save(state_path)
        counters.day(day_key(when))["realized"] += realized + extra
        counters.plans.pop(mint, None)
        counters.sell_land_s.pop(mint, None)
        counters.save(counters_path)
        H5Ledger(ledger_path, LIVE).write({
            "kind": "manual_close", "ts_ms": now, "mint": mint, "signature": sig, "landed_slot": res.get("slot"), "block_time": res.get("blockTime"),
            "tokens_sold": pre_amt - post_amt, "sol_delta_lamports": sol_delta, "fee_lamports": fee, "rent_refunded_lamports": ata_rent,
            "buy_cost_lamports": pos["buy_cost_lamports"], "pnl_lamports": realized, "realized_total_lamports": st.realized_lamports})
        print(f"h5_executor --mark-closed: {mint} closed by {sig}; realized {realized} lamports", flush=True)
        return 0
    finally:
        os.close(lock)


def clear_halt(cfg: dict[str, Any], name: str, run_mode: str) -> int:
    """Offline, manual and ledgered: clear one latched halt. Needs the lock (so no executor is running)."""
    lock = acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")
    sd = Path(cfg["state_dir"]) / run_mode
    path = sd / "h5-counters.json"
    c = H5Counters.load(path, run_mode)
    if name not in c.halts:
        print(f"no such halt: {name}; latched: {sorted(c.halts)}")
        os.close(lock)
        return 1
    gone = c.halts.pop(name)
    c.save(path)
    H5Ledger(sd / "h5-ledger.jsonl", run_mode).write({"kind": "halt_cleared", "ts_ms": int(time.time() * 1000), "mint": "", "reason": name, "was": gone})
    print(f"cleared {name}")
    os.close(lock)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H5-BOOSTFLOOR executor (dry run by default; live needs config mode AND --live AND LIVE_OK)")
    ap.add_argument("--config", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: build and simulate, never sign or send")
    g.add_argument("--live", action="store_true", help='also needs config "mode": "live", a LIVE_OK file and EXP-024 Part 1 in the tree')
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--clear-halt", metavar="NAME")
    ap.add_argument("--mark-closed", metavar="MINT", help="offline: reconcile a position closed by hand; needs --sig and the lock (refused while the unit runs)")
    ap.add_argument("--sig", metavar="SIGNATURE", help="the transaction that sold --mark-closed's mint")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    ap.add_argument("--rpc-env", metavar="VARNAME", help="dry run only: the NAME of an environment variable holding the RPC URL, for a user who "
                    "cannot read the env file. The URL itself never goes on the command line. Refused with --live")
    args = ap.parse_args(argv)
    if args.rpc_env and args.live:  # before the config, the lock, the key or any RPC object
        ap.error("--rpc-env is allowed with --dry-run only: live reads HELIUS_API_KEY from the environment and never takes a URL")
    if args.rpc_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.rpc_env):
        ap.error("--rpc-env takes the NAME of an environment variable, not a URL")  # a URL here would already have put a key in argv; it is not echoed
    cfg = json.loads(Path(args.config).read_text())
    try:
        H5Limits.from_config(cfg)
    except ValueError as exc:
        raise SystemExit(f"limits refused: {exc}") from None
    if args.status:
        print(status_report(cfg))
        return 0
    if args.mark_closed:
        if not args.sig:
            ap.error("--mark-closed needs --sig SIGNATURE")
        url = (os.environ.get(args.rpc_env) or "").strip() if args.rpc_env else None
        if args.rpc_env and not url:
            raise SystemExit(f"--rpc-env {args.rpc_env}: that environment variable is not set")
        rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(url, args.env_file)), rps=float(cfg.get("rps", 8.0)), max_rps=H5_MAX_RPS)
        return mark_closed(cfg, args.mark_closed, args.sig, rpc)
    mode, warn = pe.resolve_mode(cfg.get("mode", DRYRUN), args.live and not args.dry_run)
    if args.clear_halt:
        return clear_halt(cfg, args.clear_halt, mode)
    if warn:
        print(f"h5_executor WARNING {warn}", flush=True)
    lock_fd = acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")
    url: str | None = None
    try:
        oracle = JsonlPickOracle(cfg["pick_file"]) if cfg.get("pick_file") else None
        if mode == LIVE:
            why = start_refusal(cfg)
            if why:
                print(f"h5_executor ALERT startup_refused {why}", flush=True)
                return 2
            rc = pe.startup_rpc_env_check(True)
            if rc:
                return rc
            pl.harden_process()  # before the key is read
            if "key_path" in cfg:
                raise SystemExit("live mode has no key path override: the key comes from the systemd credential only")
            if not (os.environ.get("HELIUS_API_KEY") or "").strip():
                print("h5_executor ALERT startup_refused rpc_key_missing", flush=True)
                return 2
            kp = pl.load_probe_key()
            rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, args.env_file, use_env_file=False)), rps=float(cfg.get("rps", 8.0)), max_rps=H5_MAX_RPS)
            ex = H5Executor(rpc, cfg, kp, pick_oracle=oracle)
        else:
            url = (os.environ.get(args.rpc_env) or "").strip() if args.rpc_env else None
            if args.rpc_env and not url:
                raise SystemExit(f"--rpc-env {args.rpc_env}: that environment variable is not set")
            rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(url, args.env_file)), rps=float(cfg.get("rps", 8.0)), max_rps=H5_MAX_RPS)
            ex = H5Executor(rpc, cfg, None, pick_oracle=oracle, rpc_label=url_label(url))
        shown = f" rpc={url_label(url)}" if url else ""
        print(f"h5_executor mode={ex.run_mode} user={ex.user}{shown} paths={ex.paths} limits={ex.h5}", flush=True)
        if args.once:
            ex.tick()
            return 0
        ex.run_loop()
        return 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
