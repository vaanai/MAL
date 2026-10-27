# P76 pre-registration: PUMP hour-16 drift (library row pump-buyback-2)

Written before any pricing. Counts below were taken first; no return of this rule was computed before this file was committed. Exploration data only, in-sample for the lab (at least 14 families have read these dates). A positive is a lead, never a pass. No book has cleared the promotion gate.

## Data check (counts only)
- Source: library row pump-buyback-2, evidence `/data/mal/hunt-1008/push-1016/hunt5/pump-buyback/s4.py`. Mint `pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn` (from that source's s1.py). Tape: `pump_trades.parquet` extract of `/data/mal/audit-1008/tape/trades` (exploration blocks only).
- There is NO PUMP/SOL pool on the tape (the tape holds no WSOL-base pools; the source and boost-custom-v2 report this). The row's pool is `2uF4Xh61rDwxnG9woyxsVQP7zuA6kLFpb3NvnRQeoiSd`, read as PUMP/USDC (inferred from price scale, not proven). So a SOL-denominated claim is NOT_DECIDABLE from this tape; a SOL trade needs two hops (SOL->USDC on pool Gf7sX, USDC->PUMP on 2uF4X). The test below is a USDC-pool test with the SOL legs charged as a declared cost. It does not measure the SOL/USDC price move over the hold (no SOL price on the tape).
- 2uF4X prints: 1,499,015 over 36 tape days; 24 of 24 hours present on full days.
- The source read the hours from 2026-09-03 onward (440 hours, s4.py `block_time>=1788393600`). Held-out for this rule: explore-0814, 2026-08-14T12 to 2026-08-28T12, 14 UTC days (08-14 .. 08-27), each with hour 16 and hour 17 present. The 09-03+ days are NOT used (source-read). The held-out block is still an exploration block read by other families.

## Rule (one parameter set, no grid)
- H = 16 UTC (source: +92.96 bps, se 31.40, within-hour first-to-last).
- M = 0 minutes, K = 60 minutes: trigger-in at the first 2uF4X print with block_time >= H:00; trigger-out at the first print with block_time >= (H+1):00 (same window as the source's measurement). One trade per UTC day.
- Both fills at lag 2 slots after the trigger print, priced at the PRE-trade reserves of the first print with slot >= trigger_slot + 2. PumpSwap tape reserves are PRE-trade (verified against the repo convention). A trade is skipped if either fill print is more than 600 s after its trigger.
- Pricing: constant product on the pool's quote/base reserves. No virtual reserve V: 2uF4X is a USDC pool, V does not apply [stated, not measured here]. Fee on 2uF4X 0.35% per leg (boost-custom-v2 measured about 0.35% on this pool; buy-side fee is not observable). SOL<->USDC legs 0.30% per leg (boost-custom-v2 conversion model, pool Gf7sX), no impact modelled on that leg (deep pool; assumption). Round trip fee drag about 1.30%.
- Stakes 0.25, 1, 5 SOL, converted at an assumed 150 USD/SOL (not on the tape; impact on 2uF4X is tiny at these sizes, so the result is insensitive to this within a few times). Impact is reported at each stake.
- Fail models: flat `0.85*pnl + 0.15*(-55,000 lamports)`. Pressure-fail at slope 1 needs a per-trade p from the latency curve, which belongs to contested pump snipes; a clock trade has no race, so p is set to 0.15 and the pressure model equals the flat one here (disclosed deviation).
- Placebo: the identical rule at each of the 24 hours (entry at H:00, exit at H+1:00, same days, same costs). Rank by mean net per trade at 1 SOL under the flat model.

## Kill lines
1. Mean net per trade <= 0 under either fail model (at 1 SOL).
2. Hour 16 not in the top 2 of 24 on the placebo.
Also reported: total without the top 3 trades; without the best day; days with a majority positive (one trade per day, so a day is positive or not); CI90 lower bound (1,000 bootstrap draws, seed 1, 5th percentile); capacity = trades/day x stake x net edge, at 0.25 and 1 SOL.
Verdict vocabulary: KILL, LEAD, NOT_DECIDABLE. Gate-level claims need at least 100 trades and 5 days; 14 trades cannot clear it, so the best possible outcome is LEAD.

## Post-result corrections (2026-10-27; the body above is unchanged)
- 2uF4X is proven PUMP/USDC (QP read the pool account; quote mint EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v), not merely inferred.
- The 0.35% fee on 2uF4X was taken from PUMP-quoted pools in boost-custom-v2; the measured median on 2uF4X is 0.3000% over 64,536 sells. The registered rule used 0.35%.
- The source's s4.py hour returns start 09-03, but its s2.py and s3.py read 08-14..09-25; "held-out" means unread by s4.py only.
