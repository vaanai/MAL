# EXP-021 plan: rug signals as selector inputs (with the veto layer kept for obvious cases)

| Field | Value |
| --- | --- |
| **Status** | Plan only. Not a pre-registration. No row was read to write it. |
| **Date** | 2026-10-07 |
| **Asked by** | Owner via Helm (2026-10-07T01Z), after the live probe was stopped early (62/90 attempts, −0.210755 SOL). The question: should the rug filter be a layer before the buy, or part of the model's decision? The owner's preference is to move to rug signals as model inputs. |
| **Measured by this file** | Nothing. It cites EXP-016 §3 (the features), the holdout ledger, and the frozen EXP-012 feature list. |

## 1. Today

The rug filter is a **separate layer**. EXP-012 picks a token, and a veto can remove it. Neither attempt so far has held up:

- **#191:** six concentration vetoes. None helped. Its label (base rate 0.57) measured volatility, not rugs.
- **EXP-016:** a strict rug label plus new launch, creator and wallet-cluster features, as four arithmetic rules and a logistic veto. Its code is built. The outcome-blind precount #343 (72 GB) is queued, after #326 hit OOM at 56 GB. No try has been spent.

EXP-012 already contains weak rug proxies: `top_holder_share`, `sniper_buy_share`, `n_sells`, `sell_sol` and `creator_prior_mints_24h`. It does not see:
- who bought in the launch slots and how much they still hold;
- what the creator did on the curve;
- whether the buyers are serial dumpers.

## 2. Decision: (b) is the main bet; (a) stays as a report-only veto with no tries spent

**(b) Rug features as model inputs: EXP-021, the main bet.**
- A model can weigh a rug signal against the rest of the picture. A veto can only say no. Every veto rule so far has removed winners along with losers.
- The new features are **new information**: the lab's rule is that new edge comes from new information, not retrains.

**(a) Veto layer: kept, but only for obvious cases, and costing no tries.**
- EXP-016's four arithmetic rules (thresholds fixed from pool arithmetic before any data) are reported on EXP-021's out-of-sample picks as report-only columns, outside Holm, with no separate try.
- A veto goes into a trial config only if a later pre-registration confirms it on a fresh block.
- **The EXP-016 veto screen (6 tries) is shelved, not run.** Its feature builder and strict label are reused by EXP-021.
- The precount #343 still runs: it is the real-layout count check for that same builder.

## 3. Features: what each needs, and its cost

Every feature uses only events **strictly before the migration slot**, so all are available at k2 as well as at k6. This matters because EXP-020's report-only grid points to entry speed. That result is under quant-proof review and is not a claim.

