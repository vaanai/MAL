# DEC-030: Walk 2's push slot (DEC-021 §8's fifth slot, alpha 0.025, up to 3 books) and the DEC-021 §2 route R1 or R2 (draft)

| Field | Value |
| --- | --- |
| **Status** | **Draft, 2026-10-16. Awaiting quant-proof's review and the owner's line (section 4).** It takes effect on merge, with quant-proof's OK on the exact final head, a reviewer verdict on the PR, and the owner's line. Until the line is in section 4, nothing in this file is in force and no push-family book may register. No hour of walk 2 or any other sealed block was read to write it, and no candidate is named. |
| **Decider** | Vaan (owner) for the route (R1 or R2) and for the slot. The Claude manager runs the rest. |
| **Date** | 2026-10-16 |
| **Builds on** | [DEC-021](DEC-021-champion-challenger.md) §1, §2, §5, §8 and Amendments 1 to 4, [DEC-029](DEC-029-exp026-alpha-slot.md), [DEC-014](DEC-014-holdout-ledger-and-multiplicity.md) (a), (b), [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) (Forward walk 2 row; 2026-10-16 entry), quant-proof's walk-2 pre-registration checklist of 2026-10-16 (two blockers), [push-2026-10-16-round2](../ARTIFACTS/lab/push-2026-10-16-round2.md). |
| **Amends** | DEC-021 §8, by its Amendment 6 (in this PR), for the push family only. Under R1 only: DEC-021 §2, by the same Amendment 6, for push-family books only. |
| **Does not amend** | The promotion gate and its thresholds. The alpha, looks, windows and seals of EXP-022, EXP-024, EXP-025 and EXP-026. DEC-029. DEC-020. DEC-016 Am.2 and Am.3. |

## 0. Why this exists

On 2026-10-16 the owner reserved Forward walk 2 (`[2026-10-16T01, 2026-11-16T01)`, MiScusi job #1036) as the forward test block for the push's next candidate (ledger row; notebook n_aPA3M4wwE7uxXA). Quant-proof's checklist found two blockers to registering any book on it:

1. **No alpha slot is free.** DEC-021 §8's slots 1 to 4 are taken (EXP-022 by Amendment 1, EXP-024 by Amendment 2, EXP-025 by Amendment 3, EXP-026 by Amendment 4 and DEC-029). Amendment 4 and DEC-029 §1 say "A fifth slot needs a new DEC". EXP-022's withdrawal frees no slot in any text. The owner's general 2026-10-16 yes is not a slot line.
2. **DEC-021 §2 asks for a backward-block PASS before registration.** Only named EXPs are exempt (Amendments 1 to 4). A new book needs a DEC that exempts it, or a PASS on a reserved backward block first.

No frozen survivor exists today. This DEC prepares the slot and the route so that a future candidate can register quickly. It claims nothing about any book.

## 1. The slot

- **Slot 5, alpha 0.025.** Its members are the **push family**: at most **3** books, each pre-registered by its own `EXP-###` on walk 2 hours, each frozen by md5, each merged before its counted window's first hour begins in real time (ledger row, Forward walk 2). One EXP is one book; retunes of a book are the same book (DEC-014 clarification). A fourth book needs a new DEC.
- **Holm across the family.** Holm–Bonferroni across the k registered books at family alpha 0.025, on the **day-level p** with the DEC-021 §1 legs (flat 15% fail model and pressure-fail model at slope scale 1): each book's p is the **larger** of its two legs' p, and that decides. At k = 3 rank 1 needs p ≤ 0.00833, rank 2 needs 0.0125, rank 3 needs 0.025; at k = 1 the book needs p ≤ 0.025. Each book's own EXP fixes its day-level p exactly (24 h UTC blocks, W blocks, df = W − 1, as in DEC-021 Amendment 1 §5) and splits its level by Bonferroni if it takes more than one look. The trade-level p is report-only, because day clustering breaks it.
- **The CI90 clause is on top and unadjusted.** The promotion gate's lower 90% CI bound (1,000 draws, seed 1) applies in full to each book, as do the other three gate items and both fail models. Holm only tightens the gate; it never loosens it.
- **The new bound.** With five slots of 0.025, Bonferroni across them, the bound on a false promotion across the October promotion-eligible families rises from **0.10 to 0.125**. This is a real increase in the chance that a book with no edge is promoted by luck. This DEC does not hide it and does not offset it.
- **Controls are not in k.** A reference or control arm that cannot promote is not counted (DEC-014 (b)). A candidate put forward for promotion is.
- **What a PASS leads to.** Only the DEC-018 path and the owner's yes. A paper pass is not live evidence. Size stays governed by DEC-020.

## 2. The DEC-021 §2 question: R1 or R2

Both routes use the slot above. They differ only on §2's reserved-backward-block PASS.

### R1: exempt push-family books from the §2 PASS

- **Reasons.**
  - Walk 2 is itself fresh forward data, not exploration, not seen by the design, and read once. A push-family book's only confirmation is the walk-2 read.
  - The backward blocks are short and low-powered. For RULE B, one block with the cost haircut, gate plus the p < 0.00147 bar, has power about 0.015 (about 1.5%); gate alone 0.133 ([push-2026-10-16-round2](../ARTIFACTS/lab/push-2026-10-16-round2.md), "Confirmation power"). A true edge would almost always fail such a pass, so §2 mostly removes real candidates while adding a day or two.
  - DEC-021 Amendments 1 to 4 already exempted EXP-022, EXP-024, EXP-025 and EXP-026 for similar reasons.
- **Cost.** One fewer independent confirmation. The walk-2 read alone carries the winner's-curse risk: a book chosen as the best of many exploration cells is read once, with no earlier out-of-sample screen to remove the luckiest. The slot's Holm threshold, the day-level p, the CI90 clause and the full gate are the only protection. The bound in section 1 does not change under R1; the weaker screen shows up as a higher share of false passes among the books that reach the read, not in the bound's formula.
- **Rule under R1.** No backward-block PASS is required for a push-family book. Its exploration evidence is in-sample for the book's design and is never cited as confirmation. No sealed block is claimed.

### R2: keep §2

- A push-family book must first PASS a one-shot read on a reserved, unread backward block under the promotion gate, with DEC-014's per-block terms (the block's Part 1 merged first, one block kept, and the book claiming it at p < 0.025/m with m at least 15 per the ledger). This costs about **1 to 2 days** before registration, and its power is low (about 1.5% for RULE B on one block).
- Under R2, the Amendment 6 text for §2 is removed from the PR before merge; only the slot text enters DEC-021.

