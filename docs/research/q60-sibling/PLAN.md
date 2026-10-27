# Q* = 60 sibling `h5v1q60-boostfloor-a1`: code, ops, timeline and recommendation (DRAFT)

Written 2026-10-27 from 21:21Z (`date -u` on mal-research-0) for the manager. Records only. Companions: DEC-031 Amendment 19 (draft), `EXP/EXP-024-RULE-Q60.md`, `EXP/EXP-024-note-q60.md`. Nothing is implemented, registered, counted, priced or submitted. No book has cleared the promotion gate. Nothing here is a profit claim. The not-opened list is Amendment 19 item 10's.

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
- **Q2. Q60\* sha** (branch `claude/q60-base` off tag `q50-succ-base-1`, tagged `q60-base-1`, never merged). In `tools/h5_shadow.py`:
  - `Q_STAR_ALLOWED` = (40.0, 50.0, 60.0);
  - a per-level rule table {50.0: (`H5-BOOSTFLOOR-Q50 v1`, `b3f8ecbf…`), 60.0: (`H5-BOOSTFLOOR-Q60 v1`, `f36afaae…`)} used at the start record (F50 lines 3287 to 3288) and wherever the code names `Q50_RULE_ID` / `Q50_RULE_SHA256`;
  - `SUCC_END_MS` unchanged (2026-12-01T00Z).
  
  Tests:
  - a Q\* 60 engine triggers at exactly <= 60e9 on a fixture;
  - the 40 and 50 start records are byte-identical to S\*'s;
  - 70 or 45 is refused;
  - the AST identity of every other function against S\*;
  - the seal grid at Q\* 60 on synthetic 2026 times.
- **Q3. md5 job on 2026-09-20** (exploration day, fast-pool-0918; a host job on mal-fast-0, one heavy job at a time):
  - Run A (default) and Run B (`--q-star-sol 50`) must equal #1548's md5s at S\*;
  - Run C (`--q-star-sol 60`) runs `compare_frozen` against the frozen discovery rows with `D == 60` (`h5_shadow.py:3443-3449`). `refuse_replay` allows Q\* != 40 replays before `Q50_REPLAY_CUTOFF_HOUR`, so 09-20 is admitted.
  
  STOP on any unexplained difference.
- **Q4. P0 counter** `scripts/research/l3_count_q60p0.py`: `l3_count_q50p0.py` with its output restricted to the `G0_wsol_band|trig|Q60|strict` cell. The pinned diff is printed in the records PR. Its test: one fixture hour, output keys exactly that cell plus gap counts.
- **Q5. Wrapper, read-instant and register entry:** one row in s1's book-table module (s1 plan items C2 and C3; Q50's `arena_h5_score_q50.py` and `arena_h5_q50_instant.py` are not edited). The row is `{h5v1q60-boostfloor-a1: Q60*, clock, clock + 14 d, rule, f36afaae…, q_star 60.0, exit v1, s0 margin 60 s}`. The start-record checks are as s1's, with Q\* 60.0 and the Q60 rule id and sha. If s1's module has not merged when Q60 needs it, Q5 waits for it; it is not duplicated.
- **Q6. Books loader:** `tools/arena_h5_books.py` `EXIT_RULES` gains the id, and a pin is added for the new books-file sha (s1 plan C4 pattern). Q50's reads keep the v2 copy from a pre-change worktree.
- **Q7 (later, live only, DEFERRED).** These are needed only for a Q60 live canary:
  - the executor `Book` row (`tools/h5_arena_executor.py`, a pinned path);
  - the ladder (`tools/h5_arena_ladder.py`, a pinned path);
  - note part B;
  - its own DEC-032 copy (next free DEC number), with the one-canary-per-pool rule.

  They wait until Q50's X is settled or Q50 has ended without a PASS, and none is needed before Q60's earliest read.

## 3. Ops
- **Shadow job on mal-fast-0.** It runs as a MiScusi job, resumable false, from a clean checkout at Q60\*: `tools/h5_shadow.py --fresh-forward ARENA --q-star-sol 60`, out dir `$HOME/data/arena/h5-q60-<clock>`. It runs under `systemd-run --scope -p MemoryMax=<cap>`, so a MiScusi cancel needs a ps check afterwards.
  - The cap and the fit check are Amendment 19 item 8's. Before submit: `systemctl show user-1002.slice -p MemoryCurrent` plus the sum of the `MemoryMax` values of Q50 #1419/#1421, s1 (from 11-05 20:00Z) and Q60 must fit the slice.
  - The cap is chosen from the Q50 shadow's configured cap, read from its job spec (not its output), and recorded in the registration record.
