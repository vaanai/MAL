# P70 pre-registration: passive PumpSwap LP fee income, marked to market (lp-fee, library row web-public-pnl-1)

Written before any pricing. No LP fee, IL or P&L number of this rule was computed before this file was committed. Paper only. Exploration only. No book has cleared the promotion gate; a positive result here is a lead, never a pass. These Aug/Sep days are in-sample for the lab (at least 14 families have read them, EXP-024 sec. 9).

## Data (exploration only, same blocks as P55)

- `/data/mal/audit-1008/tape/{trades,migrations}/<hour>.parquet`: the audit-1008 exploration tape (explore-0814, fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922; hours 2026-08-14T12 .. 2026-09-25T06, none inside the forbidden window 2026-09-15T12..2026-09-18T23). Columns used: venue, mint, side, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, tx_index, event_index, block_time, lp_fee.
- V map `/data/mal/pumpswap-virtual/pool_v_0909.json` (as P55): a pool's virtual quote reserve V is used when it lies in [17.5, 17.7] SOL, else V = 0 and the pool is flagged.
- Not read: any October hour, arena tape, DBC, LaunchLab, FLOOR G1, meta-capture, any HV4/HV6/HV7/Q50 output.
- Convention (verified in the extractor note and on rows before pricing): PumpSwap tape reserves are PRE-trade. State after a print = pumpswap_post (tools/arena_state.py): base moves by exactly token_raw; quote moves by +/-(sol_lamports) +/- lp_fee via constant product when the event fields are absent. Swap curve quote = tape quote + V.

## Population and ranking (causal)

- Eligible UTC day d: every hour of d-1, d and d+1 present in the tape. 
- Canonical pool: venue pumpswap, mint is not WSOL, mint has a `complete` row in the tape's migrations (bonding-curve origin). One pool per mint (the first by slot).
- Ranking for day d uses only day d-1: pool SOL volume = sum of sol_lamports over d-1 prints. Top 10 pools per day, ties broken by pool address. A pool needs at least one print before 00:00Z of d. Nothing from day d or later enters the choice.
- Units: one "pool-day" = one pool held 00:00Z to 24 h later. Notes on survivorship: pools that migrated before the tape starts are not canonical here (cannot be told apart from third-party pools); this is a population limit, not a filter on outcome.

## Position (ONE parameter set, no grid)

- Two sizes, both reported, never selected between: S = 0.25 SOL and S = 1 SOL total outlay.
- Boundary slot X0 = first slot in the tape with block_time >= 00:00:00Z of day d; X1 = same for d+1. 
- Entry lands at slot X0+2 (exit lag convention 2 slots, applied to both legs). Entry: buy tokens with `a` SOL through the curve (real fee tier incl. creator, protocol and LP legs; impact), then deposit x = S - a SOL and y tokens in the pool's vault ratio (y/x = B/Q_real after the buy). `a` is solved by bisection. LP fraction f = x/(Q_real + x) of the LP supply. Real quote reserves are the tape quote, the curve is (tape quote + V).
- Hold 24 h. Exit lands at slot X1+2: withdraw f of the real reserves (SOL f*Q_real, tokens f*B) from the pool state at that slot, then sell the tokens through the post-withdraw curve with the real fee tier. Pool with no print after entry keeps its last state.
- Priority fee 55,000 lamports per transaction; 2 on entry (swap, deposit) and 2 on exit (withdraw, sell). No account rent.

## Net P&L decomposition (reported per pool-day)

- Net = exit proceeds - S - fixed priority fees.
- LP fee accrued = f x sum over prints in the hold of (tape lp_fee, else lp_ppm(market cap) x sol_lamports), lp_ppm from PUMPSWAP_SOL_FEE_SPLIT at the print's market cap (tools/paper_curve_math). Fees compound into reserves, so they are inside the withdrawn value; the line is a decomposition, not an addition.
- Hold value = x + y tokens marked at the exit mid price (Q+V)/B, i.e. what holding the deposited assets would be worth. IL = LP value at exit mid (f(Q_real + Q_curve)) - fees - hold value. 
- Entry/exit cost = outlay minus the mid-marked value received on each leg (swap fee tiers, impact, priority fees).
- Identity check: net = fees + IL(+hold pnl) - costs, reported with its residual.

## Fail models (exit leg only)

The deposit is passive and not raced, so the entry always lands. Only the 24 h exit leg can fail. A failed exit leg pays one wasted priority fee (55,000 lamports) and the position stays in the pool for a further 24 h, when the retry is assumed to land: the failed branch is the same position exited at X2+2 (X2 = first slot of d+2, available because day d+1 is a complete day and the d+2 boundary is the next tape hour, or the last tape state if the pool is silent). Expected net = (1-p) x net_24h + p x (net_48h - 55,000). 
- Flat: p = 0.15.
- Pressure-fail at slope scale 1: p = sigmoid(c + 0.8 ln(1+same-slot buys) + 0.35 ln(1+nearby buy SOL)), same-slot buys at the landing slot X1+2 in the pool, nearby buys = SOL bought in the pool in the 2 s block_time window before landing; c is the scale-1 intercept fitted so mean p over all exit sends is 0.289 (tools/latency_curve fit_curve).
- A day without a complete d+2 boundary in the tape uses the last tape state of the pool for the 48 h branch.

## Rug / collapse split

A pool-day is "collapsed" if exit mid price / entry mid price < 0.5. Report net, count and share of total for collapsed and surviving pool-days. Also report the number of pools whose real quote reserve at exit is below 5 SOL.

## Metrics and KILL lines

Reported for both stakes and both fail models: mean net per pool-day (SOL and % of S), total, total without the top 3 pool-days, total without the best UTC day, count of days (of days priced) with majority of pool-days positive, bootstrap CI90 lower bound of the mean (pool-day resampling by date cluster, 1,000 draws, seed 1, 5th percentile), per-day net.

KILL if any holds at either stake:
1. Mean net per pool-day <= 0 under the flat model or under the pressure-fail model.
2. Total without the top 3 pool-days <= 0 (either model).
3. Total without the best UTC day <= 0 (either model).
LEAD if none of the three holds and the CI90 lower bound is reported (a CI lower bound <= 0 is stated, not hidden). NOT_DECIDABLE if fewer than 20 priced days or fewer than 150 pool-days.

## Capacity line

Capacity = pool-days per day (10) x stake x mean net per pool-day (% of S) in SOL per day at S = 0.25 and 1 SOL, after the impact already inside the entry/exit legs. Also report f (the LP share) and the swap impact of the entry/exit legs at both sizes, and the largest S for which f stays under 1% of the pool in the median pool-day.

## Seals

Exploration blocks only; no HV4/Q50 output; no DBC forward or [10-17,10-24); no meta-capture; no arena rows; no October hour. Gate unchanged.
