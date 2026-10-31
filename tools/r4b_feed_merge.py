#!/usr/bin/env python3
"""r4b_feed_merge: the r4-b live feed swap, library half. PAPER / DRY ONLY: no wallet, no send, LIVE_BUILT is False. Nothing here runs a network job by itself;
tests use synthetic rows and fake sockets in tmp dirs.

Why (QP-RULING-1030 Addenda 3 and 4; DEC-032 Amendment 8): r4-b's decision at mark T reads every print with slot < sd(T). The public arena feed (tools/arena_feed.py,
tape rows in tools/arena_tape_schema.md) delivers about 7 slots late; a Helius websocket opened only in [T - 30 s, T + 10 s] delivered all decision inputs 224 ms (p50)
after the mark in probe #1916 (n = 11 windows; PR #1133). The live decision host therefore needs the public tape for history and a Helius window for the tail, merged
into ONE stream for tools/r4b_feed_adapter. This module is that merge and its guards. The host loop (reading the tape, scheduling marks, calling the engine) is a
separate PR.

What is here (Addendum 3 proofs 1, 4, 5, 6 in code; 2 and 3 need the host, a DRY run and, for 2, the EXP-027 read)
  HeliusWindow     one websocket per mark: opens at T - 30 s, subscribes slotSubscribe + logsSubscribe on PumpSwap and pump.fun (the probe's own requests, commitment
                   "processed" by default, compression off), closes at T + 10 s, or when the caller says the mark is complete (complete() / stop_when), or on the first
                   failure. Each logsNotification becomes tape rows with tools/arena_feed.rows_from_notice (imported, not copied). It keeps per mark: payload bytes, the
                   processed tip slots with receive times, connect / armed / first-note times. No row is kept; rows go to on_rows.
  Merger           public tape rows + window rows -> one stream, deduplicated on (signature, type, event_index) inside a bounded window (slots + a hard key cap). The
                   first arrival is emitted; the later copy is dropped and counted. First-seen source and time are kept per key; counts per source, per (first, dup)
                   pair; seam_report() says, for a slot range, which keys were seen by both sources or by one only (the measure of "did the window miss anything").
  DailyCreditCap   credits at 3 per 0.1 MB (the probe's CAP_RATE), a per-UTC-day cap (required argument), state in an atomic JSON file. At the cap no window opens and
                   buy_hold() returns a reason. Exits are not this module's business. A state file that cannot be read stops the day (fail closed).
  ntp_status / require_synced   timedatectl NTPSynchronized and, if chronyc exists, its system-time offset; require_synced raises when not synced or over the bound.
  mark_complete / window_failed / tip_silence   pure completeness and failure tests (below).

Completeness (Addendum 3 item 1 and 4), exactly what mark_complete(T, sd, state) tests
  The window delivers the slots from covered_from_slot on, where covered_from_slot = (the first processed tip slot received AFTER all three subscriptions were
  acknowledged) + 1 (a slot that was already running when the subscription started may be partly missing). Slots below it come from the public tape. The mark is
  complete when, with sd = sd(T) from the engine's clock:
    * no failure (connect, subscribe error, bad frame, decode error, early close, silence, cap, callback error) was recorded at or before the instant the tip first
      reached sd + 1 (a failure after that instant is irrelevant to this mark);
    * the tip reached sd + 1 (the lane's rule: "tip >= sd + 1 seen"). ASSUMPTION, NOT MEASURED HERE: a processed slotNotification for slot n + 1 arrives after the
      logs of slot n; Merger.seam_report during the DRY run is the measure (public-only keys inside the window's range = rows the window missed);
    * the window covered sd itself (covered_from_slot <= sd);
    * no gap between consecutive tip notices (from the arming instant to that instant) exceeded silence_s (2 s): the socket did not go silent while it was needed;
    * the seam is closed: the public stream has reached covered_from_slot (+ margin), so the slots below the window's coverage came from the public tape
      (require_seam=False only for a window-only dry check).
  Anything else returns complete=False with a reason. The caller then does what Addendum 3 item 4 says: wait for the public feed to complete sd(T) (late, flagged) or
  skip the mark's entries and log it. It never decides on incomplete inputs. window_gap_row() turns a failed window into a tape-format `gap` row (data_loss true) so
  tools/r4b_feed_adapter holds buys on it (DEC-032 section 5 item 2, PR #1136).

Disclosed, not measured
  * No live run: the socket code is tested against fake sockets only. Latency in slots (Proof 3), the sd-completeness assumption above, and the Helius processed
    versus public confirmed row sets are DRY-run questions.
  * Within-slot order is arrival order (neither source has tx_index). When both sources deliver the same slot, the first copy decides the order of that slot's rows.
  * A row seen only by the window (processed commitment) whose slot later forks away is emitted and is not removed; seam_report() counts window-only keys that the
    public stream should have delivered by now (fork candidates, Bar 2 condition 2).
  * A duplicate that arrives after its key left the dedup window is emitted again; rows below the window's horizon at arrival are counted (below_horizon).
  * One thread (asyncio), like the engine. Frames of one window are handled in order.
  * Credit accounting counts every received payload byte (acks and slot notices included) at 3 credits per 0.1 MB; it is an estimate of the Helius meter, not the meter.
  * The cap file is flushed every persist_every_bytes (1 MB, which is 30 credits at the 3-credit rate) and at each window end, so a kill can lose at most that much
    of the count. Reaching the cap is written at once.
  * The tips are the PROCESSED tip whatever commitment the logs use: slotSubscribe has no commitment parameter (the probe's "processed tip"). With commitment
    "confirmed" the logs arrive later than the tip, so a window on it is complete later; the lane's pre-declaration of which commitment decides is Proof 3's.
  * The dedup window is in SLOTS (the chain's time axis), not wall time, so the replay speed of the bulk history cannot evict a key early; a hard key cap bounds memory.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Iterator, Mapping, Sequence

if __package__ in (None, ""):  # `python tools/r4b_feed_merge.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import r4b_feed_lag_probe as lp  # noqa: E402

LIVE_BUILT = False  # this module builds no live path: no key use beyond the websocket URL, no wallet, no send
SCHEMA = "r4b_feed_merge_v1"
PRE_S, POST_S = lp.PRE_S, lp.POST_S  # the window is [T - 30 s, T + 10 s]
COMMITMENTS = ("processed", "confirmed")
SOURCE_PUBLIC, SOURCE_HELIUS = "public", "helius"
SOURCES = (SOURCE_PUBLIC, SOURCE_HELIUS)
CREDITS_PER_TENTH_MB = lp.CAP_RATE  # 3: the higher of the two published rates, as the probe's cap
SILENCE_S = 2.0  # QP Addendum 3 item 4: silence over 2 s fails the window
TICK_S = 0.2  # the receive loop wakes at least this often (silence, close request, deadline)
DEDUP_HORIZON_SLOTS = 1_500  # keys older than this many slots behind the newest slot seen are dropped (about 10 minutes of chain time)
DEDUP_MAX_KEYS = 200_000  # hard cap on remembered keys (the tape runs at roughly 100 rows/s, so this is far more than the overlap needs)
PERSIST_EVERY_BYTES = 1_000_000
NTP_MAX_OFFSET_MS = 50.0
NTP_TIMEOUT_S = 3.0
HISTORY_DAYS = 400
EXPECTED_ACK_IDS = (1, 2, 3)  # the ids of tools.r4b_feed_lag_probe.requests(): slotSubscribe, logsSubscribe pumpswap, logsSubscribe pump.fun
KEYLESS_OK = ("gap", "hb", "start", "stop", "pool_info")  # tape rows with no transaction: never deduplicated


def utc_day(t_s: float) -> str:
    return datetime.fromtimestamp(t_s, tz=timezone.utc).strftime("%Y-%m-%d")


def payload_bytes(raw: str | bytes) -> int:
    """Uncompressed payload size of one frame, as the probe counts it (compression is off on the socket)."""
    return len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)


def credits_of(n_bytes: float, rate: float = CREDITS_PER_TENTH_MB) -> float:
    return n_bytes / 1e5 * rate


# ------------------------------------------------------------------------------------------------------------------------------------ window state
@dataclass
class WindowState:
    """What one mark's window saw. Pure data: mark_complete / window_failed read it, HeliusWindow fills it."""

    mark: int
    commitment: str = "processed"
    connect_ms: int | None = None  # the connect attempt started
    connected_ms: int | None = None
    armed_ms: int | None = None  # all three subscriptions acknowledged
    first_note_ms: int | None = None
    closed_ms: int | None = None
    close_reason: str | None = None  # deadline | complete | failed | cap_closed | too_late
    tips: list = field(default_factory=list)  # (recv_ms, processed tip slot) in arrival order
    acks: set = field(default_factory=set)
    notes: int = 0  # logsNotification frames
    rows: int = 0
    bytes: int = 0
    bad_frames: int = 0
    decode_errors: int = 0
    cross_dedup: int = 0  # a transaction delivered by both program subscriptions, decoded once
    failure: str | None = None
    failure_ms: int | None = None
    opened_late_ms: int = 0
    counters: dict = field(default_factory=dict)  # arena_feed.rows_from_notice's counters (e.g. bonding_extras_mismatch), copied at the end of run()

    def fail(self, kind: str, at_ms: int) -> None:
        if self.failure is None:  # the first failure stands
            self.failure, self.failure_ms = kind, int(at_ms)

    def credits(self, rate: float = CREDITS_PER_TENTH_MB) -> float:
        return credits_of(self.bytes, rate)

    def as_record(self) -> dict:
        """The per-mark record (JSON-able): bytes, credits, tips with receive times, connect / armed / first-note times, failure."""
        return {"tool": "r4b_feed_merge", "schema": SCHEMA, "mark": self.mark, "commitment": self.commitment, "bytes": self.bytes,
                "credits_at_3_per_0.1MB": round(self.credits(), 3), "connect_ms": self.connect_ms, "connected_ms": self.connected_ms,
                "armed_ms": self.armed_ms, "first_note_ms": self.first_note_ms, "closed_ms": self.closed_ms, "close_reason": self.close_reason,
                "tips": [[r, s] for r, s in self.tips], "notes": self.notes, "rows": self.rows, "bad_frames": self.bad_frames,
                "decode_errors": self.decode_errors, "cross_dedup": self.cross_dedup, "failure": self.failure, "failure_ms": self.failure_ms,
                "opened_late_ms": self.opened_late_ms, "counters": dict(self.counters)}


