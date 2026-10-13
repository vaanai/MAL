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
                  sell_token_raw (post-trade base), v_print, gap (this pool saw a feed gap -> refused), synthetic (must be the literal false:
                  the detector classified the pool as NOT a synthetic migration (no pump PostCompleteBuyEvent; DEC-024 Am.2: it reads the
                  completing tx or the migrate tx, and a pool whose tx it cannot find is unclassified, so it gets `excluded`); absent,
                  null, true or a non-bool is refused as synthetic_unconfirmed), synthetic_src (must be "rpc": a plain pool is only ever confirmed by an RPC read; ws, pre_event_binary, absent or junk are refused too)
  type "excluded" the detector classified a pool as synthetic (or could not classify it): counted, never traded, never an alert; a later
                  trigger on the same pool or mint is refused as excluded_pool
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

H5-BOOSTCLOCK v2 exit (DEC-028 Option B, EXP-026 rule C10). OFF in this build: EXIT_MODE is a compiled-in constant ("v1"), never config
(DEC-028 s3.2, s5 item 3). With it off nothing below runs and v1 is unchanged: no slice is kept, no v2 field is written, no v2 halt exists.
Set to "v2" only by the pinned install the manager names for Option B (after v1 halts on BOOST timing); live v2 also needs EXP-026 in the
deployed tree and DEC-028's OWNER_OVERRIDE_V2_CONFIRMED line filled. In v2 mode:
  type "boost_slice"  (or flat h5_boost_slice_v1) one BOOST buy in a tracked pool: mint, pool, slot, tx_index (replay tape) and/or signature
                      (the live feed has no block position: the shadow writes tx_index null there), event_index, lamports (or sol),
                      block_time (s) or block_time_ms, t_recv_ms, boost_src ("pda" or "event_authority"; anything else is not used)
  Each position opened in v2 mode is stamped exit_rule H5-BOOSTCLOCK-v2 and leaves by that rule: an early sell at the rule's exit trigger slot
  (v2_cad_exit in this file, the projected last BOOST slice minus 10 s, from slices RECEIVED and usable 1.35 s after their slot), never later
  than v1's s0 + 330 s timer, which stays armed. No slices, fewer than 3, a feed gap, a restart or process start after the pool's s0, or the
  pool's slices evicted: no early exit (330 s).
  Halts (DEC-028 s4): v2_sell_after_boost_gt_15pct (rolling 20; it replaces v1's cumulative rule (b), boost_before_sell_gt_15pct, which is
  report-only in v2 mode), v2_plain_median_lt_330 (completed day, >= 30 plain pools),
  v2_projection_inputs (completed day: median BOOST total outside [17.5, 17.7] SOL or median slice interval outside [6, 24] s over >= 30 plain
  completed pools), late sells (> 3 s after the v2 exit trigger, or after s0 + 335 s), v2_slice_stream_stale (> 2 positions a day),
  v2_slice_lag_gt_1_35s (median receive lag over the day's traded pools). The 335 s and 337 s day-median lines are report-only in v2 mode.
  C1-NF (tools/c1nf_executor.py) pins its own exit mode to v1 (EXIT_MODE_PIN): this switch never reaches it.
"""

from __future__ import annotations

import argparse
import base64
import bisect
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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence
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
from tools.cap_pick_oracle import PickOracle

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
S0_ANNOUNCE_LAG_MAX = 2  # slots between the pool's CreatePool and the first print we call s0 (live: p50 0, p90 1, p99 4440)
BAD_INTENT_ALERT_N, BAD_INTENT_WINDOW_MS = 5, 3_600_000  # more than 5 malformed or unusable VALUES in trigger records in an hour raises an alert
# A trigger record that lacks a key this executor needs is a shadow-schema mismatch (a detector that was not restarted on the new head: the schema
# string stays h5_shadow_v1). The first one alerts at once, then at most once per SCHEMA_ALERT_WINDOW_MS. The keys are those Engine._fire of
# tools/h5_shadow.py (#477) writes and this file reads; the others it writes (outcomes, ladders, prices) are not needed here.
SHADOW_REQUIRED_KEYS = (
    "mint", "pool", "s0", "s0_t_recv_ms", "slot", "sps", "block_time", "t_recv_ms", "t_detect_ms", "q_trigger_sol", "v_print", "v_missing",
    "base_pre", "sell_token_raw", "boost_spent_sol", "base_breaks_unresolved", "base_breaks_unresolved_settled", "s0_minus_announced_slots",
    "s0_reanchored_slots", "sps_span_s", "gap",
)
SCHEMA_ALERT_WINDOW_MS = 600_000
# Synthetic migrations (owner-approved, quant-proof ruled): a pool classified as a synthetic migration (a pump PostCompleteBuyEvent), or not
# classifiable, gets no buy.
# The detector classifies at decision time and writes `synthetic: false` on a trigger it lets through; anything else is refused here, so a
# record from a detector that predates the classifier can never be traded (fail closed). This executor does not re-derive the class; see the
# PR that added this for why (no pool-naming record arrives before the trigger, so a confirmation would sit on the buy path).
SYNTHETIC_UNCONFIRMED = "synthetic_unconfirmed"
EXCLUDED_POOL = "excluded_pool"
EXCLUDED_REASONS = ("synthetic", "unclassified")  # the detector's `excluded` record reasons; anything else is counted as "other"
EXCLUDED_KEEP = 4096  # pools and mints remembered from `excluded` records (a refusal guard against a conflicting trigger), oldest dropped first
SYNTHETIC_LOGGED_KEEP = 2048  # pools for which a synthetic_unconfirmed ledger row was already written (one row per pool)
SYNTHETIC_SRC_RE = re.compile(r"[A-Za-z0-9_.\-]{1,32}")
T0_ALERT_STOPS = frozenset({"total_loss_stop", "daily_loss_stop", "max_trades_day", "max_attempts", "max_days", "end_instant"})  # a refusal at T0 alerts
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

# --- H5-BOOSTCLOCK v2 exit (DEC-028 Option B, owner pre-authorization 2026-10-11, MiScusi notebook n_soP9VudXSnG4Jw). OFF: EXIT_MODE is v1. ----
EXIT_MODE_V1, EXIT_MODE_V2 = "v1", "v2"
EXIT_MODE = EXIT_MODE_V1  # compiled-in, never config (DEC-028 s3.2, s5 item 3). Only the pinned Option B install the manager names sets "v2".
RULE_ID_V2 = "H5-BOOSTCLOCK-v2"
V2_BUDGET_LAMPORTS = 17_585_000_000  # BOOST's TWAP budget the projection assumes (17.585 SOL)
V2_DONE_LAMPORTS = 50_000_000  # budget left below this: the last slice has been seen (0.05 SOL)
V2_OFF_S = 10.0  # exit this long before the projected last BOOST slice (C10; tools/h5_shadow.py BX10_OFF_S)
V2_OBS_S = 1.35  # a slice at pool time t is usable for a decision at tau >= t + this (1.9 s entry latency - 0.55 s send-to-land; BX10_OBS_S)
V2_KMIN = 3  # slices needed before a projection exists (BX10_KMIN)
V2_CAP_S = HOLD_S  # v1's exit (s0 + 330 s); the rule never exits later
EXP026_FILE = "EXP/EXP-026-h5-boostclock-v2-prereg.md"  # live v2 needs the frozen rule in the deployed tree ...
DEC028_FILE = "DEC/DEC-028-h5-v2-canary.md"  # ... and the owner's override line filled in this file
DEC028_OVERRIDE_RE = re.compile(r"^[ \t]*OWNER_OVERRIDE_V2_CONFIRMED:[ \t]*\S", re.M)  # text on the same line (the draft's line is empty)
SCHEMA_BOOST_SLICE = "h5_boost_slice_v1"
V2_BOOST_SRC = ("pda", "event_authority")  # the per-pool vault identity; a behavioural guess is never used for a live exit
V2_SELL_AFTER_BOOST_N, V2_SELL_AFTER_BOOST_FRAC = 20, 0.15  # s4.1(a): > 15% of a rolling 20 paired sells at or after the last slice
V2_PLAIN_MEDIAN_MIN_S, V2_PLAIN_MIN_POOLS = 330.0, 30  # s4.1(b): a completed day's plain-pool median below 330 s over >= 30 plain pools
V2_TOTAL_SOL_LO, V2_TOTAL_SOL_HI = 17.5, 17.7  # s4.1(d): completed day, >= 30 plain completed pools: median BOOST total outside this ...
V2_INTERVAL_LO_S, V2_INTERVAL_HI_S = 6.0, 24.0  # ... or median slice interval outside this
V2_LATE_AFTER_TRIGGER_S = 3.0  # s4.2: a sell landing more than this after its v2 exit trigger (or after s0 + 335 s) is late
V2_STALE_S, V2_STALE_MAX_PER_DAY = 45.0, 2  # s4.7: no slice for > 45 s while budget is left, on > 2 positions in a UTC day
V2_LAG_MEDIAN_MAX_S = 1.35  # s4.7: median slice receive lag (receive - block time) over the day's traded pools
# DEC-028 s4.1: reported (once a UTC day), not halts, in v2 mode. boost_before_sell_gt_15pct is v1's CUMULATIVE rule (b): s4.1(a)'s rolling 20
# (v2_sell_after_boost_gt_15pct) replaces it, and the counters continue from v1's run, so v1's tally (the likely Option B trigger) would
# otherwise re-latch on v2's first paired sell, good or bad.
V2_REPORT_ONLY = frozenset({"boost_median_lt_335", "boost_median_lt_337_twice", "boost_before_sell_gt_15pct"})
V2_SLICE_POOLS_KEEP = 512  # pools whose slices are held (the newest; slices before the buy count, so every tracked pool is kept)
V2_EVICTED_KEEP = 4_096  # evicted pools remembered (each then has no early exit)
V2_LAG_KEEP = 5_000


# --- the H5-BOOSTCLOCK v2 rule (EXP-026 section 2, iter-r2 cell C10). A port of the frozen bc_rule.py (sha256 6b3de49e...2651d): project_E ->
# v2_project_e, cad_exit -> v2_cad_exit; the same steps as tools/h5_shadow.py's report-only bx10 (project_e, bx10_exit). The tests pin all three
# to each other (tools/test_h5_executor_v2.py). Kept in this file, not in a new module, so the pinned H5 and C1-NF installers' import closures do
# not change. Pure: pool-clock seconds after s0 (t = (slot - s0) * sps), lamports, no price.

def v2_project_e(t: Sequence[float], a: Sequence[float]) -> float:
    """Projected last BOOST slice time (s after s0) from the k >= 2 slices seen so far (times t, lamports a)."""
    k = len(t)
    d = sorted(t[i] - t[i - 1] for i in range(1, k))
    m = len(d)
    med = d[m // 2] if m % 2 else (d[m // 2 - 1] + d[m // 2]) / 2
    tot = float(sum(a))
    rem = max(0.0, V2_BUDGET_LAMPORTS - tot)
    if rem < V2_DONE_LAMPORTS:
        return t[-1]
    return t[-1] + math.floor(rem / (tot / k)) * med


def v2_cad_exit(t: Sequence[float], a: Sequence[float], tmin: float, cap: float = V2_CAP_S, off: float = V2_OFF_S, obs: float = V2_OBS_S,
             kmin: int = V2_KMIN) -> tuple[float, bool, int, float | None]:
    """tau* = inf{tau >= tmin : k(tau) >= kmin and tau >= P_E(k(tau)) - off}, capped at cap. t sorted ascending (s after s0).
    Returns (tau*, fired before the cap, k slices used, projection used or None)."""
    n = len(t)
    if n < kmin:
        return cap, False, n, None
    ob = [x + obs for x in t]
    k = max(kmin, bisect.bisect_right(ob, tmin))
    while k <= n:
        start = max(tmin, ob[k - 1])
        end = ob[k] if k < n else math.inf
        proj = v2_project_e(t[:k], a[:k])
        cand = max(start, proj - off)
        if cand < end:
            return (cand, True, k, proj) if cand < cap else (cap, False, k, proj)
        if end >= cap:
            return cap, False, k, proj
        k += 1
    return cap, False, n, None


def v2_v1_exit_slot(s0: int, sps: float, x_slot: int) -> int:
    """C = max(s0 + round(330 s / sps), X + 1): v1's exit trigger slot (python round, as the frozen scorer)."""
    return max(s0 + int(round(V2_CAP_S / sps)), x_slot + 1)


def v2_exit_trigger_slot(s0: int, sps: float, x_slot: int, tau: float, fired: bool) -> int:
    """The rule's exit trigger slot: early -> min(max(s0 + ceil(tau* / sps - 1e-9), X + 1), C); no early exit -> C."""
    c = v2_v1_exit_slot(s0, sps, x_slot)
    if not fired:
        return c
    return min(max(s0 + math.ceil(tau / sps - 1e-9), x_slot + 1), c)

# --- exit timing and entry age (review of 86a224b) ----------------------------------------------------------------------------
SPS_TOLERANCE = 0.01  # a trigger whose sps is more than 1% from the one we measure is refused; an open plan is rebuilt past the same drift
SPS_WINDOW_MS = 300_000  # trailing window of our own getSlot observations
SPS_MIN_SPAN_MS = 60_000  # ... and the two points compared must be at least this far apart
HIST_KEEP_MS = 900_000
REPLAN_TOLERANCE = 0.001  # an open plan is rebuilt when the measured slot rate is more than 0.1% from the plan's (3.3 s over 330 s is the limit)
EARLY_TOLERANCE_MS = 1_000  # a slot-timed stage never fires more than this before its wall-clock time since s0
S0_RECV_LATE_MS = 1_500  # an s0 that reached the detector later than the trigger print did, relative to the chain, by more than this: refuse
S0_ANCHOR_REASONS = frozenset({"s0_recv_late", "s0_unverifiable", "s0_before_history"})
S0_ANCHOR_ALERT_N = 3  # more than this many such refusals in an hour raises an `s0_anchor_refusals` alert
SUPERSEDE_MS = 2_000  # a sell with no status this long after it was first sent is superseded by the next rung
BUY_REBROADCAST_MS = 3_000  # a buy is rebroadcast for this long after the decision, then left to expire
LATE_SELL_MIN_N = 1  # literal reading of "more than 5% of sells": judged from the first landed sell. A code constant, not config.
ENTRY_LATE_S = 5.0  # a buy landing later than this after the trigger print is out of the rule (the binding leg is 1.9 s)
ENTRY_SEND_ALLOWANCE_S = 1.5  # nominal time left for build, send and landing after the pre-send checks. One measured path (2026-10-12): decision-to-send 221 ms, send-to-confirm 727 ms. The chain check rounds up, so it keeps ceil(5.0/sps) - ceil(3.5/sps) slots (6 slots = 1.31 s at sps 0.2187)
PRESEND_MAX_TRIGGER_AGE_MS = int((ENTRY_LATE_S - ENTRY_SEND_ALLOWANCE_S) * 1000)  # 3_500: an older trigger could not land inside ENTRY_LATE_S, so no buy is sent (stricter only; config may shorten it)
LATE_SELL_S = 335.0  # a sell landing after s0 + this is late
LATE_SELL_FRAC = 0.05  # more than 5% of our landed sells late -> halt
LANDING_MEDIAN_MAX_S = 3.0  # median trigger-to-landing over the last LANDING_WINDOW fills above this -> halt
LANDING_WINDOW = 10
PRE_UNLINKED_MIN_LANDED = 20  # a trigger whose predecessor print may be missing (see pre_unlinked) is traded, but if more than this share of
PRE_UNLINKED_MAX_SHARE = 0.15  # our landed buys, from 20 landed on, are such triggers, the share is a halt (pre_unlinked_share)

# --- EXP-022 seal guard ---------------------------------------------------------------------------------------------
SEAL_START_MS = 1792112400000  # 2026-10-16T01:00:00Z
ORACLE_EARLIEST_MS = 1792116000000  # 2026-10-16T02:00:00Z: the DEC-016 FINAL is written about now, not before
SEAL_END_DEFAULT_MS = 1793930400000  # 2026-11-06T02:00:00Z. Config may move the end later, never earlier.
SEAL_REASONS = frozenset({"seal_window_no_oracle", "seal_pick", "seal_oracle_error"})  # counted, never logged per mint
# An H5 trigger on a pick is outcome-linked (the pool drained on a CAP-PICK pick): seal_pick is counted IN THIS PROCESS ONLY, never in the
# refusal_counts ledger row, the counters file (seal_skips), the seal_count log, an alert or a log line, before EXP-022's read ends
# (quant-proof (d) on #509/#540; DEC-024 Amendment 6 item 5; the #368 precedent: lumping a sealed count with others does not cure it).
SEAL_PICK_IN_PROCESS_ONLY = "seal_pick"
PICK_STALE_S = 60.0  # DEC-024 s6: a pick feed missing or stale for more than 60 s halts buys (the oracle answers None: seal_oracle_error). Code constant.
PICK_FEED_ALERT_MS = 600_000  # the pick_feed_unavailable alert, at most once per 10 minutes (no mint, no trigger: a feed fact only)

# --- limits: code maxima, config can only lower (floors: config can only raise) ---------------------------------------
H5_DEFAULT = {
    "stake_lamports": 20_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 80_000_000,
    "total_loss_lamports": 120_000_000, "max_attempts": 150, "max_days": 14, "buy_priority_lamports": 55_000,
    "sell_priority_lamports": 55_000, "escalated_priority_lamports": 150_000, "entry_tolerance_bps": 1500,
}
# max_days 14 = DEC-024 section 4's cap ("at most 14 days from the first send"); it was 10 until PR #591 (DEC-024 Am.6 item 8 record, 2026-10-11).
# It counts from first_attempt_ms, which is at or after the first send, so the live end_ms (the cap) binds first.
H5_MAX = dict(H5_DEFAULT)  # the defaults ARE the maxima (review of 86a224b): config can only tighten. Loosening is a reviewed code change.

# --- the scale ladder (owner decision) -----------------------------------------------------------------------------------------
# The five limits that scale with the stake are a code-constant table; the active tier is read from a root-owned file before every buy. Config
# can still only tighten within the active tier. T0 equals H5_DEFAULT. Lamports (T0 0.02 / 2 / 30 / 0.08 / 0.12 SOL, T1 0.10 / 3 / 40 / 0.40 /
# 0.60, T2 0.30 / 3 / 40 / 1.20 / 1.80).
# THE LADDER ENDS AT T2 (0.30 SOL). Do not add a tier above 0.30: it needs a new price-impact check that uses LIVE-FILL evidence (the
# check behind T2 is a replay model; see T2_IMPACT_OK), and a reviewed code change. tools/test_h5_executor_tiers.py pins the keys and the top stake.
TIERS: dict[str, dict[str, int]] = {
    "T0": {"stake_lamports": 20_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 80_000_000, "total_loss_lamports": 120_000_000},
    "T1": {"stake_lamports": 100_000_000, "max_open": 3, "max_trades_per_day": 40, "daily_loss_lamports": 400_000_000, "total_loss_lamports": 600_000_000},
    "T2": {"stake_lamports": 300_000_000, "max_open": 3, "max_trades_per_day": 40, "daily_loss_lamports": 1_200_000_000, "total_loss_lamports": 1_800_000_000},
}
assert all(H5_DEFAULT[k] == v for k, v in TIERS["T0"].items())
TIER_WALLET_FRAC = 0.35  # the total stop is also capped at this share of the wallet balance measured when the tier started
# /data/mal/hunt-1008/h5-work/IMPACT.md (quant-proof, 2026-10-09T04:14Z, report-only, replay model R1, blocks already read): 0.30 SOL is OK on impact
# grounds, but only in September-depth pools. Latest blocks (09-18..25) primary flat at 0.30: +5.144%, CI90 lo +2.438; with pools half as deep
# (alpha 0.5) primary pressure lo +0.810. 0.50 fails in thinner pools (alpha 0.5: primary press lo -0.191), so 0.30 is the ceiling. This is a
# capacity check, not gate evidence. If live fills show more impact than the R1 model, size down (IMPACT.md suggests 0.20).
T2_IMPACT_OK = True  # the impact check is done for 0.30 only; the guard stays in handle_trigger (refuses T2 with t2_impact_unchecked when False)
TIER_FILE_PATH = Path("/etc/mal-h5/TIER")  # contains just T0, T1 or T2; root:root 0644 like LIVE_OK. Missing or invalid means T0.
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
    max_trigger_age_ms: int = PRESEND_MAX_TRIGGER_AGE_MS
    pool_cache_ttl_ms: int = 600_000
    buy_cu_limit: int = tx.DEFAULT_BUY_CU_LIMIT
    sell_cu_limit: int = tx.DEFAULT_SELL_CU_LIMIT
    track_volume: bool = True  # the probe's proven live shape; set false only after a dry-run simulate comparison
    gap_hold_ms: int = 20_000  # a detector `gap` record refuses buys for this long (#477 also flags the pools open at the time)
    dry_run_balance_lamports: int = 250_000_000  # dry run has no wallet: balance guard runs against this
    end_ms: int | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any], tier: str = "T0") -> "H5Limits":
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r}")
        defaults, maxima = {**H5_DEFAULT, **TIERS[tier]}, {**H5_MAX, **TIERS[tier]}  # the tier's values are the defaults AND the maxima
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
        if "exit_mode" in cfg:  # DEC-028 s5 item 3: the exit rule is compiled in (EXIT_MODE), never a runtime config
            raise ValueError("exit_mode is not configurable")
        for key in fixed:
            if key in cfg:
                raise ValueError(f"{key} is not configurable")
        kw: dict[str, Any] = {}
        for key, default in defaults.items():
            kw[key] = defaults[key] if key in fixed else num(key, default, 1, maxima[key], as_int=(key != "max_days"))
        kw["max_days"] = num("max_days", defaults["max_days"], 0.01, maxima["max_days"], as_int=False)
        kw["wallet_floor_lamports"] = int(max(MIN_WALLET_FLOOR_LAMPORTS, num("wallet_floor_lamports", DEFAULT_WALLET_FLOOR_LAMPORTS, 0, 10**12)))
        kw["send_lead_ms"] = num("send_lead_ms", 500, 0, 3_000)
        kw["exit_land_offset_s"] = num("exit_land_offset_s", 0.0, 0.0, 1.0, as_int=False)
        kw["deadline_s"] = num("deadline_s", 400.0, ESCALATE_S, 400.0, as_int=False)
        kw["arm_lead_ms"] = num("arm_lead_ms", 2_000, 500, 10_000)
        kw["rebroadcast_ms"] = num("rebroadcast_ms", 400, 200, 5_000)
        kw["status_poll_ms"] = num("status_poll_ms", 500, 250, 5_000)
        kw["slot_poll_ms"] = num("slot_poll_ms", 200, 100, 2_000)
        kw["slot_resync_ms"] = num("slot_resync_ms", 20_000, 1_000, 60_000)
        kw["max_trigger_age_ms"] = num("max_trigger_age_ms", PRESEND_MAX_TRIGGER_AGE_MS, 1_000, PRESEND_MAX_TRIGGER_AGE_MS)
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
    trigger_recv_ms: int | None = None  # the detector's receive time of the trigger print: the feed's lag at this moment, for the s0 check
    base_breaks: int | None = None  # raw per-pool counts from the detector, ledgered (they count reorders too; base_breaks_unresolved decides)
    slot_regress: int | None = None
    base_breaks_unresolved: int | None = None
    base_breaks_unresolved_settled: int | None = None  # the gate (0 = no missed print among the settled prints)
    s0_reanchored_slots: int | None = None  # ledgered: how far s0 moved down when an earlier print arrived late (before any trigger)
    s0_minus_announced_slots: int | None = None
    synthetic: bool | None = None  # the detector's class of the pool: only the literal False is tradable (None = never confirmed -> refused)
    synthetic_src: str | None = None  # how the detector got it: only "rpc" is tradable (SYNTHETIC_SRC_OK); ledgered on the decision row
    gate_path: str | None = None  # "tipauth" only for a trigger admitted by the tipauth gate; None (and never written anywhere) on the websocket path

    @property
    def pre_unlinked(self) -> bool:
        return pre_unlinked(self.base_breaks_unresolved, self.base_breaks_unresolved_settled)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        if d["gate_path"] is None:
            del d["gate_path"]  # old-path rows and plans stay byte-identical
        return d

    def log_fields(self) -> dict[str, Any]:
        return {**{k: v for k, v in asdict(self).items() if k != "mint" and (k != "gate_path" or v is not None)}, "trigger_pre_unlinked": self.pre_unlinked}  # the ledger row carries the mint itself


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def pre_unlinked(unresolved: Any, settled: Any) -> bool:
    """The detector's settled gate cannot see a missing print DIRECTLY before the trigger print (the trigger is the newest print, so its own pre
    is never checked). What it can show is the unsettled count: >= 1 with a settled count of 0 means a break that may be that predecessor, or a
    print still in flight. Manager ruling for the canary: such a trigger is traded, but marked (`trigger_pre_unlinked` on the decision row), counted
    per hour, left out of the sim-match comparison in h5_reconcile, and a halt if it is more than 15% of landed buys (from 20 landed)."""
    return _is_int(unresolved) and unresolved >= 1 and _is_int(settled) and settled == 0


