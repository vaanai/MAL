"""P78 step 2, rules 1 and 3 inside one fee band. Usage: p78_rule72_mom.py BAND_LO BAND_HI OUT_PREFIX [count]
Rule 1 = p72_run_frozen.py (verbatim copy of p72_run.py from PR #1029) functions clusters/price_one with its frozen constants.
Rule 3 = P78-M, declared in PREREG.md. Band = pre-trade market cap [lo, hi) of the entry-decision print."""
import sys, os, glob, math
import numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p72_run_frozen as R
from p78_common import *

LO, HI, OUT = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
COUNT = len(sys.argv) > 4
STAKE = 0.25e9
M_SIG, M_LOOK_S, M_HOLD_S, M_DEPTH, M_L, M_EL = 0.05, 900.0, 900.0, 100e9, 2, 2


def momo_price(sl, q, b, isb, sol, sps, i, X, stake):
    """Same AMM/state math as p72_run.price_one; time exit at X + round(900/sps), exit lag 2."""
    qpost_last = q[-1] + (0.9875 * sol[-1] if isb[-1] else -sol[-1] / 0.9875)
    def state(Z):
        k = np.searchsorted(sl, Z, "right")
        return (q[k], b[k]) if k < len(sl) else (qpost_last, b[-1])
    if X >= sl[-1]:
        return None
    qe, be = state(X)
    f = R.tier(qe, be); net = stake * (1 - f); tk = be * net / (qe + net)
    trig = X + int(round(M_HOLD_S / sps)); cens = trig > sl[-1]; trig = min(trig, int(sl[-1]))
    qy, by = state(trig + M_EL); Q = qy + net; B = by - tk
    val = tk * Q / (B + tk) * (1 - R.tier(Q, B + tk))
    ix = np.searchsorted(sl, X, "left"); hi = np.searchsorted(sl, X, "right"); lo2 = np.searchsorted(sl, X - int(round(2.0 / sps)), "left")
    return dict(pnl=val - stake - 2 * R.PRIO, censored=bool(cens), ssb=int(isb[ix:hi].sum()), nb=float(sol[lo2:hi][isb[lo2:hi]].sum()), X=int(X), ytr=int(trig))


def work(day):
    d = load_day(day)
    if d is None:
        return day, [], [], 0, 0, 0
    r72, rm = [], []; npools = 0; nseg = 0; skipped = 0
    for pool, g in d.groupby("pool", sort=False):
        mc_all = g.mc.values
        if not ((mc_all >= LO) & (mc_all < HI)).any():
            continue
        sps = pool_sps(g)
        if not (0.15 < sps < 0.6):
            skipped += 1; continue
        npools += 1
        for lo, hi in segments(g):
            h = g.iloc[lo:hi]
            if len(h) < 20:
                continue
            nseg += 1
            V = float(h.v.values[0])
            sl, q, b, isb, sol, tok, th, _ = R.pool_arrays(h.assign(bt=h.bt), V)
            mc = h.mc.values
            inb = (mc >= LO) & (mc < HI)
            # rule 1: frozen detector; entries only at in-band cluster prints
            busy = -1
            for j in R.clusters(sl, q, isb, sol, th):
                if not inb[j]:
                    continue
                r = dict(day=day, pool=pool, j=int(j), slot=int(sl[j]), q_sol=q[j] / 1e9, blk=h.blk.values[0])
                if COUNT:
                    r72.append(r); continue
                if sl[j] + R.L <= busy:
                    continue
                pr = R.price_one(sl, q, b, isb, sol, th, sps, j, STAKE)
                if pr is None:
                    continue
                r.update(pr); busy = pr["ytr"] + R.EL; r72.append(r)
            # rule 3: P78-M
            price = q / b; look = int(round(M_LOOK_S / sps)); hold = int(round(M_HOLD_S / sps)); nxt = -1
            ref = np.searchsorted(sl, sl - look, "right") - 1     # last print with slot <= slot - look
            for i in np.flatnonzero(inb & (q >= M_DEPTH) & (ref >= 0)):
                if sl[i] <= nxt or sl[ref[i]] > sl[i] - look:
                    continue
                if price[i] / price[ref[i]] - 1 < M_SIG:
                    continue
                X = int(sl[i]) + M_L
                r = dict(day=day, pool=pool, slot=int(sl[i]), blk=h.blk.values[0])
                if COUNT:
                    rm.append(r); nxt = X + hold + M_EL; continue
                pr = momo_price(sl, q, b, isb, sol, sps, i, X, STAKE)
                if pr is None:
                    continue
                r.update(pr); rm.append(r); nxt = X + hold + M_EL
    return day, r72, rm, npools, nseg, skipped


if __name__ == "__main__":
    days = sorted({os.path.basename(f).split("_")[1][:10] for f in glob.glob(f"{ROWS}/*.parquet")})
    # full days only: all 24 hours present across blocks
    full = [dd for dd in days if len({os.path.basename(f).split("_")[1] for f in day_files(dd)}) >= 12]
    print("days", len(days), "full(>=12h)", len(full), flush=True)
    a72, am = [], []; tp = ts = tk = 0
    with Pool(int(os.environ.get("NPROC", "3"))) as pl:
        for day, r1, r3, npl, nseg, sk in pl.imap_unordered(work, full):
            a72 += r1; am += r3; tp += npl; ts += nseg; tk += sk
            print(day, len(r1), len(r3), npl, nseg, flush=True)
    d1, d3 = pd.DataFrame(a72), pd.DataFrame(am)
    print(f"band [{LO},{HI}) pools {tp} segments {ts} skipped_sps {tk}; rule1 events {len(d1)} ({len(d1) / len(full):.3f}/day), rule3 events {len(d3)} ({len(d3) / len(full):.3f}/day), days {len(full)}")
    d1.to_parquet(OUT + "_r72.parquet"); d3.to_parquet(OUT + "_mom.parquet")
    if not COUNT:
        for nm, x in (("P72 sell-cluster absorb", d1), ("P78-M momentum 15m", d3)):
            if len(x):
                print("exits:", x.get("reason", pd.Series(["time"])).value_counts().to_dict() if "reason" in x else "time exit", "censored", int(x.censored.sum()))
                report(x, STAKE, len(full), nm)
