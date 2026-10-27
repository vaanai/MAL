# P80: learned rug / big-loser veto on the frozen H5 book. Verdict: NOT_DECIDABLE (no independent training sample exists; no H5 pnl, score threshold or veto was computed)

Lane P80, 2026-10-27. Paper only. No book has cleared the promotion gate. The dates are read by at least 14 families; any later result would have been in-sample.

## What was done
- PREREG.md (pushed before any feature, label or pricing) fixed 6 causal features, the -50% flat-model label, a numpy L2 logistic with no tuning, an 85th-percentile-of-discovery-scores veto and the kill lines. It also fixed the stop rule: if the independent training sample cannot be built with the H5 entry timing rule, stop NOT_DECIDABLE.
- Training pool = explore-0814 pools (days 2026-08-14..08-28, V in [17.5e9, 17.7e9]) whose mint is not in the frozen H5 set, with the H5 entry trigger applied by the identical P55 code (`p80_run.py pools()`, copied from p55_price.py): first SELL print with post-trade q <= 40 SOL within 300 s of the pool start.

## Counts (no pnl touched; `p80_train_counts_output.txt`)
| quantity | value |
|---|---:|
| explore-0814 pools scanned | 11,172 |
| in the frozen H5 set | 1,702 (all 1,702 meet the trigger window) |
| not in the H5 set | 9,470 |
| non-H5 pools that ever reach q+V <= 40 SOL | 5,168 |
| non-H5 pools with a trigger print (sell, q <= 40, within 300 s) | **0** |
| non-H5 pools failing the sps filter | 1 |

## Verdict
The frozen H5 trade set is exactly the set of pools that satisfy the H5 entry trigger on this tape, so there is no simulated H5-timing entry on any pool outside the H5 set: the independent training sample is empty (0 rows against the stop-rule minimum of 300). The brief says to stop NOT_DECIDABLE in that case. Training on the H5 trades, or on non-H5 pools with a looser entry rule, would be a different pre-registration (the latter changes what "big loser at H5 timing" means; not done here). The idea is not killed.

## What was not measured
Features were extracted for the H5 trades (`h5feat` mode, no pnl; outputs kept in scratch, not committed), but no model was fit, no score or threshold was computed on H5, and no H5 pnl, veto share, delta, placebo or kill-line number was computed. The fit and pricing code in `p80_run.py` (`fit`, `h5pnl`) was never run. Creator-sold share and true holder share are not on the tape (only wallet-hash proxies); that gap stands for any follow-up.

## Capacity
Not computed: no delta exists. Impact at 0.25 and 1 SOL untested.

## Seals
Aug/Sep exploration blocks via the audit-1008 paths (as P55/P77). No October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output or job, no #1419/#1421. 0 Helius credits.

## Files
`PREREG.md`, `p80_run.py`, `p80_train_counts.py`, `p80_train_counts_output.txt`, `REPORT.md`; library row `p80-h5-rug-veto` (INDEX.md rebuilt, library tests run).
