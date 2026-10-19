# HV4 registration records, DRAFT for T0 2026-10-21T00:00:00Z (DEC-031 §1.2, Amendments 2 and 3; written before any HV4 book has a clock)

Draft written by a worker on 2026-10-19 at about 21:58Z (`date -u`). Nothing here is registered yet. No `bots.json` line exists for these nine books; the manager prints it with `--register` (hv4_runner.py:1702-1707@d1dbcd6) and copies it into the notebook before the clock (§1.2). No arena ledger, shakedown ledger, `bots.json` content, tape or outcome was opened to write this. Every number comes from code at `d1dbcd6`, a pinned document, or a quant-proof comment named below. Where a number depends on the stub or on other books at read time, the text says **TBD**.

**No book has cleared the gate. This is a registration, not a result.** Nothing here says any HV4 book makes money.

## 1. The book
| Field | Value |
|---|---|
| Book | Haiku trader v4 (HV4), **lane G only** (Design Note N2) |
| `n_bots` | **9**, `lane_h` **false** (registration must print `{"lane_h": false, "n_bots": 9}`; SHAKEDOWN-CHECKS-2 check 10) |
| `bot_id`s | `bot_id(arm) = f"hv4-{arm}"` (hv4_runner.py:99-100@d1dbcd6) for the arms CON-L, CON-F, CON-P, CON-X, CON-R, BIG-L, BIG-F, BIG-X, BIG-R: `hv4-CON-L`, `hv4-CON-F`, `hv4-CON-P`, `hv4-CON-X`, `hv4-CON-R`, `hv4-BIG-L`, `hv4-BIG-F`, `hv4-BIG-X`, `hv4-BIG-R`. BST-L/F/X/R are not registered and get no read (DESIGN Note N2). |
| `frozen_sha` | `d1dbcd64fc434590275d34a3b65d6ad5d45d86ce` (origin/main, PR #755 merge; descends from #745) |
| `clock_start_ms` | `1792540800000` = **2026-10-21T00:00:00Z**. Fallback if the registration is not complete before that instant: the first 00:00Z after the registration record (DEC-031 Am.3 item 1, "Clock"; never earlier). The fallback value for 2026-10-22T00:00:00Z is `1792627200000`. |
| `deciding_stake` | 0.25 (register_all, hv4_runner.py:683@d1dbcd6) |
| `model_ids` | per register_all (hv4_runner.py:682): the model id for the LLM arms and the R arms, `[]` for the X arms. Model id **TBD: copy from the printed registration lines** (DESIGN §6 names `claude-haiku-5-5` only). |
| Cells | 22 (DESIGN Note N2): flat 0.25/0.5/1.0 for CON-L/F/X and BIG-L/F/X (18) plus as-sized for CON-L/F and BIG-L/F (4). 1.9 s fill, flat 15% and pressure-fail. Flat 0.25 is each book's primary. |
| Registration lines | **TBD** (copy of the nine `bots.json` lines, from the `--register` output, with the notebook entry id) |
| T0 job | A host-side MiScusi job on mal-fast-0 (not a session cron). Job id **TBD**. One process per `frozen_sha` (Am.3 item 1): a clean checkout at `d1dbcd6`. |

## 2. Pins
### 2.1 Documents (mutable `/data` files, pinned by hash; no repo copy exists yet: **TBD, manager decides whether to place byte copies in the repo as Am.3 did for its ruling**)
| sha256 | file |
|---|---|
| `e3eb46273e9aea1cac6a9e7efad83336264b1265334487debc4344f47cea588c` | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md` (292 lines; includes Amendment A1, Note N1 and Note N2) |
| `af5052d5bae4efb683d745ed0fbb9aa6c680bc44a79ddfaf3cef7c3110831b18` | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/SHAKEDOWN-CHECKS-2.md` (revision 2; supersedes revision 1) |
| `3d01f7ff6d51c22219fb116f438dac81fc6e657fdadcd961cd7bb19b96ec7205` | `/data/mal/hunt-1008/ops/hv4-feed-shakedown.sh` |
| `727de6666cef28a8757c76b3d63912ea71efc3f1667ebcdb5c3fed7dea6a6bfb` | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/QP-RULING-1019-startup.md` (quant-proof, 2026-10-19T14:57:04Z; the DESIGN and SHAKEDOWN-CHECKS-2 pins it carries equal the two rows above) |

If any of these files is edited before the clock, the hash above is stale and the edit is disclosed in every read.

### 2.2 Code at `d1dbcd6` (identical on origin/main `489fa7e`; `git diff d1dbcd6 489fa7e` touches only `tools/arena_screen.py` and its test)
| sha256 | file | note |
|---|---|---|
| `9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514` | tools/arena_paper.py | **differs from the 2026-10-18 pin** (`9a23f2aa…`, arena-records-2026-10-18.md §1) by PR #745 (`trim_history()`, opt-in). Disclosed in every HV4 read (DEC-031 §1.4); QP ruled on #745 and #755. |
| `a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778` | tools/arena_stack.py | equals the 10-18 pin |
| `8af345618e51ca480ee7538717aa001b4ad7ed5ef38941faaec812bbe217f639` | tools/arena_state.py | equals the 10-18 pin |
| `7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f` | tools/arena_feed.py | equals the 10-18 pin |
| `a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34` | tools/arena_score.py | equals the 10-18 pin |
| `a1a7628ca6e69e7c20b16c27e5462f8b127fc166ba886f45ae8a2406516b9f31` | tools/hv4_runner.py | the QP #755 comment asks the records to give this hash |
| `4f45de4c4b19bdecfdf5bff0ea980a566a0edd4293cb751aa0217e902a9211e7` | tools/hv4_cards.py | the V-band filter (2.3) |
| `0cb4e1642d94208db84eb5669221837d858db619bd338beb248af3e547894ce9` | tools/hv4_read.py | **not part of the running process.** Its "25 cells × 1 read = 25 checks" text (line 31) must be changed to 22 in a reviewed PR before the first read (DESIGN Note N2). Owed. |

### 2.3 DEC-031 Amendment 2 for HV4 (covered only if both conditions of Am.2 item 2 hold at the registered sha)
- **Rent 0 passed explicitly (condition (i); item 3; item 8).** `STACK_RENT = 0` (hv4_runner.py:1619@d1dbcd6); `STACK_FIELDS` spells every `StackConfig` field but `feed_delay_slots` as a literal (hv4_runner.py:1620-1623), including `"rent": STACK_RENT` and `"k_rounding": "ceil"`, `"e1_floor_per_005": 384_022`, `"strand_rule": "both"`, `"gap_slots": 150`, `"gap_silence_ms": 60_000`; `stack_config()` (hv4_runner.py:1626-1639) takes only `feed_delay_slots`; `main()` builds the engine from it (hv4_runner.py:1697). The strand rent stays the engine default 2,039,280 (hv4_runner.py:1632-1634). Deciding `feed_delay_slots`: 7 (feed delay d, arena-records-2026-10-18.md §3; SHAKEDOWN-CHECKS-2 line 8 runs the stub with `HV4_FEED_DELAY_SLOTS=7`); the T0 job's command line is **TBD** to confirm 7.
- **V band (condition (ii); item 6).** `V_LO_LAMPORTS = 17_500_000_000`, `V_HI_LAMPORTS = 17_700_000_000` (hv4_cards.py:63-64@d1dbcd6). In `filters`: `quote_wsol` is checked first (hv4_cards.py:419-420), then V: a WSOL pool without V fails as `v_missing` (hv4_cards.py:424), otherwise `v_band` requires `V_LO_LAMPORTS <= v <= V_HI_LAMPORTS`, both ends inclusive (hv4_cards.py:426). A non-WSOL pool fails `quote_wsol` alone and its V is not read (hv4_cards.py:409-411). Introduced by PR #749.
- **Read statement.** Rent 0 decides, the rent-charged leg is printed beside it (Am.2 items 7, 9). **TBD: the merge instant of Amendment 2 must precede both this registration and `clock_start_ms` for coverage (Am.2 item 2); the manager records the merge commit time here.**

## 3. Declarations (SHAKEDOWN-CHECKS-2, "Declarations before T0", unchanged)
1. A trade whose sell was refused or delayed is kept and counted **at the fill it actually got**; never dropped or re-priced.
2. Every read reports the trades with a refused or delayed sell, the `sell_alert` and `sell_orphan` counts, and the longest wait from the first sell decision to the landed sell.
3. `solders` is out of scope for this book unless the read tool imports `tools.h5_executor`.
4. DESIGN.md (with Amendment A1 and Notes N1, N2), SHAKEDOWN-CHECKS-2.md and `n_bots` 9 are pinned in section 1 and 2.1.

## 4. Conditions from quant-proof on PR #755 (comment 6276972823)
- **Memory plan: TBD, to be chosen from the stub's MEASURED slope (not 150 MB/h) and declared here before T0.**
  - **(A) No planned restarts.** MemoryMax >= 1.5 × (RSS after the drain + slope × read hours). For a 7-day read at 154 MB/h that is about 41 GB. QP prefers (A) on risk alone.
  - **(B) Fixed restarts.** One fixed UTC instant at a cadence of at most min(24 h, half the projected hours to MemoryMax); under 10 GB that means every 24 h. Cost, stated: each restart books every recovered position as an excluded `engine_restart` row, and a book above 5% excluded is NOT_DECIDABLE; the excluded share is about (restarts × open positions per book at each restart) / (book trades). At the gate's 100 trades with 7 restarts a book can afford 5 recovered positions in total. Each restart also opens a catch-up window (section 5). The stub cannot measure open positions per restart for the LLM arms (StubModel abstains).
  - **Chosen plan: TBD. Stub slope over hours 2..24: TBD MB/h. Projected hours to MemoryMax: TBD. MemoryMax: TBD.**
  - Either way: restarts are host-side MiScusi jobs (CLAUDE.md), not session crons; restart job ids **TBD**; every restart is recorded in LAB_STATE; an unplanned restart or OOM is disclosed as unplanned; an orphaned recovered position makes the read NOT_DECIDABLE (QP-RULING-1019-startup point 5). An OOM kill in the stub is a stub failure, not an excluded restart.
- **`trim_ms` block.** If any stub line of `engine-trim-counters.jsonl` has `trim_ms` > 1,000, HV4 does not register until QP re-rules.
- **`v_missing` evaluation (declared by QP before the stub).** On every line of `engine-trim-counters.jsonl`, per `engine_id` segment, the ratio `v_missing_pools / v_checked_pools`. If it is above 0.5 on every line of the stub, HV4 does not register until the cause is explained. The report carries the counters per segment and never adds distinct pools across a restart; it must not say "44% of the universe is lost to V".
- **NOT_DECIDABLE rule, pre-registered before T0 (QP on #745 item 4 (3)).** If any line of `engine-trim-counters.jsonl` has `rows_below_trim_floor` > 0 or `refused_history_trimmed` > 0, equivalence with an untrimmed engine is void for every book on that engine for that segment, and the read is NOT_DECIDABLE. The same condition fails the stub's trim check. (arena_paper.py:99-102@d1dbcd6 states the same void condition.)
- **Pins as path:line@`d1dbcd6`.** `RunnerConfig.bound_memory = True` (hv4_runner.py:508) and `trim_engine_history = True` (hv4_runner.py:512; no CLI switch); `HOUSEKEEP_EVERY_MS = 5 * MIN_MS`, 300,000 ms (hv4_runner.py:77); `TRIM_KEEP_SLOTS = 5_000` (arena_paper.py:104); `ENGINE_TRIM_SCHEMA = "hv4_engine_trim_v1"` (hv4_runner.py:326); no `prune()` call (the text `.prune(` appears only in comments, hv4_runner.py:510 and :619).
- **Trim-pause disclosure, in every read.** "Engine trim every 5 tape minutes on the row thread; measured trim_ms p50/p99/max [TBD]; a decision written in a trim tick is priced up to that much later." Check 6 stays as written; trim minutes are not excluded.
- **Evidence scope (QP).** The md5 equivalence of the trim was shown on 9 h with code-rule arms only (builder) and on a 2 h decision log with an empty ledger (QP's own run, which did not compare fills). No LLM-arm fill path is shown. The residual memory grower (about 150 MB/h) is unidentified. #1236 is not fixed by #755.

## 5. Start-up catch-up (QP-RULING-1019-startup.md, ruling (b), C1 to C3)
### C1: VERIFIED at `d1dbcd6` (by code reading and by tests; QP read it at 77bdf7e and left it UNVERIFIED)
**Claim.** A buy or sell called while the apply hook lags the wall clock lands at or after the slot of the wall-clock call instant, not at the slot of the last feed row the engine has seen.

Trace (every line `@d1dbcd6`):
1. **Who calls, with what.** The state is drained by `Runner.poll_to(T)` = `state.poll(until_ms=T - 1)` (hv4_runner.py:661-662), called first in `step(T)` (hv4_runner.py:1562), before `exits` (1568). Each row goes through the wrapped `state.apply` (hv4_runner.py:581-587) into `Runner._on_row` (593-598) and `engine.on_row`. `Runner.slot`, the `decision_slot` the runner passes, is the highest trade slot applied so far (hv4_runner.py:595-597), so it lags in a drain. A buy is `engine.buy(bot, card["id"], size, dec_ms, self.slot, ...)` (hv4_runner.py:1045); a code exit is `dec = T` (1257, the logical tick, behind the wall clock in a catch-up) and `engine.sell(bot_id(pos.arm), pos.pid, 1.0, dec, self.slot, ...)` (1273); an LLM answer stamps `dec_ms = now_fn()` in live mode (`_decision_ms`, 658-659). `main()` builds the engine with no `now_fn` and no `sps_fn` (hv4_runner.py:1697), so the engine's clock is the wall clock (arena_paper.py:850) and its sps comes from the tape's block times (`SlotClock`, arena_paper.py:1047-1049; h5_shadow.py:406-449). The runner overrides the engine's clock only in sim mode (hv4_runner.py:578-579).
2. **The write instant ignores the feed lag.** `_write_instant` takes `call = int(self.now_fn())` and returns `max(decision_ms, call, hw_ms)` (arena_paper.py:1062-1066). In a catch-up `hw_ms` (the newest `t_recv_ms` seen, set in `on_row`, arena_paper.py:973-976) is about 130 s old, so the instant is the wall-clock call time. A stale `decision_ms` is priced at the write instant, never refused (docstring arena_paper.py:22-23, 1082-1083); only a future stamp is refused (1068-1072).
3. **The instant is mapped to a slot by elapsed wall time.** `slot_at(ms, sps) = hw_ms_slot + floor((ms - hw_ms) / (sps * 1000))` (arena_paper.py:1051-1054). `ms - hw_ms` is the lag, so the extrapolation adds the lag's worth of slots to the last row's slot.
4. **Buy.** `sd = max(int(decision_slot), self.slot_at(instant, sps))` (arena_paper.py:1127) with `instant` from line 1087. `decision_slot` can only push the landing later (docstring 1335-1337). So `sd >= slot_at(wall-clock call instant)` by construction.
5. **Sell.** `call, instant = self._write_instant(decision_ms)` (1178); `xs = max(int(decision_slot), self.slot_at(instant, sps))` (1183); a sell with `xs < sd + k_slow + 1` is refused (1185-1186).
6. **Landing.** `sdc, xsc = pos.sd + d, pos.xs + d` (arena_paper.py:1351); entry legs land at `sdc + k` for the 1.3 s and 1.9 s legs (1361-1362; `_needed_slot` 1206-1210). The PumpSwap entry state is the first print with slot >= landing + 1 (`mint_states`, arena_stack.py:138, 141; bonding: the last print at or before the landing slot, arena_paper.py:781-788).
7. **No pricing from the lagging feed.** `sell` appends the position to `pending` and calls `_try_pending` (1202-1203). A position is priced only when `h.last_slot >= need` or `hw_slot >= need + grace_slots` (10,000 slots) (arena_paper.py:1273-1278). In a 130 s catch-up the tape the engine has seen is about 325 slots short of `need`, so the sell waits for the drained tape to reach the landing slot, and is then priced from prints at or after it (state_src `tape`).

Tests: `tools/test_hv4_catchup_landing.py` (new; no runner, model or pinned file edited). On synthetic tapes, with the engine 130 s behind its clock:
- engine level, one print per slot, exact: `sd` = the wall-clock slot, 325 slots after the last row seen; `xs` likewise; the sell stays pending; after the drain the ledger's landing slot is `sd + d + k`, `Qe` is the print at landing + 1 and `Qx` the print after the exit landing; both the stamp = wall clock and the stamp = stale tick are run;
- a lagging engine and a current engine give identical `sd`, `xs`, landing slots, `Qe`, `Be`, `Qx`, `Bx`;
- decision_ms and decision_slot cannot pull the landing earlier; a future stamp is still refused;
- with the tape-derived `SlotClock` that `main()` uses: fitted sps 0.3999993 s/slot on a 0.4 s/slot tape, `sd` equals the true wall-clock slot, 325 slots after the last row. The general error is the lag times the relative sps error, plus the floor in `slot_at` (bounded in the test);
- runner level: the real `Runner._open` and `Runner._sell` in live mode, with a drain through `ArenaState.poll` and the wrapped hook, 130 s behind a shared fake wall clock; the fill's `Qe`, `Be`, `Qx`, `Bx` equal the tape prints after the landing slots, and differ from the print the lagging feed showed at the call.

Mutation check (not committed): replacing the write instant by the last feed row's time, or `slot_at` by the last row's slot, makes 8 of the 9 tests fail (the 9th is the future-stamp test).

**What C1 does not claim.** (a) It says nothing about what the bot SAW: decisions in a catch-up window are made on a state behind the wall clock; C2 is the control for that. (b) Observation, not C1: the 15% guard compares the landing price with the price at `call_feed_slot`, the engine's latest feed slot at the call (arena_paper.py:1332-1341, 1356-1358), which in a catch-up is the lagging slot, 325 slots before the landing in the test (pinned by `test_observation_not_c1_the_guard_reference_is_the_lagging_feed_slot`). A live bot with a lagging feed would quote a stale reference too, but this is where the lag enters pricing, and QP may want it in C2's with-and-without table. (c) The slot mapping is a lower bound of the chain slot (feed delay `d`, arena_paper.py:25, 1052); `d` is added at the landing. (d) The extrapolation error scales with the lag; the stub measured about 130 s at the first minute, and the T0 catch-up is to be measured, not assumed (C3).

### C2 (read rule, declared now)
The read lists every catch-up window: the T0 start and each restart, by QP-RULING-1019-startup (a) (from the start row to the end of the first full minute with `tick_timing.exit_check_delay_ms` p99 <= 2,000 and max <= 10,000, capped at 10 min; a catch-up longer than 10 min fails check 6). Per book it reports the trades whose entry or exit decision falls inside a window. Their fills are kept as booked (Declaration 1) and the read states its verdict both with and without them. Recovered positions are booked `engine_restart` whenever their sell fires and count toward the 5% excluded share.

### C3 (T0 job)
The T0 job starts only as early as needed before 2026-10-21T00:00Z (the warm-up is 365 min of tape, `WARM_MIN_DEFAULT`, arena_state.py:66, fixed when the job builds its `ArenaState`; an earlier build makes the first drain longer). The T0 catch-up is reported like a restart's. Job start instant **TBD**; the measured T0 catch-up window **TBD**. Run-mode caveat: the stub has no Haiku calls inside scans, so the stub's catch-up time is not the T0 catch-up time.

## 6. DEC-031 Am.3 multiplicity block for this book
- **N** (every book with a clock before the read, stopped and NOT_DECIDABLE books included, plus the verdicts of the books already read): **TBD at read time.** HV4 is nine `bot_id`s with their own clocks; **TBD** whether the nine count as nine books or as one in N (the manager and QP rule; this draft does not choose).
- **Per book:** the agent (Haiku v4, `claude-haiku-5-5`); DECLARE sha256 and notebook id **TBD**; cells declared 22, priced **TBD**, killed **TBD**, surviving **TBD**; blocks: the arena tape from 2026-10-21T00:00Z; October hours used: none by HV4's design (its code was never priced on arena hours; the stub's ledger is not read).
- **Cells in total** across hunt23, J3, cohort 2 and the arena: **TBD at read time.**
- **Gate multiplicity for this book:** 22 cells × 1 read = 22 checks (DESIGN Note N2; Amendment A1 item 8 gave 25 before N2). Plain arithmetic, not a measurement: with no edge anywhere, 1 - 0.95^22 = 67.6% for at least one of these 22 checks to pass a 5% test, if they were independent (they are correlated).
- **p** (one-sided, 10,000 draws, seed 1, the larger of the two fail models): **TBD at the read.**
- **The plain line:** if no book has an edge, the chance of at least one screen pass is up to 1 - 0.95^N: **TBD** for this N (40% at N = 10, 79% at N = 30, per Am.3 item 3).
- **Screen-read line:** p and Holm rank among all arena reads, report-only: **TBD at the read.**

## 7. STUB RESULT (to be filled from the shakedown report; nothing below is measured yet)
Stub: sha `d1dbcd64fc434590275d34a3b65d6ad5d45d86ce`, dir `/data/mal/arena/_shakedown-hv4-1020`, ends 2026-10-20T21:32Z. Counts and timings only; the stub's engine ledger is never opened. StubModel trades are not evidence for any book.

| # | Check (SHAKEDOWN-CHECKS-2) | Result |
|---|---|---|
| 1 | 0 `as_of_refusal` rows and 0 LeakageError | TBD |
| 2 | `chain_stale` <= 2% of hot-pool cards, CON and BIG separately | TBD |
| 3 | every `chain_unavailable` card's pool first seen within 10 min before the scan | TBD |
| 4 | null-holders cards <= 10%, CON and BIG separately | TBD |
| 5 | chain calls <= 1,500 per rolling hour and <= 40,000 per UTC day; the 00:00Z-crossing hour and the restart-crossing hour named; `spend_state_reset`, `spend_write_errors`, `state_write_errors` each expected 0; Helius credits reported | TBD |
| 6 | timing: scan wall p99 <= 2,000 and max <= 5,000 ms; exit-check delay p99 <= 2,000 and max <= 10,000 ms outside each process start's window (QP-RULING-1019-startup (a); initial start and deliberate restart; excluded minutes listed with raw n_ticks, p50, p99, max); trim minutes included | TBD |
| 7 | `sell_alert` = 0, `sell_orphan` = 0 (or explained); "already has a sell" drops = 0 | TBD |
| 8 | after the deliberate restart: `lane_h` false, same `frozen_sha`; decision-log chain verifies; no duplicate rows; chain-credits.json and chain-prefetch-spend.json reload with no reset | TBD |
| 9 | feed #1207 writes throughout; data_loss gaps reported; disk reserve not paused | TBD |
| 10 | registration printed `{"lane_h": false, "n_bots": 9}`; no BST log, book or bot_id anywhere in the shakedown files | TBD |

Reported per persona: scans, cards, setups by type, chain_unavailable (and how many carried a setup tag), chain_stale, null-holders cards, stub LLM calls, X-arm entries and exits: **TBD**.

Items QP asked the stub report to carry on #755:
| Item | Result |
|---|---|
| trim-file line count (about 288 per 24 h plus about 73 per start), monotone per `engine_id`; number of `engine_id`s (= process starts) | TBD |
| final `hist_trimmed_prints`, `rows_below_trim_floor`, `refused_history_trimmed`, `rows_dup` | TBD |
| `trim_ms` p50/p99/max per hour against `pools` and `prints_held`; any `trim_ms` > 1,000 blocks registration | TBD |
| `hook_errors` prefixed `housekeep:` (expected 0) | TBD |
| hourly RSS; slope over hours 2..24 outside catch-up windows; projected hours to MemoryMax = (MemoryMax - RSS after the drain) / slope | TBD |
| `filter_v_missing`, `filter_v_band`, `v_checked_pools`, `v_missing_pools` per `engine_id` segment; ratio `v_missing_pools / v_checked_pools` on every line (> 0.5 on every line blocks registration) | TBD |
| v_missing share of newly checked pools, per hour | TBD, or "not measured" |
| cause statement: how many v_missing pools never show V in any tape row of the window, how many gain V later (counts from the tape) | TBD, or "not measured" |
| engine-side counters `v_missing` and `v_out_of_band` beside the card-side counts | TBD |
| memory plan (A) or (B) with numbers (section 4) | TBD |
| stub catch-up window of the initial start and of the deliberate restart (QP-RULING-1019-startup sentence for the report) | TBD |

## 8. What is not covered
- C1 is a statement about the landing slot. It is not a statement that catch-up decisions are free of bias (C2 reports them with and without).
- The stub measures no run-mode scan time with Haiku calls and no LLM-arm fill path.
- Nothing here is an edge claim. `quant-proof` reviews any later sentence that says a book made money.
