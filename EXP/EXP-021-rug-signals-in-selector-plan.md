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
