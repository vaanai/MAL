#!/usr/bin/env python3
"""arena_tip_push: the tip writer's hour files (mal-fast-0) pushed BYTE FOR BYTE to mal-research-0 (HV7 tip plan, Transport B). PAPER ONLY: no key, no RPC, 0 credits, stdlib + the `zstd` CLI.
ISOLATED: this one file is the whole transport. If QP rules that the engine must run on fast-0 (plan option A), delete it and its job script; nothing else imports it.

WHY A PUSH. tools/tape_mirror.py pulls research-0 -> fast-0 (the consumer dials the source). Here the data flows the other way and the only ssh path that exists is fast-0 -> research-0
(`ssh mal-research-0`, Cloudflare Access; research-0 holds no credential to fast-0 and the Access config is Helm's). So fast-0 DIALS and the roles are kept as in tape_mirror:
  fast-0   `send`     runs tape_mirror's Server (the frame writer: live complete lines of the open hour, the closed hour's .zst in chunks with a sha256) over the writer's out dir
  research-0 `receive` runs tape_mirror's Puller (the verifier/installer, status, lag, retention, disk reserve) on the other end of the same ssh pipe, FROM a checkout pinned at the job's commit.
The protocol is tape_mirror's (PROTO 1) with two additions: the receiver's hello is the FIRST line on its stdout (it still owns `since` / `have` / `closed`), the sender relays the writer's
heartbeat inside every hb frame (`w`), and the receiver sends `ack` / `end` frames back (lag, sync state, why it ended).

BYTE-EXACT, VERIFIED (tape_mirror's checks, inherited unchanged): `d` frames append only at the offset the receiver already holds and only whole lines; a closed hour is received as <name>.part,
its sha256 must equal the sender's, `zstd -dc` of it must reproduce the receiver's live copy as an exact PREFIX, and only then is it renamed to .zst (the live .jsonl is removed after). A position
mismatch, a diverged closed hour or an undecodable .zst stops the receiver (exit 2, sender exit 2, state stopped_mismatch): fail closed for a human. A failed sha retries (reconnect, at most 3).
Neither side parses a row: byte counts, newline positions and sha256 only. Logs and status hold hour names, sizes, offsets, hashes and timings.

LAG. Per d frame: lag_new = receiver write done - the newest source byte's mtime, lag_old = receiver write done - the instant the sender last held exactly that offset (an upper bound for the oldest
byte); rolling 60 s p50/p90/max in .mirror/status.json and one row a minute in .mirror/lag.jsonl (both cross the two hosts' NTP clocks, about +-10 ms). These feed the book's feed delay d.

FAIL CLOSED (the consumer side, research-0): push_problem(dst) is None only when tape_mirror.mirror_problem(dst) is None (status fresh, state `connected`, synced within 3 s, a source write
within 15 s) AND the writer's heartbeat, relayed in the hb frames and stored in .mirror/writer_status.json, is fresh and healthy (tools.arena_tip_tape_writer.writer_problem: live, decoder pin ok,
no problem, caught up). A dead sender, a dead ssh, a stale or unpinned writer, a paused writer: all codes. `python3 tools/arena_tip_push.py status --dst D` prints it (exit 0 healthy).

SEALS. The out dir is a DERIVED COPY of the sealed tip tape and so is the receiver's dst: both are sealed as such (SEALS entry owed by the QP item-1 ruling); the sender reads only the writer's
dir (an allowlist of the writer's roots, sealed tokens refused, the directory opened once with O_DIRECTORY | O_NOFOLLOW and every file relative to it); the receiver writes only under
DST_ROOTS (the HV7 root below /data/mal/arena, or the scratch root) and never into the public arena tape. Walk-2-hour information (DEC-030 s2 R1).
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/arena_tip_push.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import fcntl
import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from tools import arena_tip_tape_writer as aw
from tools import tape_mirror as tm
from tools.decision_forwarder import BACKOFF_MS, ssh_argv

SCHEMA_PUSH = "arena_tip_push_status_v1"
PUSH_DIR = ".push"
WRITER_RELAY = "writer_status.json"
DST_ROOTS = ("/data2/mal/arena-tip-feed", tm.SCRATCH_ROOT)  # B1: the derived copy lives on /data2, never under /data/mal (the walk-2 walker's filesystem); another book is a reviewed addition here
PUBLIC_TAPE = tm.ALLOWED_SRC  # never written
ACK_EVERY_S = 1.0
ACK_SILENCE_MS_DEFAULT = 10_000
STATUS_EVERY_S = 0.5
RELAY_MAX = 64 << 10
EXIT_OK, EXIT_MISMATCH, EXIT_REFUSED = tm.EXIT_OK, tm.EXIT_MISMATCH, tm.EXIT_REFUSED
EXIT_RECONNECT, EXIT_DISK = 75, 76  # receiver exit codes the sender reads (also sent in the `end` frame)
RETAIN_HOURS_DEFAULT = 72  # the writer keeps the same
BACKFILL_HOURS_DEFAULT = 72
MIN_FREE_PCT_DEFAULT = 20.0


def check_dst(dst: str | os.PathLike, roots: Sequence[str] | None = None) -> Path:
    """The receiver's dir: a strict child of one of the roots, never the public arena tape, no sealed token below the root; judged as a string first, then by realpath."""
    roots = DST_ROOTS if roots is None else roots
    p = os.fspath(dst)
    if not p or "\0" in p or not os.path.isabs(p):
        raise ValueError("dst_not_absolute")
    for cand in (os.path.normpath(p), os.path.realpath(p)):
        if cand == PUBLIC_TAPE or cand.startswith(PUBLIC_TAPE + "/"):
            raise ValueError("dst_is_public_tape")
        if cand == "/data/mal" or cand.startswith("/data/mal/"):
            raise ValueError("dst_on_walker_fs")
        root = next((r for r in roots if cand.startswith(os.path.normpath(r) + "/")), None)
        if root is None:
            raise ValueError("dst_not_allowlisted")
        if any(tok in cand[len(os.path.normpath(root)):].lower() for tok in aw.FORBIDDEN_PARTS):
            raise ValueError("dst_sealed_name")
    return Path(os.path.normpath(p))


