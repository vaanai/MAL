# EXP-021 Part 1: pre-registration of the single fresh-block read

**Written and merged before the EXP-021 screen runs and before any hour of the block is read.** The owner's reviewer (Lyra) asked that the one confirmation read be fixed before any scoring, so the screen result cannot shape the confirmation. No row of `/data/mal` was read to write this file or its code; the tests use fixtures.

| Field | Value |
| --- | --- |
| **ID** | `EXP-021-part1-prereg` |
| **Status** | **planned**. Inert unless the screen PASSES and the block-budget rule (section 9) is met. |
| **Declared (UTC)** | 2026-10-07 |
| **Parent** | [EXP-021 plan](EXP-021-rug-signals-in-selector-plan.md) with Amendments 1 to 3 (the screen); [EXP-012 re-freeze pre-registration](EXP-012-migrate-entry-model-refreeze-prereg.md) (format and the frozen selector); [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md); [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) |
| **Hypothesis** | The 16 rug features, added to EXP-012's 18, select migrate entries whose net after realistic costs is higher than the same learner without them, on a block nobody has read. |
| **Kill condition** | Any bar in section 5 fails on either leg, or the read is refused or aborted after `started`. One read, no second read, no retuning (section 10). |
| **Tool** | `tools/exp021_screen.py`: `--freeze OUT_DIR` (done in this PR), `--confirm` (a refusing stub in this PR; see section 8). |

## 1. Block

**fresh-0802, `[2026-08-02T12, 2026-08-08T12)`**, 144 hours, read through the clean views `/data/mal/clean-view/fresh-0802/w1`, `w2`, `w3` (walkers `[2026-08-06T12, 2026-08-08T12)`, `[2026-08-04T12, 2026-08-06T12)`, `[2026-08-02T12, 2026-08-04T12)`). The ledger records it as sealed and verified on 2026-10-07 (48/48 hours and 0 flagged per walker), never read. The block touches 7 UTC dates; the first (08-02) and the last (08-08) are half days.

**The claim takes effect only if the screen PASSES (every bar, both legs) and the claim meets the block-budget rule (section 9).** Otherwise this file is inert, the ledger row stays "reserved, unclaimed", and nothing here reads the block. A claim is recorded by a merged amendment that quotes the screen's `report.json` figures (copied, not rounded), the family count `m` and its list, and the ledger edit.

Features use the block's own hours only (as EXP-012 section 4.2: creator and wallet history is built from the block's own create hours, so the first day undercounts, identically in training).

## 2. Model (frozen before the read)

Two models, both from `python3 -m tools.exp021_screen --freeze OUT_DIR`, run once:

