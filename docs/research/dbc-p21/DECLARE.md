# P21 DECLARE: DBC D7 FLOOR (p20-dbc-start-floor) and D9 SCHEDULED (p20-dbc-fee-floor-clock). EXPLORATION SCREEN. Written before any count or price.

Binding ruling: /data/mal/hunt-1008/push-1016/hunt29/dbc/QP-RULING-1024-p20.md (sha256 4b9aa044ed7af871ec0d4cd8f9ebb4c357e6064d1b71a3e14f77c9bedc0dd652). Ideas: docs/research/p20-ideas/IDEAS.md (PR #875, head cb20e3a7).
D8 (p20-dbc-completion-step) is NOT screened here (QP: no path to a book).

## What preceded this file (no attempt count, no capacity count, no price, no config-field distribution)
- Read: the QP ruling, IDEAS.md, P14/P16/P17 DECLARE and REPORT, tools/dbc_price.py, dbc_screen.py, dbc_config.py, arena_stack.py, the IDL v0.2.2 type list.
- Schema only: the field names of ONE create row and ONE trade row of E1 hour 2026-10-14T05 (the create row carries `activation_point` from EvtInitializePool; for that row it equals the create slot).
- File listings: p17_union has 240 hour files [10-14T00, 10-24T00); P17's cached config fetch /data/mal/hunt-1008/push-1016/hunt28/p17/cfg_raw_all.json holds 7,399 raw accounts = every config referenced by a trade row of p17_union (cfg_keys_all.json). Its contents were not decoded or counted for this lane before this file.
- No P20 idea had been counted, screened or priced by anyone before this file (QP (VERDICT)).

## Hours, data and guards
- DBC blocks used: E1 [2026-10-14T00, 2026-10-17T00) and E2 [2026-10-17T00, 2026-10-24T00), both read only through /data2/mal/venue-tape-backfill/p17_union (P17 hardlink dir; tools.dbc_price.enable_spent_exploration, marker HOLDOUT_READ_DONE_v2). Never fwdv1/, never the meteora_dbc_holdout/ root.
- E3 [10-24T00, 10-25T00): may only be fetched after 2026-10-25T00:20Z into the NEW dir /data2/mal/venue-tape-backfill/p21_1024/ (tools/venue_backfill_dbc.py as a MiScusi job, credit cap). This lane's hard stop is 2026-10-25T02:00Z, so the screen below runs on E1 + E2 only; E3, if fetched later, is a separate report-only addendum and never changes a label.
- E2 is SPENT for D1 (QP-RULING-1024-dbc-m10-swap3 (1)) and was read by P17. Here it is DISCLOSED EXPLORATION only: never confirmation, never cited as evidence.
- Era split printed per cell: Swap2 [10-14T01, 10-16T04), Swap3-E1 [10-16T04, 10-17T00), E2 [10-17T00, 10-24T00). Swap3 rows are priced by the P17 `--swap3` path (tools.dbc_price.enable_swap3_pricing, config thresholds).
- Block edge: an attempt whose exit window passes 2026-10-24T00Z is refused (`block_edge`, counted). D7's 24 h cap loses about the last day of attempts; said in the report.
- DAMM v2: no DAMM print is read by this lane (the DAMM continuation line is not computed; see Strand).
- Code guard (tools/dbc_p21.py, unit test tools/test_dbc_p21.py) refuses: any DBC hour >= 2026-10-25T00Z (and >= the end of the last block used); any DAMM print >= 2026-10-25T00Z or >= the end of the last DBC block used; any path or root under fwdv1/ or meteora_dbc_holdout/; any D1 cell (idea D1 or name m4/m6/m10). The module defines no D1 cell, computes no D1 trigger, feature, position or strand continuation.
- Config data: P17's cached fetch (above), decoded with tools/dbc_config.py (IDL v0.2.2). No refetch is planned (0 credits); configs referenced by E1/E2 rows and missing from it are excluded and counted (`unmapped`). Write-once: the IDL writes PoolConfig only in create_config*; the cache was fetched 2026-10-24 ~06:55Z, i.e. under the CURRENT (post-Swap3) program; P17 checked migration_quote_threshold against the Swap2 event for 755/755 configs. The fee fields used by D9 are validated against tape fee rows (counts only, below) before pricing; if the validation shows the decoded fee schedule does not reproduce the tape, D9 is NOT priced and the report says why.
- Pool activation_point and creation instant: from the pool's `create` row (EvtInitializePool event of the init tx; `activation_point` is set at init). A pool whose create row is not in the loaded hours is excluded (age, prior peak and activation unknown), counted.

## Universe U'' (P17's U' plus the changes stated)
P17 U': SOL-quoted; pool excluded if it has a real transfer-hook row, its config has transfer_fee_basis_points > 0, or its config is unmapped (each counted per day); config fee mode known; migration threshold >= 5 SOL; a 0.25 SOL buy moves the spot <= 2% at the decision state (own fee included); progress p <= 0.85; curve not completed; decision state not a bad (order-uncertain) row; one warm-up hour.
Changes: (1) the pool's create row must be in the loaded hours; (2) the config must decode (sqrt_start_price, base fee fields, activation_type). (3) D7 adds the near-start, prior-peak and age rule; (4) D9 adds the schedule rule. Exclusions are counted per day.

## Cells (6 labelled cells; m goes from 26 to 32). Rate bar >= 3 attempts/day averaged over the good hours / 24; below it: dropped unpriced. No parameter change after a count; one pricing run.
### D7 FLOOR (domain D7; not D1: no buy-count, net-buy-SOL or spot-up condition anywhere)
- start_spot = spot(config sqrt_start_price) (same units as the tape's post sqrt price, (S/2^64)^2).
- Trigger: the FIRST print i of the pool (any side) such that, using only prints <= i:
  - post spot at i <= s x start_spot (floor band s);
  - prior peak: the running max of post spots from the pool's first print through i >= X x start_spot, X = 2.0 (fixed);
  - pool age at i (block time of i minus the create row's block time) >= 1,800 s (the creation window is excluded);
  - U'' holds at the state after print i.
  One look per pool: if that first qualifying print is refused, the pool gets no second attempt.
- Entry: buy lands k = round(1.9 s / slot_s) slots after the decision slot (state after every print with slot <= decision + k). ref = decision spot.
- Exit: tp when a post spot >= 1.5 x ref, else the time cap; no stop. Exit lands 1.9 s after the fire slot.
- Cells: d7_s102_h6 (s 1.02, cap 6 h), d7_s105_h6 (s 1.05, cap 6 h) = PRIMARY, d7_s102_h24 (s 1.02, cap 24 h), d7_s105_h24 (s 1.05, cap 24 h).
- Fees: the P14/P17 per-trade method (pool's last observed rate on that side, causal; fallback 1%); sell fee on the quote output (A2 fix); buy fee side from fee_on_base_token / collect_fee_mode.
### D9 SCHEDULED (domain D9; trigger = config-computed instant, never the realized fee path)
- Schedule (config pool_fees.base_fee, FEE_DENOMINATOR 1e9): base_fee_mode 0 = linear scheduler, 1 = exponential scheduler (first_factor = number_of_period, second_factor = period_frequency, third_factor = reduction_factor), 2 = rate limiter (first_factor = fee_increment_bps, second_factor = max_limiter_duration, third_factor = reference_amount). Expiry point = activation_point + number_of_period x period_frequency (modes 0/1) or activation_point + max_limiter_duration (mode 2), in slots (activation_type 0) or unix seconds (activation_type 1).
- Schedule rule: mode 0/1 with end fee < cliff fee by >= 10 bps, or mode 2 with fee_increment_bps > 0; and expiry >= activation + 60 s (or 150 slots). Pools with no schedule (or a schedule that is not in these modes) are not D9 candidates (counted).
- Expiry slot: activation_type 0: the expiry point itself. Type 1: the first slot whose block time >= the expiry second, from the slot/block-time map of all loaded tape rows.
- Decision state (all filters, causal): the state after the last print of the pool with slot < expiry slot. Filters there: U'' (impact, p, not completed, not bad, L known) and "alive" = at least one print of the pool in the 600 s before the expiry instant (activity, not momentum; no buy-count, net-SOL or spot condition). ref = decision spot. One attempt per pool.
- DECIDING landing: expiry slot + 2, priced on the state AFTER all prints with slot <= expiry + 2 (behind every same-slot buyer). Report-only landings: expiry + 0 (the decision state, ahead of all same-slot prints: optimistic) and expiry + round(1.9 / slot_s). REQUIRED (for advancing): mean > 0 at expiry + round(2.5 / slot_s) under both fail models.
- Fees: both legs at the config base fee at the landing point (end fee after expiry; cliff fee for mode 2), plus, for configs with dynamic_fee.initialized, the dynamic excess = max(0, the pool's last observed same-side rate minus the config base fee at that print's point). Validation (counts only): tape rate vs the config base fee on prints in [activation, expiry) and [expiry, expiry + 600 s) of candidate pools (share within 1 bp and within 10 bp; dynamic-fee configs reported separately).
- Exit: time only. Cells: d9_t60 (60 s) = PRIMARY, d9_t300 (300 s). Exit lands 1.9 s after the fire.
- Control (report-only, never a label): each D9 attempt pool is also entered at a random instant, uniform in [activation + 60 s, expiry + 3,600 s] (numpy default_rng(1), pools in pool order), same filters at that instant (refusals counted), same landing offset, fees and exits.

## Pricing (as P14/P16/P17)
- Stake 0.25 SOL DECIDING; 0.1 and 1.0 SOL report-only. Own impact; our buy re-injected into the exit state. Guard: exec/decision spot > 1.15 or the buy does not fit before the migration threshold = not filled, one send paid.
- Sends 2 x 160,000 lamports per filled trip. Rent 0 deciding; a rent-charged line (2,039,280 lamports per filled trip) for every stake (at 0.1 SOL the rent is 2.0% of the stake).
- D7 latency: entry and exit legs 1.9 s, k = round(1.9 / slot_s) per hour (printed). REQUIRED (for advancing): flat AND pressure mean > 0 at a 2.5 s entry leg. Report-only: entry 1.0 / 1.3 / 3.4 s, exit 3.4 s.
- Fail models: flat 15% and pressure at slope scale 1 with the intercept refit on the cell's fills to mean p 0.289 (tools.arena_stack.fail_legs; ssb = buy prints in the landing slot, nearby = buy lamports in the 2 s before it). BOTH decide. The pressure model is pump-fit and unvalidated on DBC. D9 adds a report-only flat 50% line (public known-slot race).
- Strand (D7 and D9): PRIMARY = full loss (stake + send + rent). Report-only: the last on-curve price. The P16 DAMM continuation line is NOT computed in this lane (E2 has no DAMM data; E1 data clipped); strands are counted per cell so its weight is visible.
- Order and refusals: P14 order fix and bad-row refusal as coded, plus a report-only "no bad-row exclusion" line (bad_row reads the exit state: A3 lookahead; a live book cannot know bad_row).
- Capital (D7): maximum concurrent open exposure at 0.25 SOL, and a line capped at 1.29 SOL open (first-come by decision time; an attempt that would pass the cap is skipped and counted).
- D7 control (report-only): share of filled trips that end at the time cap.

## Stats per cell, under both fail models (EXPLORATION ONLY)
Counts (attempts, fills, guard-unfilled, strands, refusals by reason); per-day n and SOL; mean SOL per attempt; total; ex-top-3; ex-best-day; CI90 lower (boot_lo: 1,000 draws, seed 1, 5th percentile, over all attempts incl. guard-unfilled) and the date-clustered bound as information; a Bonferroni-style one-sided lower bound at alpha 0.05/m (normal approximation, mean - z(1 - 0.05/m) x sd / sqrt(n), m = 32); days positive / days; fee paid bps (buy, sell, round trip); per-era split; E2-alone block.
Label: MARGINAL = both means > 0, ex-top-3 > 0, ex-best-day > 0 (all blocks); else FAIL. 0 is a fail. Best-of-n printed.

## Advance rule (fixed now)
At most ONE cell per idea advances to a later registration: the PRIMARY (D7 d7_s105_h6, D9 d9_t60), never the best cell. It advances only if, under BOTH fail models, it is MARGINAL on E2 alone AND on E1 + E2, with ex-top-3 > 0, ex-best-day > 0 and a strict majority of days positive, AND the REQUIRED latency line (D7 2.5 s entry; D9 expiry + round(2.5 / slot_s)) has mean > 0 under both models. Advancing means only "eligible for a non-D1 registration on hours >= 2026-11-01T00Z (QP (c))"; expect shrinkage (m10 fell to about one-fifth on the Swap3 day). Never a gate pass, never a promotion, never a live licence.

## Order
1. This file committed and pushed; sha256 and `date -u` recorded in SHA256SUMS.txt.
2. Code + tests (guard test) committed.
3. Config decode + D9 fee validation + a counts-only pass on the real layout (attempts per day per cell, refusals, exclusions); counts committed before pricing.
4. One pricing run (MiScusi job, <= 8 GB). REPORT.md labelled exploration; library rows updated.

## Disclosures
- The ideas were written after P14, P16 and P17 results were seen.
- m = 26 before, 32 after (6 labelled cells). Report-only lines do not add to m.
- E2 status: spent for D1, read by P17 (counts, validation, 11-cell screen).
- Credits: 0 planned (cached configs); any E3 fetch is reported with its job id and credits.
- No sentence in this lane says a cell makes money.

Declared at: 2026-10-24T13:49:01Z
