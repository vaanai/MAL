# DESIGN: the tip-source cohort (lane L1): HV4-T, s1 on the tip (s1t), FLOOR-T

Written 2026-11-01T22:53Z (`date -u` on mal-research-0) by lane L1 of the 2026-11-01 push (`/data/mal/hunt-1008/ops/manager18/PUSH-PLAN-1101.md`). Records and design only. Nothing was started, registered, priced or spent. **No book has cleared the promotion gate. Nothing here says any book makes money.** The DEC text is the DRAFT Amendment 25 (HV4-T and the FLOOR-T non-registration) appended to `DEC/DEC-031-forward-paper-arena.md` in the same PR. It was Amendment 24 before s1t moved to #1186. On any conflict the DEC text controls. Revised 2026-11-01T23:2xZ on reviewer comment 6565358726.

## 0. Answer in eight lines
1. **HV4-T:** all nine lane-G HV4 arms at HV4's frozen sha `9ed4c5ff`, with no code change, as new `hv4t-<arm>` books reading the research-0 receiver copy of the tip feed. API cap 3.80 USD a day (inside the withdrawn successor budget of 4.90). **Earliest honest clock 2026-11-10T00:00Z**, with one QP carve-out (reuse HV4's own P0, the Amendment 21 item 1.1 precedent). 11-08 or 11-09 needs a second QP ruling. With no carve-out, 11-12.
2. **The binding constraints are HV4's, not the tip history.** Am.23 item 7's history rule (L = 7 d, H0 + 7 d = 2026-11-05T09Z) is met before any clock considered here. What binds is HV4's run (#1562's start guard at 2026-11-07T12:05Z), HV4's seal on counted hours (no smoke pricing the HV4 rule before 2026-11-08T00Z, under the end reading of Am.25 item 6), and HV4's attempt-count seal (every tip day before 11-08 is an HV4-counted day).
3. **s1t moved to its own PR, #1186** (reviewer N5: it must merge before Q50's read verdict exists). Clock 2026-11-07T00:00Z, H 24 d, a counts-only smoke and an s1t EXP-024 note owed; design in `docs/design/s1t-tip-source.md` on #1186. Section 3 below is now a pointer.
4. **FLOOR-T: dropped, not registered, N not spent.** About 4.6 to 6.0 attempts a day gives no read within 14 days, and 24 h holds against the tip's backlog jumps (about 0.37 a day, L5) put about 31% of trades in the 5% class.
5. **Shared-slot trigger, ruled.** The writer reports `pool_slots_multi_sig` 1,283 over 85 closed hours. QP-RULING-1102-tip-shared-slot (sha256 92378049…) makes it report-only for every covered book. It adds tripwires: any mixed group, the follower `pool_cache` reaching 180,000, and a follower start (the 60-min with/without gate test). No clock moves.
6. **Exposure risk, not a blocker:** at about 0.37 tip gaps a day, the BIG arms (default 6 h holds, up to 12 h) expect 8.84% to 16.89% of trades in the 5% class, so they are likely NOT_DECIDABLE_5PCT. The CON arms expect about 1.15%. The tip gap-fill (#1182, F2) applies only if it merges before the clock with its pins in the registration record (Am.25 item 12).
7. **Not push-family:** all three are DEC-031 arena books. They count in DEC-031's N, take no DEC-030 slot, and leave EXP-027's k at 1 (DEC-031 §3 item 3).
8. **Owed code:** the field listing (builder PR in flight), the HV4-T host job scripts, the HV4-T equivalence harness, read tools before HV4-T's first B, the S\*' tip mode with its md5 proof, and a SEALS reader line for each new reader (section 5).

## 1. Facts this design rests on (all from files or code, dated)
- **Tip transport, as of 2026-11-01T22:39Z.** Source: research-0 receiver `/data2/mal/arena-tip-feed/hv7/.mirror/status.json` and `writer_status.json`, the only files of that dir that were read.
  - Writer state: caught up and live.
  - History: `history_from` 2026-10-29T09, 85 closed hours, all verified.
  - Gap rows: 1. It is a `backlog_jump` of 151 slots at 2026-11-01T01:02Z, before the writer started.
  - No-tx_index counts: `rows_no_tx_index` 17,571; `pool_slots_no_tx_index` 16,008; `pool_slots_multi_sig` 1,283; `pool_slots_mixed` 0.
  - Lags: writer `lag_ms` 485, `max_input_age_s` 0.5, receiver new-frame p90 23 ms.
  - Writer pins: adapter `2c1eeba1…`, reader `20cb05c7…`, writer `8152da39…`.
- **The HV4 code is unchanged on main.**
  - `git diff --stat 9ed4c5ff origin/main -- tools/arena_state.py tools/arena_paper.py tools/hv4_cards.py tools/hv4_runner.py` is empty.
  - So the adapter's md5 equivalence (#2023, ArenaEngine at ca9c2822) ran on HV4's own engine bytes.
- **How HV4's runner reads tape (9ed4c5ff).**
  - It reads `--tape-dir` with no path guard (`tools/hv4_runner.py:1841`, `:1865`).
  - `ArenaState(a.tape_dir)` starts at now − `WARM_MIN_DEFAULT` = 365 min (`tools/arena_state.py:66`, `:147`) and evicts keys idle more than 6 h (`:64`).
  - `hv4_cards._age` falls back to `age_since_first_seen_s` when `created_known` is false (`:413-418`).
  - The job script takes `HV4_TAPE` (`scripts/research/hv4-runner-job.sh:28` at 9ed4c5ff).
  - The P0 tool `tools/hv4_p0_count.py` (sha256 `3e2539d0…`) takes `--tape-dir` with a 365 min warm-up.
- **HV4's run.**
  - #1561 ends 2026-11-07T12:05Z. #1562 then starts, and its script refuses while any `hv4_runne[r].py` process is alive (`t0-1025/E4-runner-seg2.sh`, the `pgrep -f` line).
  - #1562 runs to 2026-11-08T13:05Z with a 45,000 MB job memory.
  - HV4's counted window and its confirmation end at 1794096000000 (2026-11-08T00:00Z; Am.15 items 1 and 3).
- **HV4 API caps** (`tools/hv4_arms.py:18-25` at 9ed4c5ff): 0.50 USD per LLM-arm ledger, 1.00 for the shared RISK ledger, 0.10 per reviewer ledger, and a month guard of 160.
  - Lane G has five LLM arms (CON-L, CON-F, CON-P, BIG-L, BIG-F) and three reviewer ledgers (REV-CON-L, REV-BIG-L, REV-CON-P).
  - So the lane-G cap is 3.80 USD a day. The 4.90 figure counts the four BST arms of lane H, which HV4 never runs.
- **H5 pricing on a slow feed.**
  - `tools/h5_shadow.py` sets landing = trigger print slot + ceil(latency / sps) (`:2008` at S\*).
  - The arena H5 scorer corrects the landing from the decision instant: age = `t_detect_ms` − block_time (`tools/arena_h5_score.py:15-18`, `:211`, `:473`).
  - It excludes a trigger older than `MAX_TRIGGER_AGE_MS` = 3,500 ms (`:97`, `:458`), and that exclusion counts in the 5% cap.
- **FLOOR v2 was never registered.** The A2 rate was 6.0 a day, below Am.9's 12.5 (manager15 log, 2026-10-25), and N was not spent. QP-RULING-1101-hv4-reads §3.4 treats FLOOR as running. That is a slip in the ruling; no FLOOR job is in the job list.

## 2. HV4-T

### 2.1 The arms and their API cost (caps per UTC day, `tools/hv4_arms.py` at 9ed4c5ff)
| bot_id | role | LLM | own ledgers at cap | promotable |
|---|---|---|---|---|
| `hv4t-CON-L` | learning + nightly reviewer | yes | 0.50 + REV 0.10 = 0.60 | yes |
| `hv4t-CON-F` | frozen playbook v0 | yes | 0.50 | yes |
| `hv4t-CON-P` | placebo reviewer (control) | yes | 0.50 + REV 0.10 = 0.60 | no |
| `hv4t-CON-X` | rule book, no LLM | no | 0 | yes |
| `hv4t-CON-R` | random, matched to CON-L (control) | no | 0 | no |
| `hv4t-BIG-L` | learning + nightly reviewer | yes | 0.60 | yes |
| `hv4t-BIG-F` | frozen playbook v0 | yes | 0.50 | yes |
| `hv4t-BIG-X` | rule book, no LLM | no | 0 | yes |
| `hv4t-BIG-R` | random, matched to BIG-L (control) | no | 0 | no |
| shared `RISK` ledger | risk agent for the L, F and P arms; the R arms open risk-cleared pools; the X arms ignore it | yes | 1.00 | n/a |

- **Total at cap: 3.80 USD a day.** Over 14 days plus one run-out day that is at most 57.00 USD, and the 24 h smoke adds at most 3.80.
- This is inside the 4.90 a day withdrawn from the successors. The owner's recorded words withdraw that spend and name no new use. The redirect to these books is the manager's plan (PUSH-PLAN-1101 L1), and the spend authority is the owner's 2026-10-28 API line (notebook `n_8Yfl--RzLCx2Dw`) plus Am.25.
- The month guard is 160 USD per root. The owner's 200 USD console limit is the backstop.
- Chain calls go through `tools/hv4_chain.py` (caps 2,000 an hour, 48,000 a day; Helius standard calls). Credits are reported per job (CLAUDE.md "Credits").
- **Why all nine.** The frozen code runs and registers all nine lane-G arms in one process (`--register` writes "the 9 lane-G bots.json lines"). Running a subset is a code change, which means a new sha and new proofs. The controls (P, R) make no promotion but cost N, as for HV4 and its successors.

### 2.2 Clock: what binds, and the options
H0 (2026-10-29T09:00Z) does **not** bind. Am.23 item 7's L is 7 d (the rule's age bound; reviewer R5), and H0 + 7 d = 2026-11-05T09Z is before every clock below. The frozen runner reads only 365 min of tape before its start. So the age band of 2 h to 7 d is judged on the runner's own view (first seen since start − 365 min, reset by a 6 h idle eviction), as HV4 ran on the public tape.
- **Two disclosed differences from HV4's run** (#1180):
  - (a) pools created outside a migration have no `pool_create` on the tip, so they fail `age_min` until they have been seen for 2 h (S3);
  - (b) bonding rows carry no `creator` (S2, `sell_route`).
  Both need a QP ruling.
