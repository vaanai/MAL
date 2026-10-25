# P28 pricing results (explore-0814, in-sample exploration)

2026-10-25. Declared in `PRICING-DECLARE.md` (committed 77547bc4 before any price; rules, driver, tests a840780d). MiScusi jobs: #1612 CALL-SYNC (23 min, exit 0), #1613 FLUSH f2 + mirror veto (24 min, exit 0), both `mal-research-0`, 8 GB / 2 CPU cap, peak RSS 0.66 and 1.35 GB, code at the job's checkout plus the pushed branch. Raw: `p28_callsync_result.json`, `p28_flush_result.json`.
Block: explore-0814, 336 h, 15 UTC dates (first and last partial). Costs: send 160,000 deciding (505,000 beside), 1.9 s leg, 0.25 SOL, rent 0, strand `total`, tier fees, flat 15% and pressure slope 1. Fill on the PRE-trade state of the first print at slot >= decision + k + 1, V added (verified in `mint_states`). Not a kit screen of record (the kit's four-block KILL, worst-in-slot and screen p were not computed). Nothing here is an edge claim; no book has cleared the promotion gate. The kill rules were applied mechanically; no parameter was moved after a price.

## 1. CALL-SYNC cs_k12 + price-lag selector (m <= 2%): KILLED

| Cell | n | /day | flat mean SOL (%) | pressure mean SOL (%) | CI90 lo trade (flat / press) | ex-top-3 (flat / press) | ex-best-date (flat / press) | dates positive | median entry slip |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **cs12_lag (tested)** | 2,421 | 161 | -0.00549 (-2.19%) | -0.00466 (-1.86%) | -0.00614 / -0.00525 | -13.5 / -11.4 | -13.1 / -11.1 | 0 of 15 | 0.77% |
| cs12_all (control) | 4,424 | 295 | -0.00565 (-2.26%) | -0.00493 (-1.97%) | -0.00614 / -0.00536 | -25.5 / -22.2 | -24.6 / -21.4 | 0 of 15 | 1.76% |
| cs12_in (control) | 2,758 | 184 | -0.00590 (-2.36%) | -0.00518 (-2.07%) | -0.00653 / -0.00572 | -16.7 / -14.7 | -16.0 / -14.0 | 0 of 15 | 3.30% |

Kill rules on cs12_lag: 1 (mean <= 0, both models) FIRED; 2 (ex-top-3) FIRED; 3 (ex-best-date) FIRED; 4 (half the dates) FIRED, 0 of 15 under both models; 5 (median slip > 2%) did not fire (0.77%; p75 2.5%, p95 7.3%); 6 (< 10 trades/day) did not fire. Date-clustered CI90 lo: -0.00614 / -0.00521. Totals: -13.3 SOL flat, -11.3 SOL pressure over the block. Sensitivity (not deciding): send 505,000 flat -0.00612, pressure -0.00524; rent-charged flat -0.00718, pressure -0.00608. Exits (lag): time 904, tp 633, sl 884. Guard-refused 2.1% (lag), 10.3% (in). Strand 0.1%. Selector share: 11,069 of 16,867 bursts (66%) have m <= 2%.
By date (flat totals, n): every one of the 15 dates is negative, -0.19 to -1.70 SOL.
Reading, descriptive only: the selector does what it says on cost (median slip 3.3% for the complement vs 0.77% for the lag set, guard refusals 10.3% vs 2.1%) but the sign does not change: even when the fill lands within 1% of the decision price, the trades lose about 2% net, about the generic cost floor and exit mix of this universe. It also does not differ visibly from the breadth rows that died (same sign, same size).
Capacity line: 161 trades/day x (-0.00549 SOL/trade) = -0.89 SOL/day flat, -0.75 SOL/day pressure at 0.25 SOL. Nothing to size.

## 2. SIZE-SKEW mirror veto on FLUSH f2: VETO KILLED (share rule); kept book KILLED

Base: hunt29 I1 FLUSH f2 (imported from `docs/library/hunt29-p4/p4rules.py`; chosen as the simplest PumpSwap-WSOL entry with an exploration pricing-kit run; the repo's own P4 run had it FAIL at -2.75% flat on four blocks, this block alone gives -2.76%, which agrees). Base n 50,759 (3,384/day after the kit's overlap rule), flat mean -0.00691 (-2.76%), pressure -0.00582 (-2.33%), 0 of 15 dates positive, ex-top-3 -353 / -297 SOL.

| | n | flat mean SOL | pressure mean SOL | 
| --- | --- | --- | --- |
| base | 50,759 | -0.00691 | -0.00582 |
| kept (flag = 0) | 47,006 | -0.00658 | -0.00555 |
| flagged (mirror <= 1/3) | 3,753 | -0.01112 | -0.00925 |

Veto share 7.39% (3,753 flags; 48,618 of the 50,759 decisions had >= 10 buys and >= 10 sells in the 30 min window, so the flag is defined for 96%). Lift of the veto L = mean(kept) - mean(base): flat +0.00034 SOL/trade, pressure +0.00027. Lift over a random veto at matched share (paired bootstrap, 1,000 draws, seed 1): D mean +0.00034 / +0.00028, **5th percentile +0.00023 flat, +0.00018 pressure** (date-clustered 5th percentile +0.00024 / +0.00019). 1,000 random vetoes of the same size: kept mean 5th to 95th percentile -0.00697 to -0.00686 flat; none reaches the veto's -0.00658 (p = 0.000).
Kill rules: the lift rule did NOT fire (lower bound > 0 under both models). The share rule FIRED: 7.4% < 10%, so the veto is killed as declared. Kept-book rules 1-4 all FIRED under both models (0 of 15 dates positive, ex-top-3 -310.6 flat / -262.0 pressure, ex-best-date -301.2 / -254.0).
Verdict: **VETO KILLED** (share rule), and the book it would sit on is killed. For the record, the flag is statistically informative about FLUSH f2 (flagged trades lose 1.6x the base per trade), but the lift is 0.3% of the stake per trade: it removes about 5% of the loss, not the loss.
Capacity line: kept book 3,384 x 0.926 trades/day x (-0.00658) = -20.6 SOL/day flat. Nothing to size.

## What was and was not measured
- Measured: exploration-block pricing of two frozen rules on explore-0814, 336 h, the kit's entry/exit/fee model, send 160k deciding.
- Not measured: any other block (fresh-0903 stays unpriced, available as a later look), any October hour, forward paper, the kit's worst-in-slot leg, the kit's four-block KILL and screen p (not a screen of record), 1.0 SOL stake, other exits, other thresholds (none were tried, so there is no multiplicity beyond the declared two ideas plus two controls).
- Deviation from the counts: the mirror flag is the rolling 30 min form, not six aligned 300 s bins (disclosed in PRICING-DECLARE). The veto does not free pools: the kit's overlap rule ran on the whole base.

## Seal disclosure
Read: `/data/mal/clean-view/explore-0814` trade files (exploration pool) and the V maps `pool_v_0814.json`, `pool_v_0909.json`. Not opened or listed: `/data/mal/meta-capture/`, arena tape, forward-1016, any October hour, HV4/Q50/HV6/DBC outputs. Output dir `/data/mal/hunt-1008/push-1016/hunt30/p28-pricing/`.
