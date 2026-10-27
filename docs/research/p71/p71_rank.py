"""P71 step 1: August wallet ranking + bot flags (docs/research/p71/PREREG.md). explore-0814 only.
Run: /data/mal/audit-1008/venv/bin/python p71_rank.py OUT_DIR
"""
import sys, glob, os
import numpy as np, pandas as pd

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
BLOCK = "explore-0814"
N_HOUR, MIN_RT, TOPN = 30, 5, 50
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

pools, hours = [], []
for f in sorted(glob.glob(f"{P}/meta/*.parquet")):
    m = pd.read_parquet(f, columns=["mint", "blk", "v", "day"])
    m = m[(m.blk == BLOCK) & (m.v >= 17.5e9) & (m.v <= 17.7e9)]
    if m.empty:
        continue
    day = os.path.basename(f)[:-8]
    g = pd.read_parquet(f"{P}/paths/{day}.parquet", columns=["mint", "slot", "isbuy", "sol", "tok", "th", "bt"])
    g = g[g.mint.isin(m.mint)]
    g["sol"] = g.sol.astype(float)
    g["hr"] = g.bt // 3600
    hours.append(g.groupby(["th", "hr"]).size().rename("n").reset_index())
    g["bsol"] = np.where(g.isbuy, g.sol, 0.0); g["ssol"] = np.where(~g.isbuy, g.sol, 0.0)
    g["btok"] = np.where(g.isbuy, g.tok, 0.0); g["stok"] = np.where(~g.isbuy, g.tok, 0.0)
    g["bslot"] = np.where(g.isbuy, g.slot, np.iinfo(np.int64).max); g["sslot"] = np.where(~g.isbuy, g.slot, np.iinfo(np.int64).max)
    a = g.groupby(["th", "mint"]).agg(bsol=("bsol", "sum"), ssol=("ssol", "sum"), btok=("btok", "sum"), stok=("stok", "sum"),
                                      nb=("isbuy", "sum"), n=("isbuy", "size"), fb=("bslot", "min"), fs=("sslot", "min")).reset_index()
    # same-slot buy and sell in the same pool
    sb = g[g.isbuy][["th", "mint", "slot"]].drop_duplicates(); ss = g[~g.isbuy][["th", "mint", "slot"]].drop_duplicates()
    same = sb.merge(ss, on=["th", "mint", "slot"])[["th", "mint"]].drop_duplicates(); same["same"] = True
    a = a.merge(same, on=["th", "mint"], how="left"); a["same"] = a.same.fillna(False).astype(bool)
    a["day"] = day
    pools.append(a)
    print(day, len(a), flush=True)

a = pd.concat(pools)
h = pd.concat(hours).groupby(["th", "hr"]).n.sum().reset_index()
b1 = set(h[h.n > N_HOUR].th)
rt = a[(a.nb > 0) & (a.nb < a.n) & (a.fs < np.iinfo(np.int64).max) & (a.fb < np.iinfo(np.int64).max)].copy()
# round trip pool = has a buy and a sell
rt = a[(a.btok > 0) & (a.stok > 0)].copy()
rt["net"] = rt.ssol * np.minimum(1.0, rt.btok / rt.stok) - rt.bsol * np.minimum(1.0, rt.stok / rt.btok)  # matched quantity (PREREG amendment 1)
rt["fast"] = (rt.fs - rt.fb) < 2
w = rt.groupby("th").agg(score=("net", "sum"), n_rt=("net", "size"), frac_fast=("fast", "mean"), b2=("same", "any"), pools_all=("net", "size")).reset_index()
w["b1"] = w.th.isin(b1); w["b3"] = w.frac_fast >= 1 / 3
w["bot"] = w.b1 | w.b2 | w.b3
rk = w[w.n_rt >= MIN_RT].sort_values("score", ascending=False)
print("pools (wallet,pool) rows", len(a), "wallets", a.th.nunique(), "round-trip wallets", len(w), "ranked (>=5 rt)", len(rk))
print("bot flags among ranked: B1", int(rk.b1.sum()), "B2", int(rk.b2.sum()), "B3", int(rk.b3.sum()), "any", int(rk.bot.sum()), "of", len(rk))
top_raw = rk.head(TOPN)
print("unfiltered top 50: flagged", int(top_raw.bot.sum()), "B1", int(top_raw.b1.sum()), "B2", int(top_raw.b2.sum()), "B3", int(top_raw.b3.sum()))
clean = rk[~rk.bot]
top = clean.head(TOPN).copy()
top["rank"] = np.arange(1, len(top) + 1)
print("clean ranked wallets", len(clean), "top50 clean: n", len(top), "score range", top.score.max() / 1e9, top.score.min() / 1e9, "sum SOL", top.score.sum() / 1e9)
print("top50 clean n_rt min/median/max", top.n_rt.min(), top.n_rt.median(), top.n_rt.max())
top.to_parquet(f"{out}/top50.parquet"); rk.to_parquet(f"{out}/ranked_all.parquet")
