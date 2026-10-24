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
                 now_ms: Callable[[], int] | None = None, meta: dict[str, Any] | None = None, decide: bool = True,
                 oracle_rows: dict[str, "PoolHist"] | None = None, oracle_mode: dict[str, tuple[tuple[int, bool], tuple[int, bool]]] | None = None) -> None:
        if not L.FOB_EVIDENCE:
            raise RuntimeError("dbc_fwd needs tools.dbc_price.enable_swap3_pricing() (registered Swap3 path; the config-mode injection uses the fob evidence switch)")
        self.first_ok_bt = float(first_ok_bt)
        self.pools: dict[str, PoolHist] = {}
        # config -> [count ev==0, count ev==1]. The screen computes the config fee mode TWICE (dbc_screen.load_exploration): mode0 from the evidence of isolated rows
        # (derive(..., iso), used by the order fix) and the final mode from the evidence of not-bad rows in fixed order (derive(..., ~bad_row)). Both are kept here, causally.
        self.cfg_ev0: dict[str, list[int]] = {}
        self.cfg_ev: dict[str, list[int]] = {}
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
        # PROOF-ONLY oracles (never in the book): the screen's two lookahead inputs. oracle_rows: every recorded row of each pool (the screen's order fix and bad flags
        # see the pool's later rows, QP "A3 lookahead"); oracle_mode: config -> ((mode0, known0), (mode, known)) as the screen's two derive calls computed them on all data.
        self.decide = decide
        self.oracle_rows = oracle_rows
        self.oracle_mode = oracle_mode
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
            if self.open and self.decide:
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
    def _arr(self, p: PoolHist, lo: int, hi: int, extend: bool = False) -> dict[str, np.ndarray]:
        if extend and self.oracle_rows is not None:  # proof-only: extend the decision pipeline input to the pool's last recorded row
            p = self.oracle_rows[p.name]
            hi = p.n()
        a = {k: np.frombuffer(p.cols[k], np.float64)[lo:hi].copy() for k in _COLS_F}
        a["slot"] = np.frombuffer(p.cols["slot"], np.int64)[lo:hi].copy()
        for k in ("side", "sw3", "fob"):
            a[k] = np.frombuffer(p.cols[k], np.int64)[lo:hi].astype(np.int8)
        return a

    def cfg_mode(self, cfg: str, first_pass: bool = False) -> tuple[int, bool]:
        """(mode, known) by majority of the evidence seen so far (dbc_price.derive's rule: mode 1 iff count(ev==1) > count(ev==0); known iff any evidence)."""
        if self.oracle_mode is not None:
            return self.oracle_mode.get(cfg, ((0, False), (0, False)))[0 if first_pass else 1]
        c = (self.cfg_ev0 if first_pass else self.cfg_ev).get(cfg)
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
        c = self.cfg_ev0.setdefault(p.config, [0, 0])
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
        # final-pass evidence of the new rows: order fix on the tail from the last anchor, then derive(..., ~bad_row) with the REAL fob
        if self.oracle_mode is None:
            te = self._tail_start(p, old_closed)
            _P, _perm, ev_fin = self._pipeline(p, te, n, want_ev=True)
            ev_fin = ev_fin[old_closed - te:n - te]  # type: ignore[index]
            c = self.cfg_ev.setdefault(p.config, [0, 0])
            c[0] += int((ev_fin == 0).sum())
            c[1] += int((ev_fin == 1).sum())
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

    def _flags(self, p: PoolHist) -> tuple[bool, int | None]:
        """(hook, completion slot) of the pool: as seen so far, or (proof-only oracle) over all recorded rows."""
        if self.oracle_rows is not None:
            q = self.oracle_rows[p.name]
            return q.hook, q.comp_slot
        return p.hook, p.comp_slot

    def _tail_start(self, p: PoolHist, lo_min: int) -> int:
        j = bisect.bisect_right(p.anchors, lo_min) - 1
        if j < 0:
            return 0
        r = p.anchors[j]
        g = bisect.bisect_right(p.gstart, r) - 1  # r's group (single row)
        return p.gstart[g - 1] if g >= 1 else 0

    def _pipeline(self, p: PoolHist, t0: int, hi: int, want_ev: bool = False, extend: bool = False) -> tuple[S.Pool, np.ndarray, np.ndarray | None]:
        """dbc_screen.load_exploration's per-pool steps on rows [t0, hi), with the config fee modes injected: every row's fob is set to (1 - mode) so that derive's
        config majority returns `mode` (FOB_EVIDENCE is on): mode0 for the first derive (order fix), the final mode for the second; mode_known is set to the causal value
        afterwards. Returns (Pool, perm relative to t0, final-pass evidence per fixed-order row with the REAL fob if want_ev)."""
        a = self._arr(p, t0, hi, extend)
        n = len(a["slot"])
        real_fob = a["fob"]
        mode0, _ = self.cfg_mode(p.config, first_pass=True)
        mode, known = self.cfg_mode(p.config)
        a["fob"] = np.full(n, 1 - mode0, np.int8)
        pid = np.zeros(n, np.int64)
        cfg = np.zeros(n, np.int64)
        iso = L.isolated(pid, a["slot"])
        d0 = L.derive(a, pid, cfg, iso)
        d0["iso"] = iso
        perm, _st, badm = L.order_fix(a, pid, d0)
        A = {k: v[perm] for k, v in a.items()}
        bad_row = badm[perm]
        ev = None
        if want_ev:
            ev = L.derive({**A, "fob": real_fob[perm]}, pid, cfg, ~bad_row)["mode_ev"]
        A["fob"] = np.full(n, 1 - mode, np.int8)
        d = L.derive(A, pid, cfg, ~bad_row)
        d["mode_known"] = np.full(n, known, bool)
        D = {"a": A, "d": d, "bad_row": bad_row, "hook_row": np.full(n, self._flags(p)[0], bool)}
        self.stats["tail_evals"] += 1
        self.stats["tail_rows"] += n
        if t0 == 0:
            self.stats["full_evals"] += 1
        return S.Pool(D, slice(0, n), 0), perm, ev

    def _decide(self, p: PoolHist, lo_new: int, hi: int) -> None:
        bt = p.cols["bt"]
        lo_min = bisect.bisect_left(bt, bt[lo_new] - WIN_S)
        t0 = self._tail_start(p, lo_min)
        P, perm, _ = self._pipeline(p, t0, hi, extend=True)
        idx = S.triggers_for(CELL, P, self.first_ok_bt, self._flags(p)[1])
        new = [i for i in idx if lo_new <= t0 + int(perm[i]) < hi]
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
            P, perm, _ = self._pipeline(p, t0, p.closed, extend=True)
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
                att, ref = S.resolve(CELL, P, [i], Dx, self._flags(p)[1])
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


