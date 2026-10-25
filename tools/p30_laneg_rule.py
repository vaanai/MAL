"""p30_laneg_rule: P30 lane-G numeric rule grid for the tools.arena_screen kit (24 entry rules x 2 exits = 48 cells). EXPLORATION ONLY. Paper only. No edge claim.

Frozen by docs/research/p30-laneg-numeric/DECLARE.md (sections 3-6) before any pricing. Pure numpy over the kit's HourRows; no I/O, no clock, no network.

    rule = LanegRule()                       # the 48-cell grid
    rule = LanegRule(only=["PULL-F1-A0|X1"])  # a one-cell rule (the frozen validation run)

Decision rule: at every minute mark M of an hour, a pool is a candidate when it is in the V band, is known to be 2 h .. 7 d old, and passes the lane-G activity filters; features use only rows with
block_time <= M, and the decision slot is the pool's last print slot with block_time <= M (inside the current hour). One trigger per (entry rule, pool) per 30 min.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import itertools
from typing import Any

import numpy as np

from tools.arena_screen import Cell, ExitSpec, HourRows, RunState

QUOTE_FLOOR = 30e9  # lamports, real quote (V not included), post-trade
AGE_MIN_S, AGE_MAX_S = 2 * 3600, 7 * 86400
PRINTS_15M, SIGNERS_15M, LAST_PRINT_S, VOL60_MIN = 20, 8, 120, 10e9
THROTTLE_S = 1800
FLOW_MIN = 0.10
ACCEL_MIN = 1.5

# entry shape -> predicate on (r15, r60); thresholds frozen in DECLARE section 6
SHAPES = {
    "MOM": lambda r15, r60: r15 >= 0.05,
    "MOM2": lambda r15, r60: r15 >= 0.10,
    "PULL": lambda r15, r60: (r60 >= 0.10) & (r15 <= -0.04),
    "PULLD": lambda r15, r60: (r60 >= 0.10) & (r15 <= -0.08),
    "BNC": lambda r15, r60: (r60 <= -0.10) & (r15 >= 0.03),
    "KNIFE": lambda r15, r60: (r15 <= -0.08) & (r60 <= 0.0),
}
EXITS = {"X1": ExitSpec(3600.0, tp=1.20, sl=0.92), "X2": ExitSpec(14400.0, tp=1.50, sl=0.85)}


def entry_names() -> list[str]:
    return [f"{s}-F{f}-A{a}" for s, f, a in itertools.product(SHAPES, (0, 1), (0, 1))]


def cell_names() -> list[str]:
    return [f"{e}|{x}" for e in entry_names() for x in EXITS]


def post_price(hr: HourRows, s: slice) -> tuple[np.ndarray, np.ndarray]:
    """(post-trade price, post-trade REAL quote reserve in lamports) per print, the kit's own convention (arena_screen.post_price: 0.99 / 1.01 and V added)."""
    ib, so, tk, q, b, v = hr.isbuy[s], hr.sol[s], hr.tok[s], hr.q[s], hr.b[s], hr.v[s]
    qr = np.where(ib, q + so * 0.99, q - so * 1.01)
    bp = np.where(ib, b - tk, b + tk)
    return (qr + v) / np.where(bp > 0, bp, b), qr