@dataclass(frozen=True)
class Completeness:
    complete: bool
    reason: str  # "ok" or why not
    ready_ms: int | None = None  # receive time of the first tip >= sd + 1
    ready_tip_slot: int | None = None
    covered_from_slot: int | None = None
    ready_after_mark_ms: int | None = None


def covered_from_slot(st: WindowState) -> int | None:
    """First slot the window covers in full: the first processed tip received after the arming instant, plus 1. None until armed and a tip has arrived."""
    if st.armed_ms is None:
        return None
    for r, s in st.tips:
        if r >= st.armed_ms:
            return int(s) + 1
    return None


def tip_silence(st: WindowState, now_ms: int, silence_s: float = SILENCE_S) -> bool:
    """True when no tip notice has arrived for more than silence_s (measured from the last tip, else from the connection)."""
    ref = st.tips[-1][0] if st.tips else st.connected_ms
    return ref is not None and now_ms - ref > silence_s * 1000


def window_failed(st: WindowState, now_ms: int | None = None, silence_s: float = SILENCE_S) -> str | None:
    """The failure reason of a window, else None: connect_failed, rpc_error, bad_frame, decode_error, closed_early:<Class>, silence, cap_reached,
    cap_closed, callback_error, too_late (recorded), or silence now (given now_ms, while the window is open)."""
    if st.failure is not None:
        return st.failure
    if now_ms is not None and st.closed_ms is None and tip_silence(st, now_ms, silence_s):
        return "silence"
    return None


