"""P79 step-1 counts only (no price outcome). Usage: python -I p79_counts.py PS_MAYHEM.parquet TRADER_AGG.parquet"""
import sys, duckdb
P, A = sys.argv[1], sys.argv[2]
W = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
c = duckdb.connect()
def q(s): return c.sql(s).fetchall()
print("== mayhem PumpSwap universe (is_mayhem_mode mints, 3 exploration blocks)")
print(q(f"select count(*), count(distinct pool), count(distinct mint), count(distinct trader), count(distinct block) from '{P}'"))
print("== non-mayhem pool count proxy: tokens.parquet non-mayhem mints with ps_n_trades>0")
print(q("select count(*) filter (where ps_n_trades>0), sum(n_pumpswap_pools) from '/data/mal/hunt-shared/tokens.parquet' where not is_mayhem_mode and block in ('explore-0814','fresh-0903','exp011-0909')"))
print("== top mayhem-pool coverage wallets: (trader, mayhem pools, share of 9211, nonmayhem pools, nonmayhem rows)")
for r in q(f"""select m.trader, m.npools, round(m.npools/9211.0,4), coalesce(n.npools,0), coalesce(n.n,0), m.n, round(m.sol,1)
 from (select * from '{A}' where mayhem) m left join (select * from '{A}' where not mayhem) n using(trader) order by m.npools desc limit 12"""): print(r)
c.sql(f"""create table r as select *, row_number() over (partition by pool order by slot, tx_index, event_index) rn,
  min(slot) over (partition by pool) s0, min(block_time) over (partition by pool) t0 from '{P}'""")
print("== constant-product / token-conservation check on consecutive same-pool PRE-trade rows (all mayhem pools)")
c.sql("""create table cp as select side, token_raw, q, b, lag(q) over w pq, lag(b) over w pb, lag(side) over w ps, lag(token_raw) over w ptok
  from r window w as (partition by pool order by slot, tx_index, event_index)""")
print(q("""select ps, count(*), avg((abs((pb-case when ps='buy' then ptok else -ptok end)-b)<=1)::int) tok_ok,
  avg((abs(q*b/(pq*pb)-1)<1e-3)::int) k_1e3, avg((abs(q*b/(pq*pb)-1)<1e-4)::int) k_1e4
  from cp where pq is not null and pq>0 and pb>0 group by 1"""))
print(q("""select count(*), avg((abs((pb-case when ps='buy' then ptok else -ptok end)-b)<=1 and abs(q*b/(pq*pb)-1)<1e-3)::int) both_ok
  from cp where pq is not null and pq>0 and pb>0"""))
print("== agent BwWK17 activity")
print(q(f"select count(distinct pool), count(*), avg((side='buy')::int), sum(sol_lamports)/1e9 from r where trader='{W}'"))
print("pools with agent / all mayhem pools, by block:")
print(q(f"select block, count(distinct pool) filter (where trader='{W}'), count(distinct pool), round(count(distinct pool) filter (where trader='{W}')/count(distinct pool)::float,4) from r group by 1 order by 1"))
print("trades per pool q10/q50/q90/mean:", q(f"select quantile_cont(n,[.1,.5,.9]), avg(n) from (select count(*) n from r where trader='{W}' group by pool)"))
print("first agent trade, slots after pool first row, q10/q50/q90/q99:", q(f"select quantile_cont(d,[.1,.5,.9,.99]) from (select min(slot)-any_value(s0) d from r where trader='{W}' group by pool)"))
print("first agent trade seconds after pool first row (block_time):", q(f"select quantile_cont(d,[.1,.5,.9,.99]) from (select min(block_time)-any_value(t0) d from r where trader='{W}' group by pool)"))
print("agent active span (last-first agent slot) q10/q50/q90/q99:", q(f"select quantile_cont(d,[.1,.5,.9,.99]) from (select max(slot)-min(slot) d from r where trader='{W}' group by pool)"))
print("last agent trade slots after pool first row q10/q50/q90/q99:", q(f"select quantile_cont(d,[.1,.5,.9,.99]) from (select max(slot)-any_value(s0) d from r where trader='{W}' group by pool)"))
print("share of agent trades within 20/50/100 slots of pool first row:", q(f"select avg((slot-s0<=20)::int), avg((slot-s0<=50)::int), avg((slot-s0<=100)::int) from r where trader='{W}'"))
print("agent size SOL q10/q50/q90:", q(f"select quantile_cont(sol_lamports/1e9,[.1,.5,.9]) from r where trader='{W}'"))
print("agent size / pre-trade quote reserve (V=0 on mayhem pools, fixture pool A) q10/q50/q90:", q(f"select quantile_cont(sol_lamports/q,[.1,.5,.9]) from r where trader='{W}' and q>0"))
print("pre-trade quote reserve at agent trades, SOL q10/q50/q90:", q(f"select quantile_cont(q/1e9,[.1,.5,.9]) from r where trader='{W}'"))
print("pool first-row quote reserve SOL q10/q50/q90:", q("select quantile_cont(q/1e9,[.1,.5,.9]) from r where rn=1"))
print("agent share of pool volume in its first 20 slots:", q(f"select sum(case when trader='{W}' then sol_lamports end)/sum(sol_lamports) from r where slot-s0<=20"))
print("agent sign transitions (prev agent side -> next agent side) within pool:")
print(q(f"""select ps, side, count(*) from (select side, lag(side) over (partition by pool order by slot, tx_index, event_index) ps from r where trader='{W}') where ps is not null group by 1,2 order by 1,2"""))
print("agent trades in same slot as previous agent trade share:", q(f"select avg((slot=ps)::int) from (select slot, lag(slot) over (partition by pool order by slot, tx_index, event_index) ps from r where trader='{W}') where ps is not null"))
print("agent trades per UTC day (pools active):", q(f"select strftime(to_timestamp(block_time),'%Y-%m-%d') d, count(distinct pool) from r where trader='{W}' group by 1 order by 1"))
