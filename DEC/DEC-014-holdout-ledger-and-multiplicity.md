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

When k books or cells are read together at one review (for example, the 9 forward-paper books read at the 2026-10-05T05:00:00Z kill review, or any EXP-009 cells beyond its single pre-registered primary cell), a book or cell promotes only if it clears the existing gate **and** passes a Holm–Bonferroni step-down at family α = 0.05, applied to the one-sided bootstrap test of mean SOL/trade > 0.

- The family is the **candidate** books or cells only. Reference cells that cannot promote (e.g. EXP-009 `gate_off`) are not counted in k. A single pre-registered primary cell read alone is k = 1 (no correction); secondary cells join the family only if any of them is put forward for promotion.
- The test statistic is the bootstrap p-value: the share of the seed-1 bootstrap-resampled means that are ≤ 0 (10,000 draws for a multiplicity-tested read; see below). Sort the k books' p-values ascending: p₁ ≤ p₂ ≤ ... ≤ pₖ. Compare p₁ to α/k, p₂ to α/(k−1), and so on; a book at rank i passes only if pᵢ ≤ α/(k−i+1) **and** every book ranked before it also passed its own threshold (standard Holm step-down — one failure below a rank stops promotion for that rank and everything below it).
- With 1,000 bootstrap draws (seed 1), the smallest resolvable p-value is 0.001 (1 in 1,000). For k = 9, the first Holm threshold is α/9 = 0.05/9 ≈ 0.0056 — above the 0.001 resolution floor, so it is resolvable at 1,000 draws (at most 5 of 1,000 means ≤ 0), but only coarsely: a book near the boundary sits on a handful of draws. **Recommendation: 10,000 draws, seed 1, for any multiplicity-tested read**, giving a resolution floor of 0.0001, comfortably below 0.0056.
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

## Amendment 1 (2026-09-28) — kill review must join the offline-settled orphans

