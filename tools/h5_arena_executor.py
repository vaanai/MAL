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
FEED_NOTE_MERGED is False: until the EXP-024 declared-observation note for this executor is merged (scope item 17) the module reads no live feed.
It only runs with config "replay_only": true (a fixture or replay dir). Flipping the constant is a code change and needs the md5 replay rerun.

Not proven offline (see the PR body): decision equivalence on the real 2026-09-20 day (tools/h5_arena_exec_replay.py is the harness; the run is a host job),
the live landing/exit gaps, the A3 pins_changed link, the Token-2022 screen on real mints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import base64
from datetime import datetime, timezone

from tools import h5_executor as h
from tools import mint_account_fetch as mf
from tools.arena_close_blocked import classify_record

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
FEED_NOTE_MERGED = False  # scope item 17: the EXP-024 note declaring this executor's observation. False = replay_only
LIVE_BUILT = False  # this PR builds no live entry point
C0_TIER = {  # DEC-032 s3, the C0 canary table, PINNED in code per canary book. The shared /etc/mal-h5/TIER (T0/T1/T2) and the config may only LOWER these.
    "stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000,
}
FUNDED_BALANCE_TOLERANCE_LAMPORTS = 10_000_000  # 0.01 SOL: a live start needs the FINALIZED balance within this of funded_lamports (minus realized loss on a restart)
A3_MAX_AGE_MS = 36 * 3_600_000  # the monitor writes daily; a newest record older than 36 h is stale = fail closed
FUNDED_MIN_LAMPORTS, FUNDED_MAX_LAMPORTS = 350_000_000, 1_000_000_000  # DEC-032 s3: "Wallet needed: funded 0.35 to 1.00 SOL"
FLOOR_BELOW_FUNDED_LAMPORTS = 250_000_000  # DEC-032 s3: floor = max(0.05 SOL, funded - 0.25 SOL)
A3_RECORD_DEFAULT = "/data/mal/structure-monitor/daily.jsonl"  # tools/pump_structure_monitor.py DEFAULT_OUT: one JSON record per line, the newest is the state
A3_POLL_MS = 60_000
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
H_A3 = "a3_pins_changed"
ARENA_HALTS = frozenset({H_FEED_BREAK, H_ENTRY_GAP, H_LANDING, H_EXIT_GAP, H_LATE_SEND, H_STUCK2, H_STRAND, H_A3})
ENTRY_GAP_WINDOW = 20  # DEC-032 s5 P1: the first 10 eligible fills, then ROLLING over the last 20
LANDING_WINDOW_N = 30  # DEC-032 s5 P2: rolling 30 buy attempts, judged once 30 exist
ENTRY_GAP_BPS, ENTRY_GAP_MIN_N = 200.0, 10
EXIT_GAP_BPS, EXIT_GAP_MIN_N = 200.0, 10
LANDING_EXCESS_PP, LANDING_MIN_N = 10.0, 30
LATE_SEND_SLOTS, LATE_SEND_TRIPS = 3, 2
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
    if not FEED_NOTE_MERGED and cfg.get("replay_only") is not True:
        return "feed_note_unmerged_replay_only"
    if live and not LIVE_BUILT:
        return "live_not_built"
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
        return canary_record_refusal(book.bot_id, root)
    if book.exit_pin == h.EXIT_MODE_V2:
        return v2_start_refusal()
    return None


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
        if now_ms - run_ms > A3_MAX_AGE_MS:
            return [], "a3_record_stale"
    # a flag counts when it is set, malformed, or NOT evaluated (halt false with evaluated false is "unknown", not "clear")
    why = sorted(k for k, v in flags.items() if not (isinstance(v, dict) and isinstance(v.get("halt"), bool)) or v["halt"] or v.get("evaluated") is False)
    pc = (((rec.get("warn") or {}).get("flags") or {}).get("program_changed")) if isinstance(rec.get("warn"), dict) else None
    if isinstance(pc, dict) and pc.get("warn") is True:
        why.append("program_changed")
    return why, None


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
        super().__init__(rpc, cfg, keypair, now_ms=now_ms, pick_oracle=None, root=root, rpc_label=rpc_label)
        if self.exit_mode != book.exit_pin:
            raise SystemExit("exit mode pin was not applied")
        if keypair is not None:
            why = self._funded_balance_problem()
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
        # C0 (gap a): the pinned table is the ceiling whatever the shared TIER file says. T1 / T2 in the file cannot raise a single field; a lower tier
        # (T0) or a tighter config still lowers it, because this takes the minimum.
        return replace(lim, gap_hold_ms=max(lim.gap_hold_ms, GAP_HOLD_MS),
                       wallet_floor_lamports=wallet_floor_lamports(getattr(self, "funded_lamports", None), lim.wallet_floor_lamports),
                       **{k: min(getattr(lim, k), v) for k, v in C0_TIER.items()})

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
        why = self.committed_loss_refusal() or self._balance_refusal(self.now_ms())  # the last look before anything is built or signed
        if why:
            return self._refuse(trg, why)
        blocked = self._screen_mint(trg.mint)  # DEC-032 s3 "Venue": a freeze authority or a Token-2022 extension outside the allowlist is a strand = total loss
        if blocked:
            self.close_blocked_refused += 1
            return self._refuse(trg, f"close_blocked_mint:{blocked}")
        super()._live_buy(trg, ps, terms, plan, now, drift, seen_ms)

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
        if problem:
            self.latch_external(H_A3, why=problem)
        elif why:
            self.latch_external(H_A3, why="a3_flags", flags=why)

    def ingest_exit_gap(self, bps_worse: float, mint: str = "") -> None:
        """One exit gap (bps the live sell received below its paper twin) from the parity join; the rolling-free check of FidelityMonitor decides."""
        name = self.monitor.note_exit_gap(bps_worse)
        if name:
            self.latch_external(name, mint=mint, n=len(self.monitor.exit_gaps))

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
            try:
                row = json.loads(line)
                bps = row["exit_gap_bps"]
            except (ValueError, KeyError, TypeError):
                self._alert("exit_gap_row_unreadable", "")
                continue
            if isinstance(bps, (int, float)) and not isinstance(bps, bool):
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
        self.exit_gap_tick()
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H5 arena executor (DRY ONLY in this build; no live entry point)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true", help="refused: live is not built (needs the canary DEC and the owner's OK)")
    args = ap.parse_args(argv)
    if args.live:
        print("h5_arena_executor ALERT startup_refused live_not_built", flush=True)
        return 2
    cfg = json.loads(Path(args.config).read_text())
    why = arena_start_refusal(BOOKS[cfg["book_id"]], cfg, h.repo_root(), live=False) if cfg.get("book_id") in BOOKS else "book_id_unknown"
    if why:
        print(f"h5_arena_executor ALERT startup_refused {why}", flush=True)
        return 2
    print("h5_arena_executor: no network entry point in this build (replay and tests construct ArenaExecutor with a stub RPC)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
