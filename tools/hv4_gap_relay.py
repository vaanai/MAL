#!/usr/bin/env python3
"""hv4_gap_relay: the PRODUCER of the book's own tape gap records for the HV4 live C0 bridge (DEC-032 s5.2). PAPER ONLY: no RPC, no key, no wallet.

The frozen HV4 runner (9ed4c5ff) writes no gap row and must not change, so this separate read-only relay (research-0) turns the arena TAPE's gap and restart records into
a per-book stream `<out-dir>/transport/<bot_id>.tapegaps.jsonl` of {"kind", "ms"} rows that the forwarder can carry and the bridge reads (feed_break latches on them).

SEAL (QP rulings, #969 6424766650 and 6423545454): KIND AND INSTANT ONLY, plus the slot of a trade row (QP #969 P7-a: a chain clock, not an outcome). No tape line is ever
parsed as a whole. Each line is matched with three byte regexes (TYPE_RE, MS_RE, SLOT_RE) that extract only `type`, `t_recv_ms` and `slot`; only a line whose type is
`gap` or `start` is JSON-parsed, and from it only `kind`, `data_loss` and `t_recv_ms` are read. No outcome, price, mint, pool, venue, signature or reserve field is read, and
nothing is joined to any outcome. The output rows are {"kind", "ms"} breach instants; a raw trade instant or slot is never written to the stream. The state file (below)
holds the REF_DEPTH highest recent trade instants and slots (what P7-b asks to carry across passes); it lives under `<out-dir>/state/`, never under `transport/`, and
nothing in this tool prints it. The relay's output is never joined to HV4 or Q50 outputs and is never used to price, screen or count anything; it is walk-2-hour arena-tape
information (DEC-030 s2 R1).

RULES (tools/arena_paper.py ArenaEngine.on_row / on_gap defines them):
  * a gap row of kind process_restart / feed_restart / feed_start, or with data_loss true -> that kind (data_loss for the rest); `takeover` -> takeover; a `start` row -> start.
  * gap_silence: a trade row whose t_recv_ms is more than GAP_SILENCE_MS (60,000) after the reference instant.
  * gap_slots (P7-a): else (the engine's elif order) a trade row whose slot is more than GAP_SLOTS (150) above the reference slot.

SUPERSET OF THE ENGINE (P7-d). The engine moves its high-water instant and slot only on ACCEPTED rows (known venue, full row, not a duplicate); the relay may not read
those fields. So the reference is not the high-water mark of every row but the REF_DEPTH-th highest instant (slot) seen, which is never above the engine's high-water mark
unless the REF_DEPTH highest prior rows were ALL rejected by the engine. Hence every gap the engine records (at the accepted row's t_recv_ms) is in the relay output, whenever
at most REF_DEPTH-1 rejected rows sit among the latest rows. The price is up to REF_DEPTH-1 extra rows (the rows right after a real gap) and a gap_silence on a feed that
delivers fewer than REF_DEPTH rows in 60 s; both are stricter, never looser. Residual: REF_DEPTH or more consecutive rejected rows can hide a gap. Not covered: a gap or start
line that is malformed JSON (dropped), and a gap the feed never wrote.

HOUR ROLL AND COMPACTION (P7-b). Tape files are `tape-<UTC hour>.jsonl`; 120 s after the hour the feed compacts it to `.jsonl.zst` and deletes the source (COMPACT_GRACE_MS).
One pass reads each unsealed hour from its byte offset (the decompressed `.zst` is byte-identical to the `.jsonl`, so the offset carries over); the offsets, the reference
instants/slots, the unsealed stems and the rows live in `<out-dir>/state/<bot_id>.relay-state.json` (atomic). So a silence that spans the hour boundary, a gap row written in
the last minutes of an hour, and an hour compacted between two passes are all seen. A cold start (no usable state) reads the newest `--cold-hours` (default 2) tape hours,
a closed one through tools.arena_zst. An hour is sealed (no longer read) once its source is a `.zst` or a trade instant at least COMPACT_GRACE_MS + 5 s past its end was seen.
LOOP: `--loop --interval-s N` (default 30; N >= 120 is refused) runs one pass per interval in one process; the stream is rewritten (atomically) on EVERY pass even with no new
row, so its mtime is a heartbeat. A second instance on the same out-dir/bot exits 3 (flock).

Output rows (sorted by ms, deduplicated, rows older than RETAIN_MS before the newest instant dropped, at most MAX_ROWS kept): {"kind": <one of RELAY_KINDS>, "ms": int}.
Until this relay is installed and its output is carried to the bridge host, DEC-032 precondition 7 is NOT met for tape gaps.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import json
import math
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools import arena_zst
from tools import hv4_live_c0 as c0

GAP_SILENCE_MS = 60_000  # tools/arena_paper.py StackConfig.gap_silence_ms (a test compares them)
GAP_SLOTS = 150  # tools/arena_paper.py StackConfig.gap_slots (a test compares them)
HARD_GAP_KINDS = ("process_restart", "feed_restart", "feed_start")  # tools/arena_paper.py HARD_GAP_KINDS (a test compares them)
RELAY_KINDS = ("data_loss", "process_restart", "feed_restart", "feed_start", "start", "takeover", "gap_silence", "gap_slots")
REF_DEPTH = 4  # see "SUPERSET OF THE ENGINE": tolerates REF_DEPTH-1 engine-rejected rows among the latest rows
COMPACT_GRACE_MS = 120_000  # tools/arena_feed.py COMPACT_GRACE_MS (a test compares them)
SEAL_AFTER_MS = COMPACT_GRACE_MS + 5_000
RETAIN_MS = 72 * 3600 * 1000
MAX_ROWS = 20_000  # ~1 MB of stream; the bridge refuses a stream over c0.GAP_LOG_MAX_BYTES (8 MB)
COLD_HOURS_DEFAULT = 2
INTERVAL_DEFAULT_S = 30.0
INTERVAL_REFUSE_S = 120.0  # QP P7-b: the cadence must stay well under the 120 s compaction grace
MAX_CONSECUTIVE_ERRORS = 10
CHUNK = 1 << 20
MAX_LINE_BYTES = 4 << 20
STATE_VERSION = 1
ARENA_ROOT = "/data/mal/arena"  # the relay never writes under it (a refusal string, never opened)
TYPE_RE = re.compile(rb'"type"\s*:\s*"([a-z_]+)"')
MS_RE = re.compile(rb'"t_recv_ms"\s*:\s*(\d+)')
SLOT_RE = re.compile(rb'"slot"\s*:\s*(\d+)')
STEM_RE = re.compile(r"^(tape-(\d{4})-(\d{2})-(\d{2})T(\d{2}))\.jsonl(\.zst)?$")
OUT_NAME = "{bot_id}.tapegaps.jsonl"
STATE_NAME = "{bot_id}.relay-state.json"
LOCK_NAME = "{bot_id}.relay.lock"
LOCK_HELD_EXIT = 3
FATAL_EXIT = 4


class RelayError(RuntimeError):
    pass


def _keep_top(top: list[int], v: int, depth: int) -> None:
    """`top` is ascending and holds the `depth` highest values seen."""
    if len(top) < depth:
        bisect.insort(top, v)
    elif v > top[0]:
        top.pop(0)
        bisect.insort(top, v)


class Scanner:
    """Rows found so far plus the reference instants/slots. Feed it tape lines in file order (`feed_line`, `feed_stream`)."""

    def __init__(self, depth: int = REF_DEPTH) -> None:
        self.depth = depth
        self.rows: set[tuple[str, int]] = set()
        self.top_t: list[int] = []  # ascending: the `depth` highest trade-row instants
        self.top_slot: list[int] = []  # ascending: the `depth` highest trade-row slots

    def feed_line(self, line: bytes) -> None:
        tm = TYPE_RE.search(line)
        typ = tm.group(1).decode() if tm else "trade"
        if typ in ("gap", "start"):
            try:
                row = json.loads(line)
            except ValueError:
                return
            if not isinstance(row, dict):
                return
            at = row.get("t_recv_ms")
            if isinstance(at, bool) or not isinstance(at, int):
                return
            if typ == "start":
                kind = "start"
            else:
                k = str(row.get("kind"))
                kind = k if k in HARD_GAP_KINDS or k == "takeover" else ("data_loss" if row.get("data_loss") else None)
            if kind:
                self.rows.add((kind, at))
        elif typ == "trade":
            mm = MS_RE.search(line)
            if mm is None:
                return
            ms = int(mm.group(1))
            sm = SLOT_RE.search(line)
            slot = int(sm.group(1)) if sm else None
            if self.top_t and ms - self.top_t[0] > GAP_SILENCE_MS:
                self.rows.add(("gap_silence", ms))
            elif slot is not None and self.top_slot and slot - self.top_slot[0] > GAP_SLOTS:  # the engine's elif order: after the silence rule
                self.rows.add(("gap_slots", ms))
            _keep_top(self.top_t, ms, self.depth)
            if slot is not None:
                _keep_top(self.top_slot, slot, self.depth)

    def feed_stream(self, read: Callable[[int], bytes]) -> int:
        """Feed every COMPLETE line of a byte stream. Returns the bytes consumed; a partial last line (a write in progress) is left for a later pass."""
        buf = b""
        consumed = 0
        feed = self.feed_line
        while True:
            chunk = read(CHUNK)
            if not chunk:
                break
            buf = buf + chunk if buf else chunk
            end = buf.rfind(b"\n")
            if end < 0:
                if len(buf) > MAX_LINE_BYTES:
                    raise RelayError("line_too_long")
                continue
            block, buf = buf[: end + 1], buf[end + 1:]
            consumed += len(block)
            for line in block.split(b"\n")[:-1]:
                feed(line)
        return consumed

    def sorted_rows(self) -> list[dict[str, Any]]:
        return [{"kind": k, "ms": m} for k, m in sorted(self.rows, key=lambda r: (r[1], r[0]))]


def _open_read(path: Path) -> tuple[Callable[[int], bytes], Callable[[], None]]:
    if path.name.endswith(".zst"):
        r = arena_zst.open_zst(path)
        return r.read, r.close
    fh = open(path, "rb")
    return fh.read, fh.close


def scan(paths: list[Path], depth: int = REF_DEPTH) -> list[dict[str, Any]]:
    """Stateless full scan of the given files in order (a `.zst` is stream-decompressed). Kind and instant of every gap / restart record and breach, sorted."""
    sc = Scanner(depth)
    for p in paths:
        read, close = _open_read(Path(p))
        try:
            sc.feed_stream(read)
        finally:
            close()
    return sc.sorted_rows()


def _fsync_dir(d: Path) -> None:
    fd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """tmp in the same directory, fsync, os.replace, fsync the directory; the tmp is removed in `finally`."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def write_stream(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write(path, b"".join((json.dumps(r, sort_keys=True) + "\n").encode() for r in rows))


