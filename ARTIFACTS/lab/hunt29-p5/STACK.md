# P5 STACK $: bankroll model for stacked books (written 2026-10-24 ~01:00Z, `date -u` checked)

**Everything here is an in-sample exploration screen number (Aug/Sep blocks), best-of-N, with winner's curse. In-sample is not forward. No sentence here says a book makes money; quant-proof decides.** No forward-book output, ledger, bots.json, runner status, canary/probe output or October outcome was opened. Gate status: no book has cleared the promotion gate; FLOOR has a screen verdict KILLED/NOT_DECIDABLE (22% block-edge exclusion > 5%); Q50 has no edge figure at all.

## Inputs and how they were built
- **FLOOR v2** (tools/arena_rule_l1.py, rule file l1_rule_tpfix.py): MiScusi job #1416 re-ran the kit (tools/arena_screen.py at 9577a8d3, send 160,000, E1 floor, rent 0, V, 1.9 s, both fail models) on the same four exploration blocks as hunt28/l3 (explore-0814, oracle-insample 09-22..25, fast-pool 09-18..22, oracle-live 09-25..27), and dumped per-trade arrays and pnl at stakes 0.05..2.0 (`dump_floor.py`). 332 trades, 25 days; 0.25 SOL pressure total/day 0.1707 reproduces hunt28/l3 (0.1707). Exits: 297 time (24 h), 35 TP. Entry second inside the hour is not dumped (hour start + uniform jitter); TP hold uses 0.4 s/slot.
- **H5** (no arena-stack trade-level screen exists): trade rows of the frozen H5 v1 rule, Q*=40, 1.9 s leg, 330 s exit, Sept 3-25 confirmation (h5-flows/out/boostdip_frozen_conf.parquet, 1,124 trades), repriced from the 55k-send stack to 160,000 per send plus E1 floor 0.768% of stake (`h5_rows.py`). Not rent-charged, no stranding, no worst-in-slot, no md5-equal executor: this is softer than the arena stack. a2 v1/v2 and Q50 are siblings using the same pools; Q50 (31/day strict) has "no Q50 edge exists" (hunt24/l3-boost/DESIGN.md), so it is NOT stacked; it would be a correlated copy, not extra capital-independent edge. "Latest regime" rows = trades on 09-18 or later (526 trades, flat/press about +4.2%/+3.8% at 0.25 SOL); all-Sept = pooled (flat about +11.7%).
- **HV4**: no screen number exists (Haiku v3/v4 learning approx placebo). Not modelled.
- **Trades/day**: FLOOR 13.28 (in-sample). H5 13/day (October arena counts 11-16/day, not outcomes); the September rate 53.5/day is stale (decayed; October 3-16/day).
- **Stake/impact models.** A = screen-implied (FLOOR: kit pricing at that stake, which barely moves per-trade % with stake; H5: only the fixed 2x160k fee amortisation). B = capacity-pessimistic (A minus entry-impact growth vs 0.25 SOL: FLOOR s/Qe per trade; H5 2(s-0.25)/q, q about 38 SOL, about 5.9% at 1 SOL as in cap-impact). Both warn: the kit and h5-flows hold other traders' reserve deltas fixed and understate impact in drained pools. **B is the headline**; A is an upper bound. Pressure fail model is reported as the lower of the two.
- Fail models: flat 15% and pressure slope 1 (H5: mean p 0.289).
- **Stacked sim** (`stack.py`, `run_stack.py`): 1,500 runs x 32 simulated days; each day draws a random real FLOOR day (its trades with their 24 h holds) and Poisson(13) H5 trades from the latest-regime pool at uniform times; one wallet; a position is skipped if free capital < stake + 0.02 SOL; realised P&L recycles into capital; daily P&L booked on exit day, first 2 days dropped. Books come from different dates (FLOOR Aug14-28 + Sep18-27, H5 Sep 3-25) so they are paired by independent day draws; real cross-book correlation is unknown. The wallet stake grid-search is itself an in-sample optimisation. p(30-day<0) only reflects day-to-day variance given the screen edge; it does not include the chance the screen edge is zero or negative.
- **SOL/USD assumed $150** (inside the $130-$200 band in ARTIFACTS/lab/push-2026-10-16.md; not a market quote, no price feed read today). Multiply by 200/150 for $200.

## 1. Per-book best stake (in-sample SOL/day, unlimited capital) and capital tied
Source: stake_tables.csv. avg_open = trades/day x mean hold / 24 h.

