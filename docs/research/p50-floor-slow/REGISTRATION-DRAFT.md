# P50 FLOOR-SLOW: registration draft (NOT registered)

Written 2026-10-26 from 19:31Z (`date -u` 2026-10-26T19:31:28Z) on mal-research-0, branch `claude/p50-floor-slow` from origin/main 98e62743. Lane P50, manager17.

**Status: DRAFT, NOT registered.** This file registers nothing, sets no clock, spends no N and writes no `bots.json` line. A registration needs its own DEC-031 amendment (PR 7 of P35), merged with quant-proof's OK on the exact head and a reviewer verdict, after every item in section 9 has merged.

**No book has cleared the promotion gate. FLOOR has 0 out-of-sample trades. Nothing here says FLOOR makes money.** Every FLOOR P&L figure quoted below is in-sample or a neighbour-block screen, copied from its record.

Sources: `docs/research/p32-survivor-audit/AUDIT.md` lane 1 (origin/claude/p32-survivor-audit); PR #905 and `QP-RULING-1025-am15.md` (sha256 `0595811f...211c94`, verdict NO); `QP-RULING-1025-seal-lift.md` (sha256 `b04a57eb...aa52`) section (3) f1-f5; P35 `DESIGN.md` (PR #945 at 50c3c08d) and `QP-RULING-1026-p35-design.md` (sha256 `2e53ed9a...62f2cc`); PRs #952, #954, #957, #962 (all draft, unmerged); DEC-031 Am.4 item 2, Am.6, Am.8, Am.9 (+ Errata), Am.11, Am.12, Am.15 on origin/main.

## 0. What is new versus FLOOR v2 (Am.9) and #905

