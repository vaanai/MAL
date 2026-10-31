#!/usr/bin/env python3
"""r4b_live_host: the DRY decision-host loop of r4-b (EXP-027 book, frozen). DRY and KEYLESS: it ingests a feed, decides at hour marks, writes the decision
log and a latency record per mark. It never sends, holds no key and opens no wallet (tools/r4b_live.LIVE_BUILT is False and stays False).

Where it sits (QP-RULING-1030 Addenda 3, 4 and 5; DEC-032 Amendment 8 and Errata 1 and 2)
  source (a Helius mark-window client merged with the public tape; a separate PR) -> tools/r4b_feed_adapter -> tools/r4b_live.Engine -> arena_declog.
  The host is programmed against a small duck-typed SOURCE and tested with fakes; the real merger is not in this module.

The source (any object with these)
  iter(source)                 yields (tape_row, t_recv_ms) in receive order, or None for an idle tick (so the host can act on time with no new row);
                               the iteration ends the run.
  source.mark_complete(T, sd)  the source's FULL completeness answer for mark T at slot sd: a bool, or an object with `.complete` (and `.reason`), as
                               tools/r4b_feed_merge.mark_complete returns (healthy window, armed, covering sd, processed tip >= sd + 1, no silence, seam closed).
                               Never the tip alone. True means every print of a slot < sd is in the stream the host has been handed.
  source.failed(T)             None, or a short code ([a-z0-9_]{1,32}: connect, silence, data_loss ...) once the source knows mark T can never be complete.
  source.buy_hold()            None, or a short code (the daily credit cap) while new buys must not be made; exits are never held.
  source.tip_slot(commitment)  optional: the highest slot the source has seen at that commitment ("processed" or "confirmed"), or None. Without it the d_*
                               fields of the latency record are None and the go/no-go cannot be judged.
  source.public_hw_slot()      the highest slot the PUBLIC stream has delivered with no gap (an open public data_loss gap holds it below the gap: the glue's
                               job, QP #1142 condition G2), or None. Needed by the late_public fallback (ruling (b3)) and the late_inputs watch (ruling (a)).
  source.window_closed(T)      optional: True once mark T's Helius window is closed (the late_inputs watch waits for it; without it that term is skipped).
  Reason codes that do not match [a-z0-9_]{1,32} are logged as "unspecified": nothing the source says (an exception text, a URL) reaches a log.

What the loop does (a mark T is every UTC hour)
  start      repo_head_sha (REFUSES a dirty tree), the `start` row (it records the run's `trigger` and `change_id`), restart_exits for every position the last
             process left open (tools.r4b_live.host_start), and the engine must be a warm-up engine (new_host_engine: no entry for 52 h of ingested block time,
             Amendment 8 Erratum 1). A change_id is required: it is the declared change the go/no-go read is keyed to (ruling (b2) condition 3).
  ingest     each row -> FeedAdapter -> engine; a GapRecord that halts buys -> a `gap` row (log_gap). A reconnect without data loss writes nothing.
  T - 5 s    engine.precompute(T): each candidate member's print state is cached (tools/r4b_live; decide(T) is byte-identical with and without it).
  T          ONE trigger per run, fixed at start (`trigger`, written in the start row):
               "guard"      decide once the engine's own clock guard holds (a later chain second is in) AND source.mark_complete(T, sd) is complete;
               "certified"  decide once source.mark_complete(T, sd) is complete for the PROVISIONAL sd (the engine's first clock second at or after T), without
                            the one-second guard (ruling (a)): engine.decide(T, certified_sd=sd) re-derives sd and REFUSES on a mismatch (the host retries).
             Either way, up to T + 20 s (or until source.failed(T)):
               complete    -> engine.decide(T), the declog rows (exits first, then entries in rank order), one latency record.
               buy_hold()  -> the same, but the engine makes NO entry (decide(hold_entries=True)); the reason is logged. No gap row (no entry row exists).
             Incomplete (failed, the bound passed, clock not complete, window unarmed / silent / seam open) -> FAIL CLOSED, and NO decision from partial inputs
             (ruling (b3); an exit is a decision): a `gap` row (gap_kind incomplete_inputs, data_loss True: the executor holds buys until 10 min after it) and an
             `incomplete_inputs` event, then the host WAITS (up to T + late_bound_s, default 15 min) for the public stream: the engine's own clock guard holds and
             source.public_hw_slot() >= sd + 1. Then engine.decide(T, hold_entries=True): the exits only, status `late_public`. If the bound passes first: no
             decide at all (status `abandoned`, the book is not stepped), and the next decided mark's own `gap` rule closes what is held (T1 = last mark + 1 h).
  Entries are never written for a mark whose inputs are not known complete.
  late_inputs (certified trigger, proof P-a3): after a certified decide the host keeps a watch on that mark. Every row it ingests afterwards with slot < sd
  (the certified sd) or block_time < T is counted (rows decide(T) would have read), until the LATER of the guard instant (the engine's clock guard for T becomes
  true), the window close and the public stream passing sd + 1 (or watch_bound_s after T). The follow-up record `mark_late` carries d_ready_signal, d_ready_guard,
  late_inputs and the late flag; a mark with late_inputs 0 decided what the guard path would have by construction, and an sd that moved with late_inputs 0 is
  counted as a fault (fault_sd_changed_without_late).

The latency record (JSON line, one per mark; counts and slots only, no mint, score or rank)
  d_ready / d_decide / d_would_send = (the source's processed tip slot at that instant) - sd(T), in slots; the same under "confirmed" when the source reports
  it. Wall times are diagnostics. slot_s is per UTC date as EXP-027 :258-261 (3600 / median of the date's hourly slots-per-hour, an hour measured from the
  engine's own clock at the two marks that bound it, the r4b_read / cap_pick_score rules), labelled provisional until all 24 hours of the date are measured;
  k_1_9 = np.round(1.9 / slot_s), k_1_3 = np.round(1.3 / slot_s) (r4b_read.k_for); go_qty = d_would_send + 4 (the upper send-and-land stub, Erratum 2 Bar 2.3).
  `summarize` gives p50 / p90 and the go / no-go; `read_go_no_go` is the once-only read (below). The 4-slot stub never enters the match clause or P5 (Bar 2.4);
  this module does not evaluate them. It does not measure landing.

The go / no-go (QP-RULING-1030 Addendum 5 ruling (b1), (b2))
  Counted marks: decided on complete inputs (not held, not late_public, not abandoned), NOT in the warm-up, the precompute ran ahead of the mark, d_would_send
  exists, and the mark's date has a FINAL slot_s (24 measured hours). A date is left out only for that last reason, never for its latency; every date is reported.
  The verdict is READ ONCE, at the first UTC-date boundary with at least 48 counted marks, over all counted marks up to that boundary, with the smallest k_1_9
  of the counted dates. Later marks never change it. A re-read is allowed only after a declared change: the run's change_id (recorded in the start row before
  its first counted mark); read_go_no_go refuses a second read with the same change_id. Held and abandoned marks are left out of the p50 and reported by reason.

Seals. The decision log stays sealed (EXP-027 section 11; Amendment 8 B1): this module prints names and counts only. Its test data is synthetic. It reads no
tape and no sealed path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

if __package__ in (None, ""):  # `python tools/r4b_live_host.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import r4b_live as lv  # noqa: E402
from tools.r4b_feed_adapter import FeedAdapter, GapRecord, apply  # noqa: E402

SCHEMA = "r4b_live_host_v1"
MARK_S = lv.MARK_S
PRECOMPUTE_LEAD_S = 5.0  # the T - 5 s precompute
READY_BOUND_S = 20.0  # decide on complete inputs, or give up on the mark, at most this long after T
SEND_LAND_STUB_SLOTS = 4  # Erratum 2 Bar 2.3: the upper send-and-land allowance added to p50(d_would_send)
FAIL_SLOTS = 1_000_000  # a counted decided mark without the precompute ahead of T, or without a would-send slot, enters the median as this (a fail)
LAT_LONG_S, LAT_SHORT_S = 1.9, 1.3  # EXP-027 section 3.3 lats
GO_NOGO_MIN_MARKS = 48
LATE_BOUND_S = 900.0  # ruling (b3): wait this long after T for the public stream to pass sd + 1, then abandon the mark
WATCH_BOUND_S = 900.0  # ruling (a) P-a3: stop counting late_inputs this long after T at the latest
TRIGGERS = ("guard", "certified")
INPUT_KINDS = ("trade", "market", "resolved", "migration", "create")  # adapter.Mapped kinds that feed the engine (a decision reads them)
STATUS_DECIDED = ("decided", "decided_held")
STATUS_NOT_COUNTED = ("late_public", "abandoned", "incomplete")
FAST0_HOSTNAME = "mal-fast-0"
HOUR_MIN_SPAN_S = 1800  # tools/cap_pick_score.HOUR_MIN_SPAN_S (a test asserts it)
HOUR_SPH_RANGE = (6000.0, 20000.0)  # tools/cap_pick_score.HOUR_SPH_RANGE (a test asserts it)
CODE_RE = re.compile(r"[a-z0-9_]{1,32}")
CHANGE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
COMMITMENTS = ("processed", "confirmed")


def code(x: Any) -> str | None:
    """A source-supplied reason as a log-safe code: None stays None, a code that matches CODE_RE stays, anything else becomes "unspecified"."""
    if x is None:
        return None
    return x if isinstance(x, str) and CODE_RE.fullmatch(x) else "unspecified"


def _is_int(x: Any) -> bool:
    return isinstance(x, (int, np.integer)) and not isinstance(x, (bool, np.bool_))


def utc_date(t: int) -> str:
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------------------------------------- slot time (EXP-027 :258-261)
def sph_from_points(slot_lo: int, bt_lo: int, slot_hi: int, bt_hi: int) -> float | None:
    """tools/cap_pick_score.hour_sph_from_points: slots per hour from two (slot, block_time) points; None if too close in time or outside the slot-clock range."""
    if bt_hi - bt_lo < HOUR_MIN_SPAN_S or slot_hi <= slot_lo:
        return None
    sph = (slot_hi - slot_lo) / (bt_hi - bt_lo) * 3600.0
    return sph if HOUR_SPH_RANGE[0] <= sph <= HOUR_SPH_RANGE[1] else None


def slot_s_table(points: Mapping[int, Sequence[int]]) -> dict[str, dict]:
    """points: mark T -> (sd(T), the clock second it came from). Hour [T, T + 1 h) of date(T) is measured from the points at T and T + 3600. Returns
    {date: {"slot_s": 3600 / median SPH_h, "hours": number of measured hours, "final": all 24 hours measured}} (r4b_read.slot_s_by_date's rule)."""
    by: dict[str, list[float]] = {}
    for T in sorted(points):
        nxt = points.get(T + MARK_S)
        if nxt is None:
            continue
        sph = sph_from_points(points[T][0], points[T][1], nxt[0], nxt[1])
        if sph is not None:
            by.setdefault(utc_date(T), []).append(sph)
    return {d: {"slot_s": 3600.0 / float(np.median(v)), "hours": len(v), "final": len(v) == 24} for d, v in by.items()}


def k_for(lat: float, slot_s: float) -> int:
    """tools/r4b_read.k_for at delta 0: np.round(lat / slot_s) (half to even)."""
    return int(np.round(lat / float(slot_s)))


def slot_s_for(table: Mapping[str, dict], date: str) -> tuple[float | None, bool, int, str | None]:
    """(slot_s, provisional, hours measured, source date): the date's own value; failing that the latest earlier date's, labelled provisional."""
    if date in table:
        t = table[date]
        return t["slot_s"], not t["final"], t["hours"], date
    earlier = [d for d in table if d < date]
    if earlier:
        d = max(earlier)
        return table[d]["slot_s"], True, table[d]["hours"], d
    return None, True, 0, None


# ------------------------------------------------------------------------------------------------------------------------------- the host
class DryHost:
    """See the module docstring. One thread; the engine, the adapter and the log are used from this loop only (the engine's rule).
    Parameters: engine (a warm-up engine, tools.r4b_live.new_host_engine), log (tools.arena_declog.DecisionLog), source (the duck type above),
    out_dir (host/latency.jsonl and host/events.jsonl are written under it), now_ms (epoch ms; default time.time), adapter (default FeedAdapter),
    hostname / ntp_offset_ms (diagnostics recorded in every latency record; the NTP check itself is the feed PR's), frozen_sha (default
    repo_head_sha(), which refuses a dirty tree), shorter_warmup_proof (as host_start),
    trigger ("guard" | "certified": ONE per run, fixed here and written in the start row; ruling (a) condition 3),
    change_id (REQUIRED by start(): the run's declared change, written in the start row and every record; the go/no-go read is keyed to it),
    ready_bound_s (20 s after T: give up on the primary trigger), late_bound_s (15 min after T: give up on the public-stream fallback, ruling (b3)),
    watch_bound_s (15 min after T: stop counting late_inputs of a certified mark)."""

    def __init__(self, engine: lv.Engine, log: Any, source: Any, *, out_dir: Path | str, adapter: FeedAdapter | None = None,
                 now_ms: Callable[[], int] | None = None, ready_bound_s: float = READY_BOUND_S, precompute_lead_s: float = PRECOMPUTE_LEAD_S,
                 hostname: str | None = None, ntp_offset_ms: float | None = None, frozen_sha: str | None = None,
                 shorter_warmup_proof: str | None = None, trigger: str = "guard", change_id: str | None = None,
                 late_bound_s: float = LATE_BOUND_S, watch_bound_s: float = WATCH_BOUND_S) -> None:
        if trigger not in TRIGGERS:
            raise lv.LiveRefused(f"trigger must be one of {TRIGGERS}")
        if change_id is not None and not (isinstance(change_id, str) and CHANGE_RE.fullmatch(change_id)):
            raise lv.LiveRefused("change_id must match [A-Za-z0-9_.-]{1,64}")
        self.engine, self.log, self.source = engine, log, source
        self.adapter = adapter if adapter is not None else FeedAdapter()
        self.now_ms = now_ms if now_ms is not None else (lambda: int(time.time() * 1000))
        self.ready_bound_ms = int(ready_bound_s * 1000)
        self.late_bound_ms = int(late_bound_s * 1000)
        self.watch_bound_ms = int(watch_bound_s * 1000)
        self.lead_ms = int(precompute_lead_s * 1000)
        self.hostname = hostname if hostname is not None else socket.gethostname()
        self.ntp_offset_ms = ntp_offset_ms
        self.frozen_sha, self.shorter_warmup_proof = frozen_sha, shorter_warmup_proof
        self._trigger, self._change_id = trigger, change_id
        self.out = Path(out_dir) / "host"
        self.latency_path, self.events_path = self.out / "latency.jsonl", self.out / "events.jsonl"
        self.counts: Counter = Counter()
        self.records: list[dict] = []
        self.points: dict[int, tuple[int, int]] = {}  # mark -> (sd, clock second): the slot_s inputs
        self.next_mark: int | None = None
        self._cur: dict | None = None
        self._watches: list[dict] = []  # certified marks whose late_inputs are still being counted
        self._gaps_since_mark = 0  # gap rows written since the previous mark's record
        self._last_ms = 0
        self._started = False

    @property
    def trigger(self) -> str:
        """The run's trigger: fixed at construction, recorded at start, read-only."""
        return self._trigger

    @property
    def change_id(self) -> str | None:
        return self._change_id

    # -- files
    def _write(self, path: Path, rec: dict) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def _event(self, kind: str, **kw: Any) -> None:
        self._write(self.events_path, {"schema": SCHEMA, "event": kind, "ms": self.now_ms(), **kw})
        self.counts[f"event_{kind}"] += 1

    def _ms(self, now: int) -> int:
        """The ms of a declog row written now: never behind the newest row (the log is one file per UTC day and must stay in order)."""
        return max(int(now), self._last_ms)

    def _seen(self, rows: Iterable[dict | None]) -> None:
        for r in rows:
            if r:
                self._last_ms = max(self._last_ms, int(r["ms"]))

    # -- start
    def start(self, now_ms: int | None = None) -> dict:
        """host_start (the dirty-tree refusal, the start row with the trigger and change_id, the restart exits), then load the slot points of a previous run of
        this out_dir (a restart keeps its per-day slot_s). The engine must carry a warm-up, and a change_id must be declared. A previous run of this out_dir under
        the SAME change_id but another trigger is refused (one trigger per run; a changed trigger is a declared change with a new change_id). Returns host_start's dict."""
        if self._started:
            raise lv.LiveRefused("host already started")
        if self.engine.warmup_s is None:
            raise lv.LiveRefused("a decision host needs a warm-up engine (tools.r4b_live.new_host_engine): a restarted engine must not buy on partial state")
        if self._change_id is None:
            raise lv.LiveRefused("a decision host needs a change_id: the go/no-go read is keyed to it and it is recorded before the first counted mark")
        now = int(now_ms if now_ms is not None else self.now_ms())
        prior_points: dict[int, tuple[int, int]] = {}
        if self.latency_path.exists():
            for line in self.latency_path.read_text().splitlines():
                r = json.loads(line) if line.strip() else {}
                if r.get("rec") != "mark":
                    continue
                if r.get("change_id") == self._change_id and r.get("trigger") not in (None, self._trigger):
                    raise lv.LiveRefused(f"change_id {self._change_id!r} already ran with trigger {r.get('trigger')!r}: one trigger per run, declare a new change_id")
                if r.get("sd") is not None and r.get("bt_star") is not None:
                    prior_points[int(r["mark"])] = (int(r["sd"]), int(r["bt_star"]))
        sha = self.frozen_sha if self.frozen_sha is not None else lv.repo_head_sha()  # refuses a dirty tree
        out = lv.host_start(self.log, now_ms=now, frozen_sha=sha, warmup_s=self.engine.warmup_s, shorter_warmup_proof=self.shorter_warmup_proof,
                            extra={"trigger": self._trigger, "change_id": self._change_id})
        self._seen([out["start"], *out["exits"]])
        self.points.update(prior_points)
        self.next_mark = (now // 1000 // MARK_S + 1) * MARK_S
        self._started = True
        self._event("start", frozen_sha=sha, warmup_s=self.engine.warmup_s, restart_exits=len(out["exits"]), hostname=self.hostname,
                    ntp_offset_ms=self.ntp_offset_ms, first_mark=self.next_mark, prior_points=len(self.points), trigger=self._trigger, change_id=self._change_id,
                    ready_bound_s=self.ready_bound_ms / 1000, late_bound_s=self.late_bound_ms / 1000, watch_bound_s=self.watch_bound_ms / 1000)
        return out

    # -- the loop
    def run(self, max_marks: int | None = None) -> dict:
        """Drive the source until it ends or max_marks marks (decided or abandoned) are done. Returns the counts (names and numbers only). When the source
        ends, the open late_inputs watches are closed as incomplete (flush_watches); a stop at max_marks leaves them open for a later call."""
        if not self._started:
            raise lv.LiveRefused("call start() first")
        done0 = self.counts["marks"]
        for item in self.source:
            now = self.now_ms()
            if item is not None:
                self._ingest(item[0], now)
            self._advance(now)
            if max_marks is not None and self.counts["marks"] - done0 >= max_marks:
                return dict(self.counts)
        self.flush_watches()
        return dict(self.counts)

    def _ingest(self, row: Mapping[str, Any], now: int) -> None:
        self.counts["rows_in"] += 1
        mapped = self.adapter.feed(row)
        if self._watches:
            self._count_late(mapped)
        for mp in apply(self.engine, mapped):
            g = lv.log_gap(self.log, mp, now_ms=self._ms(now))  # None unless it halts buys
            if g is not None:
                self._seen([g])
                self.counts["gap_rows"] += 1
                self._gaps_since_mark += 1

    def _tip(self) -> dict[str, int | None]:
        fn = getattr(self.source, "tip_slot", None)
        out: dict[str, int | None] = {}
        for c in COMMITMENTS:
            v = None
            if fn is not None:
                try:
                    v = fn(c)
                except TypeError:  # a source that takes no commitment argument reports its one tip as processed
                    v = fn() if c == "processed" else None
            out[c] = int(v) if _is_int(v) else None
        return out

    def _public_hw(self) -> int | None:
        fn = getattr(self.source, "public_hw_slot", None)
        v = fn() if fn is not None else None
        return int(v) if _is_int(v) else None

    def _advance(self, now: int) -> None:
        while self._mark_step(now):
            pass
        if self._watches:
            self._watch_step(now)

    # -- one mark: the primary trigger, then (incomplete only) the public-stream fallback
    def _primary_test(self, T: int) -> tuple[tuple[int, int] | None, bool, str | None]:
        """(clock point, complete, why not): the trigger's own test. "guard": the engine's clock guard AND the source's answer; "certified": the source's
        full answer for the provisional sd (the first clock second at or after T), no guard."""
        pt = self.engine.clock_point(T, provisional=self._trigger == "certified")
        if pt is None:
            return None, False, None
        res = self.source.mark_complete(T, pt[0])
        ok = bool(getattr(res, "complete", res))
        why = None if ok else code(str(getattr(res, "reason", "") or "").split(":")[0] or None)
        return pt, ok, why

    def _mark_step(self, now: int) -> bool:
        """Advance the pending mark one step; True if the mark was finished (so the next one may be due on the same instant)."""
        T = int(self.next_mark)  # type: ignore[arg-type]
        t_ms = T * 1000
        cur = self._cur
        if cur is None:
            cur = self._cur = {"pre": None, "phase": "primary"}
        if cur["pre"] is None and now >= t_ms - self.lead_ms:
            cur["pre"] = self._precompute(T, now)
        if now < t_ms:
            return False
        if cur["phase"] == "primary":
            fail = code(self.source.failed(T))
            if fail is not None:
                self._incomplete(T, now, f"failed_{fail}", None)
            else:
                pt, ok, why = self._primary_test(T)
                if ok and self._decide_primary(T, now, pt):  # type: ignore[arg-type]
                    return True
                if now >= t_ms + self.ready_bound_ms:
                    self._incomplete(T, now, "bound_clock" if pt is None else ("bound_refused" if ok else "bound_incomplete"), why)
        if cur["phase"] == "late":
            return self._late_step(T, now)
        return False

    def _precompute(self, T: int, now: int) -> dict:
        h0, m0 = self.engine.counters.get("precompute_hits", 0), self.engine.counters.get("precompute_misses", 0)
        try:
            st = self.engine.precompute(T)
            self.counts["precompute_ok"] += 1
            return {"ran": True, "rel_ms": now - T * 1000, "candidates": st["candidates"], "cached": st["cached"], "seconds": round(st["seconds"], 4), "_h0": h0, "_m0": m0}
        except Exception as exc:  # noqa: BLE001 - the precompute is an optimisation: a failure costs speed, never a decision (decide recomputes in full)
            self.engine.drop_precompute()
            self.counts["precompute_error"] += 1
            self._event("precompute_error", mark=T, error=type(exc).__name__)
            return {"ran": False, "rel_ms": now - T * 1000, "error": type(exc).__name__, "_h0": h0, "_m0": m0}

    def _decide_and_log(self, T: int, *, hold_entries: bool, certified_sd: int | None) -> tuple[lv.MarkResult, list[dict], int, dict, int, dict]:
        """engine.decide, then the declog rows. LiveRefused (nothing decided, nothing written) propagates."""
        res = self.engine.decide(T, hold_entries=hold_entries, certified_sd=certified_sd)
        t_decide = self.now_ms()
        tip_decide = self._tip()
        members = {m.mint: m for m in self.engine.members()}
        rows = lv.log_actions(self.log, res, members, self._ms(t_decide))
        self._seen(rows)
        self.engine.prune(T)
        return res, rows, t_decide, tip_decide, self.now_ms(), self._tip()

    def _decide_primary(self, T: int, now: int, pt: tuple[int, int]) -> bool:
        """The primary trigger fired: decide. False if the engine refused (a certified sd that moved; nothing was decided): the next step retries."""
        cur = self._cur or {}
        certified = self._trigger == "certified"
        hold = code(self.source.buy_hold())
        ready_tip = self._tip()
        try:
            res, rows, t_decide, tip_decide, t_send, tip_send = self._decide_and_log(T, hold_entries=hold is not None, certified_sd=pt[0] if certified else None)
        except lv.LiveRefused:
            self.counts["decide_refused"] += 1
            if not cur.get("refused"):
                cur["refused"] = True
                self._event("decide_refused", mark=T, trigger=self._trigger)
            return False
        cur.update(ready_ms=now, ready_tip=ready_tip, pt=pt, guard_at_ready=self.engine.clock_point(T) is not None)
        if hold is not None:
            self._event("entries_held", mark=T, reason=hold, suppressed=res.suppressed)
        self._record(T, status="decided_held" if hold else "decided", t_decide=t_decide, t_send=t_send, tip_decide=tip_decide, tip_send=tip_send, pt=pt, res=res,
                     rows=rows, hold=hold, reason=None)
        if certified:
            self._open_watch(T, now, int(pt[0]), ready_tip)
        return True

    def _incomplete(self, T: int, now: int, reason: str, source_reason: str | None) -> None:
        """The primary trigger cannot complete this mark: fail closed (the gap row first), then wait for the public stream. No decision from partial inputs."""
        cur = self._cur or {}
        cur.update(phase="late", incomplete=reason, source_reason=source_reason, incomplete_ms=now)
        self._gap_row(T, now, reason)

    def _late_step(self, T: int, now: int) -> bool:
        """Ruling (b3): decide on the now-complete inputs once the engine's clock guard holds and the public stream has passed sd + 1; exits only. Else give up
        at T + late_bound_s."""
        cur = self._cur or {}
        pt = self.engine.clock_point(T)
        hw = self._public_hw()
        if pt is not None and hw is not None and hw >= pt[0] + 1:
            ready_tip = self._tip()
            try:
                res, rows, t_decide, tip_decide, t_send, tip_send = self._decide_and_log(T, hold_entries=True, certified_sd=None)
            except lv.LiveRefused:
                self._abandon(T, now, "decide_refused")
                return True
            cur.update(ready_ms=now, ready_tip=ready_tip, pt=pt, public_hw=hw)
            self._event("late_public", mark=T, public_hw_slot=hw, wait_ms=now - T * 1000, exits=sum(1 for r in rows if r["kind"] == "exit"), suppressed=res.suppressed)
            self._record(T, status="late_public", t_decide=t_decide, t_send=t_send, tip_decide=tip_decide, tip_send=tip_send, pt=pt, res=res, rows=rows, hold=None,
                         reason=cur.get("incomplete"))
            return True
        if now >= T * 1000 + self.late_bound_ms:
            self._abandon(T, now, "late_bound")
            return True
        return False

    def _gap_row(self, T: int, now: int, reason: str) -> None:
        g = lv.log_gap(self.log, GapRecord("incomplete_inputs", None, now, T * 1000, now, True, True), now_ms=self._ms(now))
        self._seen([g])
        self.counts["gap_rows"] += 1
        self._event("incomplete_inputs", mark=T, reason=reason)

    def _abandon(self, T: int, now: int, why: str) -> None:
        """No decide at all: the book is not stepped (the next decided mark's gap rule closes what is held)."""
        cur = self._cur or {}
        self.engine.drop_precompute()
        t = self.now_ms()
        tip = self._tip()
        self._event("abandoned", mark=T, reason=cur.get("incomplete"), abandon=why)
        self._record(T, status="abandoned", t_decide=t, t_send=t, tip_decide=tip, tip_send=tip, pt=self.engine.clock_point(T), res=None, rows=[], hold=None,
                     reason=cur.get("incomplete"), abandon=why)

    def _record(self, T: int, *, status: str, t_decide: int, t_send: int, tip_decide: dict, tip_send: dict, pt: tuple[int, int] | None,
                res: lv.MarkResult | None, rows: list[dict], hold: str | None, reason: str | None, abandon: str | None = None) -> None:
        cur = self._cur or {}
        gaps, self._gaps_since_mark = self._gaps_since_mark, 0
        pre = {k: v for k, v in (cur.get("pre") or {"ran": False}).items() if not k.startswith("_")}
        if cur.get("pre") and cur["pre"].get("ran"):
            pre["hits"] = self.engine.counters.get("precompute_hits", 0) - cur["pre"]["_h0"]
            pre["misses"] = self.engine.counters.get("precompute_misses", 0) - cur["pre"]["_m0"]
        if pt is not None:
            self.points[T] = (int(pt[0]), int(pt[1]))
        date = utc_date(T)
        s_s, prov, hrs, s_src = slot_s_for(slot_s_table(self.points), date)
        k19 = k_for(LAT_LONG_S, s_s) if s_s else None
        k13 = k_for(LAT_SHORT_S, s_s) if s_s else None
        sd = int(pt[0]) if pt is not None else None
        ready_tip = cur.get("ready_tip") or {c: None for c in COMMITMENTS}
        d = lambda tip, c: (None if (sd is None or tip.get(c) is None) else int(tip[c]) - sd)  # noqa: E731
        by_c = {c: {"d_ready": d(ready_tip, c), "d_decide": d(tip_decide, c), "d_would_send": d(tip_send, c)} for c in COMMITMENTS}
        dws = by_c["processed"]["d_would_send"]
        primary = status in STATUS_DECIDED  # the late_public fallback has neither a signal nor a guard instant of its own
        certified = primary and self._trigger == "certified"
        t_guard = cur.get("ready_ms") if (primary and (not certified or cur.get("guard_at_ready"))) else None
        rec = {"schema": SCHEMA, "rec": "mark", "mark": T, "date": date, "status": status, "hold": hold, "reason": reason, "abandon": abandon,
               "source_reason": cur.get("source_reason"), "trigger": self._trigger, "change_id": self._change_id, "sd": sd,
               "bt_star": int(pt[1]) if pt is not None else None, "warmup": bool(res.warmup) if res else None, "precompute": pre,
               "slot_s": s_s, "slot_s_provisional": prov, "slot_s_hours": hrs, "slot_s_from": s_src, "k_1_9": k19, "k_1_3": k13,
               "t_mark_ms": T * 1000, "t_ready_ms": cur.get("ready_ms"), "t_decide_ms": t_decide, "t_would_send_ms": t_send,
               "t_incomplete_ms": cur.get("incomplete_ms"), "public_hw_slot": cur.get("public_hw"),
               "t_signal_ms": cur.get("ready_ms") if certified else None, "d_ready_signal": by_c["processed"]["d_ready"] if certified else None,
               "t_guard_ms": t_guard, "d_ready_guard": by_c["processed"]["d_ready"] if t_guard is not None else None,
               "wall_ready_ms": None if cur.get("ready_ms") is None else cur["ready_ms"] - T * 1000, "wall_decide_ms": t_decide - T * 1000,
               "wall_would_send_ms": t_send - T * 1000, "decide_seconds": round(res.seconds, 4) if res else None,
               "processed": by_c["processed"], "confirmed": by_c["confirmed"], "d_ready": by_c["processed"]["d_ready"],
               "d_decide": by_c["processed"]["d_decide"], "d_would_send": dws,
               "go_qty": None if dws is None else dws + SEND_LAND_STUB_SLOTS, "go_within_k_1_9": None if (dws is None or k19 is None) else dws + SEND_LAND_STUB_SLOTS <= k19,
               "n_rows": len(res.mints) if res else None, "n_entries": sum(1 for r in rows if r["kind"] == "entry"), "n_exits": sum(1 for r in rows if r["kind"] == "exit"),
               "n_suppressed": res.suppressed if res else None, "gap_rows_since_prev_mark": gaps, "hostname": self.hostname, "ntp_offset_ms": self.ntp_offset_ms}
        self._write(self.latency_path, rec)
        self.records.append(rec)
        self.counts["marks"] += 1
        self.counts[f"marks_{status}"] += 1
        self.next_mark = T + MARK_S
        self._cur = None

    # -- ruling (a) proof P-a3: late_inputs of a certified mark
    def _open_watch(self, T: int, now: int, sd: int, ready_tip: dict) -> None:
        """Called right after a certified mark's record is written: count what arrives after the decision until the later of the guard instant, the window close
        and the public stream passing sd + 1 (or the watch bound)."""
        w = {"T": T, "sd": sd, "signal_ms": now, "signal_tip": ready_tip, "late": 0, "by_kind": Counter(), "guard_ms": None, "guard_tip": None, "sd_guard": None,
             "window_ms": None, "public_ms": None, "deadline_ms": T * 1000 + self.watch_bound_ms}
        self._watches.append(w)
        self._watch_step(now)

    def _count_late(self, mapped: list) -> None:
        """Rows a decision at the watched mark would have read: slot < the certified sd, or block_time < T (the minute and clock sums). Counted per mapped input."""
        for mp in mapped:
            row = mp.row
            if mp.kind not in INPUT_KINDS or not isinstance(row, dict):
                continue
            slot, bt = row.get("slot"), row.get("block_time")
            for w in self._watches:
                if (_is_int(slot) and slot < w["sd"]) or (_is_int(bt) and bt < w["T"]):
                    w["late"] += 1
                    w["by_kind"][mp.kind] += 1

    def _window_closed(self, T: int) -> bool:
        fn = getattr(self.source, "window_closed", None)
        return True if fn is None else bool(fn(T))

    def _public_passed(self, sd: int) -> bool:
        if getattr(self.source, "public_hw_slot", None) is None:
            return True  # a source without the public high-water slot cannot be waited for: the term is skipped (the glue must provide it)
        hw = self._public_hw()
        return hw is not None and hw >= sd + 1

    def _watch_step(self, now: int) -> None:
        for w in list(self._watches):
            T = w["T"]
            if w["guard_ms"] is None:
                g = self.engine.clock_point(T)
                if g is not None:
                    w["guard_ms"], w["guard_tip"], w["sd_guard"] = now, self._tip(), int(g[0])
            if w["window_ms"] is None and self._window_closed(T):
                w["window_ms"] = now
            if w["public_ms"] is None and self._public_passed(w["sd"]):
                w["public_ms"] = now
            done = None not in (w["guard_ms"], w["window_ms"], w["public_ms"])
            if done or now >= w["deadline_ms"]:
                self._close_watch(w, now, done)

    def _close_watch(self, w: dict, now: int, complete: bool) -> None:
        self._watches.remove(w)
        T, sd = w["T"], w["sd"]
        end = self.engine.clock_point(T)
        tip_g = (w["guard_tip"] or {}).get("processed")
        tip_s = (w["signal_tip"] or {}).get("processed")
        sd_end = None if end is None else int(end[0])
        sd_changed = (w["sd_guard"] is not None and w["sd_guard"] != sd) or (sd_end is not None and sd_end != sd)
        fault = bool(sd_changed and w["late"] == 0)
        rec = {"schema": SCHEMA, "rec": "mark_late", "mark": T, "trigger": self._trigger, "change_id": self._change_id, "sd": sd,
               "t_signal_ms": w["signal_ms"], "d_ready_signal": None if tip_s is None else int(tip_s) - sd,
               "t_guard_ms": w["guard_ms"], "d_ready_guard": None if tip_g is None else int(tip_g) - sd,
               "guard_after_signal_ms": None if w["guard_ms"] is None else w["guard_ms"] - w["signal_ms"],
               "sd_guard": w["sd_guard"], "sd_end": sd_end, "sd_changed": sd_changed, "late_inputs": w["late"], "late_by_kind": dict(sorted(w["by_kind"].items())),
               "late": w["late"] > 0, "fault_sd_changed_without_late": fault, "watch_complete": complete, "watch_end_ms": now,
               "missing": [k for k, v in (("guard", w["guard_ms"]), ("window", w["window_ms"]), ("public", w["public_ms"])) if v is None],
               "t_window_closed_ms": w["window_ms"], "t_public_passed_ms": w["public_ms"]}
        self._write(self.latency_path, rec)
        self.counts["watches_closed"] += 1
        if w["late"]:
            self.counts["late_marks"] += 1
            self.counts["late_inputs"] += w["late"]
            self._event("late_inputs", mark=T, n=w["late"], by_kind=dict(sorted(w["by_kind"].items())))
        if not complete:
            self.counts["watch_incomplete"] += 1
        if fault:
            self.counts["fault_sd_changed_without_late"] += 1
            self._event("fault_sd_changed_without_late", mark=T, sd=sd, sd_guard=w["sd_guard"], sd_end=sd_end)

    def flush_watches(self) -> None:
        """Close every open late_inputs watch as it stands (the run ended): watch_complete False unless all three terms were met."""
        now = self.now_ms()
        for w in list(self._watches):
            self._close_watch(w, now, False)


# ------------------------------------------------------------------------------------------------------------------------------- summary and go / no-go
def _pq(xs: Sequence[float], q: float) -> float | None:
    return float(np.percentile(np.asarray(xs, float), q)) if len(xs) else None


def _next_date(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def summarize(records: Iterable[Mapping[str, Any]], *, min_marks: int = GO_NOGO_MIN_MARKS, decide_commitment: str = "processed", require_host: str | None = FAST0_HOSTNAME,
              stub_slots: int = SEND_LAND_STUB_SLOTS, change_id: str | None = None) -> dict:
    """p50 / p90 of d_ready, d_decide and d_would_send (slots) per commitment, and the Erratum 2 Bar 2.3 go / no-go:
         GO  iff  p50(d_would_send) + 4 <= k_1_9 = round(1.9 / slot_s), over at least `min_marks` counted marks, on mal-fast-0, with the T - 5 s precompute.
    Rules fixed here, outcome-blind (QP-RULING-1030 Addendum 5 ruling (b1), (b2); the Erratum states the quantity, these are the read rules):
      * A mark COUNTS if it was decided on complete inputs (status decided: NOT decided_held, late_public or abandoned), it is NOT a warm-up mark (warmup False;
        a record without the flag is not vouched for and is left out), and its date's slot_s is FINAL (24 measured hours, recomputed here from the recorded
        points of ALL records). A date is left out only for that last reason, never for its marks' latency; EVERY date the host ran is reported (`dates`).
        EVERY such decided mark counts (reviewer on #1143): one whose precompute did not run ahead of the mark, or that has no d_would_send for the deciding
        commitment, counts as a FAIL (its d_would_send enters the median as FAIL_SLOTS), never dropped.
      * Read once: the verdict is taken at the first UTC-date boundary with at least `min_marks` counted marks, over all counted marks up to that boundary
        (`read_date` is the last date inside it). Later marks do not enter (n_after_read); the verdict never changes as the file grows. Before that boundary the
        verdict is INSUFFICIENT and the percentiles are `monitor_only`.
      * Several dates: the verdict's k_1_9 is the SMALLEST of the counted dates' (the harder bar); per-date p50 and k are reported beside it.
      * Held (credit cap), late_public and abandoned marks never count and never help; they are reported by reason (`excluded`), the late_public ones with their
        incomplete-inputs reason. A non-zero n_incomplete is a flag.
      * Only the records of one change_id are read (the run's declared change); several change_ids in one file need `change_id`. A record set without a change_id,
        or counted marks of more than one trigger, give INSUFFICIENT.
      * p50 / p90 are np.percentile (linear) over the counted marks, unrounded.
      * require_host: every counted record must carry that hostname, else the verdict is INSUFFICIENT.
    Verdict: GO | NO_GO | INSUFFICIENT (fewer than min_marks counted by the boundary, a wrong host, mixed triggers, no change_id, or a mark without the precompute)."""
    allr = list(records)
    recs_all = [r for r in allr if r.get("rec") == "mark"]
    late_by_mark = {int(r["mark"]): r for r in allr if r.get("rec") == "mark_late"}
    points = {int(r["mark"]): (int(r["sd"]), int(r["bt_star"])) for r in recs_all if r.get("sd") is not None and r.get("bt_star") is not None}
    table = slot_s_table(points)  # the chain's slot rate: from ALL records, whatever the change_id
    why: list[str] = []
    ids = sorted({r["change_id"] for r in recs_all if r.get("change_id") is not None})
    if change_id is None:
        if len(ids) == 1:
            change_id = ids[0]
        elif ids:
            why.append(f"several change_ids in the records ({', '.join(ids)}): pass change_id")
        else:
            why.append("the records carry no change_id")
    by_mark = {int(r["mark"]): r for r in recs_all if change_id is not None and r.get("change_id") == change_id}
    recs = [by_mark[m] for m in sorted(by_mark)]
    out: dict[str, Any] = {"schema": SCHEMA, "change_id": change_id, "n_records": len(recs), "n_records_other_change_id": len(recs_all) - len(recs),
                           "n_decided": sum(1 for r in recs if r["status"] in STATUS_DECIDED), "n_held": sum(1 for r in recs if r["status"] == "decided_held"),
                           "n_late_public": sum(1 for r in recs if r["status"] == "late_public"), "n_abandoned": sum(1 for r in recs if r["status"] == "abandoned"),
                           "n_incomplete": sum(1 for r in recs if r["status"] in STATUS_NOT_COUNTED),
                           "slot_s": {d: dict(v) for d, v in sorted(table.items())}, "decide_commitment": decide_commitment, "stub_slots": stub_slots}
    excluded: Counter = Counter()
    by_date: dict[str, list] = {}
    n_prov = n_nopre = n_warm = n_nodws = 0
    marks_on = Counter(r["date"] for r in recs)
    for r in recs:
        st = r["status"]
        if st == "decided_held":
            excluded[f"held:{r.get('hold') or 'unspecified'}"] += 1
            continue
        if st in STATUS_NOT_COUNTED:
            excluded[f"{st}:{r.get('reason') or 'unspecified'}"] += 1
            continue
        if st != "decided":
            excluded[f"status_{st}"] += 1
            continue
        if r.get("warmup") is not False:
            n_warm += 1
            continue
        t = table.get(r["date"])
        if t is None or not t["final"]:
            n_prov += 1
            continue
        pre = r.get("precompute") or {}
        fail = None
        if not (pre.get("ran") and pre.get("rel_ms") is not None and pre["rel_ms"] < 0):  # the precompute ran, and ahead of the mark
            n_nopre += 1
            fail = "no_precompute_ahead"
        if (r.get(decide_commitment) or {}).get("d_would_send") is None:
            n_nodws += 1
            fail = fail or "no_d_would_send"
        by_date.setdefault(r["date"], []).append(r if fail is None else {**r, "_fail": fail})
    out["excluded"] = dict(sorted(excluded.items()))
    out["dates"] = {}
    for d in sorted(set(marks_on) | set(table)):
        t = table.get(d)
        out["dates"][d] = {"n_marks": marks_on.get(d, 0), "n_counted_candidates": len(by_date.get(d, [])), "slot_s": None if t is None else t["slot_s"],
                           "slot_s_hours": 0 if t is None else t["hours"], "final": bool(t and t["final"]), "left_out": None if (t and t["final"]) else "slot_s_not_final"}
    cum, read_date = 0, None
    for d in sorted(by_date):
        cum += len(by_date[d])
        if cum >= min_marks:
            read_date = d
            break
    used = [d for d in sorted(by_date) if read_date is None or d <= read_date]
    counted = [(r, table[d]["slot_s"]) for d in used for r in by_date[d]]
    out.update(n_counted=len(counted), n_provisional=n_prov, n_without_precompute=n_nopre, n_warmup=n_warm, n_without_d_would_send=n_nodws,
               n_after_read=sum(len(v) for d, v in by_date.items() if read_date is not None and d > read_date), read_date=read_date,
               read_boundary_utc=None if read_date is None else _next_date(read_date) + "T00:00:00Z", monitor_only=read_date is None)
    for c in COMMITMENTS:
        vals = {f: [(r[c][f]) for r, _ in counted if r.get(c) and r[c].get(f) is not None] for f in ("d_ready", "d_decide", "d_would_send")}
        out[c] = {f: {"n": len(v), "p50": _pq(v, 50), "p90": _pq(v, 90)} for f, v in vals.items()}
    per_date: dict[str, dict] = {}
    def dws_of(r: Mapping[str, Any]) -> float:
        return FAIL_SLOTS if r.get("_fail") else r[decide_commitment]["d_would_send"]

    for r, s_s in counted:
        e = per_date.setdefault(r["date"], {"slot_s": s_s, "k_1_9": k_for(LAT_LONG_S, s_s), "k_1_3": k_for(LAT_SHORT_S, s_s), "d_would_send": []})
        e["d_would_send"].append(dws_of(r))
    out["per_date"] = {d: {"slot_s": e["slot_s"], "k_1_9": e["k_1_9"], "k_1_3": e["k_1_3"], "n": len(e["d_would_send"]), "p50_d_would_send": _pq(e["d_would_send"], 50),
                           "p90_d_would_send": _pq(e["d_would_send"], 90)} for d, e in sorted(per_date.items())}
    dws = [dws_of(r) for r, _ in counted]
    out["n_counted_failed"] = sum(1 for r, _ in counted if r.get("_fail"))
    out["counted_failed_by_reason"] = dict(sorted(Counter(r["_fail"] for r, _ in counted if r.get("_fail")).items()))
    p50 = _pq(dws, 50)
    k19 = min((e["k_1_9"] for e in per_date.values()), default=None)
    out["k_1_9_used"] = k19
    out["go_qty"] = None if p50 is None else p50 + stub_slots
    hosts = sorted({str(r.get("hostname")) for r, _ in counted})
    triggers = sorted({str(r.get("trigger")) for r, _ in counted})
    out["hosts"], out["triggers"] = hosts, triggers
    # late_inputs (ruling (a), P-a3), over every decided mark of the change_id
    lates = [late_by_mark[int(r["mark"])] for r in recs if r["status"] in STATUS_DECIDED and int(r["mark"]) in late_by_mark]
    out["late_inputs"] = {"n_watched": len(lates), "n_late_marks": sum(1 for x in lates if x.get("late")), "late_inputs_total": sum(int(x.get("late_inputs") or 0) for x in lates),
                          "n_watch_incomplete": sum(1 for x in lates if not x.get("watch_complete")), "n_unwatched_decided": sum(1 for r in recs if r["status"] in STATUS_DECIDED) - len(lates),
                          "n_fault_sd_changed_without_late": sum(1 for x in lates if x.get("fault_sd_changed_without_late"))}
    if len(counted) < min_marks:
        why.append(f"fewer than {min_marks} counted marks by a date boundary ({len(counted)} on {len(used)} final date(s))")
    if require_host is not None and hosts != [require_host]:
        why.append(f"counted marks are not all from {require_host}")
    if len(triggers) > 1:
        why.append(f"counted marks carry more than one trigger ({', '.join(triggers)}): one trigger per run")
    if out["n_counted_failed"]:
        why.append(f"{out['n_counted_failed']} counted mark(s) count as fails (no precompute ahead of the mark, or no would-send slot)")
    if out["late_inputs"]["n_fault_sd_changed_without_late"]:
        why.append("a certified mark's sd moved with late_inputs 0: a code fault (QP Addendum 5 P-a3), the run blocks")
    if len(counted) < min_marks or (require_host is not None and hosts != [require_host]) or len(triggers) > 1 or change_id is None:
        out["verdict"] = "INSUFFICIENT"
    else:
        out["verdict"] = "GO" if (p50 is not None and k19 is not None and p50 + stub_slots <= k19) else "NO_GO"
    out["notes"] = why
    return out


def read_go_no_go(latency_path: Path | str, *, change_id: str, reads_path: Path | str | None = None, **kw: Any) -> dict:
    """The ONCE-ONLY read of the go / no-go (ruling (b2) condition 3): summarize the latency file for `change_id`; if the verdict is GO or NO_GO, record the read in
    `reads.jsonl` beside the latency file (change_id, verdict, read_date, counts, md5 of the counted marks). A second read with the same change_id REFUSES
    (LiveRefused): a re-read needs a declared change, i.e. a new run started with a new change_id before its first counted mark. An INSUFFICIENT result is not a
    read and records nothing."""
    lat = Path(latency_path)
    rp = Path(reads_path) if reads_path is not None else lat.with_name("reads.jsonl")
    if not (isinstance(change_id, str) and CHANGE_RE.fullmatch(change_id)):
        raise lv.LiveRefused("change_id must match [A-Za-z0-9_.-]{1,64}")
    prior = [json.loads(x) for x in rp.read_text().splitlines() if x.strip()] if rp.exists() else []
    done = [p for p in prior if p.get("change_id") == change_id]
    if done:
        raise lv.LiveRefused(f"the go/no-go for change_id {change_id!r} was already read ({done[0].get('verdict')} at {done[0].get('read_boundary_utc')}); "
                             "a re-read needs a declared change: a new run with a new change_id, recorded at its start before its first counted mark")
    recs = [json.loads(x) for x in lat.read_text().splitlines() if x.strip()]
    res = summarize(recs, change_id=change_id, **kw)
    if res["verdict"] in ("GO", "NO_GO"):
        counted = sorted(int(r["mark"]) for r in recs if r.get("rec") == "mark" and r.get("change_id") == change_id and r.get("date") is not None
                         and res["read_date"] is not None and r["date"] <= res["read_date"] and r.get("status") == "decided" and r.get("warmup") is False)
        body = {"schema": SCHEMA, "rec": "read", "change_id": change_id, "verdict": res["verdict"], "read_date": res["read_date"],
                "read_boundary_utc": res["read_boundary_utc"], "n_counted": res["n_counted"], "k_1_9_used": res["k_1_9_used"], "go_qty": res["go_qty"],
                "marks_md5": hashlib.md5(json.dumps(counted).encode()).hexdigest(), "triggers": res["triggers"]}
        rp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(rp), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(body, sort_keys=True) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        res["read_recorded"] = True
    return res


# ------------------------------------------------------------------------------------------------------------------------------- CLI: summarize a latency file
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b DRY host: the go / no-go of a latency.jsonl (p50 / p90 in slots, Bar 2.3). Counts only. The read is ONCE per change_id.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("summarize")
    sm.add_argument("--latency", required=True)
    sm.add_argument("--change-id", required=True, help="the run's declared change_id (written in its start row); a verdict is recorded once per change_id")
    sm.add_argument("--reads", default=None, help="reads.jsonl path (default: beside the latency file)")
    sm.add_argument("--min-marks", type=int, default=GO_NOGO_MIN_MARKS)
    sm.add_argument("--commitment", default="processed", choices=COMMITMENTS)
    sm.add_argument("--any-host", action="store_true", help="a rehearsal: do not require hostname mal-fast-0, allow --min-marks, and record NO read")
    a = ap.parse_args(argv)
    kw = dict(min_marks=a.min_marks, decide_commitment=a.commitment, require_host=None if a.any_host else FAST0_HOSTNAME)
    if a.any_host:
        recs = [json.loads(x) for x in Path(a.latency).read_text().splitlines() if x.strip()]
        res = summarize(recs, change_id=a.change_id, **kw)
        res["rehearsal"] = True
        print(json.dumps(res, sort_keys=True, indent=1))
        return 0
    if a.min_marks != GO_NOGO_MIN_MARKS:
        print(json.dumps({"refused": f"--min-marks is {GO_NOGO_MIN_MARKS} for a read (use --any-host for a rehearsal)"}))
        return 2
    kw.pop("min_marks")
    try:
        res = read_go_no_go(a.latency, change_id=a.change_id, reads_path=a.reads, **kw)
    except lv.LiveRefused as exc:
        print(json.dumps({"refused": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(res, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
