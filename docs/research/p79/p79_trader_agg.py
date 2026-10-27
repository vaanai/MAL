"""P79 counts: per trader, distinct PumpSwap pools and rows, split mayhem / non-mayhem mint (three exploration blocks)."""
import sys, duckdb
out = sys.argv[1]
c = duckdb.connect(); c.sql("SET memory_limit='6GB'; SET threads=4;")
c.sql("""create table tk as select mint, is_mayhem_mode mayhem from '/data/mal/hunt-shared/tokens.parquet'
 where block in ('explore-0814','fresh-0903','exp011-0909')""")
c.sql(f"""copy (select t.trader, k.mayhem, count(distinct t.pool) npools, count(*) n, sum(t.sol_lamports)/1e9 sol
 from read_parquet('/data/mal/audit-1008/tape/trades/*.parquet') t join tk k using(mint)
 where t.venue='pumpswap' and t.block in ('explore-0814','fresh-0903','exp011-0909') group by 1,2) to '{out}' (format parquet)""")
print(c.sql(f"select mayhem, count(*), sum(npools), sum(n) from '{out}' group by 1").fetchall())
