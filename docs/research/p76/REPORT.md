# P76 REPORT: PUMP hour-16 drift (library row pump-buyback-2)

**Verdict: KILL** (both kill lines hit). Plus NOT_DECIDABLE for any SOL-denominated claim: the tape has no PUMP/SOL pool.

In-sample lab dates; at least 14 families have read this block. Paper only. Nothing here says a book makes money; no book has cleared the promotion gate.

## Data
- Mint `pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn` (from the source's s1.py). The only deep pool is 2uF4X, PUMP/USDC (proven: QP read the pool account; quote mint EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v is USDC, base mint matches s1.py). No WSOL-base pool is on the tape. A SOL trade needs SOL->USDC (Gf7sX) then USDC->PUMP.
- Held-out block: explore-0814, 2026-08-14..08-27 (the source's s4.py hour returns start 09-03, but its s2.py and s3.py read 08-14..09-25, so the hour-of-day return on this block is unread by s4.py only, not by the source as a whole). The rule fired on 12 of 14 days (2 skipped: fill print more than 600 s after trigger). Not enough for the gate (100 trades) in any case.

## Rule (PREREG.md, committed before pricing)
H = 16 UTC, M = 0, K = 60, one trade per day, lag 2 slots, PRE-trade reserves, fees 0.35% on 2uF4X (corrected below: measured median is 0.3000%) + 0.30% SOL<->USDC per leg (about 1.30% round trip), no V (USDC pool), stakes 0.25 / 1 / 5 SOL at an assumed 150 USD/SOL. The pressure-fail model equals the flat one here (no race, p = 0.15 set; disclosed in PREREG).

## Results (`p76_output.txt`, `p76_test.py`)
| stake | gross hour-16 move | buy impact | mean net / trade (flat = pressure) | total | ex-top-3 | ex-best-day | days positive | CI90 low |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.25 SOL | +10.76 bps | 0.0356 bps | -0.002533 SOL | -0.03040 | -0.03720 | -0.03392 | 3/12 | -0.004094 |
| 1 SOL | +10.76 bps | 0.1426 bps | -0.010126 SOL | -0.12151 | -0.14873 | -0.13562 | 3/12 | -0.016371 |
| 5 SOL | +10.76 bps | 0.7129 bps | -0.051074 SOL | -0.61289 | -0.74769 | -0.68306 | 3/12 | -0.082291 |

- The source's +92.96 bps did not replicate: held-out gross is +10.76 bps, far under the 130 bps fee drag. Impact is negligible at all three stakes (PUMP/USDC holds about 10.6M USDC).
- Capacity: 1 trade/day x 1 SOL. Mean net before the 15% fail model: -1.1903% = -0.011903 SOL/day. Mean after the flat fail model (the table figure): -0.010126 SOL/trade. Negative at every stake.
- Placebo (same rule, 24 hours, 1 SOL, flat): hour 16 mean -0.010126 ranks **13 of 24**. Only hours 6 (+0.001975) and 21 (+0.000839) are positive, from 10-12 trades each. Top 2 line failed.
- Both kill lines hit: mean below 0 under both models, and hour 16 is not top 2.

## Not measured
SOL/USDC price move over the hold; impact on the SOL<->USDC leg; buy-side fee on 2uF4X (observable only on sells); a real PUMP/SOL pool; any October hour or sealed data (none opened); whether 99mRw is the buyback contract.

## Post-result corrections (2026-10-27, after reviewer and QP on #1030; KILL unchanged)
1. 2uF4X is proven PUMP/USDC (pool account read by QP, see Data).
2. The 0.35% fee came from PUMP-quoted pools in boost-custom-v2, not from 2uF4X. The measured median on 2uF4X is 0.3000% over 64,536 sells. At 0.30% the 1 SOL mean is -0.009283 SOL (flat model).
3. The held-out wording is narrowed as in Data.
4. Zero-leg line: with only the pool fee (no SOL<->USDC legs), the 1 SOL mean is -0.004215 SOL at 0.30% and 5 of 12 days are positive. Still negative.
5. The two capacity means are labelled above (before vs after the fail model).
6. Method behind the USDC proof: getAccountInfo on the pool; the base mint is at bytes 43..75 and the quote mint at bytes 75..107.
7. Sensitivity at the measured 0.30% pool fee (1 SOL, flat model): 3/12 days positive; hour 16 ranks 13 of 24 on the placebo.
8. Zero-leg line at 0.35% (pool fee only): mean -0.005064 SOL, 5/12 days positive, CI90 low -0.011346.
