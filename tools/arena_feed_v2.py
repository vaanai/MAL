#!/usr/bin/env python3
"""arena_feed_v2: the arena feed with a gapless OVERLAP hand-off (P35 item (a); docs/research/p35-longbook-handoff/DESIGN.md section 2). PAPER only.
No keys, no transactions, no RPC writes, 0 Helius credits.

tools/arena_feed.py is NOT edited: it is HV4's feed pin (blob sha256 7a9d424b...). This module imports it and subclasses `Feed`. Without --standby
it runs exactly v1's `run_live` (same rows, same lock, same restart rows); only the code sha differs.

STANDBY (--standby). A new process that takes over the tape from a running writer (v1 or v2) with no gap row:
  1. flock on <out-dir>/.arena-feed.standby.lock (a second standby exits 3). The writer's .arena-feed.lock is not touched yet.
  2. pool-mint cache warm-up: pool_create / pool_info rows of the last WARM_HOURS hour files (no other field is kept).
  3. it subscribes with v1's sources and buffers every notice IN MEMORY (bounded by --buffer-max rows; an overflow makes the hand-off unproven).
     Nothing is written to the tape. The standby's own v1 gap rules run on its stream; a data_loss gap (slot jump, silence, a common outage of
     every standby socket) or a decode error moves the program's coverage start `cover_from_ms` past it; every socket reconnect instant is kept.
  4. at --takeover-at-ms, once BOTH programs have cover_from_ms <= now - overlap_min_s and at least min_overlap_rows x 1.1 buffered data rows
     since then, it stops the old writer: `stop_old` (production: SIGTERM to the process that holds the writer lock, after uid / argv / out-dir /
     /proc/locks checks; SIGKILL after 60 s, disclosed). Not ready by --takeover-deadline-ms: it exits 5 and writes nothing.
  5. it polls flock(LOCK_NB) on .arena-feed.lock every 10 ms. Another process taking the lock first (pid in the lock file is neither the old
     writer nor this one) -> exit 3, nothing written.
  6. it reads the old writer's tail (hour files from the earliest buffered instant - DEDUP_WINDOW_S): data-row keys and signatures, the last data
     row per program, data_loss / hard gap rows, pool_create / pool_info rows (cache refresh), and whether the tape ends in a clean `stop` row.
  7. continuity per program (DESIGN 2.1 step 8; QP-RULING-1026 Q1). `proven` iff ALL hold:
       cover_from_ms <= old_last_data_ms - overlap_min_s;  no data_loss / hard gap of the program (or `all`) on the old tail over [cover_from, stop];
       the tape ends in a clean stop row (no torn tail);  the buffer did not overflow;  no standby reconnect in [cover_from, old_last];
       thresholds pinned;  n_old >= min_overlap_rows;  only_old / n_old <= max_disagree_rate, where n_old = old data rows of the program with
       t_recv in [cover_from, old_last] and only_old = those whose signature the standby never received.
  8. WRITE (one synchronous block: no notice can arrive between the buffer flush and live mode):
       all proven  -> one `takeover` row (NOT a start row), then the buffered notices re-decoded in order with the refreshed cache, minus every
                      signature already on the tape, then live;
       otherwise   -> exactly v1's restart rows: `start`, then a `process_restart` data_loss gap over [old_last, now] with
                      reason "handover_unproven" and the failed condition names; then the buffer (deduplicated); then live. Fail closed.
  9. status counters carry over: lines_written, gaps, disk_reserve.pauses and rows_dropped_total start from the old writer's last status, so a
     sampler never sees a counter go down. The first hb rows are written right after the takeover row.

PRODUCTION GUARD. The production tape /data/mal/arena/tape is refused in standby mode before EARLIEST_PRODUCTION_MS (2026-11-11T14:00Z: after
HV6's last in-clock instant, QP-RULING-1026 Q5) and whenever the thresholds are not pinned. Tests and rehearsals use another out-dir.

EXIT CODES: v1's (0, 2, 3, 4) plus 5: the takeover did not happen by its deadline (nothing written, the old writer untouched or, if the stop
was sent, said so on stderr).
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_feed_v2.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import collections
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools.h5_shadow import code_sha, feed_snapshot, redact_argv, write_status

HANDOVER_V = 1
STANDBY_LOCK_NAME = ".arena-feed.standby.lock"
STANDBY_STATUS_NAME = "arena-feed-standby-status.json"
PRODUCTION_TAPE = "/data/mal/arena/tape"
EARLIEST_PRODUCTION_MS = int(datetime(2026, 11, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
OVERLAP_MIN_S_FLOOR = 120.0
READY_MARGIN = 1.10  # the standby waits for min_overlap_rows x this many buffered rows before it stops the old writer
WARM_HOURS = 2
LOCK_POLL_S = 0.01
TAKEOVER_EXIT = 5
DATA_TYPES = ("trade", "create", "complete", "pool_create")
HARD_KINDS = ("process_restart", "feed_restart", "feed_start", "handover")
KILL_WAIT_S = 60.0


class TakeoverAbort(Exception):
    """The takeover did not happen and nothing was written. `code` is the process exit code."""

    def __init__(self, msg: str, code: int) -> None:
        super().__init__(msg)
        self.code = code


def _hour_files(out_dir: Path, lo_ms: int, hi_ms: int) -> list[Path]:
    """One file per hour overlapping [lo_ms, hi_ms] (.jsonl preferred over .jsonl.zst), oldest first. Names only until read."""
    by: dict[int, dict[str, Path]] = {}
    for p in out_dir.glob("tape-*.jsonl*"):
        kind = "jsonl" if p.name.endswith(".jsonl") else "zst" if p.name.endswith(".jsonl.zst") else None
        h = af.hour_start_ms(p.name)
        if kind is None or h is None or h + 3_600_000 <= lo_ms or h > hi_ms:
            continue
        by.setdefault(h, {})[kind] = p
    return [d.get("jsonl") or d["zst"] for _, d in sorted(by.items())]


def _iter_rows(p: Path):
    if p.name.endswith(".zst"):
        from tools.arena_zst import open_zst

        r = open_zst(p)
        buf = b""
        try:
            while True:
                b = r.read()
                if not b:
                    break
                buf += b
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    yield ln
            if buf:
                yield buf
        finally:
            r.close()
        return
    with open(p, "rb") as fh:
        for ln in fh:
            yield ln.rstrip(b"\n")


def read_rows(out_dir: Path, lo_ms: int, hi_ms: int, types: Sequence[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for p in _hour_files(out_dir, lo_ms, hi_ms):
        for ln in _iter_rows(p):
            if types is not None and not any(f'"type":"{t}"'.encode() in ln for t in types):
                continue  # cheap pre-filter; the parsed type is checked below
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("t_recv_ms"), int) and (types is None or r.get("type") in types):
                out.append(r)
    return out


# ---- production stop of the old writer ---------------------------------------------------------------------------
def lock_holder_pids(lock_path: Path, proc_locks: str = "/proc/locks") -> list[int]:
    """PIDs holding an flock on lock_path's inode, from /proc/locks."""
    try:
        st = os.stat(lock_path)
        text = Path(proc_locks).read_text()
    except OSError:
        return []
    out = []
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 6 and parts[1] == "FLOCK":
            try:
                _, _, ino = parts[5].split(":")
                if int(ino) == st.st_ino:
                    out.append(int(parts[4]))
            except ValueError:
                continue
    return sorted(set(out))


