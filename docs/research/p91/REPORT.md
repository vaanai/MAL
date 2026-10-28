# P91 REPORT: kNN on the low-fee, active universe (B5-B7)

Verdict: **KILL** under the frozen PREREG kill line (all four cells). PREREG unchanged (prior was KILL). Paper only, exploration days only. No book has cleared the promotion gate; this report makes no money claim.

## Build bug (disclosed)

Candidate build job #1767 wrote a `counts.txt` that contradicted itself (617 candidate hours but "share with >=3 candidates 0.0000" and 0 in both windows). Cause: in `p91_build.py` the per-hour survivor counts `npass` are indexed by decision time `td` (seconds), but the counts block reindexed them by hour bucket `hb`, so every lookup missed and filled 0. Fix (commit 2f421efa): `npass.reindex(tdall)` instead of `npass.reindex(allh)`. Structural only; no threshold, screen or candidate logic changed. The bug touched only the counts text, not `cand.parquet`. Proof: rebuilt with the fixed script (5 min, 16 GB cap, nice 15); the new `cand.parquet` equals the old one after sorting by (td, pool) (row order only differs); same 4,936 candidates, 617 hours. Original saved as `/data2/mal/p91/counts_buggy.txt`. Corrected counts (`p91_counts.txt`): decision hours with tape 626, share with >=3 candidates 0.9856, mean capped-8 candidates 7.887; TEST window 289 hours, 287 with >=3 candidates (0.9931); MEMORY 337 hours, 330 with >=3. The count floor (100 test hours) is met. Band mix of candidates B7 4,050 / B5 498 / B6 388.

Run note: the MiScusi job tool was not available in this session, so the rebuild, label and analysis ran directly on mal-research-0 under `systemd-run --user --scope -p MemoryMax=16G` and `nice -n 15` (not as MiScusi jobs). Build was also done earlier as job #1767.

## Seals

P86 tape `/data2/mal/p86/ps` = explore-0814 (2026-08-14T12 to 08-28T11), fresh-0903 (09-03T12 to 09-09T11), exp011-0909 (09-09T12 to 09-15T11), by file names; 624 files, no October file. Everything is inside the exploration pool per HOLDOUT_LEDGER; none is in EXP-024 Look-2 pools, the DBC forward window, LaunchLab holdout, fresh-0802, fresh-0828 (starts 08-28T12; the last explore hour is 08-28T11), or the D2/D3 sealed hours [10-02T10, 10-16T01). This is in-sample probing (14+ families read these dates); a positive would have been only a lead.

## Labels

4,936 candidates; 4,654 entered (282 dropped, no entry print); priced 4,440, dead 214 (158 no trigger, 56 no exit print; every dead pool has a later print, so D1 always defined). Mean p_fail 0.2890. Test: 2,190 candidates, 287 hours, 13 days (09-03 to 09-15), 92 dead. Raw outputs: `p91_label_log.txt`, `p91_analyze_output.txt`, `p91_analyze_output_strict.txt`.

## Results (test, 0.25 SOL, pp of stake; dead pools included)

| cell | book mean | U | R | S (vol top) | book - U | book - R |
| --- | --- | --- | --- | --- | --- | --- |
| flat / D1 | -4.318 | -2.892 | -4.206 | -1.029 | -1.425 | -0.111 |
| press / D1 | -3.704 | -2.442 | -3.576 | -0.881 | -1.263 | -0.128 |
| flat / D2 | -4.631 | -3.162 | -4.515 | -1.321 | -1.469 | -0.116 |
| press / D2 | -3.978 | -2.670 | -3.835 | -1.117 | -1.309 | -0.143 |

- Kill line: all four cells have mean <= 0 and book - U < +1 pp (indeed negative). KILL on both clauses in every cell.
- n 287 trades, 22.08 per day; days positive 4/13 (flat) and 3/13 (pressure); CI90 lower bound of mean SOL per trade -0.0178 (flat/D1) to -0.0186 (flat/D2), all negative. Ex-top-3 and ex-best-day totals are negative in all four cells.
- Rank IC per hour (kNN score vs label): priced only 0.1743 (t 6.81, day-block t 4.28); dead included 0.1648 (D1) and 0.1634 (D2). Lower than P86's 0.278 (different, narrower universe). Strict exit-time memory rule (report only): IC 0.1677 / 0.1605 / 0.1590, book -4.866 / -4.100 / -5.179 / -4.374 pp, book - U between -1.66 and -2.02 pp. Same KILL.
- So the ranker has rank information inside the hour, yet its top pick is no better than a random pick (-0.11 to -0.14 pp vs R) and worse than the mean of all candidates. The top-scored pools are the recently pumped, high-momentum ones (the strongest single features are flow60, close_vs_hi, and ret_60m, all positive IC), and the top-of-8 pick loses more than the average candidate. I did not test why; this is an observation, not a mechanism claim. Dead picks: 25 of 287 book picks (R 15, S 2); all-candidate dead share 4.2%.
- Fee band is not the problem: 273 of 287 book picks are B7 (0.648% floor) and 267 of 287 top scores exceed the band floor, yet the realised mean is -4 pp. Variant (b) was not run as a book.
- S (top by 60 min volume) is the least bad (-1.0 to -1.3 pp) but still negative; it is not the pre-registered book.
- Capacity at 0.25 SOL, lower of four cells: none (mean <= 0). Pricing the same picks at 1.0 SOL gives -3.68 to -4.60 pp, so no capacity either.

## Not measured

Any out-of-sample or October hour; live fills; variant (b) as a book; the picked-hour tie sensitivity; whether a different universe or a non-forced pick helps. Memory excludes dead pools (stated PREREG limitation). Because the result is a KILL on exploration days, the winner's-curse and reuse caveats only make a positive less likely, not more; they do not rescue this lane.
