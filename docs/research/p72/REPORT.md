# P72: absorb a mechanical forced-sell cluster on PumpSwap (adapted from p25-pump-order-keeper-absorb). Verdict: KILL

Lane P72, 2026-10-27, written by the lane agent. Numbers are copied unrounded from `p72_price_025_output.txt`, `p72_analyze_025_output.txt` and `p72_analyze_100_output.txt`. In-sample lab dates that at least 14 families have already read. No book has cleared the promotion gate; this is a KILL, so no lead is claimed.

## Rule (PREREG.md, committed at e33d7647 before any count)

One set, no grid: cluster = at least K=3 distinct sellers within W=10 slots, net sell at least X=2.0% of the PRE-trade quote reserve (q+V), no buy between, quote reserve at least 50 SOL. Buy 0.25 SOL landing 2 slots after the cluster's last sell (L=2). Exit at the first of +10%, -10% or 600 s, sell landing 2 slots after the trigger. One open position per pool. Population: the 36 exploration days of the PumpSwap V-era migration paths P55 used (first 7,300 slots of each migrated pool), 24,323 pools. PRE-trade convention check on this data: sign(q[j+1]-q[j]) matches the side of print j in 130,284,409 of 130,302,118 changes (0.9999).

## Counts (before any pricing)

- Raw clusters: 981,842 (27,273.3889 per day over 36 days). After the one-open-position rule: 425,236 clusters, 11,812.1111 per day; 94 had no later print and are unfilled; 425,142 priced trades. All 36 days have trades. The kill line "fewer than 2 clusters a day" does not apply: capacity in count terms is not the problem. (Many of these overlap in time across pools; the count is not a claim that the book could take them all at once.)

## Result at 0.25 SOL (exit lag 2)

| | no fail | flat 15% | pressure-fail, slope 1 |
|---|---:|---:|---:|
| mean net, % of stake | -3.09927 | -2.63768 | -2.37532 |
| mean net, SOL | -0.00775 | -0.00659 | -0.00594 |
| total SOL | -3,294.07413 | -2,803.47043 | -2,524.61615 |
| CI90 lower bound of mean (date-cluster, 1,000 draws, seed 1), % | -3.19168 | -2.71623 | -2.45632 |
| total without the top 3 trades | -3,303.21036 | -2,811.2362 | -2,532.64675 |
| total without the best day (2026-09-18) | -3,289.47471 | -2,799.55561 | -2,521.11164 |
| days with a positive total | 0 of 36 | 0 of 36 | 0 of 36 |

- Hit rate (exit at the +10% target): 41.444%. Stopped at -10%: 58.091%. Time cap: 0.465%; 1,489 trades were censored by the end of the pool window.
- No day is positive. Removing the top 3 or the best day does not change it. Kill lines 1 (mean net <= 0, both models) and 3 (total without the top 3 <= 0) are hit. The median trade is -7.09928% (flat) and -4.76553% (pressure-fail).
- Why, mechanically: a 41% target hit rate against a 58% stop rate on a symmetric +/-10% band, with PumpSwap fee tiers on both legs, own price impact and a 2-slot exit lag, is a negative expectancy. The stop gaps past -10%.
- Pressure-fail mean p of failure: 0.2890 (curve intercept fit as in tools/latency_curve).

## Died vs survived (report only; uses information after entry, not causal)

`died` = quote reserve at the pool's last in-window print below 50% of the reserve at the cluster print.

| | trades | target hit | stop | mean net no-fail / flat / pressure (% of stake) |
|---|---:|---:|---:|---|
| died | 260,330 | 40.322% | 59.492% | -4.2242 / -3.5938 / -3.2023 |
| survived | 164,812 | 43.217% | 55.878% | -1.3224 / -1.1274 / -1.069 |

Both groups lose. Pools that later died lose more, as expected, but the survivors are also negative: there is no reversion edge to absorb even in pools that did not die. Pools that died are 61.2% of trades.

## Capacity line

Trades/day 11,809.5 x 0.25 SOL x -2.6377% (lower model, flat) = -77.87418 SOL/day. At 1 SOL (rule re-run, same thresholds, report-only; `p72_analyze_100_output.txt`): 423,990 trades, mean -2.61028% flat and -2.34945% pressure-fail, 0 of 36 days positive; -307.42608 SOL/day. Impact is not what kills it: the percent loss is almost unchanged from 0.25 to 1 SOL, because the cost is the fee legs, the exit lag and the stop gap.

## What was not measured

- The tagged keeper version (app take-profit/stop-loss orders, `p29-keeper-absorb`). The tape carries no order tag; the keeper history starts 2026-09-03. This mechanical lane is the untagged proxy and says nothing about tagged sells.
- Other K, W, X, T, S, L (pre-registered as a single set; no grid). A different exit lag or a wider band was not tried.
- Clusters after the first 7,300 slots of a pool's PumpSwap life, and non-migrated pools.
- Bonding-curve pools.

## Seals

Aug/Sep exploration paths only (audit-1008 migration paths). No October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output or job. Pricing ran under `systemd-run --user --scope -p MemoryMax=8G`.

## Library

`p25-pump-order-keeper-absorb` row updated (status unchanged, the tagged version stays untested); `docs/library/INDEX.md` rebuilt and `tools/test_library_index.py` passes (23 tests).

## Files

`PREREG.md`, `p72_run.py`, `p72_analyze.py`, `p72_count_output.txt`, `p72_price_025_output.txt`, `p72_price_100_output.txt`, `p72_analyze_025_output.txt`, `p72_analyze_100_output.txt`.
