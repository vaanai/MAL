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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from tools import h5_executor as h

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
CANARY_DEC_FILE = "DEC/DEC-032-h5-arena-canary.md"  # the new canary DEC (does not exist yet: a live start is refused until it does)
CANARY_OWNER_RE = re.compile(r"^[ \t]*OWNER_CANARY_CONFIRMED:[ \t]*\S", re.M)
FEED_NOTE_MERGED = False  # scope item 17: the EXP-024 note declaring this executor's observation. False = replay_only
LIVE_BUILT = False  # this PR builds no live entry point
PAPER_FAIL_RATE = 0.15  # the flat fail model of the gate; landing-fail excess is measured against it


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

    @property
    def q_star_sol(self) -> float:
        return self.q_star_lamports / 1e9


BOOKS: dict[str, Book] = {b.bot_id: b for b in (
    Book("h5v1-boostfloor-a2", A2_SHA, A2_RULE, "v1", 40 * 10**9, A2_CLOCK_MS, SEAL_END_MS, 0, None, None),
    Book("h5v2-boostclock-a2", A2_SHA, A2_RULE, "v2", 40 * 10**9, A2_CLOCK_MS, SEAL_END_MS, S0_MARGIN_V2_MS, None, None),
    Book("h5v1q50-boostfloor-a1", Q50_SHA, Q50_RULE, "v1", 50 * 10**9, Q50_CLOCK_MS, SEAL_END_MS, S0_MARGIN_V2_MS, 50.0, Q50_RULE_SHA256),
)}

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
    if cfg.get("pick_file") or cfg.get("pick_replay_files"):
        return "pick_file_set"  # the CAP-PICK oracle no longer exists (DEC-031 s3.5): a configured one is a wrong config, never silently ignored
    if not FEED_NOTE_MERGED and cfg.get("replay_only") is not True:
        return "feed_note_unmerged_replay_only"
    if live:
        if not LIVE_BUILT:
            return "live_not_built"
        why = h.live_ok_valid()
        if why:
            return why
        if cfg.get("end_ms") is None:
            return "end_ms_missing"
        dec = root / CANARY_DEC_FILE
        try:
            if not (dec.is_file() and not dec.is_symlink() and CANARY_OWNER_RE.search(dec.read_text())):
                return "canary_dec_owner_line_missing"
        except OSError:
            return "canary_dec_unreadable"
    if book.exit_pin == h.EXIT_MODE_V2:
        return v2_start_refusal()
    return None


def v2_start_refusal() -> str | None:
    """The v2 rule source by sha256 (replaces the EXP-026 file, withdrawn 2026-10-17). Always run when the book is pinned v2."""
    try:
        data = V2_RULE_SOURCE.read_bytes()
    except OSError:
        return "v2_rule_source_unreadable"
    return None if hashlib.sha256(data).hexdigest() == V2_RULE_SHA256 else "v2_rule_source_sha_mismatch"