SYNTHETIC_SRC_OK = "rpc"  # the only source that confirms a plain pool: live, "not synthetic" can only come from an RPC read; the ws path only ever marks synthetic


def synthetic_confirmed(row: Any) -> bool:
    """The class gate, fail closed: a trigger is tradable only if the record says `synthetic` is the literal False (the detector classified the
    pool as NOT a synthetic migration) AND `synthetic_src` is exactly "rpc". Absent, null, true, 0, "false" and every other value are not a
    confirmation; nor is any other source (absent, "ws", "pre_event_binary", junk)."""
    return isinstance(row, dict) and row.get("synthetic") is False and row.get("synthetic_src") == SYNTHETIC_SRC_OK


def _synthetic_src(row: dict[str, Any]) -> str | None:
    v = row.get("synthetic_src")
    return v if isinstance(v, str) and SYNTHETIC_SRC_RE.fullmatch(v) else None  # a short name-shaped string or nothing: it is only ledgered


def parse_trigger(row: Any) -> tuple[H5Trigger | None, str | None]:
    """(trigger, None) for a good h5_intent_v1 row, (None, reason) for a malformed one, (None, None) for any other schema.
    Only the whitelisted fields are read; anything else in the row is dropped. A row whose `synthetic` is not the literal False is refused
    with SYNTHETIC_UNCONFIRMED before anything else is looked at."""
    if not isinstance(row, dict) or row.get("schema") != SCHEMA_INTENT:
        return None, None
    if not synthetic_confirmed(row):
        return None, SYNTHETIC_UNCONFIRMED
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
                     row["s0_wall_ms"] if _is_int(row.get("s0_wall_ms")) else None, row["block_time"] if _is_int(row.get("block_time")) else None,
                     row["trigger_recv_ms"] if _is_int(row.get("trigger_recv_ms")) else None,
                     synthetic=False, synthetic_src=_synthetic_src(row)), None


# --- tipauth gate (DEC-024 phase 2, PR #610 quant-proof rounds 1-3). OFF by default; nothing here runs unless the config says so. -------------
TIPAUTH_ENABLE_REF = "DEC-024-Am8"  # the only value of config `tipauth_gate` that enables the path (a DEC amendment reference, like H5_LOOK2_OBSERVED)
GATE_PATH_TIPAUTH = "tipauth"
TIPAUTH_DROPPED_MAX = 5  # C4: 1 <= dropped_n <= 5. May only be lowered.
TIPAUTH_STAKE_LAMPORTS = 20_000_000  # ruling D2: the class is 0.02 SOL at every tier
TIPAUTH_LOSS_BUDGET_LAMPORTS = 40_000_000  # ruling D4: class realized at or below -0.04 SOL switches the path off (latched)
TIPAUTH_LOOKS = (25, 50, 100)  # ruling D5: looks at this many landed tagged fills


def _int0(v: Any) -> bool:
    return _is_int(v) and v == 0


