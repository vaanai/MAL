# DEC-014 — Holdout ledger and multiplicity correction

| Field | Value |
| --- | --- |
| **Status** | Active (working law) |
| **Decider** | Claude manager (`mal-fast-0`), owner-reviewable |
| **Date** | 2026-09-28 |
| **Amends** | Promotion gate procedure in [LAB_STATE.md](../LAB_STATE.md) §"Promotion gate" — tightens it, does not replace it |
| **Does not amend** | The gate's four base thresholds (≥100 OOS trades, ≥5 distinct UTC days majority positive, 90% CI lower bound > 0, ex-top-3 still positive), paper-only fence, one-topic-per-PR, workers-do-not-merge |
| **Handoff** | [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md), [LAB_STATE.md](../LAB_STATE.md), [EXP-009](../EXP/EXP-009-migrate-creator-gate-prereg.md) |

## Context

Two related gaps surfaced on 2026-09-28:

1. Nothing on record said which experiment owns which block of sealed historical hours, or what happens when a non-owner reads into someone else's block. The migrate-direct fast OOS scorer (`mal-fast-oos-score`) had no hour floor and kept scoring newly sealed backward hours as the backfill walked past EXP-009's declared cut, reading two hours (2026-09-18T23, 2026-09-19T00) that belong to EXP-009's holdout window. See [EXP-009 Amendment 3](../EXP/EXP-009-migrate-creator-gate-prereg.md).
2. The promotion gate's 90% CI bound is a single-test criterion. The 2026-10-05 kill review reads 9 forward-paper books together, and EXP-009 plus any future confirmation experiment adds more simultaneous reads. Reading k books at one sitting and promoting any one that individually clears a 90% one-sided bound is a multiple-comparisons problem: with k = 9 independent books each tested at the nominal single-test false-positive rate, the chance at least one clears by chance alone is not 5%, it is roughly 1 − (1 − α)^9.

## Decision

### (a) Adopt the holdout ledger

[docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) is adopted as written. Each block of sealed historical hours has exactly one owner (a confirmation `EXP-###` or the shared exploration pool). Exploration results can only earn a pre-registered confirmation test on an unowned or newly bought block — never a promote directly off exploration hours. Any non-owner read of an owned block must be disclosed as a dated amendment in the owner's `EXP-###` file. Blocks enter the ledger before they are sealed or read, not after.

### (b) Multiplicity correction

When k books or cells are read together at one review (for example, the 9 forward-paper books read at the 2026-10-05T05:00:00Z kill review, or the up-to-4 EXP-009 cells), a book or cell promotes only if it clears the existing gate **and** passes a Holm–Bonferroni step-down at family α = 0.05, applied to the one-sided bootstrap test of mean SOL/trade > 0.

- The test statistic is the bootstrap p-value: the share of 1,000 seed-1 bootstrap-resampled means that are ≤ 0. Sort the k books' p-values ascending: p₁ ≤ p₂ ≤ ... ≤ pₖ. Compare p₁ to α/k, p₂ to α/(k−1), and so on; a book at rank i passes only if pᵢ ≤ α/(k−i+1) **and** every book ranked before it also passed its own threshold (standard Holm step-down — one failure below a rank stops promotion for that rank and everything below it).
- With 1,000 bootstrap draws (seed 1), the smallest resolvable p-value is 0.001 (1 in 1,000). For k = 9, the first Holm threshold is α/9 = 0.05/9 ≈ 0.0056 — below the 0.001 resolution floor only by about a factor of 5.6, but close enough that a book sitting near the boundary cannot be resolved reliably at 1,000 draws. **Recommendation: 10,000 draws, seed 1, for any multiplicity-tested read**, giving a resolution floor of 0.0001, comfortably below 0.0056.
- Apply the Holm step-down under **both** fail models (flat 15%, pressure-scale-1). A book must pass Holm under both, exactly as the un-corrected gate already requires both.
- This tightens the gate. It never loosens it: a book that fails the existing four-part gate still fails regardless of its Holm rank, and Holm can only turn an existing marginal pass into a fail, never the reverse.

**Why:** with k = 9 independent books each individually tested at a 90% one-sided CI bound (a single-test false-positive rate α = 0.05), the probability that at least one clears the bound by chance alone, with no real edge in any of them, is approximately 1 − 0.95⁹ ≈ 1 − 0.6302 ≈ 37%. That is not a small-sample coincidence risk, it is the expected behavior of reading 9 tests at once and taking any single "win." Holm–Bonferroni bounds the family-wise false-positive rate at the same α = 0.05 across all k reads.

### (c) Single read, no interim peeking

The kill-review books are read once, at 2026-10-05T05:00:00Z, per the ledger's forward-paper row. No interim read of those books before that instant may be used to make or influence a promote decision. Monitoring reads (health, lag, crash checks) are unaffected — this restricts only promote decisions.

## Consequences

- `LAB_STATE.md` §"Promotion gate" gets a pointer to this DEC and to the Holm step-down for any multi-book or multi-cell read.
- EXP-009's §7 (scoring and multiple comparisons) already restricts its primary claim to one cell (`G1_only`) — this DEC adds the numeric Holm procedure on top of that qualitative restriction, for the case where more than one cell is eventually read together.
- The 2026-10-05 kill review must run its bootstrap at 10,000 draws (seed 1) per book, not 1,000, per the recommendation above, and must report the Holm-adjusted pass/fail for each of the 9 books, under both fail models, alongside the un-adjusted gate numbers.
- `docs/HOLDOUT_LEDGER.md` is now load-bearing: a future confirmation experiment must check the ledger before declaring a holdout range, and its own `EXP-###` file must disclose any read of hours it does not own.

## Overturn path

New DEC. Default remains: one owner per historical block, Holm–Bonferroni step-down at family α = 0.05 (10,000 seed-1 bootstrap draws) on any read of k ≥ 2 books or cells together, both fail models, single read at the kill-review instant.
