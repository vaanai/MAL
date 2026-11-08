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
  entry  {order_id, pool, meta{rule, mint, venue, t_event_ms, t_fill_ms, features, tp, sl, trail, max_hold_s, spec_sha256}, decision_slot, stake_sol, attempt_key}
  exit   {order_id, pool, reason in (tp, sl, trail, time, migrated, restart_close), decision_slot, attempt, grid_ms, exec_ms}     exec_ms = the evaluator's exit time
  entry_refused_gap  evidence, not an order (the executor ignores unknown kinds)
Row `ms` is the host clock (the arrival time of the tape row that made the decision), so the chain is time-ordered; exit rows carry grid_ms (the trigger grid point) and exec_ms.

SAFETY (as tools/floor_lane_host.py)
  * GAPS fail closed. An entry is dropped (and `entry_refused_gap` logged) when a data-loss gap on its venue, or a host_down span of this host, overlaps [t_event - gap_lookback_s,
    host clock at the fill]. The default lookback is the longest feature window the spec uses (min 60 s, the evaluator's own exclusion, which is applied silently first). A dropped
    entry is still tracked as a SHADOW position (no rows), so cooldown / one_per_pool state stays what the evaluator's would be.
  * `run` refuses a dirty or unknown checkout and records the code's identity (HEAD sha as frozen_sha, host file sha256, forge file sha256s, spec sha256) in the start row. The
    registry written by `register` pins the spec and file hashes; `run` refuses on any mismatch.
  * Restart: a decision log without its checkpoint is refused; downtime is recorded as a host_down span; every entry still open in the log is closed with an explicit `exit` row,
    reason restart_close (the previous process's exit tracking is gone), which the executor must follow by selling. A checkpoint is written right after the start row.
  * Row `ms` is monotone within the chain (max of the host clock and the previous row), so the per-UTC-day files keep the hash chain through midnight; the event / fill instants
    are in meta (t_event_ms, t_fill_ms). The entry row is written when the FILL print is seen (>= 2.0 s after the event), not at the event.
  * Nothing is written inside /data/mal/arena. No state grows without bound: pool history, thinning and first-seen state are pruned once per UTC hour for quiet pools; gap,
    completion and cooldown records are pruned by age.

ENTRIES vs THE EVALUATOR. This is NOT a subset relation (QP #1277). The host applies the evaluator's own filters before cooldown / one_per_pool where the evaluator does: an event
with no fill print within 10 min, an invalid fill state, or a fill-state real quote below 2 SOL is dropped (counter events_no_usable_fill), because the entry is decided at the fill.
Remaining sources of a host entry the evaluator would not have, each with its direction:
  1. A gap row that reaches the tape AFTER the event (or after a grid point): the 60 s exclusion and the lookback refusal see only gaps already read. Extra entries; the evaluator
     would have excluded the event, so live results can differ from the backtest on those trades (direction unknown, stale data). Lookback > 60 s narrows it.
  2. The evaluator drops a trade whose latest possible exit lies past the split end; the host has no split end. Not an extra entry in live use.
  3. Pools older than the warm-up: age_s / mig_age_s differ from the batch (the host's first-seen is its own tape), so specs using them are REFUSED, not approximated.
  4. Rows that arrive late (t < the newest time seen, e.g. the previous hour's tail) update pool history only: they are never an event, fill or grid step. Entries and exits can
     differ from a batch that sorts them in.
  5. An event whose pool has an exit decided later than its exec instant: the host blocks events in (exec, decision], so the host book is smaller there, never larger.
  A fill-less entry can no longer occur; before this fix (no_fill entries) live results would have been worse than the backtest.
Live vs backtest, forced exit: after a host restart every entry still open in the log is closed early with reason restart_close (the previous process's exit tracking is gone). The
evaluator has no such exit, so a live position can end before its tp/sl/trail/time exit; the backtest never prices it.
Also differs: when a time exit and a migration fall on the same grid point the evaluator labels it migrated and the host time (same exec instant).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import argparse
import bisect
import collections
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
PIN_FILES = ("tools/forge/events.py", "tools/forge/evaluate.py", "tools/forge/common.py", "tools/arena_stack.py", "tools/arena_pool_table.py", "tools/arena_declog.py", "tools/arena_rules.py",
             "tools/arena_paper.py", "tools/forge/dsl_lane_host.py")
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
    """Reads tape-<hour>.jsonl[.zst] files of an arena-layout dir from a start hour on, in order, returning only complete new lines.

    Like tools/arena_state.py's follower it keeps the TWO NEWEST hours active on every poll (the tip writer keeps hour H open for ~120 s after H+1 exists, so rows of H still
    arrive), and reads older hours once, one hour per refill (bounded memory). Per hour it keeps (lines consumed, byte offset), valid across the writer's .jsonl -> .jsonl.zst
    switch. A file deleted between the exists check and the open (compaction) is retried with the other name and never raises. An hour with no file while a later one exists is
    injected once as a data-loss gap on BOTH venues (a `pumpswap` line and a `pump_bonding` line)."""

    def __init__(self, tape_dir: str | os.PathLike, start_hour_ms: int) -> None:
        self.dir = Path(tape_dir)
        self.cursor = int(start_hour_ms)
        self.st: dict[int, list[int]] = {}
        self.missing: set[int] = set()
        self.pending: collections.deque = collections.deque()

    def _hours(self) -> set[int]:
        hs: set[int] = set()
        try:
            names = os.listdir(self.dir)
        except OSError:
            return hs
        for nm in names:
            if nm.startswith("tape-") and (nm.endswith(".jsonl") or nm.endswith(".jsonl.zst")):
                try:
                    hs.add(parse_hour(nm.split(".")[0][5:]))
                except R.HostError:
                    pass
        return hs

    def _read_hour(self, h: int) -> list[bytes]:
        st = self.st.setdefault(h, [0, 0, 0])  # lines consumed, byte offset, 1 once the compacted .zst has been read (it is final)
        if st[2]:
            return []
        hn = C.hour_str(h)
        plain, z = self.dir / f"tape-{hn}.jsonl", self.dir / f"tape-{hn}.jsonl.zst"
        for _ in range(2):
            try:
                if plain.exists():
                    with open(plain, "rb") as fh:
                        fh.seek(st[1])
                        data = fh.read()
                    cut = data.rfind(b"\n") + 1
                    new = data[:cut].splitlines(keepends=True)
                    st[1] += cut
                    st[0] += len(new)
                    return new
                if z.exists():
                    lines = list(_lines(z))
                    new = lines[st[0]:]
                    st[0] = max(st[0], len(lines))
                    st[2] = 1  # a .zst is the closed hour: read its remainder past our offset once, never decompress it again
                    return new
                return []
            except (FileNotFoundError, RuntimeError):  # the .jsonl was compacted away between exists and open: try the other name
                continue
        return []

    def _refill(self) -> bool:
        avail = self._hours()
        later = [h for h in avail if h >= self.cursor]
        if not later:
            return False
        latest = max(later)
        n0 = len(self.pending)
        if self.cursor < latest - HOUR_MS:  # catch-up: this hour is final (two newer hours exist); one hour per refill
            hrs, final = [self.cursor], True
        else:
            hrs, final = [h for h in (latest - HOUR_MS, latest) if h >= self.cursor], False
        for h in hrs:
            if h in avail:
                self.pending.extend(self._read_hour(h))
            elif h < latest and h not in self.missing:
                self.missing.add(h)
                for prog in ("pumpswap", "pump_bonding"):
                    self.pending.append(canon({"type": "gap", "prog": prog, "data_loss": True, "kind": "missing_hour", "from_ms": h, "to_ms": h + HOUR_MS}).encode() + b"\n")
        if final:
            self.st.pop(self.cursor, None)
            self.cursor += HOUR_MS
            return True
        self.cursor = max(self.cursor, latest - HOUR_MS)
        for h in [h for h in self.st if h < self.cursor]:
            del self.st[h]
        return len(self.pending) > n0

    def read(self, limit: int = 50_000) -> list[bytes]:
        while not self.pending:
            if not self._refill():
                break
        out = []
        while self.pending and len(out) < limit:
            out.append(self.pending.popleft())
        return out


# ------------------------------------------------------------------------------------------------------------------------------ the engine
class Pos:
    __slots__ = ("pid", "pool", "mint", "pool_h", "venue", "mint_h", "t_ev", "hs_ev", "slot", "shadow", "stage", "t_fill", "spot_fill", "P", "j", "carry", "exec_ms", "reason", "meta")

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


class Cand:
    """A condition-passing event waiting for its fill print (the evaluator's table only holds events that have a usable fill)."""
    __slots__ = ("pool", "mint", "pool_h", "mint_h", "venue", "t_ev", "hs_ev", "slot", "feats")

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
        if any(c["f"] in ("age_s", "mig_age_s") for c in self.spec["entry"]):
            # the batch computes these from the first print over the WHOLE build tape; a live host has only the tape it read, so for a pool older than its warm-up they differ
            # (the host's age is smaller). Refused rather than approximated (QP #1277 B1).
            raise R.HostError("specs that use age_s / mig_age_s are refused: first-seen instants of pools older than the warm-up are unknown to the host")
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
        self.cands: list[Cand] = []
        self._prune_h = -1
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
        late = t < self.clock  # a row that arrives after later ones (the previous hour's tail): history only, never an event, fill or clock step
        self.clock = max(self.clock, t)
        pool_h, mint_h = int(r["pool"]), int(r["mint"])
        if not late:
            self._prune()
            self._sweep()
            self._fills(r, pool_h)
        else:
            self._c("late_rows")
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
        if not late:
            self._event(r, ln, pool_h, mint_h, ph, ix)

    def _prune(self) -> None:
        """Once per UTC hour: drop state of pools quiet for more than the feature history and with nothing pending, and old gap / completion / cooldown records."""
        hidx = self.clock // HOUR_MS
        if hidx == self._prune_h:
            return
        self._prune_h = hidx
        cut = hidx * HOUR_MS - HIST_H * HOUR_MS
        busy = {p.pool_h for p in self.pos} | {c.pool_h for c in self.cands}
        for ph_, h in list(self.hist.items()):
            if ph_ in busy or (h.cols["t"] and h.cols["t"][-1] >= cut):
                continue
            del self.hist[ph_]
            self.thin.pop(ph_, None)
            self.first_pool.pop(ph_, None)
        cd = self.spec["cooldown_s"] * 1000
        for k in [k for k, v in self.cool_last.items() if self.clock - v >= cd + NO_FILL_MS]:
            del self.cool_last[k]
        old = self.clock - 50 * HOUR_MS
        for k in [k for k, v in self.comp.items() if v[-1] < old]:
            del self.comp[k]
        for v in (0, 1):
            self.gaps_v[v] = [g for g in self.gaps_v[v] if g[1] >= old]
        self.gaps_all = [g for g in self.gaps_all if g["to_ms"] >= old]
        live_m = {int(h.cols["mint"][-1]) for h in self.hist.values() if h.cols["mint"]}
        for k in [k for k in self.first_mint if k not in live_m]:
            del self.first_mint[k]

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
        X = self._features(ph, ix, hs, pool_h, mint_h, venue)
        if self.feat_hook is not None:
            self.feat_hook(pool_h, t, X[0].copy())
        for c in self.spec["entry"]:
            if not bool(E.OPS[c["op"]](X[0, C.FIDX[c["f"]]], np.float32(c["v"]))):
                return
        self._c("candidates")
        try:
            raw = json.loads(ln)
        except ValueError:
            return
        slot = raw.get("slot")
        slot = int(slot) if isinstance(slot, int) and not isinstance(slot, bool) else 0
        feats = {f: (None if not np.isfinite(X[0, i]) else float(X[0, i])) for f, i in C.FIDX.items()}
        self.cands.append(Cand(pool=str(raw.get("pool") if venue == 1 else raw.get("mint")), mint=str(raw.get("mint")), pool_h=pool_h, mint_h=mint_h, venue=venue, t_ev=t, hs_ev=hs,
                               slot=slot, feats=feats))
        self.next_due = min(self.next_due, t + NO_FILL_MS)

    def _features(self, ph: PoolHist, ix: int, hs: int, pool_h: int, mint_h: int, venue: int) -> np.ndarray:
        times = ph.cols["t"]
        lo = bisect.bisect_left(times, hs - HIST_H * HOUR_MS)
        S = {k: np.asarray(ph.cols[k][lo:], dtype=_DT[k]) for k in EV.ING_COLS}
        n = len(S["t"])
        key = S["t"] - C.T0_MS
        rank = np.zeros(n, np.int64)
        fs = {"mint": (np.array([mint_h], np.int64), np.array([self.first_mint[mint_h]], np.int64)), "pool": (np.array([pool_h], np.int64), np.array([self.first_pool[pool_h]], np.int64))}
        return EV.compute_features(S, key, rank, np.zeros(n, np.int64), np.array([ix - lo], np.int64), np.array([venue], np.int8), fs)

    # -- entries: decided when the fill print is seen, so the evaluator's own look-ahead filters (a fill exists, fill-state real quote) are applied BEFORE cooldown / one_per_pool
    def _gap_refusal(self, lo: int, t: int, venue: int) -> dict | None:
        for g in self.gaps_all:
            if g["to_ms"] < lo or g["from_ms"] > t:
                continue
            prog = g.get("prog", "all")
            hit = g.get("kind") == "host_down" or prog == "all" or (venue == 1) == (prog == "pumpswap")
            if hit:
                return {k: g.get(k) for k in ("prog", "kind", "from_ms", "to_ms")}
        return None

    def _resolve(self, c: Cand, t_fill: int, Qf: float, Bf: float, spot_row: float) -> None:
        t = c.t_ev
        cd = self.spec["cooldown_s"] * 1000
        if cd > 0:
            last = self.cool_last.get(c.pool_h)
            if last is not None and t - last < cd:
                return
            self.cool_last[c.pool_h] = t
        if self.spec["one_per_pool"] and any(p.pool_h == c.pool_h and (p.stage != "done" or p.exec_ms > t) for p in self.pos):
            self._c("one_per_pool_blocked")
            return
        ex = self.spec["exit"]
        meta = {"rule": "dsl", "mint": c.mint, "venue": c.venue, "t_event_ms": t, "t_fill_ms": t_fill, "features": c.feats, "tp": ex["tp"], "sl": ex["sl"], "trail": ex["trail"],
                "max_hold_s": ex["max_hold_s"]}
        shadow = t < self.live_from_ms
        refuse = None if shadow else self._gap_refusal(t - self.lookback_ms, self.clock, c.venue)
        p = Pos(pid=position_id("dsl", c.pool, t, c.slot), pool=c.pool, mint=c.mint, pool_h=c.pool_h, venue=c.venue, mint_h=c.mint_h, t_ev=t, hs_ev=c.hs_ev, slot=c.slot,
                shadow=shadow or refuse is not None, stage="run", t_fill=t_fill, spot_fill=Qf / Bf, carry=spot_row, P=np.full(C.NG, C.C_CENS, np.uint16), j=0, meta=meta)
        self.pos.append(p)
        self.next_due = min(self.next_due, t_fill + int(C.GRID_S[0]) * 1000)
        if shadow:
            self._c("shadow_entries")
        elif refuse is not None:
            self._c("entries_refused_gap")
            self.sink.refused(self.clock, {"kind": KIND_GAP_REFUSED, "reason": "feed_gap", "pool": c.pool, "mint": c.mint, "trigger_slot": c.slot, "trigger_ms": t, "gap": refuse})
        else:
            self._c("entries")
            self.sink.entry(self.clock, {"kind": "entry", "order_id": p.pid, "pool": c.pool, "meta": meta, "decision_slot": c.slot, "stake_sol": self.spec["stake_sol"],
                                         "attempt_key": f"dsl:{c.pool}:{t}"})

    def _fills(self, r: dict, pool_h: int) -> None:
        t = int(r["t"])
        keep = []
        for c in self.cands:
            if c.pool_h != pool_h or t < c.t_ev + C.FILL_DELAY_MS:
                keep.append(c)
                continue
            if t - c.t_ev <= NO_FILL_MS:
                ph = self.hist.get(pool_h)
                if c.venue == 1:
                    Qf, Bf, rq = float(r["q"]), float(r["b"]), float(r["rq"])
                    ok = Qf >= C.V_LO and Bf > 0
                elif ph is not None and ph.cols["t"]:
                    Qf, Bf, rq = float(ph.cols["q"][-1]), float(ph.cols["b"][-1]), float(ph.cols["rq"][-1])  # the previous print's post state
                    ok = Qf > 0 and Bf > 0
                else:
                    ok = False
                if ok and rq >= C.MIN_REAL_QUOTE:
                    self._resolve(c, t, Qf, Bf, float(r["spot"]))
                    continue
            self._c("events_no_usable_fill")  # no fill within 10 min, invalid fill state, or fill-state real quote below the floor: the evaluator never has this event
        self.cands = keep

    # -- exits: grid points are finalized once the stream clock has passed them
    def _sweep(self) -> None:
        if self.clock <= self.next_due:
            return
        nd = 1 << 62
        for p in self.pos:
            if p.stage == "run":
                while p.stage == "run" and p.j < C.NG and p.t_fill + int(C.GRID_S[p.j]) * 1000 < self.clock:
                    self._point(p)
                if p.stage == "run":
                    nd = min(nd, p.t_fill + int(C.GRID_S[p.j]) * 1000)
        for c in list(self.cands):
            if self.clock - c.t_ev > NO_FILL_MS:
                self.cands.remove(c)
                self._c("events_no_usable_fill")
            else:
                nd = min(nd, c.t_ev + NO_FILL_MS)
        self.next_due = nd
        self.pos = [p for p in self.pos if not (p.stage == "done" and p.exec_ms <= self.clock - NO_FILL_MS)]  # a pending candidate (t_ev >= clock - 10 min) may still need to see it

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
        kx, _r, reason, _cens = E.simulate_paths(p.P[None, :], self.spec["exit"])
        kx, reason = int(kx[0]), int(reason[0])  # the censored flag is entry-only in evaluate.py (a spec property), so it is not used here
        # On the known prefix (later points are CENS, i.e. invalid): a trigger or a migration is final at once; a time exit is final when its grid point is known.
        if not (reason in (1, 2, 3, 4) or (reason == 0 and kx == j)):
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
        rows = log.rows(bot_id)
        self.last_ms = int(rows[-1]["ms"]) if rows else 0

    def _w(self, ms: int, body: dict) -> None:
        """Row `ms` is monotone within the chain (DecisionLog files rows by UTC day of ms, so a backwards ms at midnight would break verify): max(ms, previous row's ms)."""
        self.last_ms = max(self.last_ms, int(ms))
        try:
            self.log.append(self.bot_id, self.last_ms, body)
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
            self.down = [dict(s) for s in ck.get("down", []) if isinstance(s, dict)]
            hwm = int(ck["hwm_ms"])
            if self.live_from_ms > hwm:
                self.down.append({"prog": "all", "kind": "host_down", "from_ms": hwm, "to_ms": self.live_from_ms})
        elif files:
            raise R.HostError("a decision log exists but the checkpoint is missing: downtime is unknown; refusing to start (restore the checkpoint or use a new root)")
        if self.binding is not None:
            spec_sha = spec_hash(self.spec)
            self.sink._w(self.live_from_ms, {"kind": "start", "binding": dict(self.binding), "frozen_sha": self.binding["git_sha"], "spec_sha256": spec_sha,
                                             "spec": self.spec, "rules": [self.bot_id]})
            # RESTART RECOVERY. A position open in the log lost its exit tracking with the previous process (the replay rebuilds it only as a shadow position, which writes no rows).
            # Like FLOOR's resume rows, each is closed explicitly: an `exit` with reason restart_close, which the executor must follow by selling. Its pool stays held by the
            # shadow position the replay rebuilds, so no second entry opens there before the evaluator's own exit.
            for oid, row in open_in_log(self.log, self.bot_id).items():
                self.sink._w(self.live_from_ms, {"kind": "exit", "order_id": oid, "pool": row["pool"], "reason": "restart_close", "decision_slot": row.get("decision_slot", 0),
                                                 "attempt": 1, "grid_ms": None, "exec_ms": self.live_from_ms})
            self.checkpoint()

    def checkpoint(self) -> None:
        head, seq = self.log.head(self.bot_id)
        _atomic_write(self.ckpt_path, {"schema": CKPT_SCHEMA, "bot_id": self.bot_id, "spec_sha256": spec_hash(self.spec), "hwm_ms": int(max(getattr(getattr(self, "engine", None), "clock", 0), self.live_from_ms)),
                                       "log_head": head, "log_seq": seq, "down": self.down[-50:]})

    def step(self, to_ms: int | None = None) -> int:
        n = 0
        for ln in self.follower.read():
            if to_ms is not None and self._t_of(ln) is not None and self._t_of(ln) > to_ms:
                break
            self.engine.feed_line(ln)
            n += 1
            if self.engine.clock - self._last_ckpt >= CKPT_EVERY_MS:
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
