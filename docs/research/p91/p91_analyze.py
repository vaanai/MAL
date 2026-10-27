"""P91 step 3: walk-forward kNN (k=25, P86 model), book (a), R/U/S, rank IC, dead pools scored D1 (last price) and D2 (-100%). Usage: p91_analyze.py LABELED.parquet [strict]"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PRIO = 55_000; K = 25
FEATS = ["log_vol60", "ret_5m", "ret_15m", "ret_60m", "flow60", "log_signers15", "log_age", "top3_60m", "close_vs_hi", "log_qeff"]
FLOOR = {"B5": 1.0233, "B6": 0.8193, "B7": 0.6479}
TEST_FROM = pd.Timestamp("2026-09-03", tz="UTC")
STRICT = len(sys.argv) > 2 and sys.argv[2] == "strict"
x = pd.read_parquet(sys.argv[1])
x["ts"] = pd.to_datetime(x.td, unit="s", utc=True); x["day"] = x.ts.dt.strftime("%Y-%m-%d")
cnt = x.groupby("td").size()
x = x[x.td.isin(cnt[cnt >= 3].index)].sort_values(["td", "rank_screen"]).reset_index(drop=True)
dead = x.dead.values == 1
for st, sz in (("", "25"), ("100", "100")):
    pass
for nm in ("25", "100"):
    d1 = x["d1_" + nm].where(x["d1_" + nm].notna(), x["d2_" + nm])  # no later print at all -> D2
    x["pD1_" + nm] = np.where(dead, d1, x["pnl" + nm]); x["pD2_" + nm] = np.where(dead, x["d2_" + nm], x["pnl" + nm])
cols = {}
for dt in ("D1", "D2"):
    for nm in ("25", "100"):
        p = x[f"p{dt}_{nm}"]
        x[f"flat_{dt}_{nm}"] = 0.85 * p + 0.15 * (-PRIO)
        x[f"press_{dt}_{nm}"] = (1 - x.p_fail) * p + x.p_fail * (-PRIO)
x["yD1"] = x.pD1_25 / 0.25e9; x["yD2"] = x.pD2_25 / 0.25e9
is_test = x.ts >= TEST_FROM
print("entered candidates", len(x), "(dead %d)" % dead.sum(), "hours", x.td.nunique(), "| memory:", int((~is_test).sum()), "cands", x[~is_test].td.nunique(), "hours |",
      "test:", int(is_test.sum()), "cands (dead %d)" % int(dead[is_test.values].sum()), x[is_test].td.nunique(), "hours,", x[is_test].day.nunique(), "days", "STRICT" if STRICT else "")
print("priced mean y: memory %.5f test %.5f | all-candidate mean y D1 test %.5f D2 test %.5f" % (x[~is_test & ~dead].y.mean(), x[is_test & ~dead].y.mean(), x[is_test].yD1.mean(), x[is_test].yD2.mean()))
x["score"] = np.nan
pricedmem = ~dead
if not STRICT:
    for d in sorted(x[is_test].day.unique()):
        mem = x[(x.day < d) & pricedmem]; cur = x.index[x.day == d]
        mu, sd = mem[FEATS].mean().values, mem[FEATS].std(ddof=0).values; sd = np.where(sd > 0, sd, 1.0)
        M = (mem[FEATS].values - mu) / sd; my = mem.y.values; Z = (x.loc[cur, FEATS].values - mu) / sd
        D = ((Z[:, None, :] - M[None, :, :]) ** 2).sum(-1)
        x.loc[cur, "score"] = my[np.argsort(D, axis=1, kind="stable")[:, :K]].mean(1)
else:  # memory rows must have exited before the scored hour: td_m + 3600 + hold-ish bound (td_m + 3700 s) <= td
    for td in sorted(x[is_test].td.unique()):
        mem = x[(x.td + 3700 <= td) & pricedmem]; cur = x.index[x.td == td]
        mu, sd = mem[FEATS].mean().values, mem[FEATS].std(ddof=0).values; sd = np.where(sd > 0, sd, 1.0)
        M = (mem[FEATS].values - mu) / sd; my = mem.y.values; Z = (x.loc[cur, FEATS].values - mu) / sd
        D = ((Z[:, None, :] - M[None, :, :]) ** 2).sum(-1)
        x.loc[cur, "score"] = my[np.argsort(D, axis=1, kind="stable")[:, :K]].mean(1)
t = x[is_test].copy()


def ic(col, ycol, sub=None):
    g0 = t if sub is None else t[sub(t)]
    r = []
    for td, g in g0.groupby("td"):
        if g[col].nunique() < 2 or g[ycol].nunique() < 2 or len(g) < 3:
            continue
        r.append((td, spearmanr(g[col], g[ycol])[0]))
    a = pd.DataFrame(r, columns=["td", "ic"]).dropna(); a["day"] = pd.to_datetime(a.td, unit="s", utc=True).dt.strftime("%Y-%m-%d")
    n = len(a); m = a.ic.mean(); tt = m / (a.ic.std(ddof=1) / np.sqrt(n)); dd = a.groupby("day").ic.mean()
    return n, m, tt, dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))

print("\nRANK IC per hour (test), kNN score vs label:")
n, m, tt, tb = ic("score", "y", lambda g: g.dead == 0); print(f"  priced candidates only      hours {n} mean {m:.5f} t {tt:.3f} day-block t {tb:.3f}")
for dt in ("D1", "D2"):
    n, m, tt, tb = ic("score", "y" + dt); print(f"  dead included ({dt})          hours {n} mean {m:.5f} t {tt:.3f} day-block t {tb:.3f}")
for f in FEATS + ["vol60"]:
    n, m, tt, tb = ic(f, "y", lambda g: g.dead == 0); print(f"  single {f:14s} (priced) mean {m:+.5f} t {tt:+.3f}")

rng = np.random.default_rng(1)
pick = {"book": [], "R": [], "S": []}; hrs = []
for td, g in t.groupby("td", sort=True):
    g = g.sort_values("rank_screen"); hrs.append(td)
    pick["book"].append(g.index[int(np.argmax(g.score.values))]); pick["R"].append(g.index[int(rng.integers(len(g)))]); pick["S"].append(g.index[0])
dayof = lambda ix: t.loc[ix, "day"].values


def stats(v, days, label, stake=0.25):
    v = np.asarray(v, float) / 1e9; days = np.asarray(days); n = len(v); mp = v.mean() / stake * 100
    ex3 = np.sort(v)[:-3]; ds = pd.Series(v).groupby(days).sum(); best = ds.idxmax(); rest = v[days != best]
    r2 = np.random.default_rng(1); boot = np.array([v[r2.integers(0, n, n)].mean() for _ in range(1000)])
    ud = np.unique(days); idx = {u: np.where(days == u)[0] for u in ud}; r3 = np.random.default_rng(1)
    bd = np.array([np.concatenate([v[idx[u]] for u in r3.choice(ud, len(ud))]).mean() for _ in range(1000)])
    print(f"  {label:26s} n {n:4d} mean {v.mean():+.6f} SOL ({mp:+.3f} pp) total {v.sum():+.4f} ex-top3 total {ex3.sum():+.4f} ({ex3.mean()/stake*100:+.3f} pp) "
          f"ex-best-day({best}) total {rest.sum():+.4f} ({rest.mean()/stake*100:+.3f} pp) days+ {int((ds>0).sum())}/{len(ds)} CI90lo {np.percentile(boot,5):+.6f} CI90lo(day) {np.percentile(bd,5):+.6f} trades/day {n/len(ds):.2f}")
    return dict(mp=mp, ex3=ex3.sum(), exb=rest.sum(), ci=np.percentile(boot, 5), mean=v.mean(), dpos=int((ds > 0).sum()), nd=len(ds))

res = {}
nd = t.day.nunique()
for dt in ("D1", "D2"):
    for model in ("flat", "press"):
        col = f"{model}_{dt}_25"
        print(f"\n=== {model} / dead {dt} (0.25 SOL) ===")
        for k, lab in (("book", "kNN top pick (a)"), ("R", "R random (seed 1)"), ("S", "S vol_60m top")):
            res[(dt, model, k)] = stats(t.loc[pick[k], col].values, dayof(pick[k]), lab)
        u = t.groupby("td")[col].mean().values
        res[(dt, model, "U")] = u.mean() / 0.25e9 * 100
        print(f"  U mean of all candidates      hours {len(u)} mean {u.mean()/1e9:+.6f} SOL ({res[(dt, model, 'U')]:+.3f} pp)")
        for ref in ("R", "U", "S"):
            rv = res[(dt, model, ref)]["mp"] if ref != "U" else res[(dt, model, "U")]
            print(f"  book - {ref}: {res[(dt, model, 'book')]['mp'] - rv:+.3f} pp")
        # was the book's pick dead?
print("\nbook picks that were dead:", int(t.loc[pick["book"], "dead"].sum()), "of", len(pick["book"]), "| R picks dead", int(t.loc[pick["R"], "dead"].sum()), "| S picks dead", int(t.loc[pick["S"], "dead"].sum()),
      "| all candidates dead share %.4f" % t.dead.mean())
print("book pick band mix:", t.loc[pick["book"], "band"].value_counts().to_dict(), "| all candidate band mix:", t.band.value_counts().to_dict())
print("\n=== capacity: book picks repriced at 1.0 SOL ===")
for dt in ("D1", "D2"):
    for model in ("flat", "press"):
        stats(t.loc[pick["book"], f"{model}_{dt}_100"].values, dayof(pick["book"]), f"book@1.0 {model}/{dt}", stake=1.0)
print("\ncapacity at 0.25 SOL (trades/day x 0.25 x edge) and 1.0 SOL:")
for dt in ("D1", "D2"):
    for model in ("flat", "press"):
        e25 = t.loc[pick["book"], f"{model}_{dt}_25"].mean() / 1e9; e1 = t.loc[pick["book"], f"{model}_{dt}_100"].mean() / 1e9
        print(f"  {model}/{dt}: {len(pick['book'])/nd:.2f} trades/day x 0.25 x {e25/0.25*100:+.3f} pp = {len(pick['book'])/nd*e25:+.5f} SOL/day | x 1.0 x {e1*100:+.3f} pp = {len(pick['book'])/nd*e1:+.5f} SOL/day")
fl = t.loc[pick["book"]].apply(lambda r: FLOOR[r.band], axis=1) / 100
print("variant-b count only: hours with top score > pool band floor:", int((t.loc[pick["book"], "score"].values > fl.values).sum()), "of", len(pick["book"]), "| score>0:", int((t.loc[pick["book"], "score"] > 0).sum()))
print("per-day book total SOL (flat/D2):"); print(pd.Series(t.loc[pick["book"], "flat_D2_25"].values / 1e9).groupby(dayof(pick["book"])).agg(["sum", "count"]).round(5).to_string())
kill = False; why = []
for dt in ("D1", "D2"):
    for model in ("flat", "press"):
        b = res[(dt, model, "book")]["mp"]; u = res[(dt, model, "U")]
        if b <= 0: kill = True; why.append(f"{model}/{dt} mean {b:+.3f}<=0")
        if b - u < 1.0: kill = True; why.append(f"{model}/{dt} book-U {b-u:+.3f}<1")
lead = all(res[(dt, m, "book")]["ex3"] > 0 and res[(dt, m, "book")]["exb"] > 0 for dt in ("D1", "D2") for m in ("flat", "press"))
print("\nKILL LINE:", "KILL" if kill else ("LEAD" if lead else "NOT_DECIDABLE"), why)
