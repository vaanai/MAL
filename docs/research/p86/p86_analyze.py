"""P86 step 3: walk-forward kNN (k=25), book (a), R / U / S baselines, rank IC (PREREG.md). Usage: p86_analyze.py LABELED.parquet [nopool] > output.txt"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PRIO = 55_000
K = 25
FEATS = ["log_vol60", "ret_5m", "ret_15m", "ret_60m", "flow60", "log_signers15", "log_age", "top3_60m", "close_vs_hi", "log_qeff"]
TEST_FROM = pd.Timestamp("2026-09-03", tz="UTC")
NOPOOL = len(sys.argv) > 2 and sys.argv[2] == "nopool"  # report-only sensitivity: memory never contains the same pool as the scored candidate
x = pd.read_parquet(sys.argv[1])
x["ts"] = pd.to_datetime(x.td, unit="s", utc=True)
x["day"] = x.ts.dt.strftime("%Y-%m-%d")
cnt = x.groupby("td").size()
x = x[x.td.isin(cnt[cnt >= 3].index)].sort_values(["td", "rank_screen"]).reset_index(drop=True)
x["flat"] = 0.85 * x.pnl25 + 0.15 * (-PRIO)
x["press"] = (1 - x.p_fail) * x.pnl25 + x.p_fail * (-PRIO)
x["flat100"] = 0.85 * x.pnl100 + 0.15 * (-PRIO)
x["press100"] = (1 - x.p_fail) * x.pnl100 + x.p_fail * (-PRIO)
is_test = x.ts >= TEST_FROM
print("candidates", len(x), "hours", x.td.nunique(), "| explore (memory start):", int((~is_test).sum()), "cands,", x[~is_test].td.nunique(), "hours,", x[~is_test].day.nunique(), "days",
      "| test:", int(is_test.sum()), "cands,", x[is_test].td.nunique(), "hours,", x[is_test].day.nunique(), "days")
print("explore no-fail mean y %.5f  test no-fail mean y %.5f" % (x[~is_test].y.mean(), x[is_test].y.mean()))

# walk-forward scores
x["score"] = np.nan
for d in sorted(x[is_test].day.unique()):
    mem = x[x.day < d]
    cur = x.index[x.day == d]
    mu, sd = mem[FEATS].mean().values, mem[FEATS].std(ddof=0).values
    sd = np.where(sd > 0, sd, 1.0)
    M = ((mem[FEATS].values - mu) / sd); my = mem.y.values
    Z = (x.loc[cur, FEATS].values - mu) / sd
    D = ((Z[:, None, :] - M[None, :, :]) ** 2).sum(-1)
    if NOPOOL:
        D = np.where(np.asarray(mem.pool.astype(str))[None, :] == np.asarray(x.loc[cur, "pool"].astype(str))[:, None], np.inf, D)
    nn = np.argsort(D, axis=1, kind="stable")[:, :K]
    x.loc[cur, "score"] = my[nn].mean(1)
t = x[is_test].copy()
print("test memory size min/max:", int((x.day < sorted(t.day.unique())[0]).sum()), int((x.day < sorted(t.day.unique())[-1]).sum()))

# IC
def ic_table(col, sign=1.0):
    ics = []
    for td, g in t.groupby("td"):
        if g[col].nunique() < 2 or g.y.nunique() < 2:
            continue
        ics.append((td, spearmanr(sign * g[col], g.y)[0]))
    a = pd.DataFrame(ics, columns=["td", "ic"]).dropna()
    a["day"] = pd.to_datetime(a.td, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    n = len(a); m = a.ic.mean(); tt = m / (a.ic.std(ddof=1) / np.sqrt(n))
    dd = a.groupby("day").ic.mean(); td_ = dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))
    return n, m, tt, td_
n, m, tt, tdb = ic_table("score")
print(f"\nRANK IC per hour (kNN score vs no-fail 60-min label, test): hours {n}  mean {m:.5f}  t {tt:.3f}  day-block t {tdb:.3f} (days {t.day.nunique()})")
print("single-feature rank IC per hour (report-only, no selection; sign as computed, + = higher feature, higher label):")
for f in FEATS + ["vol60"]:
    n, m, tt, tdb = ic_table(f)
    print(f"  {f:14s} hours {n}  mean {m:+.5f}  t {tt:+.3f}  day-block t {tdb:+.3f}")

# arms
rng = np.random.default_rng(1)
pick = {"book": [], "R": [], "S": []}
Ue = {"flat": [], "press": [], "flat100": [], "press100": []}
for td, g in t.groupby("td", sort=True):
    g = g.sort_values("rank_screen")
    pick["book"].append(g.index[int(np.argmax(g.score.values))])
    pick["R"].append(g.index[int(rng.integers(len(g)))])
    pick["S"].append(g.index[0])
    for k in Ue:
        Ue[k].append((td, g[k].mean()))
hours = sorted(t.td.unique())
day_of = lambda tds: pd.to_datetime(np.array(tds), unit="s", utc=True).strftime("%Y-%m-%d").values


def stats(v, days, label, scale=1e9, stake=0.25):
    v = np.asarray(v, float) / scale  # SOL
    days = np.asarray(days)
    n = len(v)
    mean_pp = v.mean() / stake * 100
    ex3 = np.sort(v)[:-3]
    ds = pd.Series(v).groupby(days).sum()
    best = ds.idxmax()
    rest = v[days != best]
    rng2 = np.random.default_rng(1)
    boot = np.array([v[rng2.integers(0, n, n)].mean() for _ in range(1000)])
    ud = np.unique(days); idx = {u: np.where(days == u)[0] for u in ud}
    rng3 = np.random.default_rng(1)
    bd = np.array([np.concatenate([v[idx[u]] for u in rng3.choice(ud, len(ud))]).mean() for _ in range(1000)])
    print(f"  {label:28s} n {n:4d} mean/trade {v.mean():+.6f} SOL ({mean_pp:+.3f} pp)  total {v.sum():+.4f}  ex-top3 total {ex3.sum():+.4f} mean {ex3.mean() / stake * 100:+.3f} pp"
          f"  ex-best-day({best}) total {rest.sum():+.4f} mean {rest.mean() / stake * 100:+.3f} pp  days+ {int((ds > 0).sum())}/{len(ds)}"
          f"  CI90lo(trade) {np.percentile(boot, 5):+.6f}  CI90lo(day-block) {np.percentile(bd, 5):+.6f}  trades/day {n / len(ds):.2f}")
    return mean_pp


res = {}
dd_all = {k: day_of(t.loc[pick[k], "td"].values) for k in pick}
for model in ("flat", "press"):
    print(f"\n=== fail model: {model} (0.25 SOL, exit lag 2) ===")
    for k in ("book", "R", "S"):
        res[(model, k)] = stats(t.loc[pick[k], model].values, dd_all[k], {"book": "kNN top pick (book a)", "R": "R random pick (seed 1)", "S": "S vol_60m top"}[k])
    u = np.array([v for _, v in Ue[model]])
    res[(model, "U")] = u.mean() / 0.25e9 * 100
    print(f"  {'U mean of all candidates':28s} hours {len(u)}  mean/hour-trade {u.mean() / 1e9:+.6f} SOL ({res[(model, 'U')]:+.3f} pp)")
    for ref in ("R", "U", "S"):
        print(f"  book - {ref}: {res[(model, 'book')] - res[(model, ref)]:+.3f} pp")
    # paired day-block CI of book-U
    b = t.loc[pick["book"], model].values / 0.25e9 * 100; uu = u / 0.25e9 * 100
    dif = pd.Series(b - uu).groupby(dd_all["book"]).mean()
    print(f"  book - U per-day mean diff: mean {dif.mean():+.3f} pp, days>0 {int((dif > 0).sum())}/{len(dif)}")

print("\n=== capacity line (book picks repriced at 1.0 SOL, same fail models) ===")
for model in ("flat100", "press100"):
    stats(t.loc[pick["book"], model].values, dd_all["book"], "book @1.0 SOL " + model, stake=1.0)
    stats(t.loc[pick["S"], model].values, dd_all["S"], "S @1.0 SOL " + model, stake=1.0)
nd = t.day.nunique()
for model in ("flat", "press"):
    e = t.loc[pick["book"], model].mean() / 1e9
    print(f"  {model}: trades/day {len(pick['book']) / nd:.2f} x 0.25 SOL x net edge {e / 0.25 * 100:+.3f} pp = {len(pick['book']) / nd * e:+.5f} SOL/day (0.25 SOL stake)")
print("hours with top kNN score > 0 (variant b count only):", int(sum(t.loc[i, 'score'] > 0 for i in pick['book'])), "of", len(pick['book']))
print("book picks == S pick:", int(sum(a == b for a, b in zip(pick['book'], pick['S']))), "of", len(pick['book']), "| mean N per hour", round(len(t) / len(hours), 2))
print("\nper-day book total SOL (flat):")
print(pd.Series(t.loc[pick['book'], 'flat'].values / 1e9).groupby(dd_all['book']).agg(['sum', 'count']).round(5).to_string())

# kill line
kill = False
for model in ("flat", "press"):
    bm = res[(model, "book")]
    if bm <= 0 or (bm - res[(model, "R")] < 1.0) or (bm - res[(model, "U")] < 1.0):
        kill = True
print("\nKILL LINE (book mean <= 0 under either model, or book - R < +1 pp or book - U < +1 pp under either model):", "KILL" if kill else "NOT KILLED")
