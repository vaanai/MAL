# P87 pre-registration: B7 dip-absorption pullback (one rule, no grid)

Written 2026-10-27 14:3xZ before any count or pricing of this lane. Lane P87, manager17. Exploration only; paper only. These are in-sample lab dates that at least 14 families have already read (EXP-024 sec. 9, m >= 14): a positive result is a LEAD, never a pass. No book has cleared the promotion gate.

Why: P78 (PR #1041) measured the B7 (market cap >= 98,240 SOL) round-trip floor at 0.6479% (0.25 SOL), 151.9 tradeable pools/day and 990.1 tradeable pool-hours/day. Its frozen 15-min momentum rule lost -5.69432% (flat) there and 15% of its trades lost more than 50%. This lane tests the opposite shape once: buy a dip that shows absorption, in a pool that is not a fresh pump.

## Data and code (reused unchanged from P78, PR #1041 branch claude/p78-cost-floor-tiers)
- `/data2/mal/p78/rows/*.parquet` (slim rows of the three exploration clean views: explore-0814, fresh-0903, exp011-0909; PumpSwap wSOL `...pump`; market cap >= 9,820 SOL retained; PRE-trade reserves verified in P78, 0.99993). Band assignment (`mc` column, edges in `p78_common.EDGES`), `p78_common.load_day/segments/pool_sps/gate/report`, `p72_run_frozen.tier/pool_arrays/price_one`, V as in P78 (17.5845 SOL where the mint is in the P55 meta, else 0; quote = q+V). Fee = real tier fee at the state, 2 x 55,000 lamports priority, exit lag 2, both fail models (flat 15%; pressure-fail slope 1 fitted on the rule's own trades), CI90 = date-cluster bootstrap 1,000 draws seed 1, 5th percentile. Not read: any October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50.
- Retention caveat inherited from P78: a pool's series is cut at a gap of more than 150 slots between retained prints (below 9,820 SOL is not retained). A position whose pool falls out of range is priced at the last retained state, which flatters it (`censored` counts these, reported).

## The rule (one parameter set)
- Universe: B7 pool-hours that are tradeable by P78's definition: pool-hour (pool, UTC hour of block_time) with median pre-trade market cap >= 98,240 SOL, >= 20 prints in the hour and median depth q+V >= 100 SOL. The signal print must itself have pre-trade market cap >= 98,240 SOL and sit in such a pool-hour.
- Entry signal at print i (all causal, using prints strictly before i for flow):
  1. Dip: pre-trade price at i is at least 8% below the pre-trade price at the last print at least 900 s earlier (900 s converted at the pool's slots/s, as P78-M): price_i / price_ref - 1 <= -0.08.
  2. Absorption: net buy flow over the last 60 s (prints with slot in [slot_i - round(60/sps), slot_i), sum of buy SOL minus sell SOL) is > 0.
  3. Age: block time of print i minus the pool's first retained print with market cap >= 98,240 SOL (over all 28 days) is >= 6 h (21,600 s). Pools first seen in B7 on the first tape day count from that first print (conservative: they look younger than they are).
- Size 0.25 SOL, entry lag 2 slots (buy lands at slot_i + 2, priced on the state after that slot, own impact included).
- Exit: first of +6% (net of the sell-leg fee and impact, relative to stake, as P72's marks), -8% (same basis), or 45 min (2,700 s at the pool's slots/s) after the entry; exit lag 2 slots, price fills at the state after the lag (the actual print). One open position per pool: a pool is re-armed only after the exit landed.
- Thresholds are NOT tuned. If a count shows the rule is structurally broken (e.g. zero signals), the single change is written here before pricing, with the reason.

## Counts first
Funnel counts per condition (dip only; dip + absorption; + age; + tradeable pool-hour; trades after one-open-position), per day and per block, before any pricing. Pricing runs after the counts are pushed.

## Report
Trades/day and days covered; mean per trade under both fail models (% of stake and SOL); total without the top 3 trades; total without the best UTC day; days positive and whether a majority; CI90 lower bound of the mean; tail share (trades with flat-model pnl below -50% of stake); capacity at 0.25 and 1 SOL (trades/day x stake x net edge of the lower model; 1 SOL re-priced with its own impact on the same entries); all of the above per block (explore-0814, fresh-0903, exp011-0909) as well as pooled.

## Capacity line
Capacity (SOL/day) = trades/day x stake x lower-model mean edge after impact, at 0.25 SOL and at 1 SOL. A LEAD needs this to be positive at 0.25 SOL; it is reported for 1 SOL for information.

## Kill line (frozen now)
KILL if any of: mean <= 0 under either fail model (pooled); the result is driven by the top 3 (total without the top 3 <= 0 while the pooled total is > 0); fewer than 5 trades/day. Otherwise LEAD (in-sample only). NOT_DECIDABLE if the counts give fewer than 100 trades. The promotion-gate items (>= 100 trades, >= 5 days with majority positive, CI90 lower bound > 0, positive ex-top-3) are reported for information only.

## Seals
Aug/Sep exploration only: no October hour, arena tape, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 data or job. The ledger lists these three blocks as exploration pool.

## Amendment 1 (2026-10-27, before any pricing; counts of the unfiltered funnel had been run and are kept in p87_count_output history only as a disclosed first look)
Source: coordinator message relaying the P78 QP review (#1041): B7 contains quiet/pegged pools and possible supply anomalies (median mcap about 224k SOL, 25 pools above 10M SOL, 211 pools flat for >= 24 h, 35% of tradeable B7 pool-hours with median 2-slot move < 0.001%). Added to the universe, still one parameter set:
- Exclude pool-hours whose median computed market cap is above 5,000,000 SOL (supply/decimals anomaly).
- Exclude signals where the trailing 6 h realised range of the pre-trade price (max/min - 1 over retained prints of the pool segment in the 6 h up to and including the signal print) is under 2%. At the start of the tape and after a >150-slot retention gap the window is shorter, which makes the filter stricter, not looser.
- Reported: tradeable pool-hours dropped by the mcap filter, signal prints and distinct pool-hours dropped by the range filter. Also reported: composition of traded pools (entry mcap quantiles, distinct pools, top-5 pools' share of trades).
Thresholds 5M and 2% are as given, not tuned.
