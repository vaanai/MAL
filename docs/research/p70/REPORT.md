# P70: passive PumpSwap LP fee income, marked to market (web-public-pnl-1). Verdict: KILL

Lane P70, 2026-10-27. PREREG.md was committed at 536df55d before any pricing. Numbers are copied unrounded from `p70_analyze_output.txt`. Paper only, exploration tape only. These Aug/Sep days are in-sample for the lab (read by at least 14 families); a positive here would have been a lead, never a pass. This is a KILL. No book has cleared the promotion gate.

## What was run

- Population: 24 eligible UTC days (2026-08-16 .. 2026-09-23, every hour of d-1, d, d+1 in the tape), top 10 canonical PumpSwap pools per day by prior-day SOL volume (rank causal, day d-1 only): 240 pool-days, 199 distinct pools (`p70_select_output.txt`). Canonical = bonding-origin mint with a `complete` migration row in the tape (pools migrated before 2026-08-14 are not in the population).
- Position: 0.25 and 1 SOL, enter at 00:00Z (+2 slots), exit at 24 h (+2 slots). Entry = swap part of the stake to tokens through the curve (real fee tier, impact), deposit in the vault ratio; exit = withdraw pro rata and sell the tokens through the curve. V from the P55 pool map (V in [17.5, 17.7] SOL for 69.58% of pool-days, else 0). 4 priority fees of 55,000 lamports.
- Convention check: tape base reserve of print i+1 equals base of print i moved by token_raw in 99.818% of consecutive prints (PRE-trade confirmed; pools min 11.5%, rows out of order only); state at slot t = pre-state of the first later print.
- LP fee: the tape `lp_fee` field is null on every row, so fees use the real split table (PUMPSWAP_SOL_FEE_SPLIT): 0.02% of volume below 420 SOL market cap, 0.20% above.
- Fail models, exit leg only (PREREG): a failed exit pays one 55,000-lamport priority fee and the position stays 24 h more, then exits (48 h branch). Flat p = 0.15; pressure-fail scale 1, intercept -0.90965382630416 fitted so mean p = 0.289 over the 240 exit sends.
- Deviation, stated: the PREREG wrote the LP fraction as f = x/(Q_real+x). The code uses g = x/Q_real after the entry swap (our claim relative to the existing LP supply; withdrawing g of the tape reserves leaves the tape state for the sale). The two differ by a factor 1/(1+g) with median g 0.00037569511760513754 (0.25 SOL) and 0.0015019267331937598 (1 SOL), so the effect is below 0.2%.
- Deviation, stated: the net also contains the price drift of the tokens bought to fund the deposit, since the outlay is SOL. The PREREG net definition is outlay-based and this is part of it; a drift-free view (fees + IL - costs) is also reported.

## Results (24h branch composition, mean per pool-day, SOL)

| | 0.25 SOL | 1 SOL |
|---|---:|---:|
| net (24 h, no fail model) | -0.0553283045190426 | -0.2291632950916091 |
| LP fees accrued | +0.0044611561474740425 | +0.01783589306066501 |
| impermanent loss vs holding the deposit | -0.02496070802205359 | -0.10017701493807818 |
| price drift of the bought-token half | -0.031126363649513475 | -0.124182902522292 |
| entry cost | -0.00187634941402703 | -0.011858121358837485 |
| exit cost | -0.0016060395809225443 | -0.010561149333066434 |
| drift-free net (fees + IL - costs) | -0.024202 | -0.104980 |

Median fees accrued are 0.6074728 % of stake at 0.25 SOL and 0.6070603 % at 1 SOL (median 0.001518682 SOL and 0.006070604 SOL). Fees are real but small next to IL.

## Gate-style read

| | 0.25 flat | 0.25 press | 1.0 flat | 1.0 press |
|---|---:|---:|---:|---:|
| mean per pool-day (SOL) | -0.056842 | -0.058372 | -0.235193 | -0.241292 |
| mean, % of stake | -22.7367 | -23.3490 | -23.5193 | -24.1292 |
| total (SOL) | -13.642036 | -14.009378 | -56.446205 | -57.909983 |
| total without the top 3 | -14.569884 | -14.923948 | -60.156315 | -61.568367 |
| total without the best day (2026-09-10) | -13.723994 | -14.325080 | -56.777307 | -59.175710 |
| days with majority-positive pool-days | 0 of 24 | 0 of 24 | 0 of 24 | 0 of 24 |
| CI90 lower bound of the mean (date-cluster, 1,000, seed 1) | -0.068451 | -0.069478 | -0.277531 | -0.286578 |
| share of pool-days positive | 0.1708 | 0.1708 | 0.1583 | 0.1583 |

Per-day net is in `p70_analyze_output.txt`: 23 of 24 days are negative under the flat model at both stakes; the only positive day is 2026-09-10 (+0.081957 SOL at 0.25, +0.331102 at 1 SOL under flat).

## Dependence on collapsing pools (rug)

A pool-day is collapsed when the exit mid price is under half the entry mid price.

| 0.25 SOL, flat | n | total (SOL) | mean (SOL) |
|---|---:|---:|---:|
| collapsed | 81 (33.75%) | -12.342724 | -0.152379 |
| survivors | 159 | -1.299312 | -0.008172 |

At 1 SOL flat: collapsed 81, -49.367070 total (mean -0.609470); survivors 159, -7.079135 (mean -0.044523). Pressure model: survivors -1.536704 (0.25) and -8.025728 (1). So collapsing pools make up about 90% of the loss, but the survivors alone are also negative under every cell, so removing the rugs does not rescue the idea (and a filter that removes collapsed pools is not causal). 23 pool-days ended with under 5 SOL of real quote reserve.

## Capacity

10 pool-days per day x stake x mean net: -0.568418 SOL/day (0.25, flat) and -2.351925 SOL/day (1, flat); -0.583724 and -2.412916 under pressure-fail. There is no positive capacity to size. Pool share is small (median g 0.0376% at 0.25, 0.150% at 1 SOL), so LP share does not bind; entry and exit swap costs are already inside the numbers and rise from 0.00187634941402703 to 0.011858121358837485 SOL at entry from 0.25 to 1 SOL (about 6.3x for 4x the stake).

## Kill lines

1. Mean net per pool-day <= 0 under flat: hit (both stakes). Under pressure-fail: hit.
2. Total without the top 3 <= 0: hit (all four cells).
3. Total without the best day <= 0: hit (all four cells).

Verdict: KILL. Not measured: LP positions for pools migrated before the tape starts; exits at times other than 24 h; any October hour; a hedged (delta-neutral) LP, which would remove the drift and IL but also needs a short leg that PumpSwap does not offer; real withdraw latency. Even the drift-free view (fees + IL - costs) has mean -0.024202 SOL at 0.25 SOL, with 9.58% of pool-days positive, so the loss is not an artefact of the token exposure.

## Files

`PREREG.md`, `p70_select.py`, `p70_select_output.txt`, `p70_extract.py`, `p70_price.py`, `p70_analyze.py`, `p70_analyze_output.txt`, `selection.parquet`.
