# P73 pre-registration: Mayhem early-collapse fade (library row mayhem-flow-2)

Written before any pricing and before any outcome of this rule was computed. Exploration tape only. No book has cleared the promotion gate. These dates are in-sample for the lab: at least 14 families have already read them, so a positive here is a lead, never a pass.

## Step 1: pick

Candidates were the `untested` rows of docs/library/ideas.jsonl. Dropped: rows needing October data, the owner, live sends, paid feeds or new captures (scheduled-flows-*, web-landing-*, w2-pump-idl-*, p20-*, p25-*, l3-*, lib-c-R2/R3/Q1, census-*, speed-route-*); HV6/HV7/HV8 rows (p67-*, web-llm-traders-*, haiku-*); the P70-P72 rows (web-public-pnl-1/3/5, p25-pump-order-keeper-absorb). Dropped as already answered in family: library-capacity-1 (crash-bounce-v2-1 dead), exit-upgrade-5 (same crash-bounce family), web-public-pnl-2 (H5 stops answered by h5-exit-sd-3 and lib-c-N3, G4 stops by exit-upgrade-1/2), web-oct-mechanics-1 and bc-capacity-1 (bc-runup-1, bc-flow-1..4 explored-no-survivor), mayhem-flow-3 (lift gone by September). Dropped as descriptive or overlay with no standalone capacity: web-public-pnl-4, bc-bigwin-2, hunt23-b-flow-V1, r4b-depth-2.

Shortlist of three:

1. **mayhem-flow-2, Mayhem early-collapse fade.** About 20-23% of mayhem mints fall below half their +1 min price by +5 min (hunt5 REPORT). Thousands of mints per day, so trades/day is not the limit. Buying a collapsed mint has the "big win, small loss" shape (capped loss at the stop, uncapped bounce). Untested; the REPORT lists a reserve-model check as the only precondition.
2. **exit-upgrade-6, xs10 horizon families.** Capacity depends on the xs10 book, which needs a mid-to-mint join and a tape extraction that were never built. Fixed-horizon exit variants carry no big-win shape. Not chosen.
3. **hunt23-b-flow-V1, flow-surge entry veto.** Fitted on in-sample gross medians of a killed screen and has no trades of its own (an overlay). Capacity is not defined without a host book. Not chosen.

Chosen: mayhem-flow-2, for the highest trades/day and a defined stand-alone entry. Known risk: bonding fees (1.25% a side) and the non-standard Mayhem curve.

## Step 2: rule (one parameter set, no grid)

Data: `/data/mal/audit-1008/tape/trades/<hour>.parquet`, rows whose `block` is explore-0814, fresh-0903 or exp011-0909 only (as P55). `/data/mal/hunt-shared/tokens.parquet` for `is_mayhem_mode`, `has_create`, `create_ms`, `create_slot`, `complete_slot`. No October hour, no arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output, no fast-pool-0918 or oracle block, no EXP-009 hours.

Reserve conventions (verified on rows in the counts step before pricing): bonding rows carry POST-trade reserves, so price after print i = (quote_i)/(base_i*1000) with no V. This rule trades only the bonding curve, so the PumpSwap V (17.58 SOL) is not used; mints that completed are exited on the curve (see exit).

- Universe: `is_mayhem_mode` and `has_create`, a bonding print at or before create+60 s, and a bonding print at or after create+300 s. One position per mint.
- Reference price p1 = post-trade price of the last bonding print with block_time <= create_s + 60.
- Signal: p5 = post-trade price of the last bonding print with block_time <= create_s + 300. Signal fires when p5 < 0.5 * p1. The decision print is the first bonding print with block_time >= create_s + 300 (signal uses only prints before it).
- Entry: the buy lands at slot X = decision slot + 5 (1.9 s leg, 0.4 s slots). State = post-trade state of the last bonding print with slot <= X. Skip if the mint completed at or before X. Stake S = 0.25 SOL primary (1 SOL reported alongside, never selected between).
- Buy math: constant product on the tape reserves of that state, fee 1.25% of S, tokens = b * net / (q + net). Sell: fee 1.25% of proceeds. Priority fee 55,000 lamports per send, 2 sends.
- Exit, first of: take profit at net mark >= +100%; stop at net mark <= -30%; time stop 120 min after X; mint completion (exit at the last bonding print state before the complete slot). The mark is the sell value of the whole position at each later print. The sell lands 2 slots after the trigger print (primary exit lag 2), using the state at that slot.
- Fail models on the buy: flat 15% (`0.85*pnl + 0.15*(-55,000)`); pressure-fail at slope scale 1 using `tools/latency_curve.FailCurve` (b_slot 0.8, b_sol 0.35, intercept fitted to mean p 0.289 on these entries by `fit_curve`; pressure = buys at slot X plus buy SOL in the five slots up to X).

## Counts step (before any pricing)

Per day: mayhem mints, signal count, entries. Verify post-trade convention and constant-product consistency of mayhem bonding rows (token_raw vs base_reserve change; k drift between consecutive prints) and report the share of rows that fail. If fewer than 100 entries, or the curve check fails on more than 5% of rows, the verdict is NOT_DECIDABLE and no pricing is run.

## Kill lines (any one gives KILL)

1. Mean SOL per trade <= 0 under either fail model at 0.25 SOL.
2. Total without the top 3 trades <= 0 (either model).
3. Total without the best UTC day <= 0 (either model).
4. Fewer than a majority of UTC days positive (either model).
5. CI90 lower bound of the mean per trade <= 0 (1,000 bootstrap draws, seed 1, 5th percentile) under either model. This is the promotion-gate line; failing it with the others passing is NOT_DECIDABLE or KILL by line 1-4 only, never LEAD.
LEAD requires none of lines 1-4 and at least 100 trades on at least 5 UTC days.

## Capacity line

trades/day x stake x net mean per trade, at stake 0.25 SOL and at 1 SOL, each priced with its own impact from the curve reserves. The result is reported under both fail models. Concurrency is not capped (reported as a caveat: open positions at once).
