# P77: b-flow flow-surge veto on the frozen H5 binding book. Verdict: NOT_DECIDABLE (veto fires on 90.6% of trades; no P&L read)

Lane P77, 2026-10-27. Paper only. No book has cleared the promotion gate. The 2,826 frozen H5 trades sit on dates already read by at least 14 families; any later result would have been in-sample.

## What was done
- PREREG.md (commit 90a5d97) fixed one threshold set: hunt23 b-flow cells B1 (whale >= max(1 SOL, 3% of depth) plus two distinct follow-on buyers within 4 s), D1 (4+ shrinking sells, drop <= -8%, exhaustion buy) and E1 (10 min net inflow >= 10% of start depth, >= 12 buyers, no 25% whale). Veto = any state fired at a print strictly before the trade's trigger slot and within 600 s of it.
- Step 1 was counts only (`p77_run.py flags`, `p77_counts_output.txt`). PREREG set a stop rule before any pricing: discovery vetoed count < 30 or share > 60% means INSUFFICIENT.

## Counts (no P&L touched)
| set | trades | B | D | E | any (veto) |
|---|---:|---:|---:|---:|---:|
| discovery | 1,702 | 1,433 (84.20%) | 442 (25.97%) | 965 (56.70%) | 1,558 (91.54%) |
| confirmation | 1,124 | 824 (73.31%) | 167 (14.86%) | 664 (59.07%) | 968 (86.12%) |
| both | 2,826 | 2,257 | 609 | 1,629 | 2,526 (89.38%) |

## Verdict
The stop rule fired (discovery share 91.54% > 60%). The veto is the book: removing 89% of trades is a different, tiny book, not a filter, and a random veto at that share would be almost indistinguishable from it. No pricing, delta, placebo or kill-line number was computed. Verdict NOT_DECIDABLE (counts), not a KILL of the idea.

Why it fires so often (inference): H5 enters pools within 300 s of migration, which are the busiest pools on the tape (median 650 to 740 prior prints). The b-flow states were built on pools of >= 50 SOL depth and age >= 360 s; these H5 pools have q+V <= 40 SOL, so the same relative thresholds (3% of depth, 10% of depth) are small in SOL and are met almost always. The windows are also truncated at pool start.

## What was not measured
- Baseline re-pricing and the P55 reproduction check were not run in this lane (not needed once the stop rule fired). P55's reproduction (max difference 0.0 lamports) stands.
- Any veto with a much stricter threshold set (for example a whale threshold in absolute SOL or a 10-20% vetoed share) is a new pre-registration, set from counts only. It was not tried here because the brief fixes ONE threshold set and tuning thresholds against the discovery counts to reach a share is a retune.

## Seals
Aug/Sep exploration blocks via the audit-1008 paths (as P55). No October hour, arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output or job, no #1419/#1421. 0 Helius credits.

## Files
`PREREG.md`, `p77_run.py`, `p77_counts_output.txt`, `REPORT.md`.
