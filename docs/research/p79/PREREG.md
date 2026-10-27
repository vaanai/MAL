# P79 pre-registration: Mayhem agent flow on PumpSwap (library rows mayhem-flow-1/-2, scheduled-flows-2)

Written 2026-10-27T14:1xZ after the step-1 counts (`p79_counts_output.txt`, committed) and BEFORE any price, entry state, exit state or P&L of this rule was computed. Exploration tape only: the three blocks P55 and P73 used (explore-0814, fresh-0903, exp011-0909) from `/data/mal/audit-1008/tape/trades` (`block` filter) plus `/data/mal/hunt-shared/tokens.parquet`. No October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output. These dates are in-sample for the lab (at least 14 families have read them), so a positive result is a lead, never a pass. No book has cleared the promotion gate.

## What the counts showed (inputs to the rule, no price used)

- Mayhem PumpSwap universe: 1,922,459 rows, 9,211 pools, 9,203 mints, 18,241 traders. P73 showed mayhem BONDING rows break constant product, so nothing is priced on the bonding curve. On PumpSwap the pre-trade chain holds: token conservation 99.8835% (buys) and 99.6831% (sells), k drift under 1e-3 on 99.9908% and 99.7187%, both together on 99.6652% of 1,913,248 consecutive pairs. That is above the 99% condition, so the question is decidable.
- Agent wallet `BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s`: in 6,066 of 9,211 mayhem pools (65.86%), in 0 non-mayhem pools (0 rows; the other 6.7M traders' pools are in the aggregate). 355,954 rows, 53,510.5 SOL, 48.92% buys.
- Its sides are a coin flip: after a buy, next is buy 85,973 / sell 88,089; after a sell, buy 85,123 / sell 90,703. Sign is independent of the previous sign. What is predictable is timing (starts at slot 0 to 6 after the pool's first row, trades about every slot, median span 95.5 slots) and size (median 18.1% of the pre-trade quote reserve).
- Mayhem pools are tiny: first-row quote reserve median 0.0903 SOL (q10 0.0214, q90 0.9671); the control hour shows non-mayhem 9.74 SOL median. This is the capacity problem.
- Pool V: the live mayhem pool in `tools/fixtures/pumpswap/t22_mayhem_accounts.json` has flags `0100` at bytes 243..245 (mayhem flag set) and a stored V of the order of -1e4 lamports, i.e. V = 0 for pricing. The historical pools' account bytes are not on the tape, so byte 243 is NOT verified per pool: the mayhem label is `is_mayhem_mode` from tokens.parquet (as P73). The library row boost-universe-4 records 0 BOOSTed mayhem pools in these blocks. V = 0 is used for every pool; this is an assumption, stated, not measured.

## The one rule (no grid)

Per pool of a mayhem mint, with the agent wallet above, in time order (slot, tx_index, event_index):

1. Signal: the pool's last two agent trades are both SELLS (no agent buy between them), and the second one is within 150 slots of the pool's first row. Decision slot = that print's slot, S0. First signal per pool only; one position per pool.
2. Entry: the buy lands at X = S0 + 2 slots (entry lag 2). State = PRE-trade state of the first print with slot > X (the P55 `state(X)` convention; PumpSwap tape reserves are PRE-trade, verified above by the token-conservation chain). Skip if no print with slot > X exists in the pool, or if that state's quote reserve is below 1.0 SOL (depth filter fixed now from the first-row distribution: it keeps about the top decile of pools; it is not tuned).
3. Exit: fixed horizon. The exit triggers at X + 20 slots and the sell lands at X + 22 (exit lag 2), state = pre-trade state of the first print with slot > X + 22, or the last post-trade state if none.
4. Stake 0.25 SOL primary; 1 SOL reported alongside, never selected between. Same signal, same filter at both.
5. Math as `docs/research/p55/p55_price.py`: constant product on the tape reserves (V = 0), fee = `PUMPSWAP_SOL_FEE_TIERS` tier of the state's mcap proxy `Q/B*1e6` applied to the SOL leg on the buy and on the sell proceeds, priority 55,000 lamports per send, 2 sends. pnl = proceeds - stake - 2 * 55,000.
6. Fail models on the buy, both reported: flat 15% (`0.85*pnl + 0.15*(-55,000)`); pressure-fail at slope scale 1 with `tools.latency_curve.fit_curve` (b_slot 0.8, b_sol 0.35, intercept fitted so mean p = 0.289 on these entries); pressure = buys in the entry slot X plus buy SOL in the five slots up to X, from all traders' prints.

## Reported numbers

Trades per day (over the 28 UTC days with data), mean SOL per trade and as percent of stake, total, total without the top 3 trades, total without the best UTC day, number of days with positive total (of days with a trade), CI90 lower bound (5th percentile of 1,000 bootstrap draws, seed 1; trade-level bootstrap is the gate definition; a day-cluster bootstrap is reported next to it), both fail models, both stakes. Capacity line: trades per day x stake x net mean edge per trade, after the impact that the constant-product fill already contains at 0.25 and 1 SOL.

## Kill lines (any one gives KILL, at 0.25 SOL)

1. Mean <= 0 under either fail model.
2. Fewer than 3 trades per day (entries / 28 days).
3. Total without the top 3 trades <= 0, or total without the best day <= 0, under either model (a result driven by the top 3 or one day).
4. Fewer than a majority of trade days positive under either model.

If the depth filter leaves fewer than 100 entries the verdict is NOT_DECIDABLE (too few to measure). A LEAD needs all kill lines clear and says only that a pre-registered forward test on a new block is worth writing.