| | Am.9 `l1-floor-v2` (closed 10-25) | #905 draft Am.15 (QP NO) | FLOOR-SLOW (this draft) |
|---|---|---|---|
| Rule | `arena_rule_l1.py` at 5e12f8f5 | same | same file, byte-identical (blob `cf4cf1c4`) |
| Host | `arena_rules_v2.py` HostV2, one process | HostV2, two segments, plain restart | `arena_rules_v3.py` **HostV3** (#962): state-carrying segments, book lock, `segment_end`/`segment_resume`, D rule, probe |
| Feed | f5bd26e7, no restart in clock | f5bd26e7, one restart in the read span | `arena_feed_v2.py` (#952) overlap takeovers, fail-closed |
| bot_id | `l1-floor-v2` | `l1-floor-v2` | **new**, proposed `l1-floor-slow-v3` (DESIGN 3.3); `l1-floor-v2` is never reused |
| H | 12 d pinned | min(90, max(14, ceil(150/rate))), H <= 25 | min(90, max(14, ceil(150/rate))) from a fresh binding P0 at S; no H cap other than 90 |
| Rate bar | rate_att_lo >= 12.5 | >= 6.0 | >= 4.0 (P32 lane 1 kill (a)); see 2.3 |
| Restart budget | 0 | 1 (QP: wrong denominator) | 0 plain restarts; only proven HostV3 hand-offs and proven v2 takeovers |

## 1. Frozen rule pins

The rule is FLOOR v2 `l1_rule_tpfix` as ported to `tools/arena_rule_l1.py`. Pins as read at 5e12f8f5 and at origin/main 98e62743 (identical blobs):

| File | git blob | sha256 |
|---|---|---|
| `tools/arena_rule_l1.py` | `cf4cf1c4b08476b96162f735ce9ec730625b402a` | `de6e371e9479f28dbb610895e2cffb7a6704a762d3f02d3a9c49a08e3019500c` |
| `tools/arena_pool_table.py` | `daa22d3ab1a6c446c3ea44003ae9cdb6f53548cd` | `78955dacb9f8c041cace52119ddea6758570b3b9425b37ee68b0c7c6487c7692` |
| `tools/arena_paper.py` (section 1.4 pin; subclassed by #957, not edited) | `5eef156bb617138a4c04fca6779f9ff4c5215b13` | `9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514` |
| `tools/arena_stack.py` | `092bbce0854dc6760706608cb706108f67d0179d` | `a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778` |
| `tools/arena_state.py` | `38e4c608115c99a52d202a6c82cd2a92c214879e` | `8af345618e51ca480ee7538717aa001b4ad7ed5ef38941faaec812bbe217f639` |
| `tools/arena_rules.py` | `a107a677b0d70d7363543478de4e681cb0e7c3bc` | `048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af` |
| `tools/arena_rules_v2.py` | main `818d9be5cad74a90e5282a2927bbfffae1c365fd` (has `--stack-version`); 5e12f8f5 had `fd7a033a` (no `--stack-version`) | to be pinned at S |

Rule constants (from `arena_rule_l1.py` / `arena_pool_table.py` at those blobs):

- **Entry.** Canonical PumpSwap SELL print on a WSOL pool (row quote_mint WSOL tested first); V in [17.5e9, 17.7e9] lamports on the deciding row; pool age from the mint's `complete` row (`TABLE_MODE = "complete"`) in [24 h, 200 h]; real quote vault after the print <= 0.5 SOL (`Q_STAR = 0.5e9`, the "q0.5 cap"); previous print within 6 h (`ALIVE_S`); 24 h volume including this print >= 1 SOL (`VOL_MIN_LAMPORTS`); 6 h spot ratio < 0.80 (`R6_MAX`); one open position per pool; re-entry no earlier than 60 s after the exit decision (`COOLDOWN_MS = 60_000`); uncapped book; `FEED_D = 7`.
- **Exit.** Take profit when post-trade spot with the position kept >= 1.5 x the post-buy mark (`TP = 1.5`, the tpfix mark); else time exit at decision + 1.9 s + 24 h on the 1 s tick (`HOLD_S = 86400`, `LEG_MS = 1900`). **No stop.**
- **Deciding cell.** 1.9 s leg (`LAT_DECIDING`), 0.25 SOL stake (`STAKE_SOL`), rent charged (stranded 2,039,280 lamports, Am.2), **160,000 lamports per send** (DEC-031 Am.8, `--stack-version 2`), E1 floor 384,022 per 0.05 SOL, flat 15% fail AND pressure-fail at slope scale 1.
  - Am.8 item 2 coverage is a precondition, not a given: the registration must cite the executor configuration that sends buy AND sell at exactly 155,000 priority as `path:line@sha` with a test (candidate: `tools/floor_arena_executor.py:53` `PRIORITY_LAMPORTS = 155_000`, #837, dry only). If coverage fails, the deciding send is 505,000.
  - Every read prints the 505,000 leg for comparison (Am.8 item 6) and the dust-flip count and SOL, with the gate re-checked with flipped legs priced as strands (Am.8 item 7; `tools/arena_flip_read.py`).
- **Disclosed records discrepancy.** Am.9 item 9 says Am.8 (160,000) applied to `l1-floor-v2`, but `arena_rules_v2.py` at 5e12f8f5 has no `--stack-version`, so that book would have priced at 505,000. Moot (never registered); FLOOR-SLOW runs at a main-line S where the flag exists.
- **Rule file's own id.** The byte-identical rule file keeps `BOT_ID = "l1-floor-v2"` in its decision meta; the registry bot_id is new (HostV3 `RULE_MODULES_V3`). Disclosed so nobody reads the meta string as the old book.

In-sample basis (copied, not re-measured): REPRICE row `l1:l1`, 4 blocks, 332 trades, 13.3/day, 18/19 days positive, flat +0.01605 / pressure +0.01285 SOL/trade, CI90 lo +0.01118 / +0.00861 (`QP-RULING-1023-floor-fasttrack.md`); best of 51 cells (winner's curse); in-sample screen NOT_DECIDABLE on block-edge exclusion (22% Am.9 item 9; 23.2% `arena_rule_l1.py:3`). Neighbour block fresh-0903: n 62, flat +0.0073 / pressure +0.0051, CI90 lo -0.0032 / -0.0047, ex-top-3 +0.009 / -0.065, 4 of 5 days (AUDIT.md 1b). Those figures are at 505,000 per send.

## 2. Rate and window

### 2.1 Informational count (this lane, job #1678)

COUNTS ONLY, on arena tape hours before 2026-10-25T00Z, under QP-RULING-1025-seal-lift (2)(c) and the arena-lane structure-only count (ledger 2026-10-19 line). Pre-declared in the job script header before the run: days 10-20..10-24 (the 5 full closed UTC days before 10-25; tape starts 2026-10-19T08:05Z, so 10-19 is partial and excluded), rate = mean att_lo over those 5, kill if < 4.0.

- Job: MiScusi #1678 (`j_VeIMTfvW8h_Www`), mal-research-0, git_ref 5e12f8f5, ran 2026-10-26T19:05:16Z to 19:30:42Z, exit 0, peak 4.1 GB.
- Script: `/data/mal/hunt-1008/push-1016/hunt30/p50/P0-pre1025-p50.sh` sha256 `8da7ceaa4b43d7527fd352436d490d5187f388074f6b3987aa6de329a1d524f1` (copy in this directory).
- Tool: `tools/floor_p0_count.py` at 9a403a39, sha256 `e6186ac737d7ca7fcf6be7d369ca25f96b0744ceb18eca3648099546204705b9` (= the Am.9 pin). Table from 2026-10-19T08; no hour >= 2026-10-25T00 read.
- Output `/data/mal/hunt-1008/push-1016/hunt30/p50/p0-pre1025/p0-count.out` sha256 `7865259f905b90d7b04f9bddc7c2385cb178554a24b24da783ff34bc595e4806`; summary `p50-summary.txt` sha256 `69cbc2e2094593c4df3a9b2ac3e1655c3a986122bf1f880441e0fdd120e9f9ae`; directory made read-only by the job.

| UTC day | status | signals | att_lo | att_hi |
|---|---|---|---|---|
| 2026-10-20 | OK | 0 | 0 | 0 |
| 2026-10-21 | OK | 2751 | 5 | 10 |
| 2026-10-22 | OK | 243 | 6 | 9 |
| 2026-10-23 | OK | 581 | 5 | 7 |
| 2026-10-24 | OK | 832 | 7 | 10 |

- **Pre-declared rate: rate_att_lo = 4.6/day** (23 / 5). H = min(90, max(14, ceil(150 / 4.6))) = **33 days**. Kill rule (< 4.0): no kill.
- avg_att_hi = 7.2 (report only; H at att_hi would be 21).
- Comparison only: mean att_lo over 10-22..10-24 = 6.0, equal to A2 #1531's `rate_att_lo = 6.0` on the same days (an independent reproduction of that figure).
- Read with care:
  - 10-20's 0 is a universe ramp artifact: pools must `complete` on the tape and then age 24 h, and the tape starts 10-19T08:05Z. The universe is not full until pools can age 200 h on the tape (about 10-27T16Z). Every pre-10-25 day understates the rate (tool disclosure; QP-RULING-1021-floor C2). Excluding 10-20 would give 23 / 4 = 5.75, but that is not the pre-declared rule and is not used.
  - Under Am.4 item 2.1 a P0 day at 0 blocks registration. This count is not the binding P0, but at 5-7 att_lo a day a 0 day on a fresh P0 is unlikely though not impossible.
  - Per-day counts are 5 to 7. The rate is small; 100 counted trades need about 17-23 days of clock.

### 2.2 The binding rate (owed, not run)

QP-RULING-1025-seal-lift (3) f4 and P35 B-P2: the rate that sets H is a **fresh pinned P0 at S on 3 full closed UTC days after the amendment's merge**, before the clock. `tools/floor_p0_count.py` hard-codes `PINNED_SHA = 5e12f8f5`, so a P0 at S needs a tool revision (or a QP-ruled `--expect-sha` path) merged first (section 9).

- rate = that P0's `rate_att_lo` (mean att_lo over exactly those 3 OK days).
- H = min(90, max(14, ceil(150 / rate))) days (DEC-031 Am.4 item 2.2), pinned in the `bots.json` line before the clock, never extended.
- Not registered if: rate < 4.0 (2.3), any P0 day at 0, fewer than 3 OK days, or the Am.3 item 1 data-loss rule fails (Am.4 item 2.1). N is not spent in those cases.

### 2.3 The bar of 4.0, written with A2 = 6.0 and this 4.6 in view (f4)

4.0 is P32 lane 1's kill rule (a), written before this count. It is below A2's 6.0 and this count's 4.6, and both were in view when this draft was written. The bar only decides whether to spend N; the gate is unchanged and the read is count-based, so a bar near a seen count cannot create a false pass (QP-RULING-1025-am15 Q3). QP's objection to #905's 6.0 bar was that its hand-off premise made the read NOT_DECIDABLE in expectation; that premise is replaced here by HostV3 + v2 (0 plain restarts), so the bar no longer carries a restart budget.

| rate | H | END_H (clock 11-13T00Z) | END_BOOK = END_H + 25 h | expected B (about 105 / rate days; O = 105 at R = 0) |
|---|---|---|---|---|
| 4.0 | 38 | 2026-12-21T00Z | 2026-12-22T01Z | about 26.3 d (12-09) |
| 4.6 | 33 | 2026-12-16T00Z (1797379200000) | 2026-12-17T01Z | about 22.8 d (12-05/06) |
| 6.0 | 25 | 2026-12-08T00Z (1796688000000) | 2026-12-09T01Z | about 17.5 d (11-30/12-01) |
| 7.2 | 21 | 2026-12-04T00Z | 2026-12-05T01Z | about 14.6 d (11-27/28) |

## 3. The gate's own counts

Unchanged from CLAUDE.md and DEC-031 §2, on the deciding cell (1.9 s, 0.25 SOL, rent charged, 160,000 per send if Am.8-covered), under BOTH flat 15% AND pressure-fail at slope scale 1:

- n >= 100 counted trades, after the 5% exclusion cap (Am.6; `handoff_down` exclusions count toward it, P35 Q2 (e));
- >= 5 distinct UTC days, keyed by entry decision date of the scored trades, with a majority positive; fewer than 5 such days is NOT_DECIDABLE, final (QP-RULING-1025-am15 R11);
- CI90 lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile);
- total SOL > 0 after removing the top 3 trades;
- the gate must also pass with Am.8 dust-flipped legs priced as strands.

The trade set is defined as in Am.15 item 2 (attempts of kind `open` decided before B, not dropped, priced at stake 0.25, not excluded); refused buys are attempts, never trades. One read per bot_id; FAIL and NOT_DECIDABLE are final. A PASS is a screen pass only; confirmation needs >= 100 further counted trades on >= 5 further dates (Am.4 item 2.6), which at 4.6-6/day is another 17-23 days, within the same END_H.

## 4. Mechanical read boundary B

As Am.12 item 1 / Am.15 item 1, with this book's own END_H:

> B is the first 00:00Z boundary with clock_start_ms < B <= END_H such that, among the attempts decided in [clock_start_ms, B) and not dropped by Amendment 6 as decided in an excluded hour or restart gap, the attempts of kind `open` (O) and kind `refused`, any reason (R), satisfy O - floor(0.05 x (O + R)) >= 100, and the decision dates of the O attempts cover at least 5 distinct UTC dates. The count uses decision timestamps and row kind only and loads no close, exclusion class, fill, exit, leg or P&L. No such B: the book ENDS NOT_DECIDABLE at END_H, final, counts only, N + 1.

- The read itself runs at B + 25 h (every attempt decided before B closed or excluded: 24 h hold + 1.9 s leg + drain).
- END_H = clock + H d. No attempt decided at or after END_H counts.
- A HostV3 `handoff_down_missed` entry is never an attempt (it was never decided live); its count is printed at the read.
- Tool: `tools/floor_read_instant.py` (#888) hard-codes `l1-floor-v2`, the 10-26/10-27 clocks and H = 12; FLOOR-SLOW needs a parametrised revision (bot_id, clock, END_H) with QP OK and a reviewer verdict, merged before the clock (section 9).

## 5. Kill rules, fixed in advance

1. **Rate (before the clock).** Binding P0 rate_att_lo < 4.0/day, any P0 day at 0, or fewer than 3 OK days: no registration, N not spent. (This lane's informational 4.6 did not trigger it.)
2. **Futility (day 10).** At clock + 10 d (2026-11-23T00:00:00Z, 1795392000000, for an 11-13 clock), over the counted trades decided before that instant and closed: if there are **at least 50** and the **pressure-model mean is <= -0.01 SOL/trade**, the book ENDS FAIL (futility), final, N + 1. Fewer than 50, or mean > -0.01: continue; no other interim look. Run as one pre-registered host-side MiScusi job that prints only `FUTILITY_STOP` or `CONTINUE` and n, never the mean, a leg, a fill or a P&L; the job and its tool merge before the clock with QP OK. It can only stop the book; it can never pass it.
3. **Structure halt.** A PumpSwap program redeploy or a fee-config / global-config change ends the counted window at the redeploy instant unless an automatic check under Am.4 item 1 PASSes; this needs FLOOR-SLOW listed in `a3-books.json` with a QP ruling on whether the H5-specific checks (BOOST items) apply to a non-H5 book. Owed (section 9 item 11).
4. **Hand-off failure.** A hand-off that falls back to the plain restart path (crash, no valid `segment_end`, tampered checkpoint) is an unplanned restart: every open position is `engine_restart`, recorded in LAB_STATE. A v2 takeover with any program `unproven` writes `start` + `process_restart`: a hard gap. Both feed the 5% cap; at about rate x 1 d open positions per event against 5 allowed at O = 105, **one such event is likely to make the read NOT_DECIDABLE** (QP-RULING-1025-am15 core finding, R13). No re-registration "to continue".
5. **Cap.** Exclusions + refusals > 5% at B: NOT_DECIDABLE (Am.6, Am.12), final, N + 1. Any PumpSwap `data_loss` gap from the public node also costs about rate x 1 d of positions (R13).
6. **The read.** Any gate item fails under either fail model: FAIL, final. NOT_DECIDABLE is final. No early read, no extension, no variant, no new cell.

## 6. Schedule and hand-off segments (P35 HostV3 #962, feed v2 #952)

### 6.1 Earliest clock

- HV6 clock T0 2026-10-30T00Z; entries halt 11-11T00Z; last exits about 11-11T12:00Z; read 11-11T13:00Z (QP 1026 Q6). No v2 feed code inside HV6's clock (QP 1026 Q5). HV4 END_BOOK 2026-11-08T13:05Z.
- H' #1652 (f5bd26e7) runs about 10-29T00:30Z to about 11-12T00:30Z (manager17 state; exact instants from its job status, not viewed here).
- **First v2 takeover F1 (H' -> v2)** inside [HV6 last in-clock instant + 1 h, H' end - 1 h], about [2026-11-11T14:00Z, 2026-11-11T23:30Z], pinned in the amendment.
- **Clock C = the first 00:00Z >= max(F1 + 24 h, E3 + warm-up, M + 75 h)**, M the amendment's merge instant (f2). Earliest **2026-11-13T00:00:00Z (1794528000000)**, as P35 4.1 states.
- Binding P0 days must be 3 full closed UTC days after M and closed before E3: for C = 11-13T00Z they are 11-09, 11-10, 11-11 (P0 runs >= 11-12T01:10Z), so **M <= 2026-11-08T23:59:59Z**, with S (containing #952, #957, #962 and PR 6) already merged.
- G1/DR1 evidence window [C - 75 h, C - 3 h) = [11-09T21Z, 11-12T21Z) contains F1. A proven takeover writes no gap row; an unproven one writes `start` + `process_restart`, which G1 counts unless pinned as the window's single exempt hand-off by job ids (<= 600 s; QP-RULING-1025-am15 R6).
- Runner seg 1 start >= C - 22 h = 11-12T02:00Z, i.e. after F1, so the runner never straddles the first takeover.

### 6.2 Segments (rules; exact instants pinned at registration from job ids)

- MiScusi hard cap 14 d per job. Seg k is armed with `--segment-end-ms HO_k`, at least 6 h inside its own limit (QP risk 9). Seg k+1 is a sleeping standby with a byte-identical command (R9), submitted 12 h before HO_k as a host-side job (never a session cron); its 14 d count from its submit, so its usable span is about 13 d 6 h from HO_k.
- Every runner hand-off HO_k is at least 6 h from every feed takeover F_j, so each seam has its own proof.
- Feed v2 -> v2 takeovers F2, F3, ... every <= 13.5 d after the previous feed job's submit.
- D_MAX = min(5 s, B-P3 p99), pinned. D <= D_MAX with no stale row: `live_lag`, nothing excluded. Otherwise the probe path: positions whose exit triggered in D are `handoff_down` (excluded, in the cap; FLOOR has no stop, so this exclusion is pessimistic for it), entries in D counted as `handoff_down_missed`.

Example at C = 11-13T00Z (illustrative; the amendment pins the real instants):

| | H = 25 (rate 6.0) | H = 33 (rate 4.6) |
|---|---|---|
| Seg 1 | 11-12T02Z -> HO1 <= 11-25T20Z | same |
| Seg 2 | HO1 -> HO2 <= HO1 + 13 d 6 h (about 12-09T02Z) | same |
| Seg 3 | not needed if HO2 >= END_BOOK 12-09T01Z, but the margin is about 1 h, so plan a third segment | HO2 -> END_BOOK 12-17T01Z |
| Runner hand-offs in the window | 1 or 2 | 2 |
| Feed takeovers in the window | F2 about 11-24/25, F3 about 12-07/08 | F2, F3 (F4 about 12-21 is after END_BOOK) |
| Seams the read must survive | 3-4 | 4 |

Each seam is a NOT_DECIDABLE risk if it fails closed (kill rule 4). That is the honest cost of a 25-33 day window at about 5-6 trades a day.

### 6.3 md5 decision-equivalence proof plan (CLAUDE.md: every runner change carries one before deploy)

At S, before E3, all counts/md5 only, by an agent off FLOOR's execution path:

- **B-T1..B-T4** (#957, #962 unit tests): green at S, including the fresh-process restore digest and the tampered-checkpoint refusal.
- **B-P1 real replay** (`tools/longbook_handoff_proof.py`, PR 6), slice arena tape [2026-10-20T00Z, 2026-10-25T00Z) only (QP 1026 Q4), the FLOOR rule through HostV3:
  - R0 continuous; R1, R2 with D = 0 at HO_TEST = 10-22T10:00Z and 10-23T10:00Z (stepping +1 h, at most 12 times, until at least one HO_TEST has >= 1 open position; count only); R3 with D = 30 s at the instant with an open position; t0 10-21T04:00Z; run ends 10-24T23:00Z. Exact pins by PR 6's QP ruling after a ledger D2/D3 check.
  - Pass: R1/R2 decision-row md5, ledger md5 (minus engine_id, pid, written_ms) and state digests equal to R0; R3 equal except the listed `handoff_down` pid hashes and the missed-entry count.
  - The replay's ledger is in-sample FLOOR P&L on pre-10-25 hours. It lives under `/data2/mal/p35/bp1/<tag>/` (mode 0700), the job prints md5s, counts and pid hashes only, and deletes the ledger and decision rows inside the same job. No person or agent opens them. **Seal question for the manager / QP:** this lane's brief says "never price or score FLOOR"; B-P1 does price FLOOR in a scratch ledger that nobody opens. QP 1026 Q4 ruled the slice; confirm that ruling covers it before PR 6's job runs.
- **B-P2:** section 1.4 lamport parity at S, Am.6 1.3 smoke at S, P0 at S (= the binding P0 of 2.2).
- **B-P3:** real-layout timing at S (resume_ms - HO from the standby start; live dispatch-lag p99), giving D_MAX.
- **Feed side:** A-T1..A-T3 (#952) green; A-P1 rehearsal on mal-fast-0 (#954) with 2 seams, seam PASS, parity vs its own control feed, T_p and MIN_OVERLAP_ROWS pinned from control windows (never from a production seam); A-P3 hour-health on the takeover hour equal to a control hour.
- Re-run all of the above if S changes. Any code change after registration is a new bot_id and a new clock.

## 7. Disclosures (f1, f3, f5)

- Am.9 history: A1 PASS; A2 FAIL, `rate_att_lo = 6.0 < 12.5` (`a2-summary.txt` sha256 `bcbdb34f...`, as quoted by QP-RULING-1025-seal-lift; not opened here); not registered, N not spent; seal contacts #1-#3 (with seal2 Add.1/2, prep (2) and the Erratum 1 scan) as history, all disclosure-only.
- "Arena feed health was open to the manager from 2026-10-25T12:18:48Z (L0 notebook n_cuTE-bRvYIfCWA) to this merge" (f1 wording; the amendment states it with its own merge instant).
- #905 (draft Am.15 for `l1-floor-v2`) was ruled NO; this book is a new bot_id on new host code, as QP 1025 Q2 (b) requires.
- This lane's count (#1678) read arena tape hours 10-19T08..10-24T23: walk-2-hour information (DEC-030 §2 R1), before the HV4 and Q50 clocks, counts only. Its output file also holds per-day gap counts; they were not opened or printed. The printed per-day signal and attempt counts are in 2.1.
- f3: the amendment sets its own seal from merge to G1 over its evidence and slip windows, adds those windows to `tools/change_radar_seals.json` in the same PR, pauses ops monitoring for them, and names the off-path agent (session or agent id) before merge (R7, R8). Feed-health checks in that period are by that agent.
- f5: N + 1 at the clock (E3 registry line), not before. A NOT_DECIDABLE end also costs N + 1.
- Winner's curse: FLOOR was the 1 revive candidate of 51 cells; its neighbour-block CI90 lower bound is already below 0 and its ex-top-3 pressure is -0.065 SOL. AUDIT.md's own estimate is 15-25% PASS. Capacity at 0.25 SOL is small (AUDIT.md: about 0.008 SOL/day if the fresh-block edge held).
- Capital: about 1.5 SOL average open at 0.25 SOL stake in paper; any later live use is outside this draft and needs DEC-032 and the owner.

## 8. What this draft does not do

It registers nothing and runs nothing beyond the one informational count. It does not open any FLOOR, HV4, HV6, Q50 or DBC output, any ledger, `bots.json`, decision row, score, fill, exit or P&L, any job status other than #1678, or `/data/mal/meta-capture/`. It does not price FLOOR. It does not change any rule, threshold or the gate.

## 9. What must merge (or happen) first

In dependency order. None of these is merged at origin/main 98e62743.

1. **#945** P35 design + `tools/feed_handover_proof.py` (QP OK at 50c3c08d per manager17 state; reviewer pending).
2. **#957** `EngineCkpt` + `StateCkpt` (B-T1).
3. **#962** `HostV3` (stacked on #957; B-T2..B-T4). Includes the QP answers it asks for: the per-row stale switch as a form of Q2 (b), and tampered-checkpoint refusal.
4. **PR 6** (not opened): `tools/longbook_handoff_proof.py`, its QP ruling with the exact B-P1 pins (after a ledger D2/D3 check and the seal question in 6.3), then jobs B-P1 PASS and B-P3 (D_MAX).
5. **#952** `arena_feed_v2.py` (A-T1..A-T3).
6. **#954** rehearsal driver + runbook, then the A-P1 run on mal-fast-0 and QP's pins of T_p / MIN_OVERLAP_ROWS.
7. **PR 3b** (not opened): `scripts/research/feed-takeover-v2.sh` for F1, with QP OK for every in-clock tape book the manager names at F1.
8. **Fallback plain-restart script** for H' #1652's end (manager17 risk item), in case v2 is not ready by about 11-11T23:30Z. If F1 is a plain restart, FLOOR-SLOW's earliest clock moves to after the next forced hand-off (about 11-25).
9. **P0 at S**: a revision of `tools/floor_p0_count.py` (or a QP-ruled `--expect-sha`) that pins S instead of 5e12f8f5; QP OK + reviewer.
10. **Read-instant tool** for FLOOR-SLOW: parametrised `tools/floor_read_instant.py` (bot_id, clock, END_H; `handoff_down_missed` never an attempt; R11 day key) + the scoring wrapper; QP OK + reviewer. Plus the **futility job and tool** of kill rule 2 (boolean output only).
11. **A3 / structure-halt coverage** for a non-H5 PumpSwap book (`a3-books.json` entry and a QP ruling on which Am.4 item 1 checks apply).
12. **Am.8 coverage record**: executor configuration at 155,000 buy and sell cited `path:line@sha` with a test; the Am.8 item 7 flip read wired for this book.
13. **D1 sampler / D3 table successor jobs** covering [C, END_BOOK) (R5), and the G1 exempt-hand-off pin by job ids (R6).
14. **The FLOOR-SLOW amendment itself** (P35 PR 7): this draft's sections 1-7 as DEC text, R1-R13 of QP-RULING-1025-am15 carried over, the radar seal windows (f3), the named off-path agent, the pinned schedule (F1, HO_k, F_j, job ids), S and the bot_id. QP OK on the exact head + reviewer verdict. **Merge by 2026-11-08T23:59:59Z** for an 11-13T00Z clock.
15. Not merges, but preconditions: HV4 END_BOOK (11-08T13:05Z) and HV6's last in-clock instant (about 11-11T13:00Z) passed before F1; binding P0 on 11-09..11-11 >= 4.0 with no 0 day; G1 and DR1 pass; E3 before C - 15 min; seg 1 running by C - 5 min.

No book has cleared the promotion gate. FLOOR has 0 out-of-sample trades.
