#!/usr/bin/env python3
"""tape_mirror: a byte-exact, low-latency mirror of the research-0 arena tape (/data/mal/arena/tape) on mal-fast-0. PAPER ONLY: no key, no wallet,
no RPC, 0 Helius credits. Pure stdlib (plus the `zstd` CLI on the pull side).

WHY. The r4-b DRY decision host (tools/r4b_live_host.py, PR #1143) runs on mal-fast-0 and needs the public arena tape as its history source (the Helius
mark-window feed of tools/r4b_feed_merge.py, PR #1142, only covers [T - 30 s, T + 10 s]). The tape is written on mal-research-0 by the arena feed
(tools/arena_feed.py). This tool carries it across, in the PULL form of the HV4 decision-row transport (tools/decision_forwarder.py, PR #967): the
host that consumes the data dials research-0 over the existing ssh path (`ssh mal-research-0`, Cloudflare Access) and runs `serve` there; research-0
gets no credential to fast-0.

WHAT IT NEVER DOES (seals). It moves bytes. Neither side parses a tape row: the only content operations are byte counts, newline positions (so only
complete lines are sent) and sha256. Logs, events and the status hold hour names, sizes, offsets, hashes and timings only. A mirror is a COPY of the
arena tape and inherits every seal of /data/mal/arena/tape (counts and creates only under QP rulings; walk-2-hour information, DEC-030 s2 R1).

LAYOUT (identical to the source, so tools/arena_state.py and any reader of the arena tape read the mirror dir unchanged)
  <dst>/tape-<YYYY-MM-DDTHH>.jsonl       an OPEN hour: the source's bytes, appended in complete lines, same offsets.
  <dst>/tape-<YYYY-MM-DDTHH>.jsonl.zst   a CLOSED hour: the source's .zst file, byte for byte (sha256 checked end to end). It is installed by rename
                                         BEFORE the local .jsonl is removed, so a reader following the .jsonl continues in the .zst at the same
                                         decompressed offset (arena_state's rule) and never sees neither file.
  <dst>/.mirror/                         status.json (atomic, every 250 ms), events.jsonl, lag.jsonl (one row per minute), lock. No tape byte here.
  An hour is closed at the source when its .jsonl.zst exists and its .jsonl does not (tools/arena_zst.compact_file renames the verified .zst in and
  only then unlinks the .jsonl). A part transfer is <name>.part, which no tape glob matches.

PROTOCOL (one ssh session; serve -> pull frames on stdout; one hello line pull -> serve on stdin)
  pull -> serve  {"v":1,"since":"<hour>","have":{"tape-<h>.jsonl":<bytes held>},"closed":["<h>",...]}   (validated; anything else is refused)
  serve -> pull  each frame is one JSON header line; "d" and "z" headers are followed by exactly n raw bytes.
    hello  {"k":"hello","v","ms","pid"}
    d      {"k":"d","f","o","n","mt","ps","ms"} + n bytes: complete lines of an open hour from offset o. mt = the source file's mtime at the stat the
           bytes were read under (the newest byte's write instant); ps = the earlier stat instant at which the source held exactly o bytes (every byte
           of the frame was written after it; null when unknown).
    z      {"k":"z","f","o","n","size"} + n bytes: a chunk of a closed hour's .zst.      ze  {"k":"ze","f","size","sha","mt"}: end, sha256 of the file.
    hb     {"k":"hb","ms","open":{"<h>":[sent, source size, mt]},"zq","full"} every 500 ms, after that pass's data. full = no complete line of any open
           source hour is unsent.  gone  {"k":"gone","h"}: an hour the puller holds vanished at the source with no .zst.  bad  {"k":"bad","why"}.
  serve sends live data first in every pass (each open hour up to LIVE_PASS_BYTES), then at most --zst-pass-bytes of .zst backfill, then the hb.

PULL CHECKS (nothing unverified is ever placed under a tape name)
  d: the hour is not closed here, the offset equals the local size, the body ends with a newline; append (O_APPEND), fsync at most every second.
  z/ze: written to <name>.part, sha256 compared with serve's; then, off the receive path, `zstd -dc` of the part must reproduce the local live copy
  as an exact PREFIX (equal when the live copy was complete). Only then rename .part -> .zst, fsync the dir, unlink the .jsonl, fsync the dir.
  hb: every open hour's local size must equal serve's sent count (frames are ordered). A failed sha retries the hour (at most 3 times, by reconnect).
  A position mismatch, a diverged closed hour or an undecodable .zst stops the puller (exit 2, state stopped_mismatch): fail closed, for a human.
  Disconnects reconnect with backoff 0.5/1/2/5 s and resume from the local sizes; transport outages delay rows, they never lose or duplicate one.

STALENESS (fail closed). status.json carries synced_ms = serve's clock at the last hb at which the mirror held every complete line the source had,
src_mt_ms = the newest source mtime (the feed itself is alive), and ms = the puller's own heartbeat. mirror_problem(dst) returns None only when the
status is a regular, readable, current tape_mirror_v1 file in state `connected`, synced within SYNC_MAX_AGE_MS, with a source write within
SRC_MAX_AGE_MS (and, if asked, at least need_hours of contiguous history). Anything else, including a dead puller or an unreadable file, is a code.
`python3 tools/tape_mirror.py status --dst <dst>` prints it (exit 0 fresh, 1 not).

LAG. Per d frame: lag_new = local write done - mt (the newest byte), lag_old = local write done - ps (an upper bound for the oldest byte). Both cross
the two hosts' NTP clocks (about +-10 ms, PR #967 s4). Rolling 60 s p50/p90/max in the status, one row per minute in lag.jsonl.

DISK. Hours older than --retain-hours (by name) are deleted here, and nothing is written while free space is under --min-free-pct (state
disk_reserve, the host sees it stale). The first connect backfills --backfill-hours of closed hours.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/tape_mirror.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import errno
import hashlib
import json
import os
import queue
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from tools.decision_forwarder import BACKOFF_MS, ssh_argv

SCHEMA = "tape_mirror_v1"
PROTO = 1
HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}\Z")
OPEN_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\Z")
CLOSED_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst\Z")
PART_SUFFIX = ".part"
META_DIR = ".mirror"
DEFAULT_SRC = "/data/mal/arena/tape"
H_MS = 3_600_000

POLL_MS_DEFAULT = 20
HB_MS_DEFAULT = 500
LIST_MS = 1_000  # full source listing at most this often (the current UTC hour's file is also checked every pass)
LIVE_PASS_BYTES = 1 << 20  # per open hour per pass; a tape line longer than this is refused (arena rows are a few hundred bytes)
ZST_PASS_BYTES_DEFAULT = 512 << 10  # .zst backfill per pass, after the live data (about 25 MB/s at 20 ms passes)
ZST_CHUNK = 256 << 10
MAX_BODY = max(LIVE_PASS_BYTES, ZST_CHUNK)
MAX_HEADER = 64 << 10
HELLO_MAX = 256 << 10
HELLO_TIMEOUT_S = 15.0
HELLO_MAX_HAVE = 400
HELLO_MAX_CLOSED = 4_000
MAX_OFFSET = 1 << 40

STALL_MS_DEFAULT = 2_000
RECONNECT_SILENCE_MS_DEFAULT = 10_000
CONNECT_TIMEOUT_MS = 20_000
DISK_RETRY_S = 30.0
FSYNC_EVERY_S = 1.0
PRUNE_EVERY_S = 300.0
STATUS_EVERY_S = 0.25
PROGRESS_EVERY_S = 60.0
LAG_WINDOW_MS = 60_000
ZST_RETRIES = 3
REMOTE_BAD_MAX = 10  # sessions in a row that serve refused before any heartbeat: stop (a config error does not heal by reconnecting every 5 s)

RETAIN_HOURS_DEFAULT = 72  # the r4-b warm-up needs 52 h of block time (DEC-032 Am.8 Erratum 1) plus room for a restart
BACKFILL_HOURS_DEFAULT = 64
MIN_FREE_PCT_DEFAULT = 12.0

STATUS_MAX_AGE_MS = 2_000  # the puller rewrites its status every 250 ms
SYNC_MAX_AGE_MS = 3_000  # serve heartbeats every 500 ms
SRC_MAX_AGE_MS = 15_000  # the arena feed writes trade rows many times a second and an hb row per program per minute
SKEW_MS = 2_000
STATUS_READ_MAX = 64 << 10

# serve never reads from these (the seals list, ops/AGENT-BRIEF-COMMON.md s2); the arena tape itself is not on it
DENY_PREFIXES = ("/data/mal/meta-capture", "/data/mal/blocks", "/data/mal/exp012-forward", "/data/mal/kalshi", "/var/lib/mal/sealed",
                 "/data/mal/tip-tape-archive")
DENY_PARTS = ("shadow", "canary", "probe", "_shakedown-", "_smoke-", "fwdv1", "exp011-", "fresh-08", "truth-", "forward-")
CODE_RE = re.compile(r"[a-z0-9_]{1,40}")

EXIT_OK, EXIT_MISMATCH, EXIT_REFUSED = 0, 2, 3


# ---- small helpers ------------------------------------------------------------------------------------------------------------
def now_ms() -> int:
    return time.time_ns() // 1_000_000


def hour_key(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_ms(h: str) -> int:
    return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def open_name(h: str) -> str:
    return f"tape-{h}.jsonl"


def closed_name(h: str) -> str:
    return f"tape-{h}.jsonl.zst"


def code(x: Any) -> str:
    """A reason as a log-safe code (never file content)."""
    return x if isinstance(x, str) and CODE_RE.fullmatch(x) else "unspecified"


def frame(obj: dict, body: bytes = b"") -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode() + b"\n" + body


def pct(xs: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no data."""
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q / 100.0 * len(s) + 0.5)) - 1))
    return float(s[k])


