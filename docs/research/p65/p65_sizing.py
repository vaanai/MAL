"""P65 sizing overlays for frozen Q50 trades (docs/research/p65/PREREG.md). Planning input, not gate evidence.
Run: /data/mal/audit-1008/venv/bin/python -I p65_sizing.py
"""
import json
import numpy as np
import pandas as pd

FROZEN = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
IMPACT = "/data/mal/hunt-1008/push-1016/hunt/cap-impact/out/impact.json"
ST = np.array([0.1, 0.25, 0.5, 1.0, 2.0])
RT_MED = np.array([0.6197, 1.5376, 3.0374, 5.9389, 11.3583])
RT_P90 = np.array([0.8046, 1.9936, 3.9287, 7.6346, 14.4562])


def rt(s, curve):
    ls, lc = np.log(ST), np.log(curve)
    x = np.log(s)
    if x <= ls[-1]:
        return float(np.exp(np.interp(x, ls, lc)))
    sl = (lc[-1] - lc[-2]) / (ls[-1] - ls[-2])
    return float(np.exp(lc[-1] + sl * (x - ls[-1])))


def load():
    d = pd.read_parquet(FROZEN)
    d["t_in"] = d.mbt_utc + pd.to_timedelta(d.trig_t + 1.9, unit="s")
    d["t_out"] = d.mbt_utc + pd.to_timedelta(330.55, unit="s")
    return d.sort_values("t_in").reset_index(drop=True)


def kelly_f(r):
    g = np.arange(0, 1.0005, 0.001)
    v = [np.mean(np.log1p(f * r)) for f in g]
    return float(g[int(np.argmax(v))])


def simulate(d, col, mode, p, curve, impact):
    """mode: flat(stake, cap, stopX) | kelly(frac). Returns list of (t_out, sol) taken, skipped count."""
    r0 = d[col].values / 1e8
    tin, tout = d.t_in.values, d.t_out.values
    day_out = d.t_out.dt.strftime("%F").values
    day_in = d.t_in.dt.strftime("%F").values
    open_ = []  # (t_out, stake, pnl, day_out)
    booked = {}
    bankroll_realized = 0.0
    taken, skipped = [], 0
    for i in range(len(d)):
        t = tin[i]
        still = []
        for o in open_:
            if o[0] <= t:
                booked[o[3]] = booked.get(o[3], 0.0) + o[2]
                bankroll_realized += o[2]
            else:
                still.append(o)
        open_ = still
        if mode == "flat":
            s, cap, X = p
            if cap is not None and len(open_) >= cap:
                skipped += 1; continue
            if X is not None and booked.get(day_in[i], 0.0) <= -X:
                skipped += 1; continue
        else:
            s = min(1.0, p * (1.0 + bankroll_realized))
            if s <= 0 or sum(o[1] for o in open_) + s > 1.0 + bankroll_realized:
                skipped += 1; continue
        r = r0[i] - ((rt(s, curve) - rt(0.1, curve)) / 100 if impact else 0.0)
        pnl = s * r
        open_.append((tout[i], s, pnl, day_out[i]))
        taken.append((tout[i], day_out[i], pnl, s))
    for o in open_:
        pass
    return pd.DataFrame(taken, columns=["t_out", "day", "sol", "stake"]), skipped


def metrics(tk, skipped, n_cand, days_cal):
    if len(tk) == 0:
        return dict(n=0, total=0, sol_day=0, mdd=0, worst_day=0, skip=1.0, ex3=0)
    tk = tk.sort_values("t_out")
    c = np.r_[0.0, tk.sol.cumsum().values]
    mdd = float((np.maximum.accumulate(c) - c).max())
    top3 = np.sort(tk.sol.values)[::-1][:3].sum()
    return dict(n=len(tk), total=float(tk.sol.sum()), sol_day=float(tk.sol.sum() / days_cal), mdd=mdd,
                worst_day=float(tk.groupby("day").sol.sum().min()), skip=skipped / n_cand,
                ex3=float(tk.sol.sum() - top3), mean_stake=float(tk.stake.mean()))


def main():
    d = load()
    disc, conf = d[d.set == "frozen_disc"].reset_index(drop=True), d[d.set == "frozen_conf"].reset_index(drop=True)
    fstar = kelly_f(disc["flat"].values / 1e8)
    print("Kelly f* (disc, flat):", fstar, "half:", fstar / 2)
    over = [("B0 flat0.10", "flat", (0.10, None, None)), ("a flat0.25", "flat", (0.25, None, None))]
    over += [(f"b cap{k}", "flat", (0.10, k, None)) for k in (2, 4, 8)]
    over += [(f"c stop{x}", "flat", (0.10, None, x)) for x in (0.1, 0.25)]
    over += [("d halfKelly", "kelly", fstar / 2)]
    out = []
    for nm, df in (("conf", conf), ("disc_ref", disc)):
        dc = (df.t_out.dt.normalize().max() - df.t_out.dt.normalize().min()).days + 1
        act = df.t_out.dt.strftime("%F").nunique()
        print(nm, "n", len(df), "calendar days", dc, "active days", act)
        for fm in ("flat", "press"):
            for impact, curve, tag in ((False, RT_MED, "naive"), (True, RT_MED, "impact_med"), (True, RT_P90, "impact_p90")):
                for o, mode, p in over:
                    tk, sk = simulate(df, fm, mode, p, curve, impact)
                    m = metrics(tk, sk, len(df), dc)
                    out.append(dict(set=nm, fail=fm, impact=tag, overlay=o, cal_days=dc, active_days=act, **m))
    res = pd.DataFrame(out)
    res.to_csv("p65_output.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    cols = ["overlay", "n", "total", "sol_day", "mdd", "worst_day", "skip", "ex3", "mean_stake"]
    for (s, f, i), g in res.groupby(["set", "fail", "impact"], sort=False):
        print(f"\n== {s} fail={f} impact={i}")
        print(g[cols].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
