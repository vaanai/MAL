# P72 pre-registration: absorb a forced-sell cluster on PumpSwap (sell-cluster-absorb)

Written before any pricing and before any count. No cluster, return or P&L of this rule was computed before this file was committed. Library row: `p25-pump-order-keeper-absorb` (adapted: no app tag, mechanical cluster only; app tags are not assumed to exist in Aug/Sep). Exploration only. These are in-sample lab dates that at least 14 families have already read; a positive here is a lead, never a pass. No book has cleared the promotion gate. Paper only.

## Population (exploration blocks only)

`/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet`, the same PumpSwap V-era migration paths P55 used: explore-0814, fresh-0903, exp011-0909 and the other exploration days in that directory. Each pool is observed from its first PumpSwap print for a 7,300-slot window after migration (about 32 min). No October hour, no arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4, HV6, HV7 or Q50 file is read. Consequence: clusters are only searched in the first about 32 min of a migrated pool's life, so the capacity below is for that population only.

Pricing conventions: PumpSwap tape reserves are PRE-trade (extract.py header; verified again by the counts script on this data before pricing). Quote reserve used everywhere = `q + V` (p55_price.py convention, V about 17.585 SOL from the pool V map). Fee tier from `tools.paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` on `(q+V)/b`, as p55. Priority 55,000 lamports per send, two sends.

## The rule (ONE parameter set, no grid)

Walk each pool's prints in tape order (slot, tx_index, event_index).

- **Cluster detector (causal, uses prints up to j only).** Keep the current run of consecutive sell prints since the last buy print. At sell print j, take the sells of the run with slot >= slot_j - W. Declare a cluster at j (the first print where this holds) when all hold:
  - **K = 3** distinct traders among those sells;
  - **W = 10** slots (about 2.7 s);
  - net sell >= **X = 2.0%** of the quote reserve, where net sell = sum of `sol` of those sells and the reserve = `q+V` PRE-trade of the first sell in the window;
  - **no buy** between the first and the last of those sells (the run definition);
  - quote reserve `q+V` PRE-trade at j >= **50 SOL**.
  After a cluster fires, the pool cannot fire again until a buy print has occurred; one open position per pool.
- **Entry:** cluster last sell = print j in slot Sj. Buy **0.25 SOL**, landing at slot **Sj + 2 (L = 2)**, priced at the state after all prints of slot Sj+2 (pre-trade state of the first print with slot > Sj+2), p55 convention, with the AMM buy math and fee tier of p55 (own impact included).
- **Exit, the first of:**
  - target **T = +10%**: mark value net of the sell fee tier, before priority, >= 1.10 x 0.25 SOL;
  - stop **S = -10%**: mark value <= 0.90 x 0.25 SOL;
  - time cap **600 s** after landing (round(600 / sps) slots, sps from block time as p55).
  The mark is checked at each slot boundary after the landing slot, using the state after that slot. The sell lands **2 slots** after the trigger slot (exit lag 2, the realistic primary), AMM sell math, sell fee tier, priority both sides. The stop can therefore gap through -10%; that is counted. If the pool tape ends before an exit, the last state is used and the trade is flagged `censored` and counted.
- **Pools with sps outside (0.15, 0.6) are skipped** (p55 convention).

## Fail models (applied to the net P&L per trade)

- flat 15%: `0.85*pnl + 0.15*(-55,000)`;
- pressure-fail at slope scale 1: `(1-p)*pnl + p*(-55,000)`, `p` from `tools.latency_curve.fit_curve` (slopes 0.8 / 0.35, intercept fit to 0.289 mean on this book), with `same_slot_buys` = buys in the landing slot and `nearby_buy_lamports` = buy lamports in the 2 s before and inside the landing slot, exactly as `s14_boostdip.py`.

## Report (all of it, no selection)

- clusters per day (the capacity), trades per day, number of days;
- hit rate: share of trades whose exit is the target; also share stopped, share timed out;
- mean net per trade (SOL and percent of stake) under both fail models; total SOL;
- total and mean with the top 3 trades removed; with the best day removed;
- days with a positive total, majority-positive days; CI90 lower bound of the mean (date-cluster bootstrap, 1,000 draws, seed 1);
- report-only, not causal: results split by pools that later died vs survived. `died` = the pool's `q+V` at its last print inside the window is below 50% of its `q+V` at the cluster print; `survived` otherwise. This label uses information after entry and is never a filter.

## Kill lines (any one is KILL)

1. mean net <= 0 under the flat 15% model OR under the pressure-fail model;
2. fewer than 2 clusters per day (clusters that pass every filter above, divided by the number of days in the population with paths);
3. driven by the top 3: after removing the 3 best trades the total is <= 0 under either model.

Also reported against the promotion gate, not decisive here: at least 100 trades, at least 5 UTC days with a majority positive, CI90 lower bound > 0, total positive without the top 3. These dates are in-sample, so even all four are only a LEAD. A mean > 0 that fails the best-day or CI checks is reported as NOT_DECIDABLE or LEAD per the REPORT, never as a pass.

## Capacity line

Capacity (SOL/day) = trades per day x stake x net edge per trade (percent of stake, the lower of the two fail models), after own AMM impact at the stake. At 0.25 SOL this is the primary pricing. At 1 SOL the same trades are re-priced report-only (same entry/exit slots, same rule, stake 1 SOL, thresholds as a percent of stake), and the same product is stated. The cluster population is the migrated-pool window only, so the number is a floor on what a wider pool set could offer, not an estimate of it.

## Seals

Aug/Sep exploration only. No HV4/Q50 output or job, no DBC window, no meta-capture, no arena tape, no October hour, EXP-024 Look-2 seal untouched, FLOOR G1, LaunchLab and D1 untouched. The population is the one P55 already used; the ledger rows for explore-0814, fresh-0903 and exp011-0909 list them as exploration pool.
