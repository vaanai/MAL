# Q50 records line: A-Q50 extended to Q50's own confirmation read (`h5v1q50-boostfloor-a1`)

Written 2026-10-31T01:33:38Z (`date -u`, mal-research-0). Records only.

## When it was written

It was written before any Q50 confirmation instant can exist: the earliest is 2026-11-04T00:00:00Z (see "Deadline"). No Q50 read verdict was seen by the writer.

It is outcome-blind. It only adds a condition to a PASS and never creates one. No book has cleared the promotion gate, and nothing here says any book makes money.

## Source

- **The ruling:** QP-RULING-1031-tape-dupes-reads, `/data/mal/hunt-1008/ops/manager18/QP-RULING-1031-tape-dupes-reads.md`, sha256 `c658e35f3e6eace380b1a1d0a4ceafa8e07cce6f020fe8101638fd3ae02cf40b`. Its section 4.2 pre-declares A-Q50 for "the Q50 read" only.
- **The QP errata E1 to E3** to items 4 and 6: PR #1144 comment 6537179643, 2026-10-31T01:28:27Z.
- **QP's request for this line:** PR #1145 comment 6537185786, "Manager note". Its words: "Q50 has the same structure (DEC-031: one screen and one confirmation per bot_id). The ruling's A-Q50 names only 'the Q50 read'. Before any Q50 confirmation instant, add a records line extending A-Q50 (with E1 to E3) to Q50's confirmation read, for the reason in ruling (b)."
- **Ruling (b),** from the same comment, written for s1 and applied here to Q50: a confirmation PASS opens C1, a larger money step than C0. Its counted trades come from the same frozen shadow code, so the exposure in ruling section 2.3 is the same.

## Cites

- DEC-031 section 2 step 4 (the confirmation).
- DEC-031 Amendment 11 item 3. For this book, the confirmation counts "at least 100 counted trades (item 1) decided after the screen read's instant, on at least 5 further UTC dates", read at the first 00:00Z boundary that meets this.
- DEC-031 Amendment 22 item 8.3: "Its confirmation window is capped at 2026-11-06T00:00Z (R1)."
- The s1 line, PR #1145 (`ARTIFACTS/lab/s1-a-q50-predeclare.md`).
- The tool, PR #1144.

## Read and not opened

- **Read:** the ruling; the QP comments named above; DEC-031 Amendments 11 and 22 (text).
- **Not opened:**
  - any Q50 shadow dir, strip, ledger, `bots.json`, score, count or instant file, read output, job status or log;
  - any fill, exit or P&L.

## The line

> **A-Q50 for the confirmation read of `h5v1q50-boostfloor-a1`** (QP-RULING-1031-tape-dupes-reads section 4.2 items 1 to 6, with QP errata E1 to E3; pre-declared before any Q50 confirmation instant; binding on QP's post-PASS check of Q50's confirmation read, on that read's own counted trades):
>
> 1. **When.** Only after the Q50 confirmation read is recorded, and only if it is PASS. A FAIL or NOT_DECIDABLE is final and is never re-opened by this audit (a data defect may block a pass; it never creates one).
> 2. **Input.** The strip records of the read's counted trades only, at the read's exact files (QP's DEC-031 §2 step 1 check, within Q50's declared exception). Nothing else from the shadow dir.
> 3. **Repeat definition (outcome-blind).** In one strip's `rows`, a row equal on all four columns (slot, q_pv_pre, q_fv_pre, base_pre) to an EARLIER row, and arriving after a row of a higher slot, is a repeat print. In `buys`, an entry (slot, lamports) that equals an earlier entry and arrives after an entry of a higher slot is a repeat buy if a repeat print exists at that slot (for buys before `from_slot`, where no rows exist, the late exact repeat alone suffices).
> 4. **Counts first.** Per counted trade and leg: repeat buys in the landing slot, repeat buys in [landing - round(2 / sps), landing], and whether the entry or exit state came from `post_last` while the strip's last row is a repeat print. Totals. Output: counts and trade ids only.
> 5. **If every count is 0:** the read's pressure leg stands; QP records "A-Q50 clean".
> 6. **If any count is non-zero:** recompute the affected trades' ssb / nb without the repeat buys, re-run the refit pressure leg with the registered cost functions (same intercept rule, target 0.289), report-only. A trade whose state depends on a repeated last print is valued at its total loss (stake + send). QP's OK (and so every C0 step) then requires all four gate items to hold on BOTH the read's recorded cells AND this corrected pressure leg. The recorded read verdict is not changed (one read, final).
>
> **For this read**, "every C0 step" in item 6 reads "every step that the confirmation PASS allows (C1 included)".
>
> **QP errata to items 4 and 6** (quant-proof, PR #1144 comment 6537179643, 2026-10-31T01:28:27Z; outcome-blind; each only adds a condition to a PASS):
> - **E1.** In item 6, "its total loss (stake + send)" is the registered strand-class value of a trip that ends with nothing: stake + one send + the stranded rent 2,039,280 lamports (`tools/arena_h5_cost.py` `_trip`, `RENT_STRANDED`), i.e. 302,544,280 lamports at the 0.30 SOL deciding stake, under both strand rules.
> - **E2.** In item 4, a state that came from `post_last` whose slot is below the strip's `from_slot` counts as a state from a repeated last print.
> - **E3.** In item 6, the trades valued at total loss are valued so in the deciding flat cells too (both strand rules), and QP's OK also requires all four gate items to hold on those corrected flat cells.

## How the line differs from the ruling's items 1 to 6

- **Item 1:** "the Q50 read" becomes "the Q50 confirmation read".
- **Items 2 to 6** are the ruling's text word for word. Item 2's "Q50's declared exception" is Q50's own, so it is unchanged.
- **The "For this read" sentence** applies item 6's money condition to the step that a confirmation PASS opens. This is the same substitution as ruling (b) made for s1.
- **The QP errata E1 to E3** are pasted verbatim from QP comment 6537185786. They carry comment 6537179643 on PR #1144.

The A-Q50 of Q50's screen read is the ruling's own section 4.2, with E1 to E3. This line does not change it.

## Tool (information; not one of items 1 to 6)

`tools/q50_strip_dup_audit.py` (PR #1144, open) audits the trade set of a read made by `tools/arena_h5_read.py`: all counted trades before the recorded instant, which is the screen read's set.

The confirmation read's counted trades are those decided in [T_s, B), where T_s is the screen read's instant. Auditing them needs an adaptation, which goes in the confirmation read's own pack. That adaptation needs QP OK and a reviewer verdict at its exact head, and must be merged before it reads any Q50 strip. Until then, no Q50 confirmation PASS goes to QP's OK or to C1.

## Deadline, and the fallback

The line takes effect on merge, with QP OK on the exact head and a reviewer verdict on the PR.

**Merge before 2026-11-04T00:00:00Z.** That is the earliest possible Q50 confirmation instant: the earliest screen boundary is 2026-10-30T00:00Z (Amendment 11 item 5), and the confirmation needs at least 5 further UTC dates after it. The deadline is computed from the rule alone, not from any Q50 count, and it holds for any later screen instant. The confirmation window ends at 2026-11-06T00:00Z (Amendment 22 item 8.3).

If the line has not merged by then, the manager tells QP before that instant. In that case, no Q50 confirmation PASS goes to QP's OK or to any step it allows without this audit or a QP ruling in its place.

No book has cleared the promotion gate.
