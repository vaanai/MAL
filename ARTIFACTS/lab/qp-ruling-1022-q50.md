Repo copy of /data/mal/hunt-1008/push-1016/hunt24/l3-boost/QP-RULING-1022-q50.md (source sha256 a0dcf39100d30b944544955981e7452e4449d5018a942d30a35a4c3a2057cac7), unchanged below this header. Copied 2026-10-21T23:3xZ for DEC-031 Amendment 5 and EXP-024 Amendment 10; the /data file is mutable, this copy is the cited text.

# QP RULING 2026-10-22 (written 2026-10-21T23:3xZ, `date -u` 23:34:56Z): path for an H5-BOOSTFLOOR-Q50 arena book

Reviewer: quant-proof. In view: preamble 1021; DESIGN.md (Route 1, s2 row A, s4); COUNTS.md; PLAN-COUNTS.md (sha256 096ca4a2...d5e, verified); scripts/l3_count.py (sha256 c45bfbfa...424d, verified = plan); out/l3_counts.json (sha256 aabc7fd4...e19e42, verified = COUNTS); /data/mal/hunt-1008/h5-flows/RULE.md (sha256 c66b1a59...e56c, verified); origin/main eb8b32d: EXP-024 (s2, s3, s3.1, Am.2, Am.4 D1, Am.8, A3 halt record, Am.9, Note under Am.9), DEC-031 (s1-s4, Am.3 items 1/5/7/8, Am.4 items 1-2), tools/h5_shadow.py at 1f7a89c (unchanged on main), tools/arena_h5_score.py, tools/arena_h5_score_a2.py, tools/arena_h5_window_count.py, tools/a3_redeploy_check.py, tools/a3_redeploy_pins.json, tools/a3_halt.py; hunt24/h5-canary QP-RULING-1022-a2pack.md, QP-RULING-1022-exec.md, P0-RULE-a2-QP.txt. NOT opened: bots.json, any ledger, any shadow/canary output, any sealed path. The owner approval notebook entry was not opened; it is taken from the manager's brief. Nothing was run on any host. No book has cleared the promotion gate; nothing here says any book makes money.

## Verdict
NO-GO today. A Q50 book can be registered honestly, but only after items G1-G10 below are true. **Earliest honest clock: 2026-10-25T00:00:00Z.** 10-24T00Z is not available (see 5). Nothing touches the a2 books or their 10-23 clock. If any item slips, the clock moves to the next 00:00Z after the last item. Nothing may be cut to keep a date.

## 1. EXP-024 amendment (call it Amendment 10) - content
It needs its own rule block, with its own sha256 and its own rule_id. Am.2 is scoped to the shadow detector of the v1 rule (Q* = 40). Am.9 and its Note are scoped by rule_id H5-BOOSTCLOCK-v2. Am.9 (e) covers rule_id `H5-BOOSTFLOOR-v1` only from 11-06. Nothing that exists covers a Q* = 50 trigger outcome. Without Am.10 the Q50 book can count no pool with s0 before 2026-11-14T00:00Z, so it cannot run in this arena at all.

**Rule block.** `RULE-Q50.md` is RULE.md (c66b1a59...) byte for byte, except for these lines:
- (a) the header line, which names `H5-BOOSTFLOOR-Q50 v1`, sibling of v1, and is dated;
- (b) the trigger line, `post-trade Q_i = real quote + V <= 50 SOL`;
- (c) the Code line, `DS=50`;
- (d) the "Parameters chosen on discovery" line, which keeps the original text and adds "Q* = 50 here, a discarded discovery cell (discovery: monotone, smaller Q* better); chosen 2026-10-21 for attempts/day, not for edge".

Do NOT use DESIGN s3's paraphrased block. It rewrites the universe, costs and filter lines. Arena-layer terms (WSOL, V band, synthetic exclusion, stack, rent) belong to DEC-031 and the registration, not to the rule. The amendment carries the block between ~~~rule fences with a sha256 check command (as s2). It also carries `git diff --no-index RULE.md RULE-Q50.md`, which shows only (a) to (d). The repo text is the pin. A /data-only file is taint 8(f).

