# Q* = 60 sibling `h5v1q60-boostfloor-a1`: code, ops, timeline and recommendation (DRAFT)

Written 2026-10-27 from 21:21Z (`date -u` on mal-research-0) for the manager. Records only. Companions: DEC-031 Amendment 19 (draft) and `EXP/EXP-024-RULE-Q60.md` in this PR; the declared observation `EXP/EXP-024-note-q60.md` follows in a later PR (B2 split), merged before the smoke with `q60_sha` filled (its draft text is at #1064 head `356b61be`). Revised 2026-10-27 on quant-proof's comments 6454061741 and 6454095235. Nothing is implemented, registered, counted, priced or submitted. No book has cleared the promotion gate. Nothing here is a profit claim. The not-opened list is Amendment 19 item 10's.

## 1. What the book adds, and what it does not
- **Nesting.** On every pool the first Q <= 60 sell is at or before the first Q <= 50 sell. So Q60's trigger pools are a superset of Q50's and s1's, which in turn contain Q40's. On a shared pool, Q60 enters at the same print or an earlier one, at a higher Q (less floor protection), with the same 330 s exit.
- **Added information:**
  1. **The 50 < Q <= 60 band:** pools whose dump never brought Q down to 50 inside [0, 300] s.
  2. **Earlier entries** on shared pools.
- **Added counts (upper bounds, arena tape, partial days, a different feed from the shadow's):** 45 / 47 / 32 strict raw triggers against Q50's 36 / 31 / 21 on 10-19..21. That is 124 against 88, about +41%, so roughly 12 band and earlier-entry triggers a day at most.
- **Correlation with Q50: high by construction.** Most Q60 trades are on Q50's pools, with the same exit and an entry the same or a little earlier. The two P&L series share the BOOST-floor premise and the regime. If the premise fails, both fail. Q60 is a capacity and decidability hedge, not an independent test (QP-RULING-1021-a3 Q5: "more triggers is a capacity argument, not an edge argument").
- **Discovery says Q60 is the worst of {40, 50, 60}** (monotone, smaller Q\* better). Expect a lower per-trade edge than Q50.

## 2. Code (none in this PR; each its own PR or tag, with tests, QP OK and a reviewer verdict; no path on Q50's pinned list changes on main)
- **Q1. Id test** (new test file): the three Amendment 5 item 2.1 properties for `h5v1q60-boostfloor-a1`.
- **Q2. Q60\* sha** (`q60_sha` = the commit tagged `q60-base-1`, off tag `q50-succ-base-1`, never merged; target tagged by 2026-11-02T00Z). The exact diff is Amendment 19 item 2 (a) to (i):
  - in `tools/h5_shadow.py`: `Q_STAR_ALLOWED` gains 60.0; a per-level rule table {50.0: Q50's id and sha, 60.0: `H5-BOOSTFLOOR-Q60 v1`, `f36afaae…`} feeds the start record (F50 lines 3287 to 3288, S\* 3290 to 3291) and every use of `Q50_RULE_ID` / `Q50_RULE_SHA256`; `SUCC_END_MS` unchanged;
  - **`compare_frozen` refuses when no frozen row matches the level.** This is quant-proof's MINOR latent defect: at Q\* != 40 against `boostdip_frozen_conf.parquet` (D = 40 only) it silently returns 0 rows. The fix goes on the Q60\* tag only, never into the pinned `tools/h5_shadow.py` on main;
  - tests: the Q\* 60 trigger at exactly <= 60e9; 40/50 start records byte-identical to S\*'s; 45 and 70 refused; the AST and decode identity against S\*; the seal grid at Q\* 60 on synthetic 2026 times; the `compare_frozen` refusal.
- **Q3. Equivalence and parity on 2026-09-20** (exploration day, fast-pool-0918; host jobs on mal-fast-0, one heavy job at a time).
  - Runs A (default) and B (`--q-star-sol 50`) must equal #1548's md5s at S\*.
  - **Run C follows Q50's #789 precedent** (QP comment 6454095235). It is a fresh, unmodified `s14_boostdip.py` run (sha256 `70becfb7…`) with DS=60 on 09-20, compared through `parity_v2.py` with the shadow's `--q-star-sol 60` replay. It is judged against the same 2 residual pools of D1 in `ARTIFACTS/lab/arena-records-2026-10-24-q50.md`: `F9fckcTp…`, a window cut, and `7j99BJ9h…`, replay-only optimistic. **Any other difference is a STOP.** It prints counts and pool ids only. No frozen discovery rows are used.
- **Q4. P0 counter** `scripts/research/l3_count_q60p0.py`: `l3_count_q50p0.py` with its output restricted to the `G0_wsol_band|trig|Q60|strict` cell. The pinned diff is printed in the records PR. Its test: one fixture hour, output keys exactly that cell plus gap counts.
- **Q5. Wrapper, read-instant and register entry:** one row in s1's book-table module (s1 plan items C2 and C3; Q50's `arena_h5_score_q50.py` and `arena_h5_q50_instant.py` are not edited). **Fallback (B3):** if that module has not merged with QP OK and a reviewer verdict by 2026-11-03T00:00Z, these entries are built standalone in a new module of the same form. A late build is a slip, never a stop. The row is `{h5v1q60-boostfloor-a1: Q60*, clock, clock + 14 d, rule, f36afaae…, q_star 60.0, exit v1, s0 margin 60 s}`. The start-record checks are as s1's, with Q\* 60.0 and the Q60 rule id and sha.
- **Q6. Books loader:** `tools/arena_h5_books.py` `EXIT_RULES` gains the id, and a pin is added for the new books-file sha (s1 plan C4 pattern). Q50's reads keep the v2 copy from a pre-change worktree.
- **Q7 (later, live only, DEFERRED).** These are needed only for a Q60 live canary:
  - the executor `Book` row (`tools/h5_arena_executor.py`, a pinned path);
  - the ladder (`tools/h5_arena_ladder.py`, a pinned path);
  - note part B;
  - its own DEC-032 copy (next free DEC number). At most one H5-family live canary (Q50, s1 or Q60) runs at any instant (B4); a cross-book pool lock comes only under a later DEC with a QP ruling.

  They wait until Q50's X is settled or Q50 has ended without a PASS, and none is needed before Q60's earliest read.

## 3. Ops
- **Shadow job on mal-fast-0.** It runs as a MiScusi job, resumable false, from a clean checkout at Q60\*: `tools/h5_shadow.py --fresh-forward ARENA --q-star-sol 60`, out dir `$HOME/data/arena/h5-q60-<clock>`. It runs under `systemd-run --scope -p MemoryMax=<cap>`, so a MiScusi cancel needs a ps check afterwards.
  - The cap and the fit check are Amendment 19 item 8's. Before submit: `systemctl show user-1002.slice -p MemoryCurrent` plus the sum of the `MemoryMax` values of Q50 #1419/#1421, s1 (from 11-05 20:00Z) and Q60 must fit the slice.
  - The cap is chosen from the Q50 shadow's configured cap, read from its job spec (not its output), and recorded in the registration record.
- **Smoke.** At least 2 h, scratch root, its own `MemoryMax`, blocking list notebooked first, after the note PR merges. **The fit check binds the smoke too (B6):** about 11-03 it runs beside Q50's live shadow #1419/#1421. MemoryCurrent plus the sum of every concurrent H5 `MemoryMax` must fit `user-1002.slice`, or the smoke waits. A first refusal is a slip.
- **A3.** Books-file swap in the registration command (the s1 pattern). The green run is the next official 07:10Z or 19:10Z run. The no-sticky-non-PASS check is made at the swap and at the green run. The chains cover clock + 14 d plus the next 07:10Z run.
- **Jobs.** Score loop with the Q60 dir, a day-1 dry score (counts only), and a halt-alert job (M3/M4 pattern). All are host-side MiScusi jobs, sh-only.
- **DEC-032 copy.** Later (Q7). C0 needs Q60's own screen PASS. After a Q50 or s1 FAIL or NOT_DECIDABLE, the owner is asked afresh (Amendment 19 item 7).

## 4. Timeline to the clock (clock fixed: 2026-11-07T00:00:00Z; the manager merges; QP = quant-proof with a forced verdict in its own worktree; R = reviewer)
| By (UTC) | Step | Reviews |
| --- | --- | --- |
| **10-29T23:00Z** (hard: before 10-30T00Z) | #1064 merged: Am.19 + rule file (the decision) | QP OK + R on the exact head |
| 11-02T00Z | Q60\* tagged (Q2) with Q3 Runs A, B and C done (C: STOP on any difference beyond the 2 D1 pools); Q4 counter merged, P0 fixed-first then job | QP on the diff, the md5s and parity; R |
| 11-03T00Z | Q1, Q5 (s1's table row, or the standalone fallback), Q6 merged; **note PR** (`EXP/EXP-024-note-q60.md` with `q60_sha` filled, B5 wording) merged | QP + R; the note meets QP-RULING-1027-s1-r2's conditions (cited by path and blob, SEALS-BRIEF, pointer) |
| 11-03 to 11-04 | Am.6 smoke at `q60_sha`, after the note, under the memory fit check beside Q50's shadow | manager; QP reads the blocking-list result |
| 11-05, 14:30 to 18:30Z | Registration + books swap (one command), after s1's swap on 11-04; notebook copy (H, end, P0, who chose, q50p0.json not opened) | manager (job) |
| 11-05T19:10Z | Green A3 run (fallbacks 11-06T07:10Z, 19:10Z) | manager, verdict fields only |
| 11-06, after about 00:10Z (Q50's shadow cancelled) and by 22:59Z | Q60 shadow start, own `MemoryMax`, at least 1 h clean; score, dry-score and alert jobs | manager (jobs) |
| **11-07T00:00Z** | Clock (slip: the first 00:00Z after the last item, with a new P0) | — |
| 11-12T00:00Z | Earliest read boundary; the item-1 tool merged before it | QP on the read |
| 11-21T00:00Z | End (H = 14) | — |

## 5. Recommendation
**Worth it only in the lean form, which the manager chose (manager17, about 2026-10-27T21:2xZ): clock 2026-11-07T00:00Z, decided and merged before 10-30T00Z, built on s1's machinery.** Not worth it as a rush for 10-31.
- **Gain:**
  - about +41% triggers a day by the proxy counts (an upper bound), so a decidable read is somewhat more likely within H = 14 than for a Q50-rate book;
  - a second rule-variant read in mid-November, if Q50 and s1 end NOT_DECIDABLE by count.
  
  That is the realistic gain: decidability, not evidence. Live capacity gains nothing additive: at most one H5-family live canary runs at any instant (B4). So a Q60 canary, if Q60 passes on its own, replaces Q50 or s1 live; it never runs beside them. Its only capacity gain is the band pools (at most about 12 a day, an upper bound) within that one canary.
- **Costs:**
  - **N + 1**, and a third nested look at the same premise. The family chance of at least one false screen pass is up to 14.3% across Q50, s1 and Q60.
  - **Review load:**
    - one new sha with md5 and identity proofs;
    - a proxy-P0 departure;
    - a note that depends on a pending QP ruling;
    - a DEC amendment;
    - a books swap;
    - a smoke;
    - later, a DEC-032 copy and a deferred executor change.

    Done on 10-31 it competes with the Q50, HV4 and HV6 reads and s1's builds.
  - **fast-0 memory:** a third H5 shadow. An OOM would void Q50's open positions (DEC-031 Am.3 8(j)) and gap-halt any live feed.
- **Why 11-07 is cheaper:**
  - Q50's shadow is gone, so there are at most two H5 shadows (s1 and Q60);
  - Q5/Q6 are a row in s1's table, and the books swap and green run follow s1's by one day;
  - the P0 window 10-21..23 is still within 15 days;
  - its read lands about 11-12..21, beside s1's.
- **Q60 PASS with Q50 FAIL:** it licenses only a Q60 C0 under its own copy, with a fresh owner ask stating the FAIL. It is never evidence for Q50 or s1. It is labelled best-of-siblings on a cell discovery ranked worst.
- **If the owner wants it only "if Q50 disappoints",** do not do that. Registering it after seeing Q50's verdict is selection. Decide now, or not at all.

## 6. Quant-proof's answers (comments 6454061741 and 6454095235)
1. **Separate-file declared observation:** VALID, under QP-RULING-1027-s1-r2's four anchors and conditions (1) to (5), for the later note PR.
2. **Proxy P0 on 10-21..23:** ACCEPTED, with two disclosures (Am.19 item 3): #1413's output very likely holds the Q60 cells, so the P0 is blind only if nobody opened it; and by nesting the bar is near-certain, so the P0 is a formality.
3. **Run C:** the #789 precedent, a fresh s14 DS=60 run on 09-20 through `parity_v2.py` against the same 2 D1 pools, STOP otherwise. No frozen rows, and no explore-0814.
4. **Transitive guard (h):** yes. The Q60\* AST and decode identity test is committed at the tag and cited by job id.
5. **One canary per pool:** not enough. It is replaced by "at most one H5-family live canary at any instant" (B4).
6. **Multiplicity (ruling (b)):** the family line (up to 1 - 0.95^3 = 14.3% at a nominal 5% per look, with its derivation) is printed in Q60's and s1's reads and in every owner C0 ask for Q50, s1 or Q60. Q50's read is untouched.

Side note for the manager (QP-NOTE-1027-q50-reference.md): no Q50 proof claims a frozen D == 50 comparison; Q50 is unaffected.

