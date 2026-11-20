# Rotation lane method card: `osb-old`

Method card under [DEC-036](../../../DEC/DEC-036-rotation-lane.md) §3, from [the template](../METHOD-CARD-TEMPLATE.md). It registers a **paper open simulation book** (DEC-035 Am.9 §1). It loads no rotation book, starts no live instance and writes no belief record. **No book has cleared the promotion gate. Nothing here says osb-old, FLOOR or any book makes money.** No outcome of any pool aged over 200 h has been computed by anyone.

| Field | Value |
| --- | --- |
| bot_id | `osb-old` (paper book id; never `floor-lane-1`, `h5-lane`, `l1-floor-v2` or an `hv4-` id) |
| Card version / date | v2, 2026-11-20 (after reviewer and QP verdicts on 86306da3); v1 was the first push of PR #1471 |
| Library row | `n1120-floor-old` (reports/2026-11-20/new-strategy-ideas.md idea 2; QP-RULING-1120-floor-rotate-old Q1 and its Alternatives) |
| Card author | worker (the manager owns it) |

## 1. Mechanism
1. FLOOR buys a drained PumpSwap vault (real vault <= 0.5 SOL, price bound near the virtual-reserve floor) after a 6 h drop and a sell print, and takes 1.5x or exits at 24 h. The price bound is the same at any age; only the revival hazard can differ. Pools still trading at 8-21 days may have a living holder base, a bot or a CTO that keeps showing up. This is a hypothesis, not a measurement.
2. Stops if old drained pools are mostly dead (24 h volume below the rule's 1 SOL floor): the book then simply does not trade (count too low). Seen as entries per day in the book's own status. Nothing else is claimed.
3. FLOOR sibling: same host, same rule, only AGE_LO/AGE_HI differ (720000 s and 1814400 s). The pool set is disjoint from FLOOR's 24-200 h set by age, except at age exactly 200 h (720000 s), where both bounds are inclusive; the executor's same-pool refusal never binds against FLOOR otherwise. The library's "aged-pool floor hold" negative (hunt23-a-slow-D) needs real quote >= 30 SOL; this book needs a vault <= 0.5 SOL. Different mechanism.

## 2. Which sim it needs
- [x] Open simulation book on arriving tape (DEC-035 Am.9 §1), from a recorded start instant S.
- [x] Sibling config of FLOOR on FLOOR's own host code (lane host, gap guard).
Why: the question is forward behaviour of an unmeasured age band. **The MANAGER chose the forward-only route on 2026-11-20.** QP-RULING-1120-floor-rotate-old Q1 did not choose it: Q1 allowed a pinned creates-only October seed (YES under conditions), and lists a forward-only book only under "Alternatives", "only as the forward stage after a count passes", "too slow to decide anything this month". **No count screen preceded this book** (no count has passed). Skipping it is within the manager's authority for a paper book, **but a QP ruling licensing a forward-only osb-old is owed before S** (cited as owed; preflight A3/A5). The first read cannot come before about S + 14 full-coverage days.
Pool births come only from the book's own follow of keep tape from `seed_from_hour` **2026-11-03T03** (the keeper's earliest hour: `keep_earliest` 2026-11-03T03, contiguous, 26 hours 11-02T01..11-03T02 not in its source; every running book seeds there). No October tape, no creates-only seed read.
**Start-manifest disclosure (verbatim, goes in every report of this book):** "forward-only birth seed: a pool's age is known only if the pool was admitted from keep tape at or after 2026-11-03T03; the full 21 d band is covered only at hours >= 2026-11-24T03; before that, the band is 200 h to the coverage ceiling (hour minus 2026-11-03T03). Pools born in a feed gap are never admitted. Counts are a lower bound; in-sample; not a gate read."
Start instant: set by the manager, **not before 2026-11-24T04** (seed 2026-11-03T03 + 505 h, osb_follow's own check for AGE_HI 21 d). The 48 h proof and the dry run push S later still (start plan). **osb-old cannot start at the 11-22/23 look.** If S moves, the full-coverage date in the disclosure and §4 move with it (= seed_from_hour + 21 d, independent of S; S only needs to be at or after it). Tape source: the keeper's `keep` dir (SEALS-BRIEF (k)). Simulator: `osb_follow.py` at the merge commit through `scripts/research/osb_follow_old.py`, whose own sha256 and AGE_DROP_OLD_S are folded into `code.sha256` (a plain `osb_follow.py` restart with this book's manifest is refused).

## 3. What makes this sim believable
1. **Fills and latency.** The manifest equals osb-a's except `rule_overrides` (lane host, `gap_lookback_s` 21600, stage-1 flags false, send 160000, stakes); no latency override; strict gap rule (`gap_exclusion` on, lane guard). **Code version:** at the merge commit `code_sha256()` of plain osb_follow is osb-oq-v1's version (73f5f9ec at 86306da3), not osb-a's and osb-ce1's (5ee17b2c, commit 457795f4); four simulator files differ (osb_follow.py, floor_gap_range_engine.py, floor_lane_host.py, floor_nov_paper.py). Decision equivalence of the strict path rests on the #1380/#1390/v3-plumbing proofs and the proof step in the start plan, not on "same engine". The live recon (#2823) compares only FLOOR's own entries; this book has no live counterpart, so there is no measured live-minus-sim bias for old pools. State that in every read.
2. **Costs.** 160,000 lamports per send (same as osb-a); pool fee per pool by the engine; rent as osb-a's engine.
3. **PumpSwap pricing.** V-priced exactly as osb-a (same code); the follow starts after the 2026-10-04 correction.
4. **Per-token fee check.** As osb-a: engine fee tier per pool; not re-measured for old pools.
5. **Fail legs.** Flat 15% and pressure slope scale 1, from the same reader as osb-a (not yet run).
6. **Trial count.** One rule variant, one age band, chosen before any outcome of that band. Closest age-band family members already tried: `age12_200` (priced provisionally 2026-11-12, REJECT) and `age24_400` (count-only, +0 pools, untestable: history window). FLOOR variants vault/vol/r85/r90/TP were tried on 11-03..11-11 (floor-variants.md). This card adds one cell; the claim that no outcome of a pool aged over 200 h was computed stands (age24_400 added +0 pools). Treat any positive as shrunk by the family's trial count.
7. **Concentration.** Reported at the read: total after dropping the top 3, per-day totals, share from one mint or day. Not yet measured.
8. **Basis.** Net of the engine's cost table; gross is labelled gross.

## 4. Believe-the-sim criteria for this book
- Deciding latency cell: osb-a's (entry lands 4.085 s after the decision).
- Criteria (declared before any output): at least 2 distinct entries per UTC day on full-coverage days (hours >= 2026-11-24T03) as the capacity bar; the book needs at least 14 full-coverage days and at least 30 closed positions before a belief record is considered; shrunk mean per trade under flat 15%, pressure and mu0 at 0.10 all > 0; ex-top-3 > 0; the matched FLOOR control (osb-a) on the same days is shown beside it. Below 30 closed positions at day 14, the book is not decidable (not a pass).
- Why: a rare-event book, so days and positions both bind.

## 5. Negative but plausible
1. Plausible negative: too few old-pool revivals seen yet (rare events); the 21 d band fills only from 2026-11-24T03.
2. One extension: 14 more full-coverage days, fixed now.
3. Ends early on: a seal problem; the 6 h ratio rule's inputs found wrong for old pools; a cost higher than the table.
4. Parameters fixed. A change is a new book with a new start instant.

## 6. Live kill and retire rule
1. Per-book loss cap: DEC-036 §5 item 2 default (0.40 SOL without a 0.25 start row). No live step is proposed by this card.
2. Decay monitor calibration: not applicable until the sim exists with trades; recorded as "n/a, because no sim distribution exists (zero closed positions)". Calibration happens at the belief record.
3. Retire check: DEC-036 §6 item 6 defaults.
4. Mechanism-specific stop: if a pump/PumpSwap program redeploy changes the decode (as 2026-11-15T17:20Z), figures from those hours carry the DEC-035 disclosure line.

## 7. Decision host path
- [x] Own code, already pinned: FLOOR's lane host is unchanged; the new file is only the front end `scripts/research/osb_follow_old.py` (sets `tools.arena_pool_table.AGE_DROP_S` to 21 d + 1 h for the process; refuses any other overrides). `osb_follow.py` is byte-identical to main (md5 9095527f47bc9b20cccf026ac21901dd), so no md5 replay is owed for shared runner code and the existing books' code.sha256 is untouched. The front end's own proof route is in the start plan (step 4). A live port would need an l1 rule pin with AGE_HI overridden, a DEC-035 amendment and its own proof.
Frozen sha: the PR merge commit (the manager records it in the start plan).

## 8. Ladder plan
- First rung 0.10. Up-step: DEC-036 §5 item 1 minimum at the current rung plus live fill slip within the sim's assumption. Above 0.25: not in the envelope.

## 9. Records
- Belief record: none. Start line in `ops/<manager>/osb-book-starts.txt`, notebook entry, LAB_STATE row (DEC-036 §7).
