# EXP-024: declared observation of the arena book `h5v1q60-boostfloor-a1` (rule `H5-BOOSTFLOOR-Q60 v1`) (DRAFT)

**Status: DRAFT, NOT IN FORCE.** First drafted 2026-10-27T21:21:00Z on #1064 at head `356b61be`. Moved to this PR (the B2 split of DEC-031 Amendment 19 item 5 (2)) and revised 2026-10-30T15:29Z (`date -u` on mal-research-0). Records only, outcome-blind, text only. It is an EXP-024 declared observation in the Amendment 9 / Amendment 10 / successor-note pattern. Nothing here says that any book makes money. No book has cleared the promotion gate.

**Why a separate file.** `EXP/EXP-024-h5-boostfloor-part1-prereg.md` is path 1 of Q50's 43-path pinned list (`scripts/ops/q50-xtime-manifest.sh` line 23). Nothing in this work changes a path on that list (the manager's rule of 2026-10-27). This note has the standing of a section of EXP-024, as `EXP/EXP-024-note-s1-successor.md` (blob `7015abc9`) does. Quant-proof ruled such a separate-file declared observation VALID for the Look-2 seal in QP-RULING-1027-s1-r2 (`/data/mal/hunt-1008/push-1016/hunt30/q50-c0/QP-RULING-1027-s1-r2.md`, sha256 `26b522c079bf413da3247d49867b7fb7c1ed05608ebbd0c22d29c95a978a52d0`). DEC-031 Amendment 19 item 5 (2) applies that ruling to this file. Section (i) below shows how its four anchors and conditions (1) to (5) are met.

**Gating.** This note merges, with quant-proof's OK and a reviewer verdict on its exact final head, before any process at the Q60 sha reads a live feed (the Amendment 6 smoke included), and so before the book's registration and clock. `FILL:q60_sha` and `FILL:q60_blob` must be filled on this branch before merge, and QP re-checks the filled head. They are filled only once the annotated tag `q60-base-1` exists on origin. Its proofs are DEC-031 Amendment 19 item 2 (i) to (v); at writing they wait on QP-RULING-1030-q60-proofs. Without this note, no text covers a Q\* = 60 trigger outcome on any pool with s0 before 2026-11-14T00:00Z, and the book is not registered. A Q60 book could then count only pools with s0 at or after 2026-11-14T00:00Z.