- L5 asked whether "the HV4 age band is ≤ 7 d" decides an 11-06 clock. It does not. These four constraints do:

| # | Constraint | Source | Effect |
|---|---|---|---|
| K1 | #1562's start guard refuses while any `hv4_runner.py` process is alive, at about 2026-11-07T12:05Z | E4-runner-seg2.sh (pinned; a running job's command never changes, §4) | No HV4-T process (smoke or runner) may be alive at #1562's start, so the runner starts at 2026-11-07T23:55Z or later |
| K2 | "No pricing or retune of any HV4 rule on counted hours (>= 2026-10-25T00Z)": the line has no end; Am.25 item 6 reads "counted hours" as HV4's counted window [10-25T00Z, 11-08T00Z), for QP to confirm and the manager to write into the SEALS-BRIEF HV4 line before the smoke | SEALS-BRIEF HV4 line; Am.21 item 1.4 (h) applied it to a re-smoke | A 24 h smoke at 9ed4c5ff prices the HV4 rule, so it runs on hours ≥ 2026-11-08T00Z unless QP rules otherwise |
| K3 | A structure count of the HV4 rule on HV4-counted hours is HV4 attempt-count information | Am.21 item 1.1 (P0 paragraph); SEALS-BRIEF HV4 line | A tip P0 needs days ≥ 2026-11-08 until all nine HV4 reads are recorded (the last possible one is 2026-11-10T01:35Z) |
| K4 | Am.3 item 5 (no second frozen copy beside the parent) | DEC-031 Am.3 item 5 | Satisfied by windows that do not overlap: HV4 counts entries only before 1794096000000, and its confirmation ends there too. QP §3.3 already treats a different feed as a different book definition |

