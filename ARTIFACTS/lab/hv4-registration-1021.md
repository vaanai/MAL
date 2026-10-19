# HV4 registration records, DRAFT for T0 2026-10-21T00:00:00Z (DEC-031 §1.2, §1.3, Amendments 2 and 3; written before any HV4 book has a clock)

Draft first written by a worker on 2026-10-19 at about 21:58Z (`date -u`); revised the same evening after quant-proof's pre-review (PR #762 comment 6278428501 on 2d2284b, called "QP" below). Nothing here is registered yet. No `bots.json` line exists for these nine books; the manager prints it with `--register` (hv4_runner.py:1702-1707@d1dbcd6) and copies it into the notebook before the clock (§1.2). No arena ledger, shakedown ledger, `bots.json` body, `runner-status.json`, tape or outcome was opened to write this. Every number comes from code at `d1dbcd6`, a pinned document, a quant-proof comment, or the manager's job list named below. Where a value depends on the stub, on the registration output or on other books at read time, the text says **TBD**.

**No book has cleared the gate. This is a registration, not a result.** Nothing here says any HV4 book makes money.

## 1. The book
| Field | Value |
|---|---|
| Book | Haiku trader v4 (HV4), **lane G only** (Design Note N2) |
| Host | **mal-research-0** (DESIGN Note N2 "the research-0 runner"; the stub root and the trader's Anthropic credential file are on it). An earlier draft said mal-fast-0; that was wrong. |
| `n_bots` | **9**, `lane_h` **false** (registration must print `{"lane_h": false, "n_bots": 9}`; SHAKEDOWN-CHECKS-2 check 10). QP confirms lane H off and these 9 lane-G bot_ids (comment 6278428501, ruling (d)); QP does **not** confirm "22 checks" (section 6). |
| `bot_id`s | `bot_id(arm) = f"hv4-{arm}"` (hv4_runner.py:99-100@d1dbcd6): `hv4-CON-L`, `hv4-CON-F`, `hv4-CON-P`, `hv4-CON-X`, `hv4-CON-R`, `hv4-BIG-L`, `hv4-BIG-F`, `hv4-BIG-X`, `hv4-BIG-R`. BST-L/F/X/R are not registered and get no read (Note N2). |
| `frozen_sha` | `d1dbcd64fc434590275d34a3b65d6ad5d45d86ce` (origin/main, PR #755 merge; descends from #745) |
| `clock_start_ms` | `1792540800000` = **2026-10-21T00:00:00Z**. Fallback if the registration is not complete before that instant: the first 00:00Z after the registration record (DEC-031 Am.3 item 1, "Clock"; never earlier); for 2026-10-22T00:00:00Z the value is `1792627200000`. |
| `deciding_stake` | 0.25 for every arm (register_all, hv4_runner.py:683@d1dbcd6) |
| `model_ids` | From the `--register` output (QP): one exact id on CON-L, CON-F, CON-P, CON-R, BIG-L, BIG-F and BIG-R; `[]` on CON-X and BIG-X (register_all, hv4_runner.py:682). Id **TBD** (DESIGN §6 names `claude-haiku-5-5` only). If the printed id is an alias, the model id the API returns on the first live reply is recorded here too: **TBD**. |
| Sampling settings (§1.2: "the exact model IDs and sampling settings") | Thinking **disabled**; **no temperature sent** (the API default). `max_tokens`: entry 400, risk 400, recheck 120, review 4000 (`MAX_TOKENS`, hv4_arms.py:15-16@d1dbcd6; "Thinking is left at the caller's default (disabled); no temperature", hv4_prompts.py:180, request built at hv4_prompts.py:180-186 with `build_body(..., None, DEFAULT_THINKING)`; the per-kind value enters the request at hv4_prompts.py:267). `register_all` writes `model_ids` only (hv4_runner.py:676-686), so these settings are recorded here and go into the notebook copy of the nine lines. |
| Cells | 22 for the L, F and X arms (DESIGN Note N2): flat 0.25/0.5/1.0 for CON-L/F/X and BIG-L/F/X (18) plus as-sized for CON-L/F and BIG-L/F (4); 1.9 s fill, flat 15% and pressure-fail. **Plus 3 control books, one deciding cell each (section 6): 25 gate checks in all.** Flat 0.25 is each book's primary. |
| Registration lines | **TBD** (the nine lines verbatim from the `--register` output, with the notebook entry id and instant; the instant must be after 2026-10-19T17:57:08Z, the Am.2 merge, and before T0) |
| T0 job | A host-side MiScusi job on **mal-research-0** (not a session cron, not mal-fast-0). Job id **TBD**. One process per `frozen_sha` (Am.3 item 1): a clean checkout with HEAD = `d1dbcd6`. Command line **TBD**: feed delay 7 (`HV4_FEED_DELAY_SLOTS=7`), lane H off, `MAL_HV4_RPC=helius`. MemoryMax **TBD** (section 4). |

## 2. Pins
### 2.1 Documents: byte-exact repo copies are the cited text
The `/data` originals are mutable files (DESIGN.md was rewritten three times on 2026-10-19). The repo copies below are byte-exact (copied with `cp`, sha256 re-checked against the originals when this commit was made; no note inside the bytes). The originals are not touched here; the manager runs `chmod a-w` on them after merge, and writes a notebook entry with these four hashes and its instant before T0 (**TBD: notebook id**).

| sha256 (copy = original) | repo copy | original |
|---|---|---|
| `e3eb46273e9aea1cac6a9e7efad83336264b1265334487debc4344f47cea588c` | `ARTIFACTS/lab/hv4-registration-1021/DESIGN.md` (292 lines; includes Amendment A1, Note N1 and Note N2) | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md` |
| `af5052d5bae4efb683d745ed0fbb9aa6c680bc44a79ddfaf3cef7c3110831b18` | `ARTIFACTS/lab/hv4-registration-1021/SHAKEDOWN-CHECKS-2.md` (revision 2; supersedes revision 1) | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/SHAKEDOWN-CHECKS-2.md` |
| `727de6666cef28a8757c76b3d63912ea71efc3f1667ebcdb5c3fed7dea6a6bfb` | `ARTIFACTS/lab/hv4-registration-1021/QP-RULING-1019-startup.md` (quant-proof, 2026-10-19T14:57:04Z; carries the DESIGN and SHAKEDOWN-CHECKS-2 pins above) | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/QP-RULING-1019-startup.md` |
| `3d01f7ff6d51c22219fb116f438dac81fc6e657fdadcd961cd7bb19b96ec7205` | `ARTIFACTS/lab/hv4-registration-1021/hv4-feed-shakedown.sh` | `/data/mal/hunt-1008/ops/hv4-feed-shakedown.sh` |

If any original is edited before the clock, its hash above is stale and the change is disclosed in every read (QP stop rule: any /data pin hash change).

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
| `8f0dfaada9e77a7e2d6d76c574f70ee10d0c7af7433c96f15da8c2de32cc7325` | tools/hv4_review.py | the nightly reviewer (section 2.4) |
| `0cb4e1642d94208db84eb5669221837d858db619bd338beb248af3e547894ce9` | tools/hv4_read.py | **not part of the running process, and its "25 cells × 1 read = 25 checks" text (line 31) stays at 25** (22 + 3 controls). It must be changed in its own reviewed PR before the first read, to derive its cell list from the nine registered `bot_id`s and the declared cells, assert that the list equals this record, implement C2's with-and-without table and the D5 set (section 3), and check every start row and open row against the config pin of 2.3. Owed; not in this PR. |

Tree ids at `d1dbcd6` (from `git rev-parse d1dbcd6:<path>`): `tools/hv4_playbooks` `b39d0ad103ee0c02956251f0bcc37a21e5386d52`; `tools/hv4_templates` `e45184be38fa1b742d3aac30e672c7f557208825`.

### 2.3 DEC-031 Amendment 2 for HV4
- **Coverage.** Amendment 2 merged as squash commit `b2fef927f99c3960f28ced4e2a3aa5d2782804f1` (#740), committer time **2026-10-19T17:57:08Z** (QP). Coverage holds if the nine `bots.json` lines and their notebook copy are written after that instant (they do not exist yet) and the clock 2026-10-21T00:00Z is after it. Amendment 3 merged as `3eadf195661f` at 2026-10-19T20:06:29Z (QP).
- **Rent 0 passed explicitly (Am.2 item 2 (i); items 3, 8).** `STACK_RENT = 0` (hv4_runner.py:1619@d1dbcd6); `STACK_FIELDS` spells every `StackConfig` field but `feed_delay_slots` as a literal (hv4_runner.py:1620-1623), including `"rent": STACK_RENT`, `"k_rounding": "ceil"`, `"e1_floor_per_005": 384_022`, `"strand_rule": "both"`, `"gap_slots": 150`, `"gap_silence_ms": 60_000`; `stack_config()` (hv4_runner.py:1626-1639) takes only `feed_delay_slots`; `main()` builds the engine from it (hv4_runner.py:1697). The strand rent stays the engine default 2,039,280 (hv4_runner.py:1632-1634).
- **Full config pin (Am.2 item 8).** `StackConfig.as_dict()` at `d1dbcd6` with `feed_delay_slots` 7 (sort_keys, compact separators), re-computed by this worker with `tools.hv4_runner.stack_config(7)` and equal to QP's:
  `{"e1_floor_per_005":384022,"exit_lag_s":0.55,"feed_delay_slots":7,"guard":1.15,"h_in":0.002608,"h_out":0.0016,"k_rounding":"ceil","portal_ppm":0,"press_intercept_provisional":-1.4197242838379789,"rent":0,"send":505000,"strand_rule":"both"}`
  sha256 `1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986`. The read compares every key on every start row and open row against this pin. The feed delay 7 is from arena-records-2026-10-18.md §3; the T0 job's command line must show it (**TBD**).
- **V band (Am.2 item 2 (ii); item 6).** `V_LO_LAMPORTS = 17_500_000_000`, `V_HI_LAMPORTS = 17_700_000_000` (hv4_cards.py:63-64@d1dbcd6). In `filters`: `quote_wsol` is checked first (hv4_cards.py:419-420), then V: a WSOL pool without V fails as `v_missing` (hv4_cards.py:424), otherwise `v_band` requires `V_LO_LAMPORTS <= v <= V_HI_LAMPORTS`, both ends inclusive (hv4_cards.py:426). A non-WSOL pool fails `quote_wsol` alone and its V is not read (hv4_cards.py:409-411). Introduced by PR #749.
- **Read statement.** Rent 0 decides; the rent-charged leg is printed beside it (Am.2 items 7, 9).

### 2.4 DEC-031 §1.3 (self-improving book): the pins
§1.3 names Haiku trader v4. The frozen part is the code, the reviewer's instructions and the model IDs. Each daily playbook version is hashed and logged before the first decision that uses it, and a version may use only outcomes whose exit is before its write instant. For HV4:
- **Reviewer code and instructions.** `tools/hv4_review.py` sha256 `8f0dfaada9e77a7e2d6d76c574f70ee10d0c7af7433c96f15da8c2de32cc7325` at `d1dbcd6`; playbook tree `tools/hv4_playbooks` `b39d0ad103ee0c02956251f0bcc37a21e5386d52`; template tree `tools/hv4_templates` `e45184be38fa1b742d3aac30e672c7f557208825`. Job script `scripts/research/hv4-review-job.sh` (a scheduled MiScusi job at 00:05Z; nothing is edited by hand).
- **Same checkout.** The reviewer runs from the same clean checkout at `d1dbcd6` (Am.3 item 1). A reviewer added later at another sha is a new book.
- **Reviewer job ids** (00:05Z, one per learning arm: CON-L, BIG-L, CON-P; BST-L is left with lane H, DESIGN line 145 lists BST-L and it is not registered): **TBD, the manager schedules them and records the ids here before T0.** I found no scheduled reviewer job in the repo or the ops directory.
- **How a version is chosen.** `review_version_source` (hv4_runner.py:236@d1dbcd6); the runner loads the version in force through `refresh_versions` (hv4_runner.py:1483). A version is in force from `effective_from_ms = max(cutoff + 30 min, written_ms)` (hv4_review.py:48), so it is hashed and logged before its first use.
- **Outcome cutoff rule, with code.** The bundle holds the arm's own closed trades with `close_ms` strictly before the cutoff 00:00:00.000Z of the review day, where `close_ms` is the exit decision instant plus the slower exit fill leg (`EXIT_FILL_MS` 1,900 ms) (hv4_review.py:10-12). In code: `load_trade_records` drops `close >= cutoff_ms` (hv4_review.py:308); `before_cutoff` keeps only `close_ms < cutoff_ms` (hv4_review.py:390-392); a manifest whose `max_input_close_ms` is at or after its cutoff, or that is written before its cutoff, is refused (`ManifestRefused`, hv4_review.py:45-48).
- **Placebo shuffle seed (CON-P).** The (net_pct, exit_reason, hold_s) tuples are shuffled with a seed from `sha256(arm|date)` (DESIGN line 153; hv4_review.py:33).
- **If the reviewer is not running at T0,** L and P trade on v0 playbooks and knobs, and that is the registered behavior; a reviewer started later at a different sha is a new book.

## 3. Declarations (SHAKEDOWN-CHECKS-2 "Declarations before T0", plus the two QP adds)
1. A trade whose sell was refused or delayed is kept and counted **at the fill it actually got**; never dropped or re-priced.
2. Every read reports the trades with a refused or delayed sell, the `sell_alert` and `sell_orphan` counts, and the longest wait from the first sell decision to the landed sell.
3. `solders` is out of scope for this book unless the read tool imports `tools.h5_executor`.
4. DESIGN.md (with Amendment A1 and Notes N1, N2), SHAKEDOWN-CHECKS-2.md and `n_bots` 9 are pinned in sections 1 and 2.1.
5. **C2 membership (QP, before T0, outcome-blind).** "A trade is in a catch-up window if its entry `call_ms` or its exit `call_ms` falls inside the window. Stamps are not used."
6. **D5, guard lag (QP, before T0, outcome-blind).** "Per open row, guard_lag_slots = `effective_slot.entry` - `call_feed_slot`. Every read reports, per book, its p50/p99/max and the number of entries the guard reverted. Trades with guard_lag_slots > 25 (10,000 ms at 0.4 s/slot, check 6's max) form a set. The verdict is stated with and without that set, beside C2's catch-up set, and with and without the union of the two. Fills are kept as booked (Declaration 1)."
7. **Read disclosure: V counts.** Every read discloses the window's `v_missing` and `v_band` counts, with no decision rule attached (QP #755 item 4, last bullet; as in #749).
8. **Daily decision-log hash (DEC-031 §1.2: "Each book's decision-log sha256 goes into a MiScusi notebook entry at least once per UTC day").** The tool exists: `python -m tools.arena_score ... --daily-digest` prints `decision_log_sha256` per bot, "the stable commitment" (arena_score.py:13, :544-565). I found **no scheduled host-side job that runs it for the arena books** in `scripts/` or the docs I could cite. **TBD: manager creates it before T0** (a host-side MiScusi job, one notebook entry per UTC day naming the nine `bot_id`s; job id TBD).

## 4. Conditions from quant-proof on PR #755 (comment 6276972823) and the memory-plan rule (comment 6278428501, ruling (b))
- **Memory-plan rule, fixed now before the stub is read.** Compute per process segment: a restart resets RSS, so never fit a slope across it.
  - `s` = the least-squares RSS slope over stub hours 2..24, outside each catch-up window, per segment. `s_max` = the larger of the two segments' slopes. `R0` = the larger post-drain RSS. `H` = the hours from T0 to the latest planned read instant (declared here: **TBD**).
  - **(A)** if the research-0 job can get MemoryMax `M` >= 1.5 × (`R0` + `s_max` × `H`) next to the other planned jobs (J3's 64 GB, Am.3 (j)). **Otherwise (B)**, at a cadence of at most min(24 h, (`M` − `R0`) / (2 × `s_max`)).
  - The linear projection is **not accepted** if either segment's second-half slope is more than 1.5 × its first-half slope; then (B) at 24 h is mandatory.
  - An OOM fails the stub.
  - Plain arithmetic at 154 MB/h (not a measurement): H = 168 h needs about 41 GB; H = 336 h needs about 80 GB. QP prefers (A), and Am.3 (j) strengthens that.
  - **Chosen plan: TBD. `s` per segment, `s_max`, `R0`, `H`, `M`, projected hours to MemoryMax: TBD.**
  - **(B) cost, stated.** Each restart books every recovered position as an excluded `engine_restart` row, and a book above 5% excluded is NOT_DECIDABLE; the excluded share is about (restarts × open positions per book at each restart) / (book trades). At the gate's 100 trades with 7 restarts a book can afford 5 recovered positions in total. Also Am.3 item 8 (j) (as QP cites it): restarts cause feed gaps, which make every open position in that process NOT_DECIDABLE; R arms mirror L positions, so one restart excludes both. Each restart also opens a catch-up window (section 5). The stub cannot measure open positions per restart for the LLM arms (StubModel abstains).
  - Either way: restarts are host-side MiScusi jobs (CLAUDE.md), not session crons; restart job ids for (B) **TBD**, or "none planned" for (A); every restart is recorded in LAB_STATE; an unplanned restart or OOM is disclosed as unplanned; an orphaned recovered position makes the read NOT_DECIDABLE (QP-RULING-1019-startup point 5).
- **`trim_ms` block.** If any stub line of `engine-trim-counters.jsonl` has `trim_ms` > 1,000, HV4 does not register until QP re-rules.
- **`v_missing` evaluation (declared by QP before the stub).** On every line of `engine-trim-counters.jsonl`, per `engine_id` segment, the ratio `v_missing_pools / v_checked_pools`. If it is above 0.5 on every line of the stub, HV4 does not register until the cause is explained. The report carries the counters per segment and never adds distinct pools across a restart; it must not say "44% of the universe is lost to V".
- **NOT_DECIDABLE rule, pre-registered before T0 (QP on #745 item 4 (3)).** If any line of `engine-trim-counters.jsonl` has `rows_below_trim_floor` > 0 or `refused_history_trimmed` > 0, equivalence with an untrimmed engine is void for every book on that engine for that segment, and the read is NOT_DECIDABLE. The same condition fails the stub's trim check. (arena_paper.py:99-102@d1dbcd6 states the same void condition.) This also covers below-floor rows and `history_trimmed` refusals before T0.
- **Pins as path:line@`d1dbcd6`.** `RunnerConfig.bound_memory = True` (hv4_runner.py:508) and `trim_engine_history = True` (hv4_runner.py:512; no CLI switch); `HOUSEKEEP_EVERY_MS = 5 * MIN_MS`, 300,000 ms (hv4_runner.py:77); `TRIM_KEEP_SLOTS = 5_000` (arena_paper.py:104); `ENGINE_TRIM_SCHEMA = "hv4_engine_trim_v1"` (hv4_runner.py:326); no `prune()` call (the text `.prune(` appears only in comments, hv4_runner.py:510 and :619).
- **Trim-pause disclosure, in every read.** "Engine trim every 5 tape minutes on the row thread; measured trim_ms p50/p99/max [TBD, labelled as stub figures]; a decision written in a trim tick is priced up to that much later." Check 6 stays as written; trim minutes are not excluded.
- **Evidence scope (QP).** The md5 equivalence of the trim was shown on 9 h with code-rule arms only (builder) and on a 2 h decision log with an empty ledger (QP's own run, which did not compare fills). No LLM-arm fill path is shown. The residual memory grower (about 150 MB/h) is unidentified. #1236 is not fixed by #755.

## 5. Start-up catch-up (QP-RULING-1019-startup.md, ruling (b), C1 to C3)
### C1: VERIFIED at `d1dbcd6` (by code reading and by tests; QP read it at 77bdf7e and left it UNVERIFIED; QP re-verified it at 2d2284b, comment 6278428501)
**Claim.** A buy or sell called while the apply hook lags the wall clock lands at or after the slot of the wall-clock call instant, not at the slot of the last feed row the engine has seen.

**Tolerance (QP).** The landing is at or after the slot of the wall-clock call instant up to the error of the slot-clock fit: |error| <= ceil(lag_slots × |relative sps error|) + 1, the bound used in the test. At a 130 s lag, a 1% sps error is about 3 slots, in either direction.

Trace (every line `@d1dbcd6`):
1. **Who calls, with what.** The state is drained by `Runner.poll_to(T)` = `state.poll(until_ms=T - 1)` (hv4_runner.py:661-662), called first in `step(T)` (hv4_runner.py:1562), before `exits` (1568). Each row goes through the wrapped `state.apply` (hv4_runner.py:581-587) into `Runner._on_row` (593-598) and `engine.on_row`. `Runner.slot`, the `decision_slot` the runner passes, is the highest trade slot applied so far (hv4_runner.py:595-597), so it lags in a drain. A buy is `engine.buy(bot, card["id"], size, dec_ms, self.slot, ...)` (hv4_runner.py:1045); a code exit is `dec = T` (1257, the logical tick, behind the wall clock in a catch-up) and `engine.sell(bot_id(pos.arm), pos.pid, 1.0, dec, self.slot, ...)` (1273); an LLM answer stamps `dec_ms = now_fn()` in live mode (`_decision_ms`, 658-659). `main()` builds the engine with no `now_fn` and no `sps_fn` (hv4_runner.py:1697), so the engine's clock is the wall clock (arena_paper.py:850) and its sps comes from the tape's block times (`SlotClock`, arena_paper.py:1047-1049; h5_shadow.py:406-449). The runner overrides the engine's clock only in sim mode (hv4_runner.py:578-579).
2. **The write instant ignores the feed lag.** `_write_instant` takes `call = int(self.now_fn())` and returns `max(decision_ms, call, hw_ms)` (arena_paper.py:1062-1066). In a catch-up `hw_ms` (the newest `t_recv_ms` seen, set in `on_row`, arena_paper.py:950 and :973-976) is about 130 s old, so the instant is the wall-clock call time. A stale `decision_ms` is priced at the write instant, never refused (docstring arena_paper.py:22-23, 1082-1083); only a future stamp is refused (1068-1072).
3. **The instant is mapped to a slot by elapsed wall time.** `slot_at(ms, sps) = hw_ms_slot + floor((ms - hw_ms) / (sps * 1000))` (arena_paper.py:1051-1054). `ms - hw_ms` is the lag, so the extrapolation adds the lag's worth of slots to the last row's slot.
4. **Buy.** `sd = max(int(decision_slot), self.slot_at(instant, sps))` (arena_paper.py:1127) with `instant` from line 1087. `decision_slot` can only push the landing later (docstring 1335-1337). So `sd >= slot_at(wall-clock call instant)` by construction.
5. **Sell.** `call, instant = self._write_instant(decision_ms)` (1178); `xs = max(int(decision_slot), self.slot_at(instant, sps))` (1183); a sell with `xs < sd + k_slow + 1` is refused (1185-1186).
6. **Landing.** `sdc, xsc = pos.sd + d, pos.xs + d` (arena_paper.py:1351); entry legs land at `sdc + k` for the 1.3 s and 1.9 s legs (1361-1362; `_needed_slot` 1206-1210). The PumpSwap entry state is the first print with slot >= landing + 1 (`mint_states`, arena_stack.py:138, 141; bonding: the last print at or before the landing slot, arena_paper.py:781-788).
7. **No pricing from the lagging feed.** `sell` appends the position to `pending` and calls `_try_pending` (1202-1203). A position is priced only when `h.last_slot >= need` or `hw_slot >= need + grace_slots` (10,000 slots) (arena_paper.py:1273-1278). In a 130 s catch-up the tape the engine has seen is about 325 slots short of `need`, so the sell waits for the drained tape to reach the landing slot, and is then priced from prints at or after it (state_src `tape`).

Tests: `tools/test_hv4_catchup_landing.py` (new; no runner, model or pinned file edited). On synthetic tapes, with the engine 130 s behind its clock:
- engine level, one print per slot, exact: `sd` = the wall-clock slot, 325 slots after the last row seen; `xs` likewise; the sell stays pending; after the drain the ledger's landing slot is `sd + d + k`, `Qe` is the print at landing + 1 and `Qx` the print after the exit landing; both the stamp = wall clock and the stamp = stale tick are run;
- a lagging engine and a current engine give identical `sd`, `xs`, landing slots, `Qe`, `Be`, `Qx`, `Bx`;
- `decision_ms` and `decision_slot` cannot pull the landing earlier; a future stamp is still refused;
- with the tape-derived `SlotClock` that `main()` uses: fitted sps 0.3999993 s/slot on a 0.4 s/slot tape, `sd` equals the true wall-clock slot, 325 slots after the last row; the general error is bounded as in the tolerance sentence above;
- runner level: the real `Runner._open` and `Runner._sell` in live mode, with a drain through `ArenaState.poll` and the wrapped hook, 130 s behind a shared fake wall clock; the fill's `Qe`, `Be`, `Qx`, `Bx` equal the tape prints after the landing slots, and differ from the print the lagging feed showed at the call.

Mutation checks (not committed): replacing the write instant by the last feed row's time, or `slot_at` by the last row's slot, makes 8 of the 9 tests fail (the 9th is the future-stamp test). QP reproduced both at 2d2284b with its own injected plugin: 8 of 9 each.

**What C1 does not claim.** (a) It says nothing about what the bot SAW: decisions in a catch-up window are made on a state behind the wall clock; C2 is the control for that. (b) The guard reference is not covered by C1 and is not only a catch-up effect; it is declared as D5 (section 3, item 6). The 15% guard compares the landing price with the price at `call_feed_slot`, the engine's latest feed slot at the call (`_ref_slot`, arena_paper.py:1332-1341, used at :1356-1358; `guard` 1.15). In a catch-up that is the lagging slot, 325 slots before the landing in the test (pinned by `test_observation_not_c1_the_guard_reference_is_the_lagging_feed_slot`). Per QP, hv4_runner has one thread and polls only in `step()`, so the feed is frozen while a scan waits on Haiku: every LLM-arm entry's reference is stale by the scan's wall time up to that buy. StubModel cannot show this; run mode will. A live executor that quotes fresh at build time would use a newer reference; an executor on this same frozen thread would use the same one. The sign of the bias is unknown: a stale reference reverts more buys after a run-up and fewer after a drop. C2 covers the catch-up part only if window membership is taken from the wall-clock call instant (section 3, item 5); it does not cover the staleness inside a scan. (c) The slot mapping is a lower bound of the chain slot (feed delay `d`, arena_paper.py:25, 1052); `d` is added at the landing. (d) The extrapolation error scales with the lag; the stub measured about 130 s at the first minute, and the T0 catch-up is to be measured, not assumed (C3).

### C2 (read rule, declared now)
The read lists every catch-up window: the T0 start and each restart, by QP-RULING-1019-startup (a) (from the start row to the end of the first full minute with `tick_timing.exit_check_delay_ms` p99 <= 2,000 and max <= 10,000, capped at 10 min; a catch-up longer than 10 min fails check 6). Membership is by the wall-clock `call_ms` of the entry or the exit (section 3, item 5). Per book it reports the trades whose entry or exit decision falls inside a window. Their fills are kept as booked (Declaration 1) and the read states its verdict both with and without them. Recovered positions are booked `engine_restart` whenever their sell fires and count toward the 5% excluded share.

### C3 (T0 job)
The T0 job starts only as early as needed before 2026-10-21T00:00Z (the warm-up is 365 min of tape, `WARM_MIN_DEFAULT`, arena_state.py:66, fixed when the job builds its `ArenaState`; an earlier build makes the first drain longer). Declared target start: within 5 minutes before T0 (**TBD: the actual start instant**). The T0 catch-up is reported like a restart's and is **measured in the forward run, not assumed**; the stub's two windows go in section 7. Run-mode caveat: the stub has no Haiku calls inside scans, so the stub's catch-up time is not the T0 catch-up time.

## 6. DEC-031 Am.3 multiplicity block for this book
- **N: the nine `bot_id`s count as nine books in N** (QP ruling (a): §1.2 defines a book as `bot_id` + `frozen_sha` + `clock_start_ms`, and Am.3 item 3 makes N every book with a clock before the read; there is no discretion). HV4 alone puts up to 1 − 0.95^9 = 37.0% (if independent) on at least one screen pass. The total N at the read is **TBD** (other books with a clock before the read, stopped and NOT_DECIDABLE ones included, plus the verdicts of books already read).
- **Cells declared: 25** = 22 (the L, F and X arms, Note N2) + 3 control books. The control books are **CON-P, CON-R and BIG-R**: each is a registered `bot_id` with `deciding_stake` 0.25 and so one book with one gate read (§1.2, §2). Their deciding cell is declared here: flat 0.25, 1.9 s fill, under both the flat 15% and the pressure-fail model. HV4 then has **25 gate checks**, nine of them deciding (one per `bot_id`). Plain arithmetic, not a measurement: 1 − 0.95^25 = 72.3% if the checks were independent (they are correlated).
- **A control's pass is confirmed automatically.** A control's screen pass enters confirmation automatically and counts in c (Am.3 item 2: nobody chooses). It is reported as a placebo alarm beside the L arms.
- **Sibling taint (Am.3 item 8 (e)).** L, F and P of one persona are near-identical, and R mirrors L. The best HV4 arm at the read was selected among nine siblings and is in-sample for that choice. Winner's curse applies to any single-arm pass.
- **October hours.** The X-arm code rules were priced on 2026-10-19 arena tape in engineering replays and stubs before `clock_start`: the builder's 9 h replay (T08..T16, 105 trade rows per QP #755), QP's T17..T18 replay (0 ledger rows), stub #1236 and the `d1dbcd6` stub. Outcomes were never opened (md5 and counts only). None of these hours counts for any HV4 book. HV4 is not a hunt23 book, so Am.3 item 1's P0 count does not apply, and it is not screened on October hours under Am.3 item 4.
- **Per book:** the agent (Haiku v4, model id **TBD**); DECLARE sha256 and notebook id **TBD**; cells declared 25 (above), priced **TBD**, killed **TBD**, surviving **TBD**; blocks: the arena tape from 2026-10-21T00:00Z.
- **Cells in total** across hunt23, J3, cohort 2 and the arena: **TBD at read time.**
- **p** (one-sided, 10,000 draws, seed 1, the larger of the two fail models): **TBD at the read.**
- **The plain line:** if no book has an edge, the chance of at least one screen pass is up to 1 − 0.95^N: **TBD** for the total N (40% at N = 10, 79% at N = 30, per Am.3 item 3).
- **Screen-read line:** p and Holm rank among all arena reads, report-only: **TBD at the read.**

## 7. STUB RESULT (to be filled from the shakedown report; nothing below is measured yet)
**Stub identity.** Sha `d1dbcd64fc434590275d34a3b65d6ad5d45d86ce`; root `/data/mal/arena/_shakedown-hv4-1020`; started 2026-10-19T21:31:27Z (QP), ends 2026-10-20T21:32Z. Job ids, as given by the manager (not re-verified by this worker): the stub is job #1249 (`j_OwuCRwgjWv1jdQ`); the deliberate restart, about 2026-10-20T09:31Z, is job #1250 (`j_hYaLpOJpmOwiqQ`), a host-side MiScusi job and not a session cron (QP #755 item 3); the sampler is job #1252 (`j_eXIlM3XYGuqBXg`).
**Deviation from SHAKEDOWN-CHECKS-2.** That file names the root `_shakedown-hv4-1019b` and job #1218. The `d1dbcd6` stub is a new sha, so it is a new 24 h stub, as the file's own last line requires ("A failed check means fix, new sha, new 24 h stub"). It started before 2026-10-20T00:00Z, so T0 stays 2026-10-21T00:00Z.

Counts and timings only; the stub's engine ledger is never opened. StubModel trades are not evidence for any book.

| # | Check (SHAKEDOWN-CHECKS-2) | Result |
|---|---|---|
| 1 | 0 `as_of_refusal` rows and 0 LeakageError | TBD |
| 2 | `chain_stale` <= 2% of hot-pool cards, CON and BIG separately | TBD |
| 3 | every `chain_unavailable` card's pool first seen within 10 min before the scan | TBD |
| 4 | null-holders cards <= 10%, CON and BIG separately | TBD |
| 5 | chain calls <= 1,500 per rolling hour and <= 40,000 per UTC day; the 00:00Z-crossing hour and the restart-crossing hour named; `spend_state_reset`, `spend_write_errors`, `state_write_errors` each expected 0; Helius credits reported | TBD |
| 6 | timing: scan wall p99 <= 2,000 and max <= 5,000 ms; exit-check delay p99 <= 2,000 and max <= 10,000 ms outside each process start's window (QP-RULING-1019-startup (a); initial start and deliberate restart; excluded minutes listed with raw n_ticks, p50, p99, max); trim minutes included | TBD |
| 7 | `sell_alert` = 0, `sell_orphan` = 0 (or explained); "already has a sell" drops = 0. X-arm entry and exit counts given; **if the X arms made 0 entries in 24 h, check 7 is vacuous and the stub exercised no buy or sell: the report says so** (QP 3.13; a disclosure, not a new pass check) | TBD |
| 8 | after the deliberate restart: `lane_h` false, same `frozen_sha`; decision-log chain verifies; no duplicate rows; chain-credits.json and chain-prefetch-spend.json reload with no reset | TBD |
| 9 | feed #1207 writes throughout; data_loss gaps reported; disk reserve not paused | TBD |
| 10 | registration printed `{"lane_h": false, "n_bots": 9}`; no BST log, book or bot_id anywhere in the shakedown files | TBD |

Reported per persona: scans, cards, setups by type, chain_unavailable (and how many carried a setup tag), chain_stale, null-holders cards, stub LLM calls, X-arm entries and exits: **TBD**.

Items QP asked the stub report to carry on #755:
| Item | Result |
|---|---|
| trim-file line count (about 288 per 24 h plus about 73 per start), monotone per `engine_id`; number of `engine_id`s (= process starts; 2 expected) | TBD |
| final `hist_trimmed_prints`, `rows_below_trim_floor`, `refused_history_trimmed`, `rows_dup` | TBD |
| `trim_ms` p50/p99/max per hour against `pools` and `prints_held`; any `trim_ms` > 1,000 blocks registration | TBD |
| `hook_errors` prefixed `housekeep:` (expected 0) | TBD |
| hourly RSS per segment; RSS after each drain; `s` per segment and `s_max`; second-half/first-half slope check; projected hours to 10G (section 4) | TBD |
| `filter_v_missing`, `filter_v_band`, `v_checked_pools`, `v_missing_pools` per `engine_id` segment; ratio `v_missing_pools / v_checked_pools` on every line (> 0.5 on every line blocks registration) | TBD |
| v_missing share of newly checked pools, per hour | TBD, or "not measured" |
| cause statement: how many v_missing pools never show V in any tape row of the window, how many gain V later (counts from the tape) | TBD, or "not measured" |
| engine-side counters `v_missing` and `v_out_of_band` beside the card-side counts | TBD |
| memory plan (A) or (B) with numbers (section 4) | TBD |
| stub catch-up window of the initial start and of the deliberate restart (QP-RULING-1019-startup sentence for the report) | TBD |

## 8. What is not covered
- C1 is a statement about the landing slot. It is not a statement that catch-up decisions are free of bias (C2 reports them with and without) or that the guard reference is fresh (D5 reports it).
- The stub measures no run-mode scan time with Haiku calls and no LLM-arm fill path.
- Nothing here is an edge claim. `quant-proof` reviews any later sentence that says a book made money.

## Appendix A. Final-pass checklist

Verbatim from quant-proof, PR #762 comment 6278428501, section 4 (the heading line below is QP's own). The FINAL pass fills this in; nothing in it is filled yet.

## 4. Checklist for the FINAL pass
Timing: the stub ends 2026-10-20T21:32Z. The `bots.json` lines and the notebook copy go in before 2026-10-21T00:00:00Z.

**A. Head and pins**
- Re-hash the head. The diff since 2d2284b only fills TBDs and items 2-3 above.
- The pinned code has an empty diff against d1dbcd6.
- The /data pins and the repo copies all equal §2.1.

**B. Stub report fields** (from decision logs, cards, status and counter files only, never a ledger). Each line is a pass condition.
1. 0 as_of_refusal rows and 0 LeakageError.
2. chain_stale <= 2% of hot-pool cards, CON and BIG separately, as numerator/denominator.
3. Every chain_unavailable card's pool was first seen <= 10 min before its scan (cards checked / cards passing).
4. null-holders cards <= 10%, CON and BIG separately.
5. Chain calls:
   - max rolling hour <= 1,500, both processes counted;
   - the 00:00Z-crossing hour and the restart-crossing hour named, with their values;
   - <= 40,000 per UTC day;
   - spend_state_reset, spend_write_errors and state_write_errors each 0;
   - Helius credits as a number.
6. Timing:
   - scan_wall_ms p99 <= 2,000 and max <= 5,000;
   - exit_check_delay_ms p99 <= 2,000 and max <= 10,000 outside the two start windows, trim minutes included;
   - each window defined by rule (a), each <= 10 min, every excluded minute listed with raw n_ticks/p50/p99/max;
   - the QP-RULING (a) sentence with every [fill in] filled.
7. sell_alert = 0, sell_orphan = 0, "already has a sell" drops = 0. X-arm entry and exit counts given; if 0, the report says check 7 is vacuous.
8. The post-restart start row has lane_h false and frozen_sha `d1dbcd64fc434590275d34a3b65d6ad5d45d86ce`. The chain verifies, there are 0 duplicate rows, and both json counters reload with no reset. The restart job id is given, and the job is host-side.
9. Feed #1207 wrote throughout; data_loss gaps listed with durations; the disk reserve never paused.
10. The registration printed `{"lane_h": false, "n_bots": 9}`; 0 BST files and 0 BST bot_ids.

Trim:
- engine_id count = 2 (a third is an unplanned start and is disclosed);
- about 434 lines (288 + 2 x 73), monotone per engine_id;
- `rows_below_trim_floor` = 0 and `refused_history_trimmed` = 0 on every line;
- final hist_trimmed_prints and rows_dup;
- trim_ms p50/p99/max per hour, against pools and prints_held; **max trim_ms <= 1,000 on every line**;
- `housekeep:` hook_errors = 0.

V, per engine_id segment:
- the ratio v_missing_pools / v_checked_pools on every line (registration is blocked only if it is > 0.5 on every line);
- filter_v_missing, filter_v_band, and the engine's v_missing and v_out_of_band;
- the per-hour share and the cause statement, or "not measured";
- no "lost to V" sentence.

Memory:
- hourly RSS per segment, RSS after each drain;
- s per segment and s_max by rule 3(b), with the second-half/first-half slope check;
- projected hours to 10G;
- no OOM.

**C. TBDs and where each value comes from**
- §1 model_ids: from the `--register` output. One exact id on CON-L, CON-F, CON-P, CON-R, BIG-L, BIG-F and BIG-R; `[]` on CON-X and BIG-X.
- §1 registration lines: the nine lines verbatim. Each has frozen_sha d1dbcd64…, clock_start_ms 1792540800000 and deciding_stake 0.25. Add the notebook entry id and its instant, which must be after 2026-10-19T17:57:08Z and before T0.
- §1 T0 job:
  - job id; host mal-research-0;
  - a clean checkout with HEAD = d1dbcd6;
  - a command line with feed delay 7, lane H off and MAL_HV4_RPC=helius;
  - the MemoryMax.
- §2.1: the repo copy paths.
- §2.3: the Am.2 merge (3.7) and the full StackConfig pin (3.8).
- §3: add the C2 membership sentence and D5 (item 2).
- §4: the chosen plan with s per segment, s_max, R0, H, M and projected hours; restart job ids for (B), or "none planned" for (A); the stub's trim_ms p50/p99/max in the disclosure, labelled as stub figures.
- §5 C3: a declared target start (within 5 min before T0). The T0 catch-up itself is "to be measured in the forward run"; the stub's two windows go in §7.
- §6:
  - DECLARE sha256 and notebook id;
  - cells declared 25 (22 + 3 controls);
  - the sibling line (3.5) and the October-hours line (3.6);
  - N, p, the plain line and the Holm rank stay TBD until the read. That is correct.
- §7: every row PASS or FAIL, with its number.
- 3.2 reviewer pins and job ids; 3.3 sampling settings; 3.10 the daily sha job id.

**D. Stop rules.** HV4 does not register at d1dbcd6 if any of these happens; T0 then moves (SHAKEDOWN-CHECKS-2, last line):
- any pass check fails;
- any trim_ms > 1,000;
- the v_missing ratio is > 0.5 on every line;
- rows_below_trim_floor or refused_history_trimmed > 0;
- an OOM;
- an unplanned engine_id;
- any /data pin hash changes.