**What was in view.**
- This file's companion texts: DEC-031 Amendment 19 (merged from PR #1064, main `c80e2ac9`), `EXP/EXP-024-RULE-Q60.md` and `docs/research/q60-sibling/PLAN.md`.
- EXP-024 Amendments 2, 4, 9 and 10 and their Notes, `EXP/EXP-024-note-s1-successor.md`, and DEC-031 through Amendment 20.
- QP-RULING-1027-s1-r2, QP-RULING-1030-q60-tag-tests, the reviewer's notes on #1083 and QP-ASK-1030-q60-proof-i.
- The library row `l3-boost-h5q50-1`, with its structure counts only: Q\* 60 strict triggers 45 / 47 / 32 raw on 10-19 / 10-20 / 10-21, from hunt24 `COUNTS.md`, nothing priced.
- The code of `tools/h5_shadow.py` at S\* (`5c2ab7c6`) and at the tag candidate `c0cfff92` (PR #1083).

People who chose Q\* = 60 saw the real-time outcomes of the arena v1/v2 a1 books, the DEC-024 canary and (by the declared observations) Q50's, as they arrived. This writer opened none of the following: any Q50, s1 or HV4 shadow output, score or count file (`q50p0.json` included), ledger, `bots.json`, A3 record, job status or log of a book job, fill, exit or P&L.

**Q\* = 60 outcomes computed so far, all outside this note's scope:**
- On the exploration day 2026-09-20 (fast-pool-0918, outside every counted window and seal) only. Proof (ii) Run C, job #1841, compared a fresh s14 DS=60 run with the shadow's Q\* 60 replay through `parity_v2.py`, which compares per-pool results internally.
- That job printed counts and pool ids of differences only, and its writer read nothing else.
- To the writer's knowledge, no Q\* = 60 trigger outcome, fill, exit or P&L has been computed on any October or November pool, from any source.

- **(a) Scope.**
  - **What is covered.** A declared exception to section 3's seal for `rule_id` `H5-BOOSTFLOOR-Q60 v1` only:
    - block `EXP/EXP-024-RULE-Q60.md`, sha256 `f36afaae00edd5b8292fdbe732c6034251cc1371afb6c1e54b50e548a91717b9` (blob `5df57733` on main; a change after merge is a new rule);
    - computed only by `tools/h5_shadow.py` at `frozen_sha` `FILL:q60_sha` (the commit of the annotated tag `q60-base-1`: S\* plus the Q\* = 60 level, DEC-031 Amendment 19 item 2), `tools/h5_shadow.py` blob `FILL:q60_blob`;
    - started with `--fresh-forward ARENA --q-star-sol 60`;
    - for `bot_id` `h5v1q60-boostfloor-a1` only.
  - **Which pools.** It covers pools with s0 at or after that process's start record `start_ms` and before `SUCC_END_MS` = 2026-12-01T00:00:00Z (1796083200000). That is the end constant pinned in that sha's code (S\*'s, unchanged by Amendment 19 item 2), so no outcome the process writes is uncovered.
  - **What counts.** The book counts only attempts decided in [clock, clock + 14 d) = [2026-11-07T00:00:00Z, 2026-11-21T00:00:00Z) (1794009600000 to 1795219200000), per DEC-031 Amendment 19 items 3 and 4. A slipped clock moves both ends as item 4 says. Outcomes outside that span are covered but never counted, scored or read.
  - **The smoke.** It also covers the one Amendment 6 smoke at the same code and flags, in a scratch root that is not the book's root. That smoke runs after this note merges and before the clock. It is judged only on its blocking list, never counted, scored or read, and its outcome-bearing records are never opened.
  - **Where outcomes go.** Outcomes go to the book's arena output and ledger only. Anyone may observe them in real time, as in Amendment 2. They are gate-read once by the book's scoring wrapper and read tool under DEC-031 §2 and Amendment 11 item 1, as Amendment 19 item 7 applies it.
  - **What is not covered.** A process at any other code, with any other flags, or for any other `bot_id`.
- **(b) Not a revival, not a copy.**
  - EXP-024 ended NOT_DECIDABLE on 2026-10-13. Nothing here revives, re-files or re-scopes it, and no arena result changes any EXP-024 parameter or record.
  - Q\* = 60 is a different rule from Q\* = 40 and Q\* = 50. It is not a second frozen copy of Q50 or s1 (DEC-031 Amendment 3 item 5: "A retuned rule is still a new book. That is a different rule, not a copy.").
- **(c) Amendment 10 (e), (f) and (g) bind this book word for word**, with "Q50" read as `h5v1q60-boostfloor-a1` and "before 2026-11-06T00:00:00Z" read as "before `SUCC_END_MS`":
  - no bx10 or v2 exit is computed (bx10 is off at Q\* != 40 in code);
  - before 2026-11-14T00:00Z, no record carries an outcome for a synthetic or unclassified pool or splits an outcome by class;
  - nobody prices this rule on arena-tape hours outside this scope.
- **(d) Executor (live) outcomes are NOT declared here.** A live canary for this book needs its own DEC-032 copy and an executor change. `tools/h5_arena_executor.py` is on Q50's pinned list, so that change is deferred until Q50's X is settled or Q50 has ended without a PASS (PLAN item Q7). A second note (part B) is needed then, in the form of `EXP/EXP-024-note-s1-successor.md` part B.
- **(e) Independence and nesting.** Q60's triggered pools contain every Q50 and every Q\* = 40 trigger pool. The first Q <= 60 sell comes at or before the first Q <= 50 sell, because spent() is monotone and t is earlier. On a shared pool Q60 enters at the same print or an earlier one, with the same 330 s exit.
  - Nothing Q60 shows may stop, delay, re-scope or change the registration, window or read of Q50 or of `h5v1q50-boostfloor-s1` (DEC-031 Amendment 17).
  - Nothing Q50 or s1 shows may change Q60's.
  - Q60's read is reported beside Q50's and s1's and is never pooled with them (DEC-031 Amendment 19 item 7).
- **(f) No send authorised.**
- **(g) Disclosure.**
  - This note is dated after 2026-10-16T00Z, with Look 2-window outcomes of other books in view in real time (Amendment 2's clause).
  - Q\* = 60 is a discarded discovery cell, the worst of {40, 50, 60} in discovery. No Q60 edge exists or is claimed.
  - It adds 1 to N.
- **(h) Who chose Q60, and when.** This is DEC-031 Amendment 19 item 10 (B5), verbatim. "This writer" and "this agent" there are the writer of Amendment 19, not of this note.
  > - The owner asked to expand on Q50 so that the lab has a better chance if its forward test disappoints. The ask was relayed to this writer by the manager.
  > - This agent drafted the design (#1064, 2026-10-27 from 21:21Z).
  > - The manager, manager17, chose the lean form (clock 11-07T00Z) at about 2026-10-27T21:2xZ, from the library row counts (`l3-boost-h5q50-1`) and this design.
  > - Neither the manager nor this agent opened any Q50 outcome (P&L, fill, exit) or any Q50 output: they are sealed. Q50's outcomes could have been observed in real time since 2026-10-25 under EXP-024 Amendment 10 (a), but none was opened by either.
  > - Whether the owner looked at any Q50 outcome before the ask is not known to this writer. The registration record states it from the owner's or the manager's word.
  > - The label "chosen with Q50 outcomes in view" applies to every Q60 read only if the record shows that someone in the choice opened one.
- **(i) QP-RULING-1027-s1-r2: anchors and conditions.**
  - **The four anchors.**
    - **Merge on main before any covered outcome exists.** See Gating: the note merges before the smoke and any `q60_sha` process.
    - **QP OK and a reviewer verdict on its exact head.** Both are required on the filled head.
    - **An exact scope.** That scope is item (a): rule sha256, `frozen_sha` and blob, flags, `bot_id`, s0 range.
    - **Citation from the texts that rely on it.** DEC-031 Amendment 19 item 5 (2) cites this path. The Q60 records PR and the registration record cite it by path and merged blob.
  - **The five conditions.**
    - **(1)** The note merges before the smoke and before the Q60 shadow starts (Gating).
    - **(2)** The texts that rely on it cite it by path and merged blob. Any later edit is a new note, with its own QP and reviewer round, and covers nothing done before it.
    - **(3)** From its merge, the file is listed in the active-seals brief (`/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md`) and in every tape-touching agent brief. This is the manager's step at merge.
    - **(4)** After Q50's X is installed or Q50 has ended, a records PR appends one pointer line (path and blob) to EXP-024. Validity does not wait for it.
    - **(5)** Part B (the executor `FEED_NOTE_REF` for this book, with the tree-presence test) comes with the deferred executor change of item (d). Until then no executor process reads a live feed for this book.

QP OK and reviewer verdict: on the PR at its merged head.
