"""decision_forwarder: ships ONE arena bot's hash-chained decision log from mal-research-0 (where the runner writes it) to mal-fast-0 (where the
live bridge runs), byte for byte. P46 transport, gap 2 of docs/research/p43-golive/READINESS.md (item N7). Design: docs/research/p46-transport/DESIGN.md.

No key, no order, no RPC. The only network is the ssh pipe the caller configures. This is a build, not a claim: it is not gate evidence.

Modes
  serve    research-0 (in production an authorized_keys forced command, so fast-0's key can run nothing else). Tails
           `<root>/decisions/<bot_id>/*.jsonl` and writes one JSON frame per stdout line:
             hello  {"k":"hello","v":SCHEMA,"bot":..,"ms":..}
             row    {"k":"row","f":<day file>,"o":<byte offset of the line>,"b":<the line, without its newline>}  (complete lines only)
             hb     {"k":"hb","ms":<remote ms>,"f":<file>,"o":<bytes consumed>}  every hb_ms
           A root under /data/mal/arena (realpath, or an ancestor with the arena's inode: a bind or alias) is refused unless --reads-dir is given
           and the bot's RECORDED PASS read passes tools.hv4_live_c0.read_record_problem (the bridge's own gate, QP #965; QP-RULING-1025 R2.1:
           no process reads an HV4 decision row before that arm's read is recorded). No instant is configured by hand, so nothing can be
           pre-staged with a planned read time. A served file must be a regular file with st_nlink == 1 (a hard-linked copy of arena files placed
           under another root is refused). Pure stdlib except that gate, which is imported only for a sealed root.
  pull     fast-0. Holds an exclusive flock on `<dst>/transport/<bot_id>.lock` for its whole life (a second puller exits refused). Production
           form: `pull --ssh-host mal-research-0` = ssh with SSH_OPTS (keepalives) and the remote command {resume}. Runs the serve command, checks every row with the DecisionLog chain rule (prev, seq + 1, sha256(prev + canon(row))) and
           that its offset equals the local file size, then appends the same bytes + newline (O_APPEND, flock, fsync) to
           `<dst>/decisions/<bot_id>/<file>`. So the local log verifies with DecisionLog.verify and its md5 / day sha256 equal the source's.
           Side files under `<dst>/transport/` (NEVER inside decisions/, where any extra row would latch the bridge's chain_break):
             <bot_id>.status.json   state, last frame instant, last remote heartbeat, seq, file, offset (atomic replace)
             <bot_id>.gaps.jsonl    a gap row on: stall (no frame for stall_ms), resume, disconnect, reconnect, chain/offset mismatch, local torn tail
           A chain or offset mismatch stops the puller (exit 2) and appends nothing. A disconnect reconnects with backoff and resumes at
           (last local file, its size); the remote side starts reading there.
  synth    tests and the hop measurement only: appends SYNTHETIC rows through tools.arena_declog.DecisionLog and logs each append's completion time.
  measure  fast-0, the hop measurement (P46 DESIGN section 4): synth on research-0 over ssh + pull + an ssh echo channel for the clock offset.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

SCHEMA = "decision_forwarder_v1"
SEALED_ROOT_PREFIX = "/data/mal/arena"
BOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
GENESIS = "0" * 64
POLL_MS_DEFAULT = 10
HB_MS_DEFAULT = 500
STALL_MS_DEFAULT = 2_000
BACKOFF_MS = (500, 1_000, 2_000, 5_000)
CONNECT_TIMEOUT_MS = 15_000
RECONNECT_SILENCE_MS_DEFAULT = 10_000  # a connected feed silent this long (serve heartbeats every 500 ms) is half-open: kill and reconnect
MAX_PASS_BYTES = 1 << 20  # serve reads at most this much per pass (bounded catch-up); one line may not exceed it
MAX_FRAME_BYTES = 8 << 20  # the puller's receive buffer cap without a newline (a 1 MiB line JSON-escaped stays well under it)
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3")


def ssh_argv(host: str) -> list[str]:
    """The ssh prefix every pull uses: no prompts, and ssh itself drops a dead tunnel after ~15 s (5 s x 3 keepalives)."""
    return ["ssh", *SSH_OPTS, host]

EXIT_OK, EXIT_REFUSED, EXIT_MISMATCH = 0, 3, 2


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _under_sealed(path: str) -> bool:
    real = os.path.realpath(path)
    return real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/")


def canon(obj: Any) -> str:
    """Byte-identical to tools.arena_paper.canon (the test proves it); copied so the forwarder stays pure stdlib (arena_paper pulls numpy)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_hex(data: str) -> str:
    import hashlib
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def bot_id_ok(bot_id: str) -> bool:
    return bool(BOT_ID_RE.match(bot_id or "")) and ".." not in bot_id