def tipauth_gate(rec: Any, rule: str = "v1") -> tuple[bool, str]:
    """C1-C6 of quant-proof's PR #610 round-3 comment (6121123397), on a shadow TRIGGER record. (ok, reason); reason is "ok" or `tipauth:<cond>`.
    Pure and fail closed: a missing key, a null, a bool where an integer is wanted, a non-ok status or a non-dict is NOT a pass, each with its own
    reason. Only the websocket value >= 1 case is asked about: a value of 0 never reaches this function (the websocket gate passes it as today)."""
    if not isinstance(rec, dict):
        return False, "tipauth:not_a_record"
    ws = rec.get("base_breaks_unresolved_settled")
    if not _is_int(ws) or ws < 1:
        return False, "tipauth:ws_not_ge1"
    if rec.get("gap") is not False:  # C1
        return False, "tipauth:gap"
    bu, bt = rec.get("base_union"), rec.get("base_tipauth")
    if not isinstance(bu, dict):
        return False, "tipauth:union_missing"
    if not isinstance(bt, dict):
        return False, "tipauth:tipauth_missing"
    if bu.get("status") != "ok":
        return False, "tipauth:union_status"
    if bt.get("status") != "ok":
        return False, "tipauth:tipauth_status"
    if not _int0(rec.get("base_breaks_unresolved_settled_tipauth")):
        return False, "tipauth:value_not_zero"
    if not _int0(bu.get("tip_only")):  # C2
        return False, "tipauth:tip_only"
    if not _int0(bu.get("both_diff")):
        return False, "tipauth:both_diff"
    if not _int0(bt.get("dropped_boost_n")):  # C3
        return False, "tipauth:dropped_boost"
    src = rec.get("boost_src")
    if not isinstance(src, str) or src == "behavioural":
        return False, "tipauth:boost_src"
    dn = bt.get("dropped_n")  # C4
    if not _is_int(dn) or not 1 <= dn <= TIPAUTH_DROPPED_MAX:
        return False, "tipauth:dropped_n"
    if not _int0(bt.get("kept_ws_only_sig_on_tip")):
        return False, "tipauth:kept_ws_only_sig_on_tip"
    if not _int0(bt.get("orphaned_by_drop_n")):  # C5 (a)
        return False, "tipauth:orphaned_by_drop"
    if not _int0(bt.get("tipauth_nosettle")):  # C5 (b)
        return False, "tipauth:nosettle"
    first, lo = bt.get("dropped_first_slot"), bt.get("cover_lo")
    if not _is_int(first) or not _is_int(lo) or not first > lo:  # C5 (c)
        return False, "tipauth:dropped_first_slot"
    if rule != EXIT_MODE_V1:  # C6
        return False, "tipauth:rule_not_v1"
    return True, "ok"


def parse_shadow_trigger(row: Any, variant: str = "pv", tipauth: str | None = None) -> tuple[H5Trigger | None, str | None]:
    """A `type: "trigger"` record of tools/h5_shadow.py (PR #477) -> H5Trigger. Only the chosen Q variant is acted on ("pv": quote +
    the print's own V, the primary; "fv" is the rule's literal fixed V and is ignored here). Mapping, all from fields #477 writes:
      s0 -> s0_slot, slot -> trigger_slot, q_trigger_sol * 1e9 -> q_lamports (post-trade Q of that variant),
      base_pre + sell_token_raw -> base_reserve (a sell adds its tokens to the pool), v_print -> v_lamports, t_detect_ms -> decision_ms,
      gap -> gap, synthetic (the literal false) -> synthetic, synthetic_src -> synthetic_src. (None, None) for any other record or the other variant.
    `tipauth` is the tipauth path's availability, decided by the caller: None = the path is OFF (every decision here is exactly what it was without
    the argument); "" = on and available; any other string = on but unavailable, that string is the refusal. It only ever changes the answer for an
    integer base_breaks_unresolved_settled >= 1, and only from the refusal to an accept, through tipauth_gate()."""
    if not isinstance(row, dict) or row.get("type") != "trigger" or row.get("variant") != variant:
        return None, None
    if row.get("suppressed"):  # the sealed stub #477 writes from 2026-10-16T01Z: type, variant, pool, slot, suppressed ... and nothing to trade on
        return None, "bad_intent:suppressed"
    missing = [k for k in SHADOW_REQUIRED_KEYS if k not in row]
    if missing:  # a schema mismatch, not a bad value: the caller alerts at once
        return None, f"bad_intent:missing_{missing[0]}"
    if not synthetic_confirmed(row):  # the class gate: a detector that does not say `synthetic: false` is never traded (the caller tells absent from bad)
        return None, SYNTHETIC_UNCONFIRMED
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
        # The gate is base_breaks_unresolved_settled: the order-independent count over the prints at least 2 slots behind the detector's
        # high-water mark, so a predecessor print still in flight is not read as a break. A pure reorder reads 0, a missed print >= 1. On the
        # 09-20 frozen triggers with a within-slot shuffle it passes 53/72, the unsettled field 13-15/72. Missing, null or anything but the
        # integer 0 refuses. The unsettled field and the raw base_breaks / slot_regress (which count reorders too) are ledgered, not gated.
        settled = row.get("base_breaks_unresolved_settled")
        gate_path = None
        if not _is_int(settled) or settled != 0:
            if tipauth is None or not _is_int(settled) or settled < 1:
                return None, "bad_intent:base_breaks_unresolved_settled"
            if tipauth:  # the path is on but unavailable (latched off, a look pending, not v1): the reason says which
                return None, tipauth
            ok, why = tipauth_gate(row)
            if not ok:
                return None, why
            gate_path = GATE_PATH_TIPAUTH
        unresolved = row.get("base_breaks_unresolved") if _is_int(row.get("base_breaks_unresolved")) else None
        reanchored = row.get("s0_reanchored_slots") if _is_int(row.get("s0_reanchored_slots")) else None  # s0 moved down by this many slots (ledgered)
        lag = row.get("s0_minus_announced_slots")  # how long after the pool's CreatePool the first print we call s0 came; null when never announced
        if not _is_int(lag) or not 0 <= lag <= S0_ANNOUNCE_LAG_MAX:
            return None, "bad_intent:s0_minus_announced_slots"  # a late "s0" is a pool we joined mid-life, or one whose first prints were lost
        span = row.get("sps_span_s")  # the detector's sps window; null only in a tape replay, which this executor never runs
        if isinstance(span, bool) or not isinstance(span, (int, float)) or not math.isfinite(span) or span < SPS_MIN_SPAN_S:
            return None, "bad_intent:sps_span"
        spent = row.get("boost_spent_sol")  # the rule's BOOST-remaining condition, re-checked
        if isinstance(spent, bool) or not isinstance(spent, (int, float)) or not math.isfinite(spent) or spent < 0 or spent >= BOOST_DONE_FRAC * BOOST_BUDGET_SOL:
            return None, "bad_intent:boost_spent"
        t, bad = parse_trigger({"schema": SCHEMA_INTENT, "mint": row["mint"], "pool": row["pool"], "s0_slot": row["s0"], "sps": row["sps"],
                                "trigger_slot": row["slot"], "q_lamports": int(round(q_sol * 1e9)), "base_reserve": base_pre + tok,
                                "v_lamports": v, "s0_wall_ms": row.get("s0_t_recv_ms"), "block_time": row.get("block_time"),
                                "trigger_recv_ms": row.get("t_recv_ms"),
                                "decision_ms": row["t_detect_ms"] if not isinstance(row["t_detect_ms"], float) else int(row["t_detect_ms"]),
                                "gap": row.get("gap"), "synthetic": row.get("synthetic"), "synthetic_src": row.get("synthetic_src")})
        if t is None:
            return None, bad
        raw = {k: row[k] for k in ("base_breaks", "slot_regress") if _is_int(row.get(k))}  # raw counts: ledgered on the decision row
        return replace(t, base_breaks_unresolved=unresolved, base_breaks_unresolved_settled=settled, s0_reanchored_slots=reanchored,
                       s0_minus_announced_slots=lag, **({"gate_path": gate_path} if gate_path else {}), **raw), None
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
    tier_state: dict[str, Any] = field(default_factory=dict)  # {tier, since_ms, wallet_lamports, realized_at_start}: the active tier, the wallet and the run's realized P&L when it started
    tier_attempts: int = 0  # buy attempts since the active tier started: what max_attempts caps. Reset at every tier_change.
    max_total_loss_lamports: int = 0  # the highest total stop of any tier entered in this run: the run-cumulative loss is checked against it
    landed_buys: int = 0  # buys that landed (a position opened), for the pre-unlinked share
    pre_unlinked_landed: int = 0  # ... of which the trigger was pre-unlinked (see pre_unlinked)
    tail_path: str | None = None  # the intents file being read, so a restart finishes it before it moves to the newest hour
    synthetic_unconfirmed: int = 0  # trigger records refused because `synthetic` was not the literal false (a detector without the classifier, or a bug)
    excluded: dict[str, int] = field(default_factory=dict)  # the detector's `excluded` records by reason (synthetic | unclassified | other): pools kept out
    v2: dict[str, Any] = field(default_factory=dict)  # v2 exit mode only (rolling sell-vs-BOOST pairs); empty and not written in v1
    tipauth: dict[str, Any] = field(default_factory=dict)  # tipauth class state (mints, n_landed, realized, looks, pause_look, latched); empty and not written until the path admits a trade

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
            for k in ("plain", "lag", "seen_lag"):  # v2 only: the raw per-pool lists; the day's medians stay
                (self.days[old].get("v2") or {}).pop(k, None)
        body = {k: v for k, v in self.__dict__.items() if k not in ("v2", "tipauth") or v}
        _atomic_json(path, {"schema": "h5_counters_v1", **body})

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
PINNED_PATH_KEYS = ("stop_file", "halt_file", "live_ok_file", "final_marker_file", "tier_file")  # no config override in live


def root_file_problem(p: Path) -> str | None:
    """None when `p` is a regular file, not a symlink, mode exactly 0644, owner and group root, in a directory owned by root that is not
    group- or other-writable; all by lstat and O_NOFOLLOW (the same inode is checked that is opened). 'missing' when absent, 'unsafe' for anything
    else. Used for LIVE_OK and TIER: mal-live opens them, so they must be readable by it, and nobody but root may be able to write them."""
    try:
        dst, lst = os.lstat(p.parent), os.lstat(p)
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return "missing"
    except OSError:  # ELOOP for a symlink, EACCES, ENOTDIR ...
        return "unsafe"
    try:
        fst = os.fstat(fd)
    finally:
        os.close(fd)
    ok = (stat.S_ISDIR(dst.st_mode) and dst.st_uid == LIVE_OK_UID and not dst.st_mode & 0o022
          and stat.S_ISREG(lst.st_mode) and stat.S_ISREG(fst.st_mode) and (lst.st_dev, lst.st_ino) == (fst.st_dev, fst.st_ino)
          and fst.st_uid == LIVE_OK_UID and fst.st_gid == LIVE_OK_GID and stat.S_IMODE(fst.st_mode) == LIVE_OK_MODE)
    return None if ok else "unsafe"


def live_ok_valid() -> str | None:
    """None when LIVE_OK passes root_file_problem. 'live_ok_missing' when absent, 'live_ok_unsafe' for anything else. The executor cannot create it."""
    why = root_file_problem(LIVE_OK_PATH)
    return None if why is None else f"live_ok_{why}"


def read_tier(path: Path, root_checks: bool) -> tuple[str, str | None]:
    """(tier, problem). Live: `path` must pass root_file_problem (the same checks as LIVE_OK). A dry run reads its own state_dir/TIER with no
    ownership checks. The content must be exactly T0, T1 or T2 (whitespace around it is ignored). Missing, unsafe, unreadable or invalid means
    T0, with the reason: the lowest tier is the fail-safe one."""
    if root_checks:
        why = root_file_problem(path)
        if why:
            return "T0", f"tier_file_{why}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return "T0", "tier_file_missing"
    except OSError:
        return "T0", "tier_file_unreadable"
    try:
        text = os.read(fd, 64).decode("ascii", "replace").strip()
    except OSError:
        return "T0", "tier_file_unreadable"
    finally:
        os.close(fd)
    return (text, None) if text in TIERS else ("T0", "tier_file_invalid")


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
    if EXIT_MODE != EXIT_MODE_V1:  # v1 (this build): nothing more is checked
        return v2_start_refusal(root)
    return None


def v2_start_refusal(root: Path) -> str | None:
    """Live v2 (DEC-028 Option B) also needs the frozen rule (EXP-026) in the deployed tree and the owner's override line in DEC-028."""
    if EXIT_MODE != EXIT_MODE_V2:
        return "exit_mode_unknown"
    try:
        exp = root / EXP026_FILE
        if not (exp.is_file() and not exp.is_symlink() and exp.stat().st_size > 0):
            return "exp026_missing"
        dec = root / DEC028_FILE
        if not (dec.is_file() and not dec.is_symlink() and DEC028_OVERRIDE_RE.search(dec.read_text())):
            return "dec028_override_missing"
    except OSError:
        return "v2_start_files_unreadable"
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

# tools.cap_pick_oracle.PickOracle reads the exporter's boolean feed ({"mint","pick","t_ms"} rows and heartbeats; no score, price or P&L ever
# reaches it) with two regexes, never as JSON. oracle(mint) -> True (a pick; sticky), False (decided, not a pick), None (undecided, no heartbeat
# within PICK_STALE_S, a missing file, or anything raised). _seal_reason refuses on True and on anything that is not a bool, so None is fail
# closed: "If the pick feed is missing or stale for more than 60 s, H5 buys halt" (DEC-024 s6).


