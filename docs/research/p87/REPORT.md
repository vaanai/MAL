# P87 B7 dip-absorption pullback: REPORT. Verdict: KILL

Lane P87, manager17, 2026-10-27. PREREG.md (680a0ecf) and Amendment 1 were pushed before any pricing; counts were pushed before pricing. Numbers are copied from `p87_price_output.txt` (and `p87_count_output.txt`). In-sample lab dates read by at least 14 families; no book has cleared the promotion gate. Paper only.

## Rule (unchanged from PREREG, with Amendment 1)
B7 tradeable pool-hours, price down >= 8% over >= 15 min, net buy flow > 0 over the last 60 s, pool >= 6 h since first B7 print, 0.25 SOL, lag 2; exit +6% / -8% (net of sell fee and impact, P72 mark convention) / 45 min; one position per pool. Amendment 1 (QP-review warning): drop pool-hours with median mcap > 5M SOL; drop signals with trailing 6 h range < 2%.

## Filters and composition
- Tradeable B7 pool-hours before filters: 28,712; dropped by mcap > 5M SOL: 693.
- Range filter dropped 0 signals: a signal requires a >= 8% fall within the last 15 min, so the trailing 6 h range is above 2% by construction. The filter is vacuous for this rule (quiet/pegged pools never trigger the dip).
- Funnel (prints): 624,202 dip; 369,092 with absorption; 133,532 with age; 132,051 in tradeable pool-hours; 441 trades after one open position per pool. (The count-mode run, which used the 45-min upper bound as cooldown, showed 275; pricing uses actual exits.)
- Composition: 52 distinct pools; the top 5 pools carry 0.68481 of trades (104, 104, 47, 25, 22). Entry mcap SOL quantiles 5/25/50/75/95/max: 102,379 / 127,187 / 179,974 / 321,844 / 639,214 / 4,226,803. Heavily concentrated in a few pools.

## Results (28 days, 26 with trades; 441 trades; 15.75 trades/day)
| | flat 15% | pressure-fail |
|---|---:|---:|
| mean, % of stake | -0.43635 | -0.3685 |
| mean, SOL | -0.00109 | -0.00092 |
| total SOL | -0.48108 | -0.40628 |
| total ex top 3 | -0.73301 | -0.64785 |
| total ex best day (2026-09-14) | -0.59613 | -0.49405 |
| days positive | 11/26 (no majority) | 13/26 (no majority) |
| CI90 lower bound (%) | -0.78277 | -0.65536 |

Median trade +1.78167% (flat), mean negative: many small wins at the +6% target (193 tp), 134 stops, 114 timers (39 censored by the retention cut, which flatters). Tail share (flat loss > 50%): 0.00454 (2 trades). The 15% deep-tail of P78-M is absent because of the -8% stop.
Capacity: 0.25 SOL: 15.75 x 0.25 x -0.43635% = -0.01718 SOL/day. 1 SOL (same entries, own impact at 1 SOL): mean -0.40564% flat / -0.34016% pressure, capacity -0.06389 SOL/day.

## Per block
| block | trades | /day | flat mean % | pressure mean % | ex top 3 flat SOL | days pos flat / press | capacity 0.25 SOL/day | tail |
|---|---:|---:|---:|---:|---:|---|---:|---:|
| explore-0814 | 250 | 16.6667 | -0.46507 | -0.44606 | -0.34971 | 6/13, 6/13 | -0.01938 | 0.0 |
| fresh-0903 | 118 | 16.8571 | -0.30129 | -0.11275 | -0.34037 | 3/7, 5/7 | -0.0127 | 0.01695 |
| exp011-0909 | 73 | 10.4286 | -0.5563 | -0.53362 | -0.15018 | 3/7, 3/7 | -0.0145 | 0.0 |
1 SOL per block and the CI90s are in `p87_price_output.txt`. Every block is negative under both models.

## Verdict
KILL by the frozen line: mean <= 0 under both fail models, pooled and in every block. (Trades/day 15.75 clears the 5/day line; the top-3 line is moot since the total is negative anyway.) A rule with a median trade of +1.8% and a negative mean is the pattern of stop/target asymmetry plus fees (0.6% floor) and lag: +6%/-8% with 44% target hits is not enough. Not measured: other thresholds (deliberately no grid), the sub-9,820 SOL tape (positions censored at the retention cut are flattered, so the true result is no better), any block outside Aug/Sep exploration. Run note: priced under a 16 GB scope (8 GB was OOM-killed on the big days), single process.

Seals: only the three exploration clean views' slim rows (P78) were read. No October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50.
