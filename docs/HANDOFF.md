# Manager handoff 2026-10-12 ~10:30Z (manager10 -> next manager)

Replace this page at the next handoff; don't append. Read it first. Sources for every line are named in the records PR body. Nothing here is an edge claim, and no realized P&L number is written here on purpose.

Then read, as needed:
- **Seal-window steps and GAPS:** `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md` (its DEADLINE TABLE is copied below).
- **Session crons (all prompts on disk):** `/data/mal/hunt-1008/ops/recurring-crons-1011.json` and `/data/mal/hunt-1008/ops/oneshot-crons-1012.json`.
- **H5:** `/data/mal/hunt-1008/h5-work/PLAN.md`, [DEC/DEC-024-h5-live-canary.md](../DEC/DEC-024-h5-live-canary.md), [EXP/EXP-024-h5-boostfloor-part1-prereg.md](../EXP/EXP-024-h5-boostfloor-part1-prereg.md).
- **EXP-022 CAP-PICK Part 1:** [EXP/EXP-022-cap-pick-part1-prereg.md](../EXP/EXP-022-cap-pick-part1-prereg.md).
- **Tipauth count list and C1-C6:** PR #610 comment 6121123397 (QP OK, copyable block).

## 1. Where things stand (2026-10-12 ~10:30Z; H5 halt paragraph 18:41Z)

