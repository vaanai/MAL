"""Forward paper arena: fill and cost engine (hunt20). PAPER ONLY. No key, no transaction, no RPC.

Bots call `ArenaEngine.buy(...)` / `.sell(...)`. The engine reads feed rows (the `tools/arena_feed.py` tape shape: `type` trade | gap, `prog`, `slot`,
`t_recv_ms`, `signature`, `event_index`, plus the decoded trade fields) with `.on_row(row)`, fills each decision from the tape and writes write-once ledger rows
under `/data/mal/arena/ledger/<bot_id>/trades-<UTC day of the entry decision>.jsonl`:
  kind "open"      written by buy() at the decision instant, BEFORE any pricing (one per position; recoverable after a restart with `recover()`)
  kind "trade"     one per (position, stake) after the sell is priced
  kind "excluded"  one per (position, stake) that the feed-gap / missing-V / engine-restart rule removes (counted by the scorer, never priced)

BINDING STACK (DEC-031 revision 2, section 1.4). Nothing here re-derives the lab's cost model. It is imported from `tools.arena_stack`, a byte-for-byte VENDORED copy of the needed logic of tools/r4b_book.py
and tools/r4b_frozen.py (blob shas in its header; no arena process may open or use tools/r4b_*.py until EXP-027's read, DEC-031 section 3.1):
  * `tools.arena_stack.mint_states`   entry / exit pool states, spot, same-slot buys, nearby buy SOL (the prep_l3 extraction behind sc.py's panel)
  * `tools.arena_stack.round_trip`    PumpSwap round trip: V in Q, fee tiers, own buy re-injected into the exit state, +26.08 bps / -16 bps haircut,
                                   505,000 lamports per send, rent 2,039,280 per filled trip, 1.15 guard (a guarded buy pays one send)
  * `tools.arena_stack.fail_legs`     flat 15% and pressure-fail (slope scale 1; intercept refit so mean p = 0.289) legs
  * `tools.h5_shadow.SlotClock`    the rolling slot clock (`--sps rolling`)
  * `tools.paper_curve_math`       the bonding-curve quote_buy / quote_sell / reserves_with_our_buy (the curve sim of tools/latency_curve.py)
  * STRAND CLASS: an unpriced or invalid exit (r4b_book entry_invalid / exit_invalid C2, C7, C8), or proceeds below one send, is a TOTAL loss of stake plus the
    buy send plus rent (-stake - 505,000 - 2,039,280) on every leg. It bypasses the fail blend (flat = pressure = the loss) and is never dropped.
  * E1 FLOOR: per filled trip the stack carries max(haircut, 384,022 lamports per 0.05 SOL of stake) (DEC-021:54, Amendment 7 option B), scaled linearly.
  * LATENCY starts at the instant the decision is WRITTEN: that instant is mapped to the tape slot clock (the latest feed row's (slot, t_recv_ms) plus elapsed time / sps) and
    the fill lands at that slot + k, k = ceil(latency / sps - 1e-9) (`k_rounding="round"` is the EXP-027 QP value). The instant is the WRITE instant, max(the bot's decision_ms, the wall clock at the buy()/sell() call, t_recv_ms of the latest feed row
    the engine has seen): a stale stamp is priced at the write instant, never refused; a stamp in the future is refused; the clock check uses the WRITE instant too: a decision stamped before the book's clock but WRITTEN after it counts (its stamp is logged in `stamp_ms`,
    with the write lag), one written before the clock is refused. Exit lag = ceil(0.55 s / sps), at least 2 slots. t_recv_ms lags block time
    by the feed delay, so the mapped slot is a lower bound of the chain's slot at the decision instant (measured once by tools/arena_feed_delay.py; the whole-slot delay d is `StackConfig.feed_delay_slots`, REQUIRED, no default: the engine refuses to price while it is unset, and adds d to the entry decision slot and to the exit decision slot; rows record the uncorrected and the corrected slots; the guard's reference is the price at the engine's own feed slot at the call (call_feed_slot), never at a slot the bot names).
  * FEED GAPS: a trade whose decision-to-exit span holds a logged data_loss gap of its program, or a needed print without V, or a restart of this engine, is
    written as an "excluded" row with the reason. A derived_last exit after a gap falls under the gap rule. The scorer counts them (> 5% of a book -> NOT_DECIDABLE).
    Contract: the feed emits a gap row before the row that revealed it. The engine does not rely on that alone: a feed `start` row, a gap row of kind process_restart / feed_restart, a slot jump (gap_slots) or silence (gap_silence_ms) between consecutive trade rows is a data_loss gap from the last row seen, for open positions.

Conventions (confirmed against sc.py / r4b_book):
  * PumpSwap rows carry PRE-trade reserves. A fill lands on END-bound state: the PRE-trade reserves of the FIRST print with slot >= landing slot + 1,
    i.e. the pool after every print in the landing slot. If no such print exists yet the state after the last print is derived (G's 1.25% constant,
    as mint_states does) once the global slot clock is `grace_slots` past it; the row records `state_src`.
  * Bonding rows carry POST-trade reserves: the fill lands on the POST-trade reserves of the LAST print with slot <= landing slot.
  * Within-slot order (decides "first print at or after landing + 1" and "last print at or before landing"): by (slot, tx_index, event_index, signature) when the
    row carries tx_index (the exploration tape), else by (slot, t_recv_ms, signature, event_index) (the live arena tape has no tx_index). Receive order
    approximates chain order within a slot; it is the best the live row shape allows and is disclosed.
  * Spot for the guard = post-trade price of the last print with slot < decision slot (mint_states).
  * Bonding fills have no portal fee (direct route); the haircut was measured on PumpSwap and is applied to them as a conservative assumption.

`StackConfig(strand_rule="sc", e1_floor_per_005=0)` is the LEGACY stack of sc.py / VERIFY (no strand class, no floor); it exists only for the bit-parity
check against sc.py. Defaults are the binding stack: k_rounding ceil, rent charged, strand_rule "both", E1 floor on, grace_slots 10,000; feed_delay_slots has NO default. A quant-proof config ruling may change
one of them; they are config and every row records them.

Not supported in v1: partial sells (`frac` must be 1.0; refused), sells decided before the slower entry leg lands (refused).
"""

from __future__ import annotations

import bisect
import errno
import fcntl
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from tools import arena_stack as F
from tools.h5_shadow import SPS_LO, SlotClock, sps_ok
from tools.paper_curve_math import market_cap_sol, quote_buy, quote_sell, reserves_with_our_buy
from tools.arena_stack import fail_legs, mint_states, round_trip

SCHEMA = "arena_trade_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
ARENA_ROOT = "/data/mal/arena"
LEDGER_ROOT = f"{ARENA_ROOT}/ledger"
REGISTRY_PATH = f"{ARENA_ROOT}/bots.json"

STAKES_SOL = (0.25, 0.5, 1.0)
LATS = tuple(F.LATENCIES_S)  # (1.3, 1.9)
LAT_TAG = {1.3: "1p3", 1.9: "1p9"}
LEG_KEYS = ("flat_1p3", "flat_1p9", "press_1p3", "press_1p9")
SEND_LAMPORTS = F.SEND_FEE_DECIDING  # 505,000
RENT_LAMPORTS = F.RENT_REPORT  # 2,039,280 per filled trip (the brief)
GRACE_SLOTS = 10_000  # about 1 h: the state is the tape print the sim uses; derive from the last print only past this (QP ruling on #708)
HIST_KEEP_SLOTS = 20_000
# ArenaEngine.trim_history() keeps, for a pool with NO position, the last print before the cut and every print from the cut on; cut = hw_slot - TRIM_KEEP_SLOTS.
# Why that is every print a later pricing call can read (a pool with a position is never trimmed; it keeps all its prints):
#   * Every pricing read is anchored at a LATER buy()'s slots, never at an earlier one: the guard spot is the last print before call_feed_slot (= hw_slot at that
#     call, which never falls); the entry legs land at sd + d + k (d >= 0, k >= 0, k from the 1.3 s / 1.9 s legs) and read the last print <= x (bonding) or the first
#     print > x (PumpSwap); ssb is the buys in slot x; nearby is the buy lamports over [x - round(NEARBY_WINDOW_S / sps), x]; the exit state and V sources read
#     slot >= sd, forward only; GRACE_SLOTS only delays the exit read forward. The deepest look-back below the entry slot is therefore the nearby window,
#     round(2 s / sps) <= TRIM_LOOKBACK_SLOTS = round(NEARBY_WINDOW_S / SPS_LO) = 13 slots (sps_ok means sps > SPS_LO), and the guard spot needs one print before the cut.
#   * What the engine does NOT bound is how far sd can sit behind hw_slot: sd = max(decision_slot, slot_at(instant)) and slot_at >= hw_ms_slot, the slot of the
#     newest-RECEIVED row, which trails the highest slot seen by the feed's disorder (a caller that passes the highest slot seen as decision_slot, as HV4 and
#     arena_rules do, has sd ~ hw_slot and never comes near the floor). Measured on the 9 closed arena hours 2026-10-19T08..T16 (8,095,621 trade rows): at most 928 slots; late
#     duplicates arrive up to 898 slots behind. TRIM_KEEP_SLOTS is 5,000: 5.4 times the largest disorder seen, plus the 13-slot window.
#   * The assumption is checked, not trusted: buy() refuses (as no_prints, counted as refused_history_trimmed) when sd - nearby would reach below the pool's
#     trim floor, and a row below the floor is dropped as the duplicate it nearly always is (counted rows_below_trim_floor next to rows_dup). A refused buy is a
#     counted data refusal; the trim never prices from a hole. A NONZERO rows_below_trim_floor or any history_trimmed refusal VOIDS the equivalence with an
#     untrimmed engine for every book on this engine (a dropped new print would have moved hw_ms / hw_ms_slot, the clock, late_print rows and the anchor).
TRIM_LOOKBACK_SLOTS = int(round(F.NEARBY_WINDOW_S / SPS_LO))
TRIM_KEEP_SLOTS = 5_000
LATE_KEEP_SLOTS = 2_000  # a priced position is watched for late prints this many slots past its need slot
# Provisional pressure intercept stored in the ledger's press_* net_lamports. The AUTHORITATIVE pressure leg is refit per (bot, stake, latency) book by
# arena_score (the stack refits the intercept per cell so that mean p over the cell's filled sends is 0.289). Fit on the 300-case parity book (1.3 s).
E1_FLOOR_PER_005 = 384_022  # DEC-021:54 pre-seal n = 8 live-minus-sim lamports per 0.05 SOL of stake; scaled linearly (Amendment 7 option B)
PRESS_INTERCEPT_PROVISIONAL = -1.4197242838379789

VENUE_ALIASES = {"pumpswap": "pumpswap", "pump_amm": "pumpswap", "pump_bonding": "pump_bonding", "pump": "pump_bonding", "pumpfun": "pump_bonding", "bonding": "pump_bonding"}
V_BAND_MAX = 30_000_000_000


class ArenaRefused(Exception):
    """A request the arena will not price (unregistered bot, no slot clock, unknown pool, partial sell, ...)."""


class LedgerCorrupt(Exception):
    """A ledger file does not end in a newline (a torn write): nothing is appended until a person has looked at it."""


class LedgerWriteFailed(LedgerCorrupt):
    """An append that could not be made durable: an OSError on the write, the fsync or anything else it touches (ENOSPC, EIO, ...), a SHORT write, or the free-space
    floor (the disk is below the configured free fraction). It is a LedgerCorrupt, so every handler of the damaged-file case takes it, and the engine HALTS the bot
    (a disk that cannot take a row will not take the next one either). `kind` is the incident kind: write_failed, short_write, fsync_failed, io_error, disk_floor.
    A raw OSError never escapes `Ledger.append`."""

    def __init__(self, msg: str, kind: str) -> None:
        super().__init__(msg)
        self.kind = kind


class LedgerConflict(Exception):
    """A trade_id already in the ledger with different content."""


