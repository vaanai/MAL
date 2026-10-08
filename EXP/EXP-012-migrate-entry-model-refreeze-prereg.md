# EXP-012 — Migrate entry model, re-freeze on the deduplicated pool, pre-registration

**This file is written and merged before the freeze runs and before any hour of the holdout is read.** It is Part 1 (pre-registration plus code). Part 2 (a separate artifacts PR, written after the freeze) records the frozen model, threshold, the proceed-screen result and every md5. **No holdout hour has been read.** At the time of writing the three walkers on `mal-research-0` are still sealing; the holdout directories were not opened, listed or hashed while writing this file or its code (all tests use synthetic fixtures).

| Field | Value |
| --- | --- |
| **ID** | `EXP-012-migrate-entry-model-refreeze-prereg` |
| **Status** | **planned** — pre-registration; freeze pending |
| **Owner seat** | Graph (measurement). `quant-proof` reviews before any sentence claims the gated book made money. |
| **Declared (UTC)** | 2026-10-01 |
| **Parent** | [EXP-011](EXP-011-migrate-entry-model-prereg.md) (closed NOT_DECIDABLE, §8 retired its model); [dedupe note](../ARTIFACTS/lab/dedupe-exploration-pool-2026-10-01.md); [clean B3 re-run](../ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md) |
| **Hypothesis** | The same pre-migration curve-state features, read by the same S2 recipe retrained on **deduplicated** data, select `migrate` entries whose net after fees is positive on a block nobody has read. |
| **Kill condition** | The primary cell (§5) fails the unchanged promotion gate (§6) under **either** fail model on the fresh block (§4); or the proceed condition (§3.3) fails before any holdout read (withdrawn, block released). One read, no second read (§8). |
| **Result** | **PASS (2026-10-01, one-shot read, MiScusi job #32, lock 15:41:04Z).** Primary cell cleared the unchanged promotion gate under both fail models on the deduplicated block `[2026-09-03T12, 2026-09-09T12)`. n = 451. Flat: mean 0.03486573251662971 SOL/trade, 90% CI [0.018745725541463414, 0.05132849610155211], ex-top-3 +14.039689244 SOL, 6/7 UTC days positive. Pressure: mean 0.020540998002217297 SOL/trade, 90% CI [0.011026153361862526, 0.02986688542084257], ex-top-3 +8.216918514 SOL, 6/7 days positive. This is a backward simulated replay, fast-box only, and **not money made**. Caveats: the unfiltered base trade (the dead migrate-direct cell) is itself positive in this block (flat +1.452%, CI lo +0.424%), so the block is favourable. Most of the lift over no filter is fill selection (98.9% vs 28.0% fill), and the lift among filled trades (+1.54 pp flat) is not significant. The unfiltered book earns more total SOL here (40.27 vs 15.72 flat). 50.7% of flat SOL comes from the two oldest days, and the latest day is negative. The selected fraction was 8.13% vs 10.01% at freeze. Per §9 this earns a forward-paper book after the 2026-10-05T05:00:00Z kill review (on `mal-fast-0`, DEC-015). It does not authorize live trading. Outputs: `ARTIFACTS/exp012/read/` (report, result.v1, lock, the 451 entered rows, pressure-leg `book_stats`, reproduced exactly from the scorer's scratch rows). |

---

## 1. Why a re-freeze, not a reuse

- EXP-011 §8 **retired** its model, threshold and feature set. Its result was NOT_DECIDABLE, but §8 forbids retuning "against a new block without a new pre-registration under a new experiment ID". This is that pre-registration.
- EXP-011's threshold `0.8012473581008048` was the 90th percentile of out-of-fold scores on a pool that **contained duplicate rows** (fast hours 2026-09-19T16, T17, T20: 2,467,409 duplicate trade rows, 3,100 creates, 97 migrations; see the dedupe note). The frozen features are mostly trade counts and flows, so duplicates shift their distribution and therefore the score distribution the threshold was cut from.
- The new holdout (§4) will be deduplicated before it is read. Scoring a deduplicated block with a threshold cut on a duplicated pool would test the model under conditions it was not fitted on. So the model and threshold are re-derived on the deduplicated pool, by the same mechanical recipe.

## 2. This is a refit of a §10-frozen candidate, and why that is allowed

Stated plainly: EXP-011 §10 forbids "any refit of the model, threshold, or feature set after this file merges" and "a fourth" tuning pass on pools A/C/B for this candidate. EXP-012 refits the same candidate. It is allowed only by EXP-011's own closing text, not by §10:

- EXP-011 Result: "The frozen model, threshold and feature set are retired for entry filtering, as §8 requires. The B3 idea itself is not retired: it goes back to exploration on deduplicated data. Any future confirmation is a new pre-registration under a new experiment ID, on a fresh block, with the fixed walker."
- EXP-011 §8: "...they are not retuned and rerun against a new block without a new pre-registration under a new experiment ID."

This file is that new pre-registration under a new ID, on a fresh block, with the fixed walker. Nothing about the recipe is chosen after looking at results: it is EXP-011 §3 verbatim, and the only change is the input. No setting, exit, feature or threshold rule is tried against pools A/C/B (the §10 spirit holds); the retraining itself is the refit that the Result carve-out permits.

## 3. The recipe (fixed now, fully mechanical)

### 3.1 Unchanged from EXP-011 §3

- **Features (18, ablated; `ARTIFACTS/exp011/features.json`, `tools.exp011_freeze.FROZEN_FEATURE_NAMES`):**
  `time_to_migrate_s, n_bonding_trades, n_buys, n_sells, n_buyers, n_sellers, buy_sol, sell_sol, net_flow_sol, buy_sell_ratio, sniper_buy_share, top_holder_share, price_return_pre, mcap_at_t_sol, hour_of_day, hour_sin, hour_cos, creator_prior_mints_24h`
  (`same_slot_buys` and `nearby_buy_sol` stay dropped: slot+1 lookahead.)
- **Model:** LightGBM binary classifier, S2 `lgb_medium` hyperparameters `num_leaves=15, min_data_in_leaf=20, learning_rate=0.05, rounds=100`, `feature_fraction=0.9, bagging_fraction=0.9, bagging_freq=1`, `scale_pos_weight = neg/pos`, `deterministic=True`, `num_threads=1`, `force_row_wise=True`, **seed 1**. Label `1{pressure net > 0}` on exit `tp50_sl30`.
- **Threshold:** the 90th percentile of the **pooled leave-one-day-out out-of-fold scores** over the 9 exploration days (every scored `tp50_sl30` row, filled and MISS), non-interpolating `index = round(0.90 * (n-1))`.
- **Entry rule:** enter iff `score(T) >= threshold`.
- **Execution:** `migrate` trigger / slot+1 start / direct / 0.0005 SOL per side / 0.5 SOL / exit `tp50_sl30`; fail models flat 15% and the pressure curve at scale 1 (frozen fit, not refit). Both are gates.
- **Gate (§6):** exactly `CLAUDE.md`'s promotion gate.
- **Table build parameters:** `max_home_hours=12`, `buffer_hours=24`, `max_workers=2` (as EXP-011 §3).

### 3.2 The only change: the input

Pool roots are the deduplicated clean-view directories on `mal-research-0` (hardlinks of the deduplicated files; manifests in the dedupe note):

| Pool | Root |
| --- | --- |
| A (fast) | `/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00` |
| C (Oracle in-sample) | `/data/mal/clean-view/oracle-insample-2026-09-22_25` |
| B (Oracle live tape) | `/data/mal/clean-view/oracle-live-2026-09-25_27` |

The hour whitelists are unchanged (the same 9 UTC days, 2026-09-19 … 2026-09-27).

**The training input is pinned by content.** The sha256 of each root's own `VIEW.sha256` file is:

| Root | sha256 of `VIEW.sha256` |
| --- | --- |
| `fast-pool-2026-09-18T23_2026-09-22T00` | `05486f70f53c7ef848b151f40d310ecc16e3ef517ff98ed7d4348250a32effe8` |
| `oracle-insample-2026-09-22_25` | `ab4fa8b058a1a3b35c7b090b89840b6d9135aede3cd08cc64516a9446d05b2c3` |
| `oracle-live-2026-09-25_27` | `a765603e535cb6757e7fe9227355f315f82fb603239f99fa200d8d9abae09251` |

The table build refuses on any mismatch (`--verify-view`: every file listed in `VIEW.sha256` is re-hashed, `VIEW.sha256` itself must hash to the value above, paths escaping the root are refused, and all three roots must be given or none), and records the three values, `"verify_view": true` and the table settings (`buffer_hours=24`, `max_home_hours=12`, `max_workers=2`, forced whenever roots are given) in `table_row_counts.json`, which the scorer re-checks. `--verify-view` is mandatory whenever roots are given. Each `VIEW.sha256` entry equals the corresponding entry of the deduplicated `/data/mal/clean/<block>/MANIFEST.sha256` (same bytes, hardlinked; the file is renamed from `.deduped.jsonl.zst` to `.jsonl.zst`); the `MANIFEST.sha256` hashes are in the [dedupe note](../ARTIFACTS/lab/dedupe-exploration-pool-2026-10-01.md). The run also refuses unless every whitelisted hour's files exist. The code change is only the plumbing of these roots (`tools/exp011_freeze.py`, `tools/exp011_build_table.py`); `tools/test_exp012_freeze_roots.py` proves decision-equivalence: the freeze outputs from a fixed table, and the pool rows from a fixed synthetic tape, hash to the same md5s as on `main` a920437 before the change, with default paths and with explicit roots. That fixture is small, and `build_table` itself (chunking, streaming to disk) is not md5-covered.

The freeze command (run by the manager as a MiScusi job after this PR merges) is in §11.

### 3.3 Proceed condition (pre-stated; the only gate before the holdout read)

The freeze also runs the report-only **nested fixed-threshold LODO** (EXP-011 §1a method, unchanged: each outer day's model and threshold use only the other 8 days) on the clean pool. EXP-012 proceeds **only if** it passes the B3 screen (`tools.exploration_entry_model_b3.screen_candidate` rule), under **both** fail models:

- pooled mean net % of entered trades **> 0**, and
- pooled ex-top-3 SOL **> 0**, and
- **more than 4 of the 9** outer days positive (that fail model's own count).

If it fails, **EXP-012 is withdrawn before any holdout read** and the block `[2026-09-03T12, 2026-09-09T12)` is released (ledger edit; it stays unread). Nothing — features, hyperparameters, seed, threshold rule, exit, size, the roots — may change in response to the freeze output, in either direction.

The first completed freeze run is binding. A re-run is allowed only if it reproduces the same table md5, model md5 and proceed_screen.json byte for byte. Any change to code or input after the screen output exists withdraws EXP-012.

This is enforced in code, not left to discipline:

- The freeze runs only at the pre-registration PR's merge commit (`--expect-commit`, tracked files unmodified), records `code_commit` and `code_dirty` in `train_manifest.json`, refuses `--frozen-manifest` together with `--skip-nested-lodo`, and refuses to write into a directory that already holds `FROZEN.md5` (a re-run goes to a different directory and is compared byte for byte).
- The screen is computed mechanically (`tools.exp012_support.proceed_screen`, which calls B3's own `screen_candidate`) and written to `ARTIFACTS/exp012/proceed_screen.json`.
- Before taking the lock, `tools/exp012_score.py` refuses unless `proceed_screen.json` is listed in `FROZEN.md5` together with `nested_fixed_threshold_lodo.json`, `train_manifest.json` and `table_row_counts.json`; `proceed == true`; the file equals `proceed_screen()` recomputed by the scorer from `nested_fixed_threshold_lodo.json`; `train_manifest.json` records `code_commit == --freeze-commit` and `code_dirty == false`; `tools/` and `schemas/` are unchanged between `--freeze-commit` and HEAD; the pin, `FROZEN.md5` and the scorer/freeze code are tracked and unmodified; and `--frozen-manifest-md5` (required) equals the md5 of `FROZEN.md5`.

The freeze output is recorded in Part 2 (the artifacts PR) with md5s (`ARTIFACTS/exp012/FROZEN.md5`) and the freeze commit.

## 4. Holdout

**Fresh block `[2026-09-03T12, 2026-09-09T12)`**, 6 UTC days, 144 hours, fast-box public-RPC backfill by the fixed walker. Reserved before any hour was sealed ([docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md), 2026-10-01), now owned by EXP-012. **Unread.** Three non-overlapping walkers on `mal-research-0`, walker code pinned at `2317b95`:

| Walker | Directory | Range (UTC hours, `[start, end)`) | Hours |
| --- | --- | --- | ---: |
| w1 | `/data/mal/blocks/fresh-0903/w1` | `[2026-09-07T12, 2026-09-09T12)` | 48 |
| w2 | `/data/mal/blocks/fresh-0903/w2` | `[2026-09-05T12, 2026-09-07T12)` | 48 |
| w3 | `/data/mal/blocks/fresh-0903/w3` | `[2026-09-03T12, 2026-09-05T12)` | 48 |

Read once, by `tools/exp012_score.py`. The scorer takes its own freeze fence: nothing in the freeze path (`tools/exp011_freeze.py`, `tools/exp011_build_table.py`) can be pointed at a root under `/data/mal/blocks` or at the EXP-011 walker directories, and no exploration-pool hour can fall in this block (asserted at import).

**Fixed read settings (no CLI override; the scorer has them as constants):** holdout feature table built with `buffer_hours=24`, `max_home_hours=12`, `max_workers=2` (the same as the training table, EXP-011 §3); the read-once lock at `/data/mal/exp012/HOLDOUT_READ.lock`.

### 4.1 Pre-read procedure (outcome-blind; none of it parses a trade row)

1. **All 144 hours sealed**: every hour of each walker's range has `status == "sealed"` in that walker's `checkpoint.json`.
2. `python3 -m tools.backfill_verify --dir <walker dir> --from <start> --to <end>` (metadata) and again with `--content` on each of the three directories. Any hour with a slot issue (`backwards_slot_range`, `implausible_slot_span`), a missing trades file (`sealed_with_no_trades_file`), a sealed file next to a partial checkpoint, or any file not sealed to `.zst` is a refusal. (A `resumed: duplicate risk` hour is allowed: step 3 removes its exact duplicates; it is disclosed.) `backfill_verify --content` detects duplicate rows, not missing rows.
3. **Exact duplicates removed deterministically** with `backfill_verify --content --dedupe-out <clean dir>/<wN>` (first occurrence wins, input order preserved). This looks at line equality only and is outcome-blind. Per-walker row counts in, out and removed are disclosed in the result.
4. **sha256 pin**: `python3 -m tools.exp012_score --write-dedupe-pin PATH …` stream-hashes every deduplicated trades and creates file against its manifest and writes `PATH` (sha256sum format: each walker's `manifest.json` and every deduplicated file). The pin is committed and merged **before** the read.
5. **The read is on the deduplicated copy.** The scorer re-hashes every deduplicated trades and creates file against the manifest **before the lock** (hashing bytes reveals no outcome, so a mismatch is a refusal that does not spend the block). It never opens the raw walker data files. After the lock only the read and scoring happen (plus a defense-in-depth re-hash).

**Refusals before the lock.** If verification finds an unsealed, missing or slot-flagged hour (or any other pre-lock refusal below) BEFORE the lock is taken, that hour may be re-walked once with the same pinned walker code (`2317b95`). This is outcome-blind: no holdout row is scored. Then the full verification runs again. A walker that stops at its credit cap with hours unsealed has not "finished": the cap may be raised and the walker resumed, outcome-blind, inside the same 24-hour window. If the block is still not clean within 24 hours of the last walker finishing, EXP-012 closes NOT_DECIDABLE and the block is released unread. **Any refusal or failure AFTER the lock spends the block.**

Run the read from a worktree at the freeze commit plus only the Part 2 ARTIFACTS commit. Later `tools/` merges would otherwise refuse the read (safely: the scorer requires `tools/` and `schemas/` to equal the freeze commit's, and the working tree under `tools/`, `schemas/` and `ARTIFACTS/exp012` to be clean, including untracked files).

The scorer's own refusals (each tested, `tools/test_exp012_score.py`), all before the lock and before any row is read: a walker range not tiling exactly the 144-hour block; any hour not sealed; any metadata slot issue; raw creates presence differing from the manifest's for any hour; no dedupe manifest, a manifest that does not cover every hour's trades file, or a pin that does not match; any deduplicated trades/creates file whose bytes differ from the manifest; frozen artifacts whose md5s differ from `ARTIFACTS/exp012/FROZEN.md5` or fail the proceed/provenance checks of §3.3; code or pin untracked or modified; `out-dir` not writable or already holding a report or `NOT_DECIDABLE.json`; `zstdcat` missing; the frozen model failing a smoke predict; a lock that already exists. The **O_EXCL read-once lock is written before the first row is read.** The verdict and report JSON are printed to stderr before any file is written.

### 4.2 Creator history

As EXP-011 §4: `creator_prior_mints_24h` history is built from the holdout block's own create hours only (the 144 whitelisted hours), as in training where each pool builds its own. The first ~24 h undercount, identically in training and holdout. No hour outside the block is read.

### 4.3 Source

The holdout is fast-box (public-RPC backfill) data only; no cross-source split is possible. In EXP-011's exploration read the fast slice was the weakest source (§1 there). In the clean B3 re-run the lead cell's one negative day was 2026-09-20, a fast day. A holdout weaker than the pooled exploration number is the expected base case.

## 5. Cell (one, primary, k = 1)

| Cell | Rule |
| --- | --- |
| `s2_ablated_top` (primary, only gating cell) | Enter iff `score(T) >= threshold` (§3). Exit `tp50_sl30`, execution unchanged. |

The primary cell is the **only gating cell**. Everything else the scorer prints (fill-conditional net, selected fraction, per-day table, baselines) is report-only; no second cell is scored under any name. Per DEC-014, k = 1, no Holm correction.

## 6. Gate (unchanged from `CLAUDE.md`)

Under **both** the flat 15% model and the pressure model at scale 1: at least 100 out-of-sample trades; at least 5 distinct UTC days with a majority positive; lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile); total SOL still positive after removing the top 3 trades. Computed by `tools.paper_attention_promote.book_stats`, via `tools.exp011_score.compute_gate`. A selected fraction of 10% of the holdout's migrate attempts is expected to give roughly the 100-trade minimum or fewer; if `n < 100` the gate fails on `min_n` and that is the verdict (not re-tuned).

## 7. Also reported, not gating

Fill-conditional net (selected and all filled), selected fraction (reported plainly even outside 5–20%, never re-tuned), the per-day table, dedupe counts, and the block's within-block-only source note. Exactly the EXP-011 §7 list.

## 8. Kill, and no second read

Failing any gate condition under either fail model kills EXP-012. The model, threshold and feature set are retired. There is no second read of this block for any reason or under any cell name. A new idea needs a new ID and a fresh block. A NOT_DECIDABLE outcome (a refusal after the lock, or an integrity failure after the lock) also spends the block, as in EXP-011 and consistently with §4.1. A byte-hash mismatch before the lock is a refusal, not a spent block.

## 9. If it passes

A pass earns a forward-paper book, added only after the 2026-10-05T05:00:00Z kill review; never direct promotion to live. Nothing here authorizes live trading.

## 10. Disclosures (read before trusting any number)

1. **The clean B3 re-run does not test this model.** [exploration-entry-model-b3-clean-2026-10-01.md](../ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md) (#190) used B3's **unablated** 20-feature set (it includes `same_slot_buys`, `nearby_buy_sol`). It says the 09-28 screen was not a duplicate-row artifact; it says nothing about the 18-feature ablated model frozen here. The ablated model's only clean-pool evidence will be the freeze's own proceed screen (§3.3).
2. **Row-count mismatch, EXP-011.** EXP-011's table had pool A = 3092 rows against B3's 3029 (C 3372 vs 3342, B 2337 vs 2300). That is a **code difference** (the table is built with `max_home_hours=12, buffer_hours=24`; B3 with the default chunking and `buffer_hours=2`, so more create→migrate lags are resolved), **not duplicate inflation**. Evidence: all three duplicated fast hours (2026-09-19T16, T17, T20) fall on 2026-09-19, and the 2026-09-19 count is equal in both: `ARTIFACTS/exp011/table_row_counts.json` has `"2026-09-19": 962`, and the nested LODO's 2026-09-19 `n_test` (`ARTIFACTS/exp011/nested_fixed_threshold_lodo.json`, `fold_info`) is 962, the same as B3's. Had duplicates inflated the count, 2026-09-19 would be the day that differed. EXP-012 builds its table with the EXP-011 table parameters.
3. **The pool has been read in at least four passes** (#146, #152, #156, the 2026-10-01 clean re-run), plus the EXP-011 freeze and nested LODO. Fit to that week is likely. The best-of-6 winner's curse (EXP-011 §1) still applies. On the clean pool the **unablated 20-feature** lead cell (`tpsl_tp50_sl30` / `s2_clf`, not the model frozen here) moved from flat +6.73% to +6.05% and from 9/9 to 8/9 days. Its one negative day is 2026-09-20, a fast day: top-10% flat −0.13%, pressure −0.41% (clean re-run raw output, `s2_clf` per-day table). The note's own caution applies: "With about 100 trades per day, '8/9 days' is not a strength." The 9/9 and 8/9 counts, here and in EXP-011 §1a, are not strong evidence either way.
4. **The holdout is fast/backfill only.** The pool the model learned from is one-third fast; fast was the weakest source. 2026-09-20, a fast day, was the lead cell's one negative day in the clean re-run.
5. **EXP-011's hours were spent.** The EXP-011 block `[2026-09-09T12, 2026-09-15T12)` is not used here in any form.
6. **Walker provenance.** The fresh block is walked by the fixed walker (`2317b95`). Row loss on resume was fixed by `042e534` ("exactly-once resume, no-files-no-seal, refuse backwards/implausible slot ranges"), an ancestor of `2317b95`. Resumed hours may still contain exact duplicates (removed in §4.1). `backfill_verify --content` detects duplicates, not missing rows, so the guarantee against row loss rests on the walker fix, not on that check.
7. **Result records.** The scorer and the freeze can also write a `result.v1` record (`--result-out`, roles `confirmation-oneshot` and `exploration`); it does not change the verdict.

## 11. Commands

Freeze (manager, MiScusi job, after this PR merges; `mal-research-0`; one heavy job at a time; run from a checkout at the merge commit, with no tracked file modified):

```
nice -n 19 python3 -m tools.exp011_build_table --max-workers 2 --verify-view \
  --out-dir /data/mal/exp012 \
  --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
  --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
  --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
python3 -m tools.exp011_freeze --table /data/mal/exp012/table.jsonl --out-dir ARTIFACTS/exp012 --frozen-manifest \
  --expect-commit <the merge commit of the pre-registration PR> \
  --result-out /data/mal/exp012/freeze-result.json
```

The read command is `python3 -m tools.exp012_score` with `--w1-dir/--w2-dir/--w3-dir`, `--w1-clean-dir/...`, `--artifact-dir ARTIFACTS/exp012`, `--dedupe-pin`, `--frozen-manifest-md5` and `--freeze-commit` (all required); Part 2 records the exact line.

## 12. Freeze record (Part 2, 2026-10-01)

The binding freeze ran once, as MiScusi job #23 (`j_eFNPWZPCx0PEQg`) on `mal-research-0`, from a clean checkout at the pre-registration merge commit `ea5ec374010378b14a5c19e81bf045679bde73b9` (`train_manifest.json`: `code_commit` as given, `code_dirty: false`, seed 1). It used the §11 commands verbatim. The input VIEW pins were verified by the build (219 + 237 + 69 files). The deduplicated `MANIFEST.sha256` files list 220 / 238 / 70. The one extra file per block is that block's own `manifest.json` (the dedupe report), which is not data and is not read by the loaders. **The rows did not change:** the clean table has exactly EXP-011's row counts, 8,801 total, A/C/B = 3092/3372/2337, with every per-day count identical. Dedupe changed feature values and outcomes on existing rows, not which migrate rows exist. No earlier freeze output exists. A table build (step 1 only) ran before the pre-registration merged: MiScusi job #5 `j_Jd0Ka1vgs2fbIw`, code `ed6f756` (the PR head at the time), started 2026-10-01T01:48:19Z and cancelled at 02:04:19Z. The manager started it early to save wall time while the reviews ran. That went against §11's "after this PR merges". It was cancelled once the code changed under review. It never completed, never trained a model, and never produced a threshold or a screen. Its partial scratch output was moved to `/data/mal/exp012-precommit-discard-0204` and nobody inspected its rows. Any model built at `ed6f756` would also have failed the `--expect-commit` and `--freeze-commit` checks. The data involved is exploration-pool data, not holdout.

- Table: `/data/mal/exp012/table.jsonl` sha256 `7ebdd2aec002a85d9065794f7ebcfc4f9069bfd6484382ff2e7ce09efb3e9205` (`table.md5` = `14ada9ea6694f00cf7d351600b4e2193`); settings 2 / 24 / 12, `verify_view: true`.
- Frozen manifest: `ARTIFACTS/exp012/FROZEN.md5`, whose own md5 is **`a01f05dfb1e622f78b2bba55d174be09`**. `model.txt` md5 `a1810d219ed61db64a396f40dc302ce5`, `features.json` `5dec821ed0f4098b71b1c544de23a0ab` (identical to EXP-011's ablated 18-feature list), `threshold.json` `c6dfa593bfa38450d7f3b29344308315`.
- Threshold: **0.8030766588450794**, the 90th percentile of 8,801 pooled outer out-of-fold scores (881 at or above it, 10.01%). EXP-011's dirty-pool threshold was 0.8012473581008048.

**Proceed screen (§3.3): PASS** (`proceed_screen.json`, copied, not rounded):

| Leg | Pooled mean net % | Ex-top-3 SOL | Days positive (of 9) |
| --- | ---: | ---: | ---: |
| flat 15% | +7.43247570684624 | +31.140748286599997 | 9 |
| pressure scale 1 | +4.672464066442589 | +19.458084402126982 | 9 |

**Split by source (same nested LODO; the holdout is fast-only):**

| Source | n | Flat mean net % | Pressure mean net % |
| --- | ---: | ---: | ---: |
| A, fast box (the holdout's source) | 302 | +4.194131571622516 | +3.245824010529341 |
| C, Oracle in-sample | 391 | +7.647979839565218 | +3.8910183090046893 |
| B, Oracle live | 198 | +11.946202034848485 | +8.391608450503291 |

The pooled +7.43% is lifted by the Oracle sources. The only all-fast day, 2026-09-19, is flat +2.669265323655914%, the lowest flat day of the nine. If anything from this file is read as a guide to the fast-box holdout, it is the source-A row, not the pooled one.

Report-only context from `nested_fixed_threshold_lodo.json`: n = 891 entered. Flat CI lower bound +5.188668409001117%, pressure +3.1683341591043632%. These are in-sample exploration-pool numbers (each fold's threshold is chosen without its own test day, but the pool has been read many times; see §1 and §10). They are **not** gate evidence and do not predict the holdout. The comparable EXP-011 dirty-pool preview was flat +7.9934%, pressure +4.9233%, 9/9 days.

**Read command** (run once, after §4.1's verify, dedupe and pin steps; from a worktree at the freeze commit plus only the Part 2 and dedupe-pin commits; `/data/mal/blocks-clean/fresh-0903/wN` is where `backfill_verify --dedupe-out` writes):

```
python3 -m tools.exp012_score \
  --w1-dir /data/mal/blocks/fresh-0903/w1 --w1-clean-dir /data/mal/blocks-clean/fresh-0903/w1 \
  --w2-dir /data/mal/blocks/fresh-0903/w2 --w2-clean-dir /data/mal/blocks-clean/fresh-0903/w2 \
  --w3-dir /data/mal/blocks/fresh-0903/w3 --w3-clean-dir /data/mal/blocks-clean/fresh-0903/w3 \
  --artifact-dir ARTIFACTS/exp012 --dedupe-pin ARTIFACTS/exp012/dedupe_pin.sha256 \
  --frozen-manifest-md5 a01f05dfb1e622f78b2bba55d174be09 \
  --freeze-commit ea5ec374010378b14a5c19e81bf045679bde73b9 \
  --out-dir /data/mal/exp012/read --result-out /data/mal/exp012/read/result.json
```

Run `--dry-run-preconditions` with the same arguments first. Any refusal there stops the read without spending the block.

## Amendment 1 (2026-10-06): non-owner feature read of walk-1 hours by walk 2

Appended per [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) rule 3. Nothing frozen above changes. `[2026-10-14T01, 2026-10-16T01)` is also read for features only as walk 2's buffer (DEC-021 §3); no outcome is used. That read runs only after EXP-012's FINAL is written.

## Amendment 2 (2026-10-08): EXP-022 (CAP-PICK) reads of the forward walk's hours

Appended per [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) rule 3 and DEC-014(a). Nothing frozen above changes. Nothing in DEC-016 changes here.

[EXP-022](EXP-022-cap-pick-part1-prereg.md) counts from 2026-10-16T01, on walk 2 (Option Y). It counts no hour of this block. It reads this block in two ways, both only after EXP-012's FINAL is written.

1. **Walk-2 feature buffer.** This is already covered by Amendment 1 and DEC-021 §3. The gate replay's first boot reads the creates of `[2026-10-15T00, 2026-10-16T01)` and the prints of hour 2026-10-16T00, for features only. Both sit inside `[2026-10-14T01, 2026-10-16T01)`. No outcome is used.
2. **A11 October check** (SYNTHESIS A11), report-only, on `[2026-10-06T00, 2026-10-16T00)`. It reads CAP-PICK outcomes on this block's FINAL window, plus its own creator-history preload from this block's creates.
   - It runs only if a **separate DEC-016 amendment**, a manager PR, registers it before 2026-10-16T00:00Z.
   - It decides nothing in EXP-012 or EXP-022, and it is never gate evidence.
   - Its only effect is a spending pause (EXP-022 §7.5).
   - The FINAL's window, read and verdict, and the DEC-016 Am.2 and Am.3 seal, are unchanged.

**Option X rejected.** Option X, under which EXP-022 would have been a second owner counting `[2026-10-10T00, 2026-10-16T01)`, was considered and rejected on 2026-10-08 (EXP-022 §0).

## Sources

[EXP-011](EXP-011-migrate-entry-model-prereg.md) §1, §1a, §3, §4, §6–§8, §10 and Result; `tools/exp011_freeze.py`, `tools/exp011_build_table.py`, `tools/exp011_score.py`; [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md); [dedupe note](../ARTIFACTS/lab/dedupe-exploration-pool-2026-10-01.md); [clean B3 re-run](../ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md); [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md).
