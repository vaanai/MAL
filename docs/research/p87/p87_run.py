"""P87: B7 dip-absorption pullback, one frozen rule (PREREG.md). Usage: p87_run.py count|price OUT_PREFIX
Reuses P78 code unchanged (p78_common.py, p72_run_frozen.py are verbatim copies from PR #1041). PumpSwap PRE-trade reserves, quote = q+V."""
import sys, os, glob
import numpy as np, pandas as pd
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p72_run_frozen as R
from p78_common import *

MODE, OUT = sys.argv[1], sys.argv[2]
COUNT = MODE == "count"
LO = 98240.0
DIP, LOOK_S, FLOW_S, AGE_S = -0.08, 900.0, 60.0, 6 * 3600
STAKE, S1 = 0.25e9, 1.0e9
R.TGT, R.STOP, R.CAP_S, R.L, R.EL = 0.06, -0.08, 2700.0, 2, 2
MIN_PRINTS, MIN_DEPTH = 20, 100e9
FIRST = {}
MC_MAX, RNG_S, RNG_MIN, FILT = 5e6, 6 * 3600, 0.02, True
PH = [0, 0, 0, 0]   # per-worker: tradeable pool-hours, dropped by mcap>5M, signals dropped by range, pool-hours with signals dropped by range


def first_b7():
    out = {}
    for f in sorted(glob.glob(f"{ROWS}/*.parquet")):
        d = pd.read_parquet(f, columns=["pool", "bt", "mc"])
        d = d[d.mc >= LO]
        if len(d):
            m = d.groupby(d.pool.astype(str)).bt.min()
            for k, v in m.items():
                if v < out.get(k, 1 << 62):
                    out[k] = int(v)
    return out