def _cmdline(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return []


def _start_time(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def make_stop_old(out_dir: Path, *, kill_wait_s: float = KILL_WAIT_S, log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True)):
    """The production stop: exactly one feed process of this uid, argv with tools.arena_feed[_v2] and --out-dir <out_dir>, holding the writer lock
    (/proc/locks), not this process. SIGTERM; SIGKILL only after kill_wait_s (logged: a killed writer leaves no stop row, so continuity fails)."""
    lock = out_dir / af.LOCK_NAME

    def stop_old() -> dict[str, Any]:
        holders = [p for p in lock_holder_pids(lock) if p != os.getpid()]
        if len(holders) != 1:
            raise TakeoverAbort(f"writer lock holders={holders}: refusing (exactly one expected)", TAKEOVER_EXIT)
        pid = holders[0]
        argv = _cmdline(pid)
        mods = ("tools.arena_feed", "tools.arena_feed_v2")
        if not any(a in mods for a in argv) or "--out-dir" not in argv or argv[argv.index("--out-dir") + 1:argv.index("--out-dir") + 2] != [str(out_dir)]:
            raise TakeoverAbort(f"lock holder {pid} is not an arena feed on {out_dir}: refusing", TAKEOVER_EXIT)
        if os.stat(f"/proc/{pid}").st_uid != os.getuid():
            raise TakeoverAbort(f"lock holder {pid} belongs to another uid: refusing", TAKEOVER_EXIT)
        st0 = _start_time(pid)
        os.kill(pid, signal.SIGTERM)
        log(f"takeover: SIGTERM sent to {pid}")
        end = time.monotonic() + kill_wait_s
        while time.monotonic() < end:
            if _start_time(pid) != st0:
                return {"pid": pid, "sigkill": False}
            time.sleep(0.05)
        if _start_time(pid) == st0:
            os.kill(pid, signal.SIGKILL)
            log(f"takeover: SIGKILL sent to {pid} after {kill_wait_s} s (disclose: no stop row, continuity unproven)")
        return {"pid": pid, "sigkill": True}

    return stop_old


# ---- the feed ------------------------------------------------------------------------------------------------------
class FeedV2(af.Feed):
    """v1 Feed plus a standby phase (buffer, coverage clock) and the takeover. See the module docstring."""

    def __init__(self, out_dir: Path, ws_urls: Sequence[str], sockets: dict[str, int], commitment: str = "confirmed", *, standby: bool = True,
                 overlap_min_s: float = OVERLAP_MIN_S_FLOOR, min_overlap_rows: dict[str, int] | None = None,
                 max_disagree_rate: dict[str, float] | None = None, buffer_max: int = af.QUEUE_MAX, **kw: Any) -> None:
        if overlap_min_s < OVERLAP_MIN_S_FLOOR:  # checked before anything is created
            raise ValueError(f"overlap_min_s {overlap_min_s} is below the floor {OVERLAP_MIN_S_FLOOR} (QP-RULING-1026 Q1)")
        super().__init__(out_dir, ws_urls, sockets, commitment, **kw)
        self.mode = "standby" if standby else "live"
        self.overlap_min_s, self.min_overlap_rows, self.max_disagree_rate = float(overlap_min_s), min_overlap_rows, max_disagree_rate
        self.buffer_max = int(buffer_max)
        self.buffer: list[tuple[str, RawNotice, int]] = []  # (prog, notice, data rows at receipt)
        self.buffered_rows = 0
        self.overflow = False
        self.all_sigs: dict[str, int] = {}  # signature -> first t_recv_ms the standby received it (any program)
        self.data_ms: dict[str, list[int]] = {p: [] for p in af.PROG_PROGRAM}  # t_recv of buffered data rows per row program
        self.first_ms: dict[str, int | None] = {p: None for p in af.PROG_PROGRAM}
        self.cover_reset: dict[str, int] = {p: 0 for p in af.PROG_PROGRAM}
        self.reconnect_ms: dict[str, list[int]] = {p: [] for p in af.PROG_PROGRAM}
        self.counter_base = {"lines_written": 0, "gaps": 0, "pauses": 0, "rows_dropped_total": 0}
        self.takeover_info: dict[str, Any] | None = None
        self.standby_lock_fd: int | None = None
        # The old writer compacts closed hours; a standby must not run a second compactor on the same dir. Compaction starts at the takeover.
        self._compress_at_takeover = self.compress and standby
        if standby:
            self.compress = False

    # -- standby ingest
    def _standby_gap(self, g: dict[str, Any]) -> None:
        prog = str(g.get("prog"))
        if g.get("kind") == "socket_reconnect":
            for p in (af.PROG_PROGRAM if prog == "all" else [prog]):
                if p in self.reconnect_ms:
                    self.reconnect_ms[p].append(int(g["t_recv_ms"]))
        if g.get("data_loss"):
            end = int(g.get("to_ms") if isinstance(g.get("to_ms"), int) else g["t_recv_ms"])
            for p in (af.PROG_PROGRAM if prog == "all" else [prog]):
                if p in self.cover_reset:
                    self.cover_reset[p] = max(self.cover_reset[p], end)
            self.counters["standby_data_loss_gaps"] += 1

    def note_reconnect(self, prog: str, t_ms: int) -> None:
        """A socket reconnect of `prog` at t_ms (also called from the reconnect counters; tests call it directly)."""
        self.reconnect_ms[prog].append(int(t_ms))

    def handle_notice(self, prog: str, note: RawNotice) -> int:
        if self.mode != "standby":
            return super().handle_notice(prog, note)
        w, src = self.watch[prog], self.sources.get(prog)
        if src is not None and src.stats.reconnects != (w.prev["reconnects"] if w.prev else 0):
            snap = feed_snapshot(src)
            if snap is not None:
                for g in w.note_feed_stats(snap, note.t_recv_ms):
                    self._standby_gap(g)
        for g in w.on_notice(note.slot, note.t_recv_ms):
            self._standby_gap(g)
        if self.first_ms[prog] is None:
            self.first_ms[prog] = note.t_recv_ms
        if not self.seen.add(note.signature):
            self.counters["cross_dedup_dropped"] += 1
            return 0
        self.all_sigs.setdefault(note.signature, note.t_recv_ms)
        try:
            rows = af.rows_from_notice(note, self.pool_mints, self.counters)
        except Exception:  # noqa: BLE001 - the standby lost this notice's rows: coverage restarts after it
            self.counters["decode_errors"] += 1
            self.cover_reset[prog] = max(self.cover_reset[prog], note.t_recv_ms)
            return 0
        if self.buffered_rows + len(rows) > self.buffer_max:
            self.overflow = True
            self.counters["standby_buffer_overflow"] += 1
            return 0
        self.buffer.append((prog, note, len(rows)))
        self.buffered_rows += len(rows)
        for r in rows:
            if r["prog"] in self.data_ms:
                self.data_ms[r["prog"]].append(r["t_recv_ms"])
        return len(rows)

    def cover_from(self, prog: str) -> int | None:
        f = self.first_ms[prog]
        return None if f is None else max(f, self.cover_reset[prog])

    def thresholds_pinned(self) -> bool:
        return (self.min_overlap_rows is not None and self.max_disagree_rate is not None
                and all(p in self.min_overlap_rows and p in self.max_disagree_rate for p in af.PROG_PROGRAM))

    def ready(self, now: int) -> tuple[bool, list[str]]:
        """May the old writer be stopped now? (overlap floor and buffered rows since cover_from, both programs; no overflow)."""
        why = []
        if self.overflow:
            why.append("buffer_overflow")
        if not self.thresholds_pinned():
            why.append("thresholds_unpinned")
        for p in af.PROG_PROGRAM:
            c = self.cover_from(p)
            if c is None or c > now - int(self.overlap_min_s * 1000):
                why.append(f"overlap_short:{p}")
                continue
            if self.thresholds_pinned():
                n = sum(1 for t in self.data_ms[p] if t >= c)
                if n < self.min_overlap_rows[p] * READY_MARGIN:
                    why.append(f"rows_short:{p}")
        return (not why), why

    def standby_tick(self, now: int) -> None:
        for prog, w in self.watch.items():
            for g in w.tick(now):
                self._standby_gap(g)
            src = self.sources.get(prog)
            snap = feed_snapshot(src) if src is not None else None
            if snap is not None:
                for g in w.note_feed_stats(snap, now):
                    self._standby_gap(g)
        if time.monotonic() - self.last_hb >= af.HB_S:
            self.last_hb = time.monotonic()
            write_status(self.out_dir / STANDBY_STATUS_NAME, self.standby_status(now))

    def standby_status(self, now: int) -> dict[str, Any]:
        ok, why = self.ready(now)
        return {"schema": "arena_feed_standby_status_v1", "updated_ms": now, "pid": os.getpid(), "buffered_rows": self.buffered_rows,
                "overflow": self.overflow, "cover_from_ms": {p: self.cover_from(p) for p in af.PROG_PROGRAM}, "ready": ok, "not_ready": why,
                "reconnects": {p: len(v) for p, v in self.reconnect_ms.items()}}

    def tick(self, now: int) -> None:
        if self.mode == "standby":
            self.standby_tick(now)
            return
        super().tick(now)

    def status(self, now: int) -> dict[str, Any]:
        s = super().status(now)
        b = self.counter_base
        s["lines_written"] = s["lines_written"] + b["lines_written"]
        s["gaps"] = s["gaps"] + b["gaps"]
        dr = dict(s.get("disk_reserve") or {})
        dr["pauses"] = (dr.get("pauses") or 0) + b["pauses"]
        dr["rows_dropped_total"] = (dr.get("rows_dropped_total") or 0) + b["rows_dropped_total"]
        s["disk_reserve"] = dr
        s["counter_base"] = dict(b)
        if self.takeover_info is not None:
            s["handover_from_pid"] = self.takeover_info.get("prev_pid")
        return s

    # -- pool-mint warm-up
    def warm_pool_mints(self, now: int, rows: list[dict[str, Any]] | None = None) -> int:
        rows = rows if rows is not None else read_rows(self.out_dir, now - WARM_HOURS * 3_600_000, now, types=("pool_create", "pool_info"))
        n = 0
        for r in rows:
            if r.get("type") in ("pool_create", "pool_info") and r.get("pool") and r.get("mint"):
                self.pool_mints[str(r["pool"])] = (str(r["mint"]), str(r.get("quote_mint") or ""))
                n += 1
        return n

    # -- takeover
    def read_tail(self, now: int) -> dict[str, Any]:
        lo = min([t for _, n, _ in self.buffer for t in (n.t_recv_ms,)] + [c for c in (self.cover_from(p) for p in af.PROG_PROGRAM) if c is not None] + [now])
        lo -= int(af.DEDUP_WINDOW_S * 1000)
        rows = read_rows(self.out_dir, lo, now + 3_600_000)
        sigs = {str(r["signature"]) for r in rows if r.get("type") in DATA_TYPES and r.get("signature")}
        last: dict[str, int | None] = {p: None for p in af.PROG_PROGRAM}
        data_t: dict[str, list[tuple[int, str]]] = {p: [] for p in af.PROG_PROGRAM}
        for r in rows:
            if r.get("type") in DATA_TYPES and r.get("prog") in last:
                p = r["prog"]
                last[p] = max(last[p] or r["t_recv_ms"], r["t_recv_ms"])
                data_t[p].append((r["t_recv_ms"], str(r.get("signature") or "")))
        gaps = [r for r in rows if r.get("type") == "gap" and (r.get("data_loss") or r.get("kind") in HARD_KINDS)]
        prev = af.read_previous_run(self.out_dir)
        stop_ms = max((r["t_recv_ms"] for r in rows if r.get("type") == "stop"), default=None)
        return {"sigs": sigs, "last": last, "data_t": data_t, "gaps": gaps, "clean_stop": bool(prev and prev["clean_stop"] and not prev["torn_tail"]),
                "stop_ms": stop_ms, "prev_pid": (prev or {}).get("prev_pid"), "pool_rows": [r for r in rows if r.get("type") in ("pool_create", "pool_info")]}

    def verdict(self, tail: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any], list[str]]:
        cont, agree, failed = {}, {}, []
        pinned = self.thresholds_pinned()
        for p in af.PROG_PROGRAM:
            bad = []
            c, last = self.cover_from(p), tail["last"][p]
            if c is None or last is None or c > last - int(self.overlap_min_s * 1000):
                bad.append("overlap")
            stop = tail["stop_ms"] if tail["stop_ms"] is not None else (last or 0)
            for g in tail["gaps"]:
                if g.get("prog") in (p, "all", None):
                    fr = g["from_ms"] if isinstance(g.get("from_ms"), int) else g["t_recv_ms"]
                    to = g["to_ms"] if isinstance(g.get("to_ms"), int) else g["t_recv_ms"]
                    if c is not None and to >= c and fr <= stop:
                        bad.append("old_gap")
                        break
            if not tail["clean_stop"]:
                bad.append("no_clean_stop")
            if self.overflow:
                bad.append("buffer_overflow")
            rin = sum(1 for t in self.reconnect_ms[p] if c is not None and last is not None and c <= t <= last)
            if rin:
                bad.append("standby_reconnect")
            win = [s for t, s in tail["data_t"][p] if c is not None and last is not None and c <= t <= last]
            n_old, only_old = len(win), sum(1 for s in win if s not in self.all_sigs)
            agree[p] = {"n_old": n_old, "only_old": only_old, "reconnects": rin}
            if not pinned:
                bad.append("thresholds_unpinned")
            elif n_old < int(self.min_overlap_rows[p]):
                bad.append("n_old_short")
            elif only_old / n_old > float(self.max_disagree_rate[p]):
                bad.append("disagree")
            cont[p] = "proven" if not bad else "unproven"
            failed += [f"{p}:{b}" for b in bad]
        return cont, agree, failed

    def complete_takeover(self, lock_fd: int, lock_ms: int, *, standby_started_ms: int, prev_pid: int | None = None) -> dict[str, Any]:
        """Synchronous: read the tail, decide, write, switch to live. The caller holds the writer lock (lock_fd)."""
        now = lock_ms
        tail = self.read_tail(now)
        self.warm_pool_mints(now, tail["pool_rows"])  # the old writer's last pool_create / pool_info rows: re-decode with what it knew
        cont, agree, failed = self.verdict(tail)
        proven = all(v == "proven" for v in cont.values())
        try:
            st = json.loads((self.out_dir / af.STATUS_NAME).read_text())
            dr = st.get("disk_reserve") or {}
            self.counter_base = {"lines_written": int(st.get("lines_written") or 0), "gaps": int(st.get("gaps") or 0),
                                 "pauses": int(dr.get("pauses") or 0), "rows_dropped_total": int(dr.get("rows_dropped_total") or 0)}
        except (OSError, ValueError, TypeError):
            self.counters["counter_base_unreadable"] += 1
        old_pid = prev_pid if prev_pid is not None else tail["prev_pid"]
        # re-decode the buffer in order with the refreshed cache; drop every signature already on the tape
        out_rows: list[dict[str, Any]] = []
        dropped = 0
        for _, note, _n in self.buffer:
            try:
                rows = af.rows_from_notice(note, self.pool_mints, self.counters)
            except Exception:  # noqa: BLE001
                self.counters["decode_errors"] += 1
                continue
            if note.signature in tail["sigs"]:
                dropped += len(rows)
                continue
            out_rows.extend(rows)
        buffered = dropped + len(out_rows)
        last = tail["last"]
        info = {"schema": af.SCHEMA, "handover_v": HANDOVER_V, "code_sha": code_sha(), "pid": os.getpid(), "prev_pid": old_pid,
                "standby_started_ms": standby_started_ms, "old_stop_ms": tail["stop_ms"], "lock_acquired_ms": lock_ms,
                "cover_from_ms": {p: self.cover_from(p) for p in af.PROG_PROGRAM}, "old_last_data_ms": dict(last), "continuity": cont,
                "dedup": {"buffered": buffered, "dropped_dup": dropped, "written_missed": len(out_rows), "buffer_overflow": self.overflow},
                "agreement": {p: {"n_old": a["n_old"], "only_old": a["only_old"]} for p, a in agree.items()},
                "standby_reconnects_in_overlap": {p: a["reconnects"] for p, a in agree.items()},
                "thresholds": {"max_disagree_rate": dict(self.max_disagree_rate or {}), "min_overlap_rows": dict(self.min_overlap_rows or {})},
                "counter_base": dict(self.counter_base), "overlap_min_s": self.overlap_min_s}
        if proven:
            self.emit_safe(af._row("takeover", "all", 0, lock_ms, None, None, info))
        else:
            self.emit_safe(self.meta_row("start", schema=af.SCHEMA, code_sha=code_sha(), argv=redact_argv(sys.argv[1:]), out_dir=str(self.out_dir), pid=os.getpid(),
                                         sockets=self.sockets, commitment=self.commitment, keys="none", sends="none", handover_failed=failed))
            fr = min([t for t in last.values() if t is not None] + [tail["stop_ms"] or lock_ms])
            self.emit_safe(af._row("gap", "all", 0, self.wall(), None, None,
                                   {"kind": "process_restart", "data_loss": True, "from_ms": fr, "to_ms": lock_ms, "count": 1, "clean_stop": tail["clean_stop"],
                                    "torn_tail": False, "prev_pid": old_pid, "reason": "handover_unproven", "failed": failed, "handover": info}))
        for r in out_rows:
            self.emit_safe(r)
        self.buffer.clear()
        self.mode = "live"
        if self._compress_at_takeover:
            self.compress = True
            self.compactor.start()
        self.takeover_info = {**info, "proven": proven, "failed": failed}
        self.last_hb = time.monotonic()
        self.heartbeat(self.wall())
        return self.takeover_info

    async def takeover(self, at_ms: int, deadline_ms: int, stop_old: Callable[[], dict[str, Any] | None], *, standby_started_ms: int,
                       acquire: Callable[[Path], int] = af.acquire_lock) -> tuple[int, dict[str, Any]]:
        """Wait for at_ms and readiness, stop the old writer, take the lock, complete. Returns (lock fd, takeover info)."""
        while True:
            now = self.wall()
            ok, why = self.ready(now)
            if now >= at_ms and ok:
                break
            if now >= deadline_ms:
                raise TakeoverAbort(f"not ready by the deadline: {','.join(why)}", TAKEOVER_EXIT)
            await asyncio.sleep(0.2)
        stopped = stop_old() or {}
        old_pid = stopped.get("pid")
        while True:
            try:
                fd = acquire(self.out_dir)
                break
            except af.LockHeld:
                pass
            try:
                holder = int((self.out_dir / af.LOCK_NAME).read_text().strip() or 0)
            except (OSError, ValueError):
                holder = 0
            if old_pid is not None and holder not in (0, old_pid, os.getpid()):
                raise TakeoverAbort(f"another writer (pid {holder}) holds the lock: nothing written", af.LOCK_HELD_EXIT)
            if self.wall() >= deadline_ms + int(KILL_WAIT_S * 1000) + 30_000:
                raise TakeoverAbort("the writer lock was not released: nothing written (the old writer was signalled; check it now)", TAKEOVER_EXIT)
            await asyncio.sleep(LOCK_POLL_S)
        return fd, self.complete_takeover(fd, self.wall(), standby_started_ms=standby_started_ms, prev_pid=old_pid)


