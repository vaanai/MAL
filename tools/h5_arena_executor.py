"""H5 arena executor: the H5 executor pinned to ONE registered arena book (QP-SCOPE-1022-h5exec, 2026-10-22). PAPER / DRY ONLY in this build.

This is a subclass of tools.h5_executor.H5Executor. tools/h5_executor.py is NOT edited: its git blob 03995837317f is pinned by
tools/arena_h5_score.py (EXECUTOR_BLOB, DEC-031 Am.1), and a test here asserts it. Everything below the base class is reused as is (the send path,
the sell ladder, caps, write-ahead, STOP / HALT, counters). What this module changes, each item numbered as in the scope:

 1  BOOKS: one compiled-in table, bot_id -> (frozen sha, rule, exit pin, Q* in lamports, clock, seal end). Never read from bots.json or config.
 2  Seal: the EXP-022 CAP-PICK guard is dropped (DEC-031 s3.5: moot). The module REFUSES TO START if pick_file / pick_replay_files is set.
    It refuses any trigger with h5_look2_sealed, or with s0 outside [clock, 2026-11-06T00Z) (counted, alerted). The parser's `suppressed` refusal stays.
 3  Feed: the book's own arena out dir only. The start record(s) must carry code_sha == the book's frozen sha, fresh_forward "ARENA", dirty false,
    tip_union off, and the book's Q* record (absent = 40.0 for a2; 50.0 + rule H5-BOOSTFLOOR-Q50 v1 + its sha256 for Q50). Else: feed_break, latched.
    No valid start record seen yet: no buy (feed_unverified).
 4  Q*: the book's compiled constant replaces the hard-coded 40 SOL (45 refused on a2, accepted on Q50, 50.1 refused on Q50).
 5  Exit pin: v1 or v2 per book, compiled in. The v2 book runs v2 start checks of its own (rule source sha256 + the canary DEC's owner line), not EXP-026.
 6  v2 feed: a v2 book refuses buys until it has taken a boost_slice record in the executor's format (v2_feed_absent). Else it would exit at 330 s,
    which is not the book's exit.
 7  Limits: end_ms / max_days come from config, clamped to the book's seal end. TIERS are the base ones; T0 is the default stake.
 9  Priority pin: Book.priority (profile default = 55,000 / 55,000 / 150,000, the registered books; p155 = 155,000 on buy, sell and escalation, for NEW books
    per DEC-031 Am.8). Asserted at start (cfg["priority_pin"] must equal it; a p155 book requires it) and applied through the `h5` property.
 8  Fidelity log: each decision row carries bot_id, frozen_sha, the trigger key (mint, pool, trigger_slot), decision_ms. Sells add send/landed slots and
    late_slots. No exit quote, no P&L is ever written by a dry run (outcome-blind).
14  Soft halts, latched (names in ARENA_HALTS): see FidelityMonitor.
FEED_NOTE_MERGED is True since the EXP-024 "Note under Amendment 10" (PR #851, scope item 17) declared the real-time observation of this executor's dry and
live outcomes, for the bot_ids in FEED_NOTE_BOOKS only (h5v1q50-boostfloor-a1; the a2 books ENDED, DEC-031 Am.10 item 4). Any other book reads replay
inputs only ("replay_only": true) and is refused a live start. The md5 decision-equivalence replay is re-run at this flip's merge commit (QP-RULING-1024 (b)).

Live entry (LIVE_BUILT True, QP-RULING-1023-wiring ruling 7 item 11): `main --live` with config "mode": "live". Every keyless check runs BEFORE the key is read,
in this order, and any refusal is exit 2 (the unit does not restart on 2): the base start_refusal (no STOP/HALT/LIVE_OK/TIER path override, the shared
/etc/mal-h5/LIVE_OK, EXP-024 Part 1 in the tree, end_ms set); live_entry_refusal (the C0 guard tripwire c0_guard_problem, FEED_NOTE_MERGED True, no
replay_only, the arena's own root-owned /etc/mal-h5-arena/LIVE_OK naming exactly this bot_id, a configured A3 record, the v2 rule source for a v2 book);
arena_start_refusal (priority pin, funded range, sell-builder pin, canary DEC owner line for this bot_id, exit-gap / twins / tie-out inputs, TIER exactly T1);
the RPC env check; then harden_process and the key from the systemd credential only. The constructor re-runs arena_start_refusal (which includes
live_entry_refusal) and the FINALIZED funded-balance check. /etc/mal-h5-arena/LIVE_OK is re-read before every live buy. No check is skipped or loosened
by a live start. The flip opens no send by itself: a live start still needs both Helm-owned LIVE_OK files, TIER T1, the canary DEC line and the key drop-in.

Not proven offline (see the PR body): decision equivalence on the real 2026-09-20 day (tools/h5_arena_exec_replay.py is the harness; the run is a host job),
the live landing/exit gaps, the A3 pins_changed link, the Token-2022 screen on real mints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import base64
from datetime import datetime, timezone

from tools import h5_executor as h
from tools import h5_arena_ladder as ld
from tools import mint_account_fetch as mf
from tools.arena_close_blocked import classify_record
from tools.h5_arena_gates import EXIT_GAPS_NAME, HARD_ARENA_HALTS, TWIN_PIN_MISMATCH, ExitGapWriter, LiveGates, ParityRecorder, v_band_refusal
from tools.h5_arena_gates import twin_run_stamp as gt_twin_run_stamp

# --- the books (item 1). Values copied from the merged records: ARTIFACTS/lab/arena-records-2026-10-22.md (a2), qp-ruling-1022-q50.md and
# EXP-024-RULE-Q50.md (Q50). Nothing here is read from config or bots.json.
A2_SHA = "1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde"
Q50_SHA = "a5f413704eb120c8f529101a6186e3a95de7f83e"
A2_RULE = "H5-BOOSTFLOOR v1"
Q50_RULE = "H5-BOOSTFLOOR-Q50 v1"
Q50_RULE_SHA256 = "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"
A2_CLOCK_MS = 1792713600000  # 2026-10-23T00:00:00Z
Q50_CLOCK_MS = 1792886400000  # 2026-10-25T00:00:00Z
SEAL_END_MS = 1793923200000  # 2026-11-06T00:00:00Z (the end seal of EXP-024 Am.9 / Am.10)
S0_MARGIN_V2_MS = 60_000  # the scorer wrappers drop v2 / Q50 triggers whose s0 is < clock + 60 s (tools/arena_h5_score_a2.py S0_MARGIN_MS); v1-a2 has none
FRESH_FORWARD = "ARENA"
V2_RULE_SHA256 = "5368cc7b757a2f7a6cb064c71193b4e0cd740583f7348a60cb9a864401c190e0"  # iter-r2 C10 FREEZE.md
V2_RULE_SOURCE = Path("/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/FREEZE.md")  # (a test may point it elsewhere; the sha is the pin)
CANARY_DEC_GLOB = "DEC-*canary*.md"  # canary records (copies of the DEC-032 template) live in DEC/. The template itself never counts.
CANARY_OWNER_RE = re.compile(r"^[ \t]*OWNER_CANARY_CONFIRMED:[ \t]*bot_id=(\S+)[ \t]*$", re.M)  # the owner's line names the book: "OWNER_CANARY_CONFIRMED: bot_id=<bot_id>"
FEED_NOTE_MERGED = True  # scope item 17: EXP-024 "Note under Amendment 10" (#851) declares this executor's observation. False = replay_only for every book
FEED_NOTE_REF = "### Note under Amendment 10 (written 2026-10-24T01:27Z"  # the note's heading in EXP/EXP-024-h5-boostfloor-part1-prereg.md (a test checks it is in the tree)
FEED_NOTE_BOOKS = frozenset({"h5v1q50-boostfloor-a1"})  # the bot_ids the note covers; every other book stays replay_only (QP-SCOPE item 17: "per bot_id")
LIVE_BUILT = True  # the live entry point is main --live (QP-RULING-1023-wiring ruling 7 item 11); every live start passes live_entry_refusal first
ARENA_LIVE_OK_PATH = Path("/etc/mal-h5-arena/LIVE_OK")  # the arena canary's OWN gate: root:root 0644 (base root_file_problem), content exactly the bot_id. Helm creates it.
DEC032_C0 = {  # DEC-032 s3 C0 column, verbatim. C0_TIER may only be at or below these; c0_guard_problem refuses a live start otherwise.
    "stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000,
}
C0_TIER = {  # DEC-032 s3, the C0 canary table, PINNED in code per canary book. The shared /etc/mal-h5/TIER (T0/T1/T2) and the config may only LOWER these.
    "stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000,
}
FUNDED_BALANCE_TOLERANCE_LAMPORTS = 10_000_000  # 0.01 SOL: a live start needs the FINALIZED balance within this of funded_lamports (minus realized loss on a restart)
A3_MAX_AGE_MS = 36 * 3_600_000  # the monitor writes daily; a newest record older than 36 h is stale = fail closed
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = 350_000_000, 1_000_000_000  # DEC-032 s3: "Wallet needed: funded 0.35 to 1.00 SOL"
FLOOR_BELOW_FUNDED_LAMPORTS = 250_000_000  # DEC-032 s3: floor = max(0.05 SOL, funded - 0.25 SOL)
A3_RECORD_DEFAULT = "/data/mal/structure-monitor/daily.jsonl"  # tools/pump_structure_monitor.py DEFAULT_OUT: one JSON record per line, the newest is the state
A3_POLL_MS = 60_000
A3_GUARD_FLAGS = ("synthetic_share_high",)  # DEC-032 Am.3: decided by the DEC-031 Am.10 pooled guard (a3-applied.jsonl), never by the raw record
A3_APPLIED_NAME = "a3-applied.jsonl"  # scripts/ops/a3-daily.sh: $A3_OUT/a3-applied.jsonl, the same directory as the daily record
A3_GUARD_VERDICTS = ("NONE", "PASS", "HALT", "FAIL", "ERROR")  # == tools.a3_halt.VERDICTS (pinned by a test)
A3_GUARD_CLEAR = ("NONE", "PASS")  # PASS never clears a raw pins_changed: the raw check runs first and latches on it
A3_GUARD_LAG_MS = 2 * 3_600_000  # declared choice, not measured: a pending guard refuses buys; past this it latches
A3_APPLIED_MAX_BYTES = 16 * 1024 * 1024
A3_FUTURE_SKEW_MS = 300_000  # a run_utc more than 5 min ahead of the executor's clock is not a fresh record, it is a wrong one: latch
ARENA_TIER_PATH = Path("/etc/mal-h5-arena/TIER")  # the arena canary's OWN tier file (root:root 0644 like the base TIER); the shared /etc/mal-h5/TIER is ignored for arena books
ARENA_LIVE_TIER = "T1"  # a live start needs this file to read exactly T1 (the C0 stake 0.05 SOL needs T1's stake ceiling)
GAP_HOLD_MS = 600_000  # DEC-032 s5 item 2: after a feed gap, buys restart only once the feed has been gap-free for 10 minutes
SELL_RESERVE_SENDS = 10  # the worst case of one exit: the ladder's sends (escalated resends included), each at the book's top priority
SELL_RESERVE_FLOOR_LAMPORTS = 1_600_000  # 10 sends x (155,000 + 5,000): the p155 worst case (QP 10-23 gap d); never below it
PAPER_FAIL_RATE = 0.15  # the flat fail model of the gate; landing-fail excess is measured against it


@dataclass(frozen=True)
class Priority:
    """The book's compiled priority-fee pin (lamports of priority per send; the 5,000 base fee is on top). Item 9 (L1a, 2026-10-23)."""
    profile: str
    buy: int
    sell: int
    escalated: int

    def __post_init__(self) -> None:
        if self.profile == "p155" and (self.buy, self.sell, self.escalated) != (AM8_PRIORITY,) * 3:
            raise ValueError("profile p155 is 155,000 on the buy, the sell and the escalation (DEC-031 Am.8 item 2: every retry at or below 155,000)")

    def as_dict(self) -> dict[str, Any]:
        return {"profile": self.profile, "buy": self.buy, "sell": self.sell, "escalated": self.escalated}


