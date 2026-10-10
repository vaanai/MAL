"""C1 wallet ledger, step 1: per UTC tape day (hour-file day), per trader hash: activity and cash-flow stats over ALL pump venues.
Output wl/<day>.parquet. These are only ever used for days strictly AFTER <day> (prior-day ledger), so no look-ahead.
th = duckdb hash(trader) (same function as l3 ut.th; duckdb 1.5.6 audit venv).
"""
import duckdb, glob, os, sys, time
T = '/data/mal/audit-1008/tape/trades'
O = '/data/mal/hunt-1008/c1-cascade-postgrad/wl'
c = duckdb.connect()
c.execute("SET memory_limit='6GB'; SET threads=3; SET temp_directory='/data/mal/hunt-1008/tmp/c1-cascade-postgrad'; SET preserve_insertion_order=false")
days = sorted({os.path.basename(p)[:10] for p in glob.glob(f'{T}/*.parquet')})
for d in days:
    o = f'{O}/{d}.parquet'
    if os.path.exists(o):
        continue
    t0 = time.time()
    fs = sorted(glob.glob(f'{T}/{d}T*.parquet'))
    L = "['" + "','".join(fs) + "']"
    c.execute(f"""COPY (
      WITH pm AS (
        SELECT hash(trader) th, mint, count(*) n, sum((venue='pump_bonding')::INT) nbond,
               sum(CASE WHEN side='buy' THEN sol_lamports ELSE 0 END)/1e9 buy, sum(CASE WHEN side='sell' THEN sol_lamports ELSE 0 END)/1e9 sell,
               sum((side='buy')::INT) nb, sum((side='sell')::INT) ns
        FROM read_parquet({L}) WHERE venue IN ('pump_bonding','pumpswap') GROUP BY 1, 2)
      SELECT th, sum(n) n, count(*) nm, sum(nbond) nbond, sum(buy) buy, sum(sell) sell,
             sum((ns > 0 AND nb > 0 AND sell > buy)::INT) nwin, sum((ns > 0 AND nb > 0)::INT) nrt,
             sum(sell - buy) cash
      FROM pm GROUP BY 1) TO '{o}.tmp' (FORMAT parquet)""")
    os.rename(o + '.tmp', o)
    print(d, len(fs), round(time.time() - t0, 1), flush=True)
print('done')
