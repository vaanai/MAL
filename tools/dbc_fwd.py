#!/usr/bin/env python3
"""dbc_fwd: causal m10 decision engine for a forward PAPER DBC book (P18, docs/research/dbc-p18/DESIGN.md). PAPER ONLY: no key, no wallet, no send, no price.

The engine takes decoded DBC rows (tools/dbc_decode.decode_tx rows, venue_tape v1 schema) in arrival order and decides D1 m10 at each slot close, using ONLY rows already
seen. It runs the FROZEN screen code on the pool's rows at decision time (tools.dbc_price isolated/derive/order_fix, tools.dbc_screen Pool/triggers_for/resolve with cell
m10 from dbc_screen.CELLS); it does not re-implement the rule. What it adds is the causal plumbing:

* slot close: a pool's rows of slot s are decided once a row of a later slot arrives (the screen's order fix needs the whole (pool, slot) group);
* config fee mode: the screen takes each config's mode as the majority of fee-mode evidence over the WHOLE loaded data (all pools, all hours). The engine keeps the same
  majority over the evidence seen SO FAR (evidence per row as in dbc_price.derive with the isolated-row mask) and injects it (`forced fob`, see `_pipeline`);
* tail evaluation: the pipeline runs on the pool's rows from one group before the last "anchor" row (a single-row slot after a single-row slot whose quote leg is >= MIN_L_QUOTE
  under both fee modes) at or before the 60 s window. From an anchor on, every carried value (L, order-fix reference L, bad flag, S0) equals the full-history value; the
  equivalence replay below checks this on real hours;
* cooldown, two policies, both computed:
    - `live`  (the book): a trigger is SEALED when it fires (decision row in the hash-chained log); it blocks the pool until its paper exit + 600 s. A later pricer refusal
              (bad_state, completed_before_entry, ...) does not release the cooldown (DESIGN.md section 2, QP rules on the refusal cost);
    - `screen` (parity only): exactly dbc_screen.resolve's order: a trigger is resolved by dbc_screen.resolve itself once the data up to its exit horizon has arrived, and a
              refused trigger does not block. Decisions that waited for an earlier unresolved trigger are late (flagged); this policy exists only for the md5 proof.

Replay (`--replay`) reads tape hours through the dbc_price data guard and ADDITIONALLY refuses any hour outside [2026-10-14T00, 2026-10-16T04) (m10's Swap2-era exploration
window, manager15 correction 2026-10-24: hours [10-17T00, 10-24T00) are spent for every D1 variant and hours >= 10-25T00 are never read). It runs the screen (load_exploration,
triggers_for, cell_attempts) on the same hours and prints the md5 of the sorted trigger keys and of the sorted entry keys on both sides. Counts only; nothing is priced.

    python3 -m tools.dbc_fwd --replay --start 2026-10-14T00 --end 2026-10-16T04 --cfg-summary docs/research/dbc-p17/data/cfg_summary.json --out DIR
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import bisect
import hashlib
import heapq
import json
import os
import sys
import time
from array import array
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from tools import dbc_price as L
from tools import dbc_screen as S

SCHEMA = "dbc_fwd_v1"
BOOK = "dbc-m10-paper"
CELL = next(c for c in S.CELLS if c.name == "m10")
SOL = 1e9
WIN_S = 60.0  # m10 window (dbc_screen.triggers_for D1)
EXIT_RULE = "sl0.90|trail0.90|time600|cooldown600"
REPLAY_MIN = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
REPLAY_MAX = datetime(2026, 10, 16, 4, tzinfo=timezone.utc)  # exclusive: Swap2-era exploration only
SPENT_START = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)
NEVER_FROM = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
FORBIDDEN_PATH_PARTS = ("fwdv1", "meteora_dbc_holdout", "p17_union")
_COLS_F = ("bt", "S1", "ain", "aout", "fee", "qres", "thr")
_COLS_I = ("slot", "side", "sw3", "fob")


class ReplayRefused(Exception):
    """The replay window guard refused: nothing was read."""


def replay_guard(start: datetime, end: datetime, root: str | None = None) -> None:
    """Refuse any replay outside [2026-10-14T00, 2026-10-16T04) and any root under a forward / holdout / spent-union tree. Called before any file is opened."""
    if start < REPLAY_MIN or end > REPLAY_MAX or start >= end:
        raise ReplayRefused(f"replay_window_refused: [{start:%Y-%m-%dT%H}, {end:%Y-%m-%dT%H}) is not inside [2026-10-14T00, 2026-10-16T04)")
    for p in (root or "", os.path.realpath(root) if root else ""):
        if any(x in p for x in FORBIDDEN_PATH_PARTS):
            raise ReplayRefused(f"replay_root_refused: {root}")
    if L.SPENT_ROOT is not None or L.HOLDOUT_READ_ENABLED or L.FWD_ACTIVE is not None:
        raise ReplayRefused("replay_refused: a non-default dbc_price data window is enabled")


def sha256_file(p: str | os.PathLike) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def code_shas() -> dict[str, str]:
    here = Path(__file__).resolve().parent
    return {f: sha256_file(here / f) for f in ("dbc_fwd.py", "dbc_price.py", "dbc_screen.py", "dbc_decode.py", "arena_declog.py")}


def key_of(pool: str, slot: int, s1: float) -> str:
    """Decision key: pool, slot and the float64 post sqrt price (the screen keeps S1 as float64; the same conversion is used on both sides)."""
    return f"{pool}|{int(slot)}|{float(s1)!r}"


def md5_keys(keys: Iterable[str]) -> str:
    return hashlib.md5("\n".join(sorted(keys)).encode()).hexdigest()


def slot_s_of(bt: np.ndarray, sl: np.ndarray) -> float | None:
    """dbc_screen.load_exploration's per-hour slot length rule."""
    if len(bt) > 50 and bt.max() > bt.min():
        return float((bt.max() - bt.min()) / max(sl.max() - sl.min(), 1))
    return None


