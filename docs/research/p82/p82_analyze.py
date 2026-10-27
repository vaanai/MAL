"""P82 analysis (PREREG.md). Run: /data/mal/audit-1008/venv/bin/python p82_analyze.py p82_priced.parquet"""
import sys
import numpy as np, pandas as pd
d = pd.read_parquet(sys.argv[1]); PR = 55_000; CLAIM = 5_000
print("n", len(d), "cashback", int(d.cb.sum()), "unknown", int((~d.known).sum()))
print("baseline repro (frozen lag) max abs diff lamports:", float((d.pnl_f - d.stored_pnl).abs().max()))
d["flatchk"] = (0.85 * d.stored_pnl + 0.15 * -PR - d.stored_flat).abs(); d["presschk"] = ((1 - d.p_fail) * d.stored_pnl + d.p_fail * -PR - d.stored_press).abs()
print("stored fail-model reproduce max diff:", float(d.flatchk.max()), float(d.presschk.max()))
print("\n== cashback share ==")
print(d.groupby("blk").agg(trades=("cb", "size"), cb=("cb", "sum"), share=("cb", "mean")).to_string())
d["month"] = d.day.str[:7]
print(d.groupby("month").agg(trades=("cb", "size"), cb=("cb", "sum"), share=("cb", "mean")).to_string())
print("pools share (distinct == trades):", d.cb.mean())
print("by week:"); d["wk"] = pd.to_datetime(d.day).dt.strftime("%G-W%V"); print(d.groupby("wk").agg(trades=("cb", "size"), share=("cb", "mean")).to_string())

def fails(pnl, p):
    return {"flat": 0.85 * pnl + 0.15 * -PR, "press": (1 - p) * pnl + p * -PR}

def gate(x, days, label):
    x = np.asarray(x, float); n = len(x)
    ex3 = x.sum() - np.sort(x)[-3:].sum()
    dd = pd.Series(x).groupby(np.asarray(days)).sum()
    exbest = x.sum() - dd.max()
    pos = (dd > 0).astype(float)  # gate wording: days whose total is positive
    rng = np.random.default_rng(1); bs = np.array([x[rng.integers(0, n, n)].mean() for _ in range(1000)])
    return dict(label=label, n=n, mean_pp=x.mean() / 1e8 * 100, total_sol=x.sum() / 1e9, ex3_sol=ex3 / 1e9,
                exbest_sol=exbest / 1e9, maj_pos_days=int((pos > 0).sum()), ndays=len(pos), ci90_lo_pp=np.percentile(bs, 5) / 1e8 * 100)

rows = []
for tag, cond in (("primary el=2", "2"), ("frozen lag", "f")):
    for claim, cl in ((0, "refund full"), (CLAIM, "claim 5k/trade")):
        for sname, sub in (("disc", d[d.set == "frozen_disc"]), ("conf", d[d.set == "frozen_conf"]), ("all", d), ("cb-only", d[d.cb]), ("noncb", d[~d.cb])):
            base = fails(sub["pnl_" + cond], sub.p_fail)
            new = fails(sub["pnl_" + cond] + np.where(sub.cb, sub["ref_" + cond] - claim, 0.0), sub.p_fail)
            # failed send gets no refund: fails() already blends refunded pnl with 0.85/(1-p); refund only on the filled branch
            for m in ("flat", "press"):
                g0 = gate(base[m], sub.day, ""); g1 = gate(new[m], sub.day, "")
                rows.append(dict(exit=tag, refund=cl, set=sname, model=m, n=g1["n"], base_mean_pp=g0["mean_pp"], new_mean_pp=g1["mean_pp"],
                                 delta_pp=g1["mean_pp"] - g0["mean_pp"], new_ex3_SOL=g1["ex3_sol"], base_ex3_SOL=g0["ex3_sol"], new_exbest_SOL=g1["exbest_sol"],
                                 base_exbest_SOL=g0["exbest_sol"], maj_pos_days=f"{g1['maj_pos_days']}/{g1['ndays']}", base_maj_pos=f"{g0['maj_pos_days']}/{g0['ndays']}", ci90_lo_pp=g1["ci90_lo_pp"], base_ci90_lo_pp=g0["ci90_lo_pp"]))
R = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print("\n== re-cost table ==\n", R.round(4).to_string())

print("\n== subset quality (before refund), flat model mean pp, primary ==")
for nm, sub in (("cb", d[d.cb]), ("noncb", d[~d.cb])):
    print(nm, len(sub), round(fails(sub.pnl_2, sub.p_fail)["flat"].mean() / 1e8 * 100, 4))

print("\n== refund size, cashback trades, SOL at 0.1 stake (primary) ==")
r = d[d.cb].ref_2; print((r / 1e8 * 100).describe().round(4).to_string())
days = d.day.nunique(); span = (pd.to_datetime(d.day.max()) - pd.to_datetime(d.day.min())).days + 1
print(f"\n== live economics == frozen days with trades {days}, calendar span {span}, trades/day (trade days) {len(d)/days:.2f}, (calendar) {len(d)/span:.2f}")
for sfx, S in (("", 0.1), ("_2", 0.25), ("_10", 1.0)):
    tot_ref = d[d.cb]["ref_2" + sfx].sum() / 1e9
    b = fails(d["pnl_2" + sfx], d.p_fail)["flat"].mean() / (S * 1e9) * 100
    nt = fails(d["pnl_2" + sfx] + np.where(d.cb, d["ref_2" + sfx], 0), d.p_fail)["flat"].mean() / (S * 1e9) * 100
    print(f"stake {S} SOL: refund total {tot_ref:.4f} SOL; SOL/day per trade-day {tot_ref/days:.5f}, per calendar day {tot_ref/span:.5f}; flat base mean {b:.4f}% of stake -> {nt:.4f}% (delta {nt-b:.4f} pp of stake)")
