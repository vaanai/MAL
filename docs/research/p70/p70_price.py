"""P70 pricing of docs/research/p70/PREREG.md. Run: python -I p70_price.py PRINTS.parquet SELECTION.parquet OUT.parquet
PumpSwap PRE-trade reserves; state at slot t = pre-state of the first print with slot > t (exact), else last print's post by constant product."""
import json, math, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_SPLIT  # noqa: E402

PRINTS, SEL, OUT = sys.argv[1:4]
BOUNDS = pd.read_parquet(PRINTS.replace('.parquet', '_bounds.parquet')).set_index('day')
VMAP = json.load(open('/data/mal/pumpswap-virtual/pool_v_0909.json'))['v']
TH = np.array([r[0] for r in PUMPSWAP_SOL_FEE_SPLIT], float)
CR = np.array([r[1] for r in PUMPSWAP_SOL_FEE_SPLIT], float) / 1e6
PR = np.array([r[2] for r in PUMPSWAP_SOL_FEE_SPLIT], float) / 1e6
LP = np.array([r[3] for r in PUMPSWAP_SOL_FEE_SPLIT], float) / 1e6
PRIO = 55_000
LAG = 2
STAKES = (0.25e9, 1.0e9)


def fees(Qe, B):
    i = max(0, np.searchsorted(TH, Qe / B * 1e6 + 1e-9, "right") - 1)
    return CR[i] + PR[i] + LP[i], LP[i], CR[i] + PR[i]   # total, lp, non-lp


class Pool:
    def __init__(self, g, V):
        g = g.copy()
        g['ord'] = np.where(g.txi.isna(), g.frn, g.txi.fillna(0) * 1e6 + g.evi.fillna(0))
        g = g.sort_values(['slot', 'ord'], kind='stable')
        self.sl = g.slot.values; self.q = g.q.values.astype(float); self.b = g.b.values.astype(float)
        self.isb = g.isbuy.values; self.sol = g.sol.values.astype(float); self.tok = g.tok.values.astype(float)
        self.bt = g.bt.values.astype(float); self.V = V
        # base-reserve continuity: b[i+1] == b[i] -/+ tok[i]
        nb = self.b[:-1] + np.where(self.isb[:-1], -self.tok[:-1], self.tok[:-1])
        self.cont = float((np.abs(nb - self.b[1:]) <= 1.0).mean()) if len(self.b) > 1 else np.nan

    def state(self, X):
        """(Q_real, B) after slot X."""
        j = np.searchsorted(self.sl, X, "right")
        if j < len(self.sl):
            return self.q[j], self.b[j], j
        b1 = self.b[-1] + (-self.tok[-1] if self.isb[-1] else self.tok[-1])
        qe = self.q[-1] + self.V
        q1 = qe * self.b[-1] / b1 - qe + self.q[-1] if b1 > 0 else self.q[-1]
        return q1, b1, j


def enter(Qr, B, V, S):
    Qe = Qr + V
    f, lp, nl = fees(Qe, B)
    def g(a):
        net = a * (1 - f); y = B * net / (Qe + net)
        Qr1 = Qr + net + a * lp; B1 = B - y
        return y * Qr1 - (S - a) * B1, y, Qr1, B1
    lo, hi = 0.0, S
    for _ in range(80):
        mid = (lo + hi) / 2
        if g(mid)[0] > 0: hi = mid
        else: lo = mid
    a = (lo + hi) / 2
    _, y, Qr1, B1 = g(a)
    x = S - a
    return dict(a=a, x=x, y=y, gshare=x / Qr1, impact=(Qr + V) / B, post_mid=(Qr1 + V) / B1)


def leave(Qr_x, B_x, V, gsh):
    sol_w = gsh * Qr_x; tok_w = gsh * B_x
    Qe = Qr_x + V
    f, lp, nl = fees(Qe, B_x)
    gross = Qe * tok_w / (B_x + tok_w)
    return sol_w + gross * (1 - f), sol_w, tok_w, f