# ------------------------------------------------------------------------------------------------------------------------------ per pool state
class PoolHist:
    __slots__ = ("name", "config", "cols", "sig", "gstart", "anchors", "closed", "hook", "comp_slot", "q_screen", "pend_screen", "busy_screen", "pend_live", "busy_live",
                 "sum_buy_ain", "sum_sell_q", "n_buy")

    def __init__(self, name: str, config: str) -> None:
        self.name, self.config = name, config
        self.cols: dict[str, array] = {k: array("d") for k in _COLS_F}
        self.cols.update({k: array("q") for k in _COLS_I})
        self.sig: list[str] = []
        self.gstart: list[int] = []  # start index of each (slot) group, in arrival order
        self.anchors: list[int] = []  # anchor row indices (see module doc), ascending
        self.closed = 0  # rows [0, closed) belong to closed groups
        self.hook = False
        self.comp_slot: int | None = None
        self.q_screen: list[dict[str, Any]] = []  # screen policy: triggers waiting for an earlier unresolved trigger
        self.pend_screen: dict[str, Any] | None = None
        self.busy_screen = -1.0
        self.pend_live: dict[str, Any] | None = None
        self.busy_live = -1.0
        self.sum_buy_ain = array("d")  # prefix sums for the cheap upper-bound prefilter
        self.sum_sell_q = array("d")
        self.n_buy = array("q")

    def n(self) -> int:
        return len(self.sig)


