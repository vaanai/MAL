# HV4 registration records, DRAFT for T0 2026-10-23T00:00:00Z at frozen sha 9ed4c5f (DEC-031 §1.2, §1.3, Amendments 2 and 3; written before any HV4 book has a clock)

Retargeted on 2026-10-21 at about 17:40Z (`date -u`) from the draft of PR #762 (written 2026-10-19 for the older build `d1dbcd6` and T0 2026-10-21; its review is quant-proof's pre-review, #762 comment 6278428501, called "QP #762" below). This file supersedes `ARTIFACTS/lab/hv4-registration-1021.md`. Nothing here is registered yet. No `bots.json` line exists for these nine books in the arena registry; the manager prints it with `--register` (hv4_runner.py:1870-1878@9ed4c5f) and copies it into the notebook before the clock (§1.2). The only `bots.json` lines that exist for the current build are the shakedown's own, in `/data/mal/arena/_shakedown-hv4-v2-1020/bots.json` (a separate registry for a StubModel run; section 7). No arena ledger, shakedown ledger, `bots.json` body, `runner-status.json`, tape or outcome was opened to write this; of the shakedown and smoke directories only `stat` times and `ls` names were read. Every number comes from code at `9ed4c5ff63935696b2a89150e13565e123047754`, a pinned document, a quant-proof comment, or the manager's job list named below. Where a value depends on the shakedown report, on the registration output or on other books at read time, the text says **TBD** (the shakedown report is run by the manager after 2026-10-22T18:05Z and pasted into section 7).

What changed since the #762 draft, in one place: (1) the frozen sha is now `9ed4c5f` (main at the merge of #774), not `d1dbcd6`; the `d1dbcd6` stub FAILED shakedown check 6 and was cancelled, and the v2 build (hot-first chain prefetch with Note N4 holders, observability rows, scan speed-up, the `--register` fix) replaced it; (2) the pass checks are SHAKEDOWN-CHECKS-3 and -4 with their notes, not -2; DESIGN.md carries Note N4; (3) T0 is 2026-10-23T00:00:00Z by the rule, not 2026-10-21; (4) quant-proof's conditions on #774 (comment 6289565427) are met in section 7; (5) every `path:line` is re-cited at `9ed4c5f`.

**No book has cleared the gate. This is a registration, not a result.** Nothing here says any HV4 book makes money.

