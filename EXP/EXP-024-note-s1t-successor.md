# EXP-024: Note under Amendment 10 (successor `h5v1q50-boostfloor-s1t`): declared real-time observation of the s1t paper book's outcomes on the fast-0 tip source (DRAFT)

**Why a separate file.** This note follows `EXP/EXP-024-note-s1-successor.md` (blob `7015abc95c6657b267baaf660810610364e3f71b`), for the same reason:
- `EXP/EXP-024-h5-boostfloor-part1-prereg.md` is on Q50's pinned X-time install list, so it is not edited.
- This note has the same standing as a section of EXP-024. It is an EXP-024 declared observation in the Amendment 9 / Amendment 10 pattern, and it binds EXP-024 section 3's seal for the scope below.
- It is the note that DEC-031 Amendment 24 item 4 requires (Amendment 24, merged in #1186, main `ec328be2`).
- It does not edit, widen or replace the s1 note. That note covers `h5v1q50-boostfloor-s1` only, and s1 is not registered while Amendment 24 is in force (Amendment 24 item 1).

## Note under Amendment 10 (successor `h5v1q50-boostfloor-s1t`)
- **Status.** DRAFT. Written 2026-11-01T23:45:04Z from `date -u` on mal-research-0.
- **Timing.** Written before s1t's registration, smoke, P0 count, process start and clock, so before any s1t trigger or outcome exists.
- **Nature.** Outcome-blind; text only.
- **What it declares.** A real-time observation of the s1t paper book's outcomes (DEC-031 Amendment 24 items 4 and 6).

It is text only, in the form of the s1 note's Part A, Amendment 10, the Note under it, and QP-RULING-1024-successor R7(c) and R7(d). Nothing here says that any book makes money. No book has cleared the promotion gate.

**Pinned commit (filled 2026-11-01T23:50:36Z; QP-NOTE-1102-tip-readers C1).** S\*' is the commit `afa4971d9dd8b0bca2e73a758c22f4539f675d63`, the head that quant-proof and the reviewer passed on #1185.
- At that commit, `tools/h5_shadow_tip.py` is blob `05c5489a786dac4eb0696a74279a0f789ddb1deb`, and `tools/h5_shadow.py` is blob `7f859b12deef87573a7314e49e125b5121eaa645` (S\*'s, DEC-031 Amendment 24 item 3), both from `git rev-parse` at writing.
- Tag `q50-succ-tip-1` must name that commit. Before the smoke, the manager checks that `git rev-parse 'q50-succ-tip-1^{commit}'` equals `afa4971d9dd8b0bca2e73a758c22f4539f675d63`.
- A process at any other commit is not covered.
- **Tag verified 2026-11-01T23:53:29Z** (`date -u`, mal-research-0), after the tag was pushed to origin:
  - `git rev-parse 'q50-succ-tip-1^{commit}'` = `afa4971d9dd8b0bca2e73a758c22f4539f675d63`;
  - the annotated tag object is `c8cb9a8e31419fcce03b9fe3947a474ebdd7f47a`;
  - `git rev-parse 'q50-succ-tip-1:tools/h5_shadow_tip.py'` = `05c5489a786dac4eb0696a74279a0f789ddb1deb`;
  - `git rev-parse 'q50-succ-tip-1:tools/h5_shadow.py'` = `7f859b12deef87573a7314e49e125b5121eaa645`.
- #1185 is closed unmerged by design. The manager re-checks the tag before the smoke.

**Gating.**
- **Part A** merges, with quant-proof's OK and a reviewer verdict on its exact final head, under three conditions:
  - before the s1t smoke of DEC-031 Amendment 24 item 6 starts;
  - before the s1t shadow and the s1t P0 count start;
  - by 2026-11-05T12:00:00Z (Amendment 24 items 4 and 9).

  Otherwise Amendment 24 lapses, this note has no effect, and s1 proceeds under DEC-031 Amendments 17, 18 and 22 with the s1 note.
- **Part B.** There is none. s1t's C0 is out of scope (Amendment 24 item 8). This note covers no executor, dry or live. A later executor change for s1t needs its own note.

**What was in view.**
- DEC-031 through Amendment 24 (main `ec328be2`).
- The s1 note (blob `7015abc9…`) and Amendment 10 with its Note.
- QP-RULING-1024-successor and QP-RULING-1102-tip-shared-slot.
- Quant-proof's comments on #1186 (6565588204) and its report `/data/mal/hunt-1008/ops/reports/2026-11-02/qp-1102-tip-cohort.md`, and the reviewer's comment 6565593139.
- The header and CLI of `tools/h5_shadow_tip.py` on #1185 (head `afa4971d` at writing), and `tools/h5_shadow.py` at S\* `5c2ab7c6`.

**Not opened:**
- any Q50 or s1 shadow output, trigger or decision log;
- any ledger, `bots.json`, score output, count or read-instant file;
- any executor output, A3 record or verdict;
- any job status or log;
- any tip file or derived-copy `tape-*` file;
- any fill, exit or P&L.

No s1t trigger or outcome exists on any pool at writing. Q50's outcomes have been observable in real time since 2026-10-25 under Amendment 10 (a); this writer looked at none.

- **(a) Part A, scope (the shadow, its pricing-off smoke and its P0 count).**
  - **What it covers.** A declared exception to section 3's seal for `rule_id` `H5-BOOSTFLOOR-Q50 v1`, block sha256 `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4`, under all of these conditions:
    - computed only by `tools/h5_shadow.py` (blob `7f859b12deef87573a7314e49e125b5121eaa645`; it and its whole transitive first-party import set are byte-identical to S\* `5c2ab7c64b21358619e37068c6a53e4d183512ea`);
    - run through `tools/h5_shadow_tip.py` (blob `05c5489a786dac4eb0696a74279a0f789ddb1deb`) at `frozen_sha` S\*' = `afa4971d9dd8b0bca2e73a758c22f4539f675d63` (tag `q50-succ-tip-1`);
    - started with `--tip-input /data2/mal/arena-tip-feed/hv7 --fresh-forward ARENA --q-star-sol 50 --syn-sockets 1 --ws-url wss://rpc.solanatracker.io/public`.
      - The last two arguments are the class side feed, pinned per QP-NOTE-1102-tip-readers §3 condition 1: one keyless public endpoint, which is neither `api.mainnet-beta.solana.com` nor the solanavibestation endpoint.
      - A different host before the smoke is a commit on this note before it merges. A change inside s1t's window needs a quant-proof note first and is disclosed in the read.
      - The side feed only speeds up S\*'s synthetic-class lookup. Its reconnects write no gap and flag no pool (S\*'s `run_pump_feed` counts `ws_restarts` only, `tools/h5_shadow.py:3120` at S\*). S\*'s RPC fallback and the tip's `complete` rows still classify;
    - for `bot_id` `h5v1q50-boostfloor-s1t` only.
  - **Pools covered.** Pools with s0 at or after that process's start record `start_ms` and before `SUCC_END_MS` = 2026-12-01T00:00:00Z (1796083200000), the end constant pinned in S\*'s code. So no outcome the process writes is uncovered (R7(c)).
  - **What the book counts.** Only attempts decided in [clock, E), E = `SUCC_END_MS` (DEC-031 Amendment 24 item 5: H = 24 d from the 2026-11-07T00:00:00Z clock, 21 d from the 2026-11-10T00:00:00Z slip). Outcomes of pools decided before the clock are covered but never counted, scored or read.
  - **The book's process.** It starts at least 1 h before the clock and after 2026-11-06T00:00:00Z. At S\*' a pool whose s0 is before the start is never tracked (the wrapper drops rows before the start, so the pool is never announced). So the book's process computes no outcome for a pool in Q50's counted window [2026-10-25T00Z, 2026-11-06T00Z).
  - **The smoke** (DEC-031 Amendment 24 item 6) is a process at the same code with `--smoke` added, in a scratch root that is not the book's root. Its start is at or before 2026-11-05T12:00:00Z.
    - `--smoke` puts S\*'s H5 Look-2 seal on every pool, so no outcome, ladder, strip or min-Q is computed on any pool. Trigger and pool records are written flagged `h5_look2_sealed`, and the wrapper halts the process if an outcome-type record is produced.
    - The smoke therefore computes no Q50-rule outcome, on Q50's counted window or elsewhere. This note covers it all the same, as the s1 note covered s1's smoke.
    - Its records are never opened, never counted, scored or read. Before s1t's first read, no count of them is printed or notebooked as a number (Amendment 24 item 6).
    - A smoke run without `--smoke` is not covered.
  - **The P0 count** of Amendment 24 item 5 (a slip clock only) is covered on the same terms. It runs with `--smoke`, counts only.
  - **Pools with s0 in [2026-11-06T00Z, 2026-11-14T00Z)** are covered only by this note (R7(c)). Pools with s0 at or after 2026-11-14T00:00Z are outside every H5 seal of this file.
  - **Where outcomes go.** To s1t's arena output and ledger only. They may be observed in real time, by anyone, as in Amendment 2. They are gate-read once by s1t's scoring wrapper and read tool under DEC-031 §2 and Amendment 11, as DEC-031 Amendment 18 item 7 applies it (carried by Amendment 24 item 2).
  - **Not covered:** a process at any other code, with any other flags (other than `--smoke` as above), on any other input, or for any other `bot_id`. That includes S\* on the public websocket for `h5v1q50-boostfloor-s1`, which the s1 note governs.
- **(b) Part B: none.** No executor outcome of s1t is covered. Amendment 18 item 5.3's executor `Book` row and DEC-034's routes are keyed to `h5v1q50-boostfloor-s1` (DEC-031 Amendment 24 item 8).
- **(c) Not a revival, not a copy.**
  - EXP-024 ended NOT_DECIDABLE on 2026-10-13. Nothing here revives, re-files or re-scopes it, and no arena result changes any EXP-024 parameter or record.
  - Q50's end (its read verdict or its end-seal) is final for its `bot_id`.
  - While Amendment 24 is in force, s1t is the only successor `bot_id`: s1 is never registered (Amendment 24 item 1). On a lapse, this note has no effect.
  - s1t is a new `bot_id` with a window disjoint from Q50's. It adds 1 to N (DEC-031 Amendment 4 item 2.5).
- **(d) Amendment 10 (e), (f) and (g) bind s1t word for word,** with "Q50" read as `h5v1q50-boostfloor-s1t` and "before 2026-11-06T00:00:00Z" read as "before `SUCC_END_MS`":
  - no bx10 or v2 exit is computed on its triggers (bx10 is off at Q\* 50 in S\*'s code);
  - before 2026-11-14T00:00Z no s1t record carries an outcome for a synthetic or unclassified pool or splits an outcome by class, and the universe excludes them before any outcome exists;
  - nobody prices Q50's rule on arena-tape, tip-tape or derived-copy October or November hours outside this scope.

  Amendment 10 (d) ("after 2026-11-06T00Z nothing covers Q50") stays true for Q50's own `bot_id` and process.
- **(e) Independence.** As the Note under Amendment 10 (e), for this book.
  - Nothing s1t shows may stop, delay, re-scope or change Q50's reads, ledger or scorer.
  - Nothing Q50 shows may stop, delay, re-scope or change s1t's registration, window or read (DEC-031 Amendment 17 item 4; Amendment 24 item 1).
- **(f) No send authorised.** This note declares an observation. A live send needs s1t's own recorded PASS, every DEC-032 precondition and a later executor text and note (Amendment 24 item 8).
- **(g) Disclosure.**
  - Amendment 2's clause applies: this note is dated after 2026-10-16T00Z, with Look 2-window outcomes of other books in view in real time.
  - Q\* = 50 is a discarded discovery cell. No Q50, s1 or s1t edge exists or is claimed.
  - s1t's triggered-pool set is Q50's rule on later pools, read from the tip source. Its read prints Q50 beside it.
  - The tip source is walk-2-hour information (DEC-030 §2 R1; DEC-031 Amendment 23 item 9). s1t's outcomes over Forward walk 2 calendar hours are walk-2-hour outcome information, and every s1t read says so.

QP OK and reviewer verdict: on the PR at its merged head. s1t's records PR cites the comment ids.
