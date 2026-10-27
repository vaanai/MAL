# P84 pre-registration: one day-level regime switch on the frozen H5 binding book

Lane P84 (slug h5-regime-switch), written before any outcome is joined. Exploration blocks only. No book has cleared the promotion gate. A pass is a LEAD only and never changes a running book.

## Population and pricing (reused exactly from P55)
- The 2,826 frozen binding trades of `/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`: 1,702 discovery (explore-0814, 2026-08-14..08-28) and 1,124 confirmation (fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922, to 2026-09-25).
- Pricing is `docs/research/p55/p55_price.py` unchanged. The baseline P&L per trade is its `pnl_base_el2` (frozen exit, exit lag 2 slots, the realistic primary). PumpSwap PRE-trade reserves, V = 17.585 SOL, real fee tiers, 55,000-lamport priority per send.
- Fail models as P55: flat `0.85*pnl + 0.15*(-55,000)`; pressure-fail `(1-p)*pnl + p*(-55,000)` with the stored per-trade p_fail (slope scale 1).
- The tool check of P55 must hold again (re-priced frozen lag/exit equals stored pnl).

## The one regime variable
`G(D-1)` = number of tokens with a graduation (`grad_ms`) in UTC day D-1, from `/data/mal/hunt-shared/tokens.parquet` (count only, no returns, exploration days only).

Why this variable, from the library text and not from H5 outcomes: `docs/library` row `tod-regime-4` lists graduations among the activity variables of the regime switch, and the H5 rule trades PumpSwap pools that have just graduated and carry a BOOST. Graduation flow is the supply of such pools. Rows `lib-a-A25` and `lib-b-C13` (quiet-creation regime) show the library treats activity level as the regime.

Known before pricing, and disclosed: the day counts of graduations fall from about 11,000 (Aug 14) to about 2,000 (mid Sep). This is a level drift, so a median fixed on August discovery days will make most September days OFF. That is a property of the rule as the brief fixes it; I do not repair it. A drift-robust variant appears only as a report-only sensitivity (S1 below), never as the test.

## Rule (fixed now)
- A UTC day is COMPLETE if graduations occur in all 24 UTC hours of it. The variable on D-1 is defined only if D-1 is COMPLETE; otherwise the variable is undefined and day D is OFF (conservative; counted separately as "undefined-off").
- Threshold T = median of `G(D-1)` over the DISCOVERY trading days D (2026-08-14..08-28) with a defined variable. Fixed once, from counts. The book trades on day D only if `G(D-1) > T` (strictly).
- Discovery is where the variable's definition is checked. Confirmation is the one-time test; nothing is re-chosen after it.

## Report (both sets, both fail models)
Days on / off (and undefined-off); trades kept; mean per trade, switch-on vs all; delta in pp of the 0.1 SOL stake; total with and without the switch; with-switch total ex-top-3 and ex-best-day; days positive; CI90 (date-cluster bootstrap, 1,000 draws, seed 1, 5th percentile of the mean); placebo: random on/off over the defined-or-all trading days of the set with the same number of on-days, 1,000 draws, seed 1, day-level, p = share of draws with mean per trade >= the switched mean (one-sided).

## KILL lines (confirmation), any one
1. The switched book's mean per trade is <= 0 under either fail model.
2. Placebo p > 0.10 (either model).
3. The result depends on the top 3 trades or on one day: with-switch total ex-top-3 <= 0 or ex-best-day <= 0 (either model).
4. Fewer than 4 on-days.
If no confirmation trades are kept, the verdict is KILL by line 4 (reported as not decidable on the rule as fixed, with the reason). LEAD requires all four lines clear.

## Report-only sensitivity (declared now, never selected among, cannot give a LEAD)
- S1: threshold = median of `G(D-1)` within each tape block (block = the `blk` column) over that block's trade days with a defined variable, applied to its own block. It removes the Aug-to-Sep level drift. Reported for discovery and confirmation, same metrics.

## Capacity line (trades/day x stake x net edge, after impact)
- Trades per day on = kept trades / on-days (from the rows). Stake 0.1 SOL in the book.
- First-order constant-product impact per leg at stake x: `x / (Q + x)`, with Q = the row's `q_trig` (SOL quote reserve at the trigger; V is inside it per P55). Round trip impact ~ 2x/(Q+x). Evaluated at x = 0.25 and x = 1 SOL as a haircut on the mean percent return per trade, median Q over kept trades. Net edge per day = trades/day x x x (mean % - haircut). This is a rough bound, not a repricing; the stored P&L already includes the 0.1 SOL impact.

## Seals
Aug/Sep exploration blocks only. No Q50 or H5 forward output, no jobs #1419/#1421, no October hour, no HV4/HV6/HV7, no arena, DBC, LaunchLab, FLOOR G1 or meta-capture file. These sets have been read by at least 14 families (EXP-024 sec. 9): even a pass is in-sample for the lab.

## Correction note, 2026-10-27 (added after the run; body above unchanged)
The body says graduation counts fall from "about 11,000 (Aug 14)". 2026-08-14 is a 12-hour partial day (11,180). The first complete day is 2026-08-15 with 7,444. The drift to about 2,000 by mid-September stands. The rule, threshold and kill lines are unaffected.
