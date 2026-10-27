"""P68 step 2: one-shot held-out test of the 5 committed rules (PREREG.md). Held-out = exp011-0909 only."""
import json, os
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
f = pd.read_parquet("/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet")
h = f[f.blk == "exp011-0909"].copy().reset_index(drop=True)
h["hour"] = h.mbt_utc.dt.hour
M = {"flat": (h.flat / 1e6).values, "press": (h.press / 1e6).values}
OPS = {">": np.greater, "<=": np.less_equal, ">=": np.greater_equal, "<": np.less}
rules = json.load(open(f"{HERE}/rules.json"))["rules"]
print("held-out n", len(h), "days", h.day.nunique(), "base flat/press", M["flat"].mean(), M["press"].mean())


def mask(r):
    m = np.ones(len(h), bool)
    for c, op, v in r["conds"]:
        m &= OPS[op](h[c].values, v)
    return m


rng = np.random.default_rng(1)
B = 10000
idx = rng.integers(0, len(h), (B, len(h)))
rows = []
for r in rules:
    m = mask(r); n = int(m.sum())
    out = {"id": r["id"], "n": n, "days": int(h.day[m].nunique())}
    pw = []
    for k, y in M.items():
        base = y.mean()
        d = y[m].mean() - base if n else np.nan
        ys = np.sort(y[m])[:-3] if n > 3 else np.array([])
        d3 = ys.mean() - base if len(ys) else np.nan
        bd = pd.Series(y[m]).groupby(h.day[m].values).sum().idxmax() if n else None
        keep = m & (h.day.values != bd)
        dd = y[keep].mean() - base if keep.sum() else np.nan
        mb = m[idx]; ib = y[idx]
        cnt = mb.sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            diff = (ib * mb).sum(1) / cnt - ib.mean(1)
        p = float(np.mean(~(diff > 0))) if n else 1.0  # nan draws count as not>0
        out[k] = {"rule_mean": float(y[m].mean()) if n else None, "diff": d, "diff_ex_top3": d3, "diff_ex_bestday": dd, "best_day": bd, "p": p}
        pw.append(p)
    out["p_worst"] = max(pw)
    rows.append(out)
order = sorted(range(5), key=lambda i: rows[i]["p_worst"])
for rank, i in enumerate(order):
    rows[i]["holm_ok"] = rows[i]["p_worst"] <= 0.10 / (5 - rank)
    if not rows[i]["holm_ok"]:
        for j in order[rank + 1:]:
            rows[j]["holm_ok"] = False
        break
for o in rows:
    ok = {k: all(o[k][x] >= 1.0 for x in ("diff", "diff_ex_top3", "diff_ex_bestday")) for k in ("flat", "press")}
    o["keep"] = bool(o["n"] >= 30 and ok["flat"] and ok["press"] and o.get("holm_ok", False))
    print(json.dumps(o, default=float))
json.dump(rows, open(f"{HERE}/p68_results.json", "w"), indent=1, default=float)
print("KEEP:", [o["id"] for o in rows if o["keep"]])
