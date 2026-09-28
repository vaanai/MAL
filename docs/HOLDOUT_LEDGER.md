# Holdout ledger

Which experiment owns which block of sealed historical hours. This is the accounting that [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) formalizes.

## Rules

1. Each block of sealed historical hours belongs to exactly **one** owner. The owner is either one confirmation experiment (an `EXP-###` file) or the shared **exploration pool**.
2. Exploration results can only earn a pre-registered confirmation test on an **unowned** or **newly bought** block — never a promote. A cell that looked good on exploration-pool hours is a hypothesis, not a result.
3. Any read of a block by a non-owner must be disclosed in the owner's `EXP-###` file (an amendment, dated, pointing back here).
4. Blocks enter this ledger **before** they are sealed or read — the row exists as soon as the range and intended owner are fixed, not after the data shows up.

## Table

UTC hours, inclusive start, exclusive end.

| Block | Hours | Host | Owner | Status |
| --- | --- | --- | --- | --- |
| Oracle in-sample | 2026-09-22T00 → 2026-09-25T07 | Oracle | exploration pool | Seen: 972-cell grid in-sample, plus migrate-direct Oracle OOS 2026-09-22T00–10. |
| Fast pre-cut | 2026-09-19T01 → 2026-09-22T00 | fast | exploration pool | Was the frozen migrate-direct OOS book, now closed. |
| Fast EXP-009 block | `[oldest sealed at cap, 2026-09-19T01)` | fast | **EXP-009** | Owned, except the two hours below. |
| Fast EXP-009 exclusion | 2026-09-18T23, 2026-09-19T00 | fast | exploration pool | Read by the migrate-direct fast scorer (`mal-fast-oos-score`), disclosed in [EXP-009 Amendment 3](../EXP/EXP-009-migrate-creator-gate-prereg.md). Excluded from EXP-009 scoring. |
| Forward paper, kill review | 2026-09-28T00:00Z → 2026-10-05T05:00Z (9 forward books) | Oracle (forward-paper runner) | kill review | Single read, at the kill-review instant. No interim peeking used for a promote decision. Must join `settlements.jsonl` with `positions.jsonl` for restart-orphaned positions per [DEC-014 Amendment 1](../DEC/DEC-014-holdout-ledger-and-multiplicity.md#amendment-1-2026-09-28--kill-review-must-join-the-offline-settled-orphans). |
| Future fast backfill | Beyond the current +2M credit cap | fast | unassigned | To be allocated in ≥6-day blocks, one confirmation test each, as the backfill extends. |

## How to add a block

1. Pick the UTC hour range (inclusive start, exclusive end) and the host that will seal it.
2. Add a row to the table above **before** any hour in the range is sealed or read, with an owner: either a named `EXP-###` or "exploration pool."
3. If a non-owner later reads any hour in an owned block, disclose it as a dated amendment in the owner's `EXP-###` file, and update the block's `Status` cell here to point at that amendment.
4. If an owned block later needs a scope change (extend, split, or shrink), do it by ledger edit plus a note in the relevant `EXP-###` file — not silently.
5. A block never has two owners at once. Reassigning an owner is a ledger edit with the reason recorded in `Status`.

## Changelog

- 2026-09-28: initial ledger. Oracle in-sample and fast pre-cut blocks assigned to the exploration pool; fast EXP-009 block assigned to EXP-009 with the 2026-09-18T23/2026-09-19T00 exclusion disclosed; forward-paper kill-review block reserved for the single 2026-10-05T05:00Z read; future fast backfill marked unassigned. See [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md).
