# P89: P86 kNN score as a veto on the frozen H5 binding trades. Verdict: NOT_DECIDABLE (domain mismatch; veto not applicable)

Lane P89, 2026-10-27. Exploration data only. In-sample lab dates read by at least 14 families. No book has cleared the promotion gate; nothing here says a book makes money. `PREREG.md` was committed (and pushed) before the count step ran; no H5 outcome column was read by this lane (`p89_count.py` loads only day, mint, trig_t, sps, s0, set).

## Result (counts only, `p89_count_output.txt`)

- Trades: 2,826 (discovery 1,702, confirmation 1,124).
- **Trades with all ten P86 features computable under P86's own rule: 0 of 2,826.**
- Why: `ret_5m`, `ret_15m`, `ret_60m` need a print at or before t-5, t-15, t-60 min. At the H5 entry instant (trigger print + 1.9 s) the pool is 1.9 to 303.9 s old (median 102.9 s). A print at or before t-5 min exists for 17 trades, t-15 min for 0, t-60 min for 0. P86 drops any candidate with an undefined feature, so all are dropped.
- Even if the windows were truncated, the point is out of domain: P86's memory holds pools aged 2 h to 7 d (log_age 8.88 to 13.31, 4,600 candidates). The H5 trades are 0 to 7,200 s old: 0 of 2,826 reach 7,200 s. The standardised distance would be dominated by an age (and 60-min volume, flow, top-3 share) far outside the memory, so kNN neighbours would be arbitrary.

## Verdict and consequence

The PREREG inapplicability clause applies (computable share 0%, below 50% and below 100 per set). No outcome was joined and none of the veto reads (vetoed share, delta, ex-top-3, ex-best-day, big-loser capture, placebo) were computed, so there is no kill or lead on the veto itself. NOT_DECIDABLE.

Reading for the rug filter: P86's signal ("avoid structurally bad pools") is a property of mature pools (2 h to 7 d old, 30 SOL+ real quote, 20+ prints in 15 min). H5 trades enter in the first 5 minutes of a migrated pool, before any such structure exists. A veto for H5 needs features built for that timing (first-minutes prints, wallet and BOOST structure), with a training sample at H5's own timing; P80 already failed for lack of an independent one. A relaxed-feature kNN is a new pre-registration and a new memory, not a variant of this lane.

## Not measured

Any H5 outcome relation to the kNN score; a truncated-window variant; any other feature set.

## Files

`PREREG.md`, `p89_count.py`, `p89_count_output.txt`.
