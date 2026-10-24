"""L1 FLOOR-OPTION v2 (iter-r1 spec.py sha256 10ee6892...) as an arena_screen rule. PAPER ONLY, in-sample screen, no profit claim.

Trigger: a canonical PumpSwap SELL print of a WSOL pool (V in [17.5e9,17.7e9]) with pool age (since the mint's complete row) in [24 h, 200 h], at least one other print in the
previous 6 h, vol24 >= 1 SOL, r6 = post-trade spot / post-trade spot of the last print <= bt-6 h below 0.80, and the real quote vault AFTER the print <= 0.5 SOL.
CAUSAL READING (the kit decides on rows <= the decision slot): the post-trade vault of print i is derived from print i's own row, q_post = q_pre -/+ sol (sell: sol / (1 - 0.0125),
buy: sol * (1 - 0.0125)), the same approximation iter-r1 used for a pool's last print; iter-r1 read the next row's pre-trade reserve for the others. This is the one implementation
difference and it is disclosed in REPORT.md.
Universe tables (mint -> complete time, create rows, mayhem) come from aux_<block>.json built from the block's migrations / creates (with the DECLARE lookback for oracle-insample).
oracle-live has no migration or create rows: there the complete time is proxied by the pool's first print (first print >= block start + 3 h), no create / mayhem filter.
Exit: take profit at 1.5 x the post-buy mark, else time exit 24 h. No stop. One open position per pool (cooldown 60 s after the exit decision). Uncapped book.
"""
import json
import collections
import numpy as np

from tools.arena_screen import Cell, ExitSpec

FEE = 0.0125
AGE_LO, AGE_HI = 24 * 3600, 200 * 3600
ALIVE_S = 6 * 3600
V_LO, V_HI = 17_500_000_000, 17_700_000_000
Q_STAR = 0.5e9


class L1Rule:
    name = "l1_floor_option_v2"

    def __init__(self, aux=None, proxy_first_print=False, aux_dir=None):
        self.aux_dir = aux_dir
        self.proxy = bool(proxy_first_print)
        self._blk = None
        self.mig, self.have, self.may = {}, set(), set()
        if aux:
            self._load(aux)
        self.spec = ExitSpec(86400.0, tp=1.5)

    def _load(self, aux):
        d = json.load(open(aux))
        self.mig, self.have, self.may = d["mig"], set(d["have"]), set(d["mayhem"])

    def _select(self, block):
        """Per-block universe tables (the kit hands the block name in state.block)."""
        if self.aux_dir is None or block == self._blk:
            return
        self._blk = block
        b = str(block)
        key = "fresh0903" if "fresh-0903" in b else "olive" if "live" in b else "oinsample" if "insample" in b else "fastpool" if "fast-pool" in b else "explore"  # P7: ONLY this key is added
        self.proxy = key == "olive"
        self.mig, self.have, self.may = ({}, set(), set()) if self.proxy else (None, None, None)
        if not self.proxy:
            self._load(f"{self.aux_dir}/aux_{key}.json")

    def cells(self):
        return [Cell("l1", cooldown_s=60.0, event_triggered=True)]

    # ------------------------------------------------------------------
    def triggers(self, hour, state):
        self._select(state.block)
        sc = state.scratch
        hist = sc.setdefault("hist", collections.deque(maxlen=26))
        first = sc.setdefault("first", {})
        uni = sc.setdefault("uni", {})
        canon = sc.setdefault("canon", {})
        if "t0" not in sc:
            sc["t0"] = hour.T
        n = hour.n
        hist.append(hour)
        if n == 0:
            return []
        npools = len(hour.pools)
        cnt = np.diff(hour.ptr)
        pid = np.repeat(np.arange(npools), cnt)
        # --- universe per pool (decided once, from the pool's first print and the mint's complete row)
        newp = [i for i in range(npools) if str(hour.pools[i]) not in first]
        newp.sort(key=lambda i: int(hour.sl[hour.ptr[i]]))
        for i in newp:
            pool = str(hour.pools[i])
            r0 = int(hour.ptr[i])
            bt0 = int(hour.bt[r0])
            first[pool] = bt0
            mint = str(hour.mints[i])
            v0 = hour.v[r0]
            ok = bool(V_LO <= v0 <= V_HI)
            M = None
            if ok:
                if self.proxy:
                    ok = bt0 >= sc["t0"] + 3 * 3600 and pool not in state.first_seen
                    M = bt0
                else:
                    M = self.mig.get(mint)
                    ok = M is not None and mint in self.have and mint not in self.may and (M - 5 <= bt0 <= M + 600)
            if ok:
                if mint in canon and canon[mint] != pool:
                    ok = False
                else:
                    canon[mint] = pool
            uni[pool] = M if ok else None
        Mp = np.array([uni.get(str(p)) if uni.get(str(p)) is not None else np.nan for p in hour.pools], dtype=np.float64)
        Mrow = Mp[pid]
        age = hour.bt - Mrow
        isb = hour.isbuy.astype(bool)
        qpost = np.where(isb, hour.q + hour.sol * (1 - FEE), hour.q - hour.sol / (1 - FEE))
        cand = (~isb) & np.isfinite(Mrow) & (age >= AGE_LO) & (age <= AGE_HI) & (qpost <= Q_STAR) & (hour.v >= V_LO) & (hour.v <= V_HI)
        ci = np.flatnonzero(cand)
        out = []
        if len(ci) == 0:
            return out
        for p in np.unique(pid[ci]):
            pool = str(hour.pools[p])
            parts = []
            for h in hist:
                k = h.pool_index().get(pool)
                if k is None:
                    continue
                s = h.rows(k)
                parts.append((h, s))
            bt = np.concatenate([h.bt[s] for h, s in parts]).astype(np.int64)
            sol = np.concatenate([h.sol[s] for h, s in parts]).astype(np.float64)
            tok = np.concatenate([h.tok[s] for h, s in parts]).astype(np.float64)
            q = np.concatenate([h.q[s] for h, s in parts]).astype(np.float64)
            b = np.concatenate([h.b[s] for h, s in parts]).astype(np.float64)
            v = np.concatenate([h.v[s] for h, s in parts]).astype(np.float64)
            ib = np.concatenate([h.isbuy[s] for h, s in parts]).astype(bool)
            sl = np.concatenate([h.sl[s] for h, s in parts])
            qp = np.where(ib, q + sol * (1 - FEE), q - sol / (1 - FEE))
            bp = np.where(ib, b - tok, b + tok)
            spot = (qp + v) / bp
            off = sum(len(h.bt[s]) for h, s in parts[:-1])  # the current hour is the last part
            rows = np.flatnonzero(cand & (pid == p))
            csol = np.concatenate([[0.0], np.cumsum(sol)])
            for r in rows:
                j = off + (r - int(hour.ptr[p]))
                if j <= 0 or bt[j - 1] < bt[j] - ALIVE_S:
                    continue
                j24 = int(np.searchsorted(bt, bt[j] - 86400, "left"))
                vol24 = (csol[j + 1] - csol[j24]) / 1e9
                if vol24 < 1.0:
                    continue
                j6 = int(np.searchsorted(bt[: j + 1], bt[j] - ALIVE_S, "right")) - 1
                if j6 < 0:
                    continue
                if not (spot[j] / spot[j6] < 0.80):
                    continue
                out.append((pool, int(hour.sl[r]), self.spec))
        return out