def build_pick_oracle(cfg: dict[str, Any], now_ms: Callable[[], int] | None = None) -> PickOracle | None:
    """The EXP-022 pick oracle from the config: `pick_file` (the exporter's live booleans file, which carries the heartbeat) and the optional
    `pick_replay_files` (walk-2 replay booleans, OR-ed in). None when `pick_file` is not set: every buy in the seal window is then refused
    (seal_window_no_oracle). The staleness limit is PICK_STALE_S, a code constant; config cannot move it."""
    if not cfg.get("pick_file"):
        return None
    if not isinstance(cfg["pick_file"], str):
        raise ValueError("pick_file must be a path")
    replay = cfg.get("pick_replay_files") or []
    if not isinstance(replay, (list, tuple)) or not all(isinstance(p, str) and p for p in replay):
        raise ValueError("pick_replay_files must be a list of paths")
    return PickOracle(live=[cfg["pick_file"]], replay=list(replay), stale_s=PICK_STALE_S, now_ms=now_ms)


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
    # A subclass that trades its own rule pins its exit mode here, whatever H5's EXIT_MODE is (C1-NF pins v1: DEC-028 Option B is H5-only).
    EXIT_MODE_PIN: str | None = None

    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], keypair: Keypair | None, *,
                 now_ms: Callable[[], int] | None = None, pick_oracle: Callable[[str], bool | None] | None = None,
                 root: Path | None = None, rpc_label: str | None = None):
        self.h5cfg = cfg
        self.tier = "T0"  # the active tier (see the `h5` property); the file is read before every buy
        self._tier_limits: dict[str, H5Limits] = {}
        self._stake_cap: int | None = None  # set only while a tipauth-path trigger is handled (handle_trigger); None otherwise
        self._tier_problem: str | None = None
        self._stepdown_seen: set[tuple[str, str, str]] = set()
        self._t0_stop_seen: set[tuple[str, str]] = set()
        H5Limits.from_config(cfg)  # a bad config fails here, at T0
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
        self._tipauth_switch = cfg.get("tipauth_gate") == TIPAUTH_ENABLE_REF  # exactly the amendment reference; absent or anything else = OFF
        self._tipauth_mints = set(self.counters.tipauth.get("mints") or [])
        persisted = self.counters.tier_state.get("tier")
        if persisted in TIERS:
            self.tier = persisted  # a restart keeps the tier it was on; the file is re-read before the next buy
        self.final_marker = run_path(cfg, "final_marker_file", "FINAL_WRITTEN", not self.dry_run)
        self._tail_path = Path(self.counters.tail_path) if self.counters.tail_path else None  # a restart drains the file it was reading first
        seal_end = int(cfg.get("seal_end_ms") or SEAL_END_DEFAULT_MS)
        self.seal_end_ms = max(seal_end, SEAL_END_DEFAULT_MS)  # config may extend the seal, never shorten it
        self.pick_oracle = pick_oracle
        self.slots = SlotClock()
        self.pool_cache: dict[str, tuple[tx.PoolState, int]] = {}
        self.armed: dict[str, dict[str, Any]] = {}
        mode = EXIT_MODE if self.EXIT_MODE_PIN is None else self.EXIT_MODE_PIN
        if mode not in (EXIT_MODE_V1, EXIT_MODE_V2):
            raise SystemExit(f"EXIT_MODE {mode!r} is not v1 or v2")
        self.exit_mode = mode  # compiled in (DEC-028 s5 item 3); a position leaves by the rule stamped on it (exit_rule), whatever this is
        self._v2_slices: dict[str, dict[str, Any]] = {}  # v2 only: mint -> {"sl": {(slot, tx_index, event_index): (lamports, recv_ms, block_ms, seen_ms)}, ...}
        self._v2_recovered = set(self.state.open) | set(self.state.pending)  # positions from before this start: their earlier slices are gone
        self._v2_last_gap_ms = -1  # the last feed gap that lost coverage: a pool open across it has no early exit
        self._v2_reported: set[tuple[str, str]] = set()
        self._v2_start_ms = self.now_ms()  # slices are read from here on; a position needs its pool's slices since s0
        self._v2_evicted: dict[str, None] = {}  # pools whose held slices were dropped (V2_SLICE_POOLS_KEEP): a later list is partial
        self.feed_gap = False
        self._gap_until_ms = 0
        self.no_sps_pools: set[str] = set()  # pools the detector skipped for want of an sps: never traded
        self._bad_intent_ms: list[int] = []
        self._bad_intent_alert_ms = -BAD_INTENT_WINDOW_MS
        self._schema_alert_ms = -SCHEMA_ALERT_WINDOW_MS
        self._synthetic_alert_ms = -SCHEMA_ALERT_WINDOW_MS
        self.excluded_pools: dict[str, str] = {}  # pool and mint of every `excluded` record -> its reason (insertion order, EXCLUDED_KEEP newest)
        self._synthetic_logged: dict[str, None] = {}  # pools with a synthetic_unconfirmed ledger row already (one row per pool, SYNTHETIC_LOGGED_KEEP newest)
        self._anchor_refusal_ms: list[int] = []
        self._anchor_alert_ms = -BAD_INTENT_WINDOW_MS
        self._anchor_info: dict[str, Any] = {}
        self._counts_hour: int | None = None
        self._refusal_counts: dict[str, int] = {}
        self._accepted_hour = 0
        self._pre_unlinked_hour = 0
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
        self.seal_picks_in_process = 0  # SEAL_PICK_IN_PROCESS_ONLY: memory only, never saved, logged or alerted
        ts0 = self.counters.tier_state
        self._tier_state_legacy = bool(ts0.get("tier")) and "realized_at_start" not in ts0  # counters from before the tier baselines were stored
        self._seal_logged_ms = 0
        self._pick_alert_ms = -PICK_FEED_ALERT_MS
        self.save()  # state and counters exist on disk before the first ledger row, so the anti-reset guards hold from the first start
        self.counters.save(self.counters_path)
        self.paths = {"state_dir": str(Path(cfg["state_dir"])), "stop": str(run_path(cfg, "stop_file", "STOP", not self.dry_run)),
                      "halt": str(run_path(cfg, "halt_file", "HALT", not self.dry_run)), "final_marker": str(self.final_marker),
                      "live_ok": str(LIVE_OK_PATH) if not self.dry_run else None, "intents": str(self.intents_path),
                      "wallet_stop": str(Path(pe.LIVE_DIR) / "STOP"), "wallet_halt": str(Path(pe.LIVE_DIR) / "HALT"), "tier_file": str(self._tier_path())}
        self._log("start", "", rule=RULE_ID if self.exit_mode == EXIT_MODE_V1 else RULE_ID_V2, run_mode=self.run_mode, limits=asdict(self.h5), user=str(self.user), paths=self.paths,
                  rpc=sim.redact_rpc_url(rpc_label) if rpc_label else None,
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), seal_end_ms=self.seal_end_ms,
                  config_clamps=self._config_clamps(), **({} if self.exit_mode == EXIT_MODE_V1 else {"exit_mode": self.exit_mode}))
        self._clamp_alert("start")
        if self._tier_state_legacy:
            self._alert("tier_state_legacy", "", tier=self.counters.tier_state.get("tier"),
                        action="buys are refused until a tier_change re-baselines the tier (write another tier to the file, then the one wanted)")

    def __repr__(self) -> str:
        return f"H5Executor(mode={self.run_mode}, user={self.user})"

    @property
    def h5(self) -> H5Limits:
        """The limits of the ACTIVE tier, with the config tightening them (never loosening). Open positions are untouched by a tier change:
        these limits are read only when a new buy is considered."""
        lim = self._tier_limits.get(self.tier)
        if lim is None:
            lim = self._tier_limits[self.tier] = H5Limits.from_config(self.h5cfg, self.tier)
        if self._stake_cap is not None and lim.stake_lamports > self._stake_cap:  # a tipauth-path trigger being handled: its stake is the class stake
            return replace(lim, stake_lamports=self._stake_cap)
        return lim

    # -- the scale ladder ----------------------------------------------------------------------------------------------------
    def _tier_path(self) -> Path:
        return TIER_FILE_PATH if not self.dry_run else Path(self.h5cfg["state_dir"]) / "TIER"

    def _refresh_tier(self, now: int) -> None:
        """Re-read the tier file (before every buy). A change is ledgered as `tier_change` and never touches an open position. A missing file is
        T0 (a real change). An unsafe, unreadable or invalid file is a PROBLEM read: it raises an alert (once per distinct problem) and applies
        T0's limits to that trigger, but does not re-start the tier the counters hold, so that tier's baselines (realized_at_start, wallet)
        survive a half-written file. Only the first start ever (nothing held) records a tier_change for a problem read."""
        tier, problem = read_tier(self._tier_path(), root_checks=not self.dry_run)
        bad_read = problem not in (None, "tier_file_missing")  # invalid, unsafe or unreadable (a missing file is the documented T0)
        if bad_read and problem != self._tier_problem:
            self._alert("tier_file_problem", "", problem=problem)
        self._tier_problem = problem
        ts = self.counters.tier_state
        if bad_read and ts.get("tier") in TIERS:
            # A problem read (a half-written file, a bad owner) applies T0's limits to this trigger and nothing else: the tier the counters hold,
            # with its start baselines (realized_at_start, wallet), is NOT re-started, so the next valid read of the same tier carries on from them.
            self.tier = "T0"
            return
        if tier != ts.get("tier"):
            self._start_tier(tier, ts.get("tier"), problem, now)
            return
        self.tier = tier  # the same tier the counters hold (also after a problem read): keep realized_at_start and the wallet
        if ts.get("wallet_lamports") is None:  # the wallet could not be read when the tier started: try again
            wallet = self._balance_value(now)
            if wallet is not None:
                ts["wallet_lamports"] = wallet
                self.counters.save(self.counters_path)

    def _start_tier(self, tier: str, old: str | None, problem: str | None, now: int) -> None:
        wallet = self._balance_value(now)  # the total stop is also capped at 35% of this
        in_tier = self.counters.tier_attempts  # what the old tier used, for the ledger; the cap starts again from zero in the new one
        self.tier = tier
        self.counters.max_total_loss_lamports = max(self.counters.max_total_loss_lamports, self.h5.total_loss_lamports)  # (persisted below)
        realized_at_start = self.state.realized_lamports  # the tier's loss allowance counts from here, not from the start of the run
        self.counters.tier_state = {"tier": tier, "since_ms": now, "wallet_lamports": wallet, "realized_at_start": realized_at_start}
        self.counters.tier_attempts = 0
        self._tier_state_legacy = False  # a fresh baseline is stored now
        self.counters.save(self.counters_path)
        self._log("tier_change", "", from_tier=old, to_tier=tier, problem=problem, wallet_lamports=wallet, realized_at_start=realized_at_start, limits=asdict(self.h5),
                  open_positions=len(self.state.open), t2_impact_ok=T2_IMPACT_OK, attempts_in_old_tier=in_tier, lifetime_attempts=self.state.attempts,
                  config_clamps=self._config_clamps())
        self._clamp_alert("tier_change")

    def _config_clamps(self) -> dict[str, dict[str, int]]:
        """The tier-scaled limits that the config holds BELOW the active tier's table value (config can only tighten). A shipped config that sets
        them silently turns the ladder off: T1 and T2 would run at the config's numbers."""
        lim = asdict(self.h5)
        return {k: {"table": v, "effective": lim[k]} for k, v in TIERS[self.tier].items() if lim[k] < v}

    def _clamp_alert(self, when: str) -> None:
        clamps = self._config_clamps()
        if clamps:
            self._alert("config_clamps_tier", "", when=when, tier=self.tier, clamps=clamps)

    def _total_stop_lamports(self) -> int | None:
        """The tier's total stop, also capped at 35% of the wallet balance measured when the tier started. None while that balance is unknown.
        It limits the loss SINCE THE TIER STARTED (see `_tier_realized`), not the run's cumulative realized P&L."""
        wallet = self.counters.tier_state.get("wallet_lamports")
        return None if wallet is None else min(self.h5.total_loss_lamports, int(TIER_WALLET_FRAC * wallet))

    def _run_total_stop_lamports(self) -> int:
        """What the run's cumulative realized loss may reach: the highest total stop of any tier entered in this run (persisted), never less than
        the active tier's own. After a step-down the lower tier is judged by its own since-start limit; this only stops a run that has lost more
        than every tier it entered would allow."""
        return max(self.counters.max_total_loss_lamports, self.h5.total_loss_lamports)

    def _tier_realized(self) -> int:
        """Realized P&L since the active tier started. A profit made before the tier change must not widen the new tier's loss allowance (the
        35% cap is a share of the wallet measured at tier start). A counters file with no `realized_at_start` counts from the run's start."""
        return self.state.realized_lamports - int(self.counters.tier_state.get("realized_at_start") or 0)

    def _t0_budget_stop(self, why: str, now: int) -> None:
        """A budget stop that refuses a buy at the bottom tier: there is no lower tier to step down to, so it is never silent. One alert per
        reason and UTC day."""
        key = (why, day_key(now))
        if key not in self._t0_stop_seen:
            self._t0_stop_seen.add(key)
            self._alert("t0_budget_stop", "", why=why, realized_lamports=self.state.realized_lamports, since_tier_start_lamports=self._tier_realized(),
                        held_tier=self.counters.tier_state.get("tier"), tier_problem=self._tier_problem)

    def _tier_step_down_due(self, why: str) -> None:
        """A halt or a loss stop above T0: the file is NOT changed here. An alert for the watchdog; Helm or the manager edits the file."""
        if self.dry_run or self.tier == "T0":
            return
        key = (self.tier, why, day_key(self.now_ms()))
        if key not in self._stepdown_seen:
            self._stepdown_seen.add(key)
            self._alert("tier_step_down_due", "", tier=self.tier, why=why)

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
        if self.exit_mode == EXIT_MODE_V2 and name in V2_REPORT_ONLY:  # DEC-028 s4.1: reported every day, not a halt for v2
            key = (name, day_key(self.now_ms()))
            if key not in self._v2_reported:
                self._v2_reported.add(key)
                self._log("v2_report_only", "", reason=name, **detail)
            return
        now = self.now_ms()
        self.counters.halts[name] = {"ts_ms": now, **detail}
        self.counters.save(self.counters_path)
        self._log("halt_latched", "", reason=name, **detail)
        self._alert(f"halt_{name}", "", **detail)
        self._tier_step_down_due(name)

    def _seal_reason(self, trg: H5Trigger, now: int) -> str | None:
        """EXP-022 s9 / DEC-024 s6. Outside [SEAL_START_MS, seal_end_ms) the oracle is not consulted. Inside it a buy goes ahead only on an
        exact False: True (a pick), None (undecided, or the feed missing or stale > PICK_STALE_S), a non-bool or an exception all refuse.
        Every seal reason is a count only, and _refuse asks again before it writes any per-mint refusal row, so a trigger an earlier check
        refused is relabelled to its seal reason (no row) when the seal would refuse it too."""
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
        """The loss/count stops of the ACTIVE tier. It does not read the tier file: handle_trigger reads it once per trigger, so every check of one
        trigger (max_open, the T2 guard, these stops, the stake) is judged with the same tier."""
        st, h5 = self.state, self.h5
        if self.counters.halts:
            return "halt_latched:" + ",".join(sorted(self.counters.halts))
        if self._tier_state_legacy:
            return "tier_state_legacy"  # the tier the counters hold has no stored baseline (realized_at_start): only a tier_change sets one
        total_stop = self._total_stop_lamports()
        if total_stop is None:
            return "balance_unreadable"  # the wallet at tier start is unknown: fail closed
        # Worst-case exposure, not realized alone: everything open or in flight, and this stake, is assumed lost.
        at_risk = (sum(int(p.get("spend") or 0) + int(p.get("extra_cost") or 0) for p in st.open.values())
                   + sum(int(p.get("spend") or 0) for p in st.pending.values() if p["kind"] == "buy") + h5.stake_lamports)
        if self._tier_realized() - at_risk <= -total_stop:  # the loss since this tier started, against min(tier total, 35% of the wallet then)
            return "total_loss_stop"
        if st.realized_lamports - at_risk <= -self._run_total_stop_lamports():  # and the run-cumulative loss against the HIGHEST total of any tier
            return "total_loss_stop"                                         # entered in this run. A flicker of the file does not re-baseline the tier
                                                                             # (see _refresh_tier), so the since-start check above holds across it
        day = self.counters.day(day_key(now))
        if day["realized"] - at_risk <= -h5.daily_loss_lamports:
            return "daily_loss_stop"
        if day["trades"] >= h5.max_trades_per_day:
            return "max_trades_day"
        if self.counters.tier_attempts >= h5.max_attempts:  # per tier: reset at each tier_change (state.attempts is the lifetime count, for the ledger)
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
        if trg.synthetic is not False or trg.synthetic_src != SYNTHETIC_SRC_OK:  # the parsers already refuse this; a trigger handed in by a callback must not slip past (fail closed)
            return SYNTHETIC_UNCONFIRMED
        if trg.pool in self.excluded_pools or trg.mint in self.excluded_pools:  # the detector said `excluded` for this pool: no buy, whatever a trigger says
            return EXCLUDED_POOL
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

    def _s0_anchor(self, trg: H5Trigger) -> tuple[int | None, str | None, dict[str, Any]]:
        """(wall ms of s0, refusal, info for the ledger). The exit is timed from s0, so its wall anchor is OUR OWN mapping of s0_slot through the
        getSlot history we recorded, not the detector's word. A history that does not reach s0 (the first ~300 s after a start) cannot anchor
        it: refuse (`s0_before_history`); the whole-second block time is not used instead.
        The detector's s0 receive time is only checked, and RELATIVE to the same feed: the shadow hears every print late by the feed's lag
        (it listens at `confirmed`, we map `processed`: about 2.6 s at p10), so an absolute comparison refuses most live triggers. What a
        backlogged s0 shows is an s0 that came in later, against the chain, than the trigger print did on the same feed:
        (s0_recv - mapped(s0)) - (trigger_recv - mapped(trigger_slot)) > 1.5 s -> refuse (`s0_recv_late`)."""
        info: dict[str, Any] = {"anchor_source": None}
        mapped = self.slots.wall_of_slot(trg.s0_slot, trg.sps)
        if mapped is None:
            return None, "s0_before_history", info
        info["anchor_source"] = "own_getslot_history"
        if trg.s0_wall_ms is not None:
            trig_mapped = self.slots.wall_of_slot(trg.trigger_slot, trg.sps)
            if trig_mapped is None or trg.trigger_recv_ms is None:
                return mapped, "s0_unverifiable", info
            s0_lag, trig_lag = trg.s0_wall_ms - mapped, trg.trigger_recv_ms - trig_mapped
            info.update(s0_feed_lag_ms=s0_lag, trigger_feed_lag_ms=trig_lag, s0_relative_lag_ms=s0_lag - trig_lag)
            if s0_lag - trig_lag > S0_RECV_LATE_MS:
                return mapped, "s0_recv_late", info
        return mapped, None, info

    def _hour_roll(self, now: int) -> None:
        """One `refusal_counts` ledger row per UTC hour: accepted and refused triggers and the refusals by reason (seal skips as one count;
        seal_pick is not in it: SEAL_PICK_IN_PROCESS_ONLY)."""
        hour = now // 3_600_000
        if self._counts_hour is None:
            self._counts_hour = hour
        elif hour != self._counts_hour:
            if self._refusal_counts or self._accepted_hour:
                self._log("refusal_counts", "", hour_start_ms=self._counts_hour * 3_600_000, accepted=self._accepted_hour,
                          refused=sum(self._refusal_counts.values()), by_reason=dict(sorted(self._refusal_counts.items())),
                          pre_unlinked=self._pre_unlinked_hour)
            if self._pre_unlinked_hour:  # hourly, with a count: triggers traded although their predecessor print may be missing
                self._alert("trigger_pre_unlinked", "", hour_start_ms=self._counts_hour * 3_600_000, count=self._pre_unlinked_hour,
                            accepted=self._accepted_hour)
            self._counts_hour, self._refusal_counts, self._accepted_hour, self._pre_unlinked_hour = hour, {}, 0, 0

    def _count_refusal(self, reason: str) -> None:
        if reason == SEAL_PICK_IN_PROCESS_ONLY:
            return  # never in the hourly ledger row (see SEAL_PICK_IN_PROCESS_ONLY)
        now = self.now_ms()
        self._hour_roll(now)
        key = "seal_skip" if reason in SEAL_REASONS else reason
        self._refusal_counts[key] = self._refusal_counts.get(key, 0) + 1
        if reason in S0_ANCHOR_REASONS:  # these refuse most triggers when the anchor is wrong: more than 3 in an hour is an alert
            self._anchor_refusal_ms = [t for t in self._anchor_refusal_ms if now - t < BAD_INTENT_WINDOW_MS] + [now]
            if len(self._anchor_refusal_ms) > S0_ANCHOR_ALERT_N and now - self._anchor_alert_ms >= BAD_INTENT_WINDOW_MS:
                self._anchor_alert_ms = now
                self._alert("s0_anchor_refusals", "", count=len(self._anchor_refusal_ms), last_reason=reason)

    def _refuse(self, trg: H5Trigger, reason: str, **kw: Any) -> None:
        if reason not in SEAL_REASONS:
            sealed = self._seal_reason(trg, self.now_ms())
            if sealed is not None:  # EXP-022 s9 / DEC-024 s6: a mint the seal would refuse (a pick, or not known to be a non-pick) never gets a
                reason, kw = sealed, {}  # per-mint row, whatever check refused it first. The decision (refuse) is the same; only the label is.
        if reason == SEAL_PICK_IN_PROCESS_ONLY:  # a refusal on a pick: counted in this process only (no ledger, counters file, alert or log)
            self.seal_picks_in_process += 1
            return
        self._count_refusal(reason)
        if reason == SYNTHETIC_UNCONFIRMED:  # (only a trigger that skipped the parsers gets here; the parsers' refusals are counted in _bad_intent)
            self.counters.synthetic_unconfirmed += 1
            self.counters.save(self.counters_path)
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
        if trg.gate_path is None:  # the websocket path: exactly as before
            return self._handle_trigger(trg, seen_ms)
        self._stake_cap = TIPAUTH_STAKE_LAMPORTS  # the tipauth class is 0.02 SOL at every tier (ruling D2); self.h5.stake_lamports reads this
        try:
            return self._handle_trigger(trg, seen_ms)
        finally:
            self._stake_cap = None

    def _handle_trigger(self, trg: H5Trigger, seen_ms: int | None = None) -> None:
        now = self.now_ms()
        self._refresh_tier(now)  # the ONE read of the tier file for this trigger: the limits below are the active tier's, and nothing re-reads it
        why = self._hard_refusal(trg, now)
        if not why and trg.gate_path is not None:
            why = self._tipauth_recheck(trg)  # defence in depth: a handed-in trigger tagged tipauth is judged against the switch, the latch and the rule again
        if not why and self.tier == "T2" and not T2_IMPACT_OK:
            why = "t2_impact_unchecked"  # only reachable if T2_IMPACT_OK is set back to False (e.g. a live-fill impact finding): T2 is then refused again
        would: str | None = None
        if not why:
            budget = self._budget_stop(now)
            if budget in ("total_loss_stop", "daily_loss_stop"):
                self._tier_step_down_due(budget)
            if budget in T0_ALERT_STOPS and self.tier == "T0" and not self.dry_run:
                self._t0_budget_stop(budget, now)
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
            s0_wall, _why, self._anchor_info = self._s0_anchor(trg)
            plan = exit_plan(trg.s0_slot, trg.sps, self.h5, s0_wall)
            if self.dry_run:
                return self._dry_buy(trg, ps, terms, plan, now, would, drift)
            self._live_buy(trg, ps, terms, plan, now, drift, seen_ms)

    def _live_buy(self, trg: H5Trigger, ps: tx.PoolState, terms: dict[str, Any], plan: ExitPlan, now: int, drift: float | None,
                  seen_ms: int | None) -> None:
        if self.tier == "T2" and not T2_IMPACT_OK:  # the second line of defence (the first is in handle_trigger): never build a T2 buy while it is off
            return self._refuse(trg, "t2_impact_unchecked")
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
        self.counters.tier_attempts += 1
        st.bought.append(trg.mint)
        if st.first_attempt_ms is None:
            st.first_attempt_ms = now
        self.counters.day(day_key(now))["trades"] += 1
        if getattr(trg, "gate_path", None) is not None:
            self._tipauth_admit(trg.mint)  # durable before the send, so every row of this trade carries the tag
        self.counters.plans[trg.mint] = {"trigger": trg.public(), "plan": plan.public(), "min_out": terms["min_out"],  # durable before the send
                                         "tolerance_bps": self.h5.entry_tolerance_bps, **self._v2_stamp()}
        spend = self.h5.stake_lamports
        st.pending[trg.mint] = {
            "kind": "buy", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": trg.pool, "snap_slot": trg.trigger_slot,
            "drift_vs_seed": None, "decision_t_ms": trg.decision_ms, "receive_ms": now, "score": None, "spend": spend,
            "q_tokens": terms["expected_tokens"], "q_net_in": terms["net_in"], "q_mark": terms["mark"], "fee_ppm": terms["fee_ppm"],
            "v_lamports": trg.v_lamports, "base_vault": str(ps.base_vault), "quote_vault": str(ps.quote_vault),
            "base_ata": str(tx.ata(self.user, ps.base_mint, ps.base_token_program)), "base_mint": str(ps.base_mint),
            "base_tp": str(ps.base_token_program), "sends": 0, "seen_ms": seen_ms, "state_ms": now, "state_slot": trg.trigger_slot,
            "built_ms": t_built, "runner_latency": None,
            "h5": {"trigger": trg.public(), "plan": plan.public(), "min_out": terms["min_out"], "tolerance_bps": self.h5.entry_tolerance_bps,
                   **self._v2_stamp()},
        }
        self.save()
        self.counters.save(self.counters_path)
        p = st.pending[trg.mint]
        self._send(p, trg.mint)
        self.save()
        if trg.mint not in st.pending:  # the kill switch fired between the gate and the send: nothing was sent
            return
        self._count_accepted(trg.pre_unlinked)
        self._log("decision", trg.mint, **trg.log_fields(), anchor=self._anchor_info, stake_lamports=spend, expected_tokens=terms["expected_tokens"], min_out=terms["min_out"],
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
        self.counters.tier_attempts += 1
        self.state.bought.append(trg.mint)
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = now
        self.counters.day(day_key(now))["trades"] += 1
        self._count_accepted(trg.pre_unlinked)
        if getattr(trg, "gate_path", None) is not None:
            self._tipauth_admit(trg.mint)
        self._log("decision", trg.mint, **trg.log_fields(), anchor=self._anchor_info, stake_lamports=self.h5.stake_lamports, expected_tokens=terms["expected_tokens"],
                  min_out=terms["min_out"], fee_ppm=terms["fee_ppm"], tolerance_bps=self.h5.entry_tolerance_bps, plan=plan.public(),
                  would_have_halted=would, live_validate_err=validate_err, sim_tokens=sim_tokens, drift_vs_trigger=drift,
                  err=res.get("err"), cu_used=res.get("unitsConsumed"), ms_decision_to_built=t_built - trg.decision_ms,
                  ms_built_to_sim=self.now_ms() - t_built, tx_bytes=tx.serialized_size(msg), sent=False)
        if res.get("err") is None:  # a virtual position, so the exit timer and max_open are exercised
            self.state.open[trg.mint] = {
                "mint": trg.mint, "pool": trg.pool, "t_entry_ms": now, "tokens": terms["expected_tokens"], "net_in": terms["net_in"],
                "mark": terms["mark"], "spend": self.h5.stake_lamports, "virtual": True, "h5": {"trigger": trg.public(), "plan": plan.public(), **self._v2_stamp()},
                "base_mint": str(ps.base_mint), "base_ata": str(tx.ata(self.user, ps.base_mint, ps.base_token_program)),
                "base_tp": str(ps.base_token_program), "sell_attempts": 0,
            }
            self.pool_cache[trg.mint] = (ps, now)
        self.save()
        self.counters.save(self.counters_path)
        self.refresh_slot()

    # -- signing and sending (the only two places a key or the network is touched) ------------------------------------------
    def _signer_spend_cap(self) -> int:
        cap = self.h5.stake_lamports
        return cap if T2_IMPACT_OK else min(cap, TIERS["T1"]["stake_lamports"])

    def _sign(self, msg: Message, ps: tx.PoolState, mint: str, cap: int | None = None) -> tuple[str, str]:
        if self.dry_run:
            raise RuntimeError("dry run cannot sign")
        # (the spend cap is the ACTIVE tier's stake: the probe's own Limits clamps its size to 0.05 SOL, which would refuse every T1 and T2 buy;
        # while T2 is not allowed it is never above T1's stake, whatever tier the file said when the message was built)
        pl.validate_message(msg, ps, Pubkey.from_string(mint), self.user, cap if cap is not None else self.h5.escalated_priority_lamports,
                            self._signer_spend_cap())
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
          (a) UTC-day median over >= BOOST_MEDIAN_MIN_POOLS (30) pools: < 335 s -> boost_median_lt_335; < 337 s on two UTC days -> boost_median_lt_337_twice;
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
        if c.bvs_n >= BOOST_BEFORE_SELL_MIN_SELLS and share > BOOST_BEFORE_SELL_FRAC:  # in v2 mode a report, not a halt (V2_REPORT_ONLY)
            self._latch("boost_before_sell_gt_15pct", n=c.bvs_n, before=c.bvs_before, share=round(share, 4))
        if self.exit_mode == EXIT_MODE_V2:  # DEC-028 s4.1(a): the rolling 20 is v2's rule (b)
            self._v2_pair(mint, boost <= landing)

    # -- H5-BOOSTCLOCK v2 exit (DEC-028 Option B). Reached only in v2 mode or for a position stamped exit_rule H5-BOOSTCLOCK-v2. -------------
    def _v2_stamp(self) -> dict[str, Any]:
        """The exit rule stamped on the pending buy, its durable plan and the position. Nothing in v1 mode (v1 records are unchanged)."""
        return {} if self.exit_mode == EXIT_MODE_V1 else {"exit_rule": RULE_ID_V2}

    def on_boost_slice(self, row: dict[str, Any]) -> None:
        """One BOOST buy in a tracked pool (the shadow's `boost_slice` record, or a flat h5_boost_slice_v1 row). Structure only, no price.
        Kept per pool (slices before our buy count: the projection uses every slice since s0); a re-reported slice is counted once."""
        if self.exit_mode != EXIT_MODE_V2:  # the caller checks too; kept so no path holds a slice in a v1 build
            return
        mint, slot, txi, evi, recv = row.get("mint"), row.get("slot"), row.get("tx_index"), row.get("event_index"), row.get("t_recv_ms")
        sig = row.get("signature") if isinstance(row.get("signature"), str) else ""
        lam = row.get("lamports")
        sol = row.get("sol")
        if lam is None and isinstance(sol, (int, float)) and not isinstance(sol, bool) and math.isfinite(sol):
            lam = int(round(sol * 1e9))
        bt = row.get("block_time_ms") if _is_int(row.get("block_time_ms")) else (row["block_time"] * 1000 if _is_int(row.get("block_time")) else None)
        why = None
        if not isinstance(mint, str) or not mint:
            why = "no_mint"
        elif not (_is_int(slot) and (_is_int(txi) or sig) and _is_int(evi) and _is_int(recv)):  # a print's identity: tx_index or signature
            why = "bad_position_or_time"
        elif not _is_int(lam) or lam <= 0:
            why = "bad_size"
        elif row.get("boost_src") not in V2_BOOST_SRC:
            why = "not_vault_identity"
        elif not isinstance(row.get("pool"), str) or not row["pool"]:
            why = "no_pool"
        else:  # the slice must be on the pool this mint's slices (and our position) are on: no cross-pool mix
            held = (self._v2_slices.get(mint) or {}).get("pool")
            ours = (self.state.open.get(mint) or self.state.pending.get(mint) or {}).get("pool")
            if (held is not None and row["pool"] != held) or (ours is not None and row["pool"] != ours):
                why = "pool_mismatch"
        if why is not None:
            return self._log("v2_slice_ignored", str(mint or ""), why=why)
        now = self.now_ms()
        ent = self._v2_slices.get(mint)
        if ent is None:
            ent = self._v2_slices[mint] = {"pool": row.get("pool"), "sl": {}}
            busy = set(self.state.open) | set(self.state.pending)
            for old in [k for k in self._v2_slices if k not in busy][: max(0, len(self._v2_slices) - V2_SLICE_POOLS_KEEP)]:
                del self._v2_slices[old]
                self._v2_evicted[old] = None
            while len(self._v2_evicted) > V2_EVICTED_KEEP:
                self._v2_evicted.pop(next(iter(self._v2_evicted)))
        key = (slot, txi if _is_int(txi) else -1, sig, evi)  # one type per position, so the slot sort never compares None with an int
        if key in ent["sl"]:
            return
        ent["sl"][key] = (lam, recv, bt, now)
        ent["seen_ms"] = now
        pos = self.state.open.get(mint)
        v = ((pos or {}).get("h5") or {}).get("v2") or {}
        if v.get("fired") and pos["h5"]["trigger"]["sps"]:  # a slice the decision should have had (usable before it) came in after it
            trg = pos["h5"]["trigger"]
            t_i = (slot - trg["s0_slot"]) * trg["sps"]
            if t_i + V2_OBS_S <= v["tau_s"]:
                self._log("v2_slice_after_decision", mint, slice_s=round(t_i, 3), tau_s=v["tau_s"], recv_ms=recv)

    def _v2_fallback(self, mint: str, plan: dict[str, Any]) -> str | None:
        """Why this position may not use the slice list (then v1's 330 s timer is its only exit): None = it may."""
        wall0 = plan.get("s0_wall_ms")
        if wall0 is None:
            return "no_s0_wall"
        if mint in self._v2_recovered:
            return "restart_after_s0"  # the slices read before this start are not kept across it
        if wall0 < self._v2_start_ms:
            return "started_after_s0"  # the pool's s0 came before this process started: its earlier slices were never read here
        if mint in self._v2_evicted:
            return "slices_evicted"  # the pool's held slices were dropped to bound memory: the rebuilt list is partial
        if self._v2_last_gap_ms >= wall0:
            return "feed_gap"
        return None

    def _v2_tick(self, mint: str, pos: dict[str, Any], plan: dict[str, Any], est: int | None, now: int) -> tuple[bool, bool]:
        """(arm, send) for a v2 position from the rule (v2_cad_exit, this file) on the slices received so far. v1's stages stay armed in
        exit_tick: this only adds an earlier exit. Once fired, the sell stays due (a failed sell is retried, as v1's past its send stage)."""
        h = pos["h5"]
        v = h.setdefault("v2", {})
        if v.get("fired"):
            return False, True
        if h.get("immediate_exit") or h.get("no_plan"):
            return False, False
        trg = h["trigger"]
        s0, sps = trg["s0_slot"], trg["sps"]
        ent = self._v2_slices.get(mint)
        sl = sorted(ent["sl"].items()) if ent else []
        self._v2_stale_check(mint, pos, ent, sl, now)
        fb = self._v2_fallback(mint, plan)
        if fb is not None:
            if v.get("fallback") != fb:
                v["fallback"] = fb
                self._log("v2_fallback_330", mint, why=fb)
                self.save()
            return False, False
        x = h.get("buy_landed_slot") or trg["trigger_slot"]  # X: our buy's landing slot (a dry run has none: the trigger slot)
        if v.get("_n") != len(sl) or v.get("_x") != x:  # recomputed only when a slice arrived or the buy landed
            t = [(k[0] - s0) * sps for k, _ in sl]
            a = [val[0] for _, val in sl]
            tau, fired, k, proj = v2_cad_exit(t, a, (x - s0) * sps)
            trig_slot = v2_exit_trigger_slot(s0, sps, x, tau, fired)
            early = fired and trig_slot < v2_v1_exit_slot(s0, sps, x)
            tw = plan["s0_wall_ms"] + int(round((trig_slot - s0) * sps * 1000))
            v.update(_n=len(sl), _x=x, tau_s=round(tau, 4), k=k, proj_s=None if proj is None else round(proj, 4), trigger_slot=trig_slot,
                     trigger_wall_ms=tw, early=early)
        if not v["early"]:
            return False, False
        e_slot, tw = v["trigger_slot"], v["trigger_wall_ms"]
        send = now >= tw or (est is not None and est >= e_slot and now >= tw - EARLY_TOLERANCE_MS)  # as _due: the wall bounds the slot plan
        arm_w = tw - self.h5.arm_lead_ms
        arm = now >= arm_w or (est is not None and est >= e_slot - ceil_slots(self.h5.arm_lead_ms / 1000.0, sps) and now >= arm_w - EARLY_TOLERANCE_MS)
        if send:
            used = [val for k_, val in sl[: v["k"]]]
            lags = [(r - b) / 1000.0 for _l, r, b, _s in used if b is not None]  # the shadow's receive lag (t_recv_ms - block time)
            seen = [(s_ - b) / 1000.0 for _l, _r, b, s_ in used if b is not None]  # ours: when this executor read it (seen_ms - block time)
            v.update(fired=True, fired_ms=now, fired_est_slot=est)
            self._log("v2_exit_decision", mint, rule=RULE_ID_V2, tau_s=v["tau_s"], k_slices=v["k"], proj_last_slice_s=v["proj_s"],
                      exit_trigger_slot=e_slot, v1_exit_slot=v2_v1_exit_slot(s0, sps, x), landing_slot=x, est_slot=est,
                      ms_after_trigger_wall=now - tw, slices_held=len(sl), spent_lamports=sum(val[0] for _, val in sl[: v["k"]]),
                      max_recv_lag_s=round(max(lags), 3) if lags else None, max_seen_lag_s=round(max(seen), 3) if seen else None,
                      median_seen_lag_s=round(statistics.median(seen), 3) if seen else None, off_s=V2_OFF_S, obs_s=V2_OBS_S, kmin=V2_KMIN)
            self.save()
        return arm, send

    def _v2_stale_check(self, mint: str, pos: dict[str, Any], ent: dict[str, Any] | None, sl: list, now: int) -> None:
        """DEC-028 s4.7: no slice seen for more than 45 s while BOOST still has budget and our position is open: counted once per position;
        more than 2 such positions in a UTC day halts."""
        v = pos["h5"]["v2"]
        if v.get("stale") or mint in self.state.pending:
            return
        if sum(val[0] for _, val in sl) >= V2_BUDGET_LAMPORTS - V2_DONE_LAMPORTS:
            return  # BOOST is done: silence is expected
        ref = max(int((ent or {}).get("seen_ms") or 0), int(pos.get("t_entry_ms") or 0), self._v2_start_ms)
        if now - ref <= V2_STALE_S * 1000:
            return
        v["stale"] = True
        d = self.counters.day(day_key(now)).setdefault("v2", {})
        d["stale"] = d.get("stale", 0) + 1
        self._log("v2_slice_stream_stale", mint, silent_s=round((now - ref) / 1000, 3), slices_held=len(sl), day_count=d["stale"])
        self.counters.save(self.counters_path)
        self.save()
        if d["stale"] > V2_STALE_MAX_PER_DAY:
            self._latch("v2_slice_stream_stale", day=day_key(now), positions=d["stale"])

    def _v2_sell_fields(self, pos: dict[str, Any], sent_ms: int | None) -> dict[str, Any]:
        """Extra ledger fields on a v2 position's sell (none for v1): the decision, and decision-to-send ms (DEC-028 s5 item 5)."""
        h = pos.get("h5") or {}
        if h.get("exit_rule") != RULE_ID_V2:
            return {}
        v = h.get("v2") or {}
        out = {"exit_rule": RULE_ID_V2, "v2_fired": bool(v.get("fired")), "v2_fallback": v.get("fallback")}
        if v.get("fired"):
            out.update(v2_trigger_slot=v.get("trigger_slot"), v2_tau_s=v.get("tau_s"),
                       ms_decision_to_send=(sent_ms - v["fired_ms"]) if sent_ms is not None and v.get("fired_ms") is not None else None)
        return out

    def _v2_late(self, h: dict[str, Any], plan: dict[str, Any], landed_slot: int, landing_wall: int | None) -> tuple[bool, dict[str, Any]]:
        """DEC-028 s4.2: late when the sell lands more than 3 s after its v2 exit trigger (the v2 trigger, or v1's s0 + 330 s when v2 did
        not fire). The s0 + 335 s line is v1's check, kept by the caller."""
        v = h.get("v2") or {}
        sps = h["trigger"]["sps"]
        if v.get("fired"):
            trig_slot, trig_wall = v["trigger_slot"], v.get("trigger_wall_ms")
        else:
            trig_slot = plan["exit_slot"]
            trig_wall = None if plan.get("s0_wall_ms") is None else plan["s0_wall_ms"] + int(HOLD_S * 1000)
        if landing_wall is not None and trig_wall is not None:
            after = (landing_wall - trig_wall) / 1000.0
        else:
            after = (landed_slot - trig_slot) * sps
        late = after > V2_LATE_AFTER_TRIGGER_S
        return late, {"exit_rule": RULE_ID_V2, "v2_fired": bool(v.get("fired")), "v2_trigger_slot": trig_slot,
                      "v2_after_trigger_s": round(after, 3), "v2_late": late}

    def _v2_pair(self, mint: str, after_boost: bool) -> None:
        """DEC-028 s4.1(a): over a rolling 20 landed sells paired with their pool's BOOST last slice, more than 15% at or after it halts."""
        c = self.counters
        pairs = [*c.v2.get("pairs", []), bool(after_boost)][-V2_SELL_AFTER_BOOST_N:]
        c.v2["pairs"] = pairs
        c.save(self.counters_path)
        if len(pairs) >= V2_SELL_AFTER_BOOST_N and sum(pairs) / len(pairs) > V2_SELL_AFTER_BOOST_FRAC:
            self._latch("v2_sell_after_boost_gt_15pct", n=len(pairs), after=sum(pairs), share=round(sum(pairs) / len(pairs), 4))

    def _v2_close(self, mint: str) -> None:
        """Our v2 position on this pool closed: its slices' receive lags join the day's traded-pool lags (DEC-028 s4.7: median above 1.35 s
        halts), and the pool's slices are dropped."""
        ent = self._v2_slices.pop(mint, None)
        vals = [v_ for v_ in (ent or {}).get("sl", {}).values() if v_[2] is not None]
        lags = [round((r - b) / 1000.0, 3) for _l, r, b, _s in vals]  # the shadow's receive lag: the DEC-028 s4.7 halt is judged on this
        seen = [round((s_ - b) / 1000.0, 3) for _l, _r, b, s_ in vals]  # ours (seen_ms - block time): logged beside it, not a halt
        now = self.now_ms()
        d = self.counters.day(day_key(now)).setdefault("v2", {})
        d["lag"] = [*d.get("lag", []), *lags][-V2_LAG_KEEP:]
        d["seen_lag"] = [*d.get("seen_lag", []), *seen][-V2_LAG_KEEP:]
        d["lag_pools"] = d.get("lag_pools", 0) + 1
        med = round(statistics.median(d["lag"]), 3) if d["lag"] else None
        d["lag_median"] = med
        d["seen_lag_median"] = round(statistics.median(d["seen_lag"]), 3) if d["seen_lag"] else None
        self._log("v2_pool_closed", mint, slices=len(lags), day_lag_median_s=med, day_seen_lag_median_s=d["seen_lag_median"],
                  pool_seen_lag_median_s=round(statistics.median(seen), 3) if seen else None, day_pools=d["lag_pools"])
        self.counters.save(self.counters_path)
        if med is not None and med > V2_LAG_MEDIAN_MAX_S:
            self._latch("v2_slice_lag_gt_1_35s", day=day_key(now), median_s=med, pools=d["lag_pools"], slices=len(d["lag"]))

    def _v2_pool_row(self, row: dict[str, Any]) -> None:
        """A clean closed pool (the caller's filter: horizon, no gap, vault identity). Plain pools only (synthetic is the literal false) feed
        the day's v2 checks: the last-slice median (s4.1(b)) and, over pools whose BOOST completed (vault below 0.05 SOL), the BOOST total and
        slice-interval medians (s4.1(d)). A pool's interval is its mean slice gap, (last - first) / (n - 1), from the pool row."""
        mint = str(row.get("mint") or "")
        busy = mint in self.state.open or mint in self.state.pending
        if not busy:
            self._v2_slices.pop(mint, None)
        if row.get("synthetic") is not False:
            return
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)  # noqa: E731
        last, first, n, tot, rem = (row.get(k) for k in ("boost_last_slice_s", "boost_first_slice_s", "boost_slices", "boost_total_sol",
                                                         "boost_vault_remaining_sol"))
        if not num(last):
            return
        now = self.now_ms()
        d = self.counters.day(day_key(now)).setdefault("v2", {})
        plain = d.setdefault("plain", {})
        if mint in plain:
            return
        interval = (last - first) / (n - 1) if num(first) and _is_int(n) and n >= 2 else None
        complete = num(rem) and rem * 1e9 < V2_DONE_LAMPORTS
        plain[mint] = [round(float(last), 3), float(tot) if num(tot) else None, None if interval is None else round(interval, 4), bool(complete)]
        d["plain_n"] = len(plain)
        if len(plain) >= V2_PLAIN_MIN_POOLS:
            d["plain_median"] = round(statistics.median(x[0] for x in plain.values()), 3)
        comp = [x for x in plain.values() if x[3] and x[1] is not None and x[2] is not None]
        d["complete_n"] = len(comp)
        if len(comp) >= V2_PLAIN_MIN_POOLS:
            d["total_median_sol"] = round(statistics.median(x[1] for x in comp), 4)
            d["interval_median_s"] = round(statistics.median(x[2] for x in comp), 4)
        self.counters.save(self.counters_path)
        self._v2_day_checks()

    def _v2_day_checks(self) -> None:
        """Completed UTC days only (a running day's median dips and recovers): s4.1(b) plain median below 330 s over >= 30 plain pools;
        s4.1(d) median BOOST total outside [17.5, 17.7] SOL or median slice interval outside [6, 24] s over >= 30 plain completed pools."""
        today = day_key(self.now_ms())
        for k in sorted(self.counters.days):
            d = self.counters.days[k].get("v2") or {}
            if k >= today or not d:
                continue
            if d.get("plain_median") is not None and d.get("plain_n", 0) >= V2_PLAIN_MIN_POOLS and d["plain_median"] < V2_PLAIN_MEDIAN_MIN_S:
                self._latch("v2_plain_median_lt_330", day=k, median_s=d["plain_median"], pools=d["plain_n"])
            if d.get("complete_n", 0) >= V2_PLAIN_MIN_POOLS and d.get("total_median_sol") is not None:
                tot, iv = d["total_median_sol"], d["interval_median_s"]
                if not (V2_TOTAL_SOL_LO <= tot <= V2_TOTAL_SOL_HI) or not (V2_INTERVAL_LO_S <= iv <= V2_INTERVAL_HI_S):
                    self._latch("v2_projection_inputs", day=k, total_median_sol=tot, interval_median_s=iv, pools=d["complete_n"])

    def _count_accepted(self, unlinked: bool = False) -> None:
        self._hour_roll(self.now_ms())
        self._accepted_hour += 1
        self._pre_unlinked_hour += 1 if unlinked else 0

    def _bad_intent(self, row: dict[str, Any], why: str, shadow: bool = False) -> None:
        """A trigger record that cannot be traded on. Ledgered. A shadow record that LACKS a required key is a schema mismatch (a detector that is
        not on the head this executor reads): that alerts at the first record, rate-limited to one per SCHEMA_ALERT_WINDOW_MS, and is not part of
        the hourly count. A bad VALUE counts: more than BAD_INTENT_ALERT_N in an hour is an alert (a broken detector looks like this)."""
        now = self.now_ms()
        if why == SYNTHETIC_UNCONFIRMED:
            return self._synthetic_unconfirmed(row, now)
        self._log("skip", str(row.get("mint") or ""), reason=why)
        self._count_refusal(why)
        if shadow and why.startswith("bad_intent:missing_"):
            if now - self._schema_alert_ms >= SCHEMA_ALERT_WINDOW_MS:
                self._schema_alert_ms = now
                self._alert("shadow_schema_mismatch", "", missing=[k for k in SHADOW_REQUIRED_KEYS if k not in row][:12], shadow_schema=row.get("schema"),
                            reason_=why)
            return
        self._bad_intent_ms = [t for t in self._bad_intent_ms if now - t < BAD_INTENT_WINDOW_MS] + [now]
        if len(self._bad_intent_ms) > BAD_INTENT_ALERT_N and now - self._bad_intent_alert_ms >= BAD_INTENT_WINDOW_MS:
            self._bad_intent_alert_ms = now
            self._alert("bad_intent_rate", "", count=len(self._bad_intent_ms), last_reason=why)

    def _synthetic_unconfirmed(self, row: dict[str, Any], now: int) -> None:
        """A trigger record whose `synthetic` is not the literal false: never a buy. Every one is counted (the hourly refusal counts, and
        counters.synthetic_unconfirmed for --status); its ledger row is written once per pool. The two causes are told apart:
          key ABSENT   the detector is not on a head that classifies (an old-schema shadow): a schema problem, so an alert at the first record,
                       then at most one per SCHEMA_ALERT_WINDOW_MS, and not part of the hourly bad-value count
          key present  null / true / not a bool, or false from a source other than "rpc": a detector bug (it should have written `excluded`, or a trigger with false): counts like any
                       other bad value, so more than BAD_INTENT_ALERT_N in an hour is a bad_intent_rate alert"""
        absent = "synthetic" not in row
        v = row.get("synthetic")
        seen = "absent" if absent else "true" if v is True else "false" if v is False else "null" if v is None else "not_bool"
        key = str(row.get("pool") or row.get("mint") or "")
        if key not in self._synthetic_logged:
            self._synthetic_logged[key] = None
            while len(self._synthetic_logged) > SYNTHETIC_LOGGED_KEEP:
                del self._synthetic_logged[next(iter(self._synthetic_logged))]
            src = _synthetic_src(row)
            self._log("skip", str(row.get("mint") or ""), reason=SYNTHETIC_UNCONFIRMED, pool=key, synthetic_seen=seen,
                      synthetic_src_seen=src if src is not None else ("absent" if "synthetic_src" not in row else "invalid"))
        self._count_refusal(SYNTHETIC_UNCONFIRMED)
        self.counters.synthetic_unconfirmed += 1
        self.counters.save(self.counters_path)
        if absent:
            if now - self._synthetic_alert_ms >= SCHEMA_ALERT_WINDOW_MS:
                self._synthetic_alert_ms = now
                self._alert("shadow_synthetic_missing", "", pool=key, shadow_schema=row.get("schema"),
                            note="the trigger record has no `synthetic` key: the shadow job is not on the head that classifies synthetic migrations")
            return
        self._bad_intent_ms = [t for t in self._bad_intent_ms if now - t < BAD_INTENT_WINDOW_MS] + [now]
        if len(self._bad_intent_ms) > BAD_INTENT_ALERT_N and now - self._bad_intent_alert_ms >= BAD_INTENT_WINDOW_MS:
            self._bad_intent_alert_ms = now
            self._alert("bad_intent_rate", "", count=len(self._bad_intent_ms), last_reason=SYNTHETIC_UNCONFIRMED)

    def _note_excluded(self, row: dict[str, Any]) -> None:
        """An `excluded` record: the detector kept a pool out (synthetic, or it could not classify it). Counted by reason (the caller saves the
        counters once per tick), remembered by pool and mint so a conflicting trigger is refused, and otherwise silent: no ledger row, no alert,
        no halt."""
        reason = row.get("reason")
        reason = reason if reason in EXCLUDED_REASONS else "other"
        self.counters.excluded[reason] = self.counters.excluded.get(reason, 0) + 1
        for k in ("pool", "mint"):
            v = row.get(k)
            if isinstance(v, str) and v:
                self.excluded_pools.pop(v, None)
                self.excluded_pools[v] = reason
        while len(self.excluded_pools) > EXCLUDED_KEEP:
            del self.excluded_pools[next(iter(self.excluded_pools))]

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
        excluded_seen = False
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
                if self._tipauth_switch:
                    trg, bad = parse_shadow_trigger(row, self.trigger_variant, tipauth=self._tipauth_availability())
                else:
                    trg, bad = parse_shadow_trigger(row, self.trigger_variant)  # switch off: the call is exactly main's
                if trg is not None:
                    triggers.append(trg)
                elif bad:
                    self._bad_intent(row, bad, shadow=True)
            elif rtype == "gap":
                # The hold starts only when the record says coverage was lost: flags_pools true, or the key missing or anything but the
                # literal false (an unknown schema fails closed). A reconnect on a redundant feed whose other sockets stayed up says false.
                if row.get("flags_pools") is False:
                    self._log("feed_reconnect_redundant", "", kind_=row.get("kind"))
                else:
                    self._gap_until_ms = self.now_ms() + self.h5.gap_hold_ms
                    self._log("feed_gap", "", gap=True, kind_=row.get("kind"))
                    if self.exit_mode == EXIT_MODE_V2:
                        self._v2_last_gap_ms = self.now_ms()
            elif rtype == "hb":
                self.feed_last_ms = self.now_ms()
            elif rtype == "skipped_no_sps":  # the detector could not time this pool: a later trigger on it is refused
                if isinstance(row.get("mint"), str):
                    self.no_sps_pools.add(row["mint"])
            elif rtype == "excluded":  # the detector kept this pool out (synthetic migration, or unclassifiable): counted, never traded, never an alert
                self._note_excluded(row)
                excluded_seen = True
            elif rtype == "pool":  # per-pool close record: BOOST last-slice timing. The rule's own clock (slots x sps) first.
                # Only a pool that ran its full horizon, with no feed gap, and whose BOOST identity is the vault PDA or the event authority
                # (not the behavioural fallback) counts; a shutdown close, a gapped pool or a guessed BOOST wallet is ledgered and ignored.
                if row.get("sealed") is True:  # a CAP-PICK-sealed pool (a pick, or never known not to be one): it carries no BOOST timing, and
                    continue  # no per-mint row is written for it (EXP-022 s9, DEC-024 s6). The BOOST rules saw nothing from it before either.
                if row.get("reason") != "horizon" or row.get("gap") is not False or row.get("boost_src") not in ("pda", "event_authority"):
                    self._log("boost_row_ignored", str(row.get("mint") or ""), why="not_horizon_clean_pda", reason_=row.get("reason"),
                              gap=row.get("gap"), boost_src=row.get("boost_src"))
                    continue
                for k in ("boost_last_slice_s", "boost_last_slice_s_blocktime", "boost_last_slice_s_recv"):
                    v = row.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        self.on_boost_row(str(row.get("mint") or ""), None, None, None, float(v))
                        if self.exit_mode == EXIT_MODE_V2:
                            self._v2_pool_row(row)
                        break
            elif rtype == "boost_slice" or schema == SCHEMA_BOOST_SLICE:  # v2 only; v1 ignores these rows as it always did
                if self.exit_mode == EXIT_MODE_V2:
                    self.on_boost_slice(row)
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
                    self._bad_intent(row, bad)
        for trg in triggers:  # triggers first: the buy path is the latency path
            self.handle_trigger(trg, seen)
        for mint, pool in watches:
            self.prefetch(mint, pool)
        if excluded_seen:  # after the buys: one counters write per tick, not per record, and none before a trigger is handled
            self.counters.save(self.counters_path)
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
            v2_arm = False
            if pos["h5"].get("exit_rule") == RULE_ID_V2 and not send:  # never for a v1 position: v1 is the code above, unchanged
                v2_arm, send = self._v2_tick(mint, pos, plan, est, now)
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
            elif (self._due(plan, "arm", est, now) or v2_arm) and not self.dry_run:  # a dry run has nothing to precompute: it cannot sign
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
        if not emergency and (pos["h5"].get("v2") or {}).get("fired"):  # v2 only (a v1 position has no "v2" record)
            rec["reason"] = "h5_v2_exit"
        pos["exit_reason"] = rec["reason"]
        self.state.pending[mint] = rec
        self.save()  # write-ahead
        with self._prio():  # the timed send goes ahead of the status polls and slot reads in the rate-limit queue
            self._send(rec, mint)
        self.save()
        plan = pos["h5"]["plan"]
        self._log("sell_sent", mint, level=rec["h5"]["level"], emergency=emergency, est_slot=est, send_slot=plan["send_slot"],
                  land_slot=plan["land_slot"], deadline_slot=plan["deadline_slot"], min_out=rec["min_out"], priority_lamports=rec["h5"]["priority"],
                  signature=rec["signature"], sent_ms=rec.get("first_send_ms"), **self._v2_sell_fields(pos, rec.get("first_send_ms")))

    def _dry_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        """Outcome-blind: simulate the sell shape against live state and log only err, CU, slot and timing. No quote, no P&L."""
        snap, _pool, _err = self._snapshot(mint, self.exit_commitment)
        row: dict[str, Any] = {"emergency": emergency, "est_slot": est, "send_slot": pos["h5"]["plan"]["send_slot"], "land_slot": pos["h5"]["plan"]["land_slot"],
                               "late_slots": est - pos["h5"]["plan"]["send_slot"], "sent": False, **self._v2_sell_fields(pos, self.now_ms())}
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
        if pos["h5"].get("exit_rule") == RULE_ID_V2:
            self._v2_close(mint)
        del self.state.open[mint]
        self.save()

    # -- landing hooks: daily counters and the halt rules ------------------------------------------------------------------
    # -- tipauth class (DEC-024 phase 2; OFF unless config tipauth_gate == TIPAUTH_ENABLE_REF) -----------------------------------------
    def _tipauth_availability(self) -> str:
        """"" when the path may admit a trigger now; else the refusal reason. Called only with the switch on."""
        if self.exit_mode != EXIT_MODE_V1:
            return "tipauth:rule_not_v1"  # C6: v2 needs its own account
        self._tipauth_check_budget()
        t = self.counters.tipauth
        if t.get("latched"):
            return "tipauth:latched_off"
        pause = t.get("pause_look")
        ack = self.h5cfg.get("tipauth_look_ack")
        if pause and not (_is_int(ack) and ack >= pause):  # a look is a human step: no automatic continuation
            return "tipauth:look_pending"
        return ""

    def _tipauth_recheck(self, trg: H5Trigger) -> str | None:
        if not self._tipauth_switch or trg.gate_path != GATE_PATH_TIPAUTH:
            return "tipauth:unavailable"
        return self._tipauth_availability() or None

    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        """Every ledger row of a trade admitted by the tipauth path carries gate_path. A mint that was never admitted that way (every
        websocket-path trade, with the switch off or on) gets nothing added: those rows are byte-identical to main's."""
        if mint and mint in self._tipauth_mints and "gate_path" not in kw:
            kw["gate_path"] = GATE_PATH_TIPAUTH
        super()._log(kind, mint, **kw)

    def _tipauth_admit(self, mint: str) -> None:
        self._tipauth_mints.add(mint)
        self.counters.tipauth["mints"] = sorted(self._tipauth_mints)
        self.counters.save(self.counters_path)

    def _tipauth_latch(self, reason: str) -> None:
        t = self.counters.tipauth
        if t.get("latched"):
            return  # logged once; never cleared by the executor
        t["latched"] = {"reason": reason, "ts_ms": self.now_ms()}
        self.counters.save(self.counters_path)
        self._log("tipauth_off", "", reason=reason, n_landed=int(t.get("n_landed") or 0), budget_lamports=TIPAUTH_LOSS_BUDGET_LAMPORTS)
        self._alert("tipauth_off", "", reason=reason)

    def _tipauth_check_budget(self) -> None:
        t = self.counters.tipauth
        if not t.get("latched") and int(t.get("realized") or 0) <= -TIPAUTH_LOSS_BUDGET_LAMPORTS:
            self._tipauth_latch("class_loss_budget")

    def _note_tipauth_landed(self, mint: str) -> None:
        """A tagged buy landed: count it. At the looks (25, 50, 100) write a counts-only row and pause the path until a human acknowledges it
        in the reviewed config (`tipauth_look_ack` >= the look). The comparison itself (ruling D5) is offline; this decides nothing from outcomes."""
        t = self.counters.tipauth
        n = t["n_landed"] = int(t.get("n_landed") or 0) + 1
        if n in TIPAUTH_LOOKS and n not in (t.get("looks") or []):
            t["looks"] = [*(t.get("looks") or []), n]
            t["pause_look"] = n
            open_n = sum(1 for m in self.state.open if m in self._tipauth_mints)
            self._log("tipauth_look", "", look=n, n_landed=n, n_open=open_n, n_closed=n - open_n,
                      action="path paused; compare tagged vs websocket-pass fills (ruling D5), then set config tipauth_look_ack >= look and reinstall")
        self.counters.save(self.counters_path)

    def _book_realized(self, delta: int, mint: str | None = None) -> None:
        if delta:
            self.counters.day(day_key(self.now_ms()))["realized"] += delta
            tagged = mint is not None and mint in self._tipauth_mints
            if tagged:
                self.counters.tipauth["realized"] = int(self.counters.tipauth.get("realized") or 0) + delta  # the class sum (ruling D4)
            self.counters.save(self.counters_path)
            if tagged:
                self._tipauth_check_budget()

    @pe.critical
    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        super()._finish_buy(mint, p, m)
        self._bal = None  # the balance moved
        self._book_realized(self.state.realized_lamports - before, mint)
        pos = self.state.open.get(mint)
        h5 = p.get("h5") or self.counters.plans.get(mint)
        if pos is None:
            self.counters.plans.pop(mint, None)  # the buy failed: no position, no plan to keep
            self.counters.save(self.counters_path)
        if pos is None or h5 is None:
            return
        pos["h5"] = {**h5, "buy_landed_slot": m["slot"]}
        self._note_landed_buy(h5["trigger"])
        if h5["trigger"].get("gate_path") == GATE_PATH_TIPAUTH:
            self._note_tipauth_landed(mint)
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

    def _note_landed_buy(self, trigger: dict[str, Any]) -> None:
        """Halt rule: pre-unlinked triggers (see pre_unlinked) above 15% of landed buys, judged from 20 landed buys on."""
        c = self.counters
        c.landed_buys += 1
        c.pre_unlinked_landed += 1 if pre_unlinked(trigger.get("base_breaks_unresolved"), trigger.get("base_breaks_unresolved_settled")) else 0
        c.save(self.counters_path)
        if c.landed_buys >= PRE_UNLINKED_MIN_LANDED and c.pre_unlinked_landed / c.landed_buys > PRE_UNLINKED_MAX_SHARE:
            self._latch("pre_unlinked_share", landed=c.landed_buys, pre_unlinked=c.pre_unlinked_landed,
                        share=round(c.pre_unlinked_landed / c.landed_buys, 4))

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
        h5pos = (pos or {}).get("h5") or {}
        priors = list(p.get("prior") or [])
        super()._finish_sell(mint, p, m)
        self._bal = None
        self._book_realized(self.state.realized_lamports - before, mint)
        if priors:
            self._settle_priors(mint, priors)  # superseded signatures that landed with an error paid their fee
        if m.get("err") is None and plan is not None and mint not in self.state.open and m.get("slot"):
            if h5pos.get("exit_rule") != RULE_ID_V2:  # v1 (and the C1-NF subclass, which overrides _note_sell_landing): the call as it was
                self._note_sell_landing(mint, plan, m["slot"], bool(p.get("h5", {}).get("emergency")))
            else:
                self._note_sell_landing(mint, plan, m["slot"], bool(p.get("h5", {}).get("emergency")), v2=h5pos)
                self._v2_close(mint)

    def _note_sell_landing(self, mint: str, plan: dict[str, Any], landed_slot: int, emergency: bool, v2: dict[str, Any] | None = None) -> None:
        """Halt rule: more than 5% of our landed sells landing after s0 + 335 s (strictly more than 5%). A v2 position (v2 = its h5 record)
        is also late when it lands more than 3 s after its exit trigger: the v2 trigger slot, or v1's s0 + 330 s when v2 did not fire."""
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
        extra: dict[str, Any] = {}
        if v2 is not None:
            late_v2, extra = self._v2_late(v2, plan, landed_slot, landing_wall)
            late = late or late_v2
        c.sells_late += 1 if late else 0
        self._log("exit_landing", mint, landed_slot=landed_slot, exit_slot=plan["exit_slot"], land_slot=plan["land_slot"],
                  error_slots=landed_slot - plan["land_slot"], late=late, emergency=emergency, landing_s=round(landing_s, 3), **extra)
        c.save(self.counters_path)
        if c.sells_landed >= LATE_SELL_MIN_N and c.sells_late / c.sells_landed > LATE_SELL_FRAC:
            self._latch("late_sells_gt_5pct", late=c.sells_late, landed=c.sells_landed)
        self._pair_boost_with_sell(mint)

    @pe.critical
    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        super()._resolve_expired(mint, p)
        self._book_realized(self.state.realized_lamports - before, mint)

    # -- loop ----------------------------------------------------------------------------------------------------------------
    def prewarm(self) -> None:
        super().prewarm()
        self.refresh_slot()  # our own slot-rate measurement needs observations whether or not a position is open
        now = self.now_ms()
        self._hour_roll(now)  # the hourly refusal_counts row is written when the hour turns, triggers or not
        self._refresh_tier(now)  # a tier change is seen (and ledgered) without waiting for the next trigger
        if not self.dry_run:
            v = self._balance()
            if v is not None:
                self._bal = (v, now)
        c = self.counters
        if c.seal_skips != self._seal_logged and now - self._seal_logged_ms >= 60_000:
            self._seal_logged, self._seal_logged_ms = c.seal_skips, now
            self._log("seal_count", "", seal_skips=c.seal_skips)  # a count and nothing else
        self._pick_feed_check(now)

    def _pick_feed_check(self, now: int) -> None:
        """DEC-024 s6: while the pick feed is missing or stale (> PICK_STALE_S) every buy in the seal window is refused; this makes that visible.
        From ORACLE_EARLIEST_MS to the seal end, once FINAL_WRITTEN exists: alert `pick_feed_unavailable` (at most every 10 minutes) when no
        oracle is configured or its live feed has no fresh heartbeat. It reads the feed's heartbeat only, never a mint, and no trigger is
        involved, so the alert says nothing about any pick. An oracle without `staleness_s` (a test double) is not judged."""
        if not (ORACLE_EARLIEST_MS <= now < self.seal_end_ms) or now - self._pick_alert_ms < PICK_FEED_ALERT_MS:
            return
        if not Path(self.final_marker).is_file():
            return
        age: float | None = None
        if self.pick_oracle is None:
            why = "no_oracle"
        else:
            fn = getattr(self.pick_oracle, "staleness_s", None)
            if fn is None:
                return
            try:
                age = fn()
            except Exception:  # noqa: BLE001 - the check never stops the loop
                age = None
            if age is not None and age <= PICK_STALE_S:
                return
            why = "stale" if age is not None else "no_fresh_heartbeat"
        self._pick_alert_ms = now
        self._alert("pick_feed_unavailable", "", why=why, staleness_s=None if age is None else round(float(age), 1), stale_limit_s=PICK_STALE_S)

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
        self._book_realized(-fee, mint)
        self._log("sell_fee_booked", mint, fee_lamports=fee, why=why)

    def _poll_priors(self) -> None:
        """One getSignatureStatuses call for the current signature and every superseded one of each pending sell. The timed supersede stops
        only WHILE some live signature shows a NON-ERROR status at processed or better (it landed); if that status disappears on a later poll (a
        processed transaction on a dropped fork) the flag is cleared and the timed supersede goes on. A superseded sell that failed on chain
        has paid its fee: book it and drop it, without touching that flag. One that landed is the sell to resolve: swap it in (it has its
        own non-error status), keep the replaced current signature in `prior` (its status and fee are still tracked), and the base confirm
        loop then reads the new current signature."""
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
        with_status: set[str] = set()  # pending sells that have a non-error status on at least one polled signature in THIS poll
        for (m, e, cur), st in zip(items, res):
            p = self.state.pending.get(m)
            if not st or p is None:
                continue
            if st.get("err") is None:  # per signature: only a status that is not an error means a sell landed or is landing
                p.setdefault("status_seen_ms", now)
                with_status.add(m)
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
                p["status_seen_ms"] = now  # the promoted signature's own non-error status
                self._log("sell_superseded_landed", m, signature=e["signature"], level=e["h5"].get("level"))
        for m in {m for m, _e, _c in items}:  # the flag is recomputed on every poll: a status that was there and is gone (a processed transaction
            p = self.state.pending.get(m)     # on a dropped fork) no longer blocks the timed supersede
            if p is not None and m not in with_status and p.pop("status_seen_ms", None) is not None:
                self._log("sell_status_vanished", m)
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
            p.pop("confirm_seen_ms", None)  # the flags belonged to the signature that just failed; the promoted one re-earns them from its own status
            p.pop("status_seen_ms", None)
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


