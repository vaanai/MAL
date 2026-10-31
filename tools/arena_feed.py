#!/usr/bin/env python3
"""Forward paper ARENA live feed. Paper only. No keys, no transactions, no RPC writes, 0 Helius credits.

One public-RPC logsSubscribe ingest of BOTH pump.fun programs (PumpSwap and the bonding curve), decoded with observe.trade_decode
(event_v=True, exactly as tools/h5_shadow.py does for PumpSwap) and written as an hourly strict-JSON-lines tape that the arena bots, the
paper engine and tools/arena_state.py read. Schema and reserve conventions: tools/arena_tape_schema.md.

INGEST. Per program, N redundant sockets (observe.trade_source.MultiSocketLogsSource, merged by signature; --sockets-pumpswap /
--sockets-pumpfun, default 2 each). Default endpoint wss://api.mainnet-beta.solana.com; more come via repeatable --ws-url (socket i uses url
i mod n). Any Helius URL, a URL with a query string, userinfo or a path (where a key could sit) is refused at start. A transaction that both
subscriptions deliver (a migrate tx mentions both programs) is decoded once (global signature dedup). That global set (LateSeen) remembers a
signature 900 s (the per-program socket merge keeps 120 s), so a lagging socket's re-delivery minutes after the first copy is not written again;
the gap rules still see every notice, so gap and hb rows are the same as before.

DECODERS (nothing new except one field extractor). Trades: observe.trade_decode.records_from_logs(event_v=True) for both programs.
PumpSwap CreatePoolEvent: observe.trade_decode.decode_program_data (kind create_pool). Bonding CreateEvent / CompleteEvent:
tools.pump_history_backfill.lifecycle_from_logs. The ONE addition is `_bonding_extras`: the bonding TradeEvent's fee / real-reserve / creator
fields (IDL offsets 113..225), which observe.trade_decode does not return; checked on tools/fixtures/exp003_mainnet_buy_v1.json (fee = sol *
bps / 1e4 to the lamport).

OUTPUT. <out-dir>/tape-<UTC hour of t_recv_ms>.jsonl (default /data/mal/arena/tape), flushed per line, plus arena-feed-status.json (atomic)
and arena-feed-errors.log (exception class + slot only, capped). Row types: trade, create, complete, pool_create, gap, hb, start, stop.
GAPS follow tools/h5_shadow's rules per program: slot jump (> GAP_SLOTS), silence (> SILENCE_S without a notice), socket_reconnect
(counters advanced; data_loss true only when EVERY socket of that program was down or silent at a common instant), feed_restart. An hour
with a data_loss gap or a missing hb is a bad hour. One hb row per program per minute.

SAFETY. flock(LOCK_EX|LOCK_NB) on <out-dir>/.arena-feed.lock: a second instance exits with code 3 (cancelled MiScusi jobs once left a
duplicate writer alive). DISK RESERVE (DEC-031 rev 2 s3.8, default 25): free space is shutil.disk_usage(out-dir).free / total, checked before every
hour file opens and at least every 60 s. Below --min-free-pct nothing is written (no row of any type, no .zst), only arena-feed-status.json
carries a flag; when space allows again ONE gap record (kind and reason disk_reserve) says for how long and how many rows were dropped.
Memory: bounded dedup sets (the sources' 120 s socket merge, and LateSeen: 900 s, 3.3M keys, about 200 bytes a key), no per-pool state here; the hb carries VmRSS. Logs are counts only (no signature, mint or wallet is logged).

LOSS IS MARKED. A decode error, a write error (OSError) and an exception while handling a notice each become a data_loss gap row (kind decode_error /
write_error / consume_error; a burst is one coalesced record, written when the writer works again). A new process writes a process_restart gap for the
window since the previous run last wrote (a kill leaves no stop row). Every gap row carries a span [from_ms, to_ms]; see tools/arena_tape_schema.md.
EXIT CODES. 0 clean stop; 2 bad arguments; 3 another instance holds the lock; 4 fatal: writes failing for 120 s, or a core task ended while running.

Run: python -m tools.arena_feed --out-dir /data/mal/arena/tape   (MiScusi job; see the PR body for the command)
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_feed.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import collections
import fcntl
import hashlib
import json
import logging
import os
import shutil
import queue
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from observe.link_state import SILENT_MS_DEFAULT
from observe.trade_decode import (
    _TRADE_DISC,
    PUMP_BONDING_PROGRAM,
    PUMPSWAP_PROGRAM,
    _program_data_bytes,
    b58encode,
    decode_program_data,
    records_from_logs,
)
from observe.trade_source import DEFAULT_PUBLIC_WS, MultiSocketLogsSource, RawNotice
from tools.h5_shadow import DEFAULT_RPC, GAP_SLOTS, SILENCE_S, clean, code_sha, common_down, feed_snapshot, redact_argv, write_status
from tools.arena_zst import compact_file, zstd_path
from tools.pump_history_backfill import fetch_pool_mints, lifecycle_from_logs

log = logging.getLogger("mal.arena_feed")

SCHEMA = "arena_tape_v1"
STATUS_SCHEMA = "arena_feed_status_v1"
DEFAULT_OUT_DIR = "/data/mal/arena/tape"
PROG_PROGRAM = {"pumpswap": PUMPSWAP_PROGRAM, "pumpfun": PUMP_BONDING_PROGRAM}
VENUE_PROG = {"pumpswap": "pumpswap", "pump_bonding": "pumpfun"}
STATUS_NAME = "arena-feed-status.json"
LOCK_NAME = ".arena-feed.lock"
ERRORS_NAME = "arena-feed-errors.log"
LOCK_HELD_EXIT = 3

HOUSEKEEP_S = 5.0
HB_S = 60.0
LOOK_MS = 5_000
RECENT_MARGIN_MS = 5_000
DEDUP_WINDOW_S = 120.0  # per-program socket-merge window (a socket far behind its peer is a gap anyway); tools/arena_feed_v2 reads it too
DEDUP_MAX = 200_000
# Feed-level duplicate filter (cross-program AND late re-deliveries, class LateSeen). Window: the arena tape carried re-delivered, trade-identical rows
# that arrived after the old 120 s window (QP-RULING-1031-tape-dupes-reads; ops/reports/2026-10-31/tape-dupes.md); 900 s is the QP floor (the engine
# comment measured re-deliveries up to 898 slots, about 6 min). Cap: 2 x the measured peak combined notice rate (1,404.617/s PumpSwap + 396.858/s
# pump.fun) x the window = 3,242,655 keys, rounded up: no key is pushed out by the cap before its window ends at twice the measured peak.
LATE_DEDUP_WINDOW_S = 900.0
LATE_DEDUP_MAX = 3_300_000
QUEUE_MAX = 50_000
ERRORS_MAX_BYTES = 2_000_000
POOL_MINTS_MAX = 50_000
NAME_MAX, SYMBOL_MAX = 32, 16
DISK_CHECK_S = 60.0  # free space is measured at least this often, and before every hour file opens
MIN_FREE_PCT_DEFAULT = 25.0  # DEC-031 rev 2 s3.8: walk 2 keeps its room; no arena write may take the filesystem's free space below this
LOSS_FLUSH_MS = 1_000  # a burst of decode / write errors is coalesced into one gap record once it has been quiet this long
WRITE_FAIL_EXIT_MS = 120_000  # writes failing for this long without one success: stop the process with FATAL_EXIT so the supervisor restarts it
FATAL_EXIT = 4
RESUME_MARGIN_PCT = 2.0  # hysteresis: after a pause the feed resumes only at the reserve plus this (the feed and the walker both write, so free space hovers)
COMPACT_GRACE_MS = 120_000  # a closed hour is compacted this long after its end (a row can reach the writer a little late)
COMPACT_SCAN_S = 30.0
COMPACT_TRIES = 3
RESOLVE_S = 5.0  # pool resolver: one getMultipleAccounts (public RPC) per this many seconds, up to RESOLVE_BATCH pools
RESOLVE_BATCH = 100
RESOLVE_TRIES = 3
UNRESOLVED_MAX = 20_000

# keys of observe.trade_decode.seal_trade rows that the tape does not carry (constant, derivable or misleading without V; see the schema)
TRADE_DROP = frozenset({"v", "type", "feed", "commitment", "quote_is_wsol", "market_cap_supply_ui", "t_recv", "sol", "token", "price_sol",
                        "market_cap_sol", "slot", "signature", "event_index", "t_recv_ms"})


def now_ms() -> int:
    return time.time_ns() // 1_000_000


# ---- URL / path guards ----------------------------------------------------------------------------------------
def check_ws_url(url: str) -> str:
    """Public websocket only: wss, a host that is not Helius, no userinfo, no query, no path (a key can sit in any of them)."""
    try:
        u = urlsplit(url)
    except ValueError as exc:
        raise ValueError("--ws-url is not a URL") from exc
    host = (u.hostname or "").lower()
    if u.scheme != "wss" or not host:
        raise ValueError("--ws-url must be a wss:// URL")
    if "helius" in host or "helius" in url.lower():
        raise ValueError("--ws-url: Helius URLs are refused (the arena feed spends 0 credits)")
    if u.query or u.username or u.password or u.path not in ("", "/") or u.fragment:
        raise ValueError("--ws-url must be a plain public endpoint (no query string, credentials or path)")
    return url


def check_rpc_url(url: str) -> str:
    """The pool resolver's HTTPS RPC, with the same rules as --ws-url (stricter than tools.h5_shadow.check_rpc_url, which allows a path and looks for
    'helius' in the host only): https, no 'helius' anywhere in the URL, no userinfo, query, fragment or path (a key can sit in any of them)."""
    try:
        u = urlsplit(url)
    except ValueError as exc:
        raise ValueError("--rpc-url is not a URL") from exc
    if u.scheme != "https" or not u.hostname:
        raise ValueError("--rpc-url must be an https:// URL")
    if "helius" in url.lower():
        raise ValueError("--rpc-url: Helius URLs are refused (the arena feed spends 0 credits)")
    if u.query or u.username or u.password or u.path not in ("", "/") or u.fragment:
        raise ValueError("--rpc-url must be a plain public endpoint (no query string, credentials or path)")
    return url


def check_out_dir(path: str) -> Path:
    if ".." in Path(path).parts:
        raise ValueError(f"out dir {path!r} contains '..'")
    return Path(path)


class LockHeld(Exception):
    pass


def acquire_lock(out_dir: Path) -> int:
    """Exclusive non-blocking flock on <out_dir>/.arena-feed.lock; returns the held fd. Raises LockHeld when another instance has it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(out_dir / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise LockHeld(f"another arena_feed holds {out_dir / LOCK_NAME}") from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


# ---- duplicate filter -------------------------------------------------------------------------------------------
_FP_MASK = (1 << 64) - 1
_KEY_BYTES = 16  # blake2b digest of the signature: a false drop needs a 128-bit collision; the 88-char str costs ~1.3x the memory


class LateSeen:
    """Feed-level duplicate filter by transaction signature, built for re-deliveries that arrive minutes late.

    Same contract as observe.trade_source.SeenSet (add(key, fingerprint) is True when the key is new, False for a duplicate; last_mismatch says the
    duplicate's fingerprint differs from the first sight's) with three differences:
      * a key is remembered window_s after its FIRST sight (insertion order is time order, so expiry is a popleft loop; a duplicate does not refresh it) and
        at most max_keys keys are kept, the oldest first sight going first. Memory is bounded at any rate: max_keys x about 200 bytes;
      * the key is a 16-byte blake2b digest of the signature, and the value is ONE int (first-sight ms << 64 | 64-bit fingerprint);
      * it counts what it did (snapshot): dropped, dropped_late (first seen at least short_window_s ago: rows the feed wrote twice before this class),
        mismatched (same signature, other fingerprint: the first sight is kept), evicted_young (pushed out by the cap while younger than window_s: a
        re-delivery of that key would be written again), high_water (most keys held at once).
    Residual, by construction: a re-delivery later than window_s after the first sight, one of an evicted_young key, or one after a process restart
    (the set is in memory) is still written. Single-threaded: the asyncio consumer is the only caller."""

    def __init__(self, window_s: float = LATE_DEDUP_WINDOW_S, max_keys: int = LATE_DEDUP_MAX, clock: Callable[[], float] = time.monotonic, *,
                 short_window_s: float = DEDUP_WINDOW_S) -> None:
        if window_s <= 0 or max_keys < 1:
            raise ValueError("window_s must be > 0 and max_keys >= 1")
        self.window_s, self.max_keys, self.short_window_s = float(window_s), int(max_keys), float(short_window_s)
        self._window_ms, self._short_ms, self._clock = round(window_s * 1000), round(short_window_s * 1000), clock
        self._first: collections.OrderedDict[bytes, int] = collections.OrderedDict()
        self.dropped = self.dropped_late = self.mismatched = self.evicted_young = self.high_water = 0
        self.last_late = self.last_mismatch = False

    def __len__(self) -> int:
        return len(self._first)

    def _expire(self, now_ms: int) -> None:
        cutoff, first = now_ms - self._window_ms, self._first
        while first:
            key = next(iter(first))
            if (first[key] >> 64) > cutoff:  # a key expires window_s after its first sight (the boundary instant is expired, as SeenSet does)
                break
            del first[key]

    def add(self, key: str, fingerprint: Any = None) -> bool:
        """True when key is new (and is now remembered), False for a duplicate. On a duplicate, last_late says its first sight is at least
        short_window_s old and last_mismatch says its fingerprint differs from the first sight's."""
        now = round(self._clock() * 1000)
        self._expire(now)
        digest = hashlib.blake2b(str(key).encode("utf-8", "replace"), digest_size=_KEY_BYTES).digest()
        fp = hash(fingerprint) & _FP_MASK
        first = self._first
        packed = first.get(digest)
        self.last_late = self.last_mismatch = False
        if packed is not None:
            self.dropped += 1
            self.last_late = now - (packed >> 64) >= self._short_ms
            self.last_mismatch = (packed & _FP_MASK) != fp
            self.dropped_late += self.last_late
            self.mismatched += self.last_mismatch
            return False
        first[digest] = (now << 64) | fp
        while len(first) > self.max_keys:
            _old, old_packed = first.popitem(last=False)
            if now - (old_packed >> 64) < self._window_ms:
                self.evicted_young += 1
        self.high_water = max(self.high_water, len(first))
        return True

    def snapshot(self) -> dict[str, Any]:
        return {"window_s": self.window_s, "max_keys": self.max_keys, "keys": len(self._first), "high_water": self.high_water, "dropped": self.dropped,
                "dropped_late": self.dropped_late, "mismatched": self.mismatched, "evicted_young": self.evicted_young}


# ---- decode ---------------------------------------------------------------------------------------------------
def _u64(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off:off + 8], "little")


