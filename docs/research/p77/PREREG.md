# P77 pre-registration: hunt23 b-flow flow-surge states as an entry veto on the frozen H5 binding book

Written 2026-10-27T13:5xZ, before any veto flag, count or P&L was computed. Library row: hunt23-b-flow-V1. Paper only. Exploration blocks only. A pass is a LEAD toward an H5 amendment; it never changes a running book. No book has cleared the promotion gate.

## Population and pricing (reused from P55, unchanged)
- `/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`: 2,826 trades, 1,702 discovery (`frozen_disc`) and 1,124 confirmation (`frozen_conf`).
- Prints: `/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet` (PumpSwap PRE-trade reserves, V added to q, V in [17.5e9, 17.7e9]). Entry, sell math, fee tiers, 55,000 priority and exit lag 2 slots are the P55 code (p55_price.py logic copied). Baseline is validated: with the frozen exit lag (ceil(0.55 s/sps)) the re-priced pnl must equal stored `pnl` (reported as max abs difference). Primary reading: exit lag 2 (P55's realistic primary); the frozen-lag reading is reported as sensitivity.
- Fail models: flat `0.85*pnl + 0.15*(-55,000)`; pressure `(1-p)*pnl + p*(-55,000)` with stored p_fail. A vetoed trade is not taken: it contributes 0 under both models.

## Veto states (ONE threshold set, from DECLARE.md of hunt23 b-flow, cells B1, D1, E1 verbatim)
n(x s) = round(x / sps), sps the trade's pool slot clock as in P55. Slots of prints, "causal" = only prints with slot STRICTLY BEFORE the trade's trigger slot `si` (the decision print; stricter than the landing slot). Order within the pool is path row order. Qpre = q + V of the print.
- B (whale plus followers, B1): whale print w = BUY with sol >= max(1 SOL, 0.03 * Qpre_w). Trigger = the 2nd BUY print by a trader (th) different from the whale and from the first follow-on buyer, with slot in (s_w, s_w + n(4 s)]; and over rows after w up to the trigger, sell SOL <= 0.25 * buy SOL of the non-whale buys in that range.
- D (exhaustion buy, D1): at a BUY print j, the maximal run of consecutive SELL prints immediately before j, restricted to slots > s_j - n(45 s), has length >= 4; mean size of its last two sells <= 0.5 * mean of its first two; PP_last/P_first - 1 <= -0.08 (post-trade price PP: buy (q+0.99 so+V)/(b-tk), sell (q-1.01 so+V)/(b+tk); P = (q+V)/b); and sol_j >= max(0.3 SOL, 2 * mean run sell size).
- E (distributed inflow, E1): at a BUY print i over (s_i - n(600 s), s_i] (all pool rows if the pool is younger): net_in = buy SOL - sell SOL >= 0.10 * Qstart (Qstart = Qpre of the first row in the window); distinct buyers >= 12; largest buy <= 0.25 * buy SOL of the window.
- VETO(trade) = any of B, D, E triggered at a print with slot < si and slot >= si - n(600 s) (the state is "in force" for 600 s, the E window length and the shortest b-flow hold). One lookback, no grid.
- Deviations from DECLARE, stated now: the b-flow universe filters (Qpre >= 50 SOL, pool age >= 360 s, first 20 min of block dropped) are NOT applied, because H5 enters pools with q+V <= 40 SOL inside 300 s of the pool start; they would void the veto. Windows are therefore truncated at the pool start. The oracle-block slot-order caveat of DECLARE-NOTE-1 does not apply (audit paths).

## Procedure
1. Counts first: vetoed count and share per state and total, per set, with NO P&L touched. If the discovery vetoed count is < 30 or > 60% of trades, report INSUFFICIENT and stop (no pricing read).
2. Discovery is where the definition is checked (counts, sanity); confirmation is the test. No parameter is changed after any P&L is seen; a code defect against this file is fixed and logged and everything is recomputed whole.
3. Report, for discovery and confirmation separately, under both fail models: vetoed count and share; mean per trade (pp of the 0.1 SOL stake) with and without the veto and the delta; delta ex the 3 trades whose veto helps most (top-3 delta contributions); delta ex the best day (the day with the largest total delta); total with the veto ex top 3 trades by P&L of the vetoed book; random-veto placebo with the same vetoed count, 1,000 draws, seed 1 (numpy default_rng(1)), p = share of draws with delta >= observed (one-sided). Also, for each set, days with a majority positive delta and the date-cluster bootstrap 5th percentile of the mean delta (1,000 draws, seed 1).

## KILL lines (confirmation, either fail model)
1. Mean delta < +1.0 pp.
2. Placebo p > 0.10.
3. Driven by the top 3 (delta ex top 3 <= 0) or by one day (delta ex best day <= 0).
Any one kills. Otherwise LEAD (not a pass). Wrong sign in discovery is reported, not decisive.

## Capacity line
Capacity of a veto = trades avoided, not trades added: the book keeps (1 - share) of its trades/day. Edge in SOL/day = (delta pp / 100) * stake * kept-trades/day, evaluated at the frozen 0.1 SOL stake; at 0.25 and 1 SOL stake the delta is NOT scaled (entry impact is not repriced here). Impact at 0.25 and 1 SOL is reported as untested for this lane, and any LEAD must be re-priced at those stakes before use. Trades/day is the 2,826 trades over the days present.

## Seals
Aug/Sep exploration blocks (explore-0814, fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922) via the audit-1008 paths used by P55. No October hour, arena tape, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output or job, #1419/#1421. These dates are read by at least 14 families (HOLDOUT_LEDGER m >= 14), so a result is in-sample.
