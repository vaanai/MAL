"""Compact numeric copy: mint -> dense id, block -> code. Sorted by mint, slot, tx_index, event_index."""
import sys, duckdb
c = duckdb.connect(); c.sql("SET memory_limit='6GB'; SET threads=4;")
c.sql(f"""copy (select dense_rank() over (order by mint)::int mid, (side='buy') isb, sol_lamports, token_raw, q, b, slot::bigint slot,
 (block_time - cs)::int t, cs::bigint cs, create_slot::bigint create_slot, complete_slot::bigint complete_slot,
 case block when 'explore-0814' then 0 when 'fresh-0903' then 1 else 2 end blk
 from '{sys.argv[1]}' order by mint, slot, tx_index, event_index) to '{sys.argv[2]}' (format parquet, compression zstd)""")
