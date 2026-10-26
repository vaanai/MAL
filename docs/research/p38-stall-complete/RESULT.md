# P38 `p37-stall-to-complete`: counts result — **KILL** (K0 and K0c)

Lane P38, manager17. Written 2026-10-26 (`date -u` 2026-10-26T17:24:48Z). Rule, labels and kill rules: [DECLARE.md](DECLARE.md), committed and pushed (`0297f266`) before any count.

**Counts only.** No price, no return, no PumpSwap row, no V, no BOOST outcome. The only outcome is the D1 label "reached `complete`" on Aug/Sep exploration hours. Exploration data, never gate evidence. Nothing here says the idea makes money. No book has cleared the promotion gate.

## Run

- Tool `tools/p38_stall_counts.py` at `d9ce7db90bbdaceac2b06310e9b2d7c0a187c81c` (13 unit tests in `tools/test_p38_stall_counts.py`, green). Run inline under `systemd-run --user --scope -p MemoryMax=8G` (54 s wall; under the 5-min / 2 GB line, so no MiScusi job). One smoke run on the first 12 hours of each block before it.
- Inputs: `/data/mal/audit-1008/tape/{trades,migrations}` hours of explore-0814 `[08-14T12, 08-28T12)`, fresh-0903 `[09-03T12, 09-09T12)`, exp011-0909 `[09-09T12, 09-15T12)`, each hour's manifest source checked against the block's clean-view root. `pump_bonding` and `complete` rows only.
- Outputs: [data/counts.json](data/counts.json) (sha256 `74b20f6451f7c62cd470794d8b0f5753576aca6513aedc3680a5f9d38052ea19`), [data/triggers.jsonl](data/triggers.jsonl) (57 rows, sha256 `eae769c2800d7a82ddef3392f212133f027e55949f24f25c85614c6b440159e7`).

## Coverage (DECLARE §3)

| Block | Hours | Bonding rows | `complete` rows | Completes eligible for the 30-min check | 30-min chain continuous | Completing print on tape | LENIENT files in block |
| --- | --- | --- | --- | --- | --- | --- | --- |
| explore-0814 | 336/336 | 54,562,967 | 16,331 | 1,395 | 1,180 | 1,395 | 0 |
| fresh-0903 | 144/144 | 16,774,677 | 6,423 | 217 | 187 | 217 | 0 |
| exp011-0909 | 144/144 | 17,257,236 | 6,588 (1 with no bonding print) | 183 | 140 | 183 | 3 (trades 09-12T07 c, 09-15T03 b; creates 09-12T07 c) |

- The tape is a full getBlock walk, so print-level bonding rows exist for the 30 min before every completion and for stalled curves that never complete. Every eligible complete has its completing print. The strict continuity test (pre-state of each print = post-state of the one before, exact in lamports and raw tokens) holds over the 30 min before complete for 1,180/1,395, 187/217 and 140/183. I did not resolve the breaks (missing prints or within-slot ordering ties). They do not touch this lane's triggers.
- **Stall-window coverage: 57 of 57 triggers continuous** (share 1.0), so R-DATA (under 50%) does not apply. Follow window of triggers that did not complete in 6 h: 27 of 28 continuous.
- 2 triggers (explore-0814) later show a print at vSOL ≥ 115.0 with no `complete` row in the block. They are labelled not completed, as declared.

## Counts

