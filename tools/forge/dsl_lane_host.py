#!/usr/bin/env python3
"""dsl_lane_host: KEYLESS decision host that runs ONE FORGE DSL spec (tools/forge/evaluate.validate_spec) on a live arena-layout tape and writes decision rows the lane executor
can follow. PAPER decisions only: no wallet, no key, no send. FORGE-SPEC Part C, PR-A. Nothing here says a spec makes money.

WHAT IT REUSES (no second copy of the math)
  * parse: tools.forge.events.parse_lines (one tape line at a time), so the WSOL / V-band / post-trade-spot / hash conventions are the batch builder's own.
  * features: tools.forge.events.compute_features (factored out of hour_events in this PR, otherwise unchanged) on one pool's history slice. The history window is the batch
    builder's: rows of the pool from the start of the event's hour minus 6 h.
  * candidate events: the same thinning (first print per (pool, aligned 30 s bucket), then >= 30 s apart per pool, restarting each hour), the same 60 s-after-gap exclusion per
    venue, the same real-quote floor, venue filter, conditions, cooldown pass and one_per_pool pass as evaluate.select / run_trades.
  * exits: the evaluator's grid (common.GRID_S after the fill print), the same uint16 ratio codes (common.encode_ratio / DECODE), migrated and gap points, and the SAME function
    evaluate.simulate_paths, called on the prefix of the path known so far. tp/sl/trail trigger at grid point k and execute at k+1; max_hold_s executes at the first grid point
    >= max_hold_s; stop beats tp at the same point.

PRICE CONVENTION (events.py / common.py): PumpSwap rows are PRE-trade reserves, Q = quote_reserve + virtual_quote_reserves; curve rows are POST-trade. The spot of a path point is the
POST-trade spot of the pool's last print at or before the grid time; the fill state is the first print >= 2.0 s after the event (PumpSwap: that print's PRE state; curve: the previous
print's post state).

ROWS (the format tools/floor_arena_executor.Bridge reads; arena_declog.DecisionLog hash chain at <root>/decisions/<bot_id>/<day>.jsonl):
  start  {binding, frozen_sha = host HEAD sha, spec_sha256, spec, rules}      one per process start
  entry  {order_id, pool, meta{rule, mint, venue, t_event_ms, features, tp, sl, trail, max_hold_s, spec_sha256}, decision_slot, stake_sol, attempt_key}
  exit   {order_id, pool, reason in (tp, sl, trail, time, migrated, no_fill), decision_slot, attempt, grid_ms, exec_ms}     exec_ms = the evaluator's exit time
  entry_refused_gap  evidence, not an order (the executor ignores unknown kinds)
Row `ms` is the host clock (the arrival time of the tape row that made the decision), so the chain is time-ordered; exit rows carry grid_ms (the trigger grid point) and exec_ms.

SAFETY (as tools/floor_lane_host.py)
  * GAPS fail closed. An entry is dropped (and `entry_refused_gap` logged) when a data-loss gap on its venue, or a host_down span of this host, overlaps [t - gap_lookback_s, t].
    The default lookback is the longest feature window the spec uses (min 60 s, the evaluator's own exclusion, which is applied silently first). A dropped entry is still tracked as
    a SHADOW position (no rows) so cooldown / one_per_pool state stays the evaluator's: the lane entries are a subset of the evaluator's, never a superset.
  * `run` refuses a dirty or unknown checkout and records the code's identity (HEAD sha as frozen_sha, host file sha256, forge file sha256s, spec sha256) in the start row. The
    registry written by `register` pins the spec and file hashes; `run` refuses on any mismatch.
  * A restart with a position open in the log is refused (the exit tracking would be lost); a decision log without its checkpoint is refused; downtime is recorded as a host_down span.
  * Nothing is written inside /data/mal/arena.

KNOWN DIFFERENCES FROM evaluate.py (look-ahead filters a live host cannot apply; stated, not hidden)
  * An event whose fill print never comes (none within 10 min) or whose fill state is invalid is DROPPED by the evaluator before cooldown; the host has already decided and logs an
    `exit` with reason no_fill. The real-quote floor is tested on the event row's own real quote, not on the fill state's.
  * Trades whose exit would fall past the end of data are dropped (censored) by the evaluator; the host simply has not decided yet.
  * A gap row that reaches the tape AFTER a grid point was decided cannot change that decision (the evaluator would have marked the point a gap).
  * An event at exactly an exit instant (event t == exec time) is the one tie the host cannot see ahead (the evaluator frees the pool, the host has not yet decided).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import argparse
import bisect
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from tools import arena_rules as R
from tools.arena_declog import GENESIS, DecisionLog, LogChainError
from tools.arena_paper import canon
from tools.arena_pool_table import _lines
from tools.arena_rules import position_id
from tools.forge import common as C
from tools.forge import evaluate as E
from tools.forge import events as EV

REPO = Path(__file__).resolve().parent.parent.parent
CKPT_SCHEMA = "dsl_lane_host_ckpt_v1"
REG_SCHEMA = "dsl_lane_host_registry_v1"
KIND_GAP_REFUSED = "entry_refused_gap"
HOUR_MS = 3_600_000
HIST_H = 6  # events.hour_events loads [H - 6 h, H + 1 h]: the feature history starts 6 h before the event's hour
PIN_FILES = ("tools/forge/events.py", "tools/forge/evaluate.py", "tools/forge/common.py", "tools/arena_stack.py", "tools/arena_pool_table.py", "tools/forge/dsl_lane_host.py")
NO_FILL_MS = C.FILL_DELAY_MS + C.FILL_MAX_WAIT_MS
CKPT_EVERY_MS = 60_000
LOOKBACK_FEATURE_S = {"ret_30s": 30, "ret_1m": 60, "ret_5m": 300, "ret_15m": 900, "ret_1h": 3600, "ret_6h": 21600, "dd_high": 21600, "rise_low": 21600, "age_s": 21600, "mig_age_s": 21600}
for _w, _s in (("1m", 60), ("5m", 300), ("1h", 3600)):
    for _k in ("n", "uniq", "buy_share", "buy_sol", "sell_sol", "max_sell", "max_buy", "net_flow"):
        LOOKBACK_FEATURE_S[f"{_k}_{_w}"] = _s
REASON = E.REASONS  # ("time", "sl", "trail", "tp", "migrated")
_DT = dict(t=np.int64, pool=np.int64, mint=np.int64, trader=np.uint32, venue=np.int8, isbuy=np.int8, sol=np.float64, tok=np.float64, q=np.float64, b=np.float64,
           rq=np.float64, rb=np.float64, spot=np.float64, qpt=np.float64, qpr=np.float64)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def spec_hash(spec: Mapping[str, Any]) -> str:
    return hashlib.sha256(canon(spec).encode()).hexdigest()


def file_pins(repo: Path = REPO) -> dict[str, str]:
    return {rel: sha256_file(Path(repo) / rel) for rel in PIN_FILES}


def default_lookback_s(spec: Mapping[str, Any]) -> int:
    """The longest feature window the spec's conditions use (min 60 s, the evaluator's own post-gap exclusion)."""
    return max([60] + [LOOKBACK_FEATURE_S.get(c["f"], 60) for c in spec["entry"]])


def parse_hour(s: str) -> int:
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError) as e:
        raise R.HostError(f"hour must look like 2026-11-01T00 (got {s!r})") from e


# ------------------------------------------------------------------------------------------------------------------------------ tape follow
class TapeFollower:
    """Reads tape-<hour>.jsonl[.zst] files of an arena-layout dir from a start hour on, in order, returning only complete new lines. The cursor is (hour, lines consumed), valid
    across the writer's .jsonl -> .jsonl.zst switch. Advances to the next hour only when that hour's file exists; one missing hour followed by an existing one is injected as an
    `all`-program data-loss gap line."""

    def __init__(self, tape_dir: str | os.PathLike, start_hour_ms: int) -> None:
        self.dir = Path(tape_dir)
        self.hour_ms = int(start_hour_ms)
        self.nlines = 0
        self.off = 0

    def _path(self, hms: int) -> tuple[Path | None, bool]:
        h = EV.C.hour_str(hms)
        p = self.dir / f"tape-{h}.jsonl"
        if p.exists():
            return p, False
        z = self.dir / f"tape-{h}.jsonl.zst"
        return (z, True) if z.exists() else (None, False)

    def _exists(self, hms: int) -> bool:
        return self._path(hms)[0] is not None

    def read(self) -> list[bytes]:
        out: list[bytes] = []
        while True:
            p, zst = self._path(self.hour_ms)
            if p is not None:
                if zst:
                    lines = list(_lines(p))
                    new = lines[self.nlines:]
                    self.nlines = max(self.nlines, len(lines))
                    out.extend(new)
                else:
                    with open(p, "rb") as fh:
                        fh.seek(self.off)
                        data = fh.read()
                    cut = data.rfind(b"\n") + 1
                    chunk = data[:cut]
                    self.off += cut
                    new = chunk.splitlines(keepends=True)
                    self.nlines += len(new)
                    out.extend(new)
            nxt = self.hour_ms + HOUR_MS
            if self._exists(nxt):
                self.hour_ms, self.nlines, self.off = nxt, 0, 0
                continue
            if p is not None and self._exists(nxt + HOUR_MS):  # one hour file missing between two present ones
                out.append(canon({"type": "gap", "prog": "all", "data_loss": True, "kind": "missing_hour", "from_ms": nxt, "to_ms": nxt + HOUR_MS}).encode() + b"\n")
                self.hour_ms, self.nlines, self.off = nxt + HOUR_MS, 0, 0
                continue
            return out


# ------------------------------------------------------------------------------------------------------------------------------ the engine
class Pos:
    __slots__ = ("pid", "pool", "mint", "pool_h", "venue", "mint_h", "t_ev", "hs_ev", "slot", "shadow", "stage", "t_fill", "spot_fill", "P", "j", "carry", "exec_ms", "reason", "meta")

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


class PoolHist:
    __slots__ = ("cols",)

    def __init__(self) -> None:
        self.cols: dict[str, list] = {k: [] for k in EV.ING_COLS}


class Engine:
    """Streams tape lines (feed_line) and calls sink.entry / sink.exit / sink.refused. Pure of I/O apart from the sink; deterministic in the line sequence."""

    def __init__(self, spec: Mapping[str, Any], sink: Any, *, live_from_ms: int = 0, gap_lookback_s: int | None = None, host_down: list[dict] | None = None) -> None:
        self.spec = E.validate_spec(dict(spec))
        self.sink = sink
        self.live_from_ms = int(live_from_ms)
        self.lookback_ms = 1000 * max(60, int(gap_lookback_s if gap_lookback_s is not None else default_lookback_s(self.spec)))
        self.clock = 0
        self.hist: dict[int, PoolHist] = {}
        self.first_mint: dict[int, int] = {}
        self.first_pool: dict[int, int] = {}
        self.gaps_v: dict[int, list[tuple[int, int]]] = {0: [], 1: []}  # events.parse_lines gap venue coding: 1 = prog pumpswap, else 0
        self.gaps_all: list[dict] = list(host_down or [])  # safety view: data-loss gaps with their program, plus host_down spans
        self.comp: dict[int, list[int]] = {}
        self.thin: dict[int, list[int]] = {}  # pool -> [hour index, bucket of the last row seen, hour index of the greedy, last kept t]
        self.cool_last: dict[int, int] = {}
        self.pos: list[Pos] = []
        self.next_due = 1 << 62
        self.counters: dict[str, int] = {}
        self.feat_hook: Callable[[int, int, np.ndarray], None] | None = None  # tests: the feature row of every event that passes the venue and real-quote tests

    def _c(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    # -- input
    def feed_line(self, ln: bytes) -> None:
        if b'"type":"trade"' not in ln and b'"type":"complete"' not in ln and b'"data_loss":true' not in ln:
            return
        out = EV.parse_lines([ln])
        if len(out["gap_a"]):
            a, b, v = int(out["gap_a"][0]), int(out["gap_b"][0]), int(out["gap_v"][0])
            self.gaps_v[v].append((a, b))
            try:
                g = json.loads(ln)
                prog = str(g.get("prog"))
            except ValueError:
                prog = "all"
            self.gaps_all.append({"prog": prog, "kind": "data_loss", "from_ms": a, "to_ms": b})
            self._c("gaps")
            return
        if len(out["comp_t"]):
            self.comp.setdefault(int(out["comp_mint"][0]), []).append(int(out["comp_t"][0]))
            return
        if len(out["t"]) == 0:
            return
        self._row({k: out[k][0] for k in EV.ING_COLS}, ln)

    def add_gap(self, span: dict) -> None:
        self.gaps_all.append(dict(span))

    # -- one parsed trade row
    def _row(self, r: dict, ln: bytes) -> None:
        t = int(r["t"])
        self.clock = max(self.clock, t)
        pool_h, mint_h = int(r["pool"]), int(r["mint"])
        self._sweep()
        self._fills(r, pool_h)
        self.first_mint.setdefault(mint_h, t)
        self.first_pool.setdefault(pool_h, t)
        if t < self.first_mint[mint_h]:
            self.first_mint[mint_h] = t
        if t < self.first_pool[pool_h]:
            self.first_pool[pool_h] = t
        ph = self.hist.setdefault(pool_h, PoolHist())
        times = ph.cols["t"]
        ix = bisect.bisect_right(times, t)
        for k in EV.ING_COLS:
            ph.cols[k].insert(ix, r[k].item() if hasattr(r[k], "item") else r[k])
        cut = (self.clock // HOUR_MS) * HOUR_MS - HIST_H * HOUR_MS
        if times and times[0] < cut:
            n = bisect.bisect_left(times, cut)
            for k in EV.ING_COLS:
                del ph.cols[k][:n]
            ix -= n
        self._event(r, ln, pool_h, mint_h, ph, ix)

    # -- candidate events
    def _event(self, r: dict, ln: bytes, pool_h: int, mint_h: int, ph: PoolHist, ix: int) -> None:
        t = int(r["t"])
        hs = (t // HOUR_MS) * HOUR_MS
        bk = (t - hs) // C.THIN_MS
        st = self.thin.get(pool_h)
        hidx = hs // HOUR_MS
        newb = st is None or st[0] != hidx or st[1] != bk
        if st is None:
            st = self.thin[pool_h] = [hidx, bk, -1, 0]
        st[0], st[1] = hidx, bk
        if not newb:
            return
        if st[2] != hidx or t - st[3] >= C.THIN_MS:  # greedy >= 30 s per pool, restarting at the hour start
            st[2], st[3] = hidx, t
        else:
            return
        venue = int(r["venue"])
        for a, b in self.gaps_v[venue]:
            if a <= t and b + C.GAP_EXCL_MS >= t:
                self._c("events_gap_excluded")
                return
        self._c("events")
        if self.spec["venue"] != "any" and venue != self.spec["venue"]:
            return
        if not (float(r["rq"]) >= C.MIN_REAL_QUOTE):
            return
        X = self._features(ph, ix, hs, pool_h, mint_h, venue)
        if self.feat_hook is not None:
            self.feat_hook(pool_h, t, X[0].copy())
        for c in self.spec["entry"]:
            if not bool(E.OPS[c["op"]](X[0, C.FIDX[c["f"]]], np.float32(c["v"]))):
                return
        self._c("candidates")
        cd = self.spec["cooldown_s"] * 1000
        if cd > 0:
            last = self.cool_last.get(pool_h)
            if last is not None and t - last < cd:
                return
            self.cool_last[pool_h] = t
        if self.spec["one_per_pool"] and any(p.pool_h == pool_h and (p.stage != "done" or p.exec_ms > t) for p in self.pos):
            self._c("one_per_pool_blocked")
            return
        self._open(r, ln, X, t, hs, venue)

    def _features(self, ph: PoolHist, ix: int, hs: int, pool_h: int, mint_h: int, venue: int) -> np.ndarray:
        times = ph.cols["t"]
        lo = bisect.bisect_left(times, hs - HIST_H * HOUR_MS)
        S = {k: np.asarray(ph.cols[k][lo:], dtype=_DT[k]) for k in EV.ING_COLS}
        n = len(S["t"])
        key = S["t"] - C.T0_MS
        rank = np.zeros(n, np.int64)
        fs = {"mint": (np.array([mint_h], np.int64), np.array([self.first_mint[mint_h]], np.int64)), "pool": (np.array([pool_h], np.int64), np.array([self.first_pool[pool_h]], np.int64))}
        return EV.compute_features(S, key, rank, np.zeros(n, np.int64), np.array([ix - lo], np.int64), np.array([venue], np.int8), fs)

    # -- entries
    def _open(self, r: dict, ln: bytes, X: np.ndarray, t: int, hs: int, venue: int) -> None:
        try:
            raw = json.loads(ln)
        except ValueError:
            return
        pool_s = str(raw.get("pool") if venue == 1 else raw.get("mint"))
        mint_s = str(raw.get("mint"))
        slot = raw.get("slot")
        slot = int(slot) if isinstance(slot, int) and not isinstance(slot, bool) else 0
        ex = self.spec["exit"]
        feats = {f: (None if not np.isfinite(X[0, i]) else float(X[0, i])) for f, i in C.FIDX.items()}
        meta = {"rule": "dsl", "mint": mint_s, "venue": venue, "t_event_ms": t, "features": feats, "tp": ex["tp"], "sl": ex["sl"], "trail": ex["trail"], "max_hold_s": ex["max_hold_s"]}
        shadow = t < self.live_from_ms
        refuse = None if shadow else self._gap_refusal(t, venue)
        p = Pos(pid=position_id("dsl", pool_s, t, slot), pool=pool_s, mint=mint_s, pool_h=int(r["pool"]), venue=venue, mint_h=int(r["mint"]), t_ev=t, hs_ev=hs, slot=slot,
                shadow=shadow or refuse is not None, stage="pending", P=np.full(C.NG, C.C_CENS, np.uint16), j=0, meta=meta)
        self.pos.append(p)
        self.next_due = min(self.next_due, t + NO_FILL_MS)
        if shadow:
            self._c("shadow_entries")
        elif refuse is not None:
            self._c("entries_refused_gap")
            self.sink.refused(t, {"kind": KIND_GAP_REFUSED, "reason": "feed_gap", "pool": pool_s, "mint": mint_s, "trigger_slot": slot, "trigger_ms": t, "gap": refuse})
        else:
            self._c("entries")
            self.sink.entry(t, {"kind": "entry", "order_id": p.pid, "pool": pool_s, "meta": meta, "decision_slot": slot, "stake_sol": self.spec["stake_sol"],
                                "attempt_key": f"dsl:{pool_s}:{t}"})

    def _gap_refusal(self, t: int, venue: int) -> dict | None:
        lo = t - self.lookback_ms
        for g in self.gaps_all:
            if g["to_ms"] < lo or g["from_ms"] > t:
                continue
            prog = g.get("prog", "all")
            hit = g.get("kind") == "host_down" or prog == "all" or (venue == 1) == (prog == "pumpswap")
            if hit:
                return {k: g.get(k) for k in ("prog", "kind", "from_ms", "to_ms")}
        return None

    # -- fills
    def _fills(self, r: dict, pool_h: int) -> None:
        t = int(r["t"])
        for p in self.pos:
            if p.stage != "pending" or p.pool_h != pool_h or t < p.t_ev + C.FILL_DELAY_MS:
                continue
            if t - p.t_ev > NO_FILL_MS:
                self._no_fill(p, "no_fill")
                continue
            ph = self.hist.get(pool_h)
            if int(r["venue"]) == 1:
                Qf, Bf = float(r["q"]), float(r["b"])
                ok = Qf >= C.V_LO and Bf > 0
            else:
                if ph is None or not ph.cols["t"]:
                    self._no_fill(p, "no_fill")
                    continue
                Qf, Bf = float(ph.cols["q"][-1]), float(ph.cols["b"][-1])  # the previous print's post state
                ok = Qf > 0 and Bf > 0
            if not ok:
                self._no_fill(p, "no_fill")
                continue
            p.stage, p.t_fill, p.spot_fill, p.carry = "run", t, Qf / Bf, float(r["spot"])
            self.next_due = min(self.next_due, t + int(C.GRID_S[0]) * 1000)

    def _no_fill(self, p: Pos, reason: str) -> None:
        p.stage, p.exec_ms, p.reason = "done", self.clock, reason
        if not p.shadow:
            self.sink.exit(self.clock, {"kind": "exit", "order_id": p.pid, "pool": p.pool, "reason": reason, "decision_slot": p.slot, "attempt": 1, "grid_ms": None, "exec_ms": self.clock})

    # -- exits: grid points are finalized once the stream clock has passed them
    def _sweep(self) -> None:
        if self.clock <= self.next_due:
            return
        nd = 1 << 62
        for p in self.pos:
            if p.stage == "pending":
                if self.clock - p.t_ev > NO_FILL_MS:
                    self._no_fill(p, "no_fill")
                else:
                    nd = min(nd, p.t_ev + NO_FILL_MS)
            elif p.stage == "run":
                while p.stage == "run" and p.j < C.NG and p.t_fill + int(C.GRID_S[p.j]) * 1000 < self.clock:
                    self._point(p)
                if p.stage == "run":
                    nd = min(nd, p.t_fill + int(C.GRID_S[p.j]) * 1000)
        self.next_due = nd
        self.pos = [p for p in self.pos if not (p.stage == "done" and p.exec_ms <= self.clock)]

    def _point(self, p: Pos) -> None:
        j = p.j
        Tj = p.t_fill + int(C.GRID_S[j]) * 1000
        ph = self.hist.get(p.pool_h)
        sp = p.carry
        if ph is not None:
            i = bisect.bisect_right(ph.cols["t"], Tj) - 1
            if i >= 0:
                sp = float(ph.cols["spot"][i])
        p.carry = sp
        code = int(C.encode_ratio(np.array([sp / p.spot_fill]))[0])
        if p.venue == 0:
            cts = next((c for c in self.comp.get(p.mint_h, ()) if c >= p.hs_ev), None)
            if cts is not None and Tj >= cts:
                code = C.C_MIGRATED
        lo = p.t_fill + (int(C.GRID_S[j - 1]) * 1000 if j > 0 else 0)
        if any(a <= Tj and b >= lo for a, b in self.gaps_v[p.venue]):
            code = C.C_GAP
        p.P[j] = code
        p.j = j + 1
        kx, _r, reason, cens = E.simulate_paths(p.P[None, :], self.spec["exit"])
        kx, reason, cens = int(kx[0]), int(reason[0]), bool(cens[0])
        trig = reason in (1, 2, 3) and cens and kx == j + 1
        done = (not cens) and ((reason == 0 and kx == j) or reason == 4)
        if not (trig or done):
            return
        p.stage, p.exec_ms, p.reason = "done", p.t_fill + int(C.GRID_S[kx]) * 1000, REASON[reason]
        if not p.shadow:
            self.sink.exit(self.clock, {"kind": "exit", "order_id": p.pid, "pool": p.pool, "reason": p.reason, "decision_slot": p.slot, "attempt": 1, "grid_ms": Tj, "exec_ms": p.exec_ms})
        self._c("exits_" + p.reason)

    def open_positions(self) -> list[Pos]:
        return [p for p in self.pos if p.stage != "done" and not p.shadow]


# ------------------------------------------------------------------------------------------------------------------------------ sinks and I/O
class ListSink:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int, dict]] = []

    def entry(self, ms: int, body: dict) -> None:
        self.rows.append(("entry", ms, body))

    def exit(self, ms: int, body: dict) -> None:
        self.rows.append(("exit", ms, body))

    def refused(self, ms: int, body: dict) -> None:
        self.rows.append(("refused", ms, body))


class LogSink:
    """Appends to the hash-chained DecisionLog under arm `bot_id`. A write failure is fatal (HostFatal): nothing is decided that is not on disk first."""

    def __init__(self, log: DecisionLog, bot_id: str) -> None:
        self.log, self.bot_id = log, bot_id

    def _w(self, ms: int, body: dict) -> None:
        try:
            self.log.append(self.bot_id, int(ms), body)
        except (OSError, LogChainError) as e:
            raise R.HostFatal(f"decision log write failed: {type(e).__name__}") from e

    entry = exit = refused = _w


def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, canon(obj).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def open_in_log(log: DecisionLog, bot_id: str) -> dict[str, dict]:
    """order_id -> entry row for entries with no exit row yet."""
    opened: dict[str, dict] = {}
    for r in log.rows(bot_id):
        if r.get("kind") == "entry":
            opened[str(r["order_id"])] = r
        elif r.get("kind") == "exit":
            opened.pop(str(r.get("order_id")), None)
    return opened


def load_spec(d: Mapping[str, Any]) -> dict:
    spec = d.get("spec")
    if isinstance(spec, str):
        spec = json.loads(Path(spec).read_text())
    try:
        return E.validate_spec(spec)
    except E.SpecError as e:
        raise R.HostError(f"spec invalid: {e}") from e


def load_cfg(path: str) -> dict:
    d = json.loads(Path(path).read_text())
    for k in ("root", "tape_dir", "bot_id", "spec", "clock_start_ms"):
        if not d.get(k):
            raise R.HostError(f"config {k} is required")
    if not isinstance(d["bot_id"], str) or not d["bot_id"].replace("-", "").replace("_", "").isalnum():
        raise R.HostError("bot_id must be alphanumeric with - or _")
    if R._inside(str(d["root"]), R.ARENA_OUTPUT_ROOT):
        raise R.HostError(f"the DSL lane host never writes inside {R.ARENA_OUTPUT_ROOT}")
    return d


def register(d: Mapping[str, Any], repo: Path = REPO) -> dict:
    spec = load_spec(d)
    reg = {"schema": REG_SCHEMA, "bot_id": d["bot_id"], "spec": spec, "spec_sha256": spec_hash(spec), "pins": file_pins(repo), "clock_start_ms": int(d["clock_start_ms"])}
    p = Path(d["root"]) / "registry.json"
    if p.exists():
        raise R.HostError("already registered (registry.json exists): use a new root for a new spec")
    _atomic_write(p, reg)
    return reg


def check_registry(d: Mapping[str, Any], repo: Path = REPO) -> dict:
    p = Path(d["root"]) / "registry.json"
    if not p.exists():
        raise R.HostError("not registered: run `register` first")
    reg = json.loads(p.read_text())
    spec = load_spec(d)
    if reg.get("schema") != REG_SCHEMA or reg.get("bot_id") != d["bot_id"] or reg.get("spec_sha256") != spec_hash(spec):
        raise R.HostError("registry does not match this bot_id / spec")
    now = file_pins(repo)
    for rel, want in reg["pins"].items():
        if now.get(rel) != want:
            raise R.HostError(f"pin mismatch: {rel}")
    return reg


def run_binding(repo: Path = REPO, hb: Mapping[str, Any] | None = None) -> dict:
    """Refuses unless the checkout is genuinely clean (unknown counts as dirty)."""
    hb = dict(hb) if hb is not None else dict(R.head_binding(repo))
    if hb.get("dirty") is not False or not hb.get("git_sha"):
        raise R.HostError("the checkout is dirty or its state is unknown: run mode needs a clean checkout")
    return {"git_sha": hb["git_sha"], "dirty": False, "pins": file_pins(repo), "kind": "dsl_lane_host"}


class Host:
    def __init__(self, d: Mapping[str, Any], *, binding: Mapping[str, Any] | None, live: bool, since_ms: int, live_from_ms: int | None = None, now_fn: Callable[[], int] | None = None) -> None:
        self.d = d
        self.root = Path(d["root"])
        self.bot_id = str(d["bot_id"])
        self.spec = load_spec(d)
        self.log = DecisionLog(self.root)
        self.ckpt_path = self.root / "dsl_host" / "checkpoint.json"
        self.now_fn = now_fn or (lambda: int(time.time() * 1000))
        self.binding, self.live = binding, live
        self.since_ms = int(since_ms)
        self.live_from_ms = int(live_from_ms) if live_from_ms is not None else self.now_fn()
        self.sink = LogSink(self.log, self.bot_id)
        self.down: list[dict] = []
        self._last_ckpt = 0
        self._start()
        self.engine = Engine(self.spec, self.sink, live_from_ms=self.live_from_ms, gap_lookback_s=d.get("gap_lookback_s"), host_down=self.down)
        self.follower = TapeFollower(d["tape_dir"], self.since_ms)

    def _start(self) -> None:
        files = self.log.files(self.bot_id)
        if self.ckpt_path.exists():
            ck = json.loads(self.ckpt_path.read_text())
            if ck.get("schema") != CKPT_SCHEMA or ck.get("bot_id") != self.bot_id or ck.get("spec_sha256") != spec_hash(self.spec):
                raise R.HostError("checkpoint is not this book's")
            _, seq = self.log.verify(self.bot_id)
            if seq < int(ck["log_seq"]):
                raise R.HostError(f"decision log is behind its checkpoint (seq {seq} < {ck['log_seq']}): rows were lost")
            if open_in_log(self.log, self.bot_id):
                raise R.HostError("a position is open in the decision log: a restart would lose its exit tracking; refusing to start (wait for it to close, or use a new root)")
            self.down = [dict(s) for s in ck.get("down", []) if isinstance(s, dict)]
            hwm = int(ck["hwm_ms"])
            if self.live_from_ms > hwm:
                self.down.append({"prog": "all", "kind": "host_down", "from_ms": hwm, "to_ms": self.live_from_ms})
        elif files:
            raise R.HostError("a decision log exists but the checkpoint is missing: downtime is unknown; refusing to start (restore the checkpoint or use a new root)")
        if self.binding is not None:
            spec_sha = spec_hash(self.spec)
            self.log.append(self.bot_id, self.live_from_ms, {"kind": "start", "binding": dict(self.binding), "frozen_sha": self.binding["git_sha"], "spec_sha256": spec_sha,
                                                            "spec": self.spec, "rules": [self.bot_id]})

    def checkpoint(self) -> None:
        head, seq = self.log.head(self.bot_id)
        _atomic_write(self.ckpt_path, {"schema": CKPT_SCHEMA, "bot_id": self.bot_id, "spec_sha256": spec_hash(self.spec), "hwm_ms": int(self.engine.clock or self.live_from_ms),
                                       "log_head": head, "log_seq": seq, "down": self.down[-50:]})

    def step(self, to_ms: int | None = None) -> int:
        n = 0
        for ln in self.follower.read():
            if to_ms is not None and self._t_of(ln) is not None and self._t_of(ln) > to_ms:
                break
            self.engine.feed_line(ln)
            n += 1
            if self.engine.clock - self._last_ckpt >= CKPT_EVERY_MS and self.engine.clock >= self.live_from_ms:
                self._last_ckpt = self.engine.clock
                self.checkpoint()
        return n

    @staticmethod
    def _t_of(ln: bytes) -> int | None:
        i = ln.find(b'"t_recv_ms":')
        if i < 0:
            return None
        j = i + 12
        k = j
        while k < len(ln) and ln[k:k + 1].isdigit():
            k += 1
        return int(ln[j:k]) if k > j else None

    def run_forever(self, end_ms: int, sleep: Callable[[float], None] = time.sleep) -> None:
        try:
            while self.now_fn() < end_ms:
                if not self.step():
                    sleep(1.0)
        finally:
            self.checkpoint()


def decision_digest(root: str | os.PathLike, bot_id: str, kinds=("entry", "exit")) -> str:
    rows = [r for r in DecisionLog(root).rows(bot_id) if r.get("kind") in kinds]
    return hashlib.md5(canon(rows).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FORGE DSL lane decision host (keyless, paper decisions only).")
    ap.add_argument("mode", choices=("register", "run", "replay", "verify"))
    ap.add_argument("--config", required=True)
    ap.add_argument("--since-hour", default=None, help="REQUIRED for run/replay: first tape hour read (history / first-seen warm-up); nothing older is opened. Use at least 7 h before the start.")
    ap.add_argument("--from-ms", type=int, default=None, help="replay: entries before this instant are shadow (no rows)")
    ap.add_argument("--to-ms", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        d = load_cfg(a.config)
        if a.mode == "verify":
            head, seq = DecisionLog(d["root"]).verify(d["bot_id"])
            print(canon({"head": head, "seq": seq, "genesis": head == GENESIS}))
            return 0
        if a.mode == "register":
            reg = register(d)
            print(canon({"registered": d["bot_id"], "spec_sha256": reg["spec_sha256"]}))
            return 0
        check_registry(d)
        if not a.since_hour:
            raise R.HostError("--since-hour is required")
        live = a.mode == "run"
        if live:
            host = Host(d, binding=run_binding(), live=True, since_ms=parse_hour(a.since_hour))
            print(canon({"started": d["bot_id"], "down_spans": len(host.down)}), flush=True)
            host.run_forever(int(d.get("end_ms") or 2 ** 62))
        else:
            if a.from_ms is None or a.to_ms is None:
                raise R.HostError("replay needs --from-ms and --to-ms")
            host = Host(d, binding=None, live=False, since_ms=parse_hour(a.since_hour), live_from_ms=a.from_ms)
            while host.step(a.to_ms):
                pass
            host.checkpoint()
            print(canon({"done": host.engine.counters, "decision_md5": decision_digest(d["root"], d["bot_id"])}), flush=True)
        return 0
    except (R.HostError, R.HostFatal, LogChainError, E.SpecError) as e:
        print(f"dsl_lane_host: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
