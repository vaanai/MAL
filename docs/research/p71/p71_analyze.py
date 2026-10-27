"""P71 analysis. Run: /data/mal/audit-1008/venv/bin/python p71_analyze.py TOP50.parquet ROWS.parquet"""
import glob, os, sys
import numpy as np, pandas as pd
from scipy.stats import spearmanr
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.latency_curve import Pressure, fit_curve

PRIO = 55_000
P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
top = pd.read_parquet(sys.argv[1]); x = pd.read_parquet(sys.argv[2])
curve = fit_curve([Pressure(int(a), int(b)) for a, b in zip(x.ssb, x.nb)])
x["p"] = [curve.p(Pressure(int(a), int(b))) for a, b in zip(x.ssb, x.nb)]
print("rows", len(x), "mean p", x.p.mean())


def lg(pnl, p):
    return {"flat": 0.85 * pnl + 0.15 * -PRIO, "press": (1 - p) * pnl + p * -PRIO}


def boot_lo(v, days, draws=1000, seed=1):
    rng = np.random.default_rng(seed); ud = np.unique(days); idx = {u: np.where(days == u)[0] for u in ud}
    return np.percentile([np.concatenate([v[idx[u]] for u in rng.choice(ud, len(ud), replace=True)]).mean() for _ in range(draws)], 5)


def block(d, label, col="pnl_s01", stake=0.1e9):
    print(f"\n=== {label}: n={len(d)} days={d.day.nunique()} wallets={d.wallet.nunique()} ===")
    res = {}
    for mname, v in lg(d[col].values, d.p.values).items():
        sol = v / 1e9; days = d.day.values; w = d.wallet.values
        s = pd.Series(sol)
        srt = np.sort(sol)[::-1]
        bd = s.groupby(days).sum(); bw = s.groupby(w).sum()
        out = dict(mean_sol=sol.mean(), mean_pct=100 * v.mean() / stake, total=sol.sum(), ex_top3=sol.sum() - srt[:3].sum(),
                   best_day=bd.idxmax(), ex_best_day=sol.sum() - bd.max(), days_pos=f"{int((bd > 0).sum())}/{len(bd)}",
                   ci5_pct=100 * boot_lo(v, days) / stake, best_wallet=int(bw.idxmax()), ex_best_wallet=sol.sum() - bw.max(),
                   best_wallet_share=bw.max() / sol.sum() if sol.sum() else np.nan, wallets_pos=f"{int((bw > 0).sum())}/{len(bw)}", median_pct=100 * np.median(v) / stake)
        print(mname, {k: (round(val, 5) if isinstance(val, float) else val) for k, val in out.items()})
        res[mname] = out
    return res


R = {}
for bk in ("fresh-0903", "exp011-0909"):
    R[bk] = block(x[x.blk == bk], bk)
R["pooled"] = block(x, "POOLED")
print("\nhold slots/kind:", x.kind.value_counts().to_dict(), "truncated timer exits", int(x.trunc.sum()), "median hold slots", x.hold_slots.median())
print("sensitivity: copied-sell lag 2 slots (timer exits unchanged), pooled")
y = x.dropna(subset=["pnl_s01_lag2"]); block(y, "lag2 sens", col="pnl_s01_lag2")

# per-day table and per-wallet table
for mname in ("flat", "press"):
    v = lg(x.pnl_s01.values, x.p.values)[mname] / 1e9
    print("\nper-day net SOL,", mname); print(pd.Series(v).groupby([x.blk.values, x.day.values]).agg(["size", "sum"]).round(4).to_string())

