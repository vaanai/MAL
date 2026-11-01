#!/usr/bin/env python3
"""arena_tip_tape_writer: the fast-0 tip follower's tape as CLOSED ARENA HOUR FILES (QP-RULING-1101-hv4-reads 4.1 (B), "Transport B" of ops/reports/2026-11-01/hv7-tip-plan.md).
PAPER only: no key, no wallet, no RPC, 0 Helius credits, no network. Runs on mal-fast-0 as ONE long MiScusi job at nice 19 / ionice idle (scripts/ops/arena-tip-writer-job.sh).

WHAT IT DOES. A loop around tools.arena_tip_adapter.ArenaTipSource (the vendored guarded reader, checkpoint-then-drain, plus the adapter of #1165): every poll gives arena-tape rows in
slot order; this module serialises them (the arena feed's strict JSON, one line per row) and appends them to <out-dir>/tape-<UTC hour of t_recv_ms>.jsonl, the file layout that
tools/arena_state.py and tools/tape_mirror.py already know. An hour is CLOSED only when (1) the follower's checkpoint (reader.last_t_ms = checkpoint.json last_t_recv_ms) is past the
end of that hour PLUS --grace-ms, and (2) the reader has returned every row of that hour (it is caught up, or it is already draining a later hour). A closed hour is compacted by
tools.arena_zst.compact_file to tape-<hour>.jsonl.zst (verified, then the .jsonl is deleted; the arena feed's convention) and never written again: a row whose hour is closed goes to
the current hour's file (the feed's TapeWriter does the same) and is counted (`late_to_current`). The output is a pure function of the tip files: tools/test_arena_tip_tape_writer.py
proves its bytes equal an offline adaptation of the same rows and that an engine fed the written files makes the same decisions as one fed the adapter directly (the #2023 harness).

SEALS. It is a tip-tape READER (the pinned writer of the HV7 plan, step 5): only through the vendored reader (hours >= max(2026-10-16T01, start - W), the archive and any copy refused,
read-only); the decoder pin is checked from status.json before EVERY poll and fails closed (an unpinned decoder, event V off, or a stale status gives no rows). Its OUTPUT dir is a
DERIVED COPY of the sealed tip tape: sealed the same way (names and sizes of <out-dir>/.writer/{status,counts} may be read; nobody opens the tape-*.jsonl[.zst] files except the transport
(tools/arena_tip_push.py) and the book host). Walk-2-hour information (DEC-030 s2 R1). It is NOT allowed under any directory whose name carries a sealed token (FORBIDDEN_PARTS).

FILES (all in <out-dir>; the dot-dir is ignored by every tape glob)
  tape-<hour>.jsonl / .jsonl.zst     the tape. An open hour is append-only complete lines (flushed per poll, fsynced with the state); a closed hour is the verified .zst.
  .writer/status.json                heartbeat (atomic, every second): lag, hours closed, gap counts, the decoder-pin check, the no-tx_index counts, state, problem.
  .writer/counts.jsonl               one row per CLOSED hour: bytes, lines, sha256 of the hour, rows by prog|type, gap rows by kind|prog (+ slot spans), the longest data-row silence per
                                     prog, rows without tx_index, late rows. Names and counts only (the outcome-blind input of the hour-health check, plan step 6).
  .writer/state.json                 the resume state (below). .writer/lock  flock: a second writer on the dir exits 3.

FAIL CLOSED. Consumers call writer_problem(out_dir): None only for a fresh, live, healthy heartbeat. The writer itself: an unhealthy tip (stale status, unpinned decoder) writes nothing and
is reported; past --max-unhealthy-s it exits 5. A LATCHED reader problem (checkpoint_regressed / checkpoint_reset: the follower restarted without its checkpoint) exits 6 at once and never
retries. A disk below --min-free-pct pauses all reads and writes (nothing is lost: the reader does not advance). A resume that does not reproduce the bytes on disk exits 7.

RESUME. After every poll batch (at most every --snapshot-s, and at every close and stop) the writer fsyncs its open files and then saves state.json: the reader's position (hour,
per-file byte offsets, D, counters), the adapter's state (pending gaps, last row, the open (pool, slot) groups), the size and tally of every open hour, the closed hours and their
summaries, plus the pins (sha256 of this file, the adapter and the vendored reader; the three decoder blobs) and the config (start_ms, catch_up_hours, grace). A restart restores
both objects from it and continues WITHOUT re-reading old hours (the follower keeps 3 days, so a replay from the start would stop working after ~20 h). Bytes that reached the disk
after the last snapshot (a kill in the window) are VERIFIED, not trusted: the regenerated rows must reproduce them exactly (they are skipped, never rewritten, so the file never shrinks
under the transport); a torn last line is trimmed (the transport only ever ships complete lines). Any difference, a changed pin or config, or a missing/short file refuses the resume.
Deterministic by construction, tested by killing the writer after every poll of a synthetic multi-hour tip tape and comparing the final bytes with an uninterrupted run.

CATCH-UP. --catch-up-hours N reads the tip from hour(start) - N (N <= LOOKBACK_MAX_H = 72, the follower's retention; QP-RULING item 1 fixes the window W the writer may use: change the
constant only with a new sha). Hours close as the reader finishes them, so a 52 h catch-up leaves closed .zst hours behind it. If the first history hour is gone the start is refused
(history_start_missing). Bots get NO call on catch-up rows: this module only writes the tape; `caught_up` and `released_t_ms` in the status say when it is live.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/arena_tip_tape_writer.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import fcntl
import hashlib
import json
import math
import os
import queue
import shutil
import signal
import stat
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from tools import arena_tip_adapter as ta
from tools import arena_tip_reader as tr
from tools.arena_zst import compact_file, zstd_path

SCHEMA_STATUS = "arena_tip_tape_writer_status_v1"
SCHEMA_STATE = "arena_tip_tape_writer_state_v1"
SCHEMA_COUNTS = "arena_tip_tape_writer_counts_v1"
META_DIR = ".writer"
H_MS = 3_600_000
HOME = os.path.expanduser("~")
DEFAULT_OUT = os.path.join(HOME, "mal-data", "arena-tipfeed", "tape")
OUT_ROOTS = (os.path.join(HOME, "mal-data"), "/data2/mal/scratch")  # the derived dir lives under the host's data dir (or the scratch root, tests and synthetic runs)
# a name that carries one of these is refused (the closed names of the arena tools, tools/arena_feed_delay.FORBIDDEN_PARTS and the HV paths); judged on the part BELOW the root
FORBIDDEN_PARTS = ("sealed", "forward", "shadow", "canary", "probe", "walk", "exp011", "exp012", "tip-tape", "meta-capture", "fresh-08", "truth-", "_shakedown", "_smoke", "kalshi",
                   "fwdv1", "hv4", "haiku-v4", "q50", "bots.json")
GRACE_MS_DEFAULT = 120_000  # an hour closes this long after the follower's checkpoint passed its end (the arena feed's COMPACT_GRACE_MS)
LOOKBACK_MAX_H = 70  # W = 70 h (QP-RULING-1101-arena-tip-reader W2): the follower's retain() removes hours below now - 72 h, so 70 h leaves a 2 h margin at the start
CATCH_UP_H_DEFAULT = 70
CATCH_UP_DEADLINE_MIN = 120  # W2: caught_up within 120 min of the start, else the writer stops (exit 8) and its output is for the manager to discard; a later start defines a new H0
POLL_LIMIT = 20_000
POLL_S = 0.25
STATUS_EVERY_S = 1.0
SNAPSHOT_EVERY_S = 1.0
PRUNE_EVERY_S = 300.0
MIN_FREE_PCT_DEFAULT = 20.0  # fast-0 has one filesystem shared with the follower and the listeners (the arena mirror stops at the same number)
MAX_UNHEALTHY_S_DEFAULT = 900.0
STATUS_MAX_AGE_MS = 5_000  # the heartbeat is written every second
CLOSED_KEEP = 400  # closed-hour summaries kept in the state
TORN_SCAN_BYTES = 1 << 20  # a tape line is a few hundred bytes; the last newline is looked for in this much of the tail
COMPACT_TRIES = 3
EXIT_OK, EXIT_REFUSED, EXIT_LOCKED, EXIT_IO, EXIT_UNHEALTHY, EXIT_LATCHED, EXIT_DIVERGED, EXIT_CATCHUP = 0, 2, 3, 4, 5, 6, 7, 8
DATA_TYPES = ("trade", "create", "complete", "pool_create")
KIND_STATUS_UNHEALTHY, KIND_READER_PROBLEM, KIND_WRITER_HOLE = "tip_gap_status_unhealthy", "tip_gap_reader_problem", "tip_gap_writer_hole"  # W9: the registration lists these
VERIFIED_KEEP_H_DEFAULT = 24  # B5: a closed hour is deleted this long after the receiver verified it
HB_GAP_MS = 30_000
SPANS_MAX = 5000
REFUSED_OUT_PREFIXES = ("/var/lib/mal/sealed", "/data/mal")  # W2: not under the sealed tree, not on the research-0 walker filesystem
DECODER_PIN_PROBLEMS = ("decoder_blobs_mismatch", "decoder_blobs_not_pinned", "event_v_off")


class Refused(Exception):
    """A configuration, path or resume the writer will not run with. Text only, never file content."""


class Fatal(Exception):
    """Stop with `code` as the exit status: the output is intact, the problem is for a human."""

    def __init__(self, why: str, rc: int) -> None:
        super().__init__(why)
        self.why, self.rc = why, rc


# ------------------------------------------------------------------------------------------------------------------------------------ small helpers
def now_ms() -> int:
    return time.time_ns() // 1_000_000


def hour_key(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_start_ms(h: str) -> int:
    return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def open_name(h: str) -> str:
    return f"tape-{h}.jsonl"


def clean(obj: Any) -> Any:
    """tools.h5_shadow.clean (the arena feed's serialiser input): NaN / inf become None. Copied so this module stays stdlib-only; a test compares the two."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def row_bytes(row: dict[str, Any]) -> bytes:
    """One tape line: strict compact JSON, as the arena feed's TapeWriter writes it (json.dumps(clean(rec), separators=(",", ":"), allow_nan=False) + newline)."""
    return (json.dumps(clean(row), separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_file(p: Path) -> tuple[int, int, str]:
    h, n, lines = hashlib.sha256(), 0, 0
    with open(p, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            lines += b.count(b"\n")
    return n, lines, h.hexdigest()


def write_json_atomic(path: Path, obj: Any, *, fsync: bool = False) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, path)


def fsync_dir(d: Path) -> None:
    fd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rss_mb(path: str = "/proc/self/status") -> dict[str, float | None]:
    out: dict[str, float | None] = {"rss_mb": None, "peak_rss_mb": None}
    try:
        for ln in open(path, encoding="utf-8"):
            if ln.startswith("VmRSS:"):
                out["rss_mb"] = round(int(ln.split()[1]) / 1024, 1)
            elif ln.startswith("VmHWM:"):
                out["peak_rss_mb"] = round(int(ln.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return out


def check_out_dir(path: str | os.PathLike, roots: Sequence[str] = OUT_ROOTS) -> Path:
    """The derived dir must be a strict child of one of `roots` (absolute, normalised), must not carry a sealed token below the root, and its realpath must satisfy the same."""
    p = os.fspath(path)
    if not p or "\0" in p or not os.path.isabs(p):
        raise Refused("out_dir_not_absolute")
    for cand in (os.path.normpath(p), os.path.realpath(p)):
        if tr.ARCHIVE_MARK in cand or any(cand == x or cand.startswith(x + "/") for x in REFUSED_OUT_PREFIXES):
            raise Refused("out_dir_sealed_or_walker_fs")
        root = next((r for r in roots if cand.startswith(os.path.normpath(r) + "/")), None)
        if root is None:
            raise Refused("out_dir_not_allowlisted")
        low = cand[len(os.path.normpath(root)):].lower()
        if any(tok in low for tok in FORBIDDEN_PARTS):
            raise Refused("out_dir_sealed_name")
    return Path(os.path.normpath(p))


def check_tip_dir(tip_dir: str | os.PathLike, out_dir: str | os.PathLike) -> None:
    """W2: the production tip dir, or a synthetic dir under the tmp / scratch roots (tests); never the archive, never the output dir or a parent/child of it."""
    import tempfile

    real = os.path.realpath(os.fspath(tip_dir))
    ok = real == tr.DEFAULT_TIP_DIR or any(real.startswith(r.rstrip("/") + "/") for r in (tempfile.gettempdir(), "/data2/mal/scratch"))
    if not ok or tr.ARCHIVE_MARK in real:
        raise Refused("tip_dir_not_allowed")
    o = os.path.realpath(os.fspath(out_dir))
    if o == real or o.startswith(real + "/") or real.startswith(o + "/"):
        raise Refused("out_dir_overlaps_tip_dir")


def code_pins() -> dict[str, Any]:
    """What the registration pins: sha256 of this file, the adapter and the vendored reader, and the three decoder blobs the reader checks."""
    def sha(mod_file: str) -> str:
        return hashlib.sha256(Path(mod_file).read_bytes()).hexdigest()

    return {"writer_sha256": sha(__file__), "adapter_sha256": sha(ta.__file__), "reader_sha256": sha(tr.__file__), "decoder_blobs": dict(tr.PINNED_BLOBS)}


# ------------------------------------------------------------------------------------------------------------------------------------ snapshot / restore of the reader and the adapter
def snapshot_source(src: ta.ArenaTipSource) -> dict[str, Any]:
    """The pipeline state at a poll boundary, as JSON. Pure: nothing in the reader or the adapter is touched. A cursor's position is where its first UNRELEASED byte starts (rows it holds
    for slots not yet returned are read again after a restore, exactly as TipReader.rewind does at the end of a caught-up poll)."""
    r, a = src.reader, src.adapter

    def pos(c: Any) -> int:
        return int(c.rows[0][2]) if c.rows else int(c.offset - len(c.tail))

    cur = {k: pos(c) for k, c in (r._cur or {}).items()} if r._cur is not None and r._cur_h == r.h else {}
    return {"reader": {"h": r.h, "D": r.D, "last_t_ms": r.last_t_ms, "hw": r.hw, "released_D": r.released_D, "counters": list(r._counters) if r._counters else None,
                       "stats": dict(r.stats), "cursors": cur, "gaps_pos": pos(r._gaps), "latched": r.latched},
            "adapter": {"stats": dict(a.stats), "seq": a._seq, "last_t": a.last_t, "last_slot": a.last_slot, "grp_slot": a._grp_slot,
                        "grp": {p: [g[0], g[1], sorted(g[2]), g[3]] for p, g in a._grp.items()},
                        "pending": [[lo, hi, seq, [g.lo, g.hi, g.reason, g.t_recv_ms, g.slots], prog] for lo, hi, seq, g, prog in a._pending]}}


def restore_source(src: ta.ArenaTipSource, snap: dict[str, Any]) -> None:
    """Put a freshly built source (same start_ms / catch_up_hours as the snapshot's) back where `snap` was taken. The reader is the VENDORED, blob-pinned file, so its attribute names
    cannot drift unseen (tools/test_arena_tip_tape_writer.py kills and restores at every poll and compares bytes)."""
    r, a, rs, As = src.reader, src.adapter, snap["reader"], snap["adapter"]
    r.h, r.D, r.last_t_ms, r.hw, r.released_D = rs["h"], rs["D"], rs["last_t_ms"], rs["hw"], rs["released_D"]
    r._counters = tuple(rs["counters"]) if rs["counters"] else None
    r.latched = rs["latched"]
    r.stats.update(rs["stats"])
    r._cur, r._cur_h = None, None
    for k, c in r._cursors().items():
        c.offset = int(rs["cursors"].get(k, 0))
        c.missing = c.offset == 0
    r._gaps.offset, r._gaps.missing = int(rs["gaps_pos"]), int(rs["gaps_pos"]) == 0
    a.stats.update(As["stats"])
    a._seq, a.last_t, a.last_slot, a._grp_slot = As["seq"], As["last_t"], As["last_slot"], As["grp_slot"]
    a._grp = {p: [g[0], g[1], set(g[2]), g[3]] for p, g in As["grp"].items()}
    a._pending = [(lo, hi, seq, tr.TipGap(g[0], g[1], g[2], g[3], g[4]), prog) for lo, hi, seq, g, prog in As["pending"]]


# ------------------------------------------------------------------------------------------------------------------------------------ compaction of closed hours
class Compactor:
    """Background thread: tape-<hour>.jsonl -> .jsonl.zst for CLOSED hours (tools.arena_zst.compact_file verifies the .zst by a full decompress before it deletes the source). The writer
    never touches a hour it has queued. Three tries per hour; a failure keeps the .jsonl (the transport and the readers keep using it)."""

    def __init__(self, out_dir: Path, compact: Callable[[Path], dict] = compact_file, retry_s: float = 5.0) -> None:
        self.dir, self._compact, self.retry_s = out_dir, compact, retry_s
        self.q: queue.Queue = queue.Queue()
        self.queued: set[str] = set()
        self.tries: Counter = Counter()
        self.lock = threading.Lock()
        self.stats: dict[str, Any] = {"files": 0, "failed": 0, "bytes_in": 0, "bytes_out": 0, "last_ratio": None, "last_error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        for p in self.dir.glob("tape-*.jsonl.zst.tmp"):
            p.unlink(missing_ok=True)  # a killed run's leftover
        self._thread = threading.Thread(target=self._run, name="arena-tip-compactor", daemon=True)
        self._thread.start()

    def submit(self, h: str) -> None:
        name = open_name(h)
        if name not in self.queued:
            self.queued.add(name)
            self.q.put(name)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                name = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            ok = False
            try:
                p = self.dir / name
                if p.exists():  # absent = already compacted by a previous run
                    info = self._compact(p)
                    with self.lock:
                        self.stats["files"] += 1
                        self.stats["bytes_in"] += info["bytes_in"]
                        self.stats["bytes_out"] += info["bytes_out"]
                        self.stats["last_ratio"] = info["ratio"]
                ok = True
            except Exception as exc:  # noqa: BLE001 - the source is kept; retried
                self.tries[name] += 1
                with self.lock:
                    self.stats["failed"] += 1
                    self.stats["last_error"] = type(exc).__name__
            if ok or self.tries[name] >= COMPACT_TRIES or self._stop.wait(self.retry_s):
                self.queued.discard(name)
            else:
                self.q.put(name)

    def drain(self, timeout: float = 120.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.q.empty() and not self.queued:
                return True
            time.sleep(0.02)
        return False

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            s = dict(self.stats)
        s["queued"] = len(self.queued)
        return s


# ------------------------------------------------------------------------------------------------------------------------------------ the writer
class Writer:
    """See the module docstring. One thread (the host's): step() does one poll-write-close-snapshot cycle; run() loops it."""

    def __init__(self, source: ta.ArenaTipSource, out_dir: str | os.PathLike, *, start_ms: int, catch_up_hours: int, grace_ms: int = GRACE_MS_DEFAULT,
                 min_free_pct: float = MIN_FREE_PCT_DEFAULT, poll_limit: int = POLL_LIMIT, compress: bool = True,
                 snapshot_s: float = SNAPSHOT_EVERY_S, status_s: float = STATUS_EVERY_S, max_unhealthy_s: float = MAX_UNHEALTHY_S_DEFAULT,
                 catch_up_deadline_min: float = CATCH_UP_DEADLINE_MIN, keep_verified_h: float = VERIFIED_KEEP_H_DEFAULT, clock: Callable[[], int] = now_ms, disk_usage: Callable[[str], Any] = shutil.disk_usage, out_roots: Sequence[str] = OUT_ROOTS,
                 compact: Callable[[Path], dict] = compact_file, pins: dict[str, Any] | None = None) -> None:
        if not 0 <= catch_up_hours <= LOOKBACK_MAX_H:
            raise Refused(f"catch_up_hours must be 0..{LOOKBACK_MAX_H}")
        if compress and zstd_path() is None:
            raise Refused("zstd_missing")  # a closed hour cannot be compacted, and the transport verifies it through zstd
        self.src = source
        self.dir = check_out_dir(out_dir, out_roots)
        self.meta = self.dir / META_DIR
        self.meta.mkdir(parents=True, exist_ok=True)
        self._lock_fd: int | None = os.open(str(self.meta / "lock"), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise Fatal("dir_locked", EXIT_LOCKED) from None
        self.cfg = {"start_ms": int(start_ms), "catch_up_hours": int(catch_up_hours), "grace_ms": int(grace_ms)}
        self.grace_ms, self.min_free_pct = int(grace_ms), float(min_free_pct)
        self.poll_limit, self.compress = int(poll_limit), compress
        self.snapshot_s, self.status_s, self.max_unhealthy_s = float(snapshot_s), float(status_s), float(max_unhealthy_s)
        self.clock, self.disk_usage = clock, disk_usage
        self.pins = pins or code_pins()
        self.compactor = Compactor(self.dir, compact)
        self.fds: dict[str, Any] = {}  # open hour -> file object (append, binary)
        self.size: dict[str, int] = {}  # open hour -> bytes committed (written by this writer, or verified against the disk)
        self.verify: dict[str, int] = {}  # open hour -> bytes on disk beyond `size` that the regenerated rows must reproduce (resume)
        self.tally: dict[str, dict[str, Any]] = {}
        self.closed: dict[str, dict[str, Any]] = {}  # hour -> counts row (also in counts.jsonl)
        self.last_data_t: dict[str, int] = {}
        self.max_t = 0
        self.totals: Counter = Counter()
        self.state = "init"
        self.problem: str | None = None
        self.started_ms = self.clock()
        self.unhealthy_since: float | None = None
        self.paused = False
        self.resumed = False
        self._last_snapshot = float("-inf")
        self._last_status = float("-inf")
        self._last_prune = float("-inf")
        self._dirty = False
        self.tip_status_problem: str | None = None
        self.deadline_min, self.keep_verified_h = float(catch_up_deadline_min), float(keep_verified_h)
        self.run_id = hashlib.sha256(f"{os.getpid()}-{time.time_ns()}".encode()).hexdigest()[:12]
        self.status_failures: dict[str, list[list[Any]]] = {}  # hour -> [[instant ms, code]]: W3 failures (the hour-health `status_bad_samples`)
        self.hb_gaps: list[list[int]] = []  # [from_ms, to_ms] between two cycles of this writer, longer than HB_GAP_MS (a restart shows here)
        self.last_step_ms: int | None = None
        self.last_healthy_ms: int | None = None
        self.bad_kind: str | None = None  # the W9 gap kind of the unhealthy spell in progress
        self.counts_base: dict[str, int] = {}  # adapter counts at the last close (the counts row holds the delta)

    # -- start / resume
    def start(self) -> None:
        """Fresh start: the source's own checks (io priority, healthy status, first history hour). Resume: the state is validated and restored first; the history-hour check is skipped
        (the first hour may be gone by now; the state says where the reader is)."""
        sp = self.meta / "state.json"
        st = None
        if sp.exists():
            try:
                st = json.loads(sp.read_bytes())
            except (OSError, ValueError):
                raise Refused("state_unreadable") from None
            self._validate_state(st)
        if st is None:
            self.src.start()
        else:
            if self.src.require_io_idle and not self.src.io_hook():
                raise ta.TipUnhealthy("ionice: nice 19 / ionice -c3 could not be applied")
            why = self.src.status_problem()
            if why:
                raise ta.TipUnhealthy(why)
            restore_source(self.src, st["source"])
            self.src.started = True
            self._restore_output(st)
            self.resumed = True
        if self.compress:
            self.compactor.start()
            for h in sorted(self.closed):
                if (self.dir / open_name(h)).exists():
                    self.compactor.submit(h)  # closed before the last stop, compaction not finished
        self._ensure_counts()
        self.state = "catching_up"
        self.snapshot(force=True)
        self.heartbeat(force=True)

    def _validate_state(self, st: dict[str, Any]) -> None:
        if st.get("schema") != SCHEMA_STATE:
            raise Refused("state_schema")
        if st.get("config") != self.cfg:
            raise Refused("config_changed")
        for k, v in st.get("pins", {}).items():
            if v != self.pins.get(k):
                raise Refused(f"pins_changed_{k}")
        if set(st.get("pins", {})) != set(self.pins):
            raise Refused("pins_changed_keys")

    def _restore_output(self, st: dict[str, Any]) -> None:
        self.closed = dict(st["closed"])
        self.last_data_t = dict(st["last_data_t"])
        self.max_t = int(st["max_t"])
        self.totals = Counter(st["totals"])
        self.status_failures, self.hb_gaps = dict(st.get("status_failures", {})), list(st.get("hb_gaps", []))
        self.last_step_ms, self.last_healthy_ms, self.bad_kind = st.get("last_step_ms"), st.get("last_healthy_ms"), st.get("bad_kind")
        self.counts_base = dict(st.get("counts_base", {}))
        for h, o in st["open"].items():
            self.tally[h] = o["tally"]
            self.size[h] = int(o["bytes"])
        found = {p.name: p for p in self.dir.iterdir() if p.is_file()}
        for h in list(self.size):
            if open_name(h) not in found:
                raise Refused(f"open_hour_missing_{h}")
        for name, p in found.items():
            if not (name.startswith("tape-") and name.endswith(".jsonl")):
                continue
            h = name[5:-6]
            if h in self.closed:
                continue  # closed, waiting for compaction
            if h not in self.size:
                self.size[h], self.tally[h] = 0, self._new_tally()  # created after the last snapshot: all of it is unverified tail
            disk = p.stat().st_size
            if disk > 0:
                with open(p, "rb") as fh:
                    back = min(disk, TORN_SCAN_BYTES)
                    fh.seek(disk - back)
                    tail = fh.read()
                if not tail.endswith(b"\n"):  # a kill mid-write: the transport never shipped the partial line, so it can be cut (back to the last newline)
                    i = tail.rfind(b"\n")
                    if i < 0 and disk > back:
                        raise Refused(f"torn_tail_unreadable_{h}")
                    keep = disk - back + i + 1
                    with open(p, "r+b") as fh:
                        fh.truncate(keep)
                        fh.flush()
                        os.fsync(fh.fileno())
                    disk = keep
            if disk < self.size[h]:
                raise Refused(f"out_shorter_than_state_{h}")
            if disk > self.size[h]:
                self.verify[h] = disk - self.size[h]

    # -- tallies and rows
    @staticmethod
    def _new_tally() -> dict[str, Any]:
        return {"rows": {}, "gaps": {}, "missed_slots": 0, "spans": [], "spans_dropped": 0, "no_tx_rows": 0, "silence_ms": {}, "late_rows": 0, "max_loss_ms": 0, "runs": []}

    def _note(self, h: str, row: dict[str, Any], late: bool) -> None:
        t = self.tally.setdefault(h, self._new_tally())
        if self.run_id not in t["runs"]:
            t["runs"].append(self.run_id)
        prog, typ = row.get("prog"), row.get("type")
        key = f"{prog}|{typ}"
        t["rows"][key] = t["rows"].get(key, 0) + 1
        if late:
            t["late_rows"] += 1
        if typ == "gap":
            gk = f"{row.get('kind')}|{prog}"
            t["gaps"][gk] = t["gaps"].get(gk, 0) + 1
            t["missed_slots"] += int(row.get("missed_slots") or 0)
            if row.get("data_loss") is True and isinstance(row.get("from_ms"), int) and isinstance(row.get("to_ms"), int):
                t["max_loss_ms"] = max(t["max_loss_ms"], row["to_ms"] - row["from_ms"])
            if len(t["spans"]) < SPANS_MAX:
                t["spans"].append([row.get("kind"), prog, row.get("from_slot"), row.get("to_slot"), row.get("from_ms"), row.get("to_ms")])
            else:
                t["spans_dropped"] += 1
        elif typ in DATA_TYPES:
            if typ == "trade" and prog == "pumpswap" and row.get("tx_index") is None:
                t["no_tx_rows"] += 1
            last = self.last_data_t.get(prog)
            if last is not None:
                t["silence_ms"][prog] = max(t["silence_ms"].get(prog, 0), int(row["t_recv_ms"]) - last)
            self.last_data_t[prog] = max(int(row["t_recv_ms"]), last or 0)

    def _target_hour(self, t_ms: int) -> tuple[str, bool]:
        """(file hour, late). The hour of t_recv_ms, unless that hour is closed: then the hour of max(newest row, the follower's checkpoint), which is never a closed hour (an hour closes
        only when the checkpoint is a grace past its end)."""
        h = hour_key(t_ms)
        if h not in self.closed:
            return h, False
        ck = getattr(self.src.reader, "last_t_ms", None) or 0
        cur = hour_key(max(self.max_t, ck, t_ms))
        if cur in self.closed:
            raise Fatal("no_open_hour_for_late_row", EXIT_IO)
        return cur, True

    def _append(self, h: str, data: bytes) -> None:
        skip = 0
        left = self.verify.get(h, 0)
        if left:
            skip = min(left, len(data))
            with open(self.dir / open_name(h), "rb") as fh:
                fh.seek(self.size[h])
                disk = fh.read(skip)
            if disk != data[:skip]:
                raise Fatal(f"resume_diverged_{h}", EXIT_DIVERGED)
            self.verify[h] = left - skip
            if not self.verify[h]:
                del self.verify[h]
            self.size[h] += skip
            self.totals["verified_tail_bytes"] += skip
        rest = data[skip:]
        if not rest:
            return
        fh = self.fds.get(h)
        if fh is None:
            p = self.dir / open_name(h)
            new = not p.exists()
            fh = open(p, "ab")
            self.fds[h] = fh
            if new:
                fsync_dir(self.dir)
        fh.write(rest)
        fh.flush()
        self.size[h] = self.size.get(h, 0) + len(rest)
        self.totals["bytes_written"] += len(rest)

    def _write_rows(self, rows: list[dict[str, Any]]) -> None:
        per: dict[str, list[bytes]] = {}
        for row in rows:
            h, late = self._target_hour(int(row["t_recv_ms"]))
            if late:
                self.totals["late_to_current"] += 1
            b = row_bytes(row)
            per.setdefault(h, []).append(b)
            self.size.setdefault(h, 0)
            self._note(h, row, late)
            self.max_t = max(self.max_t, int(row["t_recv_ms"]))
            self.totals["rows_written"] += 1
            if row["type"] == "gap":
                self.totals["gap_rows_written"] += 1
        for h in sorted(per):
            self._append(h, b"".join(per[h]))
        if rows:
            self._dirty = True

    # -- closing
    def _close_ready(self) -> list[str]:
        r = self.src.reader
        ck_t = getattr(r, "last_t_ms", None)
        done: list[str] = []
        if ck_t is None:
            return done
        for h in sorted(self.size):
            end = hour_start_ms(h) + H_MS
            if end + self.grace_ms > ck_t:
                break  # ascending: no later hour is older
            if not (self.src.caught_up or end <= r.h * 1000):
                break
            self._close(h)
            done.append(h)
        return done

    def _close(self, h: str) -> None:
        if self.verify.get(h):
            raise Fatal(f"resume_tail_unverified_{h}", EXIT_DIVERGED)  # the hour cannot close while disk bytes are still unaccounted for
        fh = self.fds.pop(h, None)
        if fh is not None:
            fh.flush()
            os.fsync(fh.fileno())
            fh.close()
        p = self.dir / open_name(h)
        if p.exists():
            n, lines, sha = sha256_file(p)
        else:
            n, lines, sha = 0, 0, hashlib.sha256().hexdigest()
        if n != self.size.get(h, 0):
            raise Fatal(f"hour_size_mismatch_{h}", EXIT_IO)
        t = self.tally.pop(h, self._new_tally())
        self.size.pop(h, None)
        ad = self.src.adapter.counts()
        delta = {k: ad.get(k, 0) - self.counts_base.get(k, 0) for k in ad}
        self.counts_base = dict(ad)
        hs, he = hour_start_ms(h), hour_start_ms(h) + H_MS
        row = {"schema": SCHEMA_COUNTS, "hour": h, "bytes": n, "lines": lines, "sha256": sha, "closed_ms": self.clock(), "run_id": self.run_id, "runs": t["runs"],
               "started_ms": self.started_ms, "pins": {k: v for k, v in self.pins.items() if k != "decoder_blobs"},
               "rows": dict(sorted(t["rows"].items())), "gap_rows": dict(sorted(t["gaps"].items())), "gap_missed_slots": t["missed_slots"],
               "gap_spans": t["spans"], "gap_spans_dropped": t["spans_dropped"],  # [kind, prog, from_slot, to_slot, from_ms, to_ms]
               "max_loss_span_s": round(t["max_loss_ms"] / 1000, 3), "max_data_silence_s": {k: round(v / 1000, 3) for k, v in sorted(t["silence_ms"].items())},
               "no_tx_index": {k: delta.get(k, 0) for k in ("rows_no_tx_index", "pool_slots_no_tx_index", "pool_slots_multi_sig", "pool_slots_mixed")},
               "no_tx_index_rows": t["no_tx_rows"], "dropped_rows": {k[len("rows_dropped_"):]: v for k, v in sorted(delta.items()) if k.startswith("rows_dropped_") and v}, "skips": delta.get("skips", 0),
               "status_failures": self.status_failures.pop(h, []), "status_bad_samples": 0, "late_rows": t["late_rows"],
               "hb_gaps": [g for g in self.hb_gaps if g[0] < he and g[1] > hs], "adapter_cumulative": ad, "checkpoint_t_ms": getattr(self.src.reader, "last_t_ms", None)}
        row["status_bad_samples"] = len(row["status_failures"])
        self.closed[h] = row
        while len(self.closed) > CLOSED_KEEP:
            self.closed.pop(min(self.closed))
        self.totals["hours_closed"] += 1
        self.snapshot(force=True)  # the close is durable BEFORE the compaction can replace the .jsonl
        self._append_counts(row)
        if self.compress:
            self.compactor.submit(h)

    def _append_counts(self, row: dict[str, Any]) -> None:
        with open(self.meta / "counts.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    def _ensure_counts(self) -> None:
        """A kill between the durable close and the counts append leaves a closed hour without its row: write it from the state."""
        have: set[str] = set()
        try:
            for ln in open(self.meta / "counts.jsonl", encoding="utf-8"):
                try:
                    have.add(json.loads(ln)["hour"])
                except (ValueError, KeyError, TypeError):
                    continue
        except FileNotFoundError:
            pass
        for h in sorted(self.closed):
            if h not in have:
                self._append_counts(self.closed[h])

    # -- state, status, housekeeping
    def snapshot(self, force: bool = False) -> None:
        mono = time.monotonic()
        if not force and (not self._dirty or mono - self._last_snapshot < self.snapshot_s):
            return
        for fh in self.fds.values():
            fh.flush()
            os.fsync(fh.fileno())
        st = {"schema": SCHEMA_STATE, "ms": self.clock(), "config": self.cfg, "pins": self.pins, "source": snapshot_source(self.src),
              "open": {h: {"bytes": self.size[h], "tally": self.tally.get(h, self._new_tally())} for h in sorted(self.size)}, "closed": self.closed,
              "last_data_t": self.last_data_t, "max_t": self.max_t, "totals": dict(self.totals), "status_failures": self.status_failures,
              "hb_gaps": self.hb_gaps[-100:], "last_step_ms": self.last_step_ms, "last_healthy_ms": self.last_healthy_ms, "bad_kind": self.bad_kind, "counts_base": self.counts_base}
        write_json_atomic(self.meta / "state.json", st, fsync=True)
        self._last_snapshot, self._dirty = mono, False

    def status(self) -> dict[str, Any]:
        r = self.src.reader
        now = self.clock()
        ck_t = getattr(r, "last_t_ms", None)
        ad = self.src.adapter.counts()
        newest = max(self.closed) if self.closed else None
        return {"schema": SCHEMA_STATUS, "ms": now, "pid": os.getpid(), "state": self.state, "problem": self.problem, "caught_up": bool(self.src.caught_up) and self.problem is None,
                "resumed": self.resumed, "started_ms": self.started_ms, "start_ms": self.cfg["start_ms"], "catch_up_hours": self.cfg["catch_up_hours"], "grace_ms": self.grace_ms,
                "released_slot": getattr(r, "released_D", None), "released_t_ms": ck_t, "lag_ms": (now - ck_t) if isinstance(ck_t, int) else None,
                "open_hours": sorted(self.size), "closed_hours": len(self.closed), "last_closed_hour": newest, "hours_closed_run": self.totals["hours_closed"],
                "rows_written": self.totals["rows_written"], "bytes_written": self.totals["bytes_written"], "verified_tail_bytes": self.totals["verified_tail_bytes"],
                "late_to_current": self.totals["late_to_current"], "gaps": {"rows": ad.get("gap_rows", 0), "written": self.totals["gap_rows_written"],
                "missed_slots": ad.get("gap_missed_slots", 0), "seen": ad.get("gaps_seen", 0), "by_reason": {k[4:]: v for k, v in sorted(ad.items()) if k.startswith("gap_") and k not in (
                    "gap_rows", "gap_missed_slots")}, "pending": len(self.src.adapter.pending_gaps())},
                "skips": ad.get("skips", 0),
                "no_tx_index": {k: ad.get(k, 0) for k in ("rows_no_tx_index", "pool_slots_no_tx_index", "pool_slots_multi_sig", "pool_slots_mixed")},
                "dropped_rows": {k[len("rows_dropped_"):]: v for k, v in sorted(ad.items()) if k.startswith("rows_dropped_")},
                "decoder_pin": {"ok": self.tip_status_problem not in DECODER_PIN_PROBLEMS, "tip_status_problem": self.tip_status_problem,
                                "blobs": dict(tr.PINNED_BLOBS)}, "reader_stats": dict(r.stats), "pins": {k: v for k, v in self.pins.items() if k != "decoder_blobs"},
                "compaction": self.compactor.snapshot(), "paused_disk": self.paused, "run_id": self.run_id, "status_bad_samples_run": sum(len(v) for v in self.status_failures.values()),
                "newest_data_t_ms": dict(self.last_data_t), "input_age_s": {k: round((now - v) / 1000, 1) for k, v in sorted(self.last_data_t.items())},
                "max_input_age_s": round(max(((now - v) for v in self.last_data_t.values()), default=0) / 1000, 1), "catch_up_deadline_min": self.deadline_min,
                "caught_up_once": bool(self.totals["caught_up_once"]), **rss_mb()}

    def heartbeat(self, force: bool = False) -> None:
        mono = time.monotonic()
        if not force and mono - self._last_status < self.status_s:
            return
        write_json_atomic(self.meta / "status.json", self.status())
        self._last_status = mono

    def disk_ok(self) -> bool:
        du = self.disk_usage(str(self.dir))
        pct = du.free * 100.0 / du.total if du.total else 0.0
        need = self.min_free_pct + (2.0 if self.paused else 0.0)  # hysteresis after a pause
        self.paused = pct < need
        return not self.paused

    def verified_hours(self) -> dict[str, int]:
        """hour -> the instant the receiver verified it (.push/verified.json, written by the pusher from the receiver's acks). Missing or unreadable: nothing is verified."""
        try:
            obj = json.loads((self.dir / ".push" / "verified.json").read_bytes())
            return {h: int(v) for h, v in obj.get("hours", {}).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            return {}

    def prune(self, force: bool = False) -> int:
        """B5: a closed hour is deleted keep_verified_h after the receiver verified it, never before (no verification, no delete: the disk reserve pauses the writer first)."""
        mono = time.monotonic()
        if not force and mono - self._last_prune < PRUNE_EVERY_S:
            return 0
        self._last_prune = mono
        ver, now, n = self.verified_hours(), self.clock(), 0
        for p in sorted(self.dir.iterdir()):
            name = p.name
            if not (name.startswith("tape-") and name.endswith(".jsonl.zst")):
                continue
            h = name[5:18]
            if h in self.closed and h in ver and now - ver[h] >= self.keep_verified_h * H_MS:
                p.unlink()
                n += 1
        if n:
            fsync_dir(self.dir)
        return n

    # -- the loop
    def step(self) -> int:
        """One cycle. Returns the rows written. Raises Fatal for the conditions that stop the writer."""
        if not self.disk_ok():
            self.state = "disk_reserve"
            self.heartbeat()
            return 0
        rows = self.src.poll(self.poll_limit)
        sp = self.src.status_problem()
        self.tip_status_problem = sp
        self.problem = self.src.problem
        r = self.src.reader
        now = self.clock()
        if self.last_step_ms is not None and now - self.last_step_ms > HB_GAP_MS:
            self.hb_gaps.append([self.last_step_ms, now])
        self.last_step_ms = now
        if getattr(r, "latched", None):
            self.state = "failed"
            self.heartbeat(force=True)
            raise Fatal(f"reader_latched_{r.latched}", EXIT_LATCHED)
        if sp:  # W3 failed at this instant (the hour-health `status_bad_samples`)
            fl = self.status_failures.setdefault(hour_key(now), [])
            if len(fl) < 200:
                fl.append([now, sp])
        if self.problem:
            if self.unhealthy_since is None:
                self.unhealthy_since = time.monotonic()
                self.bad_kind = KIND_STATUS_UNHEALTHY if str(self.problem).startswith("tip:") else KIND_READER_PROBLEM
            self.state = "unhealthy"
            if time.monotonic() - self.unhealthy_since > self.max_unhealthy_s:
                self.heartbeat(force=True)
                raise Fatal(f"tip_unhealthy_{self.problem}", EXIT_UNHEALTHY)
            self.heartbeat()
            return 0
        self.unhealthy_since = None
        if self.src.caught_up and not self.totals["caught_up_once"]:
            self.totals["caught_up_once"] = 1
            self.totals["caught_up_ms"] = now
            r.stop_fadvise()  # catch-up is over: on the live tail DONTNEED would start writeback of the follower's newest dirty pages
        if not self.totals["caught_up_once"] and now - self.started_ms > self.deadline_min * 60_000:
            self.heartbeat(force=True)
            raise Fatal("catch_up_deadline", EXIT_CATCHUP)  # W2: the output is for the manager to discard; a later start defines a new H0
        self.state = "live" if self.src.caught_up else "catching_up"
        gap_after = None
        if self.bad_kind is not None:  # W9: the spell is over; the rows it held back are written first (their t_recv_ms precede the recovery instant), then the span row
            gap_after = (self.bad_kind, self.last_healthy_ms if self.last_healthy_ms is not None else now, now)
            self.bad_kind = None
        self.last_healthy_ms = now
        self._write_rows(rows)
        if gap_after is not None:
            kind, f_ms, t_ms = gap_after
            t_ms = max(t_ms, f_ms, self.max_t)
            slot = int(getattr(r, "released_D", None) or getattr(r, "hw", None) or 0)
            self._write_rows([{"v": 1, "type": "gap", "prog": ta.GAP_PROG, "slot": slot, "t_recv_ms": t_ms, "signature": None, "event_index": None, "kind": kind, "data_loss": True,
                               "from_ms": f_ms, "to_ms": t_ms, "reason": kind[len("tip_gap_"):], "from_slot": slot, "to_slot": slot, "missed_slots": 0, "src": "writer"}])
        if rows or r.last_t_ms:
            self._dirty = True
        self._close_ready()
        self.snapshot()
        self.heartbeat()
        self.prune()
        return len(rows)

    def run(self, stop: threading.Event, max_s: float | None = None, sleep: Callable[[float], Any] | None = None) -> int:
        end = time.monotonic() + max_s if max_s else None
        try:
            while not stop.is_set() and (end is None or time.monotonic() < end):
                n = self.step()
                if n == 0 or self.src.caught_up:
                    (sleep or stop.wait)(POLL_S)
        except Fatal as exc:
            self.problem = exc.why
            self._finish(f"failed_{exc.why[:40]}")
            print(f"arena_tip_tape_writer FATAL {exc.why}", file=sys.stderr, flush=True)
            return exc.rc
        except OSError as exc:
            self.problem = f"io_{type(exc).__name__}"
            self._finish("failed_io")
            print(f"arena_tip_tape_writer FATAL io {type(exc).__name__}", file=sys.stderr, flush=True)
            return EXIT_IO
        self._finish("stopped")
        return EXIT_OK

    def _finish(self, state: str) -> None:
        self.state = state
        try:
            self.snapshot(force=True)
            self.heartbeat(force=True)
        except OSError:
            pass

    def close(self) -> None:
        for fh in list(self.fds.values()):
            try:
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fh.close()
        self.fds.clear()
        if self.compress:
            self.compactor.drain(60.0)
            self.compactor.stop()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None


# ------------------------------------------------------------------------------------------------------------------------------------ the consumer side check
def writer_problem(out_dir: str | os.PathLike, now: int | None = None, *, max_age_ms: int = STATUS_MAX_AGE_MS, need_caught_up: bool = True, skew_ms: int = 2_000,
                   status: dict[str, Any] | None = None) -> str | None:
    """None only for a fresh heartbeat of a live, healthy writer with a pinned decoder (and, if asked, caught up). FAIL CLOSED: anything else, including an unreadable or
    unexpected file, is a short code. `status` lets the transport pass the relayed heartbeat it holds instead of reading the dir."""
    now = now_ms() if now is None else int(now)
    st = status
    if st is None:
        p = Path(out_dir) / META_DIR / "status.json"
        try:
            ls = os.lstat(p)
            if not stat.S_ISREG(ls.st_mode) or ls.st_size > (64 << 10):
                return "status_unreadable"
            st = json.loads(p.read_bytes())
        except FileNotFoundError:
            return "status_missing"
        except (OSError, ValueError):
            return "status_unreadable"
    try:
        if not isinstance(st, dict) or st.get("schema") != SCHEMA_STATUS:
            return "status_schema"
        ms = st.get("ms")
        if not isinstance(ms, int) or isinstance(ms, bool):
            return "status_schema"
        if ms > now + skew_ms:
            return "status_future"
        if now - ms > max_age_ms:
            return "status_stale"
        if st.get("problem"):
            return "writer_problem"
        if st.get("state") not in ("live", "catching_up"):
            return "state_" + str(st.get("state"))[:20].replace(" ", "_")
        if (st.get("decoder_pin") or {}).get("ok") is not True:
            return "decoder_pin"
        if need_caught_up and not (st.get("caught_up") is True and st.get("state") == "live"):
            return "not_caught_up"
    except Exception:  # noqa: BLE001 - fail closed
        return "status_unreadable"
    return None


# ------------------------------------------------------------------------------------------------------------------------------------ CLI
def build_source(tip_dir: str, tip_state_dir: str, *, start_ms: int, catch_up_hours: int, require_io_idle: bool = True) -> ta.ArenaTipSource:
    """Production source: the guarded reader (hour floor 2026-10-16T01, archive and path refusals, the production floor not lowerable on the sealed dir) with the 72 h look-back."""
    reader = tr.TipReader(tip_dir, tip_state_dir, start_ms=start_ms, catch_up_hours=catch_up_hours, lookback_h=LOOKBACK_MAX_H)
    return ta.ArenaTipSource(reader, tip_state_dir, require_io_idle=require_io_idle)


def write_result(path: str | None, w: Writer, rc: int) -> None:
    if not path:
        return
    try:
        st = w.status()
        m = {k: st.get(k) for k in ("state", "problem", "rows_written", "bytes_written", "closed_hours", "late_to_current", "lag_ms", "rss_mb", "peak_rss_mb")}
        Path(path).write_text(json.dumps({"ok": rc == EXIT_OK, "metrics": m, "summary": f"arena tip writer ended rc={rc} state={st.get('state')}"}))
    except Exception:  # noqa: BLE001 - a result file must never turn a clean stop into a crash
        pass


def run_cmd(args: argparse.Namespace) -> int:
    if args.no_compress and os.environ.get("MAL_ARENA_TEST") != "1":
        print("arena_tip_writer refused: --no-compress is for tests only", file=sys.stderr)
        return EXIT_REFUSED
    try:
        out = check_out_dir(args.out_dir)
        check_tip_dir(args.tip_dir, out)
        state_path = out / META_DIR / "state.json"
        start_ms = args.start_ms
        if state_path.exists():  # a resume keeps the original window: the state's config is the source of truth
            try:
                cfg = json.loads(state_path.read_bytes())["config"]
            except (OSError, ValueError, KeyError):
                raise Refused("state_unreadable") from None
            if args.start_ms is not None and args.start_ms != cfg["start_ms"]:
                raise Refused("config_changed")
            start_ms, args.catch_up_hours, args.grace_ms = cfg["start_ms"], cfg["catch_up_hours"], cfg["grace_ms"]
        elif start_ms is None:
            start_ms = now_ms()
        src = build_source(args.tip_dir, args.tip_state_dir, start_ms=start_ms, catch_up_hours=args.catch_up_hours)
        w = Writer(src, out, start_ms=start_ms, catch_up_hours=args.catch_up_hours, grace_ms=args.grace_ms, keep_verified_h=args.keep_verified_h, catch_up_deadline_min=args.catch_up_deadline_min, min_free_pct=args.min_free_pct,
                   poll_limit=args.poll_limit, compress=not args.no_compress, max_unhealthy_s=args.max_unhealthy_s)
    except (Refused, tr.TipRefused) as exc:
        print(f"arena_tip_tape_writer refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Fatal as exc:
        print(f"arena_tip_tape_writer refused: {exc.why}", file=sys.stderr)
        return exc.rc
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    rc = EXIT_IO
    try:
        try:
            w.start()  # on the polling thread, before the compactor thread exists (nice 19 / ionice idle are per thread, inherited by threads created later)
        except (Refused, ta.TipUnhealthy, tr.TipRefused) as exc:
            print(f"arena_tip_tape_writer refused: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        rc = w.run(stop, max_s=args.max_s)
    finally:
        write_result(os.environ.get("MISCUSI_RESULT"), w, rc)
        w.close()
    return rc


def status_cmd(args: argparse.Namespace) -> int:
    why = writer_problem(args.out_dir, need_caught_up=not args.allow_catching_up)
    print(json.dumps({"problem": why}, sort_keys=True))
    return 0 if why is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tip follower tape -> closed arena hour files (fast-0). Paper only, no key, 0 credits.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="the long-running loop")
    r.add_argument("--tip-dir", default=tr.DEFAULT_TIP_DIR)
    r.add_argument("--tip-state-dir", default=tr.DEFAULT_STATE_DIR)
    r.add_argument("--out-dir", default=DEFAULT_OUT)
    r.add_argument("--start-ms", type=int, default=None, help="the window's anchor (default: now); a resume keeps the state's")
    r.add_argument("--catch-up-hours", type=int, default=CATCH_UP_H_DEFAULT, help=f"hours before the start hour to read (0..{LOOKBACK_MAX_H})")
    r.add_argument("--grace-ms", type=int, default=GRACE_MS_DEFAULT)
    r.add_argument("--keep-verified-h", type=float, default=VERIFIED_KEEP_H_DEFAULT, help="a closed hour is deleted this long after the receiver verified it (B5)")
    r.add_argument("--catch-up-deadline-min", type=float, default=CATCH_UP_DEADLINE_MIN)
    r.add_argument("--min-free-pct", type=float, default=MIN_FREE_PCT_DEFAULT)
    r.add_argument("--poll-limit", type=int, default=POLL_LIMIT)
    r.add_argument("--max-unhealthy-s", type=float, default=MAX_UNHEALTHY_S_DEFAULT)
    r.add_argument("--max-s", type=float, default=None)
    r.add_argument("--no-compress", action="store_true", help="tests only: keep closed hours as .jsonl")
    s = sub.add_parser("status", help="print the fail-closed verdict on the heartbeat (exit 0 healthy)")
    s.add_argument("--out-dir", default=DEFAULT_OUT)
    s.add_argument("--allow-catching-up", action="store_true")
    args = ap.parse_args(argv)
    return run_cmd(args) if args.cmd == "run" else status_cmd(args)


if __name__ == "__main__":
    sys.exit(main())
