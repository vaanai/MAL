#!/usr/bin/env python3
"""H5-BOOSTFLOOR v1 live SHADOW detector. Paper only. No keys, no transactions, no RPC writes, 0 Helius credits.

Frozen rule: /data/mal/hunt-1008/h5-flows/RULE.md (sha256 in RULE_SHA256). In one paragraph: after a non-mayhem graduation the pump.fun
BOOST agent buys 17.585 SOL of the canonical PumpSwap pool in ~29 slices over ~345 s. PumpSwap prices at (real quote + V) / base, V ~ 17.58
SOL, so sells cannot push Q = real quote + V below V. For a pool with V in [17.5, 17.7] SOL, on the FIRST non-BOOST sell at t in [0, 300] s
after the pool's first print that leaves Q <= 40 SOL while BOOST spent < 0.999 x 17.585 SOL, the rule buys (landing slot + ceil(1.3 s / sps),
binding leg ceil(1.9 s / sps)) and sells at s0 + round(330 s / sps) + ceil(0.55 s / sps). One trade per pool.

This process implements the DECISION half of that rule on the live public PumpSwap tape and logs what the rule WOULD have bought and what the
pool looked like at the planned landing and exit slots. It sends nothing. An offline scorer prices the trade with the frozen cost model; the
log also carries the no-fail P&L under the rule's own fill math so a fill can be checked at a glance.

INGEST. observe.trade_source logsSubscribe (public RPC, $0) on the PumpSwap program, decoded with observe.trade_decode (event-V fields, #467).
Every row carries t_recv_ms (local wall clock at receipt) and the slot. The pool is announced by the CreatePoolEvent in the migrate tx's logs
(a PumpSwap pool is only tracked if its CreatePool was seen on this stream; an old pool that happens to trade is never mistaken for a new one).

Q TWO WAYS (disclosed implementation point, not a rule change). Since 2026-09-30 PumpSwap v2 trades keep protocol and creator fees in the vault
and the stored V falls by the same amount, so a fixed V = 17.58 SOL misprices a fresh pool. Variant "pv" (primary) takes V PER PRINT from the
event (Q = quote_reserve + virtual_quote_reserves). Variant "fv" is the frozen rule's literal fixed V (V of the pool's first print). Both are
evaluated on every print, each fires at most once per pool, and every record carries both Qs.

OUTPUT. <out-dir>/h5-shadow-<UTC hour>.jsonl, strict JSON lines flushed per line (tools/tape_lines.py clean), and h5-shadow-status.json
(atomic). Record types: start, trigger, outcome, outcome_bx10, pool (EVERY tracked pool, with the BOOST last-slice time relative to s0), gap,
hb, stop, boost_slice.

BOOST SLICE STREAM (DEC-028 section 5 item 1; record-only, no price). One `boost_slice` record per BOOST buy in a tracked pool, written as the
print is processed: pool, mint, s0, slot, tx_index (replay tape; null on the live feed, which has no block position), event_index, signature,
lamports, sol, block_time (s), t_recv_ms, t_i_s (pool clock, (slot - s0) * sps, null without an sps), sps, boost_src, boost_id, boost_n and
boost_spent_sol (that signer's buys and cumulative spend so far). Only buys signed by the pool's vault identity are written (boost_src "pda" or
"event_authority": protocol addresses known before any of their buys); a behavioural guess never is. The H5 executor's v2 exit (DEC-028
Option B) reads these; v1 ignores them. Seals, as for the trigger record: a CAP-PICK-sealed pool has none (a pool the pick oracle has no answer
for yet has its slices held in memory, written with withheld_pick_pending once it is known to be a non-pick, dropped if it is a pick); an H5
Look-2-sealed pool has them, flagged h5_look2_sealed (its trigger and pool records, which carry its BOOST timing, are written too). No decision
record changes: nothing here feeds a trigger, an outcome or a pool record.
A gap record (slot jump, silence, feed restart) also flags every pool open at the time (gap=true on its pool/trigger/outcome records); an
hour with a gap record or a missing hb is a bad hour.

EXIT LADDER. Every outcome record also carries the pool at the exit landing slot for exit triggers at s0 + 310/320/330/335/340/345/350 s
(the rule exits at 330 s; VERIFY.md found the exit sits on a cliff). Report-only; the rule's exit is the 330 s row.

BX10 (report-only paper exit variant; not v1, never traded, not read before a pre-registered read, EXP-026 draft). OFF BY DEFAULT: it is computed
and written only when the process is started with --bx10-enable EXP-026 (scripts/research/h5-shadow.sh passes H5_BX10_ENABLE through when it is
set). The draft pre-registration (PR #542) wants v2's outcomes in a WITHHELD store and an EXP-024 "G-v2" amendment merged before the shadow
computes v2 at all, so until then the shadow does no bx10 work: no outcome_bx10 record, no computation, no counter, and every other record is
byte-identical to a build without bx10. Any other value (an empty string, "1", "exp-026") counts as off; the start record's "bx10" says which.
Cell C10 of
H5-BOOSTCLOCK-EXIT (/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/FREEZE.md s2, bc_rule.py): the same trade as each v1 outcome (same trigger,
entry legs, END-bound states, fees), only the exit instant differs. From the BOOST signer's buys (slices) observed at least 1.35 s before the
decision instant, projection E gives the last slice's time; the exit triggers at the first instant tau >= our landing with >= 3 slices observed
and tau >= projection - 10 s, at slot min(max(s0 + ceil(tau / sps), landing + 1), v1's exit slot); capped at v1's s0 + 330 s (then it IS v1's
exit). Written as its own record, type "outcome_bx10", right after v1's outcome and only where v1's outcome is written (the same CAP-PICK and
H5 Look-2 seals), so every v1 record is byte-identical with or without it. The signer: when the trigger-time pick was the behavioural
detector's, that signer's slices are used; otherwise the per-pool PDA (or BoostBuyAndBurn authority) once it has signed a buy. Each record
carries boost_src_at_trigger, so a scorer can see which pools changed signer after the trigger.

H5 LOOK-2 SEAL (EXP-024 section 3.1, Amendment 2). Pools whose s0 block time is at or after H5_LOOK2_START_MS (2026-10-16T00:00Z, Look 2's
added window) have every outcome-bearing record withheld (outcome, legs, exit ladder, strip, min_q), exactly as the CAP-PICK seal withholds them,
unless the process was started with --h5-look2-observed EXP-024-Am2 (declared observation, Amendment 2). The declaration covers
[2026-10-16T00:00Z, 2026-11-06T00:00Z) only (H5_LOOK2_END_MS): a pool with s0 at or after the end stays withheld whatever the flag says.
Trigger records, which carry the decision-time state the executor needs, are still written for such pools. The CAP-PICK seal (from
2026-10-16T01Z) applies on top and does not depend on the flag: a pick stays fully sealed.

CAP-PICK PICK ORACLE (EXP-022 section 9, DEC-024 section 6). From 2026-10-16T01Z a pool is written in full only while the CAP-PICK oracle
(tools.cap_pick_oracle.PickOracle, from CAP_PICK_LIVE / CAP_PICK_REPLAY / CAP_PICK_FINAL_MARKER) says its mint is NOT a pick. The answer has three
states and is asked at each decision point: a pick is sealed for good; "not a pick" lets the pool through; "no answer" (the live gate decides
after the pool's first print, or the feed is stale > 60 s) seals it for now, and a trigger that fires then is withheld for good. Without the
environment the always-true stub seals every pool in the window, as before. With the oracle the sealed_hour `decisions` count is withheld.

TIP UNION (REPORT-ONLY, trade-rate phase 0; off unless --tip-dir is given, and then no decision reads it). The executor gates on
base_breaks_unresolved_settled, the feed's own view of the pool's base chain. With --tip-dir the shadow also tails the tip follower's hourly
trades-<UTC hour>.jsonl (TipTail: incremental, on the event loop, a byte search per line and a JSON parse only for a tracked pool's row) and
keeps, per tracked pool, the same multiset check on the UNION of feed prints and tip rows keyed by (signature, event_index) (UnionChain).
Trigger and pool records then carry base_breaks_unresolved_settled_union (null when the tip is stale, unreadable, starting or broken) and
base_union {status, ws_only, tip_only, both, both_diff, dup, tip_first_slot, ...}; pool records also carry base_unmatched_ws /
base_unmatched_union (up to 20 unmatched prints each, so missing-vs-extra can be settled offline). The tip tape is not chain truth either (it
misses prints in some pools); the union does not see a dropped head, and s0, BOOST spent/identity and the first qualifying sell still come from
the feed. Without --tip-dir every record is byte-identical to a build without this code.
TIP-AUTHORITATIVE VIEW (REPORT-ONLY; base_breaks_unresolved_settled_tipauth and base_tipauth, on the same records as the union fields and sealed
exactly like them; no decision reads them, and a gate on them is a DEC-024 phase-2 change). The union only ADDS rows, so it cannot remove a
"phantom": a swap notice the public logsSubscribe feed delivered that getTransaction (finalized) did not find (it shares its pre base with a real
print and adds one unmatched pre base). Every tip row comes from a finalized block; that does not make the tape a complete record (a sample found
3 of 50 websocket-only prints on the finalized chain). The tipauth value is the settled count of the union MINUS D = websocket-only members, at a
slot in [tip_first_slot, min(tip high-water, decision slot) - TIPAUTH_MARGIN_SLOTS], whose signature has no tip row in this pool under any
event_index. A value of 0 does NOT prove that no real print was dropped: a prefix run, a suffix run at pool close and an interior drop whose
first kept successor sits in the settle window all read 0. base_tipauth.orphaned_by_drop_n (a NET count per base value, no settle exemption) and tipauth_nosettle (the count with the exemption off)
are cross-checks for the last case, not a proof; base_tipauth.dropped lists the first 20 members of D by slot. The field is null when
a full tip chunk may be unread (status tip_pending). It is computed on the executor's buy path (cost bounded by TIPAUTH_WSONLY_MAX). Definition,
margin justification (from tools/fast_tip_follower.py), null statuses and what is still open: Engine._tipauth_fields.
DECISION TIME, enforced in code (UnionChain.line / ahead): a tip row is admitted into a pool's union only once that pool's feed has printed at
its slot or later; rows above that line wait in the pool's `ahead` heap. So the TRIGGER record's union fields (the settled union value and
tip_only, both, dup, both_diff, tip_first_slot, ws_only) count only tip rows at slot <= the trigger print's slot, also when the tip leads the
feed. When the trigger print is a slot regress below rows already admitted, the fields are null with status "regress". The trigger record's
base_union carries the reader's health as tip_hw_slot_global / tip_lag_slots_global: the reader's high-water over ALL pools at the detect
instant (and the feed's high-water minus it), relabelled because they are not filtered to the trigger slot. The POOL CLOSE record admits every
tip row read so far first, so its union fields (tip_hw_slot, tip_lag_slots included) are exactly those of the eager union.
SEALED: a CAP-PICK-sealed pool's records carry none of the union fields. An H5 Look-2-sealed pool's POOL CLOSE record carries none either (it
stays exactly as without the union); its trigger record keeps the decision-time ones above. hb / status carry tip_union: the reader's health
(status, resets on truncation / rotation, error counts, rate-limited error log) and mem, a memory self-report of the union structures next to
the process RSS. PRE-TRADE fields of the trigger record, for any comparison with the tip's row of the same (signature, event_index) (the record
carries the trigger print's own event_index; a replay row's comes from tape_event_index): real_quote_pre (quote_reserve) and base_pre
(base_reserve) are the PumpSwap row's reserves BEFORE the trade, the convention of the feed and of the tip's PumpSwap rows (bonding-curve rows
are POST-trade: never compare across venues without checking); q_pv_pre_sol / q_fv_pre_sol add V; v_print is the print's virtual reserve.
q_trigger_sol, q_pv_post_sol and q_fv_post_sol are POST-trade. The start record's code_sha is the git sha of the running checkout (null if
unknown); DEC-024's phase-2 data clock counts from it.

FRESH FORWARD (--fresh-forward ARENA, hunt20; the forward-paper arena). OFF BY DEFAULT: only the exact token ARENA turns it on (the bx10 pattern: any other
value counts as off, with a warning), and with the flag absent nothing below runs and every record is byte-identical to a build without it. On:
(a) --out-dir is required and must resolve (symlinks and '..' removed) to a directory under /data/mal/arena/h5-* or $HOME/data/arena/h5-* (mal-fast-0);
/srv/mal-h5-shadow, /var/lib/mal-live and $HOME/data/h5-shadow* are refused explicitly, so this mode never feeds the live executor; (b) the CAP-PICK oracle seal
(EXP-022 withdrawn 2026-10-15; no CAP_PICK_* environment is read) and the START of the H5 Look-2 seal (EXP-024 Am.2 covers v1 from 2026-10-16, Am.9 the arena's
v2) are not applied: every pool whose s0 is at or after the start record's time gets its trigger, outcome and pool records. The END of the Look-2 seal is applied,
fail closed: a pool with s0 at or after 2026-11-06T00Z gets decision records only (no outcome, strip or ladder). A pool whose s0 is before the start (block time,
whole seconds, or receipt time) is never tracked and writes nothing (counter fresh_ignored_pre_start); (c) bx10 / outcome_bx10 (the v2 BOOSTCLOCK exit) is always
computed and written; (d) the tip union is off (--tip-dir is refused); (e) the start record carries fresh_forward "ARENA", code_sha, start_ms (= its t_ms), dirty
(H5_DIRTY=1, set by the wrapper under ARENA_H5_ALLOW_DIRTY), endpoints, host and the seals as not applied. Two further changes of the records, this mode only: the
strip of a triggered pool also carries `buys` ([slot, lamports] of every buy from 50 slots before the first trigger print), so an offline scorer can recompute the
pressure inputs at any landing slot; and the pool record of a pool that is not the literal plain, RPC-classified class (synthetic, unclassified, classified
elsewhere) carries no min_q_pv_sol / min_q_fv_sol / pv_fv_disagree_sells (DEC-031 s3.5 embargo, until 2026-11-14T00Z). The rule, the synthetic-pool gate and the
decision logic are those of the default mode. A window that starts at process start is out-of-sample by construction.

REPLAY. --replay-tape runs the SAME engine over the audit's exploration tape (/data/mal/audit-1008/tape) so the live code can be checked
against the frozen rule's own trigger list (--compare-frozen). The PR body carries the numbers.

Run: python -m tools.h5_shadow --out-dir /var/lib/mal/h5-shadow   (MiScusi job: bash scripts/research/h5-shadow.sh)
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/h5_shadow.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import base64
import bisect
import collections
import heapq
import json
import logging
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from observe.trade_decode import (
    PUMPSWAP_PROGRAM,
    WSOL_MINT,
    _program_data_bytes,
    decode_extra_event,
    decode_program_data,
    iso_from_ms,
    records_from_logs,
)
from observe.link_state import GAP_EDGES_MS, REL_SILENT_MS_DEFAULT, SILENT_MS_DEFAULT
from tools.cap_pick_oracle import PickOracle, from_env as cap_pick_from_env
from tools.paper_curve_math import pumpswap_sol_fee_ppm
from tools.pump_structure_monitor import (  # pure python; the classifier semantics are the monitor's, not re-implemented here
    DEFAULT_RPC,
    DISC_COMPLETE,
    DISC_MIGRATE,
    PUMP_PROGRAM,
    CallBudgetExceeded,
    RpcClient,
    b58decode,
    decode_complete_event,
    decode_migration_event,
    find_program_address,
    post_complete_buy_seen,
    tx_event_blobs,
)

log = logging.getLogger("mal.h5_shadow")

SCHEMA = "h5_shadow_v1"
RULE_ID = "H5-BOOSTFLOOR v1"
RULE_SHA256 = "c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c"  # /data/mal/hunt-1008/h5-flows/RULE.md
DEFAULT_OUT_DIR = os.path.join(os.path.expanduser("~"), "data", "h5-shadow")  # user-writable; no sudo needed on fast-0

# ---- the rule's numbers (RULE.md). Do not edit without a new pre-registration ---------------------------------
V_LO = 17.5e9  # lamports, universe: first print's V in [V_LO, V_HI]
V_HI = 17.7e9
SPS_LO, SPS_HI = 0.15, 0.6  # seconds per slot must lie in this open interval
Q_STAR_SOL = 40.0  # Q = real quote + V after the sell, SOL


Q_STAR_ALLOWED = (40.0, 50.0)  # QP ruling 2026-10-22: exactly these two levels; any other value is a different, unregistered rule
Q50_RULE_ID = "H5-BOOSTFLOOR-Q50 v1"  # the rule_id of a Q* = 50 process (EXP-024 Amendment 10, owed)
Q50_RULE_SHA256: str | None = "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"  # sha256 of EXP/EXP-024-RULE-Q50.md (PR #790); the sha only, the file is never read. None = a Q* != 40 live process refuses to start


def check_q_star(v: Any) -> float:
    """--q-star-sol / Engine(q_star_sol=): a real number (not a bool) equal to 40.0 or 50.0; anything else is refused."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or float(v) not in Q_STAR_ALLOWED:
        raise ValueError(f"q_star_sol must be one of {Q_STAR_ALLOWED}, got {v!r}")
    return float(v)


def _q_star_arg(text: str) -> float:
    try:
        return check_q_star(float(text))
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


T_MIN_S, T_MAX_S = 0.0, 300.0
BOOST_BUDGET = 17.585e9
BOOST_DONE_FRAC = 0.999
BOOST_MIN_BUYS, BOOST_BUY_MIN, BOOST_BUY_MAX, BOOST_TOTAL_MAX = 3, 0.2e9, 2.0e9, 17.7e9
ENTRY_S = {"primary": 1.3, "binding": 1.9}
EXIT_AFTER_S0_S = 330.0
EXIT_LAG_S = 0.55
EXIT_LADDER_S = (310.0, 320.0, 330.0, 335.0, 340.0, 345.0, 350.0)  # report-only exit-shift sensitivity (VERIFY.md: the exit sits on a cliff)
BX10_OFF_S = 10.0  # bx10 (report-only): exit this long before the projected last BOOST slice (FREEZE.md cell C10)
BX10_OBS_S = 1.35  # a slice at tape time t is usable for a decision at tau >= t + this (1.9 s entry latency - 0.55 s send-to-land)
BX10_KMIN = 3  # slices needed before a projection exists
BX10_DONE_LAMPORTS = 0.05e9  # BOOST budget left below this: the last slice has been seen
PRESSURE_WINDOW_S = 2.0
PRIO_LAMPORTS = 55_000
STAKES = (("0.1", 100_000_000), ("0.25", 250_000_000))