def newest_pumpswap_t(body: bytes) -> int | None:
    """t_recv_ms of the last PumpSwap data row of a frame body (the sender parses the one line; the receiver never parses a row), or None."""
    for ln in reversed(body.splitlines()):
        if b'"prog":"pumpswap"' in ln and b'"type":"gap"' not in ln and b'"type":"hb"' not in ln:
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            t = row.get("t_recv_ms") if isinstance(row, dict) else None
            if row.get("type") in aw.DATA_TYPES and isinstance(t, int) and not isinstance(t, bool):
                return t
    return None


def _read_text_nofollow(dirfd: int, rel: tuple[str, ...], limit: int = 64 << 20) -> bytes | None:
    fds = []
    try:
        cur = dirfd
        for part in rel[:-1]:
            cur = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            fds.append(cur)
        fd = os.open(rel[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=cur)
        try:
            data = os.read(fd, limit)
        finally:
            os.close(fd)
        return data
    except OSError:
        return None
    finally:
        for f in fds:
            os.close(f)


def _read_json_nofollow(dirfd: int, rel: tuple[str, ...]) -> dict | None:
    """A small JSON file below the directory fd (each component O_NOFOLLOW); None on any problem."""
    fds = []
    try:
        cur = dirfd
        for part in rel[:-1]:
            cur = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            fds.append(cur)
        fd = os.open(rel[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=cur)
        try:
            data = os.read(fd, RELAY_MAX + 1)
        finally:
            os.close(fd)
        if len(data) > RELAY_MAX:
            return None
        obj = json.loads(data)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        for f in fds:
            os.close(f)


# ------------------------------------------------------------------------------------------------------------------------------------ fast-0: the sender
class TipSender(tm.Server):
    """tape_mirror.Server over the WRITER's out dir (its own allowlist), relaying the writer's heartbeat in every hb frame. Every method but __init__ and _emit is tape_mirror's."""

    SRC_ROOTS: Sequence[str] = aw.OUT_ROOTS

    def __init__(self, src: str | Path, since: str, have: dict[str, int], closed: set[str], write: Callable[[bytes], None] | None = None,
                 clock_ns: Callable[[], int] = time.time_ns, zst_pass_bytes: int = tm.ZST_PASS_BYTES_DEFAULT, live_pass_bytes: int = tm.LIVE_PASS_BYTES,
                 hb_ms: int = tm.HB_MS_DEFAULT, roots: Sequence[str] | None = None) -> None:
        if roots is not None:
            self.SRC_ROOTS = roots
        try:
            p = aw.check_out_dir(src, self.SRC_ROOTS)
        except aw.Refused as exc:
            raise tm.Bad(tm.code(str(exc))) from None
        self.dirfd = os.open(os.path.realpath(str(p)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.readlink(f"/proc/self/fd/{self.dirfd}")  # what the kernel actually opened (a component swapped after the check shows here)
            aw.check_out_dir(opened, self.SRC_ROOTS)
        except (OSError, aw.Refused):
            os.close(self.dirfd)
            raise tm.Bad("src_symlink_escape") from None
        self.src = Path(opened)
        self.since = since
        self.sent = dict(have)
        self.closed = set(closed)
        self.open = {}
        self.zq = []
        self.zcur = None
        self.gone = set()
        self.write = write or self._stdout
        self.clock_ns = clock_ns
        self.zst_pass_bytes = int(zst_pass_bytes)
        self.live_pass_bytes = int(live_pass_bytes)
        self.hb_ms = int(hb_ms)
        self._out = []
        self._last_list = -1 << 62
        self._last_hb = -1 << 62
        self._rescan = True
        self.pending = False
        self.stats = collections.Counter()

    def writer_status(self) -> dict | None:
        return _read_json_nofollow(self.dirfd, (aw.META_DIR, "status.json"))

    def record(self, h: str) -> dict | None:
        """The writer's closed-hour record (bytes and sha256 of the uncompressed hour) from .writer/counts.jsonl, or None."""
        obj = _read_text_nofollow(self.dirfd, (aw.META_DIR, "counts.jsonl"))
        for ln in (obj or b"").splitlines():
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("hour") == h and isinstance(row.get("bytes"), int) and isinstance(row.get("sha256"), str):
                return {"bytes": row["bytes"], "sha256": row["sha256"]}
        return None

    def _queue(self, h: str) -> None:
        if self.record(h) is not None:  # B2: a closed hour is sent only with the writer's record of it to check against; without one it waits (the next listing asks again)
            super()._queue(h)

    def _emit(self, obj: dict, body: bytes = b"") -> None:
        k = obj.get("k")
        if k == "hb":
            obj = {**obj, "w": self.writer_status()}
        elif k == "d":
            obj = {**obj, "pt": newest_pumpswap_t(body)}  # B2: the newest PumpSwap t_recv_ms in the frame (the receiver logs it with its arrival instant)
        elif k == "ze":
            obj = {**obj, "rec": self.record(str(obj.get("f"))[5:18])}
        super()._emit(obj, body)


class Sender:
    """Dials (cmd = the receive command as argv, ssh in production), reads the receiver's hello, then streams frames and reads acks. Reconnects with backoff; stops (fail closed) on a mismatch
    or a local source problem. One per out dir (flock on <src>/.push/lock)."""

    def __init__(self, src: str | Path, cmd: Sequence[str], *, poll_ms: int = tm.POLL_MS_DEFAULT, hb_ms: int = tm.HB_MS_DEFAULT, zst_pass_bytes: int = tm.ZST_PASS_BYTES_DEFAULT,
                 ack_silence_ms: int = ACK_SILENCE_MS_DEFAULT, connect_timeout_ms: int = tm.CONNECT_TIMEOUT_MS, clock_ns: Callable[[], int] = time.time_ns,
                 roots: Sequence[str] | None = None) -> None:
        self.src = aw.check_out_dir(src, roots if roots is not None else TipSender.SRC_ROOTS)
        self.meta = self.src / PUSH_DIR
        self.meta.mkdir(parents=True, exist_ok=True)
        self._lock_fd: int | None = os.open(str(self.meta / "lock"), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise tm.Locked("src_locked") from None
        self.cmd, self.poll_s = list(cmd), max(1, int(poll_ms)) / 1000.0
        self.hb_ms, self.zst_pass_bytes, self.ack_silence_ms, self.connect_timeout_ms = int(hb_ms), int(zst_pass_bytes), int(ack_silence_ms), int(connect_timeout_ms)
        self.clock_ns = clock_ns
        self.roots = roots
        self.state = "init"
        self.connects = 0
        self.last_ack: dict | None = None
        self.last_ack_ms: int | None = None
        self.last_end: dict | None = None
        self.sent_bytes = 0
        self.events = 0
        self.proc: subprocess.Popen | None = None
        self._buf = b""
        self._last_status = float("-inf")
        self.refusals = 0
        self.verified: dict[str, int] = {}  # hour -> the instant the receiver's ack first listed it as verified and installed (B5: the writer deletes it 24 h later)
        try:
            self.verified = {h: int(v) for h, v in json.loads((self.meta / "verified.json").read_bytes()).get("hours", {}).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _ms(self) -> int:
        return self.clock_ns() // 1_000_000

    def event(self, kind: str, **kw: Any) -> None:
        self.events += 1
        row = {"kind": kind, "ms": self._ms(), **kw}
        with open(self.meta / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        print("arena_tip_push EVENT " + json.dumps(row, sort_keys=True), file=sys.stderr, flush=True)

    def status(self) -> dict:
        return {"schema": SCHEMA_PUSH, "state": self.state, "ms": self._ms(), "pid": os.getpid(), "connects": self.connects, "sent_bytes": self.sent_bytes, "events": self.events,
                "last_ack_ms": self.last_ack_ms, "last_ack": self.last_ack, "last_end": self.last_end}

    def write_status(self) -> None:
        aw.write_json_atomic(self.meta / "status.json", self.status())
        self._last_status = time.monotonic()

    def close(self) -> None:
        self._kill()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _kill(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
        for f in (p.stdin, p.stdout) if p else ():
            try:
                if f:
                    f.close()
            except OSError:
                pass

    # -- the receiver's lines (hello first, then acks)
    def _read_lines(self, timeout_s: float) -> list[bytes]:
        assert self.proc and self.proc.stdout
        fd = self.proc.stdout.fileno()
        r, _, _ = select.select([fd], [], [], timeout_s)
        if not r:
            return []
        data = os.read(fd, 1 << 16)
        if not data:
            raise EOFError
        self._buf += data
        if len(self._buf) > tm.HELLO_MAX:
            raise ValueError("receiver_line_too_long")
        *lines, self._buf = self._buf.split(b"\n")
        return lines

    def _hello(self) -> tuple[str, dict[str, int], set[str]]:
        end = time.monotonic() + tm.HELLO_TIMEOUT_S
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise ValueError("hello_timeout")
            for ln in self._read_lines(min(left, 0.5)):
                return tm.parse_hello(ln)

    def _note_receiver(self, lines: list[bytes]) -> None:
        for ln in lines:
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("k") == "ack":
                self.last_ack, self.last_ack_ms = {k: obj.get(k) for k in ("ms", "synced_ms", "lag_new_p90_ms", "behind_bytes", "closed_verified", "zst_pending", "state")}, self._ms()
                new = [h for h in obj.get("closed_hours") or [] if isinstance(h, str) and tm.HOUR_RE.match(h) and h not in self.verified]
                if new:
                    self.verified.update({h: self._ms() for h in new})
                    aw.write_json_atomic(self.meta / "verified.json", {"hours": dict(sorted(self.verified.items())[-400:])}, fsync=True)
            elif obj.get("k") == "end":
                self.last_end = {"why": tm.code(obj.get("why")), "rc": obj.get("rc") if isinstance(obj.get("rc"), int) else None}

    def _session(self, stop: threading.Event, deadline: float | None) -> str:
        """One connection. Returns why it ended; raises tm.Bad (a local source problem) and Mismatch-like stops as exceptions of the caller's contract."""
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
        self._buf, self.last_end = b"", None
        self.state = "connecting"
        self.connects += 1
        self.write_status()
        assert self.proc.stdin is not None
        try:
            since, have, closed = self._hello()
        except (EOFError, ValueError) as exc:
            return "no_hello_" + tm.code(str(exc))
        srv = TipSender(self.src, since, have, closed, write=self._write, clock_ns=self.clock_ns, zst_pass_bytes=self.zst_pass_bytes, hb_ms=self.hb_ms, roots=self.roots)
        try:
            srv.hello()
            self.state = "connected"
            self.last_ack_ms = self._ms()
            while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
                t0 = time.monotonic()
                srv.poll()
                try:
                    self._note_receiver(self._read_lines(0))
                except EOFError:
                    return "receiver_eof"
                if self.proc.poll() is not None:
                    return "receiver_exit"
                if self._ms() - (self.last_ack_ms or 0) > self.ack_silence_ms:
                    return "ack_silence"
                if time.monotonic() - self._last_status >= STATUS_EVERY_S:
                    self.write_status()
                dt = time.monotonic() - t0
                if dt < self.poll_s:
                    stop.wait(self.poll_s - dt)
            return "done"
        except (BrokenPipeError, ConnectionResetError):
            return "pipe"
        finally:
            self.sent_bytes += srv.stats["live_bytes"] + srv.stats["zst_bytes"]
            srv.close()

    def _write(self, data: bytes) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def run(self, max_s: float | None = None, stop: threading.Event | None = None) -> int:
        stop = stop or threading.Event()
        deadline = time.monotonic() + max_s if max_s else None
        attempt = 0
        try:
            while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
                t0 = time.monotonic()
                try:
                    why = self._session(stop, deadline)
                except tm.Bad as exc:  # the local out dir is not something to send (replaced, shrunk, outside the allowlist): fail closed
                    self.state = "stopped_source_bad"
                    self.event("source_bad", why=tm.code(str(exc)))
                    self.write_status()
                    return EXIT_MISMATCH
                self._kill()
                rc = self.proc.returncode if self.proc else None
                end_rc = (self.last_end or {}).get("rc")
                if why == "done":
                    self.state = "stopped"
                    self.write_status()
                    return EXIT_OK
                if EXIT_MISMATCH in (rc, end_rc):  # the receiver found a mismatch: it stopped for good, and so do we
                    self.state = "stopped_mismatch"
                    self.event("receiver_mismatch", why=(self.last_end or {}).get("why"))
                    self.write_status()
                    return EXIT_MISMATCH
                self.refusals = self.refusals + 1 if (rc == EXIT_REFUSED or end_rc == EXIT_REFUSED) else 0
                if self.refusals >= tm.REMOTE_BAD_MAX:
                    self.state = "stopped_refused"
                    self.event("receiver_refusing")
                    self.write_status()
                    return EXIT_REFUSED
                self.state = "disconnected"
                self.event("disconnect", why=why, rc=rc, end=self.last_end)
                self.write_status()
                wait = 30.0 if EXIT_DISK in (rc, end_rc) else BACKOFF_MS[min(attempt, len(BACKOFF_MS) - 1)] / 1000.0
                stop.wait(wait)
                attempt += 1
                if time.monotonic() - t0 > 60:
                    attempt = 1
            self.state = "stopped"
            self.write_status()
            return EXIT_OK
        finally:
            self.close()


# ------------------------------------------------------------------------------------------------------------------------------------ research-0: the receiver
class _Stdin:
    """What Puller._pump needs of a child's stdout: a fileno()."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def close(self) -> None:
        pass


class _Pipe:
    pid = 0
    returncode = None
    stdin = None

    def __init__(self, fd: int) -> None:
        self.stdout = _Stdin(fd)

    def poll(self) -> None:
        return None


class TipReceiver(tm.Puller):
    """tape_mirror.Puller on the far end of the ssh pipe: it reads frames from its own stdin and sends the hello (and acks) on its stdout. Same verification, install, retention, disk reserve,
    status (.mirror/status.json, schema tape_mirror_v1, so tape_mirror.mirror_problem works on it) and lag. Differences: the dst allowlist (check_dst; Puller refuses everything under
    /data/mal/arena, the HV7 root included), the stdio session, ack frames, and the relayed writer heartbeat."""

    def __init__(self, dst: str | Path, *, backfill_hours: int = BACKFILL_HOURS_DEFAULT, retain_hours: int = RETAIN_HOURS_DEFAULT, min_free_pct: float = MIN_FREE_PCT_DEFAULT,
                 stall_ms: int = tm.STALL_MS_DEFAULT, reconnect_silence_ms: int = tm.RECONNECT_SILENCE_MS_DEFAULT, connect_timeout_ms: int = tm.CONNECT_TIMEOUT_MS,
                 clock_ns: Callable[[], int] = time.time_ns, zstd: str | None = None, disk_usage: Callable[[str], Any] = shutil.disk_usage, progress_path: str | None = None,
                 disk_retry_s: float = tm.DISK_RETRY_S) -> None:
        if not 1 <= int(backfill_hours) <= int(retain_hours):
            raise ValueError("backfill_hours must be 1..retain_hours")
        d = check_dst(dst)  # replaces Puller.__init__'s "dst_under_arena" refusal (a test compares the attribute sets, so a change of Puller.__init__ cannot go unseen)
        self.zstd = zstd or shutil.which("zstd")
        if not self.zstd:
            raise ValueError("zstd_missing")
        self.dir = d
        self.meta = self.dir / tm.META_DIR
        self.meta.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(str(self.meta / "lock"), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise tm.Locked("dst_locked") from None
        self.cmd = []
        self.backfill_hours, self.retain_hours, self.min_free_pct = int(backfill_hours), int(retain_hours), float(min_free_pct)
        self.stall_ms = int(stall_ms)
        self.reconnect_silence_ms = max(int(reconnect_silence_ms), self.stall_ms)
        self.connect_timeout_ms = int(connect_timeout_ms)
        self.clock_ns = clock_ns
        self.disk_usage = disk_usage
        self.disk_retry_s = float(disk_retry_s)
        self.progress_path = progress_path
        self.status_path = self.meta / "status.json"
        self.events_path = self.meta / "events.jsonl"
        self.lag_path = self.meta / "lag.jsonl"
        self.state = "init"
        self.sizes = {}
        self.local_closed = set()
        self.verifying = set()
        self.fds = {}
        self.part = None
        self.zst_retry = collections.Counter()
        self.since = None
        self.last_frame_ms = 0
        self.last_remote_ms = None
        self.synced_ms = None
        self.src_mt_ms = None
        self.behind_bytes = None
        self.zst_pending = 0
        self.events = 0
        self.closed_verified = 0
        self.live_bytes = 0
        self.zst_bytes = 0
        self.connects = 0
        self.remote_bad_streak = 0
        self.src_gone = set()
        self.lag = collections.deque()
        self._minute = None
        self._last_fsync = time.monotonic()
        self._last_prune = float("-inf")
        self._last_status = float("-inf")
        self._last_progress = float("-inf")
        import queue

        self.expect = {}
        self._vq = queue.Queue()
        self._vres = queue.Queue()
        self._vthread = threading.Thread(target=self._verifier, name="arena-tip-verify", daemon=True)
        self._vthread.start()
        self.proc = None
        self._out: Any = None
        self.expect: dict[str, dict] = {}
        self._arr: dict | None = None
        self.arrival_path = self.meta / "arrival.jsonl"
        self._last_ack = float("-inf")
        self.writer_hb_ms: int | None = None
        self._startup()

    # -- B2: arrival log (timing only) and the writer's closed-hour record
    def _data(self, hdr: dict, body: bytes) -> None:
        pt = hdr.get("pt")
        if pt is not None and (isinstance(pt, bool) or not isinstance(pt, int)):
            raise tm.Mismatch("bad_pt")
        super()._data(hdr, body)
        t, m = self._ms(), self._ms() // 60_000
        a = self._arr
        if a is not None and a["m"] != m:
            self.flush_arrival()
            a = None
        if a is None:
            a = self._arr = {"m": m, "frames": 0, "bytes": 0, "pt": None, "pt_arrival_ms": None, "mt": None}
        a["frames"] += 1
        a["bytes"] += len(body)
        a["mt"] = hdr.get("mt")
        if pt is not None and (a["pt"] is None or pt >= a["pt"]):
            a["pt"], a["pt_arrival_ms"] = pt, t

    def flush_arrival(self) -> None:
        a, self._arr = self._arr, None
        if a:
            row = {"minute": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(a["m"] * 60)), "frames": a["frames"], "bytes": a["bytes"], "newest_pumpswap_t_recv_ms": a["pt"],
                   "newest_pumpswap_arrival_ms": a["pt_arrival_ms"], "newest_source_write_ms": a["mt"]}
            with open(self.arrival_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")

    def close(self) -> None:
        self.flush_arrival()
        super().close()

    def _zend(self, hdr: dict) -> None:
        m = tm.CLOSED_RE.match(str(hdr.get("f")))
        rec = hdr.get("rec")
        if not m:
            raise tm.Mismatch("bad_name")
        if not (isinstance(rec, dict) and isinstance(rec.get("bytes"), int) and not isinstance(rec.get("bytes"), bool) and isinstance(rec.get("sha256"), str) and len(rec["sha256"]) == 64):
            raise tm.Mismatch("closed_hour_no_record")
        self.expect[m.group(1)] = rec
        super()._zend(hdr)

    def _verifier(self) -> None:
        """tape_mirror's verifier plus B2: the decompressed hour must also equal the WRITER's record of it (size and sha256), not only the sender's file."""
        while True:
            item = self._vq.get()
            if item is None:
                return
            h, part, sha, size = item
            try:
                res = tm.verify_closed(self.zstd, part, self.dir / tm.open_name(h))
            except Exception as exc:  # noqa: BLE001 - reported to the main loop as a failed verify
                res = {"rc": -1, "error": type(exc).__name__, "prefix_ok": False}
            exp = self.expect.pop(h, None)
            if res.get("rc") == 0 and (exp is None or res["dec_bytes"] != exp["bytes"] or res["dec_sha"] != exp["sha256"]):
                res["prefix_ok"], res["record_mismatch"] = False, True
            self._vres.put((h, part, sha, size, res))

    # -- the relayed writer heartbeat
    def _hb(self, hdr: dict) -> None:
        super()._hb(hdr)
        w = hdr.get("w")
        if isinstance(w, dict) or w is None:
            self.writer_hb_ms = int(hdr["ms"])
            aw.write_json_atomic(self.meta / WRITER_RELAY, {"rx_ms": self._ms(), "hb_ms": self.writer_hb_ms, "w": w})

    # -- the stdio session
    def _kill(self) -> None:
        pass  # there is no child: the other end is the sender's ssh

    def _end(self, why: str, rc: int) -> None:
        try:
            self._out.write(tm.frame({"k": "end", "why": tm.code(why), "rc": rc}))
            self._out.flush()
        except (OSError, ValueError):
            pass

    def tick(self) -> None:
        super().tick()
        if self._out is not None and time.monotonic() - self._last_ack >= ACK_EVERY_S:
            self._last_ack = time.monotonic()
            news = [x[1] for x in self.lag if x[1] is not None]
            try:
                self._out.write(tm.frame({"k": "ack", "ms": self._ms(), "synced_ms": self.synced_ms, "lag_new_p90_ms": tm.pct(news, 90), "behind_bytes": self.behind_bytes,
                                          "closed_verified": self.closed_verified, "zst_pending": self.zst_pending, "state": self.state,
                                          "closed_hours": sorted(self.local_closed)[-100:]}))
                self._out.flush()
            except (OSError, ValueError):
                pass

    def run_stdio(self, stdin_fd: int, out: Any, stop: threading.Event) -> int:
        """Hello out, frames in, until the sender goes away (EOF), is silent, or an integrity stop. Exit code: 0 clean, 2 mismatch (permanent), 75 reconnect, 76 disk reserve."""
        self._out = out
        t0 = time.monotonic()
        try:
            self.drop_part()
            out.write(self.hello_line())
            out.flush()
            self.proc = _Pipe(stdin_fd)
            self.state = "connecting"
            self.connects += 1
            self.write_status()
            try:
                why = self._pump(None, stop, None, t0)
            except tm.Mismatch as exc:
                self.state = "stopped_mismatch"
                self.event("mismatch", why=tm.code(str(exc)))
                self.write_status()
                self._end(str(exc), EXIT_MISMATCH)
                return EXIT_MISMATCH
            except tm.Reconnect as exc:
                self.state = "disconnected"
                self.write_status()
                self._end(str(exc), EXIT_RECONNECT)
                return EXIT_RECONNECT
            except tm.DiskReserve:
                self.state = "disk_reserve"
                self.event("disk_reserve", free_pct=self.free_pct(), min_free_pct=self.min_free_pct)
                self.write_status()
                self._end("disk_reserve", EXIT_DISK)
                return EXIT_DISK
            self.state = "stopped" if why == "done" else "disconnected"
            self.write_status()
            self._end(why, EXIT_OK if why in ("done", "eof") else EXIT_RECONNECT)
            return EXIT_OK if why in ("done", "eof") else EXIT_RECONNECT
        finally:
            self._out = None


def push_problem(dst: str | Path, now: int | None = None, *, need_hours: int | None = None, need_caught_up: bool = True) -> str | None:
    """The consumer's check on research-0. None only when the transport is fresh, connected and synced (tape_mirror.mirror_problem) AND the relayed writer heartbeat is fresh and healthy."""
    now = tm.now_ms() if now is None else int(now)
    why = tm.mirror_problem(dst, now, need_hours=need_hours)
    if why:
        return why
    try:
        relay = json.loads((Path(dst) / tm.META_DIR / WRITER_RELAY).read_bytes())
        rx, hb = relay["rx_ms"], relay["hb_ms"]
        if not (isinstance(rx, int) and isinstance(hb, int)) or now - rx > tm.SYNC_MAX_AGE_MS:
            return "writer_relay_stale"
        return aw.writer_problem(dst, hb, status=relay.get("w"), need_caught_up=need_caught_up) or None
    except FileNotFoundError:
        return "writer_relay_missing"
    except (OSError, ValueError, KeyError, TypeError):
        return "writer_relay_unreadable"


# ------------------------------------------------------------------------------------------------------------------------------------ CLI
def remote_receive_cmd(ssh_host: str, remote_repo: str, dst: str, retain_hours: int, backfill_hours: int, min_free_pct: float, python: str = "python3") -> list[str]:
    """The production argv: ssh (BatchMode, keepalives) to research-0, cd to the pinned checkout, exec receive."""
    if not remote_repo.startswith("/") or "/../" in remote_repo + "/" or not dst.startswith("/"):
        raise ValueError("remote_repo and dst must be absolute paths without ..")
    remote = (f"cd {shlex.quote(remote_repo)} && exec {shlex.quote(python)} -u tools/arena_tip_push.py receive --dst {shlex.quote(dst)} "
              f"--retain-hours {int(retain_hours)} --backfill-hours {int(backfill_hours)} --min-free-pct {float(min_free_pct)}")
    return ssh_argv(ssh_host) + [remote]


def write_result(path: str | None, status: dict, rc: int) -> None:
    if not path:
        return
    try:
        Path(path).write_text(json.dumps({"ok": rc == EXIT_OK, "metrics": {k: status.get(k) for k in ("state", "connects", "sent_bytes", "events")}, "summary": f"arena tip push ended rc={rc} state={status.get('state')}"}))
    except Exception:  # noqa: BLE001
        pass


def run_send(args: argparse.Namespace) -> int:
    if bool(args.cmd_json) == bool(args.ssh_host):
        print("send: give exactly one of --ssh-host (with --remote-repo and --remote-dst) or --cmd-json (tests)", file=sys.stderr)
        return EXIT_REFUSED
    try:
        cmd = json.loads(args.cmd_json) if args.cmd_json else remote_receive_cmd(args.ssh_host, args.remote_repo or "", args.remote_dst or "", args.retain_hours, args.backfill_hours,
                                                                                 args.min_free_pct)
        sd = Sender(args.src, cmd, poll_ms=args.poll_ms, ack_silence_ms=args.ack_silence_ms)
    except (tm.Locked, ValueError, aw.Refused) as exc:
        print(f"send refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    rc = EXIT_MISMATCH
    try:
        rc = sd.run(max_s=args.max_s, stop=stop)
    finally:
        write_result(os.environ.get("MISCUSI_RESULT"), sd.status(), rc)
    return rc


def run_receive(args: argparse.Namespace) -> int:
    try:
        rx = TipReceiver(args.dst, backfill_hours=args.backfill_hours, retain_hours=args.retain_hours, min_free_pct=args.min_free_pct)
    except (tm.Locked, ValueError) as exc:
        try:
            sys.stdout.buffer.write(tm.frame({"k": "end", "why": tm.code(str(exc).split(":")[0].replace(" ", "_")), "rc": EXIT_REFUSED}))
            sys.stdout.buffer.flush()
        except OSError:
            pass
        print(f"receive refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    rc = EXIT_RECONNECT
    try:
        rc = rx.run_stdio(sys.stdin.fileno(), sys.stdout.buffer, stop)
    finally:
        rx.close()
    return rc


def run_status(args: argparse.Namespace) -> int:
    why = push_problem(args.dst, need_hours=args.need_hours, need_caught_up=not args.allow_catching_up)
    out: dict[str, Any] = {"problem": why}
    try:
        st = json.loads((Path(args.dst) / tm.META_DIR / "status.json").read_bytes())
        out.update({"state": st.get("state"), "history_hours": st.get("history_hours"), "lag_60s": st.get("lag_60s"), "zst_pending": st.get("zst_pending"), "free_pct": st.get("free_pct")})
    except (OSError, ValueError):
        pass
    print(json.dumps(out, sort_keys=True))
    return 0 if why is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tip writer hour files fast-0 -> research-0 (push form of the tape mirror). Paper only, no key, 0 credits.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="on mal-fast-0: dial research-0, stream the writer's out dir")
    s.add_argument("--src", default=aw.DEFAULT_OUT)
    s.add_argument("--ssh-host")
    s.add_argument("--remote-repo", help="absolute path of the pinned MAL checkout on research-0")
    s.add_argument("--remote-dst", help="the derived dir on research-0 (under DST_ROOTS)")
    s.add_argument("--cmd-json", help="tests only: the receive argv as a JSON list")
    s.add_argument("--retain-hours", type=int, default=RETAIN_HOURS_DEFAULT)
    s.add_argument("--backfill-hours", type=int, default=BACKFILL_HOURS_DEFAULT)
    s.add_argument("--min-free-pct", type=float, default=MIN_FREE_PCT_DEFAULT)
    s.add_argument("--poll-ms", type=int, default=tm.POLL_MS_DEFAULT)
    s.add_argument("--ack-silence-ms", type=int, default=ACK_SILENCE_MS_DEFAULT)
    s.add_argument("--max-s", type=float, default=None)
    r = sub.add_parser("receive", help="on mal-research-0 (run by ssh): hello out, frames in, verify and install")
    r.add_argument("--dst", required=True)
    r.add_argument("--retain-hours", type=int, default=RETAIN_HOURS_DEFAULT)
    r.add_argument("--backfill-hours", type=int, default=BACKFILL_HOURS_DEFAULT)
    r.add_argument("--min-free-pct", type=float, default=MIN_FREE_PCT_DEFAULT)
    q = sub.add_parser("status", help="print the consumer's fail-closed verdict (exit 0 healthy)")
    q.add_argument("--dst", required=True)
    q.add_argument("--need-hours", type=int, default=None)
    q.add_argument("--allow-catching-up", action="store_true")
    args = ap.parse_args(argv)
    return {"send": run_send, "receive": run_receive, "status": run_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