def tipauth_gate_state(cfg: dict[str, Any], c: "H5Counters") -> str:
    if cfg.get("tipauth_gate") != TIPAUTH_ENABLE_REF:
        return "off"
    return "latched_off" if c.tipauth.get("latched") else "on"


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
        lines.append(f"[{mode}] attempts={st.attempts} (lifetime; {c.tier_attempts}/{h5.max_attempts} in {c.tier_state.get('tier', 'T0')}) realized_sol={st.realized_lamports / pe.LAMPORTS:.6f} "
                     f"open={len(st.open)}/{h5.max_open} pending={len(st.pending)} today_trades={today.get('trades', 0)} "
                     f"today_realized_sol={today.get('realized', 0) / pe.LAMPORTS:.6f} halts={sorted(c.halts)} seal_skips={c.seal_skips} "
                     f"sells_landed={c.sells_landed} sells_late={c.sells_late} synthetic_unconfirmed={c.synthetic_unconfirmed} "
                     f"excluded={sum(c.excluded.values())} ({','.join(f'{k}:{n}' for k, n in sorted(c.excluded.items())) or 'none'})"
                     + (f" tipauth_gate={tipauth_gate_state(cfg, c)} tipauth_n={int(c.tipauth.get('n_landed') or 0)}" if mode == LIVE else ""))
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
        try:
            oracle = build_pick_oracle(cfg)
        except ValueError as exc:
            print(f"h5_executor ALERT startup_refused pick_oracle_config ({exc})", flush=True)
            return 2
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