# ---- implementation constants (not the rule) ---------------------------------------------------------------
POOL_LIFE_S = 400.0  # a pool is followed from its first print to s0 + 400 s, then dropped
OUTCOME_GRACE_SLOTS = 3  # resolve a planned slot X once the global slot high-water mark reaches X + this
WALL_CLOSE_S = 430.0  # wall-clock backstop for closing a pool when the slot clock stalls
LP_FRAC = 0.002  # LP fee stays in the pool; used only when the event lacks lp_fee / pool_quote_amount (tape replay)
CHAIN_TOL = 2e-3  # |Q_pre(i+1) - Q_post(i)| / Q above this counts as a quote-chain break
GAP_SLOTS = 30  # a jump of more than this many slots between consecutive events is logged as a gap
SILENCE_S = 20.0  # no event for this long is logged as a gap
SPS_WINDOW_S = 300
SPS_MIN_SPAN_S = 8  # bootstrap: ready about 10 s after the first PumpSwap print of ANY pool; the fit sharpens as the window fills
SPS_MIN_POINTS = 8
VARIANTS = ("pv", "fv")
STRIP_MAX_ROWS = 20_000  # per triggered pool; a longer strip is cut and flagged
FRESH_BUYS_BACK_SLOTS = 50  # fresh forward: strip.buys reach this many slots before the first trigger print (the pressure window is 2 s, at most ~14 slots)
# CAP-PICK seal: the counted walk-2 window starts 2026-10-16T01:00Z. Inside it, a pool's outcome states and price strip are withheld unless a
# pick oracle says the mint is NOT a pick. Fail closed. Decision-time records (trigger) are unaffected.
SEAL_START_MS = int(datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
SEAL_REASON = "cap_pick_seal"
# H5 seal for EXP-024 Look 2's added window (EXP-024 section 3: the section 3 seal applies to the canary's and shadow's outcomes for pools with
# s0 at or after 2026-10-16T00 until Look 2 is read), lifted only by Amendment 2's declared observation. Independent of the CAP-PICK seal above.
H5_LOOK2_START_MS = int(datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
# The declared window is [H5_LOOK2_START_MS, H5_LOOK2_END_MS) only (Amendment 2). A pool with s0 at or after the end is outside it: its outcomes
# stay withheld whatever the flag says.
H5_LOOK2_END_MS = int(datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
# Q50 successor base S* (QP-RULING-1024-successor R7(b)): the end-seal of a --fresh-forward ARENA engine only (Engine.fresh_start_ms set). Chosen once,
# never edited (another value is another sha with new proofs). H5_LOOK2_END_MS and every non-fresh path are unchanged.
SUCC_END_MS = int(datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
H5_LOOK2_AMENDMENT_REF = "EXP-024-Am2"  # the only value --h5-look2-observed accepts
BX10_ENABLE_REF = "EXP-026"  # the only value that turns the report-only bx10 exit variant on (--bx10-enable / H5_BX10_ENABLE); anything else is off
H5_LOOK2_SEAL_REASON = "h5_look2_seal"
# FRESH FORWARD (arena, hunt20). The only value that turns --fresh-forward on; anything else is off (like BX10_ENABLE_REF).
FRESH_FORWARD_REF = "ARENA"
ARENA_ROOT = "/data/mal/arena"  # --out-dir must resolve to a directory under ARENA_ROOT/h5-* ...
ARENA_HOME_SUBDIR = ("data", "arena")  # ... or under $HOME/data/arena/h5-* (mal-fast-0, where the H5 arena book runs)
ARENA_OUT_PREFIX = "h5-"
# Never an out dir of a fresh-forward run: the live executor's feed (the bind to the fast-0 shadow dir) and the live ledger. $HOME/data/h5-shadow* is
# added at call time (check_fresh_out_dir): the default shadow dir of this tool and of scripts/research/h5-shadow.sh.
FRESH_FORBIDDEN_OUT = ("/srv/mal-h5-shadow", "/var/lib/mal-live")
SEEN_TTL_S = 1200.0
ANNOUNCE_TTL_S = 1200.0  # must be >= SEEN_TTL_S: an announcement may not expire before the pool it announced can be forgotten
SETTLE_SLOTS = 2  # a print this close to the high-water slot may still be waiting for a predecessor that is in flight
REL_SILENT_MS = REL_SILENT_MS_DEFAULT  # 3 s: a socket quiet this long while a peer DELIVERED after its last notice is silent (also LinkState's drop-ended quiet)
LOOK_MS = 5_000  # housekeeping interval
RECENT_MARGIN_MS = 5_000
SEALED_MIN_COUNT = 5  # sealed_hour reports decisions only from this many; below it the field is "<5"
LOOKS_WINDOW = 3  # socket-drop memory: the last 3 looks (3 x 5 s housekeeping) decide whether every socket was down
REJECT_LOG_MAX = 50  # reject records written per run (the counters are unbounded)
# heuristic only (counter unannounced_fresh): a first-seen print that looks like a new pool's first print
FRESH_REAL_QUOTE_MAX = 100 * 10**9
FRESH_BASE_MIN, FRESH_BASE_MAX = 1.8e14, 2.1e14
ERRORS_MAX_BYTES = 5_000_000
# TIP UNION (report-only, phase 0 of the trade-rate work). Nothing below feeds a decision or changes an existing record field.
TIP_DIR_DEFAULT = "/var/lib/mal/sealed/fast-trades-tip"  # the tip follower's hourly trades-<UTC hour>.jsonl (getBlock rows, seal_trade shape)
UNION_FIELD = "base_breaks_unresolved_settled_union"
UNION_WS, UNION_TIP, UNION_BOTH = 1, 2, 3  # source mask of one union member
TIP_STALE_MS = 60_000  # no new tip line for this long: the union field is null ("stale")
TIP_STALE_SLOTS = 150  # tip high-water behind the feed's high-water by more than this (~60 s): null ("stale")
TIP_POLL_S = 0.25  # tip poll interval; a full chunk is followed at once by the next one (after yielding to the event loop)
TIP_CHUNK_BYTES = 128 * 1024  # one read per file per step: ~150 rows, a fraction of a millisecond of filtering
TIP_LINE_MAX = 64 * 1024  # a partial line longer than this is dropped (corrupt tail)
TIP_PENDING_MAX = 5_000  # tip rows buffered per announced-but-not-yet-tracked pool (the tip lags the feed, so this is normally empty)
TIPAUTH_FIELD = "base_breaks_unresolved_settled_tipauth"
TIPAUTH_MARGIN_SLOTS = 4  # see UnionChain.settled_without: a slot is "covered" only at or below (tip high-water - this)
TIPAUTH_DROPPED_LIST_MAX = 20  # base_tipauth.dropped: members of D listed (report-only); dropped_n is the full count
TIPAUTH_WSONLY_MAX = 3_000  # more websocket-only members than this in one pool: the tip-authoritative field is null ("big"). The field is computed
# between detect and emit (the executor's buy path): measured worst case, every member websocket-only and droppable, 1 run best-of-5 on one
# host: 20,000 -> 29.7 ms (too slow, so lowered), 5,000 -> 6.1 ms, 2,000 -> 2.3 ms
UNION_SETTLE_SCAN_MAX = 256  # slots above the settle line scanned incrementally; more than this: null at a trigger, a full count at pool close
UNMATCHED_MAX = 20  # unmatched prints listed per pool-close record (per view)
TIP_ERR_LOG_EVERY_S = 600.0  # tip_follow: the first error of each class goes to the error log, then at most one per class per this many s
# hb memory self-report of the union structures (tip_union.mem): bytes per item, measured with tracemalloc on synthetic chains (88-char
# signatures, 1e14-size reserves; 329-479 B per member incl. its Counter entries, by_slot ref and the Pr.ukey tuple) and rounded up. An estimate
# for trend-reading; the process RSS / RssAnon / VmHWM next to it are the measured numbers.
UNION_MEMBER_BYTES_EST = 512
UNION_PENDING_ROW_BYTES_EST = 384
# tipauth side tables (tracemalloc, 88-char signatures, shared strings): a wsonly dict entry with its meta tuple ~136 B, a tip_sig set entry
# ~105 B; rounded up. Reported in hb as tip_union.mem.est_tipauth_mb, SEPARATE from est_mb (whose meaning is unchanged: members + pending rows).
UNION_WSONLY_BYTES_EST = 160
UNION_TIPSIG_BYTES_EST = 128
ERROR_RECORDS_MAX = 20

# ---- synthetic-migration class (EXP-024 Amendment 4 / DEC-024 Amendment 2): H5 buys nothing on a synthetic or unclassifiable pool ----------------
# A pool is synthetic iff a PostCompleteBuyEvent is seen in the mint's CompleteEvent tx OR in the pool's migrate (CreatePool) tx when the two differ
# (tools.pump_structure_monitor.post_complete_buy_seen: the discriminator alone, so a layout change over-counts synthetic rather than hiding it; the
# monitor ORs the two the same way). It is plain only when BOTH txs were read and neither carries one; a tx that cannot be found or read leaves the pool
# unclassified. Sources, in order: the websocket (the PumpSwap feed's CreatePool notice is the migrate tx; the pump.fun logsSubscribe delivers the
# CompleteEvent tx), an off-hot-path RPC lookup of the curve's txs (rpc), else unknown (None -> excluded as "unclassified").
# Replay only: PCB_FIRST_DEPLOY_SLOT is the 2026-10-02T15:47Z pump deploy (slot 452654932), the first deploy that MIGHT emit PostCompleteBuyEvent: the v3
# buys were already in that binary, and the s04 string scan (no PostCompleteBuy string at slot 454473446, 0 of 61 sampled) is a sample, not proof that it
# could not. A pool whose first print is below this slot (every replayable hour: < 2026-10-02T10) completed on an older binary and is plain by
# "pre_event_binary". A pool in the 10-02..10-08 window (first print at or after the slot) is NOT assumed plain: PreEventClassifier returns unknown and
# replay excludes it as unclassified. The first PostCompleteBuyEvent actually seen is slot 454600658 (2026-10-08T16:39Z), after the 10-08T16:20Z redeploy
# (slot 454596459). The s05 discriminator scan reports found=0 for every event, TradeEvent included, so it cannot discriminate and is not used.
PCB_FIRST_DEPLOY_SLOT = 452_654_932
PRE_EVENT_SRC = "pre_event_binary"
SYN_MAX = 50_000  # classified mints kept (count-bounded)
RPC_ATTEMPT_DELAYS_S = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0, 45.0, 60.0, 60.0, 60.0)  # attempts and the wait before each: backoff over ~306 s, i.e. through the trigger window (T_MAX_S = 300) and inside POOL_LIFE_S; stops as soon as the class is settled
RPC_ATTEMPT_TIMEOUT_S = 12.0  # wall bound of one attempt, enforced on the awaiting side; the worker thread is bounded by its client's own timeout
RPC_SIGS_LIMIT = 100  # getSignaturesForAddress(curve) page: 10-59 failed sniper txs crowd a short window (a 10-window missed the completing tx in 20 of 160 graduations)
RPC_TX_PER_ATTEMPT = 8  # non-failed txs fetched per attempt, newest first (two txs are needed: the migrate tx and the CompleteEvent tx; 5 missed 3 of 10 in the cost run)
RPC_MAX_INFLIGHT = 8  # concurrent fallback lookups; a request beyond this is dropped (the pool stays unclassified -> excluded)
RPC_MIN_INTERVAL_S = 0.25  # pacing of the ONE shared public RPC client: a global rate of at most 4 requests per second, retries included
_COMPLETE_EVENT_PREFIX = base64.b64encode(DISC_COMPLETE).decode()[:10]
_MIGRATE_EVENT_PREFIX = base64.b64encode(DISC_MIGRATE).decode()[:10]

_CREATE_POOL_PREFIX = base64.b64encode(bytes.fromhex("b1310cd2a076a774")).decode()[:10]
_BOOST_EVENT_PREFIX = base64.b64encode(bytes.fromhex("3f451c16305cc2b9")).decode()[:10]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def tier_fee(q: float, b: float) -> float:
    """Canonical PumpSwap total fee on the market cap of a (q incl. V, b) state, as a fraction."""
    return pumpswap_sol_fee_ppm(q / b * 1e6) / 1e6


def clean(obj: Any) -> Any:
    """JSON-safe copy: NaN / inf become None (strict JSON, allow_nan=False)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def cap_pick_seal_oracle_stub(mint: str | None) -> bool:
    """Placeholder pick oracle: True = suppress. Inside the seal window every mint is treated as a possible pick, so everything is suppressed.
    run_live uses it only when the CAP-PICK oracle is not configured (cap_pick_oracle_from_env)."""
    return True


def cap_pick_oracle_from_env(environ: dict[str, str] | None = None) -> tuple[PickOracle | None, dict[str, Any]]:
    """The CAP-PICK pick oracle for run_live, from the same environment as the C1-NF shadow (tools.cap_pick_oracle.from_env): CAP_PICK_LIVE
    and CAP_PICK_REPLAY (os.pathsep lists), CAP_PICK_FINAL_MARKER (required), CAP_PICK_STALE_S (default 60). (oracle, start-record info).
    Not configured -> (None, ...): the engine keeps the always-true stub and seals every pool from 2026-10-16T01Z (fail closed)."""
    env = os.environ if environ is None else environ
    try:
        o = cap_pick_from_env(env)
    except (ValueError, OSError) as exc:  # e.g. CAP_PICK_STALE_S not a number
        return None, {"oracle": "stub_always_true", "why": f"cap_pick_env_error:{type(exc).__name__}"}
    if not isinstance(o, PickOracle):
        return None, {"oracle": "stub_always_true", "why": "CAP_PICK_LIVE and CAP_PICK_FINAL_MARKER are not both set"}
    o.stale_s = min(o.stale_s, 60.0)  # DEC-024 s6: never trust a feed older than 60 s here, whatever CAP_PICK_STALE_S says (lower is allowed)
    n_live = len([p for p in env.get("CAP_PICK_LIVE", "").split(os.pathsep) if p])
    n_rep = len([p for p in env.get("CAP_PICK_REPLAY", "").split(os.pathsep) if p])
    return o, {"oracle": "cap_pick_oracle", "live_files": n_live, "replay_files": n_rep, "final_marker": True, "stale_s": o.stale_s}


def project_e(t: Sequence[float], a: Sequence[float]) -> float:
    """bx10 projection E (FREEZE.md s2; same steps as bc_rule.project_E): the projected last BOOST slice time (s after s0) from the k >= 2 slices
    seen so far (times t, lamports a): t_k + floor(R / mean(a)) * median(t_2 - t_1, ..., t_k - t_(k-1)), R = 17.585 SOL - sum(a); with
    R < 0.05 SOL the budget is done and the last slice is t_k."""
    k = len(t)
    d = sorted(t[i] - t[i - 1] for i in range(1, k))
    m = len(d)
    med = d[m // 2] if m % 2 else (d[m // 2 - 1] + d[m // 2]) / 2
    tot = float(sum(a))
    rem = max(0.0, BOOST_BUDGET - tot)
    if rem < BX10_DONE_LAMPORTS:
        return t[-1]
    return t[-1] + math.floor(rem / (tot / k)) * med


def bx10_exit(t: Sequence[float], a: Sequence[float], tmin: float, cap: float, off: float = BX10_OFF_S, obs: float = BX10_OBS_S,
              kmin: int = BX10_KMIN) -> tuple[float, bool, int, float | None]:
    """bx10 decision instant (same steps as bc_rule.cad_exit): tau* = inf{tau >= tmin : k(tau) >= kmin and tau >= P_E(k(tau)) - off}, where
    k(tau) counts slices with t_i + obs <= tau (t sorted, s after s0); capped at cap. Only slices observed before the decision are used.
    Returns (tau*, fired before the cap, k slices used, projection used or None)."""
    n = len(t)
    if n < kmin:
        return cap, False, n, None
    ob = [x + obs for x in t]
    k = max(kmin, bisect.bisect_right(ob, tmin))
    while k <= n:
        start = max(tmin, ob[k - 1])
        end = ob[k] if k < n else math.inf
        proj = project_e(t[:k], a[:k])
        cand = max(start, proj - off)
        if cand < end:
            return (cand, True, k, proj) if cand < cap else (cap, False, k, proj)
        if end >= cap:
            return cap, False, k, proj
        k += 1
    return cap, False, n, None


def sps_ok(sps: float | None) -> bool:
    return sps is not None and SPS_LO < sps < SPS_HI


# ---- slot clock ---------------------------------------------------------------------------------------------
class SlotClock:
    """Seconds per slot from (slot, unix block time) pairs of the events themselves: a rolling window of the first slot seen in each
    new block-time second. No RPC. None until the window spans SPS_MIN_SPAN_S."""

    def __init__(self, window_s: int = SPS_WINDOW_S, min_span_s: int = SPS_MIN_SPAN_S, min_points: int = SPS_MIN_POINTS) -> None:
        self.window_s, self.min_span_s, self.min_points = window_s, min_span_s, min_points
        self._pts: collections.deque[tuple[int, int]] = collections.deque()  # (block time s, first slot seen)
        self._dirty, self._cache = True, None

    def observe(self, slot: int, ts: int | None) -> None:
        if not ts:
            return
        if self._pts and ts <= self._pts[-1][0]:
            return
        self._pts.append((int(ts), int(slot)))
        while len(self._pts) > 2 and self._pts[-1][0] - self._pts[0][0] > self.window_s:
            self._pts.popleft()
        self._dirty = True

    def n_points(self) -> int:
        return len(self._pts)

    def span_s(self) -> int:
        return self._pts[-1][0] - self._pts[0][0] if len(self._pts) >= 2 else 0

    def sps(self) -> float | None:
        """Least-squares slope of block time on slot over the window (cached until a new second arrives)."""
        if self._dirty:
            self._dirty, self._cache = False, self._fit()
        return self._cache

    def _fit(self) -> float | None:
        n = len(self._pts)
        if n < self.min_points or self.span_s() < self.min_span_s:
            return None
        t0, s0 = self._pts[0]
        xs = [s - s0 for _, s in self._pts]
        ys = [t - t0 for t, _ in self._pts]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        return slope if slope > 0 else None


# ---- pool arithmetic ----------------------------------------------------------------------------------------
def post_state(q_pre: float, b_pre: float, buy: bool, sol: int, tok: int, lp_fee: int | None, pool_quote_amount: int | None) -> tuple[float, float]:
    """(delta Q incl. V, delta base) of one print, from its PRE-trade state (q_pre includes V).

    With the event's own fields (live): a buy adds pool_quote_amount + lp_fee to the pool, a sell removes pool_quote_amount - lp_fee
    (the CP gross less the LP fee, which stays). Protocol / creator fees that v2 keeps in the vault move stored V the other way, so Q
    is unchanged by them. Without those fields (tape replay): constant product on the base move, LP fee 0.2 % kept in the pool.
    Base moves exactly by token_raw either way."""
    db = float(-tok if buy else tok)
    if pool_quote_amount:
        lp = lp_fee or 0
        dq = float(pool_quote_amount + lp if buy else -(pool_quote_amount - lp))
        return dq, db
    b_post = b_pre + db
    if b_post <= 0:
        return 0.0, db
    q_cp = q_pre * b_pre / b_post
    dq = (q_cp - q_pre) * (1 + LP_FRAC) if buy else -(q_pre - q_cp) * (1 - LP_FRAC)
    return dq, db


def fill_round_trip(qe: float, be: float, qx: float, bx: float, stake: float) -> tuple[float, float]:
    """(pnl lamports before any fail model, gross) of a buy at landing state (qe, be) and a sell at exit state (qx, bx). Own impact on Q
    incl. V; the buy stays in the pool until the sell; 55,000 lamports on each send. Same arithmetic as RULE.md / s14_boostdip.py."""
    f = tier_fee(qe, be)
    net = stake * (1 - f)
    tk = be * net / (qe + net)
    q2 = qx + net
    b2 = bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - tier_fee(q2, b2 + tk))
    return proceeds - stake - 2 * PRIO_LAMPORTS, (q2 / b2) / (qe / be) - 1


class Pr:
    """One PumpSwap print of a tracked pool."""

    __slots__ = ("slot", "buy", "trader", "sol", "tok", "q", "v", "b", "dq", "db", "kept", "recv_ms", "ts", "sig", "v_missing", "ukey", "ei")

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def q_pre(self, variant: str, v0: int) -> float:
        return float(self.q + (self.v if variant == "pv" else v0))

    def q_post(self, variant: str, v0: int) -> float:
        return self.q_pre(variant, v0) + self.dq + (self.kept if variant == "fv" else 0)

    @property
    def b_post(self) -> float:
        return self.b + self.db


def _merge(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def common_down(states: Sequence[dict], now_ms_: int, rel_silent_ms: int = REL_SILENT_MS, min_end_ms: int | None = None) -> list[tuple[int, int]]:
    """Intervals during which EVERY socket was down or silent. states: observe.link_state snapshots {"up", "down_since_ms", "intervals",
    "last_notice_ms", "last_delivery_ms", "silent_ms", "silent_intervals"}. A socket that is down right now contributes [down_since_ms, now]. A
    socket that is up but silent contributes [last_notice_ms, now], where silent means either (absolute) nothing for more than silent_ms, or
    (relative) nothing for more than rel_silent_ms while some other socket DELIVERED a notification after this socket's last notice. Only
    last_delivery_ms counts as a peer delivery: a peer's resubscribe moves its own last_notice_ms (the quiet clock starts at the subscribe) but
    delivered nothing, so it must not turn a common stall into a common outage. A snapshot without last_delivery_ms (an older format) falls back
    to last_notice_ms, the fail-safe direction (more flags, never fewer). min_end_ms drops closed intervals that ended earlier (bounds the work
    to recent history)."""
    lds = [st["last_delivery_ms"] if "last_delivery_ms" in st else st.get("last_notice_ms") for st in states]
    per: list[list[tuple[int, int]]] = []
    for k, st in enumerate(states):
        iv = [(int(a), int(b)) for a, b in (st.get("intervals") or []) + (st.get("silent_intervals") or []) if min_end_ms is None or int(b) > min_end_ms]
        if not st.get("up") and st.get("down_since_ms") is not None:
            iv.append((int(st["down_since_ms"]), int(now_ms_)))
        ln, sm = st.get("last_notice_ms"), int(st.get("silent_ms") or 0)
        if st.get("up") and ln is not None:
            quiet = now_ms_ - int(ln)
            relative = rel_silent_ms and quiet > rel_silent_ms and any(o is not None and o > ln for j, o in enumerate(lds) if j != k)
            if (sm and quiet > sm) or relative:  # connected but not delivering: down from its last notification
                iv.append((int(ln), int(now_ms_)))
        per.append(_merge(iv))
    if not per:
        return []
    out = per[0]
    for iv in per[1:]:
        nxt: list[tuple[int, int]] = []
        for a1, b1 in out:
            for a2, b2 in iv:
                a, b = max(a1, a2), min(b1, b2)
                if b > a:
                    nxt.append((a, b))
        out = sorted(nxt)
        if not out:
            return []
    return out


# ---- synthetic-migration classifier ---------------------------------------------------------------------------
class SynClassifier:
    """mint -> (synthetic, src), from TWO txs per pool (quant-proof, EXP-024 Am.4 B1; the monitor's rule): the curve's CompleteEvent tx ("comp") and the
    pool's migrate / CreatePool tx ("mig"); they are one tx when the CompleteEvent rode in the migrate tx. Each part is a PostCompleteBuyEvent verdict
    (monitor.post_complete_buy_seen). The pool is synthetic as soon as either part says True; plain only when BOTH parts are known and False; otherwise
    unknown (None), so a tx that cannot be found or read leaves the pool unclassified. Written by the pump.fun ws side feed, the PumpSwap CreatePool
    notice and the RPC fallback; read by the Engine (`lookup`). First writer wins per part except True, which is sticky. No IO and no engine access, so
    the side feed can never touch the PumpSwap feed's clock, link state or records."""

    PARTS = ("comp", "mig")

    def __init__(self, max_n: int = SYN_MAX) -> None:
        self.max_n = max_n
        self._m: collections.OrderedDict[str, dict[str, tuple[bool, str]]] = collections.OrderedDict()
        self._sig: collections.OrderedDict[str, dict[str, str]] = collections.OrderedDict()  # mint -> {part: signature the websocket saw the defining event in}
        self.stats: collections.Counter = collections.Counter()

    def _put(self, mint: str, part: str, syn: bool, src: str) -> bool:
        """Record one part. Returns True when it contradicted an earlier verdict (True wins)."""
        parts = self._m.get(mint)
        if parts is None:
            parts = self._m[mint] = {}
        old = parts.get(part)
        conflict = old is not None and old[0] != syn
        if old is not None and (old[0] == syn or not syn):
            return conflict
        parts[part] = (bool(syn), src)
        self._m.move_to_end(mint)
        self.stats[f"{src}_{part}_{'synthetic' if syn else 'plain'}"] += 1
        while len(self._m) > self.max_n:
            self._m.popitem(last=False)
        return conflict

    def record_part(self, mint: str, part: str, syn: bool, src: str) -> None:
        if self._put(mint, part, syn, src):
            self.stats["conflicts"] += 1

    def record(self, mint: str, syn: bool, src: str) -> None:
        """The whole class at once (both parts): a drill / test convenience; the live paths use record_part."""
        if any([self._put(mint, part, syn, src) for part in self.PARTS]):
            self.stats["conflicts"] += 1

    def missing(self, mint: str) -> set[str]:
        """Parts still to be read for `mint` (empty once it is known synthetic: nothing more can change that)."""
        parts = self._m.get(mint) or {}
        if any(v[0] for v in parts.values()):
            return set()
        return {part for part in self.PARTS if part not in parts}

    def lookup(self, mint: str | None, s0: int | None = None) -> tuple[bool | None, str | None]:
        parts = self._m.get(mint) if mint else None
        if not parts:
            return None, None
        for part in self.PARTS:
            if parts.get(part, (False, ""))[0]:
                return True, parts[part][1]
        if all(part in parts for part in self.PARTS):
            return False, "rpc" if any(parts[part][1] == "rpc" for part in self.PARTS) else parts["comp"][1]
        return None, None

    # ---- websocket side: it can only ever say True ------------------------------------------------------------------
    # A notice carries log lines, not inner instructions, and real logs are cut (a migrate tx's pump events sit in emit_cpi inner instructions only). The
    # monitor reads both (tx_event_blobs); a log-only "no PostCompleteBuyEvent" would therefore not be conclusive. So the websocket records only a visible
    # PostCompleteBuyEvent (True, conclusive) and remembers the signature of the defining tx; plain (False) comes only from an RPC read of the whole tx.
    def hint(self, mint: str, part: str, sig: str) -> None:
        self._sig.setdefault(mint, {})[part] = sig
        self._sig.move_to_end(mint)
        while len(self._sig) > self.max_n:
            self._sig.popitem(last=False)

    def sig_for(self, mint: str, part: str) -> str | None:
        return (self._sig.get(mint) or {}).get(part)

    @staticmethod
    def _blobs(note: Any) -> list[bytes]:
        return [b for b in (_program_data_bytes(ln) for ln in note.logs if "Program data: " in ln) if b]

    def observe_notice(self, note: Any) -> int:
        """One pump.fun logsSubscribe notice. For every mint whose CompleteEvent ("comp") or CompletePumpAmmMigrationEvent ("mig") it carries (the defining-event
        check): a visible PostCompleteBuyEvent records True ("ws"); otherwise the signature is remembered for the RPC read and nothing is concluded. Returns
        the number of defining events seen. A failed tx carries no committed event."""
        if note.failed:
            return 0
        pref = [ln[ln.find("Program data: ") + 14:].lstrip() for ln in note.logs if "Program data: " in ln]
        if not any(p.startswith(_COMPLETE_EVENT_PREFIX) or p.startswith(_MIGRATE_EVENT_PREFIX) for p in pref):
            return 0
        blobs = self._blobs(note)
        n = 0
        for part, decoder in (("comp", decode_complete_event), ("mig", decode_migration_event)):
            for ev in (e for e in (decoder(b) for b in blobs) if e is not None):
                n += 1
                self.stats[f"ws_{part}_notices"] += 1
                if post_complete_buy_seen(blobs, ev["mint"])[0]:
                    self.record_part(ev["mint"], part, True, "ws")
                else:
                    self.hint(ev["mint"], part, note.signature)
        return n

    def observe_migrate_notice(self, mint: str, note: Any) -> int:
        """The PumpSwap CreatePool notice of `mint`'s pool, which is its migrate tx. A visible PostCompleteBuyEvent in it is conclusive (synthetic is the safe
        direction even if the CompletePumpAmmMigrationEvent line was cut): "mig" True, and "comp" True too when the notice also carries the mint's CompleteEvent.
        Anything else concludes nothing here; Engine.request_class passes the signature to the RPC read. Returns the parts recorded."""
        if note.failed or not mint:
            return 0
        blobs = self._blobs(note)
        if not post_complete_buy_seen(blobs, mint)[0]:
            return 0
        self.stats["ws_mig_notices"] += 1
        self.record_part(mint, "mig", True, "ws")
        n = 1
        if any(e["mint"] == mint for e in (decode_complete_event(b) for b in blobs) if e is not None):
            self.record_part(mint, "comp", True, "ws")
            n += 1
        return n


class PreEventClassifier:
    """Replay classifier (md5 proof). A pool whose first print is below PCB_FIRST_DEPLOY_SLOT completed its curve below it too, on a pump binary
    that cannot emit PostCompleteBuyEvent: syn=False, src="pre_event_binary". At or above it the class is unknown (None -> excluded, fail closed)."""

    def __init__(self, first_deploy_slot: int = PCB_FIRST_DEPLOY_SLOT) -> None:
        self.first_deploy_slot = first_deploy_slot
        self.stats: collections.Counter = collections.Counter()

    def lookup(self, mint: str | None, s0: int | None = None) -> tuple[bool | None, str | None]:
        if s0 is not None and s0 < self.first_deploy_slot:
            return False, PRE_EVENT_SRC
        return None, None


class StaticClassifier:
    """Fixed answer for every mint (tests, drills)."""

    def __init__(self, syn: bool | None, src: str | None = "test") -> None:
        self.syn, self.src = syn, src
        self.stats: collections.Counter = collections.Counter()

    def lookup(self, mint: str | None, s0: int | None = None) -> tuple[bool | None, str | None]:
        return (self.syn, self.src) if self.syn is not None else (None, None)


def _rpc_get_tx(client: Any, sig: str) -> dict | None:
    """getTransaction at confirmed (the monitor's fetch_tx pins finalized, ~13 s later); retries once at the tx version a -32015 reply names."""
    from tools.pump_structure_monitor import _VERSION_HINT, MAX_TX_VERSION, RpcError

    version = MAX_TX_VERSION
    for _ in range(2):
        try:
            return client.call("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": version, "commitment": "confirmed"}])
        except RpcError as exc:
            hint = _VERSION_HINT.search(str(exc))
            if "-32015" in str(exc) and hint and int(hint.group(1)) > version:
                version = int(hint.group(1))
                continue
            raise
    return None


def _rpc_tx_parts(tx: dict, mint: str) -> dict[str, bool]:
    """Parts one tx carries for `mint`, each with the tx's PostCompleteBuyEvent verdict. The bytes come from the monitor's `tx_event_blobs` (Program data log
    lines AND emit_cpi inner instructions: a migrate tx's logs are cut in practice, its inner instructions carry every event) and the verdict from
    `post_complete_buy_seen`. Defining-event check: "comp" only if the tx carries the mint's CompleteEvent, "mig" only if it carries the mint's
    CompletePumpAmmMigrationEvent. A tx that fails the check yields nothing and the caller tries the next source."""
    blobs = tx_event_blobs(tx)
    pcb = post_complete_buy_seen(blobs, mint)[0]
    out: dict[str, bool] = {}
    if any(e["mint"] == mint for e in (decode_complete_event(b) for b in blobs) if e is not None):
        out["comp"] = pcb
    if any(e["mint"] == mint for e in (decode_migration_event(b) for b in blobs) if e is not None):
        out["mig"] = pcb
    return out


def classify_via_rpc(client: Any, mint: str, migrate_sig: str | None = None, need: Sequence[str] = ("comp", "mig"), skip: set[str] | None = None,
                     comp_sig: str | None = None) -> dict[str, bool]:
    """Blocking. Returns the parts found, {"comp": PostCompleteBuyEvent verdict of the mint's CompleteEvent tx, "mig": the same for its migrate tx} (one tx can
    be both); a part that could not be found or read, or whose tx fails the defining-event check, is absent. Sources in order: 1) `migrate_sig` (the CreatePool
    notice's signature) if the migrate tx is needed, 2) `comp_sig` (the signature the pump.fun socket saw the CompleteEvent in) if the completing tx is needed,
    3) the curve PDA's signatures before the migrate tx (`before=migrate_sig` when known, so post-migration noise cannot fill the budget), newest first,
    failed txs skipped, up to RPC_TX_PER_ATTEMPT getTransaction calls; `skip` (the caller's set, extended here) holds signatures already read with no match, so
    a retry goes deeper instead of re-reading. Stops early on a True: synthetic is already decided."""
    need = set(need)
    skip = set() if skip is None else skip
    found: dict[str, bool] = {}

    def settled() -> bool:
        return any(found.values()) or not (need - set(found))

    for sig, want in ((migrate_sig, "mig"), (comp_sig, "comp")):
        if sig and want in need and want not in found and not settled() and not (want == "comp" and sig == migrate_sig):
            tx = _rpc_get_tx(client, sig)
            if tx:
                for part, v in _rpc_tx_parts(tx, mint).items():
                    found.setdefault(part, v)
    if settled():
        return found
    curve = find_program_address([b"bonding-curve", b58decode(mint)], PUMP_PROGRAM)[0]
    opts: dict[str, Any] = {"limit": RPC_SIGS_LIMIT, "commitment": "confirmed"}
    if migrate_sig:
        opts["before"] = migrate_sig
    sigs = client.call("getSignaturesForAddress", [curve, opts]) or []
    tried = 0
    for s in sigs:
        if s.get("err") is not None or s["signature"] in skip or (s["signature"] == migrate_sig and "mig" in found) or (s["signature"] == comp_sig and "comp" in found):
            continue
        if tried >= RPC_TX_PER_ATTEMPT:
            break
        tried += 1
        tx = _rpc_get_tx(client, s["signature"])
        if not tx:
            continue
        skip.add(s["signature"])
        for part, v in _rpc_tx_parts(tx, mint).items():
            found.setdefault(part, v)
        if settled():
            break
    return found


class SharedRpc:
    """One RpcClient behind a lock, shared by every lookup: the process-wide request rate is at most 1 / RPC_MIN_INTERVAL_S (4 rps), retries included, and a
    429 backoff pauses all lookups, not just one. (A client per attempt would each pace only itself.)"""

    def __init__(self, url: str, *, client: Any = None) -> None:
        self._lock = threading.Lock()
        self._client = client or RpcClient(url, min_interval=RPC_MIN_INTERVAL_S, max_retries=1, timeout=8.0, max_calls=10**9)

    def call(self, method: str, params: Sequence[Any]) -> Any:
        with self._lock:
            return self._client.call(method, params)

    def attempt(self, deadline: float, max_calls: int) -> "AttemptClient":
        return AttemptClient(self, deadline, max_calls)


class AttemptClient:
    """One lookup attempt's view of the shared client: a call budget and a monotonic deadline. A worker thread whose awaiting side already timed out cannot be
    cancelled, but it stops issuing calls at the next one (the deadline has passed) instead of burning the shared rate."""

    def __init__(self, shared: SharedRpc, deadline: float, max_calls: int) -> None:
        self.shared, self.deadline, self.max_calls, self.total_calls = shared, deadline, max_calls, 0

    def call(self, method: str, params: Sequence[Any]) -> Any:
        if time.monotonic() >= self.deadline:
            raise TimeoutError("lookup attempt deadline")
        if self.total_calls >= self.max_calls:
            raise CallBudgetExceeded(f"call cap {self.max_calls} reached before {method}")
        self.total_calls += 1
        return self.shared.call(method, params)


class RpcFallback:
    """Off-hot-path lookup for a pool still unclassified at s0. `request(mint)` returns at once; the blocking RPC runs in a worker thread, awaited with a
    per-attempt timeout, a few attempts with delays, at most RPC_MAX_INFLIGHT at a time. It writes only to the SynClassifier. Any failure leaves the
    mint unclassified, which the Engine turns into an exclusion: this path can never let a trade through."""

    def __init__(self, classifier: SynClassifier, rpc_url: str = DEFAULT_RPC, *, client_factory: Callable[[], Any] | None = None,
                 delays: Sequence[float] = RPC_ATTEMPT_DELAYS_S, attempt_timeout_s: float = RPC_ATTEMPT_TIMEOUT_S, max_inflight: int = RPC_MAX_INFLIGHT,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.classifier, self.delays, self.attempt_timeout_s, self.max_inflight, self._sleep = classifier, tuple(delays), attempt_timeout_s, max_inflight, sleep
        self._shared = None if client_factory is not None else SharedRpc(rpc_url)  # one client, one rate limiter, for every attempt of every lookup
        self._client_factory = client_factory or (lambda: self._shared.attempt(time.monotonic() + self.attempt_timeout_s, RPC_TX_PER_ATTEMPT + 6))
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_inflight), thread_name_prefix="syn-rpc")  # dedicated: the loop's default executor stays free for DNS
        self._inflight: dict[str, asyncio.Task] = {}
        self._done: collections.OrderedDict[str, None] = collections.OrderedDict()  # mints already looked up (found or given up): never asked twice
        self.calls = 0  # RPC calls made by finished attempts (RpcClient.total_calls), for the cost report

    def request(self, mint: str, migrate_sig: str | None = None) -> bool:
        """Ask for the missing parts of `mint`'s class. `migrate_sig` is the signature of the CreatePool notice (the pool's migrate tx): one getTransaction
        reads it. Returns False when nothing was started (already asked, in flight, or the class is already settled)."""
        if not mint or mint in self._inflight or mint in self._done or not self.classifier.missing(mint):
            return False
        if len(self._inflight) >= self.max_inflight:
            self.classifier.stats["rpc_dropped"] += 1
            return False
        self.classifier.stats["rpc_requests"] += 1
        self._inflight[mint] = asyncio.get_running_loop().create_task(self._run(mint, migrate_sig))
        return True

    async def _run(self, mint: str, migrate_sig: str | None = None) -> None:
        skip: set[str] = set()  # curve signatures already read with no match: a retry looks deeper, not again
        try:
            for delay in self.delays:
                if delay:
                    await self._sleep(delay)
                need = self.classifier.missing(mint)
                if not need:  # the ws path (or an earlier attempt) settled it meanwhile
                    return
                client = self._client_factory()
                self.classifier.stats["rpc_attempts"] += 1
                try:
                    res = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._executor, classify_via_rpc, client, mint, migrate_sig or self.classifier.sig_for(mint, "mig"), need, skip,
                                                                  self.classifier.sig_for(mint, "comp")),
                                                 self.attempt_timeout_s)
                except asyncio.TimeoutError:
                    self.classifier.stats["rpc_timeouts"] += 1
                    res = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - RpcError / RpcUnreachable / CallBudgetExceeded / a decode surprise: try again, then give up
                    self.classifier.stats["rpc_errors"] += 1
                    log.warning("syn rpc fallback %s: %s", type(exc).__name__, str(exc)[:120])
                    res = None
                self.calls += getattr(client, "total_calls", 0) or 0
                self.classifier.stats["rpc_calls"] = self.calls
                for part, verdict in (res or {}).items():
                    self.classifier.record_part(mint, part, verdict, "rpc")
                if not self.classifier.missing(mint):
                    return
            self.classifier.stats["rpc_gave_up"] += 1
        finally:
            self._inflight.pop(mint, None)
            self._done[mint] = None
            while len(self._done) > SYN_MAX:
                self._done.popitem(last=False)

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._executor.shutdown(wait=False, cancel_futures=True)