def stem_end_ms(stem: str) -> int | None:
    m = STEM_RE.match(stem + ".jsonl")
    if not m:
        return None
    try:
        t = datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(t.timestamp() * 1000) + 3_600_000


def discover(tape_dir: Path) -> dict[str, dict[str, Path]]:
    """{stem: {"jsonl": path, "zst": path}} of the regular (non-link) tape files; names that are not tape-<hour>.jsonl[.zst] are ignored."""
    out: dict[str, dict[str, Path]] = {}
    with os.scandir(tape_dir) as it:
        for e in it:
            m = STEM_RE.match(e.name)
            if not m or e.is_symlink() or not e.is_file(follow_symlinks=False) or stem_end_ms(m.group(1)) is None:
                continue
            out.setdefault(m.group(1), {})["zst" if m.group(6) else "jsonl"] = Path(e.path)
    return out


class Relay:
    """The incremental pass machinery and its state file."""

    def __init__(self, tape_dir: str | os.PathLike, bot_id: str, state_path: str | os.PathLike, cold_hours: int = COLD_HOURS_DEFAULT, depth: int = REF_DEPTH) -> None:
        self.tape_dir = Path(tape_dir)
        self.bot_id = bot_id
        self.state_path = Path(state_path)
        self.cold_hours = cold_hours
        self.depth = depth
        self.sc = Scanner(depth)
        self.sealed_through: str | None = None
        self.offsets: dict[str, int] = {}
        self.reset_reason: str | None = None
        self._load()

    # --- state
    def _load(self) -> None:
        try:
            raw = self.state_path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.reset_reason = "state_unreadable"
            return
        try:
            self._apply(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            self.sc, self.sealed_through, self.offsets = Scanner(self.depth), None, {}
            self.reset_reason = "state_invalid"

    def _apply(self, st: Any) -> None:
        def ints(v: Any, limit: int) -> list[int]:
            if not isinstance(v, list) or len(v) > limit or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in v):
                raise ValueError("bad int list")
            return sorted(v)

        if not isinstance(st, dict) or st.get("v") != STATE_VERSION or st.get("bot_id") != self.bot_id:
            raise ValueError("bad state header")
        if st.get("tape_dir") != str(self.tape_dir):
            self.reset_reason = "tape_dir_changed"
            return
        sealed = st.get("sealed_through")
        if sealed is not None and (not isinstance(sealed, str) or stem_end_ms(sealed) is None):
            raise ValueError("bad sealed_through")
        offsets = st.get("offsets")
        if not isinstance(offsets, dict) or any(stem_end_ms(k) is None or isinstance(v, bool) or not isinstance(v, int) or v < 0 for k, v in offsets.items()):
            raise ValueError("bad offsets")
        sc = Scanner(self.depth)
        sc.top_t, sc.top_slot = ints(st.get("top_t"), self.depth), ints(st.get("top_slot"), self.depth)
        rows = st.get("rows")
        if not isinstance(rows, list):
            raise ValueError("bad rows")
        for r in rows:
            if not isinstance(r, dict) or r.get("kind") not in RELAY_KINDS or isinstance(r.get("ms"), bool) or not isinstance(r.get("ms"), int):
                raise ValueError("bad row")
            sc.rows.add((r["kind"], r["ms"]))
        self.sc, self.sealed_through, self.offsets = sc, sealed, dict(offsets)

    def save(self) -> None:
        st = {"v": STATE_VERSION, "bot_id": self.bot_id, "tape_dir": str(self.tape_dir), "sealed_through": self.sealed_through, "offsets": self.offsets,
              "top_t": self.sc.top_t, "top_slot": self.sc.top_slot, "rows": self.sc.sorted_rows()}
        atomic_write(self.state_path, (json.dumps(st, sort_keys=True) + "\n").encode())

    def _snapshot(self) -> tuple:
        return (set(self.sc.rows), list(self.sc.top_t), list(self.sc.top_slot), dict(self.offsets), self.sealed_through)

    def _restore(self, snap: tuple) -> None:
        self.sc.rows, self.sc.top_t, self.sc.top_slot, self.offsets, self.sealed_through = snap

    # --- one pass
    def _prune(self) -> int:
        """Drop rows more than RETAIN_MS before the newest instant, and keep at most MAX_ROWS (the newest). Returns the number dropped."""
        if not self.sc.rows:
            return 0
        newest = max(max(m for _k, m in self.sc.rows), self.sc.top_t[-1] if self.sc.top_t else 0)
        keep = sorted((r for r in self.sc.rows if r[1] >= newest - RETAIN_MS), key=lambda r: (r[1], r[0]))[-MAX_ROWS:]
        dropped = len(self.sc.rows) - len(keep)
        self.sc.rows = set(keep)
        return dropped

    def _read_stem(self, paths: dict[str, Path], off: int, notes: list[str]) -> tuple[int, int, str]:
        """(new offset, bytes consumed, source kind) of one hour. The `.jsonl` is preferred (as every reader does while both exist)."""
        jp = paths.get("jsonl")
        if jp is not None:
            try:
                size = jp.stat().st_size
                if size < off:
                    notes.append("file_shrunk")
                    off = 0
                if size == off:
                    return off, 0, "jsonl"
                with open(jp, "rb") as fh:
                    fh.seek(off)
                    n = self.sc.feed_stream(fh.read)
                return off + n, n, "jsonl"
            except FileNotFoundError:  # compacted between the listing and the open: the verified .zst is there
                zp = jp.with_name(jp.name + ".zst")
        else:
            zp = paths["zst"]
        for attempt in (0, 1):
            read, close = _open_read(zp)
            try:
                skipped = 0
                while skipped < off:
                    b = read(min(CHUNK, off - skipped))
                    if not b:
                        break
                    skipped += len(b)
                if skipped < off:  # the stream is shorter than the offset: read the hour again from its start (rows and references are idempotent)
                    notes.append("offset_beyond_end")
                    off = 0
                    continue
                n = self.sc.feed_stream(read)
            finally:
                close()
            return off + n, n, "zst"
        raise RelayError("zst_unreadable")

    def run_pass(self, publish: Callable[[list[dict[str, Any]]], None]) -> dict[str, Any]:
        """One pass: read what is new, publish the stream (atomic, every pass), save the state. On any error the in-memory state is rolled back and the error propagates
        (the stream is then NOT rewritten, so its mtime stops being a heartbeat)."""
        t0 = time.monotonic()
        snap = self._snapshot()
        notes: list[str] = [self.reset_reason] if self.reset_reason else []
        self.reset_reason = None
        try:
            found = discover(self.tape_dir)
            stems = sorted(found)
            cold = self.sealed_through is None and not self.offsets
            if cold:
                todo = stems[-self.cold_hours:]
            elif self.sealed_through is not None:
                todo = [s for s in stems if s > self.sealed_through]
            else:  # nothing sealed yet: the oldest hour still being read is the floor
                todo = [s for s in stems if s >= min(self.offsets)]
            before = len(self.sc.rows)
            total = 0
            kinds: dict[str, str] = {}
            for stem in todo:
                off, n, kind = self._read_stem(found[stem], self.offsets.get(stem, 0), notes)
                self.offsets[stem] = off
                kinds[stem] = kind
                total += n
                if len(todo) > 1 and n:
                    publish(self.sc.sorted_rows())  # a catch-up over several hours: show progress
            newest_t = self.sc.top_t[-1] if self.sc.top_t else None
            for stem in todo:  # seal a prefix only
                end = stem_end_ms(stem)
                if kinds[stem] == "zst" or (newest_t is not None and end is not None and newest_t >= end + SEAL_AFTER_MS):
                    self.sealed_through = stem if self.sealed_through is None else max(self.sealed_through, stem)
                    self.offsets.pop(stem, None)
                else:
                    break
            for stem in [s for s in self.offsets if s not in found]:  # a stem that left the directory (archived) is no longer ours to read
                del self.offsets[stem]
            dropped = self._prune()
            if dropped:
                notes.append(f"rows_dropped:{dropped}")
            rows = self.sc.sorted_rows()
            publish(rows)
            self.save()
        except BaseException:
            self._restore(snap)
            self.reset_reason = None
            raise
        return {"relay": "hv4_gap_relay", "bot_id": self.bot_id, "gap_rows": len(rows), "new_rows": max(0, len(self.sc.rows) - before), "bytes": total, "hours": len(todo),
                "sealed_through": self.sealed_through, "pass_s": round(time.monotonic() - t0, 3), **({"notes": notes} if notes else {})}


