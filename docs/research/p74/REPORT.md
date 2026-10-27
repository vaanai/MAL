# P74 report: Graduation play (web-oct-mechanics-1)

Verdict: **KILL**. Exploration tape (explore-0814, fresh-0903, exp011-0909; 28 UTC days), in-sample for the lab (at least 14 families have read these days). No book has cleared the promotion gate. Rule as in PREREG.md (P=85%, lag 2, exit at 99% / -20% stop / 30 min, one parameter set, no grid). Numbers from `p74_counts_output.txt` and `p74_analyze_output.txt`.

## Data checks (pre-pricing, Amendment 1)
- 44,049 mints reached 85% progress; **13,418 Mayhem mints excluded** (tokens.parquet `is_mayhem_mode`, as P73); 30,631 non-Mayhem candidates.
- Constant product on consecutive bonding pairs: 99.9949% (|k drift| < 1e-6) over 23,090,584 pairs, above the 99% requirement. Post-trade base-change check: 99.8412%. Rows with quote_reserve 0: 4.138% of bonding rows; entries whose entry path touched one were dropped (791).
- Of 30,631: 7,888 already above 85% at their first tape print (skipped); 22,743 observed crossings; 4,603 completed within the 2-slot lag (skipped, a selection against the fastest runs); 17,123 priced at 0.25 SOL (17,059 at 1 SOL).

## Results (net of 1.25% bonding fees per side, 55,000 lamports x 2 priority, exit lag 2)

| | 0.25 SOL | 1 SOL |
| --- | --- | --- |
| attempts/day (mean, min) | 611.5, 178 | 609.2, 177 |
| own-buy curve impact (mean) | 0.3115% | 1.1978% |
| sold-into-migration share | 9.33% (1,598) | 9.28% |
| exits: stop / a99 / migration / time | 12,177 / 2,774 / 1,598 / 574 | 12,162 / 2,742 / 1,583 / 572 |
| mean per trade, flat 15% | -0.010415 SOL (-4.166%) | -0.044825 SOL (-4.483%) |
| mean per trade, pressure-fail | -0.009246 SOL (-3.698%) | -0.039501 SOL (-3.950%) |
| total flat / ex-top-3 / ex-best-day | -178.3358 / -181.8381 / -179.9463 | -764.6720 / -777.8252 / -770.2154 |
| total pressure / ex-top-3 / ex-best-day | -158.3146 / -161.1416 / -159.6567 | -673.8434 / -684.6508 / -678.4618 |
| positive days (either model) | 1 of 28 | 1 of 28 |
| CI90 lower bound of mean (trade bootstrap, 1,000, seed 1), flat / pressure | -0.011906 / -0.010597 SOL | -0.050902 / -0.044978 SOL |

Capacity line (attempts/day x stake x net mean): -6.369 SOL/day (flat) and -5.654 (pressure) at 0.25 SOL; -27.310 and -24.066 at 1 SOL. Concurrency is not capped.

Completion: 55.5% of observed 85% crossings eventually complete (tape or tokens.complete_slot), but only 9.3% of priced entries are still holding when the curve completes. Those migration exits are positive (mean raw +0.19946 SOL at 0.25 SOL) and all other exits average -0.03403 SOL; 71% of trades exit on the -20% stop.

## Kill lines
Line 1 (mean <= 0, both models): hit. Line 3 (ex-top-3 <= 0): hit. Line 4 (ex-best-day <= 0; majority of days positive): hit. CI90 lower bound below 0. Attempts/day is far above 3, so that line did not bind. KILL.

## Not measured / caveats
- Sells are priced by constant product at the 2-slot-later post-trade state; no sell-side failure or slippage-cap model. The stop is checked at prints only.
- PumpSwap exit uses only the first 5 PumpSwap prints per mint; one mint with no PumpSwap print was booked as a total loss. The real-SOL sell cap never bound.
- Entries skipped because the curve completed within 2 slots (4,603) are not counted as trades; the 55% completion figure is therefore not a tradeable rate.
- Time-stop and block-end censoring at the last tape hour were not modelled.
- The only positive pocket (migration exits) cannot be selected in advance; selecting on it would be a new rule on read data.
