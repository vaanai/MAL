# DRAFT corrections to the Q50 DEC-032 copy (QP-RULING-1025-q50-seal-live, ruling item 4 and C8). NOT IN FORCE, NOT A DEC.

The Q50 DEC copy is a desk file only (`/data/mal/hunt-1008/push-1016/hunt29/p1/DEC-copy-h5v1q50-boostfloor-a1.md`); it is not in the repo. This file holds the corrected text to paste into it when it is opened as a numbered DEC. It is deliberately outside `DEC/` (the executor globs `DEC/DEC-*canary*.md` for the owner line). Drafted 2026-10-25; records only; fields in angle brackets are filled at the read.

## Replaces §11 item 9 (seal statement)
The draft's clause "the D2 seal of the Forward walk row's domains where they overlap" is wrong and is deleted: D2/D3/D4 cover [2026-10-02T10, 2026-10-16T01) and that tip tape, which do not overlap pools with s0 on or after 2026-10-25. Use, with QP's fields filled at the read:

> Seal check (DEC-032 §1 item 3), checked <date -u> on install sha <sha>:
> - The book's pools are V-range PumpSwap pools in their first 360 s on October hours, inside the EXP-024 §3 seal (Look-2 class) until EXP-024's end state, first listed 2026-11-14T00:00Z.
> - They are inside a declared observation that covers live outcomes: the EXP-024 "Note under Amendment 10" (#851, 556f7d8c), scope (a), "its live fills, exits, wallet deltas and realized P&L". It covers bot_id h5v1q50-boostfloor-a1 only (rule sha256 b3f8ecbf…, frozen_sha a5f41370, clock 1792886400000) and pools with s0 in [2026-10-25T00:00:00Z, 2026-11-06T00:00:00Z).
> - The executor is tools/h5_arena_executor.py at <sha>. That sha contains 556f7d8c and the flip e2471186 (#852), with FEED_NOTE_BOOKS = {h5v1q50-boostfloor-a1} and end_ms <= 1793923200000. The md5 decision-equivalence replay is EQUAL at <sha> (job <#>).
> - Joins are limited to the Note's (b) items 1 to 4 until 2026-11-14T00:00Z. No outcome is split by synthetic class or by the Q* = 40 v1 trigger.
> - No other open seal applies. D2/D3/D4 cover [2026-10-02T10, 2026-10-16T01) and that tip tape. The C1-NF seal is [2026-10-10T00, 2026-10-24T02). CAP-PICK is moot (DEC-031 §3.5). No DBC, HV4 or arena-tape output is read by the executor, twin writer, parity tool or tie-out. Ledger D2/D3 checked <date>; the #1059 breach is disclosed and is not a release.
> - A send on a pool outside that window, or any join outside (b), is a seal exposure and a DEC-032 §5 item 5 hard halt.
> - EXP-024 is neither spent nor tainted: its read ended NOT_DECIDABLE on 2026-10-13 and Look 2 does not run. The observation is disclosed by Note (g).

## New sentence (scope; add to §11, ruling C8 (b))
> DEC-031 §3.5's "the live canary" and §4's "outputs of the H5 executor" mean the DEC-024 executor and canary. The outputs of this C0 (`h5v1q50-boostfloor-a1` under `tools/h5_arena_executor.py`) are observable under the EXP-024 Note under Amendment 10 (a) and this copy.

Follow-up edit at the first send (not in this PR): docs/HANDOFF.md §2 and the SEALS-BRIEF line "canary outputs are never opened" should say DEC-024's canary.

## New §11 declarations
**Impact disclosure (C8 (c)).** EXP-024 §3's tape-impact bullet named DEC-024's 0.02 SOL. A C0 buy of 0.05 SOL is about 0.1% of Q and about 0.2% in price on a pool with Q of about 45 SOL (QP estimate). It is chain truth, disclosed and not corrected.

**Fee.** Manager decision: C0 uses the compiled default, 55,000 buy / 55,000 sell / 150,000 escalated priority plus the 5,000 base fee per send. `FILL:fee` cites `tools/h5_executor.py:267-268@<install sha>`; the owner ask names those numbers. The "p155 pin" in HANDOFF §4 is stale for Q50. The gap to the registered DEC-031-rev2 stack (505,000 per send) stays declared in item 6.

**Install and replay.** Install sha 0526d5cd contains #851 and #852; md5 EQUAL, job #1481 (Run A 137/137, Run B 101/101). Any other install sha needs its own EQUAL replay before the first send.

**Sell-simulate mints (C7).** From outside the Look-2 class (a September graduation or a non-V-range pool). Pass/fail and instruction decode only, not proceeds.

**OPEN, to be settled before parity is used for C0 -> C1 (declare with bps, or fix):** the twin does not remove the canary's own trades from the paper pool states, so it double-counts their impact. QP estimate (not measured): about 20 bps per side at 0.05 SOL, about 40 bps of stake per round trip in live's favour, loosening the -100 bps match test by about 40%.

**Disclosure line for the Q50 confirmation read.** From the first send, the twin rows are observable in real time under the EXP-024 Note under Amendment 10 (a), so the confirmation read is no longer blind to those positions. Declared and allowed (DEC-032 §7; DEC-031 Am.3 item 6 "In parallel"); it does not change the read. The owner is told.
