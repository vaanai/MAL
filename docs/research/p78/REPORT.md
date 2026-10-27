# P78 cost-floor tiers: REPORT. Verdict: LEAD (weak, in-sample) for one rule (P71 copy); KILL for the other two

Lane P78, manager17, 2026-10-27. PREREG.md was committed first (b1812a6e, edge text fixed in the next commit, both before any count). Numbers are copied from `p78_costmap_output.txt`, `p78_b7_price_output.txt`, `p78_b7_rule71_output.txt`. These are in-sample lab dates that at least 14 families have read; a LEAD here is not a pass. No book has cleared the promotion gate. Paper only.

## Step 1: cost map (28 UTC days: explore-0814, fresh-0903, exp011-0909; 1,728,875 pool-hours, 94,049 pools; 278,414,547 PumpSwap wSOL `...pump` prints)

PRE-trade check on the tape: sign(q[j+1]-q[j]) matches the side of print j in 276,665,175 of 276,684,859 changes (0.99993). Per-day values are means over the 28 days. Tradeable pool-hour = at least 20 prints and median depth (q+V) at least 100 SOL. Fee per leg is the table's creator+protocol+LP at the band's median market cap. Floor = round-trip fee + 2 x 55,000 lamports / stake + median 2-slot absolute move. The own-impact term is not in the floor: a buy sold straight back into one's own impact returns it (round-trip impact is the fee only); the non-reverting 2 x one-way sensitivity is in the last two rows.

| | B0 <420 | B1 420-1470 | B2 1470-9820 | B3 9820-24560 | B4 24560-58940 | B5 58940-78590 | B6 78590-98240 | B7 >=98240 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pools/day (any print) | 14,618 | 836.5 | 472.6 | 132.9 | 126.7 | 64.1 | 50.4 | 174.4 |
| pools/day tradeable | 22.0 | 321.8 | 290.2 | 102.1 | 108.5 | 61.5 | 46.8 | 151.9 |
| pool-hours/day (all) | 46,697 | 6,315 | 4,732 | 1,235 | 1,156 | 275 | 179 | 1,157 |
| tradeable pool-hours/day | 47.8 | 1,328 | 1,579 | 648.9 | 757.3 | 240.7 | 150.0 | 990.1 |
| of which depth >=400 SOL | 0 | 0.4 | 410 | 648.9 | 757.3 | 240.7 | 150.0 | 990.1 |
| fee/leg creator+protocol+LP ppm | 3000+9300+200 | 9500+500+2000 | 8000+500+2000 | 6500+500+2000 | 4500+500+2000 | 2250+500+2000 | 1250+500+2000 | 500+500+2000 |
| fee per leg % | 1.25 | 1.20 | 1.05 | 0.90 | 0.70 | 0.475 | 0.375 | 0.30 |
| round-trip fee % | 2.4844 | 2.3856 | 2.089 | 1.7919 | 1.3951 | 0.9477 | 0.7486 | 0.5991 |
| one-way impact 0.25 SOL % (median) | 0.2248 | 0.1786 | 0.0788 | 0.0389 | 0.0278 | 0.0222 | 0.0197 | 0.0107 |
| one-way impact 1 SOL % (median) | 0.8933 | 0.7108 | 0.3143 | 0.1554 | 0.1111 | 0.0885 | 0.0787 | 0.0428 |
| median 2-slot absolute move % | 0.8705 | 0.6142 | 0.2218 | 0.0669 | 0.0478 | 0.0316 | 0.0267 | 0.0048 |
| **round-trip floor at 0.25 SOL %** | 3.3989 | 3.0438 | 2.3548 | 1.9028 | 1.4869 | 1.0233 | 0.8193 | **0.6479** |
| round-trip floor at 1 SOL % | 3.3659 | 3.0108 | 2.3218 | 1.8698 | 1.4539 | 0.9903 | 0.7863 | 0.6149 |
| floor if own impact does not revert, 0.25 SOL % | 3.8486 | 3.4011 | 2.5123 | 1.9805 | 1.5425 | 1.0676 | 0.8586 | 0.6693 |
| floor if own impact does not revert, 1 SOL % | 5.1526 | 4.4324 | 2.9503 | 2.1805 | 1.6761 | 1.1674 | 0.9437 | 0.7006 |
| median depth (q+V) SOL | 109.6 | 138.0 | 313.8 | 637.0 | 893.2 | 1,123 | 1,265 | 2,326 |
| volume SOL/day | 362,394 | 703,846 | 742,523 | 209,094 | 198,865 | 94,748 | 63,197 | 924,436 |

So the floor falls from about 3.4% (B0, where P70-P76 lived) to 0.65% (B7), a factor of 5. Every band has at least 20 tradeable pools/day, so the frozen choice rule picks B7 (market cap >= 98,240 SOL, 0.30% per leg), 151.9 tradeable pools/day, 990.1 tradeable pool-hours/day. Capacity in pool-hours is not the constraint.

