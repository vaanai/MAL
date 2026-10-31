# s1 records line: A-Q50, the pre-declared post-read strip repeat audit, for `h5v1q50-boostfloor-s1`

Written 2026-10-31T01:19:35Z (`date -u`, mal-research-0). Records only.

## When it was written

It was written before s1's registration window opens (2026-11-04T00:00:00Z, DEC-031 Amendment 18 item 4) and before s1's clock (2026-11-06T00:00:00Z; slip 2026-11-07T00:00:00Z). So it comes before any s1 outcome, count or read exists. It is outcome-blind.

It was also written after the Q50 read job #1887 was resumed. No Q50 read verdict was seen by the writer.

## Source

The source is QP-RULING-1031-tape-dupes-reads: `/data/mal/hunt-1008/ops/manager18/QP-RULING-1031-tape-dupes-reads.md`, sha256 `c658e35f3e6eace380b1a1d0a4ceafa8e07cce6f020fe8101638fd3ae02cf40b`, written 2026-10-31T00:54Z.

Its section 4.5 reads: "Same shadow code as Q50. Do not change frozen s1 code. **Before each clock, add one records line pre-declaring A-Q50 (items 1-6, with the book's bot_id) for its read**, own QP + reviewer."

`h5v1q60-boostfloor-a1` (Q60) gets no line: it is withdrawn before registration (DEC-031 Amendment 22 item 8.2).

## Cites