## 3. Scope and seals

- **H5 domain excluded.** Push-family books may not use the H5 domain: the first 360 s of V-range pools, any join of a synthetic class to an outcome, or BOOST-timing outcomes (the sealed D3 domain, ledger 2026-10-15 entry). The exclusion stands unless quant-proof rules otherwise in writing before registration; if it does, alpha for that book goes to the H5 family under DEC-029's reading and this DEC is amended.
- **EXP-026 is unaffected.** It reads the shadow and refuses walk 2 (EXP-026 sections 3 and 10). Its slot 4, alpha 0.025, one read, and its seals do not change. EXP-024's NOT_DECIDABLE Look 1 is not an end state for D3.
- **Disclosure.** EXP-025's non-owner read of `[2026-10-16T01, 2026-10-24T02)` (ledger rule 3) is disclosed. Its looks ended NOT_DECIDABLE, and EXP-025 reads chain tape only.
- **Per-book terms stay in each EXP.** Each book's EXP pins its counted-window start (the first full UTC hour after its merge, never moved), its stop rule, deadline, cost floor (V pricing, exit lag 2, entry +26.08 bps, sell −16 bps or the larger E1 penalty, as DEC-021 Amendment 1 §4), NOT_DECIDABLE conditions, and kill rule (a FAIL retires the book with no retune and no re-read). This DEC fixes none of them.
- **No claim.** Nothing here says a book makes money. No candidate is named. No book is registered by this DEC.

## 4. The owner's line

The line below holds the owner's choice. Nothing else fills it, and until a line is there, this DEC does not merge and no push-family book registers.

```
OWNER_PUSH_SLOT_CONFIRMED: <PENDING; nothing fills this but the owner's answer>. Route: <R1 | R2 | NO SLOT>.
```

The form to fill: `<date> (owner, in session, asked by <manager>). Route: R1 | R2 | NO SLOT. Question: "<verbatim>". Answer: "<verbatim>". MiScusi notebook <id>.`

The question shown to the owner must state, verbatim:

"This opens a fifth alpha slot of 0.025 for up to three push-family books on walk 2 (a Holm test across the books on the day-level p, with the promotion gate's CI90 clause on top), which raises the bound on a false promotion across October's promotion-eligible families from 0.10 to 0.125. R1 exempts these books from DEC-021 section 2's requirement of a passing reserved backward block before registration: walk 2 is itself fresh forward data and the backward blocks are short and low-powered (about 1.5% power for one block on RULE B), but a book then has one fewer independent confirmation and its winner's-curse risk rests on the walk-2 read alone. R2 keeps the backward-block pass first, which adds about 1 to 2 days and has low power. Which route: R1, R2, or no slot?"

A line whose question lacks that text does not count. An answer "NO SLOT" leaves slot 5 unopened and walk 2 without a registrable owner; this DEC then does not merge.

## 5. What this DEC is not

- **Not a registration.** It names no book and no EXP. Each book still needs its own pre-registration merged before its first counted hour, with quant-proof's OK on its exact head and a reviewer verdict.
- **Not a change to any other family.** EXP-022 (withdrawn), EXP-024, EXP-025 and EXP-026 are unchanged.
- **Not a live authorization.** DEC-018 and the owner's yes govern anything live.

## Sources

- DEC-021 §1, §2, §8, Amendments 1 to 4; DEC-029 (structure, owner-line form, verbatim-question rule); DEC-014 (a), (b); ledger Forward walk 2 row and the 2026-10-16 entry.
- Quant-proof's walk-2 pre-registration checklist, 2026-10-16 (section 0 blockers, section 2 books and Holm).
- push-2026-10-16-round2, "Confirmation power".
- No sealed data, walk 2 hour, forward-paper P&L, or canary or shadow outcome record was opened to write this file.