class UnionChain:
    """REPORT-ONLY base-chain multiset over the UNION of one pool's websocket prints and tip-tape rows, keyed by (signature, event_index).

    A key seen on both sources is one member (mask UNION_BOTH). If the two versions disagree on (pre base, post base, slot) the tip's version is
    kept (it comes from the canonical block) and both_diff counts it. Counts are kept incrementally (pre_excess as in Engine._base_chain, with
    exact removals), so the settled value costs O(members in the last SETTLE_SLOTS slots) at a trigger, not O(prints). The pool-close fields
    (unmatched lists, ws_only_sig_on_tip) are O(members): 0.23 ms at 2,600 prints, linear, so a few ms on a 50k-print pool, once per pool. Never
    read by a decision.

    Decision time: add_ws / add_tip keep an admission line, the highest slot of the pool's feed prints. A tip row above it waits in `ahead` (a
    heap by slot, O(log n) per row) and is admitted once a feed print reaches its slot (or by flush() at pool close). So at a trigger every
    member's slot is <= the line <= the feed's high-water, the settled value takes the incremental path (O(members in the last SETTLE_SLOTS
    slots)), and the counts hold only tip rows at slot <= the line. The final state does not depend on admission order (the tip's version of a
    key wins whichever source came first; dup counts repeats per source), unless the tip delivers one key twice with different contents, which a
    tape of canonical blocks cannot."""

    __slots__ = ("m", "pre", "post", "excess", "by_slot", "max_slot", "n", "both_diff", "dup", "tip_first_slot", "broken", "line", "ahead", "seq",
                 "tip_max_slot", "wsonly", "tip_sig")

    def __init__(self) -> None:
        self.m: dict[tuple, list] = {}  # key -> [pre base, post base, slot, mask]
        self.pre: collections.Counter = collections.Counter()
        self.post: collections.Counter = collections.Counter()
        self.excess = 0  # sum over values of max(0, pre - post), over every member
        self.by_slot: dict[int, list] = {}
        self.max_slot: int | None = None
        self.n = [0, 0, 0, 0]  # indexed by mask: n[1] ws only, n[2] tip only, n[3] both
        self.both_diff = 0
        self.dup = 0  # the same key twice from the same source (ignored)
        self.tip_first_slot: int | None = None
        self.broken = False
        self.line: int | None = None  # admission line: the highest slot of the pool's feed prints (None before the first)
        self.ahead: list = []  # heap of (slot, seq, key, pre base, post base): tip rows above the line, not yet in the union
        self.seq = 0
        self.tip_max_slot: int | None = None  # highest slot of an admitted tip row (member or dup)
        self.wsonly: dict[tuple, Any] = {}  # key -> meta (is_sell, trader) | None, of every member that is websocket-only RIGHT NOW (tipauth)
        self.tip_sig: set = set()  # signatures of every admitted tip row, under any event_index (tipauth: a key mismatch is not a phantom)

    def add_ws(self, key: tuple, bpre: int, bpost: int, slot: int, meta: Any = None) -> None:
        """A feed print: raise the admission line to its slot (admitting the tip rows waiting at or below it), then add it. meta is an optional
        small tuple (is_sell, trader) kept for websocket-only members only (report-only mechanism counts of tipauth)."""
        if self.line is None or slot > self.line:
            self.line = slot
            self._admit(slot)
        self.add(key, bpre, bpost, slot, UNION_WS, meta)

    def add_tip(self, key: tuple, bpre: int, bpost: int, slot: int) -> None:
        """A tip row: into the union if the pool's feed has printed at its slot or later, else held in `ahead`."""
        if self.line is None or slot > self.line:
            heapq.heappush(self.ahead, (slot, self.seq, key, bpre, bpost))
            self.seq += 1
            return
        self.add(key, bpre, bpost, slot, UNION_TIP)

    def _admit(self, line: int | None) -> None:
        while self.ahead and (line is None or self.ahead[0][0] <= line):
            slot, _, key, bpre, bpost = heapq.heappop(self.ahead)
            self.add(key, bpre, bpost, slot, UNION_TIP)

    def flush(self) -> None:
        """Pool close: admit every held tip row (the close record is the eager union of everything read)."""
        self._admit(None)

    def _inc(self, bpre: int, bpost: int) -> None:
        self.pre[bpre] += 1
        if self.pre[bpre] > self.post[bpre]:
            self.excess += 1
        old = self.post[bpost]
        self.post[bpost] += 1
        if self.pre[bpost] > old:
            self.excess -= 1

    def _dec(self, bpre: int, bpost: int) -> None:
        self.post[bpost] -= 1
        if self.pre[bpost] > self.post[bpost]:
            self.excess += 1
        if self.pre[bpre] > self.post[bpre]:
            self.excess -= 1
        self.pre[bpre] -= 1

    def add(self, key: tuple, bpre: int, bpost: int, slot: int, src: int, meta: Any = None) -> None:
        if src == UNION_TIP:
            self.tip_sig.add(key[0])
            self.wsonly.pop(key, None)
            if self.tip_first_slot is None or slot < self.tip_first_slot:
                self.tip_first_slot = slot
            if self.tip_max_slot is None or slot > self.tip_max_slot:
                self.tip_max_slot = slot
        cur = self.m.get(key)
        if cur is None:
            self.m[key] = [bpre, bpost, slot, src]
            self._inc(bpre, bpost)
            self.by_slot.setdefault(slot, []).append(key)
            if self.max_slot is None or slot > self.max_slot:
                self.max_slot = slot
            self.n[src] += 1
            if src == UNION_WS:
                self.wsonly[key] = meta
            return
        if cur[3] & src:
            self.dup += 1
            return
        self.n[cur[3]] -= 1
        cur[3] |= src
        self.n[UNION_BOTH] += 1
        if (cur[0], cur[1], cur[2]) != (bpre, bpost, slot):
            self.both_diff += 1
            if src == UNION_TIP:  # keep the canonical block's version
                self._dec(cur[0], cur[1])
                self.by_slot[cur[2]].remove(key)
                cur[0], cur[1], cur[2] = bpre, bpost, slot
                self._inc(bpre, bpost)
                self.by_slot.setdefault(slot, []).append(key)
                if self.max_slot is None or slot > self.max_slot:
                    self.max_slot = slot

    def settled(self, hw_slot: int | None, full: bool = True) -> int | None:
        """Engine.unresolved_settled's arithmetic on the union: pre bases of members at slot <= hw - SETTLE_SLOTS need a matching post base
        (posts of every member count); one chain head is subtracted. Members more than UNION_SETTLE_SCAN_MAX slots above the settle line (the
        tip leading the feed) need a full O(members) count: done only with full=True (pool close); full=False (a trigger) returns None
        instead. A trigger never gets there: admission keeps every member at or below the feed's high-water."""
        if hw_slot is None or self.max_slot is None or self.max_slot <= hw_slot - SETTLE_SLOTS:
            return max(0, self.excess - 1)
        lo = hw_slot - SETTLE_SLOTS
        if self.max_slot - lo > UNION_SETTLE_SCAN_MAX:
            if not full:
                return None
            pres = collections.Counter(v[0] for v in self.m.values() if v[2] <= lo)
            return max(0, sum(max(0, n - self.post[b]) for b, n in pres.items()) - 1)
        ex, taken = self.excess, collections.Counter()
        for s in range(lo + 1, self.max_slot + 1):
            for k in self.by_slot.get(s, ()):
                b = self.m[k][0]
                if self.pre[b] - taken[b] > self.post[b]:
                    ex -= 1
                taken[b] += 1
        return max(0, ex - 1)

    def settled_without(self, hw_slot: int | None, drop: Sequence[tuple], full: bool = True) -> int | None:
        """The settled value of settled_without_detail (see there)."""
        return self.settled_without_detail(hw_slot, drop, full)[0]

    def settled_without_detail(self, hw_slot: int | None, drop: Sequence[tuple], full: bool = True) -> tuple[int | None, int, int]:
        """REPORT-ONLY tip-authoritative variant of settled(): the same multiset arithmetic over the members MINUS `drop` (keys of members), with
        no mutation of the union (local deltas on the touched base values only). Cost: O(|drop| + members in the settle window), no recompute.
        Returns (settled, nosettle, orphaned):
          settled   what settled() would return on a union that never held the dropped members (None under the same condition as
                    settled(full=False)); with an empty `drop` it is settled() exactly;
          nosettle  the same count with the settle exemption off entirely: every pre base of every kept member needs a matching post base,
                    max(0, excess - 1) on the members minus `drop`;
          orphaned  C5: over the base values `drop` touched, the sum of max(0, excess after - excess before), where excess(b) is
                    max(0, pres(b) - posts(b)) and NO settle exemption applies. It is a NET count per base value: the net increase of
                    unmatched pre bases over the base values `drop` touched. A loss is hidden when the same drop also removes a pre base of
                    equal value, and a dropped head passes its exemption to a successor that was already unmatched. A phantom whose post
                    base nobody uses orphans nothing."""
        if not drop:
            return self.settled(hw_slot, full), max(0, self.excess - 1), 0
        dset = set(drop)
        dpre: collections.Counter = collections.Counter()
        dpost: collections.Counter = collections.Counter()
        for k in dset:
            v = self.m[k]
            dpre[v[0]] += 1
            dpost[v[1]] += 1
        ex, orphaned = self.excess, 0
        for b in set(dpre) | set(dpost):  # excess is a sum over base values: only the touched ones change
            before = max(0, self.pre[b] - self.post[b])
            after = max(0, self.pre[b] - dpre[b] - (self.post[b] - dpost[b]))
            ex += after - before
            if after > before:
                orphaned += after - before
        nosettle = max(0, ex - 1)
        if hw_slot is None or self.max_slot is None or self.max_slot <= hw_slot - SETTLE_SLOTS:
            return nosettle, nosettle, orphaned
        lo = hw_slot - SETTLE_SLOTS
        if self.max_slot - lo > UNION_SETTLE_SCAN_MAX:
            if not full:
                return None, nosettle, orphaned
            pres = collections.Counter(v[0] for k, v in self.m.items() if v[2] <= lo and k not in dset)
            return max(0, sum(max(0, n - (self.post[b] - dpost[b])) for b, n in pres.items()) - 1), nosettle, orphaned
        taken: collections.Counter = collections.Counter()
        for s in range(lo + 1, self.max_slot + 1):
            for k in self.by_slot.get(s, ()):
                if k in dset:
                    continue
                b = self.m[k][0]
                if self.pre[b] - dpre[b] - taken[b] > self.post[b] - dpost[b]:
                    ex -= 1
                taken[b] += 1
        return max(0, ex - 1), nosettle, orphaned

    def tipauth_drop(self, cover_lo: int, cover_hi: int) -> tuple[list, dict] | None:
        """The dropped set D of the tip-authoritative view (see Engine._tipauth_fields) and the counts of the websocket-only members kept, or
        None above TIPAUTH_WSONLY_MAX websocket-only members. D = websocket-only members at a slot in [cover_lo, cover_hi] whose signature has
        no tip row in this pool under any event_index. O(websocket-only members)."""
        if len(self.wsonly) > TIPAUTH_WSONLY_MAX:
            return None
        drop, before, above, on_tip = [], 0, 0, 0
        for k in self.wsonly:
            sl = self.m[k][2]
            if sl < cover_lo:
                before += 1
            elif sl > cover_hi:
                above += 1
            elif k[0] in self.tip_sig:
                on_tip += 1
            else:
                drop.append(k)
        return drop, {"kept_ws_only_before_cover": before, "kept_ws_only_above_cover": above, "kept_ws_only_sig_on_tip": on_tip}

    def unmatched(self, limit: int = UNMATCHED_MAX) -> tuple[int, list]:
        """Members whose pre base has more pres than posts on the union (the chain head is one of them), earliest first, at most `limit`:
        [signature, event_index, slot, "ws" | "tip" | "both"]. Returns (count before the cap, list)."""
        out = [(v[2], k, v[3]) for k, v in self.m.items() if self.pre[v[0]] > self.post[v[0]]]
        out.sort(key=lambda x: (x[0], str(x[1])))
        return len(out), [[k[0], k[1], s, _MASK_NAME[m]] for s, k, m in out[:limit]]


_MASK_NAME = {UNION_WS: "ws", UNION_TIP: "tip", UNION_BOTH: "both"}


def _ukey(row: dict) -> tuple:
    """(signature, event_index) of a decoded row; a replay row has no signature and carries replay_sig (slot:tx_index) instead."""
    return (row.get("signature") or row.get("replay_sig"), row.get("event_index"))


class Pool:
    @property
    def base_unresolved(self) -> int:
        return max(0, self.pre_excess - 1)

    def __init__(self, pool: str, mint: str | None, s0: int, first: Pr, v0: int, announced_slot: int | None, pda: str | None) -> None:
        self.pool, self.mint, self.s0, self.v0 = pool, mint, s0, v0
        self.s0_recv_ms, self.s0_ts = first.recv_ms, first.ts
        self.s0_open, self.s0_ts_open = s0, first.ts  # fixed at the first print to arrive; a sealed pool's record carries only these
        self.announced_slot = announced_slot
        self.pda = pda  # PDA(["boost_vault", pool], PumpSwap): BOOST's per-pool signer
        self.boost_auth: str | None = None  # learned from a BoostBuyAndBurn event
        self.boost_remaining: int | None = None
        self.boost_event_n = 0
        self.prints: list[Pr] = []
        # trader -> [n_buys, total, min, max, last_slot, last_recv_ms, last_ts, first_slot]
        self.traders: dict[str, list] = {}
        self.sold: set[str] = set()
        self.elig: dict[str, int] = {}
        self.best: str | None = None
        self.trig: dict[str, dict] = {}
        self.pending: list[dict] = []
        self.sps0: float | None = None
        self.sps_last: float | None = None
        self.chain_breaks = 0
        self.base_breaks = 0
        self.chain_max_rel = 0.0
        self.gaps: list[dict] = []
        self.min_q_pv: float | None = None
        self.min_q_fv: float | None = None
        self.no_sps = 0
        self.disagree = 0  # sells in [0, 300] s where the per-print-V and fixed-V Q fall on different sides of 40 SOL
        self.slot_regress = 0  # prints whose slot is lower than the previous print's (arrival order is not slot order)
        # Order-independent chain check over the multiset of base values. Every print's pre-trade base must equal SOME print's post-trade base,
        # except exactly one: the chain head. pre_excess = sum over values of max(0, pre_count - post_count); unresolved = max(0, pre_excess - 1).
        # Every permutation of a complete chain gives 0 (the head is whichever print really came first, however the prints arrived); a dropped
        # mid-life print gives 1. Known blind spot: a missed print is masked when the trigger print's post base equals the missed print's
        # post base (an exact-token round trip between them); this needs zero net token flow and is rare. A dropped HEAD is not caught here;
        # the s0 anchor (announced_before_gap, s0_minus_announced_slots) covers it.
        self.pre_cnt: collections.Counter = collections.Counter()
        self.post_cnt: collections.Counter = collections.Counter()
        self.pre_excess = 0
        self.s0_reanchored_slots = 0  # a print below s0 arrived late and s0 moved down by this many slots (only done before any trigger)
        self.sealed_cache: bool | None = None  # a FINAL CAP-PICK verdict (legacy hook: the first answer; pick oracle: a pick, or no mint)
        self.seal_window: bool | None = None  # inside the CAP-PICK window, judged once at the s0 the pool has when first asked
        self.sealed_noted = False  # counted in the sealed_hour aggregate already
        self.skip_withheld: dict | None = None  # a skipped_no_sps record held while the pick oracle had no answer (written if the pool unseals)
        self.slices_withheld: list[dict] = []  # boost_slice records held while the pick oracle had no answer (written if the pool unseals)
        self.h5_sealed_cache: bool | None = None
        self.skip_logged = False
        self.excluded: str | None = None  # "synthetic" | "unclassified" once a trigger of this pool was withheld (sticky: no variant of it fires later)
        self.closed = False
        self.union: UnionChain | None = None  # report-only tip union; set only when the engine has a tip source