def run_branch(P, E, Xexit, ent, S, V):
    Qx, Bx, jx = P.state(Xexit + LAG)
    recv, sol_w, tok_w, fx = leave(Qx, Bx, V, ent['gshare'])
    mid_x = (Qx + V) / Bx
    # LP fees accrued: prints with slot in (E, Xexit+LAG]
    j0 = np.searchsorted(P.sl, E, "right"); j1 = np.searchsorted(P.sl, Xexit + LAG, "right")
    qe = P.q[j0:j1] + V
    idx = np.maximum(0, np.searchsorted(TH, qe / P.b[j0:j1] * 1e6 + 1e-9, "right") - 1)
    fee_tot = float((LP[idx] * P.sol[j0:j1]).sum())
    fees_ours = ent['gshare'] * fee_tot
    lp_mid = ent['gshare'] * (Qx + (Qx + V))
    hold = ent['x'] + ent['y'] * mid_x
    return dict(recv=recv, mid_x=mid_x, Qx=Qx, fee_tot=fee_tot, fees=fees_ours, lp_mid=lp_mid, hold=hold,
                exit_cost=lp_mid - recv, il=lp_mid - fees_ours - hold, nprints=int(j1 - j0), j1=int(j1), Xexit=int(Xexit))


def pressure_inputs(P, Xexit, T_epoch):
    L = Xexit + LAG
    m = (P.isb) & (P.sl == L)
    lo = np.searchsorted(P.bt, 0)  # bt monotone enough; use mask
    t_land = T_epoch + 1.0
    near = P.isb & (P.bt >= t_land - 2.0) & (P.bt <= t_land)
    return int(m.sum()), float(P.sol[near].sum())


def main():
    sel = pd.read_parquet(SEL)
    allp = pd.read_parquet(PRINTS)
    rows = []
    conts = []
    for pool, sp in sel.groupby('pool'):
        g = allp[allp.pool == pool]
        v = VMAP.get(pool)
        Vok = v is not None and 17.5e9 <= v <= 17.7e9
        V = float(v) if Vok else 0.0
        P = Pool(g, V); conts.append((pool, len(g), P.cont))
        for _, r in sp.iterrows():
            d = r.day
            d1 = (pd.Timestamp(d) + pd.Timedelta(days=1)).strftime('%Y-%m-%d'); d2 = (pd.Timestamp(d) + pd.Timedelta(days=2)).strftime('%Y-%m-%d')
            X0 = int(BOUNDS.loc[d, 'slot0']); X1 = int(BOUNDS.loc[d1, 'slot0'])
            if d2 in BOUNDS.index: X2 = int(BOUNDS.loc[d2, 'slot0']); x2_ok = True
            else: X2 = int(P.sl[-1]); x2_ok = False
            E = X0 + LAG
            Q0, B0, _ = P.state(E)
            if not (Q0 > 0 and B0 > 0):
                continue
            ep1 = BOUNDS.loc[d1, 'epoch']
            same, nearsol = pressure_inputs(P, X1, ep1)
            for S in STAKES:
                ent = enter(Q0, B0, V, S)
                b24 = run_branch(P, E, X1, ent, S, V)
                b48 = run_branch(P, E, X2, ent, S, V)
                fixed_entry = 2 * PRIO
                def net(b, extra): return b['recv'] - S - fixed_entry - 2 * PRIO - extra
                n24 = net(b24, 0.0); n48 = net(b48, PRIO)
                entry_cost = S - (ent['x'] + ent['y'] * ent['impact'])
                drift = ent['y'] * (b24['mid_x'] - ent['impact'])
                resid = n24 - (b24['fees'] + b24['il'] + drift - entry_cost - b24['exit_cost'] - fixed_entry - 2 * PRIO)
                rows.append(dict(day=d, pool=pool, mint=r.mint, rank=r['rank'], stake=S / 1e9, V=V, Vok=Vok, Q0=Q0, B0=B0, gshare=ent['gshare'],
                                 n24=n24, n48=n48, fees=b24['fees'], il=b24['il'], drift=drift, entry_cost=entry_cost, exit_cost=b24['exit_cost'],
                                 resid=resid, mid0=ent['impact'], mid24=b24['mid_x'], pxratio=b24['mid_x'] / ent['impact'], Qx=b24['Qx'], nprints=b24['nprints'],
                                 same=same, nearsol=nearsol, x2_ok=x2_ok, entry_impact_a=ent['a'] / S, swap_share=ent['a'] / S))
    pd.DataFrame(rows).to_parquet(OUT)
    c = pd.DataFrame(conts, columns=['pool', 'n', 'cont'])
    print('base-reserve continuity (pre-trade convention), weighted share', float((c.cont * c.n).sum() / c.n.sum()), 'min', float(c.cont.min()))
    print('rows', len(rows))


main()