# ------------------------------------------------------------------------------------------------------------------------------ registry
def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def canon(obj: Any) -> str:
    """Strict, canonical JSON (no NaN / Infinity, sorted keys)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000.0, timezone.utc).strftime("%Y-%m-%d")


def line_bytes(row: Mapping[str, Any]) -> bytes:
    """Canonical ledger line. A row with a top-level "kind" is written with it FIRST, so that `line.startswith(b'{"kind":"open"')` is exact: a bot's own
    meta (nested) cannot forge a decision row for the outcome-blind count or the daily digest."""
    if "kind" not in row:
        return canon(row).encode()
    rest = {k: v for k, v in row.items() if k != "kind"}
    head = '{"kind":' + json.dumps(row["kind"], allow_nan=False)
    return (head + ("," + canon(rest)[1:] if rest else "}")).encode()


def identity(row: Mapping[str, Any]) -> str:
    """Identity of a ledger row for the write-once rule: everything except written_ms (the instant it was written, which a retry changes)."""
    return hashlib.sha256(line_bytes({k: v for k, v in row.items() if k != "written_ms"})).hexdigest()


def verify_sha_on_origin(sha: str, repo: str | os.PathLike | None = None) -> str:
    """DEC-031 section 1.2: frozen_sha is a commit whose code is ON MAIN. git fetch origin, resolve `<sha>^{commit}` to the full 40-hex sha, require
    `git merge-base --is-ancestor <full> origin/main`. Returns the full sha; refuses otherwise. No git output is echoed (a remote URL can carry a credential)."""
    r = str(repo or REPO_ROOT)

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", r, *args], capture_output=True, text=True, timeout=300)

    f = git("fetch", "--quiet", "origin")
    if f.returncode != 0:
        raise ArenaRefused(f"cannot verify frozen_sha: git fetch origin failed (exit {f.returncode})")
    p = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    full = p.stdout.strip()
    if p.returncode != 0 or len(full) != 40 or not all(c in "0123456789abcdef" for c in full):
        raise ArenaRefused(f"frozen_sha {sha} is not a (unique) commit after fetch")
    if git("merge-base", "--is-ancestor", full, "origin/main").returncode != 0:
        raise ArenaRefused(f"frozen_sha {full} is not on origin/main: the frozen code must be merged first")
    return full


def process_code_state(repo: str | os.PathLike | None = None) -> tuple[str | None, bool | None]:
    """(HEAD full sha, dirty) of the git checkout this process runs from: the frozen code is bound to the process (DEC-031 section 1.2). dirty = any tracked
    change or any untracked file that is not ignored (`git status --porcelain`). (None, None) when git cannot say; the scorer refuses such a row."""
    r = str(repo or REPO_ROOT)
    try:
        h = subprocess.run(["git", "-C", r, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60)
        st = subprocess.run(["git", "-C", r, "status", "--porcelain"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    head = h.stdout.strip()
    if h.returncode != 0 or st.returncode != 0 or len(head) != 40 or not all(c in "0123456789abcdef" for c in head):
        return None, None
    return head, bool(st.stdout.strip())


def _append_line(path: Path, obj: Mapping[str, Any]) -> None:
    """Append one strict-JSON line (flock, O_APPEND, fsync of the file and, on creation, of the directory)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, canon(obj).encode() + b"\n")
        os.fsync(fd)
        if created:
            _fsync_dir(path.parent)
    finally:
        os.close(fd)