# ------------------------------------------------------------------------------------------------------------------------------ replay (recorded tape)
def read_hour_ordered(path: str | os.PathLike, root: str | None = None) -> dict[str, Any]:
    """One tape hour through the dbc_price data guard, keeping ARRIVAL order of trade and curve_complete rows (dbc_price.read_hour keeps them in two lists).
    Line classification and `incomplete` are dbc_price.read_hour's, line for line."""
    rows: list[dict[str, Any]] = []
    gap, ended = False, False
    for line in L._zstd_lines(path, root):
        if line.startswith(b'{"v":1,"venue":"meteora_dbc","type":"backfill_hour_end"'):
            ended = True
        elif b"backfill_incomplete" in line and not line.startswith(b'{"v":1,"venue":"meteora_dbc","slot"'):
            gap = True
        elif b'"type":"trade"' in line or b'"type": "trade"' in line:
            d = json.loads(line)
            if d.get("type") == "trade":
                rows.append(d)
        elif b'"curve_complete"' in line:
            d = json.loads(line)
            if d.get("type") == "curve_complete":
                rows.append(d)
    return {"rows": rows, "incomplete": gap or not ended}


def screen_keys(start: datetime, end: datetime, root: str | None = None) -> dict[str, Any]:
    """The frozen screen on [start, end): trigger keys (dbc_screen.triggers_for, every pool, as cell_attempts calls it) and entry keys (dbc_screen.cell_attempts). Counts only.
    dbc_price.derive is wrapped for the duration of the load ONLY to record the two config fee-mode maps it computes (outputs unchanged); they are the proof-only oracle."""
    calls: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    orig = L.derive

    def rec(a, pid, cfg, iso=None):  # noqa: ANN001
        d = orig(a, pid, cfg, iso)
        calls.append((np.asarray(cfg).copy(), d["mode"].copy(), d["mode_known"].copy()))
        return d

    L.derive = rec
    try:
        D = S.load_exploration(root, log=lambda *_: None, start=start, end=end)
    finally:
        L.derive = orig
    assert len(calls) == 2, "load_exploration is expected to call derive twice (first pass with iso, final pass with ~bad_row)"
    A, pid = D["a"], D["pid"]
    bounds = np.flatnonzero(np.diff(pid)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(pid)]])
    first_ok_bt = D["start_ts"] + S.WARMUP_HOURS * 3600
    names = D["pool_names"]
    trig: list[str] = []
    s_of: dict[int, int] = {}
    for s, e in zip(starts, ends):
        s_of[int(pid[s])] = int(s)
        if e - s < 3:
            continue
        P = S.Pool(D, slice(int(s), int(e)), int(pid[s]))
        for i in S.triggers_for(CELL, P, first_ok_bt, D["complete_slot"].get(P.pid)):
            trig.append(key_of(names[P.pid], int(P.slot[i]), float(P.S1[i])))
    att, refused, st = S.cell_attempts(CELL, D)
    ent = [key_of(names[a["pid"]], int(A["slot"][s_of[a["pid"]] + a["i"]]), float(A["S1"][s_of[a["pid"]] + a["i"]])) for a in att]
    modes: list[dict[str, tuple[int, bool]]] = []
    for cfg, m, k in calls:
        mm: dict[str, tuple[int, bool]] = {}
        for c, mv, kv in zip(cfg, m, k):
            mm.setdefault(D["cfg_names"][int(c)], (int(mv), bool(kv)))
        modes.append(mm)
    oracle_mode = {c: (modes[0].get(c, (0, False)), modes[1].get(c, (0, False))) for c in set(modes[0]) | set(modes[1])}
    return {"trigger_keys": trig, "entry_keys": ent, "refused": refused, "trigger_prints": st["trigger_prints"], "slot_s": D["slot_s"], "good_hours": D["good_hours"],
            "bad_hours": D["bad_hours"], "start_ts": D["start_ts"], "oracle_mode": oracle_mode, "rows": int(len(pid)), "pools": D["n_pools"]}


