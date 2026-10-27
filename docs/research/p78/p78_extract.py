"""P78 step 1 extractor: one clean-view hour file -> pool-hour cost-map aggregates + slim rows for market cap >= 9,820 SOL.
Usage: p78_extract.py OUT_DIR BLOCKTAG HOURFILE.jsonl.zst ...   (exploration clean views only; prints counts only)
PumpSwap PRE-trade reserves; quote = q + V (V from the P55 paths meta where the mint is listed, else 0)."""
import sys, os, glob
import numpy as np, pandas as pd, duckdb
from multiprocessing import Pool
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_SPLIT as SPLIT  # noqa: E402

P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
EDGES = [0, 420, 1470, 9820, 24560, 58940, 78590, 98240, 1e18]
TH = np.array([r[0] for r in SPLIT], float); CR = np.array([r[1] for r in SPLIT], float); PR = np.array([r[2] for r in SPLIT], float); LP = np.array([r[3] for r in SPLIT], float)
KEEP_MC = 9820.0
PRIO = 55_000


def vmap():
    v = {}
    for f in sorted(glob.glob(f"{P}/meta/*.parquet")):
        m = pd.read_parquet(f, columns=["mint", "v"]); m = m[(m.v >= 17.5e9) & (m.v <= 17.7e9)]
        v.update(dict(zip(m.mint, m.v)))
    return v


V = None


def one(args):
    out, tag, path = args
    global V
    if V is None:
        V = vmap()
    hour = os.path.basename(path).replace("trades-", "").replace(".jsonl.zst", "")
    tmp = f"{out}/agg/{tag}_{hour}.parquet"
    if os.path.exists(tmp):
        return hour, -1
    con = duckdb.connect(); con.execute("SET memory_limit='3GB'; SET threads=2")
    d = con.execute(f"""SELECT pool, mint, trader, side, sol_lamports AS sol, token_raw AS tok, quote_reserve AS q, base_reserve AS b, slot,
        tx_index AS txi, event_index AS evi, coalesce(block_time, event_ts) AS bt
        FROM read_json('{path}', format='newline_delimited', compression='zstd', ignore_errors=true, columns={{venue:'VARCHAR', mint:'VARCHAR', trader:'VARCHAR', side:'VARCHAR',
        sol_lamports:'BIGINT', token_raw:'HUGEINT', quote_reserve:'HUGEINT', base_reserve:'HUGEINT', pool:'VARCHAR', slot:'BIGINT', event_index:'BIGINT', event_ts:'BIGINT',
        quote_is_wsol:'BOOLEAN', block_time:'BIGINT', tx_index:'BIGINT'}})
        WHERE venue='pumpswap' AND quote_is_wsol AND mint LIKE '%pump' AND quote_reserve>0 AND base_reserve>0""").df()
    n_raw = len(d)
    d["tok"] = d.tok.astype(float); d["q"] = d.q.astype(float); d["b"] = d.b.astype(float)
    d["v"] = d.mint.map(V).fillna(0.0).astype(float)
    d = d.sort_values(["pool", "slot", "txi", "evi"], kind="stable").reset_index(drop=True)
    d["Q"] = d.q + d.v
    d["mc"] = d.Q / d.b * 1e6
    pc = pd.factorize(d.pool)[0]
    # PRE-trade check: within a pool, q[j+1]-q[j] has the sign of print j (sells reduce q)
    same = pc[1:] == pc[:-1]
    dq = np.diff(d.q.values); s = np.where(d.side.values[:-1] == "buy", 1, -1); m = same & (dq != 0)
    sign_ok = int((np.sign(dq[m]) == s[m]).sum()); sign_n = int(m.sum())
    d["hour"] = d.bt // 3600; d["day"] = d.bt // 86400
    # 2-slot lag move per distinct (pool, slot)
    sl = d.slot.values.astype(np.int64)
    key = pc.astype(np.int64) << 32 | sl
    first = np.r_[True, key[1:] != key[:-1]]
    fk = key[first]; fp = (d.Q.values / d.b.values)[first]; fpool = pc[first]; fsl = sl[first]; fh = d.hour.values[first]
    j = np.searchsorted(fk, (fpool.astype(np.int64) << 32) | (fsl + 2), "left")
    ok = j < len(fk); jj = np.minimum(j, len(fk) - 1)
    ok &= (fpool[jj] == fpool) & ((fsl[jj] - fsl) <= 6)
    mv = pd.DataFrame({"pc": fpool[ok], "hour": fh[ok], "mv": np.abs(fp[jj][ok] / fp[ok] - 1)})
    lag = mv.groupby(["pc", "hour"]).mv.median().rename("lag_med")
    lag_n = mv.groupby(["pc", "hour"]).size().rename("lag_n")
    d["pc"] = pc
    g = d.groupby(["pc", "hour"]).agg(pool=("pool", "first"), day=("day", "first"), n=("slot", "size"), nslots=("slot", "nunique"), mc=("mc", "median"),
                                      Q=("Q", "median"), b=("b", "median"), vol=("sol", "sum")).reset_index()
    g = g.join(lag, on=["pc", "hour"]).join(lag_n, on=["pc", "hour"])
    g["band"] = np.searchsorted(EDGES, g.mc.values, "right") - 1
    k = np.searchsorted(TH, g.mc.values + 1e-9, "right") - 1
    g["creator_ppm"] = CR[k]; g["protocol_ppm"] = PR[k]; g["lp_ppm"] = LP[k]
    g["fee_ppm"] = g.creator_ppm + g.protocol_ppm + g.lp_ppm
    f = g.fee_ppm / 1e6
    for sk, S in (("025", 0.25e9), ("1", 1.0e9)):
        xin = S * (1 - f)
        g[f"imp_{sk}"] = xin / (g.Q + xin)        # one-way price impact vs mid (exact constant product)
    g["depth"] = g.Q / 1e9
    g["blk"] = tag
    g.drop(columns=["Q", "b"]).assign(depth=g.depth).to_parquet(tmp)
    keep = d[d.mc >= KEEP_MC][["pool", "mint", "trader", "side", "sol", "tok", "q", "b", "v", "slot", "txi", "evi", "bt", "mc"]].copy()
    for c in ("pool", "mint", "trader", "side"):
        keep[c] = keep[c].astype("category")
    keep.to_parquet(f"{out}/rows/{tag}_{hour}.parquet", compression="zstd")
    with open(f"{out}/agg/{tag}_{hour}.chk", "w") as fh_:
        fh_.write(f"{n_raw} {len(keep)} {sign_ok} {sign_n}\n")
    return hour, n_raw


if __name__ == "__main__":
    out, tag = sys.argv[1], sys.argv[2]
    files = sys.argv[3:]
    os.makedirs(f"{out}/agg", exist_ok=True); os.makedirs(f"{out}/rows", exist_ok=True)
    nproc = int(os.environ.get("NPROC", "6"))
    with Pool(nproc) as pl:
        for h, n in pl.imap_unordered(one, [(out, tag, f) for f in files]):
            print(h, n, flush=True)