| book | variant | fail | best stake SOL | SOL/day (ex-top-3 per day) | capital tied at best stake |
|---|---|---|---|---|---|
| FLOOR v2, 13.28/day | B | press | **0.50** | 0.250 (0.189) | 12.2 avg open x 0.5 = **6.1 SOL** |
| FLOOR v2 | B | flat | 0.75 | 0.361 (0.254) | 9.1 SOL |
| FLOOR v2 | A | press | 2.0 (grid edge, still rising) | 1.199 (0.989) | 24.4 SOL: meaningless, impact understated |
| FLOOR v2 at 0.25 (reference) | any | press | 0.25 | 0.171 (0.140) | 3.05 SOL |
| H5 latest-regime, 13/day | B | press | **0.50** | 0.181 (0.094) | 0.05 avg open; peak not modelled (BOOST bursts), 4 open x 0.5 = 2 SOL |
| H5 latest-regime | B | flat | 0.50 | 0.209 (0.063) | same |
| H5 latest-regime | A | press | 2.0 (edge) | 1.007 (0.658) | n/a |
| H5 all-Sept rows, 13/day | B | press | 1.5 | 1.039 (0.455) | peak 4 x 1.5 = 6 SOL |
| H5 all-Sept, 53.5/day (stale rate) | B | press | 1.5 | 4.278 | not a forward-relevant rate |

Reading: FLOOR is **capital-bound**: 297 of 332 trades hold 24 h, so about 12 positions are open at once. At 1 SOL you cannot hold more than about 4 at 0.25 or 12 at 0.08 SOL. H5 is trigger-rate-bound (13/day x 330 s holds), almost no capital but a thin, decayed edge. FLOOR's 0.25 SOL impact is 1.37% and 5.48% at 1.0 SOL (kit entry impact); its mean is carried by 35 TP hits (+50%), median trade is negative (median return -1.9%).

## 2. Stacked bankroll (FLOOR v2 + H5 latest-regime 13/day, B impact, mean SOL/day; $ at $150)
Stakes (sF, sH) found by grid search per wallet (in-sample): 1 SOL: 0.15/0.25; 2 SOL: 0.25/0.50; 5 SOL: 0.25/0.50. Source: stack_results.csv.

| wallet | fail | screen SOL/day | screen $/day | 50%-haircut SOL/day | haircut $/day | daily p05 / p50 / p95 SOL (screen, press) | worst day (median run / min over all) SOL | drop-best-day SOL/day | take rate FLOOR/H5 |
|---|---|---|---|---|---|---|---|---|---|
| 1 SOL | pressure | 0.196 | $29 | 0.068 | $10 | -0.22 / 0.14 / 0.78 | -0.30 / -0.99 | 0.168 | 69% / 73% |
| 1 SOL | flat | 0.243 | $36 | 0.085 | $13 | -0.27 / 0.15 / 1.00 | -0.38 / -1.24 | 0.204 | 71% / 74% |
| 2 SOL | pressure | 0.288 | $43 | 0.103 | $16 | -0.50 / 0.17 / 1.39 | -0.67 / -2.09 | 0.236 | 68% / 70% |
| 2 SOL | flat | 0.353 | $53 | 0.128 | $19 | -0.62 / 0.18 / 1.77 | -0.84 / -2.60 | 0.278 | 69% / 71% |
| 5 SOL | pressure | 0.393 | $59 | 0.183 | $27 | -0.55 / 0.32 / 1.60 | -0.73 / -2.09 | 0.335 | 95% / 95% |
| 5 SOL | flat | 0.475 | $71 | 0.223 | $33 | -0.70 / 0.34 / 2.05 | -0.91 / -2.60 | 0.388 | 95% / 95% |