| | explore-0814 | fresh-0903 | exp011-0909 | **Pooled** |
| --- | --- | --- | --- | --- |
| Triggers | 34 | 10 | 13 | **57** |
| Censored (no complete, t + 6 h past block end) | 1 | 0 | 1 | 2 |
| Uncensored | 33 | 10 | 12 | 55 |
| Completed ≤ 6 h | 17 | 3 | 7 | 27 |
| **P(complete ≤ 6 h \| trigger)** | 0.5151515151515151 | 0.3 | 0.5833333333333334 | **0.4909090909090909** |
| Completed by block end (info) | 27 | 5 | 7 | 39 |
| Median triggers / full UTC day | 3 (13 days) | 1 (5 days) | 1 (5 days) | **2.0** (24 days) |
| Min / max per full day | 0 / 5 | 1 / 2 | 0 / 5 | 0 / 5 |
| Time to completion, s (Q1, median, Q3) | 1659.0, 3768, 7028.0 | 5568.5, 10948, 11173.0 | 3642.0, 6566, 8964.5 | 2519.5, 3833, 8108.0 |
| Share of completions ≤ 1 h | 0.47058823529411764 | 0.3333333333333333 | 0.2857142857142857 | 0.4074074074074074 |
| **Race share ≤ 4.4 s** | 0/17 | 0/3 | 0/7 | **0/27 = 0.0** |
| Race share ≤ 1.9 s (IDEAS def.) | 0/17 | 0/3 | 0/7 | 0/27 = 0.0 |
| p at decision (Q1, median, Q3) | 0.9077445000000001, 0.915049, 0.9318489999999999 | 0.911364, 0.91543, 0.92039125 | 0.924282, 0.95662, 0.990971 | 0.9107, 0.917287, 0.955426 |

Per full UTC day (triggers): 08-15 3, 08-16 0, 08-17 4, 08-18 3, 08-19 3, 08-20 2, 08-21 3, 08-22 2, 08-23 1, 08-24 4, 08-25 1, 08-26 1, 08-27 5, 09-04 2, 09-05 1, 09-06 1, 09-07 2, 09-08 1, 09-09 2, 09-10 2, 09-11 5, 09-12 1, 09-13 0, 09-14 1. Half days: 08-14 1, 08-28 1, 09-03 2, 09-15 3. Per-day P(complete ≤ 6 h) ranges from 0.0 to 1.0 on 1-5 triggers a day (all values in `counts.json` → `pooled.per_day`).

## Kill rules (DECLARE §5)

| Rule | Threshold | Value | |
| --- | --- | --- | --- |
| K0 | median triggers/day < 8 | 2.0 (24 full UTC days) | **FAIL** |
| K0b | race share > 10% | 0.0 at 4.4 s and at 1.9 s | pass |
| K0c | P(complete ≤ 6 h \| trigger) < 0.5 | 0.4909090909090909 (27/55) | **FAIL** |
| (info) break-even ≈ 0.65 (IDEAS §3.2) | | 0.4909090909090909 | below |

R-THIN does not apply (55 uncensored ≥ 30). **Verdict: KILL.** K0 fails by a wide margin. K0c fails narrowly (27/55; one more completion would read 28/55 = 0.509), but it is also far below the ~0.65 break-even, so the margin does not matter for the idea. The stall does remove the race (0 of 27), as the idea expected. What it leaves is too rare, and about a coin flip to complete.

## Diagnostic (disclosed; not a variant)

To check that the trigger count is not a code artefact, I re-ran the trigger finder once with the 2 pp band removed (counts of triggers only; no completion label was read for that set): 92 triggers in total (57 / 13 / 22 by block). So even "at p ≥ 0.90 for 20 min, any movement" is under 4 a day. Near-complete clean curves almost always either complete or fall back below 0.90 within 20 min. One trigger was checked by hand against its prints (mint `2HtutbHp…`, anchor print p 0.9163, window range 0.01476 < 0.02, decision = anchor + 1200 s). Per R-NOTUNE, no variant is proposed from these counts.

## Not measured

No price, return, fee, impact, V, PumpSwap print or BOOST outcome. No October hour, no arena tape, no forward-1016, no meta-capture. The continuity breaks on the 30-min pre-complete windows (215 of 1,395, 30 of 217 and 43 of 183 eligible completes) were not explained. pump.fun's token-based progress was not used (the rule is SOL-based, as IDEAS §3.2 wrote it).
