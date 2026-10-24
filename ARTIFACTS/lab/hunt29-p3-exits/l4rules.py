"""L4 SCREENS rules (hunt28, 2026-10-23): BREADTH, ACCUM, REVIVE, SELLABS as one arena_screen rule with 12 cells. PAPER ONLY, exploration screen, no profit claim.
Pre-declaration: DECLARE.md in this directory (sha256 in DECLARE.sha256). Every feature uses rows with slot <= the decision slot; windows are cut at the hour start."""
import numpy as np

from tools.arena_screen import Cell, ExitSpec

FEE = 0.0125
V_LO, V_HI = 17_500_000_000, 17_700_000_000
QMIN = 10e9
SH = 2 ** 32

BREADTH = {"b5": 5, "b8": 8, "b12": 12}
ACCUM = {"a3x3": (3, 3.0), "a4x6": (4, 6.0), "a3x10": (3, 10.0)}
REVIVE = {"r2h": 7200, "r6h": 21600, "r12h": 43200}
SELLABS = {"s1": 1.0, "s2": 2.0, "s4": 4.0}
COOL = {"b": 600.0, "a": 1800.0, "r": 3600.0, "s": 120.0}


class L4Rule:
    name = "l4_screens_v1"

    def cells(self):
        out = []
        for d in (BREADTH, ACCUM, REVIVE, SELLABS):
            for k in d:
                out.append(Cell(k, cooldown_s=COOL[k[0]]))
        return out

    # ------------------------------------------------------------------
    def triggers(self, hour, state):
        n = hour.n
        sc = state.scratch
        last = sc.setdefault("last", {})
        if n == 0:
            return []
        npools = len(hour.pools)
        cnt = np.diff(hour.ptr)
        pid = np.repeat(np.arange(npools), cnt)
        isb = hour.isbuy.astype(bool)
        sol = hour.sol.astype(np.float64)
        tok = hour.tok.astype(np.float64)
        q = hour.q.astype(np.float64)
        b = hour.b.astype(np.float64)
        v = hour.v.astype(np.float64)
        bt = hour.bt.astype(np.int64)
        tr = hour.trader.astype(np.int64)
        qp = np.where(isb, q + sol * (1 - FEE), q - sol / (1 - FEE))
        bp = np.where(isb, b - tok, b + tok)
        with np.errstate(all="ignore"):
            spot = (qp + v) / bp
            spre = (q + v) / b
        ok = (v >= V_LO) & (v <= V_HI) & (qp >= QMIN) & np.isfinite(spot)
        key = np.maximum.accumulate(pid.astype(np.int64) * SH + bt)
        cs_buy = np.concatenate([[0.0], np.cumsum(np.where(isb, sol, 0.0))])
        cs_sell = np.concatenate([[0.0], np.cumsum(np.where(isb, 0.0, sol))])
        cs_nb = np.concatenate([[0], np.cumsum(isb.astype(np.int64))])
        out = []
        out += self._breadth(hour, pid, isb, ok, key, bt, tr, spot, spre, cs_buy, cs_sell, cs_nb)
        out += self._accum(hour, pid, isb, ok, bt, tr, sol, spot, spre)
        out += self._revive(hour, pid, isb, ok, key, bt, tr, sol, spot, spre, last)
        out += self._sellabs(hour, pid, isb, ok, key, bt, tr, sol, q, spot, spre)
        # remember each pool's last print time (for REVIVE in later hours) AFTER all of this hour's decisions
        for i in range(npools):
            last[str(hour.pools[i])] = int(bt[hour.ptr[i + 1] - 1])
        return out

    # ------------------------------------------------------------------ BREADTH
    def _breadth(self, hour, pid, isb, ok, key, bt, tr, spot, spre, cs_buy, cs_sell, cs_nb):
        ws = np.searchsorted(key, key - 60, "left")
        idx = np.arange(len(key))
        nbuy = cs_nb[idx + 1] - cs_nb[ws]
        bv = (cs_buy[idx + 1] - cs_buy[ws]) / 1e9
        sv = (cs_sell[idx + 1] - cs_sell[ws]) / 1e9
        with np.errstate(all="ignore"):
            ret = spot / spre[ws] - 1.0
        base = isb & ok & (bv >= 1.0) & (sv <= 0.25 * bv) & (ret >= 0.03) & (ret <= 0.25) & (nbuy >= min(BREADTH.values()))
        out = []
        lastfire = {}
        for i in np.flatnonzero(base):
            w = slice(int(ws[i]), int(i) + 1)
            nd = len(np.unique(tr[w][isb[w]]))
            for cell, N in BREADTH.items():
                if nd >= N:
                    pool = str(hour.pools[pid[i]])
                    k = (cell, pool)
                    if k in lastfire and bt[i] - lastfire[k] < 300:
                        continue
                    lastfire[k] = int(bt[i])
                    out.append((pool, int(hour.sl[i]), ExitSpec(1800.0, sl=0.90, trail=0.88, cell=cell)))
        return out

    # ------------------------------------------------------------------ ACCUM
    def _accum(self, hour, pid, isb, ok, bt, tr, sol, spot, spre):
        W = 600
        gid_all = pid.astype(np.int64) * SH + tr
        _, ginv = np.unique(gid_all, return_inverse=True)
        ginv = ginv.astype(np.int64)
        bi = np.flatnonzero(isb)
        si = np.flatnonzero(~isb)
        if len(bi) == 0:
            return []
        ob = np.lexsort((bt[bi], ginv[bi]))
        bsort = bi[ob]
        kb = ginv[bsort] * SH + bt[bsort]
        os_ = np.lexsort((bt[si], ginv[si])) if len(si) else np.array([], dtype=np.int64)
        ssort = si[os_]
        ks = ginv[ssort] * SH + bt[ssort]
        ws = np.searchsorted(kb, kb - W, "left")
        ii = np.arange(len(kb))
        K = ii - ws + 1
        csb = np.concatenate([[0.0], np.cumsum(sol[bsort])])
        S = (csb[ii + 1] - csb[ws]) / 1e9
        if len(ks):
            nsell = np.searchsorted(ks, kb, "right") - np.searchsorted(ks, kb - W, "left")
        else:
            nsell = np.zeros(len(kb), dtype=np.int64)
        first_row = bsort[ws]
        with np.errstate(all="ignore"):
            ret = spot[bsort] / spre[first_row] - 1.0
        base = ok[bsort] & (nsell == 0) & (ret <= 0.25) & (K >= 3) & (S >= 3.0)
        out = []
        done = set()
        for j in np.flatnonzero(base):
            r = int(bsort[j])
            for cell, (kmin, smin) in ACCUM.items():
                if K[j] >= kmin and S[j] >= smin:
                    g = (cell, int(ginv[r]))
                    if g in done:
                        continue
                    done.add(g)
                    out.append((str(hour.pools[pid[r]]), int(hour.sl[r]), ExitSpec(3600.0, sl=0.88, trail=0.85, cell=cell)))
        return out

    # ------------------------------------------------------------------ REVIVE
    def _revive(self, hour, pid, isb, ok, key, bt, tr, sol, spot, spre, last):
        n = len(bt)
        gap = np.zeros(n, dtype=np.int64)
        gap[1:] = bt[1:] - bt[:-1]
        nonempty = np.flatnonzero(hour.ptr[1:] > hour.ptr[:-1])
        for i in nonempty:
            s0 = int(hour.ptr[i])
            lb = last.get(str(hour.pools[i]))
            gap[s0] = (int(bt[s0]) - lb) if lb is not None else 0
        brk = np.flatnonzero(gap >= min(REVIVE.values()))
        out = []
        for r0 in brk:
            p = int(pid[r0])
            end = int(hour.ptr[p + 1])
            e = int(np.searchsorted(key, key[r0] + 120, "right"))
            e = min(e, end)
            seen = set()
            bsum = ssum = 0.0
            fired = set()
            for j in range(int(r0), e):
                if isb[j]:
                    seen.add(int(tr[j]))
                    bsum += sol[j] / 1e9
                else:
                    ssum += sol[j] / 1e9
                if len(seen) >= 2 and bsum >= 1.0 and ssum <= 0.3 * bsum and ok[j] and isb[j]:
                    ret = spot[j] / spre[r0] - 1.0
                    if ret <= 0.40:
                        for cell, G in REVIVE.items():
                            if gap[r0] >= G and cell not in fired:
                                fired.add(cell)
                                out.append((str(hour.pools[p]), int(hour.sl[j]), ExitSpec(3600.0, sl=0.88, trail=0.85, cell=cell)))
                        break
        return out

    # ------------------------------------------------------------------ SELLABS
    def _sellabs(self, hour, pid, isb, ok, key, bt, tr, sol, q, spot, spre):
        smin = min(SELLABS.values()) * 1e9
        cand = np.flatnonzero((~isb) & (sol >= smin) & (sol >= 0.01 * q) & np.isfinite(spre))
        out = []
        for s in cand:
            p = int(pid[s])
            end = int(hour.ptr[p + 1])
            e = min(int(np.searchsorted(key, key[s] + 45, "right")), end)
            seen = set()
            bsum = 0.0
            for j in range(int(s) + 1, e):
                if not isb[j]:
                    if sol[j] >= 0.5e9:
                        break
                    continue
                if tr[j] == tr[s]:
                    continue
                seen.add(int(tr[j]))
                bsum += sol[j]
                if len(seen) >= 3 and bsum >= sol[s] and ok[j] and spot[j] <= spre[s]:
                    tp = float(min(max(spre[s] / spot[j], 1.04), 1.50))
                    for cell, M in SELLABS.items():
                        if sol[s] >= M * 1e9:
                            out.append((str(hour.pools[p]), int(hour.sl[j]), ExitSpec(600.0, tp=tp, sl=0.92, cell=cell)))
                    break
        return out