| Option | Clock (ms) | Smoke | P0 | QP rulings needed |
|---|---|---|---|---|
| **B (recommended; QP on #1178)** | **2026-11-10T00:00Z (1794268800000)** | ≥ 24 h, starting only after the job list shows #1562 ended and not before 2026-11-08T13:05Z (ends about 11-09T13:30Z); counts-only ledger, never opened | HV4's own A2 P0 reused (ALLOWED by QP), with a per-arm tip floor of ≥ 10.714285714285714 a day measured on the smoke (Am.25 item 5) | done: R-P0 allowed; the K2 end reading accepted; S2/S3 disclosure only |
| A | 2026-11-09T00:00Z | REFUSED by QP (R-SMOKE: a smoke on hours before 11-08T00Z is HV4 attempt-count information) | - | - |
| A' | 2026-11-08T00:00Z | REFUSED by QP (R-SMOKE, R-BESIDE: one process per sha; HV4's tail shares the Anthropic key) | - | - |
| C (no P0 carve-out) | 2026-11-12T00:00Z (1794441600000) | as B | tip P0 with `tools/hv4_p0_count.py` (3e2539d0…) on 11-08, 11-09 and 11-10, after 11-10 closes | R-BESIDE, the K2 end reading and the S2/S3 rulings, as for B |

