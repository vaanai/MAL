"""P70 step 1: causal top-10 pools per UTC day by prior-day volume; counts only (no pricing)."""
import glob, os, sys, json
import duckdb
T = '/data/mal/audit-1008/tape'
OUT = sys.argv[1]
WSOL = 'So11111111111111111111111111111111111111112'
FORBID = ('2026-09-15T12', '2026-09-18T23')
hours = sorted(os.path.basename(f)[:13] for f in glob.glob(f'{T}/trades/*.parquet'))
assert not any(FORBID[0] <= h < FORBID[1] for h in hours)
hs = set(hours)
days = sorted(set(h[:10] for h in hours))
full = {d for d in days if sum(h.startswith(d) for h in hours) == 24}
from datetime import datetime, timedelta
D = lambda d, k: (datetime.strptime(d, '%Y-%m-%d') + timedelta(days=k)).strftime('%Y-%m-%d')
elig = [d for d in days if d in full and D(d, -1) in full and D(d, 1) in full]
print('hours', len(hours), 'full days', len(full), 'eligible', len(elig), elig[0], elig[-1])
con = duckdb.connect()
con.execute("SET memory_limit='5GB'; SET threads=4; SET preserve_insertion_order=false")
mf = [f for f in glob.glob(f'{T}/migrations/*.parquet')]
con.execute(f"CREATE TABLE mig AS SELECT DISTINCT mint FROM read_parquet({mf!r}) WHERE type='complete'")
print('migrated mints', con.execute('select count(*) from mig').fetchone())
vol = []
for d in sorted(full):
    fs = [f'{T}/trades/{h}.parquet' for h in hours if h.startswith(d)]
    r = con.execute(f"""SELECT '{d}' AS day, t.pool, any_value(t.mint) AS mint, sum(t.sol_lamports) AS vol, count(*) AS n, count(t.lp_fee) AS n_lpfee
       FROM read_parquet({fs!r}) t JOIN mig m ON t.mint=m.mint WHERE t.venue='pumpswap' AND t.mint<>'{WSOL}' GROUP BY t.pool""").fetchdf()
    vol.append(r)
import pandas as pd
vol = pd.concat(vol)
print('pool-days with volume', len(vol), 'lp_fee non-null share', vol.n_lpfee.sum() / vol.n.sum())
rows = []
for d in elig:
    v = vol[vol.day == D(d, -1)].sort_values(['vol', 'pool'], ascending=[False, True]).head(10)
    v = v.assign(day=d, rank=range(1, len(v) + 1), prior_vol=v.vol)
    rows.append(v[['day', 'pool', 'mint', 'rank', 'prior_vol']])
sel = pd.concat(rows)
sel.to_parquet(OUT)
print('selected pool-days', len(sel), 'days', sel.day.nunique(), 'per-day counts min', sel.groupby('day').size().min())
print('prior-day vol (SOL) median/min/max of rank1', (sel[sel['rank']==1].prior_vol/1e9).describe().to_dict())
print('rank10 vol SOL median', (sel[sel['rank']==10].prior_vol/1e9).median())