class Engine:
    """m10 engine. Feed rows with `on_row` in arrival order; call `finish()` at the end of a replay.

    first_ok_bt: block time from which signals are allowed (process / replay start + 1 h warm-up).
    ss_live(hour_key) -> slot length used by the live policy (causal). ss_screen(hour_key) -> slot length for the screen policy (parity: the screen's per-hour value).
    good_hours: the screen policy's dbc_screen.resolve input (parity only; hour keys = unix hour). log: tools.arena_declog.DecisionLog or None. now_ms: wall clock for t_decide_ms (None in replay)."""

    def __init__(self, first_ok_bt: float, *, ss_live: Callable[[int], float] | None = None, ss_screen: Callable[[int], float] | None = None,
                 good_hours: set[int] | None = None, screen_policy: bool = False, log: Any = None, log_name: str = BOOK,
                 now_ms: Callable[[], int] | None = None, meta: dict[str, Any] | None = None) -> None:
        if not L.FOB_EVIDENCE:
            raise RuntimeError("dbc_fwd needs tools.dbc_price.enable_swap3_pricing() (registered Swap3 path; the config-mode injection uses the fob evidence switch)")
        self.first_ok_bt = float(first_ok_bt)
        self.pools: dict[str, PoolHist] = {}
        self.cfg_ev: dict[str, list[int]] = {}  # config -> [count ev==0, count ev==1]
        self.cur_slot = -1
        self.open: dict[str, None] = {}  # pools with rows in the current (open) slot, insertion ordered
        self.heap: list[tuple[int, int, str]] = []  # (horizon slot, seq, pool) of pending resolutions
        self._hseq = 0
        self.ss_live = ss_live or (lambda hk: L.SLOT_S_FALLBACK)
        self.ss_screen = ss_screen or self.ss_live
        self.good_hours = good_hours
        self.screen_policy = screen_policy
        if screen_policy and not good_hours:
            raise ValueError("screen policy (parity) needs the replay's good hours (dbc_screen.resolve input)")
        self.log, self.log_name, self.now_ms = log, log_name, now_ms
        self.meta = meta or {}
        self.trig_keys: list[str] = []
        self.entries_screen: list[dict[str, Any]] = []
        self.decisions_live: list[dict[str, Any]] = []
        self.stats = {"rows": 0, "sol_rows": 0, "completes": 0, "late_rows": 0, "groups_closed": 0, "prefilter_pass": 0, "tail_evals": 0, "tail_rows": 0, "full_evals": 0,
                      "trigger_prints": 0, "live_cooldown": 0, "screen_cooldown": 0, "screen_late_decisions": 0, "live_exits": 0}
        self.screen_refused = {"bad_hour": 0, "block_edge": 0, "cooldown": 0, "no_state": 0, "bad_state": 0, "completed_before_entry": 0}
        self.sealed_slots: dict[int, None] = {}

    # ---------------------------------------------------------------- input
    def on_row(self, r: dict[str, Any]) -> None:
        t = r.get("type")
        if t == "trade":
            self.on_trade(r)
        elif t == "curve_complete":
            self.on_complete(r)

    def advance(self, slot: int) -> None:
        """A row of `slot` arrived: every group of an earlier slot is closed and decided."""
        if slot > self.cur_slot:
            if self.open:
                for name in list(self.open):
                    self._close(self.pools[name])
                self.open.clear()
            self.cur_slot = slot
            self._resolve_due(slot)

    def on_complete(self, r: dict[str, Any]) -> None:
        self.stats["completes"] += 1
        self.advance(int(r["slot"]))
        p = self.pools.get(r["pool"])
        if p is None:
            p = self.pools[r["pool"]] = PoolHist(r["pool"], r.get("config") or "")
        s = int(r["slot"])
        p.comp_slot = s if p.comp_slot is None else min(p.comp_slot, s)

    def on_trade(self, r: dict[str, Any]) -> None:
        self.stats["rows"] += 1
        if not L.usable_row(r):
            return
        self.stats["sol_rows"] += 1
        slot = int(r["slot"])
        if slot < self.cur_slot:
            self.stats["late_rows"] += 1  # live only (the tape is slot ordered): appended, its group was already decided
        self.advance(slot)
        p = self.pools.get(r["pool"])
        if p is None:
            p = self.pools[r["pool"]] = PoolHist(r["pool"], r.get("config") or "")
        if not p.config:
            p.config = r.get("config") or ""
        if L.hook_row(r) or r.get("config") in L.CFG_XFER or r.get("config") in L.CFG_UNMAPPED:
            p.hook = True
        a = L.rows_to_arrays([r])
        i = p.n()
        if i == 0 or int(p.cols["slot"][i - 1]) != slot:
            p.gstart.append(i)
        for k in _COLS_F:
            p.cols[k].append(float(a[k][0]))
        for k in _COLS_I:
            p.cols[k].append(int(a[k][0]))
        p.sig.append(r.get("signature") or "")
        buy = int(a["side"][0]) == 1
        prev = (p.sum_buy_ain[-1], p.sum_sell_q[-1], p.n_buy[-1]) if i else (0.0, 0.0, 0)
        p.sum_buy_ain.append(prev[0] + (float(a["ain"][0]) if buy else 0.0))
        p.sum_sell_q.append(prev[1] + (0.0 if buy else float(a["aout"][0] + a["fee"][0])))
        p.n_buy.append(prev[2] + (1 if buy else 0))
        self.open[p.name] = None

    def finish(self) -> None:
        """End of stream (replay): close the open slot and resolve every pending trigger with the data present (what the screen sees at the data end)."""
        for name in list(self.open):
            self._close(self.pools[name])
        self.open.clear()
        self._resolve_due(1 << 62)

    # ---------------------------------------------------------------- slot close
    def _arr(self, p: PoolHist, lo: int, hi: int) -> dict[str, np.ndarray]:
        a = {k: np.frombuffer(p.cols[k], np.float64)[lo:hi].copy() for k in _COLS_F}
        a["slot"] = np.frombuffer(p.cols["slot"], np.int64)[lo:hi].copy()
        for k in ("side", "sw3", "fob"):
            a[k] = np.frombuffer(p.cols[k], np.int64)[lo:hi].astype(np.int8)
        return a

    def cfg_mode(self, cfg: str) -> tuple[int, bool]:
        c = self.cfg_ev.get(cfg)
        if not c:
            return 0, False
        return (1 if c[1] > c[0] else 0), (c[0] + c[1]) > 0

    def _close(self, p: PoolHist) -> None:
        n = p.n()
        if p.closed >= n:
            return
        gi = bisect.bisect_left(p.gstart, p.closed)  # first new group (closed is always a group start)
        # 1) fee-mode evidence (dbc_price.derive with the isolated-row mask and the real fob) for the new rows; 2) anchors. Context: the previous group.
        lo = p.gstart[gi - 1] if gi >= 1 else 0
        a = self._arr(p, lo, n)
        pid = np.zeros(n - lo, np.int64)
        iso = L.isolated(pid, a["slot"])
        d = L.derive(a, pid, pid, iso)
        k0 = p.closed - lo
        ev = d["mode_ev"][k0:]
        c = self.cfg_ev.setdefault(p.config, [0, 0])
        c[0] += int((ev == 0).sum())
        c[1] += int((ev == 1).sum())
        buy = a["side"] == 1
        q0 = np.where(buy, a["ain"], a["aout"] + a["fee"])
        q1 = np.where(buy, a["ain"] - a["fee"], a["aout"] + a["fee"])
        S0 = d["S0"]
        anc = iso & d["same"] & np.isfinite(S0) & (a["S1"] != S0) & (np.minimum(q0, q1) >= L.MIN_L_QUOTE)
        for j in np.flatnonzero(anc[k0:]):
            p.anchors.append(lo + k0 + int(j))
        old_closed = p.closed
        p.closed = n
        self.stats["groups_closed"] += len(p.gstart) - gi
        # 3) cheap upper-bound prefilter, then the frozen rule on the tail
        if self._prefilter(p, old_closed, n):
            self.stats["prefilter_pass"] += 1
            self._decide(p, old_closed, n)

    def _prefilter(self, p: PoolHist, lo_new: int, hi: int) -> bool:
        """True if some new BUY row could fire m10. Never false for a row that fires: buys over the window incl. the whole new group is an upper bound of the count;
        buy quote in (amount_in >= the curve leg in both fee modes) minus the sells BEFORE the new group is an upper bound of the net SOL."""
        if p.hook:
            return False
        bt = p.cols["bt"]
        g0 = lo_new
        for i in range(lo_new, hi):
            if p.cols["side"][i] != 1:
                continue
            if bt[i] < self.first_ok_bt or p.cols["thr"][i] < S.THR_MIN:
                continue
            prog = p.cols["qres"][i] / p.cols["thr"][i] if p.cols["thr"][i] else float("inf")
            if not (0.02 <= prog <= 0.35):
                continue
            w = bisect.bisect_left(bt, bt[i] - WIN_S)
            nb = p.n_buy[hi - 1] - (p.n_buy[w - 1] if w else 0)
            if nb < CELL.params[0]:
                continue
            buy_up = p.sum_buy_ain[hi - 1] - (p.sum_buy_ain[w - 1] if w else 0.0)
            sell_lo = (p.sum_sell_q[g0 - 1] if g0 else 0.0) - (p.sum_sell_q[w - 1] if w else 0.0) if g0 > w else 0.0
            if buy_up - sell_lo >= CELL.params[1] * SOL * (1 - 1e-9) - 1:
                return True
        return False

    def _tail_start(self, p: PoolHist, lo_min: int) -> int:
        j = bisect.bisect_right(p.anchors, lo_min) - 1
        if j < 0:
            return 0
        r = p.anchors[j]
        g = bisect.bisect_right(p.gstart, r) - 1  # r's group (single row)
        return p.gstart[g - 1] if g >= 1 else 0

    def _pipeline(self, p: PoolHist, t0: int, hi: int) -> tuple[S.Pool, np.ndarray]:
        """dbc_screen.load_exploration's per-pool steps on rows [t0, hi), with the config fee mode injected: every row's fob is set to (1 - mode) so that derive's
        config majority returns `mode` (FOB_EVIDENCE is on), and mode_known is set to the causal value afterwards. Returns (Pool, perm) with perm relative to t0."""
        a = self._arr(p, t0, hi)
        n = hi - t0
        mode, known = self.cfg_mode(p.config)
        a["fob"] = np.full(n, 1 - mode, np.int8)
        pid = np.zeros(n, np.int64)
        cfg = np.zeros(n, np.int64)
        iso = L.isolated(pid, a["slot"])
        d0 = L.derive(a, pid, cfg, iso)
        d0["iso"] = iso
        perm, _st, badm = L.order_fix(a, pid, d0)
        A = {k: v[perm] for k, v in a.items()}
        bad_row = badm[perm]
        d = L.derive(A, pid, cfg, ~bad_row)
        d["mode_known"] = np.full(n, known, bool)
        D = {"a": A, "d": d, "bad_row": bad_row, "hook_row": np.full(n, p.hook, bool)}
        self.stats["tail_evals"] += 1
        self.stats["tail_rows"] += n
        if t0 == 0:
            self.stats["full_evals"] += 1
        return S.Pool(D, slice(0, n), 0), perm

    def _decide(self, p: PoolHist, lo_new: int, hi: int) -> None:
        bt = p.cols["bt"]
        lo_min = bisect.bisect_left(bt, bt[lo_new] - WIN_S)
        t0 = self._tail_start(p, lo_min)
        P, perm = self._pipeline(p, t0, hi)
        idx = S.triggers_for(CELL, P, self.first_ok_bt, p.comp_slot)
        new = [i for i in idx if t0 + int(perm[i]) >= lo_new]
        for i in new:
            o = t0 + int(perm[i])
            key = key_of(p.name, int(P.slot[i]), float(P.S1[i]))
            self.trig_keys.append(key)
            self.stats["trigger_prints"] += 1
            lo = P.win_lo(i, WIN_S)
            feat = {
                "n_buys_60s": int(P.c_buy_n[i + 1] - P.c_buy_n[lo]),
                "net_sol_60s": float(((P.c_buy_sol[i + 1] - P.c_buy_sol[lo]) - (P.c_sell_sol[i + 1] - P.c_sell_sol[lo])) / SOL),
                "move": float(P.p_post[i] / P.p_pre[lo] - 1.0), "prog_post": float(P.prog_post[i]), "thr_sol": float(P.thr[i] / SOL), "imp25": float(P.imp25[i]),
                "fee_mode": int(P.mode[i]), "mode_known": bool(P.mode_known),
            }
            trig = {"key": key, "pool": p.name, "config": p.config, "signature": p.sig[o], "row": o, "slot": int(P.slot[i]), "bt": float(P.bt[i]), "feat": feat}
            self._live_trigger(p, trig)
            if self.screen_policy:
                p.q_screen.append({**trig, "late": p.pend_screen is not None})
                self._screen_pump(p)

    # ---------------------------------------------------------------- policies
    @staticmethod
    def _horizon(slot: int, ss: float) -> int:
        """Last slot dbc_screen.resolve reads for a trigger at `slot`: time cap + exit leg (cap = slot + round(600 / ss), exit at <= cap + k_out)."""
        return slot + max(1, int(round(CELL.exit.hold_s / ss))) + S.k_slots(L.LAT_S, ss)

    def _push(self, horizon: int, p: PoolHist) -> None:
        self._hseq += 1
        heapq.heappush(self.heap, (horizon, self._hseq, p.name))

    def _live_trigger(self, p: PoolHist, trig: dict[str, Any]) -> None:
        if p.pend_live is not None or trig["bt"] < p.busy_live:
            self.stats["live_cooldown"] += 1
            return
        hk = int(trig["bt"] // 3600)
        ss = float(self.ss_live(hk))
        k_in = S.k_slots(L.LAT_S, ss)
        body = {"type": "decision", "book": BOOK, "cell": "m10", "pool": trig["pool"], "config": trig["config"], "signature": trig["signature"], "slot": trig["slot"],
                "block_time": trig["bt"], "bt_src": self.meta.get("bt_src", "tape"), "slot_s": ss, "k_in": k_in, "entry_slot": trig["slot"] + k_in, "exit_rule": EXIT_RULE,
                "stake_sol": L.STAKE_DECIDING, "features": trig["feat"], "key": trig["key"]}
        if self.now_ms is not None:
            body["t_decide_ms"] = int(self.now_ms())
        row = self._append(int(trig["bt"] * 1000), body)
        p.pend_live = {**trig, "ss": ss, "k_in": k_in, "seq": row.get("seq") if row else None}
        self.decisions_live.append({"key": trig["key"], "pool": trig["pool"], "slot": trig["slot"], "bt": trig["bt"], "seq": row.get("seq") if row else None})
        self._push(self._horizon(trig["slot"], ss), p)

    def _screen_pump(self, p: PoolHist) -> None:
        """Move the screen-policy queue as far as the resolved state allows (dbc_screen.resolve order: cooldown first, then resolve; a refusal does not block)."""
        while p.pend_screen is None and p.q_screen:
            t = p.q_screen.pop(0)
            if t["bt"] < p.busy_screen:
                self.stats["screen_cooldown"] += 1
                self.screen_refused["cooldown"] += 1
                continue
            hk = int(t["bt"] // 3600)
            ss = S.slot_s_for({hk: self.ss_screen(hk)}, hk)
            p.pend_screen = {**t, "ss": ss}
            self._push(self._horizon(t["slot"], ss), p)

    def _resolve_due(self, slot: int) -> None:
        while self.heap and self.heap[0][0] < slot:
            _, _, name = heapq.heappop(self.heap)
            p = self.pools[name]
            self._resolve_pool(p, slot)

    def _resolve_pool(self, p: PoolHist, slot: int) -> None:
        for which in ("live", "screen"):
            t = p.pend_live if which == "live" else p.pend_screen
            if t is None:
                continue
            ss = t["ss"]
            if self._horizon(t["slot"], ss) >= slot:
                continue
            lo_min = bisect.bisect_left(p.cols["bt"], t["bt"] - WIN_S)
            t0 = self._tail_start(p, min(lo_min, t["row"]))
            P, perm = self._pipeline(p, t0, p.closed)
            inv = np.empty_like(perm)
            inv[perm] = np.arange(len(perm))
            i = int(inv[t["row"] - t0])
            if which == "live":
                fire_slot, exit_bt = exit_fire(P, i, ss)
                p.busy_live = exit_bt + CELL.cooldown_s
                p.pend_live = None
                self.stats["live_exits"] += 1
                self._append(int(exit_bt * 1000), {"type": "exit", "book": BOOK, "pool": p.name, "decision_seq": t.get("seq"), "key": t["key"], "fire_slot": fire_slot,
                                                   "exit_slot": fire_slot + S.k_slots(L.LAT_S, ss), "busy_until_bt": p.busy_live})
            else:
                Dx = {"slot_s": {int(t["bt"] // 3600): ss}, "good_hours": sorted(self.good_hours or [])}
                att, ref = S.resolve(CELL, P, [i], Dx, p.comp_slot)
                for k, v in ref.items():
                    self.screen_refused[k] += v
                if att:
                    p.busy_screen = t["bt"] + att[0]["hold_real_s"] + CELL.cooldown_s
                    self.entries_screen.append({"key": t["key"], "pool": p.name, "slot": t["slot"], "bt": t["bt"], "late": t["late"]})
                    if t["late"]:
                        self.stats["screen_late_decisions"] += 1
                p.pend_screen = None
                self._screen_pump(p)

    def _append(self, ms: int, body: dict[str, Any]) -> dict[str, Any] | None:
        if self.log is None:
            return None
        return self.log.append(self.log_name, ms, body)


def exit_fire(P: S.Pool, i: int, ss: float, lat_in: float = L.LAT_S) -> tuple[int, float]:
    """The exit slot of a trigger at pool index i: dbc_screen.resolve's exit block (sl/trail on the running peak from the decision spot, time cap), without its refusals.
    Returns (fire_slot, exit block time estimate = bt + (fire_slot - slot) * ss)."""
    ex = CELL.exit
    k_in = S.k_slots(lat_in, ss)
    sd = int(P.slot[i])
    cap = sd + max(1, int(round(ex.hold_s / ss)))
    ref = float(P.p_post[i])
    m0 = int(np.searchsorted(P.slot, sd + k_in + 1, "left"))
    m1 = int(np.searchsorted(P.slot, cap, "right"))
    fire_slot = cap
    if m1 > m0 and (ex.tp or ex.sl or ex.trail):
        pp = np.where(P.bad[m0:m1], np.nan, P.p_post[m0:m1])
        hit = np.zeros(len(pp), bool)
        with np.errstate(invalid="ignore"):
            if ex.tp:
                hit |= pp >= ref * ex.tp
            if ex.sl:
                hit |= pp <= ref * ex.sl
            if ex.trail:
                pk = np.maximum.accumulate(np.maximum(np.nan_to_num(pp, nan=0.0), ref))
                hit |= pp <= pk * ex.trail
        if hit.any():
            fire_slot = int(P.slot[m0 + int(np.argmax(hit))])
    return fire_slot, float(P.bt[i]) + (fire_slot - sd) * ss
