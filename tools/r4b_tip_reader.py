#!/usr/bin/env python3
"""r4b_tip_reader: the fast-0 tip follower's tape as the r4-b DRY host's HISTORY source (QP-RULING-1031-r4b-history, option C; section 2).
READ-ONLY and DRY: it opens files for reading, never writes, moves or truncates, holds no key, sends nothing. Tests use synthetic files in tmp dirs and the REAL
tools.fast_tip_follower.TipFollower._commit / _emit with a fake RPC; no real tip file is opened by tests or by agents.

What it reads (and nothing else): `trades-`, `creates-`, `migrations-`, `skipped-slots-<UTC hour of t_recv_ms>.jsonl` and `gaps.jsonl` of ONE configured tip dir, plus
`checkpoint.json` and `status.json` of the follower's state dir. Code refusals (TipRefused, tested): a file whose hour is before the floor (default 2026-10-16T01,
the D2/D3 seal; at most 53 h before the host's start); a name that is not one of those files; a path that resolves outside the tip dir (a symlink, `..`, a
subdirectory); the tip-tape archive or any directory with `tip-tape-archive` in its real path. The production floor cannot be lowered on `/var/lib/mal/sealed`.

The checkpoint protocol (ruling 2.2; why checkpoint.json and not status.json)
  `_commit` writes checkpoint.json atomically after EVERY slot, after `_emit` appended and flush()ed that slot's rows. status.json is rewritten every HEARTBEAT_S
  (15 s, up to ~75 slots stale). So: poll() reads checkpoint.json FIRST (D = last_done, last_t_recv_ms), THEN drains the files, and releases only rows of slots <= D.
  Every row of every slot <= D is then in the output; a row of a slot > D (a slot being written, or committed after the checkpoint read) is held back until a
  later checkpoint covers it. Files are bucketed by the hour of t_recv_ms, which is not block time, and the marks fall exactly on that boundary: after the
  checkpoint read the reader drains hour h, and moves on to h + 1 only if the checkpoint's own last_t_recv_ms is in a later hour than h (t_recv_ms is non-decreasing,
  so hour h can no longer grow); the live hour is drained to EOF and the next hour's file is read as soon as a checkpoint says it is in use.
  Commit order is slot order, so D is contiguous: every slot <= D is a block, a recorded skip or a recorded gap. The one out-of-order write is a contradicted skip
  (`_verify_skips` refetches an older slot before the current one): it writes a gaps.jsonl row (reason skip_contradicted), which this reader turns into a tip gap.

What comes out of poll()
  ("migration" | "create" | "trade", walker-schema row)   emitted in slot order; within a slot: migrations, creates, trades (the replay's order); then
  ("skip", slot)                                           a recorded skipped slot (a Helius-owned slot reported skipped later is a fork);
  ("gap", TipGap)                                          a gaps.jsonl row (backlog_jump, unfetchable, skip_contradicted[_refetch], unresolved_dropped, or a line
                                                           this reader cannot parse: tip_bad_line). Always data loss (ruling 2.2).
`released_D` is the slot up to which everything has been returned, or what is safe (last emitted slot - 1) while the poll stopped at `limit`; `caught_up` says the
last poll drained everything to EOF. The host certifies on `caught_up and released_D`.

Disk I/O. Reads are plain read-only reads. Catch-up files get posix_fadvise(DONTNEED) after each chunk (page cache is not memory the job should keep); the nice 19 /
ionice idle part of ruling 2.1 item 6 is applied by the host before the first poll (tools.r4b_host_glue.lower_io_priority), not here.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

DEFAULT_TIP_DIR = "/var/lib/mal/sealed/fast-trades-tip"
DEFAULT_STATE_DIR = "/var/lib/mal/fast-tip-follower"
SEALED_ROOT = "/var/lib/mal/sealed"
ARCHIVE_DIR = "/data/mal/tip-tape-archive"
ARCHIVE_MARK = "tip-tape-archive"
MIN_HOUR = "2026-10-16T01"  # no hour before this, ever (D2/D3 seal; QP-RULING-1031 2.1 item 1)
MAX_LOOKBACK_H = 53  # hours before the host's start that may be read
HOUR_S = 3600
STATUS_MAX_AGE_MS = 30_000
GAP_NAME = "gaps.jsonl"
NAME_RE = re.compile(r"^(trades|creates|migrations|skipped-slots)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")
# read order inside one slot: migrations, creates, trades (the replay feeds migrations and creates before trades), then skips
KIND_ORDER = (("migrations", "migration"), ("creates", "create"), ("trades", "trade"), ("skipped-slots", "skip"))
# the three EXP027_DECODER_REF blobs (EXP-027 section 2.2; tools/fast_tip_follower.PINNED_DECODER_BLOBS equals them, a test asserts it)
PINNED_BLOBS = {
    "observe/trade_decode.py": "238942a6b3c5425389eddfde4d11268c300acbec",
    "observe/trade_store.py": "ea4e11eddf9f034e3bc7318ce8743337d753f350",
    "tools/pump_history_backfill.py": "9a8bebb32adcf86de060b55f5a08110d11c0a550",
}
CHUNK_BYTES = 4 << 20
CHUNK_ROWS = 4096


class TipRefused(Exception):
    """The reader refuses: a path, hour or name it must never open, or a configuration that would allow one. Text only, never file content."""


def hour_key(epoch_s: int | float) -> str:
    return datetime.fromtimestamp(int(epoch_s), tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_epoch(key: str) -> int:
    return int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def is_archive_path(p: str) -> bool:
    p = os.path.normpath(p)
    return p == ARCHIVE_DIR or p.startswith(ARCHIVE_DIR + "/") or ARCHIVE_MARK in p


def under_sealed(p: str) -> bool:
    p = os.path.normpath(p)
    return p == SEALED_ROOT or p.startswith(SEALED_ROOT + "/")


def floor_hour(start_ms: int, *, min_hour: str = MIN_HOUR, lookback_h: int | None = MAX_LOOKBACK_H) -> str:
    """max(min_hour, hour of (start - lookback_h hours)): the first hour the host may read."""
    if lookback_h is None:
        return min_hour
    return max(min_hour, hour_key(start_ms // 1000 - lookback_h * HOUR_S))


# ------------------------------------------------------------------------------------------------------------------------------------ the guarded opener
class TipFiles:
    """The only object that opens tip files. name -> a path inside the one tip dir, or TipRefused."""

    def __init__(self, tip_dir: str | os.PathLike, *, floor: str = MIN_HOUR) -> None:
        raw = os.fspath(tip_dir)
        real = os.path.realpath(raw)
        if is_archive_path(raw) or is_archive_path(real):
            raise TipRefused("the tip-tape archive (or a copy of it) is never read")
        if floor < MIN_HOUR and under_sealed(real):
            raise TipRefused(f"the floor {floor} is below {MIN_HOUR}: the production floor cannot be lowered on {SEALED_ROOT}")
        self.raw, self.real, self.floor = raw, real, floor
        self.opened = 0

    def check_name(self, name: str) -> None:
        if name == GAP_NAME:
            return
        m = NAME_RE.match(name)
        if not m:
            raise TipRefused("not a tip file name")
        if m.group(2) < self.floor:
            raise TipRefused(f"hour {m.group(2)} is before the floor {self.floor}")

    def path(self, name: str) -> str:
        self.check_name(name)
        p = os.path.join(self.real, name)
        rp = os.path.realpath(p)
        if os.path.dirname(rp) != self.real or os.path.basename(rp) != name or is_archive_path(rp):
            raise TipRefused("path resolves outside the tip dir")
        return p

    def open(self, name: str):
        """Read-only binary handle; FileNotFoundError if absent (an hour with no rows of that kind has no file)."""
        fd = os.open(self.path(name), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        self.opened += 1
        return os.fdopen(fd, "rb")


def read_state_json(state_dir: str | os.PathLike, name: str) -> dict | None:
    """checkpoint.json / status.json of the follower's state dir; None if absent or unreadable."""
    if name not in ("checkpoint.json", "status.json"):
        raise TipRefused("only checkpoint.json and status.json are read from the state dir")
    try:
        with open(os.path.join(os.fspath(state_dir), name), "rb") as fh:
            obj = json.loads(fh.read())
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def status_problem(st: Mapping[str, Any] | None, now_ms: int, *, max_age_ms: int = STATUS_MAX_AGE_MS, pins: Mapping[str, str] = PINNED_BLOBS) -> str | None:
    """Ruling 2.1 item 5. None when the follower's status is healthy; else a log-safe code. Fail closed: a missing or malformed field is a problem."""
    if not isinstance(st, Mapping):
        return "status_missing"
    ts = st.get("ts_ms")
    if not _is_int(ts):
        return "status_ts_missing"
    if now_ms - ts > max_age_ms or ts - now_ms > max_age_ms:
        return "status_stale"
    if st.get("decoder_blobs") != dict(pins):
        return "decoder_blobs_mismatch"
    if st.get("decoder_blobs_pinned") is not True:
        return "decoder_blobs_not_pinned"
    if st.get("trade_event_v") is not True:
        return "event_v_off"
    return None


