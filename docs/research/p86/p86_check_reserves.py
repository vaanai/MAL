"""P86: verify PumpSwap tape reserves are PRE-trade on a sample of hours (no outcome). Usage: python p86_check_reserves.py PSDIR [step]
For consecutive prints i, i+1 of a pool (same slot ordering): pre-trade => q[i+1]-q[i] ~ +sol[i] for a buy (fee-adjusted), -sol[i] for a sell.
post-trade => q[i]-q[i-1] ~ sol[i]. Count which relation holds within 5%."""
import glob, sys
import duckdb
files = sorted(glob.glob(sys.argv[1] + "/*.parquet"))[:: int(sys.argv[2]) if len(sys.argv) > 2 else 60]
con = duckdb.connect()
tot = {"pre_ok": 0, "post_ok": 0, "n": 0}
for f in files:
    r = con.execute(f"""
    WITH x AS (SELECT pool, side, sol, q, lead(q) OVER w AS qn, lag(q) OVER w AS qp FROM read_parquet('{f}')
               WINDOW w AS (PARTITION BY pool ORDER BY slot, txi, evi))
    SELECT count(*), sum(CASE WHEN abs((qn - q) - (CASE WHEN side='buy' THEN 1 ELSE -1 END)*sol) <= 0.05*sol THEN 1 ELSE 0 END),
           sum(CASE WHEN abs((q - qp) - (CASE WHEN side='buy' THEN 1 ELSE -1 END)*sol) <= 0.05*sol THEN 1 ELSE 0 END)
    FROM x WHERE qn IS NOT NULL AND qp IS NOT NULL AND sol > 10000000""").fetchone()
    tot["n"] += r[0]; tot["pre_ok"] += r[1]; tot["post_ok"] += r[2]
print("files", len(files), tot, "pre share", tot["pre_ok"] / tot["n"], "post share", tot["post_ok"] / tot["n"])
