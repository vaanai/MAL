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
| Oracle in-sample | 2026-09-22T00 → 2026-09-25T07 | Oracle | exploration pool | Seen: 972-cell grid in-sample, plus migrate-direct Oracle OOS 2026-09-22T00–10, plus the entry-model B3 lane (`tools/oracle_insample_adapter.py`, `ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md`), which reads only 2026-09-22T00 through 2026-09-25T06 -- hour 07 stays exclusive to the "Oracle live tape" row below, so no hour in this ledger is read from two pool tags at once. |
| Fast pre-cut | 2026-09-19T01 → 2026-09-22T00 | fast | exploration pool | Was the frozen migrate-direct OOS book, now closed. |
| Fast EXP-009 block | `[2026-09-15T12, 2026-09-19T01)` | fast | **EXP-009** | Owned, except the two hours below. The lower bound was fixed at 2026-09-28T20:15Z, replacing "oldest sealed at cap"; the oldest sealed fast hour at that time was 2026-09-18T20, so no hour in the new range below it had been sealed. |
| Fast EXP-009 exclusion | 2026-09-18T23, 2026-09-19T00 | fast | exploration pool | Read by the migrate-direct fast scorer (`mal-fast-oos-score`), disclosed in [EXP-009 Amendment 3](../EXP/EXP-009-migrate-creator-gate-prereg.md). Excluded from EXP-009 scoring. |
| Forward paper, kill review | 2026-09-28T00:00Z → 2026-10-05T05:00Z (9 forward books) | Oracle (forward-paper runner) | kill review | Single read, at the kill-review instant. No interim peeking used for a promote decision. Must join `settlements.jsonl` with `positions.jsonl` for restart-orphaned positions per [DEC-014 Amendment 1](../DEC/DEC-014-holdout-ledger-and-multiplicity.md#amendment-1-2026-09-28--kill-review-must-join-the-offline-settled-orphans). |
| Oracle live tape, pre-clean-clock | 2026-09-25T07 → 2026-09-28T00 (Oracle `sealed/trades`, real `t_recv_ms`) | Oracle | exploration pool | **Not a confirmation holdout.** Briefly reserved for EXP-010 by #147. That reservation was withdrawn by #148 because it broke rule 4: the hours were already sealed, and the existing 9 forward books had already traded them with their flat-model aggregates in the daily scoreboards, including sibling exits on the same migrate entry family. About 36 of the 65 hours also fall in the stale-fill void (2026-09-25T19:00Z–2026-09-27T06:58:12Z). Any trailing-exit study on this block is exploration only and is never evidence for promotion. |
| Fast `[2026-09-09T12, 2026-09-15T12)` | `[2026-09-09T12, 2026-09-15T12)` (6 days) | fast | unassigned | Briefly reserved for EXP-010; released 2026-09-28T20:15Z because the trailing-exit candidate failed its concentration check (ex-top-3 −8.0 SOL; see `ARTIFACTS/lab/exploration-exits-2026-09-28.md` addendum). Still unsealed and unread. |
| Future fast backfill | Older than 2026-09-09T12 | fast | unassigned | To be allocated in ≥6-day blocks, one confirmation test each, as the backfill extends. |

## How to add a block

1. Pick the UTC hour range (inclusive start, exclusive end) and the host that will seal it.
2. Add a row to the table above **before** any hour in the range is sealed or read, with an owner: either a named `EXP-###` or "exploration pool."
3. If a non-owner later reads any hour in an owned block, disclose it as a dated amendment in the owner's `EXP-###` file, and update the block's `Status` cell here to point at that amendment.
4. If an owned block later needs a scope change (extend, split, or shrink), do it by ledger edit plus a note in the relevant `EXP-###` file — not silently.
5. A block never has two owners at once. Reassigning an owner is a ledger edit with the reason recorded in `Status`.

## Changelog

- 2026-09-28 (lane B3): the entry-model exploration lane read the Oracle in-sample block's hours 2026-09-22T00 through 2026-09-25T06 (not T07, which stays exclusive to the Oracle live tape row) alongside the existing fast-box and Oracle live tape exploration pools, for a combined 9-UTC-day leave-one-day-out run. Exploration pool only; disclosed in the Oracle in-sample row's Status above. See `ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md`.
- 2026-09-28T20:30Z: released the EXP-010 reservation of `[2026-09-09T12, 2026-09-15T12)` (the candidate failed its concentration check). The block is unassigned, unsealed and unread.
- 2026-09-28T20:25Z: fixed the EXP-009 block's lower bound at 2026-09-15T12 and reserved `[2026-09-09T12, 2026-09-15T12)` for EXP-010. Both were recorded while the oldest sealed fast hour was 2026-09-18T20, per rule 4.
- 2026-09-28T20:20Z: withdrew the EXP-010 reservation of the Oracle live-tape block (owner review of #147: rule 4 violated, hours already sealed and partly seen, void-heavy). The block goes to the exploration pool. EXP-010's confirmation holdout must be a block registered before it is sealed or read.
- 2026-09-28T20:05Z: reserved the Oracle live-tape block 2026-09-25T07 → 2026-09-28T00 for EXP-010 (trailing-exit confirmation) before any trailing-exit read; prior exposure disclosed in its row.
- 2026-09-28: initial ledger. Oracle in-sample and fast pre-cut blocks assigned to the exploration pool; fast EXP-009 block assigned to EXP-009 with the 2026-09-18T23/2026-09-19T00 exclusion disclosed; forward-paper kill-review block reserved for the single 2026-10-05T05:00Z read; future fast backfill marked unassigned. See [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md).
