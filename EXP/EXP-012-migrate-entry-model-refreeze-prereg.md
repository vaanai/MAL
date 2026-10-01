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
| **Result** | Not run. |

---

## 1. Why a re-freeze, not a reuse

- EXP-011 §8 **retired** its model, threshold and feature set. Its result was NOT_DECIDABLE, but §8 forbids retuning "against a new block without a new pre-registration under a new experiment ID". This is that pre-registration.
- EXP-011's threshold `0.8012473581008048` was the 90th percentile of out-of-fold scores on a pool that **contained duplicate rows** (fast hours 2026-09-19T16, T17, T20: 2,467,409 duplicate trade rows, 3,100 creates, 97 migrations; see the dedupe note). The frozen features are mostly trade counts and flows, so duplicates shift their distribution and therefore the score distribution the threshold was cut from.
- The new holdout (§4) will be deduplicated before it is read. Scoring a deduplicated block with a threshold cut on a duplicated pool would test the model under conditions it was not fitted on. So the model and threshold are re-derived on the deduplicated pool, by the same mechanical recipe.

## 2. Not a new idea

Nothing about the recipe is chosen after looking at results. EXP-012 is EXP-011 §3 run again on cleaner input. §10 of EXP-011 (no further tuning pass on the exploration pool) still holds: no setting, exit, feature, or threshold rule is tried against pools A/C/B.

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

The hour whitelists are unchanged (the same 9 UTC days, 2026-09-19 … 2026-09-27). `VIEW.sha256` under each root is verified before the run (`--verify-view`); the run also refuses unless every whitelisted hour's files exist. The code change is only the plumbing of these three roots (`tools/exp011_freeze.py`, `tools/exp011_build_table.py`); `tools/test_exp012_freeze_roots.py` proves decision-equivalence: the freeze outputs from a fixed table, and the pool rows from a fixed synthetic tape, hash to the same md5s as on `main` a920437 before the change, with default paths and with explicit roots.

The freeze command (run by the manager as a MiScusi job after this PR merges) is in §11.

### 3.3 Proceed condition (pre-stated; the only gate before the holdout read)

