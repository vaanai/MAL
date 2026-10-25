"""P28 pricing rules: CALL-SYNC cs_k12 + price-lag selector, and FLUSH f2 + SIZE-SKEW mirror flag. PAPER ONLY, exploration screen, no profit claim.
Declaration: docs/library/p28-new-screens/PRICING-DECLARE.md (sha256 beside it), written before any price. Every feature uses rows with slot <= the decision slot."""
import importlib.util
import zlib
from pathlib import Path

import numpy as np

from tools.arena_screen import Cell, ExitSpec

FEE = 0.0125
V_LO, V_HI = 17_500_000_000, 17_700_000_000
SH = 2 ** 32
AGE_S = 7200
K_DIST, GROSS_MIN = 12, 2.0e9
LAG_MAX = 0.02
EXIT = dict(hold_s=900.0, tp=1.12, sl=0.92)  # the FLUSH exits of hunt29 P4 (I1), reused unchanged
WIN_S, MIN_SIDE, RATIO_MAX = 1800, 10, 1.0 / 3.0


def _first_of_runs(key: np.ndarray, tr: np.ndarray) -> np.ndarray:
    """True at the first (in the original order) occurrence of every (key, trader) pair."""
    m = len(key)
    order = np.lexsort((np.arange(m), tr, key))
    ks, ts = key[order], tr[order]
    run0 = np.r_[True, (ks[1:] != ks[:-1]) | (ts[1:] != ts[:-1])]
    out = np.zeros(m, dtype=bool)
    out[order[run0]] = True
    return out