Sensitivities (pressure, screen, B): H5 bursts compressed into a 4 h window: 1 SOL 0.191, 2 SOL 0.282, 5 SOL 0.390 SOL/day (barely changes: skips come from FLOOR's 24 h holds). H5 rate 16/day: 0.226 / 0.327 / 0.436. H5 pool = all-Sept rows (optimistic, early-Sept edge): 0.397 / 0.682 / 0.810 SOL/day = $60 / $102 / $121. Variant A (no impact growth): 1 SOL 0.181, 2 SOL 0.362, 5 SOL 0.464 (pressure).
The tails: the single worst simulated day loses 1.0 SOL at 1 SOL wallet (a whole wallet's worth of positions can be down on one day because 12 FLOOR positions are open together); 30-day total negative in 2% of runs (screen edge held fixed) at 1 SOL, 0.3% at 5 SOL; at 50% haircut 19% / 9% of runs.
**Blunt:** even the screen numbers give $29-$121/day at 1-5 SOL; the owner's end-November $200/day is not reached by these books at these wallet sizes. A 50% haircut gives $10-$33/day. Because H5's latest-regime edge (+3.8% press) is the lower tail of a decaying series (+27% -> +12% -> +5% -> +7% flat by block), and FLOOR's is 35 lottery hits, a zero or negative true edge is fully consistent with these numbers (best-of-N, CI-lower from the ladder DEC-031 not yet earned).

## 3. DEC-032-consistent stake ladder (proposal, not authorised; needs the owner's line, quant-proof and Helm per DEC-032 sections 7-8)
DEC-032 limits (section 3): C0 stake <= 0.05 SOL, max open 2, 30 buy attempts/UTC day, daily stop 0.10, total stop 0.20, funded 0.35-1.00; C1 stake <= 0.10, open 3, 40/day, daily stop 0.20, total stop 0.45, funded 1.00. Beyond C1 is "not defined" and needs a new amendment plus owner capital. The template covers H5-style books; FLOOR has no executor (P2 lane) and its 24 h holds make max-open 2-3 per book impossible to match (12 average open): at C0/C1 limits FLOOR would take only about 10-15% of its signals.

| rung | stake / limits | evidence to step up (DEC-032 section 7 plus proposal) | screen $/day (press / flat) | 50% haircut $/day | notes |
|---|---|---|---|---|---|
| C0 | 0.05 SOL, open<=2 per book, 1 SOL wallet | book's gate-read PASS on the screen cell, executor dry-proof, owner's standing OK (10-23), Helm install | $5.0 / $6.1 (H5 takes about 100% of triggers, FLOOR 10%) | $2.6 / $3.2 | an execution measurement. Fixed cost 0.64% of stake at 160k. Cannot reach $20/day under any assumption. |
| C1 | 0.10 SOL, open<=3, 1.00 SOL funded | confirmation PASS (>=100 attempts on >=5 further dates, p<=0.05/c), parity shown (>=100 trips, CI90 lo >= -100 bps), 5 signed daily tie-outs, impact re-priced at 0.10, owner line | $10.9 / $13.2 | $5.6 / $6.7 | the first rung that is even in the $10-$13 range; screen numbers |
| C2 (proposal, needs new amendment) | H5 0.25-0.5, FLOOR 0.15-0.25, no per-book open cap but a shared-wallet cap, 1 SOL then 2 SOL | C1 held for >= N further days with the realised-vs-twin mean within the agreed band, impact re-priced at the stake with the engine's own impact (not this note's B), owner adds SOL | 1 SOL: $29 / $36; 2 SOL: $43 / $53 | $10 / $13; $16 / $19 | requires FLOOR executor; H5 alone at 0.25-0.5 is about 0.12-0.18 SOL/day = $18-$27 screen (latest regime), about half that haircut |
| C3 (proposal) | 5 SOL, FLOOR 0.25, H5 0.5 | same plus a second independent confirmation window of the stacked pair | $59 / $71 | $27 / $33 | still below $200/day. Reaching $200/day would need other books or the all-Sept H5 edge to return (even then about $120/day). |

Impact must be re-priced at each stake with the engine's own impact; this note's B model is a desk bound, not the engine.

## What was not measured
Forward fills, live landing, any October outcome, rent/strand/worst-in-slot for H5 rows, H5 absolute timestamps (day-level only), FLOOR TP hold durations (0.4 s/slot approximation), Q50/a2 sibling edges, HV4, cross-book correlation, SOL/USD today, the mid-position mark-to-market drawdown (only realised P&L booked on exit day), and wallet reserve for rent beyond 0.02 SOL.

## Files
`stack.py`, `run_stack.py`, `dump_floor.py`, `floor_job.sh` (job #1416, 16 GB, about 19 min), `h5_rows.py`, `stake_tables.csv`, `stack_results.csv`, `stack_results.json` under /data/mal/hunt-1008/push-1016/hunt29/p5/ and copied (except json/npz) to ARTIFACTS/lab/hunt29-p5/ in the PR.