def _bonding_extras(raw: bytes) -> dict[str, Any]:
    """Fields of a pump.fun TradeEvent blob that observe.trade_decode does not return. Offsets count the 8-byte discriminator:
    real_sol_reserves 113, real_token_reserves 121, fee_bps 161, fee 169, creator 177, creator_fee_bps 209, creator_fee 217."""
    if len(raw) < 225:
        return {}
    return {"real_quote_reserve": _u64(raw, 113), "real_base_reserve": _u64(raw, 121), "protocol_fee_bps": _u64(raw, 161),
            "protocol_fee": _u64(raw, 169), "creator": b58encode(raw[177:209]), "creator_fee_bps": _u64(raw, 209), "creator_fee": _u64(raw, 217)}


def _row(type_: str, prog: str, slot: int, t_recv_ms: int, signature: str | None, event_index: int | None, body: dict[str, Any]) -> dict[str, Any]:
    return {"v": 1, "type": type_, "prog": prog, "slot": slot, "t_recv_ms": t_recv_ms, "signature": signature, "event_index": event_index, **body}


def rows_from_notice(note: RawNotice, pool_mints: dict[str, tuple[str, str]], counters: collections.Counter | None = None) -> list[dict[str, Any]]:
    """Every tape row of one transaction notice: pool_create, create, trade, complete (in that order). Failed txs give none."""
    cnt = counters if counters is not None else collections.Counter()
    if note.failed or not any("Program data: " in ln for ln in note.logs):
        return []
    slot, t, sig = note.slot, note.t_recv_ms, note.signature
    out: list[dict[str, Any]] = []
    bonding_blobs: list[bytes] = []
    pc = 0
    for ln in note.logs:
        if "Program data: " not in ln:
            continue
        raw = _program_data_bytes(ln)
        if raw is None:
            continue
        ev = decode_program_data(raw)
        if ev is None:
            continue
        if ev["kind"] == "create_pool":
            out.append(_row("pool_create", "pumpswap", slot, t, sig, pc, {"pool": ev["pool"], "mint": ev["base_mint"], "quote_mint": ev["quote_mint"]}))
            pc += 1
        elif raw[:8] == _TRADE_DISC and ev.get("venue") == "pump_bonding":
            bonding_blobs.append(raw)
    creates, lifecycle = lifecycle_from_logs(note.logs)
    for i, c in enumerate(creates):
        body = {k: c[k] for k in ("mint", "trader", "creator", "event_ts", "quote_reserve", "base_reserve", "real_token_reserves", "token_raw",
                                  "quote_mint", "is_mayhem_mode")}
        body["name"] = str(c.get("name") or "")[:NAME_MAX]
        body["symbol"] = str(c.get("symbol") or "")[:SYMBOL_MAX]
        out.append(_row("create", "pumpfun", slot, t, sig, i, body))
    trades = records_from_logs(note.logs, slot=slot, signature=sig, t_recv_ms=t, commitment=note.commitment, feed=note.feed, pool_mints=pool_mints,
                               event_v=True)
    bonding_rows = [r for r in trades if r.get("venue") == "pump_bonding"]
    extras = [_bonding_extras(b) for b in bonding_blobs] if len(bonding_blobs) == len(bonding_rows) else [{} for _ in bonding_rows]
    if len(bonding_blobs) != len(bonding_rows):
        cnt["bonding_extras_mismatch"] += 1
    ex_iter = iter(extras)
    for r in trades:
        prog = VENUE_PROG.get(str(r.get("venue")))
        if prog is None:
            continue
        body = {k: v for k, v in r.items() if k not in TRADE_DROP}
        if prog == "pumpfun":
            body.update(next(ex_iter, {}))
        out.append(_row("trade", prog, slot, t, sig, int(r["event_index"]), body))
    ci = 0
    for e in lifecycle:
        if e.get("type") == "complete":
            out.append(_row("complete", "pumpfun", slot, t, sig, ci,
                            {k: e[k] for k in ("trader", "mint", "bonding_curve", "event_ts", "quote_mint")}))
            ci += 1
        else:
            cnt["migration_events_not_written"] += 1
    return out