def load_registry(path: str | os.PathLike = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    """bots.json is APPEND-ONLY JSON lines: {"kind":"register", bot_id, frozen_sha, clock_start_ms, registered_at_ms, deciding_stake, model_ids} and
    {"kind":"stop", bot_id, stopped_at_ms}. No line is ever removed; a stopped book stays listed (stopped_at_ms) and counts in every multiplicity."""
    p = Path(path)
    out: dict[str, dict[str, Any]] = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "register":
            out[r["bot_id"]] = {k: v for k, v in r.items() if k != "kind"} | {"stopped_at_ms": None}
        elif r["kind"] == "stop" and r["bot_id"] in out:
            out[r["bot_id"]]["stopped_at_ms"] = r["stopped_at_ms"]
    return out


TEST_ENV = "MAL_ARENA_TEST"


def register_bot(bot_id: str, frozen_sha: str, clock_start_ms: int, path: str | os.PathLike = REGISTRY_PATH, *, deciding_stake: float, model_ids: Sequence[str],
                 repo: str | os.PathLike | None = None, now_ms: int | None = None, verify_sha: bool = True) -> dict[str, Any]:
    """Register a book once, before its clock. `deciding_stake` and `model_ids` are REQUIRED (model_ids may be an empty list for a bot that calls no LLM, but
    must be given). The line records registered_at_ms from the real clock, REFUSES a clock_start_ms earlier than it, resolves frozen_sha against origin/main
    (verify_sha_on_origin) and stores the full 40-hex sha as frozen_sha and verified_sha. `now_ms` and `verify_sha=False` exist for tests only: they are refused
    unless MAL_ARENA_TEST=1, and a line written with them says so (time_source test_override, verified_sha null) and is refused by the scorer outside tests.
    The same line again is a no-op; a different sha, clock, stake or models for an existing bot_id is REFUSED (a new version is a new bot_id)."""
    if (now_ms is not None or not verify_sha) and os.environ.get(TEST_ENV) != "1":
        raise ArenaRefused("now_ms / verify_sha=False are test-only overrides: production uses the real clock and always verifies the sha")
    if not bot_id or "/" in bot_id or bot_id.startswith("."):
        raise ArenaRefused(f"bad bot_id {bot_id!r}")
    sha = str(frozen_sha).lower()
    if not sha or not all(c in "0123456789abcdef" for c in sha) or not 7 <= len(sha) <= 40:
        raise ArenaRefused("frozen_sha must be a git hex sha (7 to 40 chars)")
    if deciding_stake not in STAKES_SOL:
        raise ArenaRefused(f"deciding_stake must be one of {STAKES_SOL}")
    if isinstance(model_ids, str) or not all(isinstance(m, str) and m for m in model_ids):
        raise ArenaRefused("model_ids must be a list of non-empty strings")
    reg_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if int(clock_start_ms) < reg_ms:
        raise ArenaRefused(f"clock_start_ms {clock_start_ms} is earlier than the registration instant {reg_ms}: a clock cannot be backdated")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"bot_id": bot_id, "clock_start_ms": int(clock_start_ms), "deciding_stake": float(deciding_stake), "model_ids": list(model_ids)}
    with open(p.with_suffix(p.suffix + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        bots = load_registry(p)
        if bot_id in bots:
            old = bots[bot_id]
            same_sha = old["frozen_sha"].startswith(sha) or sha.startswith(old["frozen_sha"])
            old_view = {k: old.get(k) for k in entry}
            if not same_sha or old_view != entry:
                raise ArenaRefused(f"bot {bot_id} is registered with {old_view} and sha {old['frozen_sha']}; a changed sha, clock, stake or model is a new bot_id, not an edit")
            return old
        full = verify_sha_on_origin(sha, repo) if verify_sha else None
        _append_line(p, dict(entry, kind="register", frozen_sha=full or sha, verified_sha=full, registered_at_ms=reg_ms,
                             time_source="wall_clock" if now_ms is None else "test_override"))
    return load_registry(p)[bot_id]


def stop_bot(bot_id: str, stopped_at_ms: int, path: str | os.PathLike = REGISTRY_PATH) -> None:
    """Append a stop line. The book stays listed and counts in every multiplicity disclosure."""
    p = Path(path)
    with open(p.with_suffix(p.suffix + ".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        bots = load_registry(p)
        if bot_id not in bots:
            raise ArenaRefused(f"unknown bot {bot_id}")
        if bots[bot_id]["stopped_at_ms"] is not None:
            raise ArenaRefused(f"bot {bot_id} is already stopped")
        _append_line(p, {"kind": "stop", "bot_id": bot_id, "stopped_at_ms": int(stopped_at_ms)})


# ------------------------------------------------------------------------------------------------------------------------------ ledger
INCIDENTS_FILE = "incidents.jsonl"
MIN_FREE_PCT_DEFAULT = 25.0  # DEC-031 section 3.8: no append below 25% free on the ledger's filesystem
MIN_FREE_PCT_FLOOR = 20.0  # the configured floor can be raised, never set below this
MAX_UNWRITTEN_INCIDENTS = 1000
log = logging.getLogger("tools.arena_paper")


class Ledger:
    """Write-once JSONL ledger: one file per (bot, UTC day of the entry decision). A trade_id is written once; the same bytes again is a no-op,
    different bytes raise LedgerConflict. Every append is flock'ed, written with O_APPEND and fsync'ed (and the directory on file creation).

    INCIDENTS. Every time `append` refuses because a day file is damaged (it does not end in a newline: a torn write; or a complete line does not parse), it raises
    LedgerCorrupt AND appends one fsync'd line to a SEPARATE per-bot file, `<root>/<bot_id>/incidents.jsonl`: {kind, at_ms, bot_id, day_file, bytes, reason, row_kind,
    trade_id, row_decision_ms}. The refusal is no longer only a counter in the engine's memory: a person can repair the day file, and the attempts that were refused
    in the meantime stay on record. The scorer reads this file and makes the bot's read NOT_DECIDABLE for any incident before the read boundary, repaired or not
    (lost attempts, DEC-014 Amendment 1). The incident file is never rewritten; an unparseable line in it is itself a reason for NOT_DECIDABLE.

    WRITE FAILURES (quant-proof on #716). Nothing that goes wrong on the disk may lose an attempt silently. `append` never lets a raw OSError out: an OSError on the
    write, the fsync (or the directory fsync) or any other step, a SHORT write (fewer bytes than the line reached the file), or a free-space floor breach raises
    LedgerWriteFailed (a LedgerCorrupt) after a best-effort incident line (kinds write_failed, short_write, fsync_failed, io_error, disk_floor). The floor is checked
    before EVERY append: below `min_free_pct` (default 25, never below 20; DEC-031 section 3.8) free on the ledger's filesystem the append is refused and the incident
    is written while there is still room for it. If even the incident line cannot be written it is kept in memory (`unwritten_incidents`, retried by `flush_incidents`
    and before the next incident) and logged at ERROR with the counters `write_failures` / `incident_failures`. A short write leaves a fragment: the file is then
    a torn tail, refused (and flagged by the scorer) until a person has looked at it."""

    def __init__(self, root: str | os.PathLike = LEDGER_ROOT, now_fn: Callable[[], int] | None = None, min_free_pct: float = MIN_FREE_PCT_DEFAULT) -> None:
        # never below 20 in production; a replay / test harness (MAL_ARENA_TEST=1, as for the registry overrides) may lower it, since its ledger is a temp dir
        lowest = 0.0 if os.environ.get(TEST_ENV) == "1" else MIN_FREE_PCT_FLOOR
        if isinstance(min_free_pct, bool) or not isinstance(min_free_pct, (int, float)) or not lowest <= min_free_pct <= 100:
            raise ValueError(f"min_free_pct must be between {lowest:g} and 100 (default {MIN_FREE_PCT_DEFAULT:g}, DEC-031 section 3.8; below {MIN_FREE_PCT_FLOOR:g} is "
                             f"refused outside tests), got {min_free_pct!r}")
        self.min_free_pct = float(min_free_pct)
        self.write_failures = 0  # appends that failed on the disk (OSError, short write, fsync) or were refused by the free-space floor
        self.floor_refusals = 0  # of which: refused by the free-space floor
        self.unwritten_incidents: list[tuple[str, bytes]] = []  # (bot_id, line) incident lines the disk would not take: the in-memory record
        self.root = Path(root)
        # path -> (inode, offset consumed, {trade_id: identity}). An append reads only the bytes written since the last look (by this or another process),
        # so its cost no longer grows with the size of the day file.
        self._cache: dict[str, tuple[int, int, dict[str, str]]] = {}
        self.torn_tail_refusals = 0  # appends refused because the file did not end in a newline
        self.corrupt_refusals = 0  # appends refused because a complete line of the day file does not parse
        self.incident_failures = 0  # incident lines that could NOT be written (the LedgerCorrupt still surfaces, and says so)
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)

    def path_for(self, bot_id: str, day: str) -> Path:
        return self.root / bot_id / f"trades-{day}.jsonl"

    def incidents_path(self, bot_id: str) -> Path:
        return self.root / bot_id / INCIDENTS_FILE

    def incident(self, bot_id: str, kind: str, reason: str, *, path: Path | None = None, **fields: Any) -> bool:
        """Append one incident line (flock, O_APPEND, fsync of the file and, on creation, of the directories). `path` is the day file concerned: its name and its
        current byte size go on the line. If the incident file itself ends in a half line (a torn incident write), a newline is written first so that the new
        incident stands on its own line and the fragment stays visible to the scorer as an unparseable line. Returns False (counts incident_failures, keeps the line in
        `unwritten_incidents` for a retry) when the line could not be written; it never raises, so that the caller's own error still surfaces."""
        size = None
        try:
            if path is not None and path.exists():
                size = path.stat().st_size
        except OSError:
            pass
        line = line_bytes({"kind": kind, "at_ms": int(self.now_fn()), "bot_id": bot_id, "day_file": None if path is None else path.name, "bytes": size,
                           "reason": reason, **fields}) + b"\n"
        if self.unwritten_incidents:  # earlier incidents that did not reach the disk go first, with their own at_ms
            self.flush_incidents()
        ok = self._write_incident_line(bot_id, line)
        if not ok:
            self.incident_failures += 1
            self.unwritten_incidents.append((bot_id, line))
            del self.unwritten_incidents[:-MAX_UNWRITTEN_INCIDENTS]
        log.error("arena ledger incident bot=%s kind=%s reason=%s incident_line_written=%s", bot_id, kind, reason, ok)
        return ok

    def _write_incident_line(self, bot_id: str, line: bytes) -> bool:
        p = self.incidents_path(bot_id)
        try:
            new_dir = not p.parent.exists()
            p.parent.mkdir(parents=True, exist_ok=True)
            created = not p.exists()
            fd = os.open(str(p), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                end = os.fstat(fd).st_size
                if end > 0 and os.pread(fd, 1, end - 1) != b"\n":
                    line = b"\n" + line
                if os.write(fd, line) != len(line):
                    raise OSError(errno.EIO, "short write of an incident line")
                os.fsync(fd)
                if created:
                    _fsync_dir(p.parent)
                if new_dir:
                    _fsync_dir(p.parent.parent)
            finally:
                os.close(fd)
        except OSError:
            return False
        return True

    def flush_incidents(self) -> int:
        """Retry the incident lines that did not reach the disk (oldest first, original at_ms). Returns how many are still unwritten."""
        keep = []
        for bot_id, line in self.unwritten_incidents:
            if keep or not self._write_incident_line(bot_id, line):
                keep.append((bot_id, line))  # order is kept: once one fails, the rest wait
        self.unwritten_incidents = keep
        return len(keep)

    def _ids(self, fd: int, key: str) -> tuple[dict[str, str], int, bool]:
        """(ids, offset, complete). Reads from the cached offset to the end of the file (the whole file when there is no usable cache: a new inode or a
        shorter file) and parses only the new whole lines. complete = the scan consumed every byte (no unterminated tail)."""
        st = os.fstat(fd)
        c = self._cache.get(key)
        ids, off = ({}, 0) if (c is None or c[0] != st.st_ino or c[1] > st.st_size) else (c[2], c[1])
        os.lseek(fd, off, os.SEEK_SET)
        data = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            data += chunk
        cut = data.rfind(b"\n") + 1
        for line in data[:cut].split(b"\n"):
            if line:
                r = json.loads(line)
                ids[r["trade_id"]] = identity(r)
        self._cache[key] = (st.st_ino, off + cut, ids)
        return ids, off + cut, cut == len(data)

    @staticmethod
    def _oserr(exc: OSError) -> str:
        return f"{type(exc).__name__} {errno.errorcode.get(exc.errno or 0, '?')} ({exc.strerror or exc})"

    def _free(self) -> tuple[float, int, int]:
        """(free percent, free bytes, total bytes) of the filesystem the ledger lives on (the nearest existing ancestor of the root)."""
        d = self.root
        while not d.exists() and d != d.parent:
            d = d.parent
        u = shutil.disk_usage(d)
        return (100.0 * u.free / u.total if u.total else 100.0), int(u.free), int(u.total)

    def _fail(self, p: Path, row: Mapping[str, Any], kind: str, reason: str, **extra: Any) -> LedgerCorrupt:
        """Count the failure, write its incident line (outside the damaged / full file) and return the exception for the caller to raise: LedgerCorrupt for a
        damaged file (torn_tail, ledger_corrupt: nothing was written), LedgerWriteFailed for a disk failure or the free-space floor."""
        disk = kind not in ("torn_tail", "ledger_corrupt")
        if kind == "torn_tail":
            self.torn_tail_refusals += 1
        elif kind == "ledger_corrupt":
            self.corrupt_refusals += 1
        else:
            self.write_failures += 1
            self._cache.pop(str(p), None)  # the file's state is no longer known
            if kind == "disk_floor":
                self.floor_refusals += 1
        entry = ((row.get("decision_ms") or {}).get("entry")) if isinstance(row.get("decision_ms"), Mapping) else None
        wrote = self.incident(str(row["bot_id"]), kind, reason, path=p, row_kind=row.get("kind"), trade_id=row.get("trade_id"),
                              row_decision_ms=None if entry is None else int(entry), **extra)
        tail = "" if wrote else " (the incident line could NOT be written either: kept in memory, logged)"
        if not disk:
            return LedgerCorrupt(f"{p.name}: {reason}: refusing to append; nothing was written{tail}")
        return LedgerWriteFailed(f"{p.name}: {reason}: the append failed; this attempt is NOT on record in the ledger (the file may hold a fragment){tail}", kind)

    def append(self, row: Mapping[str, Any]) -> bool:
        """True if written, False if the identical row is already there. Raises LedgerCorrupt (and writes an incident line, see the class doc) when the day file
        is damaged: it does not end in a newline, or a complete line of it does not parse. Raises LedgerWriteFailed (a LedgerCorrupt, with an incident line) when
        the disk fails (any OSError, a short write, an fsync error) or the free-space floor is breached; a raw OSError never escapes."""
        line = line_bytes(row)
        day = utc_day(row["decision_ms"]["entry"])
        p = self.path_for(row["bot_id"], day)
        try:
            free_pct, free_b, total_b = self._free()
            if free_pct < self.min_free_pct:  # refuse while there is still room for the incident line
                raise self._fail(p, row, "disk_floor", f"free space {free_pct:.1f}% is below the floor {self.min_free_pct:g}% (DEC-031 section 3.8)",
                                 free_pct=round(free_pct, 3), floor_pct=self.min_free_pct, free_bytes=free_b, total_bytes=total_b)
            return self._append(p, row, line)
        except OSError as exc:  # open, mkdir, flock, read, statvfs, ...: anything else the disk or the filesystem throws
            raise self._fail(p, row, "io_error", f"{self._oserr(exc)}") from None

    def _append(self, p: Path, row: Mapping[str, Any], line: bytes) -> bool:
        new_dir = not p.parent.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        created = not p.exists()
        fd = os.open(str(p), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                ids, off, complete = self._ids(fd, str(p))
            except (ValueError, KeyError, TypeError) as exc:  # json.JSONDecodeError is a ValueError: a complete line that does not parse
                raise self._fail(p, row, "ledger_corrupt", f"a line of the file does not parse ({type(exc).__name__}: {str(exc)[:80]})") from None
            if not complete:  # the file ends in a half line: appending would weld the next row onto the fragment
                raise self._fail(p, row, "torn_tail", "the file does not end in a newline (a torn write)")
            tid = row["trade_id"]
            if tid in ids:
                if ids[tid] == identity(row):
                    return False
                raise LedgerConflict(f"trade_id {tid} already written with different content")
            data = line + b"\n"
            try:
                n = os.write(fd, data)
            except OSError as exc:
                raise self._fail(p, row, "write_failed", f"os.write failed: {self._oserr(exc)}") from None
            if n != len(data):
                raise self._fail(p, row, "short_write", f"short write: {n} of {len(data)} bytes reached the file")
            try:
                os.fsync(fd)
                ids[tid] = identity(row)
                self._cache[str(p)] = (os.fstat(fd).st_ino, off + len(data), ids)
                if created:
                    _fsync_dir(p.parent)
                if new_dir:
                    _fsync_dir(p.parent.parent)
            except OSError as exc:
                raise self._fail(p, row, "fsync_failed", f"fsync failed: {self._oserr(exc)}") from None
            return True
        finally:
            os.close(fd)  # releases the flock

    def read_bot(self, bot_id: str) -> list[dict[str, Any]]:
        d = self.root / bot_id
        out: list[dict[str, Any]] = []
        if not d.exists():
            return out
        for p in sorted(d.glob("trades-*.jsonl")):
            for line in p.read_text().splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    @staticmethod
    def _scan_file(p: Path, valid: Callable[[Any], bool]) -> tuple[list[dict[str, Any]], list[str]]:
        """(records, problems) of one JSONL file, never raising on its content. The file is read under a SHARED flock, so a write in flight (an appender holds the
        exclusive lock from write to fsync) is never seen half done: an unterminated tail seen here is a real torn write, not a race. A problem is a line that does
        not parse or fails `valid`, or an unterminated last line (reported even when it happens to parse, and its record is then still returned)."""
        recs: list[dict[str, Any]] = []
        probs: list[str] = []
        try:
            fd = os.open(str(p), os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                chunks = []
                while True:
                    c = os.read(fd, 1 << 20)
                    if not c:
                        break
                    chunks.append(c)
            finally:
                os.close(fd)
        except OSError as exc:
            return recs, [f"{p.name}: cannot be read ({type(exc).__name__})"]
        data = b"".join(chunks)
        lines = data.split(b"\n")
        torn = lines.pop() if lines else b""  # the text after the last newline: b"" when the file ends in one
        if torn:
            probs.append(f"{p.name}: does not end in a newline (a torn write: {len(torn)} byte(s) after the last complete line)")
            lines.append(torn)
        for i, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                r = json.loads(raw)
            except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
                probs.append(f"{p.name} line {i}: unparseable ({type(exc).__name__}: {str(exc)[:60]})")
                continue
            if not valid(r):
                probs.append(f"{p.name} line {i}: not a well-formed record")
                continue
            recs.append(r)
        return recs, probs

    @staticmethod
    def _valid_row(r: Any) -> bool:
        if not isinstance(r, dict):
            return False
        d = r.get("decision_ms")
        return (isinstance(r.get("kind"), str) and isinstance(r.get("trade_id"), str) and isinstance(d, dict) and isinstance(d.get("entry"), (int, float))
                and not isinstance(d.get("entry"), bool))

    @staticmethod
    def _valid_incident(r: Any) -> bool:
        return isinstance(r, dict) and isinstance(r.get("kind"), str) and isinstance(r.get("at_ms"), int) and not isinstance(r.get("at_ms"), bool)

    def scan_bot(self, bot_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Like read_bot but never raises on a damaged file: (rows, problems). Every torn or unparseable line of a bot's day files is a problem string; the scorer
        makes THAT bot NOT_DECIDABLE with these reasons and still scores every other bot."""
        d = self.root / bot_id
        rows: list[dict[str, Any]] = []
        probs: list[str] = []
        if not d.exists():
            return rows, probs
        for p in sorted(d.glob("trades-*.jsonl")):
            r, q = self._scan_file(p, self._valid_row)
            rows += r
            probs += q
        return rows, probs

    def read_incidents(self, bot_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        """(incidents, problems) of `<root>/<bot_id>/incidents.jsonl`; a damaged incident line is itself a problem (an incident may have been lost)."""
        p = self.incidents_path(bot_id)
        return self._scan_file(p, self._valid_incident) if p.exists() else ([], [])


# ------------------------------------------------------------------------------------------------------------------------------ config
STRAND_STATES = ("stranded", "entry_invalid", "exit_invalid")
HARD_GAP_KINDS = ("process_restart", "feed_restart", "feed_start")
# F2 (DEC-031 repair before exclude, tip-source books): a range the fill tool completed (tools/arena_tip_gapfill.py) arrives as a gap row with filled=true and data_loss=false
# and `back_slots` (the follower's backlog slots for a backlog_jump, else 0). It is NOT a section 1.4 gap for a position whose legs stay clear of it: an exit whose pricing slot
# falls inside it executes at the first print at or after hi + 1 (mark-through-gap: a conservative timing rule, not equivalence). A position with an ENTRY leg landing in
# [lo - back_slots, hi], or an EXIT leg landing in [lo - back_slots, lo), keeps the feed_gap exclusion (QP on #1182, C4: the slot clock trails the chain there). After a fill the
# pool history is complete: the ordinary readiness rule applies (no strand rule for filled ranges, QP C5). Not in StackConfig.as_dict: existing ledger rows do not change.


@dataclass(frozen=True)
class StackConfig:
    send: int = SEND_LAMPORTS
    rent: int = RENT_LAMPORTS
    h_in: float = F.HAIRCUT_ENTRY
    h_out: float = F.HAIRCUT_SELL
    k_rounding: str = "ceil"
    stakes_sol: tuple[float, ...] = STAKES_SOL
    grace_slots: int = GRACE_SLOTS
    portal_ppm: int = 0  # bonding route fee; 0 = direct, like the PumpSwap stack
    press_intercept: float = PRESS_INTERCEPT_PROVISIONAL
    # "both" (the deciding default, quant-proof on #708): every trade is priced under BOTH rules and a cell passes only if it passes under both.
    # "total": the DEC-031 strand class (r4b_book detection: invalid entry / exit, C2, C7, C8, proceeds < one send) priced as a TOTAL loss, no fail blend.
    # "sc": no strand class (sc.py / VERIFY): a sell worth less than one send is a normal sell and a strand-like exit is blended like any fill.
    # Neither is lower on every trade: at the arena's rent "total" is the more lenient on dust sells (+412,340 lamports on the one rugged parity pool).
    strand_rule: str = "both"
    e1_floor_per_005: int = E1_FLOOR_PER_005  # lamports per 0.05 SOL of stake; 0 = off (legacy parity only)
    # The whole-slot feed delay d (confirmed-commitment feed behind the chain), measured by tools/arena_feed_delay.py. REQUIRED: None = the engine refuses to
    # price. d is added to the entry decision slot and to the exit decision slot.
    feed_delay_slots: int | None = None
    # Calling contract: a bot may be called inline with on_row or asynchronously. The latency starts at the WRITE INSTANT, max(the bot's decision_ms, the
    # wall clock at the buy()/sell() call, t_recv_ms of the latest feed row), so a stale stamp (a bot that stamped the start of its thinking) is priced at
    # the write instant and never refused; only a stamp in the future (beyond max_future_ms of the call clock) is refused.
    gap_slots: int | None = 150  # a slot jump between consecutive trade rows beyond this is a data_loss gap (about 60 s); None = off (replays)
    gap_silence_ms: int | None = 60_000  # silence between consecutive trade rows beyond this is a data_loss gap; None = off (replays)
    max_future_ms: int = 5_000

    def as_dict(self) -> dict[str, Any]:
        return {"send": self.send, "rent": self.rent, "h_in": self.h_in, "h_out": self.h_out, "k_rounding": self.k_rounding, "exit_lag_s": F.EXIT_LAG_S,
                "guard": F.GUARD, "portal_ppm": self.portal_ppm, "press_intercept_provisional": self.press_intercept, "strand_rule": self.strand_rule,
                "e1_floor_per_005": self.e1_floor_per_005, "feed_delay_slots": self.feed_delay_slots}


def rules_of(cfg: StackConfig) -> tuple[str, ...]:
    return ("total", "sc") if cfg.strand_rule == "both" else (cfg.strand_rule,)


def book_fail_legs(pnl, filled, strand, ssb, nearby, send, intercept=None):
    """Flat and pressure legs of a book. A strand is a total loss and BYPASSES the fail blend; it stays in the set the pressure intercept is fit on
    (mean p over the cell's non-guarded sends, as the stack fits it). Returns (flat, press, intercept, p)."""
    pnl = np.asarray(pnl, float)
    flat, press, c, p = fail_legs(pnl, filled, ssb, nearby, send, intercept=intercept)
    strand = np.asarray(strand, bool)
    return np.where(strand, pnl, flat), np.where(strand, pnl, press), c, p


# ------------------------------------------------------------------------------------------------------------------------------ bonding curve
def curve_trip(stake: float, qe: float, be: float, ref: float | None, qx: float, bx: float, *, send: float, rent: float, h_in: float, h_out: float,
               portal_ppm: int = 0, rent_stranded: float = F.RENT_STRANDED, auto_stranded: bool = True) -> tuple[float, str, bool]:
    """One pre-graduation bonding-curve round trip in lamports -> (pnl, state, filled).

    (qe, be): POST-trade reserves the buy lands on. ref: decision-time price (SOL per token) for the 15% slippage guard, as latency_curve._try_buy.
    (qx, bx): POST-trade reserves the sell lands on; our buy is re-injected (reserves_with_our_buy) and the curve cannot pay more real SOL than it holds
    (quote_sell). A buy that cannot fill (completed curve, guard) is a miss and pays one send. A sell that cannot land (or, with auto_stranded, pays less
    than one send) strands the position: -stake - send - rent_stranded."""
    if not (math.isfinite(qe) and math.isfinite(be) and qe > 0 and be > 0):
        return -float(send), "guarded", False
    buy = quote_buy(venue="pump_bonding", size_lamports=int(stake), quote_lamports=int(qe), base_raw=int(be), market_cap=market_cap_sol(int(qe), int(be)),
                    portal_fee_ppm=portal_ppm)
    if buy is None:
        return -float(send), "guarded", False
    if ref and ref > 0:
        spot = int(qe) / (int(be) * 1000)
        executable = (buy.net_in_lamports / 1e9) / (buy.tokens_raw / 1e6)
        if spot > ref * F.GUARD or executable > ref * F.GUARD:
            return -float(send), "guarded", False
    stranded = -float(stake) - send - rent_stranded
    if not (math.isfinite(qx) and math.isfinite(bx)):
        return stranded, "stranded", True
    book = reserves_with_our_buy(quote_lamports=int(qx), base_raw=int(bx), net_in_lamports=buy.net_in_lamports, tokens_raw=buy.tokens_raw, same_venue=True)
    if book is None:
        return stranded, "stranded", True
    q2, b2 = book
    tokh = int(buy.tokens_raw / (1 + h_in))
    out = quote_sell(venue="pump_bonding", tokens_raw=tokh, quote_lamports=q2, base_raw=b2, market_cap=market_cap_sol(q2, b2),
                     payable_quote_lamports=int(qx), portal_fee_ppm=portal_ppm)
    if out is None:
        return stranded, "stranded", True
    proceeds = out * (1 - h_out)
    if auto_stranded and proceeds < send:
        return stranded, "stranded", True
    return proceeds - stake - 2 * send - rent, "filled", True


def _trip(prog: str, stake: float, st: Mapping[str, Any], lat: float, cfg: StackConfig, h_in: float, h_out: float, rule: str) -> tuple[float, str, bool]:
    total = rule == "total"
    if prog == "pumpswap":
        pnl, filled, strand, bad, xbad = round_trip(np.array([stake]), st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], cfg.send, cfg.rent, h_in,
                                                   h_out, return_stranded=True, return_invalid=True, return_exit_invalid=True, auto_stranded=total,
                                                   k_tol=F.K_TOL if total else None)
        state = "guarded" if not filled[0] else "entry_invalid" if bad[0] else "exit_invalid" if xbad[0] else "stranded" if strand[0] else "filled"
        return float(pnl[0]), state, bool(filled[0])
    return curve_trip(stake, st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], send=cfg.send, rent=cfg.rent, h_in=h_in, h_out=h_out,
                      portal_ppm=cfg.portal_ppm, auto_stranded=total)


def price_leg(prog: str, stake_lamports: float, st: Mapping[str, Any], lat: float, cfg: StackConfig, rule: str | None = None) -> dict[str, Any]:
    """One (stake, latency) leg from extracted states `st` = {ref, Qe[lat], Be[lat], ssb[lat], nearby[lat], Qx, Bx}. Returns the no-fail pnl (after the E1
    floor), the fill state, the strand flag and the flat / provisional-pressure legs (lamports). A strand is a total loss on every leg."""
    rule = rule or rules_of(cfg)[0]
    pnl, state, filled = _trip(prog, stake_lamports, st, lat, cfg, cfg.h_in, cfg.h_out, rule)
    floor_extra = 0.0
    if cfg.e1_floor_per_005 and state == "filled":
        pnl0, _, _ = _trip(prog, stake_lamports, st, lat, cfg, 0.0, 0.0, rule)
        haircut = pnl0 - pnl
        floor_extra = max(0.0, cfg.e1_floor_per_005 * stake_lamports / 50_000_000.0 - haircut)
        pnl -= floor_extra
    strand = rule == "total" and state in STRAND_STATES
    flat, press, _, p = book_fail_legs(np.array([pnl]), np.array([filled]), np.array([strand]), np.array([st["ssb"][lat]]), np.array([st["nearby"][lat]]),
                                       cfg.send, intercept=cfg.press_intercept)
    return {"pnl": pnl, "filled": filled, "state": state, "strand": bool(strand), "e1_extra": floor_extra, "flat": float(flat[0]), "press": float(press[0]),
            "p": float(p[0])}


# ------------------------------------------------------------------------------------------------------------------------------ pool history
class PoolHist:
    """Ordered prints of one pool. PumpSwap reserves are PRE-trade, bonding POST-trade; V is per print with its source (a print whose source is "none" is
    never priced as V = 0: the trades that need it are excluded and counted)."""

    __slots__ = ("pool", "mint", "venue", "keys", "seen", "slot", "isbuy", "sol", "tok", "q", "b", "v", "vsrc", "last_v_row", "_np", "floor")

    def __init__(self, pool: str, mint: str | None, venue: str) -> None:
        self.pool, self.mint, self.venue = pool, mint, venue
        self.keys: list[tuple] = []
        self.seen: set[tuple] = set()
        self.slot: list[int] = []
        self.isbuy: list[bool] = []
        self.sol: list[float] = []
        self.tok: list[float] = []
        self.q: list[float] = []
        self.b: list[float] = []
        self.v: list[float] = []
        self.vsrc: list[str] = []  # where each print's V came from: row | pool_prev | vmap | none | na (bonding)
        self.last_v_row: float | None = None
        self._np: dict[str, np.ndarray] | None = None
        self.floor: int | None = None  # set by trim(): prints below this slot (but the anchor) are gone and so are their dedupe keys; None = never trimmed

    def add(self, key: tuple, dedupe: tuple, slot: int, isbuy: bool, sol: float, tok: float, q: float, b: float, v: float, vsrc: str) -> bool:
        if dedupe in self.seen or (self.floor is not None and slot < self.floor):  # below the floor the dedupe keys are gone: it is a duplicate
            return False
        self.seen.add(dedupe)
        i = len(self.keys) if (not self.keys or key >= self.keys[-1]) else bisect.bisect_right(self.keys, key)
        for lst, val in ((self.keys, key), (self.slot, slot), (self.isbuy, isbuy), (self.sol, sol), (self.tok, tok), (self.q, q), (self.b, b), (self.v, v),
                         (self.vsrc, vsrc)):
            lst.insert(i, val)
        self._np = None
        return True

    def trim(self, cut: int) -> int:
        """Drop every print with slot < cut except the last of them (the anchor: the guard spot and the 'last print <= x' reads of a later buy may land on it),
        and the dedupe keys below cut. Returns the number of prints dropped; 0 (and no floor) when fewer than two prints lie below cut. See TRIM_KEEP_SLOTS."""
        i = bisect.bisect_left(self.slot, cut)  # `slot` is sorted: keys sort by slot first
        if i < 2:
            return 0
        n = i - 1
        for lst in (self.keys, self.slot, self.isbuy, self.sol, self.tok, self.q, self.b, self.v, self.vsrc):
            del lst[:n]
        self.seen = {t for t in self.seen if t[0] >= cut}  # a dedupe key is (slot, signature, event_index, tx_index)
        self.floor = cut if self.floor is None else max(self.floor, cut)
        self._np = None
        return n

    @property
    def last_slot(self) -> int:
        return self.slot[-1] if self.slot else -1

    def arrays(self) -> dict[str, np.ndarray]:
        if self._np is None:
            self._np = {"sl": np.asarray(self.slot, dtype=np.int64), "ib": np.asarray(self.isbuy, dtype=bool), "so": np.asarray(self.sol, dtype=float),
                        "tk": np.asarray(self.tok, dtype=float), "q": np.asarray(self.q, dtype=float), "b": np.asarray(self.b, dtype=float),
                        "v": np.asarray(self.v, dtype=float), "vok": np.asarray([x != "none" for x in self.vsrc], dtype=bool)}
        return self._np


def bonding_states(h: PoolHist, sd: int, x_land: dict[float, int], x_exit: int, nearby_slots: int) -> dict[str, Any]:
    """Bonding fills: POST-trade reserves of the last print with slot <= x (the END bound). Pressure = buys in slot x / buy lamports over [x - nearby, x]."""
    a = h.arrays()
    sl, ib, so, q, b = a["sl"], a["ib"], a["so"], a["q"], a["b"]
    cib = np.concatenate([[0], np.cumsum(ib)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0.0))])
    j = int(np.searchsorted(sl, sd, "left"))
    ref = float(q[j - 1] / (b[j - 1] * 1000.0)) if j > 0 else None
    out: dict[str, Any] = {"ref": ref, "Qe": {}, "Be": {}, "ssb": {}, "nearby": {}}
    for lat, x in x_land.items():
        je = int(np.searchsorted(sl, x + 1, "left"))  # prints with slot <= x are [0, je)
        ix = int(np.searchsorted(sl, x, "left"))
        lo2 = int(np.searchsorted(sl, x - nearby_slots, "left"))
        out["Qe"][lat] = float(q[je - 1]) if je > 0 else float("nan")
        out["Be"][lat] = float(b[je - 1]) if je > 0 else float("nan")
        out["ssb"][lat] = float(cib[je] - cib[ix])
        out["nearby"][lat] = float(cbv[je] - cbv[lo2])
    je = int(np.searchsorted(sl, x_exit + 1, "left"))
    out["Qx"] = float(q[je - 1]) if je > 0 else float("nan")
    out["Bx"] = float(b[je - 1]) if je > 0 else float("nan")
    return out


# ------------------------------------------------------------------------------------------------------------------------------ engine
@dataclass
class Position:
    position_id: str
    bot_id: str
    frozen_sha: str
    pool: str
    mint: str | None
    prog: str
    decision_ms: int  # the write instant of the entry decision: max(the bot's stamp, the call clock, the latest feed row's t_recv_ms)
    decision_slot: int  # as given by the bot
    sd: int  # effective entry decision slot = max(decision_slot, slot mapped from decision_ms)
    sps_entry: float
    bot_sol_in: float | None
    meta: dict[str, Any]
    exit_ms: int | None = None
    exit_slot: int | None = None
    xs: int | None = None  # effective exit decision slot
    sps_exit: float | None = None
    reason: str | None = None
    restarted: bool = False  # rebuilt from the ledger after an engine restart: its pool history has a hole
    stamp_ms: int = 0  # the bot's own decision_ms (decision_ms above is the WRITE instant)
    call_ms: int = 0  # the wall clock at the buy() call
    exit_stamp_ms: int | None = None
    exit_call_ms: int | None = None
    call_feed_slot: int | None = None  # the latest feed slot the engine had seen at the buy() call: the most the bot could have seen
    mark: tuple[int, int, int] | None = None  # (xs before, lo, hi) when the exit was marked through a filled range (F2)


@dataclass
class Gap:
    prog: str | None
    t_lo: int
    t_hi: int
    s_lo: int
    s_hi: int
    kind: str


class ArenaEngine:
    def __init__(self, ledger_root: str | os.PathLike = LEDGER_ROOT, registry_path: str | os.PathLike = REGISTRY_PATH, cfg: StackConfig | None = None,
                 vmap: Mapping[str, Any] | None = None, clock: SlotClock | None = None, sps_fn: Callable[[], float | None] | None = None,
                 now_fn: Callable[[], int] | None = None, code_state_fn: Callable[[], tuple[str | None, bool | None]] | None = None,
                 min_free_pct: float = MIN_FREE_PCT_DEFAULT) -> None:
        # min_free_pct: the free-space floor of the ledger's filesystem checked before every append (default 25, DEC-031 section 3.8; never below 20, else ValueError)
        self.ledger = Ledger(ledger_root, now_fn=lambda: self.now_fn(), min_free_pct=min_free_pct)  # now_fn late-bound: an incident line carries the engine's own call clock
        self.code_state_fn = code_state_fn or process_code_state
        self._code_cache: tuple[float, tuple[str | None, bool | None]] | None = None
        self.registry_path = registry_path
        self.cfg = cfg or StackConfig()
        self.vmap = vmap or {}
        self.clock = clock or SlotClock()
        self.sps_fn = sps_fn
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        # The code state is captured ONCE here, at engine start, and written to every bot's ledger as a `start` record (before its first open row). The scorer refuses
        # a book whose start record is missing, does not carry the registered frozen_sha, or was a dirty tree: a process started from the wrong code would
        # otherwise pass if the disk were later reset to the frozen sha while it kept running.
        self.engine_id = uuid.uuid4().hex[:16]
        self.start_state = self.code_state_fn()
        self.started_ms = int(self.now_fn())
        self._started_bots: set[str] = set()
        self.pools: dict[str, PoolHist] = {}
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, list[str]] = {}  # pool -> position ids with a sell decided and not yet priced
        self.hw_slot = -1
        self.hw_ms: int | None = None  # latest t_recv_ms of a trade row
        self.hw_ms_slot = -1  # slot of the row that set hw_ms: the (slot, ms) anchor of the slot clock
        self.gaps: list[Gap] = []
        self.fills: list[tuple[str | None, int, int, int]] = []  # (prog, lo, hi, back_slots) of every filled range seen (F2): not gaps
        self.priced: dict[str, list[dict[str, Any]]] = {}  # pool -> positions already written (for late-print detection)
        self.counters: dict[str, int] = {}
        self.last_error: dict[str, str] = {}
        # bot_id -> why. A bot whose ledger hit a LedgerCorrupt in a path the FEED drives (a late print, or the pricing of a sell) is halted: that bot is no longer
        # priced and its late prints are dropped, so that every feed row does not retry the torn file and write one more incident; the feed loop and every other
        # bot carry on. The incident line is already on disk; the book's read is NOT_DECIDABLE. A restarted engine starts clean (its open positions are engine_restart).
        self.halted: dict[str, str] = {}

    # --- feed
    def _count(self, k: str) -> None:
        self.counters[k] = self.counters.get(k, 0) + 1

    def _halt(self, bot_id: str, why: str) -> None:
        """Stop pricing `bot_id` after a ledger incident in a feed-driven path (see `halted`). The Ledger has already written the incident line."""
        if bot_id not in self.halted:
            self.halted[bot_id] = why
            self._count("bot_halted")

    def _lost(self, bot_id: str, kind: str, exc: Exception, *, pool: str | None, instant: int | None, decision_slot: int | None) -> None:
        """A buy / sell the ledger could not take is a lost attempt: besides the Ledger's own incident for the refused row, write one incident for the attempt itself
        (pool, write instant, slot), so that the scorer's NOT_DECIDABLE names what was lost."""
        self._count("ledger_torn_tail")
        self._count("lost_" + kind.split("_")[0] + "s")  # lost_buys / lost_sells: the in-memory record, even if no incident line can be written
        if isinstance(exc, LedgerWriteFailed):  # a disk that cannot take this row will not take the next: stop pricing and trading this book
            self._halt(bot_id, f"{exc.kind}: {str(exc)[:120]}")
        path = self.ledger.path_for(bot_id, utc_day(instant)) if instant is not None else None
        if not self.ledger.incident(bot_id, kind, str(exc)[:200], path=path, pool=pool, decision_ms=instant, decision_slot=decision_slot):
            self._count("incident_unwritten")

    def _v_of(self, h: PoolHist, row: Mapping[str, Any], venue: str) -> tuple[float, str]:
        """(V, source). Source order: this row's event V, an earlier row of the same pool, the static map, none. V outside [0, 30 SOL] is not used."""
        if venue != "pumpswap":
            return 0.0, "na"
        for src, v in (("row", row.get("virtual_quote_reserves")), ("pool_prev", h.last_v_row), ("vmap", self.vmap.get(h.pool))):
            if v is None:
                continue
            v = float(v)
            if 0.0 <= v <= V_BAND_MAX:
                if src == "row":
                    h.last_v_row = v
                return v, src
            self._count("v_out_of_band")
        self._count("v_missing")
        return 0.0, "none"

    @staticmethod
    def order_key(row: Mapping[str, Any], slot: int, t_ms: int) -> tuple:
        """Within-slot order. With tx_index (exploration tape): (slot, tx_index, event_index, signature). Without it (the live arena tape): receive order,
        (slot, t_recv_ms, signature, event_index). The two forms never meet in one pool."""
        sig = str(row.get("signature") or "")
        ev = int(row["event_index"]) if row.get("event_index") is not None else 0
        if row.get("tx_index") is not None:
            return (slot, 0, int(row["tx_index"]), ev, sig)
        return (slot, 1, t_ms, sig, ev)

    def on_gap(self, row: Mapping[str, Any]) -> None:
        """A feed gap row (type "gap"). A data_loss gap, or a process_restart / feed_restart / feed_start gap, excludes the trades whose span it overlaps; the others
        are counted. The lost interval runs from the engine's last row before the gap (and silent_ms / down_ms when the row gives them) to the gap row's time."""
        if row.get("filled") is True and not row.get("data_loss"):
            lo, hi = row.get("from_slot"), row.get("to_slot")
            if all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)) and hi >= lo and row.get("t_recv_ms") is not None:
                back = row.get("back_slots")
                self._on_filled(row.get("prog"), lo, hi, int(row["t_recv_ms"]), back if isinstance(back, int) and not isinstance(back, bool) and back >= 0 else 0)
                return
            self._count("gap_fill_marker_bad")  # a marker the engine cannot read is a data_loss gap (fail closed)
            row = dict(row, data_loss=True)
        at = int(row["t_recv_ms"])
        silent = int(row.get("silent_ms") or row.get("down_ms") or 0)
        kind = str(row.get("kind"))
        if not (row.get("data_loss") or kind in HARD_GAP_KINDS):
            self._count("gap_soft")
            return
        t_lo = min(self.hw_ms if self.hw_ms is not None else at, at - silent)
        s_hi = max(int(row.get("to_slot") or row.get("slot") or 0), self.hw_slot)
        self._count("gap_data_loss")
        self.gaps.append(Gap(row.get("prog"), t_lo, at, max(self.hw_slot, 0), s_hi, kind))

    def _on_filled(self, prog: str | None, lo: int, hi: int, at_ms: int, back: int = 0) -> None:
        """A filled range: counted, remembered for mark-through-gap, and the time anchor moves to the follower's own time of it (the follower noticed the gap then; a quiet
        stretch that the fill rows do not cover must not turn into an engine_silence gap). It never enters `gaps`."""
        self.fills.append((prog, lo, hi, back))
        self._count("gap_filled")
        self.counters["gap_filled_slots"] = self.counters.get("gap_filled_slots", 0) + (hi - lo + 1)
        if self.hw_ms is not None and at_ms > self.hw_ms:
            self.hw_ms, self.hw_ms_slot = at_ms, max(self.hw_ms_slot, hi)
        self._flag_priced_by_fill(prog, lo, hi, back, at_ms)

    def _flag_priced_by_fill(self, prog: str | None, lo: int, hi: int, back: int, at_ms: int) -> None:
        """QP C4: a position priced BEFORE the marker reached the engine (its legs sit in the late-delivered stretch [lo - back, lo), the rows came in before the gap row) is
        flagged the way a late print is: a late_print row with gap_overlap true, which the scorer excludes under the gap rule (priced rows are write-once). Once per position and range."""
        for lst in self.priced.values():
            for e in lst:
                if "lands" not in e or self.hw_slot > e["need"] + LATE_KEEP_SLOTS or e["bot_id"] in self.halted:
                    continue
                if prog not in (None, e["prog"], "all") and not (prog == "pumpfun" and e["prog"] == "pump_bonding"):
                    continue
                if not self._legs_hit(e["lands"], e["x_trig"], lo, hi, back):
                    continue
                lid = hashlib.sha256(f"{e['pid']}|fill|{lo}|{hi}".encode()).hexdigest()[:16]
                try:
                    self.ledger.append({"schema": SCHEMA, "kind": "late_print", "trade_id": f"{e['pid']}-late-{lid}", "position_id": e["pid"], "bot_id": e["bot_id"],
                                        "decision_ms": {"entry": e["entry_ms"], "exit": None}, "slot": lo, "signature": None, "t_recv_ms": at_ms, "gap_overlap": True,
                                        "fill_margin": {"from_slot": lo, "to_slot": hi, "back_slots": back}, "written_ms": self.now_fn()})
                except LedgerCorrupt as exc:
                    self._count("ledger_torn_tail")
                    self._count("late_prints_lost")
                    self._halt(e["bot_id"], f"fill-margin flag write refused: {str(exc)[:120]}")
                    continue
                self._count("fill_margin_flagged")

    def _through_fills(self, slot: int) -> int:
        """`slot` moved up through every filled range that touches it (chained ranges too): a slot jump that a fill covers is not a jump."""
        moved = True
        while moved:
            moved = False
            for _p, lo, hi, _b in self.fills:
                if lo <= slot + 1 and hi > slot:
                    slot, moved = hi, True
        return slot

    def on_row(self, row: Mapping[str, Any]) -> None:
        typ = row.get("type", "trade")
        if typ == "gap":
            self.on_gap(row)
            return
        if typ == "start":  # the feed process (re)started: whatever it missed while down is a data_loss gap from the last row seen
            if row.get("t_recv_ms") is not None and self.hw_ms is not None:
                self.on_gap({"type": "gap", "kind": "feed_start", "data_loss": True, "prog": None, "t_recv_ms": row["t_recv_ms"], "slot": row.get("slot") or self.hw_slot})
            return
        venue = VENUE_ALIASES.get(str(row.get("prog") or row.get("venue")))
        if venue is None or typ != "trade":
            return
        pool = row.get("pool") or row.get("mint")
        if not pool or row.get("slot") is None or row.get("side") not in ("buy", "sell") or row.get("t_recv_ms") is None:
            self._count("rows_skipped")
            return
        slot, t_ms = int(row["slot"]), int(row["t_recv_ms"])
        h = self.pools.get(pool)
        if h is None:
            h = self.pools[pool] = PoolHist(pool, row.get("mint"), venue)
        key = self.order_key(row, slot, t_ms)
        dedupe = (slot, str(row.get("signature") or ""), int(row["event_index"]) if row.get("event_index") is not None else 0,
                  int(row["tx_index"]) if row.get("tx_index") is not None else -1)
        v, vsrc = self._v_of(h, row, venue)
        if not h.add(key, dedupe, slot, row["side"] == "buy", float(row["sol_lamports"]), float(row["token_raw"]), float(row["quote_reserve"]),
                     float(row["base_reserve"]), v, vsrc):
            self._count("rows_dup")
            if h.floor is not None and slot < h.floor:
                self._count("rows_below_trim_floor")  # also counted in rows_dup: below the floor a duplicate and a very late new print look the same
            return
        if self.hw_ms is not None and not row.get("fill"):  # process downtime / silence / a slot jump between consecutive rows is a gap from the last row seen (a repair row of a filled range is not)
            c = self.cfg
            if c.gap_silence_ms is not None and t_ms - self.hw_ms > c.gap_silence_ms:
                self.gaps.append(Gap(None, self.hw_ms, t_ms, self.hw_slot, slot, "engine_silence"))
                self._count("gap_engine_silence")
            elif c.gap_slots is not None and slot - self._through_fills(self.hw_slot) > c.gap_slots:
                self.gaps.append(Gap(None, self.hw_ms, t_ms, self.hw_slot, slot, "engine_slot_jump"))
                self._count("gap_engine_slot_jump")
        self._late_check(pool, slot, key, row)
        if self.hw_ms is None or t_ms > self.hw_ms:
            self.hw_ms, self.hw_ms_slot = t_ms, slot
        elif t_ms == self.hw_ms:
            self.hw_ms_slot = max(self.hw_ms_slot, slot)
        ts = row.get("event_ts") or row.get("block_time")
        self.clock.observe(slot, int(ts) if ts else None)
        if slot > self.hw_slot:
            self.hw_slot = slot
            for p in list(self.pending):
                self._try_pending(p, final=False)
        else:
            self._try_pending(pool, final=False)

    def _late_check(self, pool: str, slot: int, key: tuple, row: Mapping[str, Any]) -> None:
        """A print that arrives after a position on its pool was priced, and sorts before the print that priced it (slot < need, or slot == need and an earlier
        key; slot <= need when the state was derived), is a LATE print. It is written to the ledger (counted per book by the scorer); if a logged gap overlaps
        the position's span the row says so and the scorer excludes that trade under the gap rule. The priced row itself is write-once and not changed."""
        lst = self.priced.get(pool)
        if not lst:
            return
        keep = []
        for e in lst:
            if self.hw_slot > e["need"] + LATE_KEEP_SLOTS:
                continue
            keep.append(e)
            if slot < e["need"] or (slot == e["need"] and (e["trigger"] is None or key < e["trigger"])):
                if e["bot_id"] in self.halted:  # its ledger is damaged: the late print is dropped (the incident is on record), the feed carries on
                    self._count("late_prints_dropped_halted")
                    continue
                lid = hashlib.sha256(f"{e['pid']}|{slot}|{row.get('signature')}|{row.get('event_index')}".encode()).hexdigest()[:16]
                overlap = self._gap_hit(e["prog"], e["span"]) is not None
                try:
                    self.ledger.append({"schema": SCHEMA, "kind": "late_print", "trade_id": f"{e['pid']}-late-{lid}", "position_id": e["pid"], "bot_id": e["bot_id"],
                                        "decision_ms": {"entry": e["entry_ms"], "exit": None}, "slot": slot, "signature": row.get("signature"),
                                        "t_recv_ms": row.get("t_recv_ms"), "gap_overlap": overlap, "written_ms": self.now_fn()})
                except LedgerCorrupt as exc:
                    # A torn or corrupt day file in the FEED path. This runs after the print was added to the pool history, so raising here would leave the feed state
                    # half updated and kill the caller's feed loop for every bot. The Ledger has written the incident line; halt this bot and carry on.
                    self._count("ledger_torn_tail")
                    self._count("late_prints_lost")
                    self._halt(e["bot_id"], f"late-print write refused: {str(exc)[:120]}")
                    continue
                self._count("late_prints")
        self.priced[pool] = keep

    def start_record(self, bot_id: str) -> bool:
        """Write this engine's start record into bot_id's ledger (once per engine and bot). Called automatically before the bot's first open row; a runner should
        call start_all() right after constructing the engine."""
        if bot_id in self._started_bots:
            return False
        reg = self._bot(bot_id)
        head, dirty = self.start_state
        try:
            wrote = self.ledger.append({"schema": SCHEMA, "kind": "start", "trade_id": f"start-{self.engine_id}-{bot_id}", "engine_id": self.engine_id, "bot_id": bot_id,
                                        "frozen_sha": reg["frozen_sha"], "decision_ms": {"entry": self.started_ms, "exit": None}, "code_head": head, "code_dirty": dirty,
                                        "pid": os.getpid(), "stack": self.cfg.as_dict(), "written_ms": int(self.now_fn())})
        except LedgerWriteFailed as exc:  # the incident line is already written; the book cannot start on a disk that will not take its start record
            self._halt(bot_id, f"{exc.kind}: {str(exc)[:120]}")
            raise
        self._started_bots.add(bot_id)
        return wrote

    def start_all(self) -> int:
        """start_record for every registered book that is not stopped."""
        return sum(self.start_record(b) for b, e in load_registry(self.registry_path).items() if e["stopped_at_ms"] is None)

    def code_state(self) -> tuple[str | None, bool | None]:
        """(HEAD, dirty) of the running checkout, re-read at most once a minute. Every `open` row carries it; the scorer refuses a book whose rows do not run the
        registered frozen_sha, or ran on a dirty tree."""
        now = time.monotonic()
        if self._code_cache is None or now - self._code_cache[0] > 60.0:
            self._code_cache = (now, self.code_state_fn())
        return self._code_cache[1]

    def sps(self) -> float | None:
        s = self.sps_fn() if self.sps_fn else self.clock.sps()
        return s if sps_ok(s) else None

    def slot_at(self, ms: int, sps: float) -> int:
        """The tape slot at wall-clock `ms`: the latest feed row's (slot, t_recv_ms) plus the elapsed slots. A lower bound of the chain's slot (feed delay)."""
        assert self.hw_ms is not None
        return self.hw_ms_slot + int(math.floor((int(ms) - self.hw_ms) / (sps * 1000.0) + 1e-9))

    def _check_delay_set(self) -> int:
        d = self.cfg.feed_delay_slots
        if d is None or not isinstance(d, int) or isinstance(d, bool) or d < 0:
            raise ArenaRefused("feed_delay_unset: StackConfig.feed_delay_slots is required (a whole number of slots from tools/arena_feed_delay.py); nothing is priced without it")
        return d

    def _write_instant(self, decision_ms: int) -> tuple[int, int]:
        """(call_ms, instant_ms). call_ms is the wall clock at this call; the latency starts at instant_ms = max(the bot's decision_ms, call_ms, t_recv_ms of the
        latest feed row the engine has seen). A bot called inline holds the feed still while it thinks, so its own stamp would give it the thinking time free."""
        call = int(self.now_fn())
        return call, max(int(decision_ms), call, self.hw_ms if self.hw_ms is not None else 0)

    def _check_instant(self, decision_ms: int, call_ms: int) -> None:
        if self.hw_ms is None:
            raise ArenaRefused("no_feed_rows: no feed rows seen yet")
        if int(decision_ms) > call_ms + self.cfg.max_future_ms:
            raise ArenaRefused("future_decision: decision_ms is in the future")

    # --- bot API
    def _bot(self, bot_id: str) -> dict[str, Any]:
        reg = load_registry(self.registry_path)
        if bot_id not in reg:
            raise ArenaRefused(f"bot {bot_id} is not registered (register_bot first: the frozen sha is stamped from the registry)")
        return reg[bot_id]

    def buy(self, bot_id: str, pool: str, sol_in: float | None, decision_ms: int, decision_slot: int, meta: Mapping[str, Any] | None = None) -> str:
        """Open a paper position. The latency starts at the WRITE instant = max(decision_ms, the wall clock at this call, t_recv_ms of the latest feed row): a bot
        that thinks for 30 s with the feed frozen lands 30 s later; a stale stamp is priced at the write instant, never refused (only a stamp in the future is).
        Priced at every configured stake (0.25 / 0.5 / 1.0 SOL), so `sol_in` is the bot's own nominal size and is only recorded. An "open" row (stamp, call clock,
        write instant and write lag) is written to the ledger BEFORE anything is priced. Returns the position_id (deterministic in bot, pool, stamp, slot)."""
        reg = self._bot(bot_id)
        call, instant = self._write_instant(decision_ms)
        if instant < reg["clock_start_ms"]:
            raise ArenaRefused(f"before_clock: the write instant {instant} is before bot {bot_id}'s clock {reg['clock_start_ms']}")
        if reg["stopped_at_ms"] is not None and instant >= reg["stopped_at_ms"]:
            raise ArenaRefused(f"stopped: bot {bot_id} is stopped")
        if bot_id in self.halted:  # a lost attempt: no ledger row can be written for it, so the incident file is the only record
            exc = LedgerCorrupt(f"bot {bot_id} is halted after a ledger incident ({self.halted[bot_id]}): the buy is refused and nothing was written")
            self._lost(bot_id, "buy_refused", exc, pool=pool, instant=instant, decision_slot=int(decision_slot))
            raise exc
        pid = hashlib.sha256(f"{bot_id}|{pool}|{int(decision_ms)}|{int(decision_slot)}".encode()).hexdigest()[:16]
        timing = {"stamp_ms": {"entry": int(decision_ms), "exit": None}, "call_ms": {"entry": call, "exit": None},
                  "write_lag_ms": {"entry": instant - int(decision_ms), "exit": None}}
        try:
            self._check_delay_set()
            h = self.pools.get(pool)
            if h is None or not h.slot:
                raise ArenaRefused(f"no_prints: no prints seen for pool {pool}")
            self._check_instant(decision_ms, call)
            sps = self.sps()
            if sps is None:
                raise ArenaRefused("clock_not_ready: slot clock not ready (sps unknown)")
            # Every read of this buy is at or above sd - nearby, sd = max(decision_slot, slot_at) (d >= 0, k >= 0 only push it later); a caller that passes the highest slot
            # seen as decision_slot (HV4, arena_rules) has sd ~ hw_slot, far above any floor. Refuse only where the trimmed pool lacks prints the buy would read.
            if h.floor is not None and max(int(decision_slot), self.slot_at(instant, sps)) - int(round(F.NEARBY_WINDOW_S / sps)) < h.floor:
                self._count("refused_history_trimmed")
                raise ArenaRefused(f"no_prints: history_trimmed: the nearby window of this buy reaches below slot {h.floor}, where trim_history() cut pool {pool}")
        except ArenaRefused as exc:  # a refused buy is still a decision: it goes in the decision log and counts toward the excluded share
            try:
                self.ledger.append({"schema": SCHEMA, "kind": "refused", "trade_id": f"{pid}-refused-{instant}", "position_id": pid, "bot_id": bot_id,
                                    "frozen_sha": reg["frozen_sha"], "decision_ms": {"entry": instant, "exit": None}, **timing,
                                    "decision_slot": {"entry": int(decision_slot), "exit": None}, "pool": pool, "reason": str(exc).split(":")[0],
                                    "detail": str(exc)[:200], "bot_sol_in": sol_in, "meta": dict(meta or {}), "stack": self.cfg.as_dict(), "written_ms": call})
            except LedgerCorrupt as lost:  # the refusal itself could not be logged: the attempt would vanish from the decision log
                self._lost(bot_id, "buy_refused", lost, pool=pool, instant=instant, decision_slot=int(decision_slot))
                raise
            self._count("refused_" + str(exc).split(":")[0])
            raise
        if pid in self.positions:
            raise ArenaRefused(f"duplicate: position {pid} already open for this decision")
        d = self.cfg.feed_delay_slots
        sd = max(int(decision_slot), self.slot_at(instant, sps))  # the write instant mapped to the tape slot clock; d is added at the landing
        pos = Position(pid, bot_id, reg["frozen_sha"], pool, h.mint, h.venue, instant, int(decision_slot), sd, float(sps),
                       None if sol_in is None else float(sol_in), dict(meta or {}), stamp_ms=int(decision_ms), call_ms=call, call_feed_slot=self.hw_slot)
        try:
            head, dirty = self.code_state()
            self.start_record(bot_id)  # the engine's code state, captured once at start, is on the ledger before the first open row that points at it
            self.ledger.append({"schema": SCHEMA, "kind": "open", "code_head": head, "code_dirty": dirty, "engine_id": self.engine_id, "call_feed_slot": self.hw_slot, "trade_id": f"{pid}-open", "position_id": pid, "bot_id": bot_id, "frozen_sha": reg["frozen_sha"],
                                "decision_ms": {"entry": pos.decision_ms, "exit": None}, **timing, "decision_slot": {"entry": pos.decision_slot, "exit": None},
                                "effective_slot": {"entry": sd, "exit": None}, "corrected_slot": {"entry": sd + d, "exit": None}, "feed_delay_slots": d, "pool": pool,
                                "mint": h.mint, "prog": h.venue, "sps_entry": pos.sps_entry, "bot_sol_in": pos.bot_sol_in, "meta": pos.meta,
                                "stack": self.cfg.as_dict(), "written_ms": call})
        except LedgerCorrupt as lost:
            self._lost(bot_id, "buy_refused", lost, pool=pool, instant=instant, decision_slot=int(decision_slot))
            raise
        self.positions[pid] = pos
        return pid

    def recover(self) -> int:
        """Rebuild open positions from the ledger after a restart: every "open" row with no "trade" / "excluded" row. The pool history of a recovered position
        has a hole (the engine was down), so it is closed as an excluded trade (reason engine_restart) when its sell is decided; one never sold stays open and
        the scorer counts it as a restart orphan (the read is NOT_DECIDABLE)."""
        n = 0
        for bot_id in load_registry(self.registry_path):
            rows = self.ledger.read_bot(bot_id)
            closed = {r["position_id"] for r in rows if r.get("kind", "trade") in ("trade", "excluded")}
            for r in rows:
                if r.get("kind") == "open" and r["position_id"] not in closed and r["position_id"] not in self.positions:
                    self.positions[r["position_id"]] = Position(r["position_id"], bot_id, r["frozen_sha"], r["pool"], r["mint"], r["prog"], r["decision_ms"]["entry"],
                                                                r["decision_slot"]["entry"], r["effective_slot"]["entry"], r["sps_entry"], r["bot_sol_in"],
                                                                r["meta"], restarted=True,
                                                                stamp_ms=(r.get("stamp_ms") or {}).get("entry", r["decision_ms"]["entry"]),
                                                                call_ms=(r.get("call_ms") or {}).get("entry", 0), call_feed_slot=r.get("call_feed_slot"))
                    n += 1
        return n

    def _k(self, lat: float, sps: float) -> int:
        return int(F.k_slots(lat, sps, self.cfg.k_rounding))

    def sell(self, bot_id: str, position_id: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        pos = self.positions.get(position_id)
        if pos is None or pos.bot_id != bot_id:
            raise ArenaRefused(f"no open position {position_id} for bot {bot_id}")
        if pos.exit_slot is not None:
            raise ArenaRefused(f"position {position_id} already has a sell")
        if frac != 1.0:
            raise ArenaRefused("partial sells are not supported in v1 (frac must be 1.0)")
        if bot_id in self.halted:  # the position stays open (an orphan in the read); the incident file records the lost sell
            exc = LedgerCorrupt(f"bot {bot_id} is halted after a ledger incident ({self.halted[bot_id]}): the sell is refused and nothing was written")
            self._lost(bot_id, "sell_refused", exc, pool=pos.pool, instant=int(decision_ms), decision_slot=int(decision_slot))
            raise exc
        self._check_delay_set()
        call, instant = self._write_instant(decision_ms)
        self._check_instant(decision_ms, call)
        sps = self.sps()
        if sps is None:
            raise ArenaRefused("slot clock not ready (sps unknown)")
        xs = max(int(decision_slot), self.slot_at(instant, sps))  # the write instant of the sell, mapped to the tape slot clock
        k_slow = max(self._k(lat, pos.sps_entry) for lat in LATS)
        if xs < pos.sd + k_slow + 1:
            raise ArenaRefused("sell decided before the slower entry leg lands")
        try:
            F.exit_lag_slots(sps)
        except AssertionError as exc:
            raise ArenaRefused(str(exc)) from None
        pos.exit_ms, pos.exit_slot, pos.xs, pos.sps_exit, pos.reason = instant, int(decision_slot), xs, float(sps), reason
        pos.exit_stamp_ms, pos.exit_call_ms = int(decision_ms), call
        if pos.restarted:
            try:
                self._write_excluded(pos, "engine_restart", None)
            except LedgerCorrupt as lost:  # the exclusion row could not be written: the position stays open (an orphan) and the incident file says why
                self._lost(bot_id, "sell_refused", lost, pool=pos.pool, instant=instant, decision_slot=int(decision_slot))
                self._halt(bot_id, f"exclusion write refused: {str(lost)[:120]}")
                raise
            del self.positions[position_id]
            return
        self.pending.setdefault(pos.pool, []).append(position_id)
        self._try_pending(pos.pool, final=False)

    # --- pricing
    def _mark_through(self, pos: Position) -> None:
        """F2 mark-through-gap: an exit whose pricing slot (xs + d + exit lag + 1) falls inside a FILLED range executes at the first print at or after hi + 1, the first slot
        after the range: the paper exit never uses a print from inside a range the live bot could not have had. Applied once; it moves xs, so every later formula follows."""
        if pos.mark is not None or not self.fills or pos.xs is None:
            return
        trig = pos.xs + self._check_delay_set() + F.exit_lag_slots(pos.sps_exit) + 1  # type: ignore[operator,arg-type]
        for prog, lo, hi, _b in sorted(self.fills, key=lambda f: (f[1], f[2])):
            if prog not in (None, pos.prog, "all") and not (prog == "pumpfun" and pos.prog == "pump_bonding"):
                continue
            if lo <= trig <= hi:
                pos.mark = (pos.xs, lo, hi)
                pos.xs += hi + 1 - trig
                self._count("mark_through")
                return

    def _needed_slot(self, pos: Position) -> int:
        d = self._check_delay_set()
        x_max = pos.sd + d + max(self._k(lat, pos.sps_entry) for lat in LATS)
        x_exit = pos.xs + d + F.exit_lag_slots(pos.sps_exit)  # type: ignore[operator,arg-type]
        return max(x_max, x_exit) + 1

    def _span(self, pos: Position, need: int, src: str) -> tuple[int, int, int, int]:
        """(t_lo, t_hi, s_lo, s_hi) of decision-to-exit. A derived_last exit extends the span to the resolution instant (the prints it stands in for may sit in
        a gap). Being derived is NOT an exclusion by itself."""
        t_hi = pos.exit_ms + int((F.exit_lag_slots(pos.sps_exit) + 1) * pos.sps_exit * 1000)  # type: ignore[operator,arg-type]
        s_hi = need
        if src == "derived_last":
            t_hi, s_hi = max(t_hi, self.hw_ms or t_hi), max(s_hi, self.hw_slot)
        return pos.decision_ms, t_hi, pos.sd, s_hi

    def _gap_hit(self, prog: str, span: tuple[int, int, int, int]) -> Gap | None:
        t_lo, t_hi, s_lo, s_hi = span
        for g in self.gaps:
            if g.prog not in (None, prog, "all") and not (g.prog == "pumpfun" and prog == "pump_bonding"):
                continue
            if (g.t_lo <= t_hi and g.t_hi >= t_lo) or (g.s_lo <= s_hi and g.s_hi >= s_lo):
                return g
        return None

    def _legs_of(self, pos: Position) -> tuple[list[int], int]:
        """(entry landing slots for both latencies, the exit leg's pricing slot before any mark-through shift)."""
        d = self._check_delay_set()
        lands = [pos.sd + d + self._k(lat, pos.sps_entry) for lat in LATS]
        return lands, (pos.mark[0] if pos.mark is not None else pos.xs) + d + F.exit_lag_slots(pos.sps_exit) + 1  # type: ignore[operator,arg-type]

    @staticmethod
    def _legs_hit(lands: Sequence[int], x_trig: int, lo: int, hi: int, back: int) -> bool:
        return any(lo - back <= x <= hi for x in lands) or lo - back <= x_trig < lo

    def _fill_hit(self, pos: Position) -> bool:
        """F2 / QP C4: True if an entry leg (either latency) of `pos` lands in [lo - back_slots, hi] of a filled range of its program, or its exit leg lands in
        [lo - back_slots, lo). An exit leg inside [lo, hi] is not a hit: it is marked through."""
        if not self.fills or pos.xs is None:
            return False
        lands, x_trig = self._legs_of(pos)
        for prog, lo, hi, back in self.fills:
            if prog not in (None, pos.prog, "all") and not (prog == "pumpfun" and pos.prog == "pump_bonding"):
                continue
            if self._legs_hit(lands, x_trig, lo, hi, back):
                return True
        return False

    def _exclusion(self, pos: Position, h: PoolHist, src: str, need: int) -> str | None:
        """DEC-031 section 1.4: a logged data_loss gap in the decision-to-exit span, or a needed print without V, excludes the trade (counted, never priced).
        A position whose open row has no call_feed_slot cannot take the 15% guard reference from the engine's own feed slot: it is excluded (guard_ref_missing,
        counted, never priced) and the guard never falls back to a slot the bot named."""
        if pos.call_feed_slot is None:
            return "guard_ref_missing"
        if self._gap_hit(pos.prog, self._span(pos, need, src)) is not None or self._fill_hit(pos):
            return "feed_gap"
        if pos.prog == "pumpswap":
            a = h.arrays()
            sel = (a["sl"] >= pos.sd) & (a["sl"] <= need)
            if (~a["vok"][sel]).any():
                return "missing_v"
        return None

    def _v_sources(self, pos: Position, h: PoolHist) -> dict[str, Any]:
        """Where V came from for the print that defines the entry state (1.3 s leg), the print that defines the exit state, and every print in the span."""
        if pos.prog != "pumpswap":
            return {"entry": "na", "exit": "na", "span": {}}
        d = self._check_delay_set()
        a = h.arrays()
        sl = a["sl"]
        n = len(sl)
        k13 = self._k(LATS[0], pos.sps_entry)
        je = min(int(np.searchsorted(sl, pos.sd + d + k13 + 1, "left")), n - 1)
        jx = min(int(np.searchsorted(sl, pos.xs + d + F.exit_lag_slots(pos.sps_exit) + 1, "left")), n - 1)  # type: ignore[operator,arg-type]
        lo, hi = int(np.searchsorted(sl, pos.sd, "left")), min(int(np.searchsorted(sl, pos.xs + d + 1, "right")), n)  # type: ignore[operator]
        span: dict[str, int] = {}
        for x in h.vsrc[lo:hi]:
            span[x] = span.get(x, 0) + 1
        return {"entry": h.vsrc[je], "exit": h.vsrc[jx], "span": span}

    def _try_pending(self, pool: str, final: bool) -> None:
        ids = self.pending.get(pool)
        if not ids:
            return
        h = self.pools[pool]
        keep = []
        for pid in ids:
            pos = self.positions[pid]
            if pos.bot_id in self.halted:  # not priced while its ledger is damaged; stays open (an orphan in the read)
                keep.append(pid)
                continue
            self._mark_through(pos)
            need = self._needed_slot(pos)
            ready = h.last_slot >= need or self.hw_slot >= need + self.cfg.grace_slots or final
            if not ready:
                keep.append(pid)
                continue
            src = "tape" if h.last_slot >= need else "derived_last"
            try:
                why = self._exclusion(pos, h, src, need)
                if why:
                    self._write_excluded(pos, why, src)
                else:
                    self._price_and_write(pos, h, src)
                trig = h.keys[int(np.searchsorted(h.arrays()["sl"], need, "left"))] if src == "tape" else None
                self.priced.setdefault(pool, []).append({"pid": pid, "bot_id": pos.bot_id, "prog": pos.prog, "entry_ms": pos.decision_ms, "need": need,
                                                         "trigger": trig, "span": self._span(pos, need, src),
                                                         "lands": self._legs_of(pos)[0], "x_trig": self._legs_of(pos)[1]})
            except Exception as exc:  # a pricing failure must not drop the position silently: it stays open (an orphan in the read)
                self.last_error[pid] = f"{type(exc).__name__}: {exc}"
                self._count("price_errors")
                if isinstance(exc, LedgerCorrupt):
                    self._count("ledger_torn_tail")
                    self._halt(pos.bot_id, f"pricing write refused: {str(exc)[:120]}")
                keep.append(pid)
                continue
            del self.positions[pid]
        if keep:
            self.pending[pool] = keep
        else:
            self.pending.pop(pool, None)

    def flush(self) -> None:
        """End of a finite tape: price every pending sell on the derived state after the last print (stack behaviour)."""
        for p in list(self.pending):
            self._try_pending(p, final=True)
        self.ledger.flush_incidents()

    def prune(self) -> int:
        """Drop the history of pools with no open position that have been quiet for HIST_KEEP_SLOTS."""
        self.ledger.flush_incidents()  # housekeeping: retry incident lines the disk would not take earlier
        busy = {p.pool for p in self.positions.values()}
        drop = [k for k, h in self.pools.items() if k not in busy and h.last_slot < self.hw_slot - HIST_KEEP_SLOTS]
        for k in drop:
            del self.pools[k]
        return len(drop)

    def trim_history(self) -> int:
        """Trim the print lists of every pool with no open, pending or retrying position to the prints a later buy can read: the anchor (the last print below the
        cut) and every print from cut = hw_slot - TRIM_KEEP_SLOTS on (PoolHist.trim; the derivation is at TRIM_KEEP_SLOTS). Returns the number of prints dropped
        (also counters["hist_trimmed_prints"]). Opt-in: nothing calls it unless a host does, and prune() is unchanged.

        It never drops a pool and never touches a pool's scalar state (mint, venue, last_v_row, the V fallback of _v_of): a revived pool prices exactly as it would
        have with its whole history. A pool with a position keeps all its prints. It changes no price, slot, fill, V source or exclusion: it removes only prints no
        later buy can read, and a buy that would read below a pool's trim floor is refused instead (buy)."""
        busy = {p.pool for p in self.positions.values()}
        cut = self.hw_slot - TRIM_KEEP_SLOTS
        n = sum(h.trim(cut) for k, h in self.pools.items() if k not in busy)
        if n:
            self.counters["hist_trimmed_prints"] = self.counters.get("hist_trimmed_prints", 0) + n
        return n

    @staticmethod
    def _ref_slot(pos: Position) -> int:
        """The 15% guard reference slot: the engine's OWN latest feed slot at the buy() call (call_feed_slot), i.e. what a live executor would quote when it builds the
        transaction. The bot's decision_slot is ignored by the guard entirely (a bot must not pick its own slippage reference: an older slot with a higher earlier
        price would revert fewer buys, a future one would read prices it never saw); decision_slot can only push the LANDING later (sd = max(decision_slot, mapped
        write instant)). Same convention as the stack: the reference price is the post-trade price of the last print BEFORE this slot. There is NO fallback: a position
        without call_feed_slot (an open row from before the field existed) is refused here, and `_exclusion` writes it as an excluded trade (guard_ref_missing) first."""
        if pos.call_feed_slot is None:
            raise ArenaRefused(f"guard_ref_missing: position {pos.position_id} has no call_feed_slot: the guard reference is never taken from the bot's decision_slot")
        return pos.call_feed_slot

    def leg_inputs(self, pos: Position, h: PoolHist) -> dict[str, Any]:
        """Entry / exit states for both latencies (no stake yet). The fill lands d slots (the feed delay) later than the mapped decision slots; the guard's
        decision-time spot is the price at the engine's own latest feed slot at the call (call_feed_slot), never at a slot the bot names."""
        cfg = self.cfg
        d = self._check_delay_set()
        a = h.arrays()
        k = {lat: self._k(lat, pos.sps_entry) for lat in LATS}
        lag = F.exit_lag_slots(pos.sps_exit)  # type: ignore[arg-type]
        sdc, xsc = pos.sd + d, pos.xs + d  # type: ignore[operator]
        if pos.prog == "pumpswap":
            args = (a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"])
            ms = mint_states(*args, np.array([sdc]), pos.sps_entry, t_marks=np.array([0]), slot_s_marks=np.array([pos.sps_exit]), clock_slot_marks=np.array([xsc]),
                             lats=LATS, rounding=cfg.k_rounding)
            # the 15% guard compares against the price at the engine's latest feed slot at the call (call_feed_slot): engine-owned, the bot's decision_slot is ignored here
            ref_slot = self._ref_slot(pos)
            spot = ms["spot"][0] if ref_slot == sdc else mint_states(*args, np.array([ref_slot]), pos.sps_entry, lats=(LATS[0],), rounding=cfg.k_rounding)["spot"][0]
            return {"ref": float(spot), "Qe": {l: float(ms["Qe"][l][0]) for l in LATS}, "Be": {l: float(ms["Be"][l][0]) for l in LATS},
                    "ssb": {l: float(ms["ssb"][l][0]) for l in LATS}, "nearby": {l: float(ms["nearby"][l][0]) for l in LATS},
                    "Qx": float(ms["Qx"][0]), "Bx": float(ms["Bx"][0]), "land": {l: sdc + k[l] for l in LATS}}
        x_land = {l: sdc + k[l] for l in LATS}
        bs = bonding_states(h, self._ref_slot(pos), x_land, xsc + lag, int(round(F.NEARBY_WINDOW_S / pos.sps_entry)))  # type: ignore[operator]
        bs["land"] = x_land
        return bs

    def _base_row(self, pos: Position, kind: str, s_sol: float, src: str | None) -> dict[str, Any]:
        row = self._base_row_core(pos, kind, s_sol, src)
        if pos.mark is not None:  # only a marked-through exit carries the key: every other row is byte for byte what it was
            row["mark_through"] = {"xs_before": pos.mark[0], "filled_from_slot": pos.mark[1], "filled_to_slot": pos.mark[2], "xs": pos.xs}
        return row

    def _base_row_core(self, pos: Position, kind: str, s_sol: float, src: str | None) -> dict[str, Any]:
        return {"schema": SCHEMA, "kind": kind, "trade_id": f"{pos.position_id}-s{int(round(s_sol * 1000))}", "position_id": pos.position_id,
                "bot_id": pos.bot_id, "frozen_sha": pos.frozen_sha, "decision_ms": {"entry": pos.decision_ms, "exit": pos.exit_ms},
                "stamp_ms": {"entry": pos.stamp_ms, "exit": pos.exit_stamp_ms}, "call_ms": {"entry": pos.call_ms, "exit": pos.exit_call_ms},
                "write_lag_ms": {"entry": pos.decision_ms - pos.stamp_ms, "exit": None if pos.exit_ms is None else pos.exit_ms - (pos.exit_stamp_ms or 0)},
                "decision_slot": {"entry": pos.decision_slot, "exit": pos.exit_slot}, "effective_slot": {"entry": pos.sd, "exit": pos.xs},
                "corrected_slot": {"entry": pos.sd + (self.cfg.feed_delay_slots or 0), "exit": None if pos.xs is None else pos.xs + (self.cfg.feed_delay_slots or 0)},
                "feed_delay_slots": self.cfg.feed_delay_slots, "guard_ref_slot": pos.call_feed_slot,
                "pool": pos.pool, "mint": pos.mint, "prog": pos.prog, "stake_sol": s_sol, "bot_sol_in": pos.bot_sol_in, "exit_reason": pos.reason,
                "meta": pos.meta, "state_src": src, "stack": dict(self.cfg.as_dict(), sps_entry=pos.sps_entry, sps_exit=pos.sps_exit),
                "written_ms": self.now_fn()}

    def _write_excluded(self, pos: Position, reason: str, src: str | None) -> None:
        for s_sol in self.cfg.stakes_sol:
            self.ledger.append(dict(self._base_row(pos, "excluded", s_sol, src), exclusion_reason=reason, legs=None, calc=None))
        self._count(f"excluded_{reason}")

    def build_rows(self, pos: Position, h: PoolHist, src: str) -> list[dict[str, Any]]:
        cfg = self.cfg
        st = self.leg_inputs(pos, h)
        vs = self._v_sources(pos, h)
        rules = rules_of(cfg)
        rows = []
        for s_sol in cfg.stakes_sol:
            stake = float(round(s_sol * 1e9))
            legs: dict[str, Any] = {r: {} for r in rules}
            calc: dict[str, Any] = {}
            for lat in LATS:
                tag = LAT_TAG[lat]
                calc[tag] = {"landing_slot": int(st["land"][lat]), "Qe": st["Qe"][lat], "Be": st["Be"][lat], "spot": st["ref"], "ssb": st["ssb"][lat],
                             "nearby_lamports": st["nearby"][lat], "Qx": st["Qx"], "Bx": st["Bx"], "by_rule": {}}
                for rule in rules:
                    r = price_leg(pos.prog, stake, st, lat, cfg, rule)
                    legs[rule][f"flat_{tag}"] = {"state": r["state"], "net_lamports": r["flat"]}
                    legs[rule][f"press_{tag}"] = {"state": r["state"], "net_lamports": r["press"]}
                    calc[tag]["by_rule"][rule] = {"pnl_nofail": r["pnl"], "filled": r["filled"], "state": r["state"], "strand": r["strand"],
                                                  "e1_extra": r["e1_extra"], "p_press_provisional": r["p"]}
            rows.append(dict(self._base_row(pos, "trade", s_sol, src), legs=legs, calc=calc, v_src=vs))
        return rows

    def _price_and_write(self, pos: Position, h: PoolHist, src: str) -> None:
        for row in self.build_rows(pos, h, src):
            self.ledger.append(row)


def row_digest(rows: list[Mapping[str, Any]]) -> str:
    return hashlib.md5("\n".join(canon(r) for r in rows).encode()).hexdigest()


__all__ = ["LedgerCorrupt", "LedgerWriteFailed", "MIN_FREE_PCT_DEFAULT", "MIN_FREE_PCT_FLOOR", "process_code_state", "identity", "TEST_ENV", "line_bytes", "verify_sha_on_origin", "ArenaEngine", "ArenaRefused", "Ledger", "LedgerConflict", "StackConfig", "register_bot", "stop_bot", "load_registry", "curve_trip", "price_leg",
           "book_fail_legs", "LEG_KEYS", "STAKES_SOL", "LATS", "SCHEMA", "STRAND_STATES", "canon", "utc_day"]