@dataclass(frozen=True)
class TipGap:
    """One gaps.jsonl row as a slot range [lo, hi] (inclusive) of data loss."""

    lo: int
    hi: int
    reason: str
    t_recv_ms: int | None
    slots: int | None = None

    @property
    def code(self) -> str:
        return re.sub(r"[^a-z0-9_]", "_", f"tip_gap_{self.reason}".lower())[:32]


def parse_gap_row(obj: Mapping[str, Any], pos: int) -> TipGap:
    """backlog_jump: from_slot..to_slot; unfetchable / skip_contradicted[_refetch] / unresolved_dropped: slot. A row with neither is a gap AT `pos` (fail closed)."""
    reason = obj.get("reason") if isinstance(obj.get("reason"), str) else (obj.get("kind") if isinstance(obj.get("kind"), str) else "unknown")
    fs, ts, s = obj.get("from_slot"), obj.get("to_slot"), obj.get("slot")
    t = obj.get("t_recv_ms") if _is_int(obj.get("t_recv_ms")) else None
    if _is_int(fs) and _is_int(ts) and ts >= fs:
        return TipGap(fs, ts, reason, t, obj.get("slots") if _is_int(obj.get("slots")) else ts - fs + 1)
    if _is_int(s):
        return TipGap(s, s, reason, t, 1)
    return TipGap(pos, pos, "unparsable", t, None)