def acquire_standby_lock(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(out_dir / STANDBY_LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise af.LockHeld(f"another standby holds {out_dir / STANDBY_LOCK_NAME}") from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def parse_per_prog(arg: str | None, cast) -> dict[str, Any] | None:
    if arg is None:
        return None
    arg = str(arg).strip()
    if "=" not in arg:
        return {p: cast(arg) for p in af.PROG_PROGRAM}
    out = {}
    for part in arg.split(","):
        k, _, v = part.partition("=")
        if k.strip() not in af.PROG_PROGRAM or not v.strip():
            raise ValueError(f"bad per-program value {part!r}")
        out[k.strip()] = cast(v.strip())
    return out


def check_production(out_dir: Path, now_ms: int, pinned: bool) -> None:
    if str(out_dir.resolve()) == PRODUCTION_TAPE or str(out_dir) == PRODUCTION_TAPE:
        if now_ms < EARLIEST_PRODUCTION_MS:
            raise ValueError("standby on the production tape is refused before 2026-11-11T14:00Z (no v2 inside HV6's clock, QP-RULING-1026 Q5)")
        if not pinned:
            raise ValueError("standby on the production tape needs pinned --max-disagree-rate and --min-overlap-rows (QP-RULING-1026 Q1)")


async def run_standby(args: argparse.Namespace, stop_old: Callable[[], dict[str, Any] | None] | None = None) -> int:
    out_dir = af.check_out_dir(args.out_dir)
    ws_urls = [af.check_ws_url(u) for u in (args.ws_url or [af.DEFAULT_PUBLIC_WS])]
    rpc_url = None if args.no_resolve else args.rpc_url
    if rpc_url:
        af.check_rpc_url(rpc_url)
    mdr, mor = parse_per_prog(args.max_disagree_rate, float), parse_per_prog(args.min_overlap_rows, int)
    pinned = mdr is not None and mor is not None
    check_production(out_dir, af.now_ms(), pinned)
    sb_fd = acquire_standby_lock(out_dir)
    feed = FeedV2(out_dir, ws_urls, {"pumpswap": args.sockets_pumpswap, "pumpfun": args.sockets_pumpfun}, args.commitment, standby=True,
                  overlap_min_s=args.overlap_min_s, min_overlap_rows=mor, max_disagree_rate=mdr, buffer_max=args.buffer_max,
                  min_free_pct=args.min_free_pct, rpc_url=rpc_url, compress=not args.no_compress)
    feed.standby_lock_fd = sb_fd
    standby_started = feed.wall()
    feed.warm_pool_mints(standby_started)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, feed.stop.set)
    lock_fd: int | None = None
    rc = 0

    async def do_takeover() -> None:
        nonlocal lock_fd, rc
        try:
            lock_fd, info = await feed.takeover(args.takeover_at_ms, args.takeover_deadline_ms, stop_old or make_stop_old(out_dir),
                                                standby_started_ms=standby_started)
            print(json.dumps({"takeover": {k: info[k] for k in ("proven", "failed", "lock_acquired_ms", "dedup", "agreement")}}), file=sys.stderr, flush=True)
        except TakeoverAbort as exc:
            print(f"!!!! FEED TAKEOVER ABORTED rc={exc.code}: {exc}", file=sys.stderr, flush=True)
            rc = exc.code
            feed.stop.set()

    task = asyncio.create_task(do_takeover())
    try:
        await feed.run(max_seconds=args.max_seconds)
    finally:
        feed.stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if lock_fd is not None:  # only a writer writes a stop row
            now = feed.wall()
            feed.flush_pending(now)
            feed.emit_safe(feed.meta_row("stop", **{k: v for k, v in feed.status(now).items() if k in ("rows_by_prog_type", "gaps", "lines_written", "rows_dropped_disk", "decode_errors")}))
            write_status(out_dir / af.STATUS_NAME, feed.status(now))
            feed.writer.close()
            os.close(lock_fd)
        os.close(sb_fd)
    if rc:
        return rc
    return af.FATAL_EXIT if feed.fatal else 0


def build_parser() -> argparse.ArgumentParser:
    ap = af.build_parser()
    ap.description = "Arena feed v2: v1 plus a gapless overlap takeover (--standby). Paper only."
    ap.add_argument("--standby", action="store_true", help="take over the tape from the running writer (module docstring)")
    ap.add_argument("--takeover-at-ms", type=int, default=None, help="standby: the earliest instant to stop the old writer (epoch ms)")
    ap.add_argument("--takeover-deadline-ms", type=int, default=None, help="standby: not ready by then -> exit 5, nothing written")
    ap.add_argument("--overlap-min-s", type=float, default=OVERLAP_MIN_S_FLOOR, help="floor 120 s (QP-RULING-1026 Q1)")
    ap.add_argument("--min-overlap-rows", default=None, help="pinned minimum n_old: an integer or pumpswap=n,pumpfun=n")
    ap.add_argument("--max-disagree-rate", default=None, help="pinned T_p: a number or pumpswap=x,pumpfun=y")
    ap.add_argument("--buffer-max", type=int, default=af.QUEUE_MAX)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.standby:
        return af.main([a for a in (argv if argv is not None else sys.argv[1:])])
    if args.takeover_at_ms is None or args.takeover_deadline_ms is None or args.takeover_deadline_ms <= args.takeover_at_ms:
        print("arena_feed_v2: --standby needs --takeover-at-ms < --takeover-deadline-ms", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run_standby(args))
    except af.LockHeld as exc:
        print(f"arena_feed_v2: refusing to start, {exc}", file=sys.stderr)
        return af.LOCK_HELD_EXIT
    except ValueError as exc:
        print(f"arena_feed_v2: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
