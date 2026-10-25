#!/usr/bin/env python3
"""P28 counts-only screens (PRESET, CALL-SYNC, SIZE-SKEW, GRAD-GLUT). PAPER ONLY. Exploration blocks only. Reads no price and prices nothing.

    python -m tools.p28_counts --block /data/mal/clean-view/explore-0814 --start 2026-08-24T12 --end 2026-08-28T12 --out DIR [--workers 2]

Cells and bars are in docs/library/p28-new-screens/DECLARE.md (declared before this ran). Output: per-day trigger counts per cell and depth bands.
The only market quantity used is the PRE-trade effective quote Qeff = (quote_reserve + V) / 1e9, a depth statistic at the decision, not an outcome.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np

from tools import arena_screen as A

V_LO, V_HI = 17_500_000_000, 17_700_000_000
AGE_S = 7200
STAKES = (0.25, 0.5, 1.0)
IMPACT_MAX = 0.015
CS_K = {"cs_k5": 5, "cs_k8": 8, "cs_k12": 12}
SS_R = {"ss_r3": 3.0, "ss_r5": 5.0}
CELLS = ["pre_r1", *CS_K, *SS_R]
COOL = {"pre_r1": 300, "cs_k5": 300, "cs_k8": 300, "cs_k12": 300, "ss_r3": 1800, "ss_r5": 1800}
BIN = 300
NB = 6


def impact_share(qeff: list) -> dict:
    q = np.asarray(qeff, dtype=np.float64)
    if q.size == 0:
        return {str(s): None for s in STAKES}
    return {str(s): float(np.mean(s / (q + s) <= IMPACT_MAX)) for s in STAKES}


def cooldown_filter(pool_ids: np.ndarray, times: np.ndarray, cool: int) -> np.ndarray:
    """keep mask over triggers sorted by (pool, time): drop a trigger within `cool` s of the last KEPT trigger of the same pool."""
    keep = np.zeros(len(times), dtype=bool)
    last_p, last_t = None, 0
    for i in range(len(times)):
        if pool_ids[i] != last_p or times[i] - last_t >= cool:
            keep[i] = True
            last_p, last_t = pool_ids[i], times[i]
    return keep


class Counter:
    def __init__(self) -> None:
        self.first: dict = {}  # pool -> first bt in block
        self.bins: dict = {}  # pool -> {bin: [nb, sb, ns, ss, qeff_last]}
        self.t0 = None
        self.trig = {c: [] for c in CELLS}  # (day, qeff)
        self.veto = []
        self.base_buys = 0
        self.base_milli = 0
        self.base_tenth = 0
        self.glut_hours = []  # (hour, n_new_in_band)

    # ------------------------------------------------------------------
    def hour(self, hr) -> None:
        n = hr.n
        hstr = hr.hour
        day = hstr[:10]
        if self.t0 is None:
            self.t0 = hr.T
        age_ok_after = hr.T  # first[] holds earlier hours only until updated below
        if n == 0:
            self.glut_hours.append((hstr, 0 if hr.T - self.t0 >= 86400 else None))
            return
        cnt = np.diff(hr.ptr)
        pid = np.repeat(np.arange(len(hr.pools)), cnt)
        isb = hr.isbuy.astype(bool)
        sol = hr.sol.astype(np.float64)
        q = hr.q.astype(np.float64)
        v = hr.v.astype(np.float64)
        bt = hr.bt.astype(np.int64)
        sl = hr.sl.astype(np.int64)
        tr = hr.trader.astype(np.int64)
        with np.errstate(invalid="ignore"):
            okv = (v >= V_LO) & (v <= V_HI)
        qeff = (q + v) / 1e9
        first_hr = np.array([self.first.get(str(p), -1) for p in hr.pools], dtype=np.int64)
        own_first = bt[hr.ptr[:-1].clip(max=n - 1)]
        pool_first = np.where(first_hr < 0, own_first, first_hr)
        aged = (bt - pool_first[pid]) >= AGE_S
        # GRAD-GLUT
        new_in_band = 0
        for i in range(len(hr.pools)):
            if first_hr[i] < 0 and cnt[i] and okv[hr.ptr[i]]:
                new_in_band += 1
        self.glut_hours.append((hstr, new_in_band if hr.T - self.t0 >= 86400 else None))
        # PRESET base rate and surge
        bmask = okv & isb
        sb_ = sol[bmask].astype(np.int64)
        self.base_buys += int(bmask.sum())
        self.base_milli += int((sb_ % 10**6 == 0).sum())
        self.base_tenth += int((sb_ % 10**8 == 0).sum())
        rmask = bmask & aged & (sol.astype(np.int64) % 10**8 == 0)
        self._preset(rmask, pid, bt, tr, qeff, day, hr)
        self._callsync(bmask & aged, pid, bt, sl, tr, sol, qeff, day, hr)
        self._skew(okv, aged, isb, pid, bt, sol, qeff, day, hr)
        for i in range(len(hr.pools)):
            if cnt[i]:
                self.first.setdefault(str(hr.pools[i]), int(bt[hr.ptr[i]]))

    # ------------------------------------------------------------------
    def _preset(self, rmask, pid, bt, tr, qeff, day, hr) -> None:
        idx = np.flatnonzero(rmask)
        if idx.size < 3:
            return
        out = []
        for p in np.unique(pid[idx]):
            ii = idx[pid[idx] == p]
            if ii.size < 3:
                continue
            ii = ii[np.argsort(bt[ii], kind="stable")]
            for j in range(ii.size):
                w = ii[(bt[ii] >= bt[ii[j]] - 60) & (bt[ii] <= bt[ii[j]])]
                if np.unique(tr[w]).size >= 3:
                    out.append((p, int(bt[ii[j]]), float(qeff[ii[j]])))
        self._emit("pre_r1", out, day)

    def _callsync(self, bmask, pid, bt, sl, tr, sol, qeff, day, hr) -> None:
        idx = np.flatnonzero(bmask)
        if idx.size == 0:
            return
        key = pid[idx].astype(np.int64) * (1 << 34) + (sl[idx] // 2)
        order = np.argsort(key, kind="stable")
        idx, key = idx[order], key[order]
        starts = np.r_[0, np.flatnonzero(key[1:] != key[:-1]) + 1]
        ends = np.r_[starts[1:], idx.size]
        res = {c: [] for c in CS_K}
        for s, e in zip(starts, ends):
            if e - s < 5:
                continue
            ii = idx[s:e]
            nd = np.unique(tr[ii]).size
            if nd < 5 or sol[ii].sum() < 2.0e9:
                continue
            last = ii[-1]
            for c, k in CS_K.items():
                if nd >= k:
                    res[c].append((int(pid[last]), int(bt[last]), float(qeff[ii[0]])))
        for c, o in res.items():
            self._emit(c, o, day)

    def _skew(self, okv, aged, isb, pid, bt, sol, qeff, day, hr) -> None:
        idx = np.flatnonzero(okv)
        if idx.size == 0:
            return
        b = bt[idx] // BIN
        key = pid[idx].astype(np.int64) * (1 << 32) + (b - b.min())
        uk, inv = np.unique(key, return_inverse=True)
        nbuy = np.bincount(inv, weights=isb[idx].astype(float))
        sbuy = np.bincount(inv, weights=np.where(isb[idx], sol[idx], 0.0))
        nsel = np.bincount(inv, weights=(~isb[idx]).astype(float))
        ssel = np.bincount(inv, weights=np.where(~isb[idx], sol[idx], 0.0))
        lastrow = np.zeros(len(uk), dtype=np.int64)
        lastrow[inv] = idx  # idx ascending, so the last write is the latest row of the bin
        bmin = int(b.min())
        pools_in = {}
        for k_i, k in enumerate(uk):
            p = int(k >> 32)
            bi = int(k & 0xFFFFFFFF) + bmin
            d = self.bins.setdefault(str(hr.pools[p]), {})
            d[bi] = [nbuy[k_i], sbuy[k_i], nsel[k_i], ssel[k_i], qeff[lastrow[k_i]], bool(aged[lastrow[k_i]])]
            pools_in.setdefault(str(hr.pools[p]), []).append(bi)
        outs = {c: [] for c in SS_R}
        veto = 0
        for pname, bl in pools_in.items():
            d = self.bins[pname]
            for bi in sorted(bl):
                w = [d[x] for x in range(bi - NB + 1, bi + 1) if x in d]
                nb_ = sum(x[0] for x in w)
                ns_ = sum(x[2] for x in w)
                if nb_ < 10 or ns_ < 10 or not d[bi][5]:
                    continue
                r = (sum(x[1] for x in w) / nb_) / max(sum(x[3] for x in w) / ns_, 1.0)
                tt = (bi + 1) * BIN
                for c, thr in SS_R.items():
                    if r >= thr:
                        outs[c].append((hash(pname) & 0x7FFFFFFF, tt, float(d[bi][4])))
                if r <= 1 / 3:
                    veto += 1
            for x in [x for x in d if x < max(bl) - NB]:
                del d[x]
        self.veto.append((day, veto))
        for c, o in outs.items():
            self._emit(c, o, day)

    def _emit(self, cell, rows, day) -> None:
        if not rows:
            return
        rows.sort()
        p = np.array([r[0] for r in rows])
        t = np.array([r[1] for r in rows])
        keep = cooldown_filter(p, t, COOL[cell])
        for r, k in zip(rows, keep):
            if k:
                self.trig[cell].append((datetime.fromtimestamp(r[1], timezone.utc).strftime("%Y-%m-%d"), r[2]))


def summarize(c: Counter, name: str) -> dict:
    out = {"block": name, "cells": {}}
    for cell in CELLS:
        rows = c.trig[cell]
        days: dict = {}
        for d, _ in rows:
            days[d] = days.get(d, 0) + 1
        nd = max(len(days), 1)
        out["cells"][cell] = {"triggers": len(rows), "days_with_any": len(days), "per_day_mean_over_block_days": len(rows) / 4.0,
                              "per_day": dict(sorted(days.items())), "one_way_impact_le_1p5pct_share": impact_share([q for _, q in rows]),
                              "qeff_quantiles": [float(x) for x in np.quantile([q for _, q in rows], [0.1, 0.5, 0.9])] if rows else None}
    out["preset_base"] = {"in_universe_buys": c.base_buys, "milli_multiple_share": (c.base_milli / c.base_buys) if c.base_buys else None,
                          "tenth_multiple_share": (c.base_tenth / c.base_buys) if c.base_buys else None}
    out["ss_veto_flags_by_day"] = {}
    for d, n in c.veto:
        out["ss_veto_flags_by_day"][d] = out["ss_veto_flags_by_day"].get(d, 0) + n
    series = [(h, n) for h, n in c.glut_hours if n is not None]
    ns = [n for _, n in series]
    flags = {"ge2x": 0, "le0p5x": 0, "evaluated": 0}
    for i in range(24, len(ns)):
        med = float(np.median(ns[i - 24:i]))
        if med <= 0:
            continue
        flags["evaluated"] += 1
        flags["ge2x"] += ns[i] >= 2 * med
        flags["le0p5x"] += ns[i] <= 0.5 * med
    out["glut"] = {"hours": len(ns), "new_in_band_per_hour_p10_p50_p90": [float(x) for x in np.quantile(ns, [0.1, 0.5, 0.9])] if ns else None, **flags}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-hours", type=int, default=96)
    a = ap.parse_args()
    kind = A.check_block_path(a.block, True)
    assert kind == "clean", kind
    out_dir = A.check_out_dir(a.out)
    os.makedirs(out_dir, exist_ok=True)
    t0 = datetime.strptime(a.start, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(a.end, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    files = [(t, f) for t, f in A.discover(a.block, "clean") if t0 <= t < t1][: a.max_hours]
    vmap = A.VMap(A.check_vmap_paths(A.VMAP_DEFAULT))
    name = os.path.basename(a.block.rstrip("/"))
    c = Counter()
    for hr in A.iter_hours(files, name, "backfill", vmap, a.workers):
        c.hour(hr)
    res = summarize(c, name)
    res["hours"] = len(files)
    with open(os.path.join(out_dir, f"p28_counts_{name}.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "cells"}, indent=1)[:1500])
    for cell, s in res["cells"].items():
        print(cell, s["triggers"], s["days_with_any"], s["one_way_impact_le_1p5pct_share"])


if __name__ == "__main__":
    main()