**What it declares** (the form of Am.9 and the Note):
- **Gating.** It merges with quant-proof's OK and a reviewer verdict on its exact final head, before the Q50 process starts, and so before the clock. Otherwise the book is not registered.
- **What was in view.** The arena v1 and v2 a1 outcomes and the canary outcomes were observable in real time under Am.2 and Am.9 to the people who chose Q50, and the amendment says so. It is NOT outcome-blind for v1. Also in view: DESIGN, COUNTS (structure only) and the discovery grid. It states that no Q* = 50 trigger outcome, fill, exit or P&L has been computed on any October pool from any source. l3_count.py reads only decision-time fields; this was checked at sha256 c45bfbfa. The writer verifies that statement before signing and does not assume it.
- **Scope.** rule_id `H5-BOOSTFLOOR-Q50 v1`, block sha256 as merged. It is computed only by `tools/h5_shadow.py` at the Q50 book's frozen_sha, started with `--fresh-forward ARENA --q-star-sol 50`. It covers pools with s0 at or after that process's start record `start_ms` and before 2026-11-06T00:00:00Z. Coverage starts at process start, not at the clock, so that no outcome the process writes is ever uncovered. This is stricter than the v2-a2 Note, which left pre-clock outcomes uncovered and unopened. Outcomes go to the arena output and ledger only. They may be observed in real time. They are gate-read once by the Q50 scoring wrapper under DEC-031 s2.
- **What it must NOT do:**
  - change Am.2, Am.9 or the Note, or the v1/v2 scope or the a2 coverage;
  - change any EXP-024 parameter or record;
  - revive, re-file or re-scope EXP-024, which ended NOT_DECIDABLE on 2026-10-13;
  - cover any pool with s0 at or after 2026-11-06T00Z (the code's end-seal stays; after 11-06 nothing covers Q50, and Am.9 (e) is not extended);
  - cover any bx10/v2 exit computed on Q50 triggers. That is a third rule. It is not declared and must never be computed (G2);
  - let EXP-024's read tool use any of this.
- **Synthetic class.** Am.4 D1, A2 and B3 and Am.9 (d) bind Q50 word for word:
  - before 2026-11-14T00:00Z no Q50 record carries an outcome for a synthetic or unclassified pool, or splits an outcome by class;
  - the universe excludes synthetic and unclassified pools before any outcome exists;
  - pool records of non-plain pools carry no min_q fields (the existing fresh-mode behaviour, unchanged).
- **Look-2 seal for Q50 pools in their first 360 s.** Covered only by this declaration, inside the scope above. Outside it (pools before process start; any other process, replay or tape; arena tape research-0; walk 2; RPC), the s3 seal holds until EXP-024's end state, first 2026-11-14T00:00Z. In particular, NOBODY prices Q50 on arena-tape October hours, before or after the clock (s3 seal plus DEC-031 Am.3 8(d)). That includes a "sanity" replay.
- **Disclosures.**
  - Am.2's clause applies: an amendment dated after 10-16T00Z was made with Look-2-window outcomes in view.
  - Q50 is a sibling of h5v1-boostfloor-a2 and h5v2-boostclock-a2. Its triggered-pool set contains every v1 trigger pool. The first Q<=50 sell is at or before the first Q<=40 sell, because spent() is monotone and t is earlier. The v1 and Q50 books share the 330 s exit.
  - Adds 1 to N (DEC-031 Am.4 2.5).
  - Owner provenance: the notebook id, and whether the writer opened it.

## 2. Code
- **Flag.** `--q-star-sol` (float, default 40.0) in tools/h5_shadow.py replaces every read of Q_STAR_SOL. At 1f7a89c those reads are at :1866, :1884, :1895 and :1977. The --compare-frozen filter at :3404 (`f.D == 40.0`) must follow the flag too.
- **Refusals.**
  - Allowed values are exactly {40.0, 50.0}.
  - Any value other than 40.0 requires `--fresh-forward ARENA`. It forces bx10 OFF even in fresh mode (today :3197 forces it on), and it refuses `--bx10-enable`.
  - It refuses `--h5-look2-observed`, because Am.2 does not cover Q50.
  - The start record and every trigger record carry `q_star_sol`. When it is not 40.0, the start record's `rule` is `H5-BOOSTFLOOR-Q50 v1` and `rule_sha256` is the Q50 block sha.
  - At 40.0 nothing in any record changes: no new key on the default path.
- **Equivalence proof** (MiScusi job, research-0, under a memory cap, one heavy job):
  - (i) Run 1f7a89c and the new sha in replay on the pinned E0-H5 exploration day 2026-09-20 (fast-pool-0918), hours as in EXP-024 Am.5 C, identical args, flag absent AND flag `--q-star-sol 40`. md5 of every record stream, with only the start record's code_sha, argv, pid and t_ms masked, must be EQUAL. The mask list is pinned in the PR. Report n and md5 for each run.
  - (ii) The Q50 path on the same exploration day: per-trade parity against the frozen s14_boostdip.py (sha256 70becfb7) run with DS=50 TMIN=0 TMAX=300 TEND=330 NOBOOSTREQ=0. n equal and max |delta pnl| at the #476 tolerance. This is exploration data, outside every counted window, and allowed.
  - (iii) Unit tests for every refusal above, and a test that fresh mode with q != 40 writes no outcome_bx10 line.
  - (iv) A source check: every function that tools/a3_redeploy_check.py imports or exercises from h5_shadow.py (its FROZEN_FILES decode path) is byte-identical between 1f7a89c and the new sha. Check it with an AST or inspect.getsource comparison in a committed test. This is needed for G6.
- **Frozen sha.** The Q50 book's frozen_sha is the merge commit (on origin) that carries the flag and the Q50 wrapper. Its own process runs from a clean checkout at that sha (R5(a)) with its own out dir under $HOME/data/arena/h5-q50-*.
- **a2 is NOT affected and must not be.** a2 runs at 1f7a89c, whose objects and checkout are untouched. The scorer identity check (`code_sha == frozen_sha`, arena_h5_score.py:326) makes the a2 books refuse the Q50 dir, and the reverse. Do not touch:
  - tools/arena_h5_score.py (pinned sha256 dfe7f018...);
  - tools/arena_h5_score_a2.py (it asserts exactly four ids);
  - tools/arena_h5_window_count.py;
  - a3_redeploy_pins.json or a3_redeploy_check.py.
  Editing any of them while the a2 books run is DEC-031 Am.3 8(i). That means NO-GO for Q50 and a taint on both a2 reads.

## 3. Scoring
- **Wrapper.** A NEW `tools/arena_h5_score_q50.py`, built in the pattern of the a2 wrapper:
  - It imports the unmodified scorer, asserts its sha256, and sets BOTS to exactly {Q50_ID: "v1"} inside a restore block.
  - It refuses any dir unless EVERY start record has `q_star_sol == 50.0` and rule `H5-BOOSTFLOOR-Q50 v1`. Without this check, the same sha run without the flag would silently feed a Q40 book under the Q50 id.
  - It applies the Note's fail-closed s0 filter: it drops pv triggers whose `s0_t_recv_ms` is missing or below clock_start_ms + 60,000. The drops are counted, not attempts.
  - It has its own `register` that writes the line with rule `H5-BOOSTFLOOR-Q50 v1`, `q_star_sol` 50, rule_sha256 and frozen_sha. Do not use register_books: it hard-codes "rule": "H5-BOOSTFLOOR v1" (:385) and derives ids from BOTS.
  - It is the book's ledger code, so it is MERGED, with tests, a reviewer verdict and QP OK, before the clock.
  - A Q50 read tool (count and read) is owed before the read, not before the clock.
- **bot_id.** `h5v1q50-boostfloor-a1`.
  - It starts with "h5v1", so the scorer prices the 330 s exit (:469, :529).
  - It does NOT start with "h5v1-boostfloor-", so the legacy set_window_end (:416) can never end it.
  - It starts with "h5", so a3_halt requires it to be listed (:121).
  - Any other id must meet the same three properties, shown in a test.
- **A3 rule.** DEC-031 Am.4 item 1, the new automatic rule. The book is registered after 136cd98, so its id goes in `new_rule_bot_ids` of $HOME/data/arena/a3-books.json.
  - The swap of the books file (a2 + Q50) happens in the same command as registration, away from :05-:20 of 07, 13 and 19.
  - Its sha256 and a verbatim copy go in the Q50 records PR and the notebook copy before the clock (Am.4 1.10).
  - One full green a3-daily run on the new books file is required before the clock: all RCs 0, verdict NONE, six flags clear.
- **Cost stack and deciding cell** are pinned before the clock: the 1.9 s leg at 0.30 SOL, the same as the a2 siblings.
  - Rent: QP requires rent-CHARGED deciding with the rent-0 leg report-only, the same as a2 (QP-RULING-1022-exec item 9/10 logic). Choosing rent-0 for the sibling with the most trades only is a favourable-direction degree of freedom.
  - H = min(90, max(14, ceil(150/rate))) = 14 at the P0 rate. The effective end is 2026-11-06T00:00Z (end-seal). It is pinned in the notebook copy and the records PR (no H field), as for a2.

## 4. P0 for Q50
- The book's own shadow has logged only Q40 triggers. Opening those logs is out of bounds anyway. Running the Q50 sha against October tape in replay is a seal breach. So the only structure-only P0 before a live run is the l3 counter: scripts/l3_count.py, sha256 c45bfbfa98f06c561e1bf302e5cc318580bae21323f13d4ce9cdc38626a7424d, counts only, 0 RPC.
- It is NOT "the book's rule module at its registration-candidate sha" (DEC-031 Am.3 item 1, kept by Am.4 2). It is therefore allowed only through a DEC-031 note (G5) that declares the departure and the following reason:
  - P0 decides only the floor (>= 2.0 a day, no day at 0) and H. H is 14 for any rate >= 10.7 a day and is capped by the end-seal.
  - The proxy's error cannot change either decision unless the true rate is below the floor.
- **Bias is not conservative.** The counter does not exclude synthetic or unclassified pools (an upper bound). Its strict detector is a behavioural proxy for the PDA. So a proxy count that merely clears 2.0 is NOT a pass. The proxy's PASS bar is the old Am.3 bar: G0, Q* 50, strict detector, RAW per day, mean >= 20 and no day < 10. Below that bar the result is NOT_DECIDABLE, not DROP: no registration, no re-count, no other window. Then the only route left is a live P0 from the Q50 process's own trigger records (3 full days after it starts, under Am.10 from its start), and the clock moves to 10-28 or later.
- **Window.** UTC days 2026-10-21, 2026-10-22 and 2026-10-23: [1792540800000, 1792800000000), closed arena tape hours /data/mal/arena/tape/tape-2026-10-2{1,2,3}T*.jsonl.zst, 72 files. 10-20 is excluded on gap metadata alone, decided now before any count. Its 17:00 hour is an all-feed outage of 3,395 s (COUNTS.md quality line), which is a hard gap.
- **Gap rule.** NOT_DECIDABLE if:
  - any hour file is missing;
  - any 'all' (feed) gap exists;
  - PumpSwap data_loss in the window is > 3,600 s in total, with overlaps counted twice.
- **Job.** Submitted at or after 2026-10-24T00:10:00Z.
- **Fixed-first notebook text** (posted, with its instant, before the job is submitted; fill the two placeholders):
  "P0 for h5v1q50-boostfloor-a1 (H5-BOOSTFLOOR-Q50 v1, rule sha256 <...>). Structure only, counts only: no price after any decision, no fill, no exit, no outcome, 0 RPC; no ledger, bots.json, shadow, canary or sealed path opened. Tool: hunt24/l3-boost/scripts/l3_count.py sha256 c45bfbfa98f06c561e1bf302e5cc318580bae21323f13d4ce9cdc38626a7424d (a declared proxy under DEC-031 note <PR>; not the book's module). Window: UTC days 2026-10-21, 2026-10-22, 2026-10-23, [1792540800000, 1792800000000), arena tape hours 2026-10-21T00 to 2026-10-23T23 (72 files); declared now; no other window tried after a failure. Deciding cell: G0_wsol_band, Q* 50, strict detector, raw per UTC day; all other cells report-only. PASS iff: 72 files present, no 'all' gap, PumpSwap data_loss <= 3,600 s total, and mean >= 20 and no day < 10. Gap failure or a count below the bar: NOT_DECIDABLE, no registration on this count, no re-count, no other window. Outputs: per-day deciding counts, gap counts, data_loss seconds, output sha256, job id. rate for H = mean of the 3 days."
- **Gap logging.** The counter today logs gaps but does not apply this rule. Either the job script applies the rule mechanically to the JSON, in a committed wrapper with a test, or the manager applies it from the JSON under the text above. Either way, the rule is fixed before the job runs.

## 5. Clock, N and c
- **Earliest clock: 2026-10-25T00:00:00Z.**
  - P0 needs 3 full closed days with no hard gap: 10-21, 10-22, 10-23, counted after 10-24T00:10Z. Registration and process start follow on 10-24 before 23:50Z, and the a3 green run on the new books file before the clock.
  - 10-24T00Z would need 10-20 in the window, which holds a hard gap. It would also stack Q50's PRs, P0 and registration on the a2 registration and clock day, which is exactly when a mistake costs the a2 books (2 N).
  - Build order: the code PR (flag, Q50 wrapper, equivalence job) and the EXP-024 Am.10 and DEC-031 note PRs may be drafted 10-22 but merge AFTER the a2 registration is done (do not touch the fast-0 a2 path on 10-22), and all before 10-24 registration.
- **Window.** [10-25T00Z, 11-06T00Z): 12 dates. At the COUNTS strict rate (21-36 a day, an upper bound), 100 attempts on 5 dates is expected around 10-29 to 10-31. That is a forecast only.
- **N.** +1 from the clock (Am.4 2.5). It is disclosed on the a2 reads too: their multiplicity blocks are written at read time and will list it.
- **Siblings (Am.3 8(e)).** v1-a2, v2-a2 and Q50 share the trigger family and the entry legs. Q50's pools contain v1's. At the read the best of the three is in-sample. A Q50 screen pass while v1-a2 fails is selection on a correlated sibling, not new evidence. Every read of any of the three prints all three side by side, with the shared-pool count (counts only).
- **c.** A Q50 screen pass enters confirmation automatically (Am.3 item 2) and adds 1 to c.
- **Winner's curse.** Q50 is a discarded discovery cell. Discovery found Q*=40 best and monotone, and the only edge figure cited for the family (+4.939% per attempt, best of 413 variants, enrichment p 0.1953) is Q40 and in-sample. No Q50 edge exists. Expect lower per-trade edge than v1. At 1.0 SOL the cited impact (median 5.94%) exceeds the cited Q40 edge.

## 6. NO-GO triggers (any one stops Q50; none of them touches a2)
1. The Q50 process writes, or is able to write, an outcome_bx10 record, or runs without the q_star_sol records, or the wrapper lacks the q_star/rule check.
2. Any edit to a file the a2 books run or are scored by: 1f7a89c objects, arena_h5_score.py, arena_h5_score_a2.py, arena_h5_window_count.py, a3 tools or pins.
3. No accepted cover for A3 guard (h) (G6).
4. Any Q50 outcome on any October pool computed before Am.10 merges, or outside its scope. That is an EXP-024 s3 breach: recorded, and Q50 is never registered on October data.
5. P0 not PASS under 4.
6. A kill-review week in [registration, 2026-10-31]. The manager writes the check line in the registration notebook entry, as in the a2 pack (7).
7. The equivalence md5 is not EQUAL, or the Q50 s14 parity fails.
8. The records PR is not on origin before the clock, or its before-clock text changes after the clock.
9. Fast-0 memory: the Q50 process plus the a2 process plus the feeds must fit the user slice. Check `systemctl show user-1002.slice -p MemoryCurrent` before start. An OOM inside the window makes open positions NOT_DECIDABLE (8(j)) for every book on that host, a2 included. So start Q50 only with headroom, under its own MemoryMax.

## GO list (all must be true before the 10-25T00Z clock)
- G1. RULE-Q50 block in the repo with its sha256 and the 4-line diff.
- G2. Flag PR merged: refusals and bx10-off as in 2, tests, md5 EQUAL (i), s14 DS=50 parity (ii), decode-path source identity (iv); QP OK and reviewer verdict on the exact head.
- G3. EXP-024 Am.10 merged (content in 1), QP OK and reviewer verdict, before the Q50 process starts.
- G4. Q50 wrapper merged (3), QP OK and reviewer verdict.
- G5. DEC-031 note merged, QP OK. It covers: (a) the P0 proxy departure and its bar (4); (b) the bot_id, books-file membership and the rent-charged deciding choice; (c) G6.
- G6. A3 guard (h). FROZEN_FILES includes tools/h5_shadow.py, whose blob is pinned to 1f7a89c (23b9ccc). The merged A3 job cannot check the Q50 sha's own file. Acceptable cover, declared in the G5 note: the decode-path source identity proof of 2 (iv), with a statement that A3 parity run on the 1f7a89c decoders transfers to Q50 because its decode functions are byte-identical. NOT acceptable: editing pins or the check while a2 runs (6.2).
- G7. P0 fixed-first entry posted, then the job after 10-24T00:10Z, then PASS.
- G8. Registration with the Q50 wrapper, the books-file swap in the same command, and the kill-review line, all on 10-24 before 23:50Z. Then the Q50 process started from a clean checkout at its frozen_sha, after registered_at_ms, with `--fresh-forward ARENA --q-star-sol 50` and its own MemoryMax.
- G9. One green a3-daily run on the new books file, after registration and before the clock.
- G10. Records PR open on origin before the clock: the line, books-file sha256 and copy, pins, H, P0 ids and sha, rent choice, frozen blobs.

## Not measured
I did not run any of the following:
- P0, any count, md5 or parity;
- the Q50 trigger rate on the shadow's own feed (COUNTS is arena tape, a different feed, and an upper bound);
- fast-0 memory headroom;
- whether a kill-review week exists after 10-25;
- the owner notebook entry.

The claim that l3_count.py computes no price rests on its source header and the trigger-test lines read here, not a full audit. No Q50 edge exists anywhere, and none is claimed.