def _max_tip_gap_ms(st: WindowState, upto_ms: int) -> int:
    """Largest gap between consecutive tip notices from the arming instant to upto_ms."""
    pts = [st.armed_ms] + [r for r, _ in st.tips if st.armed_ms <= r <= upto_ms]
    return max((b - a for a, b in zip(pts, pts[1:])), default=0)


def mark_complete(T: int, sd: int | None, st: WindowState, *, public_hw_slot: int | None = None, seam_margin_slots: int = 0, require_seam: bool = True,
                  silence_s: float = SILENCE_S) -> Completeness:
    """Has the window delivered every slot below sd(T) and the slot sd itself? See the module docstring. Pure. `sd` is the engine clock's sd(T) (None: unknown yet);
    `public_hw_slot` the highest slot the public stream has delivered with no gap."""
    if st.mark != T:
        return Completeness(False, "wrong_mark")
    ready = next(((r, s) for r, s in st.tips if sd is not None and s >= sd + 1), None)
    if st.failure is not None and (ready is None or st.failure_ms is None or st.failure_ms <= ready[0]):
        return Completeness(False, f"window_failed:{st.failure}")
    if sd is None:
        return Completeness(False, "sd_unknown")
    if st.armed_ms is None:
        return Completeness(False, "not_armed")
    cov = covered_from_slot(st)
    if ready is None:
        return Completeness(False, "tip_below_sd_plus_1", covered_from_slot=cov)
    ready_ms, ready_slot = ready
    after = ready_ms - T * 1000
    base = {"ready_ms": ready_ms, "ready_tip_slot": ready_slot, "covered_from_slot": cov, "ready_after_mark_ms": after}
    if cov is None or cov > sd:
        return Completeness(False, "armed_late", **base)
    if _max_tip_gap_ms(st, ready_ms) > silence_s * 1000:
        return Completeness(False, "silence", **base)
    if require_seam:
        if public_hw_slot is None:
            return Completeness(False, "seam_unknown", **base)
        if public_hw_slot < cov + seam_margin_slots:
            return Completeness(False, "seam_open", **base)
    return Completeness(True, "ok", **base)


def window_gap_row(st: WindowState, reason: str | None = None, now_ms: int | None = None) -> dict:
    """A tape-format `gap` row (tools/arena_tape_schema.md) for a failed window: data_loss true, so tools/r4b_feed_adapter reports it as a GapRecord that halts buys."""
    why = reason or st.failure or "unknown"
    to_ms = st.failure_ms if st.failure_ms is not None else (st.closed_ms if st.closed_ms is not None else now_ms)
    frm = st.connect_ms if st.connect_ms is not None else (st.mark - PRE_S) * 1000
    t = to_ms if to_ms is not None else frm
    return {"v": 1, "type": "gap", "prog": "all", "slot": 0, "t_recv_ms": t, "signature": None, "event_index": None, "kind": "helius_window_failed",
            "data_loss": True, "from_ms": frm, "to_ms": t, "closed": True, "reason": why, "mark": st.mark, "commitment": st.commitment}