# --- the twin writer pins (QP 10-23 blocker 2 / P5, ruling 4). The H5 books register stack DEC-031-rev2 (tools/arena_h5_cost.py), not the arena host's StackConfig.
# The config pin of a book = sha256 of canonical JSON {adapter, arena_h5_cost_sha256, stack_id} (tools/arena_twin_writer.h5_config_hash), with arena_h5_cost_sha256 =
# `git show <registration sha>:tools/arena_h5_cost.py | sha256sum` (eb8b32d for the a2 books, a5f4137 for Q50: both give the same file). A test recomputes them.
# TWIN_WRITER_SHA256 = sha256 of tools/arena_twin_writer.py. The twin's rent 0 is a declared twin-only deviation (reported beside as pnl_rent0_lamports).
TWIN_STACK_ID = "DEC-031-rev2"
TWIN_ADAPTER = "h5_twin_adapter_v1"
H5_COST_SHA256 = {"eb8b32d": "b20d51ceb939822ce813ca48b14348e61873277fef54a3d71af660e37fe03328", "a5f4137": "b20d51ceb939822ce813ca48b14348e61873277fef54a3d71af660e37fe03328"}
TWIN_WRITER_SHA256 = "1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb"


def h5_twin_config_hash(cost_sha256: str) -> str:
    return hashlib.sha256(json.dumps({"adapter": TWIN_ADAPTER, "arena_h5_cost_sha256": cost_sha256, "stack_id": TWIN_STACK_ID}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


TWIN_REG_SHA = {"h5v1-boostfloor-a2": "eb8b32d", "h5v2-boostclock-a2": "eb8b32d", "h5v1q50-boostfloor-a1": "a5f4137"}


def twin_pins(book: "Book") -> tuple[str, str]:
    """(config_hash, code_sha256) the book's twin run record must carry."""
    reg = TWIN_REG_SHA.get(book.bot_id)
    if reg is None:  # a book with no registration sha has no pin a real writer could carry: its pin never matches (test books only)
        return (f"unregistered:{book.bot_id}", TWIN_WRITER_SHA256)
    return (h5_twin_config_hash(H5_COST_SHA256[reg]), TWIN_WRITER_SHA256)


AM8_PRIORITY = 155_000  # DEC-031 Amendment 8 item 2: buy AND sell pinned at exactly 155,000, every escalation at or below it (160,000 per send with the base fee)
PRIORITY_DEFAULT = Priority("default", h.H5_DEFAULT["buy_priority_lamports"], h.H5_DEFAULT["sell_priority_lamports"], h.H5_DEFAULT["escalated_priority_lamports"])
PRIORITY_P155 = Priority("p155", AM8_PRIORITY, AM8_PRIORITY, AM8_PRIORITY)
assert (PRIORITY_DEFAULT.buy, PRIORITY_DEFAULT.sell, PRIORITY_DEFAULT.escalated) == (55_000, 55_000, 150_000)  # the registered books' values, h5_executor.py:267-268


@dataclass(frozen=True)
class Book:
    bot_id: str
    frozen_sha: str
    rule: str  # the start record's `rule`
    exit_pin: str  # "v1" | "v2"
    q_star_lamports: int
    clock_start_ms: int
    end_ms_seal: int
    s0_margin_ms: int
    q_star_in_record: float | None  # None: the start record carries no q_star_sol (the default 40.0 path is byte-identical)
    rule_sha256: str | None
    priority: Priority = PRIORITY_DEFAULT  # registered books keep h5_executor's 55,000 / 55,000 / 150,000; only a NEW book (registered after Am.8) may be p155

    @property
    def q_star_sol(self) -> float:
        return self.q_star_lamports / 1e9


BOOKS: dict[str, Book] = {b.bot_id: b for b in (
    Book("h5v1-boostfloor-a2", A2_SHA, A2_RULE, "v1", 40 * 10**9, A2_CLOCK_MS, SEAL_END_MS, 0, None, None),
    Book("h5v2-boostclock-a2", A2_SHA, A2_RULE, "v2", 40 * 10**9, A2_CLOCK_MS, SEAL_END_MS, S0_MARGIN_V2_MS, None, None),
    Book("h5v1q50-boostfloor-a1", Q50_SHA, Q50_RULE, "v1", 50 * 10**9, Q50_CLOCK_MS, SEAL_END_MS, S0_MARGIN_V2_MS, 50.0, Q50_RULE_SHA256),
)}

# --- the sell builder pin (DEC-032 section 4, FILL:builder_path). The sell + close of the base ATA in ONE transaction is built by the unmodified base class:
# tools/h5_executor.py H5Executor._arm_sell (tx.sell_instructions: budget, WSOL ATA, swap, WSOL close; then pl.close_token_account(base_ata, user, base_tp)
# with base_tp = the pool's base_token_program = the mint's own token program). Pinned by sha256; a test recomputes them and decodes a built sell.
SELL_BUILDER_PIN = {
    "path": "tools/h5_executor.py", "symbol": "H5Executor._arm_sell",
    "file_sha256": "bc4c957c11caa536e107fc9b944fc33d13397039fcaf74cba45c8c2c603d5c17",
    "symbol_sha256": "3c4bf3f8a748afcbc2a991a9126988f023acf9692c20d8009a69f3d37764effb",
    "close_token_account_sha256": "93119b2ea159bcb9defcb5e7d2424a5635df432f7ffc9b18d07355d2f364895a",  # tools/probe_live.py close_token_account
    "sell_instructions_sha256": "ad40960ef8cc18260f0682cf83989be8ab36f7366354cada1a84367194757eeb",  # tools/pumpswap_tx.py sell_instructions
}

def sell_pin_problem(pin: dict[str, str] | None = None) -> str | None:
    """Recompute the four sha256 values of SELL_BUILDER_PIN from the code on disk. None when all match; else the first item that differs. A live start
    refuses on any mismatch (QP 10-23: the pin was test-checked only)."""
    import inspect
    from tools import probe_live as pl
    from tools import pumpswap_tx as ptx
    pin = pin or SELL_BUILDER_PIN
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    got = {"file_sha256": lambda: sha(Path(h.__file__).read_bytes()),
           "symbol_sha256": lambda: sha(inspect.getsource(h.H5Executor._arm_sell).encode()),
           "close_token_account_sha256": lambda: sha(inspect.getsource(pl.close_token_account).encode()),
           "sell_instructions_sha256": lambda: sha(inspect.getsource(ptx.sell_instructions).encode())}
    for k, fn in got.items():
        try:
            if fn() != pin.get(k):
                return f"sell_builder_pin_mismatch:{k}"
        except (OSError, TypeError):
            return f"sell_builder_pin_unreadable:{k}"
    return None


# --- soft halts (item 14), latched by name
H_FEED_BREAK = "feed_break"
H_ENTRY_GAP = "entry_gap_gt_200bps"
H_LANDING = "landing_fail_excess_gt_10pp"
H_EXIT_GAP = "exit_gap_gt_200bps"
H_LATE_SEND = "sell_send_late_x2"
H_STUCK2 = "two_stuck_sells"
H_STRAND = "close_blocked_strand"
H_TWIN_PIN = "twin_pin_mismatch"  # QP 10-23 blocker 2: the twin writer's run record carries a config_hash or code_sha256 other than the pinned one
H_A3 = "a3_pins_changed"
H_LADDER = "ladder_error"  # DEC-032 Am.2 wiring: a crash in the ladder step latches this (no buys) and never skips an exit (QP #862 condition 1)
SIGNAL_AGE_MAX_MS = 5_000  # DEC-032 s3 "Buy guard": signal age at most 5 s (the base executor's own PRESEND_MAX_TRIGGER_AGE_MS is 3.5 s; the stricter of the two applies)
H_BUY_UNREADABLE = "landed_buy_unreadable"  # QP 10-23 W2: a landed buy whose token or spend amount cannot be read (an open, flagged trip exists)
ARENA_HALTS = frozenset({H_BUY_UNREADABLE, H_FEED_BREAK, H_ENTRY_GAP, H_LANDING, H_EXIT_GAP, H_LATE_SEND, H_STUCK2, H_STRAND, H_A3, H_TWIN_PIN, H_LADDER})
ENTRY_GAP_WINDOW = 20  # DEC-032 s5 P1: the first 10 eligible fills, then ROLLING over the last 20
LANDING_WINDOW_N = 30  # DEC-032 s5 P2: rolling 30 buy attempts, judged once 30 exist
ENTRY_GAP_BPS, ENTRY_GAP_MIN_N = 200.0, 10
EXIT_GAP_BPS, EXIT_GAP_MIN_N = 200.0, 10
LANDING_EXCESS_PP, LANDING_MIN_N = 10.0, 30
LATE_SEND_SLOTS, LATE_SEND_TRIPS = 3, 2
MARK_EXIT_GUARD_MS = 5_000  # no mark read from 5 s before a position's sell send onward (the exit path stays free of mark RPCs)
MARKS_PER_TICK = 1  # at most one position marked (2 RPC calls) per exit tick
LAST_MARK_LEAD_MS = 1_000  # the last mark is taken in the 1 s before a position's no-lookup window (it then ages normally: item 3 (a), 10 s)
LAST_MARK_MIN_GAP_MS = 500  # ... at most twice a second
REFUSAL_LOOK2 = "h5_look2_sealed"
REFUSAL_S0_WINDOW = "s0_outside_book_window"
REFUSAL_DECISION_WINDOW = "decision_outside_book_window"
REFUSAL_UNVERIFIED = "feed_unverified"
REFUSAL_V2_FEED = "v2_feed_absent"
REFUSAL_Q = "q_above_book_q_star"
WINDOW_REFUSALS = (REFUSAL_LOOK2, REFUSAL_S0_WINDOW, REFUSAL_DECISION_WINDOW)


def executor_blob() -> str:
    """git blob id (full) of tools/h5_executor.py as it is on disk: sha1("blob <n>\\0" + bytes)."""
    data = Path(h.__file__).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def start_record_problem(book: Book, rec: dict[str, Any]) -> str | None:
    """Why a start record of the feed is not this book's (None: it is). Item 3. Fail closed on every key."""
    sha = rec.get("code_sha")
    if not sha:
        return "null_code_sha"
    if sha != book.frozen_sha:
        return "frozen_sha_mismatch"
    if rec.get("fresh_forward") != FRESH_FORWARD:
        return "not_fresh_forward"
    if rec.get("dirty") is not False:
        return "dirty_or_unknown"
    if rec.get("tip_union") != "off":
        return "tip_union_on"
    if rec.get("rule") != book.rule:
        return "rule_mismatch"
    q = rec.get("q_star_sol")
    if book.q_star_in_record is None:
        if "q_star_sol" in rec and not (isinstance(q, float) and q == 40.0):
            return "q_star_mismatch"
    else:
        if isinstance(q, bool) or not isinstance(q, (int, float)) or float(q) != book.q_star_in_record:
            return "q_star_mismatch"
    if book.rule_sha256 is not None and rec.get("rule_sha256") != book.rule_sha256:
        return "rule_sha256_mismatch"
    if not isinstance(rec.get("start_ms"), int) or isinstance(rec.get("start_ms"), bool):
        return "no_start_ms"
    return None


def arena_start_refusal(book: Book, cfg: dict[str, Any], root: Path, live: bool) -> str | None:
    """Static start conditions (keyless). Item 2 pick files, item 5 v2 checks, item 17 feed note. Live also needs the canary DEC's owner line."""
    why = priority_refusal(book, cfg)
    if why:
        return why
    if cfg.get("pick_file") or cfg.get("pick_replay_files"):
        return "pick_file_set"  # the CAP-PICK oracle no longer exists (DEC-031 s3.5): a configured one is a wrong config, never silently ignored
    if live and not LIVE_BUILT:
        return "live_not_built"
    if live:
        why = live_entry_refusal(book, cfg)  # first for a live start: it refuses an unmerged feed note and any replay_only key itself
        if why:
            return why
    if cfg.get("replay_only") is not True:
        if not FEED_NOTE_MERGED:
            return "feed_note_unmerged_replay_only"
        if book.bot_id not in FEED_NOTE_BOOKS:
            return "feed_note_not_covering_book_replay_only"  # an ended or undeclared book never reads the live feed
    why = funded_refusal(cfg, live)
    if why:
        return why
    if live:
        why = sell_pin_problem()
        if why:
            return why
        why = h.live_ok_valid()
        if why:
            return why
        if cfg.get("end_ms") is None:
            return "end_ms_missing"
        why = canary_record_refusal(book.bot_id, root)
        if why:
            return why
        if not cfg.get("exit_gap_file"):
            return "exit_gap_file_missing"  # the exit-gap feed (parity P6) is mandatory live: unset would mean no check at all
        if cfg.get("state_dir") and str(cfg["exit_gap_file"]) != str(Path(cfg["state_dir"]) / "arena-parity" / EXIT_GAPS_NAME):
            return "exit_gap_file_not_own_writer"  # live reads only the file its own ExitGapWriter fills (an empty file elsewhere would never fire)
        for key in ("parity_twins_file", "tieout_dir"):  # the parity tool's twins input and the tie-out results directory (DEC-032 s5, s6)
            if not cfg.get(key):
                return f"{key}_missing"
        return arena_tier_refusal()
    if book.exit_pin == h.EXIT_MODE_V2:
        return v2_start_refusal()
    return None


def arena_live_ok_refusal(bot_id: str, path: Path | None = None) -> str | None:
    """The arena's own LIVE_OK: the base file checks (regular root:root 0644 file, no symlink, root-owned parent not group/other writable) and its content
    is exactly this bot_id (one line). Missing, unsafe, unreadable or naming another book is a refusal; the executor never creates it."""
    p = path or ARENA_LIVE_OK_PATH
    problem = h.root_file_problem(p)
    if problem:
        return f"arena_live_ok_{problem}"
    try:
        text = p.read_text()
    except (OSError, UnicodeDecodeError):
        return "arena_live_ok_unreadable"
    return None if text.strip() == bot_id and len(text.strip().splitlines()) == 1 else "arena_live_ok_other_book"


def c0_guard_problem() -> str | None:
    """A tripwire run at every live start: the compiled C0 guards are still at least as strict as DEC-032 (s3 limits, s3 fail-closed 60 s, s3 signal age
    5 s, s5 10-minute gap hold and P1/P2/exit thresholds, the T1 tier file, the p155 sell reserve) and every guard method is still the arena override.
    None when all hold; else the first item that does not. It never loosens anything: it only refuses."""
    from tools import h5_arena_gates as g
    why = ld.ladder_guard_problem()  # DEC-032 Am.2 item 9.1: a live start refuses if any ladder constant is looser than item 3
    if why:
        return why
    for k, cap in DEC032_C0.items():
        v = C0_TIER.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= cap:
            return f"c0_guard:{k}"
    checks = (
        ("gap_hold", GAP_HOLD_MS >= 600_000), ("signal_age", 0 < SIGNAL_AGE_MAX_MS <= 5_000), ("parity_interval", 0 < g.PARITY_INTERVAL_S_MAX <= 60),
        ("funded_range", 350_000_000 <= FUNDED_MIN_LAMPORTS <= FUNDED_MAX_LAMPORTS <= 1_000_000_000),
        ("floor_below_funded", 0 < FLOOR_BELOW_FUNDED_LAMPORTS <= 250_000_000), ("sell_reserve", SELL_RESERVE_FLOOR_LAMPORTS >= 1_600_000),
        ("tier", ARENA_LIVE_TIER == "T1"), ("a3_max_age", 0 < A3_MAX_AGE_MS <= 36 * 3_600_000),
        ("entry_gap", ENTRY_GAP_BPS <= 200.0 and ENTRY_GAP_MIN_N <= 10), ("exit_gap", EXIT_GAP_BPS <= 200.0 and EXIT_GAP_MIN_N <= 10),
        ("landing", LANDING_EXCESS_PP <= 10.0 and LANDING_MIN_N <= 30 and PAPER_FAIL_RATE >= 0.15),
        ("hard_halts", {H_FEED_BREAK, H_A3, H_STRAND, H_TWIN_PIN, H_BUY_UNREADABLE} <= g.HARD_ARENA_HALTS),
        ("arena_halts", {H_BUY_UNREADABLE, H_FEED_BREAK, H_ENTRY_GAP, H_LANDING, H_EXIT_GAP, H_LATE_SEND, H_STUCK2, H_STRAND, H_A3, H_TWIN_PIN} <= ARENA_HALTS),
        ("overrides", all(m in _ARENA_CLASS.__dict__ for m in GUARD_METHODS)),
    )
    for name, ok in checks:
        if not ok:
            return f"c0_guard:{name}"
    return None


def live_entry_refusal(book: Book, cfg: dict[str, Any]) -> str | None:
    """The keyless conditions a LIVE start adds on top of arena_start_refusal (QP-RULING-1023-wiring rulings 7/8; DEC-032 s3, s8). Fail closed on each."""
    why = c0_guard_problem()
    if why:
        return why
    if not FEED_NOTE_MERGED:
        return "live_feed_note_unmerged"
    if book.bot_id not in FEED_NOTE_BOOKS:
        return "live_feed_note_not_covering_book"  # scope item 17: no live feed is read until the EXP-024 observation note is merged and this constant flipped
    if "replay_only" in cfg:
        return "live_replay_only_set"  # a replay declaration in a live config is a wrong config, never silently ignored
    if cfg.get("mode") != h.LIVE:
        return "live_config_mode_not_live"
    if not cfg.get("a3_record_file"):
        return "a3_record_file_missing"  # the host's A3 daily record path is a declared input, never the research-0 default
    if not cfg.get("intents_file"):
        return "intents_file_missing"
    why = arena_live_ok_refusal(book.bot_id)
    if why:
        return why
    if book.exit_pin == h.EXIT_MODE_V2:
        return v2_start_refusal()  # the v2 rule source by sha256, live too (it was checked on the dry path only)
    return None


FEED_HOUR_GLOB = "h5-shadow-????-??-??T??.jsonl"  # the base executor's own pattern (H5Executor._resolve_intents)


def latest_start_record(intents: Path) -> dict[str, Any] | None:
    """The newest `start` record of the book's feed (oldest hourly file first; the last one read wins), or None. The shadow writes its start record ONCE, into the
    hour it started in, and the base tailer starts at the END of the newest hour (history is never replayed), so an executor started later never sees it by
    tailing. Only lines that parse as a JSON object with type "start" are kept; every other line is skipped unread beyond a substring test."""
    files = sorted(intents.glob(FEED_HOUR_GLOB)) if intents.is_dir() else [intents]
    last = None
    for f in files:
        try:
            with f.open("rb") as fh:
                for raw in fh:
                    if b'"start"' not in raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("type") == "start":
                        last = row
        except OSError:
            continue
    return last


def canary_record_refusal(bot_id: str, root: Path) -> str | None:
    """A live start needs a canary record in DEC/ whose owner line names THIS bot_id exactly (QP gap h). Regular files only (no symlink), the template
    file never counts, and a line naming another book does not cover this one."""
    d = root / "DEC"
    try:
        files = sorted(d.glob(CANARY_DEC_GLOB))
    except OSError:
        return "canary_dec_unreadable"
    seen_unreadable = False
    for f in files:
        if "template" in f.name or f.is_symlink() or not f.is_file():
            continue
        try:
            if bot_id in CANARY_OWNER_RE.findall(f.read_text()):
                return None
        except OSError:
            seen_unreadable = True
    return "canary_dec_unreadable" if seen_unreadable else "canary_record_for_bot_id_missing"


def arena_tier_refusal(path: Path | None = None) -> str | None:
    """A live start needs the arena's own tier file, with the base class' file checks (root-owned regular file, 0644, no symlink), to read exactly T1.
    The shared /etc/mal-h5/TIER is not read for arena books (ArenaExecutor._tier_path)."""
    tier, problem = h.read_tier(path or ARENA_TIER_PATH, root_checks=True)
    if problem == "tier_file_invalid" and ld.read_ceiling(path or ARENA_TIER_PATH, root_checks=True) == ("C1", None):
        return None  # DEC-032 Am.2 item 9.2: LADDER_C1 = ceiling C1 (the rung itself still starts at C0 and steps only per item 5)
    if problem:
        return f"arena_{problem}"
    return None if tier == ARENA_LIVE_TIER else f"arena_tier_not_{ARENA_LIVE_TIER.lower()}:{tier}"


def funded_refusal(cfg: dict[str, Any], live: bool) -> str | None:
    """`funded_lamports` is what the wallet was funded with for this canary (the owner's line). A live start needs it, in 0.35 .. 1.00 SOL; the wallet
    floor of the run is derived from it in code (funded - 0.25 SOL, never below 0.05), so it cannot be a config choice that is only checked elsewhere."""
    f = cfg.get("funded_lamports")
    if f is None:
        return "funded_lamports_missing" if live else None
    if isinstance(f, bool) or not isinstance(f, int) or not FUNDED_MIN_LAMPORTS <= f <= FUNDED_MAX_LAMPORTS:
        return "funded_lamports_out_of_range"
    return None


def wallet_floor_lamports(funded: int | None, configured: int) -> int:
    """max(configured, 0.05 SOL, funded - 0.25 SOL). Config can raise the floor, never lower it below the derived one."""
    derived = h.MIN_WALLET_FLOOR_LAMPORTS if funded is None else max(h.MIN_WALLET_FLOOR_LAMPORTS, funded - FLOOR_BELOW_FUNDED_LAMPORTS)
    return max(configured, derived)


def priority_refusal(book: Book, cfg: dict[str, Any]) -> str | None:
    """The priority pin is compiled in (item 9) and asserted at start. cfg["priority_pin"] is a declaration, not a setting: it must equal the book's
    compiled pin (a p155 book REQUIRES it, so a canary config names its fee). A buy priority in the config may only restate the pin."""
    pin = book.priority
    decl = cfg.get("priority_pin")
    if book.priority.profile != "default" and decl is None:
        return "priority_pin_missing"
    if decl is not None and decl != pin.as_dict():
        return "priority_pin_mismatch"
    for key in ("sell_priority_lamports", "escalated_priority_lamports"):
        if key in cfg:
            return f"{key}_not_configurable"
    if "buy_priority_lamports" in cfg and cfg["buy_priority_lamports"] != pin.buy:
        return "buy_priority_lamports_not_pin"
    return None


def v2_start_refusal() -> str | None:
    """The v2 rule source by sha256 (replaces the EXP-026 file, withdrawn 2026-10-17). Always run when the book is pinned v2."""
    try:
        data = V2_RULE_SOURCE.read_bytes()
    except OSError:
        return "v2_rule_source_unreadable"
    return None if hashlib.sha256(data).hexdigest() == V2_RULE_SHA256 else "v2_rule_source_sha_mismatch"


def entry_gap_bps(expected_tokens: int, got_tokens: int) -> float:
    """DEC-032 s5 g_entry: live buy average price over the entry price, minus 1, in bps. Price per token is stake / tokens, so with the same stake the
    ratio is expected / got: g = (expected / got - 1) * 10,000. 2% fewer tokens than 1/1.02 of expected reads exactly 200 (the old (exp - got) / exp read 196).
    The comparator is the trigger quote's expected tokens, not the paper twin (the twin join is the parity tool's). Zero tokens read as +inf."""
    if got_tokens <= 0:
        return float("inf")
    return 1e4 * (expected_tokens - got_tokens) / got_tokens


def sell_reserve_lamports(book: Book) -> int:
    """Worst-case fees reserved per open position for its exit (DEC-032 s3): SELL_RESERVE_SENDS sends at the book's highest sell priority plus the
    5,000 base fee, never below the p155 figure of 1.6M. (The base class' 1.0M constant covers 6 sends at p155 only.)"""
    top = max(book.priority.sell, book.priority.escalated)
    return max(SELL_RESERVE_FLOOR_LAMPORTS, SELL_RESERVE_SENDS * (top + h.tx.BASE_FEE_PER_SIGNATURE))


def a3_record_problem(path: Path, now_ms: int | None = None) -> tuple[list[str], str | None]:
    """(halt reasons, problem) from the NEWEST line of the A3 daily record (DEC-032 s5 item 1). A halt reason is any `halt.flags.*.halt` true (pins_changed
    included) or `warn.flags.program_changed.warn` true (a pump / PumpSwap / fees programdata hash differs from its pin). A missing, empty, unparsable or
    flag-less record is a problem, not a clear: the caller latches (fail closed)."""
    try:
        with path.open("rb") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        rec = json.loads(lines[-1])
    except (OSError, ValueError, IndexError):
        return [], "a3_record_unreadable"
    flags = ((rec.get("halt") or {}).get("flags")) if isinstance(rec, dict) and isinstance(rec.get("halt"), dict) else None
    if not isinstance(flags, dict) or not flags:
        return [], "a3_record_no_halt_flags"
    if now_ms is not None:
        try:
            run_ms = int(datetime.strptime(rec["run_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
        except (KeyError, ValueError, TypeError):
            return [], "a3_record_no_run_utc"
        if run_ms - now_ms > A3_FUTURE_SKEW_MS:
            return [], "a3_record_future"  # fail closed: a future stamp would stay "fresh" for ever
        if now_ms - run_ms > A3_MAX_AGE_MS:
            return [], "a3_record_stale"
    # a flag counts when it is set, malformed, or NOT evaluated (halt false with evaluated false is "unknown", not "clear")
    why = sorted(k for k, v in flags.items() if k not in A3_GUARD_FLAGS
                 and (not (isinstance(v, dict) and isinstance(v.get("halt"), bool)) or v["halt"] or v.get("evaluated") is False))
    pc = (((rec.get("warn") or {}).get("flags") or {}).get("program_changed")) if isinstance(rec.get("warn"), dict) else None
    if isinstance(pc, dict) and pc.get("warn") is True:
        why.append("program_changed")
    return why, None


def a3_guard_problem(record_path: Path, now_ms: int) -> tuple[str | None, str | None]:
    """(latch, refuse) from the DEC-031 Am.10 pooled guard's applied log for the SAME run as the daily record (DEC-032 Am.3 item 1). The raw record
    is checked first (pins_changed, program_changed, stale, future, every flag but synthetic_share_high): a raw reason is returned as the latch. Otherwise
    the newest line of `dirname(record)/a3-applied.jsonl` decides: HALT / FAIL / ERROR latch; NONE / PASS clear; a guard behind the record by at most
    A3_GUARD_LAG_MS refuses buys only (a3_guard_pending); a missing, unreadable, linked, oversize, malformed or ahead log, or a guard behind for longer, latches."""
    record_path = Path(record_path)
    why, problem = a3_record_problem(record_path, now_ms)
    if problem:
        return problem, None
    if why:
        return "a3_flags:" + ",".join(why), None
    try:
        with record_path.open("rb") as fh:
            rec_lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        rec = json.loads(rec_lines[-1])
        rec_mtime_ms = int(os.stat(record_path).st_mtime * 1000)
    except (OSError, ValueError, IndexError):
        return "a3_record_unreadable", None
    try:
        rec_ms = int(datetime.strptime(rec["run_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (KeyError, ValueError, TypeError):
        return "a3_record_no_run_utc", None
    ap = record_path.parent / A3_APPLIED_NAME
    try:
        st = os.lstat(ap)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > A3_APPLIED_MAX_BYTES:
            return "a3_guard_unreadable", None
        fd = os.open(ap, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st2 = os.fstat(fd)
            if (st2.st_ino, st2.st_dev) != (st.st_ino, st.st_dev) or st2.st_size > A3_APPLIED_MAX_BYTES:
                return "a3_guard_unreadable", None
            data = b""
            while len(data) <= A3_APPLIED_MAX_BYTES:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
        if len(data) > A3_APPLIED_MAX_BYTES:
            return "a3_guard_unreadable", None
        lines = [ln for ln in data.splitlines() if ln.strip()]
        line = json.loads(lines[-1])
    except (OSError, ValueError, IndexError):
        return "a3_guard_unreadable", None
    try:
        app_ms = int(datetime.strptime(line["run_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
        verdict = line.get("verdict")
    except (KeyError, ValueError, TypeError, AttributeError):
        return "a3_guard_malformed", None
    if verdict not in A3_GUARD_VERDICTS:
        return "a3_guard_malformed", None
    if app_ms > rec_ms:
        return "a3_guard_ahead", None
    if app_ms < rec_ms:
        if now_ms - max(rec_ms, rec_mtime_ms) <= A3_GUARD_LAG_MS:
            return None, "a3_guard_pending"
        return "a3_guard_missing", None
    if verdict not in A3_GUARD_CLEAR:
        return "a3_guard:" + str(verdict), None
    return None, None


def mint_screen_record(rpc: Callable[[str, list], Any], mint: str) -> dict[str, Any]:
    """The r4b-style record of one mint from ONE getAccountInfo (base64) on the executor's own RPC (no key handled here). Any failure is fetch_ok false,
    which classify_record denies (deny by default, DEC-031 Am.2 item 6)."""
    rec: dict[str, Any] = {"mint": mint, "fetch_ok": False, "owner_program": None, "freeze_authority": None, "token2022_extensions": []}
    try:
        res = rpc("getAccountInfo", [mint, {"encoding": "base64", "commitment": "confirmed"}])
        value = (res or {}).get("value")
        data = (value or {}).get("data")
        if value is None or not (isinstance(data, list) and len(data) == 2 and data[1] == "base64"):
            return rec
        owner = value.get("owner")
        parsed = mf.parse_mint_base64(base64.b64decode(data[0]), owner)
        rec.update(owner_program=owner, freeze_authority=parsed["freeze_authority"], token2022_extensions=parsed["token2022_extensions"], fetch_ok=True)
    except (Exception, SystemExit):
        rec["fetch_ok"] = False
    return rec


class FidelityMonitor:
    """Pure counters for the item-14 soft halts. Each `note_*` returns the halt name to latch, or None. No network, no price of its own: the paper
    comparator values arrive from the caller (entry: the trigger-quote expected tokens; exit: the parity join, which runs only under the item-17 note)."""

    def __init__(self) -> None:
        self.entry_gaps: deque[float] = deque(maxlen=ENTRY_GAP_WINDOW)  # rolling: an old good stretch never dilutes a bad one (DEC-032 s5: no window reset)
        self.exit_gaps: list[float] = []
        self.attempts = 0  # lifetime counts, for the report; the halt reads the rolling window below
        self.failed = 0
        self.recent_attempts: deque[bool] = deque(maxlen=LANDING_WINDOW_N)  # True = landed
        self.late_trips = 0
        self.stuck: set[str] = set()
        self.late_slots: list[int] = []

    def note_entry_gap(self, bps_worse: float) -> str | None:
        self.entry_gaps.append(float(bps_worse))
        n = len(self.entry_gaps)  # 10 .. 20: every fill so far; beyond that the last 20
        return H_ENTRY_GAP if n >= ENTRY_GAP_MIN_N and sum(self.entry_gaps) / n > ENTRY_GAP_BPS else None

    def note_exit_gap(self, bps_worse: float) -> str | None:
        self.exit_gaps.append(float(bps_worse))
        n = len(self.exit_gaps)
        return H_EXIT_GAP if n >= EXIT_GAP_MIN_N and sum(self.exit_gaps) / n > EXIT_GAP_BPS else None

    def note_attempt(self, landed: bool) -> str | None:
        self.attempts += 1
        self.failed += 0 if landed else 1
        self.recent_attempts.append(bool(landed))
        n = len(self.recent_attempts)
        if n < LANDING_MIN_N:
            return None
        excess = 100.0 * ((n - sum(self.recent_attempts)) / n - PAPER_FAIL_RATE)  # rolling 30 against the flat 15% model (the pressure refit is not available here)
        return H_LANDING if excess > LANDING_EXCESS_PP else None

    def note_sell_send(self, send_slot: int, sent_at_slot: int) -> str | None:
        late = sent_at_slot - send_slot
        self.late_slots.append(late)
        if late > LATE_SEND_SLOTS:
            self.late_trips += 1
        return H_LATE_SEND if self.late_trips >= LATE_SEND_TRIPS else None

    def note_stuck(self, mint: str) -> str | None:
        self.stuck.add(mint)
        return H_STUCK2 if len(self.stuck) >= 2 else None

    def late_slots_distribution(self) -> dict[str, int]:
        """Counts only (scope item 15)."""
        out: dict[str, int] = {}
        for v in self.late_slots:
            out[str(v)] = out.get(str(v), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


class ArenaExecutor(h.H5Executor):
    """H5Executor for one arena book. cfg["book_id"] names the book (a key of BOOKS); every other rule constant is compiled in."""

    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], keypair: Any = None, *, now_ms: Callable[[], int] | None = None,
                 root: Path | None = None, rpc_label: str | None = None):
        book = BOOKS.get(cfg.get("book_id"))  # type: ignore[arg-type]
        if book is None:
            raise SystemExit(f"book_id {cfg.get('book_id')!r} is not one of {sorted(BOOKS)}")
        self.abook = book
        self.EXIT_MODE_PIN = book.exit_pin  # instance pin, read by the base __init__ (item 5)
        why = arena_start_refusal(book, cfg, root or h.repo_root(), live=keypair is not None)
        if why:
            raise SystemExit(f"arena executor start refused: {why}")
        cfg = dict(cfg)
        cfg.pop("buy_priority_lamports", None)  # restated pin only (checked above); the base would clamp 155,000 to its 55,000 maximum and report a clamp
        end = cfg.get("end_ms")
        cfg["end_ms"] = book.end_ms_seal if end is None else min(int(end), book.end_ms_seal)  # item 7: clamped, never later
        days = cfg.get("max_days")
        if days is not None:
            cfg["max_days"] = min(float(days), (book.end_ms_seal - book.clock_start_ms) / 86_400_000)
        self.funded_lamports: int | None = cfg.get("funded_lamports")
        self.cfg_a3_file = cfg.get("a3_record_file") or (A3_RECORD_DEFAULT if keypair is not None else None)
        self.cfg_exit_gap_file = cfg.get("exit_gap_file")
        self._a3_polled_ms = -A3_POLL_MS
        self._a3_refuse: str | None = None  # DEC-032 Am.3: a pending pooled guard refuses buys (not a latch)
        self._exit_gap_off = 0
        self.mint_screens: dict[str, str | None] = {}  # mint -> close-blocked reason (None = clear), written by the pre-send screen
        self.close_blocked_refused = 0
        self.monitor = FidelityMonitor()
        self.feed_verified = False
        self.starts_seen = 0
        self.slices_taken = 0
        self.window_counts: dict[str, int] = {}
        self._pools_seen: set[str] = set()
        self._start_ms: int | None = None
        self.gates: LiveGates | None = None  # live only: parity recorder and the parity / tie-out gates
        self._sends_noted: set[str] = set()  # signatures already written as an `arena_send` row (this process; the exporter de-duplicates across restarts)
        super().__init__(rpc, cfg, keypair, now_ms=now_ms, pick_oracle=None, root=root, rpc_label=rpc_label)
        if keypair is not None:
            gdir = Path(self.h5cfg["state_dir"]) / "arena-parity"
            interval = cfg.get("parity_interval_s")
            self.gates = LiveGates(gdir, ParityRecorder(gdir), str(cfg["parity_twins_file"]), str(cfg["tieout_dir"]), self.paths["stop"],
                                   self._parity_state, twin_pins=twin_pins(book), interval_s=interval if isinstance(interval, (int, float)) and interval > 0 else None)
        self.ladder: ld.LadderLog | None = None
        self._marks: dict[str, tuple[int, int]] = {}  # mint -> (marked value lamports, mark ms): item 3 (a)
        self._trigger_day: str | None = None
        self._ceiling_cached = "C0"  # refreshed before the buys of every tick (_ladder_pre_buy); C0 until read
        self._c1_by_this_process = False  # C1 is honoured only after THIS process wrote the step_up (a record found at start is never trusted: reviewer #862 item 1)
        if keypair is not None:
            self.ladder = ld.LadderLog(Path(self.h5cfg["state_dir"]) / "arena-ladder" / "ladder.jsonl", owner_uid=os.geteuid())
            why = self._ladder_start_problem()
            if why:
                raise SystemExit(f"arena executor start refused: {why}")
        self.exit_gap_writer: ExitGapWriter | None = None
        if keypair is not None:
            try:
                self.exit_gap_writer = ExitGapWriter(Path(str(self.cfg_exit_gap_file)))
            except OSError as exc:
                raise SystemExit(f"arena executor start refused: exit_gap_file_unusable ({type(exc).__name__})") from None
        if self.exit_mode != book.exit_pin:
            raise SystemExit("exit mode pin was not applied")
        if keypair is not None:
            why = self._funded_balance_problem() or self._rescue_problem()
            if why:
                raise SystemExit(f"arena executor start refused: {why}")
        got = (self.h5.buy_priority_lamports, self.h5.sell_priority_lamports, self.h5.escalated_priority_lamports)
        if got != (book.priority.buy, book.priority.sell, book.priority.escalated):  # asserted at start, after the base has built its limits
            raise SystemExit(f"arena executor start refused: priority pin not applied {got}")

    @property
    def h5(self) -> h.H5Limits:
        """The base limits with the book's compiled priority pin on all three fee fields (item 9). The base clamps these to its 55,000 / 150,000 code
        constants; this is the only place a book's fee differs from them, and the start row's `limits` shows the result."""
        pr = self.abook.priority
        lim = replace(super().h5, buy_priority_lamports=pr.buy, sell_priority_lamports=pr.sell, escalated_priority_lamports=pr.escalated)
        if self._rung() == "C1":  # DEC-032 Am.2 item 3: C1's own column (the base tier is T1 underneath, whose stake 0.10 would otherwise cap it)
            r = ld.LADDER["C1"]
            floor = max(lim.wallet_floor_lamports, h.MIN_WALLET_FLOOR_LAMPORTS, int(getattr(self, "funded_lamports", 0) or 0) - r.floor_below_deposits_lamports)
            c1 = {"stake_lamports": r.stake_lamports, "max_open": r.max_open, "max_trades_per_day": r.max_trades_per_day,
                  "daily_loss_lamports": r.daily_trigger_lamports, "total_loss_lamports": r.lifetime_cap_lamports}
            cfg = self.h5cfg  # config may only tighten (reviewer #862 item 2), never raise a C1 field
            c1 = {k: min(v, int(cfg[k])) if isinstance(cfg.get(k), int) and not isinstance(cfg.get(k), bool) else v for k, v in c1.items()}
            return replace(lim, gap_hold_ms=max(lim.gap_hold_ms, GAP_HOLD_MS), wallet_floor_lamports=floor, **c1)
        # C0 (gap a): the pinned table is the ceiling whatever the shared TIER file says. T1 / T2 in the file cannot raise a single field; a lower tier
        # (T0) or a tighter config still lowers it, because this takes the minimum.
        return replace(lim, gap_hold_ms=max(lim.gap_hold_ms, GAP_HOLD_MS),
                       wallet_floor_lamports=wallet_floor_lamports(getattr(self, "funded_lamports", None), lim.wallet_floor_lamports),
                       **{k: min(getattr(lim, k), v) for k, v in C0_TIER.items()})

    def _tier_path(self) -> Path:
        """Item 2 (QP 10-23): live reads the arena's own tier file, never /etc/mal-h5/TIER. A dry run keeps the base's <state_dir>/TIER."""
        return ARENA_TIER_PATH if not self.dry_run else super()._tier_path()

    def _refresh_tier(self, now: int) -> None:
        """Live: the arena TIER file may read LADDER_C1 (DEC-032 Am.2 item 9.2). For the BASE tier machinery that is T1 (the base tier stays T1 at every rung;
        the rung's own limits come from the `h5` property). Only that one content is mapped; every other read is the base's own, unchanged. Dry: the base."""
        if self.dry_run:
            return super()._refresh_tier(now)
        orig = h.read_tier
        arena = self._tier_path()

        def mapped(path: Path, root_checks: bool) -> tuple[str, str | None]:
            tier, problem = orig(path, root_checks)
            if Path(path) == arena and problem == "tier_file_invalid" and ld.read_ceiling(Path(path), root_checks) == ("C1", None):
                return "T1", None
            return tier, problem

        h.read_tier = mapped  # swapped for this call only; the loop is single-threaded (the same pattern as intent_tick's tail_lines)
        try:
            super()._refresh_tier(now)
        finally:
            h.read_tier = orig

    def __repr__(self) -> str:
        return f"ArenaExecutor(book={self.abook.bot_id}, mode={self.run_mode})"

    # -- committed loss and the wallet floor (DEC-032 s3; QP gap b). The base class' at_risk is the stakes only and its balance_need omits open stakes.
    def _funded_balance_problem(self) -> str | None:
        """funded_lamports (the owner's line) must match the wallet's FINALIZED balance: balance in [funded - realized loss - tol, funded + tol]. A restart
        after losses is allowed to be lower by exactly what was realized; anything else is a wrong funded figure and the floor derived from it would lie."""
        try:
            bal = int(self.rpc("getBalance", [str(self.user), {"commitment": "finalized"}])["value"])
        except (Exception, SystemExit):
            return "funded_balance_unreadable"
        lost = max(0, -int(self.state.realized_lamports))
        f, tol = int(self.funded_lamports or 0), FUNDED_BALANCE_TOLERANCE_LAMPORTS
        return None if f - lost - tol <= bal <= f + tol else "funded_balance_mismatch"

    # -- DEC-032 Amendment 2: the stake ladder (live only; a dry run is always C0 and touches none of this)
    def _rung(self) -> str:
        """min(the log's rung, the ceiling read before this tick's buys). C1 also needs: this process wrote the step_up, and funded_lamports >= C1's deposits.
        Anything else is C0 (QP #861 condition 3: the log's `to` is clamped to LADDER)."""
        lg = getattr(self, "ladder", None)
        if lg is None or getattr(self, "dry_run", True) or lg.problem:
            return "C0"
        if lg.rung == "C1" and getattr(self, "_ceiling_cached", "C0") == "C1" and getattr(self, "_c1_by_this_process", False) \
                and int(getattr(self, "funded_lamports", 0) or 0) >= ld.LADDER["C1"].deposits_min_lamports:
            return "C1"
        return "C0"

    def _ceiling(self) -> str:
        return ld.read_ceiling(ARENA_TIER_PATH)[0]

    def _ladder_start_problem(self) -> str | None:
        lg = self.ladder
        assert lg is not None
        if lg.problem:
            return f"ladder_log_broken:{lg.problem}"  # the hash chain does not verify: Helm and the manager look before anything trades
        self._ceiling_cached = self._ceiling()
        if self._ceiling_cached == "C1" and self.abook.bot_id != ld.LADDER_BOOK:
            return "ladder_ceiling_for_uncovered_book"
        if lg.rung == "C1":
            # A C1 record is never honoured at start (QP #862 condition 2, reviewer item 1): ceiling lowered, unlock not verified now, or the step-down
            # inputs (trailing-30, chain deposits) not available -> step down before the first tick, so no round runs at 0.15.
            why = ("ceiling_lowered" if self._ceiling_cached != "C1" else
                   "unlock_not_verified_at_start" if not ld.verify_unlock(self.abook.bot_id)[0] else "c1_record_at_start_step_down_inputs_unavailable")
            self._step(lg, "step_down", "C1", "C0", why)
        today = h.day_key(self.now_ms())
        if any(r.get("kind") in ("daily_trigger", "step_down") and r.get("day") == today and r.get("why") == "daily_trigger" for r in lg.records):
            self._trigger_day = today  # a restart inside the day keeps the trigger (item 3 (a): it holds until 00:00Z)
        return None

    def _step(self, lg: "ld.LadderLog", kind: str, frm: str, to: str, why: str, **detail: Any) -> None:
        rec = lg.append(kind, self.now_ms(), frm, to, why, day=h.day_key(self.now_ms()), realized_lamports=int(self.state.realized_lamports), **detail)
        self._log("ladder_" + kind, "", rung_from=frm, rung_to=to, why=why, seq=rec["seq"])
        if kind != "daily_trigger":
            self._alert("ladder_" + kind, "", rung_from=frm, rung_to=to, why=why)

    def _unlock_still_valid(self, step_up: dict[str, Any]) -> str | None:
        ok, why = ld.verify_unlock(self.abook.bot_id)
        if not ok:
            return f"unlock_no_longer_valid:{why}"
        try:
            sha = hashlib.sha256(ld.UNLOCK_PATH.read_bytes()).hexdigest()
        except OSError:
            return "unlock_unreadable"
        return None if sha == step_up.get("unlock_sha256") else "unlock_changed_since_step_up"

    def _realized_today(self, now: int) -> int:
        return int(self.counters.day(h.day_key(now)).get("realized") or 0)

    def _ladder_buy_refusal(self) -> str | None:
        if self.dry_run:
            return None
        now = self.now_ms()
        if self._trigger_day == h.day_key(now):
            return "daily_trigger_until_0000z"
        rung = ld.LADDER[self._rung()]
        st = self.state
        worst = [self._position_worst_case(int(p.get("spend") or 0), int(p.get("extra_cost") or 0)) for p in st.open.values()]
        worst += [self._position_worst_case(int(p.get("spend") or 0)) for p in st.pending.values() if p["kind"] == "buy"]
        if ld.intraday_committed_lamports(self._realized_today(now), worst, self._position_worst_case(self.h5.stake_lamports)) > rung.intraday_cap_lamports:
            return "intraday_cap"
        return None

    def _refresh_marks(self, now: int) -> None:
        """Item 3 (a): every open position marked at its sell quote from the pool's current reserves, at least every 10 s. A failed read keeps the old mark,
        which goes stale after 10 s and then counts the position at zero."""
        done = 0
        for mint, pos in list(self.state.open.items()):
            old = self._marks.get(mint)
            send, guard = self._exit_window_start(pos)
            in_window = send is not None and now >= send - guard
            if mint in self.state.pending or in_window:
                continue  # never an RPC read in a position's exit window or while its sell is in flight (QP #862 condition 3)
            last_chance = send is not None and now >= send - guard - LAST_MARK_LEAD_MS  # the last ticks before the window: refresh now, whatever the cadence
            if old is not None and now - old[1] < ld.MARK_MAX_AGE_MS // 2 and not (last_chance and now - old[1] >= LAST_MARK_MIN_GAP_MS):
                continue
            if done >= MARKS_PER_TICK:
                break
            done += 1
            try:
                snap, _pool, _err = self._snapshot(mint, self.exit_commitment)
                if snap is None:
                    continue
                q = h.pe.exit_check({**pos, "tokens": int(pos.get("tokens") or 0)}, snap, now, own_trade_in_state=True).get("quote_out")
            except (Exception, SystemExit):  # noqa: BLE001 - a mark that cannot be read is a stale mark, never a crash
                continue
            if isinstance(q, int) and not isinstance(q, bool):
                self._marks[mint] = (q, now)
        for gone in set(self._marks) - set(self.state.open):
            self._marks.pop(gone, None)

    def _exit_window_start(self, pos: dict[str, Any]) -> tuple[int | None, int]:
        """(the position's sell send instant or None, the no-lookup guard before it)."""
        send = (((pos.get("h5") or {}).get("plan")) or {}).get("send_wall_ms")
        guard = max(MARK_EXIT_GUARD_MS, int(self.h5.arm_lead_ms) + 3_000)  # never inside the arm-and-send window, whatever arm_lead_ms is configured to
        return (send if isinstance(send, int) and not isinstance(send, bool) else None), guard

    def daily_loss_now(self, now: int) -> int:
        """Item 3 (a), literally: a mark older than 10 s (or none) counts the position at zero. No mark is ever treated as fresher than it is.
        What avoids false triggers on normal exits is WHEN the last mark is taken: _refresh_marks forces one in the LAST_MARK_LEAD_MS (1 s) before a
        position's no-lookup window, so at the sell-send instant the mark is guard + at most 1 s old (5 to 6 s with the default 2 s arm lead) and stays
        valid until about send + 4 s, which covers a normal landing. A sell still open after that (stuck, failed) counts at zero, as item 3 (a) says.
        With arm_lead_ms above 7 s the guard is above 10 s and the mark is already stale at the send instant (the position counts at zero: conservative).
        The pre-send intraday cap still counts every open position at zero (unchanged)."""
        positions = [{"cost_lamports": int(p.get("buy_cost_lamports") or p.get("spend") or 0) + int(p.get("extra_cost") or 0),
                      "mark_value_lamports": (self._marks.get(m) or (None, None))[0], "mark_ms": (self._marks.get(m) or (None, None))[1]}
                     for m, p in self.state.open.items()]
        return ld.daily_loss_lamports(self._realized_today(now), positions, now)

    def _ladder_guarded(self, fn: Any, now: int) -> None:
        """Run one ladder step; an exception latches ladder_error (no buys) and is never raised into the loop or the exits (QP #862 condition 1)."""
        try:
            fn(now)
        except Exception as exc:  # noqa: BLE001
            self._ceiling_cached = "C0"
            if H_LADDER not in self.counters.halts:
                self._latch(H_LADDER, why=type(exc).__name__)

    def _ladder_pre_buy(self, now: int) -> None:
        """Before the buys of every tick: re-read the ceiling and run the trigger and step-down rules, so a lowered ceiling never gets a round at C1."""
        if self.ladder is None or self.dry_run:
            return
        self._ceiling_cached = self._ceiling()
        self.ladder_tick(now, marks=False, step_up=False)

    def ladder_tick(self, now: int, marks: bool = True, step_up: bool = True) -> None:
        """Live: marks, the daily trigger (a pause at C0, a step down at C1), the step-down rules at C1, and the step-up evaluation at C0 under a C1 ceiling."""
        lg = self.ladder
        if lg is None or self.dry_run:
            return
        if marks:
            self._refresh_marks(now)
        rung = self._rung()
        today = h.day_key(now)
        fired = self._trigger_day == today
        if not fired and self.daily_loss_now(now) >= ld.LADDER[rung].daily_trigger_lamports:
            if rung == "C0":
                self._step(lg, "daily_trigger", "C0", "C0", "daily_trigger")  # persisted FIRST (reviewer #862 item 4); an append failure raises -> ladder_error latch
            self._trigger_day, fired = today, True
        if lg.rung == "C1" and self._c1_by_this_process:
            entered = next((r for r in reversed(lg.records) if r.get("kind") == "step_up"), {})
            since = int(self.state.realized_lamports) - int(entered.get("realized_lamports") or 0)
            why = "ceiling_lowered" if self._ceiling_cached != "C1" else ld.step_down_reason(
                "C1", soft_halts_latched=list(self.counters.halts), daily_trigger_fired=fired, realized_since_rung_lamports=since,
                trailing_mean_d_bps=None, deposits_lamports=None)  # trailing-30 and chain deposits: not built yet -> a step down (fail closed)
            if why is None and int(getattr(self, "funded_lamports", 0) or 0) < ld.LADDER["C1"].deposits_min_lamports:
                why = "funded_below_c1_need"
            if why is None:
                why = self._unlock_still_valid(entered)  # reviewer nit on #862: the root evidence is re-checked at every tick while at C1
            if why:
                self._step(lg, "step_down", "C1", "C0", why)
                self._c1_by_this_process = False
            return
        if lg.rung == "C1":  # a C1 record this process did not write (written at runtime by someone else): never honoured; step it down
            self._step(lg, "step_down", "C1", "C0", "c1_record_not_written_by_this_process")
            return
        if step_up and self._ceiling() == "C1" and not lg.stepped_down and self.abook.bot_id == ld.LADDER_BOOK:
            ok, res = ld.evaluate_step_up(self._step_up_inputs(now))
            self._last_step_up_eval = {"at_ms": now, "ok": ok, "checks": res}
            if ok:
                unlock_sha = hashlib.sha256(ld.UNLOCK_PATH.read_bytes()).hexdigest()
                self._step(lg, "step_up", "C0", "C1", "dec032_am2_item5_all_checks", checks=[n for n, _o, _w in res], unlock_sha256=unlock_sha)
                self._c1_by_this_process = True

    def _step_up_inputs(self, now: int) -> dict[str, Any]:
        """Only the item 9.2 sources. Inputs that are not built yet (trailing-30, halt history, tie-out sign-offs, chain total, deposits, floor) are left OUT,
        so evaluate_step_up fails them: C1 is unreachable until they are built, reviewed and merged."""
        inp: dict[str, Any] = {"ceiling": self._ceiling(), "stepped_down": self.ladder.stepped_down if self.ladder else True,
                               "unlock": ld.verify_unlock(self.abook.bot_id), "sell_pin": sell_pin_problem(), "ms_to_end": int(self.h5.end_ms or 0) - now}
        if self.gates is not None:
            try:
                inp["parity"] = json.loads((self.gates.dir / "parity-result.json").read_text())
            except (OSError, ValueError):
                pass
        return inp

    def _rescue_problem(self) -> str | None:
        """Live start: (1) a root rescue sent for a mint that is still open in the state (marker <state_dir>/RESCUE-<mint>.json, tools/h5_arena_sell_and_close.py)
        refuses until the base --mark-closed reconciled it; (2) every reconciled `manual_close` row closes its parity trip at the trip's chain delta (QP #839
        condition 1: a rescue-closed trip enters parity d and the tie-out, it is never a stuck exit_pending trip)."""
        sd = Path(self.h5cfg["state_dir"])
        try:
            markers = sorted(sd.glob("RESCUE-*.json"))
        except OSError:
            return "rescue_marker_unreadable"
        for m in markers:
            if m.name[len("RESCUE-"):-len(".json")] in self.state.open:
                return "rescue_reconcile_pending"
        if self.gates is None:
            return None
        try:
            rows = [json.loads(x) for x in Path(self.fills.path).read_text().splitlines() if '"manual_close"' in x]
        except (OSError, ValueError):
            return "ledger_unreadable_for_manual_close"
        for r in rows:
            if isinstance(r, dict) and r.get("kind") == "manual_close":
                why = self.gates.recorder.on_manual_close(str(r.get("mint")), r)
                if why:
                    return why
        return None

    def _parity_state(self, now: int) -> dict[str, Any]:
        """The parity tool's state.json: a stamp only for a read that succeeded just now; a missing stamp is P7 (no buy). The H5 executor has no hash-chained
        decision log; its analogue (the feed's start record pinned to the book's frozen sha, no feed_break latched) stamps decision_chain."""
        at: dict[str, int] = {}
        try:
            self.committed_loss_lamports()
            at["loss"] = now
        except Exception:  # noqa: BLE001
            pass
        if self._balance_value(now) is not None and self._bal is not None:
            at["balance"] = self._bal[1]
        stamp = gt_twin_run_stamp(self.gates.twins_file, list(self.gates.recorder.trips))  # type: ignore[union-attr]
        if stamp is not None:  # the writer's own run time, never now (QP 10-23 condition 1)
            at["twin"] = stamp
        at["parity"] = now
        if self.feed_verified and H_FEED_BREAK not in self.counters.halts:
            at["decision_chain"] = now
        tier, problem = h.read_tier(self._tier_path(), root_checks=not self.dry_run)
        if problem == "tier_file_invalid" and not self.dry_run and ld.read_ceiling(self._tier_path())[1] is None:
            problem = None  # LADDER_C1 is a valid arena TIER (DEC-032 Am.2 item 9.2)
        if h.live_ok_valid() is None and problem is None:
            at["control_files"] = now
        # DEC-032 Am.5 item 6b: the parity tool is told the rung explicitly (C0: a gap-share breach is NOT_DECIDABLE, not a halt; C1: unchanged)
        return {"at_ms": at, "unresolved_hard_halts": sorted(HARD_ARENA_HALTS & set(self.counters.halts)), "rung": self._rung()}

    def _exit_bound_ms(self, mint: str, decision_ms: Any) -> int | None:
        """The book's own late instant for the exit (plan late_wall_ms = s0 + LATE_SELL_S), never an input of the parity files."""
        late = ((self.counters.plans.get(mint) or {}).get("plan") or {}).get("late_wall_ms")
        return late if isinstance(late, int) and not isinstance(late, bool) else None

    def _sell_reserve(self) -> int:
        return sell_reserve_lamports(self.abook)

    def _buy_fee(self) -> int:
        return self.h5.buy_priority_lamports + h.tx.BASE_FEE_PER_SIGNATURE

    def _position_worst_case(self, stake: int, extra: int = 0) -> int:
        """One position or in-flight buy at zero: the stake, what the buy cost in fees, the ATA rent, the reserved sell fees, and fees booked since."""
        return stake + extra + self._buy_fee() + h.RENT_RESERVE_LAMPORTS + self._sell_reserve()

    def committed_loss_lamports(self) -> int:
        """realized loss since the first send (floored at 0) + every open or pending position's worst case. Does NOT include the buy about to be sent."""
        st = self.state
        total = max(0, -int(st.realized_lamports))
        total += sum(self._position_worst_case(int(p.get("spend") or 0), int(p.get("extra_cost") or 0)) for p in st.open.values())
        total += sum(self._position_worst_case(int(p.get("spend") or 0)) for p in st.pending.values() if p["kind"] == "buy")
        return total

    def committed_loss_refusal(self) -> str | None:
        """Pre-send: committed loss plus this buy's worst case must be at most the total stop (DEC-032 s3; the stop does not reset on restart)."""
        if self.committed_loss_lamports() + self._position_worst_case(self.h5.stake_lamports) > self.h5.total_loss_lamports:
            return "committed_loss_stop"
        return None

    def _budget_stop(self, now: int) -> str | None:
        return super()._budget_stop(now) or self.committed_loss_refusal()

    def _balance_refusal(self, now: int) -> str | None:
        """balance - open stakes at zero - this stake - fees - rent - the sell reserves must stay at or above the floor. The balance has already paid
        for the open stakes; counting them again is deliberate (DEC-032 s3)."""
        bal = self._balance_value(now)
        if bal is None:
            return "balance_unreadable"
        st = self.state
        held = sum(int(p.get("spend") or 0) for p in st.open.values()) + sum(int(p.get("spend") or 0) for p in st.pending.values() if p["kind"] == "buy")
        n_pos = len(st.open) + sum(1 for p in st.pending.values() if p["kind"] == "buy")
        need = held + self.h5.stake_lamports + self._buy_fee() + h.RENT_RESERVE_LAMPORTS + (n_pos + 1) * self._sell_reserve() + self.h5.wallet_floor_lamports
        return None if bal >= need else "balance_floor"

    def _live_buy(self, trg: h.H5Trigger, ps: Any, terms: dict[str, Any], plan: Any, now: int, drift: float | None, seen_ms: int | None) -> None:
        why = arena_live_ok_refusal(self.abook.bot_id)  # Helm's gate for THIS book, re-read before every live buy (removing it stops new buys at once)
        if why:
            return self._refuse(trg, why)
        why = self._ladder_buy_refusal()  # DEC-032 Am.2 items 3 and 4: the daily trigger (held to 00:00Z) and the intraday cap, at the current rung
        if why:
            return self._refuse(trg, why)
        why = self.committed_loss_refusal() or self._balance_refusal(self.now_ms())  # the last look before anything is built or signed
        if why:
            return self._refuse(trg, why)
        why = v_band_refusal(trg.v_lamports, ps.quote_mint)  # canonical pools only (DEC-031 Am.2 item 6)
        if why:
            return self._refuse(trg, why)
        if self.gates is not None:
            t = self.now_ms()
            twin_why = self.gates.twin_refusal(t)
            if twin_why and twin_why.startswith(TWIN_PIN_MISMATCH):
                self.latch_external(H_TWIN_PIN, why=twin_why)  # no buy and a latch: the twins were priced by another config or another writer
            why = twin_why or self.gates.parity_refusal(t) or self.gates.tieout_refusal(t, self.state.first_attempt_ms)
            if why:
                return self._refuse(trg, why)
        blocked = self._screen_mint(trg.mint)  # DEC-032 s3 "Venue": a freeze authority or a Token-2022 extension outside the allowlist is a strand = total loss
        if blocked:
            self.close_blocked_refused += 1
            return self._refuse(trg, f"close_blocked_mint:{blocked}")
        if self._signal_age_ms(trg) > self._signal_age_limit_ms():  # W3: the gates and the mint screen above can take seconds; the age is judged AFTER them, not before
            return self._refuse(trg, "stale_trigger_after_gates")
        super()._live_buy(trg, ps, terms, plan, now, drift, seen_ms)

    def _live_gate(self) -> str | None:
        """The base class's gate, read immediately before every live buy send and rebroadcast (h5_executor._send / _live_buy): the arena's OWN LIVE_OK first
        (it must exist and name this bot_id), then the base checks (/etc/mal-h5/LIVE_OK, EXP-024 Part 1). Removing either file stops the next send."""
        if self.dry_run:
            return None
        return arena_live_ok_refusal(self.abook.bot_id) or super()._live_gate()

    def _signal_age_limit_ms(self) -> int:
        return min(SIGNAL_AGE_MAX_MS, int(self.h5.max_trigger_age_ms))

    def _signal_age_ms(self, trg: h.H5Trigger) -> int:
        return self.now_ms() - trg.decision_ms

    def _send(self, p: dict[str, Any], mint: str) -> None:
        """Immediately before the FIRST send of a live buy, judge the signal age once more (W3: building and signing come after the gates and take time too).
        A stale signal is not sent: the pending buy is dropped exactly as the base class drops one whose kill switch fired. Rebroadcasts are left to the base."""
        if not self.dry_run and p.get("kind") == "buy" and p.get("sends", 0) == 0 and not self._halt_present():
            age = self.now_ms() - int(p.get("decision_t_ms") or 0)
            if age > self._signal_age_limit_ms():
                self.state.pending.pop(mint, None)
                self._log("send_blocked", mint, reason="stale_trigger_presend", kind_="buy", signal_age_ms=age)
                return
        if not self.dry_run and p.get("kind") == "buy" and p.get("sends", 0) == 0:
            self._send_guarded(p, mint)  # the first send of a live buy: the age is judged once more at the sendTransaction call itself
        else:
            super()._send(p, mint)
        self._note_send(p, mint)

    def _note_send(self, p: dict[str, Any], mint: str) -> None:
        """Live only: one `arena_send` ledger row per signature the executor handed to sendTransaction (the daily tie-out's `sends`, with the blockhash's
        last valid height that decides a confirmed drop; tools/arena_tieout_ledger.py exports it). A send that was blocked before the socket (STOP, HALT, a
        stale signal: the pending record is gone or no send was counted) is not one. Rebroadcasts of the same signature are not repeated."""
        if self.dry_run or not p.get("sends") or (self.state.pending.get(mint) or {}).get("signature") != p.get("signature"):
            return
        sig = p.get("signature")
        if not sig or sig in self._sends_noted:
            return
        self._sends_noted.add(sig)
        self._log("arena_send", mint, signature=sig, side=p.get("kind"), last_valid_block_height=p.get("lvbh"), first_send_ms=p.get("first_send_ms"))

    def _send_guarded(self, p: dict[str, Any], mint: str) -> None:
        """QP 10-23 condition 5: tools/h5_executor.py and tools/probe_live.py stay untouched, so the last check is made at the one place the base class writes to the socket,
        `self.rpc("sendTransaction", ...)`, by wrapping `self.rpc` for the duration of this one call. A stale signal raises before the call is made (nothing is written); the base
        class files that as a send_error alert and counts a send, so afterwards the pending buy is dropped and a send_blocked row is written. The wrapper is always removed."""
        real, stale = self.rpc, []

        def guard(method: str, params: Any = None, *a: Any, **k: Any) -> Any:
            if method == "sendTransaction":
                age = self.now_ms() - int(p.get("decision_t_ms") or 0)
                if age > self._signal_age_limit_ms():
                    stale.append(age)
                    raise RuntimeError("stale_trigger_at_write")
            return real(method, params, *a, **k) if params is not None else real(method)

        self.rpc = guard
        try:
            super()._send(p, mint)
        finally:
            self.rpc = real
        if stale:
            self.state.pending.pop(mint, None)
            self._log("send_blocked", mint, reason="stale_trigger_at_write", kind_="buy", signal_age_ms=stale[0])

    def _screen_mint(self, mint: str) -> str | None:
        """Fresh read of the mint account (never a cache: the pre-send look is the one that counts). Returns the close-blocked reason, None when clear."""
        why = classify_record(mint_screen_record(self.rpc, mint))
        self.mint_screens[mint] = why
        return why

    # -- seal (item 2): the CAP-PICK guard is gone; the book's own windows are the refusals below
    def _seal_reason(self, trg: h.H5Trigger, now: int) -> str | None:
        return None

    def _halted_arena(self) -> str | None:
        hit = sorted(ARENA_HALTS & set(self.counters.halts))
        return hit[0] if hit else None

    def _hard_refusal(self, trg: h.H5Trigger, now: int) -> str | None:
        """The base refusals, with the book's Q* (item 4) and the arena halts enforced in dry runs too (the base only enforces budget halts live)."""
        why = self._halted_arena()
        if why:
            return why
        if self._a3_refuse:
            return self._a3_refuse
        if not self.feed_verified:
            return REFUSAL_UNVERIFIED
        if self.abook.exit_pin == h.EXIT_MODE_V2 and self.slices_taken == 0:
            return REFUSAL_V2_FEED
        if trg.q_lamports > self.abook.q_star_lamports:
            return REFUSAL_Q
        if trg.decision_ms < self.abook.clock_start_ms or trg.decision_ms >= self.abook.end_ms_seal or \
                (self._start_ms is not None and trg.decision_ms < self._start_ms):
            return REFUSAL_DECISION_WINDOW
        # the base check tests the 40 SOL constant; the trigger handed to it is capped at that constant (our own check above is the book's)
        capped = trg if trg.q_lamports <= h.RULE_Q_MAX_LAMPORTS else replace(trg, q_lamports=h.RULE_Q_MAX_LAMPORTS)
        return super()._hard_refusal(capped, now)

    # -- feed screen (items 2, 3): runs on every line the base is about to parse
    def _latch_feed_break(self, why: str, **detail: Any) -> None:
        self._latch(H_FEED_BREAK, why=why, **detail)

    def decide_row(self, row: dict[str, Any]) -> tuple[str | None, str | None]:
        """(keep, verdict) for one feed record. verdict is for trigger records only: 'attempt', or the refusal reason (parser, window or exclusion).
        The order of the window filters follows tools/arena_h5_score.py score_book (clock, end, look2, duplicate pool) so the md5 of verdicts matches."""
        t = row.get("type")
        if t == "start":
            why = start_record_problem(self.abook, row)
            self.starts_seen += 1
            if why:
                self.feed_verified = False
                self._latch_feed_break(why)
                return "drop", None
            self._start_ms = int(row["start_ms"])
            self.feed_verified = True
            return "drop", None
        if t == "gap":
            # DEC-032 s5 item 2: ANY gap record holds buys for 10 minutes. The base class skips the hold when flags_pools is the literal false
            # (a redundant-socket reconnect); a canary does not take that exemption.
            self._gap_until_ms = max(self._gap_until_ms, self.now_ms() + self.h5.gap_hold_ms)
            if self.gates is not None:
                self.gates.recorder.note_gap(self.now_ms())
            return "keep", None
        if t == "boost_slice":
            self.slices_taken += 1 if self._slice_format_ok(row) else 0
            return "keep", None
        if t != "trigger" or row.get("variant") != h.TRIGGER_VARIANT:
            return "keep", None
        d = row.get("t_detect_ms")
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            return "keep", None  # the parser will refuse it with its own reason
        if int(d) < self.abook.clock_start_ms or (self._start_ms is not None and int(d) < self._start_ms) or int(d) >= self.abook.end_ms_seal:
            return self._window("drop", REFUSAL_DECISION_WINDOW)
        s0 = row.get("s0_t_recv_ms")
        if self.abook.s0_margin_ms or s0 is not None:
            if isinstance(s0, bool) or not isinstance(s0, int) or s0 < self.abook.clock_start_ms + self.abook.s0_margin_ms or s0 >= self.abook.end_ms_seal:
                return self._window("drop", REFUSAL_S0_WINDOW)
        if h._sealed_flag(row.get("h5_look2_sealed")):
            return self._window("drop", REFUSAL_LOOK2)
        pool = row.get("pool")
        if isinstance(pool, str) and pool in self._pools_seen:
            return self._window("keep_dup", "duplicate_pool")
        if isinstance(pool, str):
            self._pools_seen.add(pool)
        trg, bad = h.parse_shadow_trigger(row, h.TRIGGER_VARIANT)
        if trg is not None:
            return "keep", "attempt"
        return "keep", bad

    def _window(self, keep: str, reason: str) -> tuple[str, str]:
        self.window_counts[reason] = self.window_counts.get(reason, 0) + 1
        if reason in (REFUSAL_LOOK2, REFUSAL_S0_WINDOW):
            self._alert(reason, "", count=self.window_counts[reason])
        return keep, reason

    @staticmethod
    def _slice_format_ok(row: dict[str, Any]) -> bool:
        """The executor's own v2 reader accepts this record (same conditions as on_boost_slice, which logs v2_slice_ignored otherwise)."""
        ii = h._is_int
        return (isinstance(row.get("mint"), str) and bool(row.get("mint")) and ii(row.get("slot")) and (ii(row.get("tx_index")) or isinstance(row.get("signature"), str))
                and ii(row.get("event_index")) and ii(row.get("t_recv_ms")) and ii(row.get("lamports")) and row["lamports"] > 0
                and row.get("boost_src") in h.V2_BOOST_SRC and isinstance(row.get("pool"), str) and bool(row.get("pool")))

    def intent_tick(self) -> int:
        orig = h.tail_lines

        def screened(path: Path, st: Any) -> list[str]:
            kept = []
            for line in orig(path, st):
                try:
                    row = json.loads(line)
                except ValueError:
                    kept.append(line)
                    continue
                if isinstance(row, dict):
                    keep, _v = self.decide_row(row)
                    if keep == "drop" or keep == "keep_dup":
                        continue
                kept.append(line)
            return kept

        self.a3_tick(self.now_ms())
        self._ladder_guarded(self._ladder_pre_buy, self.now_ms())  # before this tick's buys (QP #862 condition 2)
        h.tail_lines = screened  # swapped for this call only; the loop is single-threaded
        try:
            return super().intent_tick()
        finally:
            h.tail_lines = orig

    # -- v2 slices pass through the base reader; the count above only gates v2_feed_absent
    # -- fidelity (item 8) and soft-halt hooks (item 14)
    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        if kind == "decision":
            trg = kw.get("pool"), kw.get("trigger_slot")
            kw = {**kw, "bot_id": self.abook.bot_id, "frozen_sha": self.abook.frozen_sha, "trigger_key": [mint, trg[0], trg[1]]}
        elif kind in ("dry_sell", "sell_sent", "exit_landing"):
            kw = {**kw, "bot_id": self.abook.bot_id, "frozen_sha": self.abook.frozen_sha}
        super()._log(kind, mint, **kw)
        if self.gates is not None and kind in ("buy", "sell"):
            if kind == "buy":
                if self.gates.recorder.on_buy(mint, kw, self.now_ms(), self._exit_bound_ms(mint, kw.get("decision_t_ms")), self.h5.stake_lamports):
                    self._latch(H_BUY_UNREADABLE, position_mint=mint, signature=kw.get("signature"))
            else:
                self.gates.recorder.on_sell(mint, kw, self.now_ms())

    def prime_feed_start(self) -> str | None:
        """Called by main() once, before the first tick (never by the replay harness, whose input starts with its start record): verify the feed's newest
        start record through decide_row, exactly as if it had been tailed. A mismatch latches feed_break (hard). None = verified; else why buys stay refused."""
        rec = latest_start_record(self.intents_path)
        if rec is None:
            return "feed_start_record_missing"  # buys stay refused (feed_unverified) until a start record is tailed
        self.decide_row(rec)
        return None if self.feed_verified else "feed_start_record_rejected"

    def _latch(self, name: str, **detail: Any) -> None:
        """The base `_latch` logs `halt_latched` through `_log(kind, mint, **kw)`, so a detail key called `mint` collides with its positional argument and the
        latch raises TypeError (every arena halt passed mint=...: H_STRAND, H_STUCK2, H_LATE_SEND, H_LANDING, H_ENTRY_GAP never latched). Renamed once, here."""
        if "mint" in detail:
            detail["position_mint"] = detail.pop("mint")
        super()._latch(name, **detail)

    def _latch_name(self, name: str | None, **detail: Any) -> None:
        if name:
            self._latch(name, **detail)

    def a3_tick(self, now: int) -> None:
        """Poll the A3 daily record (throttled) and latch H_A3 on any halt flag, pins_changed, program_changed or a record that cannot be read. Live runs
        poll the monitor's default file; a dry run polls only a configured `a3_record_file` (so replays never depend on the host's file)."""
        path = self.cfg_a3_file
        if path is None or now - self._a3_polled_ms < A3_POLL_MS:
            return
        self._a3_polled_ms = now
        why, problem = a3_record_problem(Path(path), now)
        self._a3_refuse = None
        if problem:
            self.latch_external(H_A3, why=problem)
        elif why:
            self.latch_external(H_A3, why="a3_flags", flags=why)
        else:
            latch, refuse = a3_guard_problem(Path(path), now)
            if latch:
                self.latch_external(H_A3, why=latch)
            self._a3_refuse = refuse

    def ingest_exit_gap(self, bps_worse: float, mint: str = "") -> None:
        """One exit gap (bps the live sell received below its paper twin) from the parity join; the rolling-free check of FidelityMonitor decides."""
        name = self.monitor.note_exit_gap(bps_worse)
        if name:
            self.latch_external(name, mint=mint, n=len(self.monitor.exit_gaps))

    def write_exit_gaps(self) -> None:
        """Live: append the exit gap of every newly closed eligible trip (live sell price against its priced twin's exit price) to the executor's own exit-gap file."""
        if self.exit_gap_writer is None or self.gates is None:
            return
        try:
            _n, problems = self.exit_gap_writer.write(self.gates.recorder.trips, self.gates.twins_file, self.now_ms())
        except OSError:
            self.latch_external(H_EXIT_GAP, why="exit_gap_writer_failed")  # fail closed: an exit we cannot record is not a clear
            return
        if problems:  # an exit that cannot be measured is never skipped silently (QP #847 conditions 1, 4)
            why, trip_id = problems[0]
            self.latch_external(H_EXIT_GAP, why=why, trip_id=trip_id, n_problems=len(problems))

    def exit_gap_tick(self) -> None:
        """Tail the parity join's exit-gap file (`exit_gap_file`, JSON lines {"mint", "exit_gap_bps"}); each new line goes through ingest_exit_gap."""
        path = self.cfg_exit_gap_file
        if path is None:
            return
        try:
            data = Path(path).read_bytes()
        except OSError:
            self.latch_external(H_EXIT_GAP, why="exit_gap_file_unreadable")  # fail closed: a configured input that cannot be read is not a clear
            return
        if len(data) < self._exit_gap_off:  # rotated / truncated: start again from the top and say so
            self._log("exit_gap_file_reset", "", old_offset=self._exit_gap_off, new_size=len(data))
            self._exit_gap_off = 0
        chunk = data[self._exit_gap_off:]
        end = chunk.rfind(b"\n") + 1  # only complete lines; a partial last line waits for its newline
        self._exit_gap_off += end
        for line in chunk[:end].decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                bps = row["exit_gap_bps"]
            except (ValueError, KeyError, TypeError):
                bps = None
            if isinstance(bps, bool) or not isinstance(bps, (int, float)) or not math.isfinite(bps):
                # a row that cannot be read is a parity input we cannot see: latch (QP 10-23: bad rows used to alert only, and a non-numeric bps was dropped silently)
                self._alert("exit_gap_row_unreadable", "")
                self.latch_external(H_EXIT_GAP, why="exit_gap_row_bad", row=line[:120])
                continue
            self.ingest_exit_gap(float(bps), str(row.get("mint") or ""))

    def latch_external(self, name: str, **detail: Any) -> None:
        """For the checks that live outside this process (the parity join's exit gap, A3 pins_changed / program_changed)."""
        if name not in ARENA_HALTS:
            raise ValueError(f"{name!r} is not an arena halt")
        self._latch(name, **detail)

    def _fire_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        self._latch_name(self.monitor.note_sell_send(pos["h5"]["plan"]["send_slot"], est), mint=mint, est_slot=est)
        super()._fire_sell(mint, pos, est, emergency)

    def exit_tick(self, now: int) -> None:
        self.a3_tick(now)
        self.write_exit_gaps()
        self.exit_gap_tick()
        if self.gates is not None:
            self.gates.maybe_run_parity(now)
        super().exit_tick(now)
        for mint, pos in self.state.open.items():
            if pos.get("stuck") and mint not in self.monitor.stuck:
                self._latch_name(self.monitor.note_stuck(mint), mint=mint)
            # strand = the position cannot be closed: the sell has failed to the point of being stuck AND the deadline's emergency sell was already tried
            # (or the base class gave the position up). Set here, where the base class has just updated `stuck` / `abandoned` / `emergency_attempted`.
            if pos.get("stuck") and (pos.get("emergency_attempted") or pos.get("abandoned")) and not pos.get("strand"):
                pos["strand"] = True
                self.save()
            if pos.get("abandoned") and not pos.get("strand"):
                pos["strand"] = True
                self.save()
            if pos.get("close_blocked") or pos.get("strand"):
                self._latch(H_STRAND, mint=mint, close_blocked=bool(pos.get("close_blocked")), strand=bool(pos.get("strand")), why=pos.get("close_blocked_why"))
        self._ladder_guarded(self.ladder_tick, now)  # after every exit of this tick, and guarded: a ladder error never skips an exit (QP #862 condition 1)

    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        super()._finish_buy(mint, p, m)
        pos = self.state.open.get(mint)
        if pos is not None and not self.dry_run:
            # Off the hot path, right after the buy landed: look at the mint again. A freeze authority or an extension added since the pre-send screen
            # makes this position close-blocked (it cannot be sold and closed); exit_tick latches H_STRAND on it.
            why = classify_record(mint_screen_record(self.rpc, mint))
            self.mint_screens[mint] = why
            if why:
                pos["close_blocked"], pos["close_blocked_why"] = True, why
                self.save()
        self._latch_name(self.monitor.note_attempt(pos is not None), mint=mint)
        exp = int(p.get("q_tokens") or 0)
        if pos is not None and exp > 0:
            self._latch_name(self.monitor.note_entry_gap(entry_gap_bps(exp, int(pos.get("tokens") or 0))), mint=mint)

    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        super()._resolve_expired(mint, p)
        if p.get("kind") == "buy":
            self._latch_name(self.monitor.note_attempt(False), mint=mint)


_ARENA_CLASS = ArenaExecutor  # the class the tripwire inspects (a name bound at import, so rebinding ArenaExecutor cannot hide a removed override)
GUARD_METHODS = ("h5", "_tier_path", "_refresh_tier", "_ladder_buy_refusal", "ladder_tick", "_ladder_start_problem", "_ladder_pre_buy", "_ladder_guarded", "_unlock_still_valid", "daily_loss_now", "_refresh_marks", "_live_gate", "prime_feed_start", "_live_buy", "_send", "_send_guarded", "_note_send", "write_exit_gaps", "_rescue_problem", "_budget_stop", "_balance_refusal", "committed_loss_refusal", "_hard_refusal",
                 "_parity_state", "decide_row", "intent_tick", "_latch", "exit_tick", "_fire_sell", "_finish_buy", "_resolve_expired", "_seal_reason")


def _refused(why: str) -> int:
    print(f"h5_arena_executor ALERT startup_refused {why}", flush=True)
    return 2


def live_status(cfg: dict[str, Any], root: Path | None = None) -> str:
    """Keyless, read-only: the first live-start refusal this config would get now (or "none"), and the file facts behind it. Opens no key and no RPC."""
    book = BOOKS.get(cfg.get("book_id"))  # type: ignore[arg-type]
    if book is None:
        return "live_start=book_id_unknown"
    why = h.start_refusal(cfg, root) or arena_start_refusal(book, cfg, root or h.repo_root(), live=True)
    tier, tp = h.read_tier(ARENA_TIER_PATH, root_checks=True)
    return (f"live_start={why or 'none'} book={book.bot_id} live_built={LIVE_BUILT} feed_note_merged={FEED_NOTE_MERGED} "
            f"arena_live_ok={arena_live_ok_refusal(book.bot_id) or 'valid'} ({ARENA_LIVE_OK_PATH}) shared_live_ok={h.live_ok_valid() or 'valid'} "
            f"arena_tier={tier if tp is None else 'problem:' + tp} c0_guard={c0_guard_problem() or 'ok'} sell_pin={sell_pin_problem() or 'ok'}")


def main(argv: list[str] | None = None, *, rpc_factory: Callable[[str | None, str, bool, float], Any] | None = None) -> int:
    """The entry point. Dry run by default (keyless, public payer). Live needs ALL of: config "mode": "live", --live, and every keyless check of the
    module docstring, in that order, BEFORE the key is read; the key comes only from the systemd credential (tools.probe_live.load_probe_key)."""
    ap = argparse.ArgumentParser(description="H5 arena executor (dry run by default; live needs config mode AND --live AND every C0 check)")
    ap.add_argument("--config", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: build and simulate, never sign or send")
    g.add_argument("--live", action="store_true", help='also needs config "mode": "live", both LIVE_OK files, TIER T1, the canary DEC line, FEED_NOTE_MERGED')
    ap.add_argument("--once", action="store_true", help="one tick, then exit")
    ap.add_argument("--status", action="store_true", help="keyless: print the live-start check line and exit (no key, no RPC)")
    ap.add_argument("--env-file", default=h.sim.DEFAULT_ENV_FILE)
    ap.add_argument("--rpc-env", metavar="VARNAME", help="dry run only: the NAME of an environment variable holding the RPC URL. Refused with --live")
    args = ap.parse_args(argv)
    if args.rpc_env and args.live:
        ap.error("--rpc-env is allowed with --dry-run only: live reads HELIUS_API_KEY from the environment and never takes a URL")
    if args.rpc_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.rpc_env):
        ap.error("--rpc-env takes the NAME of an environment variable, not a URL")
    try:
        cfg = json.loads(Path(args.config).read_text())
    except (OSError, ValueError):
        return _refused("config_unreadable")
    if not isinstance(cfg, dict):
        return _refused("config_not_an_object")
    book = BOOKS.get(cfg.get("book_id"))  # type: ignore[arg-type]
    if book is None:
        return _refused("book_id_unknown")
    if args.status:
        print(live_status(cfg), flush=True)
        return 0
    try:
        h.H5Limits.from_config(cfg)
        mode, warn = h.pe.resolve_mode(cfg.get("mode", h.DRYRUN), args.live and not args.dry_run)
    except (ValueError, SystemExit) as exc:
        return _refused(f"config_refused ({exc})")
    if args.live and mode != h.LIVE:
        return _refused("live_flag_without_config_mode_live")  # a canary never falls back to a silent dry run under a live unit
    if warn:
        print(f"h5_arena_executor WARNING {warn}", flush=True)
    make_rpc = rpc_factory or (lambda url, env_file, use_env_file, rps: h.pe.LimitedRpc(h.pe.ProbeRpc(h.sim.load_rpc_url(url, env_file, use_env_file=use_env_file)),
                                                                                         rps=rps, max_rps=h.H5_MAX_RPS))
    rps = float(cfg.get("rps", 8.0))
    lock_fd = h.acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")  # the base executor's lock name: never two executors on one state dir
    url: str | None = None
    try:
        if mode == h.LIVE:
            why = h.start_refusal(cfg) or arena_start_refusal(book, cfg, h.repo_root(), live=True)  # arena_start_refusal runs live_entry_refusal first
            if why:
                return _refused(why)
            rc = h.pe.startup_rpc_env_check(True)
            if rc:
                return rc
            h.pl.harden_process()  # before the key is read
            if "key_path" in cfg:
                return _refused("key_path_set")  # the key comes from the systemd credential only
            if not (os.environ.get("HELIUS_API_KEY") or "").strip():
                return _refused("rpc_key_missing")
            kp = h.pl.load_probe_key()
            rpc = make_rpc(None, args.env_file, False, rps)
            try:
                ex = ArenaExecutor(rpc, cfg, kp)
            except SystemExit as exc:  # the constructor's own refusals (funded balance, re-run start checks): exit 2, not a restart loop
                return _refused(str(exc).replace("arena executor start refused: ", ""))
        else:
            why = arena_start_refusal(book, cfg, h.repo_root(), live=False)
            if why:
                return _refused(why)
            url = (os.environ.get(args.rpc_env) or "").strip() if args.rpc_env else None
            if args.rpc_env and not url:
                return _refused(f"rpc_env_unset:{args.rpc_env}")
            rpc = make_rpc(url, args.env_file, True, rps)
            try:
                ex = ArenaExecutor(rpc, cfg, None, rpc_label=h.url_label(url))
            except SystemExit as exc:
                return _refused(str(exc).replace("arena executor start refused: ", ""))
        shown = f" rpc={h.url_label(url)}" if url else ""
        print(f"h5_arena_executor mode={ex.run_mode} book={book.bot_id} frozen_sha={book.frozen_sha} user={ex.user}{shown} paths={ex.paths} limits={ex.h5}", flush=True)
        feed = ex.prime_feed_start()
        print(f"h5_arena_executor feed_start={feed or 'verified'} start_ms={ex._start_ms}", flush=True)  # not verified: every trigger is refused feed_unverified
        if args.once:
            ex.tick()
            return 0
        ex.run_loop()
        return 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
