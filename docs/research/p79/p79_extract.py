"""P79 extract: PumpSwap rows of mayhem and non-mayhem mints, three exploration blocks only (P55/P73 paths)."""
import sys, duckdb
out = sys.argv[1]
c = duckdb.connect(); c.sql("SET memory_limit='6GB'; SET threads=4;")
c.sql("""create table tk as select mint, is_mayhem_mode mayhem, has_create, create_ms, block tblock, complete_slot, pool tpool, v0_lamports
 from '/data/mal/hunt-shared/tokens.parquet' where block in ('explore-0814','fresh-0903','exp011-0909')""")
c.sql(f"""copy (select t.mint, t.trader, t.side, t.sol_lamports, t.token_raw, t.quote_reserve q, t.base_reserve b, t.pool, t.slot, t.tx_index, t.event_index,
  t.block_time, t.lp_fee, t.protocol_fee, t.creator_fee, t.block, k.mayhem, k.v0_lamports
 from read_parquet('/data/mal/audit-1008/tape/trades/*.parquet') t join tk k using(mint)
 where k.mayhem and t.venue='pumpswap' and t.block in ('explore-0814','fresh-0903','exp011-0909')
 order by t.pool, t.slot, t.tx_index, t.event_index) to '{out}' (format parquet, compression zstd)""")
print(c.sql(f"select mayhem, count(*), count(distinct mint), count(distinct pool), count(distinct trader) from '{out}' group by 1").fetchall())