# ------------------------------------------------------------------------------------------------------------------------------------ cursors
class _Cursor:
    """A lazy complete-line reader over one append-only file, by byte offset. Rows are (slot, dict) in file order. at_eof: the last fill ended on a short read."""

    def __init__(self, files: TipFiles, name: str, stats: Counter, fadvise: bool) -> None:
        self.files, self.name, self.stats, self.fadvise = files, name, stats, fadvise
        self.offset, self.tail = 0, b""
        self.rows: deque = deque()
        self.at_eof = False
        self.missing = True
        self.bad = 0  # complete lines this cursor could not use since the last take_bad()
        self.keep_le: int | None = None  # D of the current poll: rows of slots above it are dropped if the file shrinks (the follower truncated an in-flight write)

    def take_bad(self) -> int:
        n, self.bad = self.bad, 0
        return n

    def fill(self, want: int = CHUNK_ROWS) -> None:
        while len(self.rows) < want:
            try:
                fh = self.files.open(self.name)
            except FileNotFoundError:
                self.at_eof = True
                return
            with fh:
                self.missing = False
                size = os.fstat(fh.fileno()).st_size
                if size < self.offset:
                    # the follower restarted mid-write and cut the file back to its pre-write size (`_recover`): that is the end of slot D's rows. What we read
                    # beyond it is gone; the slot is fetched again and appended anew.
                    self.stats["file_shrunk"] += 1
                    self.offset, self.tail = size, b""
                    self.rows = deque(r for r in self.rows if self.keep_le is not None and r[0] <= self.keep_le)
                fh.seek(self.offset)
                data = fh.read(CHUNK_BYTES)
                if self.fadvise and data:
                    try:
                        os.posix_fadvise(fh.fileno(), self.offset, len(data), os.POSIX_FADV_DONTNEED)
                    except (OSError, AttributeError):
                        pass
            self.at_eof = len(data) < CHUNK_BYTES
            if not data:
                return
            self.offset += len(data)
            parts = (self.tail + data).split(b"\n")
            self.tail = parts.pop()  # a torn last line (the follower is mid-write) waits for its newline
            for ln in parts:
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                except (ValueError, UnicodeDecodeError):
                    row = None
                if isinstance(row, dict) and (_is_int(row.get("slot")) or self.name == GAP_NAME):
                    self.rows.append((row.get("slot") if _is_int(row.get("slot")) else -1, row))
                else:
                    self.bad += 1
                    self.stats["bad_lines"] += 1
            if self.at_eof:
                return


