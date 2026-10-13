# Manager handoff 2026-10-13T09:48Z (manager10 -> next manager)

Replace this page at the next handoff; don't append. Read it first. **Section 1 is new (2026-10-13). Sections 2, 3, 4 and 6 are from the 10-12 handoff, edited only where marked; where they disagree with section 1, section 1 governs.** Nothing here is an edge claim, and no realized P&L number is written here on purpose.

Then read, as needed:
- **The memory state file** `~/.claude/projects/-home-claude--miscusi-repos-vaanai-MAL/memory/state-2026-10-04.md`: read its END first (dated lines for every step of 10-13).
- **H5 stop and resume of 2026-10-13:** [DEC/DEC-024-h5-live-canary.md](../DEC/DEC-024-h5-live-canary.md), "Halt record: section 5.6, 2026-10-13" and "Resume record"; quant-proof's checklist and rulings in PR #632 comments 6142079418, 6142226900, 6142341741 (copies in the session scratchpad as `qp632-*.md`).
- **Seal-window steps and GAPS:** `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md` and the resolved specs of 10-13 in `/data/mal/hunt-1008/ops/GAPS-DAY-1013.md` (25 quant-proof questions; replacement one-shot prompts, not applied yet).
- **Session crons (all prompts on disk):** `/data/mal/hunt-1008/ops/recurring-crons-1011.json`, `/data/mal/hunt-1008/ops/oneshot-crons-1012.json`, `/data/mal/hunt-1008/ops/oneshot-1013-1833-t1-block-check.md`, `/data/mal/hunt-1008/ops/oneshot-1013-2137-tipauth-36h.md`.
- **EXP-022 CAP-PICK Part 1:** [EXP/EXP-022-cap-pick-part1-prereg.md](../EXP/EXP-022-cap-pick-part1-prereg.md). **Tipauth count list and C1-C6:** PR #610 comment 6121123397.

## 1. Where things stand (2026-10-13T09:48:28Z)

### 1.1 H5 is live at T0 again, as an execution measurement

