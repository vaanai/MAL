# EXP-013 (plan, exploration stage): graduation-completion classifier

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan.** It fixes the screen and the tries cap before any code or result exists. It is not a pre-registration. A confirmation pre-registration follows only if this screen passes. |
| **Date** | 2026-10-02 |
| **Owner direction** | Push toward profit with a genuinely different signal (after DEC-017's three variants failed, [dec017-candidates note](../ARTIFACTS/lab/dec017-candidates-2026-10-02.md)). |
| **Target confirmation block** | The reserved, unread backup block `[2026-08-28T12, 2026-09-03T12)` (docs/HOLDOUT_LEDGER.md), read once, k = 1, if and only if this screen passes. |
| **Prior odds (manager's honest estimate)** | About 20% or less of clearing the gate. Every non-migrate family tried before lost after the round-trip fee, and the getBlock-only slice has been the weakest source. |

## Hypothesis

At 80% bonding-curve progress, the price path to completion is fixed by the curve (vSOL about 73 → 115, about +145% to graduation). The bet is P(complete within the cap). It is independent of EXP-012 in three ways:
- about half the triggers never graduate, and EXP-012 never sees those mints;
- the holding window ends where EXP-012's entry starts;
- the target is different.

## Fixed design (exploration)

- **Trigger:** the first `pump_bonding` print with curve progress ≥ 0.80.
- **Features:** EXP-012's 18 features, cut at the trigger print, plus four new ones:
  - progress velocity over 60 s;
  - SOL in over 30 s;
  - distinct buyers over 60 s;
  - seconds since create.
- **Model:** S2 `lgb_medium` (EXP-012's hyperparameters, seed 1); threshold at the 90th percentile of pooled OOF.
- **Exit:** buy on the curve; hold through migration; sell at the PumpSwap state at migration + 4 slots. Stop at −30% on the curve; 30-minute cap.
- **Execution:** 0.5 SOL, direct route, both fail models. **Entry at slot + 4 is primary**; slot + 1 is reported as reference only.
- **Data:** the 9-day exploration pool plus verified expansion views (`explore-0814/wN` with `VIEW.sha256`). Never the backup block, the EXP-012 holdout, the EXP-011 block or the forward walk.
- **Tries cap:** at most **3** tries (configurations), each logged in `data/tries.jsonl`.

## Screen (stated before any computation)

Nested leave-one-day-out over all clean days. The screen passes only if **all** of these hold under **both** fail models:
1. pooled mean SOL/trade > 0, with 90% CI lower bound > 0 (1,000 draws, seed 1);
2. ex-top-3 total SOL > 0;
3. more than half of the days positive;
4. 1–3 hold again **on the getBlock-only days alone** (pool A plus the expansion days);
5. the pooled mean stays > 0 at entry slot + 8;
6. mint Jaccard with EXP-012's OOF-selected set ≤ 0.5. The daily-PnL correlation with EXP-012 is reported.

On a FAIL the family is closed and not re-tuned.

## Amendment 1 (2026-10-02, before any real-data run): design clarifications from PR1 (#244)

Fixed before any table is built on real data:

1. **Stop sell delay.** The stop's sell lands k slots after the stop print, the same k as the entry. This is conservative, because lag applies to exits too.
2. **Priority fee.** 500,000 lamports at every k (EXP-012's convention).
3. **Pressure curve.** Evaluated at the entry state and applied to both legs, including the PumpSwap sell, as in EXP-012. Disclosed: the post-migration sell may face more contention than this models.
4. **Missed entries.** An entry after the curve completed or migrated is a MISS that costs the priority fee. It is a row with label 0, kept in training, like EXP-012's MISS rows.
5. **Day and overlap.** A row's day is the trigger day. Screen item 6:
   - Jaccard is computed by mint;
   - the daily-PnL correlation pairs trigger days with EXP-012's migration days, and that mismatch is disclosed.
6. **The screen runs exactly once**, on the 9-day pool plus every `explore-0814/wN` view whose `VIEW.sha256` exists at **2026-10-04T12:00:00Z**. Table builds before then are for debugging only: no model, no LODO and no screen output is computed or read from them. The run's manifest pins the view shas.

## Amendment 2 (2026-10-02, before any real-data run): screen item 4 made explicit and stricter; number reuse; execution assumptions

From a review of #243. No EXP-013 code has run on real data, and no model, LODO or screen output exists.

1. **What "getBlock-only" means.** "Pool A" is the **fast-box getBlock backfill**: the `pump_history_backfill` walker output in `/var/lib/mal/backfill-fast`, served as clean view `fast-pool-2026-09-18T23_2026-09-22T00`. Its rows carry `source: "backfill"` and no `t_recv_ms`, the same feed as the expansion walks. "Fast" names the host it was walked on, **not** the fast live listener. Pools B and C are the Oracle live tape, the non-getBlock source.
2. **Screen item 4 is replaced by two separate items. Both must pass**, under both fail models, with bars 1–3 (mean and CI lower bound > 0, ex-top-3 > 0, more than half of days positive):
   - **4a. All getBlock-sourced days.** Pool A `[2026-09-19T01, 2026-09-22T00)`, plus every `explore-0814/wN` view verified by 2026-10-04T12:00Z (Amendment 1 §6).
   - **4b. The August expansion days alone.** Only the `explore-0814/wN` views from 4a. This is the out-of-period getBlock slice, the source the prior calls weakest, and it gets its own hard bar.

   This is stricter than the original wording, since 4b is an added requirement. No bar is relaxed.
3. **Pinned days.** The screen run's manifest lists each view's hour range and the sha256 of its `VIEW.sha256` file. It also lists the clean-view `VIEW.sha256` for pools A, B and C. The screen refuses a view that isn't listed there.
4. **Number reuse.** The earlier `exp013-candidate` / `tools/exp013_*` refit tooling (#239–#242) was DEC-017 candidate (a), the expanded-pool refit of EXP-012's recipe. It was never a registered EXP-013 and is closed (FAIL, [dec017-candidates note](../ARTIFACTS/lab/dec017-candidates-2026-10-02.md)). This plan is a new, independent family that reuses the number. Its code lives in `tools/exp013_grad_*`.
5. **Execution assumptions, stated in full:**
   - 0.5 SOL per entry, direct route (portal fee 0), priority 500,000 lamports per side at every k;
   - curve buy and PumpSwap sell priced by the existing curve math in `tools/latency_curve.py` and `tools/paper_curve_math.py`, including pool and protocol fees as implemented there;
   - fail models: flat 15% and the pressure curve at scale 1, both as expected values (`mixed_net`), as in EXP-012;
   - a MISS costs the priority fee.
6. **Holdout.** Nothing here changes ledger ownership. The backup block stays reserved and unread until a pre-registration merges after a clean screen.

## Amendment 3 (2026-10-02, before any real-data run): two execution details aligned with EXP-012 and made conservative

From the #244 review:
1. **Cap exit.** The 30-minute cap's sell lands **k slots after the cap instant**, the same k as the entry, like the stop and migration sells. Every exit lags.
2. **Missing entry state.** It is scored as a **MISS** (priority fee lost, label 0), as in EXP-012, not censored.

Rows near each pool's first and last day are flagged in the table: censored mints, capped `secs_since_create`, and mints absent because they trigger after the pool end. The screen reports bars 1–3 with and without the flagged edge days. **The pass decision uses the full set**, so no bar moves.

## Amendment 4 (2026-10-02, before any real-data run): migration sell delay

The migration sell lands at **migration + max(4, k) slots**. That equals the fixed design (migration + 4) at k ≤ 4, including the primary k = 4. At k = 8 it lags like the other exits. This resolves the wording in Amendment 3 §1 conservatively.

## Amendment 5 (2026-10-02, before any real-data model run): how the screen computes each item

Written by the manager before the screen code (PR3) exists. Revised after a `quant-proof` review of #252, which raised edits E1–E8. No model, LODO or screen output has been computed on real data. Debug table build job #88 (9-day pool + `explore-0814/w1`) builds the table only, to measure runtime and memory (Amendment 1 §6). Nothing below relaxes a bar.

1. **Selection.**
   - **Scheme.** One nested LODO over all screen days (`tools/exp013_grad_model.nested_lodo_select`). For each outer day d:
     - the threshold is the p90 of the pooled inner-LODO OOF scores over the other days;
     - the outer model is trained on the other days;
     - day d's rows are selected at score ≥ threshold.

     Selection is by mint.
   - **Label.** `1{press > 0}` on the k = 4 row (PR2 `label`), with MISS rows at label 0. This is fixed here, and no other label is screened.
   - **Skips (by trigger time, not by outcome).** A (mint, k) row is excluded from scoring **by its trigger time alone**. It is excluded, whatever its realized exit, if `trigger_ms + 1,800,000 ms + (k + max(4, k)) × 400 ms` falls at or after either of these, both taken from the table manifest's `pool_runs`:
     - the end of its pool run;
     - the first gap start after the trigger.

     Rows the table already censored on a realized exit are counted separately, and the report gives the number of them the trigger-time rule would have kept. An outer day is skipped if it has fewer than 20 training rows, a single label class or no inner OOF scores. All of these are counted in the report.
2. **Bars 1–3** call `tools.paper_attention_promote.book_stats` **once per fail model**:
   - flat: `BookTrade.pnl = flat`; pressure 1: `pnl = press`;
   - amounts in lamports, `t_ms = trigger_ms`, and assert `_utc_day(trigger_ms) == row["day"]`.

   The `pressure_scale_1=` keyword and the `promote` / `promote_blockers` fields are not used.
   - **Bar 1:** `mean_sol > 0` **and** `mean_ci90_sol[0] > 0`, using the mint-cluster bootstrap (1,000 draws, `random.Random(1)`, 5th percentile, linear interpolation).
   - **Bar 2:** `total_ex_top3_sol > 0`. A `None` value fails.
   - **Bar 3:** positive days × 2 > N, where N is **every UTC day in the pinned manifest for that item**. A day with no selected trade, or with a skipped outer fold, counts as not positive. `book_stats`'s own `majority_days_positive` is reported too.

   Selected MISS rows are trades at their fee loss and are never dropped. The gate's n ≥ 100 and ≥ 5-day conditions are reported, not screen bars.
3. **Items 4a and 4b** restrict the **same** pooled nested-LODO selected trades from item 1 to:
   - 4a: the getBlock days (pool A plus the August views);
   - 4b: the August views only.

   Bars 1–3 are applied again to each. Rows are restricted by their `pool` tag (A and X for 4a, X for 4b), and the tag is asserted to agree with the day list. No within-source or per-source model is fit, not even as a report. The report gives each outer fold's selected fraction by source.

   Disclosed: the `explore-0814/w1` days were already read by DEC-017 candidate (a) ([dec017-candidates note](../ARTIFACTS/lab/dec017-candidates-2026-10-02.md), "getblock_only (August w1)"). So 4b on w1 is not untouched data.
4. **Item 5 (slot + 8).** For the item-1 selected mints, the pooled mean of the k = 8 rows (the same mints, priced at entry slot + 8 under Amendment 4) must be > 0 under both fail models.
   - The k = 8 rows use the trigger-time exclusion from §1.
   - A missing entry state or a completed curve at k = 8 is a MISS row and is included.
   - Excluded mints are counted and reported, never imputed.

   The bar is the mean per selected trade, MISS included.
5. **Item 6 (Jaccard).**
   - A = the EXP-013 selected mints with trigger day in 2026-09-19..2026-09-27.
   - B = the EXP-012 OOF mints with `score ≥ 0.8030766588450794` (`ARTIFACTS/exp012/threshold.json`, 881 mints) that have a k = 4 row in the EXP-013 table.
   - The bar uses J(A, B) ≤ 0.5. If A ∪ B is empty, item 6 FAILS.
   - Reported, not gating: J over the unrestricted B, and the overlap |A∩B| / min(|A|, |B|).
   - The daily-PnL correlation uses EXP-012's `per_day` in `ARTIFACTS/exp012/nested_fixed_threshold_lodo.json` (`n_entered × mean_pct × 0.5 SOL`, flat and press), which is the same nested-LODO scheme.
   - Only `oof_scores.json`, `threshold.json`, `features.json` and `nested_fixed_threshold_lodo.json` are opened. `ARTIFACTS/exp012/read/` is refused.

   The August days have no EXP-012 OOF set and are reported as n/a.
6. **Tries.**
   - A try is one configuration screened on real data. The plan fixes one configuration, so the screen runs once (Amendment 1 §6).
   - The plan header's cap of 3 is withdrawn for this family. The code refuses any run if the tries log (`data/tries.jsonl`, via `MAL_TRIES_LOG`) already holds an `exp013_grad` screen entry, whatever the manifest.
   - The screen appends a `started` line (manifest sha, code commit) before any model is fit. A run that starts uses the try, even if it crashes. A table rebuilt after that point cannot be screened.
   - After a FAIL the family closes.
7. **Order of the single run** (after 2026-10-04T12:00:00Z):
   1. Pin the view manifest. It lists only views whose `VIEW.sha256` mtime is ≤ 2026-10-04T12:00:00Z, and records those mtimes.
   2. Build the table fresh, without reusing debug build #88.
   3. Append the `started` tries line.
   4. Run the screen once.
   5. Append the result line.

   Debug job #88's outputs, other than runtime, memory and row counts, are not opened. That includes the outcomes in `trigger_counts.json` and the `flat` / `press` / `outcome` fields in `table.jsonl`. Its run dir is deleted before step 1.

   The report shows bars 1–3 with and without edge-flagged days (Amendment 3). The pass decision uses the full set.

## Amendment 6 (2026-10-03, before any real-data model run): table code fix for out-of-order rows

- **The defect.** Within one hour file, rows are not in time order. On real hours of pool A, pool C and `explore-0814/w1`, PumpSwap rows trailed the running time maximum by up to 1,594 s (checked on slot, block_time and venue only).
- **The effect.** The table builder's mid-file sweep resolved a triggered mint on that running maximum. Late rows before the mint's exit could then be ignored, and the result depended on the sweep interval.
- **The fix (#267).** Every non-final sweep uses `min(running max, start of the current hour)` as its watermark. The output is now byte-identical for any sweep interval (tested).
- **Pool B check.** The fix relies on every row in hour file h having a clock at or after the start of hour h. In pool B (files named by write time), at most one row per file precedes its hour, by at most 246 ms. That is inside the 30 s `SCORE_MARGIN_MS`.
- **Scope.** No EXP-013 model, LODO or screen output existed when this was found. The single screen run (Amendment 5 §7) builds its table with the fixed code. Debug build #88 used the old code; its table is never opened and is deleted before the run. No design item or bar changes.

**Amendment 6, addendum (2026-10-03, before the run): output order.** In the single run, `screen.json` and `screen.md` are now written before the result tries line and before result.v1 (#271). If result.v1 fails its schema check, the run writes `result_error.txt`, keeps `screen.json` as the record, and still spends the try. No computed value, item or bar changes: the fixture output is byte-identical apart from a hash of temporary paths.

## Amendment 7 (2026-10-06, before any real-data model run and before the EXP-012 backcheck read): V pricing; August bars no longer on unread data

- **V pricing.** PumpSwap pools price on quote vault + V, the virtual quote reserve ([pumpswap-virtual note](../ARTIFACTS/lab/exp012-virtual-rescore-r2-2026-10-04.md)). Job #197 shows that V = 17.5845 SOL was already in effect from 2026-08-14. This plan's PumpSwap legs are the sell at migration + 4 slots, the reference sell at slot + 1, and every PumpSwap mark before the exit. In the screen they are priced through `tools.pumpswap_virtual_adapter` with `mcap_mode="v"`.
  - The map is `/data/mal/pumpswap-virtual/pool_v_0814.json`, sha256 `2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8`. It is a superset of `pool_v.json`, extended to the August pools by job #196: 34,945 pools, all 20,057 existing entries unchanged, 1 null of the 16,346 August pools, read-only since 2026-10-06T00:30Z.
  - The screen refuses before any outcome is computed if more than 1% of the triggered mints' PumpSwap prints have no V. A pool whose account fetch returned null counts as missing, never as V = 0.
  - The run records the adapter counts.
  - Bonding-curve prints are unchanged.
- **Code.** The adapter goes in as its own PR before the single screen run, with a test that it is active inside the spawned table workers. Nothing else in the design changes.
- **Disclosure: August outcomes read before this screen.** The EXP-012 backcheck ([EXP-012-backcheck-0814](EXP-012-backcheck-0814.md), PR #342) reads migrate-entry outcomes on the `explore-0814` days once. Its entries are 4, 6 and 8 slots after the first PumpSwap print, with 30-minute holds, on the frozen EXP-012 set. That run will happen before this screen.
  - Items 4 (getBlock-only days) and its August-only restriction are therefore **no longer on unread data**. That is in addition to the `w1` disclosure in Amendment 5.
  - No bar is relaxed, and every item still gates.
  - `screen.md` must carry this disclosure in its banner.
- **Post-read changes.** Any change to EXP-013 beyond V pricing that merges after the EXP-012 backcheck read counts as post-read. It must be disclosed as post-read in `screen.md` and needs a `quant-proof` pass. The reviewer of the adapter PR checks that its diff is pricing-only.
- **Tries.** Unchanged: at most 3, and none has been spent.