## 1. The book
| Field | Value |
|---|---|
| Book | Haiku trader v4 (HV4), **lane G only** (Design Note N2) |
| Host | **mal-research-0** (DESIGN Note N2 "the research-0 runner"; the stub root and the trader's Anthropic credential file are on it). An earlier draft said mal-fast-0; that was wrong. |
| `n_bots` | **9**, `lane_h` **false** (registration must print `{"lane_h": false, "n_bots": 9}`; SHAKEDOWN-CHECKS-2 check 10). QP confirms lane H off and these 9 lane-G bot_ids (comment 6278428501, ruling (d)); QP does **not** confirm "22 checks" (section 6). |
| `bot_id`s | `bot_id(arm) = f"hv4-{arm}"` (hv4_runner.py:103-104@9ed4c5f): `hv4-CON-L`, `hv4-CON-F`, `hv4-CON-P`, `hv4-CON-X`, `hv4-CON-R`, `hv4-BIG-L`, `hv4-BIG-F`, `hv4-BIG-X`, `hv4-BIG-R`. BST-L/F/X/R are not registered and get no read (Note N2). |
| `frozen_sha` | `9ed4c5ff63935696b2a89150e13565e123047754` (origin/main, the merge of PR #774; SHAKEDOWN-CHECKS-4-NOTES-1 R-1 makes it `HV4_FROZEN_SHA`). It descends from #755 (`d1dbcd6`, the build of the #762 draft), #767, #771 (HV4 v2) and #770 (report tool revisions 3 and 4). Stub commands: `/data/mal/hunt-1008/ops/HV4-V2-STUB-COMMANDS-1020b.md`. |
| `clock_start_ms` | `1792713600000` = **2026-10-23T00:00:00Z** (checked with `date -u -d @1792713600`). T0 rule (SHAKEDOWN-CHECKS-3, Run): the first 00:00Z or 12:00Z at least 24 h after the stub's first start row, and not before 2026-10-21T00:00Z. The first start row of the current stub is at `clock_start_ms` 1792604118000 = 2026-10-21T17:35:18Z (registry written 17:33:24Z), so 24 h later is 2026-10-22T17:35:18Z and the first 00:00Z/12:00Z after it is 2026-10-23T00:00:00Z. If the stub is not a PASS, HV4 does not register at `9ed4c5f` and T0 moves (CHECKS-3, last line of Pass checks). Fallback if the registration is not complete before that instant: the first 00:00Z after the registration record (DEC-031 Am.3 item 1, "Clock"; never earlier); for 2026-10-24T00:00:00Z the value is `1792800000000`. |
| `deciding_stake` | 0.25 for every arm (`register_arms`, hv4_runner.py:122@9ed4c5f; `Runner.register_all` and `main --register` both come through it) |
| `model_ids` | From the `--register` output (QP): one exact id on CON-L, CON-F, CON-P, CON-R, BIG-L, BIG-F and BIG-R; `[]` on CON-X and BIG-X (`register_arms`, hv4_runner.py:121). Id **TBD** (DESIGN §6 names `claude-haiku-5-5` only). If the printed id is an alias, the model id the API returns on the first live reply is recorded here too: **TBD**. |
| Sampling settings (§1.2: "the exact model IDs and sampling settings") | Thinking **disabled**; **no temperature sent** (the API default). `max_tokens`: entry 400, risk 400, recheck 120, review 4000 (`MAX_TOKENS`, hv4_arms.py:15-16@9ed4c5f, a file unchanged since `d1dbcd6`; "Thinking is left at the caller's default (disabled); no temperature", hv4_prompts.py:180, request built at hv4_prompts.py:180-186 (unchanged since `d1dbcd6`) with `build_body(..., None, DEFAULT_THINKING)`; the per-kind value enters the request at hv4_prompts.py:267). `register_arms` writes `model_ids` only (hv4_runner.py:112-123), so these settings are recorded here and go into the notebook copy of the nine lines. |
| Cells | 22 for the L, F and X arms (DESIGN Note N2): flat 0.25/0.5/1.0 for CON-L/F/X and BIG-L/F/X (18) plus as-sized for CON-L/F and BIG-L/F (4); 1.9 s fill, flat 15% and pressure-fail. **Plus 3 control books, one deciding cell each (section 6): 25 gate checks in all.** Flat 0.25 is each book's primary. |
| Registration lines | **TBD** (the nine lines verbatim from the `--register` output, with the notebook entry id and instant; the instant must be after 2026-10-19T17:57:08Z, the Am.2 merge, and before T0) |
| T0 job | A host-side MiScusi job on **mal-research-0** (not a session cron, not mal-fast-0). Job id **TBD**. One process per `frozen_sha` (Am.3 item 1): a clean checkout with HEAD = `9ed4c5ff63935696b2a89150e13565e123047754` (`git_ref` that sha; the job checks `head=` as the shakedown jobs do). Command line **TBD**: feed delay 7 (`HV4_FEED_DELAY_SLOTS=7`), lane H off, `MAL_HV4_RPC=helius`. MemoryMax **TBD** (section 4). |

## 2. Pins
### 2.1 Documents: byte-exact repo copies are the cited text
The `/data` originals are mutable files (DESIGN.md was rewritten on 2026-10-19 and again on 2026-10-20 for Note N4; its `.bak-*` copies are beside it). The repo copies below are byte-exact (copied with `cp`; sha256 re-computed with `sha256sum` on the `/data` original and on the repo copy when this commit was made, 2026-10-21 about 17:50Z, all equal; no note inside the bytes). The originals are not touched here; the manager runs `chmod a-w` on them after merge and writes a notebook entry with these hashes and its instant before T0 (**TBD: notebook id and instant**). The directory is `ARTIFACTS/lab/hv4-registration-1023/`.

| sha256 (copy = original), full hex | repo copy | original | role |
|---|---|---|---|
| `bcd26affadf1c9900852ee9f2f3489b46c418b5ee393f75d154d965ee6e176d8` | `DESIGN.md` (with Amendment A1 and Notes N1, N2, N3, N4) | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md` | the design; Note N4 sets the v2 chain rules and the CAP 2,000 |
| `55fbba0559327c1e18728596506de11fdf0408ae9bedcee49a21966d9314a3af` | `SHAKEDOWN-CHECKS-3.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-3.md` | pass checks 1-11, Rule M, run and T0 rule (revision 3; supersedes -2) |
| `0f093e68140f021f53979daa7b6251f6b2561b0194e46f3576e77e44fbf1426c` | `SHAKEDOWN-CHECKS-3-NOTES-1.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-3-NOTES-1.md` | Rule M guards N-2, check 5 counters N-3, N-5, F2, F3 (the corrected version of 2026-10-20T00:57:18Z; the superseded first version `be6df321...` is not cited) |
| `a9b019ca9c8db38e4172a04ceb338c6edc03ef17333bc0ae3d462079bc7dd583` | `SHAKEDOWN-CHECKS-4.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-4.md` | revision 4: DESIGN pin with N4, check 5 limits 2,000 an hour and 48,000 a day, new check 12 |
| `3e8032b5b431293e31ea7e0969e48ff3645daf29cddf2e928d3e8b3a08ceebde` | `SHAKEDOWN-CHECKS-4-NOTES-1.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-4-NOTES-1.md` | R-1 run sha `9ed4c5f`; R-3 proof #1278; R-5 fresh directory; R-6 rule M applies; R-7 conditions |
| `727de6666cef28a8757c76b3d63912ea71efc3f1667ebcdb5c3fed7dea6a6bfb` | `QP-RULING-1019-startup.md` (quant-proof, 2026-10-19T14:57:04Z) | `.../haiku-v4/QP-RULING-1019-startup.md` | the start-up catch-up rule (a) and C1-C3 (section 5). It carries the older DESIGN and CHECKS-2 pins; those are superseded by the rows above |
| `af5052d5bae4efb683d745ed0fbb9aa6c680bc44a79ddfaf3cef7c3110831b18` | `SHAKEDOWN-CHECKS-2.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-2.md` | **superseded** by CHECKS-3; kept because QP-RULING-1019-startup and the d1dbcd6 stub cite it |
| `3d01f7ff6d51c22219fb116f438dac81fc6e657fdadcd961cd7bb19b96ec7205` | `hv4-feed-shakedown.sh` | `/data/mal/hunt-1008/ops/hv4-feed-shakedown.sh` | the stub script (its default `SK` is an old root; the stub job sets `HV4_SK` to a fresh path, QP #774 Q4.2) |

The `DESIGN.md` copy of the #762 draft (sha256 `e3eb4627...`, before Notes N3 and N4) is removed from this branch: it is not the design of this build.

If any original is edited before the clock, its hash above is stale and the change is disclosed in every read (QP stop rule: any /data pin hash change). Report tool: `tools/hv4_shakedown_report.py` sha256 `f5e62ab57a72ebed0620b36a084893b10030350949204bd20624a78299b438ae` at `9ed4c5ff63935696b2a89150e13565e123047754` (re-computed with `sha256sum`; equals the pin in CHECKS-4-NOTES-1 R-2, QP #774 Q4.5). Its blob id at `9ed4c5f` is `15769a9`.

### 2.2 Code at `9ed4c5f` (sha256 re-computed with `sha256sum` on the checkout at `9ed4c5f`)
Files that did not change since `d1dbcd6` keep the same blob id (`git rev-parse d1dbcd6:<path>` equals `9ed4c5f:<path>`) and the same sha256 as in the #762 draft. Files that changed are marked.

| sha256 | file | note |
|---|---|---|
| `9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514` | tools/arena_paper.py | unchanged since `d1dbcd6`. **Differs from the 2026-10-18 pin** (`9a23f2aa…`, arena-records-2026-10-18.md §1) by PR #745 (`trim_history()`, opt-in). Disclosed in every HV4 read (DEC-031 §1.4); QP ruled on #745 and #755. |
| `a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778` | tools/arena_stack.py | unchanged; equals the 10-18 pin |
| `8af345618e51ca480ee7538717aa001b4ad7ed5ef38941faaec812bbe217f639` | tools/arena_state.py | unchanged; equals the 10-18 pin |
| `7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f` | tools/arena_feed.py | unchanged; equals the 10-18 pin |
| `a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34` | tools/arena_score.py | unchanged; equals the 10-18 pin |
| `048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af` | tools/arena_rules.py | unchanged (CHECKS-4-NOTES-1 R-2 lists it as byte-identical to `b240191`) |
| `5649359077cf92020be613da8fc644165cad6bdc32e42f82fbb876f193b9574a` | tools/hv4_runner.py | **changed since `d1dbcd6`** (v2 observability rows, prefetch policy v2 wiring, scan speed-up, `register_arms`/`arm_names_for`, the `--register` branch of #774) |
| `fb33f750d2ba270711aa37b86348505d93946b974f071b765ff317e36f64ce52` | tools/hv4_cards.py | **changed** (Note N4's `CHAIN_HOLDERS_MAX_AGE_MS_V2` and `holders_max_age_ms`; scan-speed helpers). The V-band filter (2.3) is the same check as at `d1dbcd6`, at new line numbers. |
| `9dda6a88c2fce3faefe5115d2cf47ef80c6600d824ba005610f956223cd22cce` | tools/hv4_chain.py | **changed** (policy v2; `DEFAULT_DAILY_CALL_CAP` 48,000 at hv4_chain.py:709) |
| `6bfac8d83a8c8ab7fb64ef9f0208c5fda64afda3d7bc094a5e992953c1001ed3` | tools/hv4_prefetch.py | **changed** (policy v2 with N4; `HOURLY_BUDGET_V2` 2,000 at hv4_prefetch.py:53) |
| `68e3a43398640c0472edad4ecb946f06f88642fe1e7fa01f590be3a7f9a44eec` | tools/hv4_obs.py | **new since `d1dbcd6`** (observability side file `hv4-obs.jsonl`) |
| `75a802e2799d011075314fefdf5c39022f29e7f712ed3ed305170bc915e89c0b` | tools/hv4_arms.py | unchanged |
| `bf0f03646b19ced6efd93683a1507416b1524c1200235b2b5463764360e675b8` | tools/hv4_model.py | unchanged |
| `933f9c52d337e79fa9f065a5b965fbe4af91518a370985f1739c91053ed301b2` | tools/hv4_prompts.py | unchanged |
| `8f0dfaada9e77a7e2d6d76c574f70ee10d0c7af7433c96f15da8c2de32cc7325` | tools/hv4_review.py | unchanged; the nightly reviewer (section 2.4) |
| `f5e62ab57a72ebed0620b36a084893b10030350949204bd20624a78299b438ae` | tools/hv4_shakedown_report.py | not part of the running process; the stub report tool (revisions 3 and 4, #770) |
| `0cb4e1642d94208db84eb5669221837d858db619bd338beb248af3e547894ce9` | tools/hv4_read.py | unchanged; **not part of the running process, and its "25 cells × 1 read = 25 checks" text (line 31) stays at 25** (22 + 3 controls; QP #762 ruling (d)). It must be changed in its own reviewed PR before the first read, to derive its cell list from the nine registered `bot_id`s and the declared cells, assert that the list equals this record, implement C2's with-and-without table and the D5 set (section 3), and check every start row and open row against the config pin of 2.3. Owed; not in this PR. |

Tree ids at `9ed4c5f` (from `git rev-parse 9ed4c5f:<path>`; equal to the ids at `d1dbcd6`): `tools/hv4_playbooks` `b39d0ad103ee0c02956251f0bcc37a21e5386d52`; `tools/hv4_templates` `e45184be38fa1b742d3aac30e672c7f557208825`. `git diff d1dbcd6 9ed4c5f -- tools/hv4_playbooks tools/hv4_templates scripts` is empty.

### 2.3 DEC-031 Amendment 2 for HV4 (checked at `9ed4c5f`)
- **Coverage.** Amendment 2 merged as squash commit `b2fef927f99c3960f28ced4e2a3aa5d2782804f1` (#740), committer time **2026-10-19T17:57:08Z** (QP). Coverage holds if the nine `bots.json` lines and their notebook copy are written after that instant (they do not exist yet; the arena registry is untouched) and the clock 2026-10-23T00:00Z is after it. Amendment 3 merged as `3eadf195661f` at 2026-10-19T20:06:29Z (QP).
- **Am.2 item 2 (i): rent 0 passed explicitly. HOLDS at `9ed4c5f`.** `STACK_RENT = 0` (hv4_runner.py:1764); `STACK_FIELDS` spells every `StackConfig` field but `feed_delay_slots` as a literal (hv4_runner.py:1765-1768), including `"rent": STACK_RENT` (:1766), `"k_rounding": "ceil"`, `"e1_floor_per_005": 384_022`, `"strand_rule": "both"`, `"gap_slots": 150`, `"gap_silence_ms": 60_000` (:1766-1767); `stack_config()` (hv4_runner.py:1771-1784) takes only `feed_delay_slots`; `main()` builds the engine from it: `engine = ArenaEngine(a.ledger_root, a.registry, cfg=stack_config(feed_delay_slots=a.feed_delay_slots))` (hv4_runner.py:1866). The strand rent stays the engine default 2,039,280 (docstring hv4_runner.py:1775-1781). Re-computed in the venv at `9ed4c5f`: `stack_config(7).as_dict()` below.
- **Am.2 item 2 (ii): frozen code enforces the pool filter. HOLDS at `9ed4c5f`.** The runner calls `C.filters(snap, as_of_ms=T)` in the scan (hv4_runner.py:1000), and `hv4_cards.build_card` calls it (hv4_cards.py:1064). In `filters` (hv4_cards.py:445-): `quote_wsol` is checked first (`add("quote_wsol", ...)`, hv4_cards.py:458; `wsol = ... is True`, :459), then V only for a WSOL pool (:460): a WSOL pool without V fails as `v_missing` (:463), otherwise `v_band` requires `V_LO_LAMPORTS <= v <= V_HI_LAMPORTS`, both ends inclusive (:465). `V_LO_LAMPORTS = 17_500_000_000`, `V_HI_LAMPORTS = 17_700_000_000` (hv4_cards.py:63-64). A non-WSOL pool fails `quote_wsol` alone and its V is not read (:450, :460). Introduced by PR #749; Note N4 does not touch it.
- **Rent finding (DEC-031 Am.2 item 2, plain).** Both (i) and (ii) hold at `9ed4c5f`, so HV4 is covered: the deciding rent is 0 on a filled trip. It is **not** "rent charged (2,039,280) for HV4". A strand still loses 2,039,280 (item 4). This PR changes no runtime code; the frozen sha is `9ed4c5f`.
- **Full config pin (Am.2 item 8).** `StackConfig.as_dict()` at `9ed4c5f` with `feed_delay_slots` 7 (sort_keys, compact separators), re-computed by this worker with `tools.hv4_runner.stack_config(7)`; it equals the pin at `d1dbcd6` in the #762 draft and QP's:
  `{"e1_floor_per_005":384022,"exit_lag_s":0.55,"feed_delay_slots":7,"guard":1.15,"h_in":0.002608,"h_out":0.0016,"k_rounding":"ceil","portal_ppm":0,"press_intercept_provisional":-1.4197242838379789,"rent":0,"send":505000,"strand_rule":"both"}`
  sha256 `1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986`. The read compares every key on every start row and open row against this pin. The feed delay 7 is from arena-records-2026-10-18.md §3 and the shakedown ran with `HV4_FEED_DELAY_SLOTS=7` (CHECKS-3, Run); the T0 job's command line must show it (**TBD**).
- **Read statement.** Rent 0 decides; the rent-charged leg is printed beside it (Am.2 items 7, 9).

### 2.4 DEC-031 §1.3 (self-improving book): the pins
§1.3 names Haiku trader v4. The frozen part is the code, the reviewer's instructions and the model IDs. Each daily playbook version is hashed and logged before the first decision that uses it, and a version may use only outcomes whose exit is before its write instant. For HV4:
- **Reviewer code and instructions.** `tools/hv4_review.py` sha256 `8f0dfaada9e77a7e2d6d76c574f70ee10d0c7af7433c96f15da8c2de32cc7325` at `9ed4c5f` (unchanged since `d1dbcd6`); playbook tree `tools/hv4_playbooks` `b39d0ad103ee0c02956251f0bcc37a21e5386d52`; template tree `tools/hv4_templates` `e45184be38fa1b742d3aac30e672c7f557208825`. Job script `scripts/research/hv4-review-job.sh` (a scheduled MiScusi job at 00:05Z; nothing is edited by hand).
- **Same checkout.** The reviewer runs from the same clean checkout at `9ed4c5f` (Am.3 item 1). A reviewer added later at another sha is a new book.
- **Reviewer job ids** (00:05Z, one per learning arm: CON-L, BIG-L, CON-P; BST-L is left with lane H, DESIGN line 145 lists BST-L and it is not registered): **TBD, the manager schedules them and records the ids here before T0.** I found no scheduled reviewer job in the repo or the ops directory.
- **How a version is chosen.** `review_version_source` (hv4_runner.py:259@9ed4c5f); the runner loads the version in force through `refresh_versions` (hv4_runner.py:1602). A version is in force from `effective_from_ms = max(cutoff + 30 min, written_ms)` (hv4_review.py:48), so it is hashed and logged before its first use.
- **Outcome cutoff rule, with code.** The bundle holds the arm's own closed trades with `close_ms` strictly before the cutoff 00:00:00.000Z of the review day, where `close_ms` is the exit decision instant plus the slower exit fill leg (`EXIT_FILL_MS` 1,900 ms) (hv4_review.py:10-12). In code: `load_trade_records` drops `close >= cutoff_ms` (hv4_review.py:308); `before_cutoff` keeps only `close_ms < cutoff_ms` (hv4_review.py:390-392); a manifest whose `max_input_close_ms` is at or after its cutoff, or that is written before its cutoff, is refused (`ManifestRefused`, hv4_review.py:45-48).
- **Placebo shuffle seed (CON-P).** The (net_pct, exit_reason, hold_s) tuples are shuffled with a seed from `sha256(arm|date)` (DESIGN line 153; hv4_review.py:33).
- **If the reviewer is not running at T0,** L and P trade on v0 playbooks and knobs, and that is the registered behavior; a reviewer started later at a different sha is a new book.

## 3. Declarations (SHAKEDOWN-CHECKS-2/3/4 "Declarations before T0", plus the two QP adds)
1. A trade whose sell was refused or delayed is kept and counted **at the fill it actually got**; never dropped or re-priced.
2. Every read reports the trades with a refused or delayed sell, the `sell_alert` and `sell_orphan` counts, and the longest wait from the first sell decision to the landed sell.
3. `solders` is out of scope for this book unless the read tool imports `tools.h5_executor`.
4. DESIGN.md (with Amendment A1 and Notes N1 to N4), SHAKEDOWN-CHECKS-3.md, -3-NOTES-1, -4.md, -4-NOTES-1 and `n_bots` 9 are pinned in sections 1 and 2.1, with prefetch policy v2 with N4, CAP 2,000 chain calls in any rolling hour and 48,000 a day, and the observability rows on (CHECKS-4 Declarations before T0).
5. **C2 membership (QP, before T0, outcome-blind).** "A trade is in a catch-up window if its entry `call_ms` or its exit `call_ms` falls inside the window. Stamps are not used."
6. **D5, guard lag (QP, before T0, outcome-blind).** "Per open row, guard_lag_slots = `effective_slot.entry` - `call_feed_slot`. Every read reports, per book, its p50/p99/max and the number of entries the guard reverted. Trades with guard_lag_slots > 25 (10,000 ms at 0.4 s/slot, check 6's max) form a set. The verdict is stated with and without that set, beside C2's catch-up set, and with and without the union of the two. Fills are kept as booked (Declaration 1)."
7. **Read disclosure: V counts.** Every read discloses the window's `v_missing` and `v_band` counts, with no decision rule attached (QP #755 item 4, last bullet; as in #749).
9. **Credits (CLAUDE.md "Credits").** Helius credits per UTC day from the `chain_hour` rows are recorded in the stub report (section 7) and, for the run, in the daily note: which unit (the research-0 HV4 chain source and prefetcher, `MAL_HV4_RPC=helius`), how many credits, and what it made measurable. **TBD** until the report.
8. **Daily decision-log hash (DEC-031 §1.2: "Each book's decision-log sha256 goes into a MiScusi notebook entry at least once per UTC day").** The tool exists: `python -m tools.arena_score ... --daily-digest` prints `decision_log_sha256` per bot, "the stable commitment" (arena_score.py:13, :544-565). I found **no scheduled host-side job that runs it for the arena books** in `scripts/` or the docs I could cite. **TBD: manager creates it before T0** (a host-side MiScusi job, one notebook entry per UTC day naming the nine `bot_id`s; job id TBD).

## 4. Conditions from quant-proof on PR #755 (comment 6276972823) and the memory-plan rule (QP #762 comment 6278428501, ruling (b)); the shakedown that measures it is now the v2 stub (section 7)
- **Memory-plan rule, fixed before the v2 stub is read (it was fixed on 2026-10-19/20 and is unchanged; the v2 stub's counts are not yet read by this worker).** Compute per process segment: a restart resets RSS, so never fit a slope across it.
  - `s` = the least-squares RSS slope over stub hours 2..24, outside each catch-up window, per segment. `s_max` = the larger of the two segments' slopes. `R0` = the larger post-drain RSS. `H` = the hours from T0 to the latest planned read instant (declared here: **TBD**).
  - **(A)** if the research-0 job can get MemoryMax `M` >= 1.5 × (`R0` + `s_max` × `H`) next to the other planned jobs (J3's 64 GB, Am.3 (j)). **Otherwise (B)**, at a cadence of at most min(24 h, (`M` − `R0`) / (2 × `s_max`)).
  - The linear projection is **not accepted** if either segment's second-half slope is more than 1.5 × its first-half slope; then (B) at 24 h is mandatory.
  - An OOM fails the stub.
  - Plain arithmetic at 154 MB/h (not a measurement): H = 168 h needs about 41 GB; H = 336 h needs about 80 GB. QP prefers (A), and Am.3 (j) strengthens that.
  - **Chosen plan: TBD, to be computed by the manager from the v2 stub's `rss_mb` per hour (`runner_hour` rows) and pasted here after 2026-10-22T18:05Z. `s` per segment, `s_max`, `R0`, `H`, `M`, projected hours to MemoryMax: TBD.** The two segments are the v2 stub's two jobs (#1286 and #1296, section 7); the restart between them is the deliberate one.
  - **(B) cost, stated.** Each restart books every recovered position as an excluded `engine_restart` row, and a book above 5% excluded is NOT_DECIDABLE; the excluded share is about (restarts × open positions per book at each restart) / (book trades). At the gate's 100 trades with 7 restarts a book can afford 5 recovered positions in total. Also Am.3 item 8 (j) (as QP cites it): restarts cause feed gaps, which make every open position in that process NOT_DECIDABLE; R arms mirror L positions, so one restart excludes both. Each restart also opens a catch-up window (section 5). The stub cannot measure open positions per restart for the LLM arms (StubModel abstains).
  - Either way: restarts are host-side MiScusi jobs (CLAUDE.md), not session crons; restart job ids for (B) **TBD**, or "none planned" for (A); every restart is recorded in LAB_STATE; an unplanned restart or OOM is disclosed as unplanned; an orphaned recovered position makes the read NOT_DECIDABLE (QP-RULING-1019-startup point 5).
- **`trim_ms` block.** If any stub line of `engine-trim-counters.jsonl` has `trim_ms` > 1,000, HV4 does not register until QP re-rules.
- **`v_missing` evaluation (declared by QP before the stub).** On every line of `engine-trim-counters.jsonl`, per `engine_id` segment, the ratio `v_missing_pools / v_checked_pools`. If it is above 0.5 on every line of the stub, HV4 does not register until the cause is explained. The report carries the counters per segment and never adds distinct pools across a restart; it must not say "44% of the universe is lost to V".
- **NOT_DECIDABLE rule, pre-registered before T0 (QP on #745 item 4 (3)).** If any line of `engine-trim-counters.jsonl` has `rows_below_trim_floor` > 0 or `refused_history_trimmed` > 0, equivalence with an untrimmed engine is void for every book on that engine for that segment, and the read is NOT_DECIDABLE. The same condition fails the stub's trim check. (arena_paper.py:99-102@9ed4c5f states the same void condition.) This also covers below-floor rows and `history_trimmed` refusals before T0.
- **Pins as path:line@`9ed4c5f`.** `RunnerConfig.bound_memory = True` (hv4_runner.py:542) and `trim_engine_history = True` (hv4_runner.py:546; no CLI switch); `HOUSEKEEP_EVERY_MS = 5 * MIN_MS`, 300,000 ms (hv4_runner.py:81); `TRIM_KEEP_SLOTS = 5_000` (arena_paper.py:104); `ENGINE_TRIM_SCHEMA = "hv4_engine_trim_v1"` (hv4_runner.py:359); no `prune()` call (the text `.prune(` appears only in comments, hv4_runner.py:544 and :699).
- **Trim-pause disclosure, in every read.** "Engine trim every 5 tape minutes on the row thread; measured trim_ms p50/p99/max [TBD, labelled as stub figures]; a decision written in a trim tick is priced up to that much later." Check 6 stays as written; trim minutes are not excluded.
- **Evidence scope (QP).** The md5 equivalence of the trim was shown on 9 h with code-rule arms only (builder) and on a 2 h decision log with an empty ledger (QP's own run, which did not compare fills). No LLM-arm fill path is shown. The residual memory grower (about 150 MB/h) is unidentified. #1236 is not fixed by #755.

## 5. Start-up catch-up (QP-RULING-1019-startup.md, ruling (b), C1 to C3)
### C1: VERIFIED at `d1dbcd6` by QP (comment 6278428501); re-checked at `9ed4c5f` by this worker (code reading of the changed lines, and the nine tests)
**Re-check at `9ed4c5f`.** `tools/arena_paper.py` is byte-identical to `d1dbcd6` (sha256 `9d2fdb04…`, same blob id), so every `arena_paper.py:line` below is unchanged. `tools/hv4_runner.py` changed; `git diff d1dbcd6 9ed4c5f -- tools/hv4_runner.py` touches `register_all` (moved into `register_arms`), the scan (cards get `feats=` and `prints=`, `watch(..., vols=)`), observability rows (`_obs_hour_tick`), `unpinned_run_mode`, `main()` and the `--register` branch. It does not touch `poll_to`, `step`, the wrapped apply hook, `_on_row`, `_open`'s `engine.buy` call, `_sell`'s `engine.sell` call, or the engine construction in `main()` other than by line number. The runner lines are re-cited below at `9ed4c5f`. The one change the tests needed: a live `Runner` now refuses to build without `obs=True` and a policy-v2 prefetcher marker (`unpinned_run_mode(..., live_runner=True)`, hv4_runner.py:1813-1830, called at :591-593), so `tools/test_hv4_catchup_landing.py` builds its live runner with `obs=True` and `V2Chain(FakeChain())` (`V2Chain` is the same marker `tools/test_hv4_runner.py:144` uses). Before that edit two of the nine tests failed with that `RuntimeError` at `9ed4c5f`; after it, 9 passed. No assertion was changed. Mutation checks were not re-run at `9ed4c5f` by this worker; QP's two mutations at `2d2284b` (8 of 9 fail each) are the evidence for what the tests detect.

(The text below is the #762 draft's, with the runner citations moved to `9ed4c5f`.)
**Claim.** A buy or sell called while the apply hook lags the wall clock lands at or after the slot of the wall-clock call instant, not at the slot of the last feed row the engine has seen.

**Tolerance (QP).** The landing is at or after the slot of the wall-clock call instant up to the error of the slot-clock fit: |error| <= ceil(lag_slots × |relative sps error|) + 1, the bound used in the test. At a 130 s lag, a 1% sps error is about 3 slots, in either direction.

Trace (`arena_paper.py` lines `@d1dbcd6` = `@9ed4c5f`; `hv4_runner.py` lines `@9ed4c5f`):
1. **Who calls, with what.** The state is drained by `Runner.poll_to(T)` = `state.poll(until_ms=T - 1)` (hv4_runner.py:741-742), called first in `step(T)` (hv4_runner.py:1681), before `exits` (1687). Each row goes through the wrapped `state.apply` (hv4_runner.py:661-667) into `Runner._on_row` (673-) and `engine.on_row`. `Runner.slot`, the `decision_slot` the runner passes, is the highest trade slot applied so far (hv4_runner.py:676-677), so it lags in a drain. A buy is `engine.buy(bot, card["id"], size, dec_ms, self.slot, ...)` (hv4_runner.py:1164); a code exit is `dec = T` (1376, the logical tick, behind the wall clock in a catch-up) and `engine.sell(bot_id(pos.arm), pos.pid, 1.0, dec, self.slot, ...)` (1392); an LLM answer stamps `dec_ms = now_fn()` in live mode (`_decision_ms`, 738-739). `main()` builds the engine with no `now_fn` and no `sps_fn` (hv4_runner.py:1866), so the engine's clock is the wall clock (arena_paper.py:850) and its sps comes from the tape's block times (`SlotClock`, arena_paper.py:1047-1049; h5_shadow.py:406-449). The runner overrides the engine's clock only in sim mode (hv4_runner.py:658-659).
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
- **October hours.** The X-arm code rules were priced on 2026-10-19 arena tape in engineering replays and stubs before `clock_start`: the builder's 9 h replay (T08..T16, 105 trade rows per QP #755), QP's T17..T18 replay (0 ledger rows), stub #1236 and the `d1dbcd6` stub. Outcomes were never opened (md5 and counts only). The `b240191` smoke (#1275) and the current `9ed4c5f` stub (#1286, #1296) are on the same kind of hours (counts and timings only, StubModel). None of these hours counts for any HV4 book. HV4 is not a hunt23 book, so Am.3 item 1's P0 count does not apply, and it is not screened on October hours under Am.3 item 4.
- **Per book:** the agent (Haiku v4, model id **TBD**); DECLARE sha256 and notebook id **TBD**; cells declared 25 (above), priced **TBD**, killed **TBD**, surviving **TBD**; blocks: the arena tape from 2026-10-21T00:00Z.
- **Cells in total** across hunt23, J3, cohort 2 and the arena: **TBD at read time.**
- **p** (one-sided, 10,000 draws, seed 1, the larger of the two fail models): **TBD at the read.**
- **The plain line:** if no book has an edge, the chance of at least one screen pass is up to 1 − 0.95^N: **TBD** for the total N (40% at N = 10, 79% at N = 30, per Am.3 item 3).
- **Screen-read line:** p and Holm rank among all arena reads, report-only: **TBD at the read.**

## 7. Shakedown runs: the current v2 stub, the smoke disclosure, and every earlier HV4 run
### 7.1 The current stub (the one that decides T0)
Sha `9ed4c5ff63935696b2a89150e13565e123047754`; root `/data/mal/arena/_shakedown-hv4-v2-1020` (fresh; CHECKS-4-NOTES-1 R-5); StubModel, `MAL_HV4_RPC=helius`, feed delay 7, lane H off, prefetch policy v2 and obs on. Two MiScusi segments on mal-research-0, same root, sha and command (HV4-V2-STUB-COMMANDS-1020b.md): **job #1286** (segment 1; the registry was written 2026-10-21T17:33:24Z, first start row `clock_start_ms` 1792604118000 = 2026-10-21T17:35:18Z; stops at RESTART_ISO, HH:05Z of the first non-00 hour boundary at least 12 h after the start) then **job #1296** (segment 2, after-chained; ends 2026-10-22T18:05Z, the first hour that closes at least 24 h after the first start row). The job ids are as given by the manager and not re-read by this worker. This worker did not open the stub's ledger or any file under its root. After END the manager checks by hand that the job log `head=` line and the report's `runner_start` `frozen_sha` both equal `9ed4c5ff63935696b2a89150e13565e123047754` (QP #774 Q4.4) and that the six pins of the job's first command (CHECKS-3, -3-NOTES-1, -4, -4-NOTES-1, DESIGN, feed script, report tool) equal section 2.1. Proof of decision neutrality of the `--register` change: MiScusi job #1278, `/data/mal/hunt-1008/ops/proofs-774/` (CHECKS-4-NOTES-1 R-3; QP #774 verdict is conditional on it).

**SHAKEDOWN REPORT: PLACEHOLDER FOR THE MANAGER.** Run `tools/hv4_shakedown_report.py --checks-rev 4` at `9ed4c5f` (counts only) after 2026-10-22T18:05Z and paste its output here, unedited, with the instant, the command, and each check PASS/FAIL/UNMEASURED with its number. Rule M: `runner_start_rows_without_a_start_row` must be 0 (CHECKS-4-NOTES-1 R-6). Verdict: **TBD**. Until it is pasted and PASS, HV4 does not register at `9ed4c5f` (stop rules, Appendix A section D).

### 7.2 Smoke disclosure (quant-proof #774 comment 6289565427, section 3; the text is copied verbatim)
Before the stub, the manager ran smoke job #1275 at b240191 in /data/mal/arena/_smoke-hv4-v2-1020 (StubModel, real Helius, 2026-10-20 08:26Z to 16:30Z; notebook n_DOIsbmO-f5xZ_Q and n_s_KzgaMBnLwcCw). Its counts were read at 2.3 h with report tool revision 4: check 2 0/116 CON and 0/147 BIG; check 4 null holders CON 1/77 and BIG 2/94; check 6 scan p99 681 ms; check 12 0; chain calls 957 in partial hour 08 and 1,488 in hour 09. Rule M fired on it (runner_start_rows_without_a_start_row = 1, from the --register process), so under SHAKEDOWN-CHECKS-4 the smoke would be FAIL. That defect is the only change made after the smoke (PR #774, md5-identical on R2 by job #1278). No check, limit, rule M guard or DESIGN note changed after its counts were read. The smoke does not count toward the stub verdict, and a stub FAIL is not replaced by the smoke, or by a re-run, without a new pinned record.

Added by this worker, not part of the verbatim text: the smoke's root files were last written at 2026-10-20T16:29:17Z (`ls -l` mtime of `chain-prefetch-spend.json` in `/data/mal/arena/_smoke-hv4-v2-1020/root`; no file was opened), consistent with the 16:30Z end. SSD-RESTART-CHECKLIST-1020.md asked for the actual end instant if the smoke ended early; it did not end early on that evidence.

### 7.3 Every earlier HV4 shakedown or smoke run (QP #774 section 3, last paragraph)
Directory birth times are from `stat` (names and times only; no ledger or `bots.json` was opened). The `bots.json` line counts are QP's (#774 comment). Job ids are taken from the Console events, `SHAKEDOWN-CHECKS-2.md`, QP-RULING-1019-startup.md and `ARTIFACTS/lab/hv4-memory-growth-2026-10-19.md`; **where the mapping from directory to job is an inference it says so, and the manager confirms it before merge.**

| Directory (`/data/mal/arena/`) | Created (UTC) | `bots.json` lines | Job id | Sha | Verdict |
|---|---|---|---|---|---|
| `_shakedown-hv4-1019` | 2026-10-19 08:05:20 | 13 (lane H on) | #1219 (Console event hv4-feed-shakedown-1019: "A 24 h stub shakedown (job #1219) is running"); mapping to this directory is an inference from the creation time and the 13 lines | before `d1dbcd6` (lane-H-on build; sha **not recorded here, TBD manager**) | superseded: lane H later switched off (Note N2); not a verdict for this build |
| `_shakedown-hv4-1019b` | 2026-10-19 14:33:07 | 9 | #1236 (start row 14:35:23Z in QP-RULING-1019-startup.md; killed exit 137 at 2026-10-19T17:07:29Z, Console event hv4-stub-oom-1019). SHAKEDOWN-CHECKS-2.md names job #1218 for this root; whether #1218 was an earlier submission of the same root is **TBD manager** | before `d1dbcd6` (sha **TBD manager**) | FAILED: out-of-memory (about 740 MB/h, #744 did not fix it; engine trim #745 followed) |
| `_shakedown-hv4-1020` | 2026-10-19 21:31:28 | 9 | #1249, then #1250 (restart, queued); sampler #1252 (as given by the manager, not re-verified). Started 2026-10-19T21:31:27Z | `d1dbcd64fc434590275d34a3b65d6ad5d45d86ce` | FAILED shakedown check 6 (scan timing) on 2026-10-19 23:00Z; cancelled 2026-10-20 (Console events hv4-check6-fail-1019 and hv4-d1dbcd6-stub-cancelled-1020). Fixed by v2 (#767, #771) |
| `_smoke-hv4-v2-1020` | 2026-10-20 08:26:59 | not counted | #1275 | `b240191083a1705e4fa1c86ee2413de5550c288d` | smoke, not a verdict; under CHECKS-4 it would be FAIL (rule M, 1 orphan `runner_start` from `--register`); defect fixed by #774 (see 7.2) |
| `_shakedown-hv4-v2-1020` | 2026-10-21 17:33:18 | 9 expected | #1286 then #1296 | `9ed4c5ff63935696b2a89150e13565e123047754` | **TBD** (7.1) |

Also on record, not runs in these directories: replay proofs #1278 (R2 window, md5 equivalence of #774) and the builder's and QP's engineering replays (section 6, October hours). A stub FAIL is not replaced by the smoke or by a re-run without a new pinned record (7.2).

### 7.4 Result layout for the report (nothing below is measured yet)
Counts and timings only; the stub's engine ledger is never opened. StubModel trades are not evidence for any book.

| # | Check (CHECKS-2 text, carried by CHECKS-3 and -4; this table is the layout, the pass rules are CHECKS-3, -3-NOTES-1, -4, -4-NOTES-1) | Result |
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

Verbatim from quant-proof, PR #762 comment 6278428501, section 4 (the heading line below is QP's own). **It was written for the `d1dbcd6` build and stub; read it with these substitutions: sha `d1dbcd6` -> `9ed4c5f` (`9ed4c5ff63935696b2a89150e13565e123047754`); T0 2026-10-21 -> 2026-10-23T00:00:00Z (`clock_start_ms` 1792713600000); the stub is section 7.1; the pass rules are CHECKS-3/-4 and their notes (section 2.1), which add Rule M, check 11, check 12, and the 2,000/48,000 caps; section 2.1 pins replace the old ones.** The FINAL pass fills this in; nothing in it is filled yet.

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

