# P79: Mayhem agent flow on PumpSwap. Verdict: KILL

Lane P79, 2026-10-27. Exploration tape only (explore-0814, fresh-0903, exp011-0909; `/data/mal/audit-1008/tape/trades`, `block` filter). These dates are in-sample for the lab (at least 14 families have read them): a positive would have been a lead, never a pass. No book has cleared the promotion gate. `PREREG.md` (commit f9c350e2) was pushed after the step-1 counts and before any price or P&L. The rule is the single pre-registered one, no grid.

## Step 1: counts (p79_counts_output.txt)

- Universe: 1,922,459 PumpSwap rows, 9,211 pools, 9,203 mints with `is_mayhem_mode`, 18,241 traders. Nothing is priced on the bonding curve (P73).
- Constant product on PumpSwap mayhem pairs (consecutive pre-trade rows, same pool, 1,913,248 pairs): token conservation 99.8835% (buys) / 99.6831% (sells); k drift under 1e-3 on 99.9908% / 99.7187%; both tests together 99.6652%. This clears the 99% condition, so the study is decidable. (Opposite of P73's bonding result.)
- Agent wallet `BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s`: signer in 6,066 of 9,211 mayhem pools (65.86%; by block 65.15% explore-0814, 55.42% fresh-0903, 37.73% exp011-0909) and in 0 non-mayhem pools (0 rows against about 19,800 non-mayhem mints with PumpSwap trades). 355,954 rows, 53,510.5 SOL volume. The next mayhem-only wallets are `Casino6wk...` (2,823 pools, 0 non-mayhem, 13,351 rows) and `152HmqBs...` (2,478 pools, 0 non-mayhem, 23,061 rows); `Axja4d..`, `3BDBAy..`, `6UuMWN..`, `6cHyfC..` have 14 or fewer non-mayhem pools. Several mayhem-wide wallets (`27HFm..`, `Fs9RN..`, `3KcPS..`) are zero-SOL spam present in non-mayhem pools too, so not agents.
- Agent activity: 58.68 trades per pool on average (q10/q50/q90 12 / 44 / 123); first trade 0 / 1 / 6 slots after the pool's first row (q10/q50/q90; 0 / 0 / 2 seconds by block_time); last trade 25 / 98.5 / 360.5 slots after the first row; 16.2% / 37.0% / 60.3% of its trades fall within 20 / 50 / 100 slots; 0.36% of its trades share a slot with its previous one; it is 53.4% of pool SOL volume in the first 20 slots.
- Mix: 48.92% buys. Sides are a coin flip: buy->buy 85,973, buy->sell 88,089, sell->buy 85,123, sell->sell 90,703 (matches the hunt5 bonding finding). Size: median 0.0157 SOL, q10 0.0024, q90 0.1889; median 18.1% of the pre-trade quote reserve (q10 7.0%, q90 34.8%), so each agent trade moves the price a lot, but its direction is not predictable.
- Depth: mayhem pools are tiny. First-row quote reserve q10/q50/q90 0.0214 / 0.0903 / 0.9671 SOL; control hour 2026-09-05T12 median max reserve 0.058 SOL (mayhem, 84 pools) vs 9.80 SOL (non-mayhem, 787 pools). Trades per UTC day with the agent present: 109 to 358 pools.

## Step 2: the rule and the result (p79_price_output.txt)

Rule: after the agent's second consecutive SELL in a pool (within 150 slots of the first row), buy 0.25 SOL landing 2 slots later, exit 20 slots after that landing with exit lag 2; entry state quote reserve at least 1.0 SOL; PumpSwap pre-trade reserves, V = 0, tier fee on both legs, 55,000 lamports per send; flat 15% and pressure-fail (mean p 0.289 on 661 entries).

5,857 pools produced the signal; 5,196 failed the 1 SOL depth filter (88.7%); 661 entries over 28 UTC days, 23.607 per day. 5 entries, all at the 1 SOL stake and none at 0.25 SOL, had the held tokens exceed the pool's remaining base at exit and were booked as a total loss (conservative; stated in the script). The 0.25 SOL primary is unaffected.

| 0.25 SOL | flat 15% | pressure-fail |
| --- | --- | --- |
| mean SOL per trade | -0.034346 (-13.738% of stake) | -0.026466 (-10.586%) |
| total | -22.7027 | -17.4941 |
| total without top 3 | -30.2516 | -24.5583 |
| total without best day (2026-08-18) | -25.4572 | -20.2771 |
| days positive | 6 of 28 | 7 of 28 |
| CI90 lower bound of mean (trade-level, 1,000 draws, seed 1) | -0.051181 | -0.041589 |
| CI90 lower bound (day-cluster) | -0.048968 | -0.039482 |

No-fail gross mean at 0.25 SOL is -0.040397 SOL (median -0.131365; 25.57% of trades positive). Fail models make it less negative only because a failed buy costs 55,000 lamports instead of the round trip.

| 1 SOL | flat 15% | pressure-fail |
| --- | --- | --- |
| mean SOL per trade | -0.189806 (-18.981%) | -0.155512 (-15.551%) |
| total / ex top 3 / ex best day | -125.4621 / -147.0085 / -131.9323 | -102.7932 / -121.8327 / -108.2303 |
| days positive | 2 of 28 | 2 of 28 |
| CI90 lower bound (trade-level) | -0.239346 | -0.198284 |

Capacity line (trades/day x stake x mean edge, impact already inside the constant-product fill): 0.25 SOL: 23.607 x 0.25 x -0.13738 = -0.81081 SOL/day (flat), -0.62479 (pressure). 1 SOL: -4.48079 / -3.67119 SOL/day.

## Kill lines (PREREG)

1. Mean <= 0 under either model: HIT (both, both stakes).
2. Fewer than 3 trades per day: clear (23.6).
3. Driven by top 3 or one day: not applicable, the total is negative before and after dropping them.
4. Majority of days positive: HIT (6 and 7 of 28).

Verdict KILL. Reading: the agent's sign is independent of its history, so a buy after its sells has no information beyond the price drop that it caused, and the round trip costs the 1.25% tier on both legs plus the spread of impact in a pool that holds about 2.4 SOL at the median entry. Buying where it sold does not recover the impact inside 20 slots.

## Not measured

- The pool account byte-243 mayhem flag per historical pool: not on the tape; the label is `is_mayhem_mode` from tokens.parquet. V = 0 was applied to all pools. QP's per-pool measurement from the tape (PR #1035 comment 6444359021, QP OK at dd37b6eb) replaces the earlier fixture-based assumption: implied V is at most about 0.0002 x quote; on pools with quote of at least 1 SOL the median V is within +-0.0004 SOL. Repricing with V from -1 to +17.58 SOL keeps the 0.25 SOL flat mean between -0.034 and -0.047, so the sign never flips.
- Alternative rules (other horizons, buy after buy bursts, selling into agent buys, entry lag other than 2, depth filters below 1 SOL, the non-agent mayhem wallets): none priced. One rule, one parameter set.
- Any October hour, and the agent's behaviour after the exploration period.
- Fee legs come from the P55 tier function, not from the empty `lp_fee/protocol_fee/creator_fee` columns of the tape.
