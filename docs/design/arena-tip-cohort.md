# DESIGN: the tip-source cohort (lane L1): HV4-T, s1 on the tip (s1t), FLOOR-T

Written 2026-11-01T22:53Z (`date -u` on mal-research-0) by lane L1 of the 2026-11-01 push (`/data/mal/hunt-1008/ops/manager18/PUSH-PLAN-1101.md`). Records and design only. Nothing was started, registered, priced or spent. **No book has cleared the promotion gate. Nothing here says any book makes money.** The DEC text is the DRAFT "Amendment 24" appended to `DEC/DEC-031-forward-paper-arena.md` in the same PR; on any conflict the DEC text controls.

## 0. Answer in eight lines
1. **HV4-T:** all nine lane-G HV4 arms at HV4's frozen sha `9ed4c5ff`, with no code change, as new `hv4t-<arm>` books reading the research-0 receiver copy of the tip feed. API cap 3.80 USD a day (inside the withdrawn successor budget of 4.90). **Earliest honest clock 2026-11-10T00:00Z**, with one QP carve-out (reuse HV4's own P0, the Amendment 21 item 1.1 precedent). 11-08 or 11-09 needs a second QP ruling. With no carve-out, 11-12.
2. **The binding constraints are HV4's, not the tip history.** H0 does not bind: the frozen runner reads only 365 min of tape before its start. What binds is HV4's run (#1562's start guard at 2026-11-07T12:05Z), HV4's seal on counted hours (no smoke pricing the HV4 rule before 2026-11-08T00Z), and HV4's attempt-count seal (every tip day before 11-08 is an HV4-counted day).
3. **s1t (the manager's decision of 2026-11-01):** s1 is not registered on the public websocket. The same rule at S\* `5c2ab7c6` runs on the tip as `h5v1q50-boostfloor-s1t`, with s1's power-n reads. **Clock 2026-11-07T00:00Z** if the tip input mode of `tools/h5_shadow.py` (S\*') is frozen by 2026-11-05T12:00Z, else the OrbitFlare fallback (s1 as registered).
4. **FLOOR-T: dropped, not registered, N not spent.** About 4.6 to 6.0 attempts a day gives no read within 14 days, and 24 h holds against the tip's backlog jumps (about 0.37 a day, L5) put about 31% of trades in the 5% class.
5. **New blocker for every covered clock, HV7 included:** the writer reports `pool_slots_multi_sig` 1,283 over 85 closed hours (about 15 an hour). QP's Am.23 trigger is "above 1 an hour". QP must re-rule before any covered clock.
6. **Exposure risk, not a blocker:** at about 0.37 tip gaps a day, the BIG arms (default 6 h holds, up to 12 h) expect about 8.8% of trades in the 5% class, so a NOT_DECIDABLE_5PCT is likely. The CON arms (45 min default, up to 2 h) expect about 1.2%. QP option C (a tip gap-fill) would cut this for every covered book.
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
- This is inside the withdrawn successor budget of 4.90 a day, which the owner redirected to these books (notebook decision "Push 11-01").
- The month guard is 160 USD per root. The owner's 200 USD console limit is the backstop.
- Chain calls go through `tools/hv4_chain.py` (caps 2,000 an hour, 48,000 a day; Helius standard calls). Credits are reported per job (CLAUDE.md "Credits").
- **Why all nine.** The frozen code runs and registers all nine lane-G arms in one process (`--register` writes "the 9 lane-G bots.json lines"). Running a subset is a code change, which means a new sha and new proofs. The controls (P, R) make no promotion but cost N, as for HV4 and its successors.

### 2.2 Clock: what binds, and the options
H0 (2026-10-29T09:00Z) does **not** bind. The frozen runner reads 365 min of tape before its start and nothing older. The age band of 2 h to 7 d is judged on the runner's own view (first seen since start − 365 min, reset by a 6 h idle eviction), exactly as HV4 ran on the public tape. L5 asked whether "the HV4 age band is ≤ 7 d" decides an 11-06 clock. It does not. These four constraints do:

| # | Constraint | Source | Effect |
|---|---|---|---|
| K1 | #1562's start guard refuses while any `hv4_runner.py` process is alive, at about 2026-11-07T12:05Z | E4-runner-seg2.sh (pinned; a running job's command never changes, §4) | No HV4-T process (smoke or runner) may be alive at #1562's start, so the runner starts at 2026-11-07T23:55Z or later |
| K2 | "No pricing or retune of any HV4 rule on counted hours (>= 2026-10-25T00Z)" | SEALS-BRIEF HV4 line; Am.21 item 1.4 (h) applied it to a re-smoke | A 24 h smoke at 9ed4c5ff prices the HV4 rule, so it runs on hours ≥ 2026-11-08T00Z unless QP rules otherwise |
| K3 | A structure count of the HV4 rule on HV4-counted hours is HV4 attempt-count information | Am.21 item 1.1 (P0 paragraph); SEALS-BRIEF HV4 line | A tip P0 needs days ≥ 2026-11-08 until all nine HV4 reads are recorded (the last possible one is 2026-11-10T01:35Z) |
| K4 | Am.3 item 5 (no second frozen copy beside the parent) | DEC-031 Am.3 item 5 | Satisfied by windows that do not overlap: HV4 counts entries only before 1794096000000, and its confirmation ends there too. QP §3.3 already treats a different feed as a different book definition |

| Option | Clock (ms) | Smoke | P0 | QP rulings needed |
|---|---|---|---|---|
| **B (recommended)** | **2026-11-10T00:00Z (1794268800000)** | [2026-11-08T00:00Z, 2026-11-09T00:00Z), starting after #1562 has started; beside #1562 until 13:05Z | HV4's own A2 P0 reused (10-21..10-23 at 9ed4c5ff, lowest arm 230.667 a day, no day at 0), as Am.21 item 1.1 did | R-P0: carve-out from Am.23 item 8's tip P0 for HV4-T |
| A | 2026-11-09T00:00Z | [11-07T12:30Z, 11-08T12:30Z]: 11.5 h of it on HV4-counted hours, in a scratch ledger never opened | reused | R-P0, plus R-SMOKE: a never-opened scratch-ledger smoke on counted hours is not "pricing" |
| A' | 2026-11-08T00:00Z | [11-06T11:00Z, 11-07T11:00Z], ending before #1562's start | reused | R-P0, R-SMOKE, and R-BESIDE: the runner runs beside #1562 (2 × 45 GB) from 11-07T23:55Z |
| C (no carve-out) | 2026-11-12T00:00Z (1794441600000) | as B | tip P0 with `tools/hv4_p0_count.py` (3e2539d0…) on 11-08, 11-09 and 11-10, after 11-10 closes | none beyond the Am.23 items |

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
   - It starts only after #1562 has started (K1).
   - Memory: 45,000 MB beside #1562's 45,000 MB until 13:05Z. Check `MemAvailable` and the sum of running reservations first.
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

## 3. s1 on the tip (s1t), the manager's decision of 2026-11-01

### 3.1 Why it is an alternative, not an addition
- Am.3 item 5 forbids a second frozen copy beside the parent. s1 and a tip book at the same S\* would be two copies of one rule in overlapping windows.
- The manager chose the tip, so s1 is never registered on the public websocket.
- Am.17 item 2's stop list has no manager-withdrawal reason. So this is written as the commitment being MET by a covered book at the same S\*, not as a stop. QP rules on that reading.
- Every text after Am.17 is barred from conditions that depend on a Q50 outcome (P8). The re-source depends on feed health only: HV4's NOT_DECIDABLE_5PCT (no P&L) and the Q50 executor's gap-held share of about 1.0 on 10-28/29 (HELM-REPLY-1031-1).
- It must merge before Q50's read verdict exists. Read job #1887 is still queued behind waiter #1886. If it merges after, the verdict in view is stated.

### 3.2 What changes and what does not
| Item | s1 (Am.17, Am.18, Am.22) | s1t |
|---|---|---|
| bot_id | `h5v1q50-boostfloor-s1` (never registered) | `h5v1q50-boostfloor-s1t` (new, Am.23 item 1) |
| rule | EXP-024-RULE-Q50, Q\* 50 | same |
| code | S\* `5c2ab7c6` (tag `q50-succ-base-1`) | S\*' = S\* plus a tip input mode only; decode and decision functions byte-identical to S\* (identity test); new tag |
| feed | public `logsSubscribe`, plus a second provider (Am.22 items 10, 11) | the receiver copy of the tip (Am.23); Am.22 items 10 and 11 do not apply |
| host | mal-fast-0 | mal-research-0 (transport B) |
| P0 / H | Q50's P0 reused (#1413, 35.333333333333336 a day), H 14 | same reuse. Every tip day before the clock lies in Q50's counted window [10-25, 11-06) and Q50's read is unread, so a tip P0 would be Q50 attempt-count information. This is QP's R-P0 for s1t |
| reads | Am.22 power-n: N_s 200, S_cap clock + 10 d with at least 100, N_c 300, E = E_c 1796083200000 (2026-12-01T00Z) | same numbers, s1t's clock |
| clock | 2026-11-06T00Z (slip 11-07) | **2026-11-07T00:00Z (1794009600000)**; slip 2026-11-08T00:00Z |
| A3 | Am.22 item 9 loop and books file | the same, with the s1t id |

### 3.3 The latency question (a pre-clock bar, timing only)
- The tip shows a print at block time + about 2.0 s (p90 block lag), plus the writer's ingest lag, plus transport. The scorer prices the landing from `t_detect_ms`, so the delay is charged, not given free.
- **What S\*' must guarantee.** In tip mode, `t_detect_ms` is the engine host's wall clock when the trigger is decided, never the row's `t_recv_ms`. The fast-0 stamp would under-charge the writer and transport lag, a bias toward P&L. A test pins this.
- **The 3,500 ms trigger-age exclusion** counts in the 5% cap. Its share on the tip is unknown.
- **Pre-clock bar (proposed to QP, outcome-blind):** in the ≥ 24 h smoke, at most 2.5% (half the cap) of trigger records have age > 3,500 ms. Report the p50, p90 and max age.
  - A fail is a slip, never a stop.
  - If it fails twice, the manager takes the fallback.
- **Am.23 item 6 for s1t.** T_max is about 330 s + 0.55 s + 1.9 s ≈ 333 s ≈ 0.0925 h, which is below 0.6076 h. Up to 6 pumpswap, pumpfun or `all` data_loss rows in the 24 h before the clock pass (n x 333 / 86,400 ≤ 0.0253).
- **Prior (in-sample, disclosed, not a measurement of s1t).** The EXP-012 latency note says edge under V was gone by k ≥ 12 slots. A tip book lands about d slots later than a websocket book.

### 3.4 Dated steps to the 11-07 clock (UTC)
| By | Step | Owner |
|---|---|---|
| 11-02 12Z | QP rulings: R-P0 (s1t), the Am.17 "met, not stopped" reading, the trigger-age bar, the multi-sig trigger re-rule (section 4) | manager → QP |
| 11-03 12Z | S\*' tip mode built: tests, identity test, md5 equivalence (S\*' on writer output from explore-0814 hours against S\* replay on the same walker rows; trigger and outcome records equal except the stamp fields) | builder, lane L1 or L2 |
| 11-04 18Z | S\*' QP OK + reviewer APPROVE at the exact head; tag `q50-succ-tip-1` | QP, reviewer, manager |
| 11-04 22Z | This amendment (Part B) merged, before Q50's read verdict exists | manager |
| 11-05 12Z | **Freeze deadline (fallback trigger).** Smoke starts at S\*' on research-0, scratch root, ≥ 24 h; blocking list and trigger-age bar notebooked first | manager |
| 11-06 12Z | Smoke judged; A3 books file and loop for s1t; SEALS reader line | manager, QP |
| 11-06 20Z | Registration record (notebook + `bots.json` line), kill-review check, N + 1 line | manager |
| 11-06 23:00Z | Shadow job start at S\*', at least 1 h before the clock (Am.18 item 8) | manager |
| 11-07 00Z | Clock, if the Am.23 item 6 records line holds over [11-06T00Z, 11-07T00Z) | — |

- **Fallback.** If S\*' is not frozen by 2026-11-05T12:00Z, Part B lapses and s1 runs as registered under Am.17, 18 and 22, with the owner's OrbitFlare key.
- **11-06T00Z is not honest.** It needs S\*' frozen and a 24 h smoke passed before 11-05T23Z, so the code would have to be frozen by about 11-04T00Z.

## 4. Blockers for every covered clock (HV7 included)
1. **`pool_slots_multi_sig` is about 15 an hour** (1,283 over 85 hours, cumulative writer status at 22:39Z). QP's trigger in Am.23 item 3 is "above 1 an hour outside the first hour after a follower start". The follower has not restarted since 10-05. So QP must re-rule before any covered clock. The per-hour counts log will show whether the count is spread out or bunched; it was not read for this design.
2. **Am.23 item 6 zero-row bar** for any T_max above 0.6076 h (HV4-T, HV7). At about 0.37 jumps a day, each day passes with probability about 0.69. Option C would help.
3. **The SEALS reader list** names HV7 only. Each new reader needs a QP note and the manager's edit before its first read.

## 5. Code gaps (what lets each book read the research-0 receiver copy)
| # | Gap | Size | Status |
|---|---|---|---|
| G1 | HV4-T state-field listing and tests (Am.23 item 8) | builder | PR `claude/hv4t-tip-fields` in flight |
| G2 | HV4-T host job scripts: register, genesis, runner (2 segments), smoke, 3 reviewers, digest, decision-log sha notebook. Wrappers of `scripts/research/hv4-runner-job.sh` at 9ed4c5ff with `HV4_TAPE=/data2/mal/arena-tip-feed/hv7`, the /data2 roots, `HV4_FEED_DELAY_SLOTS=d`, clock and END. Each refuses HV4's paths and takes a lock on its own root. Each also refuses to start before 2026-11-07T12:30:00Z, and until the job list shows #1562 running or finished (K1: never alive at #1562's start) | builder, after the clock option and d are chosen | listed |
| G3 | HV4-T equivalence harness (2.5 item 5) | builder | listed |
| G4 | HV4-T read tools (2.6), before the first B | builder | listed |
| G5 | Tip hour-health tool, thresholds and 60 s sampler (Am.23 item 5) | lane H (L2) | shared; for HV4-T it needs `pumpswap`; for s1t, `pumpswap` and `pumpfun` |
| G6 | S\*' tip input mode for `tools/h5_shadow.py` | builder (the 11-07 critical path) | listed |
| G7 | s1t scripts: register, shadow job, A3 books entry and loop, score/instant wrapper (`arena_h5_score_q50s` with the s1t id) | builder | listed |
| G8 | SEALS reader lines (2.5 item 2; s1t) | QP + manager | listed |

**G6 spec (S\*' tip input mode; the 11-07 critical path).** Builder-sized only if the scope stays exactly this:
- **Input.** In `tools/h5_shadow.py` at S\*, add an input that tails the derived copy's `tape-<hour>.jsonl[.zst]` in the arena layout.
  - Reuse `tools/arena_state.py`'s file reader, or the receiver's verified-offset rule.
  - Refuse every other dir except test tmp dirs, and refuse `/var/lib/mal/sealed`, `tip-tape-archive` and `/data/mal/arena/tape`.
- **Row mapping:**
  - `pool_create` → the CreatePool announce (pool, mint, quote_mint);
  - `complete` → the bonding complete;
  - `trade` → the print, with event V from `virtual_quote_reserves`;
  - `gap` (data_loss) → the engine's gap record, so pools are flagged as on the websocket.
- **Stamp.** `t_detect_ms` is the host wall clock at decision. In replay, it comes from a fixed offset given by the harness and is recorded.
- **Classifier and seals.** The RPC classifier stays as at S\*. The Look-2 handling stays as at S\* (pools with s0 ≥ 2026-11-06T00Z are computed; October pools are never in a decision).
- **Identity.** The decode and decision functions are byte-identical to S\* (an identity test like `test_h5_shadow_succ_identity.py`).
- **Proof.** An md5 harness compares S\*' tip mode on writer output built from explore-0814 hours (the qp-1165 arm-T fake tip dir) with S\* on the same walker rows through its existing replay path. Trigger and outcome records are equal after removing `t_recv_ms` and `t_detect_ms`. A minimum-triggers floor is notebooked first.

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
