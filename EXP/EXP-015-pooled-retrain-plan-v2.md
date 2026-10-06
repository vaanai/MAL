# EXP-015 plan v2 (exploration stage): EXP-012's recipe, re-fit on a pooled ~34-day training set, judged under live-calibrated costs

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan, written before any EXP-015 code, fit or result exists.** It is not a pre-registration. A pre-registration (Part 1) and a freeze record (Part 2) follow only if the screen in §5 passes. It supersedes the rolling-window plan in draft [#278](https://github.com/vaanai/MAL/pull/278) (`EXP/EXP-015-rolling-retrain-plan.md`, W = 7, 17 test days), which this file replaces; closing #278 is the manager's call. |
| **Date** | 2026-10-06 |
| **Why** | EXP-012 was tuned on 9 exploration days. Those days have had 74 logged tries (`data/tries.jsonl`) and every new idea on them is flat (entry-veto, exit re-check, see [exp012-exit-veto-2026-10-05.md](../ARTIFACTS/lab/exp012-exit-veto-2026-10-05.md)). The live probe and the simulator agree that the strategy is about break-even at 0.05 SOL (see the arithmetic in §8, item 7, which is less comfortable than "break-even"). The one lever left that is not another tweak on the same 9 days is more training data and a judgement under realistic costs. mal-research-0 has idle capacity. |
| **Hypothesis** | The S2 selector re-fit on about 34 days selects `migrate` entries with a positive net **after** the measured live costs, and does so better than the frozen EXP-012 model on days the frozen model never trained on. |
| **Prior (manager-style honest estimate, mine)** | Low, about 15-20%. The 9-day number is best-of-6 and then best-of-many; the unfiltered base cell was already positive in the EXP-012 holdout; and the edge, if real, is thin against a 505k-lamport per side fee at 0.05 SOL. This is an estimate, not a measurement. |
| **Measured by this file** | Nothing. No row was read, no model was fit, no hour was opened. |

## 1. What changed from v1 (#278)

- v1 retrained on the 7 most recent days. That is less data per fit, not more. v2 pools everything the ledger will allow as exploration, which is the lever the owner asked for.
- v1 judged under the old cost model (0.5 SOL, k=1 entry). v2's deciding cells carry the live-calibrated costs (§4).
- v1 had one try. v2 allows at most three configurations (§7), because the label definition is a real design choice (§3).
- v1's confirmation was "fresh-0828 if EXP-013 does not claim it". EXP-013's plan also names fresh-0828 ([EXP-013 plan](EXP-013-graduation-classifier-plan.md), "Target confirmation block"). This plan names fresh-0828 for EXP-015 in the ledger edit, and **that conflict needs a manager decision before the ledger edit merges** (§6, §9).

## 2. Training pool (exploration only), exact hours

UTC hours, inclusive start, exclusive end. Four sources, 816 counted hours = **34 counted days**, if every precondition below is met.

| # | Pool | Counted hours | Hours | Days | Source | Status today |
| --- | --- | --- | --- | ---: | --- | --- |
| P1 | The original 9-day exploration pool | `[2026-09-19T00, 2026-09-28T00)` | 216 | 9 | fast tape (A), Oracle in-sample (C), Oracle live (B): EXP-012 §3.2 clean views, same hour whitelists, same `VIEW.sha256` pins | Exploration pool in the ledger already. Not restated here: the exact per-hour whitelists are EXP-012's and are used by reference, not re-derived. |
| P2 | `explore-0814` | migrations in `[2026-08-15T12, 2026-08-28T12)`; the first 24 h of `[2026-08-14T12, 2026-08-28T12)` are feature buffer only | 312 counted (336 sealed) | 13 | getBlock, `/data/mal/clean-view/explore-0814/w1..w7` | Exploration pool. Its migrate outcomes are read once by the EXP-012 backcheck ([EXP-012-backcheck-0814.md](EXP-012-backcheck-0814.md), job #207). EXP-015 job starts after #207 finishes (one heavy job at a time; no other dependence). |
| P3 | The spent fresh-0903 block | `[2026-09-03T12, 2026-09-09T12)` | 144 | 6 | getBlock, `/data/mal/blocks-clean/fresh-0903/w1..w3` (dedupe + sha256 manifest already exist from the EXP-012 read) | Spent by EXP-012's one read (job #32, 2026-10-01). Moves to exploration by the ledger edit in this PR. |
| P4 | The EXP-011 block | `[2026-09-09T12, 2026-09-15T12)` | 144 | 6 | fast box walkers B and C (`/var/lib/mal/backfill-fast-b`, `-c`) | **Not usable today.** Per the ledger it "can move to the exploration pool only by a later ledger edit, after it has been deduplicated and the missing hour re-fetched with the fixed walker". Preconditions below. |

Sum: 216 + 312 + 144 + 144 = **816 h = 34 days**. If P4's preconditions are not met, the pool is P1+P2+P3 = 672 h = 28 days and every number in this plan scales accordingly (the screen still runs on that smaller pool, and the report says so in its first line).

**Not in the pool, ever, for EXP-015:** the EXP-009 block `[2026-09-15T12, 2026-09-19T01)` (except its two exploration-pool exclusion hours, which are already inside P1), the fresh-0828 confirmation block, the second backup block `[2026-08-08T12, 2026-08-14T12)`, the forward-paper era from 2026-09-28T00 onward, and every hour of the forward walk (§8, item "Ordering").

**P4 preconditions (outcome-blind, in this order; none parses a trade outcome):**

1. Re-fetch hour 2026-09-11T03 with the fixed walker (the hour the EXP-011 scorer aborted on). One hour. By proportion of the fresh blocks' cost this is on the order of 1/144 of a block's roughly 550k credits (about 4k); that is arithmetic, not a measurement, and the real number goes in the PR that does it.
2. Exact-duplicate removal on both walkers (`backfill_verify --content --dedupe-out`); both walkers had resume-duplicate rows (EXP-011 Result).
3. `backfill_verify --content --min-slots-per-hour 8000` shows 144/144 sealed, 0 flagged, 0 duplicates. Note that `--content` detects duplicates, not missing rows; completeness rests on the walker fix, as in EXP-012 §10.6.
4. sha256 manifest and a clean view with `VIEW.sha256`, copied to mal-research-0 if training runs there. Print counts and hashes only.
5. A one-line ledger follow-up records steps 1-4 done. Only then does the owner cell change.

**What "34 days" does not mean.** The four pools are not one tape. P1 is fast-tape plus Oracle tape. P2, P3 and P4 (as re-walked) are getBlock or backfill sources, and P4 is fast-box backfill. EXP-012's own split showed the source matters: the fast-box slice (A) was the weakest in-pool source, +4.19% flat versus +11.95% for Oracle live. The confirmation block is getBlock-only, so the screen's decisive bars use the 25 non-P1 days (§5). Creator history (`creator_prior_mints_24h`) is built per block from that block's own create hours (EXP-012 §4.2), so each of the five blocks undercounts that feature for about its first 24 h, uniformly in training and in the confirmation read. P2 counts 13 days because the backcheck did; P3 and P4 count all 144 h as the EXP-012 read did. That is a known inconsistency in how the first day is treated. It is disclosed and not "fixed", because changing it is a tuning knob.

If the table has the same density as P1 (8,801 rows in 9 days) the pool is on the order of 33,000 rows. August days have fewer slots, so the real count is lower. This is a back-of-envelope, not a measurement.

## 3. Recipe and threshold

**Recipe.** [EXP-012 §3.1](EXP-012-migrate-entry-model-refreeze-prereg.md), re-fit: the 18 frozen features, LightGBM S2 `lgb_medium` (`num_leaves=15, min_data_in_leaf=20, learning_rate=0.05, rounds=100, feature_fraction=0.9, bagging_fraction=0.9, bagging_freq=1`, `scale_pos_weight=neg/pos`, `deterministic=True`, `num_threads=1`, `force_row_wise=True`), seed 1, `migrate` trigger, `tp50_sl30` exit. Table built with `buffer_hours=24`, `max_home_hours=12`. `tools.exp011_freeze._fit/_predict/_percentile` are imported unchanged. Frozen EXP-012 (model md5 `a1810d219ed61db64a396f40dc302ce5`, threshold 0.8030766588450794) is never touched.

**The three configurations (the entire try budget, §7).** They differ only in the label and in one regulariser, because those are the two places where "EXP-012's recipe" is not obviously right on a 4x larger pool at different costs:

| Config | Label | Hyperparameters | Why it is on the list |
| --- | --- | --- | --- |
| **C1** | EXP-012's label `1{pressure net > 0}` on `tp50_sl30`, as the EXP-012 table path computes it (old pricing) | EXP-012's | The literal re-fit. Isolates "more data" with nothing else changed. |
| **C2** | The same sign label, recomputed under the deciding costs of §4 (V pricing, k=6, 0.05 SOL, exit lag 2, haircut, 505k fee, pressure fail model) | EXP-012's | EXP-012's label was cut under costs the live probe has since shown to be wrong. Trains the selector on the target it is judged on. |
| **C3** | The label of whichever of C1 or C2 has the larger pooled nested-LODO pressure mean on the 25 non-P1 days (a mechanical rule, no choice) | `min_data_in_leaf = 75` (20 scaled by about 34/9, rounded) | Tests whether the 4x larger pool wants a larger leaf. The only hyperparameter touched. |

C3 depends on C1 and C2 having run; so all three fits and their `started` lines are logged before any is judged, and C3's label is chosen by the stated rule, not by eye.

**Threshold rule (pre-declared).** The entry fraction is fixed at the top 10%: threshold = the 90th percentile of the **pooled nested leave-one-day-out out-of-fold scores**, non-interpolating `index = round(0.90 * (n-1))`, as EXP-012 §3.1. Nested means: for each outer held-out UTC day d, the model and the threshold are built only from the other days: a fit per inner fold (each other day held out in turn) to get the inner OOF scores whose p90 is that outer day's threshold, then one fit on all other days to score d. The threshold for the final model is the p90 of the full pooled OOF scores over all training days. The fraction 0.90 is **not** tuned. Thresholds at p80 and p95 are reported, are never selected on, and do not count as tries (they are sensitivities of the same fit).

**Purge.** Rows whose migration time falls within 35 minutes (the 30-minute exit cap plus slack) before the start of a held-out day are dropped from that fold's training, because their exits overlap it; the same for the 35 minutes after its end. Days are UTC days of migration time (as EXP-012's nine). The edge days of each block are partial days.

**A pooled retrain cannot be scored on the days it trained on.** The final model, fit on every pool day, has no honest in-sample number; any per-day figure computed from it on those days is in-sample and is **never reported as performance**. The only evidence the pool can give is the nested out-of-fold scoring above (each day scored by a model that excluded it and its purge neighbours), and the one read in §6. Two further limits on the nested number: it trains on days both before and after each test day (no time order), so it cannot see drift, and the three configs plus all earlier tries on P1 make the best OOF number optimistic by selection, not just by noise. The honest read of the screen is "worth a confirmation read", never "has an edge".

## 4. Costs in every deciding cell

Every cell that can pass or fail a bar (nested screen §5, confirmation §6) uses all of these at once. Raw simulator rows are report-only.

- **Pricing:** PumpSwap virtual reserve V (about 17.58 SOL; `pumpswap-virtual-reserve` finding; a paper price that ignored V is not allowed). Pool fields come from a pool-field-only pre-pass.
- **Entry:** k = 6 slots **from the first PumpSwap print, not from migration**. k = 4 and k = 8 are reported (report-only).
- **Size:** 0.05 SOL. **Fee:** 505,000 lamports per side (500,000 priority + 5,000 base, DEC-019 Am.1). A MISS pays the fee.
- **Exit:** `tp50_sl30`, 30-minute cap, **exit lag 2** slots (`exit_land_k`). The exit re-check kept the frozen exit; the exit is not a tuning axis here. Lag 2 is still optimistic against the measured live exit leak.
- **Measured live haircut**, applied to each filled trade as in the backcheck: P = max(net0 + size, 0); entry gap g = 26.08 bps (mean sim-vs-live entry gap, job #175, n = 20 trades, median +1.03 bps); sell shortfall s = 16 bps of proceeds (the worst of the −11..−16 bps measured); net0' = net0 − P·(1 − (1 − g)(1 − s)) = net0 − P·0.0042038. Misses and censored rows are unchanged. MEV is not added; AMM concavity is ignored.
- **Fail models:** flat 15% and the pressure model at slope scale 1, both gating.
- **Gate computation:** `tools.paper_attention_promote.book_stats`, 1,000 bootstrap draws, seed 1, 5th percentile, cluster bootstrap by day as the gate does.

Not in the costs: entry noise (±300 bps), MEV, and the exit leak beyond lag 2. All three push the true number down. Preconditions: V maps for P3, P4 and fresh-0828 (the backcheck built `/data/mal/pumpswap-virtual/pool_v_0814.json` for P2 only; P1's was built for job #180). Each is a pool-field read with sha256 recorded; a missing V on more than 1% of migrating mints, or any primary-cell trade on a no-V pool, refuses the run, as in the backcheck.

## 5. Screen bars (exploration, stated before any computation)

Computed on the nested out-of-fold scores of each of C1, C2, C3, with §4's costs, under **both** fail models. A configuration passes only if **all** of the following hold. A day with no trade counts as not positive.

1. **Gate shape on all held-out days (P1+P2+P3+P4):** n entered ≥ 100; ≥ 5 UTC days with a majority positive; CI90 lower bound of the mean > 0; total ex-top-3 > 0.
2. **The same four bars on the 25 non-P1 days alone (P2+P3+P4).** These are the days the frozen model and the earlier 74 tries never fit. P1's days were hammered, so they cannot carry the pass.
3. **Beats frozen EXP-012, paired on the same mints, on those 25 days.** For each migration m the pairing statistic is x_m = (s_new,m − s_frozen,m) · net_m, where s is the 0/1 selection (new: nested-OOF score ≥ its fold threshold; frozen: score ≥ 0.8030766588450794) and net_m is the haircut per-trade net under the leg. The mean of x_m over all migrations must be > 0 and its CI90 lower bound (day-cluster bootstrap, 1,000 draws, seed 1) must be > 0, under both fail models. The frozen model never trained on these days, so the comparison is fair; the new model's nested fold trained on the other 33 days, which include P1. It is stricter than the gate on purpose: if a 4x larger pool cannot beat 9 days' fit by a margin the CI can see, the pool bought nothing.
4. **Concentration:** no one UTC day contributes more than 40% of the positive-day total SOL of the screen's mean leg. (Added because EXP-012's holdout had 50.7% of flat SOL from its two oldest days.)

Report-only, never gating and never selected on: k = 4 and k = 8 versions, the raw (unhaircut) simulator result, the lag-0 result, p80 and p95 thresholds, per-source and per-block tables, first-half vs second-half signs, selected fraction. If the k=6 cell passes and both k=4 and k=8 are negative, the PR that reports it says "knife-edge" in its first line.

**If more than one configuration passes,** the one with the largest pooled pressure mean on the 25 non-P1 days goes to confirmation; there is no discretion. **If none passes, the family is closed:** no fourth configuration, no recipe tweak, no new window or fraction, fresh-0828 goes back to "reserved" by a ledger edit unread, and the PR says EXP-015 failed its screen.

## 6. Confirmation: one read of fresh-0828, under the promotion gate

**Block:** `[2026-08-28T12, 2026-09-03T12)`, 6 days, 144 h, ledger row "Backup confirmation block", mal-research-0, sealed and verified 2026-10-02, unread (dedupe copies in `/data/mal/blocks-clean/fresh-0828`, clean view `/data/mal/clean-view/fresh-0828/w{1,2,3}` with `VIEW.sha256`). Its ledger owner becomes EXP-015 by this PR's edit, and the row says no read of any kind before Part 1 and Part 2 merge.

**Pre-registration (Part 1) and freeze record (Part 2), written after the screen passes and before the read.** Part 2 records: the winning configuration, the final model fit on every pool day (model md5, feature list md5, table sha256, the clean view pins of P1-P4), the final threshold, `FROZEN.md5`, and the freeze code commit. Part 2 is a separate PR. The scorer refuses unless all of it matches (the EXP-012 refusal list, §3.3 and §4.1, is the template: code commit match, tracked and unmodified code, O_EXCL read-once lock at `/data/mal/exp015/HOLDOUT_READ.lock` taken **before** the first row is read, clean view re-hash before the lock, all 144 hours sealed, V map sha256 asserted, V coverage over 99% pre-lock). A refusal before the lock does not spend the block; one outcome-blind re-walk of a bad hour is allowed (EXP-012 §4.1). Any failure after the lock spends the block.

**The exact reading.**

- **Cell: exactly one gating cell.** The frozen model of the winning configuration, the frozen threshold, entry iff `score ≥ threshold`, k = 6 from the first PumpSwap print, 0.05 SOL, `tp50_sl30`, exit lag 2, V pricing, fee 505,000 per side, the §4 haircut. k = 1 under DEC-014; no Holm among cells because there is one.
- **PASS** iff, under the flat 15% model **and** under the pressure model at scale 1: (a) n entered ≥ 100, where an entered trade includes a MISS (a fee-paying attempt), as in EXP-012's n = 451; (b) trades on ≥ 5 distinct UTC days (by migration time, days with trades only; this 6-day block spans seven UTC dates, the two edge dates being partial) with a majority of those days positive; (c) the mean SOL per trade has CI90 lower bound > 0 (1,000 draws, seed 1, 5th percentile, `book_stats`); (d) total SOL is still > 0 after removing the top 3 trades.
- **FAIL** if any of (a)-(d) fails under either model. n < 100 fails (a) and that is the verdict. The model, threshold and config are retired; no second read, no re-tune, no new threshold, no second cell. The block is spent and moves to exploration by ledger edit.
- **NOT_DECIDABLE** for a refusal or integrity failure after the lock; same consequence as FAIL for the block.
- **Reported with the verdict, not gating:** the paired comparison to frozen EXP-012 on the same mints (6 days is too little for a thin margin), the raw simulator result, k = 4 and 8, per-day table, per-walker table, and the p-value at 10,000 draws seed 1 together with the Holm thresholds (§7).
- **What a PASS says:** the selector cleared the gate once, on a getBlock-only block that sits **between** two training blocks (explore-0814 ends exactly at its start; fresh-0903 begins exactly at its end), at modelled costs whose exit assumption is optimistic. That is interpolation in time, not forecasting. It does not show persistence, which only a forward book can.

## 7. Multiplicity, tries and the winner's curse (read before trusting a PASS)

- **Tries cap: at most 3 configurations (C1-C3), each logged.** A `started` line goes to `data/tries.jsonl` (key `exp015_c1..c3`) before any fit; each config's result line follows. The run refuses if any prior `exp015_*` line exists. Each config counts as a try on **every** pool it touches: the 9-day pool goes from 74 to 77, `explore-0814` from 1 (DEC-017 (a)) + 6 (backcheck) = 7 to 10, and P3 and P4 start at 3 each (P3 additionally carries EXP-012's one confirmation read). Report-only sensitivities are not tries only if nothing is ever selected on them.
- **Winner's curse on the screen.** The recipe was tuned on P1 (best of 6 cells for EXP-012, then 68-74 tries of further ideas, all flat). The three configs are chosen on the same pool days they will be OOF-scored on. The screen is therefore a filter for "worth one confirmation read", and its numbers are upward-biased. A pass is not evidence for an edge.
- **Multiplicity across EXP-012, EXP-013 and EXP-015.** Each of the three families is read on its own block with k = 1, so DEC-014's Holm step-down (k books or cells read together at one review) does not formally pool them. They are still one lab hunting in one data set. With three independent reads each tested at the gate's nominal 5% one-sided false-positive rate, the chance that at least one passes with no real edge anywhere is 1 − 0.95³ ≈ 14.3%. EXP-012's holdout PASS is one of those draws, and a pooled retrain trained on that holdout cannot be called independent confirmation of EXP-012. If any two of EXP-012's forward book, EXP-013 and EXP-015 are put forward in the same promote decision, DEC-014 (b) applies with k = the number put forward (10,000 draws, seed 1, both fail models). For EXP-015's own read the confirmation prints the 10,000-draw p-value and the Holm threshold for k = 3 (0.05/3 ≈ 0.0167), report-only. Whether the single-cell gate itself should be tightened to α/3 is a call for `quant-proof` and the manager, not made here.
- **The paired bar's reference is already seen.** The frozen model's per-day results on `explore-0814` come from the backcheck (job #207) and on fresh-0903 from the EXP-012 read. They will be known when the screen runs. The plan does not change in response, and this is disclosed.
- **Seeds:** seed 1 everywhere. No seed search.

## 8. Disclosures, and ordering against EXP-012's 10-16 read

1. **P3 was a confirmation block.** EXP-012's PASS on it stands as one read; training on it afterwards spends it for good. No claim in EXP-015 may cite EXP-012's PASS as independent of the new model's training set.
2. **P4 was walked for a confirmation test that never produced an outcome** (EXP-011 NOT_DECIDABLE, no metric computed). It is the closest thing to unseen data in the pool, and EXP-015 is about to use it as training data. That is the ledger's own allowance, but it is a loss of a clean block.
3. **The 9-day pool is the most over-used data here.** The paired and concentration bars therefore centre on the 25 non-P1 days.
4. **Temporal structure.** LODO trains on the future of each test day. Fresh-0828 is sandwiched between training blocks. Neither tests drift; v1's rolling window did, and it is dropped, not forgotten. The forward book is where drift is tested.
5. **Costs are calibrated, not conservative.** The 26.08 bps entry gap is a mean on 20 trades (median +1.03 bps). The 16 bps sell shortfall is the worst of three builds. The live exit leak (stops firing at −31% to −74% with fills of −326 to −780 bps in earlier builds; a fix was in progress when this was written and is not verified here) is larger than lag 2 captures.
6. **Source mix.** P1 is tape, P2-P4 are getBlock or backfill. A model that learns source-specific feature distributions (counts, flows) is an obvious failure mode; the per-source table is mandatory in every report.
7. **The probe is not break-even in the numbers I read.** [probe-calibration-2026-10-05.md](../ARTIFACTS/lab/probe-calibration-2026-10-05.md), job #189: 5 trades outside the simulator's 15% cap made +10,642,436 lamports and the other 23 made −164,004,662. Those sum to **−153,362,226 lamports (about −0.153 SOL) over 28 closed trades**, about −0.0055 SOL per trade at 0.05 SOL, before accounting for builds with since-fixed exit bugs. The probe is an execution check, not an edge test, and 28 trades is small. But a plan that treats "about break-even" as the starting point should not start from this arithmetic without saying so.
8. **Ordering vs EXP-012's 10-16 forward read.** EXP-015 never opens any hour in `[2026-10-02T10, 2026-10-16T01)` (the whole Forward walk row, a superset of the `[2026-10-06T00, 2026-10-16T00)` migration window), never reads `/data/mal/blocks/forward-1002`, and never reads the forward-paper runner's files. The frozen EXP-012 model, threshold and pre-registered reading are not changed by any EXP-015 result; EXP-015 neither waits for the 10-16 read nor can alter it. Compute: EXP-015 jobs run under `nice` and never preempt the forward follower; one heavy job at a time (OOM rule), as a MiScusi job.
9. **Compute.** Nested LODO is about (D + 1) fits per outer day over D ≈ 33 other days: roughly 34 × 34 ≈ 1,150 fits per configuration, 3,500 for three. Each is a ~33k-row, 100-round, single-thread LightGBM fit. Order of hours of wall time on idle research-0 cores; unmeasured. No Helius credits other than the one hour in §2 P4 (step 1).

## 9. What a PASS earns, and what must be decided first

- **A PASS earns a forward paper book (a challenger), never live directly.** The book runs on the new reserved ledger row "Forward walk 2" (`[2026-10-16T01, 2026-11-16T01)`, getBlock follower, reserved owner) alongside the incumbent EXP-012, paired on the same mints, under both fail models and the §4 costs, with its own pre-registered review. It does not enter DEC-019/DEC-020 live probe sizing, and nothing in EXP-015 authorises any live size.
- **A FAIL closes the family** and releases fresh-0828 to the next candidate by ledger edit.
- **Before this plan can run (manager):** (1) resolve EXP-013's naming of fresh-0828 (the ledger keeps `[2026-08-08T12, 2026-08-14T12)` for "the test after EXP-013"; EXP-013's plan text needs a one-line amendment either way); (2) decide whether the catalog change is acceptable: `tools/mal_catalog.py` reads this ledger, so after the edit exploration-role reads of fresh-0903 are allowed, EXP-012 confirmation reads of it are denied (right, it is spent), and the EXP-015 id is an allowed owner of fresh-0828 before its pre-registration exists, so the scorer's own pre-registration guards are the real fence; (3) run P4's preconditions; (4) wait for #207.

## Sources

[docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md); [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md); [EXP-012 pre-registration](EXP-012-migrate-entry-model-refreeze-prereg.md) §3-§4, §6, §10, §12; [EXP-012 backcheck](EXP-012-backcheck-0814.md); [exit/veto note](../ARTIFACTS/lab/exp012-exit-veto-2026-10-05.md); [probe calibration](../ARTIFACTS/lab/probe-calibration-2026-10-05.md); draft [#278](https://github.com/vaanai/MAL/pull/278); [EXP-013 plan](EXP-013-graduation-classifier-plan.md); `data/tries.jsonl` (74 lines on 2026-10-06).
