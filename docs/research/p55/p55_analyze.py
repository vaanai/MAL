"""P55 analysis of p55_price.py rows against the PREREG kill lines. Usage: python p55_analyze.py ROWS.parquet"""
import sys

import numpy as np
import pandas as pd

PRIO = 55_000
x = pd.read_parquet(sys.argv[1])
print("rows", len(x), "sets", x.set.value_counts().to_dict())
d = (x.pnl_base_frozenlag - x.stored_pnl).abs()
print("validation: max |repriced frozen pnl - stored pnl| lamports", d.max(), "n>1 lamport", int((d > 1).sum()))
pf = x.p_fail.values
print("validation: stored flat reproduces", float((x.stored_flat - (0.85 * x.stored_pnl + 0.15 * -PRIO)).abs().max()),
      "press", float((x.stored_press - ((1 - pf) * x.stored_pnl + pf * -PRIO)).abs().max()))


def models(pnl):
    return {"flat": 0.85 * pnl + 0.15 * -PRIO, "press": (1 - pf) * pnl + pf * -PRIO}


def boot_lo(delta, days, draws=1000, seed=1):
    rng = np.random.default_rng(seed)
    ud = np.unique(days)
    idx = {u: np.where(days == u)[0] for u in ud}
    m = [np.concatenate([delta[idx[u]] for u in rng.choice(ud, len(ud), replace=True)]).mean() for _ in range(draws)]
    return np.percentile(m, 5)


def report(col, label):
    base = models(x.pnl_base_el2.values); new = models(x[col].values)
    print(f"\n=== {label} (exit lag 2 slots; delta = new - frozen exit, pp of the 0.1 SOL stake) ===")
    n_ch = int(x.changed.sum())
    print("trades with changed exit:", n_ch, "of", len(x), f"({100 * n_ch / len(x):.3f}%)", "E1 fired", int(x.e1.sum()), "E2 fired", int(x.e2.sum()))
    for m in ("flat", "press"):
        dl = (new[m] - base[m]) / 1e8 * 100
        day = x.day.values
        tot = dl.sum()
        ex3 = tot - np.sort(dl)[::-1][:3].sum()
        bd = pd.Series(dl).groupby(day).sum()
        exd = tot - bd.max()
        out = dict(mean_pp=dl.mean(), total_pp=tot, ex_top3_mean=ex3 / (len(dl) - 3), ex_top3_total=ex3, best_day=bd.idxmax(),
                   ex_bestday_mean=exd / (len(dl) - (day == bd.idxmax()).sum()), ex_bestday_total=exd,
                   disc_mean=dl[(x.set == "frozen_disc").values].mean(), conf_mean=dl[(x.set == "frozen_conf").values].mean(),
                   boot_ci5_mean=boot_lo(dl, day), mean_on_changed=dl[x.changed.values].mean() if n_ch else np.nan)
        base_pct = base[m] / 1e8 * 100
        out["base_mean_pct"] = base_pct.mean(); out["new_mean_pct"] = (new[m] / 1e8 * 100).mean()
        big = base_pct <= -50
        out["trades<=-50% base"] = int(big.sum()); out["of which exit changed"] = int((big & x.changed.values).sum())
        win = base_pct > 0
        out["winners made worse"] = int((win & (dl < -1e-9)).sum()); out["winners"] = int(win.sum())
        print(m, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()})
        kills = []
        if out["mean_pp"] < 1.0: kills.append("1 mean<+1.0pp")
        if out["ex_top3_total"] <= 0 or out["ex_top3_mean"] < 0.5: kills.append("2 top3")
        if out["ex_bestday_total"] <= 0 or out["ex_bestday_mean"] < 0.5: kills.append("3 one-day")
        if out["disc_mean"] <= 0 or out["conf_mean"] <= 0: kills.append("4 sign")
        if n_ch < 30: kills.append("5 INSUFFICIENT(<30 changed)")
        print("  kill lines hit:", kills or "none")


report("pnl_new_el2", "PRIMARY E1 or E2")
report("pnl_e1only_el2", "sensitivity E1 only")
report("pnl_e2only_el2", "sensitivity E2 only")
# sensitivity: frozen lag convention (0.55 s) baseline vs new
b2 = x.pnl_base_frozenlag.values; n2 = x.pnl_new_frozenlag.values
for m in ("flat", "press"):
    dl = (models(n2)[m] - models(b2)[m]) / 1e8 * 100
    print("\nsensitivity frozen lag 0.55 s", m, "mean delta pp", round(dl.mean(), 4), "total", round(dl.sum(), 4))
print("\nchanged-exit trades: exit advance seconds describe", x[x.changed].exit_adv_s.describe().round(2).to_dict())
