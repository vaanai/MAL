# EXP-011 — Migrate entry model (frozen, ablated), pre-registration

**This file is written before any holdout data exists on disk.** It is a pre-registration only: no holdout hour has been read, no threshold was tuned on holdout data, no result is reported. Declared (UTC): **2026-09-29T01:24:00Z** (the freeze training run against pools A/C/B — none of which touch the holdout — was already in progress at declaration time; see §3 and the commit history for `tools/exp011_freeze.py`).

| Field | Value |
| --- | --- |
| **ID** | `EXP-011-migrate-entry-model-prereg` |
| **Status** | **planned** — pre-registration; the frozen model is trained on exploration data only, holdout is not read |
| **Owner seat** | Graph (measurement). `quant-proof` reviews before any sentence claims the gated book made money. |
| **Started** | 2026-09-29 |
| **Parent context** | Lane B3 (#156, `tools/exploration_entry_model_b3.py`, [ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](../ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md)) found that S2 (an `lgb_medium` classifier on `P(pressure net > 0)`), taking the top 10% of migrate entries scored under exit `tp50_sl30`, is the best of 6 pre-stated screening cells (3 settings x 2 exits) over a 9-UTC-day leave-one-day-out. The manager+quant-proof audit on PR #156 found two of S2's 20 input features (`same_slot_buys`, `nearby_buy_sol`) are computed at the trade's own slot+1 landing — **after** the migrate decision time T, i.e. lookahead the model's no-lookahead test never covered. With those two features removed, the result **survives**: flat **+6.22%** (CI lo **+3.85%**), pressure **+3.59%** (CI lo **+2.06%**), ex-top-3 **+24.97 / +14.17 SOL**, **9/9 days** positive under both fail models (pooled top-10%, 9-day LODO). This file freezes that ablated model and pre-registers its one out-of-sample test. |
| **Hypothesis** | Pre-migration curve state — price return, buyer counts, flows, holder concentration, market cap at T, time to migrate — read by the frozen model (`tools/exp011_freeze.py`, see §3), selects `migrate` entries whose net after fees is positive. |
| **Kill condition** | The primary cell (§5) does not clear the unchanged promotion gate (§6) on the reserved holdout (§4) under **both** fail models. There is no second read of this block, and no threshold, feature, or model change after this file merges (§3). A single positive in-sample or exploration read is not a promote — see the winner's-curse note in §1 and `LAB_STATE.md`'s own note on the frozen migrate-direct cell's first positive run. |
| **Method** | Offline replay of the frozen migrate-direct execution (unchanged from [ARTIFACTS/lab/migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md), migrate trigger / slot+1 start / direct / 0.0005 SOL per side / 0.5 SOL primary), gated by `enter iff score(T) >= threshold`, scored once on the reserved fresh block in §4 by `tools/exp011_score.py` (a follow-up PR, not this one — see §7). No RPC calls beyond what the backward backfill has already made. No evaluate/runner code change, no forward-paper wiring in this PR. |
| **As-of-T** | Constitution rule 3 (knowable-at-T). T is the migrate decision time (the same receive-clock substitution the frozen cell uses when `t_recv_ms` is missing). Every one of the model's 18 frozen features is computed from `tools.exploration_entry_model.causal_events`'s output — rows strictly before T — and nothing else; see §3 and the no-lookahead proof in `tools/test_exp011_freeze.py`. |
| **Regime labels** | Unchanged from the base cell — the model does not stratify by `regime_id`; every row still carries its regime tag at ingest (Constitution rule 4). |
| **Windows** | Not the 1s/5s/15s/30s/60s Discovery grid. The outcome horizon is the base trade's own exit: `tp50_sl30` (first of +50% / −30% / 30-minute cap), unchanged from the frozen cell. |
| **Fail models** | Flat 15% on sends, and the pressure curve at scale 1 (frozen fit, not refit on this test set). Both are gates. |
| **Promotion gate** | Unchanged: see §6. |
| **Result** | Not run. Pre-registration only. |
| **Conclusion** | Not run. |

---

## 1. Evidence, and why it is weak

- **B3's screen** ([ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](../ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md)): 3 settings (`s1_reg` regression, `s2_clf` classification, `s3_reg_winsor` winsorized regression) x 2 exits (`tpsl_tp50_sl30`, `trail_30_act20`) = 6 pre-stated cells, screened by one pre-fixed rule (pooled top-10% mean net % > 0, pooled ex-top-3 SOL > 0, majority of 9 held-out days individually positive, under **both** fail models). 4 of 6 cells passed the screen. `tpsl_tp50_sl30 / s2_clf` was the strongest: flat **+6.73%** (CI lo +4.35%), pressure **+3.84%** (CI lo +2.35%), 9/9 days both models. This is the cell this file freezes (before the lookahead ablation).
- **The ablation** (manager+quant-proof audit, PR #156 review comment): `same_slot_buys` and `nearby_buy_sol` are computed by `tools.latency_curve._pressure` at the order's own slot+1 landing state — a time strictly after the migrate decision T this model is supposed to decide at. The no-lookahead test in `tools/test_exploration_entry_model.py` covers `causal_events`/`compute_features` only; it never touched these two features, which are appended to the feature dict separately in `score_one` (`tools/exploration_entry_model.py:370-371`). Retrained and rescored on the ablated 9-day LODO, `tpsl_tp50_sl30 / s2_clf` top-10% survives: flat **+6.22%** (CI lo +3.85%), pressure **+3.59%** (CI lo +2.06%), ex-top-3 **+24.97 / +14.17 SOL**, **9/9 days** positive under both models. By source (flat / pressure): fast +1.95% / +1.24%, Oracle in-sample +8.48% / +4.52%, Oracle live +8.56% / +5.31% — positive on every one of the three source pools, not carried by one.
- **Best of 6, still winner's curse.** The ablated cell frozen here is still the best of the 6 screened (setting, exit) cells on the exploration pool, chosen by looking at all 6 results together. A cell passing an in-sample or exploration screen is not evidence by itself — see the standing winner's-curse note on the frozen migrate-direct cell (`LAB_STATE.md` §"Promotion gate": "The first positive in-sample cell does **not** clear this gate... it was the best of 972 cells, the CI lower bound is below 0"). This file's holdout (§4) is the only mechanism that can turn this from a hypothesis into a result, and it is read exactly once.
- **9 exploration days, not more.** The LODO is 9 folds over 9 UTC days pooled from three source pools (fast-box backfill, Oracle in-sample backfill, Oracle live tape) — see B3's docstring for the exact hour ranges. 9 days is a small sample for a leave-one-day-out; the per-fold feature-importance table in the B3 report shows `nearby_buy_sol`/`top_holder_share`/`price_return_pre`-family features stable across folds, but stability across 9 folds of the same pool is not the same claim as stability on unseen weeks.
- **Fill-selection caveat** (from the same audit): under the un-ablated S2, the top-10% cohort's fill rate is 98.96% vs 34.72% for the full population. The miss penalty is only the priority fee (~0.1% of size), so miss-avoidance alone cannot produce a multi-percent absolute mean lift — but the model is disproportionately selecting entries that land, and a fill-conditional comparison has not been reported for the holdout yet. §7 requires the scoring PR to report it (non-gating).
- **The holdout is entirely fast-box data, and fast was the weakest exploration source.** §4's holdout block is one continuous range from a single fast-box walker (`walker B`, `/var/lib/mal/backfill-fast-b`) — the same source (fast, public-RPC-derived backfill, not Oracle) live trading itself would run against once forward-paper starts on the fast box. In the exploration split-by-source table (§1's ablation bullet, and B3's own report), the fast slice was consistently the **weakest of the three source pools**: pressure net **+2.50%** for the un-ablated `s2_clf` top-10% (`ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md`, "pooled top10%, split by source" table, `tpsl_tp50_sl30 / s2_clf`, row `fast`), falling to **+1.24%** after the ablation (PR #156 audit comment's by-source breakdown) — both positive, but well below the Oracle in-sample (+4.52%) and Oracle live (+5.31%) slices that make up the rest of the pooled 9-day mean. **A holdout result weaker than the pooled 9-day number above is therefore the expected base case, not a surprise or a sign something broke** — the holdout is drawn from exactly the source pool that already underperformed the other two during exploration.
- **The 9-day pool was read across several passes, not once.** The same 9 exploration days were read and modeled across at least three separate PRs — the original 3-day entry model (#146), its extension to 5.7 days (#152), and B3's extension to the full 9 days plus the (setting, exit) screen (#156) that produced the cell this file freezes. Each pass looked at (a subset of) the same week's data before choosing the next step. Some fit to that particular week — beyond what a single clean LODO captures — is therefore likely, on top of the already-disclosed best-of-6 winner's curse above.
- **The fixed-threshold nested LODO (§1a) is the fairest in-sample preview available**, because unlike the frozen threshold in §3 (which pools OOF scores across all 9 days to pick one number), each of its 9 outer folds picks its own threshold from an inner LODO that never sees that fold's own test day at all — closer to what §4's true one-shot holdout read will look like, though still drawn from the same fitted-to week as everything else in this section.

### 1a. Fixed-threshold nested LODO (report-only, in-sample preview — NOT gating)

Owner-requested scope addition (2026-09-29), run before any holdout data is read. For each of the 9 outer held-out days: an outer fold model is trained on the other 8 days (the exact frozen spec — ablated features, S2 hyperparameters, deterministic, §3); its entry threshold is chosen using **only** those 8 days, via an inner 8-fold LODO over them (8 inner models, each trained on 7 of the 8 and scored on the 8th) — the 90th percentile of the pooled inner out-of-fold scores. That one fixed threshold is then applied to the outer day's own rows, scored by the outer fold model: enter iff `score >= threshold`. The entered trades are pooled across all 9 outer days and reported below under both fail models. **This is report-only — it is not a gate, and per the owner's instruction it must not be used to change the frozen threshold, feature set, or hyperparameters in §3 (it was not).** Full detail: `ARTIFACTS/exp011/nested_fixed_threshold_lodo.json`; implementation: `tools/exp011_freeze.py::nested_fixed_threshold_lodo` / `nested_lodo_report`, tested in `tools/test_exp011_freeze.py::NestedFixedThresholdLodoTests`.

[Results table filled in after the freeze run — see the PR body and `ARTIFACTS/exp011/nested_fixed_threshold_lodo.json` for the numbers, copied verbatim, not re-typed.]

## 2. Base trade — unchanged frozen-cell execution

Copied verbatim from [migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md), frozen 2026-09-27T13:06:36Z, and reused unchanged by `tools/exploration_entry_model.py` / `tools/exploration_exits.py`:

- Trigger: `migrate` (first PumpSwap print after a bonding print on that mint)
- Fill bound: slot+1 start (state is the last print with `slot < migration_slot + 1`)
- Slippage cap: 15% (`DEFAULT_SLIPPAGE_CAP = 0.15`)
- Exit: `tp50_sl30` (one sell; +50% / −30%, else the 30-minute cap)
- Route: direct (`portal_fee_ppm = 0`)
- Priority: 0.0005 SOL per side (500,000 lamports), no Jito tip
- Size: 0.5 SOL primary (500,000,000 lamports)
- Fail models: flat 15% on sends, and the pressure curve at scale 1 (intercept −1.4548727851312098, slot slope 0.8, SOL slope 0.35, scale 1 — the same fit `mixed_net` in `tools/latency_curve.py` uses, not refit here)

This experiment adds only an entry filter (§3) in front of the same trade. Nothing about priority, route, exit, fill bound, or size changes.

## 3. The frozen spec

Everything below is produced by `tools/exp011_freeze.py` and committed under `ARTIFACTS/exp011/`. **No number in this section is tuned after this file merges — no refit, no threshold change, no feature change.**

- **Feature set (18 features, frozen):** `tools.exploration_entry_model.FEATURE_NAMES` (B3's own unchanged feature list) **minus** `same_slot_buys` and `nearby_buy_sol` (§1's ablation) — `FROZEN_FEATURE_NAMES` in `tools/exp011_freeze.py`:

  `time_to_migrate_s, n_bonding_trades, n_buys, n_sells, n_buyers, n_sellers, buy_sol, sell_sol, net_flow_sol, buy_sell_ratio, sniper_buy_share, top_holder_share, price_return_pre, mcap_at_t_sol, hour_of_day, hour_sin, hour_cos, creator_prior_mints_24h`

  This list is asserted, at import time, to equal exactly the key set `tools.exploration_entry_model.compute_features()` returns on its own — i.e. every frozen feature comes from the pure aggregation over `causal_events()`'s strictly-before-T output, never from `tools.latency_curve._pressure()` (the slot+1 landing state that produced the two dropped features). `tools/test_exp011_freeze.py` proves this three ways: (a) the set-equality assertion as a unit test, (b) every one of the 18 features individually checked against post-cutoff event injection (not just a whole-dict comparison), and (c) an end-to-end `score_one` perturbation test that holds pre-cutoff `feat.events` fixed and varies only landing-slot fill data, proving the frozen feature subset of the resulting row is byte-identical while the two dropped features actually move (i.e. the test is not vacuous).
- **Model:** LightGBM binary classifier, S2's own `lgb_medium` hyperparameters unchanged (`num_leaves=15, min_data_in_leaf=20, learning_rate=0.05, rounds=100`), `objective=binary`, `deterministic=True`, `num_threads=1`, fixed `seed=1` (`tools.exploration_entry_model.SEED`). Label: `1{pressure net > 0}` under the frozen execution (§2) on exit `tp50_sl30`.
- **Training data:** all 9 exploration days, pools A (fast-box backfill, `tools.exploration_exits`) + C (Oracle in-sample backfill, `tools.oracle_insample_adapter`) + B (Oracle live tape, `tools.oracle_live_adapter`), B3's own loaders and hour whitelists, reused unchanged. `tp50_sl30` rows only (both filled and MISS — DEC-007, no survivorship), sorted deterministically by `(day, mint, spec)` before every fit.
- **Threshold:** the ablated S2 LODO is recomputed on the frozen 18-feature set (9 folds — each held-out day's model trained on the other 8, same hyperparameters, deterministic). The **pooled out-of-fold scores** across all 9 folds (every scored `tp50_sl30` row, filled and MISS) set the threshold at their **90th percentile**, non-interpolating: `index = round(0.90 * (n-1))` into the sorted score array (same convention as B3's own bootstrap `_pct`). The OOF table is saved (`ARTIFACTS/exp011/oof_scores.json`) so the threshold computation is reproducible without rerunning the full pipeline.
- **Entry rule (frozen):** enter the `migrate` trade iff `score(T) >= threshold`, else skip. `score(T)` is the frozen model's `predict()` on the 18-feature vector, in `FROZEN_FEATURE_NAMES` order, computed at the migrate decision time T.
- **Artifacts committed** under `ARTIFACTS/exp011/` (text only, no data files):
  - `model.txt` — LightGBM text dump of the model fit on all 9 exploration days
  - `model.md5` — md5 of `model.txt`
  - `threshold.json` — the threshold value, its percentile definition, `n_oof`, and the observed selected fraction
  - `features.json` — `FROZEN_FEATURE_NAMES`, the two dropped features, and the source feature list
  - `train_manifest.json` — hours read per pool, row counts, hyperparameters, seed, code commit, `model.md5`
  - `oof_scores.json` — the pooled out-of-fold score table (day, mint, score, label, filled) used to set the threshold
  - `nested_fixed_threshold_lodo.json` — §1a's report-only nested LODO result (pooled entered-trade stats under both fail models, per-day table, fill-conditional net, source split, per-fold thresholds). Not part of the frozen spec; never read by `tools/exp011_score.py`.

  **Model md5, threshold value, and the OOF selected-fraction check are recorded in the PR body and in `LAB_STATE.md`, copied verbatim from these files — not re-typed by hand, not rounded.**

## 4. Holdout

**Fast-box `[2026-09-09T12, 2026-09-15T12)`** — walker B, `/var/lib/mal/backfill-fast-b` — reserved for EXP-011 first (if it survives the leakage audit and is pre-registered — it did, this is that pre-registration) by [#157](https://github.com/vaanai/MAL/pull/157), recorded in [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md). **Unread.** This file's own freeze script (`tools/exp011_freeze.py`) asserts, at import time, that none of its three training pools (A, C, B) ever reaches into this range — see `_assert_never_holdout` and the module-level assertions immediately after the pool hour imports.

- Six UTC days, 144 hours, walking backward from `2026-09-15T12:00:00Z` (exclusive) to `2026-09-09T12:00:00Z` (inclusive), capped at +2,100,000 credits (`tools.pump_history_backfill --until 2026-09-15T12:00:00Z --hours 144 --credit-cap 2100000`, running on the fast box alongside the primary backfill walker).
- **Read exactly once, after all 144 hours are sealed.** `tools/exp011_score.py` (§7, a follow-up PR — not built in this PR) must check the walker-B checkpoint shows every one of the 144 hours `status == "sealed"` (not `"partial"`, not missing) before it scores anything, and refuse to run otherwise. There is no partial or incremental read of this block for EXP-011 — no interim peek is used to inform any decision about this experiment before that check passes.
- No burn-in / trailing-window causality question applies here the way it did for EXP-009's creator-recurrence gate: this model's features are all computed from the same causal per-mint accumulation (`causal_events`, gated strictly before each mint's own T) that pools A/B/C already use, with no cross-mint or trailing-window state. A sealed hour is immediately usable once its own mint-level create→migrate data is on disk.

## 5. Cell (one, primary — no Holm needed)

| Cell | Rule |
| --- | --- |
| `s2_ablated_top` (primary, only cell) | Enter iff `score(T) >= threshold` (§3). Exit `tp50_sl30`, execution unchanged (§2). |

This is the single pre-registered cell. Per [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md)'s multiplicity rule, a single primary cell needs **k = 1** — no Holm–Bonferroni correction applies, because there is nothing to correct against: this file declares exactly one cell before any holdout row is read, and no second cell is scored on this same holdout under any other name.

## 6. Gate (unchanged from `CLAUDE.md`)

A book promotes only if all of the following hold, under **both** the flat 15% fail model and the pressure-fail model at slope scale 1:

- at least 100 out-of-sample trades
- at least 5 distinct UTC days, with a majority of those days positive
- lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile)
- total SOL still positive after removing the top 3 trades

Failing any one of those, under either fail model, kills EXP-011's primary cell — there is no second read of this holdout to try again (§8).

## 7. Also reported, not gating

The scoring PR (`tools/exp011_score.py`, a follow-up, not this one) must report all of the following alongside the gate result, but none of them changes the pass/fail call in §6:

- **Fill-conditional net** for the selected (`score >= threshold`) trades, and separately for all filled trades in the holdout population — the §1 fill-selection caveat applies until this is checked on holdout data.
- **Selected fraction** — the share of holdout `migrate` attempts with `score >= threshold`. If it falls outside 5–20%, report it plainly but do **not** re-tune the threshold, the feature set, or the model in response. The threshold was fixed in §3, before any holdout hour existed.
- **Per-day table** — trade count, flat net %, pressure net % for every one of the (up to 6) UTC days the holdout covers.
- **Per-source note** — the holdout is a single fast-box block from one walker; state plainly that no cross-source split (unlike §1's exploration read) is possible here, only within-block day variation.

## 8. Kill

Failing any gate condition in §6, under either fail model, kills EXP-011. There is no second read of this holdout block — reading it once, and once only, is the entire point of a pre-registration. If the primary cell fails, the model, threshold, and feature set frozen in §3 are retired for entry-filtering purposes; they are not retuned and rerun against a new block without a new pre-registration under a new experiment ID.

## 9. If it passes

A pass earns the ablated model a **forward-paper book**, added after the 2026-10-05T05:00:00Z kill review, never direct promotion to live. Nothing in this file authorizes live trading — that requires a book to separately clear the promotion gate on its own forward-paper data (per `CLAUDE.md`'s "Promotion gate" and "North star" sections) and the owner's approval. A pass on this backward holdout is a green light to start that forward-paper book, not a green light to trade real SOL.

## 10. Not in this test

- **Any further tuning pass on the 9-day exploration pool for this candidate.** §1's "read across several passes" note (#146, #152, #156) already describes three; this file forbids a fourth. No new setting, exit, feature, or screen for the ablated `s2_clf` / `tp50_sl30` candidate may be tried against pools A/C/B after this file merges — not to pick a different threshold, not to "improve" the cell, not in response to §4's holdout result either way. A genuinely new idea is a new experiment with its own ID and its own fresh block, not a retune of this one.
- Any refit of the model, threshold, or feature set after this file merges.
- The un-ablated model (with `same_slot_buys`/`nearby_buy_sol`) — that version is retired by the §1 audit, not tested here.
- `trail_30_act20` or any exit other than `tp50_sl30`.
- Any evaluate/runner code change or forward-paper wiring — this PR only freezes the offline model and pre-registers the holdout read.
- A different priority, tip, route, fill bound, or size than §2.
- A refit of the pressure intercept.
- Direct promotion to live trading (§9).
- Any second read of the §4 holdout, for any reason, under any cell name.

---

## Sources

- [ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](../ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md) — the 9-day, 6-cell screen this file freezes the winner of
- [ARTIFACTS/lab/migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) — frozen base-cell execution (§2)
- PR [#156](https://github.com/vaanai/MAL/pull/156) review comment — the lookahead finding and the ablated LODO numbers quoted in §1
- PR [#157](https://github.com/vaanai/MAL/pull/157) — reserved the holdout block in [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md)
- `tools/exp011_freeze.py`, `tools/test_exp011_freeze.py` — the frozen spec and its no-lookahead proof (§3)
- `ARTIFACTS/exp011/` — the committed frozen artifacts (§3)
- `LAB_STATE.md` — clean clock, kill review, winner's-curse note on the frozen migrate-direct cell
- [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) — holdout ledger rules and the multiplicity correction (§5)