# --- serve (research-0) -----------------------------------------------------------------------------------------------------------


def _alias_of_sealed(path: str) -> bool:
    """An ancestor of realpath(path) with the same (st_dev, st_ino) as the sealed prefix (a bind mount or alias realpath cannot see). Stat only.
    A host without the prefix has nothing to alias."""
    try:
        sp = os.stat(SEALED_ROOT_PREFIX)
    except OSError:
        return False
    cur = os.path.realpath(path)
    while True:
        try:
            st = os.stat(cur)
            if (st.st_dev, st.st_ino) == (sp.st_dev, sp.st_ino):
                return True
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def sealed_root(root: str, bot_id: str) -> bool:
    d = os.path.join(root, "decisions", bot_id)
    return _under_sealed(root) or _under_sealed(d) or _alias_of_sealed(root) or _alias_of_sealed(d)


def _read_gate() -> Callable[[str, str], str | None] | None:
    """The bridge's recorded-read gate (tools.hv4_live_c0.read_record_problem, merged in #965), or None when it cannot be imported (fail closed)."""
    try:
        from tools import hv4_live_c0 as c0
    except ImportError:
        return None
    f = getattr(c0, "read_record_problem", None)
    return None if f is None else (lambda reads_dir, bot_id: f(reads_dir, bot_id)[0])


def serve_refusal(root: str, bot_id: str, reads_dir: str | None = None, gate: Callable[[str, str], str | None] | None = None) -> str | None:
    """None, or why serve must not open anything. Decided from paths and the read record only; no decision file is listed or opened here."""
    if not bot_id_ok(bot_id):
        return "bad_bot_id"
    if not sealed_root(root, bot_id):
        return None
    if not reads_dir:
        return "sealed_root_needs_read_record"
    gate = gate or _read_gate()
    if gate is None:
        return "read_record_gate_unavailable"
    why = gate(reads_dir, bot_id)
    return f"gate_{why}" if why else None


def parse_resume(text: str | None) -> tuple[str, int]:
    """The forced-command form. SSH_ORIGINAL_COMMAND is one of: empty or exactly `resume` (start at the beginning; what the puller sends from an
    empty local dir), or exactly `resume <YYYY-MM-DD.jsonl> <offset>`. Anything else raises ValueError (the serve exits refused)."""
    if not text or not text.strip():
        return "", 0
    parts = text.split()
    if parts == ["resume"]:
        return "", 0
    if len(parts) != 3 or parts[0] != "resume" or not FILE_RE.match(parts[1]) or not parts[2].isdigit():
        raise ValueError("bad_resume")
    return parts[1], int(parts[2])


def resume_string(file: str, offset: int) -> str:
    """What the puller sends as the remote command in the forced-command form (the inverse of parse_resume)."""
    return f"resume {file} {int(offset)}" if file else "resume"


class Server:
    """Tails the bot's day files from (start_file, start_offset) and emits complete lines as frames. Files sorted by name; files before
    start_file are skipped; start_offset applies to start_file only."""

    def __init__(self, root: str, bot_id: str, start_file: str = "", start_offset: int = 0, write: Callable[[bytes], None] | None = None) -> None:
        self.dir = Path(root) / "decisions" / bot_id
        self.bot_id = bot_id
        self.start_file = start_file
        self.offsets: dict[str, int] = {start_file: start_offset} if start_file else {}
        self.last = (start_file, start_offset)
        self._write = write or self._stdout

    @staticmethod
    def _stdout(b: bytes) -> None:
        sys.stdout.buffer.write(b)
        sys.stdout.buffer.flush()

    def frame(self, obj: dict) -> bytes:
        return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")

    def hello(self) -> None:
        self._write(self.frame({"k": "hello", "v": SCHEMA, "bot": self.bot_id, "ms": now_ms(), "f": self.last[0], "o": self.last[1]}))

    def hb(self) -> None:
        self._write(self.frame({"k": "hb", "ms": now_ms(), "f": self.last[0], "o": self.last[1]}))

    def _bad(self, out: list[bytes], name: str, pos: int, why: str) -> None:
        out.append(self.frame({"k": "bad", "f": name, "o": pos, "why": why}))
        self._flush(out)
        raise SystemExit(EXIT_MISMATCH)

    def poll(self, max_bytes: int = MAX_PASS_BYTES) -> int:
        """One pass, reading at most max_bytes in total (a long backlog is caught up over several passes). Returns the number of row frames
        written. The log is append-only: a file that shrinks below what was already sent, or a single line longer than max_bytes, is a loud
        `bad` frame and exit (the puller stops on it)."""
        try:
            names = sorted(n for n in os.listdir(self.dir) if FILE_RE.match(n))
        except FileNotFoundError:
            return 0
        out: list[bytes] = []
        budget = max_bytes
        for name in names:
            if self.start_file and name < self.start_file:
                continue
            off = self.offsets.get(name, 0)
            p = self.dir / name
            try:
                ls = os.lstat(p)
            except FileNotFoundError:
                continue
            import stat as _stat
            if _stat.S_ISLNK(ls.st_mode) or not _stat.S_ISREG(ls.st_mode):
                self._bad(out, name, off, "source_not_regular")
            if ls.st_nlink != 1:
                self._bad(out, name, off, "source_hardlink")
            size = ls.st_size
            if size < off:
                self._bad(out, name, off, "source_shrank")
            if size == off:
                continue
            if budget <= 0:
                break
            want = min(size - off, budget)
            fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                fs = os.fstat(fh.fileno())
                if (fs.st_dev, fs.st_ino) != (ls.st_dev, ls.st_ino) or fs.st_nlink != 1:
                    self._bad(out, name, off, "source_swapped")
                fh.seek(off)
                chunk = fh.read(want)
            end = chunk.rfind(b"\n")
            if end < 0:
                if want >= max_bytes:
                    self._bad(out, name, off, "line_too_long")
                break  # writer mid-append (or the budget ends mid-line): later files wait, so rows stay in order
            budget -= end + 1
            pos = off
            for line in chunk[: end + 1].splitlines(keepends=True):
                body = line[:-1]
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError:
                    self._bad(out, name, pos, "not_utf8")
                out.append(self.frame({"k": "row", "f": name, "o": pos, "b": text}))
                pos += len(line)
            self.offsets[name] = pos
            self.last = (name, pos)
            if pos < size:
                break  # this file still has bytes (partial line or budget): do not start a later file yet
        self._flush(out)
        return len(out)

    def _flush(self, out: list[bytes]) -> None:
        if out:
            self._write(b"".join(out))


