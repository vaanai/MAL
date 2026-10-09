"""C1 v2 meta. universe.parquet: post-grad universe (pump.fun 'complete' graduation, canonical PumpSwap pool with V in [17.5e9, 17.7e9],
pool's first print within [-5 s, +120 s] of the complete row; known at graduation time, no survivorship). creates.parquet: every create on the tape
(for creator track record / narrative heat, used strictly as-of). mkt.parquet: per-minute market activity from the shared bars."""
import duckdb, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from common2 import *
c = duckdb.connect(); c.execute(f"SET memory_limit='6GB'; SET threads=3; SET temp_directory='{TMP}'; SET max_temp_directory_size='8GB'")
c.execute(f"""COPY (SELECT row_number() OVER (ORDER BY complete_ms, mint) mid, mint, pool, v0_lamports v, complete_ms/1000 g, complete_slot gslot,
     strftime(to_timestamp(complete_ms/1000), '%Y-%m-%d') gday, create_ms/1000 cbt, hash(creator) ch, creator, name, symbol, is_mayhem_mode mayhem, has_create,
     (complete_ms - create_ms)/1000.0 bc_dur, bc_n_trades, bc_n_traders, bc_buy_sol, bc_sell_sol, ps_first_price
   FROM '{SH}/tokens.parquet' WHERE grad_src='complete' AND v0_lamports BETWEEN 17.5e9 AND 17.7e9 AND (ps_first_ms - complete_ms) BETWEEN -5000 AND 120000)
   TO '{O}/work/universe.parquet' (FORMAT parquet, COMPRESSION zstd)""")
c.execute(f"""COPY (SELECT mint, hash(creator) ch, name, symbol, create_ms/1000 cbt, complete_ms/1000 gbt FROM '{SH}/tokens.parquet' WHERE has_create)
   TO '{O}/work/creates.parquet' (FORMAT parquet, COMPRESSION zstd)""")
c.execute(f"""COPY (SELECT minute_ms//1000 mnt, sum(CASE WHEN venue='pumpswap' THEN n_buys+n_sells ELSE 0 END) n_ps,
     sum(CASE WHEN venue='pumpswap' THEN coalesce(buy_sol,0)+coalesce(sell_sol,0) ELSE 0 END) v_ps,
     sum(CASE WHEN venue='pump_bonding' THEN n_buys+n_sells ELSE 0 END) n_bd,
     sum(CASE WHEN venue='pump_bonding' THEN coalesce(buy_sol,0)+coalesce(sell_sol,0) ELSE 0 END) v_bd
   FROM read_parquet('{SH}/bars_1m/*/*.parquet') GROUP BY 1 ORDER BY 1) TO '{O}/work/mkt.parquet' (FORMAT parquet, COMPRESSION zstd)""")
print(c.sql(f"SELECT gday, count(*) n, sum(has_create::INT) hc FROM '{O}/work/universe.parquet' GROUP BY 1 ORDER BY 1").df().to_string())
print(c.sql(f"SELECT count(*), min(mnt), max(mnt) FROM '{O}/work/mkt.parquet'"))
