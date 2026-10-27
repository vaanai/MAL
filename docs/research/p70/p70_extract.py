"""P70 step 2: pull prints of the selected pools (all tape hours; exploration tape only) to a parquet, plus day boundary slots."""
import glob, os, sys
import duckdb, pandas as pd
T = '/data/mal/audit-1008/tape'; SEL, OUT = sys.argv[1], sys.argv[2]
sel = pd.read_parquet(SEL)
pools = sorted(sel.pool.unique())
hours = sorted(os.path.basename(f)[:13] for f in glob.glob(f'{T}/trades/*.parquet'))
assert not any('2026-09-15T12' <= h < '2026-09-18T23' for h in hours) and max(hours) < '2026-09-26'
con = duckdb.connect(); con.execute("SET memory_limit='5GB'; SET threads=4; SET preserve_insertion_order=false")
con.register('pl', pd.DataFrame({'pool': pools}))
fs = [f'{T}/trades/{h}.parquet' for h in hours]
con.execute(f"""COPY (SELECT t.pool, t.mint, t.slot, t.side='buy' AS isbuy, t.sol_lamports AS sol, t.token_raw AS tok, t.quote_reserve AS q, t.base_reserve AS b,
   t.block_time AS bt, t.tx_index AS txi, t.event_index AS evi, t.block, t.file_row_number AS frn
   FROM read_parquet({fs!r}, file_row_number=true) t JOIN pl USING(pool) WHERE t.venue='pumpswap') TO '{OUT}' (FORMAT parquet)""")
# boundary slots: first slot with block_time >= 00:00Z of each day in the tape
rows = []
for d in sorted(set(h[:10] for h in hours)):
    ep = int(pd.Timestamp(d + 'T00:00:00Z').timestamp())
    h0 = f'{T}/trades/{d}T00.parquet'
    if os.path.exists(h0):
        s = con.execute(f"select min(slot) from read_parquet('{h0}') where block_time>={ep}").fetchone()[0]
        rows.append((d, ep, s))
pd.DataFrame(rows, columns=['day', 'epoch', 'slot0']).to_parquet(OUT.replace('.parquet', '_bounds.parquet'))
print(con.execute(f"select count(*), count(distinct pool) from read_parquet('{OUT}')").fetchone(), len(rows))
