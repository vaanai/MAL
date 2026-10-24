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

## I5 GRAD-FLOOR (added 2026-10-24 by manager15 scope addition, before any count or price; NOT PRICED in this lane)
FLOOR-style post-graduation idea: a LaunchLab token that graduates to a Raydium CPMM pool; enter on the CPMM pool after graduation, long hold (take-profit +50%, 24 h cap, as FLOOR v2 in tools/arena_rule_l1.py).
Tape status: the P10 backfill fetched only the LaunchLab program (LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj). CPMM pool trades are NOT on the tape, and the LaunchLab TradeEvent/`ix` rows carry no
destination pool address (the tape stores instruction names only for `ix` rows, no accounts). So I5 cannot be counted or priced; it is specified here only.
Extra fetch needed (manager decides):
 1. Migrate transactions: the LaunchLab `ix` rows whose names include the migrate instruction (MigrateToCpswap / MigrateToAmm) give signatures; getTransaction on each (jsonParsed, accounts + inner instructions)
    yields the new CPMM pool address and the token mint. Cost ~ 1 call per migration (Helius getTransaction, 50 credits/call at the backfill's measured --credits-per-call).
 2. CPMM trades: Raydium CPMM program CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C (executable on mainnet, checked 10-24 by P8) -- the program-wide stream is router noise (458 notices in 90 s), so fetch per POOL:
    getTransactionsForAddress(pool address) from the migration time for 24 h + 1 h per graduated pool, decode CPMM swap events (SwapEvent: pool, input/output vault balances before/after, fees; decoder NOT written yet).
 3. Credits estimate: migrations/day M (counted from the exploration `ix` rows in this lane's count step; reported in the PR) x (1 + P pages per pool-day). Rough planning figure until M is counted:
    M x (50 + 24 h of pool history at ~50 credits per 100 txs, unknown tx count) -- cannot be bounded before the first pool is fetched; propose a 5-pool probe fetch (<= ~5,000 credits) before a full run.
Cell pre-declaration if fetched: entry = first CPMM state >= 1.9 s after graduation + 5 min; exit tp 1.5 / time 24 h; stakes 0.25 and 1.0 SOL; same fail models; decided on a separate DECLARE then.