class LanegRule:
    name = "p30_laneg"

    def __init__(self, only: list[str] | None = None) -> None:
        names = cell_names()
        if only is not None:
            bad = [n for n in only if n not in names]
            if bad:
                raise ValueError(f"unknown cells {bad}")
            names = [n for n in names if n in only]
        self._cells = names
        self._entries = sorted({n.split("|")[0] for n in names})
        self.counts: dict[str, int] = {}

    def cells(self) -> list[Cell]:
        return [Cell(n, cooldown_s=60.0, event_triggered=True) for n in self._cells]

    def start_block(self, state: RunState) -> None:
        self.state_ref = state  # the counters in state.scratch["c"] are read by the runner after the block
        state.scratch.clear()
        state.scratch.update(legacy=None, last={}, c={"hours": 0, "pools_ok_v": 0, "legacy_skipped": 0, "age_known": 0, "marks": 0, "pass_universe": 0, "gap_hours": 0})

    def triggers(self, hour: HourRows, state: RunState) -> list[tuple[str, int, ExitSpec]]:
        sc = state.scratch
        if "c" not in sc:
            self.start_block(state)
        c = sc["c"]
        c["hours"] += 1
        c["gap_hours"] += int(bool(state.gap_before))
        if sc["legacy"] is None:  # the block's first hour: every pool printing now has an unknown age
            sc["legacy"] = set(hour.pools.tolist())
        legacy = sc["legacy"]
        last: dict = sc["last"]
        prev = state.prev
        out: list[tuple[str, int, ExitSpec]] = []
        in_u = hour.in_universe
        marks = hour.T + 60 * np.arange(60)
        for i in np.flatnonzero(in_u):
            pool = str(hour.pools[i])
            c["pools_ok_v"] += 1
            if pool in legacy:
                c["legacy_skipped"] += 1
                continue
            s = hour.rows(int(i))
            first = min(state.first_seen.get(pool, 1 << 60), int(hour.bt[s][0]))
            if hour.T + 3600 - first < AGE_MIN_S or hour.T - first > AGE_MAX_S:
                continue
            c["age_known"] += 1
            p_cur, q_cur = post_price(hour, s)
            n_prev = 0
            if prev is not None and pool in prev.pool_index():
                sp = prev.rows(prev.pool_index()[pool])
                p_pre, q_pre = post_price(prev, sp)
                bt = np.concatenate([prev.bt[sp], hour.bt[s]])
                P = np.concatenate([p_pre, p_cur])
                Q = np.concatenate([q_pre, q_cur])
                so = np.concatenate([prev.sol[sp], hour.sol[s]])
                ib = np.concatenate([prev.isbuy[sp], hour.isbuy[s]])
                tr = np.concatenate([prev.trader[sp], hour.trader[s]])
                n_prev = len(p_pre)
            else:
                bt, P, Q, so, ib, tr = hour.bt[s], p_cur, q_cur, hour.sol[s], hour.isbuy[s], hour.trader[s]
            sl_cur = hour.sl[s]
            cg = np.concatenate([[0.0], np.cumsum(so)])
            cn = np.concatenate([[0.0], np.cumsum(np.where(ib, so, -so))])
            e = np.searchsorted(bt, marks, "right") - 1  # last row with block_time <= M
            st15 = np.searchsorted(bt, marks - 900, "right")
            st60 = np.searchsorted(bt, marks - 3600, "right")
            j15, j60 = st15 - 1, st60 - 1
            ok = (e >= n_prev) & (j15 >= 0) & (j60 >= 0)
            ok &= (marks - first >= AGE_MIN_S) & (marks - first <= AGE_MAX_S)
            ok &= (marks - bt[np.maximum(e, 0)] <= LAST_PRINT_S) & ((e + 1 - st15) >= PRINTS_15M)
            ok &= Q[np.maximum(e, 0)] >= QUOTE_FLOOR
            g15 = cg[e + 1] - cg[st15]
            g60 = cg[e + 1] - cg[st60]
            ok &= g60 >= VOL60_MIN
            for k in np.flatnonzero(ok):
                if len(np.unique(tr[st15[k]:e[k] + 1])) < SIGNERS_15M:
                    ok[k] = False
            c["marks"] += len(marks)
            c["pass_universe"] += int(ok.sum())
            if not ok.any():
                continue
            ks = np.flatnonzero(ok)
            r15 = P[e[ks]] / P[j15[ks]] - 1.0
            r60 = P[e[ks]] / P[j60[ks]] - 1.0
            flow = (cn[e[ks] + 1] - cn[st15[ks]]) / np.maximum(g15[ks], 1.0)
            accel = 4.0 * g15[ks] / np.maximum(g60[ks], 1.0)
            dec_slot = sl_cur[e[ks] - n_prev]
            for en in self._entries:
                shape, f, a = en.split("-")
                m = SHAPES[shape](r15, r60)
                if f == "F1":
                    m = m & (flow >= FLOW_MIN)
                if a == "A1":
                    m = m & (accel >= ACCEL_MIN)
                for t in np.flatnonzero(m):
                    k = ks[t]
                    key = (en, pool)
                    if marks[k] - last.get(key, -1 << 60) < THROTTLE_S:
                        continue
                    last[key] = int(marks[k])
                    for x in EXITS:
                        cn_ = f"{en}|{x}"
                        if cn_ in self._cells:
                            out.append((pool, int(dec_slot[t]), ExitSpec(EXITS[x].hold_s, EXITS[x].tp, EXITS[x].sl, EXITS[x].trail, cell=cn_)))
        return out
