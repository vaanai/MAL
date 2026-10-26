"""decision_forwarder: ships ONE arena bot's hash-chained decision log from mal-research-0 (where the runner writes it) to mal-fast-0 (where the
live bridge runs), byte for byte. P46 transport, gap 2 of docs/research/p43-golive/READINESS.md (item N7). Design: docs/research/p46-transport/DESIGN.md.

No key, no order, no RPC. The only network is the ssh pipe the caller configures. This is a build, not a claim: it is not gate evidence.

Modes
  serve    research-0 (in production an authorized_keys forced command, so fast-0's key can run nothing else). Tails
           `<root>/decisions/<bot_id>/*.jsonl` and writes one JSON frame per stdout line:
             hello  {"k":"hello","v":SCHEMA,"bot":..,"ms":..}
             row    {"k":"row","f":<day file>,"o":<byte offset of the line>,"b":<the line, without its newline>}  (complete lines only)
             hb     {"k":"hb","ms":<remote ms>,"f":<file>,"o":<bytes consumed>}  every hb_ms
           A root under /data/mal/arena is refused unless --after-read-ms is given and has passed (QP-RULING-1025 R2.1: no process reads an HV4
           decision row before that arm's read is recorded). Pure stdlib; imports nothing from the repo.
  pull     fast-0. Runs the serve command (ssh), checks every row with the DecisionLog chain rule (prev, seq + 1, sha256(prev + canon(row))) and
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


def serve_refusal(root: str, bot_id: str, after_read_ms: int | None, now: int) -> str | None:
    if not bot_id_ok(bot_id):
        return "bad_bot_id"
    sealed = _under_sealed(root) or _under_sealed(os.path.join(root, "decisions", bot_id))
    if sealed and after_read_ms is None:
        return "sealed_root_needs_read_instant"
    if sealed and now < int(after_read_ms):
        return "before_read_instant"
    return None


def parse_resume(text: str | None) -> tuple[str, int]:
    """The forced-command form: SSH_ORIGINAL_COMMAND is either empty (start at the beginning) or exactly `resume <YYYY-MM-DD.jsonl> <offset>`.
    Anything else raises ValueError (the serve exits refused)."""
    if not text or not text.strip():
        return "", 0
    parts = text.split()
    if len(parts) != 3 or parts[0] != "resume" or not FILE_RE.match(parts[1]) or not parts[2].isdigit():
        raise ValueError("bad_resume")
    return parts[1], int(parts[2])


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

    def poll(self) -> int:
        """One pass. Returns the number of row frames written."""
        try:
            names = sorted(n for n in os.listdir(self.dir) if FILE_RE.match(n))
        except FileNotFoundError:
            return 0
        out: list[bytes] = []
        for name in names:
            if self.start_file and name < self.start_file:
                continue
            off = self.offsets.get(name, 0)
            p = self.dir / name
            try:
                size = os.stat(p).st_size
            except FileNotFoundError:
                continue
            if size <= off:
                continue
            with open(p, "rb") as fh:
                fh.seek(off)
                chunk = fh.read(size - off)
            end = chunk.rfind(b"\n")
            if end < 0:
                continue  # writer mid-append
            pos = off
            for line in chunk[: end + 1].splitlines(keepends=True):
                body = line[:-1]
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError:
                    out.append(self.frame({"k": "bad", "f": name, "o": pos, "why": "not_utf8"}))
                    self._flush(out)
                    raise SystemExit(EXIT_MISMATCH)
                out.append(self.frame({"k": "row", "f": name, "o": pos, "b": text}))
                pos += len(line)
            self.offsets[name] = pos
            self.last = (name, pos)
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
    why = serve_refusal(args.root, args.bot_id, args.after_read_ms, now_ms())
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


class Puller:
    """Receives frames, verifies, appends. `cmd` is the serve command as an argv list; the tokens {file} and {offset} are replaced with the
    resume position at every (re)connect."""

    def __init__(self, dst: str, bot_id: str, cmd: Sequence[str], stall_ms: int = STALL_MS_DEFAULT, latency_log: str | None = None,
                 clock: Callable[[], int] = time.time_ns, connect_timeout_ms: int = CONNECT_TIMEOUT_MS) -> None:
        if not bot_id_ok(bot_id):
            raise ValueError("bad_bot_id")
        if _under_sealed(dst):
            raise ValueError("dst_under_sealed_arena")  # the bridge's guard refuses any read there anyway
        self._canon, self._sha = canon, sha256_hex
        self.connect_timeout_ms = int(connect_timeout_ms)
        self.bot_id = bot_id
        self.cmd = list(cmd)
        self.stall_ms = int(stall_ms)
        self.clock = clock
        self.dir = Path(dst) / "decisions" / bot_id
        self.tdir = Path(dst) / "transport"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tdir.mkdir(parents=True, exist_ok=True)
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
                    self._check(line)
            self.file, self.offset = name, len(data)

    def _check(self, line: bytes) -> dict:
        try:
            row = json.loads(line)
            sha = row.pop("sha")
        except (ValueError, KeyError, AttributeError):
            raise Mismatch("unreadable_row") from None
        if row.get("prev") != self.prev or row.get("seq") != self.seq + 1 or self._sha(self.prev + self._canon(row)) != sha:
            raise Mismatch(f"chain_broken_at_seq_{self.seq + 1}")
        self.prev, self.seq = sha, row["seq"]
        row["sha"] = sha
        return row

    def handle(self, frame: dict, recv_ns: int) -> None:
        k = frame.get("k")
        self.last_frame_ms = recv_ns // 1_000_000
        if k == "hb" or k == "hello":
            self.last_remote_ms = frame.get("ms")
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
        row = self._check(line) if line.strip() else None
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        vis_ns = self.clock()
        self.file, self.offset = name, off + len(line) + 1
        if row is not None:
            self.rows += 1
            if self.lat_fh:
                self.lat_fh.write(json.dumps({"seq": row["seq"], "row_ms": row.get("ms"), "recv_ns": recv_ns, "vis_ns": vis_ns}) + "\n")
                self.lat_fh.flush()

    # -- the connection loop
    def _argv(self) -> list[str]:
        return [a.replace("{file}", self.file or "").replace("{offset}", str(self.offset)) for a in self.cmd]

    def run(self, max_s: float | None = None, stop: threading.Event | None = None, until: Callable[["Puller"], bool] | None = None) -> int:
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
    cmd = json.loads(args.cmd_json)
    pl = Puller(args.dst, args.bot_id, cmd, stall_ms=args.stall_ms, latency_log=args.latency_log)
    return pl.run(max_s=args.max_s)


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


def run_measure(args: argparse.Namespace) -> int:
    ssh = json.loads(args.ssh_prefix_json) if args.ssh_prefix_json else ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", args.ssh_host]
    rdir = args.remote_dir.rstrip("/")
    ldir = Path(args.local_dir)
    ldir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).read_bytes()
    t = time.monotonic()
    subprocess.run(ssh + [f"mkdir -p {rdir} && cat > {rdir}/decision_forwarder.py"], input=src, check=True)
    cold_ms = int((time.monotonic() - t) * 1000)
    rroot = f"{rdir}/root"
    bot = "p46-synthetic"
    serve_cmd = ssh + [f"python3 -u {rdir}/decision_forwarder.py serve --root {rroot} --bot-id {bot} --start-file '{{file}}' --start-offset {{offset}}"]
    echo = subprocess.Popen(ssh + ["python3 -u -c " + shlex.quote(ECHO_CODE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    samples: list[tuple[int, int, int]] = []
    stop = threading.Event()

    def echo_loop() -> None:
        assert echo.stdin and echo.stdout
        while not stop.is_set():
            t1 = time.time_ns()
            echo.stdin.write(b"x\n")
            echo.stdin.flush()
            line = echo.stdout.readline()
            if not line:
                return
            samples.append((t1, int(line), time.time_ns()))
            stop.wait(args.echo_every_s)

    th = threading.Thread(target=echo_loop, daemon=True)
    th.start()
    n_total = args.n_fast + args.n_slow + args.bursts * args.burst_size
    synth = subprocess.Popen(ssh + [
        f"cd {args.remote_pythonpath} && PYTHONPATH=. python3 -u {rdir}/decision_forwarder.py synth --root {rroot} --bot-id {bot} "
        f"--append-log {rdir}/appends.jsonl --n-fast {args.n_fast} --mean-fast-ms {args.mean_fast_ms} --n-slow {args.n_slow} "
        f"--mean-slow-ms {args.mean_slow_ms} --bursts {args.bursts} --burst-size {args.burst_size} --seed {args.seed} --lead-s 3"],
        stdin=subprocess.DEVNULL)
    lat_path = ldir / "latency.jsonl"
    pl = Puller(str(ldir / "dst"), bot, serve_cmd, stall_ms=args.stall_ms, latency_log=str(lat_path))
    rc = pl.run(max_s=args.max_s, until=lambda p: p.seq + 1 >= n_total)
    synth.wait(timeout=60)
    stop.set()
    if echo.stdin:
        echo.stdin.close()
    echo.wait(timeout=10)
    appends_raw = subprocess.run(ssh + [f"cat {rdir}/appends.jsonl; md5sum {rroot}/decisions/{bot}/*.jsonl"], capture_output=True, check=True).stdout
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
    if not args.keep:
        subprocess.run(ssh + [f"rm -rf {rdir}"], check=False)
        import shutil
        shutil.rmtree(ldir / "dst", ignore_errors=True)
    Path(args.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps(res, sort_keys=True), flush=True)
    return EXIT_OK if rc == EXIT_OK and res["bytes_equal"] else 1


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
    s.add_argument("--after-read-ms", type=int, default=None, help="required for a root under /data/mal/arena: the arm's recorded read instant")
    s.add_argument("--poll-ms", type=int, default=POLL_MS_DEFAULT)
    s.add_argument("--hb-ms", type=int, default=HB_MS_DEFAULT)
    s.add_argument("--max-s", type=float, default=None)
    p = sub.add_parser("pull")
    p.add_argument("--dst", required=True)
    p.add_argument("--bot-id", required=True)
    p.add_argument("--cmd-json", required=True, help='serve argv as JSON, e.g. ["ssh","mal-research-0","resume {file} {offset}"]')
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
