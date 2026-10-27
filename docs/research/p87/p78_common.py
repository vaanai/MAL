"""P78 shared: load a UTC day of slim rows, build per-pool segmented arrays, gate metrics. PumpSwap PRE-trade, quote = q+V."""
import glob, math, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
ROWS = "/data2/mal/p78/rows"
EDGES = [0, 420, 1470, 9820, 24560, 58940, 78590, 98240, 1e18]
GAP = 150          # slots; a gap in retained prints this long ends a segment (price fell below the 9,820 SOL retention floor or went dormant)
PRIO = 55_000


def day_files(day, blocks=None):
    fs = sorted(glob.glob(f"{ROWS}/*_{day}T*.parquet"))
    return [f for f in fs if blocks is None or os.path.basename(f).split("_")[0] in blocks]


def load_day(day, blocks=None):
    fs = day_files(day, blocks)
    if not fs:
        return None
    d = pd.concat([pd.read_parquet(f).assign(blk=os.path.basename(f).split("_")[0]) for f in fs], ignore_index=True)
    d["pool"] = d.pool.astype(str); d["side"] = d.side.astype(str)
    d["isbuy"] = (d.side == "buy").values
    d["th"] = pd.util.hash_array(d.trader.astype(str).values)
    d = d.sort_values(["pool", "slot", "txi", "evi"], kind="stable").reset_index(drop=True)
    return d


def segments(g):
    """g: one pool's rows sorted. Yields (lo, hi) index ranges split at slot gaps > GAP."""
    sl = g.slot.values
    cut = np.flatnonzero(np.diff(sl) > GAP) + 1
    st = np.r_[0, cut]; en = np.r_[cut, len(sl)]
    return list(zip(st, en))


def pool_sps(g):
    sl = g.slot.values; bt = g.bt.values
    return (bt[-1] - bt[0]) / max(1, sl[-1] - sl[0]) if sl[-1] > sl[0] + 300 else np.nan


def gate(v, days, stake, draws=1000, seed=1):
    v = np.asarray(v, float) / 1e9; days = np.asarray(days); ud = np.unique(days)
    idx = {d: np.where(days == d)[0] for d in ud}
    per = pd.Series(v).groupby(days).sum()
    rng = np.random.default_rng(seed)
    bm = np.array([np.concatenate([v[idx[d]] for d in rng.choice(ud, len(ud), replace=True)]).mean() for _ in range(draws)])
    srt = np.sort(v)[::-1]; S = stake / 1e9
    bd = per.idxmax(); nb = (days != bd).sum()
    return dict(n=len(v), days=len(ud), mean_sol=v.mean(), mean_pct=100 * v.mean() / S, ci5_pct=100 * np.percentile(bm, 5) / S, total=v.sum(),
                days_pos=f"{int((per > 0).sum())}/{len(ud)}", majority_pos=bool((per > 0).sum() > len(ud) / 2),
                ex_top3_total=v.sum() - srt[:3].sum(), best_day=bd, ex_bestday_total=v.sum() - per.max(),
                ex_bestday_mean_pct=100 * (v.sum() - per.max()) / max(1, nb) / S, median_pct=100 * np.median(v) / S)


def report(x, stake, ndays, label, extra_col=None):
    """x: DataFrame with day, pnl (lamports), ssb, nb. Prints both fail models. Returns dict."""
    from tools.latency_curve import Pressure, fit_curve
    x = x.copy(); pnl = x.pnl.values.astype(float)
    curve = fit_curve([Pressure(int(a), int(b)) for a, b in zip(x.ssb, x.nb)])
    p = np.array([curve.p(Pressure(int(a), int(b))) for a, b in zip(x.ssb, x.nb)])
    x["flat"] = 0.85 * pnl + 0.15 * -PRIO; x["press"] = (1 - p) * pnl + p * -PRIO; x["nofail"] = pnl
    print(f"\n=== {label}: trades {len(x)}, days with trades {x.day.nunique()}, days in population {ndays}, trades/day {len(x) / ndays:.4f}, mean p_fail {p.mean():.4f}")
    res = {}
    for m in ("nofail", "flat", "press"):
        res[m] = gate(x[m].values, x.day.values, stake)
        print(m, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in res[m].items()})
    e = min(res["flat"]["mean_pct"], res["press"]["mean_pct"])
    print(f"capacity: {len(x) / ndays:.4f} trades/day x {stake / 1e9} SOL x {e:.4f}% (lower model) = {len(x) / ndays * stake / 1e9 * e / 100:.5f} SOL/day")
    res["x"] = x
    return res
