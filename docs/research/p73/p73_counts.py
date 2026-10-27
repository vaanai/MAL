"""P73 counts step: convention/curve checks and signal counts. No pricing, no outcome."""
import sys, numpy as np, pandas as pd
d = pd.read_parquet(sys.argv[1])
mid = d.mid.values; isb = d.isb.values
q = d.q.values; b = d.b.values; sol = d.sol_lamports.values.astype(float); tok = d.token_raw.values
same = np.r_[False, mid[1:] == mid[:-1]]
dq = np.r_[np.nan, np.diff(q)]; db = np.r_[np.nan, np.diff(b)]
k0 = np.r_[np.nan, (q * b)[:-1]]; kd = np.abs(q * b / k0 - 1)
print("rows", len(d), "mints", len(np.unique(mid)))
for nm, m in (("buy", same & isb), ("sell", same & ~isb)):
    tok_ok = np.isclose(np.abs(db[m]), tok[m], rtol=1e-6, atol=2)
    r = dq[m] / sol[m]
    print(nm, "n", int(m.sum()), "token_raw==|d base| share", round(float(tok_ok.mean()), 5), "median |k drift|", float(np.median(kd[m])),
          "share |k drift|<1e-6", round(float((kd[m] < 1e-6).mean()), 5), "share <1e-3", round(float((kd[m] < 1e-3).mean()), 5),
          "dq/sol q5/50/95", np.quantile(r, [.05, .5, .95]).round(5).tolist())
print("buy: share dq>0 & db<0", round(float(((dq > 0) & (db < 0))[same & isb].mean()), 5), " sell: share dq<0 & db>0", round(float(((dq < 0) & (db > 0))[same & ~isb].mean()), 5))
d["p"] = q / (b * 1000)
d["day"] = pd.to_datetime(d.cs, unit="s").dt.strftime("%Y-%m-%d")
a = d[d.t <= 60].groupby("mid").p.last(); bb = d[d.t <= 300].groupby("mid").p.last()
has_dec = d[d.t >= 300].groupby("mid").size()
s = pd.DataFrame({"p1": a, "p5": bb}).join(has_dec.rename("dec"), how="inner").dropna()
s["fire"] = s.p5 < 0.5 * s.p1
s = s.join(d.groupby("mid").day.first())
print("eligible mints", len(s), "signals", int(s.fire.sum()), "share", round(float(s.fire.mean()), 5))
print(s.groupby("day").fire.agg(["size", "sum"]).T.to_string())
