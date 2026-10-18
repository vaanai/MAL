#!/usr/bin/env python3
"""Forward paper ARENA live feed. Paper only. No keys, no transactions, no RPC writes, 0 Helius credits.

One public-RPC logsSubscribe ingest of BOTH pump.fun programs (PumpSwap and the bonding curve), decoded with observe.trade_decode
(event_v=True, exactly as tools/h5_shadow.py does for PumpSwap) and written as an hourly strict-JSON-lines tape that the arena bots, the
paper engine and tools/arena_state.py read. Schema and reserve conventions: tools/arena_tape_schema.md.

INGEST. Per program, N redundant sockets (observe.trade_source.MultiSocketLogsSource, merged by signature; --sockets-pumpswap /
--sockets-pumpfun, default 2 each). Default endpoint wss://api.mainnet-beta.solana.com; more come via repeatable --ws-url (socket i uses url
i mod n). Any Helius URL, a URL with a query string, userinfo or a path (where a key could sit) is refused at start. A transaction that both
subscriptions deliver (a migrate tx mentions both programs) is decoded once (global signature dedup).

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
duplicate writer alive). Disk guard: below --min-free-pct free space the feed stops writing rows (gap kind disk_pause / disk_resume).
Memory: bounded dedup sets, no per-pool state here; the hb carries VmRSS. Logs are counts only (no signature, mint or wallet is logged).

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
import json
import logging
import os
import shutil
import signal
import sys
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
from observe.trade_source import DEFAULT_PUBLIC_WS, MultiSocketLogsSource, RawNotice, SeenSet
from tools.h5_shadow import DEFAULT_RPC, GAP_SLOTS, SILENCE_S, check_rpc_url, clean, code_sha, common_down, feed_snapshot, redact_argv, write_status
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
DEDUP_WINDOW_S = 120.0  # socket merge + cross-program dedup windows (a socket far behind its peer is a gap anyway)
DEDUP_MAX = 200_000
QUEUE_MAX = 50_000
ERRORS_MAX_BYTES = 2_000_000
POOL_MINTS_MAX = 50_000
NAME_MAX, SYMBOL_MAX = 32, 16
DISK_CHECK_S = 30.0
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
class TapeWriter:
    """Hourly tape-<UTC hour of t_recv_ms>.jsonl, strict JSON, flushed per line. Counts rows by (prog, type)."""

    def __init__(self, out_dir: Path) -> None:
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._name: str | None = None
        self.lines = 0
        self.paused = False
        self.dropped = 0
        self.by_prog_type: collections.Counter = collections.Counter()
        self.minute: dict[str, collections.deque] = {p: collections.deque(maxlen=12) for p in PROG_PROGRAM}

    @staticmethod
    def name_for(t_ms: int) -> str:
        return "tape-" + datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"

    def write(self, rec: dict[str, Any], *, force: bool = False) -> None:
        if self.paused and not force:
            self.dropped += 1
            return
        name = self.name_for(int(rec["t_recv_ms"]))
        if self._name != name:
            if self._fh:
                self._fh.close()
            self._fh = open(self.dir / name, "a", encoding="utf-8")
            self._name = name
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

    def rows_last_minute(self, prog: str, now: int) -> int:
        """Data rows of `prog` in the last COMPLETED minute before `now`."""
        m = now // 60_000 - 1
        return next((n for mm, n in reversed(self.minute[prog]) if mm == m), 0)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class ErrorLog:
    def __init__(self, path: Path, max_bytes: int = ERRORS_MAX_BYTES) -> None:
        self.path, self.max_bytes, self.bytes, self.dropped = path, max_bytes, 0, 0

    def log(self, exc: BaseException, ctx: dict | None = None) -> None:
        if self.bytes >= self.max_bytes:
            self.dropped += 1
            return
        head = json.dumps(clean({"t_ms": now_ms(), "exc": type(exc).__name__, "msg": str(exc)[:200], **(ctx or {})}), allow_nan=False)
        text = head + "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)
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
        self.gaps = 0
        self.notices = 0
        self.prev: dict | None = None
        self.flagged_starts: collections.deque = collections.deque(maxlen=256)
        self.looks: collections.deque = collections.deque(maxlen=3)

    def _gap(self, kind: str, at_ms: int, data_loss: bool, **kw: Any) -> dict[str, Any]:
        self.gaps += 1
        return _row("gap", self.prog, self.hw_slot if self.hw_slot is not None else 0, at_ms, None, None, {"kind": kind, "data_loss": data_loss, **kw})

    def on_notice(self, slot: int, recv_ms: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        self.notices += 1
        self.silence_open = False
        self.last_ms = recv_ms
        if self.hw_slot is not None and slot > self.hw_slot + GAP_SLOTS:
            out.append(self._gap("slot_jump", recv_ms, True, from_slot=self.hw_slot, to_slot=slot, missed_slots=slot - self.hw_slot - 1))
        if self.hw_slot is None or slot > self.hw_slot:
            self.hw_slot = slot
        return out

    def tick(self, now: int) -> list[dict[str, Any]]:
        if not self.silence_open and now - self.last_ms > SILENCE_S * 1000:
            self.silence_open = True
            return [self._gap("silence", now, True, silent_ms=now - self.last_ms)]
        return []

    def feed_restart(self, now: int, reason: str) -> dict[str, Any]:
        rec = self._gap("feed_restart", now, True, reason=reason)
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

        return [self._gap("socket_reconnect", now, data_loss, delta_reconnects=delta, reconnects_total=snap["reconnects"], sockets=n,
                          per_socket_delta=per_socket, delta_closes=dd(snap["closes"], prev["closes"]),
                          delta_rejections=dd(snap["rejections"], prev["rejections"]), delta_errors=dd(snap["errors"], prev["errors"]),
                          common_down_ms=sum(b - a for a, b in pieces))]


# ---- live runner ----------------------------------------------------------------------------------------------
def build_source(prog: str, ws_urls: Sequence[str], sockets: int, commitment: str, stagger_s: float = 3.0) -> MultiSocketLogsSource:
    return MultiSocketLogsSource(ws_urls=list(ws_urls) or [DEFAULT_PUBLIC_WS], sockets=sockets, programs=(PROG_PROGRAM[prog],),
                                 commitment=commitment, stagger_s=stagger_s, seen_window_s=DEDUP_WINDOW_S, max_seen=DEDUP_MAX)


class Feed:
    """The arena feed engine: sources in, tape rows out. One instance per process."""

    def __init__(self, out_dir: Path, ws_urls: Sequence[str], sockets: dict[str, int], commitment: str = "confirmed", *, min_free_pct: float = 10.0,
                 stagger_s: float = 3.0, wall: Callable[[], int] = now_ms, disk_usage: Callable[[Path], Any] = shutil.disk_usage,
                 rpc_url: str | None = DEFAULT_RPC, fetch: Callable[[str, list[str]], dict] = fetch_pool_mints) -> None:
        self.rpc_url, self.fetch = (check_rpc_url(rpc_url) if rpc_url else None), fetch  # rpc_url None = pool resolver off
        self.unresolved: collections.OrderedDict[str, list] = collections.OrderedDict()  # pool -> [first_ms, tries]
        self.out_dir, self.ws_urls, self.sockets, self.commitment = out_dir, [check_ws_url(u) for u in ws_urls], dict(sockets), commitment
        self.min_free_pct, self.stagger_s, self.wall, self.disk_usage = min_free_pct, stagger_s, wall, disk_usage
        self.writer = TapeWriter(out_dir)
        self.errlog = ErrorLog(out_dir / ERRORS_NAME)
        self.started_ms = wall()
        self.watch = {p: ProgWatch(p, self.started_ms) for p in PROG_PROGRAM}
        self.sources: dict[str, MultiSocketLogsSource] = {}
        self.seen = SeenSet(DEDUP_WINDOW_S, DEDUP_MAX)
        self.pool_mints: dict[str, tuple[str, str]] = {}
        self.counters: collections.Counter = collections.Counter()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.stop = asyncio.Event()
        self.last_hb = 0.0
        self.last_disk = 0.0
        self.disk_free_pct: float | None = None

    # -- rows
    def emit(self, rec: dict[str, Any], *, force: bool = False) -> None:
        self.writer.write(rec, force=force)

    def meta_row(self, type_: str, **body: Any) -> dict[str, Any]:
        return _row(type_, "all", 0, self.wall(), None, None, body)

    def handle_notice(self, prog: str, note: RawNotice) -> int:
        """One notice from `prog`'s feed: gap watch first (every notice counts as sign of life), then decode once per signature."""
        for g in self.watch[prog].on_notice(note.slot, note.t_recv_ms):
            self.emit(g, force=True)
        if not self.seen.add(note.signature):
            self.counters["cross_dedup_dropped"] += 1
            return 0
        try:
            rows = rows_from_notice(note, self.pool_mints, self.counters)
        except Exception as exc:  # noqa: BLE001 - one bad blob must not end the feed
            self.counters["decode_errors"] += 1
            self.errlog.log(exc, {"where": "decode", "slot": note.slot})
            return 0
        for r in rows:
            self.emit(r)
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
                self.emit(g, force=True)
            src = self.sources.get(prog)
            snap = feed_snapshot(src) if src is not None else None
            if snap is not None:
                for g in w.note_feed_stats(snap, now):
                    self.emit(g, force=True)
        self.check_disk(now)
        if time.monotonic() - self.last_hb >= HB_S or self.stop.is_set():
            self.last_hb = time.monotonic()
            self.heartbeat(now)

    def check_disk(self, now: int) -> None:
        if time.monotonic() - self.last_disk < DISK_CHECK_S and self.disk_free_pct is not None:
            return
        self.last_disk = time.monotonic()
        try:
            du = self.disk_usage(self.out_dir)
            self.disk_free_pct = round(100.0 * du.free / du.total, 2)
        except OSError:
            return
        low = self.disk_free_pct < self.min_free_pct
        if low and not self.writer.paused:
            self.emit(_row("gap", "all", 0, now, None, None, {"kind": "disk_pause", "data_loss": True, "free_pct": self.disk_free_pct, "min_free_pct": self.min_free_pct}), force=True)
            self.writer.paused = True
        elif not low and self.writer.paused:
            self.writer.paused = False
            self.emit(_row("gap", "all", 0, now, None, None, {"kind": "disk_resume", "data_loss": True, "free_pct": self.disk_free_pct, "rows_dropped": self.writer.dropped}), force=True)

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
            "dedup_dropped": {p: s.stats.dedup_dropped for p, s in self.sources.items()}, "cross_dedup_dropped": self.counters["cross_dedup_dropped"],
            "decode_errors": self.counters["decode_errors"], "counters": dict(self.counters), "errors_logged_bytes": self.errlog.bytes,
            "rss_mb": proc_rss_mb(), "queue": self.queue.qsize(),
        }

    def heartbeat(self, now: int) -> None:
        for prog in PROG_PROGRAM:
            w = self.watch[prog]
            self.emit(_row("hb", prog, w.hw_slot if w.hw_slot is not None else 0, now, None, None,
                           {"rows_last_min": self.writer.rows_last_minute(prog, now), "notices": w.notices, "gaps": w.gaps,
                            "sockets_up": [bool(s["up"]) for s in self.socket_rows(prog)], "rss_mb": proc_rss_mb()}), force=True)
        write_status(self.out_dir / STATUS_NAME, self.status(now))

    # -- tasks
    async def pump(self, prog: str) -> None:
        backoff, first = 1.0, True
        while not self.stop.is_set():
            if not first:
                self.emit(self.watch[prog].feed_restart(self.wall(), "source_restart"), force=True)
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
                self.emit(_row("pool_info", "pumpswap", self.watch["pumpswap"].hw_slot or 0, now, None, None,
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
            self.handle_notice(prog, note)

    async def run(self, max_seconds: float | None = None) -> None:
        tasks = [asyncio.create_task(self.pump(p)) for p in PROG_PROGRAM]
        hk = asyncio.create_task(self.housekeeping())
        cons = asyncio.create_task(self.consume())
        res = asyncio.create_task(self.resolve_loop()) if self.rpc_url else None
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


async def run_live(args: argparse.Namespace) -> int:
    out_dir = check_out_dir(args.out_dir)
    ws_urls = [check_ws_url(u) for u in (args.ws_url or [DEFAULT_PUBLIC_WS])]
    lock_fd = acquire_lock(out_dir)  # raises LockHeld
    rpc_url = None if args.no_resolve else args.rpc_url
    if rpc_url:
        check_rpc_url(rpc_url)
    feed = Feed(out_dir, ws_urls, {"pumpswap": args.sockets_pumpswap, "pumpfun": args.sockets_pumpfun}, args.commitment, min_free_pct=args.min_free_pct,
                rpc_url=rpc_url)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, feed.stop.set)
    feed.emit(feed.meta_row("start", schema=SCHEMA, code_sha=code_sha(), argv=redact_argv(sys.argv[1:]), out_dir=str(out_dir), pid=os.getpid(),
                            sockets=feed.sockets, commitment=args.commitment, keys="none", sends="none", gap_slots=GAP_SLOTS, silence_s=SILENCE_S,
                            min_free_pct=args.min_free_pct), force=True)
    try:
        await feed.run(max_seconds=args.max_seconds)
    finally:
        feed.stop.set()
        now = feed.wall()
        feed.emit(feed.meta_row("stop", **{k: v for k, v in feed.status(now).items() if k in ("rows_by_prog_type", "gaps", "lines_written", "rows_dropped_disk", "decode_errors")}), force=True)
        write_status(out_dir / STATUS_NAME, feed.status(now))
        feed.writer.close()
        os.close(lock_fd)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Forward paper arena live feed (public RPC only; paper only; no keys, no transactions)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="hourly tape-<UTC hour>.jsonl, status and lock live here (default %(default)s)")
    ap.add_argument("--ws-url", action="append", default=None, help="public wss endpoint; repeatable (socket i uses url i mod n); Helius refused")
    ap.add_argument("--sockets-pumpswap", type=int, default=2, help="redundant logsSubscribe sockets on the PumpSwap program (default 2)")
    ap.add_argument("--sockets-pumpfun", type=int, default=2, help="redundant logsSubscribe sockets on the pump.fun bonding-curve program (default 2)")
    ap.add_argument("--commitment", default="confirmed", choices=("processed", "confirmed", "finalized"))
    ap.add_argument("--min-free-pct", type=float, default=10.0, help="stop writing rows while the out-dir filesystem has less free space than this (percent)")
    ap.add_argument("--rpc-url", default=DEFAULT_RPC, help="public HTTPS RPC for the pool resolver (default %(default)s); Helius URLs are refused")
    ap.add_argument("--no-resolve", action="store_true", help="turn the pool resolver off (then PumpSwap pools whose CreatePool was not seen keep mint null)")
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (smoke test)")
    ap.add_argument("--log-level", default="WARNING")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.WARNING), format="%(asctime)s %(levelname)s %(name)s %(message)s")
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