A runner restart drops any position still open in `_Ledger.open` at that moment: `positions.jsonl` gets an "open" row and never a matching "close" row, silently, for every book (#138, `tools/forward_paper_settle_orphans.py`). `tools/forward_paper_settle_orphans.py` replays the sealed tape offline and writes the missing close as a row in `settlements.jsonl`, flagged `settled_offline: true` (or `settled_offline: false` with a `settle_error`, if that one orphan's replay itself fails — one bad orphan no longer aborts settlement for the rest, see the same PR).

The 2026-10-05T05:00:00Z kill review, and any promotion read under this DEC, **must**:

1. join `settlements.jsonl` with `positions.jsonl` on `(ledger, book, mint, decision_t_ms)`, so a restart-orphaned position's real close (offline-settled) is counted instead of being silently dropped from the book;
2. deduplicate on that same key — a position closes exactly once, whichever source (live `close` row or offline settlement) provides it;
3. report, per book, the settled-offline count and the settle-failed count (rows with `settled_offline: false`) alongside the gate numbers, so a reviewer can see how much of a book's read was reconstructed offline versus closed live, and how many orphans could not be settled at all.

This does not change the four base gate thresholds or the Holm step-down in (b). It closes a gap in what "the book" means when a runner restarted mid-window: without this join, a restart-heavy window undercounts trades and is biased toward shorter holds (a position still open at a restart is disproportionately likely to have been a longer hold).

## Amendment 2 (2026-09-28) — the pressure leg is never relaxed; NOT_DECIDABLE

`tools/forward_paper.py` bakes only the flat 15% fail model into `pnl_lamports` (`_landing_failed`, a per-attempt coin flip). Nothing on `positions.jsonl` carried a pressure-fail counterfactual until `tools/forward_paper_pressure_stamp.py` (offline, read-only, snapshot-only — same pattern as `tools/forward_paper_settle_orphans.py`), which computes `pressure_scale_1_pnl_lamports` (gate) and `pressure_scale_2_pnl_lamports` (reporting only) per close/miss/settled row and writes `pressure.jsonl`, keyed the same as everything else in this DEC: `(ledger, book, mint, decision_t_ms)`.

`tools/kill_review.py` (#140), the designated 2026-10-05T05:00:00Z scorer, joins `--pressure pressure.jsonl` onto its already-deduplicated `positions.jsonl` + `settlements.jsonl` (#141) rows on that key. This does **not** loosen the promotion gate's requirement to clear both the flat and pressure-fail models — it can only make that requirement checkable. A book's pressure leg is never relaxed to reach a verdict:

- A book gets the status **NOT_DECIDABLE** — never PROMOTE, and never a plain KILL that could be misread as "measured on both legs and found wanting" — if any row counted as a flat trade lacks `pressure_scale_1_pnl_lamports`, or carries a `pressure_error` from the stamp tool (e.g. a `flat_15pct_landing` miss row, which cannot be reconstructed offline: the counterfactual "if it had landed" exit needs `size_lamports`/`exit_rule`/`applied_latency_ms`, none of which are logged on that row shape — see the stamp tool's module docstring).
- The same NOT_DECIDABLE status applies to a book with an unresolved restart-orphan (an `open` row with no matching `close` and no settlement row of *either* outcome for its key) or any `settle_failed` settlement row — missing data, not a zero, same rule as Amendment 1.
- A book only reads PROMOTE or KILL once its pressure coverage is complete and it has no unresolved orphan or settle_failed row. `tools/kill_review.py`'s verdict line reports all three buckets: `VERDICT: PROMOTE=<books or NONE>; NOT_DECIDABLE=<books>; KILL=<books>`.

If the pressure stamp cannot cover a book by 2026-10-05T05:00:00Z, that book is NOT_DECIDABLE at the kill review — it does not promote on the flat leg alone, and it is not scored as a failure it was never actually measured against.

## Amendment 3 (2026-09-28) — pressure-leg start instant; counterfactual flat-fail misses now reconstructable

[#145](https://github.com/vaanai/MAL/pull/145) (`d7485d2`) makes the live runner log the discarded fill on a flat-fail `miss` row (`counterfactual_fill: true` plus `applied_latency_ms`, `size_lamports`, `exit_rule`, `entry_venue`, `entry_spot_sol`, `entry_tokens_raw`, `entry_t_ms` — the same field names an `open` row carries). `tools/forward_paper_pressure_stamp.py` now reconstructs that discarded fill's exit offline, through the same `reconstruct_fill` function `tools/forward_paper_settle_orphans.py` uses to settle a restart orphan (`try_entry`, cross-checked against the logged `entry_venue`/`entry_tokens_raw`, then the same exit branch `_try_exit` uses: `LadderRule` → `simulate_ladder`, else `simulate_exit`), and mixes the resulting would-be net with the pressure `p_fail` the same way a real send is. Amendment 2's `flat_fail_miss_unreconstructable` `pressure_error` now applies only to a flat-fail miss row written by a runner build **before** `d7485d2` — one that never logged the discarded fill.

For the 2026-10-05 kill review, the pressure-fail leg is scored over the full UTC days that begin at or after the first 00:00:00Z runner restart running code ≥ d7485d2 (#145). The flat leg is scored from the 2026-09-28T00:00:00Z clean clock as before. Each leg must independently meet the gate (≥5 UTC days etc.). Days before that restart are excluded from the pressure leg, and that exclusion is not a pass. If the pressure leg has <5 eligible days, the book is NOT_DECIDABLE.

`tools/kill_review.py --pressure-from-ms` implements exactly that: the pressure leg only uses trades whose `decision_t_ms` ≥ that instant; the flat leg is unchanged. A book whose pressure leg has fewer than 5 eligible UTC days after that cut is NOT_DECIDABLE, same status as incomplete pressure coverage — never PROMOTE, never a plain KILL.