def run_serve(args: argparse.Namespace) -> int:
    start_file, start_offset = args.start_file or "", int(args.start_offset or 0)
    if args.from_env:
        try:
            start_file, start_offset = parse_resume(os.environ.get("SSH_ORIGINAL_COMMAND"))
        except ValueError as exc:
            print(json.dumps({"k": "refused", "why": str(exc)}), flush=True)
            return EXIT_REFUSED
    why = serve_refusal(args.root, args.bot_id, args.reads_dir)
    if why:
        print(json.dumps({"k": "refused", "why": why}), flush=True)
        return EXIT_REFUSED
    srv = Server(args.root, args.bot_id, start_file, start_offset)
    end = time.monotonic() + args.max_s if args.max_s else None
    try:
        srv.hello()
        last_hb = time.monotonic()
        while end is None or time.monotonic() < end:
            if srv.poll() == 0 and (time.monotonic() - last_hb) * 1000 >= args.hb_ms:
                srv.hb()
                last_hb = time.monotonic()
            time.sleep(args.poll_ms / 1000.0)
    except BrokenPipeError:
        return EXIT_OK
    return EXIT_OK


# --- pull (fast-0) ----------------------------------------------------------------------------------------------------------------


class Mismatch(Exception):
    pass


class Locked(RuntimeError):
    """Another puller holds transport/<bot_id>.lock for this destination."""