- **The case for R-P0.** P0 decides two things: the floor (2.0 a day, no day at 0) and H, which is 14 for any rate ≥ 150/14 = 10.714285714285714. HV4's count is 230.667 a day at its lowest arm, about 21 times the H threshold. That count ran on the public tape, which lost data. On the tip, the print and signer filters see at least as many rows. A tip count is still the better evidence, but every tip day before 11-08 lies in HV4's counted hours. The smoke day (option B) is outside them, so its decision-row count can serve as a tip-side sanity floor. That floor is fixed in a notebook entry before the smoke: at least one counted-kind entry decision per persona group.
- **Window.** H = 14 d. Entries at or after clock + 14 d do not count. The runner's END is the window end + 13 h 05 min (HV4's run-out). For option B that is 2026-11-24T00:00Z (1795478400000), and END is 2026-11-24T13:05Z (1795525500000). That is longer than one 14-day MiScusi job, so the runner is after-chained in two segments, with the hand-off instant pinned before the clock (HV4's seg1/seg2 pattern; Am.6 item 4 restart gap).
- **Slip.** A late pre-clock item moves the clock to the next 00:00Z. It is never a stop and spends no N.

### 2.3 Feed delay d (Am.23 item 2), from status files only
- **Formula:** d = ceil((p90 of the follower's `block_lag_ms_p90` samples + p90 writer ingest lag + p90 transport lag) / ms per slot), from at least 24 h of one window.
  - The samples come from sampler #2022, and from its successor #2048 to 2026-11-07T01Z (`tip-health-110x.jsonl` on fast-0).
  - The transport lag comes from the receiver's log.
  - One d serves every covered book on this transport, pinned per book with its date. HV4's 7 is not reused.
- **Scale only, not the pin:**
  - block lag p90: 1,697 to 1,995 ms (r4-b sidecar, manager log 2026-11-01);
  - writer `lag_ms`: 485 (to confirm that this status field is the ingest lag the formula means);
  - transport p90: 23 ms.
  - At 0.4 s a slot, ceil((1,995 + 485 + 23) / 400) = 7.

### 2.4 Exposure (an expectation, not a measurement)
- **Inputs.** L5 counts tip backlog jumps on 10-27, 10-28 and 11-01, about 0.37 a day, all prog `all`. A trade's 5% class share is about 1 − e^(−λT).
- **By hold length:**
  - CON default 45 min: 0.0115.
  - CON maximum 120 min: about 0.030.
  - BIG default 360 min: 0.0884.
  - BIG maximum 720 min: 0.169.
- **The pre-clock bar (Am.23 item 6).** T_max = 12 h, which is above 0.6076 h, so the bar needs ZERO pumpswap or `all` data_loss rows in the 24 h before the clock. At λ = 0.37 a day, that holds on about e^(−0.37) = 69% of days.
- **What follows.** The BIG arms are likely to read NOT_DECIDABLE_5PCT unless the gap rate falls or QP option C (fill a jump's missed slots with the pinned decoder before release, about 150 credits a jump, L5) is built. This is disclosed, not hidden. It is the main argument for asking QP about option C now.

### 2.5 Pre-clock list (Am.3 item 1, Am.4 item 2, Am.6 item 1, Am.23 item 8)
1. This amendment and the stop record (PR #1175) merged; Am.23 Erratum 1 (#1169) merged.
2. **A SEALS reader line.** The SEALS-BRIEF tip entry names only "the pinned tip-source HV7 runner and its smoke ... the pinned tip-source P0 counter". It must also name the HV4-T runner and smoke at 9ed4c5ff (and the HV4 P0 tool under option C) as readers of `/data2/mal/arena-tip-feed/hv7`. That needs a QP note and the manager's edit. The same dir serves every covered book; no second copy is made.
3. **Registration (Am.21 item 1.2 pattern).**
   - Own `--root`, `--ledger-root` and `--registry` on /data2, for example `/data2/mal/arena/hv4t/`. Never HV4's paths.
   - Nine `hv4t-<arm>` lines in `/data/mal/arena/bots.json`, written by `register_bot`.
   - The map `hv4t-<arm>` = (registry, root, ledger root, internal `hv4-<arm>`), pinned in BOTH the notebook copy and a pushed commit.
   - Genesis manifests for CON-L, BIG-L and CON-P.
4. **The field listing** (Am.23 item 8, "State fields"): builder PR `claude/hv4t-tip-fields`, in flight at writing. Any field that is always None on the tip and changes a decision is a NEED, and makes HV4-T ineligible until it is resolved.
5. **Equivalence** (Am.23 item 8). Run HV4's runner in sim at 9ed4c5ff (StubModel on the LLM arms; the X and R arms are deterministic) over the WRITER's output built from explore-0814 walker hours (a fake tip dir and state dir, the arm-T pattern). Compare its decision-log md5 with the same runner fed the adapter's in-memory stream. Pin a minimum-decisions floor in a notebook entry before the job. Outputs: md5s, field names and counts only.
6. **Smoke.** At least 24 h, real model, at 9ed4c5ff, in a scratch root, on the receiver copy, with `--feed-delay-slots d`.
   - Blocking list: Am.6 1.3 (a) to (f), notebooked first.
   - Reports: the Am.23 item 8 list (dropped rows by code, anon RSS peak, rows per minute, catch-up time, follower `lag_slots` and `backlog_jumps` with every tip reader running).
   - It starts only after the job list (state only) shows #1562 ended, and not before 2026-11-08T13:05Z (R-BESIDE refused by QP on #1178).
   - Memory: check `MemAvailable` and the sum of running reservations at the smoke's start and again at the runner's start.
7. **Hour health.** The Am.23 item 5 tool, thresholds file and 60 s sampler (lane H, shared with HV7), for program `pumpswap`. The daily table is notebooked.
8. **The §4.4 / Am.23 item 6 records line:** zero rows in the 24 h before the clock.
9. **Kill-review-week check** (LAB_STATE "Now": none). An N line: N + 9.

### 2.6 Read
- **Rule.** Amendment 15 items 1, 2, 3, 6 and 7, re-based to HV4-T's clock and window end, as Am.21 item 1.3 does for the successors. The earliest B for option B is 2026-11-15T00:00Z.
- **Tools.** Copies of `tools/hv4_read_instant.py` and `tools/hv4_am15_read.py`, whose only differences are:
  - the `hv4t-` id map, clock, window end and roots;
  - the arena-wide multiplicity source;
  - the Am.6 hour table, taken from the tip hour-health tool, not from `arena_hour_health`.
  They merge with QP and a reviewer before 2026-11-15T00:00Z. They are not needed before the clock.

## 3. s1 on the tip (s1t): moved to PR #1186
Split out on reviewer note N5, because it must merge before any Q50 read verdict exists. See `docs/design/s1t-tip-source.md` and DEC-031 Amendment 24 (DRAFT) on #1186: clock 2026-11-07T00:00Z (slip 11-08), H = 24 d to `SUCC_END_MS` (Am.22 item 8.1), counts-only smoke, an s1t EXP-024 note owed before the smoke, Q50 P0 reuse, and the 2026-11-05T12:00Z fallback.

## 4. Blockers for every covered clock (HV7 included)
1. **`pool_slots_multi_sig` (about 15 an hour): ruled report-only** by QP-RULING-1102-tip-shared-slot. No read of HV7 or s1t can touch a no-tx row. HV4-T is also clear while the follower cache is below its cap and the follower is not restarted. The tripwires of R2 replace the trigger.
2. **Am.23 item 6 zero-row bar** for any T_max above 0.6076 h (HV4-T, HV7). At about 0.37 jumps a day, each day passes with probability about 0.69. Option C would help.
3. **The SEALS reader list** names HV7 only. Each new reader needs a QP note and the manager's edit before its first read.

## 5. Code gaps (what lets each book read the research-0 receiver copy)
| # | Gap | Size | Status |
|---|---|---|---|
| G1 | HV4-T state-field listing and tests (Am.23 item 8) | builder | PR #1180 (reviewer APPROVE at b98f07f0): S2 creator/`sell_route` and S3 `age_min` decision changes, owed QP rulings (Am.25 item 7) |
| G2 | HV4-T host job scripts: register, genesis, runner (2 segments), smoke, 3 reviewers, digest, decision-log sha notebook. Wrappers of `scripts/research/hv4-runner-job.sh` at 9ed4c5ff with `HV4_TAPE=/data2/mal/arena-tip-feed/hv7`, the /data2 roots, `HV4_FEED_DELAY_SLOTS=d`, clock and END. Each refuses HV4's paths and takes a lock on its own root. Each also refuses to start before 2026-11-07T12:30:00Z, and until the job list shows #1562 running or finished (K1: never alive at #1562's start) | builder, after the clock option and d are chosen | listed |
| G3 | HV4-T equivalence harness (2.5 item 5) | builder | listed |
| G4 | HV4-T read tools (2.6), before the first B | builder | listed |
| G5 | Tip hour-health tool, thresholds and 60 s sampler (Am.23 item 5) | lane H (L2) | shared; for HV4-T it needs `pumpswap`; for s1t, `pumpswap` and `pumpfun` |
| G6 | S\*' tip input mode for `tools/h5_shadow.py` (s1t, #1186) | builder (the 11-07 critical path) | listed on #1186 |
| G7 | s1t scripts: register, shadow job, A3 books entry and loop, score/instant wrapper (`arena_h5_score_q50s` with the s1t id) | builder | listed |
| G8 | SEALS reader lines (2.5 item 2; s1t) | QP + manager | listed |

**G6 and G7 (s1t)** are specified in `docs/design/s1t-tip-source.md` section 6 on PR #1186, with the counts-only smoke switch.

## 6. FLOOR-T: dropped (not registered; N not spent)
- **Rate.** The rate in view is 6.0 a day (A2, 2026-10-25) and 4.6 a day (P50 #1678; public tape, which lost data). At those rates, 100 attempts need about 17 to 22 days. A 12-day H (Am.9) cannot reach 100. Am.4 item 2 would give H = 25 to 33 days, which needs the P35 in-clock hand-off (#962, draft).
- **Exposure.** T_max is 24 h. At about 0.37 tip gaps a day, 1 − e^(−0.37) ≈ 0.31 of trades fall in the 5% class, and the zero-row bar passes on about 69% of days.
- **What a revival needs:** a tip gap-fill (option C) that brings λ well under 1 per 19.5 days; the P35 hand-off merged; a tip P0 of at least 12.5 a day for H = 12, or the hand-off for a longer H; and its own amendment. L5 reaches the same conclusion.

## 7. Push family
- These are DEC-031 arena books. DEC-031 §3 item 3 says: "Arena books are not push-family books. They take no DEC-030 slot and do not change EXP-027's k. Arena results are walk-2-hour information under DEC-030 §2 R1 ("a book's design must not use them"). No book designed, tuned or selected with any arena result registers as a push-family book on walk 2."
- Am.23 item 1 counts covered books in DEC-031's N from their clocks.
- QP-RULING-1101-arena-tip-reader W13: "DEC-030 §2 R1 forbids any push-family use of these results."
- So HV4-T and s1t add to DEC-031's N (N + 9 and N + 1). They add nothing to DEC-030's slot or to the walk-2 family's k, which stays 1, and EXP-027's power is unchanged. Nothing here registers into DEC-030 or DEC-014's push family.

## 8. Not measured
- The tip's data-loss rate over a fixed window (#2022 and #2048 decide it).
- The trigger-age distribution on the tip.
- The per-hour `pool_slots_multi_sig`.
- d.
- HV4-T and s1t memory on tip rows.
- Whether the frozen HV4 runner's warm-up of 365 min fits the receiver's open-hour tail behaviour, and the S\*' code.
- Any P0, smoke, attempt or outcome.

No tape, tip file, derived-copy `tape-*` file, counts log, arrival log, ledger, `bots.json`, decision row or sealed job view was opened.