# ---- output ---------------------------------------------------------------------------------------------------
def hour_start_ms(name: str) -> int | None:
    """UTC start (ms) of the hour in a tape file name (tape-<YYYY-MM-DDTHH>.jsonl[.zst]); None for any other name."""
    if not name.startswith("tape-"):
        return None
    try:
        return int(datetime.strptime(name[5:18], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


TAIL_BYTES = 256 * 1024


def read_previous_run(out_dir: Path) -> dict[str, Any] | None:
    """What the previous process left behind, for the process_restart gap: {"last_ms", "clean_stop", "torn_tail", "prev_pid"}, or None on a first run.
    last_ms is the larger of the newest .jsonl's last parseable row time and the status file's updated_ms. A kill (OOM, MiScusi cancel, crash) leaves no
    stop row, so a missing stop row is the normal sign of an unclean exit; either way the time until this start is a window with no feed."""
    last_ms: int | None = None
    clean_stop, torn, prev_pid = False, False, None
    files = sorted(out_dir.glob("tape-*.jsonl"))
    if files:
        p = files[-1]
        try:
            size = p.stat().st_size
            with open(p, "rb") as fh:
                fh.seek(max(0, size - TAIL_BYTES))
                data = fh.read()
            torn = size > 0 and not data.endswith(b"\n")
            lines = data.split(b"\n")
            if size > TAIL_BYTES:
                lines = lines[1:]  # the first piece of a tail read starts mid-line
            for ln in reversed(lines):
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("t_recv_ms"), int):
                    last_ms, clean_stop = r["t_recv_ms"], r.get("type") == "stop"
                    break
        except OSError:
            pass
    try:
        st = json.loads((out_dir / STATUS_NAME).read_text())
        prev_pid = st.get("pid")
        if isinstance(st.get("updated_ms"), int) and (last_ms is None or st["updated_ms"] > last_ms):
            last_ms, clean_stop = st["updated_ms"], False
    except (OSError, ValueError):
        pass
    if last_ms is None:
        return None
    return {"last_ms": last_ms, "clean_stop": clean_stop, "torn_tail": torn, "prev_pid": prev_pid}


class Compactor:
    """Background thread: compress each CLOSED hour (tape-<hour>.jsonl -> .jsonl.zst) and delete the source after it verifies (tools.arena_zst).
    Ingest never waits for it. A leftover *.zst.tmp from a killed run is removed at start; closed hours left by an earlier run are picked up by
    the first scan. A hour is closed once its end plus COMPACT_GRACE_MS is past (the wall clock) and the writer does not hold it."""

    def __init__(self, out_dir: Path, wall: Callable[[], int] = now_ms, *, grace_ms: int = COMPACT_GRACE_MS, compact: Callable[[Path], dict] = compact_file,
                 allow: Callable[[int], bool] | None = None) -> None:
        self.dir, self.wall, self.grace_ms, self._compact = out_dir, wall, grace_ms, compact
        self.allow = allow  # allow(extra_bytes) False = writing the .zst could take the disk below the reserve: skip this scan, retry at the next
        self.q: queue.Queue = queue.Queue()
        self.queued: set[str] = set()
        self.tries: dict[str, int] = {}
        self.lock = threading.Lock()
        self.stats: dict[str, Any] = {"enabled": True, "files": 0, "failed": 0, "skipped_disk": 0, "bytes_in": 0, "bytes_out": 0, "last_ratio": None, "last_error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_scan = float("-inf")

    def start(self) -> None:
        for p in self.dir.glob("tape-*.jsonl.zst.tmp"):
            p.unlink(missing_ok=True)
        self._thread = threading.Thread(target=self._run, name="arena-compactor", daemon=True)
        self._thread.start()

    def eligible(self, now: int, current_name: str | None) -> list[Path]:
        out = []
        for p in sorted(self.dir.glob("tape-*.jsonl")):
            h = hour_start_ms(p.name)
            if h is None or p.name == current_name or p.name in self.queued or self.tries.get(p.name, 0) >= COMPACT_TRIES:
                continue
            if h + 3_600_000 + self.grace_ms <= now:
                out.append(p)
        return out

    def scan(self, now: int, current_name: str | None, *, force: bool = False) -> int:
        if not force and time.monotonic() - self._last_scan < COMPACT_SCAN_S:
            return 0
        self._last_scan = time.monotonic()
        found = self.eligible(now, current_name)
        for p in found:
            self.queued.add(p.name)
            self.q.put(p)
        return len(found)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                p = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if self.allow is not None and not self.allow(p.stat().st_size // 2):  # the .zst is about a fifth of the source; half is the margin
                    with self.lock:
                        self.stats["skipped_disk"] += 1
                    continue
                info = self._compact(p)
                with self.lock:
                    self.stats["files"] += 1
                    self.stats["bytes_in"] += info["bytes_in"]
                    self.stats["bytes_out"] += info["bytes_out"]
                    self.stats["last_ratio"] = info["ratio"]
            except Exception as exc:  # noqa: BLE001 - the source is kept; retried up to COMPACT_TRIES scans
                self.tries[p.name] = self.tries.get(p.name, 0) + 1
                with self.lock:
                    self.stats["failed"] += 1
                    self.stats["last_error"] = type(exc).__name__
                log.warning("compact failed class=%s", type(exc).__name__)
            finally:
                self.queued.discard(p.name)

    def drain(self, timeout: float = 120.0) -> None:
        """Wait until the queue is empty and idle (tests, shutdown)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and (not self.q.empty() or self.queued):
            time.sleep(0.05)

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            s = dict(self.stats)
        s["queued"] = len(self.queued)
        return s


class TapeWriter:
    """Hourly tape-<UTC hour of t_recv_ms>.jsonl, strict JSON, flushed per line. Counts rows by (prog, type)."""

    def __init__(self, out_dir: Path, clock: Callable[[], int] | None = None) -> None:
        self.dir = out_dir
        self.clock = clock  # wall clock (ms) for the closed-hour rule; None = the newest row time
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._name: str | None = None
        self.before_open: Callable[[], bool] | None = None  # set by the Feed: False = below the disk reserve, do not open the file
        self.latest_t = 0
        self.late_to_current = 0
        self.torn_lines = 0
        self.lines = 0
        self.paused = False
        self.dropped = 0
        self.by_prog_type: collections.Counter = collections.Counter()
        self.minute: dict[str, collections.deque] = {p: collections.deque(maxlen=12) for p in PROG_PROGRAM}

    @staticmethod
    def name_for(t_ms: int) -> str:
        return "tape-" + datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"

    @property
    def current_name(self) -> str | None:
        return self._name

    def release_stale(self, now_ms_: int, grace_ms: int = COMPACT_GRACE_MS) -> None:
        """Close the open file when its hour ended more than grace_ms ago (no row has needed it since), so the compactor may replace it."""
        if self._fh and self._name and hour_start_ms(self._name) is not None and hour_start_ms(self._name) + 3_600_000 + grace_ms <= now_ms_:
            self._fh.close()
            self._fh, self._name = None, None

    def write(self, rec: dict[str, Any], *, resume: bool = False) -> bool:
        """Append one row; True when it was written. False = dropped: the writer is paused below the disk reserve, or the pre-open check refused.
        resume=True is for the single disk_reserve gap record the Feed writes right after it measured that space allows again: it skips the pause
        and the pre-open check (the Feed has just done both), so the record cannot be dropped by a second reading."""
        if self.paused and not resume:  # below the disk reserve: no row of any type is written (the Feed writes one gap record when space allows again)
            self.dropped += 1
            return False
        t = int(rec["t_recv_ms"])
        self.latest_t = max(self.latest_t, t)
        name = self.name_for(t)
        now = self.clock() if self.clock is not None else self.latest_t
        cur = self.name_for(max(self.latest_t, now))
        hs = hour_start_ms(name)
        closed = hs is not None and hs + 3_600_000 + COMPACT_GRACE_MS <= now  # the compactor may take this hour at any moment from here on
        if name != cur and (closed or (self.dir / (name + ".zst")).exists()):
            name = cur  # a row for an hour that is closed (compacted or about to be) goes to the current hour's file: the compactor never races a writer
            self.late_to_current += 1
        if self._name != name:
            if not resume and self.before_open is not None and not self.before_open():  # the disk-reserve check, before an hour file is created or reopened
                self.dropped += 1
                return False
            if self._fh:
                self._fh.close()
            torn = False
            try:  # a killed process can leave a half-written last line: end it, so the next row does not fuse with it into one bad line
                p = self.dir / name
                if p.stat().st_size > 0:
                    with open(p, "rb") as fh:
                        fh.seek(-1, os.SEEK_END)
                        torn = fh.read(1) != b"\n"
            except OSError:
                pass
            self._fh = open(self.dir / name, "a", encoding="utf-8")
            self._name = name
            if torn:
                self._fh.write("\n")
                self.torn_lines += 1
        self._fh.write(json.dumps(clean(rec), separators=(",", ":"), allow_nan=False) + "\n")
        self._fh.flush()
        self.lines += 1
        prog = rec.get("prog")
        self.by_prog_type[(prog, rec["type"])] += 1
        if rec["type"] in ("trade", "create", "complete", "pool_create") and prog in self.minute:
            m = int(rec["t_recv_ms"]) // 60_000
            dq = self.minute[prog]
            if dq and dq[-1][0] == m:
                dq[-1][1] += 1
            else:
                dq.append([m, 1])
        return True

    def rows_last_minute(self, prog: str, now: int) -> int:
        """Data rows of `prog` in the last COMPLETED minute before `now`."""
        m = now // 60_000 - 1
        return next((n for mm, n in reversed(self.minute[prog]) if mm == m), 0)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class ErrorLog:
    """Exception class, message[:200] and traceback of decode / write failures. Capped. It is an arena write like any other: while the disk reserve
    pauses the writer (`paused()` true) nothing is logged, and a failing disk never raises out of it."""

    def __init__(self, path: Path, max_bytes: int = ERRORS_MAX_BYTES, paused: Callable[[], bool] | None = None) -> None:
        self.path, self.max_bytes, self.bytes, self.dropped, self.paused = path, max_bytes, 0, 0, paused

    def log(self, exc: BaseException, ctx: dict | None = None) -> None:
        if self.bytes >= self.max_bytes or (self.paused is not None and self.paused()):
            self.dropped += 1
            return
        head = json.dumps(clean({"t_ms": now_ms(), "exc": type(exc).__name__, "msg": str(exc)[:200], **(ctx or {})}), allow_nan=False)
        text = head + "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            self.dropped += 1
            return
        self.bytes += len(text)


def proc_rss_mb(path: str = "/proc/self/status") -> float | None:
    try:
        for ln in Path(path).read_text().splitlines():
            if ln.startswith("VmRSS:"):
                return round(int(ln.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


# ---- gap rules (per program) ----------------------------------------------------------------------------------
class ProgWatch:
    """Slot-jump, silence and reconnect gaps of one program's merged socket feed (tools/h5_shadow's rules)."""

    def __init__(self, prog: str, started_ms: int) -> None:
        self.prog = prog
        self.hw_slot: int | None = None
        self.last_ms = started_ms  # the start counts as the last sign of life: a feed that never delivers is flagged after SILENCE_S
        self.silence_open = False
        self.silence_from: int | None = None
        self.restart_from: int | None = None
        self.hw_ms: int | None = None  # t_recv_ms of the notice that set hw_slot
        self.gaps = 0
        self.notices = 0
        self.prev: dict | None = None
        self.flagged_starts: collections.deque = collections.deque(maxlen=256)
        self.looks: collections.deque = collections.deque(maxlen=3)

    def _gap(self, kind: str, at_ms: int, data_loss: bool, **kw: Any) -> dict[str, Any]:
        self.gaps += 1
        return _row("gap", self.prog, self.hw_slot if self.hw_slot is not None else 0, at_ms, None, None, {"kind": kind, "data_loss": data_loss, **kw})

    def on_notice(self, slot: int, recv_ms: int) -> list[dict[str, Any]]:
        """Every gap row has an explicit span [from_ms, to_ms] in t_recv_ms. A silence or a feed restart is written TWICE: open at detection
        (closed false, to_ms = detection time) and closed when the feed delivers again (closed true, to_ms = that notice's t_recv_ms)."""
        out: list[dict[str, Any]] = []
        self.notices += 1
        if self.silence_open and self.silence_from is not None:
            out.append(self._gap("silence", recv_ms, True, from_ms=self.silence_from, to_ms=recv_ms, closed=True, silent_ms=recv_ms - self.silence_from))
        if self.restart_from is not None:
            out.append(self._gap("feed_restart", recv_ms, True, from_ms=self.restart_from, to_ms=recv_ms, closed=True, reason="first_notice_after_restart"))
            self.restart_from = None
        self.silence_open, self.silence_from = False, None
        self.last_ms = recv_ms
        if self.hw_slot is not None and slot > self.hw_slot + GAP_SLOTS:
            out.append(self._gap("slot_jump", recv_ms, True, from_ms=self.hw_ms, to_ms=recv_ms, from_slot=self.hw_slot, to_slot=slot,
                                 missed_slots=slot - self.hw_slot - 1))
        if self.hw_slot is None or slot > self.hw_slot:
            self.hw_slot, self.hw_ms = slot, recv_ms
        return out

    def tick(self, now: int) -> list[dict[str, Any]]:
        if not self.silence_open and now - self.last_ms > SILENCE_S * 1000:
            self.silence_open, self.silence_from = True, self.last_ms
            return [self._gap("silence", now, True, from_ms=self.last_ms, to_ms=now, closed=False, silent_ms=now - self.last_ms)]
        return []

    def feed_restart(self, now: int, reason: str) -> dict[str, Any]:
        """The source was rebuilt. The outage runs from the last notice to the first notice of the new source (a closed record follows then)."""
        self.restart_from = self.last_ms if self.restart_from is None else self.restart_from
        rec = self._gap("feed_restart", now, True, from_ms=self.restart_from, to_ms=now, closed=False, reason=reason)
        self.hw_slot = None
        self.prev = None
        return rec

    def note_feed_stats(self, snap: dict, now: int) -> list[dict[str, Any]]:
        prev = self.prev or {"reconnects": 0, "closes": {}, "rejections": {}, "errors": {}, "per_socket": []}
        self.prev = snap
        delta = snap["reconnects"] - prev["reconnects"]
        states = snap.get("socket_states")
        pieces: list[tuple[int, int]] = []
        if states:
            sm = max([int(st.get("silent_ms") or 0) for st in states] + [SILENT_MS_DEFAULT])
            recent = now - (sm + 2 * LOOK_MS + RECENT_MARGIN_MS)
            pieces = [(a, b) for a, b in common_down(states, now, min_end_ms=recent) if a not in self.flagged_starts]
            self.flagged_starts.extend(a for a, _ in pieces)
        n = int(snap.get("sockets") or 1)
        ps_prev = prev.get("per_socket") or [0] * n
        per_socket = [c - (ps_prev[i] if i < len(ps_prev) else 0) for i, c in enumerate(snap.get("per_socket") or [])]
        self.looks.append(per_socket)  # every look is remembered, with or without a reconnect
        if delta <= 0 and not pieces:
            return []
        # With per-socket link state the common outage is exact; without it, the sockets that reconnected over the last 3 looks decide.
        recent_down = {i for look in self.looks for i, d in enumerate(look) if d > 0}
        data_loss = bool(pieces) if states else (n == 1 or len(recent_down) >= n)

        def dd(a: dict, b: dict) -> dict:
            return {k: v - b.get(k, 0) for k, v in a.items() if v > b.get(k, 0)}

        span = (min(a for a, _ in pieces), max(b for _, b in pieces)) if pieces else (now, now)  # the common outage when link state knows it
        return [self._gap("socket_reconnect", now, data_loss, from_ms=span[0], to_ms=span[1], delta_reconnects=delta, reconnects_total=snap["reconnects"], sockets=n,
                          per_socket_delta=per_socket, delta_closes=dd(snap["closes"], prev["closes"]),
                          delta_rejections=dd(snap["rejections"], prev["rejections"]), delta_errors=dd(snap["errors"], prev["errors"]),
                          common_down_ms=sum(b - a for a, b in pieces))]


# ---- live runner ----------------------------------------------------------------------------------------------
def build_source(prog: str, ws_urls: Sequence[str], sockets: int, commitment: str, stagger_s: float = 3.0) -> MultiSocketLogsSource:
    return MultiSocketLogsSource(ws_urls=list(ws_urls) or [DEFAULT_PUBLIC_WS], sockets=sockets, programs=(PROG_PROGRAM[prog],),
                                 commitment=commitment, stagger_s=stagger_s, seen_window_s=DEDUP_WINDOW_S, max_seen=DEDUP_MAX)


class Feed:
    """The arena feed engine: sources in, tape rows out. One instance per process."""

    def __init__(self, out_dir: Path, ws_urls: Sequence[str], sockets: dict[str, int], commitment: str = "confirmed", *, min_free_pct: float = MIN_FREE_PCT_DEFAULT,
                 stagger_s: float = 3.0, wall: Callable[[], int] = now_ms, disk_usage: Callable[[Path], Any] | None = None,
                 rpc_url: str | None = DEFAULT_RPC, fetch: Callable[[str, list[str]], dict] = fetch_pool_mints, compress: bool = True,
                 compact: Callable[[Path], dict] = compact_file, dedup_window_s: float = LATE_DEDUP_WINDOW_S, dedup_max: int = LATE_DEDUP_MAX,
                 dedup_clock: Callable[[], float] = time.monotonic) -> None:
        if compress and zstd_path() is None:
            raise ValueError("the zstd CLI is not on PATH: the closed hours cannot be compacted (pass --no-compress to run without)")
        self.compress = compress
        self.rpc_url, self.fetch = (check_rpc_url(rpc_url) if rpc_url else None), fetch  # rpc_url None = pool resolver off
        self.unresolved: collections.OrderedDict[str, list] = collections.OrderedDict()  # pool -> [first_ms, tries]
        self.out_dir, self.ws_urls, self.sockets, self.commitment = out_dir, [check_ws_url(u) for u in ws_urls], dict(sockets), commitment
        self.min_free_pct, self.stagger_s, self.wall = min_free_pct, stagger_s, wall
        self.disk_usage = disk_usage or (lambda p: shutil.disk_usage(p))  # looked up at call time: the walker's own measure
        self.paused_since_ms: int | None = None
        self.pause_dropped0 = 0
        self.pauses = 0
        self.writer = TapeWriter(out_dir, clock=wall)
        self.writer.before_open = lambda: self.check_disk(self.wall(), force=True)
        self.compactor = Compactor(out_dir, wall, compact=compact, allow=self.disk_allows)
        self.errlog = ErrorLog(out_dir / ERRORS_NAME, paused=lambda: self.writer.paused)
        self.started_ms = wall()
        self.watch = {p: ProgWatch(p, self.started_ms) for p in PROG_PROGRAM}
        self.sources: dict[str, MultiSocketLogsSource] = {}
        self.seen = LateSeen(dedup_window_s, dedup_max, dedup_clock)  # cross-program and late re-delivery filter (the sources keep their own 120 s socket merge)
        self.pool_mints: dict[str, tuple[str, str]] = {}
        self.counters: collections.Counter = collections.Counter()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.stop = asyncio.Event()
        self.last_hb = 0.0
        self.last_disk = 0.0
        self.disk_free_pct: float | None = None
        self.pending: dict[tuple[str, str], dict[str, Any]] = {}  # (prog, kind) -> a span of lost rows not yet written as a data_loss gap record
        self.write_fail_since_ms: int | None = None
        self.fatal: str | None = None  # set when the process must exit non-zero (run_live returns FATAL_EXIT)

    # -- losses: nothing is dropped without a data_loss gap row
    def note_loss(self, prog: str, kind: str, ms: int, slot: int | None, n: int = 1, **extra: Any) -> None:
        """Record n lost rows at time ms. Losses of one (prog, kind) coalesce into one span: count, from_ms, to_ms, from_slot, to_slot."""
        e = self.pending.get((prog, kind))
        if e is None:
            e = self.pending[(prog, kind)] = {"count": 0, "from_ms": ms, "to_ms": ms, "from_slot": slot, "to_slot": slot, **extra}
        e["count"] += n
        e["from_ms"], e["to_ms"] = min(e["from_ms"], ms), max(e["to_ms"], ms)
        if slot is not None:
            e["from_slot"] = slot if e["from_slot"] is None else min(e["from_slot"], slot)
            e["to_slot"] = slot if e["to_slot"] is None else max(e["to_slot"], slot)

    def emit_safe(self, rec: dict[str, Any]) -> bool:
        """emit() that never raises. A row the writer refuses because of the disk reserve is covered by the disk_reserve record. A row lost to a
        write error is counted, becomes a pending write_error loss (a data_loss gap row once the writer works again), and starts the clock that ends
        the process after WRITE_FAIL_EXIT_MS of failed writes."""
        try:
            ok = self.emit(rec)
        except Exception as exc:  # noqa: BLE001 - the feed must outlive any one write
            self.counters["write_errors"] += 1
            self.errlog.log(exc, {"where": "write", "slot": rec.get("slot")})
            if self.write_fail_since_ms is None:
                self.write_fail_since_ms = self.wall()
            if rec.get("type") != "hb":
                self.note_loss(str(rec.get("prog") or "all"), "write_error", int(rec["t_recv_ms"]), rec.get("slot"))
            return False
        if ok:
            self.write_fail_since_ms = None
        return ok

    def flush_pending(self, now: int) -> int:
        """Write the pending loss spans as data_loss gap records (a span that has been quiet for LOSS_FLUSH_MS, or all of them at shutdown).
        A record the writer cannot take (failing disk, or paused below the reserve) stays pending. Returns how many were written."""
        n = 0
        for key in list(self.pending):
            e = self.pending[key]
            if not self.stop.is_set() and key[1] != "process_restart" and now - e["to_ms"] < LOSS_FLUSH_MS:
                continue
            rec = _row("gap", key[0], e.get("to_slot") or 0, now, None, None, {"kind": key[1], "data_loss": True, **e})
            try:
                ok = self.writer.write(rec)
            except Exception as exc:  # noqa: BLE001
                self.counters["write_errors"] += 1
                self.errlog.log(exc, {"where": "flush_pending"})
                if self.write_fail_since_ms is None:
                    self.write_fail_since_ms = now
                continue
            if ok:
                del self.pending[key]
                self.write_fail_since_ms = None
                n += 1
        return n

    # -- rows
    def emit(self, rec: dict[str, Any]) -> bool:
        return self.writer.write(rec)

    def meta_row(self, type_: str, **body: Any) -> dict[str, Any]:
        return _row(type_, "all", 0, self.wall(), None, None, body)

    def handle_notice(self, prog: str, note: RawNotice) -> int:
        """One notice from `prog`'s feed: gap watch first (every notice counts as sign of life), then decode once per signature.
        ORDER (the arena engine assumes it): any gap this notice reveals is written BEFORE the notice's own rows, so on the tape a gap row always precedes
        the first trade row after it. A socket reconnect is therefore checked here, on the first notice after it, not only by the 5 s housekeeping tick."""
        w, src = self.watch[prog], self.sources.get(prog)
        if src is not None and src.stats.reconnects != (w.prev["reconnects"] if w.prev else 0):
            snap = feed_snapshot(src)
            if snap is not None:
                for g in w.note_feed_stats(snap, note.t_recv_ms):
                    self.emit_safe(g)
        for g in w.on_notice(note.slot, note.t_recv_ms):
            self.emit_safe(g)
        if not self.seen.add(note.signature, (note.failed, len(note.logs), note.slot)):
            self.counters["cross_dedup_dropped"] += 1
            if self.seen.last_late:  # first seen >= DEDUP_WINDOW_S ago: the feed used to write these rows a second time
                self.counters["late_dedup_dropped"] += 1
            if self.seen.last_mismatch:  # same signature, other (failed, logs, slot): the first sight is kept
                self.counters["dedup_content_mismatch"] += 1
            return 0
        try:
            rows = rows_from_notice(note, self.pool_mints, self.counters)
        except Exception as exc:  # noqa: BLE001 - one bad blob must not end the feed
            self.counters["decode_errors"] += 1
            self.errlog.log(exc, {"where": "decode", "slot": note.slot})
            self.note_loss(prog, "decode_error", note.t_recv_ms, note.slot)  # that transaction's rows are gone: a data_loss gap row says so
            return 0
        for r in rows:
            self.emit_safe(r)
            if self.rpc_url and r["prog"] == "pumpswap":
                if r["type"] == "trade" and not r.get("mint") and r.get("pool") not in self.unresolved:
                    self.unresolved[r["pool"]] = [r["t_recv_ms"], 0]
                    if len(self.unresolved) > UNRESOLVED_MAX:
                        self.unresolved.popitem(last=False)
                        self.counters["unresolved_overflow"] += 1
                elif r["type"] == "pool_create":
                    self.unresolved.pop(r["pool"], None)
        if len(self.pool_mints) > POOL_MINTS_MAX:
            for k in list(self.pool_mints)[:POOL_MINTS_MAX // 2]:
                del self.pool_mints[k]
        return len(rows)

    # -- housekeeping
    def tick(self, now: int) -> None:
        for prog, w in self.watch.items():
            for g in w.tick(now):
                self.emit_safe(g)
            src = self.sources.get(prog)
            snap = feed_snapshot(src) if src is not None else None
            if snap is not None:
                for g in w.note_feed_stats(snap, now):
                    self.emit_safe(g)
        if self.compress:
            self.writer.release_stale(now)
            self.compactor.scan(now, self.writer.current_name)
        self.check_disk(now)
        self.flush_pending(now)
        if self.write_fail_since_ms is not None and now - self.write_fail_since_ms >= WRITE_FAIL_EXIT_MS and self.fatal is None:
            self.fatal = "write_failing"  # no write has succeeded for WRITE_FAIL_EXIT_MS: exit non-zero, the supervisor restarts (a process_restart gap follows)
            self.stop.set()
        if time.monotonic() - self.last_hb >= HB_S or self.stop.is_set():
            self.last_hb = time.monotonic()
            self.heartbeat(now)

    def measure_free_pct(self) -> float | None:
        """Free space of the out-dir filesystem the way the walker measures it: shutil.disk_usage(path).free / total, in percent. None if unreadable."""
        try:
            du = self.disk_usage(self.out_dir)
            return 100.0 * du.free / du.total
        except (OSError, ZeroDivisionError):
            return None

    def disk_allows(self, extra_bytes: int = 0) -> bool:
        """True when writing extra_bytes more would still leave the free space at or above the reserve (the compactor asks before it writes a .zst)."""
        try:
            du = self.disk_usage(self.out_dir)
            return 100.0 * (du.free - extra_bytes) / du.total >= self.min_free_pct
        except (OSError, ZeroDivisionError):
            return False

    def check_disk(self, now: int, *, force: bool = False) -> bool:
        """The disk-reserve check (DEC-031 rev 2 s3.8). Returns True when rows may be written. Run at least every DISK_CHECK_S, and (force=True) by the
        writer before it opens an hour file. Below the reserve: writing stops and NOTHING is written, not even a gap row; only the status file carries
        a flag. When space allows again, ONE gap record (kind and reason disk_reserve) says how long and how many rows were dropped."""
        if not force and self.disk_free_pct is not None and time.monotonic() - self.last_disk < DISK_CHECK_S:
            return not self.writer.paused
        self.last_disk = time.monotonic()
        pct = self.measure_free_pct()
        if pct is None:  # cannot tell: keep the current state
            return not self.writer.paused
        self.disk_free_pct = round(pct, 2)
        if not self.writer.paused:
            if pct < self.min_free_pct:
                self.writer.paused, self.paused_since_ms, self.pause_dropped0 = True, now, self.writer.dropped
                self.pauses += 1
                return False
            return True
        if pct < self.min_free_pct + RESUME_MARGIN_PCT:  # paused: stay paused until the reserve plus the hysteresis margin is free
            return False
        # Resume. The one gap record is written FIRST, directly (resume=True: no pause drop, no second pre-open reading), and the writer
        # un-pauses only once it is on disk; a failed write keeps the pause and the pause interval for the next check.
        rec = _row("gap", "all", 0, now, None, None, {"kind": "disk_reserve", "reason": "disk_reserve", "data_loss": True,
                                                       "from_ms": self.paused_since_ms, "to_ms": now, "paused_since_ms": self.paused_since_ms,
                                                       "paused_ms": now - (self.paused_since_ms if self.paused_since_ms is not None else now),
                                                       "rows_dropped": self.writer.dropped - self.pause_dropped0, "free_pct": self.disk_free_pct,
                                                       "min_free_pct": self.min_free_pct, "resume_margin_pct": RESUME_MARGIN_PCT})
        try:
            written = self.writer.write(rec, resume=True)
        except OSError as exc:
            self.counters["write_errors"] += 1
            self.errlog.log(exc, {"where": "disk_reserve_gap"})
            return False
        if written:
            self.writer.paused, self.paused_since_ms = False, None
        return written

    def socket_rows(self, prog: str) -> list[dict[str, Any]]:
        src = self.sources.get(prog)
        out = []
        for s in (src.socket_stats if src is not None else []):
            out.append({"i": s.index, "up": s.link.up, "reconnects": s.reconnects, "notes": s.notes, "unique": s.unique,
                        "last_notice_ms": s.link.last_notice_ms})
        return out

    def status(self, now: int) -> dict[str, Any]:
        w = self.writer
        return {
            "schema": STATUS_SCHEMA, "updated_ms": now, "started_ms": self.started_ms, "pid": os.getpid(), "out_dir": str(self.out_dir),
            "rows_per_min": {p: w.rows_last_minute(p, now) for p in PROG_PROGRAM},
            "last_slot": {p: self.watch[p].hw_slot for p in PROG_PROGRAM},
            "gaps": sum(x.gaps for x in self.watch.values()), "gaps_by_prog": {p: self.watch[p].gaps for p in PROG_PROGRAM},
            "sockets": {p: self.socket_rows(p) for p in PROG_PROGRAM},
            "rows_by_prog_type": {f"{p}:{t}": n for (p, t), n in sorted(w.by_prog_type.items(), key=str)},
            "lines_written": w.lines, "rows_dropped_disk": w.dropped, "writing_paused": w.paused, "disk_free_pct": self.disk_free_pct,
            "disk_reserve": {"min_free_pct": self.min_free_pct, "free_pct": self.disk_free_pct, "paused": w.paused, "paused_since_ms": self.paused_since_ms,
                             "pauses": self.pauses, "rows_dropped_total": w.dropped},
            "dedup_dropped": {p: s.stats.dedup_dropped for p, s in self.sources.items()}, "cross_dedup_dropped": self.counters["cross_dedup_dropped"],
            "late_dedup": self.seen.snapshot(),
            "decode_errors": self.counters["decode_errors"], "counters": dict(self.counters), "errors_logged_bytes": self.errlog.bytes,
            "rss_mb": proc_rss_mb(), "queue": self.queue.qsize(), "rows_late_to_current_hour": w.late_to_current, "torn_lines_ended": w.torn_lines,
            "compaction": {**self.compactor.snapshot(), "enabled": self.compress},
        }

    def heartbeat(self, now: int) -> None:
        for prog in PROG_PROGRAM:
            w = self.watch[prog]
            self.emit_safe(_row("hb", prog, w.hw_slot if w.hw_slot is not None else 0, now, None, None,
                           {"rows_last_min": self.writer.rows_last_minute(prog, now), "notices": w.notices, "gaps": w.gaps,
                            "sockets_up": [bool(s["up"]) for s in self.socket_rows(prog)], "rss_mb": proc_rss_mb()}))
        write_status(self.out_dir / STATUS_NAME, self.status(now))

    # -- tasks
    async def pump(self, prog: str) -> None:
        backoff, first = 1.0, True
        while not self.stop.is_set():
            if not first:
                self.emit_safe(self.watch[prog].feed_restart(self.wall(), "source_restart"))
            first = False
            try:
                src = build_source(prog, self.ws_urls, self.sockets[prog], self.commitment, self.stagger_s)
                self.sources[prog] = src
                async for note in src.notices(self.stop):
                    backoff = 1.0
                    await self.queue.put((prog, note))
                if self.stop.is_set():
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.counters["feed_errors"] += 1
                log.warning("feed error prog=%s class=%s", prog, type(exc).__name__)
            self.counters["feed_restarts"] += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def housekeeping(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=HOUSEKEEP_S)
            except asyncio.TimeoutError:
                pass
            try:
                self.tick(self.wall())
            except Exception as exc:  # noqa: BLE001 - housekeeping must not die
                self.counters["housekeeping_errors"] += 1
                self.errlog.log(exc, {"where": "housekeeping"})

    async def resolve_once(self) -> int:
        """Look up mint / quote mint of up to RESOLVE_BATCH unresolved PumpSwap pools (one public getMultipleAccounts). Returns how many resolved.
        A resolved pool is cached (later trades carry its mint) and written as a pool_info row."""
        batch = list(self.unresolved)[:RESOLVE_BATCH]
        if not batch or not self.rpc_url:
            return 0
        try:
            found = await asyncio.get_running_loop().run_in_executor(None, self.fetch, self.rpc_url, batch)
        except Exception as exc:  # noqa: BLE001 - public RPC errors and rate limits are expected
            self.counters["resolve_errors"] += 1
            self.errlog.log(exc, {"where": "resolve"})
            return 0
        now, n = self.wall(), 0
        for pool in batch:
            if pool in found:
                base, quote = found[pool]
                self.pool_mints[pool] = (base, quote)
                self.unresolved.pop(pool, None)
                self.emit_safe(_row("pool_info", "pumpswap", self.watch["pumpswap"].hw_slot or 0, now, None, None,
                               {"pool": pool, "mint": base, "quote_mint": quote, "source": "getMultipleAccounts"}))
                n += 1
            elif pool in self.unresolved:
                self.unresolved[pool][1] += 1
                if self.unresolved[pool][1] >= RESOLVE_TRIES:
                    del self.unresolved[pool]
                    self.counters["pools_unresolvable"] += 1
        self.counters["pools_resolved"] += n
        return n

    async def resolve_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=RESOLVE_S)
            except asyncio.TimeoutError:
                pass
            if not self.stop.is_set():
                await self.resolve_once()

    async def consume(self) -> None:
        while True:
            try:
                prog, note = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if self.stop.is_set():
                    return
                continue
            try:  # the last line of defence: nothing a single notice does may end the consumer
                self.handle_notice(prog, note)
            except Exception as exc:  # noqa: BLE001
                self.counters["consume_errors"] += 1
                self.errlog.log(exc, {"where": "consume", "slot": getattr(note, "slot", None)})
                self.note_loss(prog, "consume_error", int(getattr(note, "t_recv_ms", self.wall())), getattr(note, "slot", None))

    def _core_task_ended(self, task: "asyncio.Task") -> None:
        if not self.stop.is_set() and not task.cancelled():
            self.fatal = self.fatal or "core_task_ended"
            self.counters["core_task_ended"] += 1
            self.stop.set()

    async def run(self, max_seconds: float | None = None) -> None:
        tasks = [asyncio.create_task(self.pump(p)) for p in PROG_PROGRAM]
        hk = asyncio.create_task(self.housekeeping())
        cons = asyncio.create_task(self.consume())
        res = asyncio.create_task(self.resolve_loop()) if self.rpc_url else None
        if self.compress:
            self.compactor.start()
        for t in (*tasks, hk, cons):  # a core task that ends while the feed is running is fatal: exit non-zero rather than run on blind
            t.add_done_callback(self._core_task_ended)
        if max_seconds is not None:
            asyncio.get_running_loop().call_later(max_seconds, self.stop.set)
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await cons  # drains what is queued, then returns once stop is set and the queue is quiet
        await hk
        if res is not None:
            await res
        if self.compress:
            await asyncio.get_running_loop().run_in_executor(None, self.compactor.stop)


async def run_live(args: argparse.Namespace) -> int:
    out_dir = check_out_dir(args.out_dir)
    ws_urls = [check_ws_url(u) for u in (args.ws_url or [DEFAULT_PUBLIC_WS])]
    rpc_url = None if args.no_resolve else args.rpc_url
    if rpc_url:
        check_rpc_url(rpc_url)  # every argument is validated before the lock file or the directory is created
    lock_fd = acquire_lock(out_dir)  # raises LockHeld
    feed = Feed(out_dir, ws_urls, {"pumpswap": args.sockets_pumpswap, "pumpfun": args.sockets_pumpfun}, args.commitment, min_free_pct=args.min_free_pct,
                rpc_url=rpc_url, compress=not args.no_compress)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, feed.stop.set)
    prev = read_previous_run(out_dir)  # before anything is written: the window since the last process wrote is a gap, clean stop or not
    if prev is not None:
        feed.pending[("all", "process_restart")] = {"count": 1, "from_ms": min(prev["last_ms"], feed.started_ms), "to_ms": feed.started_ms,
                                                    "from_slot": None, "to_slot": None, "clean_stop": prev["clean_stop"],
                                                    "torn_tail": prev["torn_tail"], "prev_pid": prev["prev_pid"]}
    feed.emit_safe(feed.meta_row("start", schema=SCHEMA, code_sha=code_sha(), argv=redact_argv(sys.argv[1:]), out_dir=str(out_dir), pid=os.getpid(),
                            sockets=feed.sockets, commitment=args.commitment, keys="none", sends="none", gap_slots=GAP_SLOTS, silence_s=SILENCE_S,
                            min_free_pct=args.min_free_pct, compress=feed.compress))
    feed.flush_pending(feed.wall())  # the process_restart record goes out now if the writer allows (else at the first tick after it does)
    try:
        await feed.run(max_seconds=args.max_seconds)
    finally:
        feed.stop.set()
        now = feed.wall()
        feed.flush_pending(now)
        feed.emit_safe(feed.meta_row("stop", **{k: v for k, v in feed.status(now).items() if k in ("rows_by_prog_type", "gaps", "lines_written", "rows_dropped_disk", "decode_errors")}))
        write_status(out_dir / STATUS_NAME, feed.status(now))
        feed.writer.close()
        os.close(lock_fd)
    if feed.fatal:
        print(f"arena_feed: exiting non-zero: {feed.fatal}", file=sys.stderr)
        return FATAL_EXIT
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Forward paper arena live feed (public RPC only; paper only; no keys, no transactions)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="hourly tape-<UTC hour>.jsonl, status and lock live here (default %(default)s)")
    ap.add_argument("--ws-url", action="append", default=None, help="public wss endpoint; repeatable (socket i uses url i mod n); Helius refused")
    ap.add_argument("--sockets-pumpswap", type=int, default=2, help="redundant logsSubscribe sockets on the PumpSwap program (default 2)")
    ap.add_argument("--sockets-pumpfun", type=int, default=2, help="redundant logsSubscribe sockets on the pump.fun bonding-curve program (default 2)")
    ap.add_argument("--commitment", default="confirmed", choices=("processed", "confirmed", "finalized"))
    ap.add_argument("--min-free-pct", type=float, default=MIN_FREE_PCT_DEFAULT, help="DEC-031 rev 2 s3.8: write nothing while the out-dir filesystem has less free space than this, shutil.disk_usage(path).free / total in percent (default %(default)s)")
    ap.add_argument("--no-compress", action="store_true", help="do not compact closed hours to .jsonl.zst (default: compact in a background thread; needs the zstd CLI)")
    ap.add_argument("--rpc-url", default=DEFAULT_RPC, help="public HTTPS RPC for the pool resolver (default %(default)s); Helius URLs are refused")
    ap.add_argument("--no-resolve", action="store_true", help="turn the pool resolver off (then PumpSwap pools whose CreatePool was not seen keep mint null)")
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (smoke test)")
    ap.add_argument("--log-level", default="WARNING")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.WARNING), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("mal.trade_tape").setLevel(logging.ERROR)  # logs are counts only: the socket library logs slots and signatures; the status file has the counters
    if args.sockets_pumpswap < 1 or args.sockets_pumpfun < 1:
        print("arena_feed: sockets must be >= 1", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run_live(args))
    except LockHeld as exc:
        print(f"arena_feed: refusing to start, {exc}", file=sys.stderr)
        return LOCK_HELD_EXIT
    except ValueError as exc:
        print(f"arena_feed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