class FidelityMonitor:
    """Pure counters for the item-14 soft halts. Each `note_*` returns the halt name to latch, or None. No network, no price of its own: the paper
    comparator values arrive from the caller (entry: the trigger-quote expected tokens; exit: the parity join, which runs only under the item-17 note)."""

    def __init__(self) -> None:
        self.entry_gaps: list[float] = []
        self.exit_gaps: list[float] = []
        self.attempts = 0
        self.failed = 0
        self.late_trips = 0
        self.stuck: set[str] = set()
        self.late_slots: list[int] = []

    def note_entry_gap(self, bps_worse: float) -> str | None:
        self.entry_gaps.append(float(bps_worse))
        n = len(self.entry_gaps)
        return H_ENTRY_GAP if n >= ENTRY_GAP_MIN_N and sum(self.entry_gaps) / n > ENTRY_GAP_BPS else None

    def note_exit_gap(self, bps_worse: float) -> str | None:
        self.exit_gaps.append(float(bps_worse))
        n = len(self.exit_gaps)
        return H_EXIT_GAP if n >= EXIT_GAP_MIN_N and sum(self.exit_gaps) / n > EXIT_GAP_BPS else None

    def note_attempt(self, landed: bool) -> str | None:
        self.attempts += 1
        self.failed += 0 if landed else 1
        if self.attempts < LANDING_MIN_N:
            return None
        excess = 100.0 * (self.failed / self.attempts - PAPER_FAIL_RATE)
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
        self.book = book
        self.EXIT_MODE_PIN = book.exit_pin  # instance pin, read by the base __init__ (item 5)
        why = arena_start_refusal(book, cfg, root or h.repo_root(), live=keypair is not None)
        if why:
            raise SystemExit(f"arena executor start refused: {why}")
        cfg = dict(cfg)
        end = cfg.get("end_ms")
        cfg["end_ms"] = book.end_ms_seal if end is None else min(int(end), book.end_ms_seal)  # item 7: clamped, never later
        days = cfg.get("max_days")
        if days is not None:
            cfg["max_days"] = min(float(days), (book.end_ms_seal - book.clock_start_ms) / 86_400_000)
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

    def __repr__(self) -> str:
        return f"ArenaExecutor(book={self.book.bot_id}, mode={self.run_mode})"

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
        if self.book.exit_pin == h.EXIT_MODE_V2 and self.slices_taken == 0:
            return REFUSAL_V2_FEED
        if trg.q_lamports > self.book.q_star_lamports:
            return REFUSAL_Q
        if trg.decision_ms < self.book.clock_start_ms or trg.decision_ms >= self.book.end_ms_seal or \
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
            why = start_record_problem(self.book, row)
            self.starts_seen += 1
            if why:
                self.feed_verified = False
                self._latch_feed_break(why)
                return "drop", None
            self._start_ms = int(row["start_ms"])
            self.feed_verified = True
            return "drop", None
        if t == "boost_slice":
            self.slices_taken += 1 if self._slice_format_ok(row) else 0
            return "keep", None
        if t != "trigger" or row.get("variant") != h.TRIGGER_VARIANT:
            return "keep", None
        d = row.get("t_detect_ms")
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            return "keep", None  # the parser will refuse it with its own reason
        if int(d) < self.book.clock_start_ms or (self._start_ms is not None and int(d) < self._start_ms) or int(d) >= self.book.end_ms_seal:
            return self._window("drop", REFUSAL_DECISION_WINDOW)
        s0 = row.get("s0_t_recv_ms")
        if self.book.s0_margin_ms or s0 is not None:
            if isinstance(s0, bool) or not isinstance(s0, int) or s0 < self.book.clock_start_ms + self.book.s0_margin_ms or s0 >= self.book.end_ms_seal:
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
            kw = {**kw, "bot_id": self.book.bot_id, "frozen_sha": self.book.frozen_sha, "trigger_key": [mint, trg[0], trg[1]]}
        elif kind in ("dry_sell", "sell_sent", "exit_landing"):
            kw = {**kw, "bot_id": self.book.bot_id, "frozen_sha": self.book.frozen_sha}
        super()._log(kind, mint, **kw)

    def _latch_name(self, name: str | None, **detail: Any) -> None:
        if name:
            self._latch(name, **detail)

    def latch_external(self, name: str, **detail: Any) -> None:
        """For the checks that live outside this process (the parity join's exit gap, A3 pins_changed / program_changed)."""
        if name not in ARENA_HALTS:
            raise ValueError(f"{name!r} is not an arena halt")
        self._latch(name, **detail)

    def _fire_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        self._latch_name(self.monitor.note_sell_send(pos["h5"]["plan"]["send_slot"], est), mint=mint, est_slot=est)
        super()._fire_sell(mint, pos, est, emergency)

    def exit_tick(self, now: int) -> None:
        super().exit_tick(now)
        for mint, pos in self.state.open.items():
            if pos.get("stuck") and mint not in self.monitor.stuck:
                self._latch_name(self.monitor.note_stuck(mint), mint=mint)
            if pos.get("close_blocked") or pos.get("strand"):
                self._latch(H_STRAND, mint=mint)

    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        super()._finish_buy(mint, p, m)
        pos = self.state.open.get(mint)
        self._latch_name(self.monitor.note_attempt(pos is not None), mint=mint)
        exp = int(p.get("q_tokens") or 0)
        if pos is not None and exp > 0:
            self._latch_name(self.monitor.note_entry_gap(1e4 * (exp - int(pos.get("tokens") or 0)) / exp), mint=mint)

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
