# P35: long-book hand-off infrastructure (design)

Written 2026-10-26 from 17:03Z (`date -u` 2026-10-26T17:03:08Z) on mal-research-0, branch `claude/p35-longbook-handoff` from origin/main 5071e29a.
Records and tooling only. **No book has cleared the promotion gate. Nothing here says a book makes money.** No tape row, ledger, job status
or job log was opened for this design. It reads code, the runbook and the QP ruling only. The tests use synthetic tape.

## 0. Why, and what QP already ruled

The slow structures (24 h holds, a read window of about 28 days) cannot be read inside the 14-day MiScusi job cap without a hand-off.
`QP-RULING-1025-am15.md` (PR #905, verdict NO) found:

- **Restart budget.** At frozen sha 5e12f8f5, a runner or feed restart excludes every open position. That is about rate x 1 day of the read's
  positions, against the 5 that the 5% cap allows at O = 105. So the only sound budget at that sha is **0 planned restarts in
  [clock, END_H + 25 h)**.
- **(a) Overlap feed hand-off.** Not possible at f5bd26e7 without code:
  - flock LOCK_NB makes a second instance exit 3;
  - a new process writes `process_restart`;
  - the engine turns any `start` row into a hard gap (arena_paper.py:939-941).

  It is sound only as a new, QP-reviewed feed with a warm standby, an atomic lock takeover and signature dedup. It may omit restart rows only
  when continuity is proven to the multi-socket standard, and must fail closed otherwise. It is **not allowed at 11-07**. It is usable only
  at a hand-off after HV4 END_BOOK, with every in-clock tape book named and QP OK for each.
- **(b) State-carrying runner hand-off.** At 5e12f8f5, `recover()` sets `restarted=True` and the position is booked `engine_restart`
  (arena_paper.py:818, 1144-1193). Carrying state is a change to arena_paper (a section 1.4 pin), so it needs a **new frozen sha and a new
  bot_id**. Conditions:
  - (i) the tape is continuous across HO;
  - (ii) the engine's pool history is rebuilt bit-identically;
  - (iii) an exit trigger that falls in the down interval is never decided after the fact, but booked excluded.

  Pre-register: code + unit tests, lamport parity, the Am.6 1.3 smoke, P0 at the new sha, and the md5 proof at >= 2 HO_TEST instants, at
  least one with >= 1 open position.

This design turns those conditions into code, proofs and a schedule. Neither part moves any clock before 2026-11-08.

## 1. What this must never touch

| Never | Why / how it is enforced |
|---|---|
| `tools/arena_feed.py` (blob at f5bd26e7, sha256 `7a9d424b...`), `hv4-feed-shakedown.sh` (sha256 `3d01f7ff...`), `scripts/research/feed-handover-1107.sh` and its pinned copy in `/data/mal/hunt-1008/ops/` | HV4's feed pin (section 1.4). The new feed is a NEW module `tools/arena_feed_v2.py` that imports v1. A test asserts the sha256 of `tools/arena_feed.py` is unchanged. |
| Jobs #1516, #1584 (H), #1585 (C): the 11-07T11:50-11:58Z hand-off | Not cancelled, signalled, re-submitted or resumed by P35 work. The v2 takeover targets only the feed process that holds the production lock **after HV4 END_BOOK** (section 4). Before then the v2 code refuses the production tape (test mode only). |
| HV4: jobs #1561/#1562, its runner at 9ed4c5ff, all HV4 outputs | Not opened, not read, not joined. No P35 job runs on research-0's public-RPC IP before HV4 END_BOOK (2026-11-08T13:05Z) if it adds websocket load (section 3.4). |
| `tools/arena_paper.py`, `tools/arena_rules.py`, `tools/arena_rules_v2.py`, `tools/arena_rule_l1.py`, `tools/arena_state.py`, `tools/arena_pool_table.py` | Pinned by registered books. (b) adds new modules (`arena_paper_ckpt.py`, `arena_rules_v3.py`) that subclass and import them. A test pins their blob sha256s. |
| `/data/mal/arena/bots.json` | Only the E3 registration of the new bot_id writes it, under its own amendment. |
| Q50 (#1419/#1421/#817), DBC forward window and `fwdv1/`, `/data/mal/meta-capture/` | Out of scope. The proof tools refuse `/data/mal/meta-capture` outright. They refuse any path under `/data/mal/arena` unless a QP ruling file is named (its sha256 is printed). |
| Arena tape contents | Every proof prints counts, booleans, md5s and sha256s only: never a signature, mint, pool, wallet, price, fill or P&L. Production-tape runs need a QP ruling naming the window, with the ledger checked first. |

## 2. (a) Gapless overlap feed hand-off

### 2.1 Mechanism: warm standby, then lock takeover

New module `tools/arena_feed_v2.py` (class `FeedV2(Feed)`), mode `--standby-for <out-dir>`:

1. **Standby lock.** The new process takes `flock(LOCK_EX|LOCK_NB)` on `<out-dir>/.arena-feed.standby.lock`. A second standby exits 3. It
   does NOT touch `.arena-feed.lock`.
2. **Subscribe first.** It builds the same sources as v1 (`build_source`, same sockets, same commitment, same dedup windows) and starts
   ingesting. Every notice is decoded with v1's `rows_from_notice` into an **in-memory buffer**. Nothing is written to the tape. The
   `ProgWatch` gap rules run on the standby's own stream from its first notice, so it has its own `hw_slot`, silence and reconnect state.
   - The buffer is bounded (QUEUE_MAX rows; at about 118 rows/s, measured on the 10-18 smoke tape, that is about 7 min).
   - An overflow sets `continuity = "unproven"` (fail closed).
3. **Pool-mint cache warm-up.** While standing by, it reads the `pool_create` and `pool_info` rows of the open and previous hour (types
   filtered at parse; no other field kept), so it writes `mint` on the same rows the old writer would. This is the one field where a cold
   cache changes the row set.
4. **Coverage clock.** Per program p, `cover_from_ms[p]` is the earliest instant from which the standby's link state (`observe.link_state`,
   the same `common_down` the v1 reconnect rule uses) shows **no instant with every standby socket of p down or silent**, up to takeover. A
   later common-down moves `cover_from_ms[p]` forward.
5. **Stop the old writer.** At the pinned instant (a host-side MiScusi job, as in feed-handover-1107.sh: same uid/argv/lock-holder/
   start-time checks, SIGTERM, SIGKILL only after 60 s, disclosed), and only if `cover_from_ms[p]` is at least `OVERLAP_MIN_S` (default
   120 s) before now for both programs. Otherwise it refuses, leaves the old feed alone and retries until a deadline; after the deadline it
   exits loud.
   - v1 on SIGTERM drains its queue, flushes its pending loss rows, writes `stop`, closes the hour file and releases the lock
     (arena_feed.py:1001-1008).
6. **Atomic takeover.** It polls `flock(LOCK_NB)` on `.arena-feed.lock` every 10 ms. Between the old writer's release and this acquisition
   nothing writes, and the sockets keep buffering.
   - If any other process gets the lock first (a resumed old job), the standby never writes a row. It exits 3, and that writer's own
     `process_restart` covers the gap as today.
7. **Read the old tail.** From the newest `.jsonl` (and the previous hour's `.jsonl` or `.zst` if the window crosses an hour) it reads,
   over [min(cover_from_ms) - DEDUP_WINDOW_S, end]:
   - the set of (signature, type, event_index) keys;
   - the last data-row `t_recv_ms` per program (`old_last_data_ms[p]`);
   - any gap rows;
   - the `stop` row.

   Mechanical, in-process; nothing is logged but counts.
8. **Continuity verdict per program p.** `proven` if and only if all of these hold:
   - `cover_from_ms[p] <= old_last_data_ms[p] - OVERLAP_MIN_S * 1000`;
   - the old tail has no data_loss gap of p (or `all`) in [cover_from_ms[p], stop];
   - the old tail ends in a `stop` row (a clean stop: no kill, no torn tail);
   - the buffer did not overflow;
   - the standby's own ProgWatch has no data_loss gap of p since cover_from_ms[p];
   - at least `MIN_SHARED_SIGS` (default 20) buffered signatures of p are also on the old tail. This is the empirical check that both
     writers see the same stream.

   This is the multi-socket standard of the v1 reconnect rule applied to the union {old sockets} u {standby sockets}: data is lost only at an
   instant where every socket of both writers is down. Before old_last_data_ms the old writer was live; from cover_from_ms the standby was.
9. **Write.**
   - **All programs proven.** First one `takeover` row (prog `all`, schema below; NOT a `start` row, which every engine at every sha books
     as a hard gap). Then the buffered rows whose key is not on the old tail, in buffer order (`written_missed`), with their own
     `t_recv_ms`. Then live.
   - **Any program unproven (fail closed).** A `start` row and a `process_restart` gap row exactly as v1 writes them, span
     [old_last_data_ms, now], `reason: "handover_unproven"` plus the failing condition names. Every consumer at every sha then sees exactly
     today's restart. Buffered rows are still written after it (deduplicated), because they are real rows.
10. **Status counters carry over.** `pauses` and `rows_dropped_total` in `arena-feed-status.json` start from the old writer's last status
    values (`counter_base`), so `arena_hour_health`'s `feed_pauses_delta` / `feed_rows_dropped_delta` never see "a counter that went down"
    (arena_hour_health.py:25, 281). `started_ms` and `pid` are the new process's; `handover_from_pid` is added.
11. **Heartbeat cadence.** The first `hb` per program is written right after the takeover row, then every 60 s. The longest hb interval
    across the seam is at most about 60 s plus the stop time, so the hour keeps at least 60 hb rows per program.

`takeover` row (arena_tape_v1 common keys plus):

```
{"v":1,"type":"takeover","prog":"all","slot":0,"t_recv_ms":<lock acquired>,"signature":null,"event_index":null,
 "schema":"arena_tape_v1","handover_v":1,"code_sha":"<v2 head>","pid":<new>,"prev_pid":<old>,
 "standby_started_ms":..,"old_stop_ms":..,"lock_acquired_ms":..,
 "cover_from_ms":{"pumpswap":..,"pumpfun":..},"old_last_data_ms":{"pumpswap":..,"pumpfun":..},
 "continuity":{"pumpswap":"proven","pumpfun":"proven"},
 "dedup":{"buffered":n,"dropped_dup":n,"written_missed":n,"shared_sigs":{"pumpswap":n,"pumpfun":n}},
 "counter_base":{"pauses":n,"rows_dropped_total":n},"overlap_min_s":120}
```

Readers at every pinned sha ignore an unknown row type:
- `ArenaState.apply` uses if/elif on type (arena_state.py:305-328);
- `ArenaEngine.on_row` returns for `typ != "trade"` after the gap/start branches (arena_paper.py:935-945);
- `arena_hour_health` counts only data rows for age and only gap rows for loss.

PR 2 adds a test for each reader. The old writer's `stop` row is already ignored by all three.

### 2.2 What it costs and what it does not fix

- The schema gains one row type. It is still `arena_tape_v1`; `takeover` goes in `tools/arena_tape_schema.md`.
- Missed rows written at takeover can carry a `t_recv_ms` older than the old writer's last row by up to the overlap. That is the same order
  property as a socket reconnect today (late rows), and v1's ORDER contract (gap before the trade after it) is untouched because no gap row
  is written on success.
- The **feed pin changes** for every book that reads the tape after the takeover: the feed sha becomes v2's. QP: allowed only after HV4
  END_BOOK, with each in-clock tape book named and QP OK. At the planned first use (section 4.1) that list includes **HV6** (T0 2026-10-30,
  reads to about 11-11 or later). The manager names the full list; it is not derived here.
- It does not remove the 11-07 restart. That stays the f5bd26e7 hand-off of #1584/#1585.

### 2.3 Proofs and tests for (a)

| ID | What | Where / when | Pass |
|---|---|---|---|
| A-T1 | Fake-socket unit tests: overlap, takeover, lock race (another writer grabs the lock: the standby writes 0 rows, exits 3), a common-down inside the overlap gives `handover_unproven` + `process_restart`, buffer overflow gives unproven, no duplicate row, ordering, counter carry, hb cadence | PR 2, pytest | all green |
| A-T2 | **Tape equivalence**: the same synthetic notice stream through (i) one v1 process and (ii) v1 then a v2 takeover mid-stream. Row sets are equal on every field but `t_recv_ms`, plus exactly one `takeover` and one `stop` row | PR 2, pytest, checked by `feed_handover_proof.py parity` | `PARITY_EXACT` |
| A-T3 | Seam proof on the synthetic takeover tape | PR 1 tool, PR 2 test | `seam` PASS |
| A-P1 | **Live rehearsal** on a scratch out-dir with 2 takeovers in a 6 h run; `feed_handover_proof.py seam` on each seam, and `parity` against the production tape on the same window, split seam/control | PR 3 job, run by an agent off every book's path, counts only, after HV4 END_BOOK or from a different host (3.4) | 0 gap rows of any data_loss kind in either seam window; seam PASS; seam-window miss rate (`only_b / n_b`) not above the control windows' max |
| A-P2 | Both feed shas, the switch instant and the takeover row's sha256 pinned in the records PR of every in-clock book | PR 7 | present before E3 |
| A-P3 | `arena_hour_health` on the takeover hour: no new failure class vs a control hour | after A-P1 | equal |

### 2.4 Rehearsal load (a risk to HV4 if done wrong)

A rehearsal runs extra feeds against `api.mainnet-beta.solana.com` from the same IP as the production feed:
- a scratch old writer and a scratch standby, 4 sockets each;
- plus the production overlap's own doubling during the seam.

A per-IP limit there could make the production sockets drop: data_loss gaps on HV4's in-clock tape. So no P35 process opens a public
websocket from research-0 before 2026-11-08T13:05Z. The early rehearsal runs on mal-fast-0 (its own IP; the fast box's memory rules apply)
with `--out-dir` under `/data2/mal/p35/rehearsal/`. Its parity reference is its own continuous v1 control feed on the same box, not the
production tape. The research-0 rehearsal, if wanted, comes after END_BOOK.

## 3. (b) State-carrying runner hand-off

### 3.1 State, and how each part crosses HO

| Component | Today (5e12f8f5) | P35 |
|---|---|---|
| Decision log (hash chain) | `Host.resume()` replays it (arena_rules.py:453-485) | unchanged, plus a `segment_end` row (log head hash, tape cursor, checkpoint sha256) and a `segment_resume` row |
| Pool table (rule history, 26 h of prints) | checkpoint every 10 min + at end (arena_rules_v2.py:145) | unchanged; the final checkpoint is taken at HO, at the same tape cursor |
| Engine (`ArenaEngine`: pools, gaps, hw, slot clock, positions, pending, priced, counters, halted) | NOT persisted; `recover()` -> `restarted=True` -> `engine_restart` | **NEW** `EngineCkpt(ArenaEngine)` in `tools/arena_paper_ckpt.py`: `checkpoint(path)` writes canonical JSON of the whole engine state with a `state_digest`; `restore(path)` rebuilds it and recomputes the digest; positions in a verified checkpoint keep `restarted=False` |
| `ArenaState` (warm-up + live) | rebuilt by the 365-min warm-up from the tape | rebuilt by warm-up; **digest-compared** at HO_TEST in the proof. If the rule-visible digest differs from the continuous run's, PR 4 adds `ArenaState` to the checkpoint (fallback written into the plan, not decided after the proof) |
| Engine `start` ledger record | one per engine start; the scorer checks frozen_sha | seg 2 writes its start record plus a `handoff` record: prev engine_id, checkpoint sha256, the `segment_end` log head. The scorer for the new bot_id accepts a position priced by engine 2 iff that chain matches; otherwise it falls back to `engine_restart` (fail closed) |

The checkpoint is written by segment 1 **after** the last tick before HO and before exit: rows with `t_recv_ms < HO` applied,
`step(HO - 1000)` done. The cursor is the per-hour byte offset of `ArenaState` and the pool table's `hwm_ms` / `hwm_keys`. It is atomic
(tmp, fsync, rename, fsync dir), and its sha256 goes in the `segment_end` log row and the ledger.

### 3.2 Hand-off sequence

1. **Book lock.** `arena_rules_v3.HostV3` holds `flock` on `<root>/decisions/<bot_id>/.book.lock` for its life. Two segments can never both
   append to one chain.
2. **Seg 2 is a sleeping standby**, submitted days ahead as a host-side MiScusi job (never a session cron). Its command is byte-identical to
   seg 1's (R9). Which segment it is follows from the root's state: "a valid `segment_end` with no `segment_resume` after it" means resume.
   - It imports, opens the tape and runs the `ArenaState` warm-up while seg 1 still runs. Read-only: no log, no ledger, no table write.
3. **At HO**, seg 1 runs its last tick, checkpoints the engine and the table, appends `segment_end`, releases the book lock and exits 0.
   - Seg 1's job is armed with `--segment-end-ms HO`, which must be at least 6 h inside its 14 d limit; it refuses at submit otherwise
     (QP risk 9).
4. **Seg 2** takes the book lock and then, in this order:
   - verifies the chain;
   - checks that the checkpoint sha256 equals the `segment_end` row's, and that the log head equals the one the checkpoint recorded;
   - restores the engine;
   - loads the table;
   - catches the state up to the cursor (dispatch off for rows before HO only).

   Then it runs ticks from **T = HO** with live polling (`Host.run(t_from=HO)`).
5. **Down interval D = [HO, resume_ms).** The live host shows a rule only rows not older than `stale_row_ms` (10 s) at dispatch
   (arena_rules.py:24-25), and ticks T >= HO run once the wall clock passes them.
   - **If resume_ms - HO <= D_MAX = 5 s:** every row and tick in D still reaches the rule, at most 5 s late. That is the same as a live lag
     spike: decision rows are written live, with the write instant as the engine's decision instant (arena_paper.py:15-20). Nothing is
     excluded. The `segment_resume` row records resume_ms - HO.
   - **If resume_ms - HO > D_MAX (fail closed, QP (iii)):** before dispatch, seg 2 runs the rule in a probe context over the rows and
     ticks of D. Orders are captured and never submitted or logged as decisions.
     - Each exit order for a position open at HO gets a `handoff_down_exit` log row. That position's sell goes out at the first tick, and
       the engine books it `excluded` with reason `handoff_down`.
     - Each entry order is counted in a `handoff_down_missed` row (count and attempt-key hashes only; never a position).
     - D's rows are then applied with dispatch off.

     The probe is the book's own pure rule on tape rows. It reads no ledger, fill or P&L.
6. **Crash fallback.** A seg 1 that dies without a `segment_end` leaves no valid checkpoint at the log head. Seg 2 then runs today's
   `recover()` path (`engine_restart` exclusions), and the event is an unplanned restart in LAB_STATE.

### 3.3 New sha / bot_id rule

- (a) and (b) code merge to main; the book's frozen sha **S** is a commit that contains both.
- The rule file is unchanged: FLOOR-SLOW uses `tools/arena_rule_l1.py` byte-identical to its sha at 5e12f8f5. A test pins its blob
  sha256, so the rule logic equals the frozen FLOOR v2 rule.
- The host is new (`arena_rules_v3.py`, `RULE_MODULES_V3 = {"l1-floor-slow-v3": "tools.arena_rule_l1"}`), so the **bot_id is new**:
  `l1-floor-slow-v3` (name to be fixed in the amendment). `l1-floor-v2` at 5e12f8f5 is never reused.
- Every segment of the book runs at S. Seg 2 refuses a different HEAD, a dirty tree, or a registry sha that is not S
  (`verify_registered`, as v1).
- Any code change after registration means a new bot_id and a new clock. The hand-off changes no code, so it needs no new sha.
- The md5 proof (3.4) runs **at S** and is re-run if S changes.

### 3.4 Proofs and tests for (b)

| ID | What | Pass |
|---|---|---|
| B-T1 | `EngineCkpt` unit tests: checkpoint -> restore in a **fresh process** (subprocess) reproduces `state_digest`; positions, pending sells, priced/late-print lists, gaps, slot clock, trims; a tampered checkpoint (sha256 or log-head mismatch) falls back to `engine_restart` | green |
| B-T2 | Synthetic md5 equivalence: synthetic tape + a toy deterministic rule (TP on a print, time exit) through the real `HostV3` and `EngineCkpt`. R0 continuous vs R1 seg1 + seg2 at HO_TEST with D = 0 | decision-row md5 equal; ledger md5 equal; state digests at HO equal |
| B-T3 | Fail-closed D: the same with D = 30 s (> stale_row_ms); positions whose time or TP exit falls in D | exactly those pids are `handoff_down`; every other row is identical; missed entries are counted |
| B-T4 | Lock: seg 2 started while seg 1 holds the book lock appends nothing | green |
| B-P1 | **Real md5 proof at S** (`tools/longbook_handoff_proof.py`), replay mode (sim clock), output only under a scratch root outside `/data/mal/arena`, on a **QP-pinned pre-2026-10-25 arena slice** (ledger checked first; the HV6 real-layout drill pattern). HO_TEST instants are fixed before the run by rule: t0 + 30 h and t0 + 54 h. If the open count at an instant is 0, step +1 h up to 12 times, until at least one instant has >= 1 open position (count only). Runs: R0 continuous; R1, R2 (D = 0) at each HO_TEST; R3 with D = 30 s at the instant with an open position. Seg 2's command is byte-identical to production's (R9) | R1/R2: decision md5, ledger md5 (minus engine_id, pid, written_ms) and state digests equal to R0. R3: equal except the listed `handoff_down` pids and missed-entry count. Prints md5s, counts and pid hashes only: never a pool, price or P&L |
| B-P2 | Section 1.4 lamport parity at S; Am.6 1.3 smoke at S; P0 at S (Am.4 2.1) | as registered |
| B-P3 | Real-layout timing at HO_TEST on the live tape layout: resume_ms - HO from seg 2's standby start (counts only) | <= 5 s, else the fail-closed path is the planned one and that goes in the amendment |

The ledger rows the replay writes are in-sample P&L on pre-10-25 hours. They stay in the scratch root, are never opened by a person or
agent, and are deleted after the md5s are notebooked (like `drill_hv6_real_layout.py`).

## 4. First use: FLOOR-SLOW (AUDIT.md lane 1, origin/claude/p32-survivor-audit)

Lane 1 re-registers the frozen FLOOR rule (`l1_rule_tpfix`, 0.25 SOL, 1.9 s leg, rent charged, 24 h hold) for a window of about 28 days
at about 6 attempts a day (AUDIT.md; A2 measured 6.0). That window spans at least two feed job lives and two runner job lives, so it
cannot be read without (a) and (b). AUDIT.md's own honest estimate is 15-25% PASS; the lane's kill rules (< 4/day on counts first,
futility at day 10) stand.

### 4.1 Schedule (instants from the records named; to be pinned in the amendment, R4)

| Step | Instant | Source |
|---|---|---|
| 11-07 hand-off, f5bd26e7 -> f5bd26e7 (H #1584, C #1585) | 2026-11-07T11:50-11:58Z | runbook feed-handover-1107; not touched |
| HV4 END_BOOK | 2026-11-08T13:05Z | docs/HANDOFF.md HV4 row |
| H's own limit end (submit + 14 d) | about 2026-11-09T02:01Z (submitted about 10-26T02:01Z) | docs/HANDOFF.md line 52; the exact instant comes from H's notebook entry, not from its log |
| **First v2 overlap takeover (H -> v2)** | inside [11-08T14:00Z, 11-09T00:00Z], pinned in the amendment | a hand-off is needed there anyway, because H ends; QP OK per in-clock tape book first (HV6 at least) |
| If QP refuses v2 for any in-clock book | the 11-09 hand-off is a plain f5bd26e7 restart (the 11-07 pattern), and v2's first use moves to the next forced feed hand-off (about 11-23) | |
| FLOOR-SLOW registration (new bot_id at S) | after the v2 takeover and A-P1, B-P1 and B-P2 are done | |
| FLOOR-SLOW clock | the first 00:00Z >= max(takeover + 24 h, registration + the amendment's warm-up) | |
| Feed hand-offs in the book window | every <= 13.5 d (v2 -> v2 takeovers) | |
| Runner hand-offs | seg k ends <= 13.5 d after its start; never within 6 h of a feed hand-off, so each seam has its own proof and a failure of one is attributable | |
| D1 sampler / D3 table jobs (14 d caps, QP risk 5) | overlapping successor jobs (two samplers write separate files; the hour-health reader takes the union) | owed in PR 3 |

### 4.2 What it buys and what it does not

With (a) and (b), a hand-off excludes nothing when both proofs hold and D <= 5 s. It excludes the `handoff_down` positions (expected about
0 at D <= 30 s) otherwise. That makes a 28-day read decidable at rate 6, where QP found it NOT_DECIDABLE in expectation at 5e12f8f5.

It does not:
- move any clock before 11-08;
- lower the gate;
- cover a PumpSwap data_loss gap from the public node (each still costs about rate x 1 day against the 5% cap: QP R13);
- make FLOOR positive.

FLOOR has 0 OOS trades.

## 5. PRs and effort

| PR | Content | Depends | Effort (worker-days) |
|---|---|---|---|
| 1 (this) | DESIGN.md; `tools/feed_handover_proof.py` (seam + parity, counts only); `tools/test_feed_handover_proof.py` on synthetic tape | none | 0.5 |
| 2 | `tools/arena_feed_v2.py` (standby, takeover, dedup, continuity, fail-closed, counter carry, mint warm-up) + fake-socket tests A-T1, A-T2, A-T3; schema doc row; reader-ignores-`takeover` tests; arena_feed.py sha pin test | 1 | 1.5 |
| 3 | `scripts/research/feed-takeover-v2.sh` (pinned instant, process checks, as 1107) + runbook + rehearsal job spec (mal-fast-0, scratch) + sampler/table successor jobs; run A-P1 | 2 | 1.0 + 6 h run |
| 4 | `tools/arena_paper_ckpt.py` (`EngineCkpt`: checkpoint, restore, state_digest, handoff recover) + B-T1; pin tests for the unchanged arena modules | none | 1.5 |
| 5 | `tools/arena_rules_v3.py` (`HostV3`: book lock, `--segment-end-ms`, segment_end / segment_resume, D rule, probe) + scorer support for `handoff` records + B-T2, B-T3, B-T4 | 4 | 1.5 |
| 6 | `tools/longbook_handoff_proof.py` + synthetic run in tests; QP-pinned real-slice job B-P1 and timing B-P3 | 5 | 1.0 |
| 7 | Records: FLOOR-SLOW amendment (R1-R13 applied; schedule 4.1; both feed shas; switch instant; S; bot_id); manager + QP | 3, 6 | 0.5 |

About **7.5 worker-days in 7 PRs**. Tracks (a) = PRs 1-3 and (b) = PRs 4-6 are independent, so two builders finish by about **2026-11-04**
if started 10-27, leaving 11-05..11-06 for QP rulings on A-P1 and B-P1. The schedule risk is QP turnaround and the rehearsal, not code.

## 6. Open items for QP

1. Is `OVERLAP_MIN_S = 120` with `MIN_SHARED_SIGS = 20` per program enough for "continuity proven", or should the threshold be set from
   the rehearsal's control-window miss rate?
2. D_MAX = 5 s as "a live lag, nothing excluded" (stale_row_ms is 10 s): acceptable, or must every D > 0 use the probe path?
3. Is the `ArenaState` warm-up + digest compare acceptable, or must `ArenaState` be checkpointed from the start?
4. Which pre-10-25 arena slice for B-P1, and whether the replay's scratch ledger may exist at all (it is in-sample P&L, never opened).
5. The list of in-clock tape books at the first v2 takeover, and the OK for each.