- **State.** `STOP` was removed at **2026-10-13T09:44:43Z** (job #928): `stop_file=False halt_file=False live_ok=valid`, attempts 11, open 0/2, pending 0, halts=[]. Installed build `b7ddeb6` (pre-send limit 3500 ms). It was stopped from 07:14:44Z after the daily A3 monitor (job #907) flagged `pins_changed`: the pump fees program was redeployed on 2026-10-12T17:40:24Z.
- **Label, on every report, note, Console entry and proposal:** "execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book". No report may say a canary result shows that H5 makes money.
- **The resume record is in review at writing** (this PR; DEC-024 "Resume record"). Until it is merged with both verdicts the stop "has fired and is unresolved" for T1.
- **The owner's question `q_hw6mloW_n2goPQ` is OPEN on purpose** (the two corrected sentences; sent 09:12:57Z; no answer at the removal). If the owner answers "This changes my decision", or anything that withdraws or questions the decision: place `STOP` at once (MiScusi job on mal-fast-0: `sudo -n touch /var/lib/mal-live/h5/STOP`, then `--status`), tell the owner, report to quant-proof. If the owner answers "My decision stands", record it in DEC-024 with its instant, then close it. Any other free-text reply goes to quant-proof first. **Read its state (`miscusi_ask_status`) at the start of every manager session, before every daily tracking note and before any T1 proposal; each daily note gives the state with the instant of the read. Do not close it unanswered before the first daily tracking note (2026-10-14) has gone to the owner; an unanswered close is announced in a Console entry (closed unanswered; the owner can withdraw the decision at any time by a message), recorded in DEC-024 with its instant, and is never an approval** (quant-proof, PR #634 comment 6143061927, ruling F). If the owner withdraws, the stop is again "fired and unresolved" until a new dated owner line and a new resume record.
- **T1 is not near.** It needs: the resume record merged; the install record's daily check after 2026-10-13T18:28:03Z printing `h5_daily_check ALERTS=0` in the same job as the start row (3500) and `halts=[]` (**done after this handoff was written:** run 1, job #936 at 18:33:32Z, printed one alert, so the condition was NOT met by it; run 2, job #938 at 18:49:17Z, printed `ALERTS=0`; all runs are in LAB_STATE under the 2026-10-13 install bullet); 20 landed buys (11 attempts and 11 sells landed when this handoff was written; 13 and 13 at job #938) and the Amendment 3 item 2 means; the DEC-024 landing-window note (quant-proof's text, PR #626 comment 6135815429 item E; its own PR, both verdicts). Every T1 proposal opens with "EXP-024 ended NOT_DECIDABLE on 2026-10-13; H5 v1 has no forward read and will not get one; the Look 1 condition of Amendment 3 item 2 cannot bind", cites notebook `n_MD1zlSFaAgFrXA`, names the stops and halts of 10-10, 10-11, 10-12 and 10-13 and the one buy dated after the fees-program deploy, and goes to quant-proof before any ask to Helm.

### 1.2 What happened on 2026-10-13 (all merged unless marked)

| PR | What |
| --- | --- |
| #626 | DEC-024 install record: Helm installed `b7ddeb6` at 00:42:22Z |
| #627, #630, #633 | Records: the stop, the review outcome, the pre-removal message |
| #628 | `tools/h5_upgrade_dryrun.py` (keyless round trips, fee bps against the pinned tier) |
| #629 | Re-pin of `programs.fees` and the upgrade review (`ARTIFACTS/lab/a3-repin-2026-10-13-*`) |
| #631 | `tools/boost_slice_watch.py` (report-only daily slice and pause counts) |
| #632 | DEC-024 halt record with the owner's line on the slices band; EXP-024 A3 halt record |
| this PR | DEC-024 resume record, this handoff, Console (in review at writing) |

Declared runs and their results are in the DEC records and in LAB_STATE (jobs #911 to #928).

### 1.3 The owner's decision of 2026-10-13 and what follows from it

- The owner's words are quoted in DEC-024. In short: read "Slices per pool: 29–30" on the middle value; resume H5; track daily the share of pools under 29 slices and any pause between slices of 60 s or more; flag the owner if either rises clearly above the pre-upgrade level.
- **The owner's line** applies to the median of the monitor's per-pool slice count over the profiled post-upgrade pools, n >= 15 (the last two conditions are quant-proof's). With fewer than 15, the per-pool reading governs. If the signature-list median and the exact median disagree about 29–30, the decision is the owner's.
- **Daily tracking starts with the 2026-10-14 daily A3 run.** After the monitor job: `cd <checkout of main> && /data/mal/venv/bin/python -m tools.boost_slice_watch --record /data/mal/structure-monitor/daily.jsonl --out /data/mal/structure-monitor/slice-watch.jsonl` as a MiScusi job on mal-research-0 (exact mode is the default; about 550 public-RPC calls for a 20-graduation day by the builder's estimate, and job #919 used 1,351 calls for 40 pools; the default cap is 5000). It is a reading only if `status` is done and every drop count is 0. The daily note carries: the number profiled; the counts at 28 or fewer and at 27 or fewer and the minimum; the two pause counts; the pooled counts with their Fisher p values; what was not evaluated. The first note also lists the pools seen before the series (the review's 40; the step-3 run's 28: 26 on 1, 28 on 1, 29 on 15, 30 on 9, 31 on 2) and states that the tool returned 6 of 50, 0 of 50 and 0 of 50 on the four pre-upgrade ranges (job #919). The flag rule and its limits are in the DEC-024 halt record. A flag is a message, never a stop.
- **The signature-list count reads LOW for "pools under 29"** (per pool it is an upper bound). Say it that way.

### 1.4 EXP-024 ended; EXP-022 and the 10-16 window

- **EXP-024's read ended NOT_DECIDABLE** at job #907's run (quant-proof, PR #629 comment 6140947978; the record is in the EXP file). Look 1 and Look 2 do not run. Nothing is run under EXP-024: no classification pass, no P5, P6, P7, no `look`, no hand edit of `LOOK_READS.jsonl`, no join of any H5 outcome. The seal stays. On 10-16/17 the daily note, LAB_STATE and the Console carry one line: "EXP-024 Look 1: not run. The read ended NOT_DECIDABLE on 2026-10-13 (A3 `pins_changed`); nothing was computed." **The EXP-024 rows of the deadline table in section 4 are void**; the one-shots `exp024-look1`, `e1` and `post-final-checks` need the replacement prompts written in `GAPS-DAY-1013.md` (apply them after quant-proof's ruling on that file's questions).
- **EXP-022 / CAP-PICK:** P2 is the last daily A3 run before 2026-10-16T01:00Z, the 2026-10-15 run of about 06:41Z. Six flags count there, and the two-consecutive-days clause. **Before that run, declare which job is the P2 run and that no other monitor run of any kind follows it before 2026-10-16T01:00Z** (declaration text and an errored-run protocol are drafted in `GAPS-DAY-1013.md`, item 9; quant-proof rules on Q22 to Q25 first). Walk 2 is submitted on 10-16 whatever P2 shows.
- **Walk #433 continues** (EXP-025 reads it). EXP-026: draft, window record empty, W0 has not occurred.
- **One quant-proof session is owed on 2026-10-14** (it was moved from 10-13: the H5 stop took the day's sessions): the 25 questions of `GAPS-DAY-1013.md`, the 16 OCT-QUEUE questions (`/data/mal/hunt-1008/oct-queue/QUESTIONS-FOR-MANAGER.md`), the F1 ledger release sentence PR (`docs/HOLDOUT_LEDGER.md`; `owner_ok_ref` = notebook `n_OLnV5a4MFslEHw`; due before 2026-10-16T02Z; run the catalog tests after ledger edits), and the DEC-024 landing-window note. Nothing in `GAPS-DAY-1013.md` needs a ruling before 2026-10-14T12:00Z.

### 1.5 Tipauth (H5 trade-rate fix), counts only

- The +24 h reads of 2026-10-13 were **not the deciding read**: item-4 job #924 printed `duration_24h_and_10_nogap_pv_ws_ge1 False {"covered_h": 23.9907, "elapsed_h": 23.9907, "n": 3}`; 26 triggers, 721 closes, would-pass 0, consistency failing 0, `tip_incompleteness_le_5pct` False (share 0.1875). Health job #925: tip ok on 1,443 of 1,443 heartbeats, 0 errors. The interim trigger print is job #926 (running at writing; out `/home/claude/data/h5-daily/tipauth/1791884043640/trigger-print.json` on mal-fast-0): read its ws-pass absent count, tell the owner at once if a ws-pass print is absent or different, and post one counts-only line on PR #610 for the +24 h reads (owed).
- **The +36 h reads of 2026-10-13 were not the deciding read either** (duration line n 5 of 10; "tipauth reads so far" below). **Next read: +48 h, until-ms 1791970443640 (2026-10-14T09:34:03.640Z)**; prompt in `/data/mal/hunt-1008/ops/oneshot-1014-0937-tipauth-48h.md`; then every 12 h. C5 of the count list is three conditions: `orphaned_by_drop_n == 0`, `tipauth_nosettle == 0`, `dropped_first_slot > cover_lo`. The stopped span 07:14:44Z to 09:44:43Z does not stop the shadow; it kept recording.

### 1.6 Running, in flight, and the crons

- Long-running MiScusi jobs: #817 (H5 shadow), #729 (enforce loop), #658 and #662 (C1-NF regime watch), #433 and #382 (walkers; both through 2026-10-13T07 at 09:43Z). research-0: 231 GB free.
- Open PRs that are not waiting on anything new: #624 (install branch: NEVER merge; keep the branches `claude/h5-exec-presend-age-install` and `install-base-f873aac`), #614 (tipauth gate, round 2 owed), #608, #471, #465, #90.
- **Session crons die with the session. Recreate them from the pointer files** (each prompt says "read the JSON file, take the prompt of key X"): recurring `23 3`, `47 5,11,17,23`, `41 6`, `13 9` (housekeeping), `17 12`, `16 0`, `21 0`; one-shots still ahead in `oneshot-crons-1012.json` (10-14 10:07 e'-dry-run, 10-14 11:23 extend-walkers [a verify now], 10-15 07:17 exp022-p2, 10-15 21:37 shadow-verify-exporter, 10-16 00:23, 00:43, 02:03, 03:07, 06:13, 07:05, 10-17 03:07, 10-21 12:07, 10-23 00:30; the 10-18 12:07 cron-renewal one-shot has no key in that file: its job is to recreate every recurring cron before the 7-day expiry) plus today's 18:33Z T1-block check and 21:37Z tipauth +36 h. Recurring crons fire up to about 30 minutes late.
- **Today's 12:17Z daily check:** the synthetic audit for 2026-10-12 has already run (job #918, exit 0); still run the health check and give the first since-install counts (ledger rows with `ts_ms` >= 1791852142303: `skip` with reason `stale_trigger`, `skip` with reason `stale_trigger_chain`, `decision` rows). The cron text says shadow #735; the shadow is #817.

### 1.7 Lessons of 2026-10-13 (they cost review rounds)

- **Anything the owner must see goes in a turn's final message, a MiScusi question or notebook entry, or the Console.** A mid-turn narration was not recorded as a message to the owner, and a record had to be corrected because of it.
- Owner-facing text never says "everything else is done", "fees unchanged" or "clear on every flag": name the checks, the sample, and what is still owed.
- Copy every sha, instant and count from command output. `date -u` before any timestamp.
- Declare before running (sample rule, statistic, test, rerun rule); record every run, failing ones too; never re-run to get a cleaner answer.
- Old job commands: `miscusi_job_status` forgets old jobs; research-0 job records are under `/home/claude/.miscusi/jobs/`, and fast-0 job commands can be recovered from the session transcript.
- After a 524 on `miscusi_job_submit`, check job #N+1 by number before resubmitting.

## 2. What is running

| Job / unit | Where | What |
| --- | --- | --- |
| H5 live executor (Helm-installed systemd unit, `LoadCredential`) | `mal-fast-0` | **2026-10-13: live again since 09:44:43Z after the structure stop (section 1.1).** T0, 0.02 SOL, max 2 open. Not a MiScusi job. Reinstall gate on 10-16 00:43Z: confirm `b7ddeb60aa4ced9243211775cf3d5a22ccc42dff` is the live build (`readlink /usr/local/lib/mal-h5-exec/current`); it is `f873aac` plus the pre-send commit, installed 2026-10-13. |
| #817 H5 shadow | `mal-fast-0`, 1.5 GB | Started 09:34:03Z (t_ms 1791797643640 = item-4 T0) on main `5db78a6` (shadow blob `918e76bc`), clean checkout (porcelain 0). Replaced #810 (09:06:45Z start, cancelled; its first log line had `porcelain_lines=1`), which had replaced #735 (main b65e2ec, blob cb1deb4). Time limit 13 d. Feeds the executor through the `/srv/mal-h5-shadow` bind. |
| #729 enforce loop | `mal-fast-0` | Halt-rule enforcement (#593), to 10-24. |
| #658 C1-NF soak, #662 monitor | `mal-fast-0` | Regime watch only now. #662 limit about 2026-10-17T22:00Z. |
| #382 forward walk 1, #433 | `mal-research-0` | Auto-resumed after the reboot. Never open outputs before the FINAL. |
| Tip follower `mal-fast-tip-follower` | `mal-fast-0` | Keeps 3 days (~18 GB/day). Disk 81% is steady state. Archive script `/data/mal/hunt-1008/ops/tape-archive.sh`. |
| Daily A3 structure monitor | `mal-research-0` | 06:41Z. Two consecutive unevaluated days count as a halt. Check a 10-12 run exists. |

**Crons (session-only, 7-day expiry).** 6 recurring pointer prompts from `recurring-crons-1011.json`: 0a25dc0e 03:23 archive, 638333d8 12:17 health+audit, 5745877b 06:41 structure, 51b24359 00:21 day check, a4af36b3 at 5/11/17/23:47 running check, 14193fc2 00:16 ledger. Plus 9a5bfe76 09:13 housekeeping, a renewal one-shot 4c1103db for 10-18 12:07Z, and 15 seal-window one-shots from `oneshot-crons-1012.json`. Verify with CronList after any compaction. **If the session dies again, recreate from those two JSON files and SEAL-WINDOW-OPS-1012.md.**

**Open PRs.** Draft PR #614 (`claude/h5-exec-tipauth-gate`, head 0c4fccd, executor tipauth gate, switch `tipauth_gate` default OFF): Reviewer CHANGES (comment 6121596920) and QP CHANGES (comment 6121641942, R1-R12); round 2 is not done. #608 (C1-NF 14c, parked with C1-NF), #471, #465, #90 parked. Merged: #603 as `848bd3a`; #611 as `c7de29e` (Reviewer OK 6121326459, QP OK 6121333548 at 87ac510); the three count-only tools #613 `fd20fd5`, #615 `ca4fa6a`, #616 `7a90d02` (section 4).

**Follow-ups to #603:** the FIRST_DAY pin and the Option B runbook wording were done in #611 (merged `c7de29e`). Later-PR items from the quant-proof pass on #611, still owed: a failed running-mode slot has no step (a) rule; finding 5 (loop warn on resume); a DEC-028 manager's-reading line on `c.v2["pairs"]` before any v2 send; the executor 1(b) pool-set `synthetic_src == "rpc"` filter. Also check the v2 latch for `boost_before_sell_gt_15pct` before any Option B (V2_REPORT_ONLY lists it though DEC-028 section 4 1(a) makes it v2's primary halt).

## 3. Research results 10-12 (none is an edge claim)

- Search A, exits on H5 entries: no survivor. `/data/mal/hunt-1008/search-a-h5-runner/REPORT.md`.
- Search B, what is new in October: 5 hypotheses, H10 dropped. `/data/mal/hunt-1008/search-b-october-new/REPORT.md`.
- SB-H1 September count: THIN. 67 cheap-fee boosted custom-pair pools on 11 dates. Parked to the FINAL. `/data/mal/hunt-1008/search-h1-sept/REPORT.md`.
- Search C, other venues: no BOOST-like forced buyer. `/data/mal/hunt-1008/search-c-other-venues/REPORT.md`.
- Strategy library and nearest-to-live shortlist: `/data/mal/hunt-1008/library/` (first pass had errors, corrected; verify before quoting).
- EXP-013 revive check: NOT FEASIBLE. `/data/mal/hunt-1008/revive-exp013/REPORT.md`.
- G4 bankroll desk read: no capped result between 10 and 79 slots; three open items. `/data/mal/hunt-1008/library/G4-BANKROLL-1012.md`.
- Seal-window drafts (FINAL job spec, markers, EXP-024 Look 1 chain, exporter, P4 read tool spec, (e') rehearsal, shadow limit): `/data/mal/hunt-1008/ops/seal-window-drafts/INDEX.md`.
- 10-12 evening, Haiku and big-win lines (exploration only; detail in LAB_STATE "Research results 10-12"): Haiku as trader or veto, "no lift shown" (`/data/mal/hunt-1008/haiku-loop/pilot-disc/REPORT.md`); Haiku name judge, DROP (`/data/mal/hunt-1008/bigwin-haiku-narr/REPORT.md`); Mayhem census, no trading rule tested (`/data/mal/hunt-1008/search-mayhem/REPORT.md`); LightGBM big-win selector r5-bw-a, fail under the declared rules on the rebuilt LAYA cache (`/data/mal/hunt-1008/haiku-loop/g-select-2/REPORT.md`). The 10-08 r4-b result did not reproduce on the rebuilt cache; cause not determined.
- October study queue and 16 open rule questions for one quant-proof ruling: `/data/mal/hunt-1008/oct-queue/QUEUE.md`, `/data/mal/hunt-1008/oct-queue/QUESTIONS-FOR-MANAGER.md`. Nothing declared or run.
- F1f quote-coin Step 1: loader frozen 08:31:41Z, the real run is at the FINAL (one-shot 10-16 03:07Z). **It needs the owner's OK for RELEASE.json (and the F1 ledger release) before 10-16.** Ask the owner.

## 4. Next days: DEADLINE TABLE (UTC)

Copied row for row from the DEADLINE TABLE in `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md`, with `#735` read as `#817` (the shadow was restarted twice), the 10-16 00:43Z row changed to name `b7ddeb6` and the `readlink` check (installed 10-13), and the 10-13 09:17Z row expanded from the manager notes. The tipauth reads are in the second table.

| Instant | Item | One-line action |
| --- | --- | --- |
| 10-12 (today) | 2 / finding 6 | Check a 10-12 A3 run exists in `daily.jsonl`; run the monitor today if not (two consecutive unevaluated days = halt) |
| daily 06:41Z through 11-06 | 2, 8 | A3 structure monitor job (research-0, 300 MB, 20 min); read `halt.flags` one by one |
| 10-13 09:17Z | — | "EXP-022 readiness" one-shot (no command written). Newer, from the manager notes: gaps-day resolves the open specs (FINAL job out-dir, the 4 FINAL_WRITTEN marker paths, (e') spec, exporter spec, EXP-024 Look 1 chain and pre-read amendment draft, EXP-025 run-or-record, EXP-022 P4 read tool builder); walk 2 runs regardless (EXP-022 Am.2 item 3) |
| 10-14 10:07Z (old slot) | 1 | (e′) dry-run `merge --dry-run` on the ledgered V-map snapshots |
| 10-14 11:23Z | 10 | Verify / extend #382 (own limit ~10-15T08:10Z before the +48 h) and #433 |
| 10-14 12:41Z | — | "probe rent audit" one-shot (no command written) |
| 10-15 06:41Z | 2 | **P2 run**: all six flags for EXP-022; five for H5 |
| 10-15 07:17Z | 2 | Read P2; if withdrawn decide walk 2 (G2.1), keep exporter + markers; record the withdrawal |
| 10-15 ~20:00Z | 10 | #433 near 3.0M credits; cap 3.6M binds (owner OK on record) |
| 10-15 21:37Z | 3 | Verify #817 start record `cap_pick_oracle 1 True`; `mkdir -p ~/data/cap-pick-oracle`; submit the one exporter job; extend its limit |
| **10-15 23:00Z** | 1 | **Hard deadline** (e′): pass recorded in DEC-016, or the failure and its counts recorded |
| 10-16 00:00Z | 6 | Last instant for an A11 DEC-016 amendment or a slot-span bound amendment (none planned) |
| 10-16 00:23–00:30Z | 4 | Submit walk 2 (`bash scripts/research/forward-walk2.sh`, `{"start":"2026-10-16T01"}`, resumable, 10080 min, ~6 GB); needs a free Helius slot |
| 10-16 00:43Z | 5 | Reinstall gate: confirm b7ddeb6 (f873aac plus the pre-send commit, installed 10-13) is the live build (`readlink /usr/local/lib/mal-h5-exec/current`) |
| **10-16 01:00Z** | 4, 5 | Walk 2 must have started (P3); seal window opens; every H5 buy refused until markers + exporter |
| 10-16 ~02:00–02:10Z | 6 | FINAL: `score` then `report` (no `--strict-lines`); reported compromised |
| right after the FINAL row, ≥ 02:00Z | 6 | Markers: h5 (`sudo touch`), job-user `cap-pick-oracle`, c1nf (decide), research-0 `/data/mal/exp012-forward/FINAL_WRITTEN` (decide); record `date -u` in LAB_STATE |
| 10-16 ~03:00Z | 5, 6 | Check both markers, `picks.jsonl` 644, oracle `fresh: true`; #433 closes: credits + completion amendment; DEC-016 Am.9 Log line for walk 2's gitRef and blobs |
| 10-16 06:13Z | 7 | E1 job (pinned command, new out-dir with no forbidden path fragment); n ≥ 20 |
| 10-16 06:41Z | 8 | A3 run dated 10-16 (Look 1 condition (f)) |
| 10-16 ~07:00Z → | 8 | EXP-024 chain: join → vmap ev → extract → classify → p5 → account → p7 → boost-pda → precount → e1 copy → amendment merged → `look --look 1` **[VOID 2026-10-13: EXP-024 ended NOT_DECIDABLE; record the one line of section 1.4 instead.]** |
| 10-17 02:00Z → ~03:00Z | 9a | EXP-025 Look 1 tools may start (R1 cross-check, P7, driver, `ready`, `run`) |
| **10-17 12:00Z** | 8 | **Hard deadline** EXP-024 Look 1 (after it: NOT_DECIDABLE, window and α_1 spent) **[VOID 2026-10-13: EXP-024 ended NOT_DECIDABLE; record the one line of section 1.4 instead.]** |
| 10-17 ~22:00Z | 10 | C1-NF monitor #662 limit; chain a successor or stop |
| **10-18 12:00Z** | 9a | **Hard deadline** EXP-025 Look 1 (a `run` after it locks and writes `not_decidable` R8) |
| 10-18 12:07Z | 10 | Renew the recurring session crons (7-day expiry) |
| 10-21 12:07Z | 10 | Extend #817, the exporter **and walk 2** |
| ~10-23 00:30Z | 10 | Walk 2's first 7-day limit ends — must already be extended or resubmitted |
| **10-23 01:00Z** | 9b | EXP-022 look 1 data end; needs P4 read tool merged (not built), E1 amendment, A2, hours through 10-23T01 sealed |
| 10-23 19:29:53Z | 5, 10 | H5 `end_ms`: no new buys; delete the `21 0` and `47 …` crons; shadow restart window (14-day limit ~10-25T07Z) |
| 10-24 ~03:00Z / 10-26 12:00Z | 9a | EXP-025 Look 2 earliest / deadline (only if Look 1 did not pass) |
| 10-30 01:00Z | 9b | EXP-022 look 2 data end = last instant for a late look 1 |

Added since that file (tipauth, shadow job #817, read from T0 = 1791797643640 = 2026-10-12T09:34:03Z; run rules in the next subsection):

| Instant | Item | Action |
| --- | --- | --- |
| 10-12 15:34:03Z (+6 h, 1791819243640) | tipauth | Interim read. **Done** (jobs #827, #828, #830; "tipauth reads so far" below). No further run at this instant |
| 10-13 09:34:03Z (+24 h, 1791884043640) | tipauth | First instant that can be the deciding read |
| 10-13 21:34:03Z (+36 h, 1791927243640) | tipauth | Interim read. **Done** (jobs #944, #945, #946, #947; "tipauth reads so far" below). Not the deciding read |
| 10-14 09:34:03Z (+48 h, 1791970443640) | tipauth | Read; then every 12 h |
| (old: 10-13 ~07Z) | union rss | The old "+48 h union rss re-read" targeted #735, which is cancelled. The rss rule (1.0 GB) is checked at the fixed reads above |

Seal-window drafts for each gap: `/data/mal/hunt-1008/ops/seal-window-drafts/INDEX.md`. It lists 9 manager decisions and 11 document disagreements, to be put to one quant-proof ruling at the 10-13 09:17Z gaps-day.

Tipauth path (not a fixed date): after >=24 h and >=10 no-gap pv triggers with ws >=1 (expect ~48 h), the phase-2 PR, DEC-024 amendment, decision-change account, QP on the final head, Helm reinstall. Live ~10-15/16.

### tipauth acceptance: how the reads run

**Fixed instants** (T0 = 1791797643640, job #817; quant-proof ruling 4, PR #613 comment 6121547427): +6 h `1791819243640` (interim), +24 h `1791884043640`, +36 h `1791927243640`, +48 h `1791970443640`, then every 12 h. No other runs.

**Common arguments, every tool:** `--since-ms 1791797643640 --expect-code-sha 5db78a663a0617527daacb22f5ce6c1a7e92dfa3`. Add `--until-ms <the instant>`. Verify each tool's sha256 with `sha256sum` before a run (item-4 `8de7f91af873f651d2ed065deaa1f77eebf333f4f7250d1c7f779cfb2d032c65`, census `b5ea6a29bec0d4d23f99f9b421c9e63e1e8bff10f761deafd24dbdfd0b63101b`, trigger print `186275b1858a99ac4465d1eccea5943aae96c0d7a48f295db95fe7cc6a39ee58`). Run as fast-0 ops jobs at git_ref main, outputs under `/home/claude/data/h5-daily/tipauth/<until_ms>/` (never the job's cwd).

**Where each tool's run rule lives:**
- Item-4 count (`tools/h5_tipauth_item4.py`, #613): PR #613 comments 6121865997 and 6121945678. Exit 0 is not a verdict; read `verdict.tool_rules_clear`.
- RPC census (`tools/h5_tipauth_census.py`, #615): PR #615 comment 6122101184, including "check R" before any sentence.
- Trigger print (`tools/h5_tipauth_trigger_print.py`, #616): PR #616 comment 6122332650 section 5. It requires a seal line in LAB_STATE with the first run (tool sha256, until-ms, output sha256). If any file has matches != lines_parsed, or bad or partial lines, it goes to quant-proof before quoting.

**How to launch the tools (learned at +6 h).** Run each as a `mal-fast-0` ops job that writes nothing in its working directory, with `nice -n 19 ionice -c 3`, after checking the tool sha256 and 0 porcelain lines in the job itself. Item 4: `/usr/bin/python3 -I tools/h5_tipauth_item4.py ...` at `git_ref` main. Trigger print: `/usr/bin/python3 -m tools.h5_tipauth_trigger_print ...` from the checkout root at `git_ref` 7a90d027f70ec445bf32d5ce8af2fb6b45bfc3eb, **without `-I`** (with `-I` the import fails; job #829). Run `--help` first inside the job so a launcher failure exits before the run. A second launcher failure at one instant leaves no repeat; the read then moves to the next instant (quant-proof, PR #610 comment 6127657282). Job logs show about 3,700 characters, so read the out files back with small jobs that print chosen keys.

**Peeking rule.** Read only at the fixed instants. The exactly-0 rules are cumulative across reads. The share rules apply at the deciding read only. The tools change only by tightening after the first run, never loosening.

**Wording rule.** Counts only. An interim read is labelled "interim, not the deciding read". Never write "met", "clear", "passing" or "on track". Never state "phantom" or "not on chain" as a fact: say the public RPC did not return the signatures (asked twice, at least 10 minutes apart) and the controls were returned, which is consistent with not being on the finalized chain and is not proof. Never write "profitable".

### tipauth reads so far

**+6 h, 2026-10-12, INTERIM, not the deciding read** (`--until-ms 1791819243640`). The full record with paths, hashes and every count is in LAB_STATE under "2026-10-12 events"; PR #610 comment 6127603546; notebook n_L_X6dM9QjKBxDQ.

- Item 4 (job #828): covered 5.9979 h; triggers 11 (pv 6, fv 5); closes 180; no-gap pv triggers with ws >= 1: 0; would-pass 0; consistency failing 0; item 5 `n_ok` 60, share 0.233333 (short: no result below 100).
- Trigger print (job #830 after the failed launch #829): ws_pass n 5, equal 4, different 0, **absent 1**; would_pass n 0; refused n 1, equal 1. Scan check: matches equal lines_parsed in every file; bad, partial and unreadable lines 0.
- **Owner flag: 1 of 5 ws-pass trigger prints has no row on the tip tape.** Cause not determined (four possible causes, listed in LAB_STATE). The absent count is cumulative from T0 and is carried by every later read. Told to the owner on 10-12 at about 15:45Z.
- **Quant-proof ruling, PR #610 comment 6127657282.** No DEC-024 rule requires STOP or HALT; the canary continues at T0 on the manager's call. The owner wording to use is in section 2 of that comment (item 5 only in its own sentence, never next to a cause; no percentage; no "phantom", "ghost", "not on chain", "verified", "likely", "isolated"). Nothing more is looked at for that trigger until the follow-up tool is merged: `tools/h5_wspass_print_rpc.py` (spec in section 3: public RPC `getTransaction` at finalized on every ws-pass and refused trigger-print signature through the census tool's `ask` / `reask`, counts per class only, reading rule fixed in that section's "Reading rule, fixed now" paragraph; needs the Reviewer's verdict and QP OK on the exact head, and its LAB_STATE line in the same PR). A T1 proposal must carry the count, its interim label and the undetermined cause; quant-proof recommends the RPC check before proposing T1. Proposed amendment, not in force: STOP when a ws-pass trigger print on which the executor sent a buy is absent at a scheduled read and not returned by the RPC check.
- **The RPC check ran on the +6 h read (10-12, 16:04Z to 16:40Z), interim, not the deciding read: `reading` "no conclusion".** Tool `tools/h5_wspass_print_rpc.py` (PR #619, merged as `8960574`; sha256 `5a514c40197bcbed8ed71da4c3ceba151940fbea8305bc2bedfd0d73aea1ce42`). ws_pass asked 5, returned 2, not_returned 0, error 3; refused asked 1, returned 1; three reask rounds; report run once (jobs #838 to #843; full record in LAB_STATE; PR #610 comment 6128539499; notebook n_Eah_Z11EieReUQ). The error class is not known: the report does not print it and the state file must not be opened. The check is finished for this read (rounds used up, plan never re-made).
- **How to run it at a later read** (only if `ws_pass.absent` rose at that scheduled read; PR #619 comment 6127897922 section D): `mal-fast-0` ops jobs, low priority, one after the other, at `git_ref` 89605742d65478b45ba69552129856a2ae907f52 or later main with the same tool sha256, `/usr/bin/python3 -m tools.h5_wspass_print_rpc <plan|ask|reask|report> ...` without `-I`, a new out-dir `/home/claude/data/h5-daily/tipauth/wspass-rpc-<until_ms>`, reask at least 600,000 ms after the last ask until it prints calls 0 or 3 rounds ran, report once to `wspass_report.json`, then checks R1 to R5. A read with 2 or more not_returned gets "no conclusion" from the tool and needs a new declared rule before its ask (that rule is written in PR #610 comment 6156017843 section 1 and is declared only by the tool PR it names; until that PR is merged there is no `plan` and no `ask`). If a later scheduled read's absent count has risen again before `plan` runs, plan on that read's until-ms instead; one plan either way. Never: a second plan, a by-hand call, opening the state or log, a join to the tip tape or the executor ledger.
- **Owed after this run:** a follow-up tool PR (error-class counts per class in the report; report with exclusive create; `state_sha256`, `first_asked_ms`, `last_asked_ms`) with the Reviewer's verdict and QP OK, and a quant-proof ruling on whether any further ask is allowed for the +6 h read.

**+36 h, 2026-10-13, INTERIM, not the deciding read** (`--until-ms 1791927243640`). The full record is in LAB_STATE (bullet written 2026-10-13T21:58:39Z); PR #610 comments 6155782559 and 6155883949; notebooks `n_PvMTlrEUE9__Hg` and `n_qCyubo4btM9WYA`.

- Item 4 (job #945): covered 35.9902 h; duration line False with n 5 (needs 10); triggers 68 (pv 36, fv 32); closes 1132; would-pass 1; consistency failing 0; `tip_incompleteness_le_5pct` False (`n_ok` 295, share 0.220339). Blocking lines: the duration line and `tip_incompleteness_le_5pct`.
- Health (job #944): tip ok on 2,160 of 2,161 heartbeats, `errors_logged` max 0.
- Trigger print (job #946; log read by #947): ws_pass n 15, equal 13, different 0, **absent 2**; would_pass n 1, equal 1; refused n 20, equal 17, absent 3. Scan check: matches equal lines_parsed on 35 of 35 lines; bad and partial lines 0.
- **Owner flag: the cumulative ws-pass absent count is 2 of 15** (1 of 5 at +6 h, 1 of 8 at +24 h). Cause not determined. Told to the owner on 2026-10-13. **Quant-proof's ruling: PR #610 comment 6156017843. Read it before any follow-up.** No `plan` with the RPC tool at sha256 `5a514c40…ce42` on this read: one tool PR first (new reading rule S0 / S1 / S2, `--tape-absent-*` plan flags, error classes, write-once report, `errclass`), both verdicts, merge, one `errclass` run on `/home/claude/data/h5-daily/tipauth/wspass-rpc-1791819243640` (`state_sha256` must be `8444d05d0cddedde742b260c5c6c3ebce7faa0dbe0da0289a800a4779add3804`), counts posted, one short ruling, then `plan` on 1791927243640 (or on a later read's until-ms if its ws-pass absent count is above 2 before `plan` runs). The canary continues at T0 on the manager's call. Then `tools/h5_wspass_sent_count.py` (spec in section 2) and the stricter stop amendment (text in section 2; its own DEC-024 PR; the owner is told). The tape is "our own block tape (every block, read at confirmed commitment)"; the word "finalized" for it, used at +6 h, was an error and is corrected. Owner wording for this read: section 4 of the comment, verbatim.
- **Item 5 at three reads (job #955, printed values):** `incomplete_n` 14 of 60, 36 of 192, 65 of 295. Judged only at the deciding read; a False there is final for this origin (ruling section 3). The owner text is section 3 (b), verbatim. A count-only diagnostic tool is allowed under the four conditions of section 3 (d).
- **Before the +48 h read:** the health job also prints the `t_ms` and status of the one heartbeat that was not ok; the trigger print is one launch without `-I`; nothing is restarted.
- **Decision-change tool, first run, "interim, not the account"** (job #952; out `/home/claude/data/h5-daily/tipauth-decision-change/interim-1791927243640.json`, sha256 `d69f906a…2eaf`): 68 records, 0 sealed excluded; installed parser against the head with the path off: 0 of 68 differ; with the path on: `a_passes` 18, `c_passes` 19, `refusals_to_passes` 1, 15 of the 16 websocket refusals still refused (`tipauth:gap` 11, `tipauth:dropped_first_slot` 2, `tipauth:value_not_zero` 2), `passes_to_refusals` 0, `other_changes` 0; agreement with item 4: 1 and 1, remainder 0; seven booleans true (necessary; not "met" before quant-proof's check). The account is the run at the deciding read's instant. Run it only under the 13 rules (`/data/mal/hunt-1008/ops/tipauth-decision-change-run-rules.md`): hash-only job on the installed file first, the item-4 read of the same instant saved first, interpreter `/home/claude/venvs/tipfollower-test/bin/python` on `mal-fast-0`, the command of job #952 with the new instant and a new `--out`.

**Evening of 2026-10-13 (after the +36 h reads).** The full record is in LAB_STATE (bullet written 2026-10-13T23:31:22Z, and the sub-bullets under the +6 h RPC-check bullet and under the diagnostic's declaration).

- **Merged:** DEC-024 Amendment 9 (PR #640, `c24b935`; cannot fire until `tools/h5_wspass_sent_count.py` exists, which is not built); the RPC check tool with the new reading rule (PR #641, `4bd65b0`, sha256 `90ab40bd…5369`); the item-5 and gap-record diagnostic (PR #642, `f42a446`, sha256 `282d6dde…4703`).
- **RPC check on the +36 h read: `reading` "no conclusion"** (jobs #965, #967, #969, #970, #971, #972). ws-pass asked 15: returned 10, not_returned 0, error 5; refused asked 20: returned 1, error 19; would-pass asked 1: error 1. Final error class `HTTPError` in every class; `http_status` 429 and 413 on most answers; `rpc_code` -32015 on 8 ws-pass answers. The check on this until-ms is finished: no further ask, no second plan, no by-hand call, nothing further until quant-proof rules on the counts. Run rule and rulings: `/data/mal/hunt-1008/ops/wspass-rpc-run-rules-1013.md`.
- **Diagnostic, first run at +36 h** (job #962; PR #610 comment 6156947862): both tables' totals equal item 4's. The second run is at the +48 h instant, only after its item-4 read and the named-keys job; the flags come from that job's printed values; run rules R1 to R12 in `/data/mal/hunt-1008/ops/tipauth-item5-diag-run-rules-1013.md` (fixed out name `/home/claude/data/h5-daily/tipauth-item5-diag/diag-<until_ms>.json`; what may be said is R10, what may not is R11; R12 compares the two files).
- **Floor-simulator judge:** the FLOOR-OPTION v2 exploration figure stands with sells capped at the pool's real quote (`/data/mal/hunt-1008/haiku-trader-v2/stage0/floor-judge/JUDGE.md`).

### Phase 2 still to build (none of it is done)

- PR #614 round 2: reviewer comment 6121596920 (rename `_tipauth_latch` so it does not match the HALT-name regex; merge main) and quant-proof comment 6121641942, R1-R12. The cross-test spec is in PR #613 comment 6121865997 section 3 (the watcher imports `would_pass`; one shared predicate).
- The watcher tool `tools/h5_tipauth_watch.py` (spec in comment 6121641942, 2(c)).
- The look-comparison tool (2(b) in the same comment).
- The decision-change account tool.
- DEC-024 Amendment 8 (skeleton in comment 6121641942). Section 3 needs the owner's dated acceptance; see the open question below.
- Helm's pinned reinstall of the executor.

**Owner question `q_o1f3-Qvs1HbwYw`, CLOSED 10-12** (asked 2026-10-12 09:39 UTC, key DEC-024-Am8; the owner's answer is in the owner decisions list above): the owner's conditional pre-approval ("so long as trade quality isn't affected, and everything's still profitable") cannot be certified. The amendment needs the owner's dated acceptance of bounded-risk terms instead (0.02 SOL class stake, -0.04 SOL latch, about 0.06 SOL worst case, looks at 25, 50 and 100, n=1 switch-offs). The answer goes into Amendment 8 section 3 verbatim and dated. Include it in the morning update. (answer received; Amendment 8 text owed)

## 5. Owed (as of 2026-10-13T09:48:28Z; replaces the 10-12 list)

1. **This PR's verdicts and merge** (resume record: reviewer and quant-proof on the exact head), then `git -C /home/claude/MAL pull --ff-only` on mal-fast-0 as a job, then remove the worktree.
2. **Watch the open owner question `q_hw6mloW_n2goPQ`** (section 1.1).
3. **Today:** 11:47Z and 17:47Z running checks; 12:17Z daily check (section 1.6); 18:33Z T1-block check (done: jobs #936 and #938, see section 1.1); the PR #610 line for the +24 h tipauth reads once job #926 has ended; 21:37Z tipauth +36 h read; 23:47Z running check.
4. **2026-10-14:** 00:16Z and 00:21Z; 03:23Z tape archive; 06:41Z A3 monitor, then the first `boost_slice_watch` reading and daily note (section 1.3); the quant-proof session of section 1.4; 10:07Z (e') dry run (spec in `GAPS-DAY-1013.md` item 4); 11:23Z walkers verify.
5. **Before 2026-10-15T06:41Z:** the EXP-022 P2 declaration (section 1.4). **Before 2026-10-16T02Z:** the F1 ledger release sentence PR.
6. **Records nits for the next records PR** (non-blocking, from the reviews of 10-13): the question file `a3-repin-2026-10-13-owner-question.md` says "four changed lines" where a reader counts five items (label and sentence 1 share a line); the DEC-024 halt record's item 6 bullet has two parentheses in a row; the Console entry `fy-20261013-h5-owner-decided` is stamped 08:50:52Z and names later instants.
7. **Still owed from before 10-13:** DEC-024 Amendment 8 text with the owner's words ("Okay, for the amendment I agree, let's go with yes on those terms."); the sent-count tool that DEC-024 Amendment 9 needs (`tools/h5_wspass_sent_count.py`, spec in PR #610 comment 6156017843 section 2; Amendment 9 itself merged on 2026-10-13 as `c24b935`, and the wspass tool follow-up merged as PR #641, `4bd65b0`); PR #614 round 2; the watcher, look-comparison and decision-change tools; a later Helm install for the tipauth gate (branch cut from the installed sha `b7ddeb6`).
8. **research-0 watchdogs** (disk, CPU guard, report sync) were background loops of an older session: check they exist (`pgrep -af 'df --output=pcent'`, `pgrep -af loadavg`) and restart them if not.
9. **Housekeeping:** builder worktrees under `.claude/worktrees/` (agent-a41b766f769336bdc, agent-aae4ea1fc5a569f2f, agent-a7f42f3892d094b7f) can be removed; never delete the two install branches. Record every mid-week shadow or runner restart in LAB_STATE.

## 6. Rules that bite

- **The 10-16 FINAL** runs as written at about 10-16T02Z. It is reported compromised, and `exp012_forward` runs without `--strict-lines`. The (e') dry run is due before 10-15T23Z.
- **EXP-022 seal (section 9).** No outcome of a counted CAP-PICK hour is opened, priced or printed from any source except the sealed look. From 10-16T01, H5 (live and shadow) must not trade or price CAP-PICK picks: the pick_oracle reads only a boolean after the FINAL and fails closed. Before 10-16 the runner is read only through `runner_timing_read.py`; a breach means the read is reported compromised.
- **EXP-024 declared observation.** Canary and shadow outcomes in Look 1's window [10-10, 10-16) are watched live. Look 1 is read and reported as written. **[2026-10-13: void for EXP-024. Its read ended NOT_DECIDABLE; no look is run; see section 1.4.]**
- **Gate changes need the owner's dated decision.** Any later change to H5's trade filter is judged on mechanism and counts only (DEC-024 note, #600). A pre-approval does not execute itself.
- **Pinned exit gates.** Post QP OK on the exact head and a Reviewer verdict ON THE PR before every merge, workflow verdicts too. Merge in a SEPARATE call with `--match-head-commit`; never `; gh pr merge` after a checkout or edit.
- **Check jobs write nothing into the job's working directory** and run at a git ref different from the shadow's tree. A stray `st.out` from a flat-check job forced the second restart on 10-12.
- **Agents never checkout or switch branches in `/home/claude/.miscusi/repos/vaanai/MAL`.** Use `git worktree add` in a scratch dir (a reviewer's `git checkout` there once left it detached). Check `git status` before any pull or merge.
- **Shadow restart procedure.** Executor flat first (open 0, pending 0 via the `--status` job), cancel the old job, resubmit its exact command as a new MiScusi job (clean checkout, 0 porcelain lines, then extend the limit to 13 d), check the start record fields, write the LAB_STATE row.
- **Hunts use exploration tape only.** Sealed blocks fresh-0802, fresh-0808 and fresh-0828 are never read.
- **Reserve convention:** PumpSwap rows are PRE-trade with V; bonding rows are POST-trade.
- **Jobs run under `sh`.** No bash-isms in job commands; name `bash` explicitly where a script needs it.
- **Memory on `mal-fast-0`:** one heavy replay at a time. MiScusi refuses jobs of 2 GB or more, or 2 CPUs or more, there.
- **Subagents.** Builders stop at about 40 turns and reviewers at about 20; resume them with SendMessage. Builder agents cannot call MiScusi tools, so the manager submits jobs. Reviewers use their own scratch worktree. Agent types `reviewer` and `quant-proof` can return empty inside workflows; use default agents with a tool budget and a forced verdict.
- **Never write a realized P&L number** in a status line. Check drop-best-date totals before telling the owner anything is positive.
- **Never print or commit** keys, the OpenRouter key, or the DB password. Do not touch `ufw`, `sshd`, cloudflared, or `/opt/miscusi`.