| Arm | Columns | Files |
| --- | ---: | --- |
| RUG | 34 (`FEATURES_RUG`) | `model.txt`, `model.md5`, `features.json` |
| CONTROL | 18 (`FEATURES_CONTROL`, EXP-012's) | `control-model.txt`, `control-model.md5`, `control-features.json` |

plus `train-manifest.json` (universe sha256, feature-table sha256, row counts per source, code head, V-map sha256, learner, md5 of each model).

**Recipe, fixed now.**

- **Training rows:** ALL exploration rows, P1A, P1C, P2, P3 and P4, in the screen's own table (the same loader, the same limits, the 8% no-create limit and the censoring signature of Amendment 3). **P1B is excluded** (Amendment 3(b)). The freeze reads no held-out date, because there is none; both models are trained on pools whose labels the screen has already read, so the freeze spends no try and writes no tries line.
- **Learner:** `tools.exp015_screen.fit_cfg(x, y, 20)`, which is `tools.exp011_freeze._fit`: LightGBM binary, `num_leaves 15`, `min_data_in_leaf 20`, `learning_rate 0.05`, 100 rounds, `feature_fraction 0.9`, `bagging_fraction 0.9`, `bagging_freq 1`, `scale_pos_weight = neg/pos`, **seed 1**, `deterministic=True`, `num_threads 1`, `force_row_wise`.
- **Label:** `1` iff the trade FILLED and its pressure net at the primary cell is > 0 (`label_row`; a MISS is 0), the screen's label.
- **Reproducibility:** nothing in the written files depends on the clock, host or thread count. `tools/test_exp021_screen.py` shows two runs on a fixture give byte-identical files and the same md5s. A real re-run is allowed only if it reproduces both md5s.
- **Code sha:** the freeze runs from a clean checkout whose `tools/` equals the Part 1 merge commit's (the tool refuses a dirty `tools/` and records `code_head`). The V map is the pinned `VMAP_EXP016_SHA256`; the freeze refuses unless the pin is set.
- **Order:** this file merges; the screen runs once; only on a PASS and a granted claim does the freeze run (it does not depend on the screen output, so the order cannot shape the models); then a **merged amendment records both model md5s, the manifest md5 and the freeze commit BEFORE the block is read.** `--confirm` refuses without that amendment.

## 3. Arms on the block, and the pick count

Arms: **RUG (frozen)** and **CONTROL (frozen)**. Per date `d` of the block, both arms keep the top-`n_d` scores (ties broken by mint id), where `n_d` is the number of picks the **frozen EXP-012 model** makes on that date (`ARTIFACTS/exp012/model.txt`, threshold 0.8030766588450794, md5s in `FROZEN.md5`). Scoring the EXP-012 model on the block is a model application on features; it does not read an outcome. A date with no frozen pick has no pick in either arm. No threshold is tuned.

## 4. Deciding cell

One cell, on both legs (flat 15% fail model and the pressure model at slope scale 1):

- **k6**, exit **`tp50_sl30`**, **exit lag 2**, with the haircut, **0.05 SOL**, V pricing (as the screen).
- Exit lag 2 is what was measured live: landing p90 2, mark-consistent crossing p90 2 to 5 (#445).
- **Report-only leg at exit lag 5**, never gating.
- **No k2 cell.** Live k p50 is 5, so k2 is not reachable on the current build; no k2 number is computed or reported as a result.

## 5. Primary test (all must hold, on both legs)

On the block's dates, with the paired gain `x_m = (s_RUG - s_CONTROL) * net_m` over every counted row:

1. **B1:** mean paired gain > 0 and a one-sided **date-cluster bootstrap p < 0.025** (10,000 draws, seed 1; the screen's `paired_stats`).
2. **B3:** a strict majority of the block's dates have a positive paired gain (a zero is not positive).
3. **B4:** paired ex-top-3 > 0.
4. **B5:** paired ex-best-date > 0.
5. **B6:** net paired total > 0 and no date exceeds 20% of it (Amendment 2: the max date's sum divided by the NET total).

Power note, stated now: the block has 7 date clusters, two of them half days. A date-cluster bootstrap on 7 clusters is coarse and one half-day outlier can move B1. That is accepted; the bars are not relaxed.

## 6. Secondary (report-only, never gating)

The RUG kept book against the **full CLAUDE.md promotion gate** at **0.05, 0.25 and 0.5 SOL**: at least 100 trades, at least 5 distinct UTC days with a majority positive, lower 90% CI bound of mean SOL per trade > 0 (1,000 draws, seed 1), total positive after removing the top 3, under both fail models. **Size projection caveat:** the simulator at 0.05 SOL does not price impact at the larger sizes. The 0.25 and 0.5 SOL rows are projections, not measurements; entry impact of +22.9 and +51.6 bps from #445 is not applied inside them, and a larger size shrinks the edge by at least that. A positive paired gain does not make the kept book a promote. Also report-only: the rug-label rate among picks per arm, the four EXP-016 veto rules on RUG's picks (no separate try), the exit-lag-5 leg, and the per-date table.

## 7. Refusals before `started` (counts only, no outcome)

Each is a refusal that does not spend the block:

- **V coverage** of the block's pools against the pinned map, and the **V constancy check on the 0802 pools**. The constancy sample (pool ids only) is built from the 0802 pools. **That part runs only after this Part 1 merges**; it is outcome-blind.
- **No-create limit: 8%** per source with the **censoring signature** (the share in the first 24 h of the block above the share after it), exactly Amendment 3(a).
- **At least 100 frozen picks** (the frozen EXP-012 model's picks on the block).
- The screen's other pre-declared table refusals that apply (every feature finite and present, no duplicate mint, at least one universe row on every date), the clean-view `VIEW.sha256` verification, and the model md5s matching the amendment.

If any refuses, the block may be re-checked once outcome-blind; a refusal after `started` spends it (section 10).

## 8. One read, locks and tries

Mirrors the screen: guards, then ONE tape pass, then the section 7 counts, then `started`, then the fit-free scoring and the report.

- `started`, `completed` and `aborted` lines in the ops tries log **and** `data/tries.jsonl`, under one config key, one try.
- A `RUN.lock` taken before `started`, and the canonical tries check (refuse if an EXP-021 confirmation line exists in either log), re-checked under the lock.
- After `started` there is no resume and no re-run.

**Implementation status.** This PR ships `--freeze` and this file. **`--confirm` is a refusing stub** (TODO: a separate PR, after the md5 amendment and this file's merge). It opens no path, takes no lock and writes no line. The confirm implementation needs its own review before the block is touched; nothing may read the block with any other tool.

## 9. Block-budget gate (the manager adds this to DEC-014 and the ledger)

A screen PASS may claim a sealed block only if all three hold:

1. **Multiplicity.** The screen's **B1 p < 0.025 / m** on both legs, where **m is the number of families that have read outcomes on the 27 non-P1 dates**, counted from `data/tries.jsonl` **at claim time** (a family is the experiment id in the lines' `config.experiment`; the claim amendment lists them), **including EXP-021 itself**. The plan's own count calls EXP-021 the eighth family, which would give p < 0.003125; the manager's recount at claim time governs.
2. **One block per family.** A family that has claimed a block claims no second one, whatever its result.
3. **One in reserve.** At most 2 of the 3 sealed blocks (fresh-0828, fresh-0808, fresh-0802) are claimed before a new block is walked, sealed and verified, so one is always unread. A claim counts as spent from its merge.

The screen's own bar (B1 p < 0.025) is unchanged; this rule is applied by the claim amendment from the report's `p_one_sided` on both legs.

## 10. Kill and no second read

Failing any section 5 bar on either leg kills the RUG selector. There is no second read of this block, no new cell name, no retuned feature, parameter, count rule, label or lag. A refusal or failure after `started` also spends the block (NOT_DECIDABLE). A pass earns **only** a forward-paper challenger under DEC-021 after quant-proof and the owner; it does not authorize live trading. Nothing here lowers the CLAUDE.md promotion gate.

## 11. Honest prior and disclosures

- **Prior (estimates, not measurements):** screen pass about 15 to 20%; confirmation about 5 to 8%.
- EXP-021 is the eighth family on the 27 dates; the base book is best-of-many; the strict rug events are few; five earlier filters on this book failed.
- fresh-0802 is August data, further from the September training pools than the screen's dates; drift against the September-heavy pool is expected to cost, not help.
- The control is retrained on the same rows, so the paired gain isolates the 16 features. It does not show the selector beats the frozen EXP-012 model; that comparison is report-only.
- Exit lag 2 is optimistic against the live exit leak; the lag-5 leg is the check.
- Credits: 0 (the block is already walked). Pending the manager's amendments: model md5s, the claim, the constancy sample.

## Sources

[EXP-021 plan and Amendments 1 to 3](EXP-021-rug-signals-in-selector-plan.md); [EXP-012 pre-registration](EXP-012-migrate-entry-model-refreeze-prereg.md); `tools/exp021_screen.py`; `tools/test_exp021_screen.py`; [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) (fresh-0802, fresh-0808, fresh-0828 rows); [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md); CLAUDE.md promotion gate; #445 (exit-lag and entry-impact measurements, as cited by the manager).