class Puller:
    """Receives frames, verifies, appends. `cmd` is the serve command as an argv list. At every (re)connect the tokens are replaced with the
    resume position: {resume} -> `resume` (empty local dir) or `resume <file> <offset>` (the forced-command form, see parse_resume); {file} and
    {offset} -> the flag form (`--start-file '' --start-offset 0` from an empty dir)."""

    def __init__(self, dst: str, bot_id: str, cmd: Sequence[str], stall_ms: int = STALL_MS_DEFAULT, latency_log: str | None = None,
                 clock: Callable[[], int] = time.time_ns, connect_timeout_ms: int = CONNECT_TIMEOUT_MS,
                 reconnect_silence_ms: int = RECONNECT_SILENCE_MS_DEFAULT) -> None:
        if not bot_id_ok(bot_id):
            raise ValueError("bad_bot_id")
        if _under_sealed(dst):
            raise ValueError("dst_under_sealed_arena")  # the bridge's guard refuses any read there anyway
        self._canon, self._sha = canon, sha256_hex
        self.connect_timeout_ms = int(connect_timeout_ms)
        self.reconnect_silence_ms = max(int(reconnect_silence_ms), int(stall_ms))
        self.bot_id = bot_id
        self.cmd = list(cmd)
        self.stall_ms = int(stall_ms)
        self.clock = clock
        self.dir = Path(dst) / "decisions" / bot_id
        self.tdir = Path(dst) / "transport"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tdir.mkdir(parents=True, exist_ok=True)
        self._lock_fd: int | None = os.open(str(self.tdir / f"{bot_id}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        import fcntl
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # single writer for the puller's whole life
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise Locked(f"dst_locked:{self.tdir / (bot_id + '.lock')}") from None
        self.status_path = self.tdir / f"{bot_id}.status.json"
        self.gaps_path = self.tdir / f"{bot_id}.gaps.jsonl"
        self.lat_fh = open(latency_log, "a", encoding="utf-8") if latency_log else None
        self.prev, self.seq = GENESIS, -1
        self.file, self.offset = "", 0
        self.state = "init"
        self.last_frame_ms = 0
        self.last_remote_ms: int | None = None
        self.rows = 0
        self.gaps = 0
        self.proc: subprocess.Popen | None = None
        self.connect_ms: list[int] = []
        self._resume_local()

    def close(self) -> None:
        """Releases the single-writer lock (run() does this when it ends)."""
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        if self.lat_fh:
            self.lat_fh.close()
            self.lat_fh = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -- local state
    def _ms(self) -> int:
        return self.clock() // 1_000_000

    def gap(self, kind: str, **kw: Any) -> None:
        self.gaps += 1
        row = {"kind": kind, "ms": self._ms(), "bot": self.bot_id, "seq": self.seq, "f": self.file, "o": self.offset, **kw}
        with open(self.gaps_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        print(f"decision_forwarder GAP {json.dumps(row, sort_keys=True)}", file=sys.stderr, flush=True)

    def write_status(self) -> None:
        st = {"schema": SCHEMA, "bot": self.bot_id, "state": self.state, "ms": self._ms(), "last_frame_ms": self.last_frame_ms,
              "last_remote_ms": self.last_remote_ms, "seq": self.seq, "f": self.file, "o": self.offset, "rows": self.rows, "gaps": self.gaps,
              "stall_ms": self.stall_ms, "pid": os.getpid()}
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, sort_keys=True) + "\n")
        os.replace(tmp, self.status_path)

    def _resume_local(self) -> None:
        """Verify what is already here (same rule as the bridge). A torn last line (our own crash mid-write) is trimmed: the source still has
        the row and resends it. A chain break here is a hard stop."""
        names = sorted(n for n in os.listdir(self.dir) if FILE_RE.match(n))
        for name in names:
            p = self.dir / name
            data = p.read_bytes()
            if data and not data.endswith(b"\n"):
                keep = data.rfind(b"\n") + 1
                with open(p, "r+b") as fh:
                    fh.truncate(keep)
                    os.fsync(fh.fileno())
                self.gap("local_torn_trimmed", file=name, dropped=len(data) - keep)
                data = data[:keep]
            for line in data.splitlines():
                if line.strip():
                    _, self.prev, self.seq = self._check(line)
            self.file, self.offset = name, len(data)

    def _check(self, line: bytes) -> tuple[dict, str, int]:
        """Checks one row against the current head and returns (row, new prev, new seq). It does NOT move the head: the caller commits the
        new head only after the row is on disk (fsynced), so memory never runs ahead of the file."""
        try:
            row = json.loads(line)
            sha = row.pop("sha")
        except (ValueError, KeyError, AttributeError, TypeError):
            raise Mismatch("unreadable_row") from None
        if row.get("prev") != self.prev or row.get("seq") != self.seq + 1 or self._sha(self.prev + self._canon(row)) != sha:
            raise Mismatch(f"chain_broken_at_seq_{self.seq + 1}")
        row["sha"] = sha
        return row, sha, row["seq"]

    def handle(self, frame: dict, recv_ns: int) -> None:
        if self._lock_fd is None:
            raise Mismatch("not_locked")
        k = frame.get("k")
        self.last_frame_ms = recv_ns // 1_000_000
        if k == "hb" or k == "hello":
            self.last_remote_ms = frame.get("ms")
            f, o = frame.get("f"), frame.get("o")
            if f is not None and o is not None and (f, o) != (self.file, self.offset):
                raise Mismatch(f"{k}_position_mismatch_{f}_{o}_local_{self.file}_{self.offset}")  # frames are ordered: the remote must be where we are
            return
        if k == "refused" or k == "bad":
            raise Mismatch(f"remote_{k}_{frame.get('why')}")
        if k != "row":
            raise Mismatch("unknown_frame")
        name, off, text = frame.get("f"), frame.get("o"), frame.get("b")
        if not isinstance(name, str) or not FILE_RE.match(name) or not isinstance(off, int) or not isinstance(text, str):
            raise Mismatch("bad_row_frame")
        if name < self.file:
            raise Mismatch("file_went_backwards")
        p = self.dir / name
        size = p.stat().st_size if p.exists() else 0
        if off != size:
            raise Mismatch(f"offset_mismatch_{name}_{off}_local_{size}")
        line = text.encode("utf-8")
        row, new_prev, new_seq = self._check(line) if line.strip() else (None, self.prev, self.seq)
        created = not p.exists()
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:  # a new day file: make its directory entry durable too
            dfd = os.open(str(self.dir), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        vis_ns = self.clock()
        self.prev, self.seq = new_prev, new_seq  # committed only after the fsync
        self.file, self.offset = name, off + len(line) + 1
        if row is not None:
            self.rows += 1
            if self.lat_fh:
                self.lat_fh.write(json.dumps({"seq": row["seq"], "row_ms": row.get("ms"), "recv_ns": recv_ns, "vis_ns": vis_ns}) + "\n")
                self.lat_fh.flush()

    # -- the connection loop
    def _argv(self) -> list[str]:
        rs = resume_string(self.file, self.offset)
        return [a.replace("{resume}", rs).replace("{file}", self.file or "").replace("{offset}", str(self.offset)) for a in self.cmd]

    def run(self, max_s: float | None = None, stop: threading.Event | None = None, until: Callable[["Puller"], bool] | None = None) -> int:
        """The connection loop. Any unexpected exception writes state `stopped_error` before it propagates, so the status file never stays
        `connected` after the puller is gone (a SIGTERM sets `stop` in run_pull and ends as `stopped`)."""
        try:
            return self._run(max_s, stop, until)
        except BaseException as exc:
            if self.state not in ("stopped", "stopped_mismatch"):
                self.state = "stopped_error"
                try:
                    self.gap("error", why=type(exc).__name__ + ":" + str(exc)[:160])
                    self.write_status()
                except OSError:
                    pass
            self._kill()
            raise
        finally:
            self.close()

    def _run(self, max_s: float | None, stop: threading.Event | None, until: Callable[["Puller"], bool] | None) -> int:
        deadline = time.monotonic() + max_s if max_s else None
        attempt = 0
        stop = stop or threading.Event()
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            t0 = time.monotonic()
            self.proc = subprocess.Popen(self._argv(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, start_new_session=True)
            self.state = "connecting"
            self.write_status()
            if attempt:
                self.gap("reconnect", attempt=attempt)
            try:
                rc = self._pump(deadline, stop, until, t0)
            except Mismatch as exc:
                self.state = "stopped_mismatch"
                self.gap("mismatch", why=str(exc)[:200])
                self.write_status()
                self._kill()
                return EXIT_MISMATCH
            self._kill()
            if rc == "done":
                self.state = "stopped"
                self.write_status()
                return EXIT_OK
            self.state = "disconnected"
            self.gap("disconnect", why=rc, rc=self.proc.returncode if self.proc else None)
            self.write_status()
            time.sleep(BACKOFF_MS[min(attempt, len(BACKOFF_MS) - 1)] / 1000.0)
            attempt += 1
        self.state = "stopped"
        self.write_status()
        return EXIT_OK

    def _pump(self, deadline: float | None, stop: threading.Event, until: Callable[["Puller"], bool] | None, t0: float) -> str:
        assert self.proc and self.proc.stdout
        fd = self.proc.stdout.fileno()
        buf = b""
        stalled_since: int | None = None
        connected = False
        last_status = 0.0
        self.last_frame_ms = self._ms()
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                data = os.read(fd, 1 << 16)
                if not data:
                    return "eof"
                buf += data
                if b"\n" not in buf and len(buf) > MAX_FRAME_BYTES:
                    raise Mismatch("frame_too_long")
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    recv_ns = self.clock()
                    try:
                        frame = json.loads(raw)
                    except ValueError:
                        raise Mismatch("unreadable_frame") from None
                    if not connected:
                        connected = True
                        self.connect_ms.append(int((time.monotonic() - t0) * 1000))
                        self.state = "connected"
                    self.handle(frame, recv_ns)
                    if stalled_since is not None:
                        self.gap("resume", gap_ms=self._ms() - stalled_since)
                        stalled_since = None
                        self.state = "connected"
                if until and until(self):
                    return "done"
            if not connected and (time.monotonic() - t0) * 1000 > self.connect_timeout_ms:
                return "connect_timeout"
            if connected and self._ms() - self.last_frame_ms > self.reconnect_silence_ms:
                return "silent_reconnect"  # half-open: the ssh may never see EOF; kill it and reconnect (stall row already written)
            if connected and stalled_since is None and self._ms() - self.last_frame_ms > self.stall_ms:
                stalled_since = self.last_frame_ms
                self.state = "stalled"
                self.gap("stall", last_frame_ms=self.last_frame_ms)
                self.write_status()
            if time.monotonic() - last_status >= 0.25:
                self.write_status()
                last_status = time.monotonic()
        return "done"

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
        if p and p.stdout:
            p.stdout.close()


def run_pull(args: argparse.Namespace) -> int:
    if bool(args.cmd_json) == bool(args.ssh_host):
        print("pull: give exactly one of --ssh-host (production: ssh with SSH_OPTS, remote command {resume}) or --cmd-json (tests)", file=sys.stderr)
        return EXIT_REFUSED
    cmd = json.loads(args.cmd_json) if args.cmd_json else ssh_argv(args.ssh_host) + ["{resume}"]
    try:
        pl = Puller(args.dst, args.bot_id, cmd, stall_ms=args.stall_ms, latency_log=args.latency_log, reconnect_silence_ms=args.reconnect_silence_ms)
    except Locked as exc:
        print(f"pull refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    return pl.run(max_s=args.max_s, stop=stop)


# --- synth (tests / measurement only) -------------------------------------------------------------------------------------------


def run_synth(args: argparse.Namespace) -> int:
    """Appends SYNTHETIC rows only (body {"synthetic": true, ...}); a root under the sealed arena is refused."""
    from tools.arena_declog import DecisionLog
    if _under_sealed(args.root) or not bot_id_ok(args.bot_id):
        print("synth refused", file=sys.stderr)
        return EXIT_REFUSED
    Path(args.root).mkdir(parents=True, exist_ok=True)
    (Path(args.root) / "SYNTHETIC_FIXTURE_ROOT").write_text("p46 transport synthetic\n")
    dlog = DecisionLog(args.root)
    rng = random.Random(args.seed)
    plan: list[float] = []  # inter-arrival seconds
    for _ in range(args.n_fast):
        plan.append(rng.expovariate(1000.0 / args.mean_fast_ms))
    for b in range(args.bursts):  # an exit wave: burst_size rows back to back, placed inside the fast phase
        at = rng.randrange(len(plan)) if plan else 0
        plan[at:at] = [0.0] * args.burst_size
    for _ in range(args.n_slow):
        plan.append(rng.expovariate(1000.0 / args.mean_slow_ms))
    out = open(args.append_log, "w", encoding="utf-8")
    time.sleep(args.lead_s)
    for i, gap_s in enumerate(plan):
        if gap_s:
            time.sleep(gap_s)
        ms = now_ms()
        row = dlog.append(args.bot_id, ms, {"kind": "entry" if i % 2 == 0 else "exit", "synthetic": True, "mint": f"SYNTH{i:06d}", "i": i})
        out.write(json.dumps({"seq": row["seq"], "row_ms": ms, "done_ns": time.time_ns()}) + "\n")
        out.flush()
    out.close()
    print(json.dumps({"synth_rows": len(plan)}), flush=True)
    return EXIT_OK


# --- measure (fast-0) -----------------------------------------------------------------------------------------------------------


def pct(xs: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 100])."""
    s = sorted(xs)
    if not s:
        return float("nan")
    k = max(1, -(-len(s) * q // 100))
    return s[int(min(len(s), k)) - 1]


def clock_offset_ns(samples: Sequence[tuple[int, int, int]]) -> tuple[int, int, int]:
    """(offset remote-local, error bound, rtt_min) from echo samples (t1 local send, tr remote, t2 local recv): NTP style, the median offset of the
    10% lowest-RTT samples; the bound is half the largest RTT among them."""
    best = sorted(samples, key=lambda s: s[2] - s[0])[: max(1, len(samples) // 10)]
    offs = sorted(tr - (t1 + t2) // 2 for t1, tr, t2 in best)
    return offs[len(offs) // 2], max(t2 - t1 for t1, _, t2 in best) // 2, min(t2 - t1 for t1, _, t2 in samples)


ECHO_CODE = "import sys,time\nfor l in sys.stdin:\n    sys.stdout.write(str(time.time_ns())+'\\n'); sys.stdout.flush()\n"


def summarize(lat_rows: list[dict], appends: dict[int, dict], offset_ns: int) -> dict:
    vis_append, vis_row, recv_append = [], [], []
    for r in lat_rows:
        a = appends.get(r["seq"])
        if not a:
            continue
        remote_done_local = a["done_ns"] - offset_ns
        vis_append.append((r["vis_ns"] - remote_done_local) / 1e6)
        recv_append.append((r["recv_ns"] - remote_done_local) / 1e6)
        vis_row.append((r["vis_ns"] - (a["row_ms"] * 1_000_000 - offset_ns)) / 1e6)

    def stats(xs: list[float]) -> dict:
        return {"n": len(xs), "p50": pct(xs, 50), "p90": pct(xs, 90), "p99": pct(xs, 99), "max": max(xs) if xs else None,
                "min": min(xs) if xs else None}

    return {"append_done_to_visible_ms": stats(vis_append), "row_ms_to_visible_ms": stats(vis_row), "append_done_to_recv_ms": stats(recv_append)}


def rq(path: str) -> str:
    """shell-quote a remote path, keeping a leading `~/` unquoted so the remote shell still expands it."""
    return "~/" + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)


def run_measure(args: argparse.Namespace) -> int:
    """A trusted-operator tool (paths come from the job script), but every remote string is shell-quoted anyway. The serve runs in the
    production forced-command form (serve --from-env with SSH_ORIGINAL_COMMAND = {resume}). Remote and local scratch dirs are removed in a
    finally block, whatever fails."""
    ssh = json.loads(args.ssh_prefix_json) if args.ssh_prefix_json else ssh_argv(args.ssh_host)
    rdir = rq(args.remote_dir.rstrip("/"))
    rroot = rq(args.remote_dir.rstrip("/") + "/root")
    rpy = rq(args.remote_dir.rstrip("/") + "/decision_forwarder.py")
    rapp = rq(args.remote_dir.rstrip("/") + "/appends.jsonl")
    bot = "p46-synthetic"
    ldir = Path(args.local_dir)
    ldir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).read_bytes()
    procs: list[subprocess.Popen] = []
    stop = threading.Event()
    try:
        t = time.monotonic()
        subprocess.run(ssh + [f"mkdir -p {rdir} && cat > {rpy}"], input=src, check=True)
        cold_ms = int((time.monotonic() - t) * 1000)
        serve_cmd = ssh + [f"SSH_ORIGINAL_COMMAND='{{resume}}' python3 -u {rpy} serve --from-env --root {rroot} --bot-id {shlex.quote(bot)}"]
        echo = subprocess.Popen(ssh + ["python3 -u -c " + shlex.quote(ECHO_CODE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        procs.append(echo)
        samples: list[tuple[int, int, int]] = []

        def echo_loop() -> None:
            assert echo.stdin and echo.stdout
            try:
                while not stop.is_set():
                    t1 = time.time_ns()
                    echo.stdin.write(b"x\n")
                    echo.stdin.flush()
                    line = echo.stdout.readline()
                    if not line:
                        return
                    samples.append((t1, int(line), time.time_ns()))
                    stop.wait(args.echo_every_s)
            except (OSError, ValueError):
                return

        th = threading.Thread(target=echo_loop, daemon=True)
        th.start()
        n_total = args.n_fast + args.n_slow + args.bursts * args.burst_size
        synth = subprocess.Popen(ssh + [
            f"cd {rq(args.remote_pythonpath)} && PYTHONPATH=. python3 -u {rpy} synth --root {rroot} --bot-id {shlex.quote(bot)} "
            f"--append-log {rapp} --n-fast {int(args.n_fast)} --mean-fast-ms {float(args.mean_fast_ms)} --n-slow {int(args.n_slow)} "
            f"--mean-slow-ms {float(args.mean_slow_ms)} --bursts {int(args.bursts)} --burst-size {int(args.burst_size)} --seed {int(args.seed)} --lead-s 3"],
            stdin=subprocess.DEVNULL)
        procs.append(synth)
        lat_path = ldir / "latency.jsonl"
        pl = Puller(str(ldir / "dst"), bot, serve_cmd, stall_ms=args.stall_ms, latency_log=str(lat_path))
        rc = pl.run(max_s=args.max_s, until=lambda p: p.seq + 1 >= n_total)
        synth.wait(timeout=60)
        stop.set()
        if echo.stdin:
            echo.stdin.close()
        echo.wait(timeout=10)
        appends_raw = subprocess.run(ssh + [f"cat {rapp}; md5sum {rroot}/decisions/{shlex.quote(bot)}/*.jsonl"], capture_output=True, check=True).stdout
        appends, remote_md5 = {}, {}
        for line in appends_raw.decode().splitlines():
            if line.startswith("{"):
                a = json.loads(line)
                appends[a["seq"]] = a
            elif line.strip():
                h, path = line.split()
                remote_md5[os.path.basename(path)] = h
        import hashlib
        local_md5 = {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in sorted((ldir / "dst" / "decisions" / bot).glob("*.jsonl"))}
        lat_rows = [json.loads(x) for x in lat_path.read_text().splitlines() if x.strip()]
        off, err, rtt_min = clock_offset_ns(samples)
        rtts = [(t2 - t1) / 1e6 for t1, _, t2 in samples]
        gaps = [json.loads(x) for x in pl.gaps_path.read_text().splitlines()] if pl.gaps_path.exists() else []
        res = {"schema": SCHEMA + "_measure", "ssh_host": args.ssh_host, "rows_expected": n_total, "rows_forwarded": pl.rows, "puller_rc": rc,
               "bytes_equal": local_md5 == remote_md5 and bool(local_md5), "md5_local": local_md5, "md5_remote": remote_md5,
               "clock_offset_ms": off / 1e6, "clock_offset_err_ms": err / 1e6, "echo_rtt_ms": {"n": len(rtts), "min": rtt_min / 1e6, "p50": pct(rtts, 50),
               "p90": pct(rtts, 90), "p99": pct(rtts, 99), "max": max(rtts) if rtts else None},
               "ssh_cold_connect_ms": cold_ms, "serve_connect_ms": pl.connect_ms, "gaps": gaps,
               "params": {k: getattr(args, k) for k in ("n_fast", "mean_fast_ms", "n_slow", "mean_slow_ms", "bursts", "burst_size", "seed", "stall_ms")},
               **summarize(lat_rows, appends, off)}
        Path(args.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        print(json.dumps(res, sort_keys=True), flush=True)
        return EXIT_OK if rc == EXIT_OK and res["bytes_equal"] else 1
    finally:
        stop.set()
        for pr in procs:
            if pr.poll() is None:
                pr.kill()
                try:
                    pr.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if not args.keep:
            subprocess.run(ssh + [f"rm -rf {rdir}"], check=False)
            import shutil
            shutil.rmtree(ldir / "dst", ignore_errors=True)


# --- CLI --------------------------------------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--root", required=True)
    s.add_argument("--bot-id", required=True)
    s.add_argument("--start-file", default="")
    s.add_argument("--start-offset", type=int, default=0)
    s.add_argument("--from-env", action="store_true", help="take the resume position from SSH_ORIGINAL_COMMAND (forced-command use)")
    s.add_argument("--reads-dir", default=None, help="required for a root under /data/mal/arena: the recorded-read dir the bridge's gate checks "
                   "(hv4_live_c0.READS_DIR_CANON); serve opens nothing until <reads-dir>/<bot_id>.json is a recorded PASS for this bot")
    s.add_argument("--poll-ms", type=int, default=POLL_MS_DEFAULT)
    s.add_argument("--hb-ms", type=int, default=HB_MS_DEFAULT)
    s.add_argument("--max-s", type=float, default=None)
    p = sub.add_parser("pull")
    p.add_argument("--dst", required=True)
    p.add_argument("--bot-id", required=True)
    p.add_argument("--ssh-host", default=None, help="production: ssh <SSH_OPTS> HOST {resume} (the forced command on HOST runs serve --from-env)")
    p.add_argument("--cmd-json", default=None, help='tests: serve argv as JSON with {resume} or {file}/{offset} tokens')
    p.add_argument("--reconnect-silence-ms", type=int, default=RECONNECT_SILENCE_MS_DEFAULT)
    p.add_argument("--stall-ms", type=int, default=STALL_MS_DEFAULT)
    p.add_argument("--latency-log", default=None)
    p.add_argument("--max-s", type=float, default=None)
    y = sub.add_parser("synth")
    y.add_argument("--root", required=True)
    y.add_argument("--bot-id", required=True)
    y.add_argument("--append-log", required=True)
    y.add_argument("--n-fast", type=int, default=400)
    y.add_argument("--mean-fast-ms", type=float, default=500)
    y.add_argument("--n-slow", type=int, default=120)
    y.add_argument("--mean-slow-ms", type=float, default=3000)
    y.add_argument("--bursts", type=int, default=5)
    y.add_argument("--burst-size", type=int, default=10)
    y.add_argument("--seed", type=int, default=1)
    y.add_argument("--lead-s", type=float, default=0.0)
    m = sub.add_parser("measure")
    m.add_argument("--ssh-host", default="mal-research-0")
    m.add_argument("--remote-dir", required=True)
    m.add_argument("--remote-pythonpath", required=True, help="a MAL checkout on the remote host (synth imports tools.arena_declog from it)")
    m.add_argument("--local-dir", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--n-fast", type=int, default=400)
    m.add_argument("--mean-fast-ms", type=float, default=500)
    m.add_argument("--n-slow", type=int, default=120)
    m.add_argument("--mean-slow-ms", type=float, default=3000)
    m.add_argument("--bursts", type=int, default=5)
    m.add_argument("--burst-size", type=int, default=10)
    m.add_argument("--seed", type=int, default=1)
    m.add_argument("--stall-ms", type=int, default=STALL_MS_DEFAULT)
    m.add_argument("--echo-every-s", type=float, default=1.0)
    m.add_argument("--max-s", type=float, default=1800)
    m.add_argument("--keep", action="store_true")
    m.add_argument("--ssh-prefix-json", default=None, help='tests: e.g. ["sh","-c"] runs the "remote" side locally')
    args = ap.parse_args(argv)
    return {"serve": run_serve, "pull": run_pull, "synth": run_synth, "measure": run_measure}[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
