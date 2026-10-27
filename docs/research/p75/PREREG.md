# P75 pre-registration: completion predictor at the 85% crossing (library row web-oct-mechanics-1, successor of P74)

Written before any pricing, any feature value and any model fit. Exploration tape only. No book has cleared the promotion gate. These dates are in-sample for the lab (at least 14 families have read them): a positive here is a LEAD only, never a pass.

Parent: P74 (PR #1028, KILL). P74 buys every non-Mayhem 85% crossing; the 9.3% of entries still held at completion averaged +0.19946 SOL at 0.25 SOL and stop exits lost. P75 asks whether features known at the crossing can select the crossings worth buying. Entry set, pricing, exits and fail models are P74's, unchanged (`docs/research/p74/p74_price.py`, run unmodified on a re-extracted rows file that also carries the `trader` column).

## Data
As P74: `/data/mal/audit-1008/tape/trades/<hour>.parquet`, rows with `block` in explore-0814, fresh-0903, exp011-0909; `/data/mal/hunt-shared/tokens.parquet` (is_mayhem_mode, complete_slot, v0_lamports, create_ms). Mayhem mints excluded. No October hour, no arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output.
Pre-pricing check (as P74 Amendment 1): constant product must hold on >= 99% of consecutive bonding pairs, else NOT_DECIDABLE.

## Features (ONE set of 5, all causal: computed from bonding prints of the mint with (slot, tx_index, event_index) <= the crossing print, i.e. known before the entry slot X)
1. `vel60`: progress at the crossing print minus progress at the last print with block_time <= crossing block_time - 60 s (0 gap if none: progress of first print).
2. `vel300`: same over 300 s.
3. `buyers300`: distinct `trader` values on buy prints with block_time in (t - 300, t].
4. `flow300`: (buy SOL - sell SOL) in the same 300 s window, in SOL.
5. `age_min`: log1p of minutes from tokens.create_ms to the crossing block_time; missing create_ms imputed with the train median.
(Creator-holding and top-10 holder shares are not used: the tape does not carry a holder snapshot and a tape-derived share is only valid for mints whose whole life is in the tape.)

## Model (no grid, no tuning)
Logistic regression, features standardised with train mean/sd, L2 penalty lambda = 1.0 fixed (on the standardised weights, not the intercept), IRLS. Label y = the curve completes (bonding print with progress >= 1 or tokens.complete_slot) within 30 min (4,500 slots) after the entry slot X. Training rows: the priced entries (the P74 entry set) of explore-0814. Score = predicted probability.

## Split
- Train: explore-0814.
- Threshold: fresh-0903 only. Cutoff = the 90th percentile of the fresh-0903 scores (top 10% of scores). Nothing else is chosen on fresh-0903; no feature, model or cutoff is revisited after seeing it.
- Final test: exp011-0909, ONCE: take entries with score >= the fresh-0903 cutoff. The test is run one time; there is no re-run after reading it.
Fresh-0903 and exp011-0909 results are both reported. The ledger status of these blocks (fresh-0903 spent, exp011-0909 exploration pool) is not changed by this lane.

## Outcome
P74 pricing exactly: stake 0.25 SOL primary (1 SOL reported alongside), exit lag 2, exit at 99% progress / -20% stop / 30 min / sold into migration, fees and priority as P74, flat 15% fail and pressure-fail at slope 1 (curve fitted as P74 on the whole entry set; this is a property of the fail model, not of the outcome).
Reported on the exp011 test: trades per day, mean per trade under both models, total without top 3, total without best UTC day, majority-positive days, CI90 lower bound of mean (1,000 bootstrap draws, seed 1, 5th percentile), AUC of the score for the completion label on exp011 (and on fresh-0903), and unselected-base comparison.

## Kill lines (exp011-0909 test, any one gives KILL)
1. Mean SOL per trade <= 0 under either fail model at 0.25 SOL.
2. Fewer than 5 trades per day (mean over exp011 UTC days).
3. Result driven by the top 3: total without the top 3 trades <= 0 under either model.
Also reported as caveats: total without best day <= 0, minority-positive days, CI90 lower bound <= 0 (those downgrade LEAD to NOT_DECIDABLE). LEAD needs lines 1-3 passed, majority-positive days, ex-best-day > 0, CI90 lower bound > 0, and at least 100 trades on at least 5 UTC days.

## Capacity line
trades/day x stake x net mean per trade at 0.25 and 1 SOL (each with its own curve impact), both fail models, concurrency uncapped.
