# DEC-034: Arena live canary for the Q50 successor `h5v1q50-boostfloor-s1`, copy of DEC-032 with Amendment 2 item 12 (rung carry-over down only, C1 -> C2) (SKELETON DRAFT)

| Field | Value |
| --- | --- |
| **Status** | **SKELETON DRAFT, NOT IN FORCE.** Opened 2026-10-27T20:00:08Z (`date -u` on mal-research-0; revised 2026-10-27T20:18:49Z on QP-RULING-1027-s1) as DEC-034, the next free DEC number at origin/main `c8fcf761` (no DEC-034 file, branch or open PR found). Records only. This is the skeleton of the successor's own DEC-032 copy (DEC-032 Amendment 2 item 12.1, as corrected by its Erratum 1). The full copy is made at fill time by copying DEC-033 at its merged head and applying the deltas of section 6; until then every `FILL:` below is open. It becomes a canary DEC only when every precondition of section 1 holds for one of its routes, every fill is made, QP OKs the exact head, a reviewer verdict is on the PR, and the owner's own lines are added from the owner's own messages. No book has cleared the promotion gate. Nothing here is a profit claim. |
| **Decider** | Vaan (owner), for each live send, wallet funding, rung and ceiling. No agent message, manager message or PR text is the owner's consent. |
| **Builds on** | [DEC-032](DEC-032-arena-canary-template.md) (body, Amendments 1, 2 with Erratum 1, 3 and 4's pattern); [DEC-033](DEC-033-h5v1q50-canary.md) (Q50's copy: its section 9a fills, section 11 declarations, Amendments 2 to 4 as copied); [DEC-031](DEC-031-forward-paper-arena.md) §2, Amendment 3 items 2, 5, 6 and Amendment 17 (the successor's registration path); EXP-024 "Note under Amendment 10 (successor `h5v1q50-boostfloor-s1`)" parts A and B (`EXP/EXP-024-note-s1-successor.md`); QP-RULING-1024-successor R7, R8 |
| **Does not amend** | The promotion gate and its thresholds. DEC-031's deciding cell, one read per `bot_id`, confirmation rule, c, N or taint list. DEC-032's limits, halts P1 to P7, rungs, caps, worst cases or step rules. DEC-033 and Q50's canary. Any seal. |

## 0. What this is and is not
- **Is:** the successor's own DEC-032 copy (Amendment 2 item 12.1). It lets the BOOST-floor line's live execution measurement continue after Q50's `end_ms` (clamped to 2026-11-06T00:00Z) on the successor book, and it is the only place where the C2 rung (0.30 SOL) can be reached (Amendment 2 items 7 and 12).
- **Is not:** gate evidence, a promotion or a profit claim. Every report about it is labelled "unpromoted execution measurement". A rung is never cited as evidence of edge (Amendment 2 item 5).
- **Carry-over is down only** (Amendment 2 item 12.3): the successor may start at the rung Q50 held at its end, or lower, never higher, and only on the conditions of section 2.

## 1. Book and routes
**The book.** `h5v1q50-boostfloor-s1`, rule `H5-BOOSTFLOOR-Q50 v1` (sha256 `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4`), `frozen_sha` S\* `5c2ab7c64b21358619e37068c6a53e4d183512ea` (tag `q50-succ-base-1`), `clock_start_ms` 1793923200000 (2026-11-06T00:00:00Z), book end E_s1 = 1795132800000 (2026-11-20T00:00:00Z; slip: clock 2026-11-07T00Z, E_s1 2026-11-21T00Z), deciding cell 1.9 s at 0.30 SOL, rent charged (DEC-031 Amendment 18).

A live start uses exactly one of these routes. Each needs every DEC-032 §1 item except the one the route names, and the coverage of EXP-024's successor note part B (executor change merged, md5 EQUAL at its sha).

| Route | When | Starting rung | Gate basis |
| --- | --- | --- | --- |
| **R-C (carry-over)** | Q50's DEC-031 §2 step 4 confirmation read is a **PASS** at p at most 0.05/c with QP OK on its exact files, and Q50's canary held rung r at its end (C0 or C1) | at most r, never higher (item 12.3) | Q50's confirmation PASS, with quant-proof's statement in its OK on this copy that S\*'s decision path is identical to F50's: the shadow decision path (AST identity test at S\*, seal grid, md5 #1548 EQUAL) and the executor's `decide_row` for the successor's row (its source-identity list and md5 replay EQUAL 101/101, plan C6/C7.2) (DEC-032 Amendment 2 Erratum 1 item 4; QP-RULING-1027-s1 answer 4). Code identity says nothing about a regime change between Q50's window and the successor's |
| **R-0 (own screen)** | no Q50 confirmation PASS (Q50 FAIL, NOT_DECIDABLE, unread, screen PASS only, or confirmation not PASS) | C0 only | the successor's own DEC-031 §2 screen PASS under Amendment 11 as DEC-031 Amendment 18 item 7 applies it; earliest read boundary 2026-11-11T00:00:00Z |

- **A Q50 screen PASS alone carries nothing** (Erratum 1 item 4). Under R-0 the C0 owner OK can be the standing line of DEC-032 Amendment 1 item 3 (it covers any book that meets every §1 precondition at C0), **except after a Q50 FAIL**: then the standing line is not used, and the owner is asked afresh, with Q50's FAIL stated in the question (QP-RULING-1027-s1 (c); DEC-031 Amendment 17 item 4). The manager tells the owner in one line whenever the standing line is used.
- **R-C rests on Q50's evidence, not the successor's.** The successor's own screen read still runs at its own instant (Amendment 11 as applied) and, if it passes, enters its own confirmation (DEC-031 Amendment 3 item 2). A successor FAIL or NOT_DECIDABLE ends the successor's canary at the next 00:00Z (DEC-032 §7), whatever route it started on.
- **A successor step up from C0 to C1 under R-0** follows DEC-032 Amendment 2 item 5 "Step C0 -> C1" with the successor's own confirmation PASS; at E_s1 = 11-20T00Z that confirmation needs 100 counted trades on 5 further dates after a screen read at or after 11-11T00Z, so it is at the edge of the window and may not occur.

## 2. Rung carry-over (Amendment 2 item 12.3, with Erratum 1)
All of these, recorded in this copy before the first send:
1. Q50's confirmation PASS (report path and sha256, p, c, 0.05/c, QP comment id): `FILL:q50_confirmation`.
2. Q50's rung at its end, from Q50's executor's own hash-chained rung record (the last record before `end_ms`), read as structure only (rung name and instant; no P&L): `FILL:q50_end_rung`. A Q50 step-down or stop after its last step-up lowers it; a Q50 hard halt at its end means C0 at most, and an unresolved (O) halt means no carry-over until the owner clears it.
3. The successor starts at `FILL:start_rung` = at most item 2's rung.
4. **Lifetime accounting starts again** (item 12.3): committed loss, intraday cap and lifetime cap count from the successor's first send. The wallet is one wallet (Amendment 2 item 1): the owner chooses a fresh wallet, or Q50's wallet after Q50's final daily tie-out is signed off with residual 0 (`FILL:wallet_choice`). The tie-out baseline is the balance at the successor's first send, reconciled to the last signed-off balance. Owner deposits needed by the rung are counted only from chain transfers from the pinned owner addresses (`FILL:owner_deposit_addresses`); Q50's realized gains never count toward them.
5. Parity and halts start fresh: section 5 of the template ("no window reset") runs from the successor's first send. Q50's live trips are reported beside the successor's, never counted toward the successor's parity windows, steps or loss guards (item 12.4).
6. The worst case is stated afresh in the successor's own owner line (section 4).
7. A carried rung above C0 also needs at the instant of the first send: the sell builder unchanged (`SELL_BUILDER_PIN` re-checked), the twin priced at the rung's stake (Amendment 2 item 6), per-attempt pressure p in the parity tool (DEC-033 §11 item 5: "required before C1"), and the twin double-count of DEC-033 §11 item 19 settled or declared with its bps.

## 3. Step C1 -> C2 (Amendment 2 items 5 and 12.4; copied, with the successor's book)
Only in this book, only under its own owner line naming C2. The checks, all at one instant, computed by the executor from the sources of Amendment 2 item 9 (chain, or files the manager account cannot write):
1. At least **30** eligible trips at the C1 stake (0.15 SOL) in this book, and at least one full UTC day at C1 signed off with residual 0.
2. "Live fills match paper" still holds over all eligible trips of this book since its first send (DEC-032 §5; no window reset).
3. Trailing fill gap over the last 30 eligible trips, all at C1: mean d_i at or above **-100 bps**; mean g_entry at most **+100 bps** (with the declared +26 bps bias included); cumulative mean exit gap at most **100 bps**.
4. No stop-class halt since this book's first send, and no soft halt at C1.
5. Tie-outs: every closed UTC day since the first send signed off with residual 0 by the Helm-installed tie-out unit, at least 5 days, and the executor's own chain tie-out at the step instant finds residual 0.
6. **Loss guards, not edge tests:** this book's chain total since its first send **above 0**; the chain total of its C1 trips without their 3 best trips at least **-0.15 SOL**. Neither is evidence of edge, and no report cites the step as such.
7. **This book's own gate read PASS** (item 12.4): at least 100 counted trades on at least 5 UTC dates of its own clock (Amendment 11 as DEC-031 Amendment 18 item 7 applies it), both fail models, deciding cell at 0.30 SOL, with quant-proof's OK on its exact files; impact at 0.30 SOL is that read's deciding cell. The PASS enters confirmation and adds 1 to c (DEC-031 Amendment 3 item 2). **p rule (QP-RULING-1027-s1, answer 3: the stricter reading binds).** C2 also needs this read's one-sided p (10,000 draws, seed 1, the larger of the two fail models) at most 0.05/c, with c counting this book, on this book's trades only. That is a minimum, not a confirmation: C2 stays a loss-guarded execution step and is never evidence of edge.
8. Owner deposits of at least **2.00 SOL** (chain transfers from the pinned owner addresses); the floor check passes for one C2 buy with 4 open.
9. The sell builder unchanged, and at least one full UTC day remaining before `end_ms`.

C2's column is DEC-032 Amendment 2 item 3's, unchanged: stake 0.30 SOL, max open 4, 50 buy attempts a day, daily trigger 0.60 SOL, intraday cap 1.40 SOL, lifetime cap 1.70 SOL, slack 0.15 SOL, worst case 1.85 SOL, deposits 2.00 SOL, floor deposits - 1.85. Pause, step-down and stop rules are Amendment 2 item 5's, with "C1 or C2" as written; there is no automatic step back up after a step down.

**What C2 can reach in time (stated so that nobody plans on a rung that cannot occur).** Earliest screen boundary 2026-11-11T00:00Z; the read runs once every attempt before it has closed, then quant-proof. With 30 C1 trips and a signed C1 day in hand by then (R-C at C1 from 11-06), C2 could start about 2026-11-12, and `end_ms` is at most E_s1 = 2026-11-20T00Z minus the one-day rule: at most about 7 days at C2. Under R-0 C2 cannot occur in this book (C1 needs the successor's own confirmation first). If the successor's read is NOT_DECIDABLE (fewer than 100 counted trades on 5 dates by E_s1), C2 cannot occur. The `bots.json`-pinned H (14) is never extended for this.

## 4. Owner lines (none written here)
Recorded only from the owner's own message, verbatim, with question, answer and notebook id, in the form of DEC-033 §10a and "Owner ladder pre-approval". The drafts below are what the manager would ask the owner to send; they are not approvals.

- Route R-C at C1 with C2 pre-approved (the owner's line for item 12.4; one line, sent exactly):
  `OWNER_LADDER_PREAPPROVED: book=h5v1q50-boostfloor-s1 rungs=C1,C2 stake_sol=0.15,0.30 max_open=3,4 daily_trigger_sol=0.30,0.60 intraday_cap_sol=0.55,1.40 lifetime_cap_sol=0.70,1.70 worst_case_sol=0.80,1.85 deposits_sol=1.00,2.00 rules=DEC-032-Am2 helm_ceiling=C2`
- Route R-C at C0 or R-0 with C1 pre-approved:
  `OWNER_LADDER_PREAPPROVED: book=h5v1q50-boostfloor-s1 rungs=C0,C1 stake_sol=0.05,0.15 max_open=2,3 daily_trigger_sol=0.10,0.30 intraday_cap_sol=0.20,0.55 lifetime_cap_sol=0.20,0.70 worst_case_sol=0.25,0.80 deposits_sol=0.35,1.00 rules=DEC-032-Am2 helm_ceiling=C1`
- Q50's line (notebook n_p5ZEchJioV1W7A) names `book=h5v1q50-boostfloor-a1` and does not cover this book (Amendment 2 item 7). Any changed word is not the approval and the manager asks again. A later "no" steps the canary down to C0 at once and ends the pre-approval.
- The executor's canary line for this book (DEC-032 Amendment 1 item 3 "Use") is written on a line of its own, with nothing after the id, only on the owner's word. Until then this placeholder stays, and the executor refuses `canary_record_for_bot_id_missing`:

<!-- OWNER LINE GOES HERE, on a line of its own, only on the owner's word: the bare executor line for bot_id h5v1q50-boostfloor-s1 (DEC-032 Am.1 item 3 "Use"). Not written by the drafter. -->

`FILL:owner` (route chosen, rung, deposits, ceiling, question and notebook ids).

## 5. Fill values (the successor's; everything else is DEC-033 section 9a at its merged head)
| Field | Value |
| --- | --- |
| `FILL:dec_number` | DEC-034 |
| `FILL:bot_id` | h5v1q50-boostfloor-s1 |
| `FILL:frozen_sha` | 5c2ab7c64b21358619e37068c6a53e4d183512ea (S\*, tag `q50-succ-base-1`) |
| `FILL:clock_start_ms` | 1793923200000 (2026-11-06T00:00:00Z); slip 1794009600000 |
| `FILL:days`, `FILL:end_ms` | at most 14 days from the first send, `end_ms` clamped to the successor `Book` row's `end_ms_seal` = E_s1 1795132800000 (2026-11-20T00:00:00Z) |
| `FILL:seal_status` | EXP-024 Note under Amendment 10 (successor `h5v1q50-boostfloor-s1`) part B (`EXP/EXP-024-note-s1-successor.md`); pools with s0 in [2026-11-06T00Z, 2026-11-20T00Z); joins limited to that Note's (b) until 2026-11-14T00:00Z; QP seal statement FILL:review |
| `FILL:executor` | `tools/h5_arena_executor.py` ArenaExecutor, book `h5v1q50-boostfloor-s1` (its compiled `BOOKS` row, priority profile `default`), install sha `FILL:install_sha` (must contain this merged file, the executor change of the plan's item C6, and DEC-033 Amendment 4's four blobs) |
| `FILL:md5_proof` | executor md5 replay at the install sha for this book (`tools/h5_arena_exec_replay.py --bot-id h5v1q50-boostfloor-s1`, reference scorer from a `git archive` of S\*, `--expect-code-sha` S\*), and Q50's re-run reproducing #1481 (A 137/137, B 101/101, B synthetic_unconfirmed 0/0): FILL:gate-read |
| `FILL:twin_tool` | the twin writer with the successor's frozen sha accepted (plan item C8), its new `TWIN_WRITER_SHA256` and `config_hash`: FILL:gate-read |
| `FILL:feed_gap_source` | the h5_shadow `gap` records in the successor's own arena shadow dir (`$HOME/data/arena/h5-q50s1-*`), bound read-only at the executor's feed path |
| `FILL:a3_source`, `FILL:a3_guard_source` | as DEC-033 (fast-0 `daily.jsonl` and `a3-applied.jsonl`), with the loop, dead-man and alert job chain covering this canary's `end_ms` (DEC-032 Amendment 3 item 7: E becomes at least 2026-11-20T00Z) |
| `FILL:start_route`, `FILL:start_rung` | R-C or R-0 (section 1), and the rung (section 2) |
| `FILL:q50_confirmation`, `FILL:q50_end_rung` | section 2 items 1 and 2 |
| `FILL:wallet_choice`, `FILL:owner_deposit_addresses` | section 2 item 4 |
| `FILL:c2_p_rule` | filled: p at most 0.05/c on this book's own read, c counting this book, this book's trades only (QP-RULING-1027-s1 answer 3) |
| every other `FILL:gate-read` | as DEC-033 section 9a, filled for this book at its own install |

## 6. Deltas from DEC-033 for the full copy (the only edits a filler may make)
1. Every `h5v1q50-boostfloor-a1` that names the book in force reads `h5v1q50-boostfloor-s1`; mentions of Q50 as the predecessor stay.
2. `frozen_sha`, clock, end and seal fields as section 5. "clamped to the book's seal end 2026-11-06T00:00:00Z, `SEAL_END_MS`" reads "clamped to the successor `Book` row's `end_ms_seal` (E_s1)".
3. DEC-033 §11 item 9 (seal statement) is rewritten for EXP-024's successor note part B and the window of section 5; item 10 (twin pins) takes the successor's values; item 7 (md5 scope) names the successor's replay.
4. DEC-033 Amendment 2's text is replaced by DEC-032 Amendment 2 with Erratum 1 and sections 1 to 3 here; Q50's owner ladder line is not copied.
5. DEC-033 Amendment 3 item 7: the binding end E is at least this canary's `end_ms`.
6. DEC-033 Amendment 4 (four changed blobs at Q50's X-time install) is carried as a pin list and re-verified at this book's install sha; any other changed blob is a new review.
7. Nothing else: no limit raised, no halt threshold loosened, the fee rule unchanged.

## 7. Build items this copy depends on (none exist on main at writing; DEFERRED)
**Deferred (DEC-031 Amendment 18 item 5.4):** the executor and ladder items change `tools/h5_arena_executor.py` and `tools/h5_arena_ladder.py`, which are on Q50's 43-path pinned list (`scripts/ops/q50-xtime-manifest.sh` line 23). They do not merge until Q50's X is settled (installed) or Q50 has ended without a PASS. This book's live path is not needed before its earliest read, 2026-11-11T00:00Z.
Listed in `docs/research/q50-successor/S1-1106-PLAN.md` section 3 (items C6 to C11): the executor's successor `Book` row and per-book feed note, the ladder's per-book carry-over and C2 rung, the twin writer's successor pin, the go-live checker's book argument, and the md5 replay. Each is its own PR with tests, QP OK and a reviewer verdict.

## Erratum 1 (written 2026-10-30T15:56:08Z from `date -u` on mal-research-0; records only; DEC-031 Amendment 22 (the power-n amendment, QP-RULING-1030-power-n R2 and Appendix B item 8.1 "Owed": "a DEC-034 erratum: E_s1 -> 1796083200000, the C0 `end_ms` clamp, and its 11-20 sentences"); before the successor's registration and clock and before any of its outcomes exists; outcome-blind; this file stays a SKELETON DRAFT, NOT IN FORCE; status: DRAFT until merge, QP OK on the exact head and a reviewer verdict)
DEC-031 Amendment 22 moves the successor's book end from E_s1 = 2026-11-20T00:00:00Z (1795132800000) to **E = 2026-12-01T00:00:00Z (1796083200000)**, S\*'s `SUCC_END_MS`, and changes its read sizes: the screen is at the first 00:00Z boundary with at least 200 counted trades on at least 5 dates, or at B >= S_cap = clock + 10 d (2026-11-16T00:00:00Z; 2026-11-17T00:00:00Z on the slip) with at least 100; the confirmation is at the first boundary with at least 300 counted trades on at least 5 further dates, or at E_c = E with at least 100. The slip keeps E (E is no longer clock + 14 d). In this file:
1. **Section 1, "The book."** "book end E_s1 = 1795132800000 (2026-11-20T00:00:00Z; slip: clock 2026-11-07T00Z, E_s1 2026-11-21T00Z)" reads "book end E = 1796083200000 (2026-12-01T00:00:00Z, S\*'s `SUCC_END_MS`; the same on the 2026-11-07 slip)", and "(DEC-031 Amendment 18)" reads "(DEC-031 Amendments 18 and 22)".
2. **Section 1, table, route R-0.** "the successor's own DEC-031 §2 screen PASS under Amendment 11 as DEC-031 Amendment 18 item 7 applies it; earliest read boundary 2026-11-11T00:00:00Z" reads "the successor's own DEC-031 §2 screen PASS at the screen instant of DEC-031 Amendment 22 item 8.1; earliest read boundary 2026-11-11T00:00:00Z (200 counted trades on 5 dates), and at the latest S_cap with at least 100".
3. **Section 1, the R-0 step-up sentence ("... at E_s1 = 11-20T00Z ... so it is at the edge of the window and may not occur").** It reads: "A successor step up from C0 to C1 under R-0 follows DEC-032 Amendment 2 item 5 'Step C0 -> C1' with the successor's own confirmation PASS. That confirmation reads at the first boundary with 300 counted trades on 5 further dates after the screen instant, or at E = 2026-12-01T00Z with at least 100 (DEC-031 Amendment 22 item 8.1). On QP-RULING-1030-power-n's table, C1 comes about 2026-11-22 at the earliest; when the confirmation reads at E, no day of the window remains and C1 cannot occur in this book."
4. **Section 3 item 7.** "at least 100 counted trades on at least 5 UTC dates of its own clock (Amendment 11 as DEC-031 Amendment 18 item 7 applies it)" reads "its screen read at the instant of DEC-031 Amendment 22 item 8.1 (the gate's n >= 100 and 5 dates unchanged)".
5. **Section 3, "What C2 can reach in time."** "`end_ms` is at most E_s1 = 2026-11-20T00Z minus the one-day rule: at most about 7 days at C2" reads "`end_ms` is at most E = 2026-12-01T00Z minus the one-day rule (item 9), so C2 could run from about 2026-11-12 at the earliest (item 7 needs the screen PASS first; on QP-RULING-1030-power-n's rate table the screen reads about 2026-11-12 at 35.333333333333336 a day and 2026-11-16 at 20 a day) to at most 2026-11-30, within the canary's own DEC-032 duration". The screen boundary there reads "2026-11-11T00:00Z at the earliest (200 counted trades on 5 dates), else at most S_cap". "(fewer than 100 counted trades on 5 dates by E_s1)" reads "(fewer than 100 counted trades on 5 dates by E)". "The `bots.json`-pinned H (14)" reads "The `bots.json`-pinned end E (H = 25 d)".
6. **Section 5, `FILL:days`, `FILL:end_ms`.** "`end_ms` clamped to the successor `Book` row's `end_ms_seal` = E_s1 1795132800000 (2026-11-20T00:00:00Z)" reads "`end_ms` clamped to the successor `Book` row's `end_ms_seal` = E 1796083200000 (2026-12-01T00:00:00Z)". The executor change that adds that row (deferred, plan item C6) takes E as `S1_END_MS`.
7. **Section 5, `FILL:seal_status`.** "pools with s0 in [2026-11-06T00Z, 2026-11-20T00Z)" reads "pools with s0 in [2026-11-06T00Z, 2026-12-01T00Z)". `EXP/EXP-024-note-s1-successor.md` (blob `7015abc95c6657b267baaf660810610364e3f71b`) is not edited: DEC-031 Amendment 22 item 8.1 reads its E_s1 as E. Pools with s0 at or after 2026-11-14T00:00Z are outside every H5 seal of that note; quant-proof states at the fill whether the note's part B needs its own erratum.
8. **Section 5, `FILL:a3_source`, `FILL:a3_guard_source`.** "as DEC-033 (fast-0 `daily.jsonl` and `a3-applied.jsonl`)" reads "s1's own loop-2 output on mal-fast-0 (DEC-031 Amendment 22 item 9: its own `A3_OUT` with `daily.jsonl` and `a3-applied.jsonl`)", and "E becomes at least 2026-11-20T00Z" reads "E becomes at least 2026-12-01T00Z".
9. **Section 5, `FILL:feed_gap_source`.** Unchanged, read in s1's own arena root (DEC-031 Amendment 22 item 9).

Nothing else changes: no limit, rung, halt, cap, worst case, step rule or owner line; the C0 duration of DEC-032 §3; the promotion gate. No book has cleared the promotion gate, and nothing here is a profit claim.