def work(day):
    d = load_day(day)
    if d is None:
        return day, [], np.zeros(6, int), list(PH)
    rows = []; fun = np.zeros(6, int); nseg = 0
    d = d.drop(columns=[c for c in ("mint", "trader") if c in d.columns]); d["pool"] = d.pool.astype("category")
    d["hr"] = d.bt // 3600
    d["qd"] = d.q + d.v
    d["n_h"] = d.groupby(["pool", "hr"]).bt.transform("size")
    d["mc_h"] = d.groupby(["pool", "hr"]).mc.transform("median")
    d["q_h"] = d.groupby(["pool", "hr"]).qd.transform("median")
    t0 = (d.n_h >= MIN_PRINTS) & (d.q_h >= MIN_DEPTH) & (d.mc_h >= LO)
    big = t0 & (d.mc_h > MC_MAX)
    d["trad"] = t0 & ~big
    ph = d[t0].drop_duplicates(["pool", "hr"]).shape[0]
    phb = d[big].drop_duplicates(["pool", "hr"]).shape[0]
    PH[0] += ph; PH[1] += phb
    for pool, g in d.groupby("pool", sort=False):
        if not g.trad.any():
            continue
        sps = pool_sps(g)
        if not (0.15 < sps < 0.6):
            continue
        f0 = FIRST.get(pool)
        for lo, hi in segments(g):
            h = g.iloc[lo:hi]
            if len(h) < 20:
                continue
            nseg += 1
            V = float(h.v.values[0])
            sl, q, b, isb, sol, tok, th, _ = R.pool_arrays(h, V)
            bt = h.bt.values; mc = h.mc.values; trad = h.trad.values
            price = q / b
            look = int(round(LOOK_S / sps)); kf = int(round(FLOW_S / sps)); hold = int(round(R.CAP_S / sps))
            ref = np.searchsorted(sl, sl - look, "right") - 1
            cs = np.r_[0.0, np.cumsum(np.where(isb, sol, -sol))]
            flow = cs[np.arange(len(sl))] - cs[np.searchsorted(sl, sl - kf, "left")]
            ok_ref = (ref >= 0) & (sl[np.maximum(ref, 0)] <= sl - look)
            dip = ok_ref & (price / price[np.maximum(ref, 0)] - 1 <= DIP) & (mc >= LO)
            ab = dip & (flow > 0)
            age = ab & (bt - f0 >= AGE_S)
            sig = age & trad
            sigi = np.flatnonzero(sig); keep = np.ones(len(sigi), bool)
            for n_, i in enumerate(sigi):
                w0 = np.searchsorted(bt, bt[i] - RNG_S, "left")
                pw = price[w0:i + 1]
                if pw.max() / pw.min() - 1 < RNG_MIN:
                    keep[n_] = False
            PH[2] += int((~keep).sum()); PH[3] += len({(pool, bt[i] // 3600) for i in sigi[~keep]})
            sig = np.zeros(len(sl), bool); sig[sigi[keep]] = True
            fun += [len(h), int(dip.sum()), int(ab.sum()), int(age.sum()), int(sig.sum()), 0]
            nxt = -1
            for i in np.flatnonzero(sig):
                if sl[i] + R.L <= nxt:
                    continue
                r = dict(day=day, pool=pool, slot=int(sl[i]), blk=h.blk.values[0], q_sol=q[i] / 1e9, mc=float(mc[i]), ph=0)
                if COUNT:
                    rows.append(r); nxt = sl[i] + R.L + hold + R.EL; continue
                pr = R.price_one(sl, q, b, isb, sol, th, sps, i, STAKE)
                if pr is None:
                    continue
                p1 = R.price_one(sl, q, b, isb, sol, th, sps, i, S1)
                r.update(pr); r["pnl1"] = p1["pnl"]; r["reason1"] = p1["reason"]
                rows.append(r); nxt = pr["ytr"] + R.EL
    fun[5] = len(rows)
    return day, rows, fun, list(PH)


def show(x, ndays, label):
    res = report(x, STAKE, ndays, label)
    xx = res["x"]
    print("tail share (flat pnl < -50% of stake):", round(float((xx.flat / STAKE < -0.5).mean()), 5), "| exits", x.reason.value_counts().to_dict(), "censored", int(x.censored.sum()))
    y = x.assign(pnl=x.pnl1)
    r1 = report(y, S1, ndays, label + " @1 SOL (same entries, own impact at 1 SOL)")
    print("tail share @1 SOL:", round(float((r1["x"].flat / S1 < -0.5).mean()), 5))


if __name__ == "__main__":
    FIRST.update(first_b7())
    print("pools with a B7 print", len(FIRST), flush=True)
    days = sorted({os.path.basename(f).split("_")[1][:10] for f in glob.glob(f"{ROWS}/*.parquet")})
    full = [dd for dd in days if len({os.path.basename(f).split("_")[1] for f in day_files(dd)}) >= 12]
    print("days", len(days), "full(>=12h)", len(full), flush=True)
    allr = []; tf = np.zeros(6, int); tph = np.zeros(4, int)
    with Pool(int(os.environ.get("NPROC", "1")), maxtasksperchild=1) as pl:
        for day, r, fun, nseg in pl.imap_unordered(work, full):
            allr += r; tf += fun; tph += nseg; print(day, len(r), fun.tolist(), nseg, flush=True)
    print("funnel (prints in segments; dip; +absorb; +age; +tradeable B7 pool-hour signal prints; trades after one-open-position):", tf.tolist())
    print("filters: tradeable B7 pool-hours (before filters) %d; dropped by pool mcap > 5M SOL: %d; signal prints dropped by trailing-6h range < 2%%: %d (in %d distinct pool-hours)" % tuple(tph))
    x = pd.DataFrame(allr); x.to_parquet(OUT + f"_{MODE}.parquet")
    blk_days = {}
    for dd in full:
        for f in day_files(dd):
            blk_days.setdefault(os.path.basename(f).split("_")[0], set()).add(dd)
    print(f"trades {len(x)}, days {len(full)}, trades/day {len(x) / len(full):.4f}")
    if len(x):
        print("per block trades:", x.blk.value_counts().to_dict(), "per day min/median/max", x.groupby("day").size().min(), x.groupby("day").size().median(), x.groupby("day").size().max())
    if not COUNT and len(x):
        show(x, len(full), "P87 pooled")
        for blk in sorted(blk_days):
            xb = x[x.blk == blk]
            if len(xb):
                show(xb, len(blk_days[blk]), f"P87 block {blk}")

    if len(x):
        vc = x.pool.value_counts()
        print("composition: distinct pools", len(vc), "top5 pools share of trades", round(float(vc.head(5).sum() / len(x)), 5), "top5 counts", vc.head(5).tolist())
        print("entry mcap SOL quantiles (5/25/50/75/95/max):", [round(float(v), 1) for v in x.mc.quantile([.05, .25, .5, .75, .95, 1.0])])