def stream_hours(eng: "Engine", start: datetime, end: datetime, root: str | None, live_ss: dict[int, float] | None = None, log: Any = None) -> int:
    """Feed every complete recorded hour of [start, end) to `eng` in tape order (incomplete hours are skipped, as dbc_screen.load_exploration does). Returns hours streamed."""
    n = 0
    for h in L.exploration_hours(start, end):
        f = L.hour_file(h, root)
        hk = int(h.timestamp() // 3600)
        if not os.path.exists(f):
            continue
        r = read_hour_ordered(f, root)
        if r["incomplete"]:
            continue
        n += 1
        for row in r["rows"]:
            eng.on_row(row)
        if live_ss is not None:
            sol = [x for x in r["rows"] if x.get("type") == "trade" and L.usable_row(x)]
            v = slot_s_of(np.array([L.block_time_s(x) for x in sol]), np.array([int(x["slot"]) for x in sol])) if sol else None
            if v is not None:
                live_ss[hk + 1] = v  # causal: the live policy uses the previous hour's measured slot length
        if log is not None and eng.decide:
            log.append(BOOK, int((h.timestamp() + 3600) * 1000) - 1, {"type": "hb", "book": BOOK, "hour": f"{h:%Y-%m-%dT%H}", "decisions": len(eng.decisions_live),
                                                                       "trigger_prints": eng.stats["trigger_prints"], "sol_rows": eng.stats["sol_rows"]})
        del r
    if eng.decide:
        eng.finish()
    return n


VARIANTS = (("oracle_rows+oracle_mode", True, True), ("oracle_mode_only", False, True), ("oracle_rows_only", True, False), ("causal", False, False))


def replay(start: datetime, end: datetime, root: str | None = None, cfg_summary: str | None = None, log_root: str | None = None) -> dict[str, Any]:
    """Equivalence replay on recorded hours [start, end) inside [2026-10-14T00, 2026-10-16T04). Runs the screen, then streams the same hours through the engine four times:
    with both proof-only oracles (must equal the screen exactly: md5 of sorted trigger keys and of sorted entry keys), with one oracle each (attribution), and causal (the
    book; its hash-chained decision log is written). Returns counts and md5s only; nothing is priced."""
    replay_guard(start, end, root)
    L.enable_swap3_pricing()
    if cfg_summary:
        L.load_config_thresholds(cfg_summary)
    t0 = time.time()
    scr = screen_keys(start, end, root)
    res: dict[str, Any] = {"schema": SCHEMA, "window": [f"{start:%Y-%m-%dT%H}", f"{end:%Y-%m-%dT%H}"], "good_hours": len(scr["good_hours"]), "bad_hours": scr["bad_hours"],
                           "screen": {"trigger_prints": len(scr["trigger_keys"]), "entries": len(scr["entry_keys"]), "md5_triggers": md5_keys(scr["trigger_keys"]),
                                      "md5_entries": md5_keys(scr["entry_keys"]), "refused": scr["refused"], "rows": scr["rows"], "pools": scr["pools"],
                                      "seconds": round(time.time() - t0, 1)}, "variants": {}}
    first_ok = scr["start_ts"] + S.WARMUP_HOURS * 3600
    coll = Engine(first_ok, decide=False)
    stream_hours(coll, start, end, root)
    from tools.arena_declog import DecisionLog  # noqa: PLC0415

    for name, o_rows, o_mode in VARIANTS:
        t1 = time.time()
        log = DecisionLog(log_root) if (log_root and name == "causal") else None
        live_ss: dict[int, float] = {}
        eng = Engine(first_ok, ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), ss_screen=lambda hk: S.slot_s_for(scr["slot_s"], hk),
                     good_hours=set(scr["good_hours"]), screen_policy=True, log=log, meta={"bt_src": "tape_block_time"},
                     oracle_rows=coll.pools if o_rows else None, oracle_mode=scr["oracle_mode"] if o_mode else None)
        if log is not None:
            log.append(BOOK, int(scr["start_ts"] * 1000), {"type": "start", "book": BOOK, "mode": "replay", "window": res["window"], "cell": "m10", "exit_rule": EXIT_RULE,
                                                           "code_sha256": code_shas(), "schema": SCHEMA})
        hs = stream_hours(eng, start, end, root, live_ss, log)
        e_trig, e_ent = eng.trig_keys, [x["key"] for x in eng.entries_screen]
        live = [x["key"] for x in eng.decisions_live]
        st, se = set(scr["trigger_keys"]), set(scr["entry_keys"])
        v = {"hours_streamed": hs, "trigger_prints": len(e_trig), "entries_screen_policy": len(e_ent), "decisions_live_policy": len(live),
             "md5_triggers": md5_keys(e_trig), "md5_entries_screen_policy": md5_keys(e_ent), "md5_decisions_live_policy": md5_keys(live),
             "equal_triggers": md5_keys(e_trig) == res["screen"]["md5_triggers"], "equal_entries": md5_keys(e_ent) == res["screen"]["md5_entries"],
             "triggers_screen_only": len(st - set(e_trig)), "triggers_engine_only": len(set(e_trig) - st), "entries_screen_only": len(se - set(e_ent)),
             "entries_engine_only": len(set(e_ent) - se), "live_decisions_not_screen_entries": len(set(live) - se), "screen_entries_not_live_decisions": len(se - set(live)),
             "examples": {"triggers_screen_only": sorted(st - set(e_trig))[:5], "triggers_engine_only": sorted(set(e_trig) - st)[:5]},
             "refused_screen_policy": eng.screen_refused, "stats": eng.stats, "seconds": round(time.time() - t1, 1)}
        if o_mode is False:
            v["config_mode_differs_from_screen_at_end"] = sum(1 for c, (m0, m1) in scr["oracle_mode"].items() if m1[1] and eng.cfg_mode(c) != m1)
        if log is not None:
            v["declog"] = {"md5": log.md5(), "head": list(log.verify(BOOK))}
        res["variants"][name] = v
        del eng
    o = res["variants"]["oracle_rows+oracle_mode"]
    res["equivalence_pass"] = bool(o["equal_triggers"] and o["equal_entries"])
    res["code_sha256"] = code_shas()
    res["taken_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    res["note"] = ("Decision equivalence only (keys = pool|slot|post sqrt price as float64). Nothing priced. Not evidence that m10 makes money. The two oracles are the screen's "
                   "lookahead inputs (A3: order fix / bad flags from the pool's later rows; config fee mode from all data); they are proof-only and never used by the book.")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--replay", action="store_true", help="equivalence replay on recorded exploration hours (required; the live mode is a later PR)")
    ap.add_argument("--start", required=True, help="YYYY-MM-DDTHH, >= 2026-10-14T00")
    ap.add_argument("--end", required=True, help="YYYY-MM-DDTHH exclusive, <= 2026-10-16T04")
    ap.add_argument("--root", default=None)
    ap.add_argument("--cfg-summary", default=str(Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json"))
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    if not ns.replay:
        print("REFUSED: only --replay exists in this build (no live feed is started by this tool)", file=sys.stderr)
        return 2
    pa = lambda v: datetime.strptime(v, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)  # noqa: E731
    start, end = pa(ns.start), pa(ns.end)
    try:
        replay_guard(start, end, ns.root)
    except ReplayRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    out = Path(S.check_out_dir(ns.out))
    if out.exists() and any(out.iterdir()):
        print(f"REFUSED: output dir not empty: {out}", file=sys.stderr)
        return 4
    out.mkdir(parents=True, exist_ok=True)
    res = replay(start, end, ns.root, ns.cfg_summary, str(out / "declog"))
    txt = json.dumps(res, indent=1, default=float)
    (out / "equivalence.json").write_text(txt)
    print(json.dumps({"window": res["window"], "equivalence_pass": res["equivalence_pass"], "screen": {k: res["screen"][k] for k in ("trigger_prints", "entries", "md5_triggers", "md5_entries")}}))
    for k, v in res["variants"].items():
        print(k, json.dumps({x: v[x] for x in ("trigger_prints", "entries_screen_policy", "decisions_live_policy", "md5_triggers", "md5_entries_screen_policy", "equal_triggers",
                                               "equal_entries", "triggers_screen_only", "triggers_engine_only", "entries_screen_only", "entries_engine_only", "seconds")}))
    print("sha256 equivalence.json", hashlib.sha256(txt.encode()).hexdigest())
    return 0 if res["equivalence_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
