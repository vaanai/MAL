# P74 pre-registration: Graduation play (library row web-oct-mechanics-1)

Written before any pricing and before any outcome of this rule was computed. Exploration tape only. No book has cleared the promotion gate. These dates are in-sample for the lab: at least 14 families have already read them, so a positive here is a lead, never a pass.

Overlap check: P73 chose mayhem-flow-2 (branch claude/p73-mayhem-collapse-fade, head d03d83fc). No overlap.

Prior art on this tape: bc-runup-1 (minute bars, buy at 60/75/85% progress, flat-15% negative at 0.5 SOL, 3.3% late-curve coverage). This lane re-tests the 85% cell at print level and 2-slot lag, the "revive trigger" that row names. It is one new frozen rule, not a revival of the old result.

## Data

`/data/mal/audit-1008/tape/trades/<hour>.parquet`, rows whose `block` is explore-0814, fresh-0903 or exp011-0909 (as P55). `/data/mal/hunt-shared/tokens.parquet` for `is_mayhem_mode`, `complete_slot`, `v0_lamports`. No October hour, no fast-pool-0918 or oracle block, no EXP-009 hour, no arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output.

## Conventions (verified on rows in the counts step before pricing)

- Bonding rows carry POST-trade reserves. Check: for consecutive bonding prints of a mint, `base_reserve` change equals `-token_raw` (buy) / `+token_raw` (sell), and `quote_reserve` change has the sign of the side. Report the share of rows that fail. If more than 5% fail, or the post-trade reading is contradicted, verdict NOT_DECIDABLE and no pricing.
- Bonding progress = `tools.paper_curve_math.bonding_progress(base_reserve)` (fraction of the 793.1M real tokens bought; 1.0 = graduation threshold). Curve price: constant product on the tape reserves (virtual SOL included). Fee 1.25% per side (`BONDING_FEE_PPM`), real-SOL sell cap as in `quote_sell`.
- PumpSwap rows carry PRE-trade reserves. V handling as p55: V = `v0_lamports` when within [17.5, 17.7] SOL, else 0; quote used for pricing = `quote_reserve + V`. PumpSwap fee tier from `PUMPSWAP_SOL_FEE_TIERS` as in `docs/research/p55/p55_price.py`.
- Priority fee 55,000 lamports per send, 2 sends (as P55/P73). Token-account rent ignored (recoverable).

## Rule (ONE parameter set, no grid)

- Universe: mints with `is_mayhem_mode` false (the Mayhem curve is non-standard, so progress from the base reserve is not valid there). One position per mint.
- Entry trigger: the first bonding print whose post-trade progress is >= **P = 85%**, and the mint has at least one earlier bonding print in the tape with progress < 85% (the crossing is observed; mints already above 85% at their first tape print are skipped and counted).
- Entry: buy lands at slot X = trigger slot + **L = 2**. State = post-trade state of the last bonding print with slot <= X. Skip if the curve completed at or before X, or the buy would take all remaining real tokens (`quote_buy` refusal). Stake 0.25 SOL primary, 1 SOL reported alongside (never selected between).
- Buy math: fee 1.25% of stake, `tokens = b * net / (q + net)`. Sell math reinjects our buy (`Q = q + net`, `B = b - tokens`) on the bonding curve.
- Exit, first of: (a) first bonding print with post-trade progress >= **99%**; (b) first print where the net mark of the whole position <= **-S = -20%**; (c) **T = 30 min** after X (time stop, the first print at or after X + 30 min). The sell lands 2 slots after the trigger print (exit lag 2), using the post-trade state of the last bonding print with slot <= landing slot.
- Sold into migration: if the curve completed (tokens.parquet `complete_slot`, or a bonding print with zero real tokens) at or before the landing slot, the position is exited on PumpSwap: state = pre-trade reserves (+V) of the first PumpSwap print with slot > (first PumpSwap print slot + 2); proceeds = constant product on those reserves (no reinjected buy: separate pool), PumpSwap fee tier. These trades are labelled "sold into migration" and reported separately. If the mint has no PumpSwap print in the tape, the trade is booked as a total loss of the stake and counted (conservative).
- Fail models on the buy: flat 15% (`0.85*pnl + 0.15*(-55,000)`); pressure-fail at slope scale 1 (`tools.latency_curve.FailCurve`, b_slot 0.8, b_sol 0.35, intercept from `fit_curve` so mean p = 0.289 on these entries; pressure = buys at slot X plus buy SOL in the five slots up to X, on the same mint's bonding tape).

## Counts step (before pricing)

Per UTC day: mints crossing 85%, entries, completes. Convention check as above. Report the completion rate: of all entries, the share that completes before exit trigger (a), (b) or (c) lands. Capacity: attempts/day at 0.25 and 1 SOL, with curve impact (price impact of our buy on the state, in percent).

## Kill lines (any one gives KILL)

1. Mean SOL per trade <= 0 under either fail model at 0.25 SOL.
2. Fewer than 3 attempts per day (entries per UTC day on average over the block's days).
3. Result driven by the top 3: total without the top 3 trades <= 0 (either model).
4. Total without the best UTC day <= 0 (either model); a majority of UTC days must be positive (either model).
5. CI90 lower bound of the mean per trade <= 0 (1,000 bootstrap draws, seed 1, 5th percentile): not LEAD; with lines 1-4 passed it is NOT_DECIDABLE.

LEAD requires none of lines 1-4, CI90 lower bound > 0, and at least 100 trades on at least 5 UTC days. Fewer than 100 entries: NOT_DECIDABLE.

## Capacity line

attempts/day x stake x net mean per trade, at 0.25 and 1 SOL (each with its own impact), under both fail models. Concurrency uncapped (open positions at once reported as a caveat).

## Amendment 1 (2026-10-27, made before any pricing or result was read; at the manager's instruction after P73's finding)

- Mayhem identification as P73: `is_mayhem_mode` true in `/data/mal/hunt-shared/tokens.parquet` (mints absent from tokens.parquet count as non-mayhem). These mints are excluded from entry; the count excluded is reported. (The universe line above already excluded them; this fixes the identification.)
- Pre-pricing check: constant product must hold on at least 99% of consecutive bonding print pairs of the priced mints (pairs in the window from the first tape print to the last print used by any exit). Otherwise the verdict is NOT_DECIDABLE and no pricing is run.
- Rows with `quote_reserve` = 0 are counted and any entry/exit path touching one is dropped and reported.
- The extraction (p74_extract.py: all rows of mints reaching >= 85% progress, 44,049 mints) was run before this amendment; it computed no price and no outcome.
