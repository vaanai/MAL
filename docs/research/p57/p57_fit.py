"""P57 fit/eval/placebo per docs/research/p57/PREREG.md.
Run: PYTHONPATH=/data/mal/venv/lib/python3.12/site-packages /data/mal/audit-1008/venv/bin/python p57_fit.py ROWS.parquet
ROWS.parquet = output of docs/research/p55/p55_price.py (unchanged, PR #986)."""
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb

PRIO = 55_000
FEAT = "/data/mal/hunt-1008/h5-lossfilter/features_decision_time.parquet"
EXCL = {"f_boost_spent_frozen_sol", "f_trig_t_frozen_s", "f_hour_utc", "f_dow", "f_q_post_trig"}
HP = dict(n_estimators=200, learning_rate=0.03, num_leaves=7, max_depth=3, min_child_samples=40, subsample=0.8,
          subsample_freq=1, colsample_bytree=0.7, reg_lambda=5, random_state=1, n_jobs=2, verbose=-1)
Q = 0.20

x = pd.read_parquet(sys.argv[1])
f = pd.read_parquet(FEAT)
d = x.merge(f, on=["set", "day", "mint"], how="inner", validate="1:1")
assert len(d) == len(x) == 2826, (len(d), len(x))
allf = [c for c in f.columns if c.startswith("f_") and c not in EXCL]
cols = [c for c in allf if d[c].nunique(dropna=True) > 1]
X = d[cols].astype(float)
y = (d.pnl_base_el2 / 1e8 * 100).values
tr = (d.set == "frozen_disc").values
te = ~tr
print("rows", len(d), "train", int(tr.sum()), "test", int(te.sum()), "features", len(cols))
print("dropped constants:", [c for c in allf if c not in cols])
pf = d.p_fail.values


def nets(pnl_lam, p):
    return {"flat": 0.85 * pnl_lam + 0.15 * -PRIO, "press": (1 - p) * pnl_lam + p * -PRIO}


def fit_pred(Xtr, ytr, Xte):
    lo, hi = np.percentile(ytr, [1, 99])
    m = lgb.LGBMRegressor(**HP).fit(Xtr, np.clip(ytr, lo, hi))
    return m.predict(Xte), m


def oof_cut(Xtr, ytr, days):
    ud = np.unique(days)
    rng = np.random.default_rng(1)
    rng.shuffle(ud)
    fold = {u: k % 5 for k, u in enumerate(ud)}
    fk = np.array([fold[u] for u in days])
    oof = np.zeros(len(ytr))
    for k in range(5):
        a = fk != k
        oof[~a], _ = fit_pred(Xtr[a], ytr[a], Xtr[~a])
    return np.percentile(oof, 100 * Q), oof


def run(ytr, seed=None):
    yt = ytr.copy()
    if seed is not None:
        yt = np.random.default_rng(seed).permutation(yt)
    Xtr = X[tr].values
    days = d.day[tr].values
    cut, oof = oof_cut(Xtr, yt, days)
    p, m = fit_pred(Xtr, yt, X[te].values)
    return p > cut, p, cut, oof, m


def improvement(keep):
    out = {}
    for k, v in nets(d.pnl_base_el2.values[te], pf[te]).items():
        v = v / 1e8 * 100
        out[k] = dict(all=v.mean(), kept=v[keep].mean(), imp=v[keep].mean() - v.mean(), v=v)
    return out


keep, p, cut, oof, m = run(y[tr])
r = improvement(keep)
day = d.day.values[te]
n = int(te.sum())
print(f"\ncut (20th pct of train OOF preds) = {cut:.4f} pp; kept {keep.sum()} of {n} = {keep.mean():.4f}")
ytc = np.clip(y[tr], *np.percentile(y[tr], [1, 99]))
print("train OOF corr(pred,y) =", round(float(np.corrcoef(oof, ytc)[0, 1]), 4),
      " test corr(pred,y) =", round(float(np.corrcoef(p, y[te])[0, 1]), 4))
kill = []
real = {}
for k in ("flat", "press"):
    v = r[k]["v"]
    imp = r[k]["imp"]
    real[k] = imp
    top3 = np.argsort(v)[::-1][:3]
    ok = np.ones(n, bool)
    ok[top3] = False
    imp3 = v[ok & keep].mean() - v[ok].mean()
    skip_sum = pd.Series(np.where(~keep, v, 0.0)).groupby(day).sum()
    bd = skip_sum.idxmin()
    okd = day != bd
    impd = v[okd & keep].mean() - v[okd].mean()
    rng = np.random.default_rng(1)
    ud = np.unique(day)
    idx = {u: np.where(day == u)[0] for u in ud}
    bs = []
    for _ in range(1000):
        ii = np.concatenate([idx[u] for u in rng.choice(ud, len(ud), replace=True)])
        kk = keep[ii]
        bs.append(v[ii][kk].mean() - v[ii].mean() if kk.any() else 0.0)
    sk = v[~keep]
    print(f"[{k}] all {r[k]['all']:.4f}  kept {r[k]['kept']:.4f}  improvement {imp:.4f} pp | ex-top3 {imp3:.4f} | "
          f"ex-best-day({bd}) {impd:.4f} | day-boot 5th pct {np.percentile(bs, 5):.4f} | skipped mean {sk.mean():.4f}, "
          f"skipped<=-50%: {(sk <= -50).sum()} of {(v <= -50).sum()} total <=-50% trades")
    if imp < 1.0:
        kill.append(f"1 improvement<+1.0pp ({k})")
    if imp3 < 0.5 or impd < 0.5:
        kill.append(f"2 does not survive drop ({k}: top3 {imp3:.3f}, bestday {impd:.3f})")
if keep.mean() < 0.60:
    kill.append("3 kept<60%")

Ntr = nets(d.pnl_base_el2.values[tr], pf[tr])
ko = oof > cut
for k in Ntr:
    v = Ntr[k] / 1e8 * 100
    print(f"[{k}] train OOF improvement {v[ko].mean() - v.mean():.4f} pp (kept {ko.mean():.3f})")

pl = {"flat": [], "press": []}
for s in range(1, 21):
    kp = run(y[tr], seed=s)[0]
    rr = improvement(kp)
    for k in pl:
        pl[k].append(rr[k]["imp"])
for k in pl:
    a = np.array(pl[k])
    print(f"placebo[{k}] mean {a.mean():.4f} sd {a.std():.4f} max {a.max():.4f}; placebos >= real: {(a >= real[k]).sum()}/20; real {real[k]:.4f}")
    if a.mean() >= 0.5 * real[k]:
        kill.append(f"4 placebo mean {a.mean():.3f} >= half real {real[k]:.3f} ({k})")

ndays = len(np.unique(day))
for k in ("flat", "press"):
    v = r[k]["v"]
    kd = keep.sum() / ndays
    print(f"capacity[{k}]: {ndays} confirmation days; kept {kd:.3f} trades/day x 0.1 SOL x kept mean net {r[k]['kept']:.4f}% = "
          f"{kd * 0.1 * r[k]['kept'] / 100:.6f} SOL/day (all-trades baseline {len(v) / ndays:.3f}/day -> "
          f"{len(v) / ndays * 0.1 * r[k]['all'] / 100:.6f} SOL/day)")
imp_ = pd.Series(m.booster_.feature_importance("gain"), cols).sort_values(ascending=False)
print("\ntop gain features:", imp_.head(8).round(1).to_dict())
print("\nKILL lines hit:", kill or "none")
print("VERDICT:", "KILL" if kill else "CONTINUE (not a promote)")
