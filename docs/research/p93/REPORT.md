# P93: Haiku reads token name and symbol to predict pool survival. Verdict: KILL

Lane P93, 2026-10-27. Exploration data only (explore-0814; in-sample dates read by at least 14 families). 0 API calls. Numbers are copied from `p93_counts_output.txt`, `p93_label_output.txt` and `p93_score_output.txt`. No book has cleared the promotion gate; no sentence here says anything makes money. PREREG committed at 35e4ed09 before any rating or label.

## Step 1: counts
Create rows carry `name` and `symbol` (no `uri` field in any row). Share of unique creates with both non-empty: explore-0814 557,896 / 559,018 = 0.9979929089939858; fresh-0903 160,458 / 160,714 = 0.9984071082793036; exp011-0909 179,015 / 179,219 = 0.9988617278301966. Decidable. explore-0814 population for the sample: 13,299 graduates (complete event within 12 h of create, create + 24 h inside the block).

## Sample and label
400 graduates, stratified by day, seed 1 (sample sha256 ae6c9cda...). Label: survive = last pre-trade PumpSwap quote reserve in [create, create + 24 h] >= 50% of the peak in that window. 0 excluded. **Survivors 19 of 400, base rate 0.0475.**

## Rating
6 Haiku subagents (model haiku), Read and Write only, no scripts, 67/67/67/67/66/66 cards, all six answers valid on the first try (no re-ask, no exclusion). Haiku used 29 distinct values (mean P 22.045, sd 8.202).

## Result
- Haiku AUC 0.4476, CI90 [0.3384, 0.5499] (1,000 draws, seed 1). The point estimate is below 0.5.
- Baseline (logistic on name length and symbol all-caps, 5-fold OOF): AUC 0.5184. Raw: -len(name) 0.46953999171156235, all-caps flag 0.5482801491918774 (share all-caps 0.6975).
- Decile calibration (n=40 each): survive rate 0.075, 0.05, 0.05, 0.025, 0.075, 0.025, 0.05, 0.05, 0.075, 0.0 for mean P 8.0 to 35.88. No monotone pattern; the top-P decile has 0 survivors.
- Kill line: CI lower bound <= 0.55 (true) and Haiku AUC <= baseline (true). **KILL.**

## Caveats
- Only 19 positives: the test has little power, and a small real effect would not be seen. It does show no sign of a large one. The CI upper bound 0.5499 is itself under the 0.55 line.
- Graduates only; the label is pool drain (real quote reserve), not price; the last print's reserve is pre-trade.
- In-sample lab dates. A pass would have been a lead only.

## Capacity
None: no edge found, so no trades/day x stake x edge line.

## Seals
No contact. Used only explore-0814, fresh-0903, exp011-0909 creates and explore-0814 migrations/trades. No meta-capture, October, arena, DBC, LaunchLab, FLOOR G1, HV4/6/7, Q50.
