# P11 LAUNCHLAB IDEAS (lane P11, push 10-24)

Venue: Raydium LaunchLab on-curve trading (program LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj), constant-product curve with virtual reserves
(virtual_quote ~30 SOL, virtual_base ~1.073e15, total_base_sell ~793.1e12 on the fixture pools). Tape rows carry NO trader and NO mint: only pool,
side, amounts, pre/post real reserves, fees. So "distinct buyers" and wallet features do not exist here; every idea below uses print counts,
sizes, price and curve progress only. Progress p = real_base_after / total_base_sell (config-agnostic, 0..1).

Hypothesis common to all (one sentence, not a claim): competition on LaunchLab may be thinner than on pump.fun, so a rule that was near zero on pump
might not be near zero here. Round-trip fees on LaunchLab are ~1.5% in + ~1.0-1.5% out (measured on two fixture rows; re-measured on the exploration window).

Common universe: pool_status "fund" trades (on-curve), succeeded transactions (the backfill drops failed ones), entry only when post-trade p <= 0.85,
no signal before 2026-09-24T01 (a one-hour warm-up so a window never starts before the tape). Not in scope: FLOOR-style post-migration CPMM dip-hold
(no CPMM tape), DBC (no amounts).

## I1 EARLY-MOMENTUM (early-curve momentum)
Trigger on a BUY print with post p in [0.02, 0.35]: within the last 60 s of the pool, buys >= N, net buy SOL (buy amount_in minus sell gross out) >= S,
and post-trade spot is up between +5% and +40% from the window's first pre-trade spot. Variants (N, S): m4 = (4, 1.0 SOL), m6 = (6, 2.0), m10 = (10, 4.0).
Exit: sl 0.90, trail 0.90 on the running peak (peak starts at the decision spot), time 600 s. Cooldown 600 s per pool.

## I2 MILESTONE (curve-progress milestones)
Trigger on a BUY print that is the first in its pool to cross progress milestone m (pre-trade p < m <= post-trade p), with net buy SOL >= 1.0 over the last 120 s.
Variants m = 0.30 (ms30), 0.50 (ms50), 0.70 (ms70). Exit: tp 1.15, sl 0.90, time 900 s. One entry per pool per cell.

## I3 BIGBUY (large-buy follow)
Trigger on a BUY print with amount_in >= M SOL and its own price impact (post spot / pre spot - 1) between +2% and +25%, post p in [0.02, 0.80].
Variants M = 1 SOL (bb1), 2 SOL (bb2), 4 SOL (bb4). Exit: sl 0.90, trail 0.90, time 300 s. Cooldown 120 s per pool.

## I4 CASCADE-FADE (sell-cascade fade)
Trigger on a SELL print with post p >= 0.05: within the last 30 s, sell prints >= K, sell gross SOL >= S, and the post-trade spot is down >= d from the window's
highest post-trade spot (window peak includes the pre-trade spot of the first print in the window).
Variants (K, S, d): c3 = (3, 1.0, 10%), c4 = (4, 2.0, 15%), c6 = (6, 4.0, 20%). Exit: tp 1.10, sl 0.92, time 180 s. Cooldown 120 s per pool.

## Rate bar
>= 3 attempts/day averaged over the exploration window (counts first; a cell below the bar is dropped unpriced and said so). 4 ideas x 3 variants = 12 cells; nothing else is tried;
no parameter changes after a count.
