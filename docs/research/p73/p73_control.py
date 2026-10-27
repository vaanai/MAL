"""P73 control: same constant-product check on one exploration hour, mayhem vs non-mayhem bonding mints. No pricing."""
import duckdb, numpy as np
c = duckdb.connect(); c.sql("SET memory_limit='4GB'")
d = c.sql("""select t.mint, t.side, t.sol_lamports sol, t.quote_reserve q, t.base_reserve b, t.slot, t.tx_index, t.event_index, coalesce(k.is_mayhem_mode,false) my
 from '/data/mal/audit-1008/tape/trades/2026-09-05T12.parquet' t left join '/data/mal/hunt-shared/tokens.parquet' k using(mint)
 where t.venue='pump_bonding' and t.block='fresh-0903' order by t.mint, t.slot, t.tx_index, t.event_index""").df()
same = (d.mint.values[1:] == d.mint.values[:-1])
q = d.q.values; b = d.b.values
pos = (q[1:] > 0) & (q[:-1] > 0) & (b[1:] > 0)
kd = np.abs(q[1:] * b[1:] / (q[:-1] * b[:-1] + 1e-300) - 1)
for lab, my in (("mayhem", True), ("non-mayhem", False)):
    m = same & pos & (d.my.values[1:] == my)
    print(lab, "rows", int((d.my == my).sum()), "q==0 share", round(float((q[d.my.values == my] == 0).mean()), 5), "pairs", int(m.sum()),
          "share |k drift|<1e-6", round(float((kd[m] < 1e-6).mean()), 5), "share <1e-3", round(float((kd[m] < 1e-3).mean()), 5), "median", float(np.median(kd[m])))
