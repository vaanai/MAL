"""P82 pricing: P55 baseline math (no exit change) + cashback creator-leg refund. Exploration days only (docs/research/p82/PREREG.md).
Run: /data/mal/audit-1008/venv/bin/python p82_price.py POOLS.json FLAGS.json OUT.parquet
Mirrors docs/research/p55/p55_price.py up to the baseline sell; the BOOST detector is not needed (exit unchanged)."""
import json, math, sys
import numpy as np, pandas as pd, pyarrow.parquet as pq
sys.path.insert(0, "/data2/mal/wt/p82")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS, PUMPSWAP_SOL_FEE_SPLIT  # noqa: E402

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FROZEN = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
SPH = np.array([r[0] for r in PUMPSWAP_SOL_FEE_SPLIT], float)
SPC = np.array([r[1] for r in PUMPSWAP_SOL_FEE_SPLIT], float)
assert (SPH == TH).all() and ((SPC + np.array([r[2] + r[3] for r in PUMPSWAP_SOL_FEE_SPLIT])) == TP).all()
PRIO = 55_000; BUDGET = 17.585e9; LEG_B = 1.9; EL_FROZEN_S = 0.55; EL_PRIMARY = 2
STAKES = [0.1e9, 0.25e9, 1.0e9]


def idx(Q, B):
    return np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1


def tier(Q, B): return TP[idx(Q, B)] / 1e6
def cr(Q, B): return SPC[idx(Q, B)] / 1e6


def main(pools_f, flags_f, out):
    pools = json.load(open(pools_f)); flags = json.load(open(flags_f))
    fz = pd.read_parquet(FROZEN); res = []
    for day, xd in fz.groupby("day"):
        xd = xd.set_index("mint")
        meta = pd.read_parquet(f"{P}/meta/{day}.parquet")
        meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9)]
        paths = pq.read_table(f"{P}/paths/{day}.parquet", filters=[("mint", "in", list(xd.index))]).to_pandas()
        for mint, g in paths.groupby("mint", sort=False):
            mm = meta[meta.mint == mint]
            if len(mm) != 1: continue
            mm = mm.iloc[0]; V = float(mm.v); s0 = int(mm.s0)
            g = g[g.slot >= s0]
            sl = g.slot.values; q = g.q.values + V; b = g.b.values; isb = g.isbuy.values
            sol = g.sol.values.astype(float); tok = g.tok.values; th = g.th.values; bt = g.bt.values
            ok = bt > 0
            sps = (bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]) if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300 else np.nan
            if not (0.15 < sps < 0.6): continue
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
            if len(trig) == 0: continue
            i = trig[0]; ref = xd.loc[mint]
            if abs(tsec[i] - ref.trig_t) > 1e-6: continue
            si = sl[i]

            def state(X):
                j = np.searchsorted(sl, X, "right")
                return (q[j], b[j]) if j < len(sl) else (qpost[-1], bpost[-1])

            X = si + math.ceil(LEG_B / sps - 1e-9)
            xs = s0 + int(round(330.0 / sps))
            if xs <= X: continue
            qe, be = state(X)
            el_f = math.ceil(EL_FROZEN_S / sps - 1e-9)
            pool = pools[mint]["pool"]; fl = flags[pool]
            cb = fl["state"] == "ok" and fl["cashback"] == 1
            row = dict(day=day, mint=mint, pool=pool, blk=ref.blk, set=ref["set"], cb=bool(cb), known=fl["state"] == "ok",
                       sps=sps, stored_pnl=ref.pnl, p_fail=ref.p_fail, stored_flat=ref.flat, stored_press=ref.press)
            for S in STAKES:
                f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)
                buy_ref = S * cr(qe, be)
                for tag, el in (("f", el_f), ("2", EL_PRIMARY)):
                    qx, bx = state(xs + el)
                    Q = qx + net; B = bx - tk
                    gross = tk * Q / (B + tk)
                    proceeds = gross * (1 - tier(Q, B + tk))
                    sfx = "" if S == 0.1e9 else f"_{int(S/1e8)}"
                    row[f"pnl_{tag}{sfx}"] = proceeds - S - 2 * PRIO
                    row[f"ref_{tag}{sfx}"] = buy_ref + gross * cr(Q, B + tk)
            res.append(row)
        print(day, len(res), flush=True)
    pd.DataFrame(res).to_parquet(out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
