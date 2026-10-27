"""P80: why the independent training sample is empty. Counts only (no pnl). Run with the audit venv."""
import glob, sys
import importlib.util
import numpy as np, pandas as pd, pyarrow.parquet as pq
spec = importlib.util.spec_from_file_location("p", "p80_run.py"); p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
fz = pd.read_parquet(p.FROZEN); fzm = set(fz.mint)
tot = dict(pools=0, h5=0, nonh5=0, nonh5_sps_fail=0, nonh5_q_le40=0, nonh5_trigger=0, h5_trigger=0)
for f in sorted(glob.glob(f"{p.P}/meta/*.parquet")):
    day = f.split("/")[-1][:-8]
    if not ("2026-08-14" <= day <= "2026-08-28"): continue
    meta = pd.read_parquet(f); meta = meta[(meta.v >= 17.5e9) & (meta.v <= 17.7e9) & (meta.blk == "explore-0814")]
    tot["pools"] += len(meta); isf = meta.mint.isin(fzm); tot["h5"] += int(isf.sum()); tot["nonh5"] += int((~isf).sum())
    paths = pq.read_table(f"{p.P}/paths/{day}.parquet", filters=[("mint", "in", list(meta.mint))]).to_pandas()
    for mint, g in paths.groupby("mint", sort=False):
        mm = meta[meta.mint == mint]
        if len(mm) != 1: continue
        mm = mm.iloc[0]; V = float(mm.v); s0 = int(mm.s0); g = g[g.slot >= s0]
        sl = g.slot.values; q = g.q.values + V; isb = g.isbuy.values; sol = g.sol.values.astype(float); bt = g.bt.values
        ok = bt > 0
        sps = (bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]) if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300 else np.nan
        h = mint in fzm
        if not (0.15 < sps < 0.6):
            if not h: tot["nonh5_sps_fail"] += 1
            continue
        tsec = (sl - s0) * sps
        qpost = np.r_[q[1:], q[-1] + np.where(isb[-1], 0.9875 * sol[-1], -sol[-1] / 0.9875)]
        if not h and (qpost / 1e9 <= 40).any(): tot["nonh5_q_le40"] += 1
        w = ((qpost / 1e9 <= 40) & (tsec <= 300) & (tsec >= 0) & (~isb)).any()
        if w: tot["h5_trigger" if h else "nonh5_trigger"] += 1
    print(day, tot, flush=True)
print("FINAL", tot)