def fsync_dir(d: Path) -> None:
    fd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def src_refusal(src: str) -> str | None:
    """serve reads only an arena-tape-like dir: never a sealed tree (by real path), never a non-directory."""
    real = os.path.realpath(src)
    for p in DENY_PREFIXES:
        if real == p or real.startswith(p + "/"):
            return "src_sealed"
    low = real.lower()
    if any(part in low for part in DENY_PARTS):
        return "src_sealed"
    if not os.path.isdir(real):
        return "src_not_dir"
    return None


def parse_hello(raw: bytes) -> tuple[str, dict[str, int], set[str]]:
    """The puller's hello -> (since, {hour: bytes held}, {closed hours}). Raises ValueError(code) on anything unexpected."""
    try:
        h = json.loads(raw)
    except ValueError:
        raise ValueError("hello_unreadable") from None
    if not isinstance(h, dict) or set(h) != {"v", "since", "have", "closed"} or h["v"] != PROTO:
        raise ValueError("hello_shape")
    since, have, closed = h["since"], h["have"], h["closed"]
    if not isinstance(since, str) or not HOUR_RE.match(since):
        raise ValueError("hello_since")
    if not isinstance(have, dict) or len(have) > HELLO_MAX_HAVE or not isinstance(closed, list) or len(closed) > HELLO_MAX_CLOSED:
        raise ValueError("hello_size")
    out: dict[str, int] = {}
    for name, off in have.items():
        m = OPEN_RE.match(name) if isinstance(name, str) else None
        if not m or isinstance(off, bool) or not isinstance(off, int) or not 0 <= off < MAX_OFFSET:
            raise ValueError("hello_have")
        out[m.group(1)] = off
    cl: set[str] = set()
    for x in closed:
        if not isinstance(x, str) or not HOUR_RE.match(x):
            raise ValueError("hello_closed")
        cl.add(x)
    if cl & set(out):
        raise ValueError("hello_overlap")
    if any(x < since for x in cl | set(out)):
        raise ValueError("hello_before_since")
    return since, out, cl


# ---- serve (mal-research-0) ---------------------------------------------------------------------------------------------------
class Bad(Exception):
    """serve stops with a `bad` frame (code only)."""