**H5 halted itself and the manager cleared the latch on 10-12.** The live executor latched `out_of_rule_entry` at 2026-10-12T18:28:02.667Z on a buy that landed 29 slots (about 6.3 s) after its trigger; the limit is 5 s, and the trigger reached the detector 5.4 s late. It sold at once. The manager cleared the latch at 18:38:41.306Z after checking nothing was open. No rule, config or code changed; tier stays T0. Owed: a reviewed pre-send age check (Helm's reinstall). If `out_of_rule_entry` latches again before that fix is installed, H5 stays halted. Full record: DEC-024 "Halt record: executor latch `out_of_rule_entry`, 2026-10-12, and the manager's clear"; LAB_STATE "2026-10-12 events (manager10)". H5 has not cleared the promotion gate.

**H5 is live at T0** (0.02 SOL per trade). As of 09:06Z 10-12: 7 attempts, 7 sells landed, no STOP/HALT (status at 18:35Z: attempts 11). 20 landed buys are needed before T1, and the step also needs the DEC-024 Am.3 item 2 conditions, quoted below: "If either mean is negative at 20 landed buys, there is no step. The manager reports, and only a new dated line from the owner can step anyway." End `2026-10-23T19:29:53Z`. Enforce loop job #729, shadow job #817.

**Research-0 froze and rebooted on 10-12.** It froze about 06:11Z and was rebooted at 07:29Z. manager10 took over from manager9. Every session cron died. `mal-fast-0` (H5 executor, shadow, enforce loop) was not affected. Walkers #382 and #433 auto-resumed. The crons were recreated as on-disk pointer prompts (section 2).

**The H5 trade-rate fix is the critical path, and it is not done.**
1. The union field (second feed) was read at +24 h and recovers nothing. Cross-tab, 24 h of unsealed pv triggers (PR #596 comment 6120296558):

   | pv triggers | ws 0, union 0 | ws >=1, union 0 | ws >=1, union >=1 |
   | --- | ---: | ---: | ---: |
   | no gap flag | 3 | 0 | 6 |
   | gap flag (refused as `feed_gap`) | 1 | 1 | 0 |

   Cause: the public websocket delivers swap notices for transactions that are not on the finalized chain. Of the websocket-only prints sampled with the public RPC: 46 not found, 3 found, 1 error; the 46 come from two samples that may overlap, so read it as at least 28 distinct signatures not found. Controls (prints that are on the tip): 18 found, 0 not found, 2 errors. A union can only add rows, so no phase 2 on the union field.
2. So PR #610 added a report-only field, `base_breaks_unresolved_settled_tipauth` (tip-authoritative check: websocket-only prints are left out where the tip tape covers the slots). **Merged as `5db78a663a0617527daacb22f5ce6c1a7e92dfa3` at head `3102581` after three rounds.** Verdicts on the PR: Reviewer OK 6121009783; md5 job #807 PASS 6121076394 (pv-trigger md5 `75cb0b0c585bc2479137cae31330e73e`, 72/72); QP OK 6121123397.
3. **The field is report-only.** No trigger, gate or executor path reads it.
4. **Shadow restarted twice on 10-12.** The first restart (job #810, 09:06:45Z) was replaced because its first log line read `porcelain_lines=1`. The second, **job #817 at 2026-10-12T09:34:03Z (T0 = 1791797643640)**, is the item-4 clock start (LAB_STATE rows; PR #610 comment 6121610196): at least 24 h AND at least 10 no-gap pv triggers with ws >=1.

**What phase 2 still needs (none of it is done):**
- the item-4 count (the three read tools are merged; the reads run at the fixed instants in "tipauth acceptance: how the reads run" below; the +6 h interim read ran on 10-12, see "tipauth reads so far" below),
- a quant-proof review of that count,
- an executor gate PR under conditions C1-C6, tagged `gate_path: "tipauth"`, with the switch-off rule (QP ruling D),
- a DEC-024 amendment (Amendment 8),
- Helm's pinned reinstall of the executor.

The full list is under "Phase 2 still to build" in section 4.

Realistic live date is about 10-15 or 10-16 (the owner was told ~10-15/16, not 10-14). **No claim that it adds trades or profit.** The manager cannot certify "profitable": H5 is not through its gate.

**Owner decisions still binding from the old handoff (10-11; quoted from the old HANDOFF on main and DEC-024):**
- v2 pre-authorization: "v2: paper now (DEC-028 A), with live v2 at 0.02 pre-authorized if v1 halts on BOOST timing. Both §3.1 sentences were confirmed at 05:26:36Z." Not self-executing. Conditions are in DEC-028.
- Option B prerequisite: "**Before Option B:** quit the loop and delete the `--place-stop` crons (DEC-028 §4)." The loop is enforce loop #729.
- Second strike: "A completed UTC day from 10-11 on below 337 s (executor, or plain over ≥ 30) halts. The 00:21Z day check enforces this with `--place-stop`."
- Scale ladder: "T0 0.02 → ~50 trades → T1 0.10 → ~25 trades → T2 0.30 → onward, 25–50 trades per step. No skipped steps." Later DEC-024 Am.3 puts the T0 to T1 step at 20 landed buys with the owner's top-up to about 1 SOL.
- DEC-024 Am.3 item 2, "The step to T1 requires all of these at that point": "the landing p50 is at most 3.0 s over those 20 landed buys (section 5, item 3)"; "no live-halt rule and no stop that ends the canary has fired and is unresolved"; "the canary's realized mean per closed trade, after fees, is not below zero"; "the shadow twin's mean per trade over the same trades is not below zero"; "if EXP-024 Look 1 has been read, its deciding-cell flat and pressure means are not below zero (section 7)". Then: "If either mean is negative at 20 landed buys, there is no step. The manager reports, and only a new dated line from the owner can step anyway."
- DEC-024 Am.7 item 1, on a Look 1 FAIL: "new buys do **not** stop at the next 00:00Z, and the canary continues on these terms: **T0 only, 0.02 SOL per entry.** T1 stays blocked: Amendment 3's step-up is not taken, and the +0.5 SOL top-up is not sent." Through 2026-10-23T19:29:53Z only, with every section 5 halt live. "Look 1 FAIL keeps T0 for execution measurement (DEC-024 Am.7, #595)."
- October goal: "$1000 of REAL income by 10-31, with $400 as the minimum." This is the owner's target, not evidence.
- Jobs, as of the old handoff (not re-verified now): #391 "T2 B90 full run (#471), 5 configs", "Report-only. Read the picks P2–P4 scopes against the §7 kill rule in `ARTIFACTS/lab/cap-pick-t2-boost-exit-predeclare-2026-10-08.md`". #394 "OpenRouter arm of the LLM trader", "**Done, dead** (notebook n_o6OGSKeiRBB5Rg)", "The LLM-trader line is closed."

**Owner decisions on record 10-12:**
- **Closed 10-12 (about 18:19Z):** Amendment 8 terms, "Okay, for the amendment I agree, let's go with yes on those terms." The amendment is owed. F1 release by domain, "For two, I agree, research is very very important, arguable the most important right now." The ledger release sentence is owed. Quant-proof's stop rule (PR #610 comment 6127657282, section 4), "For 3, I guess go for it, thats a decision you can make if you think it's going to get us to profit faster, or if it's the right thing to do." It is not in force until the DEC-024 amendment and the reviewed sent/not-sent join exist.
- **Conditional pre-approval of the H5 trade-rate fix** (notebook n_RI3UsY-cJ_mGAA): "so long as trade quality isn't affected, and everything's still profitable". It is NOT self-executing. The DEC-024 amendment must quote the question and answer verbatim and restate the conditions, and the owner's dated decision still counts for any trade-filter change (DEC-024 note, #600).
- **C1-NF canary shelved.** Soak #658 and monitor #662 continue as a regime watch. Revive at >=10 kept trades per day on 2 days.
- Focus until 10-16 is H5 plus light research. Intensive research after.
- Haiku 5.5 (never 4.5) for simple delegated work. Verify its numbers against sources before quoting; strong models for QP, reviewer, live-money and P&L confirmation.
- November goal about $200/day (the owner's target, not evidence).

## 2. What is running

| Job / unit | Where | What |
| --- | --- | --- |
| H5 live executor (Helm-installed systemd unit, `LoadCredential`) | `mal-fast-0` | T0, 0.02 SOL, max 2 open. Not a MiScusi job. Reinstall gate on 10-16 00:43Z: confirm `f873aac3a07b52ba03c0c13db675051a88e21b38` is the live build (`--status`). |
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
- F1f quote-coin Step 1: loader frozen 08:31:41Z, the real run is at the FINAL (one-shot 10-16 03:07Z). **It needs the owner's OK for RELEASE.json (and the F1 ledger release) before 10-16.** Ask the owner.

## 4. Next days: DEADLINE TABLE (UTC)

Copied row for row from the DEADLINE TABLE in `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md`, with `#735` read as `#817` (the shadow was restarted twice) and the 10-13 09:17Z row expanded from the manager notes. The tipauth reads are in the second table.

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
| 10-16 00:43Z | 5 | Reinstall gate: confirm f873aac is the live build (`--status`) |
| **10-16 01:00Z** | 4, 5 | Walk 2 must have started (P3); seal window opens; every H5 buy refused until markers + exporter |
| 10-16 ~02:00–02:10Z | 6 | FINAL: `score` then `report` (no `--strict-lines`); reported compromised |
| right after the FINAL row, ≥ 02:00Z | 6 | Markers: h5 (`sudo touch`), job-user `cap-pick-oracle`, c1nf (decide), research-0 `/data/mal/exp012-forward/FINAL_WRITTEN` (decide); record `date -u` in LAB_STATE |
| 10-16 ~03:00Z | 5, 6 | Check both markers, `picks.jsonl` 644, oracle `fresh: true`; #433 closes: credits + completion amendment; DEC-016 Am.9 Log line for walk 2's gitRef and blobs |
| 10-16 06:13Z | 7 | E1 job (pinned command, new out-dir with no forbidden path fragment); n ≥ 20 |
| 10-16 06:41Z | 8 | A3 run dated 10-16 (Look 1 condition (f)) |
| 10-16 ~07:00Z → | 8 | EXP-024 chain: join → vmap ev → extract → classify → p5 → account → p7 → boost-pda → precount → e1 copy → amendment merged → `look --look 1` |
| 10-17 02:00Z → ~03:00Z | 9a | EXP-025 Look 1 tools may start (R1 cross-check, P7, driver, `ready`, `run`) |
| **10-17 12:00Z** | 8 | **Hard deadline** EXP-024 Look 1 (after it: NOT_DECIDABLE, window and α_1 spent) |
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
| 10-13 21:34:03Z (+36 h, 1791927243640) | tipauth | Read |
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
- **How to run it at a later read** (only if `ws_pass.absent` rose at that scheduled read; PR #619 comment 6127897922 section D): `mal-fast-0` ops jobs, low priority, one after the other, at `git_ref` 89605742d65478b45ba69552129856a2ae907f52 or later main with the same tool sha256, `/usr/bin/python3 -m tools.h5_wspass_print_rpc <plan|ask|reask|report> ...` without `-I`, a new out-dir `/home/claude/data/h5-daily/tipauth/wspass-rpc-<until_ms>`, reask at least 600,000 ms after the last ask until it prints calls 0 or 3 rounds ran, report once to `wspass_report.json`, then checks R1 to R5. A read with 2 or more not_returned gets "no conclusion" from the tool and needs a new declared rule before its ask. If a later scheduled read's absent count has risen again before `plan` runs, plan on that read's until-ms instead; one plan either way. Never: a second plan, a by-hand call, opening the state or log, a join to the tip tape or the executor ledger.
- **Owed after this run:** a follow-up tool PR (error-class counts per class in the report; report with exclusive create; `state_sha256`, `first_asked_ms`, `last_asked_ms`) with the Reviewer's verdict and QP OK, and a quant-proof ruling on whether any further ask is allowed for the +6 h read.

### Phase 2 still to build (none of it is done)

- PR #614 round 2: reviewer comment 6121596920 (rename `_tipauth_latch` so it does not match the HALT-name regex; merge main) and quant-proof comment 6121641942, R1-R12. The cross-test spec is in PR #613 comment 6121865997 section 3 (the watcher imports `would_pass`; one shared predicate).
- The watcher tool `tools/h5_tipauth_watch.py` (spec in comment 6121641942, 2(c)).
- The look-comparison tool (2(b) in the same comment).
- The decision-change account tool.
- DEC-024 Amendment 8 (skeleton in comment 6121641942). Section 3 needs the owner's dated acceptance; see the open question below.
- Helm's pinned reinstall of the executor.

**Owner question `q_o1f3-Qvs1HbwYw`, CLOSED 10-12** (asked 2026-10-12 09:39 UTC, key DEC-024-Am8; the owner's answer is in the owner decisions list above): the owner's conditional pre-approval ("so long as trade quality isn't affected, and everything's still profitable") cannot be certified. The amendment needs the owner's dated acceptance of bounded-risk terms instead (0.02 SOL class stake, -0.04 SOL latch, about 0.06 SOL worst case, looks at 25, 50 and 100, n=1 switch-offs). The answer goes into Amendment 8 section 3 verbatim and dated. Include it in the morning update. (answer received; Amendment 8 text owed)

## 5. Owed

- The count: tipauth reads at the fixed instants (section 4), then the item-4 count list (items 1-10 in comment 6121123397). C5 is three conditions: `orphaned_by_drop_n == 0`, `tipauth_nosettle == 0`, `dropped_first_slot > cover_lo`.
- Phase-2 package (section 1), including the owner's verbatim Q&A in the amendment and the switch-off rule for tipauth-only trades.
- Phase 2 still to build (section 4), and the open owner question `q_o1f3-Qvs1HbwYw` (decide by 2026-10-14T09:00Z).
- Owner: the F1f RELEASE.json OK before 10-16; a wake-up update (H5 status, fix timeline ~10-15/16 with Helm needed, what the searches found).
- Records PR (this one) then `git -C ~/MAL pull --ff-only` on fast-0 (as a job) so the Console shows it; copy any result.v1 record to `~/data/results/`.
- The research-0 watchdogs (disk, CPU guard, report sync) were background loops of the old session; verify they exist after the reboot (`pgrep -af 'df --output=pcent'`, `pgrep -af loadavg`) and restart if not.
- Record every future mid-week shadow or runner restart in LAB_STATE.

## 6. Rules that bite

- **The 10-16 FINAL** runs as written at about 10-16T02Z. It is reported compromised, and `exp012_forward` runs without `--strict-lines`. The (e') dry run is due before 10-15T23Z.
- **EXP-022 seal (section 9).** No outcome of a counted CAP-PICK hour is opened, priced or printed from any source except the sealed look. From 10-16T01, H5 (live and shadow) must not trade or price CAP-PICK picks: the pick_oracle reads only a boolean after the FINAL and fails closed. Before 10-16 the runner is read only through `runner_timing_read.py`; a breach means the read is reported compromised.
- **EXP-024 declared observation.** Canary and shadow outcomes in Look 1's window [10-10, 10-16) are watched live. Look 1 is read and reported as written.
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