The freeze also runs the report-only **nested fixed-threshold LODO** (EXP-011 §1a method, unchanged: each outer day's model and threshold use only the other 8 days) on the clean pool. EXP-012 proceeds **only if** it passes the B3 screen (`tools.exploration_entry_model_b3.screen_candidate` rule), under **both** fail models:

- pooled mean net % of entered trades **> 0**, and
- pooled ex-top-3 SOL **> 0**, and
- **more than 4 of the 9** outer days positive (that fail model's own count).

If it fails, **EXP-012 is withdrawn before any holdout read** and the block `[2026-09-03T12, 2026-09-09T12)` is released (ledger edit; it stays unread). Nothing — features, hyperparameters, seed, threshold rule, exit, size, the roots — may change in response to the freeze output, in either direction. In particular a pass does not license picking among variants and a fail does not license a second freeze with different settings under this ID. The freeze output is recorded in Part 2 (the artifacts PR) with md5s (`ARTIFACTS/exp012/FROZEN.md5`).

## 4. Holdout

**Fresh block `[2026-09-03T12, 2026-09-09T12)`**, 6 UTC days, 144 hours, fast-box public-RPC backfill by the fixed walker. Reserved before any hour was sealed ([docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md), 2026-10-01), now owned by EXP-012. **Unread.** Three non-overlapping walkers on `mal-research-0`, walker code pinned at `2317b95`:

| Walker | Directory | Range (UTC hours, `[start, end)`) | Hours |
| --- | --- | --- | ---: |
| w1 | `/data/mal/blocks/fresh-0903/w1` | `[2026-09-07T12, 2026-09-09T12)` | 48 |
| w2 | `/data/mal/blocks/fresh-0903/w2` | `[2026-09-05T12, 2026-09-07T12)` | 48 |
| w3 | `/data/mal/blocks/fresh-0903/w3` | `[2026-09-03T12, 2026-09-05T12)` | 48 |

Read once, by `tools/exp012_score.py`. The scorer takes its own freeze fence: nothing in the freeze path (`tools/exp011_freeze.py`, `tools/exp011_build_table.py`) can be pointed at a root under `/data/mal/blocks` or at the EXP-011 walker directories, and no exploration-pool hour can fall in this block (asserted at import).

### 4.1 Pre-read procedure (outcome-blind; none of it parses a trade row)

1. **All 144 hours sealed**: every hour of each walker's range has `status == "sealed"` in that walker's `checkpoint.json`. Any missing or unsealed hour: **NOT_DECIDABLE, no read.**
2. `python3 -m tools.backfill_verify --dir <walker dir> --from <start> --to <end>` (metadata) and again with `--content` on each of the three directories. Any hour with a slot issue (`backwards_slot_range`, `implausible_slot_span`), a missing trades file (`sealed_with_no_trades_file`), a sealed file next to a partial checkpoint, or any file not sealed to `.zst`: **NOT_DECIDABLE, no read.** (A `resumed: duplicate risk` hour is allowed: step 3 removes its exact duplicates; it is disclosed.)
3. **Exact duplicates removed deterministically** with `backfill_verify --content --dedupe-out <clean dir>/<wN>` (first occurrence wins, input order preserved). This step looks at line equality only and is outcome-blind. Per-walker row counts in, out and removed are disclosed in the result.
4. **sha256 manifest**: `python3 -m tools.exp012_score --write-dedupe-pin PATH …` writes `PATH` (sha256sum format: each walker's `manifest.json` and every deduplicated file). The pin is committed and merged **before** the read.
5. **The read is on the deduplicated copy.** The scorer refuses unless the pin matches the manifests, re-hashes every deduplicated trades and creates file after taking the lock (an integrity failure there is NOT_DECIDABLE and spends the lock), and never opens the raw walker data files.

The scorer's own refusals (each tested, `tools/test_exp012_score.py`), all before the lock and without opening any trade/create data file: a walker range not tiling exactly the 144-hour block; any hour not sealed; any metadata slot issue; no dedupe manifest, a manifest that does not cover every hour's trades file, or a pin that does not match; frozen artifacts whose md5s differ from `ARTIFACTS/exp012/FROZEN.md5` (or whose features differ from `FROZEN_FEATURE_NAMES`); a lock that already exists. The **O_EXCL read-once lock is written before the first holdout data file is opened.**

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

Failing any gate condition under either fail model kills EXP-012. The model, threshold and feature set are retired. There is no second read of this block for any reason or under any cell name. A new idea needs a new ID and a fresh block. A NOT_DECIDABLE outcome (a refusal after the lock, an integrity failure) also spends the block, as in EXP-011.

## 9. If it passes

A pass earns a forward-paper book, added only after the 2026-10-05T05:00:00Z kill review; never direct promotion to live. Nothing here authorizes live trading.

## 10. Disclosures (read before trusting any number)

1. **The clean B3 re-run does not test this model.** [exploration-entry-model-b3-clean-2026-10-01.md](../ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md) (#190) used B3's **unablated** 20-feature set (it includes `same_slot_buys`, `nearby_buy_sol`). It says the 09-28 screen was not a duplicate-row artifact; it says nothing about the 18-feature ablated model frozen here. The ablated model's only clean-pool evidence will be the freeze's own proceed screen (§3.3).
2. **Row-count mismatch, EXP-011.** EXP-011's table had pool A = 3092 rows against B3's 3029 (C 3372 vs 3342, B 2337 vs 2300). That is a **code difference** (the table is built with `max_home_hours=12, buffer_hours=24`; B3 with the default chunking and `buffer_hours=2`, so more create→migrate lags are resolved), **not duplicate inflation**: for 2026-09-19 the counts are equal (962). EXP-012 builds its table with the EXP-011 table parameters.
3. **The pool has been read in at least four passes** (#146, #152, #156, the 2026-10-01 clean re-run), plus the EXP-011 freeze and nested LODO. Fit to that week is likely. The best-of-6 winner's curse (EXP-011 §1) still applies; the clean re-run weakened the lead cell slightly (flat +6.73% to +6.05%, 9/9 to 8/9 days).
4. **The holdout is fast/backfill only.** The pool the model learned from is one-third fast; fast was the weakest source. 2026-09-20, a fast day, was the lead cell's one negative day in the clean re-run.
5. **EXP-011's hours were spent.** The EXP-011 block `[2026-09-09T12, 2026-09-15T12)` is not used here in any form.
6. **Walker provenance.** The fresh block is walked by the fixed walker (`2317b95`). Resumed hours may still contain exact duplicates (removed in §4.1) and the duplicate-free guarantee is only as good as `backfill_verify --content`.
7. **Result records.** The scorer and the freeze can also write a `result.v1` record (`--result-out`, roles `confirmation-oneshot` and `exploration`); it does not change the verdict.

## 11. Commands

Freeze (manager, MiScusi job, after this PR merges; `mal-research-0`; one heavy job at a time):

```
nice -n 19 python3 -m tools.exp011_build_table --max-workers 2 --verify-view \
  --out-dir /data/mal/exp012 \
  --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
  --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
  --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
python3 -m tools.exp011_freeze --table /data/mal/exp012/table.jsonl --out-dir ARTIFACTS/exp012 --frozen-manifest
```

The read command and its flags are in `tools/exp012_score.py --help`; Part 2 records the exact line with the frozen manifest md5 and the dedupe pin.

## Sources

[EXP-011](EXP-011-migrate-entry-model-prereg.md) §1, §1a, §3, §4, §6–§8, §10 and Result; `tools/exp011_freeze.py`, `tools/exp011_build_table.py`, `tools/exp011_score.py`; [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md); [dedupe note](../ARTIFACTS/lab/dedupe-exploration-pool-2026-10-01.md); [clean B3 re-run](../ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md); [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md).