class CallSyncRule:
    name = "p28_callsync_cs12_lag"

    def __init__(self):
        self.stats = {"bursts": 0, "lag": 0}

    def cells(self):
        return [Cell("cs12_all", cooldown_s=300.0), Cell("cs12_lag", cooldown_s=300.0), Cell("cs12_in", cooldown_s=300.0)]

    def triggers(self, hour, state):
        sc = state.scratch
        first = sc.setdefault("first", {})
        n = hour.n
        if n == 0:
            return []
        npools = len(hour.pools)
        cnt = np.diff(hour.ptr)
        pid = np.repeat(np.arange(npools), cnt).astype(np.int64)
        isb = hour.isbuy.astype(bool)
        sol = hour.sol.astype(np.float64)
        tok = hour.tok.astype(np.float64)
        q = hour.q.astype(np.float64)
        b = hour.b.astype(np.float64)
        v = hour.v.astype(np.float64)
        bt = hour.bt.astype(np.int64)
        sl = hour.sl.astype(np.int64)
        tr = hour.trader.astype(np.int64)
        own_first = bt[hour.ptr[:-1].clip(max=n - 1)]
        pool_first = np.array([first.get(str(p), -1) for p in hour.pools], dtype=np.int64)
        pool_first = np.where(pool_first < 0, own_first, pool_first)
        with np.errstate(all="ignore"):
            okv = (v >= V_LO) & (v <= V_HI)
            cand = isb & okv & ((bt - pool_first[pid]) >= AGE_S)
        idx = np.flatnonzero(cand)
        out = []
        if len(idx):
            key = pid[idx] * SH + (sl[idx] // 2)
            chg = np.r_[True, key[1:] != key[:-1]]
            gid = np.cumsum(chg) - 1
            gstart = np.flatnonzero(chg)
            fo = _first_of_runs(key, tr[idx])
            cd = np.cumsum(fo)
            cg = np.cumsum(sol[idx])
            base_d = np.r_[0, cd][gstart][gid]
            base_g = np.r_[0.0, cg][gstart][gid]
            dist = cd - base_d
            gross = cg - base_g
            cond = (dist >= K_DIST) & (gross >= GROSS_MIN)
            ci = np.flatnonzero(cond)
            if len(ci):
                _, u = np.unique(gid[ci], return_index=True)
                dec = ci[u]  # first row in each bucket where both counts are met
                for d in dec:
                    r = int(idx[d])
                    r0 = int(idx[gstart[gid[d]]])
                    with np.errstate(all="ignore"):
                        spre = (q[r0] + v[r0]) / b[r0]
                        qp = q[r] + sol[r] * (1 - FEE)
                        bp = b[r] - tok[r]
                        spost = (qp + v[r]) / bp
                    m = spost / spre - 1.0
                    if not np.isfinite(m):
                        continue
                    pool = str(hour.pools[pid[r]])
                    slot = int(sl[r])
                    self.stats["bursts"] += 1
                    out.append((pool, slot, ExitSpec(cell="cs12_all", **EXIT)))
                    if m <= LAG_MAX:
                        self.stats["lag"] += 1
                        out.append((pool, slot, ExitSpec(cell="cs12_lag", **EXIT)))
                    else:
                        out.append((pool, slot, ExitSpec(cell="cs12_in", **EXIT)))
        for i in range(npools):  # remember first prints AFTER this hour's decisions
            if cnt[i]:
                first.setdefault(str(hour.pools[i]), int(bt[hour.ptr[i]]))
        return out


def _load_p4():
    p = Path(__file__).resolve().parent.parent / "hunt29-p4" / "p4rules.py"
    spec = importlib.util.spec_from_file_location("p28_hunt29_p4rules", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FlushVetoRule(_load_p4().P4Rule):
    """hunt29 I1 FLUSH cell f2 exactly as p4rules (imported), the other three ideas stubbed out, plus the SIZE-SKEW mirror flag per trigger (flags[(crc32(pool), slot)])."""

    name = "p28_flush_f2_sizeskew_flag"

    def __init__(self):
        super().__init__(skip=("d30", "d60", "d120"))
        self.flags = {}

    def cells(self):
        return [Cell("f2", cooldown_s=300.0)]

    def _wake(self, *a, **k):
        return []

    def _rankup(self, *a, **k):
        return []

    def _drought(self, *a, **k):
        return []

    def triggers(self, hour, state):
        out = [t for t in super().triggers(hour, state) if getattr(t[2], "cell", None) == "f2"]
        sc = state.scratch
        n = hour.n
        tails = sc.get("p28_tail") if sc.get("p28_tail_T") == hour.T - 3600 else {}
        if n:
            bt = hour.bt.astype(np.int64)
            sol = hour.sol.astype(np.float64)
            isb = hour.isbuy.astype(bool)
            sl = hour.sl.astype(np.int64)
            for pool, slot, _spec in out:
                i = hour.pool_index()[pool]
                s0, e0 = int(hour.ptr[i]), int(hour.ptr[i + 1])
                j = s0 + int(np.searchsorted(sl[s0:e0], slot, "right")) - 1
                t_dec = int(bt[j])
                lo = s0 + int(np.searchsorted(bt[s0:j + 1], t_dec - WIN_S, "left"))
                wb, ws_, wi = bt[lo:j + 1], sol[lo:j + 1], isb[lo:j + 1]
                tl = tails.get(pool)
                if tl is not None:
                    k = tl[0] >= t_dec - WIN_S
                    ws_, wi = np.r_[tl[1][k], ws_], np.r_[tl[2][k], wi]
                nb, ns = int(wi.sum()), int((~wi).sum())
                flag, ratio = 0, None
                if nb >= MIN_SIDE and ns >= MIN_SIDE:
                    sb, ss = float(ws_[wi].sum()), float(ws_[~wi].sum())
                    if ss > 0:
                        ratio = (sb / nb) / (ss / ns)
                        flag = int(ratio <= RATIO_MAX)
                self.flags[(zlib.crc32(pool.encode()), int(slot))] = (flag, nb, ns, ratio)
            new = {}
            for i in range(len(hour.pools)):
                s0, e0 = int(hour.ptr[i]), int(hour.ptr[i + 1])
                if e0 > s0:
                    k = bt[s0:e0] >= hour.T + 1800
                    if k.any():
                        new[str(hour.pools[i])] = (bt[s0:e0][k], sol[s0:e0][k], isb[s0:e0][k])
            sc["p28_tail"], sc["p28_tail_T"] = new, hour.T
        else:
            sc["p28_tail"], sc["p28_tail_T"] = {}, hour.T
        return out


def make_callsync():
    return CallSyncRule()


def make_flush():
    return FlushVetoRule()
