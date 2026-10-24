"""P3 EXITS rule (hunt29): one arena_screen rule, 34 cells over three entry families. PAPER ONLY, exploration screen, no profit claim. Declaration: DECLARE.md (sha256 in DECLARE.sha256).
Entries: FLOOR v2 (l1_rule.py unchanged), G4 v1 kit form (this file, `_g4`), REVIVE (l4rules.py `_revive` unchanged). Exits: p3exits.py (cell names carry the exit)."""
import numpy as np

from tools.arena_screen import Cell, ExitSpec
import l1_rule
import l4rules

V_LO, V_HI = 17_500_000_000, 17_700_000_000
FEE = 0.0125
H24, H6, H26 = 24 * 3600, 6 * 3600, 26 * 3600
AGE_LO, AGE_HI = 24 * 3600, 200 * 3600
HOLD24, HOLD12, HOLD6 = 86400.0, 43200.0, 21600.0
TP = 1.5

# cell -> ExitSpec fields. tp = full exit at R >= 1.5 against the post-buy mark (p3exits)
FLOOR_SPECS = {
    "F0": dict(hold_s=HOLD24, tp=TP), "TPH6": dict(hold_s=HOLD6, tp=TP), "TPH12": dict(hold_s=HOLD12, tp=TP),
    "T6": dict(hold_s=HOLD6), "T12": dict(hold_s=HOLD12), "T24": dict(hold_s=HOLD24),
    "TR30": dict(hold_s=HOLD24), "TR50": dict(hold_s=HOLD24), "TR100": dict(hold_s=HOLD24),
    "B2": dict(hold_s=HOLD24, tp=TP), "B5": dict(hold_s=HOLD24, tp=TP), "BR": dict(hold_s=HOLD24, tp=TP),
    "PA": dict(hold_s=HOLD24, tp=TP), "PB": dict(hold_s=HOLD24),
}
G4_SPECS = {"G0": dict(hold_s=HOLD24, tp=TP), "GA": dict(hold_s=HOLD24, tp=TP), "GB": dict(hold_s=HOLD24),
            "GB2": dict(hold_s=HOLD24, tp=TP), "GBR": dict(hold_s=HOLD24, tp=TP)}
PAIRS = {"PA", "PB", "GA", "GB"}  # fed identical, per-pool spaced triggers
REV_G = ("r2h", "r6h", "r12h")
REV_EXITS = {"RC": dict(hold_s=3600.0, sl=0.88, trail=0.85), "H30": dict(hold_s=1800.0), "H60": dict(hold_s=3600.0), "H240": dict(hold_s=14400.0),
             "W": dict(hold_s=3600.0, sl=0.70, trail=0.65)}
REV_SPECS = {f"{e}_{g}": f for g in REV_G for e, f in REV_EXITS.items()}


def spec_of(name, f):
    return ExitSpec(f["hold_s"], f.get("tp"), f.get("sl"), f.get("trail"), cell=name)