# --- running
def interval_problem(v: float | None) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
        return "interval_s_not_a_number"
    if not 0 < v < INTERVAL_REFUSE_S:
        return f"interval_s_out_of_range (must be > 0 and < {INTERVAL_REFUSE_S:g}; the cadence must stay well under the {COMPACT_GRACE_MS // 1000} s compaction grace)"
    return None


def out_dir_problem(out_dir: str, tape_dir: str) -> str | None:
    """The relay never writes under the arena tree and never inside (or around) the tape dir. The string check comes first so a refused path is never statted."""
    norm = re.sub(r"/+", "/", os.path.normpath(os.path.abspath(out_dir)))  # normpath keeps a leading "//": collapse it so "//data/mal/arena/x" is caught too
    if norm == ARENA_ROOT or norm.startswith(ARENA_ROOT + "/"):
        return "out_dir_under_arena_root"
    o, t = os.path.realpath(out_dir), os.path.realpath(tape_dir)
    if o == ARENA_ROOT or o.startswith(ARENA_ROOT + "/"):
        return "out_dir_under_arena_root"
    if o == t or o.startswith(t + os.sep) or t.startswith(o + os.sep):
        return "out_dir_overlaps_tape_dir"
    return None


def serve(step: Callable[[], dict[str, Any]], interval_s: float, max_passes: int = 0, sleep: Callable[[float], None] | None = None,
          monotonic: Callable[[], float] = time.monotonic, stop: Callable[[], bool] = lambda: False, log: Callable[[str], None] = lambda s: print(s, flush=True),
          err: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True)) -> int:
    """Run `step` every `interval_s` (measured start to start; a slow pass starts the next at once). Exit 4 after MAX_CONSECUTIVE_ERRORS failed passes in a row."""
    sleep = sleep or time.sleep
    passes = 0
    errors = 0
    while not stop():
        t0 = monotonic()
        try:
            info = step()
            errors = 0
            info["pass"] = passes + 1
            if info["pass_s"] >= INTERVAL_REFUSE_S:
                info["slow_pass"] = True
            log(json.dumps(info, sort_keys=True))
        except Exception as exc:  # noqa: BLE001 - a failed pass must not end the loop; the stream simply stops being rewritten
            errors += 1
            err(json.dumps({"relay": "hv4_gap_relay", "pass": passes + 1, "error": type(exc).__name__, "detail": str(exc)[:200], "consecutive_errors": errors}))
            if errors >= MAX_CONSECUTIVE_ERRORS:
                return FATAL_EXIT
        passes += 1
        if max_passes and passes >= max_passes:
            return 0
        wait = interval_s - (monotonic() - t0)
        while wait > 0 and not stop():  # short slices so a stop request is prompt
            s = min(1.0, wait)
            sleep(s)
            wait -= s
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hv4_gap_relay", allow_abbrev=False)
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--bot-id", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--loop", action="store_true", help="run a pass every --interval-s seconds in this process")
    ap.add_argument("--interval-s", type=float, default=None, help=f"loop cadence in seconds (default {INTERVAL_DEFAULT_S:g}; {INTERVAL_REFUSE_S:g} or more is refused)")
    ap.add_argument("--cold-hours", type=int, default=COLD_HOURS_DEFAULT, help="cold start: read the newest N tape hours (default 2)")
    ap.add_argument("--max-passes", type=int, default=0, help="loop: stop after N passes (0 = until stopped); a test aid")
    a = ap.parse_args(argv)
    why = c0.bot_id_problem(a.bot_id) or c0.arm_refusal(a.bot_id) or interval_problem(a.interval_s) or out_dir_problem(a.out_dir, a.tape_dir)
    if not why and not 1 <= a.cold_hours <= 48:
        why = "cold_hours_out_of_range"
    if not why and a.max_passes < 0:
        why = "max_passes_negative"
    if not why and not os.path.isdir(a.tape_dir):
        why = "tape_dir_missing"
    if why:
        print(f"hv4_gap_relay: REFUSED {why}", file=sys.stderr)
        return 2
    interval = INTERVAL_DEFAULT_S if a.interval_s is None else a.interval_s
    out = Path(a.out_dir)
    tdir, sdir = out / "transport", out / "state"
    tdir.mkdir(parents=True, exist_ok=True)
    sdir.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(sdir / LOCK_NAME.format(bot_id=a.bot_id), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("hv4_gap_relay: another instance holds the lock for this bot and out-dir", file=sys.stderr)
        os.close(lock_fd)
        return LOCK_HELD_EXIT
    try:
        relay = Relay(a.tape_dir, a.bot_id, sdir / STATE_NAME.format(bot_id=a.bot_id), cold_hours=a.cold_hours)
        stream = tdir / OUT_NAME.format(bot_id=a.bot_id)

        def step() -> dict[str, Any]:
            return relay.run_pass(lambda rows: write_stream(stream, rows))

        if not a.loop:
            try:
                print(json.dumps(step(), sort_keys=True))
            except Exception as exc:  # noqa: BLE001 - class and a short detail only; never a tape line
                print(json.dumps({"relay": "hv4_gap_relay", "error": type(exc).__name__, "detail": str(exc)[:200]}), file=sys.stderr)
                return FATAL_EXIT
            return 0
        stop = {"now": False}

        def _on_signal(_signum, _frame) -> None:
            stop["now"] = True

        old = {s: signal.signal(s, _on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            return serve(step, interval, a.max_passes, stop=lambda: stop["now"])
        finally:
            for s, h in old.items():
                signal.signal(s, h)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