# capacity
ndays = x.day.nunique()
print(f"\n=== capacity: copied buys {len(x)} over {ndays} Sep days => {len(x) / ndays:.2f} per day (fresh-0903 {int((x.blk=='fresh-0903').sum())}/{x[x.blk=='fresh-0903'].day.nunique()}d, exp011-0909 {int((x.blk=='exp011-0909').sum())}/{x[x.blk=='exp011-0909'].day.nunique()}d)")
for sk, S in (("s01", 0.1), ("s025", 0.25), ("s1", 1.0)):
    for mname, v in lg(x[f"pnl_{sk}"].values, x.p.values).items():
        e = v.mean() / 1e9
        print(f"stake {S} SOL {mname}: mean net/trade {e:+.5f} SOL ({100*e/S:+.3f}% of stake) | trades/day x stake x edge = {len(x)/ndays:.2f} x {S} x {100*e/S:+.3f}% => {len(x)/ndays*e:+.5f} SOL/day")

# persistence
wal = x.groupby("wallet").apply(lambda d: pd.Series({"n": len(d), **{f"sum_{m}": v.sum() / 1e9 for m, v in lg(d.pnl_s01.values, d.p.values).items()},
        **{f"mean_{m}": v.mean() / 1e9 for m, v in lg(d.pnl_s01.values, d.p.values).items()}})).reset_index()
wal = wal.merge(top[["th", "score", "rank"]].assign(wallet=lambda t: t.th.astype("uint64").astype(object)), left_on="wallet", right_on="wallet", how="left") if False else wal
top2 = top.assign(wallet=top.th.astype("uint64").astype(np.int64, copy=False) if False else top.th.astype(object))
sc = {int(t): s for t, s in zip(top.th.values, top.score.values)}
wal["aug"] = [sc[int(w) % 2**64] for w in (wal.wallet.astype(np.int64).astype(object) % 2**64)]
print("\n=== persistence: wallets with a priced copy", len(wal))
for m in ("flat", "press"):
    for c in (f"sum_{m}", f"mean_{m}"):
        r = spearmanr(wal.aug, wal[c]); print(f"Spearman IC Aug net vs Sep copy {c}: {r.statistic:+.4f} (p={r.pvalue:.4f}, n={len(wal)})")
# descriptive: wallet's own Sep realized net (matched quantity), same blocks
own = []
for f in sorted(glob.glob(f"{P}/meta/*.parquet")):
    m = pd.read_parquet(f, columns=["mint", "blk", "v"]); m = m[m.blk.isin(("fresh-0903", "exp011-0909")) & (m.v >= 17.5e9) & (m.v <= 17.7e9)]
    if m.empty: continue
    g = pd.read_parquet(f"{P}/paths/{os.path.basename(f)[:-8]}.parquet", columns=["mint", "isbuy", "sol", "tok", "th"])
    g = g[g.mint.isin(m.mint) & g.th.isin(top.th.values.astype(np.uint64)) & (g.sol <= 100e9) & (g.sol != 987_654_320)]
    g["sol"] = g.sol.astype(float)
    a = g.assign(bs=np.where(g.isbuy, g.sol, 0), ss=np.where(~g.isbuy, g.sol, 0), bt=np.where(g.isbuy, g.tok, 0), st=np.where(~g.isbuy, g.tok, 0)).groupby(["th", "mint"])[["bs", "ss", "bt", "st"]].sum().reset_index()
    a = a[(a.bt > 0) & (a.st > 0)]
    a["net"] = a.ss * np.minimum(1, a.bt / a.st) - a.bs * np.minimum(1, a.st / a.bt)
    own.append(a.groupby("th").net.agg(["sum", "size"]).reset_index())
own = pd.concat(own).groupby("th")[["sum", "size"]].sum().reset_index()
own["aug"] = [sc[int(t)] for t in own.th]
r = spearmanr(own.aug, own["sum"]); print(f"descriptive: Spearman IC Aug net vs wallet's own Sep realized net (matched qty): {r.statistic:+.4f} (p={r.pvalue:.4f}, n={len(own)} wallets with Sep round trips); Sep own-net total {own['sum'].sum()/1e9:.2f} SOL; wallets positive {(own['sum']>0).sum()}/{len(own)}")
print("Aug top-50 score total SOL", top.score.sum() / 1e9)