class P3Rule:
    name = "p3_exits_v1"

    def __init__(self, aux_dir=None):
        self.l1 = l1_rule.L1Rule(aux_dir=aux_dir)
        self.l4 = l4rules.L4Rule()
        self.fl = {k: spec_of(k, f) for k, f in FLOOR_SPECS.items()}
        self.g4 = {k: spec_of(k, f) for k, f in G4_SPECS.items()}
        self.rv = {k: spec_of(k, f) for k, f in REV_SPECS.items()}

    def cells(self):
        return ([Cell(k, cooldown_s=60.0) for k in (*FLOOR_SPECS, *G4_SPECS)] + [Cell(k, cooldown_s=3600.0) for k in REV_SPECS])

    # ------------------------------------------------------------------
    def triggers(self, hour, state):
        sc = state.scratch
        raw = self.l1.triggers(hour, state)  # also maintains sc['uni'] (pool -> base time M or None) for this hour
        out = []
        rate = float(hour.slots_per_s) if hour.n else 0.0
        nxt = sc.setdefault("p3_nxt", {})
        for pool, slot, _ in raw:
            for k, sp in self.fl.items():
                if k in PAIRS:
                    if slot < nxt.get(("F", pool), -1):
                        continue
                    if k == "PB":
                        nxt[("F", pool)] = slot + int(np.rint(HOLD24 * rate)) + int(np.rint(60.0 * rate))  # set once per trigger, after PA and PB are emitted
                out.append((pool, slot, sp))
        out += self._g4(hour, sc, rate)
        out += self._rev(hour, sc)
        return out

    # ------------------------------------------------------------------ G4 v1 kit form
    def _g4(self, hour, sc, rate):
        if hour.n == 0:
            return []
        uni = sc.get("uni", {})
        gh = sc.setdefault("p3_gh", {})
        nxt = sc.setdefault("p3_nxt", {})
        H = int(hour.T)
        out = []
        for i in range(len(hour.pools)):
            pool = str(hour.pools[i])
            M = uni.get(pool)
            if M is None:
                continue
            s = hour.rows(i)
            bt = hour.bt[s].astype(np.int64)
            sol = hour.sol[s].astype(np.float64)
            with np.errstate(all="ignore"):
                pre = (hour.q[s].astype(np.float64) + hour.v[s].astype(np.float64)) / hour.b[s].astype(np.float64)
            g = gh.get(pool)
            if g is not None and AGE_LO <= H - M <= AGE_HI:
                kk = np.flatnonzero(bt >= H)
                if kk.size:
                    k0 = int(kk[0])
                    v0 = float(hour.v[s][k0])
                    if V_LO <= v0 <= V_HI and self._g4_signal(g, H):
                        slot = int(hour.sl[s][k0])
                        for k, sp in self.g4.items():  # GA / GB share per-pool spacing (identical entries); the other cells use the kit's own overlap rule
                            if k in PAIRS and slot < nxt.get(("G", pool), -1):
                                continue
                            out.append((pool, slot, sp))
                        if slot >= nxt.get(("G", pool), -1):
                            nxt[("G", pool)] = slot + int(np.rint(HOLD24 * rate)) + int(np.rint(60.0 * rate))
            # history update AFTER the decision (the decision used prior arrival hours only)
            if g is None:
                nbt, nsol, npre = bt, sol, pre
            else:
                nbt, nsol, npre = np.concatenate([g[0], bt]), np.concatenate([g[1], sol]), np.concatenate([g[2], pre])
            c = max(int(np.searchsorted(nbt, H - H26, "left")) - 1, 0)  # keep one anchor print before the 26 h window (forward fill)
            gh[pool] = (nbt[c:], nsol[c:], npre[c:])
        return out

    @staticmethod
    def _g4_signal(g, H):
        gbt, gsol, gpre = g
        j = int(np.searchsorted(gbt, H, "left")) - 1  # the last print with bt < H
        if j < 0 or gbt[j] < H - H6:
            return False
        j24 = int(np.searchsorted(gbt, H - H24, "left"))
        if float(gsol[j24:j + 1].sum()) < 1e9:
            return False
        pT = float(gpre[j])
        if not (np.isfinite(pT) and pT * 1e6 < 25.0):
            return False
        j6 = int(np.searchsorted(gbt, H - H6, "left")) - 1
        if j6 < 0:
            return False
        p6 = float(gpre[j6])
        return bool(np.isfinite(p6) and p6 > 0 and pT / p6 < 0.80)

    # ------------------------------------------------------------------ REVIVE (l4rules.py unchanged)
    def _rev(self, hour, sc):
        n = hour.n
        if n == 0:
            return []
        last = sc.setdefault("p3_last", {})
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
        qp = np.where(isb, q + sol * (1 - l4rules.FEE), q - sol / (1 - l4rules.FEE))
        bp = np.where(isb, b - tok, b + tok)
        with np.errstate(all="ignore"):
            spot = (qp + v) / bp
            spre = (q + v) / b
        ok = (v >= l4rules.V_LO) & (v <= l4rules.V_HI) & (qp >= l4rules.QMIN) & np.isfinite(spot)
        key = np.maximum.accumulate(pid.astype(np.int64) * l4rules.SH + bt)
        res = self.l4._revive(hour, pid, isb, ok, key, bt, tr, sol, spot, spre, last)
        for i in range(npools):
            last[str(hour.pools[i])] = int(bt[hour.ptr[i + 1] - 1])
        out = []
        for pool, slot, sp in res:
            g = sp.cell  # r2h / r6h / r12h
            for e in REV_EXITS:
                out.append((pool, slot, self.rv[f"{e}_{g}"]))
        return out


def make_rule(aux_dir=None):
    return P3Rule(aux_dir)
