"""P4 NEW SCREENS rules (push 10-24): FLUSH, WAKE, RANKUP, DROUGHT as one arena_screen rule with 12 cells. PAPER ONLY, exploration screen, no profit claim.
Pre-declaration: DECLARE.md in this directory (sha256 in DECLARE.sha256). Every feature uses rows with slot <= the decision slot."""
import numpy as np

from tools.arena_screen import Cell, ExitSpec

FEE = 0.0125
V_LO, V_HI = 17_500_000_000, 17_700_000_000
SH = 2 ** 32

FLUSH = {"f2": (2, 0.5e9), "f3": (3, 1.0e9), "f5": (5, 2.0e9)}
WAKE = {"w1": 3600, "w6": 21600, "w24": 86400}
RANKUP = {"k10": 10, "k25": 25, "k50": 50}
DROUGHT = {"d30": (1800, 10, 3.0e9), "d60": (3600, 20, 6.0e9), "d120": (7200, 40, 12.0e9)}
COOL = {"f": 300.0, "w": 21600.0, "k": 1800.0, "d": 3600.0}
KEEP_HOURS = 48


class P4Rule:
    name = "p4_screens_v1"

    def cells(self):
        return [Cell(k, cooldown_s=COOL[k[0]]) for d in (FLUSH, WAKE, RANKUP, DROUGHT) for k in d]

    # ------------------------------------------------------------------
    def triggers(self, hour, state):
        n = hour.n
        sc = state.scratch
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
        okv = (v >= V_LO) & (v <= V_HI) & np.isfinite(spot)
        key = np.maximum.accumulate(pid.astype(np.int64) * SH + bt)
        first = sc.setdefault("first", {})
        pool_first = np.array([first.get(str(p), -1) for p in hour.pools], dtype=np.int64)
        own_first = bt[hour.ptr[:-1].clip(max=n - 1)]
        pool_first = np.where(pool_first < 0, own_first, pool_first)
        out = []
        out += self._flush(hour, state, pid, isb, okv, qp, key, bt, tr, sol, tok, pool_first)
        out += self._wake(hour, sc, pid, isb, okv, q, bt, sol, spot)
        out += self._rankup(hour, sc, pid, isb, okv, qp, bt, sol, spot, spre)
        out += self._drought(hour, sc, pid, isb, okv, qp, bt, sol, spot)
        # remember first/last print per pool AFTER all of this hour's decisions
        last = sc.setdefault("last", {})
        for i in range(npools):
            if cnt[i]:
                first.setdefault(str(hour.pools[i]), int(bt[hour.ptr[i]]))
                last[str(hour.pools[i])] = int(bt[hour.ptr[i + 1] - 1])
        return out

    # ------------------------------------------------------------------ FLUSH
    def _flush(self, hour, state, pid, isb, okv, qp, key, bt, tr, sol, tok, pool_first):
        sc = state.scratch
        gid = sc.setdefault("gid", {})
        gp = np.empty(len(hour.pools), dtype=np.int64)
        for i, p in enumerate(hour.pools):
            p = str(p)
            g = gid.get(p)
            if g is None:
                g = gid[p] = len(gid)
            gp[i] = g
        n = hour.n
        pk = gp[pid] * SH + tr
        order = np.lexsort((np.arange(n), pk))
        pk_s = pk[order]
        isb_s = isb[order]
        bs = np.where(isb_s, sol[order], 0.0)
        bk = np.where(isb_s, tok[order], 0.0)
        cs_s = np.concatenate([[0.0], np.cumsum(bs)])
        cs_t = np.concatenate([[0.0], np.cumsum(bk)])
        chg = np.r_[True, pk_s[1:] != pk_s[:-1]]
        starts = np.flatnonzero(chg)
        gi = np.cumsum(chg) - 1
        gkey = pk_s[starts]
        ends = np.r_[starts[1:], n]
        stk, stS, stT, stH = sc.get("fl_k"), sc.get("fl_S"), sc.get("fl_T"), sc.get("fl_H")
        if stk is None:
            stk = np.zeros(0, np.int64)
            stS = np.zeros(0)
            stT = np.zeros(0)
            stH = np.zeros(0, np.int64)
        if len(stk):
            pos = np.searchsorted(stk, gkey)
            posc = np.minimum(pos, len(stk) - 1)
            found = (pos < len(stk)) & (stk[posc] == gkey)
            cS = np.where(found, stS[posc], 0.0)
            cT = np.where(found, stT[posc], 0.0)
        else:
            cS = np.zeros(len(gkey))
            cT = np.zeros(len(gkey))
        idx = np.arange(n)
        Sb_s = cS[gi] + cs_s[idx] - cs_s[starts[gi]]
        Tb_s = cT[gi] + cs_t[idx] - cs_t[starts[gi]]
        Sb = np.empty(n)
        Tb = np.empty(n)
        Sb[order] = Sb_s
        Tb[order] = Tb_s
        with np.errstate(all="ignore"):
            lc = (~isb) & (Tb > 0) & (sol >= 1e8) & ((sol / tok) <= 0.80 * (Sb / Tb))
        sc['_lc_dbg'] = lc  # debug handle for the unit check only
        # ---- triggers
        cs_lc = np.concatenate([[0], np.cumsum(lc.astype(np.int64))])
        cs_lcs = np.concatenate([[0.0], np.cumsum(np.where(lc, sol, 0.0))])
        ia = np.arange(n)
        wA = np.searchsorted(key, key - 90, "left")
        wB = np.searchsorted(key, key - 10, "right")
        wB = np.minimum(wB, ia)  # rows strictly before the decision row
        wA = np.minimum(wA, wB)
        cntw = cs_lc[wB] - cs_lc[wA]
        sumw = cs_lcs[wB] - cs_lcs[wA]
        recent = cs_lc[ia] - cs_lc[wB]  # loss-cut rows between the window end and the decision row (exclusive of the decision row)
        age = bt - pool_first[pid]
        base = isb & okv & (qp >= 10e9) & (age >= 3600) & (sol >= 1e8) & (cntw >= 2) & (sumw >= 0.5e9) & (recent == 0)
        out = []
        lastfire = sc.setdefault("fl_lastfire", {})
        for i in np.flatnonzero(base):
            a, e = int(wA[i]), int(wB[i])
            m = lc[a:e]
            sellers = tr[a:e][m]
            if tr[i] in set(sellers.tolist()):
                continue
            nd = len(np.unique(sellers))
            ssum = float(sol[a:e][m].sum())
            pool = str(hour.pools[pid[i]])
            for cell, (N, S) in FLUSH.items():
                if nd >= N and ssum >= S:
                    k = (cell, pool)
                    if k in lastfire and bt[i] - lastfire[k] < 300:
                        continue
                    lastfire[k] = int(bt[i])
                    out.append((pool, int(hour.sl[i]), ExitSpec(900.0, tp=1.12, sl=0.92, cell=cell)))
        # ---- update pair state AFTER the decisions
        gS = cs_s[ends] - cs_s[starts]
        gT = cs_t[ends] - cs_t[starts]
        hasb = gS > 0
        nk = np.concatenate([stk, gkey[hasb]])
        nS = np.concatenate([stS, gS[hasb]])
        nT = np.concatenate([stT, gT[hasb]])
        nH = np.concatenate([stH, np.full(int(hasb.sum()), hour.idx, dtype=np.int64)])
        o = np.argsort(nk, kind="stable")
        nk, nS, nT, nH = nk[o], nS[o], nT[o], nH[o]
        if len(nk):
            st = np.flatnonzero(np.r_[True, nk[1:] != nk[:-1]])
            uk = nk[st]
            uS = np.add.reduceat(nS, st)
            uT = np.add.reduceat(nT, st)
            uH = np.maximum.reduceat(nH, st)
            keep = uH >= hour.idx - KEEP_HOURS
            sc["fl_k"], sc["fl_S"], sc["fl_T"], sc["fl_H"] = uk[keep], uS[keep], uT[keep], uH[keep]
        else:
            sc["fl_k"], sc["fl_S"], sc["fl_T"], sc["fl_H"] = nk, nS, nT, nH
        return out

    # ------------------------------------------------------------------ WAKE
    def _wake(self, hour, sc, pid, isb, okv, q, bt, sol, spot):
        n = hour.n
        last = sc.get("last", {})
        gap = np.full(n, -1, dtype=np.int64)  # -1 = no previous print known
        gap[1:] = bt[1:] - bt[:-1]
        for i in np.flatnonzero(hour.ptr[1:] > hour.ptr[:-1]):
            s0 = int(hour.ptr[i])
            lb = last.get(str(hour.pools[i]))
            gap[s0] = (int(bt[s0]) - lb) if lb is not None else -1
        base = isb & okv & (q <= 2e9) & (sol >= 5e7) & (gap >= min(WAKE.values()))
        out = []
        for i in np.flatnonzero(base):
            pool = str(hour.pools[pid[i]])
            for cell, G in WAKE.items():
                if gap[i] >= G:
                    out.append((pool, int(hour.sl[i]), ExitSpec(21600.0, tp=1.50, cell=cell)))
        return out

    # ------------------------------------------------------------------ RANKUP
    def _rankup(self, hour, sc, pid, isb, okv, qp, bt, sol, spot, spre):
        T = hour.T
        npools = len(hour.pools)
        inu = hour.in_universe
        row_in = inu[pid]
        binx = np.clip((bt - T) // 60, 0, 59).astype(np.int64)
        w = np.where(row_in, sol, 0.0) / 1e9
        wb = np.where(row_in & isb, sol, 0.0) / 1e9
        ws_ = np.where(row_in & ~isb, sol, 0.0) / 1e9
        flat = pid.astype(np.int64) * 60 + binx
        tot = np.bincount(flat, weights=w, minlength=npools * 60).reshape(npools, 60)
        bv = np.bincount(flat, weights=wb, minlength=npools * 60).reshape(npools, 60)
        sv = np.bincount(flat, weights=ws_, minlength=npools * 60).reshape(npools, 60)
        ctot = np.concatenate([np.zeros((npools, 1)), np.cumsum(tot, axis=1)], axis=1)
        cbv = np.concatenate([np.zeros((npools, 1)), np.cumsum(bv, axis=1)], axis=1)
        csv = np.concatenate([np.zeros((npools, 1)), np.cumsum(sv, axis=1)], axis=1)
        names = hour.pools
        name_rank = np.argsort(np.argsort(names.astype(str)))
        cand_idx = np.flatnonzero(inu)
        last_top = sc.setdefault("rk_last", {c: {} for c in RANKUP})
        out = []
        for m in range(9, 59):
            R = ctot[:, m + 1] - ctot[:, m - 9]
            sub = cand_idx[R[cand_idx] > 0]
            if len(sub) == 0:
                continue
            o = sub[np.lexsort((name_rank[sub], -R[sub]))]
            B = T + 60 * (m + 1)
            for cell, K in RANKUP.items():
                top = o[:K]
                lt = last_top[cell]
                if m >= 10:
                    for p in top:
                        nm = str(names[p])
                        if lt.get(nm, -10 ** 12) >= B - 7200:
                            continue
                        wbuy = cbv[p, m + 1] - cbv[p, m - 9]
                        wsell = csv[p, m + 1] - csv[p, m - 9]
                        if wbuy < 3.0 or wbuy - wsell <= 0:
                            continue
                        s0, e0 = int(hour.ptr[p]), int(hour.ptr[p + 1])
                        btp = bt[s0:e0]
                        a0 = s0 + int(np.searchsorted(btp, T + 60 * (m - 9), "left"))
                        j0 = s0 + int(np.searchsorted(btp, B, "left"))
                        if a0 >= e0 or not np.isfinite(spre[a0]):
                            continue
                        j = j0
                        while j < e0 and bt[j] < B + 60:
                            if isb[j] and okv[j] and qp[j] >= 30e9:
                                break
                            j += 1
                        if j >= e0 or bt[j] >= B + 60:
                            continue
                        if spre[j] / spre[a0] - 1.0 > 0.30:
                            continue
                        out.append((nm, int(hour.sl[j]), ExitSpec(1800.0, sl=0.90, trail=0.88, cell=cell)))
                for p in top:
                    lt[str(names[p])] = B
        return out

    # ------------------------------------------------------------------ DROUGHT
    def _drought(self, hour, sc, pid, isb, okv, qp, bt, sol, spot):
        n = hour.n
        npools = len(hour.pools)
        car = sc.setdefault("dr", {})
        first = sc.get("first", {})
        ps = hour.ptr[:-1]
        c_clock = np.zeros(npools, dtype=np.int64)
        c_nb = np.zeros(npools)
        c_vs = np.zeros(npools)
        for i in range(npools):
            c = car.get(str(hour.pools[i]))
            if c is None:
                f = first.get(str(hour.pools[i]))
                c_clock[i] = f if f is not None else (int(bt[ps[i]]) if hour.ptr[i + 1] > ps[i] else 0)
            else:
                c_clock[i], c_nb[i], c_vs[i] = c
        idx = np.arange(n)
        lsa = np.maximum.accumulate(np.where(~isb, idx, -1))
        pstart = ps[pid]
        has = lsa >= pstart
        cb = np.concatenate([[0.0], np.cumsum(isb.astype(np.float64))])
        cv = np.concatenate([[0.0], np.cumsum(np.where(isb, sol, 0.0))])
        lsc = np.where(has, lsa, 0)
        nb_s = np.where(has, cb[idx] - cb[lsc + 1], c_nb[pid] + cb[idx] - cb[pstart])
        vs_s = np.where(has, cv[idx] - cv[lsc + 1], c_vs[pid] + cv[idx] - cv[pstart])
        clock = np.where(has, bt[lsc], c_clock[pid])
        dur = bt - clock
        dmin = min(v[0] for v in DROUGHT.values())
        base = isb & okv & (qp >= 10e9) & (dur >= dmin) & (nb_s >= 10) & (vs_s >= 3.0e9)
        out = []
        lastfire = sc.setdefault("dr_lastfire", {})
        for i in np.flatnonzero(base):
            pool = str(hour.pools[pid[i]])
            for cell, (D, nn, S) in DROUGHT.items():
                if dur[i] >= D and nb_s[i] >= nn and vs_s[i] >= S:
                    k = (cell, pool)
                    if k in lastfire and bt[i] - lastfire[k] < 3600:
                        continue
                    lastfire[k] = int(bt[i])
                    out.append((pool, int(hour.sl[i]), ExitSpec(1800.0, sl=0.90, cell=cell)))
        # ---- update carry AFTER the decisions
        for i in range(npools):
            s0, e0 = int(hour.ptr[i]), int(hour.ptr[i + 1])
            if e0 <= s0:
                continue
            sells = np.flatnonzero(~isb[s0:e0])
            if len(sells):
                ls = s0 + int(sells[-1])
                after = slice(ls + 1, e0)
                car[str(hour.pools[i])] = (int(bt[ls]), float(isb[after].sum()), float(sol[after][isb[after]].sum()))
            else:
                car[str(hour.pools[i])] = (int(c_clock[i]), float(c_nb[i] + isb[s0:e0].sum()), float(c_vs[i] + sol[s0:e0][isb[s0:e0]].sum()))
        return out
