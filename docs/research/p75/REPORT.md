# P75 report: completion predictor at the 85% crossing

Verdict: **KILL**. Exploration tape (explore-0814 train, fresh-0903 cutoff, exp011-0909 test), in-sample for the lab (at least 14 families have read these days). No book has cleared the promotion gate. Rule, features, split and kill lines: PREREG.md and Amendment 1. Pricing is P74's (Mayhem excluded; 0.25 SOL stake primary; exit lag 2; exit 99% / -20% stop / 30 min / sold into migration). Outputs: `p75_model_test_output.txt` (no portal fee, primary), `p75_model_test_portal_output.txt` (0.5% portal fee on each bonding leg).

## Checks
- Constant product holds on 99.9949% of 23,090,584 consecutive bonding pairs (>= 99% required). Post-trade base-change check 99.8412%. quote_reserve 0 rows: 4.138%; 791 entries dropped.
- 22,743 observed crossings; priced entries by block: explore-0814 12,529 (15 days), fresh-0903 2,580 (7), exp011-0909 2,014 (7). Completion label (within 30 min of entry) base rate 38.0% / 34.1% / 34.3%.
- Real-SOL sell cap, tested with the corrected bound (observed reserve less 30 SOL floor; Amendment 1 item 3): it did bind. 157 reverting sells in the full priced set (154 with portal), 1 in exp011; booked as a total loss of the stake. P74's "cap never bound" does not carry over.

## Model
5 features (vel60, vel300, buyers300, flow300, age_min), logistic regression, L2 = 1, standardised weights (intercept -0.5075; 0.0489, -0.0391, -0.2626, 0.3211, 0.0041). AUC: train 0.6023, fresh-0903 0.6031, **exp011-0909 test 0.6196**. Cutoff (90th percentile of fresh scores) 0.4797; it selected 10.43% of exp011 entries. Fresh-0903 top 10% (seen before the test, as designed): 258 trades, mean -5.508% / -3.475% of stake (flat / pressure), already negative.

## exp011-0909 test, 0.25 SOL, top-scored (score >= cutoff): 210 trades, 7 days, 30.00 trades/day
| | no portal fee (primary) | with 0.5% portal fee |
| --- | --- | --- |
| mean/trade flat 15% | -0.003352 SOL (-1.341%) | -0.004888 SOL (-1.955%) |
| mean/trade pressure-fail | -0.000493 SOL (-0.197%) | -0.001693 SOL (-0.677%) |
| total flat / ex-top3 / ex-best-day | -0.7039 / -1.5785 / -1.4773 | -1.0265 / -1.8936 / -1.7267 |
| total pressure / ex-top3 / ex-best-day | -0.1036 / -0.8367 / -0.9918 | -0.3555 / -1.0821 / -1.1865 |
| positive days flat / pressure | 2/7 / 4/7 | 2/7 / 4/7 |
| CI90 lower bound (trade bootstrap, 1,000, seed 1) flat / pressure | -0.017757 / -0.013387 | -0.019090 / -0.014406 |
| capacity 0.25 SOL (SOL/day) flat / pressure | -0.10055 / -0.01480 | -0.14664 / -0.05078 |

1 SOL (primary fee): flat -0.018251 SOL/trade (capacity -0.53970 SOL/day), pressure -0.005448 (-0.16112). Exit kinds at 0.25 SOL: stop 94, a99 71, migration 40, time 5. Selected migration share 19.05% versus 7.85% unselected, so the score does enrich completions, but the stop losses outweigh the gain. Unselected exp011 baseline: -0.014972 / -0.013584 SOL per trade (flat / pressure), 0/7 positive days.

## Kill lines (exp011 test)
1. Mean <= 0 under either fail model: **hit**, both fee versions, both models.
2. Fewer than 5 trades/day: not hit (30.00).
3. Total without top 3 <= 0: **hit** (both models, both fee versions); the total itself is negative, so there is nothing for the top 3 to drive.
Also: CI90 lower bound < 0. Neither fee version is a LEAD.

## Not measured / caveats
- Selection improves the mean from about -6% to -1.3%/-0.2% of stake but not above zero; the best case (pressure-fail, no portal fee) is -0.000493 SOL/trade, indistinguishable from zero with a CI90 lower bound of -0.013387.
- Creator-holding and top-10 holder features were not available causally from the tape; not tested. Untuned logistic only (no LightGBM). AUC 0.62 is weak.
- Sells priced by constant product at the 2-slot-later state; stop checked at prints only; PumpSwap exit uses first 5 prints; time-stop/block-end censoring not modelled; the pressure-fail curve is fitted on all entries (as P74).
- Disclosure: the first pricing attempts were OOM-killed at 8G before any output; the first numbers seen by anyone were the fresh-0903 pre-test numbers (all negative); the exp011 test was scored once per fee variant with the same fitted model and cutoff.
