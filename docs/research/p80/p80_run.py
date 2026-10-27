"""P80: learned rug veto on the frozen H5 book (PREREG.md). Exploration days only.
Modes (run with /data/mal/audit-1008/venv/bin/python under systemd-run --user --scope -p MemoryMax=8G):
  train  OUT.parquet   features + labelled pnl for non-H5 explore-0814 pools (H5 outcomes never touched)
  h5feat OUT.parquet   features only for the H5 trades (NO pnl computed)
  h5pnl  OUT.parquet   pnl for the H5 trades (run only after counts)
  fit    TRAIN H5FEAT OUT.parquet   fit once, score, threshold from discovery scores, veto flags
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
EL2 = 2
FEATS = ["snipers03", "sell_share", "max_sell_rel", "top_hold", "drawdown", "nwal"]


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


def features(sl, q, b, isb, sol, tok, th, s0, si):
    m = int(np.searchsorted(sl, si, "left"))          # rows strictly before the trigger slot
    if m < 1:
        return None
    sl, q, isb, sol, tok, th = sl[:m], q[:m], isb[:m], sol[:m], tok[:m], th[:m]
    snip = len(set(th[isb & (sl <= s0 + 3)]))
    bs = sol[isb].sum(); ss = sol[~isb].sum()
    sell_share = ss / (bs + ss) if bs + ss > 0 else 0.0
    max_sell_rel = float((sol[~isb] / q[~isb]).max()) if (~isb).any() else 0.0
    net = pd.Series(np.where(isb, tok, -tok)).groupby(th).sum().clip(lower=0)
    top_hold = float(net.max() / net.sum()) if net.sum() > 0 else 0.0
    drawdown = float(q[-1] / q.max() - 1)
    return dict(snipers03=math.log1p(snip), sell_share=float(sell_share), max_sell_rel=max_sell_rel, top_hold=top_hold,
                drawdown=drawdown, nwal=math.log1p(len(set(th))), nrows=m)


def pools(day, meta, mints, fz_ref=None):
    """Yield per-pool dict using the P55 trigger code (identical). fz_ref: frozen rows for H5 reproduction check."""
    paths = pq.read_table(f"{P}/paths/{day}.parquet", filters=[("mint", "in", list(mints))]).to_pandas()
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
        if fz_ref is not None and abs(tsec[i] - fz_ref.loc[mint].trig_t) > 1e-6:
            continue
        yield dict(mint=mint, V=V, s0=s0, sps=sps, sl=sl, q=q, b=b, isb=isb, sol=sol, tok=tok, th=th, qpost=qpost,
                   bpost=bpost, i=i, has_boost=bool(bw != -1), mblk=mm.blk)


def price(d):
    sl, q, b, qpost, bpost, sps, s0, i = d["sl"], d["q"], d["b"], d["qpost"], d["bpost"], d["sps"], d["s0"], d["i"]
    si = sl[i]

    def state(X):
        j = np.searchsorted(sl, X, "right")
        return (q[j], b[j]) if j < len(sl) else (qpost[-1], bpost[-1])
    kb = math.ceil(LEG_B / sps - 1e-9)
    X = si + kb
    qe, be = state(X)
    f = tier(qe, be); net = S * (1 - f); tk = be * net / (qe + net)
    xs = s0 + int(round(330.0 / sps))
    if xs <= X:
        return None

    def sell(el):
        qx, bx = state(xs + el)
        Q = qx + net; B = bx - tk
        return tk * Q / (B + tk) * (1 - tier(Q, B + tk)) - S - 2 * PRIO
    return sell(math.ceil(EL_FROZEN_S / sps - 1e-9)), sell(EL2)


def run(mode, out):
    fz = pd.read_parquet(FROZEN)
    fz_mints = set(fz.mint)
    rows = []
    days = sorted(fz.day.unique()) if mode != "train" else None
    if mode == "train":
        days = sorted(d for d in (p.split("/")[-1][:-8] for p in __import__("glob").glob(f"{P}/meta/*.parquet")) if "2026-08-14" <= d <= "2026-08-28")
    for day in days:
        meta = pd.read_parquet(f"{P}/meta/{day}.parquet")
        meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9)]
        if mode == "train":
            meta = meta[meta.blk == "explore-0814"]
            mints = [m for m in meta.mint if m not in fz_mints]
            ref = None
        else:
            xd = fz[fz.day == day].set_index("mint")
            mints, ref = list(xd.index), xd
        if not mints:
            continue
        for d in pools(day, meta, mints, ref):
            ft = features(d["sl"], d["q"], d["b"], d["isb"], d["sol"], d["tok"], d["th"], d["s0"], d["sl"][d["i"]])
            row = dict(day=day, mint=d["mint"], has_boost=d["has_boost"], **(ft or {}), feat_ok=ft is not None)
            if mode in ("train", "h5pnl"):
                pr = price(d)
                row["priced"] = pr is not None
                if pr is not None:
                    row["pnl_frozenlag"], row["pnl_el2"] = pr
            if ref is not None:
                row["set"] = ref.loc[d["mint"]]["set"]; row["stored_pnl"] = ref.loc[d["mint"]].pnl if mode == "h5pnl" else np.nan
                row["p_fail"] = ref.loc[d["mint"]].p_fail
            rows.append(row)
        print(day, len(rows), flush=True)
    pd.DataFrame(rows).to_parquet(out)


def logit_fit(X, y, lam=1.0, iters=50):
    n, k = X.shape
    Xa = np.c_[np.ones(n), X]
    w = np.zeros(k + 1)
    R = np.eye(k + 1) * lam; R[0, 0] = 0
    for _ in range(iters):
        z = Xa @ w; p = 1 / (1 + np.exp(-z))
        g = Xa.T @ (p - y) + R @ w
        H = (Xa * (p * (1 - p))[:, None]).T @ Xa + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    return w


def fit(train_p, h5_p, out):
    tr = pd.read_parquet(train_p); h5 = pd.read_parquet(h5_p)
    tr = tr[tr.feat_ok & tr.priced.fillna(False)].copy()
    tr["flat"] = 0.85 * tr.pnl_el2 + 0.15 * (-PRIO)
    tr["big"] = (tr.flat <= -0.5 * S).astype(int)
    print("train rows", len(tr), "with boost", int(tr.has_boost.sum()), "big losers", int(tr.big.sum()), "prev %.4f" % tr.big.mean())
    if len(tr) < 300 or tr.big.sum() < 30:
        print("NOT_DECIDABLE: training too small"); return
    mu = tr[FEATS].mean(); sd = tr[FEATS].std().replace(0, 1)
    w = logit_fit(((tr[FEATS] - mu) / sd).values, tr.big.values.astype(float))
    print("weights (intercept, then", FEATS, ")", np.round(w, 4).tolist())
    ok = h5.feat_ok.fillna(False)
    print("H5 unscored:", h5[~ok].groupby("set").size().to_dict())
    h5 = h5[ok].copy()
    h5["score"] = np.c_[np.ones(len(h5)), ((h5[FEATS] - mu) / sd).values] @ w
    thr = np.percentile(h5.score[h5.set == "frozen_disc"], 85)
    h5["veto"] = h5.score > thr
    print("threshold (85th pct of discovery scores) %.6f" % thr)
    print(h5.groupby("set").veto.agg(["sum", "size", "mean"]))
    h5[["day", "mint", "set", "score", "veto"]].to_parquet(out)
    tr["score"] = np.c_[np.ones(len(tr)), ((tr[FEATS] - mu) / sd).values] @ w
    from math import erf
    # train-side sanity only: rank AUC of score vs label (in-sample)
    r = tr.score.rank().values; pos = tr.big.values == 1
    print("train in-sample AUC %.4f" % ((r[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum())))


if __name__ == "__main__":
    if sys.argv[1] == "fit":
        fit(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        run(sys.argv[1], sys.argv[2])