# ------------------------------------------------------------------------------------------------------------------------------------ credits
class DailyCreditCap:
    """Hard per-UTC-day credit cap for the mark windows, at `rate` credits per 0.1 MB (default 3) of received payload. `max_credits_per_day` is required.

    add(n) counts bytes and returns False once the cap is reached (the caller closes the socket); can_open() is False once it is reached, or when the largest
    window seen so far would not fit in what is left; buy_hold() returns a reason string exactly when can_open() is False (exits are not this module's business).
    State (today's bytes / windows / stopped, and the per-day record lines) is one JSON file written atomically (tmp, fsync, rename, fsync of the directory), so a
    restart sees the day's count. A state file that exists but cannot be read stops the day (fail closed) and is kept aside as <path>.corrupt-<time>.
    The clock is read on every call; a new UTC date starts a new day (the old one becomes a record line); a clock that moves back does not reset the count."""

    def __init__(self, max_credits_per_day: float, state_path: str | os.PathLike, *, clock: Callable[[], float] = time.time,
                 persist_every_bytes: int = PERSIST_EVERY_BYTES, rate: float = CREDITS_PER_TENTH_MB) -> None:
        if isinstance(max_credits_per_day, bool) or not isinstance(max_credits_per_day, (int, float)) or not math.isfinite(max_credits_per_day) or max_credits_per_day <= 0:
            raise ValueError("max_credits_per_day must be a positive finite number (it is required)")
        self.cap = float(max_credits_per_day)
        self.rate = float(rate)
        self.path = Path(state_path)
        self.clock = clock
        self.persist_every = int(persist_every_bytes)
        self.day = utc_day(self.clock())
        self.bytes = 0
        self.windows = 0
        self.stopped = False
        self.max_window_bytes = 0
        self.state_error: str | None = None
        self.history: list[dict] = []
        self.clock_back = 0
        self._unsaved = 0
        self._load()

    # -- state
    def credits(self, extra_bytes: float = 0) -> float:
        return credits_of(self.bytes + extra_bytes, self.rate)

    def _line(self, day: str, n_bytes: int, windows: int, stopped: bool) -> dict:
        return {"date": day, "bytes": n_bytes, "credits": round(credits_of(n_bytes, self.rate), 3), "windows": windows, "stopped": stopped}

    def day_lines(self) -> list[dict]:
        """Record lines: every closed day, then today (date, bytes, credits, windows, stopped)."""
        return list(self.history) + [self._line(self.day, self.bytes, self.windows, self.stopped)]

    def jsonl(self) -> str:
        return "".join(json.dumps(x, sort_keys=True) + "\n" for x in self.day_lines())

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            if doc.get("v") != 1 or not isinstance(doc.get("day"), str) or not isinstance(doc.get("bytes"), int) or doc["bytes"] < 0:
                raise ValueError("shape")
            hist = doc.get("history", [])
            if not isinstance(hist, list) or not all(isinstance(h, dict) for h in hist):
                raise ValueError("history")
            self.day, self.bytes, self.windows = doc["day"], doc["bytes"], int(doc.get("windows", 0))
            self.stopped, self.max_window_bytes = bool(doc.get("stopped", False)), int(doc.get("max_window_bytes", 0))
            self.history = hist[-HISTORY_DAYS:]
        except (OSError, ValueError, TypeError, AttributeError):
            self.state_error = "unreadable_state_file"
            try:
                os.replace(self.path, self.path.with_name(f"{self.path.name}.corrupt-{int(self.clock())}"))
            except OSError:
                pass
            self.day, self.windows, self.max_window_bytes = utc_day(self.clock()), 0, 0
            self.bytes, self.stopped = math.ceil(self.cap / self.rate * 1e5), True  # unknown spend: the day is treated as spent
            self.persist()
        self._roll()

    def persist(self) -> None:
        """Atomic write of the state (tmp in the same directory, fsync, rename, fsync of the directory)."""
        doc = {"v": 1, "cap_credits": self.cap, "rate": self.rate, "day": self.day, "bytes": self.bytes, "windows": self.windows, "stopped": self.stopped,
               "max_window_bytes": self.max_window_bytes, "state_error": self.state_error, "history": self.history[-HISTORY_DAYS:]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, sort_keys=True))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        dfd = os.open(str(self.path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        self._unsaved = 0

    flush = persist

    def _roll(self) -> None:
        today = utc_day(self.clock())
        if today < self.day:
            self.clock_back += 1  # never reset the count because the clock moved back
            return
        if today > self.day:
            self.history.append(self._line(self.day, self.bytes, self.windows, self.stopped))
            self.day, self.bytes, self.windows, self.stopped, self.state_error = today, 0, 0, False, None
            self.persist()

    # -- use
    def add(self, n_bytes: int) -> bool:
        """Count received bytes. False once the cap is reached (the caller closes its socket); the stop is persisted at once."""
        self._roll()
        self.bytes += int(n_bytes)
        self._unsaved += int(n_bytes)
        if self.credits() >= self.cap and not self.stopped:
            self.stopped = True
            self.persist()
        elif self._unsaved >= self.persist_every:
            self.persist()
        return not self.stopped

    def can_open(self, est_bytes: float | None = None) -> bool:
        """May a window open now? Not once the cap is reached today, and not if est_bytes (default the largest window seen) would not fit."""
        self._roll()
        if self.stopped or self.credits() >= self.cap:
            return False
        est = self.max_window_bytes if est_bytes is None else est_bytes
        return self.credits(est) < self.cap

    def window_done(self, window_bytes: int) -> None:
        self._roll()
        self.windows += 1
        self.max_window_bytes = max(self.max_window_bytes, int(window_bytes))
        self.persist()

    def buy_hold(self) -> str | None:
        """A reason to hold new buys (the next window cannot open today), else None. Exits continue; that is the caller's rule."""
        if self.can_open():
            return None
        why = "reached" if (self.stopped or self.credits() >= self.cap) else "no_room_for_a_window"
        return f"helius_credit_cap_{why}: {self.credits():.1f} of {self.cap:g} credits on {self.day} UTC (windows {self.windows})"


# ------------------------------------------------------------------------------------------------------------------------------------ clock
@dataclass(frozen=True)
class NtpStatus:
    synced: bool | None  # None: could not be determined
    offset_ms: float | None  # chrony system time, + = this host is ahead of NTP time; None when chronyc is not available
    detail: str = ""

    def as_record(self) -> dict:
        return {"ntp_synchronized": self.synced, "ntp_offset_ms": self.offset_ms, "detail": self.detail}


class ClockNotSynced(RuntimeError):
    pass


Runner = Callable[[Sequence[str], float], "tuple[int, str]"]


def default_runner(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    """Run a fixed argv (no shell); (returncode, stdout). 127 = not found, 124 = timeout."""
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 126, ""


_CHRONY_OFFSET = re.compile(r"^System time\s*:\s*([0-9.eE+-]+)\s+seconds\s+(fast|slow)\s+of NTP time", re.M)
_CHRONY_LEAP = re.compile(r"^Leap status\s*:\s*(.+?)\s*$", re.M)


def parse_chrony(text: str) -> tuple[float | None, str | None]:
    """(signed offset in ms, leap status) from `chronyc tracking` output; fast of NTP time is positive."""
    m, lp_ = _CHRONY_OFFSET.search(text), _CHRONY_LEAP.search(text)
    off = None
    if m:
        try:
            off = float(m.group(1)) * 1000.0 * (1 if m.group(2) == "fast" else -1)
        except ValueError:
            off = None
    return off, (lp_.group(1) if lp_ else None)


def ntp_status(runner: Runner | None = None, timeout_s: float = NTP_TIMEOUT_S) -> NtpStatus:
    """`timedatectl show -p NTPSynchronized --value` and, if available, the `chronyc tracking` offset. The runner is injectable (tests)."""
    run = runner or default_runner
    rc, out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout_s)
    word = out.strip().lower()
    synced: bool | None = True if (rc == 0 and word == "yes") else (False if (rc == 0 and word == "no") else None)
    notes = [f"timedatectl rc={rc} value={word or '-'}"]
    crc, cout = run(["chronyc", "tracking"], timeout_s)
    off = None
    if crc == 0:
        off, leap = parse_chrony(cout)
        notes.append(f"chronyc leap={leap or '-'}")
        if leap is not None and leap.lower().startswith("not synchron"):
            synced = False
    else:
        notes.append(f"chronyc rc={crc}")
    return NtpStatus(synced, off, "; ".join(notes))


def require_synced(max_offset_ms: float = NTP_MAX_OFFSET_MS, *, runner: Runner | None = None, timeout_s: float = NTP_TIMEOUT_S) -> NtpStatus:
    """The host status, or ClockNotSynced when NTP is not synchronized (or cannot be read), or when the chrony offset, if available, exceeds max_offset_ms."""
    s = ntp_status(runner, timeout_s)
    if s.synced is not True:
        raise ClockNotSynced(f"host clock not confirmed NTP-synchronized ({s.detail})")
    if s.offset_ms is not None and abs(s.offset_ms) > max_offset_ms:
        raise ClockNotSynced(f"host clock offset {s.offset_ms:.3f} ms exceeds {max_offset_ms:g} ms ({s.detail})")
    return s


# ------------------------------------------------------------------------------------------------------------------------------------ rows
def notice_rows(msg: Mapping[str, Any], recv_ms: int, commitment: str, pool_mints: dict, counters: Counter) -> list[dict]:
    """One logsNotification -> arena tape rows, by tools/arena_feed.rows_from_notice (imported, not copied; the feed label is the only difference). Raises on a
    notification that does not parse; the window records that as data loss."""
    from observe.trade_source import parse_logs_notification
    from tools.arena_feed import rows_from_notice

    note = parse_logs_notification(msg, recv_ms, commitment)
    if note is None:
        raise ValueError("unparsable logsNotification")
    return rows_from_notice(replace(note, feed="helius_window"), pool_mints, counters)


def row_key(row: Mapping[str, Any]) -> tuple | None:
    """(signature, type, event_index), the tape's unique key; None for a row with no transaction (gap, hb, start, stop, pool_info)."""
    sig = row.get("signature")
    if not isinstance(sig, str) or not sig:
        return None
    return (sig, row.get("type"), row.get("event_index"))


class Merger:
    """The public tape stream and the Helius window rows as ONE stream. offer(source, row) returns the row when it is emitted (first arrival) or None for a
    later copy. Keys: (signature, type, event_index); rows with no signature pass through. The key map is bounded: keys older than horizon_slots behind the newest
    slot seen are dropped, and at most max_keys are kept (oldest first). A row below the horizon at arrival cannot be checked: it is emitted and counted
    (below_horizon). pool_mints (pool -> (mint, quote_mint)) is learned from emitted pool_create / pool_info rows, for the window's decoder (a PumpSwap print of a
    known pool then carries its mint, as on the tape). Time is the caller's now_ms (the merger's own clock for both sources, not the rows' t_recv_ms)."""

    def __init__(self, *, horizon_slots: int = DEDUP_HORIZON_SLOTS, max_keys: int = DEDUP_MAX_KEYS, clock_ms: Callable[[], int] | None = None,
                 pool_mints: dict | None = None) -> None:
        self.horizon_slots, self.max_keys = int(horizon_slots), int(max_keys)
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.pool_mints: dict = pool_mints if pool_mints is not None else {}
        self._keys: OrderedDict = OrderedDict()  # key -> [first_source, first_seen_ms, slot, {source, ...}]
        self.max_slot = -1
        self.stats: Counter = Counter()

    def __len__(self) -> int:
        return len(self._keys)

    def first_seen(self, key: tuple) -> tuple[str, int] | None:
        e = self._keys.get(key)
        return None if e is None else (e[0], e[1])

    def _purge(self) -> None:
        lo = self.max_slot - self.horizon_slots
        while self._keys:
            k, e = next(iter(self._keys.items()))
            if len(self._keys) > self.max_keys:
                self.stats["evicted_cap"] += 1
            elif e[2] < lo:
                self.stats["evicted_horizon"] += 1
            else:
                break
            del self._keys[k]

    def offer(self, source: str, row: Mapping[str, Any], now_ms: int | None = None) -> dict | None:
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        now = self.clock_ms() if now_ms is None else int(now_ms)
        self.stats[f"offered_{source}"] += 1
        key = row_key(row)
        slot = row.get("slot") if isinstance(row.get("slot"), int) and not isinstance(row.get("slot"), bool) else None
        if key is None:
            self.stats["passthrough"] += 1
            self.stats[f"emitted_{source}"] += 1
            self._learn(row)
            return dict(row)
        e = self._keys.get(key)
        if e is not None:
            self.stats["dup"] += 1
            self.stats[f"dup_{e[0]}_then_{source}"] += 1
            e[3].add(source)
            return None
        if slot is not None:
            if slot < self.max_slot - self.horizon_slots:
                self.stats["below_horizon"] += 1
            self.max_slot = max(self.max_slot, slot)
        self._keys[key] = [source, now, slot if slot is not None else self.max_slot, {source}]
        self.stats[f"emitted_{source}"] += 1
        self._learn(row)
        self._purge()
        return dict(row)

    def _learn(self, row: Mapping[str, Any]) -> None:
        if row.get("type") in ("pool_create", "pool_info") and isinstance(row.get("pool"), str) and row.get("mint") and row.get("quote_mint"):
            self.pool_mints.setdefault(row["pool"], (row["mint"], row["quote_mint"]))

    def offer_many(self, source: str, rows: Iterable[Mapping[str, Any]], now_ms: int | None = None) -> list[dict]:
        out = []
        for r in rows:
            e = self.offer(source, r, now_ms)
            if e is not None:
                out.append(e)
        return out

    def counts(self) -> dict:
        """Per-source and duplicate counts (a plain dict, for the per-day record)."""
        return {"offered": {s: self.stats[f"offered_{s}"] for s in SOURCES}, "emitted": {s: self.stats[f"emitted_{s}"] for s in SOURCES},
                "dup": self.stats["dup"], "dup_pairs": {f"{a}_then_{b}": self.stats[f"dup_{a}_then_{b}"] for a in SOURCES for b in SOURCES},
                "passthrough": self.stats["passthrough"], "below_horizon": self.stats["below_horizon"], "keys": len(self._keys),
                "evicted_horizon": self.stats["evicted_horizon"], "evicted_cap": self.stats["evicted_cap"]}

    def seam_report(self, lo_slot: int, hi_slot: int) -> dict:
        """For keys whose slot is in [lo_slot, hi_slot]: how many both sources delivered, and how many only one did. Over the window's range (covered_from_slot
        to the newest slot the public stream has reached) public_only is what the window missed; helius_only is what the public stream has not (yet) delivered
        (fork or public loss)."""
        n = Counter()
        for e in self._keys.values():
            if e[2] is not None and lo_slot <= e[2] <= hi_slot:
                n["both" if len(e[3]) == 2 else f"{next(iter(e[3]))}_only"] += 1
        return {"lo_slot": lo_slot, "hi_slot": hi_slot, "both": n["both"], "public_only": n["public_only"], "helius_only": n["helius_only"]}


def merge_ordered(streams: Mapping[str, Iterable[Mapping[str, Any]]]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Interleave several (source, rows) streams by t_recv_ms (ties: the order of `streams`), for replays and tests. Each stream must itself be in arrival order."""
    def tag(rank: int, source: str, rows: Iterable[Mapping[str, Any]]) -> Iterator[tuple]:
        for i, r in enumerate(rows):
            t = r.get("t_recv_ms")
            if not isinstance(t, int) or isinstance(t, bool):
                raise ValueError(f"row {i} of {source} has no integer t_recv_ms")
            yield (t, rank, i, source, r)

    for _, _, _, source, r in heapq.merge(*(tag(k, s, rows) for k, (s, rows) in enumerate(streams.items())), key=lambda x: x[:3]):
        yield source, r


# ------------------------------------------------------------------------------------------------------------------------------------ the socket
def read_url(key_file: str) -> str:
    """The Helius websocket URL, with the key read from the key file inside this process (never printed, never in argv). Raises ValueError without the key."""
    key = lp.read_key(key_file)
    if key is None:
        raise ValueError("no usable HELIUS_API_KEY in the key file")
    return f"{lp.BASE}?api-key={key}"


def default_connect(url: str) -> Any:
    """websockets.connect as the probe opens it: certifi context, compression off. Imported late, so the pure parts need neither package."""
    import ssl

    import certifi
    import websockets

    ctx = ssl.create_default_context(cafile=certifi.where())
    return websockets.connect(url, ssl=ctx, compression=None, ping_interval=20, ping_timeout=20, open_timeout=15, max_size=8_000_000)


async def default_recv(ws: Any, timeout: float) -> Any:
    return await asyncio.wait_for(ws.recv(), timeout=timeout)


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


class HeliusWindow:
    """One mark's websocket. run() waits for T - pre_s, connects, subscribes, reads until T + post_s / complete() / stop_when / the first failure, and returns the
    WindowState. `url` is a string or a callable returning one (evaluated inside run, never stored in the state, never printed: an exception is recorded by class
    only). `connect(url)` returns an async context manager yielding a socket with send() and recv(); `recv(ws, timeout_s)` raises TimeoutError on timeout;
    `now_ms` and `sleep` are the clock (all four injectable for tests). `to_rows(msg, recv_ms, commitment, pool_mints, counters)` turns a logsNotification into
    tape rows (default notice_rows); `on_rows(rows, recv_ms)` receives them; `stop_when(state)` is checked after each frame (a closure over mark_complete).
    `cap` (a DailyCreditCap) is consulted before opening and counted on every frame; at the cap the socket closes."""

    def __init__(self, mark: int, url: str | Callable[[], str], *, commitment: str = "processed", cap: DailyCreditCap | None = None,
                 connect: Callable[[str], Any] | None = None, recv: Callable[[Any, float], Awaitable[Any]] | None = None,
                 now_ms: Callable[[], int] | None = None, sleep: Callable[[float], Awaitable[None]] | None = None,
                 to_rows: Callable[..., list] | None = None, on_rows: Callable[[list, int], None] | None = None,
                 stop_when: Callable[[WindowState], bool] | None = None, pool_mints: dict | None = None, silence_s: float = SILENCE_S,
                 pre_s: float = PRE_S, post_s: float = POST_S, tick_s: float = TICK_S) -> None:
        if commitment not in COMMITMENTS:
            raise ValueError(f"commitment must be one of {COMMITMENTS}")
        self.mark, self.commitment = int(mark), commitment  # an hour mark in the live host; a dry check may use a pseudo-mark (as the probe does)
        self._url, self.cap = url, cap
        self._connect, self._recv = connect or default_connect, recv or default_recv
        self._now, self._sleep = now_ms or _wall_ms, sleep or asyncio.sleep
        self.to_rows, self.on_rows, self.stop_when = to_rows or notice_rows, on_rows, stop_when
        self.pool_mints = pool_mints if pool_mints is not None else {}
        self.silence_s, self.tick_s = float(silence_s), float(tick_s)
        self.open_ms, self.close_ms = int((self.mark - pre_s) * 1000), int((self.mark + post_s) * 1000)
        self.state = WindowState(self.mark, commitment)
        self.counters: Counter = Counter()
        self._seen: set = set()
        self._close_requested = False

    def __repr__(self) -> str:
        return f"HeliusWindow(mark={self.mark}, commitment={self.commitment!r})"  # never the url

    def complete(self) -> None:
        """The caller says the mark is complete: the window closes at its next wake (within tick_s)."""
        self._close_requested = True

    # -- frames
    def _handle(self, raw: Any, recv_ms: int) -> None:
        st = self.state
        n = payload_bytes(raw)
        st.bytes += n
        if self.cap is not None and not self.cap.add(n):
            st.fail("cap_reached", recv_ms)
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            msg = None
        if not isinstance(msg, dict):
            st.bad_frames += 1
            st.fail("bad_frame", recv_ms)
            return
        method = msg.get("method")
        if method is None:  # a JSON-RPC response: a subscription ack, or an error
            if msg.get("error") is not None:
                st.fail("rpc_error", recv_ms)
            elif isinstance(msg.get("id"), int) and isinstance(msg.get("result"), int):
                st.acks.add(msg["id"])
                if st.armed_ms is None and all(i in st.acks for i in EXPECTED_ACK_IDS):
                    st.armed_ms = recv_ms
            return
        if method not in ("slotNotification", "logsNotification"):
            return
        if st.first_note_ms is None:
            st.first_note_ms = recv_ms
        res = (msg.get("params") or {}).get("result") or {}
        if method == "slotNotification":
            if isinstance(res.get("slot"), int) and not isinstance(res.get("slot"), bool):
                st.tips.append((recv_ms, int(res["slot"])))
            return
        st.notes += 1
        sig = (res.get("value") or {}).get("signature")
        if isinstance(sig, str):
            if sig in self._seen:  # a migrate tx mentions both programs and arrives on both subscriptions
                st.cross_dedup += 1
                return
            self._seen.add(sig)
        try:
            rows = self.to_rows(msg, recv_ms, self.commitment, self.pool_mints, self.counters)
        except Exception:  # noqa: BLE001 - a decode failure is data loss for this mark: counted, never raised
            st.decode_errors += 1
            st.fail("decode_error", recv_ms)
            return
        st.rows += len(rows)
        if rows and self.on_rows is not None:
            try:
                self.on_rows(rows, recv_ms)
            except Exception:  # noqa: BLE001 - the caller's callback broke: the inputs are no longer trustworthy
                st.fail("callback_error", recv_ms)

    # -- the run
    async def run(self) -> WindowState:
        st = self.state
        wait = (self.open_ms - self._now()) / 1000
        if wait > 0:
            await self._sleep(wait)
        t0 = self._now()
        if t0 >= self.close_ms:
            st.fail("too_late", t0)
            st.close_reason, st.closed_ms = "too_late", t0
            return st
        if self.cap is not None and not self.cap.can_open():
            st.fail("cap_closed", t0)
            st.close_reason, st.closed_ms = "cap_closed", t0
            return st
        st.opened_late_ms = max(0, t0 - self.open_ms)
        st.connect_ms = t0
        try:
            url = self._url() if callable(self._url) else self._url
            async with self._connect(url) as ws:
                st.connected_ms = self._now()
                for req in lp.requests(self.commitment):
                    await ws.send(json.dumps(req))
                await self._loop(ws)
        except Exception as exc:  # noqa: BLE001 - the message may carry the URL: only the class is kept
            st.fail("connect_failed" if st.connected_ms is None else f"closed_early:{type(exc).__name__}", self._now())
        st.closed_ms = self._now()
        st.counters = dict(self.counters)
        if st.close_reason is None:
            st.close_reason = "failed" if st.failure else "deadline"
        if self.cap is not None:
            self.cap.window_done(st.bytes)
        return st

    async def _loop(self, ws: Any) -> None:
        st = self.state
        while True:
            now = self._now()
            if st.failure is not None:
                st.close_reason = "failed"
                return
            if self._close_requested:
                st.close_reason = "complete"
                return
            if now >= self.close_ms:
                st.close_reason = "deadline"
                return
            if tip_silence(st, now, self.silence_s):
                st.fail("silence", now)
                continue
            try:
                raw = await self._recv(ws, min(self.tick_s, (self.close_ms - now) / 1000))
            except (TimeoutError, asyncio.TimeoutError):
                continue
            except Exception as exc:  # noqa: BLE001 - socket closed or broke before the end of the window
                st.fail(f"closed_early:{type(exc).__name__}", self._now())
                continue
            self._handle(raw, self._now())
            if st.failure is None and self.stop_when is not None:
                try:
                    if self.stop_when(st):
                        self._close_requested = True
                except Exception:  # noqa: BLE001
                    st.fail("callback_error", self._now())