class Engine:
    """The H5 decision logic over decoded PumpSwap rows. No IO: records go to `emit`. Deterministic for a given row sequence."""

    def __init__(
        self,
        emit: Callable[[dict], None],
        *,
        boost_mode: str = "auto",  # auto = per-pool PDA when it signs a buy, else behavioural | pda | behavioural
        sps_fn: Callable[[Pool], float | None] | None = None,  # replay parity: the frozen rule's per-pool sps
        require_announce: bool = True,
        clock: SlotClock | None = None,
        wall: Callable[[], int] = now_ms,
        pda_fn: Callable[[str], str] | None = None,
        suppress_outcome: Callable[[str | None], bool] | None = None,  # legacy seal hook: True = withhold this mint's outcome / strip (frozen)
        pick_oracle: Callable[[str], bool | None] | None = None,  # the CAP-PICK oracle, three states (tools.cap_pick_oracle.PickOracle); wins over suppress_outcome
        seal_start_ms: int | None = SEAL_START_MS,  # None disables the seal (replay of exploration data)
        h5_look2_start_ms: int | None = H5_LOOK2_START_MS,  # None disables the H5 Look-2 outcome seal (tests on synthetic 2027 times)
        h5_look2_observed: bool = False,  # True only with the declared observation of EXP-024 Amendment 2 (run_live validates the reference)
        classifier: Any = None,  # synthetic-migration class source: .lookup(mint, s0) -> (syn, src). None = nothing is classified: every trigger is excluded
        bx10_enabled: bool = False,  # True only with the declared flag (bx10_flag(); EXP-026). Off: no outcome_bx10 record, no bx10 computation, no counter
        fresh_start_ms: int | None = None,  # fresh forward (--fresh-forward ARENA): a pool whose s0 is before this instant is never tracked. None = off
        q_star_sol: float = Q_STAR_SOL,  # trigger level in SOL; the module constant is the frozen default (--q-star-sol; another value is another book)
    ) -> None:
        self.q_star_sol = check_q_star(q_star_sol)
        if boost_mode not in ("auto", "pda", "behavioural"):
            raise ValueError("boost_mode")
        self._emit, self.boost_mode, self.sps_fn, self.require_announce = emit, boost_mode, sps_fn, require_announce
        self.clock = clock or SlotClock()
        self.wall = wall
        if pda_fn is None:
            from tools.pump_structure_monitor import boost_vault_authority as pda_fn  # pure python, no network
        self.pda_fn = pda_fn
        self.suppress_outcome, self.seal_start_ms = suppress_outcome, seal_start_ms
        self.pick_oracle = pick_oracle
        self.bx10_enabled = bx10_enabled is True and self.q_star_sol == Q_STAR_SOL  # a Q* != 40 engine never computes bx10 (QP ruling 2026-10-22, NO-GO 1)
        # strictly the boolean True: a string or a number never turns it on
        self.fresh_start_ms = None if fresh_start_ms is None else int(fresh_start_ms)
        self.h5_look2_start_ms, self.h5_look2_observed = h5_look2_start_ms, bool(h5_look2_observed)
        self.pools: dict[str, Pool] = {}
        self.announced: collections.OrderedDict[str, tuple[int, int, str | None, str | None]] = collections.OrderedDict()
        self.seen: collections.OrderedDict[str, int] = collections.OrderedDict()  # pool -> recv_ms of first sight (rejected or tracked)
        self.hw_slot: int | None = None
        self.hw_recv_ms: int | None = None
        self.last_event_ms: int | None = None
        self.silence_open = False
        self.last_flag_gap_ms: int | None = None  # time of the last gap that flagged pools; a CreatePool announced before it may have lost first prints
        self.counters: collections.Counter = collections.Counter()
        self.counters["excluded_synthetic"] = 0  # present in every hb, even at zero
        self.counters["excluded_unclassified"] = 0
        self.classifier = classifier
        self.syn_request: Callable[..., Any] | None = None  # run_live: RpcFallback.request(mint, migrate_sig); must not block
        self.mint_pools: dict[str, set[str]] = {}
        self._feed_prev: dict[int, dict] = {}
        self._looks: collections.deque = collections.deque(maxlen=LOOKS_WINDOW)
        self._prev_look_ms: int | None = None
        self.on_error: Callable[[BaseException, dict], None] | None = None  # error log hook (run_live: ErrorLog.log)
        self.link_probe: Callable[[], tuple[int, dict] | None] | None = None  # () -> (source key, feed snapshot); checked right before a trigger is written
        self._flagged_starts: collections.deque = collections.deque(maxlen=256)  # starts of common-outage pieces already flagged
        self.link_prev: tuple[int, list[dict]] | None = None  # (feed id, per-socket cumulative link histograms) at the previous status record
        self._sealed_hours: dict[int, list[int]] = {}  # UTC hour index -> [sealed pools opened, sealed decisions] (unlabelled aggregate; the only trace of a sealed decision)
        self._sealed_cursor: int | None = None  # next UTC hour index to report; starts at the seal start or the first event, whichever is later
        # Report-only tip union (phase 0): None = off, and then every record is byte-identical to a build without it. When set (run_live
        # --tip-dir: TipTail; replay --replay-tip-lag-slots: ReplayTip) it needs .status(feed hw slot) -> str, .hw_slot and .stats().
        self.tip: Any = None
        self.tip_pending: dict[str, list] = {}  # announced, not yet tracked pool -> tip rows (normally empty: the tip lags the feed)

    # ---- emit -----------------------------------------------------------------------------------------------
    def emit(self, rec: dict) -> None:
        rec.setdefault("v", 1)
        rec.setdefault("schema", SCHEMA)
        rec.setdefault("t_ms", self.wall())
        self._emit(rec)

    # ---- announcements --------------------------------------------------------------------------------------
    def on_create_pool(self, pool: str, mint: str | None, quote_mint: str | None, slot: int, recv_ms: int) -> None:
        self.counters["create_pool_events"] += 1
        self.announced[pool] = (slot, recv_ms, mint, quote_mint)
        self.announced.move_to_end(pool)
        if mint:
            self.mint_pools.setdefault(mint, set()).add(pool)
        self._evict_announced(recv_ms)

    def _evict_announced(self, now_ms_: int) -> None:
        """Drop announcements older than ANNOUNCE_TTL_S, and the mint_pools entries that pointed at them (bounded memory)."""
        cut = now_ms_ - ANNOUNCE_TTL_S * 1000
        while self.announced:
            pool, (_slot, rms, mint, _q) = next(iter(self.announced.items()))
            if rms >= cut:
                break
            del self.announced[pool]
            pools = self.mint_pools.get(mint) if mint else None
            if pools is not None:
                pools.discard(pool)
                if not pools:
                    del self.mint_pools[mint]

    def on_boost_event(self, ev: dict, slot: int, recv_ms: int) -> None:
        """BoostBuyAndBurn event: learn the BOOST signer and the vault's remaining budget for a tracked pool (cross-check; report only)."""
        self.counters["boost_events"] += 1
        p = self.pools.get(ev.get("pool", ""))
        if p is None:
            return
        p.boost_event_n += 1
        p.boost_auth = ev.get("authority") or p.boost_auth
        p.boost_remaining = int(ev["boost_vault_remaining"])

    # ---- clock ----------------------------------------------------------------------------------------------
    def advance(self, slot: int, recv_ms: int, ts: int | None = None) -> None:
        """Any event (a trade, a failed tx, another program's notice) moves the slot clock; due outcomes resolve and old pools close."""
        self._advance_clock(slot, recv_ms, ts)
        self._housekeep(recv_ms)

    def _advance_clock(self, slot: int, recv_ms: int, ts: int | None) -> None:
        self.counters["events"] += 1
        self._ensure_sealed_cursor(recv_ms)
        if self.last_event_ms is not None and self.silence_open:
            self.silence_open = False
        self.last_event_ms = recv_ms
        if ts:
            self.clock.observe(slot, ts)
        if self.hw_slot is not None and slot > self.hw_slot + GAP_SLOTS:
            self._gap("slot_jump", at_ms=recv_ms, from_slot=self.hw_slot, to_slot=slot, missed_slots=slot - self.hw_slot - 1)
        if self.hw_slot is None or slot > self.hw_slot:
            self.hw_slot, self.hw_recv_ms = slot, recv_ms

    def _housekeep(self, recv_ms: int) -> None:
        self._resolve_due()
        self._close_due(recv_ms)

    def tick(self, now: int) -> None:
        """Wall-clock housekeeping (no events needed): silence detection, backstop closes, purge of the seen / announced maps."""
        if self.last_event_ms is not None and not self.silence_open and now - self.last_event_ms > SILENCE_S * 1000:
            self.silence_open = True
            self._gap("silence", at_ms=now, silent_ms=now - self.last_event_ms)
        self._close_due(now)
        self._evict_announced(now)
        if self.tip_pending:
            self._purge_tip_pending()
        self._flush_sealed_hours(now)
        cut = now - SEEN_TTL_S * 1000
        while self.seen and next(iter(self.seen.values())) < cut:
            self.seen.popitem(last=False)

    def feed_restart(self, now: int, reason: str) -> None:
        """The feed was down or restarted: every open pool may have missed prints. Pools announced before this moment are suspect."""
        self._gap("feed_restart", at_ms=now, reason=reason)
        self.hw_slot = None  # the next event is a fresh reference; do not report a slot jump across a known restart

    def _gap(self, kind: str, *, flag_pools: bool = True, at_ms: int | None = None, **kw: Any) -> None:
        self.counters["gaps"] += 1
        rec = {"type": "gap", "kind": kind, "open_pools": len(self.pools), "flags_pools": flag_pools, **kw}
        if flag_pools:
            at = self.wall() if at_ms is None else at_ms
            self.last_flag_gap_ms = at if self.last_flag_gap_ms is None else max(self.last_flag_gap_ms, at)
            for p in self.pools.values():
                p.gaps.append({"kind": kind, **{k: v for k, v in kw.items() if isinstance(v, (int, str))}})
        self.emit(rec)

    def note_feed_stats(self, key: int, snap: dict, now_ms_: int) -> None:
        """Compare the source's reconnect counters with the last look. Any advance is a gap record carrying the cause (close codes, handshake
        rejections, errors, which socket).

        Whether the open pools are FLAGGED depends on whether every socket was down at a common instant. With per-socket link state
        (snap["socket_states"], from observe.link_state: drop -> resubscribe intervals, an unsubscribed socket is down since its drop) the
        common instant is exact, and the flag's time is the real start of the common outage, not the look time. Without link state
        (a stateless snapshot) the fallback is the counter rule: distinct sockets that reconnected over the last LOOKS_WINDOW looks reach
        the socket count. A single socket always flags."""
        if key not in self._feed_prev:  # a rebuilt source starts at zero: only the current one is tracked
            self._looks.clear()
            self._flagged_starts.clear()
        prev = self._feed_prev.get(key) or {"reconnects": 0, "closes": {}, "rejections": {}, "errors": {}, "per_socket": []}
        self._feed_prev = {key: snap}
        delta = snap["reconnects"] - prev["reconnects"]
        n = int(snap.get("sockets") or 1)
        ps_prev = prev.get("per_socket") or [0] * n
        per_socket = [c - (ps_prev[i] if i < len(ps_prev) else 0) for i, c in enumerate(snap.get("per_socket") or [])]
        self._looks.append(per_socket)  # every look is remembered, with or without a reconnect
        states = snap.get("socket_states")
        pieces: list[tuple[int, int]] = []
        if states:
            # A silence only counts once it is long enough, so a common outage can be found AFTER the fact: no filter on the previous look.
            # Dedupe by piece start; bound the work to intervals that ended inside (silent_ms + 2 looks + margin).
            sm = max([int(st.get("silent_ms") or 0) for st in states] + [SILENT_MS_DEFAULT])
            recent = now_ms_ - (sm + 2 * LOOK_MS + RECENT_MARGIN_MS)
            pieces = [(a, b) for a, b in common_down(states, now_ms_, min_end_ms=recent) if a not in self._flagged_starts]
        self._prev_look_ms = now_ms_
        if delta <= 0 and not pieces:
            return
        down = sorted({i for look in self._looks for i, d in enumerate(look) if d > 0})  # distinct sockets that reconnected in the last LOOKS_WINDOW looks
        if states:
            flag, at = bool(pieces), (min(a for a, _ in pieces) if pieces else now_ms_)
            for a, _ in pieces:
                self._flagged_starts.append(a)
            extra = {"common_down_ms": sum(b - a for a, b in pieces), "common_down_start_ms": at if pieces else None, "link_state": True}
        else:
            flag, at, extra = (n == 1 or len(down) >= n), now_ms_, {"link_state": False}

        def dd(a: dict, b: dict) -> dict:
            return {k: v - b.get(k, 0) for k, v in a.items() if v > b.get(k, 0)}

        self._gap("socket_reconnect", flag_pools=flag, at_ms=at, sockets_down_recent=down, delta_reconnects=delta, reconnects_total=snap["reconnects"],
                  sockets=n, redundant=n > 1, per_socket_delta=per_socket, delta_closes=dd(snap["closes"], prev["closes"]),
                  delta_rejections=dd(snap["rejections"], prev["rejections"]), delta_errors=dd(snap["errors"], prev["errors"]), **extra)

    # ---- trades ---------------------------------------------------------------------------------------------
    def on_trade(self, row: dict) -> None:
        """One decoded PumpSwap Buy/Sell row (observe.trade_decode.seal_trade shape; event-V keys optional)."""
        if row.get("venue") != "pumpswap" or not row.get("pool"):
            return
        slot, recv_ms, ts = int(row["slot"]), int(row["t_recv_ms"]), row.get("event_ts")
        self._advance_clock(slot, recv_ms, ts)
        self.counters["pumpswap_prints"] += 1
        pool = row["pool"]
        p = self.pools.get(pool)
        if p is None and pool not in self.seen:
            p = self._maybe_track(row, slot, recv_ms)
        if p is not None:
            self._print_row(p, row, slot, recv_ms, ts)
        self._housekeep(recv_ms)  # after the print: an END-bound state wants the first print past the planned slot, if it is this one

    def _print_row(self, p: Pool, row: dict, slot: int, recv_ms: int, ts: int | None) -> None:
        buy = row["side"] == "buy"
        v_raw = row.get("virtual_quote_reserves")
        q, b = int(row["quote_reserve"]), int(row["base_reserve"])
        v = int(v_raw) if v_raw is not None else p.v0
        dq, db = post_state(q + v, b, buy, int(row["sol_lamports"]), int(row["token_raw"]), row.get("lp_fee"), row.get("pool_quote_amount"))
        kept = 0
        if row.get("fee_recipient_zero"):
            kept = int(row.get("protocol_fee") or 0) + int(row.get("creator_fee") or 0)
        ei = row["tape_event_index"] if "tape_event_index" in row else row.get("event_index")  # a replay row's own position (see _boost_slice)
        pr = Pr(slot=slot, buy=buy, trader=row.get("trader") or "", sol=int(row["sol_lamports"]), tok=int(row["token_raw"]), q=q, v=v, b=b, dq=dq,
                db=db, kept=kept, recv_ms=recv_ms, ts=ts, sig=row.get("signature"), v_missing=v_raw is None,
                ukey=None if self.tip is None else _ukey(row),  # the union key only exists with the union on (no per-print cost when off)
                ei=ei if isinstance(ei, int) and not isinstance(ei, bool) else None)
        self._on_print(p, pr)
        if buy:
            self._boost_slice(p, pr, row)

    def _maybe_track(self, row: dict, slot: int, recv_ms: int) -> Pool | None:
        pool = row["pool"]
        self.seen[pool] = recv_ms
        v_raw = row.get("virtual_quote_reserves")
        if v_raw is None:
            self.counters["rejected_no_event_v"] += 1
            return None
        if not (V_LO <= v_raw <= V_HI):  # V range first: most pools on the tape are not BOOST-era graduations at all
            self.counters["rejected_v_range"] += 1
            return None
        ann = self.announced.get(pool)
        if ann is None and self.require_announce:
            self.counters["rejected_unannounced"] += 1
            q, b = int(row["quote_reserve"]), int(row["base_reserve"])
            if q <= FRESH_REAL_QUOTE_MAX and FRESH_BASE_MIN <= b <= FRESH_BASE_MAX:  # heuristic: a first print of a pool that looks new
                self.counters["unannounced_fresh"] += 1
            return None
        if ann is not None and ann[3] is not None and ann[3] != WSOL_MINT:
            self.counters["rejected_quote_mint"] += 1
            if self.counters["reject_records"] < REJECT_LOG_MAX:
                self.counters["reject_records"] += 1
                self.emit({"type": "reject", "reason": "quote_mint", "pool": pool, "slot": slot, "base_mint": ann[2], "quote_mint": ann[3],
                           "wsol_expected": WSOL_MINT, "v": int(v_raw)})
            return None
        if self.fresh_start_ms is not None:  # fresh forward: only a pool whose s0 is at or after the process start exists; no record of any kind for the others
            ts0 = row.get("event_ts")
            if (int(ts0) * 1000 if ts0 else recv_ms) < self.fresh_start_ms or recv_ms < self.fresh_start_ms:  # block time is whole seconds: the start second counts as before
                self.counters["fresh_ignored_pre_start"] += 1
                return None
        mint = ann[2] if ann else row.get("mint")
        first = Pr(recv_ms=recv_ms, ts=row.get("event_ts"))
        try:
            pda = self.pda_fn(pool)
        except Exception:  # noqa: BLE001 - a bad pubkey string must not stop the feed
            pda = None
        p = Pool(pool, mint, slot, first, int(v_raw), ann[0] if ann else None, pda)
        p.sps0 = self._sps(p)
        self.pools[pool] = p
        self.counters["pools_tracked"] += 1
        if self.tip is not None:
            p.union = UnionChain()
            for item in self.tip_pending.pop(pool, ()):
                self._union_add(p, item, UNION_TIP)
        if self._sealed(p) and p.sealed_cache is True:  # a final verdict at s0 (always so with the legacy hook); a pick oracle's pool that is
            self._note_sealed_pool(p.s0_recv_ms)  # not known yet at s0 (the usual case: the gate decides after the first print) is counted at close
            p.sealed_noted = True
        elif self._h5_sealed(p):
            self.counters["h5_look2_sealed_pools"] += 1  # a pool count only; its trigger records are still written
        if mint and len(self.mint_pools.get(mint, ())) > 1:
            p.gaps.append({"kind": "ambiguous_mint"})
        if mint and p.sealed_cache is not True:  # still unclassified at s0: ask the RPC fallback (async; a no-op if it was already asked at the announcement)
            self.request_class(mint)  # (a pool whose pick verdict is still open is asked too: it may turn out a non-pick and trigger)
        if ann is not None and self.last_flag_gap_ms is not None and ann[1] < self.last_flag_gap_ms:
            p.gaps.append({"kind": "announced_before_gap"})  # first prints may have been lost in a gap between the CreatePool and this s0
        return p

    def request_class(self, mint: str, migrate_sig: str | None = None) -> None:
        """Ask the (async, off-hot-path) RPC fallback for the parts of `mint`'s synthetic class that are still unknown. Never blocks, never raises."""
        if self.syn_request is None or self.classifier is None or self.classifier.lookup(mint)[0] is not None:
            return
        try:
            self.syn_request(mint, migrate_sig)
        except Exception:  # noqa: BLE001 - the fallback may not take the feed down; the pool stays unclassified and is excluded
            self.counters["syn_request_errors"] += 1

    def _syn(self, p: Pool) -> tuple[bool | None, str | None]:
        """(synthetic, src) of the pool's mint right now: (True|False, src) or (None, None) when unknown (no classifier, no mint, nothing recorded)."""
        if self.classifier is None or not p.mint:
            return None, None
        return self.classifier.lookup(p.mint, p.s0)

    def _exclude(self, p: Pool, pr: Pr, var: str, reason: str) -> None:
        """A trigger the rule would have written, withheld because the pool is synthetic or unclassified. One `excluded` record per pool, no Q, price, V or
        outcome field, no pending outcome and no strip: nothing about its paper result is computed. The variant is marked fired (as a stub without a slot) so
        _eval stops exactly as it does after a real trigger."""
        p.trig[var] = {"excluded": p.excluded or reason}
        if p.excluded is not None:
            return
        p.excluded = reason
        self.counters[f"excluded_{reason}"] += 1
        self.emit({"type": "excluded", "reason": reason, "pool": p.pool, "mint": p.mint, "s0": p.s0, "slot": pr.slot})

    def _sps(self, p: Pool) -> float | None:
        s = self.sps_fn(p) if self.sps_fn else self.clock.sps()
        p.sps_last = s
        return s

    def _on_print(self, p: Pool, pr: Pr) -> None:
        # chain: base reserves are exact, so a mismatch means a missed or reordered print; the quote chain is informational
        if p.prints:
            prev = p.prints[-1]
            if pr.slot < prev.slot:
                p.slot_regress += 1
                self.counters["slot_regress"] += 1
            if pr.b != prev.b_post:
                p.base_breaks += 1
                self.counters["base_breaks"] += 1
            qa, qp = pr.q_pre("pv", p.v0), prev.q_post("pv", p.v0)
            rel = abs(qa - qp) / max(qa, 1.0)
            p.chain_max_rel = max(p.chain_max_rel, rel)
            if rel > CHAIN_TOL:
                p.chain_breaks += 1
                self.counters["chain_breaks"] += 1
        if p.prints and pr.slot < p.s0:
            self._below_s0(p, pr)
        self._base_chain(p, pr)
        if p.union is not None:
            self._union_add(p, (pr.ukey, int(pr.b), int(pr.b_post), pr.slot), UNION_WS, (not pr.buy, pr.trader))
        p.prints.append(pr)
        qpv, qfv = pr.q_post("pv", p.v0), pr.q_post("fv", p.v0)
        if pr.slot - p.s0 <= 1500:  # first 300 s at the fastest slot time the rule allows (0.2 s); a monitoring field only
            p.min_q_pv = qpv if p.min_q_pv is None else min(p.min_q_pv, qpv)
            p.min_q_fv = qfv if p.min_q_fv is None else min(p.min_q_fv, qfv)
        self._boost_update(p, pr)
        if not pr.buy:
            self._eval(p, pr, len(p.prints) - 1)

    def unresolved_settled(self, p: Pool) -> int:
        """Same multiset check, but only the pre-trade bases of SETTLED prints (slot <= high-water - SETTLE_SLOTS) need a matching post-trade base
        (posts of every print count). base_breaks_unresolved counts the arrival prefix as it stands, so a predecessor still in flight in the last
        slot or two reads as a break at decision time; this one waits for those prints to settle and still catches a print missing for longer.
        One chain head is subtracted. With no settled print it is 0."""
        if self.hw_slot is None:
            return p.base_unresolved
        posts = collections.Counter(int(r.b_post) for r in p.prints)
        pres = collections.Counter(int(r.b) for r in p.prints if r.slot <= self.hw_slot - SETTLE_SLOTS)
        return max(0, sum(max(0, n - posts.get(v, 0)) for v, n in pres.items()) - 1)

    # ---- report-only tip union (phase 0; never read by a decision) --------------------------------------------
    def _union_add(self, p: Pool, item: tuple, src: int, meta: Any = None) -> None:
        """Fail-soft: an error marks the pool's union broken (its field goes null) and never reaches the print or trigger path."""
        u = p.union
        if u is None or u.broken:
            return
        try:
            if src == UNION_WS:  # decision time: see UnionChain
                u.add_ws(item[0], item[1], item[2], item[3], meta)
            else:
                u.add_tip(item[0], item[1], item[2], item[3])
        except Exception:  # noqa: BLE001
            u.broken = True
            self.counters["union_errors"] += 1

    def _union_flush(self, p: Pool) -> None:
        """Pool close: admit the tip rows still held above the pool's admission line. Fail-soft like _union_add."""
        u = p.union
        if u is None or u.broken or not u.ahead:
            return
        try:
            u.flush()
        except Exception:  # noqa: BLE001
            u.broken = True
            self.counters["union_errors"] += 1

    def tip_interested(self, pool: str) -> bool:
        """The tip reader keeps a row only for a tracked pool, or an announced pool the feed has not judged yet."""
        return pool in self.pools or (pool in self.announced and pool not in self.seen)

    def on_tip_row(self, row: dict) -> None:
        """One tip-tape trade row (observe.trade_decode.seal_trade shape, base_reserve PRE-trade as on the feed). Report-only: it only ever
        touches Pool.union and tip_pending. Never raises."""
        try:
            if row.get("venue") != "pumpswap":
                return
            pool = row.get("pool")
            tok, bpre = int(row["token_raw"]), int(row["base_reserve"])
            item = (_ukey(row), bpre, bpre + (-tok if row["side"] == "buy" else tok), int(row["slot"]))
            p = self.pools.get(pool)
            if p is not None:
                self._union_add(p, item, UNION_TIP)
            elif pool in self.announced and pool not in self.seen:
                buf = self.tip_pending.setdefault(pool, [])
                if len(buf) < TIP_PENDING_MAX:
                    buf.append(item)
        except Exception:  # noqa: BLE001
            self.counters["tip_bad_rows"] += 1

    def _purge_tip_pending(self) -> None:
        for pool in [k for k in self.tip_pending if k not in self.announced or k in self.seen]:
            del self.tip_pending[pool]

    def union_mem(self) -> dict:
        """Memory self-report of the report-only union for hb / status (O(open pools), once a minute): sizes of the union structures, an
        estimate in MB from the measured per-item bytes, and the process's own RSS / RssAnon / VmHWM (kB, /proc/self/status) next to it."""
        pools = members = slots = keys = broken = ahead = wsonly = tipsig = 0
        for p in self.pools.values():
            u = p.union
            if u is None:
                continue
            pools += 1
            if u.broken or u.m is None:
                broken += 1
                continue
            members += len(u.m)
            ahead += len(u.ahead)
            slots += len(u.by_slot)
            keys += len(u.pre) + len(u.post)
            wsonly += len(u.wsonly)
            tipsig += len(u.tip_sig)
        pend = sum(len(v) for v in self.tip_pending.values())
        est = members * UNION_MEMBER_BYTES_EST + (pend + ahead) * UNION_PENDING_ROW_BYTES_EST
        return {"union_pools": pools, "union_broken_pools": broken, "union_members": members, "union_slots": slots, "union_counter_keys": keys,
                "union_ahead_rows": ahead, "pending_pools": len(self.tip_pending), "pending_rows": pend, "est_mb": round(est / 1e6, 1),
                "tipauth_wsonly": wsonly, "tipauth_tip_sigs": tipsig,
                "est_tipauth_mb": round((wsonly * UNION_WSONLY_BYTES_EST + tipsig * UNION_TIPSIG_BYTES_EST) / 1e6, 1), **_proc_mem()}

    def _union_fields(self, p: Pool, trig_slot: int | None = None) -> dict:
        """base_breaks_unresolved_settled_union (null unless the tip is fresh and the pool's union is intact) plus the source counts.
        trig_slot None: the pool close (every tip row read so far is admitted first; tip_hw_slot / tip_lag_slots as in phase 0). trig_slot set:
        a trigger, decision time only: the counts and the settled value hold only tip rows at slot <= trig_slot (UnionChain admission); if an
        admitted tip row is above it (the trigger print is a slot regress) every count is null and status is "regress". The reader's own
        high-water is not filtered: it is written as tip_hw_slot_global / tip_lag_slots_global."""
        try:
            tip = self.tip
            st = tip.status(self.hw_slot)
            u = p.union
            if u is None or u.broken:
                st = "error"
            lag = None if tip.hw_slot is None or self.hw_slot is None else self.hw_slot - tip.hw_slot
            if trig_slot is None:
                self._union_flush(p)
                if u is not None and u.broken:
                    st = "error"
                val = u.settled(self.hw_slot) if st == "ok" else None
                info = {"status": st, "ws_only": None if u is None else u.n[UNION_WS], "tip_only": None if u is None else u.n[UNION_TIP],
                        "both": None if u is None else u.n[UNION_BOTH], "both_diff": None if u is None else u.both_diff,
                        "dup": None if u is None else u.dup, "tip_first_slot": None if u is None else u.tip_first_slot,
                        "tip_hw_slot": tip.hw_slot, "tip_lag_slots": lag}
                return {UNION_FIELD: val, "base_union": info, **self._tipauth_fields(p, tip, st, val, None)}
            cnt = u
            if u is not None and u.tip_max_slot is not None and u.tip_max_slot > trig_slot:
                st, cnt = "regress", None
            val = None
            if st == "ok":
                val = u.settled(self.hw_slot, full=False)  # never None here: admission keeps every member <= the feed's high-water
                if val is None:
                    st = "ahead"
            info = {"status": st, "ws_only": None if cnt is None else cnt.n[UNION_WS], "tip_only": None if cnt is None else cnt.n[UNION_TIP],
                    "both": None if cnt is None else cnt.n[UNION_BOTH], "both_diff": None if cnt is None else cnt.both_diff,
                    "dup": None if cnt is None else cnt.dup, "tip_first_slot": None if cnt is None else cnt.tip_first_slot,
                    "tip_hw_slot_global": tip.hw_slot, "tip_lag_slots_global": lag}
        except Exception:  # noqa: BLE001 - a report-only field may never take the trigger or the close down
            self.counters["union_errors"] += 1
            return {UNION_FIELD: None, "base_union": {"status": "error"}, TIPAUTH_FIELD: None, "base_tipauth": {"status": "error", "margin_slots": TIPAUTH_MARGIN_SLOTS}}
        return {UNION_FIELD: val, "base_union": info, **self._tipauth_fields(p, tip, st, val, trig_slot)}

    def _tipauth_fields(self, p: Pool, tip: Any, st: str, union_val: int | None, trig_slot: int | None) -> dict:
        """REPORT-ONLY, never read by a decision. base_breaks_unresolved_settled_tipauth: the settled count of the union MINUS the websocket
        prints the tip tape does not contain (UnionChain.settled_without_detail), plus base_tipauth with the evidence. Written exactly where the
        union fields are (trigger and pool close; absent on a CAP-PICK-sealed record and on an H5 Look-2-sealed pool-close record, present on a
        Look-2-sealed trigger record, whose union fields are the decision-time ones). It is computed between detect and emit, i.e. on the
        executor's buy path: TIPAUTH_WSONLY_MAX bounds its cost.

        Why. The public logsSubscribe feed delivers swap notices that getTransaction (finalized) did not find ("phantoms": measured on at least
        28 distinct signatures, one public RPC; the name is the interpretation of "not found", D below is defined by absence from the tip, not by
        that test). A phantom shares its pre-trade base with a real print, so it adds one unmatched pre base; a union can only ADD rows and never
        removes it. Every tip row comes from a finalized block. That does NOT make the tape complete: the same sample found 3 of 50 websocket-only
        prints on the finalized chain, and the tip misses prints in some pools. A dropped real print is the failure this view must not hide.

        D, the dropped set: members that are (1) websocket-only, (2) at a slot in the covered range [cover_lo, cover_hi], and (3) whose signature
        has no tip row in this pool under any event_index (a websocket print whose signature IS on the tip under another event_index is a decoder
        key mismatch: kept, counted in kept_ws_only_sig_on_tip). cover_lo = this pool's first admitted tip row (tip_first_slot; websocket-only
        prints before it are kept: the tip may not have been following the pool yet). cover_hi = min(the tip reader's high-water slot, the
        decision slot) - TIPAUTH_MARGIN_SLOTS (a pool close has no decision slot: the reader's high-water alone).

        What a value of 0 does NOT prove. tipauth 0 does not prove that no real print was dropped. (a) A dropped real print at the START of the
        chain is invisible: its successor's pre base simply becomes the head (a contiguous prefix run). (b) At a pool close a contiguous SUFFIX run
        of dropped real prints is invisible too (at a trigger the trigger print, above cover_hi, protects the end). (c) A dropped real interior
        print raises the value only if its first kept successor is OUTSIDE the settle window: when that successor is in the last SETTLE_SLOTS
        slots its unmatched pre base is exempt, and the value stays 0 (the counterexample: A 1000->900, M 900->850 missing from both, N 850->800
        dropped, T 800->700 the trigger print: union 1, tipauth 0). orphaned_by_drop_n and tipauth_nosettle are cross-checks for (c), and
        they do NOT close it alone: orphaned_by_drop_n is the net increase of unmatched pre bases over the base values D touched (no settle
        exemption); a loss is hidden when the same drop also removes a pre base of equal value, and a dropped head passes its exemption to a
        successor that was already unmatched. tipauth_nosettle is the tipauth count with the settle exemption off entirely. (d) An equal-value
        collision of two base reserves can mask a break (not measured; an exact round trip, buy X then sell X, returns the reserve to the same
        value, so it is not rare by construction). base_tipauth.dropped lists the first TIPAUTH_DROPPED_LIST_MAX members of D by (slot,
        event_index) as [signature, event_index, slot] so a census can look each up (null whenever dropped_n is null). Inside a tip gap (fast_tip_follower writes a "backlog_jump" range to gaps.jsonl and has NO rows for those slots) real
        websocket prints are dropped; an interior one breaks the chain (the intended direction), a prefix or suffix run does not. The reader cannot
        tell a gap from a quiet slot.

        Why TIPAUTH_MARGIN_SLOTS = 4 (SETTLE_SLOTS + 2), and what is closed. Read from tools/fast_tip_follower.py: slots are committed STRICTLY in
        slot order (the parallel fetch only prefetches; _step_parallel commits last_done + 1 only; a contradicted skip is refetched and written
        before the block that contradicted it), and a block's rows are appended to ONE hourly file with one flushed write before the checkpoint
        moves. So in one file a complete line of slot S means every slot < S is already in it; slot S itself may be partly visible (1 slot of
        margin); SETTLE_SLOTS and one more slot are kept on top. Consecutive slots can straddle trades-<H> and trades-<H+1>, and TipTail.hw_slot
        is a max over files; that hole is closed two ways, as of the last poll and only if the follower writes slot S-1 to hour H before slot S to hour H+1
        (read in the follower, not re-verified by test): poll() re-reads the earlier hour after reading a later one that delivered lines (so
        S - 1, written before S began, is in), and this field is null with status tip_pending while any open hour file's last read was a full
        chunk (TipTail.pending(): unread bytes may remain). STILL OPEN, not closed by anything here: a tip row lost to TIP_PENDING_MAX (rows
        buffered for a pool announced but not yet tracked), and a truncation of the tip file that TipTail does not notice. A phantom inside the
        margin, or above cover_hi, is not dropped: the field then reads like the union (conservative).

        Null (status says why): the tip is not "ok" (that status itself), tip_pending, the union is broken (error), the trigger print is a slot
        regress (regress), no admitted tip row in this pool (no_tip_rows), cover_hi < cover_lo (no_cover), more than TIPAUTH_WSONLY_MAX websocket-
        only members (big), the settle window too long for an incremental count (ahead). Mechanism counts for a later proposal: dropped_n and its
        first / last slot, dropped_sells_n, dropped_boost_n (buys signed by the pool's BOOST identity as known at the time of the record), the
        kept_ws_only_* counts, orphaned_by_drop_n, tipauth_nosettle. Never raises: an error counts union_errors and gives a null field with
        status "error"."""
        info: dict = {"status": st, "dropped_n": None, "dropped_first_slot": None, "dropped_last_slot": None, "cover_lo": None, "cover_hi": None,
                      "kept_ws_only_before_cover": None, "kept_ws_only_above_cover": None, "kept_ws_only_sig_on_tip": None,
                      "dropped_boost_n": None, "dropped_sells_n": None, "orphaned_by_drop_n": None, "tipauth_nosettle": None,
                      "dropped": None, "margin_slots": TIPAUTH_MARGIN_SLOTS}
        try:
            u = p.union
            if st != "ok":
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            if getattr(tip, "pending", lambda: False)():  # a full chunk may be waiting in an open hour file: hw_slot may lead unread rows
                info["status"] = "tip_pending"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            if u is None or u.broken:
                info["status"] = "error"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            if union_val is None:
                info["status"] = "ahead"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            if u.tip_first_slot is None or tip.hw_slot is None:
                info["status"] = "no_tip_rows"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            lo = u.tip_first_slot
            hi = (tip.hw_slot if trig_slot is None else min(tip.hw_slot, trig_slot)) - TIPAUTH_MARGIN_SLOTS
            info["cover_lo"], info["cover_hi"] = lo, hi
            if hi < lo:
                info["status"] = "no_cover"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            got = u.tipauth_drop(lo, hi)
            if got is None:
                info["status"] = "big"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            drop, kept = got
            val, nosettle, orphaned = u.settled_without_detail(self.hw_slot, drop, full=trig_slot is None)
            if val is None:
                info["status"] = "ahead"
                return {TIPAUTH_FIELD: None, "base_tipauth": info}
            slots = [u.m[k][2] for k in drop]
            ident = self.boost_identity(p)[0]
            metas = [u.wsonly.get(k) for k in drop]
            info.update(kept)
            # REPORT-ONLY listing of D: the first TIPAUTH_DROPPED_LIST_MAX members by (slot, event_index); dropped_n stays the full count
            info["dropped"] = [[k[0], k[1], u.m[k][2]] for k in sorted(drop, key=lambda k: (u.m[k][2], k[1] if isinstance(k[1], int) else -1, str(k[0])))[:TIPAUTH_DROPPED_LIST_MAX]]
            info.update({"orphaned_by_drop_n": orphaned, "tipauth_nosettle": nosettle, "dropped_n": len(drop), "dropped_first_slot": min(slots, default=None), "dropped_last_slot": max(slots, default=None),
                         "dropped_sells_n": sum(1 for m in metas if m is not None and m[0]),
                         "dropped_boost_n": sum(1 for m in metas if m is not None and not m[0] and ident is not None and m[1] == ident)})
            return {TIPAUTH_FIELD: val, "base_tipauth": info}
        except Exception:  # noqa: BLE001
            self.counters["union_errors"] += 1
            return {TIPAUTH_FIELD: None, "base_tipauth": {"status": "error", "margin_slots": TIPAUTH_MARGIN_SLOTS}}

    def _union_close_fields(self, p: Pool) -> dict:
        """Pool close: the union fields plus up to UNMATCHED_MAX unmatched prints of each view, so missing-vs-extra can be settled offline.
        base_unmatched_ws: the feed's own view (the gate's): [signature, event_index, slot, on_tip, pred_tip_only] where on_tip says the print is
        on the tip tape too and pred_tip_only says a tip-only row supplies its pre base as a post base (a predecessor the feed missed).
        base_unmatched_union: the same on the union, [signature, event_index, slot, "ws" | "tip" | "both"]. The chain head is in both lists."""
        out = self._union_fields(p)
        try:
            u = p.union
            ws = [r for r in p.prints if p.pre_cnt[int(r.b)] > p.post_cnt[int(r.b)]]
            ws.sort(key=lambda r: (r.slot, str(r.ukey)))
            rows = []
            for r in ws[:UNMATCHED_MAX]:
                on_tip = pred = None
                if u is not None and not u.broken:
                    m = u.m.get(r.ukey)
                    on_tip = bool(m is not None and m[3] & UNION_TIP)
                    pred = u.post[int(r.b)] > p.post_cnt[int(r.b)]
                rows.append([r.ukey[0], r.ukey[1], r.slot, on_tip, pred])
            out["base_unmatched_ws_n"], out["base_unmatched_ws"] = len(ws), rows
            if u is not None and not u.broken and isinstance(out.get("base_union"), dict):
                # a feed print whose signature IS on the tip under another event_index would read as ws_only + tip_only: count them, so a
                # key mismatch between the two decoders cannot pass for a feed defect
                tip_sigs = {k[0] for k, v in u.m.items() if v[3] & UNION_TIP}
                out["base_union"]["ws_only_sig_on_tip"] = sum(1 for k, v in u.m.items() if v[3] == UNION_WS and k[0] in tip_sigs)
            if u is not None and not u.broken:
                out["base_unmatched_union_n"], out["base_unmatched_union"] = u.unmatched()
            else:
                out["base_unmatched_union_n"], out["base_unmatched_union"] = None, None
        except Exception:  # noqa: BLE001
            self.counters["union_errors"] += 1
            out.update({"base_unmatched_ws_n": None, "base_unmatched_ws": None, "base_unmatched_union_n": None, "base_unmatched_union": None})
        return out

    def _below_s0(self, p: Pool, pr: Pr) -> None:
        """A print with a slot below s0 arrived after s0 was set (the pool's true first print was delivered late). Before any trigger this is a
        pure reorder across slots and s0 moves down to it (safe: sells already evaluated against the later s0 had a smaller t, so none that
        qualifies under the true s0 was skipped). After a trigger the exit plan is already anchored on the old s0, so the pool is flagged."""
        real_trigger = any("slot" in r for r in p.trig.values())  # a trigger record went out (with a pick oracle, a pool may seal only after it)
        if real_trigger or (p.trig and not self._sealed(p)):  # a sealed pool has no exit plan: its path must not depend on whether it triggered
            p.gaps.append({"kind": "slot_below_s0", "s0": p.s0, "slot": pr.slot})
            self.counters["slot_below_s0_after_trigger"] += 1
            return
        self._sealed(p)  # judge the pool on the s0 it has NOW; the verdict is frozen: moving s0 down can only make it earlier, so a sealed pool
        # stays sealed (fail closed) and an unsealed one cannot become sealed
        p.s0_reanchored_slots += p.s0 - pr.slot
        p.s0 = pr.slot
        if pr.ts:
            p.s0_ts = pr.ts
        self.counters["s0_reanchored"] += 1

    @staticmethod
    def _base_chain(p: Pool, pr: Pr) -> None:
        """Multiset counts of pre- and post-trade base reserves, incremental. pre_excess = sum over values of max(0, pres - posts)."""
        bpre, bpost = int(pr.b), int(pr.b_post)
        p.pre_cnt[bpre] += 1
        if p.pre_cnt[bpre] > p.post_cnt[bpre]:
            p.pre_excess += 1
        old = p.post_cnt[bpost]
        p.post_cnt[bpost] += 1
        if p.pre_cnt[bpost] > old:
            p.pre_excess -= 1

    # ---- BOOST tracking -------------------------------------------------------------------------------------
    def _boost_update(self, p: Pool, pr: Pr) -> None:
        t = pr.trader
        if not pr.buy:
            p.sold.add(t)
            if t in p.elig:
                del p.elig[t]
                if p.best == t:
                    p.best = self._recompute_best(p)
            return
        st = p.traders.get(t)
        if st is None:
            st = p.traders[t] = [0, 0, pr.sol, pr.sol, pr.slot, pr.recv_ms, pr.ts, pr.slot]
        st[0] += 1
        st[1] += pr.sol
        st[2], st[3] = min(st[2], pr.sol), max(st[3], pr.sol)
        st[4], st[5], st[6] = pr.slot, pr.recv_ms, pr.ts
        ok = t not in p.sold and st[0] >= BOOST_MIN_BUYS and st[2] >= BOOST_BUY_MIN and st[3] <= BOOST_BUY_MAX and st[1] <= BOOST_TOTAL_MAX
        if ok:
            p.elig[t] = st[0]
            b = p.best
            if b is None or b not in p.elig or (st[0], _neg(t)) > (p.elig[b], _neg(b)):
                p.best = t
        elif t in p.elig:
            del p.elig[t]
            if p.best == t:
                p.best = self._recompute_best(p)

    @staticmethod
    def _recompute_best(p: Pool) -> str | None:
        if not p.elig:
            return None
        return max(p.elig, key=lambda k: (p.elig[k], _neg(k)))

    def boost_identity(self, p: Pool) -> tuple[str | None, str]:
        """(BOOST signer, source). The per-pool PDA when it has signed a buy (or a BoostBuyAndBurn event named it); else the RULE.md
        behavioural detector (buy-only, >= 3 buys of 0.2..2.0 SOL, total <= 17.7 SOL, most buys wins), causal."""
        if self.boost_mode in ("auto", "pda"):
            for cand, src in ((p.boost_auth, "event_authority"), (p.pda, "pda")):
                if cand and p.traders.get(cand, [0])[0] > 0:
                    return cand, src
            if self.boost_mode == "pda":
                return None, "none"
        if p.best is not None:
            return p.best, "behavioural"
        return None, "none"

    def boost_spent(self, p: Pool) -> tuple[int, str | None, str]:
        ident, src = self.boost_identity(p)
        return (p.traders[ident][1] if ident else 0), ident, src

    # ---- trigger --------------------------------------------------------------------------------------------
    def _eval(self, p: Pool, pr: Pr, idx: int) -> None:
        sealed = self._sealed(p)
        if not sealed:
            self._flush_withheld_skip(p)
            self._flush_withheld_slices(p)
        if len(p.trig) == len(VARIANTS) and not sealed:  # a sealed pool keeps evaluating, so nothing it does depends on whether it triggered
            return
        sps = self._sps(p)
        if not sps_ok(sps):
            if not sealed:
                p.no_sps += 1
                self.counters["no_sps_evals"] += 1
            if not p.skip_logged and min(pr.q_post("pv", p.v0), pr.q_post("fv", p.v0)) / 1e9 <= self.q_star_sol:
                spent, ident, src = self.boost_spent(p)
                if not (ident is not None and pr.trader == ident) and spent < BOOST_BUDGET * BOOST_DONE_FRAC:
                    p.skip_logged = True  # once per pool: this sell would have been evaluated as a trigger candidate if sps had been known
                    rec = {"type": "skipped_no_sps", "pool": p.pool, "mint": p.mint, "s0": p.s0, "slot": pr.slot, "slot_offset": pr.slot - p.s0,
                           "sps": sps, "t_recv_ms": pr.recv_ms, "signature": pr.sig, "q_pv_post_sol": pr.q_post("pv", p.v0) / 1e9,
                           "q_fv_post_sol": pr.q_post("fv", p.v0) / 1e9, "boost_spent_sol": spent / 1e9, "boost_src": src}
                    if self._sealed(p):  # EXP-022 s9: no per-pool trace of a decision; only the unlabelled hourly aggregate
                        self._note_sealed_decision(p.s0_recv_ms)
                        if p.sealed_cache is None:  # pick oracle, no answer yet: held, and written once the pool is known to be a non-pick, so
                            p.skip_withheld = rec  # the executor still refuses this pool's later triggers (sps_skipped_pool) exactly as before
                        return
                    self.counters["skipped_no_sps_would_trigger"] += 1
                    self.emit(rec)
            return
        t = (pr.slot - p.s0) * sps
        if not (T_MIN_S <= t <= T_MAX_S):
            return
        if (pr.q_post("pv", p.v0) / 1e9 <= self.q_star_sol) != (pr.q_post("fv", p.v0) / 1e9 <= self.q_star_sol) and not self._sealed(p):
            p.disagree += 1
            self.counters["pv_fv_disagree_sells"] += 1
        spent, ident, src = self.boost_spent(p)
        if ident is not None and pr.trader == ident:
            return
        if not (spent < BOOST_BUDGET * BOOST_DONE_FRAC):
            return
        for var in VARIANTS:
            if var in p.trig:
                continue
            if pr.q_post(var, p.v0) / 1e9 <= self.q_star_sol:
                self._fire(p, pr, idx, var, sps, t, spent, ident, src)

    def _flush_withheld_skip(self, p: Pool) -> None:
        """A skipped_no_sps record held while the pick oracle had no answer, written now that the pool is known to be a non-pick (before any
        later record of the pool, so the executor learns the skip before a trigger). Only the caller's verdict is used: it is unsealed."""
        if p.skip_withheld is None:
            return
        rec, p.skip_withheld = p.skip_withheld, None
        self.counters["skipped_no_sps_would_trigger"] += 1
        self.emit({**rec, "withheld_pick_pending": True})

    # ---- BOOST slice stream (DEC-028 s5 item 1) ----------------------------------------------------------------
    def _boost_slice(self, p: Pool, pr: Pr, row: dict) -> None:
        """One `boost_slice` record for a buy signed by the pool's vault identity (see BOOST SLICE STREAM in the module doc). Structure only:
        no price, no quote state. Nothing here changes a decision: the signer stats were updated by _boost_update already, and the slot rate is
        read without _sps() (which sets p.sps_last, a field of the pool record)."""
        if not pr.trader:
            return
        if p.boost_auth and pr.trader == p.boost_auth:  # the order boost_identity uses
            src = "event_authority"
        elif p.pda and pr.trader == p.pda:
            src = "pda"
        else:
            return  # not the vault: a behavioural guess is never a live exit input
        st = p.traders.get(pr.trader)
        sps = self.sps_fn(p) if self.sps_fn else self.clock.sps()
        # a replay row's event_index is the union key's constant 0; its tape position rides in tape_event_index (the live feed has no such key)
        txi, evi = row.get("tx_index"), (row["tape_event_index"] if "tape_event_index" in row else row.get("event_index"))
        isint = lambda x: isinstance(x, int) and not isinstance(x, bool)  # noqa: E731
        rec = {"type": "boost_slice", "pool": p.pool, "mint": p.mint, "s0": p.s0, "slot": pr.slot, "tx_index": txi if isint(txi) else None,
               "event_index": evi if isint(evi) else None, "signature": pr.sig, "lamports": int(pr.sol), "sol": pr.sol / 1e9, "block_time": pr.ts,
               "t_recv_ms": pr.recv_ms, "t_i_s": round((pr.slot - p.s0) * sps, 4) if sps_ok(sps) else None, "sps": sps if sps_ok(sps) else None,
               "boost_src": src, "boost_id": pr.trader, "boost_n": None if st is None else st[0],
               "boost_spent_sol": None if st is None else st[1] / 1e9}
        if self._sealed(p):  # CAP-PICK (EXP-022 s9): no per-pool trace of a sealed pool
            if p.sealed_cache is None:  # pick oracle, no answer yet: held, and written once the pool is known to be a non-pick
                p.slices_withheld.append(rec)
            else:
                p.slices_withheld = []
            return
        self._flush_withheld_slices(p)
        if self._h5_sealed(p):  # judged at the pool's open already (an unsealed pool always is): asking here changes nothing
            rec["h5_look2_sealed"] = True
        self.emit(rec)

    def _flush_withheld_slices(self, p: Pool) -> None:
        """boost_slice records held while the pick oracle had no answer, written now that the pool is known to be a non-pick (the caller's
        verdict), in slot order, before any later record of the pool. Their t_recv_ms is the shadow's receive time, not the write time."""
        if not p.slices_withheld:
            return
        held, p.slices_withheld = p.slices_withheld, []
        h5s = self._h5_sealed(p)
        for rec in held:
            self.emit({**rec, "withheld_pick_pending": True, **({"h5_look2_sealed": True} if h5s else {})})

    def _fire(self, p: Pool, pr: Pr, idx: int, var: str, sps: float, t: float, spent: int, ident: str | None, src: str) -> None:
        k_p = math.ceil(ENTRY_S["primary"] / sps - 1e-9)
        k_b = math.ceil(ENTRY_S["binding"] / sps - 1e-9)
        el = math.ceil(EXIT_LAG_S / sps - 1e-9)
        exit_slot = p.s0 + int(round(EXIT_AFTER_S0_S / sps))
        sealed = self._sealed(p)
        if not sealed:
            self._probe_links(self.wall())  # a half-open or flapping socket seen since the last 5 s look must show on THIS trigger
        detect = self.wall()  # after the probe, so detect_lag_ms includes its cost
        if sealed:  # EXP-022 s9 (from 2026-10-16T01Z): no per-pool trigger record, no counters, no pending outcome, no strip
            # A pick oracle that has no answer yet (the gate has not decided, or its feed is stale) seals this trigger for good: it is never
            # written later (the executor would buy late). The stub remembers that, for the close record if the pool turns out a non-pick.
            p.trig[var] = {"sealed": True, "pick_pending": True} if p.sealed_cache is None else {"sealed": True}
            self._note_sealed_decision(p.s0_recv_ms)
            return
        syn, syn_src = self._syn(p)
        if p.excluded is not None or syn is not False:  # EXP-024 Am.4: no buy on a synthetic or unclassifiable pool
            self._exclude(p, pr, var, "synthetic" if syn else "unclassified")
            return
        rec = {
            "type": "trigger", "variant": var, "pool": p.pool, "mint": p.mint, "s0": p.s0, "s0_t_recv_ms": p.s0_recv_ms,
            "slot": pr.slot, "signature": pr.sig, "event_index": pr.ei, "t_since_s0_s": t, "sps": sps,
            "block_time": pr.ts, "t_recv_ms": pr.recv_ms, "t_recv": iso_from_ms(pr.recv_ms), "t_detect_ms": detect,
            "detect_lag_ms": detect - pr.recv_ms,
            "q_trigger_sol": pr.q_post(var, p.v0) / 1e9, "q_pv_post_sol": pr.q_post("pv", p.v0) / 1e9, "q_fv_post_sol": pr.q_post("fv", p.v0) / 1e9,
            "q_pv_pre_sol": pr.q_pre("pv", p.v0) / 1e9, "q_fv_pre_sol": pr.q_pre("fv", p.v0) / 1e9,
            "pv_fv_disagree_at_trigger": (pr.q_post("pv", p.v0) / 1e9 <= self.q_star_sol) != (pr.q_post("fv", p.v0) / 1e9 <= self.q_star_sol),
            "real_quote_pre": pr.q, "v_print": pr.v, "v0": p.v0, "base_pre": pr.b, "sell_token_raw": pr.tok, "sell_user_out": pr.sol,
            "boost_spent_sol": spent / 1e9, "boost_remaining_sol": (BOOST_BUDGET - spent) / 1e9, "boost_id": ident, "boost_src": src,
            "boost_vault_remaining_sol": None if p.boost_remaining is None else p.boost_remaining / 1e9,
            "landing_slot_primary": pr.slot + k_p, "landing_slot_binding": pr.slot + k_b, "entry_slots": {"primary": k_p, "binding": k_b},
            "exit_trigger_slot": exit_slot, "exit_landing_slot": exit_slot + el, "exit_lag_slots": el,
            "prints_seen": len(p.prints), "chain_breaks": p.chain_breaks, "base_breaks": p.base_breaks, "slot_regress": p.slot_regress,
            "base_breaks_unresolved": p.base_unresolved, "base_breaks_unresolved_settled": self.unresolved_settled(p),
            "announced_slot": p.announced_slot,
            "s0_minus_announced_slots": None if p.announced_slot is None else p.s0 - p.announced_slot,
            "s0_reanchored_slots": p.s0_reanchored_slots,
            "sps_n": None if self.sps_fn else self.clock.n_points(), "sps_span_s": None if self.sps_fn else self.clock.span_s(),
            "gap": bool(p.gaps), "gaps": p.gaps[:5], "announced": p.announced_slot is not None, "v_missing": pr.v_missing,
            "synthetic": False, "synthetic_src": syn_src,
        }
        if self.tip is not None:  # report-only, after every decision field is set; O(recent prints), never raises; decision time only
            rec.update(self._union_fields(p, pr.slot))
        if self.q_star_sol != Q_STAR_SOL:  # absent at 40.0: the default record is unchanged
            rec["q_star_sol"] = self.q_star_sol
        p.trig[var] = rec
        if self._h5_sealed(p):  # EXP-024 Look 2's added window without the declared observation: the decision-time record only, no ladder, no outcome
            rec["h5_look2_sealed"] = True
        else:
            ladder = {T: p.s0 + int(round(T / sps)) for T in EXIT_LADDER_S}  # exit trigger slot per ladder point
            rec["exit_ladder_trigger_slots"] = {str(int(T)): v for T, v in ladder.items()}
            p.pending.append({"variant": var, "idx": idx, "sps": sps, "k": {"primary": k_p, "binding": k_b}, "exit_slot": exit_slot, "exit_land": exit_slot + el,
                              "trig_slot": pr.slot, "el": el, "ladder": ladder, "resolve_at": max(ladder.values()) + el,
                              "boost_id": ident, "boost_src": src})  # bx10 only (never written to a v1 record)
        self.counters[f"triggers_{var}"] += 1
        self.emit(rec)

    # ---- outcomes -------------------------------------------------------------------------------------------
    def _state_at(self, p: Pool, X: int, var: str) -> tuple[float, float, str]:
        """END bound: the pool after every print in slots <= X = pre-trade state of the first later print, else the last print's post state."""
        for pr in p.prints:
            if pr.slot > X:
                return pr.q_pre(var, p.v0), float(pr.b), "next_pre"
        last = p.prints[-1]
        return last.q_post(var, p.v0), last.b_post, "post"

    def _resolve_due(self) -> None:
        if self.hw_slot is None:
            return
        for p in self.pools.values():
            if p.pending:
                for pend in [x for x in p.pending if self.hw_slot >= x["resolve_at"] + OUTCOME_GRACE_SLOTS]:
                    self._resolve(p, pend, final=False)

    def _resolve(self, p: Pool, pend: dict, final: bool) -> None:
        if pend not in p.pending:
            return
        if self._sealed(p) and p.sealed_cache is None and not final and not self._h5_sealed(p):
            return  # a pick oracle with no answer right now (its feed went stale after the trigger): keep the outcome pending; at close it is dropped
        p.pending.remove(pend)
        if self._sealed(p) or self._h5_sealed(p):  # a sealed pool's _fire creates no pending outcome; with a pick oracle a pool can seal after its
            return  # trigger (a later pick row): the outcome is then withheld, silently
        var, sps, X_exit = pend["variant"], pend["sps"], pend["exit_land"]
        complete = (self.hw_slot is not None and self.hw_slot >= pend["resolve_at"]) or not final
        legs: dict[str, Any] = {}
        qx, bx, xsrc = self._state_at(p, X_exit, var)
        w = int(round(PRESSURE_WINDOW_S / sps))
        for leg, k in pend["k"].items():
            X = pend["trig_slot"] + k
            qe, be, esrc = self._state_at(p, X, var)
            ssb = sum(1 for r in p.prints if r.buy and r.slot == X)
            nb = sum(r.sol for r in p.prints if r.buy and X - w <= r.slot <= X)
            entry = {"landing_slot": X, "q_sol": qe / 1e9, "base": be, "price_lamports_per_raw": qe / be, "state_src": esrc, "ssb": ssb, "nb_lamports": nb}
            if pend["exit_slot"] <= X:
                entry["net"] = None
                entry["note"] = "exit_trigger_not_after_landing"
            else:
                entry["net"] = {}
                for label, stake in STAKES:
                    pnl, gross = fill_round_trip(qe, be, qx, bx, float(stake))
                    entry["net"][label] = {"pnl_nofail_lamports": pnl, "net_pct_nofail": 100 * pnl / stake, "gross": gross}
            legs[leg] = entry
        ladder_out: dict[str, Any] = {}
        for T, trig_slot in pend["ladder"].items():
            land = trig_slot + pend["el"]
            lq, lb, lsrc = self._state_at(p, land, var)
            row = {"trigger_slot": trig_slot, "landing_slot": land, "q_sol": lq / 1e9, "base": lb, "state_src": lsrc}
            for leg in ("primary", "binding"):
                ent = legs[leg]
                if trig_slot > ent["landing_slot"]:
                    pnl, _ = fill_round_trip(ent["q_sol"] * 1e9, ent["base"], lq, lb, float(STAKES[0][1]))
                    row[f"net_pct_{leg}_0.1"] = 100 * pnl / STAKES[0][1]
            ladder_out[str(int(T))] = row
        rec = {
            "type": "outcome", "variant": var, "pool": p.pool, "mint": p.mint, "s0": p.s0, "trigger_slot": pend["trig_slot"], "sps": sps,
            "exit": {"trigger_slot": pend["exit_slot"], "landing_slot": X_exit, "q_sol": qx / 1e9, "base": bx, "state_src": xsrc},
            "legs": legs, "exit_ladder": ladder_out, "complete": bool(complete), "hw_slot": self.hw_slot, "prints_seen": len(p.prints),
            "chain_breaks": p.chain_breaks, "base_breaks": p.base_breaks, "gap": bool(p.gaps), "gaps": p.gaps[:5],
        }
        self.counters["outcomes"] += 1
        if not complete:
            self.counters["outcomes_incomplete"] += 1
        self.emit(rec)
        if self.bx10_enabled:  # default off (EXP-026 flag not declared): no bx10 work of any kind
            try:  # after v1's record, and nothing it does feeds back: v1's records and counters are the same with or without bx10
                self._resolve_bx10(p, pend, complete)
            except Exception as e:  # noqa: BLE001 - a bx10 fault must not touch v1 (no counter, no error record): it reports on its own record type
                self.emit({"type": "outcome_bx10", "variant": var, "pool": p.pool, "mint": p.mint, "s0": p.s0, "trigger_slot": pend["trig_slot"],
                           "boost_src_at_trigger": pend.get("boost_src"), "error": repr(e)[:300]})

    def _bx10_slices(self, p: Pool, pend: dict) -> tuple[list[tuple[int, int, int]], str | None, str | None]:
        """The BOOST signer's buys as (slot, lamports, recv_ms), slot order. If the pick AT THE TRIGGER was the behavioural detector's, that signer
        is kept even when the PDA signs later: switching to a PDA that only starts buying after the trigger would pick the signer with later
        knowledge. Otherwise the per-pool PDA (or the BoostBuyAndBurn authority) once it has signed a buy: a protocol address, known before any of
        its buys, so naming it later looks at nothing ahead. Failing both, the trigger-time pick (a later behavioural pick could be hindsight)."""
        ident, src = pend.get("boost_id"), pend.get("boost_src")
        if src != "behavioural":
            now_id, now_src = self.boost_identity(p)
            if now_src in ("pda", "event_authority"):
                ident, src = now_id, now_src
        if ident is None:
            return [], None, src
        sl = sorted(((r.slot, r.sol, r.recv_ms) for r in p.prints if r.buy and r.trader == ident), key=lambda x: x[0])
        return sl, ident, src

    def _resolve_bx10(self, p: Pool, pend: dict, complete: bool) -> None:
        """Report-only bx10 outcome for one resolved v1 outcome (see BX10 in the module doc). Same entry legs and END-bound pricing as v1."""
        if not self.bx10_enabled:  # the caller checks too; kept so no path writes a bx10 record without the declared flag
            return
        if self._sealed(p) or self._h5_sealed(p):  # the same seals as v1's outcome (already checked by the caller; kept so this never leaks alone)
            return
        var, sps, s0, v1_slot, el = pend["variant"], pend["sps"], p.s0, pend["exit_slot"], pend["el"]
        sl, ident, src = self._bx10_slices(p, pend)
        t = [(s - s0) * sps for s, _, _ in sl]
        a = [x for _, x, _ in sl]
        legs: dict[str, Any] = {}
        for leg, k in pend["k"].items():
            X = pend["trig_slot"] + k
            tau, fired, k_used, proj = bx10_exit(t, a, (X - s0) * sps, EXIT_AFTER_S0_S)
            trig = min(max(s0 + math.ceil(tau / sps - 1e-9), X + 1), v1_slot) if fired else v1_slot
            ent: dict[str, Any] = {"landing_slot": X, "tau_s": tau, "fired": fired, "k_slices": k_used, "proj_last_slice_s": proj,
                                   "exit_trigger_slot": trig, "exit_landing_slot": trig + el, "same_as_v1": trig == v1_slot}
            if v1_slot <= X:  # v1 has no trade on this leg (exit_trigger_not_after_landing); neither has bx10
                ent["net"] = None
                ent["note"] = "exit_trigger_not_after_landing"
            else:
                qe, be, _ = self._state_at(p, X, var)
                qx, bx, xsrc = self._state_at(p, trig + el, var)
                ent.update({"q_sol": qx / 1e9, "base": bx, "state_src": xsrc, "net": {}})
                for label, stake in STAKES:
                    pnl, gross = fill_round_trip(qe, be, qx, bx, float(stake))
                    ent["net"][label] = {"pnl_nofail_lamports": pnl, "net_pct_nofail": 100 * pnl / stake, "gross": gross}
            legs[leg] = ent
        self.emit({
            "type": "outcome_bx10", "variant": var, "pool": p.pool, "mint": p.mint, "s0": s0, "trigger_slot": pend["trig_slot"], "sps": sps,
            "rule": "bx10", "off_s": BX10_OFF_S, "obs_s": BX10_OBS_S, "kmin": BX10_KMIN, "cap_s": EXIT_AFTER_S0_S,
            "boost_id": ident, "boost_src": src, "boost_src_at_trigger": pend.get("boost_src"), "n_slices": len(sl),
            "slices": [[s - s0, x, rm] for s, x, rm in sl],  # slot offset from s0, lamports, recv ms: the observation timing can be audited
            "v1_exit_trigger_slot": v1_slot, "exit_lag_slots": el, "legs": legs, "complete": bool(complete), "hw_slot": self.hw_slot,
            "prints_seen": len(p.prints), "gap": bool(p.gaps),
        })

    def _sealed(self, p: Pool) -> bool:
        """True when this pool's post-decision states must not be written. Fail closed: no mint, no oracle, or an oracle that raises inside
        the window all suppress. Before the window nothing is suppressed. Whether the pool is inside the window is judged once, at the s0 it
        has when first asked.

        Legacy hook (`suppress_outcome`): the first answer is frozen. Pick oracle (`pick_oracle`, three states): the live CAP-PICK gate decides
        after the pool's first print (300 ms holdback, tape, exporter poll), so at s0 the answer is usually None. Freezing that would seal every
        pool, so only a pick (True) is frozen; False lets the pool through for now and None seals it for now, and both are asked again at the
        next decision point. A trigger that fires while the answer is None is withheld for good (_fire)."""
        if p.sealed_cache is not None:
            return p.sealed_cache
        sealed, final = self._sealed_now(p)
        if final:
            p.sealed_cache = sealed
        return sealed

    def _sealed_now(self, p: Pool) -> tuple[bool, bool]:
        """(sealed, final)."""
        if self.seal_start_ms is None:
            return False, True
        if p.seal_window is None:
            t = p.s0_ts * 1000 if p.s0_ts else p.s0_recv_ms
            p.seal_window = t >= self.seal_start_ms
        if not p.seal_window:
            return False, True
        if p.mint is None:
            return True, True
        if self.pick_oracle is not None:
            try:
                ans = self.pick_oracle(p.mint)
            except Exception:  # noqa: BLE001 - fail closed (PickOracle itself never raises)
                self.counters["seal_oracle_errors"] += 1
                return True, False
            if ans is True:
                return True, True  # a pick never flips back
            return ans is not False, False  # False: through for now; None or a non-bool: sealed for now
        if self.suppress_outcome is None:
            return True, True
        try:
            return bool(self.suppress_outcome(p.mint)), True
        except Exception:  # noqa: BLE001 - fail closed
            self.counters["seal_oracle_errors"] += 1
            return True, True

    def _h5_sealed(self, p: Pool) -> bool:
        """True when this pool's outcome-bearing records are withheld by the H5 seal of EXP-024 Look 2's added window: its s0 block time is at or
        after h5_look2_start_ms and the process was not started with the declared observation of Amendment 2. Independent of the CAP-PICK seal
        (`_sealed`), which applies on top and also withholds the trigger record. Judged once, at the s0 the pool has when first asked (like `_sealed`)."""
        if p.h5_sealed_cache is None:
            p.h5_sealed_cache = self._h5_sealed_now(p)
        return p.h5_sealed_cache

    def _h5_sealed_now(self, p: Pool) -> bool:
        if self.h5_look2_start_ms is None:
            return False
        t = p.s0_ts * 1000 if p.s0_ts else p.s0_recv_ms
        if t < self.h5_look2_start_ms:
            return False
        if t >= (SUCC_END_MS if self.fresh_start_ms is not None else H5_LOOK2_END_MS):
            return True  # outside the declared window [start, end): sealed whatever the flag says
        return not self.h5_look2_observed

    def _emit_strip(self, p: Pool, sealed: bool) -> None:
        """Compact per-print pre-trade states of a TRIGGERED pool from its first trigger print to the end of its life, so an offline scorer can
        reprice any landing / exit slot exactly (state at X = pre of the first row with slot > X, else post_last), e.g. s0 + round(330 / sps_path)."""
        if not p.trig:
            return
        if sealed:  # EXP-022 s9: not even a stub (its existence would say the pool triggered)
            return
        real = {v: r for v, r in p.trig.items() if "slot" in r}  # a sealed stub ({"sealed": True}) has no slot
        if not real:
            return
        start = min(r["slot"] for r in real.values())
        base = {"type": "strip", "pool": p.pool, "mint": p.mint, "s0": p.s0, "from_slot": start, "variants": sorted(real)}
        prints = [r for r in p.prints if r.slot >= start]
        rows = [[r.slot, int(round(r.q_pre("pv", p.v0))), int(round(r.q_pre("fv", p.v0))), int(r.b)] for r in prints[:STRIP_MAX_ROWS]]
        last = p.prints[-1]
        self.counters["strips"] += 1
        extra: dict[str, Any] = {}
        if self.fresh_start_ms is not None:  # fresh forward only: the buys (slot, lamports) from FRESH_BUYS_BACK_SLOTS before the first trigger print on, so an
            # offline scorer can recompute the pressure inputs (same-slot buys, buy lamports in the 2 s window) at any landing slot; the default strip is unchanged
            buys = [[r.slot, int(r.sol)] for r in p.prints if r.buy and r.slot >= start - FRESH_BUYS_BACK_SLOTS]
            extra = {"buys_cols": ["slot", "sol_lamports"], "buys": buys[:STRIP_MAX_ROWS], "buys_truncated": len(buys) > STRIP_MAX_ROWS}
        self.emit({**base, "sps_path": self._sps_path(p), "sps_trigger": {v: r["sps"] for v, r in real.items()}, "n_prints": len(p.prints),
                   "cols": ["slot", "q_pv_pre", "q_fv_pre", "base_pre"], "rows": rows, "truncated": len(prints) > STRIP_MAX_ROWS,
                   "post_last": [last.slot, int(round(last.q_post("pv", p.v0))), int(round(last.q_post("fv", p.v0))), int(last.b_post)], **extra})

    # ---- pool close -----------------------------------------------------------------------------------------
    def _close_due(self, now_ms_: int) -> None:
        for pool in [k for k, p in self.pools.items() if self._due(p, now_ms_)]:
            self._close_safe(self.pools[pool], "horizon")

    def _due(self, p: Pool, now_ms_: int) -> bool:
        sps = p.sps_last or p.sps0 or 0.4
        if self.hw_slot is not None and self.hw_slot >= p.s0 + int(round(POOL_LIFE_S / sps)) + OUTCOME_GRACE_SLOTS:
            return True
        return now_ms_ - p.s0_recv_ms > WALL_CLOSE_S * 1000

    def _close(self, p: Pool, reason: str) -> None:
        for pend in list(p.pending):
            self._resolve(p, pend, final=True)
        sealed = self._sealed(p)
        h5s = self._h5_sealed(p)
        if not sealed:
            self._flush_withheld_skip(p)
            self._flush_withheld_slices(p)
        else:
            p.slices_withheld = []
        self._emit_strip(p, sealed or h5s)
        if sealed:  # EXP-022 s9: only what was known when the pool opened, before any decision print; nothing here can differ with the decision
            if not p.sealed_noted:  # a pick oracle's pool that sealed after s0 (or never got an answer) is counted under its open hour now
                self._note_sealed_pool(p.s0_recv_ms)
                p.sealed_noted = True
            self.emit({"type": "pool", "reason": reason, "sealed": True, "pool": p.pool, "mint": p.mint, "s0": p.s0_open, "s0_t_recv_ms": p.s0_recv_ms,
                       "s0_block_time": p.s0_ts_open, "announced_slot": p.announced_slot,
                       "s0_minus_announced_slots": None if p.announced_slot is None else p.s0_open - p.announced_slot, "v0": p.v0, "sps_at_s0": p.sps0,
                       "boost_pda": p.pda})
            p.closed = True
            del self.pools[p.pool]
            return
        ident, src = self.boost_identity(p)
        if ident is None and p.best is not None:
            ident, src = p.best, "behavioural"
        syn, syn_src = self._syn(p)
        st = p.traders.get(ident) if ident else None
        sps = p.sps_last or p.sps0
        last_rel = last_rel_ts = last_rel_recv = first_rel = None
        if st is not None:
            if sps_ok(sps):
                last_rel = (st[4] - p.s0) * sps
                first_rel = (st[7] - p.s0) * sps
            if st[6] and p.s0_ts:
                last_rel_ts = st[6] - p.s0_ts
            last_rel_recv = (st[5] - p.s0_recv_ms) / 1000
        rec = {
            "type": "pool", "reason": reason, "pool": p.pool, "mint": p.mint, "s0": p.s0, "s0_t_recv_ms": p.s0_recv_ms, "s0_block_time": p.s0_ts,
            "announced_slot": p.announced_slot, "v0": p.v0, "prints": len(p.prints), "buys": sum(1 for r in p.prints if r.buy),
            "sps_at_s0": p.sps0, "sps_last": p.sps_last, "no_sps_evals": p.no_sps,
            "boost_id": ident, "boost_src": src, "boost_pda": p.pda, "boost_event_authority": p.boost_auth,
            "boost_slices": None if st is None else st[0], "boost_total_sol": None if st is None else st[1] / 1e9,
            "boost_first_slice_s": first_rel, "boost_last_slice_s": last_rel, "boost_last_slice_s_blocktime": last_rel_ts,
            "boost_last_slice_s_recv": last_rel_recv, "boost_last_slice_slot": None if st is None else st[4],
            "boost_vault_remaining_sol": None if p.boost_remaining is None else p.boost_remaining / 1e9,
            "min_q_pv_sol": None if (sealed or h5s or p.min_q_pv is None) else p.min_q_pv / 1e9,
            "min_q_fv_sol": None if (sealed or h5s or p.min_q_fv is None) else p.min_q_fv / 1e9, "sealed": sealed,
            "sps_path": self._sps_path(p), "pv_fv_disagree_sells": None if sealed else p.disagree,
            "triggered": None if sealed else sorted(v for v, r in p.trig.items() if "excluded" not in r),
            "synthetic": syn, "synthetic_src": syn_src, "chain_breaks": p.chain_breaks, "base_breaks": p.base_breaks, "slot_regress": p.slot_regress,
            "base_breaks_unresolved": p.base_unresolved, "base_breaks_unresolved_settled": self.unresolved_settled(p),
            "s0_minus_announced_slots": None if p.announced_slot is None else p.s0 - p.announced_slot,
            "s0_reanchored_slots": p.s0_reanchored_slots,
            "chain_max_rel_err": p.chain_max_rel,
            "gap": bool(p.gaps), "gaps": p.gaps[:5],
        }
        if self.fresh_start_ms is not None and not (syn is False and syn_src == "rpc"):
            # fresh forward, synthetic-class embargo (DEC-031 s3.5, EXP-024 Am.4 A3/D1): the pool record of a pool that is not the literal plain, RPC-classified
            # class (synthetic, unclassified, or classified by another source) carries no post-s0 price field until the embargo ends (2026-11-14T00Z)
            rec["min_q_pv_sol"] = None
            rec["min_q_fv_sol"] = None
            rec["pv_fv_disagree_sells"] = None
            rec["price_fields_withheld"] = "synthetic_class_embargo"
        if self.tip is not None and not h5s:  # report-only. Never on a CAP-PICK-sealed pool's record (that branch returned above) and never on an
            rec.update(self._union_close_fields(p))  # H5 Look-2-sealed one (its record stays exactly as without the union: the unmatched lists
            # carry signatures / slots of prints after the trigger). The sealed pool's union is only dropped with the pool.
        if h5s:
            rec["h5_look2_sealed"] = True  # min_q withheld; no outcome and no strip were written for this pool
        pending = sorted(v for v, r in p.trig.items() if r.get("pick_pending"))
        if pending:  # a non-pick whose trigger fired before the pick oracle had an answer: in `triggered`, but no trigger record was written
            rec["trigger_withheld_pick_pending"] = pending
        self.emit(rec)
        p.closed = True
        del self.pools[p.pool]

    @staticmethod
    def _sps_path(p: Pool) -> float | None:
        """The pool's own seconds per slot over the prints it was followed for (the rule's per-pool sps, on this pool's first 400 s only)."""
        pts = [(r.slot, r.ts) for r in p.prints if r.ts]
        if len(pts) < 3 or pts[-1][0] <= pts[0][0] + 300:
            return None
        return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])

    def _close_safe(self, p: Pool, reason: str) -> None:
        """One bad pool is logged and dropped; it never blocks the others or wedges the engine. A sealed pool must not be named anywhere, so for it
        (or any pool whose verdict is not known to be unsealed) the error hook gets a sanitized exception: the class name only, no message, no
        traceback. The message of a KeyError(pool) or a traceback frame could carry the pool or mint."""
        try:
            self._close(p, reason)
        except Exception as exc:  # noqa: BLE001
            self.counters["close_errors"] += 1
            log.warning("pool close failed: %s", type(exc).__name__)
            if self.on_error is not None:
                try:
                    self.on_error(exc if p.sealed_cache is False else _sanitized(exc), {"where": "close"})
                except Exception:  # noqa: BLE001
                    pass
            self.pools.pop(p.pool, None)

    def close_all(self, reason: str) -> None:
        for p in list(self.pools.values()):
            self._close_safe(p, reason)
        self._flush_sealed_hours(self.last_event_ms if self.last_event_ms is not None else self.wall(), final=True)

    def _ensure_sealed_cursor(self, ms: int) -> None:
        if self.seal_start_ms is not None and self._sealed_cursor is None:
            self._sealed_cursor = max(self.seal_start_ms // 3_600_000, int(ms) // 3_600_000)

    def _note_sealed_decision(self, pool_open_ms: int) -> None:
        """A sealed decision is counted under the hour its pool OPENED in (the same key as pools_opened), however much later it was taken."""
        self._ensure_sealed_cursor(pool_open_ms)
        self._sealed_hours.setdefault(int(pool_open_ms) // 3_600_000, [0, 0])[1] += 1

    def _note_sealed_pool(self, pool_open_ms: int) -> None:
        self._ensure_sealed_cursor(pool_open_ms)
        self._sealed_hours.setdefault(int(pool_open_ms) // 3_600_000, [0, 0])[0] += 1

    def _probe_links(self, now_ms_: int) -> None:
        if self.link_probe is None:
            return
        try:  # the probe AND its evaluation: nothing here may stop a trigger from being written
            got = self.link_probe()
            if got:
                self.note_feed_stats(got[0], got[1], now_ms_)
        except Exception:  # noqa: BLE001
            self.counters["link_probe_errors"] += 1

    def _flush_sealed_hours(self, now_ms_: int, final: bool = False) -> None:
        """One unlabelled record for EVERY UTC hour from the seal start (or from the process start, if later), whether or not it has any sealed pool:
        how many sealed pools opened in it and how many decisions their pools took (trigger or skipped candidates, counted under the pool's open hour).
        `decisions` is the string "<5" below 5, and the string "withheld" whenever a pick oracle is wired (see the comment below). With a pick
        oracle a pool is counted in `pools_opened` when its verdict is final: at s0 for a pick known then, else at close if it closed sealed. A pool
        closes within WALL_CLOSE of its s0, so the count lands before its hour is reported. An hour is reported only once WALL_CLOSE has passed for every pool that could have opened in it, so
        the existence of a record says nothing. No pool, mint, slot or variant. The current hour is reported only on shutdown, flagged partial; so
        is any earlier hour whose WALL_CLOSE wait had not run out at shutdown (close_all closed its pools early). Hours are per run: a restart
        writes a second record for its first hour and none for the downtime hours, so a reader sums sealed_hour records by hour."""
        if self._sealed_cursor is None:
            return
        cur = int(now_ms_) // 3_600_000
        last = cur if final else (int(now_ms_) - int(WALL_CLOSE_S * 1000)) // 3_600_000 - 1
        while self._sealed_cursor <= last:
            h = self._sealed_cursor
            opened, n = self._sealed_hours.pop(h, [0, 0])
            hour = datetime.fromtimestamp(h * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H")
            # With a pick oracle the sealed pools are the CAP-PICK picks, so their H5 decision count (a trigger means the pool drained) would be
            # an outcome-linked count of counted picks: EXP-022 s9 lets monitoring print only hour counts and gate decision counts. Withheld.
            dec = "withheld" if self.pick_oracle is not None else (n if n >= SEALED_MIN_COUNT else f"<{SEALED_MIN_COUNT}")
            self.emit({"type": "sealed_hour", "hour": hour, "pools_opened": opened, "decisions": dec,
                       "partial": bool(final and (h + 1) * 3_600_000 + int(WALL_CLOSE_S * 1000) > int(now_ms_))})
            self._sealed_cursor += 1


def _sanitized(exc: BaseException) -> BaseException:
    """A fresh exception of a class with the same NAME and nothing else (no args, no traceback, no cause), for logging about a sealed pool."""
    return type(type(exc).__name__, (Exception,), {})()


def _neg(s: str) -> tuple:
    """Sort key that makes max() pick the lexicographically lowest id on a tie."""
    return tuple(-ord(c) for c in s)


# ---- output ---------------------------------------------------------------------------------------------------
class JsonlSink:
    """Hourly strict-JSON-lines files, flushed per line. The hour is the UTC hour of the record's t_ms."""

    def __init__(self, out_dir: str | Path, prefix: str = "h5-shadow") -> None:
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self._fh = None
        self._hour: str | None = None
        self.lines = 0

    def path_for(self, t_ms: int) -> Path:
        hour = datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
        return self.dir / f"{self.prefix}-{hour}.jsonl"

    def write(self, rec: dict) -> None:
        t_ms = int(rec.get("t_ms") or now_ms())
        path = self.path_for(t_ms)
        if self._hour != path.name:
            if self._fh:
                self._fh.close()
            self._fh = open(path, "a", encoding="utf-8")
            self._hour = path.name
        self._fh.write(json.dumps(clean(rec), separators=(",", ":"), allow_nan=False) + "\n")
        self._fh.flush()
        self.lines += 1

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class ErrorLog:
    """Full tracebacks of engine / decode failures, kept out of the JSONL. Capped so a stuck bug cannot fill the disk."""

    def __init__(self, path: str | Path, max_bytes: int = ERRORS_MAX_BYTES) -> None:
        self.path, self.max_bytes, self.bytes, self.dropped = Path(path), max_bytes, 0, 0

    def log(self, exc: BaseException, ctx: dict | None = None) -> None:
        if self.bytes >= self.max_bytes:
            self.dropped += 1
            return
        head = json.dumps(clean({"t_ms": now_ms(), "exc": type(exc).__name__, "msg": str(exc)[:300], **(ctx or {})}), allow_nan=False)
        text = head + "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)
        self.bytes += len(text)


def _strip_url(u: str) -> str:
    """scheme://host/path only: no userinfo, query or fragment (public RPC URLs can carry an api-key query)."""
    try:
        sp = urlsplit(u)
    except ValueError:
        return "REDACTED"
    if not sp.scheme or not sp.hostname:
        return "REDACTED"
    return f"{sp.scheme}://{sp.hostname}{':' + str(sp.port) if sp.port else ''}{sp.path}" + ("?REDACTED" if sp.query else "")


def _host_only(u: str) -> str:
    """scheme://host only: an RPC URL can carry its key in the userinfo, the path (/v2/<key>) or the query."""
    try:
        sp = urlsplit(u)
    except ValueError:
        return "REDACTED"
    return f"{sp.scheme}://{sp.hostname}" if sp.scheme and sp.hostname else "REDACTED"


def redact_argv(argv: Sequence[str]) -> list[str]:
    """--ws-url keeps scheme://host/path (no userinfo or query); --rpc-url keeps scheme://host only."""
    out: list[str] = []
    flag: str | None = None
    for a in argv:
        if flag is not None:
            out.append(_host_only(a) if flag == "--rpc-url" else _strip_url(a))
            flag = None
        elif a in ("--ws-url", "--rpc-url"):
            out.append(a)
            flag = a
        elif a.startswith("--rpc-url="):
            out.append("--rpc-url=" + _host_only(a.split("=", 1)[1]))
        elif a.startswith("--ws-url="):
            out.append("--ws-url=" + _strip_url(a.split("=", 1)[1]))
        else:
            out.append(a)
    return out


def check_out_dir(path: str) -> Path:
    if ".." in Path(path).parts:
        raise ValueError(f"out dir {path!r} contains '..'")
    return Path(path)


def feed_snapshot(source: Any) -> dict | None:
    """Reconnect counters of a trade_source feed (single socket or merged), merged across sockets, for note_feed_stats."""
    stats = getattr(source, "stats", None)
    if stats is None or not isinstance(getattr(stats, "reconnects", None), int):
        return None
    socks = getattr(stats, "sockets", None)
    parts = list(socks) if socks else [stats]

    def merged(attr: str) -> dict:
        out: dict[str, int] = {}
        for s in parts:
            for k, v in (getattr(s, attr, None) or {}).items():
                out[str(k)] = out.get(str(k), 0) + int(v)
        return out

    links = [getattr(s, "link", None) for s in parts]
    snap = {"reconnects": stats.reconnects, "closes": merged("closes"), "rejections": merged("rejections"), "errors": merged("errors"),
            "per_socket": [int(getattr(s, "reconnects", 0)) for s in parts], "sockets": len(parts)}
    if all(l is not None for l in links):
        snap["socket_states"] = [l.snapshot() for l in links]
    return snap


def write_status(path: Path, status: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean(status), indent=1, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


# ---- notice decode (live) -------------------------------------------------------------------------------------
def decode_notice(engine: Engine, note: Any, pool_mints: dict[str, tuple[str, str]]) -> int:
    """One RawNotice (observe.trade_source) into the engine. Returns the number of PumpSwap prints fed. Failed txs are skipped."""
    if note.failed:
        engine.advance(note.slot, note.t_recv_ms, None)
        return 0
    logs = note.logs
    if not any("Program data: " in ln for ln in logs):
        engine.advance(note.slot, note.t_recv_ms, None)
        return 0
    boost_events = []
    for ln in logs:  # a CreatePool announcement must precede the pool's first print; a BOOST event follows the BuyEvent of its own tx
        i = ln.find("Program data: ")
        if i < 0:
            continue
        blob = ln[i + 14:].strip()
        if blob.startswith(_CREATE_POOL_PREFIX):
            raw = _program_data_bytes(ln)
            ev = decode_program_data(raw) if raw else None
            if ev and ev.get("kind") == "create_pool":
                engine.on_create_pool(ev["pool"], ev["base_mint"], ev["quote_mint"], note.slot, note.t_recv_ms)
                clf = engine.classifier
                if clf is not None and hasattr(clf, "observe_migrate_notice"):  # this tx is the pool's migrate tx: its PostCompleteBuyEvent (if any) counts
                    clf.observe_migrate_notice(ev["base_mint"], note)
                    engine.request_class(ev["base_mint"], note.signature)  # the notice's logs are cut in practice (the pump events sit in inner instructions): read the tx
        elif blob.startswith(_BOOST_EVENT_PREFIX):
            raw = _program_data_bytes(ln)
            ev = decode_extra_event(raw) if raw else None
            if ev and ev.get("type") == "boost_buy_and_burn":
                boost_events.append(ev)
    rows = records_from_logs(logs, slot=note.slot, signature=note.signature, t_recv_ms=note.t_recv_ms, commitment=note.commitment, feed=note.feed,
                             pool_mints=pool_mints, event_v=True)
    n = 0
    for row in rows:
        if row.get("venue") == "pumpswap":
            engine.on_trade(row)
            n += 1
    for ev in boost_events:
        engine.on_boost_event(ev, note.slot, note.t_recv_ms)
    if n == 0:
        engine.advance(note.slot, note.t_recv_ms, None)
    return n


# ---- live runner ----------------------------------------------------------------------------------------------
async def run_feed(source_factory: Callable[[], Any], engine: Engine, stop: asyncio.Event, *, backoff0: float = 1.0, backoff_max: float = 60.0,
                   max_seconds: float | None = None, sleep: Callable[[float], Any] = asyncio.sleep,
                   on_decode_error: Callable[[BaseException, Any], None] | None = None) -> None:
    """Consume notices until stop. A source that raises or ends is rebuilt after a backoff; each restart is a logged gap that flags open pools."""
    pool_mints: dict[str, tuple[str, str]] = {}
    started = time.monotonic()
    backoff = backoff0
    first = True
    while not stop.is_set():
        if not first:
            engine.feed_restart(engine.wall(), "source_restart")
        first = False
        try:
            source = source_factory()
            async for note in source.notices(stop):
                backoff = backoff0
                try:  # an engine / decode bug on one notice must not take the sockets down
                    decode_notice(engine, note, pool_mints)
                except Exception as exc:  # noqa: BLE001
                    engine.counters["decode_errors"] += 1
                    if engine.counters["decode_errors"] <= ERROR_RECORDS_MAX:
                        engine.emit({"type": "error", "where": "decode", "exc": type(exc).__name__, "msg": str(exc)[:200], "slot": getattr(note, "slot", None),
                                     "signature": getattr(note, "signature", None)})
                    if on_decode_error is not None:
                        try:
                            on_decode_error(exc, note)
                        except Exception:  # noqa: BLE001
                            pass
                if len(pool_mints) > 50_000:
                    for k in list(pool_mints)[:25_000]:
                        del pool_mints[k]
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    stop.set()
                    break
            if stop.is_set():
                break
            log.warning("source ended; restarting")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the feed must outlive any one failure
            engine.counters["feed_errors"] += 1
            log.warning("feed error %s: %s", type(exc).__name__, str(exc)[:200])
        engine.counters["feed_restarts"] += 1
        await sleep(backoff)
        backoff = min(backoff * 2, backoff_max)


async def housekeeping(engine: Engine, sink: JsonlSink, status_path: Path, stop: asyncio.Event, source_ref: dict, interval_s: float = 5.0,
                       hb_s: float = 60.0, on_error: Callable[[BaseException, dict], None] | None = None) -> None:
    last_hb = 0.0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
        now = engine.wall()
        try:
            engine.tick(now)
            src = source_ref.get("source")
            snap = feed_snapshot(src)
            if snap is not None:
                engine.note_feed_stats(id(src), snap, now)
            if time.monotonic() - last_hb >= hb_s or stop.is_set():
                last_hb = time.monotonic()
                st = status_snapshot(engine, sink, src)
                engine.emit({"type": "hb", **st})
                write_status(status_path, {"schema": SCHEMA, "updated_ms": now, **st})
        except Exception as exc:  # noqa: BLE001 - housekeeping must not die
            engine.counters["housekeeping_errors"] += 1
            if on_error is not None:
                on_error(exc, {"where": "housekeeping"})


def _tip_pool(ln: bytes) -> str | None:
    """The "pool" value of one tip JSON line without parsing it (a pool-less row has "pool": null -> None)."""
    i = ln.find(b'"pool":')
    if i < 0:
        return None
    j, n = i + 7, len(ln)
    while j < n and ln[j] == 32:
        j += 1
    if j >= n or ln[j] != 34:
        return None
    k = ln.find(b'"', j + 1)
    return ln[j + 1:k].decode("ascii", "replace") if k > j else None


def _tip_slot(ln: bytes) -> int | None:
    i = ln.find(b'"slot":')
    if i < 0:
        return None
    j, n = i + 7, len(ln)
    while j < n and ln[j] == 32:
        j += 1
    k = j
    while k < n and 48 <= ln[k] <= 57:
        k += 1
    return int(ln[j:k]) if k > j else None


def _proc_mem(path: str = "/proc/self/status") -> dict:
    """VmRSS, RssAnon and VmHWM of this process in kB (null when /proc is not readable). Never raises."""
    out: dict[str, int | None] = {"rss_kb": None, "rss_anon_kb": None, "hwm_kb": None}
    names = {"VmRSS:": "rss_kb", "RssAnon:": "rss_anon_kb", "VmHWM:": "hwm_kb"}
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            for ln in fh:
                parts = ln.split()
                if len(parts) >= 2 and parts[0] in names:
                    out[names[parts[0]]] = int(parts[1])
    except (OSError, ValueError):
        pass
    return out


class TipTail:
    """Incremental, fail-soft reader of the tip follower's hourly trade files (<dir>/trades-<YYYY-MM-DDTHH>.jsonl) for the report-only union.

    Only the current and the previous UTC hour (wall clock) are open. A file already present at the first poll is read from its END (the shadow
    tracks only pools announced after it started, so older rows are never needed); a file that appears later is read from its start. Each poll
    reads at most TIP_CHUNK_BYTES per file; a line is JSON-parsed only when its pool is wanted (`interested`), so the cost of an unwanted row is a
    byte search. No thread, no lock: tip_follow runs it on the event loop between notices and yields after every chunk. Any OS or parse error is
    counted and the union field goes null; the reader never raises into the caller and never touches a decision field. An OS error on the tip
    dir itself (missing, or PermissionError on it or a parent) makes the status "unreadable"; the exception is kept in last_exc for tip_follow's
    rate-limited error log. When a read finds no new bytes the open file is checked against its path: a new inode (rotation) reopens the path
    from its start, a size below the read offset (truncation: fast_tip_follower cuts a crashed slot write back to its pre-write size, a line
    boundary) reopens it at the new end with the partial line dropped. Both count a reset; rows read twice are absorbed as `dup`. Not detected:
    a truncation followed by enough new bytes to pass the old offset before the next poll (0.25 s).
    Offsets: each hour keeps the byte offset of its first unconsumed line (offs). After a read error the file is reopened THERE, so no row is
    delivered twice and an hour is never re-read from byte 0; a file present at the first poll that could not be opened then starts at its size
    at that poll (stat) or, if even that failed, at its end. Whenever a file is (re)opened at an offset that is not a line start (the end of a
    file being written, a truncation cut inside a line, a dropped over-long line) the first fragment, up to and including the next newline, is
    dropped (fragments_dropped), never parsed as a row."""

    def __init__(self, tip_dir: str | Path, on_row: Callable[[dict], None], interested: Callable[[str], bool], *, wall: Callable[[], int] = now_ms,
                 chunk: int = TIP_CHUNK_BYTES) -> None:
        self.dir = Path(tip_dir)
        self.on_row, self.interested, self.wall, self.chunk = on_row, interested, wall, chunk
        self.files: dict[str, list] = {}  # hour -> [fd, partial line, file offset read up to, skipping the first fragment]
        self.offs: dict[str, int] = {}  # hour -> file offset of the first unconsumed line (where a reopen starts)
        self.end_hours: set[str] = set()  # hours present at the first poll whose open and stat both failed: opened at their end later
        self.started = False
        self.hw_slot: int | None = None  # highest slot of the last complete line read (any pool)
        self.last_line_ms: int | None = None  # wall time a line was last read
        self.err: str | None = None  # last poll's error ("no_dir", an OSError class name); cleared by a clean poll
        self.last_exc: BaseException | None = None  # the last OS error caught by poll, for tip_follow's rate-limited log (it clears it)
        self.full: set[str] = set()  # hours whose last read returned a full chunk (see pending)
        self.c: collections.Counter = collections.Counter()

    @staticmethod
    def _hour(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")

    def _close_fd(self, hour: str) -> None:
        self.full.discard(hour)
        ent = self.files.pop(hour, None)
        if ent is not None:
            try:
                os.close(ent[0])
            except OSError:
                pass

    def _path(self, hour: str) -> Path:
        return self.dir / f"trades-{hour}.jsonl"

    def _open(self, hour: str, whence: int | None, offset: int = 0) -> list | str | None:
        """Open one hour file: [fd, partial, offset, skip] (whence None = from the start), None when it does not exist, an error class name on an
        OS error. skip is True when the offset is not a line start (the byte before it is not a newline)."""
        try:
            fd = os.open(self._path(hour), os.O_RDONLY)
        except FileNotFoundError:
            return None
        except OSError as e:
            self.last_exc = e
            self.c["open_errors"] += 1
            return type(e).__name__
        try:
            pos = 0 if whence is None else os.lseek(fd, offset, whence)
            skip = pos > 0 and os.pread(fd, 1, pos - 1) != b"\n"
        except OSError as e:
            os.close(fd)
            self.last_exc = e
            self.c["open_errors"] += 1
            return type(e).__name__
        ent = self.files[hour] = [fd, b"", pos, skip]
        self.offs[hour] = pos
        self.end_hours.discard(hour)
        self.c["files_opened"] += 1
        return ent

    def _check_reset(self, hour: str, ent: list) -> str | None:
        """At EOF of an open hour file: reopen on rotation (a new inode at the path: from its start) or truncation (size below the offset: at
        the new end, the partial line dropped). Returns an error class name on an OS error, else None."""
        try:
            fst = os.fstat(ent[0])
            pos = os.lseek(ent[0], 0, os.SEEK_CUR)
            try:
                pst = os.stat(self._path(hour))
            except FileNotFoundError:
                return None  # removed: keep the open file until its hour leaves the window
        except OSError as e:
            self.last_exc = e
            self.c["stat_errors"] += 1
            return type(e).__name__
        if (pst.st_ino, pst.st_dev) != (fst.st_ino, fst.st_dev):
            kind, whence, off = "resets_rotated", None, 0
        elif fst.st_size < pos:
            kind, whence, off = "resets_truncated", os.SEEK_SET, fst.st_size
        else:
            return None
        self._close_fd(hour)
        self.offs[hour] = off  # a failed reopen is retried there on the next poll
        self.c["resets"] += 1
        self.c[kind] += 1
        got = self._open(hour, whence, off)
        return got if isinstance(got, str) else None

    def poll(self) -> bool:
        """One step over the open hours. True when a chunk came back full (more is waiting: call again after yielding)."""
        now = self.wall()
        want = [self._hour(now - 3_600_000), self._hour(now)]
        for hour in [h for h in self.files if h not in want]:
            self._close_fd(hour)
        for hour in [h for h in self.offs if h not in want]:
            del self.offs[hour]
        self.end_hours &= set(want)
        try:
            is_dir = self.dir.is_dir()  # py3.12: PermissionError (not False) when a parent cannot be traversed
        except OSError as e:
            self.err, self.last_exc = type(e).__name__, e
            self.c["dir_errors"] += 1
            return False
        if not is_dir:
            self.err = "no_dir"
            self.c["no_dir_polls"] += 1
            return False
        err, more, delivered_at = None, False, 0
        for i, hour in enumerate(want):
            e, m, d = self._poll_hour(hour)
            err, more = e or err, more or m
            if d:
                delivered_at = i  # the LAST file that delivered lines
        if delivered_at > 0:
            # Second pass over the EARLIER hours (tipauth, hour boundary). The follower writes slot S - 1 to trades-<H> and, after that write has
            # returned, slot S to trades-<H+1>. If this poll read <H+1> after <H> and saw slot S, a read of <H> made AFTER it sees slot S - 1 and
            # everything before: so hw_slot never leads a drained earlier file. Rows delivered here come within the same poll (no await between).
            for hour in want[:delivered_at]:
                e, m, _ = self._poll_hour(hour)
                err, more = e or err, more or m
        self.started = True
        self.err = err
        return more

    def _poll_hour(self, hour: str) -> tuple[str | None, bool, bool]:
        """One read step of one hour file: (error class name | None, a full chunk came back, lines were delivered). Maintains self.full."""
        ent = self.files.get(hour)
        if ent is None:
            off = self.offs.get(hour)
            if off is not None:  # reopened after an error or a failed reset: at the saved line start, never at byte 0
                ent = self._open(hour, os.SEEK_SET, off)
            elif not self.started or hour in self.end_hours:
                ent = self._open(hour, os.SEEK_END)
                if isinstance(ent, str) and not self.started:  # present but not openable at the first poll: start at its size now
                    try:
                        self.offs[hour] = os.stat(self._path(hour)).st_size
                    except OSError:
                        self.end_hours.add(hour)
            else:
                ent = self._open(hour, None)
            if isinstance(ent, str):
                return ent, False, False
            if ent is None:
                self.full.discard(hour)
                return None, False, False
        try:
            data = os.read(ent[0], self.chunk)
        except OSError as e:
            self.last_exc = e
            self.c["read_errors"] += 1
            self._close_fd(hour)  # offs[hour] still holds the first unconsumed line: the next poll reopens there
            return type(e).__name__, False, False
        if not data:
            self.full.discard(hour)
            reset = self._check_reset(hour, ent)
            return (reset if reset is not None else None), False, False
        if len(data) >= self.chunk:
            self.full.add(hour)  # more may be waiting in this file: nothing about its tail is known
        else:
            self.full.discard(hour)
        more = len(data) >= self.chunk
        self.c["bytes"] += len(data)
        ent[2] += len(data)
        buf = ent[1] + data
        if ent[3]:  # not at a line start: drop the first fragment
            i = buf.find(b"\n")
            if i < 0:
                ent[1] = b""
                self.offs[hour] = ent[2]
                return None, more, False
            buf = buf[i + 1:]
            ent[3] = False
            self.c["fragments_dropped"] += 1
        lines = buf.split(b"\n")
        ent[1] = lines.pop()
        if len(ent[1]) > TIP_LINE_MAX:
            ent[1] = b""
            ent[3] = True  # the rest of that line is dropped too, up to its newline
            self.c["long_lines_dropped"] += 1
        self.offs[hour] = ent[2] - len(ent[1])
        if lines:
            self._lines(lines)
        return None, more, bool(lines)

    def pending(self) -> bool:
        """True while any open hour file's last read was a full chunk (unread bytes may remain, so hw_slot may lead unread rows). tipauth reads
        it: the field is null (status tip_pending) then. With it False, and the second pass of poll(), every open file was drained after the
        latest read of any later hour file."""
        return bool(self.full)

    def _lines(self, lines: list[bytes]) -> None:
        self.c["lines"] += len(lines)
        for ln in lines:
            pool = _tip_pool(ln)
            if pool is None or not self.interested(pool):
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                self.c["parse_errors"] += 1
                continue
            self.c["rows_kept"] += 1
            self.on_row(row)
        s = _tip_slot(lines[-1])
        if s is not None and (self.hw_slot is None or s > self.hw_slot):
            self.hw_slot = s
        self.last_line_ms = self.wall()

    def status(self, feed_hw_slot: int | None) -> str:
        """ok | starting (no line read yet) | stale (no line for TIP_STALE_MS, or TIP_STALE_SLOTS behind the feed) | unreadable."""
        if self.err is not None:
            return "unreadable"
        if self.last_line_ms is None:
            return "starting"
        if self.wall() - self.last_line_ms > TIP_STALE_MS:
            return "stale"
        if feed_hw_slot is not None and self.hw_slot is not None and feed_hw_slot - self.hw_slot > TIP_STALE_SLOTS:
            return "stale"
        return "ok"

    def stats(self) -> dict:
        return {"dir": str(self.dir), "hw_slot": self.hw_slot, "err": self.err, "files_open": sorted(self.files),
                "partial_bytes": sum(len(e[1]) for e in self.files.values()),
                "last_line_age_ms": None if self.last_line_ms is None else self.wall() - self.last_line_ms, **dict(self.c)}

    def close(self) -> None:
        for hour in list(self.files):
            self._close_fd(hour)


async def tip_follow(tail: TipTail, stop: asyncio.Event, *, poll_s: float = TIP_POLL_S, on_error: Callable[[BaseException, dict], None] | None = None,
                     log_every_s: float = TIP_ERR_LOG_EVERY_S, clock: Callable[[], float] = time.monotonic) -> None:
    """Drive TipTail on the event loop: a full chunk is followed by the next after asyncio.sleep(0), so a notice waiting in the loop runs between
    chunks. Errors are counted and logged, rate-limited: the first of each exception class goes to on_error, then at most one per class per
    log_every_s (carrying the number suppressed since), so a persistent error (e.g. an unreadable tip dir, 4 polls a second) cannot fill the
    capped error log and crowd out the engine's own tracebacks. The task never stops the feed."""
    logged: dict[str, list] = {}  # exception class -> [last logged (clock s), suppressed since]

    def report(exc: BaseException) -> None:
        if on_error is None:
            return
        name, now = type(exc).__name__, clock()
        ent = logged.get(name)
        if ent is not None and now - ent[0] < log_every_s:
            ent[1] += 1
            tail.c["errors_log_suppressed"] += 1
            return
        tail.c["errors_logged"] += 1
        logged[name] = [now, 0]
        try:
            on_error(exc, {"where": "tip_follow", "suppressed_since_last": 0 if ent is None else ent[1]})
        except Exception:  # noqa: BLE001 - the error log itself may fail; the tail goes on
            pass

    try:
        while not stop.is_set():
            try:
                while tail.poll() and not stop.is_set():
                    await asyncio.sleep(0)
            except Exception as exc:  # noqa: BLE001
                tail.c["poll_errors"] += 1
                tail.err = type(exc).__name__
                report(exc)
            if tail.last_exc is not None:  # an OS error poll counted and absorbed (status "unreadable", or a skipped file)
                exc, tail.last_exc = tail.last_exc, None
                report(exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            except asyncio.TimeoutError:
                pass
    finally:  # a cancelled task (CancelledError at an await) closes its file descriptors too
        tail.close()


class ReplayTip:
    """Replay stand-in for TipTail (A/B and cost measurement): the tape is its own tip, each row released once the feed's high-water slot is
    `lag` slots past it. Always fresh."""

    def __init__(self, lag: int) -> None:
        self.lag, self.q, self.hw_slot = int(lag), collections.deque(), None
        self.c: collections.Counter = collections.Counter()

    def status(self, feed_hw_slot: int | None) -> str:
        return "ok"

    def push(self, row: dict) -> None:
        self.q.append(row)

    def release(self, engine: Engine, final: bool = False) -> None:
        hw = engine.hw_slot
        while self.q and (final or (hw is not None and self.q[0]["slot"] <= hw - self.lag)):
            row = self.q.popleft()
            self.hw_slot = row["slot"] if self.hw_slot is None else max(self.hw_slot, row["slot"])
            self.c["rows"] += 1
            engine.on_tip_row(row)

    def stats(self) -> dict:
        return {"replay_lag_slots": self.lag, "hw_slot": self.hw_slot, **dict(self.c)}


def status_snapshot(engine: Engine, sink: JsonlSink | None, source: Any) -> dict:
    s = {
        "hw_slot": engine.hw_slot, "last_event_ms": engine.last_event_ms, "open_pools": len(engine.pools), "sps": engine.clock.sps(),
        "sps_points": engine.clock.n_points(), "sps_span_s": engine.clock.span_s(), "announced": len(engine.announced),
        "mint_pools": len(engine.mint_pools), "seen": len(engine.seen),
        "counters": dict(engine.counters),
    }
    clf_stats = getattr(engine.classifier, "stats", None)
    if clf_stats is not None:
        s["syn_class"] = dict(clf_stats)
    if sink is not None:
        s["lines"] = sink.lines
    if engine.tip is not None:  # report-only tip union health (absent when off)
        try:
            s["tip_union"] = {**engine.tip.stats(), "status": engine.tip.status(engine.hw_slot), "pending_pools": len(engine.tip_pending),
                              "mem": engine.union_mem()}
        except Exception as exc:  # noqa: BLE001
            s["tip_union"] = {"status": "error", "err": type(exc).__name__}
    stats = getattr(source, "stats", None)
    if stats is not None:
        s["feed"] = {"reconnects": getattr(stats, "reconnects", None), "notes": getattr(stats, "notes", None), "slot_jumps": getattr(stats, "slot_jumps", None),
                     "rejections": dict(getattr(stats, "rejections", {}) or {}), "closes": dict(getattr(stats, "closes", {}) or {})}
    snap = feed_snapshot(source) if source is not None else None
    if snap and snap.get("socket_states"):  # per-socket link health: lets the silence thresholds be tuned from live gaps
        now = engine.wall()
        states = snap["socket_states"]
        lp = engine.link_prev
        prev = lp[1] if lp is not None and lp[0] == id(source) and len(lp[1]) == len(states) else [{}] * len(states)  # a rebuilt source starts at zero
        socks, cur_all = [], []
        for st, pv in zip(states, prev):
            cur = {"gap_hist": list(st.get("gap_hist") or []), "sub_latency_hist": list(st.get("sub_latency_hist") or []),
                   "drop_silences": int(st.get("drop_silences") or 0)}
            cur_all.append(cur)
            socks.append({
                "up": st["up"], "last_notice_age_ms": None if st.get("last_notice_ms") is None else now - st["last_notice_ms"],
                "last_delivery_age_ms": None if st.get("last_delivery_ms") is None else now - st["last_delivery_ms"],
                "max_gap_ms": st.get("max_gap_ms"), "gap_hist": st.get("gap_hist"), "gap_hist_delta": _hist_delta(cur["gap_hist"], pv.get("gap_hist")),
                "sub_latency_hist": st.get("sub_latency_hist"), "sub_latency_hist_delta": _hist_delta(cur["sub_latency_hist"], pv.get("sub_latency_hist")),
                "max_sub_latency_ms": st.get("max_sub_latency_ms"), "drop_silences": cur["drop_silences"],
                "drop_silences_delta": max(0, cur["drop_silences"] - int(pv.get("drop_silences") or 0)) if pv else cur["drop_silences"],
                "silent_intervals": len(st.get("silent_intervals") or []), "down_intervals": len(st.get("intervals") or [])})
        engine.link_prev = (id(source), cur_all)
        s["link"] = {"gap_edges_ms": list(GAP_EDGES_MS), "delta_since": "previous status record of this run (hb or stop)", "sockets": socks}
    return s


def _hist_delta(cur: list, prev: list | None) -> list:
    """Per-bucket increase since the previous status record. A histogram that shrank (a rebuilt source starts at zero) is taken as new."""
    if not prev or len(prev) != len(cur) or any(c < p for c, p in zip(cur, prev)):
        return list(cur)
    return [c - p for c, p in zip(cur, prev)]


def build_source(ws_urls: Sequence[str], sockets: int, commitment: str) -> Any:
    from observe.trade_source import DEFAULT_PUBLIC_WS, LogsSubscribeSource, MultiSocketLogsSource  # needs websockets + certifi (the listener venv)

    urls = list(ws_urls) or [DEFAULT_PUBLIC_WS]
    if sockets > 1:
        return MultiSocketLogsSource(ws_urls=urls, sockets=sockets, programs=(PUMPSWAP_PROGRAM,), commitment=commitment)
    return LogsSubscribeSource(ws_url=urls[0], programs=(PUMPSWAP_PROGRAM,), commitment=commitment)


def build_pump_source(ws_urls: Sequence[str], sockets: int, commitment: str) -> Any:
    """The pump.fun bonding-curve logsSubscribe, a side feed for the synthetic-migration class only (the PumpSwap feed above does not carry the
    CompleteEvent). Its own source object: it is never in source_ref, so the engine's clock, link state and gap records do not see it."""
    from observe.trade_source import DEFAULT_PUBLIC_WS, LogsSubscribeSource, MultiSocketLogsSource
    from observe.trade_decode import PUMP_BONDING_PROGRAM

    urls = list(ws_urls) or [DEFAULT_PUBLIC_WS]
    if sockets > 1:
        return MultiSocketLogsSource(ws_urls=urls, sockets=sockets, programs=(PUMP_BONDING_PROGRAM,), commitment=commitment)
    return LogsSubscribeSource(ws_url=urls[0], programs=(PUMP_BONDING_PROGRAM,), commitment=commitment)


async def run_pump_feed(source_factory: Callable[[], Any], classifier: SynClassifier, stop: asyncio.Event, *, backoff0: float = 1.0, backoff_max: float = 60.0,
                        sleep: Callable[[float], Any] = asyncio.sleep, on_error: Callable[[BaseException, dict], None] | None = None) -> None:
    """Feed pump.fun notices to the classifier until stop. A source that raises or ends is rebuilt after a backoff. Nothing here writes a record or
    touches the engine: a failure only leaves mints unclassified, which the RPC fallback and then the exclusion rule cover."""
    backoff = backoff0
    while not stop.is_set():
        try:
            source = source_factory()
            async for note in source.notices(stop):
                backoff = backoff0
                try:
                    classifier.observe_notice(note)
                except Exception as exc:  # noqa: BLE001 - one bad notice must not take the side feed down
                    classifier.stats["ws_decode_errors"] += 1
                    if on_error is not None:
                        try:
                            on_error(exc, {"where": "syn_ws", "slot": getattr(note, "slot", None), "signature": getattr(note, "signature", None)})
                        except Exception:  # noqa: BLE001
                            pass
            if stop.is_set():
                break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            classifier.stats["ws_feed_errors"] += 1
            log.warning("pump side feed error %s: %s", type(exc).__name__, str(exc)[:200])
        classifier.stats["ws_restarts"] += 1
        await sleep(backoff)
        backoff = min(backoff * 2, backoff_max)


def check_rpc_url(url: str) -> str:
    """The RPC fallback is public RPC only: no Helius URL (0 credits), no key in the URL."""
    u = urlsplit(url)
    if u.scheme != "https" or "helius" in (u.hostname or "").lower() or u.query or u.username or u.password:
        raise ValueError("--rpc-url must be a plain https public RPC URL (no Helius, no query string, no credentials)")
    return url


def check_look2_ref(ref: str | None) -> str | None:
    """The declared-observation flag of EXP-024 Amendment 2. None (absent) keeps the H5 Look-2 outcome seal; the only value that lifts it is the
    literal H5_LOOK2_AMENDMENT_REF. Anything else, an empty string included, is refused rather than ignored."""
    if ref is None:
        return None
    if ref != H5_LOOK2_AMENDMENT_REF:
        raise ValueError(f"--h5-look2-observed must be exactly {H5_LOOK2_AMENDMENT_REF!r} (the EXP-024 Amendment 2 reference), got {ref!r}")
    return ref


def bx10_flag(ref: str | None) -> bool:
    """True only for the literal BX10_ENABLE_REF (EXP-026). None, an empty string and every other value, "1" and "exp-026" included, count as off:
    the report-only bx10 variant is a declared flag, not a truthy switch. Unlike --h5-look2-observed a wrong value is not refused; it just stays off."""
    return ref == BX10_ENABLE_REF


def fresh_forward_flag(ref: str | None) -> bool:
    """True only for the literal FRESH_FORWARD_REF ("ARENA"). None, an empty string and every other value, "arena" and "1" included, count as off (the
    bx10 pattern): the fresh-forward arena mode is a declared flag, not a truthy switch, and a wrong value just leaves the default behaviour."""
    return ref == FRESH_FORWARD_REF


def check_fresh_out_dir(path: str, *, home: str | None = None) -> Path:
    """The out dir of a fresh-forward run. It must resolve (symlinks and '..' removed) to a directory under ARENA_ROOT/h5-*, and the unresolved
    path must too. Refused first and explicitly: /srv/mal-h5-shadow (the live executor's feed), /var/lib/mal-live and $HOME/data/h5-shadow*, whether
    spelled directly or reached through a symlink. This mode never feeds the live executor. Raises ValueError."""
    if not path or ".." in Path(path).parts:
        raise ValueError(f"--fresh-forward out dir {path!r} is empty or contains '..'")
    home = home or os.path.expanduser("~")
    forbidden = [(p, "live shadow / executor path") for p in FRESH_FORBIDDEN_OUT]
    forbidden.append((os.path.join(home, "data", "h5-shadow"), "$HOME/data/h5-shadow*"))
    raw, real = os.path.abspath(path), os.path.realpath(path)
    for cand in (raw, real):
        for bad, why in forbidden:
            for b in {bad, os.path.realpath(bad)}:
                if cand == b or cand.startswith(b + "/") or (b.endswith("h5-shadow") and cand.startswith(b)):  # the last clause: h5-shadow-<anything>
                    raise ValueError(f"--fresh-forward refuses out dir {path!r} ({why}): this mode never feeds the live executor")
    roots = [os.path.realpath(ARENA_ROOT), os.path.realpath(os.path.join(home, *ARENA_HOME_SUBDIR))]  # /data/mal/arena (research-0), $HOME/data/arena (mal-fast-0)
    for cand in (raw, real):
        ok = False
        for root in roots:
            rel = os.path.relpath(cand, root)
            if rel != "." and not rel.startswith("..") and rel.split(os.sep)[0].startswith(ARENA_OUT_PREFIX):
                ok = True
        if not ok:
            raise ValueError(f"--fresh-forward out dir {path!r} must resolve under {ARENA_ROOT}/{ARENA_OUT_PREFIX}* or {home}/{'/'.join(ARENA_HOME_SUBDIR)}/{ARENA_OUT_PREFIX}* (got {cand!r})")
    return Path(real)


def look2_start_info(look2_ref: str | None) -> dict:
    """The H5 Look-2 seal state, as the `start` record logs it."""
    return {"reason": H5_LOOK2_SEAL_REASON, "start_ms": H5_LOOK2_START_MS, "start": iso_from_ms(H5_LOOK2_START_MS),
            "end_ms": H5_LOOK2_END_MS, "end": iso_from_ms(H5_LOOK2_END_MS),
            "observed": look2_ref is not None, "amendment_ref": look2_ref}


_SHA_RE = re.compile(r"[0-9a-f]{40}")


def code_sha(environ: dict[str, str] | None = None, *, git_dir: str | Path | None = None) -> str | None:
    """The git sha of the running checkout, for the start record's code_sha: H5_CODE_SHA (scripts/research/h5-shadow.sh sets it from
    `git rev-parse HEAD` of its checkout) when it is a 40-hex sha, else `git rev-parse HEAD` of this module's checkout, else None. Never raises."""
    env = os.environ if environ is None else environ
    v = (env.get("H5_CODE_SHA") or "").strip().lower()
    if _SHA_RE.fullmatch(v):
        return v
    try:
        r = subprocess.run(["git", "-C", str(git_dir or Path(__file__).resolve().parent), "rev-parse", "HEAD"], capture_output=True, text=True,
                           timeout=5, stdin=subprocess.DEVNULL)
        v = r.stdout.strip().lower()
        return v if r.returncode == 0 and _SHA_RE.fullmatch(v) else None
    except Exception:  # noqa: BLE001 - no git, a timeout, not a checkout: unknown
        return None


async def run_live(args: argparse.Namespace) -> int:
    fresh_ref = getattr(args, "fresh_forward", None)
    fresh = fresh_forward_flag(fresh_ref)
    if fresh_ref and not fresh:
        log.warning("--fresh-forward %r is not %r: fresh forward stays OFF (default behaviour, seals applied)", fresh_ref, FRESH_FORWARD_REF)
    q_star = check_q_star(getattr(args, "q_star_sol", Q_STAR_SOL))
    if q_star != Q_STAR_SOL:  # QP ruling 2026-10-22 s2: Q* != 40 is the Q50 arena book only
        if not fresh:
            raise ValueError("--q-star-sol != 40 requires --fresh-forward ARENA")
        if getattr(args, "h5_look2_observed", None) is not None:
            raise ValueError("--h5-look2-observed does not apply with --q-star-sol != 40 (EXP-024 Am.2 does not cover it)")
        if getattr(args, "bx10_enable", None) is not None:
            raise ValueError("--bx10-enable does not apply with --q-star-sol != 40 (bx10 is off for a Q* != 40 book)")
        if Q50_RULE_SHA256 is None:
            raise ValueError("Q50_RULE_SHA256 is not pinned yet (EXP-024 Amendment 10): a Q* != 40 process cannot start")
    if fresh:  # the arena mode: its own out dir, never the live executor's feed; checked before anything is created
        out_dir = check_fresh_out_dir(args.out_dir or "")
    else:
        out_dir = check_out_dir(args.out_dir or DEFAULT_OUT_DIR)
    tip_dir = getattr(args, "tip_dir", None)
    if fresh and getattr(args, "h5_look2_observed", None) is not None:
        raise ValueError("--h5-look2-observed does not apply with --fresh-forward (neither seal is applied)")
    if fresh and tip_dir:
        raise ValueError("--tip-dir does not apply with --fresh-forward (the tip union is off)")
    sink = JsonlSink(out_dir)
    errlog = ErrorLog(out_dir / "h5-shadow-errors.log")
    look2_ref = check_look2_ref(getattr(args, "h5_look2_observed", None))
    bx10_ref = getattr(args, "bx10_enable", None)
    bx10_on = (q_star == Q_STAR_SOL) if fresh else bx10_flag(bx10_ref)  # fresh forward writes outcome_bx10, except a Q* != 40 book (off)
    if bx10_ref and not bx10_flag(bx10_ref) and not fresh:
        log.warning("--bx10-enable %r is not %r: bx10 stays OFF", bx10_ref, BX10_ENABLE_REF)
    rpc_url = check_rpc_url(getattr(args, "rpc_url", None) or DEFAULT_RPC)
    classifier = SynClassifier()
    fallback = RpcFallback(classifier, rpc_url)
    start_ms: int | None = None
    if fresh:
        # The CAP-PICK oracle seal does not apply (EXP-022 was withdrawn 2026-10-15; no pick oracle is read). The H5 Look-2 seal's START does not apply (EXP-024 Am.2
        # covers v1 from 2026-10-16, and Am.9 the arena's v2), but an END does, fail closed: at S* the successor end SUCC_END_MS (2026-12-01T00Z; R7(b)), so a pool
        # with s0 at or after it gets its decision records only, no outcome, strip or ladder. A fresh window that starts at this instant is out-of-sample
        # by construction: a pool whose s0 is before it is never tracked.
        seal_info = {"oracle": "not_applied", "why": "fresh_forward_arena"}
        start_ms = now_ms()
        engine = Engine(sink.write, boost_mode=args.boost_mode, suppress_outcome=None, pick_oracle=None, seal_start_ms=None, h5_look2_start_ms=SUCC_END_MS,
                        h5_look2_observed=False, classifier=classifier, bx10_enabled=(q_star == Q_STAR_SOL), fresh_start_ms=start_ms, q_star_sol=q_star)
    else:
        pick_oracle, seal_info = cap_pick_oracle_from_env()
        if pick_oracle is None:
            log.warning("CAP-PICK pick oracle not configured (%s): every pool with s0 from %s is sealed (fail closed); set CAP_PICK_LIVE and "
                        "CAP_PICK_FINAL_MARKER", seal_info.get("why"), iso_from_ms(SEAL_START_MS))
        engine = Engine(sink.write, boost_mode=args.boost_mode, suppress_outcome=cap_pick_seal_oracle_stub, pick_oracle=pick_oracle,
                        h5_look2_observed=look2_ref is not None, classifier=classifier, bx10_enabled=bx10_on, q_star_sol=q_star)
    engine.syn_request = fallback.request
    engine.on_error = errlog.log
    if tip_dir:  # report-only tip union (phase 0); off = byte-identical records
        engine.tip = TipTail(tip_dir, engine.on_tip_row, engine.tip_interested, wall=engine.wall)
    if look2_ref is not None:
        log.warning("H5 Look-2 outcomes are DECLARED-OBSERVED (%s): pools with s0 in [%s, %s) write outcomes; CAP-PICK picks stay sealed", look2_ref,
                    iso_from_ms(H5_LOOK2_START_MS), iso_from_ms(H5_LOOK2_END_MS))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    source_ref: dict = {}

    def factory() -> Any:
        source_ref["source"] = build_source(args.ws_url or [], args.sockets, args.commitment)
        return source_ref["source"]

    start_rec = {"type": "start", "rule": RULE_ID, "rule_sha256": RULE_SHA256, "code_sha": code_sha(), "argv": redact_argv(sys.argv[1:]), "out_dir": str(out_dir), "pid": os.getpid(), "sockets": args.sockets,
                 "commitment": args.commitment, "boost_mode": args.boost_mode, "keys": "none", "sends": "none",
                 "seal": {"reason": SEAL_REASON, "start_ms": SEAL_START_MS, **seal_info},
                 "h5_look2": look2_start_info(look2_ref),
                 "bx10": bx10_on,
                 "synthetic_gate": {"rule": "no buy on a synthetic or unclassified pool (EXP-024 Am.4)", "sources": ["ws", "rpc"], "ws_program": "pump.fun logsSubscribe",
                                    "syn_sockets": getattr(args, "syn_sockets", 1), "rpc": "public getSignaturesForAddress+getTransaction", "rpc_attempt_delays_s": list(RPC_ATTEMPT_DELAYS_S)}}
    if fresh:  # the only change to the start record, and only in this mode: the flag, the start instant, and the seals as NOT applied
        start_rec.update({"t_ms": start_ms, "start_ms": start_ms, "fresh_forward": FRESH_FORWARD_REF,
                          "seal": {"reason": SEAL_REASON, "start_ms": None, "applied": False, **seal_info},
                          "h5_look2": {"reason": H5_LOOK2_SEAL_REASON, "start_ms": None, "applied": False, "observed": False, "amendment_ref": None,
                                       "end_only_ms": SUCC_END_MS, "end_only": iso_from_ms(SUCC_END_MS)},
                          "tip_union": "off", "dirty": os.environ.get("H5_DIRTY") == "1",  # the wrapper sets H5_DIRTY=1 under ARENA_H5_ALLOW_DIRTY: code_sha does not name the code
                          "endpoints": sorted({_strip_url(u) for u in (args.ws_url or [])}) or ["default"], "host": os.uname().nodename})
    if q_star != Q_STAR_SOL:  # only a non-default level changes the start record: the default one stays byte-identical
        start_rec.update({"rule": Q50_RULE_ID, "rule_sha256": Q50_RULE_SHA256, "q_star_sol": q_star})
    engine.emit(start_rec)

    def probe() -> tuple[int, dict] | None:
        src = source_ref.get("source")
        snap = feed_snapshot(src)
        return (id(src), snap) if snap is not None else None

    if engine.tip is not None:
        engine.emit({"type": "tip_union_start", "dir": str(tip_dir), "field": UNION_FIELD, "report_only": True, "stale_ms": TIP_STALE_MS,
                     "stale_slots": TIP_STALE_SLOTS, "poll_s": TIP_POLL_S, "chunk_bytes": TIP_CHUNK_BYTES})
    engine.link_probe = probe
    tip_task = None
    if engine.tip is not None:
        tip_task = asyncio.create_task(tip_follow(engine.tip, stop, on_error=lambda e, ctx: errlog.log(e, ctx)))
    hk = asyncio.create_task(housekeeping(engine, sink, out_dir / "h5-shadow-status.json", stop, source_ref, on_error=lambda e, ctx: errlog.log(e, ctx)))
    pump_task = asyncio.create_task(run_pump_feed(lambda: build_pump_source(args.ws_url or [], max(1, getattr(args, "syn_sockets", 1)), args.commitment), classifier,
                                                  stop, on_error=lambda e, ctx: errlog.log(e, ctx)))
    try:
        await run_feed(factory, engine, stop, max_seconds=args.max_seconds,
                       on_decode_error=lambda e, note: errlog.log(e, {"where": "decode", "slot": getattr(note, "slot", None), "signature": getattr(note, "signature", None)}))
    finally:
        stop.set()
        await hk
        if tip_task is not None:
            await asyncio.gather(tip_task, return_exceptions=True)
        pump_task.cancel()
        await asyncio.gather(pump_task, return_exceptions=True)
        await fallback.close()
        engine.close_all("shutdown")
        engine.emit({"type": "stop", **status_snapshot(engine, sink, source_ref.get("source"))})
        sink.close()
    return 0


# ---- replay (exploration tape) --------------------------------------------------------------------------------
TAPE_DIR = "/data/mal/audit-1008/tape"
WORK_DIR = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FROZEN_CONF = "/data/mal/hunt-1008/h5-flows/out/boostdip_frozen_conf.parquet"
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "oracle-live", "forward-paper", "forward-walk", "forward_walk", "forward-1002", "forward-1016",
             "exp012-gate", "runner-status", "/var/lib/mal/paper", ".env", "helius", "keypair", "walk-2", "walk2")
VOID = ("2026-09-15T12", "2026-09-18T23")  # EXP-009 hours [lo, hi)
CUTOFF_HOUR = "2026-10-02T10"


class Refused(Exception):
    pass


Q50_REPLAY_CUTOFF_HOUR = "2026-10-01T00"  # QP comment on #789 (P3): a Q* != 40 replay reads no hour at or after this, refused before anything is read


def refuse_replay(path: str, hours: Sequence[str], q_star_sol: float = Q_STAR_SOL) -> None:
    if q_star_sol != Q_STAR_SOL:
        for h in hours:
            if h >= Q50_REPLAY_CUTOFF_HOUR:
                raise Refused(f"{h}: a --q-star-sol != 40 replay reads no hour at or after {Q50_REPLAY_CUTOFF_HOUR}")
    for bad in FORBIDDEN:
        if bad in path:
            raise Refused(f"{path}: outside the exploration pools ({bad!r})")
    for h in hours:
        if VOID[0] <= h < VOID[1]:
            raise Refused(f"{h}: EXP-009 void/holdout hour")
        if h >= CUTOFF_HOUR:
            raise Refused(f"{h}: at or after {CUTOFF_HOUR}Z (post-upgrade data is not exploration)")


def replay_rows(tape_dir: str, work_dir: str, hours: Sequence[str]) -> tuple[Iterator[dict], dict[str, dict], dict[str, float]]:
    """Rows of the tape's V-range canonical pools for `hours`, in (slot, tx_index, event_index) order, shaped like sealed rows, plus the meta
    of those pools (pool -> {s0, v, mint}) and the frozen rule's per-pool sps (from the day's paths, non-causal, for parity)."""
    import pandas as pd  # lazy: the audit venv

    days = sorted({h[:10] for h in hours})
    meta: dict[str, dict] = {}
    sps_pool: dict[str, float] = {}
    for day in days:
        m = pd.read_parquet(f"{work_dir}/meta/{day}.parquet")
        m = m[(m.v >= V_LO) & (m.v <= V_HI)]
        cnt = m.mint.value_counts()
        m = m[m.mint.isin(cnt[cnt == 1].index)]
        for r in m.itertuples():
            meta[r.pool] = {"s0": int(r.s0), "v": int(r.v), "mint": r.mint}
        pa = pd.read_parquet(f"{work_dir}/paths/{day}.parquet", columns=["mint", "slot", "bt"])
        by_mint = {v["mint"]: k for k, v in meta.items()}
        pa = pa[pa.mint.isin(by_mint)]
        for mint, g in pa.groupby("mint", sort=False):
            s0 = meta[by_mint[mint]]["s0"]
            g = g[g.slot >= s0]
            sl, bt = g.slot.values, g.bt.values
            ok = bt > 0
            if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300:
                sps_pool[by_mint[mint]] = float((bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]))

    def gen() -> Iterator[dict]:
        for h in hours:
            t = pd.read_parquet(f"{tape_dir}/trades/{h}.parquet")
            t = t[(t.venue == "pumpswap") & t.pool.isin(meta)].sort_values(["slot", "tx_index", "event_index"], kind="stable")
            for r in t.itertuples():
                yield {
                    "venue": "pumpswap", "pool": r.pool, "slot": int(r.slot), "side": r.side, "trader": r.trader, "sol_lamports": int(r.sol_lamports),
                    "token_raw": int(r.token_raw), "quote_reserve": int(r.quote_reserve), "base_reserve": int(r.base_reserve),
                    "virtual_quote_reserves": meta[r.pool]["v"], "event_ts": int(r.block_time), "t_recv_ms": int(r.block_time) * 1000,
                    "signature": None, "mint": meta[r.pool]["mint"],
                    # union key only (no record carries them); the tape row's index is unique per hour, and tx_index / event_index can be NaN
                    "event_index": 0, "replay_sig": f"{h}:{r.Index}",
                    # boost_slice records only (never a decision, never the union key): the tape's block position, NaN -> None
                    "tx_index": _opt_int(r.tx_index), "tape_event_index": _opt_int(r.event_index),
                }

    return gen(), meta, sps_pool


def _opt_int(x: Any) -> int | None:
    """A tape position (tx_index / event_index) as an int, or None where the tape has none (NaN): boost_slice records only, never a decision."""
    try:
        return None if x is None or x != x else int(x)
    except (TypeError, ValueError):
        return None


def shuffle_within_slot(rows: Iterable[dict], seed: int) -> Iterator[dict]:
    """Measurement aid: permute rows that share a slot (arrival order is not tx order on the live feed), seeded."""
    import random

    rng = random.Random(seed)
    buf: list[dict] = []
    for r in rows:
        if buf and r["slot"] != buf[0]["slot"]:
            rng.shuffle(buf)
            yield from buf
            buf = []
        buf.append(r)
    rng.shuffle(buf)
    yield from buf


def replay(rows: Iterable[dict], meta: dict[str, dict], engine: Engine) -> None:
    """Feed rows; announce each pool one slot before its first print (the tape's meta is its CreatePool)."""
    announced: set[str] = set()
    tip = engine.tip if isinstance(engine.tip, ReplayTip) else None  # report-only union A/B: the tape is its own tip, `lag` slots behind
    for row in rows:
        pool = row["pool"]
        if pool not in announced and pool in meta and row["slot"] == meta[pool]["s0"]:  # only a pool's own first print is its announcement
            announced.add(pool)
            engine.on_create_pool(pool, meta[pool]["mint"], WSOL_MINT, row["slot"] - 1, row["t_recv_ms"])
        engine.wall = lambda ms=row["t_recv_ms"]: ms  # records carry tape time, not the wall clock
        engine.on_trade(row)
        if tip is not None:
            tip.push(row)
            tip.release(engine)
    if tip is not None:
        tip.release(engine, final=True)
    engine.close_all("replay_end")


def compare_frozen(records: list[dict], meta: dict[str, dict], frozen_path: str, s0_hour_prefix: str, tape_hours: Sequence[str], q_star_sol: float = Q_STAR_SOL) -> dict:
    """Trigger lists vs the frozen rule's (leg p, exit 'end', stake 0.1) for pools whose s0 block time (UTC, 'YYYY-MM-DDTHH') starts with
    `s0_hour_prefix` (an hour, or a day). The tape hours must cover s0 + 400 s of every such pool."""
    import pandas as pd

    f = pd.read_parquet(frozen_path)
    f = f[(f.D == q_star_sol) & (f.leg == "p") & (f.H == "end") & (f.stake == "01")]
    pool_of = {v["mint"]: k for k, v in meta.items()}
    pools = {r["pool"]: r for r in records if r["type"] == "pool" and r["s0_block_time"] is not None
             and datetime.fromtimestamp(r["s0_block_time"], tz=timezone.utc).strftime("%Y-%m-%dT%H").startswith(s0_hour_prefix)}
    mine = {r["pool"]: r for r in records if r["type"] == "trigger" and r["variant"] == "pv" and r["pool"] in pools}
    out_by_pool = {r["pool"]: r for r in records if r["type"] == "outcome" and r["variant"] == "pv" and r["pool"] in pools}
    frozen = {pool_of[m]: row for m, row in zip(f.mint, f.itertuples()) if m in pool_of and pool_of[m] in pools}
    both, only_mine, only_frozen = sorted(set(mine) & set(frozen)), sorted(set(mine) - set(frozen)), sorted(set(frozen) - set(mine))
    dt = [abs(mine[p]["t_since_s0_s"] - frozen[p].trig_t) for p in both]
    dq = [abs(mine[p]["q_pv_post_sol"] - frozen[p].q_trig) for p in both]
    dg = []
    for p in both:
        o = out_by_pool.get(p)
        if o and o["legs"]["primary"].get("net"):
            dg.append(abs(o["legs"]["primary"]["net"]["0.1"]["gross"] - frozen[p].gross))
    return {"pools_s0_in_hour": len(pools), "mine": len(mine), "frozen": len(frozen), "both": len(both), "only_mine": only_mine, "only_frozen": only_frozen,
            "max_abs_dt_s": max(dt) if dt else None, "max_abs_dq_sol": max(dq) if dq else None, "median_abs_dq_sol": sorted(dq)[len(dq) // 2] if dq else None,
            "gross_compared": len(dg), "max_abs_dgross": max(dg) if dg else None, "median_abs_dgross": sorted(dg)[len(dg) // 2] if dg else None,
            "tape_hours": list(tape_hours)}


def run_replay(args: argparse.Namespace) -> int:
    hours = [h.strip() for h in args.replay_hours.split(",") if h.strip()]
    try:
        refuse_replay(args.replay_tape, hours, check_q_star(getattr(args, "q_star_sol", Q_STAR_SOL)))
        refuse_replay(args.work_dir, [])
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    rows, meta, sps_pool = replay_rows(args.replay_tape, args.work_dir, hours)
    if args.shuffle_slot_seed is not None:
        rows = shuffle_within_slot(rows, args.shuffle_slot_seed)
    records: list[dict] = []
    sps_fn = (lambda p: sps_pool.get(p.pool)) if args.sps == "pool" else None
    engine = Engine(records.append, boost_mode=args.boost_mode, sps_fn=sps_fn, classifier=PreEventClassifier(),  # every replayable hour is pre-event
                    bx10_enabled=bx10_flag(getattr(args, "bx10_enable", None)), q_star_sol=getattr(args, "q_star_sol", Q_STAR_SOL))
    if getattr(args, "replay_tip_lag_slots", None) is not None:
        engine.tip = ReplayTip(args.replay_tip_lag_slots)
    replay(rows, meta, engine)
    if args.out_dir:
        sink = JsonlSink(args.out_dir, prefix="h5-replay")
        for r in records:
            sink.write(r)
        sink.close()
    summary: dict[str, Any] = {"rule": RULE_ID, "hours": hours, "sps": args.sps, "boost_mode": args.boost_mode, "counters": dict(engine.counters),
                               "triggers_pv": sum(1 for r in records if r["type"] == "trigger" and r["variant"] == "pv"),
                               "triggers_fv": sum(1 for r in records if r["type"] == "trigger" and r["variant"] == "fv"),
                               "pools": sum(1 for r in records if r["type"] == "pool")}
    if engine.tip is not None:  # counts only: gate-view vs union-view cross-tab of settled breaks (0 vs >= 1)
        xt: collections.Counter = collections.Counter()
        for r in records:
            if r["type"] in ("trigger", "pool") and UNION_FIELD in r:
                u = r[UNION_FIELD]
                ta = r.get(TIPAUTH_FIELD)  # report-only tipauth bucket, counts only
                xt[f'{r["type"]}:ws{"0" if not r["base_breaks_unresolved_settled"] else "1+"}:union{"null" if u is None else ("0" if u == 0 else "1+")}'
                   f':tipauth{"null" if ta is None else ("0" if ta == 0 else "1+")}'] += 1
        summary["tip_union"] = {**engine.tip.stats(), "crosstab": dict(sorted(xt.items()))}
    if args.compare_frozen:
        summary["compare"] = compare_frozen(records, meta, args.compare_frozen, args.compare_s0 or hours[0], hours, check_q_star(getattr(args, "q_star_sol", Q_STAR_SOL)))
    print(json.dumps(clean(summary), indent=1, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="H5-BOOSTFLOOR v1 live shadow detector (paper only; no keys, no transactions)")
    ap.add_argument("--out-dir", default=None, help=f"live: output dir (default {DEFAULT_OUT_DIR}); replay: written only when given")
    ap.add_argument("--ws-url", action="append", default=None, help="public RPC websocket; repeatable (socket i uses url i mod n)")
    ap.add_argument("--sockets", type=int, default=2, help="redundant logsSubscribe sockets merged by signature")
    ap.add_argument("--commitment", default="confirmed", choices=("processed", "confirmed", "finalized"))
    ap.add_argument("--syn-sockets", type=int, default=1, help="logsSubscribe sockets on the pump.fun program for the synthetic-migration class (side feed; the RPC fallback covers a miss)")
    ap.add_argument("--rpc-url", default=DEFAULT_RPC, help="public RPC for the synthetic-class fallback (default %(default)s); Helius URLs are refused")
    ap.add_argument("--boost-mode", default="auto", choices=("auto", "pda", "behavioural"))
    ap.add_argument("--q-star-sol", type=_q_star_arg, default=Q_STAR_SOL, metavar="SOL",
                    help="trigger level Q* in SOL: 40 (default, the frozen rule) or 50 (H5-BOOSTFLOOR-Q50, its own amendment and frozen sha; live needs --fresh-forward ARENA, bx10 off)")
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (smoke test)")
    ap.add_argument("--h5-look2-observed", default=None, metavar="AMENDMENT_REF",
                    help=f"live: declare EXP-024 Amendment 2's observation of Look 2 outcomes for pools with s0 in [2026-10-16T00Z, 2026-11-06T00Z); the value must be "
                         f"exactly {H5_LOOK2_AMENDMENT_REF}. Without it, pools with s0 >= 2026-10-16T00Z get trigger records only (no outcome, strip, legs, ladder or "
                         "min_q); pools with s0 >= 2026-11-06T00Z are withheld with it too. CAP-PICK picks stay sealed either way.")
    ap.add_argument("--bx10-enable", default=None, metavar="EXP-026",
                    help=f"write the report-only bx10 exit variant (outcome_bx10 records). Off by default; only the exact value {BX10_ENABLE_REF} turns it on, "
                         "any other value counts as off. The shell wrapper passes H5_BX10_ENABLE through when it is set.")
    ap.add_argument("--fresh-forward", default=None, metavar="ARENA",
                    help=f"live: the forward-paper arena mode (hunt20). Off by default; only the exact value {FRESH_FORWARD_REF} turns it on, any other value counts as off. "
                         f"On: --out-dir is required and must resolve under {ARENA_ROOT}/{ARENA_OUT_PREFIX}* (/srv/mal-h5-shadow and $HOME/data/h5-shadow* are refused; this mode never "
                         "feeds the live executor); neither the H5 Look-2 seal nor the CAP-PICK oracle seal is applied; pools with s0 before the process start are ignored, every "
                         "later pool gets its trigger, outcome, outcome_bx10 and pool records; the tip union is off; the start record carries fresh_forward, code_sha and start_ms.")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--tip-dir", default=None, help="live: REPORT-ONLY tip union (base_breaks_unresolved_settled_union, base_union, "
                    "base_unmatched_*) from this tip-follower dir, e.g. " + TIP_DIR_DEFAULT + "; off when not given (records byte-identical)")
    ap.add_argument("--replay-tip-lag-slots", type=int, default=None, help="replay: also run the report-only union with the tape as its own tip, N slots behind")
    ap.add_argument("--replay-tape", default=None, help="run the engine over this exploration tape dir instead of the live feed")
    ap.add_argument("--replay-hours", default=None, help="comma-separated tape hours, e.g. 2026-09-20T12,2026-09-20T13")
    ap.add_argument("--work-dir", default=WORK_DIR)
    ap.add_argument("--shuffle-slot-seed", type=int, default=None, help="replay: permute prints within a slot (seeded) to measure the reorder-proof chain check")
    ap.add_argument("--sps", default="pool", choices=("pool", "rolling"), help="replay: frozen per-pool sps (parity) or the live rolling slot clock")
    ap.add_argument("--compare-frozen", default=None, help="parquet of the frozen rule's trades (boostdip_frozen_conf.parquet)")
    ap.add_argument("--compare-s0", default=None, help="compare pools whose first print is in this UTC hour or day prefix (default: the first replayed hour)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if fresh_forward_flag(args.fresh_forward):  # the arena mode: its own checks, before anything else; flag absent = none of this runs
        if args.replay_tape:
            ap.error("--fresh-forward applies to the live feed only")
        if args.h5_look2_observed is not None:
            ap.error("--h5-look2-observed does not apply with --fresh-forward (neither seal is applied)")
        if args.tip_dir:
            ap.error("--tip-dir does not apply with --fresh-forward (the tip union is off)")
        try:
            check_fresh_out_dir(args.out_dir or "")
        except ValueError as e:
            ap.error(str(e))
    if args.q_star_sol != Q_STAR_SOL:  # QP ruling 2026-10-22 s2 (the live refusals are repeated in run_live)
        if args.h5_look2_observed is not None:
            ap.error("--h5-look2-observed does not apply with --q-star-sol != 40 (EXP-024 Am.2 does not cover it)")
        if args.bx10_enable is not None:
            ap.error("--bx10-enable does not apply with --q-star-sol != 40")
        if not args.replay_tape and not fresh_forward_flag(args.fresh_forward):
            ap.error("--q-star-sol != 40 requires --fresh-forward ARENA (replay on exploration tape is the only other mode)")
    if args.out_dir:
        try:
            check_out_dir(args.out_dir)
        except ValueError as e:
            ap.error(str(e))
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.sockets < 1:
        ap.error("--sockets must be >= 1")
    try:
        check_look2_ref(args.h5_look2_observed)
    except ValueError as e:
        ap.error(str(e))
    if args.h5_look2_observed is not None and args.replay_tape:
        ap.error("--h5-look2-observed applies to the live feed only (replay reads exploration tape before the seal)")
    if args.replay_tape:
        if not args.replay_hours:
            ap.error("--replay-tape needs --replay-hours")
        return run_replay(args)
    return asyncio.run(run_live(args))


if __name__ == "__main__":
    sys.exit(main())