Caveats on the map: V is 17.5845 SOL where the mint is in the P55 paths meta, else 0 (deep pools have V about 0; error at most 1% of a 2,000 SOL reserve). The 2-slot move uses pairs of slots at most 6 apart; pool-hours have a median of 5 such pairs. The band uses the print's market cap from the supply-1B formula; it is the same one `tools/paper_curve_math` tiers on. The clean views have 2 unparseable JSON lines in the whole set (exp011-0909/b 09-15T03: 1; exp011-0909/c 09-12T07: 1; `skipped_lines.txt`), skipped with duckdb `ignore_errors=true` (the convention of p22 `wick_bound.py`). Job #1752 failed on that data error (peak memory 4.5 GB is the limit, but the cause was the malformed line, confirmed in the logs), not OOM; the loop skips finished hours.

## Step 2: three frozen rules inside B7 only

Adapter changes only (listed in `PREREG.md`; `p72_run_frozen.py` is a verbatim copy of `p72_run.py` from PR #1029, sha256 b041c206...). Retention: the extractor keeps only prints with market cap >= 9,820 SOL, and a pool's series is cut at a gap of more than 150 slots between retained prints. A position whose pool falls out of that range is therefore priced at the last retained state, which flatters it (the crash below 9,820 is not seen); `censored` counts these. 28 days, 5,055 pools, 19,190 segments, 156 pools skipped for slot time outside (0.15, 0.6).

| | P72 sell-cluster absorb | P71 copy (0.1 SOL) | P78-M 15-min momentum |
|---|---:|---:|---:|
| counts before pricing | 2,645 raw clusters (94.46/day) | 954 copies, 22 wallets, 13 Sep days | 9,668 signals (345.3/day) |
| trades | 1,257 | 954 | 9,661 |
| trades/day | 44.89 (28 days) | 73.38 (13 days) | 345.04 (28 days) |
| mean, flat 15% (% of stake) | -17.91992 | +0.13175 | -5.69432 |
| mean, pressure-fail (% of stake) | -17.21996 | +0.14068 | -4.66352 |
| mean, flat (SOL) | -0.0448 | +0.00013 | -0.01424 |
| CI90 lower bound flat / pressure (%) | -20.18229 / -19.38476 | -0.9873 / -0.73157 | -6.82338 / -5.58453 |
| total ex top 3, flat / pressure (SOL) | -56.46383 / -54.24838 | -0.01328 / +0.01279 | -138.68583 / -113.59836 |
| total ex best day, flat / pressure (SOL) | -56.15457 / -53.95828 | -0.11329 / -0.06004 | -137.48389 / -112.44185 |
| days positive (flat) | 0 of 28 | 9 of 13 (majority) | 0 of 28 |
| capacity, lower model | -2.01119 SOL/day at 0.25 | +0.00967 SOL/day at 0.1; 0.03536 at 0.25; 0.16369 at 1.0 | -4.91186 SOL/day at 0.25 |
| verdict | KILL | LEAD (weak, see below) | KILL |

P72 exits: stop 800, cap 320, target 137 (censored 139). 15.67% of its trades lost more than 50%; 15% of momentum trades did too (median momentum trade +4.35% flat, tail -97%; ex-tail mean +8.3%): these deep pools carry real dump events (example: a pool at 124k SOL market cap, 1,478 SOL depth, lost 63% in 175 slots to sells of 37, 8 and 286 SOL), so a lower fee floor does not remove the tail. Prior killed lanes at B0/B1: P72 -2.63768% flat at 0.25 SOL (mean now much worse, because the tail is heavier).

P71 copy in B7: ranking on explore-0814 in-band round trips (13,269 wallets with at least 5 round trips; 8,858 flagged as bots; 44 of the unfiltered top 50 flagged; top 50 clean sum 900.2485 SOL). Mean is positive under both models at all three stakes (0.1 SOL: +0.13175 flat, +0.14068 pressure; 0.25: +0.19276, +0.1971; 1.0: +0.22306, +0.22513), so by the lane's kill line it is a LEAD, in-sample. Everything else says it is fragile, and by P71's own frozen kill lines it would be killed:
- CI90 lower bound is below 0 at every stake (-0.9873 flat at 0.1 SOL);
- total without the top 3 is -0.01328 SOL (flat) at 0.1 SOL;
- total without the best day (2026-09-07) is negative at 0.1 and 0.25 SOL under both models (and flat at 1.0);
- the best wallet's share of the 0.1 SOL no-fail total is 1.9511 (195%), so without that wallet the total is negative (P71's kill line 3); only 22 wallets have a priced copy;
- 293 of 954 copies are timer exits truncated at a segment end, so those exits are flattered (see retention note);
- B1 bot counts use only the retained (>= 9,820 SOL) prints.
Mean edge of +0.13% of stake is smaller than the 0.65% floor it already pays, so it is not a margin that survives a 1-2 slot worse lag; capacity 0.01 SOL/day at 0.1 SOL.

## Verdict and what follows

- Cost floor: confirmed real and large (3.4% to 0.65%), the premise of the lane holds.
- P72 and P78-M: KILL. Cheaper fees do not cure negative expectancy; both lose in 28 of 28 days.
- P71 copy: LEAD (weak, in-sample), failing day, top-3, wallet-concentration and CI90 checks; no basis for forward registration without a new, untouched block and a corrected retention (full tape, not >= 9,820 SOL). It is not a promotion candidate.
- Not measured: other rules, other stakes for P72/P78-M, the sub-9,820 SOL tape in B7 positions, any block outside Aug/Sep exploration. No bot-flow filtering beyond P71's own flags.

Seals: only the three exploration clean views, Aug 14 to Sep 15, were read. No October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 data.