class TipReader:
    """See the module docstring. One thread (the host's). poll() never blocks and never writes."""

    def __init__(self, tip_dir: str | os.PathLike, state_dir: str | os.PathLike, *, start_ms: int, catch_up_hours: int = 52, min_hour: str = MIN_HOUR,
                 lookback_h: int | None = MAX_LOOKBACK_H, fadvise: bool = True) -> None:
        if catch_up_hours < 0 or (lookback_h is not None and catch_up_hours > lookback_h):
            raise TipRefused(f"catch_up_hours must be 0..{lookback_h}")
        self.floor = floor_hour(start_ms, min_hour=min_hour, lookback_h=lookback_h)
        self.files = TipFiles(tip_dir, floor=self.floor)  # refuses a floor below MIN_HOUR on the sealed dir (an unbounded look-back lowers it to min_hour)
        self.state_dir = os.fspath(state_dir)
        self.h0 = hour_epoch(hour_key(start_ms // 1000)) - catch_up_hours * HOUR_S
        if hour_key(self.h0) < self.floor:
            raise TipRefused(f"the first hour {hour_key(self.h0)} is before the floor {self.floor}")
        self.read_from_ms = self.h0 * 1000
        self.h = self.h0
        self.fadvise = fadvise
        self.stats: Counter = Counter()
        self.D: int | None = None  # last checkpoint last_done seen
        self.last_t_ms: int | None = None
        self.problem: str | None = None  # a checkpoint problem of the last poll (the host treats the tip side as unhealthy)
        self.caught_up = False
        self.released_D: int | None = None
        self.hw: int | None = None  # highest slot emitted
        self._cur: dict[str, _Cursor] | None = None
        self._cur_h: int | None = None
        self._gaps = _Cursor(self.files, GAP_NAME, self.stats, False)
        self._cat = False

    # -- the history start
    def start_problem(self) -> str | None:
        """None if the first hour's trades file exists (the 52 h of warm-up can start); else a code. Opens the file only to see that it exists."""
        try:
            self.files.open(f"trades-{hour_key(self.h0)}.jsonl").close()
        except FileNotFoundError:
            return "history_start_missing"
        except TipRefused:
            raise
        return None

    # -- the two seams the mutation tests replace
    def _read_checkpoint(self) -> tuple[int, int | None] | None:
        """(D, last_t_recv_ms) from checkpoint.json: the follower writes it after every slot, once that slot's rows are flushed."""
        ck = read_state_json(self.state_dir, "checkpoint.json")
        if ck is None or not _is_int(ck.get("last_done")):
            return None
        t = ck.get("last_t_recv_ms")
        return int(ck["last_done"]), (int(t) if _is_int(t) and t > 0 else None)

    def _last_hour(self, last_t_ms: int | None) -> int:
        """The latest hour whose files can hold a row of a slot <= D: the hour of the checkpoint's last_t_recv_ms."""
        return hour_epoch(hour_key(last_t_ms // 1000)) if last_t_ms else self.h

    # -- polling
    def _cursors(self) -> dict[str, _Cursor]:
        if self._cur is None or self._cur_h != self.h:
            hk = hour_key(self.h)
            self._cur = {k: _Cursor(self.files, f"{k}-{hk}.jsonl", self.stats, self.fadvise) for k, _ in KIND_ORDER}
            self._cur_h = self.h
        return self._cur

    def _gap_items(self, out: list) -> None:
        self._gaps.fill(CHUNK_ROWS)
        while self._gaps.rows:
            _, obj = self._gaps.rows.popleft()
            g = parse_gap_row(obj, self.hw if self.hw is not None else 0)
            if g.t_recv_ms is not None and g.t_recv_ms < self.read_from_ms:
                self.stats["gaps_before_window"] += 1  # older than the first hour this run reads: counted, not parsed further
                continue
            self.stats["gaps"] += 1
            out.append(("gap", g))
            if not self._gaps.rows:
                self._gaps.fill(CHUNK_ROWS)
        for _ in range(self._gaps.take_bad()):
            out.append(("gap", TipGap(self.hw if self.hw is not None else 0, self.hw if self.hw is not None else 0, "tip_bad_line", None, None)))

    def poll(self, limit: int = 20_000) -> list[tuple]:
        """Read checkpoint.json, then drain gaps.jsonl and the hour files, and return up to `limit` items (see the module docstring)."""
        out: list[tuple] = []
        self.caught_up = False
        ck = self._read_checkpoint()
        if ck is None:
            self.problem = "checkpoint_unreadable"
            return out
        D, last_t = ck
        if self.D is not None and D < self.D:
            self.problem = "checkpoint_regressed"
            return out
        self.problem = None
        self.D, self.last_t_ms = D, last_t
        for c in (*self._cursors().values(), self._gaps):
            c.at_eof, c.keep_le = False, D  # the live files may have grown since the last poll
        self._gap_items(out)  # after the checkpoint read: a gap row of a slot <= D is written before the checkpoint passes it
        last_h = self._last_hour(last_t)
        budget = max(0, limit - len(out))
        stopped = False
        while True:
            cur = self._cursors()
            stopped = self._merge(cur, D, budget, out)
            for c in cur.values():
                for _ in range(c.take_bad()):
                    out.append(("gap", TipGap(self.hw if self.hw is not None else D, self.hw if self.hw is not None else D, "tip_bad_line", None, None)))
            if stopped:
                break
            drained = all(not c.rows and c.at_eof for c in cur.values())
            if drained and self.h < last_h:
                if cur["trades"].missing:
                    self.stats["hour_trades_missing"] += 1
                    pos = self.hw if self.hw is not None else D
                    out.append(("gap", TipGap(pos, pos, "hour_file_missing", None, None)))
                self.h += HOUR_S
                self.stats["hours_done"] += 1
                continue
            break
        if stopped:
            self.released_D = (self.hw - 1) if self.hw is not None else self.released_D
            return out
        cur = self._cursors()
        self.caught_up = self.h >= last_h and all(c.at_eof or (c.rows and c.rows[0][0] > D) for c in cur.values())
        if self.caught_up:
            self.released_D = D
        return out

    def _merge(self, cur: dict[str, _Cursor], D: int, budget: int, out: list) -> bool:
        """Emit rows of slots <= D from the cursors of the current hour in (slot, kind order). True if the budget stopped it."""
        n = 0
        while True:
            best = None
            for rank, (k, _) in enumerate(KIND_ORDER):
                c = cur[k]
                if not c.rows and not c.at_eof:
                    c.fill(CHUNK_ROWS)
                if c.rows and c.rows[0][0] <= D and (best is None or c.rows[0][0] < best[0]):
                    best = (c.rows[0][0], rank)
            if best is None:
                return False
            if n >= budget:
                return True
            k, item = KIND_ORDER[best[1]]
            slot, row = cur[k].rows.popleft()
            n += 1
            self.hw = slot if self.hw is None else max(self.hw, slot)
            if item == "skip":
                self.stats["skips"] += 1
                out.append(("skip", slot))
            else:
                self.stats[f"rows_{item}"] += 1
                out.append((item, row))