- `EXP/EXP-024-note-s1-successor.md` (blob `7015abc95c6657b267baaf660810610364e3f71b`), part A: s1's declared exception to EXP-024 section 3's seal.
- DEC-031 section 2 and Amendments 11, 17, 18 and 22 (#1107).
- The A-Q50 tool PR #1144 (open, `claude/a-q50-audit`).

No book has cleared the promotion gate. Nothing here says any book makes money.

## Read and not opened

- **Read:**
  - the ruling;
  - `EXP/EXP-024-note-s1-successor.md`;
  - DEC-031 Amendments 18 and 22 (text);
  - `ARTIFACTS/lab/s1-registration-record-n3.md`;
  - `tools/h5_shadow.py` at S\* `5c2ab7c64b21358619e37068c6a53e4d183512ea` (blob `7f859b12deef87573a7314e49e125b5121eaa645`, `build_source`);
  - `observe/trade_source.py` at S\* (the SeenSet defaults).
- **Not opened:**
  - any Q50 or s1 shadow dir, strip, ledger, `bots.json`, score, count or read-instant file, read output, job status or log;
  - any fill, exit or P&L.

## Why s1 needs it

The ruling's section 2.3 covers s1 by its words ("Q50 (and s1, Q60)").

s1's shadow is `tools/h5_shadow.py` at S\*. It ingests through `MultiSocketLogsSource` with the SeenSet defaults: 600 s and 500,000 keys (`build_source`, S\* lines 3073-3079; `observe/trade_source.py` lines 352 and 467-468 at S\*). It keeps no per-pool (signature, event_index) filter.

The registered scorer recomputes ssb and nb from the strip's `buys` = [slot, lamports]. These carry no signature, so a repeat can reach a counted trade's pressure inputs at peak load. Under the per-cell intercept refit, the sign of that effect on the deciding pressure leg is not determined (ruling section 3).

The frozen s1 code is not changed (ruling section 4.5).

## The line

> **A-Q50 for `h5v1q50-boostfloor-s1`** (QP-RULING-1031-tape-dupes-reads section 4.2 items 1 to 6, with s1's bot_id; pre-declared before s1's clock; binding on QP's post-PASS check of s1's read):
>
> 1. **When.** Only after the `h5v1q50-boostfloor-s1` read is recorded, and only if it is PASS. A FAIL or NOT_DECIDABLE is final and is never re-opened by this audit (a data defect may block a pass; it never creates one).
> 2. **Input.** The strip records of the read's counted trades only, at the read's exact files (QP's DEC-031 §2 step 1 check, within s1's declared exception, `EXP/EXP-024-note-s1-successor.md` part A, blob `7015abc95c6657b267baaf660810610364e3f71b`). Nothing else from the shadow dir.
> 3. **Repeat definition (outcome-blind).** In one strip's `rows`, a row equal on all four columns (slot, q_pv_pre, q_fv_pre, base_pre) to an EARLIER row, and arriving after a row of a higher slot, is a repeat print. In `buys`, an entry (slot, lamports) that equals an earlier entry and arrives after an entry of a higher slot is a repeat buy if a repeat print exists at that slot (for buys before `from_slot`, where no rows exist, the late exact repeat alone suffices).
> 4. **Counts first.** Per counted trade and leg: repeat buys in the landing slot, repeat buys in [landing - round(2 / sps), landing], and whether the entry or exit state came from `post_last` while the strip's last row is a repeat print. Totals. Output: counts and trade ids only.
> 5. **If every count is 0:** the read's pressure leg stands; QP records "A-Q50 clean".
> 6. **If any count is non-zero:** recompute the affected trades' ssb / nb without the repeat buys, re-run the refit pressure leg with the registered cost functions (same intercept rule, target 0.289), report-only. A trade whose state depends on a repeated last print is valued at its total loss (stake + send). QP's OK (and so every C0 step) then requires all four gate items to hold on BOTH the read's recorded cells AND this corrected pressure leg. The recorded read verdict is not changed (one read, final).
>
> **Which read.** "The read" is s1's screen read (DEC-031 Amendment 22 item 2), and also its confirmation read if s1 reaches one, each on its own counted trades. For a confirmation PASS, "every C0 step" reads "every step that PASS allows" (C1 included).
>
> **QP errata to items 4 and 6** (quant-proof, PR #1144 comment 6537179643, 2026-10-31T01:28:27Z; outcome-blind; each only adds a condition to a PASS):
> - **E1.** In item 6, "its total loss (stake + send)" is the registered strand-class value of a trip that ends with nothing: stake + one send + the stranded rent 2,039,280 lamports (`tools/arena_h5_cost.py` `_trip`, `RENT_STRANDED`), i.e. 302,544,280 lamports at the 0.30 SOL deciding stake, under both strand rules.
> - **E2.** In item 4, a state that came from `post_last` whose slot is below the strip's `from_slot` counts as a state from a repeated last print.
> - **E3.** In item 6, the trades valued at total loss are valued so in the deciding flat cells too (both strand rules), and QP's OK also requires all four gate items to hold on those corrected flat cells.

## How the line differs from the ruling's items 1 to 6

- `Q50` is replaced by `h5v1q50-boostfloor-s1`.
- "within Q50's declared exception" is replaced by s1's own exception (part A of the s1 note).
- **The "Which read" sentence is this line's addition.** The ruling says "for its read". Under Amendment 22, s1 has two gate reads, a screen and a confirmation. The sentence only adds a condition to a PASS and never creates one. QP ruled to KEEP it (PR #1145, QP comment 6537185786, ruling (b)).
- **The QP errata E1 to E3 (comment 6537179643) are carried.** They are pasted verbatim from QP comment 6537185786. Items 3 to 6 above them stay word for word.

Items 3 to 6 are the ruling's text word for word.

## Tool (information; not one of items 1 to 6)

The audit tool is `tools/q50_strip_dup_audit.py` (PR #1144, open). It already takes `--bot h5v1q50-boostfloor-s1` with s1's root `/home/claude/s1home/data/arena` and its `h5-q50s1-*` shadow dirs.

Its s1 path covers a screen read made by `tools/arena_h5_read.py`: all counted trades before the recorded instant. A confirmation read, with trades in [T_s, B), needs an adaptation.

s1's read pack is owed before 2026-11-11T00:00Z (`ARTIFACTS/lab/s1-registration-record-n3.md`). It names the exact command and that adaptation. Each change needs QP OK and a reviewer verdict at the exact head, and must be merged before the tool reads any s1 strip.

## Takes effect, and the fallback

The line takes effect on merge, with QP OK on the exact head and a reviewer verdict on the PR. It must merge **before s1's clock 2026-11-06T00:00:00Z**. On the Amendment 18 slip, that becomes the slipped clock.

If it has not merged by then, the manager tells QP before the clock. In that case, no s1 PASS goes to QP's OK or to any C0 step without this audit or a QP ruling in its place. A slip never drops the line. A successor registered under another bot_id (for example `-s2`) needs its own line before its own clock.

No book has cleared the promotion gate.
