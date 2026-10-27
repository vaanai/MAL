"""P73 extract: bonding rows of mayhem mints, three exploration blocks only. Run under systemd-run MemoryMax=8G."""
import sys, duckdb
out = sys.argv[1]
c = duckdb.connect(); c.sql("SET memory_limit='6GB'; SET threads=4;")
c.sql("""create table my as select mint, create_ms/1000 cs, create_slot, complete_slot from '/data/mal/hunt-shared/tokens.parquet'
 where is_mayhem_mode and has_create and block in ('explore-0814','fresh-0903','exp011-0909')""")
print("mayhem mints", c.sql("select count(*) from my").fetchall())
c.sql(f"""copy (select t.mint, t.side, t.sol_lamports, t.token_raw, t.quote_reserve q, t.base_reserve b, t.slot, t.tx_index, t.event_index, t.block_time, t.block, m.cs, m.create_slot, m.complete_slot
 from read_parquet('/data/mal/audit-1008/tape/trades/*.parquet') t join my m using(mint)
 where t.venue='pump_bonding' and t.block in ('explore-0814','fresh-0903','exp011-0909')
 order by t.mint, t.slot, t.tx_index, t.event_index) to '{out}' (format parquet, compression zstd)""")
print(c.sql(f"select count(*), count(distinct mint), count(distinct block) from '{out}'").fetchall())
