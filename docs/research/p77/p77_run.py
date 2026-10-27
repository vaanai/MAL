"""P77: b-flow veto states (PREREG.md) on the frozen H5 binding book. Exploration days only.
Usage: python p77_run.py flags OUT.parquet   (no pricing; veto states and counts)
       python p77_run.py price OUT.parquet   (P55 pricing of the baseline, el=2 and frozen lag)
Run with /data/mal/audit-1008/venv/bin/python under systemd-run --user --scope -p MemoryMax=8G.
"""
import math
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS  # noqa: E402

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FROZEN = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
BUDGET = 17.585e9
S = 0.1e9
LEG_B = 1.9
EL_FROZEN_S = 0.55
EL_PRIMARY = 2
SOL = 1e9


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


def setup(day, xd, meta):
    """Yield (mint, ref, arrays...) exactly as p55_price.py up to the trigger print i."""
    paths = pq.read_table(f"{P}/paths/{day}.parquet", filters=[("mint", "in", list(xd.index))]).to_pandas()
    for mint, g in paths.groupby("mint", sort=False):
        mm = meta[meta.mint == mint]
        if len(mm) != 1:
            continue
        mm = mm.iloc[0]
        V = float(mm.v); s0 = int(mm.s0)
        g = g[g.slot >= s0]
        sl = g.slot.values; q = g.q.values + V; b = g.b.values; isb = g.isbuy.values
        sol = g.sol.values.astype(float); tok = g.tok.values; th = g.th.values; bt = g.bt.values
        ok = bt > 0
        sps = (bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]) if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300 else np.nan
        if not (0.15 < sps < 0.6):
            continue
        tsec = (sl - s0) * sps
        qpost = np.r_[q[1:], q[-1] + np.where(isb[-1], 0.9875 * sol[-1], -sol[-1] / 0.9875)]
        bpost = np.r_[b[1:], b[-1] - (tok[-1] if isb[-1] else -tok[-1])]
        first400 = (sl - s0) <= 1600
        h = pd.DataFrame({"th": th[first400], "sol": sol[first400], "isb": isb[first400]})
        gg = h.groupby("th").agg(tot=("sol", "sum"), ob=("isb", "all"), n=("sol", "size"), mn=("sol", "min"), mx=("sol", "max"))
        cand = gg[(gg.ob) & (gg.n >= 3) & (gg.mn >= 0.2e9) & (gg.mx <= 2.0e9) & (gg.tot <= 17.7e9)].sort_values("n", ascending=False)
        bw = cand.index[0] if len(cand) else -1
        spent = np.cumsum(np.where(th == bw, sol, 0.0))
        trig = np.flatnonzero((qpost / 1e9 <= 40) & (tsec <= 300) & (tsec >= 0) & (~isb) & (spent < BUDGET * 0.999))
        if len(trig) == 0:
            continue
        i = trig[0]
        ref = xd.loc[mint]
        if abs(tsec[i] - ref.trig_t) > 1e-6:
            continue
        yield dict(mint=mint, ref=ref, V=V, s0=s0, sps=sps, sl=sl, q=q, b=b, isb=isb, sol=sol, tok=tok, th=th,
                   qpost=qpost, bpost=bpost, i=i)