| Group | Features (EXP-016 §3 ids) | Data | Credits |
|---|---|---|---|
| EXP-012 base | the 18 frozen features | tape we hold | 0 |
| Launch bundle | a1–a5 (launch-slot buyers, supply bought and still held, create-slot bundle, largest same-slot cohort) | tape: slot plus trader | 0 |
| Creator behaviour on the curve | b1–b5 (create-tx buy share, buys and sells after the create, sold fraction, held share) | tape | 0 |
| Slot-based sniping | c2 | tape | 0 |
| Wallet history inside the block | d1–d4 (serial launch buyers still holding, creator's earlier dumps, earlier dumpers holding, recurring buyers) | tape, one causal per-block pass (trailing 24 h) | 0 |
| Concentration | e1 `top3_share` | tape | 0 |
| **Phase 2:** creator funding source | f1 (funded by an exchange, a fresh wallet, or one shared with the early buyers) | `getSignaturesForAddress` plus up to 5 txs per creator | about 32k credits (selected mints) to 0.3M (all migrations) |
| Not used: sell pressure after migration | none | only observable after entry, so it conflicts with early entry | n/a |

**Phase 1 uses only tape we already hold: 0 credits.** Phase 2 (f1) runs only if phase 1 shows a lift. Credit use is reported per the CLAUDE.md rule.

## 4. Design (fixed before any data is read)

- **Model.** The same learner, label and decision cell as EXP-012 (k6, `tp50_sl30`, realistic costs: exit lag 2 and the haircut, both fail models). The **only** change is the feature set, so any difference can be attributed to the rug features.
- **Control arm.** EXP-012's 18 features, retrained on the same folds by the same code. The decision is **paired**: features+rug vs the control, on the same mints and the same folds.
  - Comparing against the frozen model instead would mix the effect of retraining with the effect of the features. EXP-015 showed that retraining alone does not help.
- **Folds.** Nested leave-one-day-out over the 27 non-P1 dates. P1 is report-only.
- **Threshold.** Fixed at the frozen model's out-of-fold selection rate, not tuned, so both arms pick the same number of trades.
- **Screen bars** (both legs):
  1. paired mean > 0, with a one-sided p below the family α in DEC-021;
  2. kept-book CI90 lower bound > 0;
  3. a majority of dates positive;
  4. ex-top-3 > 0;
  5. **ex-best-date > 0** (new after the EXP-017 lesson: 2026-08-21 carried every positive C0 total);
  6. no single date contributes more than 20% of the paired gain.
- **Report-only extras:**
  - k2 cells, as an upper bound (ENTRY_BOUND=start);
  - the four EXP-016 veto rules applied on top;
  - the rug-label rate among picks in both arms.
- **Disclosure.** EXP-021 is the 8th family on these 27 dates, and the base book is best-of-many. A screen pass is exploration only. The real test is the confirmation block.

## 5. Cost

| Step | Tries | Compute | Credits |
|---|---|---|---|
| Builder: features adapter (EXP-016 BlockHistory → model matrix), control arm, screen tool, tests | 0 | none | 0 |
| Real-layout precount (counts only) | 0 | one research-0 job, 72 GB, about 4 h (the EXP-016 builder measured 57 GB at P2 with 4 workers) | 0 |
| Screen (once) | **1** (one family, one paired test) | the same job size; training takes minutes | 0 |
| f1 phase 2 (only on a lift) | 0 | Helius walk | 32k to 0.3M |
| Confirmation (only after a screen pass and a merged Part 1) | consumes **one unread block** | one job | 0 |

**Confirmation blocks.** Three unread 6-day blocks are sealed and verified, or nearly so: `fresh-0828`, `fresh-0808` and `fresh-0802`. fresh-0802's w3 is still walking. EXP-016 had priority on fresh-0802; with its screen shelved, EXP-021 can claim it through its own Part 1. No block is named here.

## 6. Order and clocks

1. Builder PR: the EXP-021 tool, reusing `tools/exp016_screen` features and label. **WIP pushes; no outcome is read.**
2. Precount #343 (EXP-016 builder, already queued) finishes. Then the EXP-021 precount.
3. Quant-proof on the screen tool and the plan. Then the screen, once.
4. On a pass: f1 phase 2, then a Part 1 pre-registration naming a block, then the one read.

This runs in parallel with the 10-16 FINAL read and the DEC-020 0.25 SOL step. Nothing goes live from EXP-021 without the full gate and the owner's approval.

## 7. Honest prior

| Outcome | Probability (estimate, not a measurement) |
|---|---|
| Screen pass | about 15–20% |
| Confirmation pass | about 5–8% |

**Why higher than EXP-016's veto:** a model can use a signal where a veto cannot without removing winners.

**Why still low:**
- five filters on this book have failed;
- the strict rug event may be rare among EXP-012's picks;
- the 27 dates have been read heavily.

## Amendment 1 (2026-10-07, before any data read)

Decided by the coordinator on the builder PR (#438). No row was read. Earlier sections are not edited; where this amendment differs, it governs.

1. **Column-name swap.** `fz._fit` names the LightGBM columns from `fz.FROZEN_FEATURE_NAMES` (18). The RUG arm has 34 columns, so the tool swaps that name list in a context manager for the call and restores it. The hyperparameters are unchanged. Disclosed: `feature_fraction` 0.9 samples a different number of columns in the two arms (34 vs 18). This belongs to the feature-set change and is not a tuned difference.
2. **Count matching.** "Threshold fixed at the frozen model's out-of-fold selection rate" is implemented per held-out date: both arms keep the top-n scores (ties by mint id), where n is the frozen EXP-012 selection's count on that date. There is no inner loop and nothing is tuned. A date with no frozen pick has no pick in either arm.
3. **Bars (replaces the bar list in section 4).** All on both legs (flat and pressure), on the 27 non-P1 dates:
   1. Paired test: mean of RUG minus CONTROL > 0 with a one-sided date-cluster bootstrap p < 0.025 (10,000 draws, seed 1).
   2. Majority of the 27 dates with a positive paired gain.
   3. Paired ex-top-3 > 0.
   4. Paired ex-best-date > 0.
   5. No date contributes more than 20% of the paired gain.

   **Report-only** (never gating): the RUG kept book's CI90 lower bound (both resamplers), its ex-top-3 and its ex-best-date. Reason: at 0.05 SOL fixed fees dominate the kept book (EXP-017 C0), so a selection improvement cannot show on it. The confirmation pre-registration will set the stake and the full gate. (In the tool the bar ids stay B1, B3, B4, B5 and B6; there is no gating B2.)
4. **Alpha.** 0.025 (the DEC-021 per-walk family alpha) is the tool's own constant. It is not borrowed from `tools/exp017_screen.py`, whose `FAMILY_ALPHA` is 0.05.
5. **Pre-declared refusals** (counts only, before `started`): at least 100 frozen-selected rows on the non-P1 dates; at least one universe row on every non-P1 date; every feature finite and present; no duplicate mint; P4 in the pool (the 27 dates need it).
6. **k2.** Not available from the cached cells, which are (6,2), (6,0), (4,2) and (8,2). It is reported as n/a. The k2 upper bound in section 4 would need a new tape pass and is not part of this screen.

## Amendment 2 (2026-10-07, before any data read)

Decided by the coordinator after the quant-proof pass on the tool at 9013460. No row was read. Earlier sections and Amendment 1 are not edited; where this amendment differs, it governs.

1. **B6 is made precise.** "No single date contributes more than 20% of the paired gain" means: the net paired total (the sum over the 27 dates of the paired gain, RUG minus CONTROL) must be > 0, and max over dates of the per-date paired sum, divided by that net total, must be <= 0.20. The tool previously divided by the sum of the positive dates only, which is the weaker reading: 20 dates at +1 and 7 dates at -2.5 net to 2.5, so the best date is 40% of the gain, yet it is 5% of the positive total. Under this amendment B6 fails on that case. The reading is stricter. B5 (paired ex-best-date > 0) is unchanged. The share of the positive-date total is still reported, as report-only.
2. **New pre-declared refusal (counts only, before `started`).** Refuse if fewer than 14 of the 27 non-P1 dates have at least one frozen pick, because the majority bar B3 (14 of 27 dates with a positive paired gain) could then never pass. The count (`non_p1_dates_with_frozen_pick`) is in the precount output and its would-refuse list.

## Amendment 3 (2026-10-07, before any outcome read)

Decided by the coordinator after the real-layout precount (job #349, `/data/mal/exp021-precount-20261007T020219Z/precount.json`) and the outcome-blind diagnostic (job #353, which read `creates/` and `migrations/` only). The precount passed every EXP-021 table check: 27 of 27 non-P1 dates have a frozen pick, no feature is missing, non-finite or duplicated, and `frozen_selected_non_p1` is 2376. It would still refuse on the inherited EXP-016 loader limits. Earlier sections and Amendments 1 and 2 are not edited; where this amendment differs, it governs.

**Diagnosis.** The no-create-row shares were P1A 194/3415, P1C 104/3590, P2 408/15278, P3 414/6423 and P4 312/6588, every one above the 2% limit. P1B also failed on cell yield (2341/2789, under 90%), no-pool (57/2789, 2.04%) and pre-tape migrations (143/2487, 5.75%). Job #353 shows the no-create share falls with the days since block start: P3 16.3% on day 0, then 4.5, 5.6, 4.4, 5.1 and 2.4%; P2 10.4% on day 0, falling to 1.2-2.2% on days 9-13. That is left-censoring (tokens created before the block that graduate late), not a missing-hours loader bug. The plan's assumption that the honest rate is about 0 was wrong. These mints have no launch-slot information, so both arms already exclude them and the paired design is unaffected.

(a) **No-create limit.** For EXP-021 only, the no-create-row limit is **8% per source** (`LIMIT_NO_CREATE_021`, a loader guard sized from the diagnostic). EXP-016's `LIMIT_NO_CREATE` (2%) is unchanged: the tool swaps it for the call and restores it. A **censoring-signature check** is added: refuse if a source's no-create share in its first 24 h of counted window is not above its share after the first 24 h. A P1 source with a counted span under 48 h (or with no migration on one side) is exempt from the signature check only; the 8% limit still applies to it.

(b) **P1B is dropped from EXP-021 entirely**, from training and from report-only. Reasons: the yield failure and the pre-tape rate; P1 is report-only; P1B is the Oracle tape without `tx_index`. The P1B no-pool and pre-tape limits then do not apply. The tool does not read the P1B view: `--p1-oracle-live-dir` is optional and the tool **refuses if it is given**, and the report records "P1B excluded (Amendment 3)". The OOF-without-cell check counts only the P1 dates covered by P1A and P1C. (The coordinator's message named `--p1-oracle-insample-dir`; that argument is P1C. P1B is the `oracle-live-2026-09-25_27` view, passed as `--p1-oracle-live-dir`, and that is the argument made optional. P1C is kept.)

(c) **Precount.** `--precount` reports `would_refuse` under these rules and the per-source censoring profile (counts only). On the job #349 numbers the no-create shares are P1A 5.7%, P1C 2.9%, P2 2.7%, P3 6.4% and P4 4.7%, all under 8%. The signature check has not been run on real data.

(d) **V map pin.** `VMAP_EXP016_SHA256` is pinned by the manager on 2026-10-07 to `1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec` (the sha256 of `pool_v_exp016.json`, the fixed-parser merged map built 2026-10-06), shared with EXP-016 (see EXP-016 plan section 13 item 14). The screen no longer refuses at startup for a PENDING pin.

## Amendment 4 (2026-10-07, before any outcome read)

Outcome-blind diagnosis of the real-layout precount (job #356, which reported `would_refuse = ["P1: 569 of 7023 OOF-scored in-window mints have no cell (> 5%)"]` and nothing else). Earlier sections and Amendments 1 to 3 are not edited; where this amendment differs, it governs. No bar, threshold, try count, block or deciding cell changes.

1. **OOF-without-cell window.** `ARTIFACTS/exp012/oof_scores.json` holds 844 OOF mints on 2026-09-25; the precount table held 263 rows on that date. On the other P1 dates OOF mints and rows agree within about 20. The cause: P1C covers 2026-09-25 only 00-06Z, and 07-23Z was P1B, dropped by Amendment 3(b). Amendment 3(b) said the check counts "only the P1 dates covered by P1A and P1C", which wrongly kept the partly covered date. The check now counts only P1 dates with no dropped-source hours (`p1_oof_check_dates()`); 2026-09-25 is excluded (`p1_oof_check_excluded()`, from `oracle_live_adapter.POOL_B_HOURS`) and both are recorded in `precount.json`. 2026-09-19 stays (23 of 24 h covered by P1A; OOF and rows match). Training inputs are unchanged: the P1C cells on 2026-09-25 stay in training. The 5% limit is unchanged.
2. **The precount must predict the real run (EXP-016 plan).** Precount #356 did not surface V coverage or the constancy check in `would_refuse`. `--precount` now runs both as the screen does (V coverage on the raw pinned map, over the item 4 basis (all cells except NO_SIM 'bad create row' cells); `check_v_constancy` and `check_constancy_sample` when `--v-constancy-json` is given), reports a refusal as a `would_refuse` line instead of raising, and records `p1_oof_without_cell`, `v_coverage` and `v_constancy` in `precount.json`. For the unreadable-V pools it records an outcome-blind split per block (NO_SIM by reason, in the feature table, frozen-selected, other; counts only) and writes the sorted pool ids to `unreadable_pools.json`. The screen and `--freeze` use the same OOF date set. `--freeze` reads V (the training labels are simulated with the merged map), so it records the V coverage in the train manifest; it does not refuse on it.
3. **V coverage finding (existing, not new).** On the pinned map, overall V coverage is 33,098 of 34,809 pools = 95.08%, every source under 99%, and 1,711 pools are absent from the map. `n_null` in the map is 0, so these pools were never in the map's pool list. The screen's 99% refusal would therefore fire.
4. **V coverage remedy (decided 2026-10-07 after precount #3, job #366, head d984abd, `/data/mal/exp021-precount3-20261007T165032Z/precount.json`).** Precount #3 gave: OOF check 10 of 6,179 without a cell (fixed by item 1); V coverage 33,098 of 34,809 = 95.085%, and every one of the 1,711 unreadable pools belongs to a NO_SIM "bad create row" cell (P1 206, P2 682, P3 358, P4 465), with 0 in the feature table and 0 frozen-selected. A NO_SIM 'bad create row' cell returns before any print is V-priced, so its V is never read. V coverage is therefore computed over **all cells except NO_SIM "bad create row" cells, which return before any print is V-priced** (the other NO_SIM reasons do pass prints through `make_wrapper`, so they stay in the basis), in the screen, the precount and the freeze manifest (`v_coverage_simulated`, a wrapper in `tools/exp021_screen.py`; `e16.v_coverage` and EXP-016 are unchanged). The **99% floor is unchanged**. The all-cells coverage stays in the output as report-only (`v_coverage_all_cells`). No refetch and no new V map. Any other cell, including a NO_SIM cell with another reason, that has an unreadable V still lowers the coverage.
5. **Constancy reserve walk (EXP-021 only).** This departs from EXP-016 plan section 13 item 8(c), which says "A null reserve row is not replaced ... Replacements do not cascade." Under that rule precount #3 refuses (199 checked; 9 null, all dust: 8 primary plus reserve index 1). Decided after precount #3 from null counts and reasons only. The 200 floor, the 1% rule, the seeded primary draw and the reserve order are unchanged. EXP-016's own rule is not changed. Inputs: `/data/mal/exp021-constancy/sample.json` (primary 200, reserve 20); `constancy.json` has 208 rows (primary plus reserve[0:8]). Implementation: `check_constancy_sample_cascade` in `tools/exp021_screen.py` (used by the screen and the precount) accepts exactly primary + reserve[:r], r the smallest r >= 0 with r >= (null rows among primary + reserve[:r]); `e16.check_constancy_sample` and the default `exp016_constancy.build` are exactly as before. `tools/exp016_constancy.py --cascade` selects the cascading walk, and `--extend <existing constancy.json> --out <NEW file>` is cascade-only (it refuses without `--cascade`): it keeps the old rows unchanged, fetches only the missing reserve pools in order, and records `extended_from_sha256` in the meta. **The `--extend` input is sha256 dd7e701fa6003bd7f62aa2c74f839ad8c1355c7c217115535496935d30359442; the extended file's meta `extended_from_sha256` must equal it.** The screen and the precount refuse when the meta beside the JSON is missing, lacks `extended_from_sha256`, or records another value. **The constancy file will be re-pinned:** its sha256 changes, and the screen's `v_constancy_json_sha256` input records it.
6. Both decisions (items 4 and 5) are outcome-blind: they were made from counts and V-map facts only. No bar, threshold, try count, block or deciding cell changes.
7. **Gate before the freeze pins.** A precount on the final merged head, with the extended constancy file, must show `would_refuse == []` before the freeze pins are taken. The manager records its result, and the extended constancy file's own sha256, in the pin amendment.
