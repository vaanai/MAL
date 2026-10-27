"""P73 counts step 2: split the curve check by zero-reserve rows. No pricing, no outcome."""
import sys, numpy as np, pandas as pd
d = pd.read_parquet(sys.argv[1])
mid = d.mid.values; isb = d.isb.values; q = d.q.values; b = d.b.values; sol = d.sol_lamports.values.astype(float)
print("rows", len(d), "q==0", float((q == 0).mean()), "b==0", float((b == 0).mean()), "sol==0", float((sol == 0).mean()), "q or b zero", float(((q == 0) | (b == 0)).mean()))
print("mints with any zero-reserve row", d[(d.q == 0) | (d.b == 0)].mid.nunique(), "of", d.mid.nunique())
same = np.r_[False, mid[1:] == mid[:-1]]
pos = (q > 0) & (b > 0); pos0 = np.r_[False, pos[:-1]]
ok = same & pos & pos0
dq = np.r_[np.nan, np.diff(q)]; db = np.r_[np.nan, np.diff(b)]
k0 = np.r_[np.nan, (q * b)[:-1]]; kd = np.abs(q * b / k0 - 1)
for nm, m in (("buy", ok & isb), ("sell", ok & ~isb)):
    r = dq[m] / sol[m]
    print(nm, "n", int(m.sum()), "dir ok", round(float(((dq[m] > 0) == (nm == "buy")).mean()), 5), "|k drift| q50/q90/q99", np.quantile(kd[m], [.5, .9, .99]).tolist(),
          "share k<1e-6", round(float((kd[m] < 1e-6).mean()), 5), "dq/sol q5/50/95", np.quantile(r[np.isfinite(r)], [.05, .5, .95]).round(5).tolist())
print("pair rows usable", float(ok.sum() / same.sum()))