def veto_states(d):
    """Causal B/D/E trigger prints with slot < slot of trigger print i; returns (B, D, E) booleans for 'in force'."""
    sl, q, b, isb, sol, tok, th, V = d["sl"], d["q"], d["b"], d["isb"], d["sol"], d["tok"], d["th"], d["V"]
    sps = d["sps"]; i = d["i"]; si = sl[i]
    n = lambda x: int(round(x / sps))
    m = int(np.searchsorted(sl, si, "left"))           # rows strictly before the trigger slot
    lo_slot = si - n(600)
    price = q / b
    # post-trade price (bonding of PP in DECLARE): buy (q+0.99 so)/(b-tk), sell (q-1.01 so)/(b+tk); q already includes V
    pp = np.where(isb, (q + 0.99 * sol) / (b - tok), (q - 1.01 * sol) / (b + tok))
    fired = {"B": False, "D": False, "E": False}
    # B
    for w in range(m):
        if not (isb[w] and sol[w] >= max(1 * SOL, 0.03 * q[w]) and sl[w] + n(4) >= lo_slot):
            continue
        f1 = None; f2 = None
        for r in range(w + 1, m):
            if sl[r] > sl[w] + n(4):
                break
            if isb[r] and th[r] != th[w]:
                if f1 is None:
                    f1 = th[r]
                elif th[r] != f1:
                    f2 = r
                    break
        if f2 is None or sl[f2] < lo_slot:
            continue
        rows = np.arange(w + 1, f2 + 1)
        buys = sol[rows][isb[rows] & (th[rows] != th[w])].sum()
        sells = sol[rows][~isb[rows]].sum()
        if sells <= 0.25 * buys:
            fired["B"] = True
            break
    # D and E at buy prints in [lo_slot, si)
    for j in range(m):
        if not isb[j] or sl[j] < lo_slot:
            continue
        if not fired["D"]:
            k = j - 1
            run = []
            while k >= 0 and (not isb[k]) and sl[k] > sl[j] - n(45):
                run.append(k); k -= 1
            run = run[::-1]
            if len(run) >= 4:
                sizes = sol[run]
                if (sizes[-2:].mean() <= 0.5 * sizes[:2].mean() and pp[run[-1]] / price[run[0]] - 1 <= -0.08
                        and sol[j] >= max(0.3 * SOL, 2 * sizes.mean())):
                    fired["D"] = True
        if not fired["E"]:
            ws = int(np.searchsorted(sl, sl[j] - n(600), "right"))
            idx = np.arange(ws, j + 1)
            bm = isb[idx]
            bsol = sol[idx][bm]; ssol = sol[idx][~bm]
            if len(bsol) and bsol.sum() - ssol.sum() >= 0.10 * q[ws] and len(set(th[idx][bm])) >= 12 and bsol.max() <= 0.25 * bsol.sum():
                fired["E"] = True
    return fired


def run_flags(out):
    fz = pd.read_parquet(FROZEN)
    rows = []
    for day, xd in fz.groupby("day"):
        xd = xd.set_index("mint")
        meta = pd.read_parquet(f"{P}/meta/{day}.parquet")
        meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9)]
        for d in setup(day, xd, meta):
            f = veto_states(d)
            rows.append(dict(day=day, mint=d["mint"], set=d["ref"]["set"], B=f["B"], D=f["D"], E=f["E"], veto=any(f.values()),
                             nprior=int(np.searchsorted(d["sl"], d["sl"][d["i"]], "left"))))
        print(day, len(rows), flush=True)
    pd.DataFrame(rows).to_parquet(out)


def run_price(out):
    fz = pd.read_parquet(FROZEN)
    res = []
    for day, xd in fz.groupby("day"):
        xd = xd.set_index("mint")
        meta = pd.read_parquet(f"{P}/meta/{day}.parquet")
        meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9)]
        for d in setup(day, xd, meta):
            sl, q, b, qpost, bpost, sps, s0, i, ref = d["sl"], d["q"], d["b"], d["qpost"], d["bpost"], d["sps"], d["s0"], d["i"], d["ref"]

            def state(X):
                j = np.searchsorted(sl, X, "right")
                return (q[j], b[j]) if j < len(sl) else (qpost[-1], bpost[-1])

            X = sl[i] + math.ceil(LEG_B / sps - 1e-9)
            qe, be = state(X)
            f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)
            xs = s0 + int(round(330.0 / sps))
            if xs <= X:
                continue

            def sell(el):
                qx, bx = state(xs + el)
                Q = qx + net; B = bx - tk
                return tk * Q / (B + tk) * (1 - tier(Q, B + tk)) - S - 2 * PRIO

            el_f = math.ceil(EL_FROZEN_S / sps - 1e-9)
            res.append(dict(day=day, mint=d["mint"], set=ref["set"], stored_pnl=ref.pnl, pnl_frozenlag=sell(el_f), pnl_el2=sell(EL_PRIMARY),
                            p_fail=ref.p_fail, stored_flat=ref.flat, stored_press=ref.press))
        print(day, len(res), flush=True)
    pd.DataFrame(res).to_parquet(out)


if __name__ == "__main__":
    {"flags": run_flags, "price": run_price}[sys.argv[1]](sys.argv[2])
