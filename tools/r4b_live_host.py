#!/usr/bin/env python3
"""r4b_live_host: the DRY decision-host loop of r4-b (EXP-027 book, frozen). DRY and KEYLESS: it ingests a feed, decides at hour marks, writes the decision
log and a latency record per mark. It never sends, holds no key and opens no wallet (tools/r4b_live.LIVE_BUILT is False and stays False).

Where it sits (QP-RULING-1030 Addenda 3 and 4; DEC-032 Amendment 8 and Errata 1 and 2)
  source (a Helius mark-window client merged with the public tape; a separate PR) -> tools/r4b_feed_adapter -> tools/r4b_live.Engine -> arena_declog.
  The host is programmed against a small duck-typed SOURCE and tested with fakes; the real merger is not in this module.

The source (any object with these)
  iter(source)                 yields (tape_row, t_recv_ms) in receive order, or None for an idle tick (so the host can act on time with no new row);
                               the iteration ends the run.
  source.mark_complete(T, sd)  True once every print of a slot < sd is in the stream the host has been handed (the seam: public tape for history,
                               the window for the tail, deduplicated on (signature, event_index)).
  source.failed(T)             None, or a short code ([a-z0-9_]{1,32}: connect, silence, data_loss ...) once the source knows mark T can never be complete.
  source.buy_hold()            None, or a short code (the daily credit cap) while new buys must not be made; exits are never held.
  source.tip_slot(commitment)  optional: the highest slot the source has seen at that commitment ("processed" or "confirmed"), or None. Without it the d_*
                               fields of the latency record are None and the go/no-go cannot be judged.
  Reason codes that do not match [a-z0-9_]{1,32} are logged as "unspecified": nothing the source says (an exception text, a URL) reaches a log.

What the loop does (a mark T is every UTC hour)
  start      repo_head_sha (REFUSES a dirty tree), the `start` row, restart_exits for every position the last process left open (tools.r4b_live.host_start),
             and the engine must be a warm-up engine (new_host_engine: no entry for 52 h of ingested block time, Amendment 8 Erratum 1).
  ingest     each row -> FeedAdapter -> engine; a GapRecord that halts buys -> a `gap` row (log_gap). A reconnect without data loss writes nothing.
  T - 5 s    engine.precompute(T): each candidate member's print state is cached (tools/r4b_live; decide(T) is byte-identical with and without it).
  T          wait until source.mark_complete(T, sd) (sd from the engine's own clock), or source.failed(T), or T + 20 s.
               complete    -> engine.decide(T), the declog rows (exits first, then entries in rank order), one latency record.
               buy_hold()  -> the same, but the engine makes NO entry (decide(hold_entries=True)); the reason is logged. No gap row (no entry row exists).
               failed / bound passed / clock not complete -> FAIL CLOSED: a `gap` row (gap_kind incomplete_inputs, data_loss True: the executor holds buys until
                  10 min after it), an `incomplete_inputs` event, a latency record with status "incomplete". If the engine's clock can still give sd(T), the
                  engine is stepped with hold_entries=True so the rank exits of that mark are written (an exit reduces risk; the ranking is the one the inputs
                  that did arrive give, and no entry exists). If the clock cannot, nothing is decided: the book is not stepped, and the next decided mark closes
                  what is held with the engine's own `gap` rule (T1 = last mark + 1 h).
  Entries are never written for a mark whose inputs are not known complete.

The latency record (JSON line, one per mark; counts and slots only, no mint, score or rank)
  d_ready / d_decide / d_would_send = (the source's processed tip slot at that instant) - sd(T), in slots; the same under "confirmed" when the source reports
  it. Wall times are diagnostics. slot_s is per UTC date as EXP-027 :258-261 (3600 / median of the date's hourly slots-per-hour, an hour measured from the
  engine's own clock at the two marks that bound it, the r4b_read / cap_pick_score rules), labelled provisional until all 24 hours of the date are measured;
  k_1_9 = np.round(1.9 / slot_s), k_1_3 = np.round(1.3 / slot_s) (r4b_read.k_for); go_qty = d_would_send + 4 (the upper send-and-land stub, Erratum 2 Bar 2.3).
  `summarize` gives p50 / p90 and the go / no-go over at least 48 marks. The 4-slot stub never enters the match clause or P5 (Bar 2.4); this module does not
  evaluate them. It does not measure landing.

Seals. The decision log stays sealed (EXP-027 section 11; Amendment 8 B1): this module prints names and counts only. Its test data is synthetic. It reads no
tape and no sealed path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from collections import Counter
from datetime import datetime, timezone
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
LAT_LONG_S, LAT_SHORT_S = 1.9, 1.3  # EXP-027 section 3.3 lats
GO_NOGO_MIN_MARKS = 48
FAST0_HOSTNAME = "mal-fast-0"
HOUR_MIN_SPAN_S = 1800  # tools/cap_pick_score.HOUR_MIN_SPAN_S (a test asserts it)
HOUR_SPH_RANGE = (6000.0, 20000.0)  # tools/cap_pick_score.HOUR_SPH_RANGE (a test asserts it)
CODE_RE = re.compile(r"[a-z0-9_]{1,32}")
COMMITMENTS = ("processed", "confirmed")


def code(x: Any) -> str | None:
    """A source-supplied reason as a log-safe code: None stays None, a code that matches CODE_RE stays, anything else becomes "unspecified"."""
    if x is None:
        return None
    return x if isinstance(x, str) and CODE_RE.fullmatch(x) else "unspecified"


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
    repo_head_sha(), which refuses a dirty tree), shorter_warmup_proof (as host_start)."""

    def __init__(self, engine: lv.Engine, log: Any, source: Any, *, out_dir: Path | str, adapter: FeedAdapter | None = None,
                 now_ms: Callable[[], int] | None = None, ready_bound_s: float = READY_BOUND_S, precompute_lead_s: float = PRECOMPUTE_LEAD_S,
                 hostname: str | None = None, ntp_offset_ms: float | None = None, frozen_sha: str | None = None,
                 shorter_warmup_proof: str | None = None) -> None:
        self.engine, self.log, self.source = engine, log, source
        self.adapter = adapter if adapter is not None else FeedAdapter()
        self.now_ms = now_ms if now_ms is not None else (lambda: int(time.time() * 1000))
        self.ready_bound_ms = int(ready_bound_s * 1000)
        self.lead_ms = int(precompute_lead_s * 1000)
        self.hostname = hostname if hostname is not None else socket.gethostname()
        self.ntp_offset_ms = ntp_offset_ms
        self.frozen_sha, self.shorter_warmup_proof = frozen_sha, shorter_warmup_proof
        self.out = Path(out_dir) / "host"
        self.latency_path, self.events_path = self.out / "latency.jsonl", self.out / "events.jsonl"
        self.counts: Counter = Counter()
        self.records: list[dict] = []
        self.points: dict[int, tuple[int, int]] = {}  # mark -> (sd, clock second): the slot_s inputs
        self.next_mark: int | None = None
        self._cur: dict | None = None
        self._gaps_since_mark = 0  # gap rows written since the previous mark's record
        self._last_ms = 0
        self._started = False

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
        """host_start (the dirty-tree refusal, the start row, the restart exits), then load the slot points of a previous run of this out_dir (a restart
        keeps its per-day slot_s). The engine must carry a warm-up. Returns host_start's dict."""
        if self._started:
            raise lv.LiveRefused("host already started")
        if self.engine.warmup_s is None:
            raise lv.LiveRefused("a decision host needs a warm-up engine (tools.r4b_live.new_host_engine): a restarted engine must not buy on partial state")
        now = int(now_ms if now_ms is not None else self.now_ms())
        sha = self.frozen_sha if self.frozen_sha is not None else lv.repo_head_sha()  # refuses a dirty tree
        out = lv.host_start(self.log, now_ms=now, frozen_sha=sha, warmup_s=self.engine.warmup_s, shorter_warmup_proof=self.shorter_warmup_proof)
        self._seen([out["start"], *out["exits"]])
        if self.latency_path.exists():
            for line in self.latency_path.read_text().splitlines():
                r = json.loads(line) if line.strip() else {}
                if r.get("rec") == "mark" and r.get("sd") is not None and r.get("bt_star") is not None:
                    self.points[int(r["mark"])] = (int(r["sd"]), int(r["bt_star"]))
        self.next_mark = (now // 1000 // MARK_S + 1) * MARK_S
        self._started = True
        self._event("start", frozen_sha=sha, warmup_s=self.engine.warmup_s, restart_exits=len(out["exits"]), hostname=self.hostname,
                    ntp_offset_ms=self.ntp_offset_ms, first_mark=self.next_mark, prior_points=len(self.points))
        return out

    # -- the loop
    def run(self, max_marks: int | None = None) -> dict:
        """Drive the source until it ends or max_marks marks (decided or abandoned) are done. Returns the counts (names and numbers only)."""
        if not self._started:
            raise lv.LiveRefused("call start() first")
        done0 = self.counts["marks"]
        for item in self.source:
            now = self.now_ms()
            if item is not None:
                self._ingest(item[0], now)
            self._advance(now)
            if max_marks is not None and self.counts["marks"] - done0 >= max_marks:
                break
        return dict(self.counts)

    def _ingest(self, row: Mapping[str, Any], now: int) -> None:
        self.counts["rows_in"] += 1
        for mp in apply(self.engine, self.adapter.feed(row)):
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
            out[c] = int(v) if isinstance(v, (int, np.integer)) and not isinstance(v, bool) else None
        return out

    def _advance(self, now: int) -> None:
        while self._mark_step(now):
            pass

    def _mark_step(self, now: int) -> bool:
        """Advance the pending mark one step; True if the mark was finished (so the next one may be due on the same instant)."""
        T = int(self.next_mark)  # type: ignore[arg-type]
        t_ms = T * 1000
        cur = self._cur
        if cur is None:
            cur = self._cur = {"pre": None}
        if cur["pre"] is None and now >= t_ms - self.lead_ms:
            cur["pre"] = self._precompute(T, now)
        if now < t_ms:
            return False
        fail = code(self.source.failed(T))
        pt = self.engine.clock_point(T)
        if fail is None and pt is not None and bool(self.source.mark_complete(T, pt[0])):
            cur.update(ready_ms=now, ready_tip=self._tip(), pt=pt)
            hold = code(self.source.buy_hold())
            self._decide(T, now, hold=hold, incomplete=None)
            return True
        if fail is not None:
            self._abandon(T, now, f"failed_{fail}", pt)
            return True
        if now >= t_ms + self.ready_bound_ms:
            self._abandon(T, now, "bound_clock" if pt is None else "bound_incomplete", pt)
            return True
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

    # -- one mark
    def _decide(self, T: int, now: int, *, hold: str | None, incomplete: str | None, pt: tuple[int, int] | None = None) -> None:
        cur = self._cur or {}
        pt = pt or cur.get("pt")
        gaps, self._gaps_since_mark = self._gaps_since_mark, 0
        if incomplete is not None:  # fail closed first: the gap row precedes anything else of this mark, so the executor holds buys from here
            self._gap_row(T, now, incomplete)
        res = None
        if pt is not None:
            try:
                res = self.engine.decide(T, hold_entries=hold is not None or incomplete is not None)
            except lv.LiveRefused:  # the clock guard said ready, so this is not expected; nothing was decided, so hold buys
                if incomplete is None:
                    incomplete = "decide_refused"
                    self._gap_row(T, now, incomplete)
        t_decide = self.now_ms()
        tip_decide = self._tip()
        rows: list[dict] = []
        if res is not None:
            members = {m.mint: m for m in self.engine.members()}
            rows = lv.log_actions(self.log, res, members, self._ms(t_decide))
            self._seen(rows)
            self.engine.prune(T)
        t_send = self.now_ms()
        tip_send = self._tip()
        if hold is not None and res is not None:
            self._event("entries_held", mark=T, reason=hold, suppressed=res.suppressed)
        status = "incomplete" if (incomplete is not None or res is None) else ("decided_held" if hold else "decided")
        self._record(T, status=status, t_decide=t_decide, t_send=t_send, tip_decide=tip_decide, tip_send=tip_send, pt=pt, res=res, rows=rows, hold=hold,
                     reason=incomplete, gaps=gaps)

    def _gap_row(self, T: int, now: int, reason: str) -> None:
        g = lv.log_gap(self.log, GapRecord("incomplete_inputs", None, now, T * 1000, now, True, True), now_ms=self._ms(now))
        self._seen([g])
        self.counts["gap_rows"] += 1
        self._event("incomplete_inputs", mark=T, reason=reason)

    def _abandon(self, T: int, now: int, reason: str, pt: tuple[int, int] | None) -> None:
        self._decide(T, now, hold=None, incomplete=reason, pt=pt)

    def _record(self, T: int, *, status: str, t_decide: int, t_send: int, tip_decide: dict, tip_send: dict, pt: tuple[int, int] | None,
                res: lv.MarkResult | None, rows: list[dict], hold: str | None, reason: str | None, gaps: int) -> None:
        cur = self._cur or {}
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
        rec = {"schema": SCHEMA, "rec": "mark", "mark": T, "date": date, "status": status, "hold": hold, "reason": reason, "sd": sd,
               "bt_star": int(pt[1]) if pt is not None else None, "warmup": bool(res.warmup) if res else None, "precompute": pre,
               "slot_s": s_s, "slot_s_provisional": prov, "slot_s_hours": hrs, "slot_s_from": s_src, "k_1_9": k19, "k_1_3": k13,
               "t_mark_ms": T * 1000, "t_ready_ms": cur.get("ready_ms"), "t_decide_ms": t_decide, "t_would_send_ms": t_send,
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


# ------------------------------------------------------------------------------------------------------------------------------- summary and go / no-go
def _pq(xs: Sequence[float], q: float) -> float | None:
    return float(np.percentile(np.asarray(xs, float), q)) if len(xs) else None


def summarize(records: Iterable[Mapping[str, Any]], *, min_marks: int = GO_NOGO_MIN_MARKS, decide_commitment: str = "processed", require_host: str | None = FAST0_HOSTNAME,
              stub_slots: int = SEND_LAND_STUB_SLOTS) -> dict:
    """p50 / p90 of d_ready, d_decide and d_would_send (slots) per commitment, and the Erratum 2 Bar 2.3 go / no-go:
         GO  iff  p50(d_would_send) + 4 <= k_1_9 = round(1.9 / slot_s), over at least `min_marks` counted marks, on mal-fast-0, with the T - 5 s precompute.
    Rules fixed here, outcome-blind (flag them in review; the Erratum states the quantity, not these corners):
      * A mark counts if it was decided on complete inputs (status decided or decided_held), its precompute ran, d_would_send exists for the deciding
        commitment, and its date's slot_s is FINAL (24 measured hours, recomputed here from the recorded points of ALL records). Marks on a provisional date
        are reported (n_provisional) and left out of the verdict.
      * Several dates: the verdict's k_1_9 is the SMALLEST of the counted dates' (the harder bar); per-date p50 and k are reported beside it.
      * p50 / p90 are np.percentile (linear) over the counted marks, unrounded.
      * Incomplete marks never count toward the 48 and never improve the quantity; their number is reported (n_incomplete) and a non-zero count is a flag.
      * require_host: every counted record must carry that hostname, else the verdict is INSUFFICIENT.
    Verdict: GO | NO_GO | INSUFFICIENT (fewer than min_marks counted, a wrong host, or a mark without the precompute)."""
    recs = [r for r in records if r.get("rec") == "mark"]
    points = {int(r["mark"]): (int(r["sd"]), int(r["bt_star"])) for r in recs if r.get("sd") is not None and r.get("bt_star") is not None}
    table = slot_s_table(points)
    out: dict[str, Any] = {"schema": SCHEMA, "n_records": len(recs), "n_incomplete": sum(1 for r in recs if r["status"] == "incomplete"),
                           "n_decided": sum(1 for r in recs if r["status"] in ("decided", "decided_held")), "n_held": sum(1 for r in recs if r["status"] == "decided_held"),
                           "slot_s": {d: dict(v) for d, v in sorted(table.items())}, "decide_commitment": decide_commitment, "stub_slots": stub_slots}
    counted, n_prov, n_nopre = [], 0, 0
    for r in recs:
        if r["status"] not in ("decided", "decided_held"):
            continue
        pre = r.get("precompute") or {}
        if not (pre.get("ran") and pre.get("rel_ms") is not None and pre["rel_ms"] < 0):  # the precompute ran, and ahead of the mark
            n_nopre += 1
            continue
        if (r.get(decide_commitment) or {}).get("d_would_send") is None:
            continue
        t = table.get(r["date"])
        if t is None or not t["final"]:
            n_prov += 1
            continue
        counted.append((r, t["slot_s"]))
    out["n_counted"], out["n_provisional"], out["n_without_precompute"] = len(counted), n_prov, n_nopre
    for c in COMMITMENTS:
        vals = {f: [(r[c][f]) for r, _ in counted if r.get(c) and r[c].get(f) is not None] for f in ("d_ready", "d_decide", "d_would_send")}
        out[c] = {f: {"n": len(v), "p50": _pq(v, 50), "p90": _pq(v, 90)} for f, v in vals.items()}
    per_date: dict[str, dict] = {}
    for r, s_s in counted:
        e = per_date.setdefault(r["date"], {"slot_s": s_s, "k_1_9": k_for(LAT_LONG_S, s_s), "k_1_3": k_for(LAT_SHORT_S, s_s), "d_would_send": []})
        e["d_would_send"].append(r[decide_commitment]["d_would_send"])
    out["per_date"] = {d: {"slot_s": e["slot_s"], "k_1_9": e["k_1_9"], "k_1_3": e["k_1_3"], "n": len(e["d_would_send"]), "p50_d_would_send": _pq(e["d_would_send"], 50),
                           "p90_d_would_send": _pq(e["d_would_send"], 90)} for d, e in sorted(per_date.items())}
    dws = [r[decide_commitment]["d_would_send"] for r, _ in counted]
    p50 = _pq(dws, 50)
    k19 = min((e["k_1_9"] for e in per_date.values()), default=None)
    out["k_1_9_used"] = k19
    out["go_qty"] = None if p50 is None else p50 + stub_slots
    hosts = sorted({str(r.get("hostname")) for r, _ in counted})
    out["hosts"] = hosts
    why = []
    if len(counted) < min_marks:
        why.append(f"fewer than {min_marks} counted marks ({len(counted)})")
    if require_host is not None and hosts != [require_host]:
        why.append(f"counted marks are not all from {require_host}")
    if n_nopre:
        why.append(f"{n_nopre} decided mark(s) left out: no precompute ahead of the mark")
    if len(counted) < min_marks or (require_host is not None and hosts != [require_host]):
        out["verdict"] = "INSUFFICIENT"
    else:
        out["verdict"] = "GO" if (p50 is not None and k19 is not None and p50 + stub_slots <= k19) else "NO_GO"
    out["notes"] = why
    return out


# ------------------------------------------------------------------------------------------------------------------------------- CLI: summarize a latency file
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b DRY host: summarize a latency.jsonl (p50 / p90 in slots and the Bar 2.3 go / no-go). Counts only.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("summarize")
    sm.add_argument("--latency", required=True)
    sm.add_argument("--min-marks", type=int, default=GO_NOGO_MIN_MARKS)
    sm.add_argument("--commitment", default="processed", choices=COMMITMENTS)
    sm.add_argument("--any-host", action="store_true", help="do not require hostname mal-fast-0 (a rehearsal)")
    a = ap.parse_args(argv)
    recs = [json.loads(x) for x in Path(a.latency).read_text().splitlines() if x.strip()]
    print(json.dumps(summarize(recs, min_marks=a.min_marks, decide_commitment=a.commitment, require_host=None if a.any_host else FAST0_HOSTNAME), sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