class Server:
    """Tails the source dir and writes frames. One instance per ssh session; all state comes from the hello."""

    def __init__(self, src: str | Path, since: str, have: dict[str, int], closed: set[str], write: Callable[[bytes], None] | None = None,
                 clock_ns: Callable[[], int] = time.time_ns, zst_pass_bytes: int = ZST_PASS_BYTES_DEFAULT, live_pass_bytes: int = LIVE_PASS_BYTES,
                 hb_ms: int = HB_MS_DEFAULT) -> None:
        self.src = Path(src)
        self.since = since
        self.sent: dict[str, int] = dict(have)  # open hour -> bytes the puller holds of its .jsonl
        self.closed = set(closed)  # hours the puller holds (or is installing) as .zst: never sent again
        self.open: dict[str, dict] = {}  # open hour -> {"ino", "size", "mt", "clean_ms"}
        self.zq: list[str] = []
        self.zcur: dict | None = None
        self.gone: set[str] = set()
        self.write = write or self._stdout
        self.clock_ns = clock_ns
        self.zst_pass_bytes = int(zst_pass_bytes)
        self.live_pass_bytes = int(live_pass_bytes)
        self.hb_ms = int(hb_ms)
        self._out: list[bytes] = []
        self._last_list = -1 << 62
        self._last_hb = -1 << 62
        self._rescan = True
        self.pending = False
        self.stats = collections.Counter()

    @staticmethod
    def _stdout(b: bytes) -> None:
        sys.stdout.buffer.write(b)
        sys.stdout.buffer.flush()

    def _ms(self) -> int:
        return self.clock_ns() // 1_000_000

    def _emit(self, obj: dict, body: bytes = b"") -> None:
        self._out.append(frame(obj, body))

    def flush(self) -> None:
        if self._out:
            data, self._out = b"".join(self._out), []
            self.write(data)

    def hello(self) -> None:
        self._emit({"k": "hello", "v": PROTO, "ms": self._ms(), "pid": os.getpid()})
        self.flush()

    def bad(self, why: str) -> None:
        self._out = []
        self._emit({"k": "bad", "why": code(why)})
        self.flush()

    # -- listing
    def _queue(self, h: str) -> None:
        if h not in self.closed and h not in self.zq and not (self.zcur and self.zcur["h"] == h):
            self.zq.append(h)
            self.zq.sort()

    def _gone(self, h: str) -> None:
        if h not in self.gone:
            self.gone.add(h)
            self._emit({"k": "gone", "h": h})
        self.sent.pop(h, None)
        self.open.pop(h, None)

    def scan(self) -> None:
        opens, closeds = set(), set()
        with os.scandir(self.src) as it:
            for e in it:
                m = OPEN_RE.match(e.name)
                if m and m.group(1) >= self.since:
                    opens.add(m.group(1))
                    continue
                m = CLOSED_RE.match(e.name)
                if m and m.group(1) >= self.since:
                    closeds.add(m.group(1))
        for h in sorted(opens):
            if h in self.closed:
                raise Bad("reopened_closed_hour")  # the feed never writes an hour that has a .zst (arena_feed.TapeWriter.write)
            if h not in self.open:
                self.open[h] = {"ino": None, "size": None, "mt": None, "clean_ms": None}
                self.sent.setdefault(h, 0)
        for h in list(self.open):
            if h not in opens:
                del self.open[h]
                self.sent.pop(h, None)
                if h in closeds:
                    self._queue(h)
                else:
                    self._gone(h)
        for h in sorted(closeds - opens):
            self._queue(h)
        for h in list(self.sent):
            if h not in opens and h not in closeds:
                self._gone(h)
        self._rescan = False

    def _current_hour(self, now: int) -> None:
        """A new hour file is picked up within one pass, not one listing."""
        h = hour_key(now)
        if h >= self.since and h not in self.open and h not in self.closed and os.path.exists(self.src / open_name(h)):
            self.open[h] = {"ino": None, "size": None, "mt": None, "clean_ms": None}
            self.sent.setdefault(h, 0)

    # -- one open hour
    def _live(self, h: str, now: int) -> int:
        try:
            fd = os.open(self.src / open_name(h), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            self._rescan = True  # compacted (or removed) since the listing
            return 0
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Bad("src_not_regular") from None
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise Bad("src_not_regular")
            o = self.open[h]
            if o["ino"] is None:
                o["ino"] = st.st_ino
            elif o["ino"] != st.st_ino:
                raise Bad("src_replaced")
            s = self.sent.get(h, 0)
            size = st.st_size
            o["size"], o["mt"] = size, st.st_mtime_ns // 1_000_000
            if size < s:
                raise Bad("src_shrunk")
            if size == s:
                o["clean_ms"] = now  # the source held exactly what was sent at this instant
                return 0
            n = min(size - s, self.live_pass_bytes)
            data = os.pread(fd, n, s)
        finally:
            os.close(fd)
        if len(data) != n:
            raise Bad("src_shrunk")
        cut = data.rfind(b"\n") + 1
        if cut == 0:
            if n >= self.live_pass_bytes:
                raise Bad("line_too_long")
            return 0  # a partial line only: not a row yet
        self._emit({"k": "d", "f": open_name(h), "o": s, "n": cut, "mt": o["mt"], "ps": o["clean_ms"], "ms": now}, data[:cut])
        self.sent[h] = s + cut
        if s + cut == size:
            o["clean_ms"] = now  # nothing beyond `sent` existed at this instant
        elif n >= self.live_pass_bytes:
            self.pending = True  # budget hit: complete lines may remain
        # else: a partial line remains; the earlier clean_ms still bounds it (it was written after that instant)
        self.stats["live_bytes"] += cut
        return cut

    # -- closed hours
    def _zst(self, budget: int) -> int:
        sent = 0
        while budget > 0:
            if self.zcur is None:
                if not self.zq:
                    break
                h = self.zq.pop(0)
                try:
                    fd = os.open(self.src / closed_name(h), os.O_RDONLY | os.O_NOFOLLOW)
                except FileNotFoundError:
                    self._rescan = True
                    continue
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise Bad("zst_not_regular") from None
                    raise
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    os.close(fd)
                    raise Bad("zst_not_regular")
                self.zcur = {"h": h, "fd": fd, "size": st.st_size, "mt_ns": st.st_mtime_ns, "off": 0, "sha": hashlib.sha256()}
            z = self.zcur
            n = min(ZST_CHUNK, z["size"] - z["off"], budget)
            if n > 0:
                data = os.pread(z["fd"], n, z["off"])
                if len(data) != n:
                    raise Bad("zst_changed")
                self._emit({"k": "z", "f": closed_name(z["h"]), "o": z["off"], "n": n, "size": z["size"]}, data)
                z["sha"].update(data)
                z["off"] += n
                budget -= n
                sent += n
            if z["off"] >= z["size"]:
                st = os.fstat(z["fd"])
                os.close(z["fd"])
                self.zcur = None
                if st.st_size != z["size"] or st.st_mtime_ns != z["mt_ns"]:
                    raise Bad("zst_changed")
                self._emit({"k": "ze", "f": closed_name(z["h"]), "size": z["size"], "sha": z["sha"].hexdigest(), "mt": z["mt_ns"] // 1_000_000})
                self.closed.add(z["h"])
                self.stats["zst_files"] += 1
        self.stats["zst_bytes"] += sent
        return sent

    def close(self) -> None:
        if self.zcur is not None:
            os.close(self.zcur["fd"])
            self.zcur = None

    def poll(self, force_hb: bool = False) -> int:
        """One pass: listing (if due), live data, .zst budget, heartbeat (if due). Returns the payload bytes framed."""
        now = self._ms()
        if self._rescan or now - self._last_list >= LIST_MS:
            self.scan()
            self._last_list = now
        self._current_hour(now)
        self.pending = False
        n = 0
        for h in sorted(self.open):
            n += self._live(h, now)
        if self._rescan:  # an open hour vanished mid-pass (compaction): list again now, so the hb below never names a stale hour
            self.scan()
            self._last_list = now
            for h in sorted(self.open):
                n += self._live(h, now)
        n += self._zst(self.zst_pass_bytes)
        if force_hb or now - self._last_hb >= self.hb_ms:
            self._emit({"k": "hb", "ms": now, "open": {h: [self.sent.get(h, 0), o["size"], o["mt"]] for h, o in sorted(self.open.items()) if o["size"] is not None},
                        "zq": len(self.zq) + (1 if self.zcur else 0), "full": not self.pending})
            self._last_hb = now
        self.flush()
        return n


def read_hello(fd: int, timeout_s: float = HELLO_TIMEOUT_S) -> bytes:
    buf = b""
    end = time.monotonic() + timeout_s
    while b"\n" not in buf:
        left = end - time.monotonic()
        if left <= 0:
            raise ValueError("hello_timeout")
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            raise ValueError("hello_eof")
        buf += chunk
        if len(buf) > HELLO_MAX:
            raise ValueError("hello_too_long")
    return buf.split(b"\n", 1)[0]


def run_serve(args: argparse.Namespace) -> int:
    def say_bad(why: str) -> int:
        try:
            sys.stdout.buffer.write(frame({"k": "bad", "why": code(why)}))
            sys.stdout.buffer.flush()
        except OSError:
            pass
        return EXIT_MISMATCH

    why = src_refusal(args.src)
    if why:
        return say_bad(why)
    try:
        since, have, closed = parse_hello(read_hello(sys.stdin.fileno()))
    except ValueError as exc:
        return say_bad(str(exc))
    srv = Server(args.src, since, have, closed, zst_pass_bytes=args.zst_pass_bytes, hb_ms=args.hb_ms)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    poll_s = max(1, args.poll_ms) / 1000.0
    try:
        srv.hello()
        while True:
            t0 = time.monotonic()
            srv.poll()
            dt = time.monotonic() - t0
            if dt < poll_s:
                time.sleep(poll_s - dt)
    except Bad as exc:
        srv.bad(str(exc))
        return EXIT_MISMATCH
    except (BrokenPipeError, ConnectionResetError):
        return EXIT_OK  # the puller went away
    finally:
        srv.close()


# ---- pull (mal-fast-0) --------------------------------------------------------------------------------------------------------
class Mismatch(Exception):
    """Local integrity: stop (exit 2), nothing unverified is written."""


class Reconnect(Exception):
    """Drop this session and reconnect (a failed transfer, a remote refusal)."""


class DiskReserve(Exception):
    pass


class Locked(RuntimeError):
    pass


def verify_closed(zstd: str, part: Path, local: Path | None) -> dict:
    """Decompress `part` and check that the live copy `local` (if any) is an exact prefix of it. Counts and hashes only."""
    L, hl = 0, hashlib.sha256()
    if local is not None and local.exists():
        with open(local, "rb") as fh:
            while True:
                b = fh.read(1 << 20)
                if not b:
                    break
                hl.update(b)
                L += len(b)
    hp, hall, total = hashlib.sha256(), hashlib.sha256(), 0
    p = subprocess.Popen([zstd, "-dc", "-q", str(part)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert p.stdout is not None
    try:
        while True:
            b = p.stdout.read(1 << 20)
            if not b:
                break
            if total < L:
                hp.update(b[: L - total])
            hall.update(b)
            total += len(b)
    finally:
        p.stdout.close()
        rc = p.wait()
    return {"rc": rc, "dec_bytes": total, "dec_sha": hall.hexdigest(), "live_bytes": L, "prefix_ok": rc == 0 and total >= L and hp.hexdigest() == hl.hexdigest(),
            "live_complete": rc == 0 and total == L}


class Puller:
    """Connects (cmd = the serve command as argv), sends the hello, verifies and writes frames, keeps status/events/lag. One per dst (flock)."""

    def __init__(self, dst: str | Path, cmd: Sequence[str], *, backfill_hours: int = BACKFILL_HOURS_DEFAULT, retain_hours: int = RETAIN_HOURS_DEFAULT,
                 min_free_pct: float = MIN_FREE_PCT_DEFAULT, stall_ms: int = STALL_MS_DEFAULT, reconnect_silence_ms: int = RECONNECT_SILENCE_MS_DEFAULT,
                 connect_timeout_ms: int = CONNECT_TIMEOUT_MS, clock_ns: Callable[[], int] = time.time_ns, zstd: str | None = None,
                 disk_usage: Callable[[str], Any] = shutil.disk_usage, progress_path: str | None = None, disk_retry_s: float = DISK_RETRY_S) -> None:
        if not 1 <= int(backfill_hours) <= int(retain_hours):
            raise ValueError("backfill_hours must be 1..retain_hours")
        if os.path.realpath(str(dst)).startswith("/data/mal/arena"):
            raise ValueError("dst_under_arena")  # never write into the source tree
        self.zstd = zstd or shutil.which("zstd")
        if not self.zstd:
            raise ValueError("zstd_missing")  # a closed hour cannot be verified, and no reader here could read it
        self.dir = Path(dst)
        self.meta = self.dir / META_DIR
        self.meta.mkdir(parents=True, exist_ok=True)
        self._lock_fd: int | None = os.open(str(self.meta / "lock"), os.O_RDWR | os.O_CREAT, 0o644)
        import fcntl

        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise Locked("dst_locked") from None
        self.cmd = list(cmd)
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
        self.sizes: dict[str, int] = {}  # open hour -> local .jsonl size
        self.local_closed: set[str] = set()
        self.verifying: set[str] = set()
        self.fds: dict[str, int] = {}
        self.part: dict | None = None
        self.zst_retry: collections.Counter = collections.Counter()
        self.since: str | None = None
        self.last_frame_ms = 0
        self.last_remote_ms: int | None = None
        self.synced_ms: int | None = None
        self.src_mt_ms: int | None = None
        self.behind_bytes: int | None = None
        self.zst_pending = 0
        self.events = 0
        self.closed_verified = 0
        self.live_bytes = 0
        self.zst_bytes = 0
        self.connects = 0
        self.remote_bad_streak = 0
        self.src_gone: set[str] = set()
        self.lag: collections.deque = collections.deque()
        self._minute: dict | None = None
        self._last_fsync = time.monotonic()
        self._last_prune = float("-inf")
        self._last_status = float("-inf")
        self._last_progress = float("-inf")
        self._vq: queue.Queue = queue.Queue()
        self._vres: queue.Queue = queue.Queue()
        self._vthread = threading.Thread(target=self._verifier, name="tape-mirror-verify", daemon=True)
        self._vthread.start()
        self.proc: subprocess.Popen | None = None
        self._startup()

    # -- housekeeping
    def _ms(self) -> int:
        return self.clock_ns() // 1_000_000

    def event(self, kind: str, **kw: Any) -> None:
        self.events += 1
        row = {"kind": kind, "ms": self._ms(), **kw}
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        print("tape_mirror EVENT " + json.dumps(row, sort_keys=True), file=sys.stderr, flush=True)

    def _startup(self) -> None:
        for p in self.dir.glob("tape-*" + PART_SUFFIX):
            p.unlink()
        for p in sorted(self.dir.iterdir()):
            m = CLOSED_RE.match(p.name)
            if m and p.is_file():
                self.local_closed.add(m.group(1))
        for p in sorted(self.dir.iterdir()):
            m = OPEN_RE.match(p.name)
            if not m or not p.is_file():
                continue
            h = m.group(1)
            if h in self.local_closed:  # a crash between the .zst rename and the .jsonl unlink: the .zst was verified before its rename
                p.unlink()
                self.event("local_jsonl_removed_after_install", h=h)
                continue
            data_size = p.stat().st_size
            keep = data_size
            if data_size:
                with open(p, "rb") as fh:
                    fh.seek(max(0, data_size - LIVE_PASS_BYTES))
                    tail = fh.read()
                if not tail.endswith(b"\n"):  # our own crash mid-write (the source only ever gave us whole lines): trim, the source resends
                    i = tail.rfind(b"\n")
                    keep = data_size - len(tail) + i + 1 if i >= 0 else (0 if data_size <= LIVE_PASS_BYTES else -1)
                    if keep < 0:
                        raise ValueError("local_tail_unreadable")  # no newline in the last LIVE_PASS_BYTES: not a file this tool wrote
                    with open(p, "r+b") as fh:
                        fh.truncate(keep)
                        os.fsync(fh.fileno())
                    self.event("local_torn_trimmed", h=h, dropped=data_size - keep)
            self.sizes[h] = keep
        self.prune(force=True)

    def _since(self) -> str:
        now = self._ms()
        floor = hour_key(now - self.retain_hours * H_MS)
        cand = [hour_key(now - self.backfill_hours * H_MS)] + [h for h in self.sizes if h >= floor]
        return min(cand)

    def hello_line(self) -> bytes:
        self.since = self._since()
        have = {open_name(h): s for h, s in sorted(self.sizes.items()) if h >= self.since}
        closed = sorted(h for h in self.local_closed | self.verifying if h >= self.since)
        return json.dumps({"v": PROTO, "since": self.since, "have": have, "closed": closed}, sort_keys=True).encode() + b"\n"

    def _disk_ok(self, extra: int = 0) -> None:
        du = self.disk_usage(str(self.dir))
        if du.total <= 0 or (du.free - extra) * 100.0 / du.total < self.min_free_pct:
            raise DiskReserve()

    def free_pct(self) -> float | None:
        try:
            du = self.disk_usage(str(self.dir))
            return round(du.free * 100.0 / du.total, 3) if du.total else None
        except OSError:
            return None

    def _close_fd(self, h: str) -> None:
        fd = self.fds.pop(h, None)
        if fd is not None:
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def fsync_all(self) -> None:
        for fd in self.fds.values():
            os.fsync(fd)
        self._last_fsync = time.monotonic()

    def prune(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._last_prune < PRUNE_EVERY_S:
            return
        self._last_prune = time.monotonic()
        cutoff = hour_key(self._ms() - self.retain_hours * H_MS)
        n, nbytes = 0, 0
        for p in sorted(self.dir.iterdir()):
            m = OPEN_RE.match(p.name) or CLOSED_RE.match(p.name)
            if not m or m.group(1) >= cutoff or not p.is_file() or m.group(1) in self.verifying:
                continue
            h = m.group(1)
            self._close_fd(h)
            nbytes += p.stat().st_size
            p.unlink()
            n += 1
            self.sizes.pop(h, None)
            self.local_closed.discard(h)
        if n:
            fsync_dir(self.dir)
            self.event("pruned", files=n, bytes=nbytes, before=cutoff)

    # -- frames
    def handle(self, hdr: dict, body: bytes = b"") -> None:
        k = hdr.get("k")
        if k == "d":
            self._data(hdr, body)
        elif k == "z":
            self._zchunk(hdr, body)
        elif k == "ze":
            self._zend(hdr)
        elif k == "hb":
            self._hb(hdr)
        elif k == "hello":
            self.last_remote_ms = int(hdr.get("ms") or 0)
        elif k == "gone":
            h = hdr.get("h")
            if not isinstance(h, str) or not HOUR_RE.match(h):
                raise Mismatch("bad_gone_frame")
            if h not in self.src_gone:
                self.src_gone.add(h)
                self.event("src_vanished", h=h)
        elif k == "bad":
            self.remote_bad_streak += 1
            if self.remote_bad_streak >= REMOTE_BAD_MAX:
                raise Mismatch("remote_refusing")
            raise Reconnect("remote_" + code(hdr.get("why")))
        else:
            raise Mismatch("unknown_frame")

    def _data(self, hdr: dict, body: bytes) -> None:
        m = OPEN_RE.match(str(hdr.get("f")))
        if not m:
            raise Mismatch("bad_name")
        h = m.group(1)
        if h in self.local_closed or h in self.verifying:
            raise Mismatch("data_for_closed_hour")
        o, n = int(hdr["o"]), int(hdr["n"])
        if n <= 0 or len(body) != n or not body.endswith(b"\n"):
            raise Mismatch("torn_frame")
        if o != self.sizes.get(h, 0):
            raise Mismatch("offset")
        self._disk_ok(n)
        fd = self.fds.get(h)
        if fd is None:
            path = self.dir / open_name(h)
            new = not path.exists()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            self.fds[h] = fd
            if new:
                fsync_dir(self.dir)
        view = memoryview(body)
        while view:
            w = os.write(fd, view)
            view = view[w:]
        self.sizes[h] = o + n
        self.live_bytes += n
        t = self._ms()
        mt, ps = hdr.get("mt"), hdr.get("ps")
        self._lag(t, t - int(mt) if isinstance(mt, int) else None, t - int(ps) if isinstance(ps, int) else None, n)

    def _zchunk(self, hdr: dict, body: bytes) -> None:
        m = CLOSED_RE.match(str(hdr.get("f")))
        if not m:
            raise Mismatch("bad_name")
        h = m.group(1)
        if h in self.local_closed or h in self.verifying:
            raise Mismatch("zst_for_closed_hour")
        o, n, size = int(hdr["o"]), int(hdr["n"]), int(hdr["size"])
        if len(body) != n or n <= 0:
            raise Mismatch("torn_frame")
        if o == 0:
            if self.part is not None:
                raise Mismatch("zst_interleaved")
            self._disk_ok(size)
            path = self.dir / (closed_name(h) + PART_SUFFIX)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            self.part = {"h": h, "fd": fd, "path": path, "size": size, "off": 0, "sha": hashlib.sha256()}
        elif self.part is None or self.part["h"] != h or self.part["off"] != o or self.part["size"] != size:
            raise Mismatch("zst_offset")
        view = memoryview(body)
        while view:
            w = os.write(self.part["fd"], view)
            view = view[w:]
        self.part["sha"].update(body)
        self.part["off"] += n
        self.zst_bytes += n

    def drop_part(self) -> None:
        if self.part is not None:
            os.close(self.part["fd"])
            self.part["path"].unlink(missing_ok=True)
            self.part = None

    def _zend(self, hdr: dict) -> None:
        m = CLOSED_RE.match(str(hdr.get("f")))
        p = self.part
        if not m:
            raise Mismatch("bad_name")
        h = m.group(1)
        if p is None or p["h"] != h:
            raise Mismatch("zst_end_without_part")  # a zero-byte .zst also lands here: it cannot be a compacted hour
        size = hdr.get("size")
        if p["off"] != size or p["size"] != size or p["sha"].hexdigest() != hdr.get("sha"):
            self.drop_part()
            self.zst_retry[h] += 1
            self.event("zst_sha_mismatch", h=h, attempt=self.zst_retry[h])
            if self.zst_retry[h] >= ZST_RETRIES:
                raise Mismatch("zst_retry_exhausted")
            raise Reconnect("zst_sha")
        os.fsync(p["fd"])
        os.close(p["fd"])
        self.part = None
        self.verifying.add(h)
        self._close_fd(h)  # the live copy is final: serve sends no more data for a closed hour
        self._vq.put((h, p["path"], hdr["sha"], size))

    def _hb(self, hdr: dict) -> None:
        ms = int(hdr["ms"])
        self.last_remote_ms = ms
        self.remote_bad_streak = 0
        opens = hdr.get("open") or {}
        behind, mt_max = 0, None
        for h, v in opens.items():
            if not HOUR_RE.match(str(h)) or not isinstance(v, list) or len(v) != 3:
                raise Mismatch("bad_hb")
            sent, size, mt = v
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
                raise Mismatch("bad_hb")
            if h in self.local_closed or h in self.verifying:
                raise Mismatch("hb_open_for_closed_hour")
            if self.sizes.get(h, 0) != sent:
                raise Mismatch("hb_position")
            behind += max(0, int(size) - int(sent))
            mt_max = mt if mt_max is None else max(mt_max, mt)
        if hdr.get("full") is True:
            self.synced_ms = ms
        if mt_max is not None:
            self.src_mt_ms = int(mt_max)
        self.behind_bytes = behind
        self.zst_pending = int(hdr.get("zq") or 0) + len(self.verifying)

    # -- the verifier thread (off the receive path) and the install
    def _verifier(self) -> None:
        while True:
            item = self._vq.get()
            if item is None:
                return
            h, part, sha, size = item
            try:
                res = verify_closed(self.zstd, part, self.dir / open_name(h))
            except Exception as exc:  # noqa: BLE001 - reported to the main loop as a failed verify
                res = {"rc": -1, "error": type(exc).__name__, "prefix_ok": False}
            self._vres.put((h, part, sha, size, res))

    def drain_verified(self, block_s: float = 0.0) -> int:
        """Install every verified hour that is ready. block_s > 0 waits that long for the FIRST result only (tests); the receive loop never blocks."""
        n = 0
        end = time.monotonic() + block_s
        while True:
            try:
                left = end - time.monotonic()
                item = self._vres.get(timeout=left) if (left > 0 and n == 0) else self._vres.get_nowait()
            except queue.Empty:
                return n
            h, part, sha, size, res = item
            self.verifying.discard(h)
            if res.get("rc") != 0:
                raise Mismatch("zst_decode_failed")
            if not res["prefix_ok"]:
                raise Mismatch("closed_hour_diverged")
            final = self.dir / closed_name(h)
            os.replace(part, final)
            fsync_dir(self.dir)
            live = self.dir / open_name(h)
            live.unlink(missing_ok=True)
            fsync_dir(self.dir)
            self.sizes.pop(h, None)
            self.local_closed.add(h)
            self.closed_verified += 1
            self.event("closed", h=h, zst_bytes=size, zst_sha=sha, dec_bytes=res["dec_bytes"], dec_sha=res["dec_sha"], live_bytes=res["live_bytes"],
                       live_complete=res["live_complete"])
            n += 1

    # -- lag, status, progress
    def _lag(self, t: int, new: int | None, old: int | None, nbytes: int) -> None:
        self.lag.append((t, new, old))
        while self.lag and t - self.lag[0][0] > LAG_WINDOW_MS:
            self.lag.popleft()
        minute = t // 60_000
        if self._minute is not None and self._minute["m"] != minute:
            self.flush_minute()
        if self._minute is None:
            self._minute = {"m": minute, "frames": 0, "bytes": 0, "new": [], "old": []}
        mm = self._minute
        mm["frames"] += 1
        mm["bytes"] += nbytes
        if new is not None:
            mm["new"].append(new)
        if old is not None:
            mm["old"].append(old)

    def flush_minute(self) -> None:
        mm, self._minute = self._minute, None
        if not mm:
            return
        row = {"minute": datetime.fromtimestamp(mm["m"] * 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "frames": mm["frames"], "bytes": mm["bytes"],
               "lag_new_p50_ms": pct(mm["new"], 50), "lag_new_p90_ms": pct(mm["new"], 90), "lag_new_max_ms": max(mm["new"]) if mm["new"] else None,
               "lag_old_p50_ms": pct(mm["old"], 50), "lag_old_p90_ms": pct(mm["old"], 90), "lag_old_max_ms": max(mm["old"]) if mm["old"] else None}
        with open(self.lag_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def coverage(self) -> tuple[str | None, int]:
        """(oldest hour of the contiguous run of local hours that ends at the current UTC hour, its length in hours). At most retain_hours + 2 set lookups."""
        have = set(self.sizes) | self.local_closed
        t = (self._ms() // H_MS) * H_MS
        start, n = None, 0
        while hour_key(t) in have and n <= self.retain_hours + 1:
            start, n, t = hour_key(t), n + 1, t - H_MS
        return start, n

    def status(self) -> dict:
        news = [x[1] for x in self.lag if x[1] is not None]
        olds = [x[2] for x in self.lag if x[2] is not None]
        start, n = self.coverage()
        return {"schema": SCHEMA, "state": self.state, "ms": self._ms(), "pid": os.getpid(), "since": self.since, "last_frame_ms": self.last_frame_ms,
                "last_remote_ms": self.last_remote_ms, "synced_ms": self.synced_ms, "src_mt_ms": self.src_mt_ms, "behind_bytes": self.behind_bytes,
                "open_hours": len(self.sizes), "closed_hours": len(self.local_closed), "zst_pending": self.zst_pending, "closed_verified": self.closed_verified,
                "history_from": start, "history_hours": n, "live_bytes": self.live_bytes, "zst_bytes": self.zst_bytes, "connects": self.connects,
                "events": self.events, "src_vanished": len(self.src_gone), "free_pct": self.free_pct(), "retain_hours": self.retain_hours,
                "backfill_hours": self.backfill_hours, "min_free_pct": self.min_free_pct,
                "lag_60s": {"frames": len(self.lag), "new_p50_ms": pct(news, 50), "new_p90_ms": pct(news, 90), "new_max_ms": max(news) if news else None,
                            "old_p90_ms": pct(olds, 90), "old_max_ms": max(olds) if olds else None}}

    def write_status(self) -> None:
        tmp = self.meta / "status.json.tmp"
        tmp.write_text(json.dumps(self.status(), sort_keys=True) + "\n")
        os.replace(tmp, self.status_path)
        self._last_status = time.monotonic()

    def write_progress(self) -> None:
        if not self.progress_path or time.monotonic() - self._last_progress < PROGRESS_EVERY_S:
            return
        self._last_progress = time.monotonic()
        st = self.status()
        pc = min(100, int(100 * st["history_hours"] / max(1, self.backfill_hours)))
        note = f"state={st['state']} history_h={st['history_hours']} lag_new_p90_ms={st['lag_60s']['new_p90_ms']} closed_verified={st['closed_verified']}"
        try:
            Path(self.progress_path).write_text(json.dumps({"pct": pc, "note": note}))
        except OSError:
            pass

    def tick(self) -> None:
        """Everything time-driven, called from the receive loop."""
        self.drain_verified()
        if time.monotonic() - self._last_fsync >= FSYNC_EVERY_S:
            self.fsync_all()
        self.prune()
        if time.monotonic() - self._last_status >= STATUS_EVERY_S:
            self.write_status()
        self.write_progress()

    # -- the connection loop
    def close(self) -> None:
        for h in list(self.fds):
            self._close_fd(h)
        if self.part is not None:
            self.drop_part()
        if self._vthread.is_alive():
            self._vq.put(None)
            self._vthread.join(timeout=60)
        if self._minute:
            self.flush_minute()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def run(self, max_s: float | None = None, stop: threading.Event | None = None, until: Callable[["Puller"], bool] | None = None) -> int:
        try:
            return self._run(max_s, stop or threading.Event(), until)
        except BaseException as exc:
            if self.state not in ("stopped", "stopped_mismatch"):
                self.state = "stopped_error"
                try:
                    self.event("error", why=type(exc).__name__)
                    self.write_status()
                except OSError:
                    pass
            self._kill()
            raise
        finally:
            self.close()

    def _sleep(self, s: float, stop: threading.Event) -> None:
        stop.wait(s)

    def _run(self, max_s: float | None, stop: threading.Event, until: Callable[["Puller"], bool] | None) -> int:
        deadline = time.monotonic() + max_s if max_s else None
        attempt = 0
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            t0 = time.monotonic()
            self.drop_part()  # a transfer interrupted by the last session restarts at its first byte
            hello = self.hello_line()
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
            self.state = "connecting"
            self.connects += 1
            self.write_status()
            if attempt:
                self.event("reconnect", attempt=attempt)
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(hello)
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                why = self._pump(deadline, stop, until, t0)
            except Mismatch as exc:
                self.state = "stopped_mismatch"
                self.event("mismatch", why=code(str(exc)))
                self.write_status()
                self._kill()
                return EXIT_MISMATCH
            except Reconnect as exc:
                why = code(str(exc))
            except DiskReserve:
                self._kill()
                self.state = "disk_reserve"
                self.event("disk_reserve", free_pct=self.free_pct(), min_free_pct=self.min_free_pct)
                self.write_status()
                self._sleep(self.disk_retry_s, stop)
                self.prune(force=True)
                continue
            self._kill()
            if why == "done":
                self.state = "stopped"
                self.write_status()
                return EXIT_OK
            self.state = "disconnected"
            self.event("disconnect", why=why, rc=self.proc.returncode if self.proc else None)
            self.write_status()
            self._sleep(BACKOFF_MS[min(attempt, len(BACKOFF_MS) - 1)] / 1000.0, stop)
            attempt += 1
            if time.monotonic() - t0 > 60:
                attempt = 1  # a session that lived a minute was healthy: the next reconnect starts at the short backoff again
        self.state = "stopped"
        self.write_status()
        return EXIT_OK

    def _pump(self, deadline: float | None, stop: threading.Event, until: Callable[["Puller"], bool] | None, t0: float) -> str:
        assert self.proc and self.proc.stdout
        fd = self.proc.stdout.fileno()
        buf = bytearray()
        hdr: dict | None = None
        connected = False
        stalled_since: int | None = None
        self.last_frame_ms = self._ms()
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                data = os.read(fd, 1 << 18)
                if not data:
                    return "eof"
                buf += data
                while True:
                    if hdr is None:
                        i = buf.find(b"\n")
                        if i < 0:
                            if len(buf) > MAX_HEADER:
                                raise Mismatch("frame_too_long")
                            break
                        try:
                            hdr = json.loads(bytes(buf[:i]))
                        except ValueError:
                            raise Mismatch("unreadable_frame") from None
                        del buf[: i + 1]
                        if not isinstance(hdr, dict):
                            raise Mismatch("unreadable_frame")
                        if hdr.get("k") in ("d", "z"):
                            n = hdr.get("n")
                            if isinstance(n, bool) or not isinstance(n, int) or not 0 < n <= MAX_BODY:
                                raise Mismatch("bad_body_size")
                    need = int(hdr["n"]) if hdr.get("k") in ("d", "z") else 0
                    if len(buf) < need:
                        break
                    body = bytes(buf[:need])
                    del buf[:need]
                    h, hdr = hdr, None
                    self.last_frame_ms = self._ms()
                    if not connected:
                        connected = True
                        self.state = "connected"
                    self.handle(h, body)
                    if stalled_since is not None:
                        self.event("resume", gap_ms=self._ms() - stalled_since)
                        stalled_since = None
                        self.state = "connected"
                if until and until(self):
                    return "done"
            self.tick()
            if not connected and (time.monotonic() - t0) * 1000 > self.connect_timeout_ms:
                return "connect_timeout"
            if connected and self._ms() - self.last_frame_ms > self.reconnect_silence_ms:
                return "silent_reconnect"
            if connected and stalled_since is None and self._ms() - self.last_frame_ms > self.stall_ms:
                stalled_since = self.last_frame_ms
                self.state = "stalled"
                self.event("stall", last_frame_ms=self.last_frame_ms)
                self.write_status()
            if until and until(self):
                return "done"
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


def remote_serve_cmd(ssh_host: str, remote_repo: str, src: str, python: str = "python3") -> list[str]:
    """The production argv: ssh (BatchMode, keepalives) to research-0, cd to the pinned checkout, exec serve."""
    if not remote_repo.startswith("/") or "/../" in remote_repo + "/" or not src.startswith("/"):
        raise ValueError("remote_repo and src must be absolute paths without ..")
    remote = f"cd {shlex.quote(remote_repo)} && exec {shlex.quote(python)} -u tools/tape_mirror.py serve --src {shlex.quote(src)}"
    return ssh_argv(ssh_host) + [remote]


def run_pull(args: argparse.Namespace) -> int:
    if bool(args.cmd_json) == bool(args.ssh_host):
        print("pull: give exactly one of --ssh-host (with --remote-repo) or --cmd-json (tests)", file=sys.stderr)
        return EXIT_REFUSED
    try:
        cmd = json.loads(args.cmd_json) if args.cmd_json else remote_serve_cmd(args.ssh_host, args.remote_repo or "", args.src)
        pl = Puller(args.dst, cmd, backfill_hours=args.backfill_hours, retain_hours=args.retain_hours, min_free_pct=args.min_free_pct,
                    stall_ms=args.stall_ms, reconnect_silence_ms=args.reconnect_silence_ms, progress_path=os.environ.get("MISCUSI_PROGRESS"))
    except (Locked, ValueError) as exc:
        print(f"pull refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    rc = EXIT_MISMATCH
    try:
        rc = pl.run(max_s=args.max_s, stop=stop)
    finally:
        write_result(os.environ.get("MISCUSI_RESULT"), pl, rc)
    return rc


def write_result(path: str | None, pl: Puller, rc: int) -> None:
    """The MiScusi result of a pull job (the job script execs python, so a cancel's SIGTERM reaches the puller and this still runs). Counts only."""
    if not path:
        return
    try:
        st = pl.status()
        keys = ("state", "closed_verified", "history_hours", "live_bytes", "zst_bytes", "connects", "events", "src_vanished", "free_pct")
        m = {k: st.get(k) for k in keys}
        m.update({f"lag_{k}": v for k, v in (st.get("lag_60s") or {}).items()})
        Path(path).write_text(json.dumps({"ok": rc == EXIT_OK, "metrics": m, "summary": f"tape mirror ended rc={rc} state={st.get('state')}"}))
    except Exception:  # noqa: BLE001 - a result file must never turn a clean stop into a crash
        pass


# ---- the reader side (the host on mal-fast-0) ---------------------------------------------------------------------------------
def _int_or_none(x: Any) -> bool:
    return x is None or (isinstance(x, int) and not isinstance(x, bool))


def mirror_problem(dst: str | Path, now: int | None = None, *, status_max_age_ms: int = STATUS_MAX_AGE_MS, sync_max_age_ms: int = SYNC_MAX_AGE_MS,
                   src_max_age_ms: int = SRC_MAX_AGE_MS, skew_ms: int = SKEW_MS, need_hours: int | None = None) -> str | None:
    """None when the mirror at dst is fresh; otherwise a short code. FAIL CLOSED: anything unexpected is a problem, never an exception."""
    now = now_ms() if now is None else int(now)
    p = Path(dst) / META_DIR / "status.json"
    try:
        ls = os.lstat(p)
        if not stat.S_ISREG(ls.st_mode):
            return "status_not_regular"
        if ls.st_size > STATUS_READ_MAX:
            return "status_unreadable"
        st = json.loads(p.read_bytes())
    except FileNotFoundError:
        return "status_missing"
    except (OSError, ValueError):
        return "status_unreadable"
    try:
        if not isinstance(st, dict) or st.get("schema") != SCHEMA:
            return "status_schema"
        for k in ("ms", "synced_ms", "src_mt_ms", "history_hours"):
            if not _int_or_none(st.get(k)):
                return "status_schema"
        if st.get("ms") is None:
            return "status_schema"
        if st["ms"] > now + skew_ms:
            return "status_future"
        if now - st["ms"] > status_max_age_ms:
            return "status_stale"
        if st.get("state") != "connected":
            return "state_" + code(st.get("state"))
        if st.get("synced_ms") is None:
            return "never_synced"
        if st["synced_ms"] > now + skew_ms:
            return "sync_future"
        if now - st["synced_ms"] > sync_max_age_ms:
            return "sync_stale"
        if st.get("src_mt_ms") is None:
            return "source_unknown"
        if now - st["src_mt_ms"] > src_max_age_ms:
            return "source_stale"
        if need_hours is not None and (st.get("history_hours") or 0) < int(need_hours):
            return "history_short"
    except Exception:  # noqa: BLE001 - fail closed
        return "status_unreadable"
    return None


def run_status(args: argparse.Namespace) -> int:
    why = mirror_problem(args.dst, need_hours=args.need_hours)
    out: dict[str, Any] = {"problem": why}
    try:
        st = json.loads((Path(args.dst) / META_DIR / "status.json").read_bytes())
        n = now_ms()
        out.update({"state": st.get("state"), "synced_age_ms": n - st["synced_ms"] if isinstance(st.get("synced_ms"), int) else None,
                    "src_age_ms": n - st["src_mt_ms"] if isinstance(st.get("src_mt_ms"), int) else None, "history_hours": st.get("history_hours"),
                    "history_from": st.get("history_from"), "lag_60s": st.get("lag_60s"), "zst_pending": st.get("zst_pending"), "free_pct": st.get("free_pct")})
    except (OSError, ValueError, KeyError, TypeError):
        pass
    print(json.dumps(out, sort_keys=True))
    return 0 if why is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Byte-exact arena tape mirror research-0 -> mal-fast-0 (pull form). Paper only, no key, 0 credits.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="on mal-research-0: read the hello on stdin, stream frames on stdout")
    s.add_argument("--src", default=DEFAULT_SRC)
    s.add_argument("--poll-ms", type=int, default=POLL_MS_DEFAULT)
    s.add_argument("--hb-ms", type=int, default=HB_MS_DEFAULT)
    s.add_argument("--zst-pass-bytes", type=int, default=ZST_PASS_BYTES_DEFAULT)
    p = sub.add_parser("pull", help="on mal-fast-0: connect, verify, write the mirror")
    p.add_argument("--dst", required=True)
    p.add_argument("--ssh-host")
    p.add_argument("--remote-repo", help="absolute path of the pinned MAL checkout on the serve host")
    p.add_argument("--src", default=DEFAULT_SRC, help="the tape dir on the serve host")
    p.add_argument("--cmd-json", help="tests only: the serve argv as a JSON list")
    p.add_argument("--retain-hours", type=int, default=RETAIN_HOURS_DEFAULT)
    p.add_argument("--backfill-hours", type=int, default=BACKFILL_HOURS_DEFAULT)
    p.add_argument("--min-free-pct", type=float, default=MIN_FREE_PCT_DEFAULT)
    p.add_argument("--stall-ms", type=int, default=STALL_MS_DEFAULT)
    p.add_argument("--reconnect-silence-ms", type=int, default=RECONNECT_SILENCE_MS_DEFAULT)
    p.add_argument("--max-s", type=float, default=None)
    q = sub.add_parser("status", help="print the mirror's staleness verdict (exit 0 fresh, 1 not)")
    q.add_argument("--dst", required=True)
    q.add_argument("--need-hours", type=int, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        return run_serve(args)
    if args.cmd == "pull":
        return run_pull(args)
    return run_status(args)


if __name__ == "__main__":
    sys.exit(main())
