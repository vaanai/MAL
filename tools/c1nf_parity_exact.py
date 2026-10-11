#!/usr/bin/env python3
"""DEC-026 item 13 (parity task 3) and item 14(a): the shadow's replay with a state-mode switch, in a SEPARATE harness.

Why a separate file. QP-1010 part (a) P2 asks for two replays on the same inputs: run A in state mode `exact` (the batch's state: the PRE-trade
reserves of the pool's first print with slot >= SD, as #506's harness tools/c1nf_parity.py defines it) and run B in state mode `live` (prints
with slot < SD only; the fee-model post-trade estimate), the mode the canary runs. tools/c1nf_shadow.py has no exact switch, and changing it
would move the soak's pinned blob (c2dafcdc, MiScusi #658). This harness leaves tools/c1nf_shadow.py and tools/c1nf_features.py byte-unchanged:

  * `--state-mode live`  runs tools.c1nf_shadow.run_replay itself, with an engine built exactly as tools.c1nf_shadow.build_engine builds it
                         (same class, ledger_keep_days and print_window). Its picks are the shadow's replay picks by construction (tested).
  * `--state-mode exact`  runs the same run_replay, with the evaluation point of #506's harness (tools/c1nf_parity.py:225-230). That
                         harness evaluates every decision live at SD and, for a pool that prints again before the batch's horizon
                         (graduation day D + 2 days, 02:00Z; tools/c1nf_parity.end_hour_of), re-evaluates it when that next print
                         arrives, before the print is ingested, with next_state = the print's PRE-trade (quote_reserve, base_reserve).
                         Every other table is read at that moment too: the creator record (_passc: cr_known_out, cr_lmax6, cr_lend6,
                         cr_big6 read Q.outcome or Q.outcome_now() of the creator's other pools, which a print of Q between SD and P's
                         next print changes), the market minutes, the create, graduation and narrative lists. ExactShadow does the same:
                         at decision time it asks a look-ahead index (built from the same tape hours before the replay) whether the
                         pool has a print with slot >= SD before the horizon. If not (or that print's state is not a valid state), the
                         pool is decided at SD, live, as #506 keeps the live row ("no later print for the pool in the window -> exact
                         == live"). If so, the decision is DEFERRED: it runs, through the shadow's own Shadow._decide (model, PRED_MIN,
                         cap, seal, book, decision state, pick schema unchanged), when the pool's next PumpSwap row reaches _on_trade,
                         before Shadow._on_trade ingests it, with that row's (quote_reserve, base_reserve) as next_state.
                         The book stays the shadow's: a mint's deferred decisions resolve in T order, before any later decision of the
                         same pool, and keep their own T, SD and decision bt (book, pick t_ms). The pool's decision state (its last
                         print with slot < SD) is unchanged, since no print of the pool arrives in between. Exact mode prices no
                         outcome (a deferred Pending would see prints a live one never sees): no c1nf_outcome record is written.
                         Cross-harness equality on a synthetic creator fixture: tools/test_c1nf_parity_exact.py.

Not part of the evaluation point, and shared by run A and run B (the cross-harness check on 09-20 measures them): the shadow's SD (the
slot of the first row of a new minute) against #506's (the least trade slot with block_time >= T), the row order inside a slot (row_key
against #506's creates-trades-migrations order), and #506's hourly expire (pools freed at g0 + 49 h, outcome frozen) where replay never
expires.

Creator history (QP round 2 on 39a06b4, blocking item 1; `--history`, default `tape-start`). The frozen scorer counts creator history from tape
start: ARTIFACTS/exp025/scripts/10_meta.py writes every create of hunt-shared tokens.parquet (has_create) to creates.parquet, and 12_passC.py
builds by_cr, g_cr and m_cr from it with no segment filter (cr_prev_creates, cr_prev_grads, cr_known_out, cr_lmax6, cr_lend6, cr_big6). The
engine counts a creator only from its own first row, and the shadow's replay reads one contiguous run of hours, so a replay that starts at a
later segment's start drops that history. Convention: a run first feeds the HISTORY, every exploration-tape hour from tape start (TAPE_START,
2026-08-14T12) up to --replay-from, in order, gap hours skipped (the tape has no file for them), with decisions OFF: the effective decide_from
is --replay-from (or a later --decide-from), so no decision, stage-1 row, deferral, pick or book entry is made before it. Then it decides from
--replay-from, and the book starts there. S2 (09-03T12, 288 h) feeds 336 history hours first, S3 (09-18T23, 152 h) 624, so 776 tape hours in
all. It is still tools.c1nf_shadow.run_replay itself: run_replay_mode swaps cs.replay_hours for that one call so it returns the history hours
followed by the --replay-from hours (all exploration hours; refuse_replay checks every one), and passes the effective decide_from. The
look-ahead index is built from the --replay-from hours only (no decision is deferred before them, and a later print has a later slot).
Gap creates: hunt-shared tokens.parquet (sha256 2c01a6d4...) holds 0 has_create rows with create_ms in 08-28T12..09-03T12 or 09-15T12..09-18T23
(and 0 before 08-14T12 or at/after 09-25T07; 1,109,020 in the three segments; counted 2026-10-11 from create_ms and creator only), so skipping
the gap hours drops no create the frozen scorer counts. `--history none` is the run before this change (history from --replay-from); it is not
P3's convention. #506's harness (tools/c1nf_parity.py replay) streams the tape's hour files from --from-hour (default 2026-08-14T12), so it
skips the same gap hours.

Outputs in --out-dir (which must be empty or absent):
  c1nf-prewindow-picks-*.jsonl, c1nf-*.jsonl     the shadow's own sink (every replay pick is a pre-window pick)
  parity-stage1-<mode>.npz                       every stage-1 decision row with a wallet snapshot: pool, mint, T, sd, h_top1, vec (float32)
  parity-summary-<mode>.json                     mode, input sha256s and git blobs, look-ahead counters, engine health, picks per UTC day,
                                                 and P3's list md5 (lines `mint,decision_T_ms,SD_slot`, sorted, newline-terminated) per day
                                                 and overall, BEFORE tie removal (P3 recomputes it after removing boundary ties and
                                                 their book cascades unless both sides have 0 ties). It opens no outcome or P&L column.
                                                 `history` (convention, hours fed before --replay-from, their contiguous runs),
                                                 `decide_from_effective` and `hours_fed` record the creator-history feed.
                                                 Exact mode adds `exact`: deferred / resolved / unresolved decisions and row-vs-index
                                                 state mismatches; a run with an unresolved deferral or a mismatch exits 1.

  python tools/c1nf_parity_exact.py --state-mode exact --replay-from 2026-09-03T12 --replay-hours 288 \
      --model-manifest M.json --ledger-root ROOT --out-dir OUT_A
      (feeds S1's 336 hours as history, decisions off, then decides 09-03T12 .. 09-15T11)
  python tools/c1nf_parity_exact.py --state-mode exact --replay-from 2026-09-03T12 --replay-hours 288 --probe-lookahead --out-dir OUT_P
      (builds the look-ahead index only, prints its size and the process VmHWM, writes parity-lookahead-probe.json, and exits)
  python tools/c1nf_parity_exact.py cross-compare REPLAY506.npz OUT_A/parity-stage1-exact.npz [--skip-wallet] [--json J]
      (QP's 09-20 cross-harness check: #506's replay npz against the stage-1 vectors of a #594 exact run whose decisions cover all of #506's
      rows; exit 0 only if shared_rows > 0, rows_differing == 0, only_506 == 0 and only_594 == 0, reported with by_feature;
      only_594_outside_on_506_pools is reported and does not gate the exit code: GO-PLAN 2.4 requires
      it to be 0, or each (pool, T) listed and classified, before run A)

Exploration tape only: tools.c1nf_shadow.refuse_replay runs before any read (exploration hours, forbidden path tokens). No CAP-PICK oracle,
no seal (replay). Heavy runs are MiScusi jobs, memory-capped; this file does not decide the cap.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import c1nf_shadow as cs  # noqa: E402
from tools.c1nf_features import FeatureEngine, utc_day  # noqa: E402

STATE_LIVE, STATE_EXACT = "live", "exact"
STATE_MODES = (STATE_LIVE, STATE_EXACT)
HORIZON_DAYS, HORIZON_HOUR = 2, 2              # tools/c1nf_parity.end_hour_of: the batch loads D00 .. D+2 T01 for graduation day D
EXACT_LEDGER_KEEP_DAYS = 3                     # a deferred decision of day D can resolve on D+2 while T moves on: keep 3 as-of snapshots so the
                                               # engine does not reopen one per minute (FeatureEngine._ledger_for: a reopened day has the same values)
EXIT_EXACT_CHECK = 1                           # exact mode: an unresolved deferral or a row/index state mismatch
TAPE_START = cs.EXPLORATION[0][0]              # 2026-08-14T12: the frozen scorer's creator history starts here (10_meta.py, 12_passC.py)
HISTORY_TAPE_START, HISTORY_NONE = "tape-start", "none"
HISTORY_MODES = (HISTORY_TAPE_START, HISTORY_NONE)


def _hour_dt(h: str) -> datetime:
    return datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def is_exploration_hour(h: str) -> bool:
    return any(lo <= h < hi for lo, hi in cs.EXPLORATION)


def history_hours(replay_from: str, history: str = HISTORY_TAPE_START) -> list[str]:
    """The creator-history feed (module docstring): every exploration-tape hour in [TAPE_START, replay_from), in order, gap hours skipped.
    `none`: no history (the run starts cold at replay_from)."""
    if history not in HISTORY_MODES:
        raise ValueError(f"history must be one of {HISTORY_MODES}, got {history!r}")
    if history == HISTORY_NONE:
        return []
    n = int((_hour_dt(replay_from) - _hour_dt(TAPE_START)).total_seconds() // 3600)
    return [h for h in cs.replay_hours(TAPE_START, max(n, 0)) if is_exploration_hour(h)]


def hour_runs(hours: Sequence[str]) -> list[list[str]]:
    """[first, last] of each run of consecutive hours."""
    out: list[list[str]] = []
    prev: Optional[datetime] = None
    for h in hours:
        t = _hour_dt(h)
        if prev is not None and t - prev == timedelta(hours=1):
            out[-1][1] = h
        else:
            out.append([h, h])
        prev = t
    return out


class FeedPlan(NamedTuple):
    """What one run feeds: the history hours (decisions off), then the --replay-from hours (decisions from `decide_from`)."""
    history: str
    history_hours: list
    decide_hours: list
    decide_from: Optional[str]

    @property
    def hours(self) -> list[str]:
        return list(self.history_hours) + list(self.decide_hours)

    def report(self) -> dict:
        return {"convention": self.history, "tape_start": TAPE_START, "hours": len(self.history_hours),
                "runs": hour_runs(self.history_hours), "decide_from_effective": self.decide_from, "hours_fed": len(self.hours)}


def feed_plan(replay_from: str, replay_hours: int, history: str = HISTORY_TAPE_START, decide_from: Optional[str] = None) -> FeedPlan:
    """With a history, decisions start at replay_from (or at a later decide_from): none is made during the history feed."""
    decide = cs.replay_hours(replay_from, replay_hours)
    hist = history_hours(replay_from, history)
    eff = decide_from
    if hist and (decide_from is None or decide_from < replay_from):
        eff = replay_from
    return FeedPlan(history, hist, decide, eff)


def horizon_bt(g0: Optional[int]) -> Optional[int]:
    """First block_time past the batch's horizon for a pool that graduated at g0 (None: no horizon known, no cut)."""
    if g0 is None:
        return None
    d = datetime.strptime(utc_day(int(g0)), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int((d + timedelta(days=HORIZON_DAYS, hours=HORIZON_HOUR)).timestamp())


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class Lookahead:
    """Per canonical pool, its PumpSwap prints in (slot, tx_index, event_index) order: slot, block_time and the PRE-trade reserves the tape
    row carries (quote_reserve, base_reserve). next_state(pool, sd, horizon) = the first print with slot >= sd, or None."""

    def __init__(self, pool_v: Mapping[str, float]) -> None:
        self.pool_v = pool_v
        self._parts: dict[str, list[tuple[np.ndarray, ...]]] = {}
        self._idx: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        self.rows = 0
        self.c: dict[str, int] = {"asked": 0, "hit": 0, "no_pool": 0, "none_after_sd": 0, "past_horizon": 0, "invalid_reserves": 0}
        self._last_key: dict[str, tuple[int, int, int]] = {}

    def add_columns(self, venue: Sequence[Any], pool: Sequence[Any], slot: Sequence[Any], tx: Sequence[Any], ev: Sequence[Any],
                    bt: Sequence[Any], q: Sequence[Any], b: Sequence[Any]) -> int:
        """One tape hour (any row order; sorted here, stable, by (slot, tx_index or 0, event_index or 0) like row_key). Hours must come in
        order: a print that sorts before a pool's last added print raises (the index would no longer be the tape's order)."""
        keep = [i for i in range(len(venue)) if venue[i] == "pumpswap" and pool[i] in self.pool_v]
        if not keep:
            return 0
        k_slot = np.array([int(slot[i]) for i in keep], dtype=np.int64)
        k_tx = np.array([int(tx[i] or 0) if _num(tx[i]) is not None else 0 for i in keep], dtype=np.int64)
        k_ev = np.array([int(ev[i] or 0) if _num(ev[i]) is not None else 0 for i in keep], dtype=np.int64)
        order = np.lexsort((k_ev, k_tx, k_slot))                 # stable: equal keys keep tape order
        by_pool: dict[str, list[int]] = {}
        for j in order.tolist():
            by_pool.setdefault(pool[keep[j]], []).append(j)
        for p, js in by_pool.items():
            first = (int(k_slot[js[0]]), int(k_tx[js[0]]), int(k_ev[js[0]]))
            last = self._last_key.get(p)
            if last is not None and first < last:
                raise ValueError(f"{p}: rows added out of order ({first} after {last})")
            self._last_key[p] = (int(k_slot[js[-1]]), int(k_tx[js[-1]]), int(k_ev[js[-1]]))
            ii = [keep[j] for j in js]
            qq = np.array([_num(q[i]) if _num(q[i]) is not None else np.nan for i in ii], dtype=np.float64)
            bb = np.array([_num(b[i]) if _num(b[i]) is not None else np.nan for i in ii], dtype=np.float64)
            tt = np.array([int(bt[i]) if _num(bt[i]) is not None else -1 for i in ii], dtype=np.int64)
            self._parts.setdefault(p, []).append((k_slot[js], tt, qq, bb))
        self.rows += len(keep)
        return len(keep)

    def add_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        rows = [r for r in rows if r.get("_k", "trades") == "trades"]
        cols = ("venue", "pool", "slot", "tx_index", "event_index", "block_time", "quote_reserve", "base_reserve")
        return self.add_columns(*[[r.get(c) for r in rows] for c in cols])

    def freeze(self) -> "Lookahead":
        for p, parts in self._parts.items():
            self._idx[p] = tuple(np.concatenate([x[k] for x in parts]) for k in range(4))  # type: ignore[assignment]
        self._parts.clear()
        return self

    def nbytes(self) -> int:
        return int(sum(a.nbytes for v in self._idx.values() for a in v))

    def next_state(self, pool: str, sd: int, horizon: Optional[int] = None) -> Optional[tuple[float, float]]:
        self.c["asked"] += 1
        ix = self._idx.get(pool)
        if ix is None:
            self.c["no_pool"] += 1
            return None
        slots, bts, qs, bs = ix
        i = int(np.searchsorted(slots, int(sd), side="left"))
        if i >= len(slots):
            self.c["none_after_sd"] += 1
            return None
        if horizon is not None and bts[i] >= horizon:
            self.c["past_horizon"] += 1
            return None
        qv, bv = float(qs[i]), float(bs[i])
        if not (math.isfinite(qv) and math.isfinite(bv)):
            self.c["invalid_reserves"] += 1                 # the engine would mark the pool bad at that print; the decision stays live
            return None
        self.c["hit"] += 1
        return qv, bv


class StateModeEngine(FeatureEngine):
    """tools.c1nf_features.FeatureEngine with the shadow's build_engine settings, plus the state-mode switch and a stage-1 recorder.
    live: features_at and alive_pools are the base methods, unchanged. exact: ExactShadow sets `only_pools` (alive_pools restricted to a
    subset, in the base order) and `inject` ({pool: next_state} for the deferred decision it is resolving); nothing else differs, except
    that exact mode keeps EXACT_LEDGER_KEEP_DAYS as-of snapshots (the same values)."""

    def __init__(self, ledger: Any = None, *, v_source: str, state_mode: str, lookahead: Optional[Lookahead] = None, record: bool = True) -> None:
        if state_mode not in STATE_MODES:
            raise ValueError(f"state_mode must be one of {STATE_MODES}, got {state_mode!r}")
        if state_mode == STATE_EXACT and lookahead is None:
            raise ValueError("state_mode 'exact' needs a look-ahead index")
        if state_mode == STATE_EXACT and v_source != cs.V_SOURCE_REPLAY:
            raise ValueError("state_mode 'exact' is defined for the exploration replay (v_source 'const') only")
        keep = EXACT_LEDGER_KEEP_DAYS if state_mode == STATE_EXACT else cs.LIVE_LEDGER_KEEP_DAYS
        super().__init__(ledger=ledger, v_source=v_source, ledger_keep_days=keep, print_window=cs.LIVE_PRINT_WINDOW)
        self.state_mode, self.lookahead, self.record = state_mode, lookahead, record
        self.stage1_rows: list[tuple[str, Optional[str], int, int, float, np.ndarray]] = []
        self.exact_injected = 0
        self.only_pools: Optional[Sequence[str]] = None
        self.inject: Optional[dict[str, tuple[float, float]]] = None

    def alive_pools(self, T: int) -> list[str]:
        base = super().alive_pools(T)
        if self.only_pools is None:
            return base
        keep = set(self.only_pools)
        return [p for p in base if p in keep]

    def features_at(self, pool: str, T: int, sd: Optional[int] = None, next_state: Optional[tuple] = None) -> Any:
        if next_state is None and self.inject is not None:
            next_state = self.inject.get(pool)
            if next_state is not None:
                self.exact_injected += 1
        res = super().features_at(pool, T, sd, next_state=next_state) if next_state is not None else super().features_at(pool, T, sd)
        if self.record and res is not None and res.stage1 and res.wallet_ok is not False:
            self.stage1_rows.append((res.pool, getattr(res, "mint", None), int(res.t), int(res.sd), float(res.h_top1),
                                     np.asarray(res.vec, dtype=np.float32).copy()))
        return res


class ExactShadow(cs.Shadow):
    """Run A's evaluation point (module docstring): a decision whose pool prints again (slot >= SD, before the horizon, valid state) is
    deferred to that print and resolved through Shadow._decide itself, so the decision code is the shadow's, byte for byte."""

    def __init__(self, engine: Any, *a: Any, **kw: Any) -> None:
        if getattr(engine, "state_mode", None) != STATE_EXACT:
            raise ValueError("ExactShadow needs a StateModeEngine in state mode 'exact'")
        super().__init__(engine, *a, **kw)
        self.lookahead: Lookahead = engine.lookahead
        self.deferred: dict[str, list[tuple[int, int, int, tuple[float, float]]]] = {}
        self.xc: collections.Counter = collections.Counter()
        self._clock_ms_override: Optional[int] = None

    def clock_ms(self) -> int:
        return self._clock_ms_override if self._clock_ms_override is not None else super().clock_ms()

    def _decide(self, T: int, sd: int, bt: int) -> None:
        eng = self.engine
        now: list[str] = []
        for pool in eng.alive_pools(T):
            P = eng._pools.get(pool)
            ns = self.lookahead.next_state(pool, int(sd), horizon_bt(P.g0)) if P is not None else None
            if ns is not None and not (ns[0] + P.V > 0 and ns[1] > 0):   # features_at would answer None there; #506 keeps the live row
                self.xc["invalid_next_state_live"] += 1
                ns = None
            if ns is None:
                now.append(pool)
            else:
                self.deferred.setdefault(pool, []).append((int(T), int(sd), int(bt), ns))
                self.xc["deferred"] += 1
        self.xc["decided_at_sd"] += len(now)
        eng.only_pools = now
        try:
            super()._decide(T, sd, bt)
        finally:
            eng.only_pools = None

    def _on_trade(self, row: Mapping[str, Any], slot: int, bt: int) -> None:
        if row.get("venue") == "pumpswap":
            pool = row.get("pool")
            if pool is not None and self.deferred.get(pool):
                self._resolve_deferred(pool, row, slot)
        super()._on_trade(row, slot, bt)

    def _resolve_deferred(self, pool: str, row: Mapping[str, Any], slot: int) -> None:
        """#506 (tools/c1nf_parity.py:225-230): every pending decision of the pool, at its next PumpSwap print, before on_trade, with the
        row's own (quote_reserve, base_reserve). The row must be the print the look-ahead named (counted; the run exits 1 if not)."""
        keep: list[tuple[int, int, int, tuple[float, float]]] = []
        eng = self.engine
        for T, sd, bt, ns in self.deferred.pop(pool):
            if slot < sd:                                     # cannot happen in slot order; kept for the print it was deferred to
                keep.append((T, sd, bt, ns))
                continue
            q, b = _num(row.get("quote_reserve")), _num(row.get("base_reserve"))
            if q is None or b is None or (q, b) != (float(ns[0]), float(ns[1])):
                self.xc["row_state_mismatch"] += 1
                if q is None or b is None:
                    continue
            minutes = self.c["minutes"]
            eng.only_pools, eng.inject, self._clock_ms_override = [pool], {pool: (q, b)}, int(bt) * 1000
            try:
                super()._decide(T, sd, bt)                    # the base class bound at definition: during run_replay_mode the
                                                              # module-global cs.Shadow is the factory function, not the class
            finally:
                eng.only_pools, eng.inject, self._clock_ms_override = None, None, None
            self.c["minutes"] = minutes                       # the minute was counted once, at T
            self.xc["resolved"] += 1
        if keep:
            self.deferred[pool] = keep

    def _resolve(self, pend: Any, final: bool) -> None:
        pend.done = True                                      # exact mode prices no outcome (module docstring)
        self.xc["outcomes_not_priced"] += 1

    def finish(self, reason: str = "end") -> None:
        self.xc["unresolved"] = sum(len(v) for v in self.deferred.values())
        super().finish(reason)

    def exact_report(self) -> dict:
        r: dict[str, Any] = {k: int(self.xc.get(k, 0)) for k in ("deferred", "resolved", "unresolved", "decided_at_sd", "invalid_next_state_live",
                                                                "row_state_mismatch", "outcomes_not_priced")}
        r["ok"] = r["unresolved"] == 0 and r["row_state_mismatch"] == 0 and r["deferred"] == r["resolved"]
        return r


def run_replay_mode(args: argparse.Namespace, models: Any, engine: StateModeEngine, sink: Any,
                    plan: Optional[FeedPlan] = None) -> tuple[int, Optional[ExactShadow]]:
    """tools.c1nf_shadow.run_replay for both modes (the same tape rows, Shadow arguments and finish). Exact mode builds ExactShadow in place
    of Shadow for this one call (run_replay constructs `Shadow` by its module-global name); live mode is cs.run_replay, untouched.
    With a plan (main always passes one), the same call feeds plan.hours (the history, then the --replay-from hours) with decide_from =
    plan.decide_from: run_replay asks `replay_hours(args.replay_from, args.replay_hours)` by its module-global name, which is swapped for this
    one call (any other question raises) and restored after, as Shadow is."""
    run_args = args
    orig_rh = cs.replay_hours
    if plan is not None:
        run_args = argparse.Namespace(**vars(args))
        run_args.decide_from = plan.decide_from
        want, fed = (args.replay_from, int(args.replay_hours)), plan.hours

        def plan_hours(first: str, n: int) -> list[str]:
            if (first, int(n)) != want:
                raise RuntimeError(f"replay_hours({first!r}, {n}) is not this run's ({want})")
            return list(fed)

        cs.replay_hours = plan_hours  # type: ignore[assignment]
    try:
        if engine.state_mode != STATE_EXACT:
            return cs.run_replay(run_args, models, engine, sink), None
        made: list[ExactShadow] = []
        orig = cs.Shadow

        def factory(*a: Any, **kw: Any) -> ExactShadow:
            sh = ExactShadow(*a, **kw)
            made.append(sh)
            return sh

        cs.Shadow = factory  # type: ignore[assignment,misc]
        try:
            rc = cs.run_replay(run_args, models, engine, sink)
        finally:
            cs.Shadow = orig  # type: ignore[misc]
        if len(made) != 1:
            raise RuntimeError(f"run_replay built {len(made)} shadows, expected 1")
        return rc, made[0]
    finally:
        cs.replay_hours = orig_rh  # type: ignore[assignment]


# ---- P3's list --------------------------------------------------------------------------------------------------------------------------
def p3_line(p: Mapping[str, Any]) -> str:
    return f"{p['mint']},{int(p['decision_T_ms'])},{int(p['SD_slot'])}"


def p3_md5(picks: Iterable[Mapping[str, Any]]) -> dict:
    """QP-1010 P3: L = lines mint,decision_T_ms,SD_slot (decimal), sorted, newline-terminated; md5 per UTC day of decision_T and overall."""
    lines = sorted(p3_line(p) for p in picks)
    days: dict[str, list[str]] = {}
    for ln in lines:
        days.setdefault(utc_day(int(ln.split(",")[1]) // 1000), []).append(ln)
    md5 = lambda ls: hashlib.md5("".join(x + "\n" for x in ls).encode()).hexdigest()  # noqa: E731
    return {"n": len(lines), "md5": md5(lines), "days": {d: {"n": len(v), "md5": md5(v)} for d, v in sorted(days.items())}}


def read_picks(out_dir: str | Path) -> list[dict]:
    out = []
    for f in sorted(Path(out_dir).glob("c1nf-*picks-*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("type") == "c1nf_pick":
                    out.append(r)
    return out


# ---- the cross-harness check (QP on ab1df5f: before run A) -------------------------------------------------------------------------------
WALLET_PREFIXES = ("wb5_", "ws5_", "wb15_", "ws15_", "sk_net")


def wallet_feature(name: str) -> bool:
    return name.startswith(WALLET_PREFIXES)


def cross_compare(replay506: Mapping[str, Any], stage1_594: Mapping[str, Any], *, skip_wallet: bool = False) -> dict:
    """#506's `replay --day D` npz (exact_pool, exact_T, exact_stage1, exact_vec) against this harness's parity-stage1-exact.npz (pool, T,
    vec float32): float32 vectors on the shared stage-1 (pool, T) rows, NaN equal to NaN, the differing-row count per feature name, and the
    two stage-1 MEMBERSHIP counts (QP on 626973f: stage-1 membership is a float32 function of v5, surge, new5 and qreal, which are pool-state
    features read at the next print, so a state difference can move a row out of stage 1 on one side and out of the shared set):
      only_506  #506 stage-1 rows that are not #594 stage-1 rows;
      only_594  #594 stage-1 rows whose (pool, T) is one of #506's decision keys (exact_pool/exact_T, any stage-1 flag) but is not a #506
                stage-1 row. #594 rows outside #506's key set (the other graduation days a continuous run also decides) are counted in
                only_594_outside_506_keys and do not gate; only_594_outside_on_506_pools is the part of those on #506's own pools.
    `pass` (the exit-0 rule) = shared_rows > 0 and rows_differing == 0 and only_506 == 0 and only_594 == 0 and no duplicate (pool, T) key on
    either side. skip_wallet: compare only the 65 non-wallet columns (when the two runs did not read the same as-of ledger). Opens no
    outcome."""
    from tools.c1nf_features import FEATURE_NAMES

    keys506 = [(str(p), int(t)) for p, t in zip(replay506["exact_pool"], replay506["exact_T"])]
    s1_506 = [bool(x) for x in replay506["exact_stage1"]]
    keys594 = [(str(p), int(t)) for p, t in zip(stage1_594["pool"], stage1_594["T"])]
    all506 = set(keys506)
    a = {k: i for i, (k, s1) in enumerate(zip(keys506, s1_506)) if s1}
    b = {k: i for i, k in enumerate(keys594)}
    shared = sorted(set(a) & set(b))
    in_506_keys = {k for k in b if k in all506}
    outside = set(b) - all506
    pools506 = {k[0] for k in all506}
    cols = [j for j, n in enumerate(FEATURE_NAMES) if not (skip_wallet and wallet_feature(n))]
    A = np.asarray(replay506["exact_vec"], dtype=np.float64)[[a[k] for k in shared]][:, cols].astype(np.float32) if shared else np.zeros((0, len(cols)), np.float32)
    B = np.asarray(stage1_594["vec"], dtype=np.float32)[[b[k] for k in shared]][:, cols] if shared else np.zeros((0, len(cols)), np.float32)
    diff = ~((A == B) | (np.isnan(A) & np.isnan(B)))
    by = {FEATURE_NAMES[j]: int(diff[:, i].sum()) for i, j in enumerate(cols)}
    out = {"shared_rows": len(shared), "stage1_506": len(a), "stage1_594": len(b), "decision_keys_506": len(all506),
           "only_506": len(set(a) - set(b)), "only_594": len(in_506_keys - set(a)),
           "only_594_outside_506_keys": len(outside), "only_594_outside_on_506_pools": sum(1 for k in outside if k[0] in pools506),
           "duplicate_keys_506": len(keys506) - len(all506), "duplicate_keys_594": len(keys594) - len(b),
           "columns": len(cols), "rows_differing": int(diff.any(axis=1).sum()), "by_feature": {n: c for n, c in by.items() if c}}
    out["pass"] = bool(out["shared_rows"] > 0 and out["rows_differing"] == 0 and out["only_506"] == 0 and out["only_594"] == 0
                       and out["duplicate_keys_506"] == 0 and out["duplicate_keys_594"] == 0)
    return out


# ---- inputs -----------------------------------------------------------------------------------------------------------------------------
def pool_v_from_tokens(tokens: str) -> dict[str, float]:
    """The same pool -> V0 map tools.c1nf_shadow.run_replay builds (V band applied)."""
    import pandas as pd  # lazy: the audit venv

    tk = pd.read_parquet(tokens, columns=["mint", "pool", "v0_lamports"])
    tk = tk[(tk.v0_lamports >= cs.V_LO) & (tk.v0_lamports <= cs.V_HI)]
    return dict(zip(tk.pool, tk.v0_lamports.astype(float)))


def lookahead_from_tape(tape_dir: str, hours: Sequence[str], pool_v: Mapping[str, float]) -> Lookahead:
    import pandas as pd  # lazy: the audit venv

    la = Lookahead(pool_v)
    cols = ["venue", "pool", "slot", "tx_index", "event_index", "block_time", "quote_reserve", "base_reserve"]
    for h in hours:
        t = pd.read_parquet(f"{tape_dir}/trades/{h}.parquet", columns=cols)
        t = t[(t.venue == "pumpswap") & t.pool.isin(pool_v.keys())]
        la.add_columns(*[t[c].astype(object).where(t[c].notna(), None).tolist() for c in cols])
    return la.freeze()


def sha256_file(path: str | Path) -> str:
    return cs.sha256_file(path)


def git_blob(path: str | Path) -> Optional[str]:
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def vm_hwm_kb() -> Optional[int]:
    return cs.proc_mem_kb().get("VmHWM")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="C1-NF parity replay with a state-mode switch (DEC-026 items 13 and 14a). Exploration tape only.")
    p.add_argument("--state-mode", required=True, choices=STATE_MODES)
    p.add_argument("--replay-from", required=True, help="first exploration-tape hour YYYY-MM-DDTHH (the segment start)")
    p.add_argument("--replay-hours", type=int, required=True)
    p.add_argument("--replay-tape", default=cs.TAPE_DIR)
    p.add_argument("--tokens", default=cs.TOKENS_PARQUET)
    p.add_argument("--model", help="LightGBM model file (with --model-sha256)")
    p.add_argument("--model-sha256")
    p.add_argument("--model-manifest", help='JSON {"models":[{"from_day","file","sha256"}]} (item 13: 21 walk-forward files)')
    p.add_argument("--ledger-root", help="tools/c1nf_wallet_ledger.py output root (asof/asof-<day>)")
    p.add_argument("--decide-from", help="decisions from this hour; with a history, never before --replay-from")
    p.add_argument("--decide-to")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--probe-lookahead", action="store_true", help="build the look-ahead index only, report its size and VmHWM, and exit")
    p.add_argument("--history", choices=HISTORY_MODES, default=HISTORY_TAPE_START,
                   help="tape-start (default, P3's convention): feed every exploration hour from 2026-08-14T12 to --replay-from first, gap "
                        "hours skipped, decisions off; none: start cold at --replay-from")
    return p


def main_cross(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(prog="c1nf_parity_exact.py cross-compare")
    p.add_argument("replay506", help="tools/c1nf_parity.py replay --day D output (npz)")
    p.add_argument("stage1_594", help="this harness's parity-stage1-exact.npz")
    p.add_argument("--skip-wallet", action="store_true", help="the 65 non-wallet columns only")
    p.add_argument("--json")
    a = p.parse_args(argv)
    out = cross_compare(np.load(a.replay506, allow_pickle=False), np.load(a.stage1_594, allow_pickle=False), skip_wallet=a.skip_wallet)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, sort_keys=True))
    return cs.EXIT_OK if out["pass"] else EXIT_EXACT_CHECK


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["cross-compare"]:
        return main_cross(argv[1:])
    args = build_parser().parse_args(argv)
    plan = feed_plan(args.replay_from, args.replay_hours, args.history, args.decide_from)
    hours = plan.decide_hours                                 # the look-ahead index reads these only
    try:
        cs.refuse_replay([args.replay_tape, args.out_dir, args.tokens] + [x for x in (args.model, args.model_manifest, args.ledger_root) if x],
                         plan.hours)
    except cs.Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return cs.EXIT_REFUSED
    out = Path(args.out_dir)
    if out.exists() and any(out.iterdir()):
        print(f"refusing: {out} is not empty (one run per out dir; the first complete run is the one reported)", file=sys.stderr)
        return cs.EXIT_REFUSED
    if not args.probe_lookahead and not (args.model_manifest or (args.model and args.model_sha256)):
        print("refusing: --model with --model-sha256, or --model-manifest, is required", file=sys.stderr)
        return cs.EXIT_USAGE
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    la = None
    if args.state_mode == STATE_EXACT or args.probe_lookahead:
        la = lookahead_from_tape(args.replay_tape, hours, pool_v_from_tokens(args.tokens))
        probe = {"rows": la.rows, "pools": len(la._idx), "index_bytes": la.nbytes(), "seconds": round(time.time() - t0, 1), "VmHWM_kb": vm_hwm_kb(),
                 "hours": [hours[0], hours[-1]], "history": plan.report()}
        print("lookahead " + json.dumps(probe), flush=True)
        if args.probe_lookahead:
            (out / "parity-lookahead-probe.json").write_text(json.dumps(probe, indent=1) + "\n")
            return cs.EXIT_OK
    models = cs.ModelSet.from_manifest(args.model_manifest) if args.model_manifest else cs.ModelSet.single(args.model, args.model_sha256)
    ledger = cs.AsofDirLedger(args.ledger_root) if args.ledger_root else None
    engine = StateModeEngine(ledger, v_source=cs.V_SOURCE_REPLAY, state_mode=args.state_mode, lookahead=la)
    sink = cs.JsonlSink(out)
    try:
        rc, xsh = run_replay_mode(args, models, engine, sink, plan)
    finally:
        sink.close()
    exact = xsh.exact_report() if xsh is not None else None
    if exact is not None and not exact["ok"] and rc == cs.EXIT_OK:
        rc = EXIT_EXACT_CHECK
    rows = engine.stage1_rows
    np.savez_compressed(out / f"parity-stage1-{args.state_mode}.npz", pool=np.array([r[0] for r in rows]), mint=np.array([r[1] or "" for r in rows]),
                        T=np.array([r[2] for r in rows], dtype=np.int64), sd=np.array([r[3] for r in rows], dtype=np.int64),
                        h_top1=np.array([r[4] for r in rows], dtype=np.float64),
                        vec=np.stack([r[5] for r in rows]) if rows else np.zeros((0, cs.N_FEATURES), dtype=np.float32))
    here = Path(__file__).resolve()
    tools_dir = here.parent
    summary = {
        "state_mode": args.state_mode, "hours": [hours[0], hours[-1]], "replay_hours": args.replay_hours, "decide_from": args.decide_from,
        "decide_to": args.decide_to, "decide_from_effective": plan.decide_from, "history": plan.report(), "hours_fed": len(plan.hours),
        "exit": rc, "seconds": round(time.time() - t0, 1), "VmHWM_kb": vm_hwm_kb(),
        "blobs": {n: git_blob(tools_dir / n) for n in ("c1nf_shadow.py", "c1nf_features.py", "c1nf_parity_exact.py")},
        "sha256": {"tokens": sha256_file(args.tokens), "model_manifest": sha256_file(args.model_manifest) if args.model_manifest else None,
                   "models": models.shas},
        "lookahead": None if la is None else {"rows": la.rows, "pools": len(la._idx), "index_bytes": la.nbytes(), **la.c},
        "exact_injected": engine.exact_injected, "exact": exact, "stage1_rows": len(rows), "engine_health": cs.clean(engine.health()),
        "p3": p3_md5(read_picks(out)),
    }
    try:
        summary["git_head"] = subprocess.run(["git", "-C", str(tools_dir), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        summary["git_head"] = None
    (out / f"parity-summary-{args.state_mode}.json").write_text(json.dumps(cs.clean(summary), indent=1, sort_keys=True) + "\n")
    print(f"done: mode {args.state_mode} history {args.history} ({len(plan.history_hours)} h, decide_from {plan.decide_from}) picks {summary['p3']['n']} md5 {summary['p3']['md5']} stage1 {len(rows)} "
          f"exact_injected {engine.exact_injected} exact {json.dumps(exact)} {summary['seconds']}s VmHWM {summary['VmHWM_kb']} kB", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
