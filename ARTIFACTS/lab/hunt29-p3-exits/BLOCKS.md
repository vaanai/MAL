# P3 EXITS: block choice (written by `date -u` 2026-10-24T00:2xZ, BEFORE any pricing or counting)

Exploration only. Nothing here is gate evidence. Same four canonical arena_screen kit blocks as hunt28/l4/BLOCKS.md, all under /data/mal/clean-view:
| Block | Hours | Ledger row (docs/HOLDOUT_LEDGER.md @ origin/main 91544819; last ledger commit 549e40f6, 2026-10-19) | Status |
|---|---|---|---|
| explore-0814 | [2026-08-14T12, 2026-08-28T12), 336 h | "Exploration expansion" | exploration pool |
| fast-pool-2026-09-18T23_2026-09-22T00 | 73 h | "Fast pre-cut" | exploration pool (closed migrate-direct OOS book) |
| oracle-insample-2026-09-22_25 | 79 h | "Oracle in-sample" | exploration pool |
| oracle-live-2026-09-25_27 | 65 h | "Oracle live tape, pre-clean-clock" | exploration pool; NOT a confirmation holdout (reservation withdrawn by #148) |
Total 553 h.

Not touched: fresh-0828 (EXP-028 bank cohort), fresh-0808, fresh-0802 (reserved), fresh-0903 / exp011-0909 (not kit roots), forward-1002, forward walk 2, any October tape, the arena tape; the kit refuses fresh-08*, and its SEALED_HOURS guard (2026-10-02T10 to 2026-10-16T01) is on. Never opened: shadow outputs, decision ledgers, bots.json, runner-status.json, canary/probe outputs, sealed paths.

Multiplicity (prior reads of these exact blocks): many (hunt23, hunt24 l4-audit, hunt26, hunt28 l3 and l4). The FLOOR v2 entries were already selected on these blocks; every positive number below is in-sample and a survivor of this lane's declared cells (see DECLARE.md) on mined data. Winner's curse applies.