- **Smoke.** At least 2 h, scratch root, its own `MemoryMax`, blocking list notebooked first, after the note merges.
- **A3.** Books-file swap in the registration command (the s1 pattern). The green run is the next official 07:10Z or 19:10Z run. The no-sticky-non-PASS check is made at the swap and at the green run. The chains cover clock + 14 d plus the next 07:10Z run.
- **Jobs.** Score loop with the Q60 dir, a day-1 dry score (counts only), and a halt-alert job (M3/M4 pattern). All are host-side MiScusi jobs, sh-only.
- **DEC-032 copy.** Later (Q7). C0 needs Q60's own screen PASS. After a Q50 or s1 FAIL or NOT_DECIDABLE, the owner is asked afresh (Amendment 19 item 7).

## 4. Timeline to the clock (the manager merges; QP = quant-proof with a forced verdict in its own worktree; R = reviewer)
| Step | Earliest-legal path (clock 10-31T00Z) | Recommended path (clock 11-07T00Z) | Reviews |
| --- | --- | --- | --- |
| This PR's texts (Am.19, rule file, note) | QP + R round 10-28, merge by 10-29T23Z, together with QP's separate-file ruling | the same, by 10-29T23Z (the decision-timing line) | QP, R |
| Q60\* (Q2, Q3) | built and md5 job 10-28; QP OK 10-29 | 11-01 to 11-02 | QP on the diff and md5, R |
| P0 (Q4), fixed-first then job | 10-29 | 11-02 | QP on the counter diff |
| Q1, Q5, Q6 | 10-29 to 10-30 (Q5 needs s1's C2/C3 first) | with s1's C2 to C5, 11-01 | QP, R |
| Am.6 smoke at Q60\* | 10-30 day | 11-03, beside s1's smoke if memory fits, else 11-04 | manager; QP reads the blocking-list result |
| Registration + books swap | 10-30 by 18:30Z | 11-05, 14:30 to 18:30Z, after s1's swap (one change at a time) | manager (job) |
| Green A3 run | 10-30T19:10Z | 11-05T19:10Z (fallback 11-06T07:10Z, 19:10Z) | manager, verdict fields only |
| Shadow start (>= 1 h before the clock) | 10-30 20:00 to 22:30Z | 11-06, after Q50's shadow is cancelled (about 00:10Z), with at least 1 h before 11-07T00Z | manager (job) |
| Clock | **2026-10-31T00:00Z** | **2026-11-07T00:00Z** | — |
| Earliest read boundary | 11-05T00Z | 11-12T00Z | QP on the read |
| End (H = 14) | 11-14T00Z | 11-21T00Z | — |

The earliest-legal path puts two QP rounds, a new sha, a P0 job, a smoke and a registration into 10-28..10-30. Those days are Q50's and HV4's earliest read days and HV6's T0, and they overlap s1's C1 to C5 builds. It also runs three H5 shadows on fast-0 at once from 11-05 20:00Z to about 11-06T00:10Z.

## 5. Recommendation
**Worth it only in the lean form: clock 2026-11-07T00:00Z, decided and merged before 10-30T00Z, built on s1's machinery.** Not worth it as a rush for 10-31.
- **Gain:**
  - about +41% triggers a day by the proxy counts (an upper bound), so a decidable read is somewhat more likely within H = 14 than for a Q50-rate book;
  - a second rule-variant read in mid-November, if Q50 and s1 end NOT_DECIDABLE by count.
  
  That is the realistic gain: decidability, not evidence. Live capacity gains little:
  - on shared pools a second live entry would double stake and impact, so the one-canary-per-pool rule allows only one;
  - only the band pools (at most about 12 a day, an upper bound) are additional live trades;
  - and only if Q60 passes on its own.
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

## 6. Questions for quant-proof
1. Is the separate-file declared observation valid for the Look-2 seal? This is the same question as on #1057, and the same ruling serves.
2. Is the Q60-only proxy P0 on Q50's P0 hours (10-21..23) an acceptable departure under Amendment 4 item 2.1 / Amendment 5 item 1, given the same counter and hours?
3. Is Run C (`compare_frozen` against the discovery `D == 60` rows on 09-20) the right equivalence reference for the new level?
4. Does the transitive guard (h) cover extend to Q60\* with the same proofs as s1?
5. Is the one-canary-per-pool rule for the H5 family enough to keep live impact from stacking?
