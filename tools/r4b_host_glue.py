#!/usr/bin/env python3
"""r4b_host_glue: the glue between the r4-b DRY decision host (tools/r4b_live_host, PR #1143) and the feed-swap library (tools/r4b_feed_merge, #1142).
PAPER / DRY ONLY: no wallet, no send, no key use beyond the Helius websocket URL (read in-process from a key file, never printed, never in argv). LIVE_BUILT is
False. Tests use synthetic rows, synthetic tape files in tmp dirs and fake sockets; no tape of the lab, no network.

Why (QP-RULING-1030 Addendum 5, conditions G1 and G2 on #1142)
  G1  no double count (Proof 1). tools.r4b_feed_merge.Merger measures its dedup horizon from the newest slot of EITHER source, so a public stream that lags more
      than the horizon (a stalled tape mirror that catches up in bulk across the next window) re-emits every window row. HostMerger fixes it two ways, both tested:
        * a key first seen from the Helius window is kept until the PUBLIC high-water slot has passed its slot (public_hw_slot() > slot + margin), then released to
          the ordinary horizon rule;
        * a public row that arrives below the horizon, whose key is unknown, and whose slot lies inside a slot range a healthy window covered, is NOT emitted. It is
          counted (below_horizon_covered) as data loss for that slot and keeps the seam open (see G2) for gap_guard_slots of public progress.
      The same rule covers a held key evicted by the hard key cap (unsafe_below).
  G2  public_hw_slot() = the highest slot the PUBLIC stream delivered with no gap. A public data_loss gap row (tools/arena_tape_schema.md `gap`) holds it at the
      high-water slot of the gap's start. A range is [hold + 1, first slot delivered after the gap - 1] (slot_jump and the decode / write / consume errors name it
      themselves). The hold is released when (a) a healthy Helius window covered the whole range, or (b) the public stream has progressed gap_guard_slots past the
      range: the range is then flagged for good (`flagged`, counted, never silently forgotten). While the hold stands mark_complete cannot close the seam
      (public_hw_slot < covered_from_slot), so the mark is not certified and the host fails closed. A public gap that never closes (a dead feed) holds for ever.
      A gap with data_loss false holds nothing. A gap before the first data row holds nothing (no history yet).

What LiveSource is (the duck-typed source of tools/r4b_live_host.DryHost)
  iter(source)             yields (tape_row, t_recv_ms) or None for an idle tick. Rows: the public tape (a TapeTail over the arena tape dir: closed hours
                           `tape-<H>.jsonl.zst` for catch-up, the open hour `tape-<H>.jsonl` by byte offset) and the Helius mark windows, deduplicated by HostMerger.
  mark_complete(T, sd)     a Completeness (tools.r4b_feed_merge) whose truth value IS .complete (a plain dataclass is always truthy; this one is not). The full record
                           stays readable (.reason, .ready_ms, ...). True only when the window is healthy, armed, covered, the processed tip reached sd + 1, there was no
                           silence, the seam is closed by public_hw_slot(), AND every row the window had received before the ready tip has been handed to the host
                           (rows_in_flight otherwise). The state is snapshotted BEFORE the queue is drained, so a tip seen implies the rows that preceded it are counted.
  failed(T)                a log-safe code once mark T can never be complete through the window (failure before ready, armed late, window over without ready), else None.
  buy_hold()               a log-safe code while the daily credit cap blocks the next window (exits are the host's business and are never held).
  tip_slot(commitment)     the window's processed tip while it is open; None for "confirmed" (there is no confirmed tip source) and for a closed window. A stale tip would
                           understate d_decide / d_would_send, so none is given instead.
  public_hw_slot()         the G2 value (None before the first public row).
The window runs in its own thread with its own event loop and hands rows over through a queue; ALL merger / hw / engine work stays on the host thread.
A window stays open complete_grace_s (3 s) after the host certified the mark, so the processed tip keeps moving while the host decides and would-sends (the d_*
latency fields read it); the brief's "close on complete" is therefore delayed by that grace (0 closes at once). It still closes at T + 10 s at the latest.

Not done here, disclosed
  * No live run, no socket, no network. The mirror of the arena tape on mal-fast-0 is another lane (claude/arena-tape-mirror). A missing hour of the tape is turned
    into a data_loss gap row only when a later hour is present and a grace has passed; a mirror that lags is then read as a gap. The thresholds are parameters.
  * The call into the host (HOST_CALL below) is written against the interface documented in PR #1143 and must be confirmed once #1143 has merged.
  * `python -I -m tools.r4b_host_glue` cannot import `tools` (isolated mode puts neither the cwd nor PYTHONPATH on sys.path; checked). Run it as
    `python -I tools/r4b_host_glue.py run ...` (the file inserts the repo root itself) or `python -s -m tools.r4b_host_glue run ...` from the repo root.
  * Counts and names only reach the logs. The decision log stays sealed.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import heapq
import importlib
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

if __package__ in (None, ""):  # `python tools/r4b_host_glue.py` from anywhere
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import r4b_feed_lag_probe as lp  # noqa: E402
from tools import r4b_feed_merge as fm  # noqa: E402

LIVE_BUILT = False
SCHEMA = "r4b_host_glue_v1"
HOST_CALL = "tools.r4b_live_host.DryHost(engine, log, source, out_dir=..., ntp_offset_ms=..., frozen_sha=...).start() then .run(max_marks)"  # confirm after #1143
SOURCE_PUBLIC, SOURCE_HELIUS = fm.SOURCE_PUBLIC, fm.SOURCE_HELIUS
MARK_S = 3600
GAP_GUARD_SLOTS = 1_500  # public progress (slots) after a lost range closes before its hold is released (about 10 minutes, the executor's buy hold after a gap)
PUBLIC_MARGIN_SLOTS = 64  # a Helius-first key is kept until the public high-water slot is past its slot by this much (arrival jitter between sockets)
SLOT_SANITY = 5_000_000  # a public row this far above the high-water slot is not believed (counted, ignored for the high-water)
COVERAGE_KEEP = 256
FLAGGED_MAX = 10_000
LOSS_KINDS_WITH_SLOTS = ("decode_error", "write_error", "consume_error")  # gap rows that name the lost slots [from_slot, to_slot]
CAP_DEFAULT = 50_000
CAP_REFUSE_ABOVE = 100_000  # the CLI refuses a per-day cap above this many credits
ZSTD_CMD = ("zstd", "-dc")
MIRROR_TTL_S = 5.0  # the mirror's status file is re-read at most this often (and once at the first look at each mark)
_AUTO = object()  # LiveSource(mirror_check=_AUTO): resolve tools.tape_mirror.mirror_problem lazily (PR #1147); None switches the check off
CHANGE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
CODE_RE = re.compile(r"[a-z0-9_]{1,32}")
HOUR_FILE_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def hour_key(h_s: int) -> str:
    return datetime.fromtimestamp(int(h_s), tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_from_key(k: str) -> int:
    return int(datetime.strptime(k, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def codeify(s: Any) -> str:
    """A failure text as a log-safe code [a-z0-9_]{1,32} (the host logs nothing else)."""
    out = re.sub(r"[^a-z0-9_]", "_", str(s).lower()).strip("_")[:32]
    return out if CODE_RE.fullmatch(out or "") else "unspecified"


# ------------------------------------------------------------------------------------------------------------------------------------ G1 and G2
class HostMerger(fm.Merger):
    """tools.r4b_feed_merge.Merger plus G1 and G2 (see the module docstring). Same offer(source, row, now_ms) contract. One thread (the host's)."""

    def __init__(self, *, gap_guard_slots: int = GAP_GUARD_SLOTS, public_margin_slots: int = PUBLIC_MARGIN_SLOTS, **kw: Any) -> None:
        super().__init__(**kw)
        self.gap_guard_slots, self.public_margin_slots = int(gap_guard_slots), int(public_margin_slots)
        self.pub_top = -1  # highest slot of any public transaction row
        self.unsafe_below = -1  # a held key at or below this slot was evicted by the key cap: an absent key proves nothing
        self._held: dict = {}  # key -> slot: Helius-first, the public copy not seen yet
        self._held_heap: list = []  # (slot, seq, key)
        self._seq = 0
        self._gaps: list[dict] = []  # lost ranges whose hold stands
        self.flagged: list[dict] = []  # every lost range ever recorded (bounded), with how it ended
        self._cov: list[list[int]] = []  # merged slot ranges a healthy window covered

    # -- G2: the public high-water slot
    def public_hw_slot(self) -> int | None:
        if self.pub_top < 0:
            return None
        hw = self.pub_top
        for g in self._gaps:
            hw = min(hw, g["hold"])
        return hw if hw >= 0 else None

    def open_gaps(self) -> list[dict]:
        return [dict(g) for g in self._gaps]

    def note_window(self, cov_from: int | None, hi_slot: int | None) -> None:
        """A healthy window delivered the slots [cov_from, hi_slot] (its rows were offered). Merged into the coverage; may release holds."""
        if cov_from is None or hi_slot is None or hi_slot < cov_from:
            return
        self._cov.append([int(cov_from), int(hi_slot)])
        self._cov.sort()
        merged: list[list[int]] = []
        for lo, hi in self._cov:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        self._cov = merged[-COVERAGE_KEEP:]
        self._update_gaps()

    def covered(self, lo: int, hi: int) -> bool:
        return any(a <= lo and hi <= b for a, b in self._cov)

    def _new_range(self, hold: int, lo: int, hi: int | None, state: str, kind: str, prog: Any, from_ms: Any, why: str, cover_releases: bool = True) -> dict:
        g = {"kind": kind, "prog": prog, "from_ms": from_ms, "hold": int(hold), "lo": int(lo), "hi": hi, "state": state, "closed_top": max(self.pub_top, hi) if state == "closed" else None,
             "cover_releases": cover_releases, "why": why, "ended": None}
        self._gaps.append(g)
        self.flagged.append(g)
        del self.flagged[:-FLAGGED_MAX]
        self.stats["lost_ranges"] += 1
        return g

    def _release(self, g: dict, how: str) -> None:
        if g in self._gaps:
            self._gaps.remove(g)
        g["ended"] = how
        self.stats[f"hold_released_{how}"] += 1

    def _update_gaps(self, data_slot: int | None = None) -> None:
        for g in list(self._gaps):
            if g["state"] == "resume" and data_slot is not None and data_slot > g["hold"]:
                g["hi"], g["state"], g["closed_top"] = data_slot - 1, "closed", self.pub_top
            if g["state"] != "closed":
                continue
            if g["hi"] < g["lo"]:
                self._release(g, "no_whole_slot")
            elif g["cover_releases"] and self.covered(g["lo"], g["hi"]):
                self._release(g, "covered")
            elif self.pub_top >= g["closed_top"] + self.gap_guard_slots:
                self._release(g, "aged_flagged")  # not covered: the range stays in `flagged` for good

    def _public_gap(self, row: Mapping[str, Any]) -> None:
        self.stats["pub_gap_rows"] += 1
        if row.get("data_loss") is False:
            self.stats["pub_gap_benign"] += 1
            return
        if self.pub_top < 0:
            self.stats["pub_gap_before_data"] += 1  # no history delivered yet: nothing to hold
            return
        kind = row.get("kind") if isinstance(row.get("kind"), str) else "unknown"
        prog, closed, from_ms = row.get("prog"), row.get("closed"), row.get("from_ms")
        fs, ts = row.get("from_slot"), row.get("to_slot")
        if closed is True:
            for g in self._gaps:
                if g["state"] == "open" and (g["prog"], g["kind"], g["from_ms"]) == (prog, kind, from_ms):
                    g["state"] = "resume"  # the feed delivers again: the range ends at the next row above the hold
                    self._update_gaps()
                    return
        top = self.pub_top
        if kind == "slot_jump" and _is_int(fs) and _is_int(ts) and ts > fs:
            hold = min(top, fs)
            self._new_range(hold, hold + 1, ts - 1, "closed", kind, prog, from_ms, "gap")
        elif kind in LOSS_KINDS_WITH_SLOTS and _is_int(fs) and _is_int(ts) and ts >= fs:
            hold = min(top, fs - 1)
            self._new_range(hold, hold + 1, ts, "closed", kind, prog, from_ms, "gap")
        else:
            self._new_range(top, top + 1, None, "open" if closed is False else "resume", kind, prog, from_ms, "gap")
        self._update_gaps()

    def _public_row(self, row: Mapping[str, Any]) -> None:
        """Bookkeeping for a PUBLIC row, before the dedup decision (a duplicate is still a delivery)."""
        if row.get("type") == "gap":
            self._public_gap(row)
            return
        slot = row.get("slot")
        if not _is_int(slot) or not isinstance(row.get("signature"), str) or not row.get("signature"):
            return
        if self.pub_top >= 0 and slot > self.pub_top + SLOT_SANITY:
            self.stats["pub_slot_unbelieved"] += 1
            return
        self.pub_top = max(self.pub_top, slot)
        self._update_gaps(slot)

    # -- G1: dedup
    def offer(self, source: str, row: Mapping[str, Any], now_ms: int | None = None) -> dict | None:
        now = self.clock_ms() if now_ms is None else int(now_ms)
        key = fm.row_key(row)
        if source == SOURCE_PUBLIC:
            self._public_row(row)
            if key is not None and key not in self._keys:
                slot = row.get("slot")
                if _is_int(slot) and (slot < self.max_slot - self.horizon_slots or slot <= self.unsafe_below) and self.covered(slot, slot):
                    # absent key, behind the horizon, inside a window-covered range: it was either evicted or never seen by the window. It is NOT emitted again.
                    self.stats["offered_public"] += 1
                    self.stats["below_horizon_covered"] += 1
                    # the hold is the start of the covering window's range, minus 1: that window's seam (public_hw_slot >= covered_from_slot) cannot close
                    start = next(a for a, b in self._cov if a <= slot <= b)
                    self._new_range(start - 1, slot, slot, "closed", "below_horizon_covered", row.get("prog"), None, "below_horizon_covered", cover_releases=False)
                    self._update_gaps()
                    return None
        e = super().offer(source, row, now)
        if key is not None:
            ent = self._keys.get(key)
            if ent is not None:
                if source == SOURCE_HELIUS and e is not None and len(ent[3]) == 1:
                    slot = ent[2]
                    if slot is not None:
                        self._held[key] = slot
                        self._seq += 1
                        heapq.heappush(self._held_heap, (slot, self._seq, key))
                elif source == SOURCE_PUBLIC and key in self._held:
                    del self._held[key]  # matched: the public copy has been seen
        return e

    def _release_held(self) -> None:
        hw = self.public_hw_slot()
        if hw is None:
            return
        while self._held_heap and self._held_heap[0][0] + self.public_margin_slots < hw:
            slot, _, key = heapq.heappop(self._held_heap)
            if self._held.get(key) == slot:
                del self._held[key]
                ent = self._keys.get(key)
                if ent is not None and len(ent[3]) == 1:
                    self.stats["helius_only_released"] += 1  # the public stream passed this slot and never delivered the row (fork or public loss)

    def _purge(self) -> None:
        """The base rule (horizon behind the newest slot of either source, hard key cap), except that a Helius-first key whose public copy has not been seen is kept
        until the public high-water slot has passed it (G1). Held keys are skipped by moving them to the back; the cap still wins (counted, and it raises unsafe_below)."""
        self._release_held()
        lo = self.max_slot - self.horizon_slots
        stuck = 0
        while self._keys:
            k, e = next(iter(self._keys.items()))
            over = len(self._keys) > self.max_keys
            if k in self._held and not over:
                if e[2] is not None and e[2] < lo:
                    self._keys.move_to_end(k)
                    stuck += 1
                    if stuck >= len(self._held):
                        break
                    continue
                break
            if over:
                self.stats["evicted_cap"] += 1
                if k in self._held:
                    self.stats["held_evicted_cap"] += 1
                    self.unsafe_below = max(self.unsafe_below, e[2] if e[2] is not None else -1)
                    del self._held[k]
            elif e[2] is not None and e[2] < lo:
                self.stats["evicted_horizon"] += 1
            else:
                break
            del self._keys[k]
            stuck = 0

    def host_counts(self) -> dict:
        """Names and counts for the run summary (no row content)."""
        why = Counter(g["why"] for g in self.flagged)
        ended = Counter(g["ended"] or "holding" for g in self.flagged)
        return {"pub_top": self.pub_top, "public_hw_slot": self.public_hw_slot(), "held_keys": len(self._held), "unsafe_below": self.unsafe_below,
                "active_holds": len(self._gaps), "lost_ranges": dict(why), "lost_range_ends": dict(ended),
                "below_horizon_covered": self.stats["below_horizon_covered"], "held_evicted_cap": self.stats["held_evicted_cap"],
                "helius_only_released": self.stats["helius_only_released"], "pub_gap_rows": self.stats["pub_gap_rows"],
                "pub_gap_benign": self.stats["pub_gap_benign"], "coverage_ranges": len(self._cov)}


# ------------------------------------------------------------------------------------------------------------------------------------ the public tape
def tape_gap_row(kind: str, from_ms: int, to_ms: int, extra: Mapping[str, Any] | None = None) -> dict:
    """A tape-format `gap` row the reader writes for a loss it saw itself (a missing hour, a bad line, a decompressor failure): data_loss true, closed true."""
    return {"v": 1, "type": "gap", "prog": "all", "slot": 0, "t_recv_ms": int(to_ms), "signature": None, "event_index": None, "kind": kind, "data_loss": True,
            "from_ms": int(from_ms), "to_ms": int(to_ms), "closed": True, **dict(extra or {})}


class TapeTail:
    """The arena tape (tools/arena_tape_schema.md) as one ordered stream of rows. From start_hour_s on, hour by hour: the open `tape-<H>.jsonl` by byte offset
    (complete lines only), or the closed `tape-<H>.jsonl.zst` through `zstd -dc` (skipping the bytes already read from the .jsonl: the two are byte-identical).
    An hour is left when its file is final (its end + final_after_s has passed and no complete line is left), or early (its end + early_settle_s) when the next
    hour's file exists; the early-left hour is read once more at end + final_after_s for lines appended late. A missing hour with a later hour present, after
    missing_grace_s, becomes a data_loss gap row (kind tape_hour_missing). A bad line (not JSON, a NUL, not an object) is counted and becomes one data_loss gap row
    per burst (kind tape_bad_line); a decompressor failure becomes tape_zst_error. Counts only; no row is kept."""

    CHUNK = 4 << 20

    def __init__(self, tape_dir: str | os.PathLike, start_hour_s: int, *, clock_ms: Callable[[], int] | None = None, final_after_s: float = 125.0,
                 early_settle_s: float = 2.0, missing_grace_s: float = 600.0, zstd_cmd: Sequence[str] = ZSTD_CMD) -> None:
        self.dir = Path(tape_dir)
        self.hour = int(start_hour_s) // MARK_S * MARK_S
        self.offset = 0
        self.clock = clock_ms or _wall_ms
        self.final_after_s, self.early_settle_s, self.missing_grace_s = float(final_after_s), float(early_settle_s), float(missing_grace_s)
        self.zstd_cmd = tuple(zstd_cmd)
        self.stats: Counter = Counter()
        self._z: subprocess.Popen | None = None
        self._bad_burst = False
        self._last_ms = 0
        self._trail: list[tuple[int, int]] = []  # (hour, offset) left early, to be read again once final

    def _names(self, h: int) -> tuple[Path, Path]:
        k = hour_key(h)
        return self.dir / f"tape-{k}.jsonl", self.dir / f"tape-{k}.jsonl.zst"

    def _max_hour_present(self) -> int | None:
        best = None
        try:
            with os.scandir(self.dir) as it:
                for ent in it:
                    m = HOUR_FILE_RE.match(ent.name)
                    if m:
                        h = hour_from_key(m.group(1))
                        best = h if best is None else max(best, h)
        except OSError:
            return None
        return best

    def _after(self, h: int, s: float) -> bool:
        return self.clock() >= (h + MARK_S + s) * 1000

    def _parse(self, line: bytes, out: list) -> None:
        if not line.strip():
            return
        try:
            row = json.loads(line, strict=False)
        except (ValueError, UnicodeDecodeError):
            row = None
        if isinstance(row, dict):
            self._bad_burst = False
            t = row.get("t_recv_ms")
            if _is_int(t):
                self._last_ms = t
            self.stats["rows"] += 1
            out.append(row)
            return
        self.stats["bad_lines"] += 1
        if not self._bad_burst:
            self._bad_burst = True
            out.append(tape_gap_row("tape_bad_line", self._last_ms, self._last_ms))

    def _read_jsonl(self, path: Path, n: int, h: int) -> tuple[list, bool]:
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            return [], False  # compacted under us: the next call takes the .zst branch from the same offset
        with f:
            f.seek(self.offset)
            buf = f.read(self.CHUNK)
            at_eof = self.offset + len(buf) >= os.fstat(f.fileno()).st_size
        rows: list = []
        pos = 0
        while len(rows) < n:
            nl = buf.find(b"\n", pos)
            if nl < 0:
                break
            self._parse(buf[pos:nl], rows)
            pos = nl + 1
        self.offset += pos
        drained = buf.find(b"\n", pos) < 0 and len(rows) < n
        if not (drained and at_eof):
            return rows, False
        nxt_exists = self._names(h + MARK_S)[0].exists() or self._names(h + MARK_S)[1].exists()
        if self._after(h, self.final_after_s):
            if len(buf) - pos > 0:  # a torn last line in a final file
                self.stats["torn_tail"] += 1
                self._parse(buf[pos:] + b"\x00", rows)  # counted as a bad line
                self.offset += len(buf) - pos
            return rows, True
        if nxt_exists and self._after(h, self.early_settle_s):
            if len(buf) - pos == 0:
                self._trail.append((h, self.offset))
                self.stats["left_early"] += 1
                return rows, True
        return rows, False

    def _read_zst(self, path: Path, n: int, h: int) -> tuple[list, bool]:
        rows: list = []
        if self._z is None:
            if not path.exists():
                return rows, False
            try:
                self._z = subprocess.Popen([*self.zstd_cmd, str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError:
                self.stats["zst_error"] += 1
                rows.append(tape_gap_row("tape_zst_error", self._last_ms, self._last_ms, {"hour": hour_key(h)}))
                return rows, True
            remaining = self.offset
            while remaining > 0:
                chunk = self._z.stdout.read(min(remaining, 1 << 20))  # type: ignore[union-attr]
                if not chunk:
                    break
                remaining -= len(chunk)
        while len(rows) < n:
            line = self._z.stdout.readline()  # type: ignore[union-attr]
            if not line:
                rc = self._z.wait()
                self._z.stdout.close()  # type: ignore[union-attr]
                self._z = None
                if rc != 0:
                    self.stats["zst_error"] += 1
                    rows.append(tape_gap_row("tape_zst_error", self._last_ms, self._last_ms, {"hour": hour_key(h)}))
                return rows, True
            self.offset += len(line)
            if not line.endswith(b"\n"):
                self.stats["torn_tail"] += 1
                self._parse(line + b"\x00", rows)
            else:
                self._parse(line, rows)
        return rows, False

    def _read_final(self, h: int, offset: int) -> list:
        """The rest of a final hour from `offset` (an hour left early): the .jsonl if it is still there, else the .zst."""
        jl, zs = self._names(h)
        data = b""
        try:
            with open(jl, "rb") as f:
                f.seek(offset)
                data = f.read()
        except FileNotFoundError:
            if zs.exists():
                try:
                    p = subprocess.run([*self.zstd_cmd, str(zs)], capture_output=True, timeout=300, check=True)
                    data = p.stdout[offset:]
                except (OSError, subprocess.SubprocessError):
                    self.stats["zst_error"] += 1
                    return [tape_gap_row("tape_zst_error", self._last_ms, self._last_ms, {"hour": hour_key(h)})]
            else:
                self.stats["trail_lost"] += 1
                return []
        rows: list = []
        for line in data.split(b"\n"):
            self._parse(line, rows)
        self.stats["trail_rows"] += len(rows)
        return rows

    def _advance(self) -> None:
        self.hour += MARK_S
        self.offset = 0
        self._z = None
        self._bad_burst = False

    def poll(self, limit: int = 500) -> list[dict]:
        """Up to `limit` new rows (possibly none). Never blocks."""
        out: list = []
        for item in list(self._trail):
            if self._after(item[0], self.final_after_s):
                self._trail.remove(item)
                out.extend(self._read_final(*item))
        while len(out) < limit:
            h = self.hour
            jl, zs = self._names(h)
            if self._z is None and jl.exists():
                rows, adv = self._read_jsonl(jl, limit - len(out), h)
            elif self._z is not None or zs.exists():
                rows, adv = self._read_zst(zs, limit - len(out), h)
            else:
                later = self._max_hour_present()
                if later is not None and later > h and self._after(h, self.missing_grace_s):
                    self.stats["missing_hours"] += 1
                    rows, adv = [tape_gap_row("tape_hour_missing", h * 1000, (h + MARK_S) * 1000, {"hour": hour_key(h)})], True
                else:
                    rows, adv = [], False
            out.extend(rows)
            if adv:
                self._advance()
                continue
            if not rows:
                break
        return out


# ------------------------------------------------------------------------------------------------------------------------------------ the source
class Certified(fm.Completeness):
    """tools.r4b_feed_merge.Completeness whose truth value is .complete. (A plain dataclass instance is always truthy, so `if source.mark_complete(T, sd)` on
    the library's own result would certify every mark.)"""

    def __bool__(self) -> bool:
        return bool(self.complete)


def certified(c: fm.Completeness, reason: str | None = None) -> Certified:
    return Certified(False if reason else c.complete, reason or c.reason, c.ready_ms, c.ready_tip_slot, c.covered_from_slot, c.ready_after_mark_ms)


class LockedCap:
    """DailyCreditCap shared by the window thread (add / can_open / window_done) and the host thread (buy_hold): one lock around every call."""

    def __init__(self, cap: fm.DailyCreditCap) -> None:
        self._cap, self._lock = cap, threading.RLock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._cap, name)
        if not callable(attr):
            return attr

        def locked(*a: Any, **kw: Any) -> Any:
            with self._lock:
                return attr(*a, **kw)
        return locked


class MirrorUnhealthy(RuntimeError):
    """The tape mirror is not fresh at start (tools.tape_mirror.mirror_problem returned a code): the host does not start."""


def default_mirror_check(tape_dir: str | os.PathLike) -> Callable[[int | None], str | None]:
    """mirror_problem(dst, need_hours=...) of tools/tape_mirror.py (PR #1147), imported lazily at the first call so this module works before #1147 merges. If the
    module cannot be imported the answer is a problem code (fail closed), never None."""
    def check(need_hours: int | None = None) -> str | None:
        try:
            mod = importlib.import_module("tools.tape_mirror")
            return mod.mirror_problem(tape_dir, need_hours=need_hours)
        except ImportError:
            return "mirror_module_missing"
        except Exception:  # noqa: BLE001 - fail closed
            return "mirror_check_error"
    return check


@dataclasses.dataclass
class _Win:
    mark: int
    window: fm.HeliusWindow
    started_ms: int
    complete_at: int | None = None
    complete_sent: bool = False
    finished: bool = False
    seam_pending: tuple | None = None
    thread_error: str | None = None


def snapshot(st: fm.WindowState) -> fm.WindowState:
    return dataclasses.replace(st, tips=list(st.tips), acks=set(st.acks), counters=dict(st.counters))


def window_coverage(st: fm.WindowState) -> tuple[int, int] | None:
    """(first slot, last slot) the window delivered in full: from covered_from_slot to the newest processed tip it saw before any failure, minus 1 (the logs of slot
    n arrive before the tip notice n + 1: the lane's ASSUMPTION, measured by seam_report in the DRY run)."""
    cov = fm.covered_from_slot(st)
    if cov is None:
        return None
    tips = [s for r, s in st.tips if st.armed_ms is not None and r >= st.armed_ms and (st.failure_ms is None or r <= st.failure_ms)]
    if not tips:
        return None
    hi = max(tips) - 1
    return (cov, hi) if hi >= cov else None


def thread_spawn(w: fm.HeliusWindow) -> threading.Thread:
    def target() -> None:
        try:
            asyncio.run(w.run())
        except BaseException as exc:  # noqa: BLE001 - recorded by class; the window's own failure code stands if it has one
            w.state.fail("thread_error", _wall_ms())
            w.thread_error = type(exc).__name__  # type: ignore[attr-defined]
    th = threading.Thread(target=target, name=f"r4b-window-{w.mark}", daemon=True)
    th.start()
    return th


def inline_spawn(w: fm.HeliusWindow) -> None:
    """Run the window to its end on the calling thread (tests with a fake socket on a virtual clock)."""
    asyncio.run(w.run())


class LiveSource:
    """See the module docstring. The host thread calls everything except the window threads' on_rows (which only enqueues)."""

    def __init__(self, *, tape_dir: str | os.PathLike | None = None, cap: fm.DailyCreditCap, url: str | Callable[[], str] | None = None, key_file: str | None = None,
                 commitment: str = "processed", merger: HostMerger | None = None, out_dir: str | os.PathLike | None = None, start_hour_s: int | None = None,
                 catch_up_hours: int = 0, now_ms: Callable[[], int] | None = None, sleep: Callable[[float], None] | None = None,
                 window_factory: Callable[..., fm.HeliusWindow] | None = None, spawn: Callable[[fm.HeliusWindow], Any] | None = None, tape: Any = None,
                 ntp_runner: fm.Runner | None = None, require_ntp: bool = True, sd_fn: Callable[[int], int | None] | None = None,
                 complete_grace_s: float = 3.0, lead_s: float = 5.0, idle_s: float = 0.05, batch: int = 500, seam_margin_slots: int = 0,
                 stop_when: Callable[[], bool] | None = None, mirror_check: Any = _AUTO, mirror_need_hours: int | None = None,
                 mirror_ttl_s: float = MIRROR_TTL_S) -> None:
        if commitment not in fm.COMMITMENTS:
            raise ValueError(f"commitment must be one of {fm.COMMITMENTS}")
        if tape is None and tape_dir is None:
            raise ValueError("a tape_dir (or a tape reader) is required")
        if window_factory is None and url is None and key_file is None:
            raise ValueError("a url, a key_file or a window_factory is required")
        self.cap = cap if isinstance(cap, LockedCap) else LockedCap(cap)
        self.merger = merger if merger is not None else HostMerger()
        self.commitment = commitment
        self.now_ms = now_ms or _wall_ms
        self.sleep = sleep or time.sleep
        self._url = url if url is not None else (lambda: fm.read_url(key_file))  # type: ignore[arg-type]  # read in-process, inside the window, never stored
        self._factory, self._spawn = window_factory, spawn or thread_spawn
        self.tape_dir, self._tape, self._start_hour_s, self.catch_up_hours = tape_dir, tape, start_hour_s, int(catch_up_hours)
        self.ntp_runner, self.require_ntp, self.ntp = ntp_runner, bool(require_ntp), None
        self.sd_fn, self.complete_grace_ms = sd_fn, int(complete_grace_s * 1000)
        self.lead_ms, self.idle_s, self.batch, self.seam_margin = int(lead_s * 1000), float(idle_s), int(batch), int(seam_margin_slots)
        self.stop_when = stop_when
        if mirror_check is _AUTO:
            mirror_check = default_mirror_check(tape_dir) if tape_dir is not None else None
        self.mirror_check: Callable[[int | None], str | None] | None = mirror_check
        self.mirror_need_hours, self.mirror_ttl_ms = mirror_need_hours, int(mirror_ttl_s * 1000)
        self._mirror: tuple[int, str | None] | None = None  # (checked at ms, problem)
        self._mirror_marks: set[int] = set()
        self.out = Path(out_dir) / "glue" if out_dir is not None else None
        self.events_path = self.out / "events.jsonl" if self.out else None
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._pending: deque = deque()
        self._wins: "OrderedDict[int, _Win]" = OrderedDict()
        self._sd_hint: dict[int, int] = {}
        self.certs: dict[int, fm.Completeness] = {}
        self._last_reason: dict[int, str] = {}
        self.first_mark: int | None = None
        self._started = self._stop = False
        self._buy_hold_logged: str | None = None
        self.stats: Counter = Counter()

    # -- files and events (names and counts only)
    def _event(self, kind: str, **kw: Any) -> None:
        self.stats[f"event_{kind}"] += 1
        if self.events_path is None:
            return
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.events_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps({"schema": SCHEMA, "event": kind, "ms": self.now_ms(), **kw}, sort_keys=True) + "\n").encode())
        finally:
            os.close(fd)

    def _write_credit_days(self) -> None:
        if self.out is None:
            return
        self.out.mkdir(parents=True, exist_ok=True)
        tmp = self.out / "credit-days.jsonl.tmp"
        tmp.write_text(self.cap.jsonl(), encoding="utf-8")
        os.replace(tmp, self.out / "credit-days.jsonl")

    # -- start
    def start(self) -> None:
        if self._started:
            return
        if self.require_ntp:
            self.ntp = fm.require_synced(runner=self.ntp_runner)  # raises ClockNotSynced: the wall-time fields assume a synced clock
        if self.mirror_check is not None:
            why = self._mirror_problem(force=True, need_hours=self.mirror_need_hours)
            if why is not None:
                self._event("mirror_refused_start", problem=why)
                raise MirrorUnhealthy(f"the tape mirror is not fresh at start: {why}")
        now = self.now_ms()
        self.first_mark = (now // 1000 // MARK_S + 1) * MARK_S
        if self._tape is None:
            h0 = self._start_hour_s if self._start_hour_s is not None else now // 1000 // MARK_S * MARK_S - self.catch_up_hours * MARK_S
            self._tape = TapeTail(self.tape_dir, h0, clock_ms=self.now_ms)  # type: ignore[arg-type]
        self._started = True
        self._event("start", first_mark=self.first_mark, commitment=self.commitment, cap_credits=self.cap.cap, ntp=self.ntp.as_record() if self.ntp else None,
                    start_hour=hour_key(self._tape.hour) if hasattr(self._tape, "hour") else None, trigger="certified")

    def request_stop(self) -> None:
        self._stop = True

    # -- windows
    def _make_window(self, T: int) -> fm.HeliusWindow:
        on_rows = lambda rows, recv_ms, _T=T: self._q.put((_T, rows, recv_ms))  # noqa: E731
        if self._factory is not None:
            return self._factory(T, commitment=self.commitment, cap=self.cap, on_rows=on_rows, pool_mints=self.merger.pool_mints)
        return fm.HeliusWindow(T, self._url, commitment=self.commitment, cap=self.cap, on_rows=on_rows, pool_mints=self.merger.pool_mints)

    def _maybe_start_windows(self, now: int) -> None:
        base = now // 1000 // MARK_S * MARK_S
        for T in (base, base + MARK_S):
            if self.first_mark is not None and T < self.first_mark or T in self._wins:
                continue
            if (T - lp.PRE_S) * 1000 - self.lead_ms <= now < (T + lp.POST_S) * 1000:
                w = self._make_window(T)
                self._wins[T] = _Win(T, w, now)
                while len(self._wins) > 48:
                    self._wins.popitem(last=False)
                self._spawn(w)
                self._event("window_start", mark=T)

    def _drain_windows(self) -> int:
        n, now = 0, self.now_ms()
        while True:
            try:
                _, rows, _ = self._q.get_nowait()
            except queue.Empty:
                return n
            n += 1
            for r in rows:
                e = self.merger.offer(SOURCE_HELIUS, r, now)
                if e is not None:
                    self._pending.append(e)

    def _tick(self, now: int) -> None:
        self._maybe_start_windows(now)
        snaps = {T: snapshot(w.window.state) for T, w in self._wins.items()}  # BEFORE the drain: a tip in the snapshot implies the rows before it were queued
        self._drain_windows()
        for T, w in self._wins.items():
            s = snaps[T]
            cov = window_coverage(s)
            if cov is not None:
                self.merger.note_window(*cov)
            if w.complete_at is not None and not w.complete_sent and now >= w.complete_at + self.complete_grace_ms:
                w.window.complete()
                w.complete_sent = True
            if s.closed_ms is not None and not w.finished:
                w.finished = True
                self._write_credit_days()
                rec = s.as_record()
                rec.pop("tips", None)
                self._event("window_end", mark=T, record=rec, covered=list(cov) if cov else None)
                if cov is not None:
                    w.seam_pending = cov
            if w.seam_pending is not None:
                hw = self.merger.public_hw_slot()
                if hw is not None and hw >= w.seam_pending[1] + 16:
                    self._event("seam", mark=T, **self.merger.seam_report(*w.seam_pending))
                    w.seam_pending = None
        hold = self.cap.buy_hold()
        if hold != self._buy_hold_logged:
            self._buy_hold_logged = hold
            self._event("buy_hold", detail=hold)

    def _read_public(self, now: int) -> None:
        for r in self._tape.poll(self.batch):  # type: ignore[union-attr]
            e = self.merger.offer(SOURCE_PUBLIC, r, now)
            if e is not None:
                self._pending.append(e)

    # -- iteration
    def __iter__(self) -> Iterable[tuple[dict, int] | None]:
        if not self._started:
            self.start()
        try:
            while not self._stop and not (self.stop_when is not None and self.stop_when()):
                now = self.now_ms()
                self._tick(now)
                if not self._pending:
                    self._read_public(now)
                if self._pending:
                    row = self._pending.popleft()
                    t = row.get("t_recv_ms")
                    yield row, (t if _is_int(t) else now)
                    continue
                self.sleep(self.idle_s)
                yield None
        finally:
            for w in self._wins.values():
                if not w.finished:
                    w.window.complete()
            self._write_credit_days()
            self._event("stop", report=self.report())

    # -- the tape mirror (PR #1147): a stale or broken mirror makes the PUBLIC side unhealthy, so the seam cannot close through it
    def _mirror_problem(self, *, force: bool = False, need_hours: int | None = None) -> str | None:
        """None when the mirror is fresh (or no check is configured); else its problem code. Cached mirror_ttl_s; forced at the first look at each mark and at start.
        A check that raises is a problem (fail closed)."""
        if self.mirror_check is None:
            return None
        now = self.now_ms()
        if not force and self._mirror is not None and now - self._mirror[0] < self.mirror_ttl_ms:
            return self._mirror[1]
        try:
            why = self.mirror_check(need_hours)
        except Exception:  # noqa: BLE001
            why = "mirror_check_error"
        if why is not None and not isinstance(why, str):
            why = "mirror_check_error"
        if why is not None:
            self.stats["mirror_problem_checks"] += 1
            self.stats[f"mirror_problem_{codeify(why)}"] += 1
        if self._mirror is None or self._mirror[1] != why:
            self._event("mirror", problem=why)
        self._mirror = (now, why)
        return why

    def _mirror_for_mark(self, T: int) -> str | None:
        first = T not in self._mirror_marks
        self._mirror_marks.add(T)
        return self._mirror_problem(force=first)

    # -- the host interface
    def _sd_of(self, T: int, sd: int | None = None) -> int | None:
        if sd is not None:
            self._sd_hint[T] = int(sd)
            return int(sd)
        if self.sd_fn is not None:
            try:
                v = self.sd_fn(T)
            except Exception:  # noqa: BLE001 - an unreadable clock is "unknown"
                v = None
            if v is not None:
                self._sd_hint[T] = int(v)
        return self._sd_hint.get(T)

    def _eval(self, T: int, sd: int | None, snap: fm.WindowState) -> fm.Completeness:
        return fm.mark_complete(T, sd, snap, public_hw_slot=self.merger.public_hw_slot(), seam_margin_slots=self.seam_margin)

    def mark_complete(self, T: int, sd: int | None) -> Certified:
        """The full tools.r4b_feed_merge.mark_complete result for mark T, truthy only when complete AND every row the window received has reached the host."""
        T = int(T)
        bad = self._mirror_for_mark(T)
        w = self._wins.get(T)
        if bad is not None:  # fail closed: the history below the window comes from the public stream
            sd = self._sd_of(T, sd)
            cert = certified(fm.Completeness(False, f"mirror:{bad}"))
            self.certs[T] = cert
            if self._last_reason.get(T) != cert.reason:
                self._last_reason[T] = cert.reason
                self._event("mark_complete", mark=T, complete=False, reason=cert.reason, sd=sd)
            return cert
        if w is None:
            return certified(fm.Completeness(False, "no_window"))
        sd = self._sd_of(T, sd)
        snap = snapshot(w.window.state)  # BEFORE the drain
        self._drain_windows()
        c = self._eval(T, sd, snap)
        cert = certified(c)
        if c.complete and self._pending:
            cert = certified(c, "rows_in_flight")  # accepted rows are still on their way to the host: not yet "in the stream the host has been handed"
        if cert.complete and w.complete_at is None:
            w.complete_at = self.now_ms()
        self.certs[T] = cert
        if self._last_reason.get(T) != cert.reason:
            self._last_reason[T] = cert.reason
            self._event("mark_complete", mark=T, complete=cert.complete, reason=cert.reason, sd=sd, ready_after_mark_ms=cert.ready_after_mark_ms,
                        covered_from_slot=cert.covered_from_slot, public_hw_slot=self.merger.public_hw_slot())
        return cert

    def failed(self, T: int) -> str | None:
        T = int(T)
        bad = self._mirror_for_mark(T)
        if bad is not None:
            # the reason is `mirror:<problem>` in mark_complete; the host logs only [a-z0-9_]{1,32}, so the code here is `mirror_<problem>`
            return codeify(f"mirror_{bad}")
        w = self._wins.get(T)
        if w is None:
            return None
        st = w.window.state
        sd = self._sd_of(T)
        snap = snapshot(st)
        if sd is None:
            f_ms = snap.failure_ms
            return codeify(snap.failure) if snap.failure is not None and (f_ms is None or f_ms < T * 1000) else None
        c = self._eval(T, sd, snap)
        if c.reason.startswith("window_failed:"):
            return codeify(c.reason.split(":", 1)[1])
        if c.reason == "armed_late":
            return "armed_late"
        if c.reason == "silence":
            return "silence"
        if snap.closed_ms is not None and not c.complete and c.reason in ("tip_below_sd_plus_1", "not_armed"):
            return codeify(f"window_{snap.close_reason or 'over'}")
        return None

    def buy_hold(self) -> str | None:
        r = self.cap.buy_hold()
        if r is None:
            return None
        return "credit_cap_no_room" if "no_room" in r else "credit_cap_reached"

    def tip_slot(self, commitment: str = "processed") -> int | None:
        if commitment != "processed" or not self._wins:
            return None
        st = next(reversed(self._wins.values())).window.state
        tips = st.tips
        return int(tips[-1][1]) if (tips and st.closed_ms is None) else None

    def public_hw_slot(self) -> int | None:
        return self.merger.public_hw_slot()

    def report(self) -> dict:
        """Names and counts only."""
        return {"schema": SCHEMA, "merger": self.merger.counts(), "host_merger": self.merger.host_counts(), "tape": dict(getattr(self._tape, "stats", {})),
                "windows": len(self._wins), "marks_certified": sum(1 for c in self.certs.values() if c.complete), "marks_seen": len(self.certs),
                "credit_days": self.cap.day_lines(), "mirror_problem_now": None if self._mirror is None else self._mirror[1], "stats": dict(self.stats)}


# ------------------------------------------------------------------------------------------------------------------------------------ CLI
class Refused(Exception):
    pass


def check_args(a: argparse.Namespace) -> None:
    """Argument refusals that need no host (before git, NTP or any file is touched)."""
    if a.trigger != "certified":
        raise Refused("only --trigger certified is built here (the engine's own guard trigger is the host's)")
    if not isinstance(a.daily_credit_cap, (int, float)) or not math.isfinite(a.daily_credit_cap) or a.daily_credit_cap <= 0:
        raise Refused("--daily-credit-cap must be a positive finite number")
    if a.daily_credit_cap > CAP_REFUSE_ABOVE:
        raise Refused(f"--daily-credit-cap {a.daily_credit_cap:g} is above {CAP_REFUSE_ABOVE} credits a day: refused")
    if not CHANGE_ID_RE.match(a.change_id or ""):
        raise Refused("--change-id must match [A-Za-z0-9_.:-]{1,64}")
    if not Path(a.tape_dir).is_dir():
        raise Refused("--tape-dir is not a directory")
    if lp.read_key(a.key_file) is None:
        raise Refused("--key-file is missing or holds no usable HELIUS_API_KEY")  # the key itself is never printed
    if a.catch_up_hours < 0 or a.catch_up_hours > 72:
        raise Refused("--catch-up-hours must be 0..72")


def cmd_run(a: argparse.Namespace, *, host_module: Any = None, ntp_runner: fm.Runner | None = None, now_ms: Callable[[], int] | None = None,
            repo_sha: Callable[[], str] | None = None, source_hook: Callable[[LiveSource], None] | None = None, mirror_check: Any = _AUTO) -> int:
    """Refusals first (args, a dirty tree, an unsynced clock), then build engine, log, merger, cap and LiveSource, and hand them to the host's run loop. DRY: the
    host holds no key and sends nothing."""
    check_args(a)
    from tools import r4b_count as rc
    from tools import r4b_live as lv
    from tools.arena_declog import DecisionLog

    sha = (repo_sha or lv.repo_head_sha)()  # raises LiveRefused on a dirty tree or an unreadable HEAD
    ntp = fm.require_synced(runner=ntp_runner)  # raises ClockNotSynced
    host_mod = host_module or importlib.import_module("tools.r4b_live_host")
    out = Path(a.out)
    (out / "glue").mkdir(parents=True, exist_ok=True)
    engine = lv.new_host_engine(universe_min_mbt=a.universe_min_mbt if a.universe_min_mbt is not None else rc.UNIVERSE_MIN_MBT)
    log = DecisionLog(a.declog_root)
    cap = fm.DailyCreditCap(a.daily_credit_cap, out / "glue" / "credit-cap.json")
    clock_point = getattr(engine, "clock_point", None)
    sd_fn = (lambda T: (clock_point(T) or (None,))[0]) if clock_point is not None else None
    if a.mirror_check == "off":
        mirror_check = None
    need = a.mirror_need_hours if a.mirror_need_hours is not None else (a.catch_up_hours or None)  # the history the warm-up will read; else freshness only
    source = LiveSource(tape_dir=a.tape_dir, cap=cap, key_file=a.key_file, commitment=a.commitment, out_dir=out, catch_up_hours=a.catch_up_hours,
                        now_ms=now_ms, require_ntp=False, sd_fn=sd_fn, merger=HostMerger(), mirror_check=mirror_check, mirror_need_hours=need)
    source.ntp = ntp
    source.start()  # the mirror refusal comes BEFORE the host writes its `start` row
    (out / "glue" / "run.json").write_text(json.dumps({"schema": SCHEMA, "change_id": a.change_id, "frozen_sha": sha, "trigger": a.trigger, "commitment": a.commitment,
                                                        "daily_credit_cap": a.daily_credit_cap, "catch_up_hours": a.catch_up_hours, "ntp": ntp.as_record(),
                                                        "host_call": HOST_CALL, "live_built": LIVE_BUILT, "mirror_check": a.mirror_check,
                                                        "mirror_need_hours": need}, sort_keys=True, indent=1) + "\n")
    host = host_mod.DryHost(engine, log, source, out_dir=out, ntp_offset_ms=ntp.offset_ms, frozen_sha=sha)
    host.start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: source.request_stop())
        except ValueError:  # not the main thread (tests)
            pass
    if source_hook is not None:
        source_hook(source)
    counts = host.run(max_marks=a.max_marks)
    summary = {"schema": SCHEMA, "change_id": a.change_id, "host_counts": counts, "source": source.report()}
    (out / "glue" / "run-summary.json").write_text(json.dumps(summary, sort_keys=True, indent=1, default=str) + "\n")
    print(json.dumps({"schema": SCHEMA, "change_id": a.change_id, "host_counts": counts}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="r4-b DRY host glue: public tape + Helius mark windows -> the DRY decision host. No wallet, no send.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the DRY host on the merged live stream")
    r.add_argument("--tape-dir", required=True, help="arena tape dir (on mal-fast-0: the mirror of the tape-mirror lane)")
    r.add_argument("--key-file", required=True, help="file holding HELIUS_API_KEY=...; read in-process, never printed")
    r.add_argument("--declog-root", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--commitment", default="processed", choices=list(fm.COMMITMENTS))
    r.add_argument("--daily-credit-cap", type=float, default=float(CAP_DEFAULT), help=f"Helius credits per UTC day for mark windows (refused above {CAP_REFUSE_ABOVE})")
    r.add_argument("--trigger", default="certified")
    r.add_argument("--change-id", required=True)
    r.add_argument("--catch-up-hours", type=int, default=0, help="read the tape from this many hours before the start (the engine's 52 h warm-up)")
    r.add_argument("--universe-min-mbt", type=int, default=None, help="members graduate at or after this epoch second (default: EXP-027's r4b_count.UNIVERSE_MIN_MBT)")
    r.add_argument("--max-marks", type=int, default=None)
    r.add_argument("--mirror-check", default="auto", choices=("auto", "off"),
                   help="auto: tools.tape_mirror.mirror_problem (PR #1147) must say fresh at start and before each mark (refused if the module is missing); "
                        "off: the tape dir is not a mirror (recorded in run.json)")
    r.add_argument("--mirror-need-hours", type=int, default=None, help="history hours the mirror must hold at start (default: --catch-up-hours, else freshness only)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return cmd_run(a)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (fm.ClockNotSynced, MirrorUnhealthy) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - LiveRefused (dirty tree, warm-up) and the host's own refusals: text only, never a URL or a key
        if type(exc).__name__ == "LiveRefused":
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
