# Manager handoff 2026-10-12 ~09:15Z (manager10 -> next manager)

Replace this page at the next handoff; don't append. Read it first. Sources for every line are named in the records PR body. Nothing here is an edge claim, and no realized P&L number is written here on purpose.

Then read, as needed:
- **Seal-window steps and GAPS:** `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md` (its DEADLINE TABLE is copied below).
- **Session crons (all prompts on disk):** `/data/mal/hunt-1008/ops/recurring-crons-1011.json` and `/data/mal/hunt-1008/ops/oneshot-crons-1012.json`.
- **H5:** `/data/mal/hunt-1008/h5-work/PLAN.md`, [DEC/DEC-024-h5-live-canary.md](../DEC/DEC-024-h5-live-canary.md), [EXP/EXP-024-h5-boostfloor-part1-prereg.md](../EXP/EXP-024-h5-boostfloor-part1-prereg.md).
- **EXP-022 CAP-PICK Part 1:** [EXP/EXP-022-cap-pick-part1-prereg.md](../EXP/EXP-022-cap-pick-part1-prereg.md).
- **Tipauth count list and C1-C6:** PR #610 comment 6121123397 (QP OK, copyable block).

## 1. Where things stand (2026-10-12 ~09:15Z)

**H5 is live at T0** (0.02 SOL per trade). As of 09:06Z 10-12: 7 attempts, 7 sells landed, no STOP/HALT. 20 landed buys are needed before T1, and the step also needs the DEC-024 Am.3 item 2 conditions, quoted below: "If either mean is negative at 20 landed buys, there is no step. The manager reports, and only a new dated line from the owner can step anyway." End `2026-10-23T19:29:53Z`. Enforce loop job #729, shadow job #810.

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
4. **Shadow restarted 2026-10-12T09:06:45Z** (LAB_STATE row). The **item-4 clock starts at that instant**: at least 24 h AND at least 10 no-gap pv triggers with ws >=1.

**What phase 2 still needs (none of it is done):**
- the item-4 count (read command `/data/mal/hunt-1008/ops/tipauth-read.cmd`; reads at +1 h ~10:07Z, +6 h ~15:07Z, +24 h 10-13 ~09:07Z),
- a quant-proof review of that count,
- an executor gate PR under conditions C1-C6, tagged `gate_path: "tipauth"`, with the switch-off rule (QP ruling D),
- a DEC-024 amendment,
- Helm's pinned reinstall of the executor.

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
- **Conditional pre-approval of the H5 trade-rate fix** (notebook n_RI3UsY-cJ_mGAA): "so long as trade quality isn't affected, and everything's still profitable". It is NOT self-executing. The DEC-024 amendment must quote the question and answer verbatim and restate the conditions, and the owner's dated decision still counts for any trade-filter change (DEC-024 note, #600).
- **C1-NF canary shelved.** Soak #658 and monitor #662 continue as a regime watch. Revive at >=10 kept trades per day on 2 days.
- Focus until 10-16 is H5 plus light research. Intensive research after.
- Haiku 5.5 (never 4.5) for simple delegated work. Verify its numbers against sources before quoting; strong models for QP, reviewer, live-money and P&L confirmation.
- November goal about $200/day (the owner's target, not evidence).

## 2. What is running

| Job / unit | Where | What |
| --- | --- | --- |
| H5 live executor (Helm-installed systemd unit, `LoadCredential`) | `mal-fast-0` | T0, 0.02 SOL, max 2 open. Not a MiScusi job. Reinstall gate on 10-16 00:43Z: confirm `f873aac3a07b52ba03c0c13db675051a88e21b38` is the live build (`--status`). |
| #810 H5 shadow | `mal-fast-0`, 1.5 GB | Started 09:06:45Z on main `5db78a6` (shadow blob `918e76bc`). Replaced #735 (main b65e2ec, blob cb1deb4). Time limit 13 d. Feeds the executor through the `/srv/mal-h5-shadow` bind. |
| #729 enforce loop | `mal-fast-0` | Halt-rule enforcement (#593), to 10-24. |
| #658 C1-NF soak, #662 monitor | `mal-fast-0` | Regime watch only now. #662 limit about 2026-10-17T22:00Z. |
| #382 forward walk 1, #433 | `mal-research-0` | Auto-resumed after the reboot. Never open outputs before the FINAL. |
| Tip follower `mal-fast-tip-follower` | `mal-fast-0` | Keeps 3 days (~18 GB/day). Disk 81% is steady state. Archive script `/data/mal/hunt-1008/ops/tape-archive.sh`. |
| Daily A3 structure monitor | `mal-research-0` | 06:41Z. Two consecutive unevaluated days count as a halt. Check a 10-12 run exists. |

**Crons (session-only, 7-day expiry).** 6 recurring pointer prompts from `recurring-crons-1011.json`: 0a25dc0e 03:23 archive, 638333d8 12:17 health+audit, 5745877b 06:41 structure, 51b24359 00:21 day check, a4af36b3 at 5/11/17/23:47 running check, 14193fc2 00:16 ledger. Plus 9a5bfe76 09:13 housekeeping, a renewal one-shot 4c1103db for 10-18 12:07Z, and 15 seal-window one-shots from `oneshot-crons-1012.json`. Verify with CronList after any compaction. **If the session dies again, recreate from those two JSON files and SEAL-WINDOW-OPS-1012.md.**

**Open PRs.** PR #611 at head 95634e2 (docs/test follow-ups to #603): Reviewer OK (comment 6121244434); quant-proof pass on that head pending (last QP verdict was CHANGES at c728d4f1, comment 6121212063); merge only with QP OK and Reviewer OK on the PR at the final head. In review: PR #613 (`claude/h5-tipauth-item4-count`, head cbb0920, frozen count-only item-4 read tool; reviewer and quant-proof passes running). The executor tipauth gate PR (branch `claude/h5-exec-tipauth-gate`, switch default OFF) is being built and is not open yet (no draft PR in `gh pr list` at the time of writing). #608 (C1-NF 14c, parked with C1-NF), #471, #465, #90 parked. #603 merged as `848bd3a`.

**Follow-ups to #603 (owed, in #611):** pin FIRST_DAY in `test_sigterm_while_waiting...` (it fails on main since 10-12T00:21Z), Option B runbook wording (3 items in QP comment 6120551060), and check the v2 latch for `boost_before_sell_gt_15pct` before any Option B (V2_REPORT_ONLY lists it though DEC-028 section 4 1(a) makes it v2's primary halt).

## 3. Research results 10-12 (none is an edge claim)

- Search A, exits on H5 entries: no survivor. `/data/mal/hunt-1008/search-a-h5-runner/REPORT.md`.
- Search B, what is new in October: 5 hypotheses, H10 dropped. `/data/mal/hunt-1008/search-b-october-new/REPORT.md`.
- SB-H1 September count: THIN. 67 cheap-fee boosted custom-pair pools on 11 dates. Parked to the FINAL. `/data/mal/hunt-1008/search-h1-sept/REPORT.md`.
- Search C, other venues: no BOOST-like forced buyer. `/data/mal/hunt-1008/search-c-other-venues/REPORT.md`.
- Strategy library and nearest-to-live shortlist: `/data/mal/hunt-1008/library/` (first pass had errors, corrected; verify before quoting).
- F1f quote-coin Step 1: loader frozen 08:31:41Z, the real run is at the FINAL (one-shot 10-16 03:07Z). **It needs the owner's OK for RELEASE.json (and the F1 ledger release) before 10-16.** Ask the owner.

## 4. Next days: DEADLINE TABLE (UTC)

Copied row for row from the DEADLINE TABLE in `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md`, with `#735` read as `#810` (the shadow was restarted) and the 10-13 09:17Z row expanded from the manager notes. The tipauth reads are in the second table.

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
| 10-15 21:37Z | 3 | Verify #810 start record `cap_pick_oracle 1 True`; `mkdir -p ~/data/cap-pick-oracle`; submit the one exporter job; extend its limit |
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
| 10-21 12:07Z | 10 | Extend #810, the exporter **and walk 2** |
| ~10-23 00:30Z | 10 | Walk 2's first 7-day limit ends — must already be extended or resubmitted |
| **10-23 01:00Z** | 9b | EXP-022 look 1 data end; needs P4 read tool merged (not built), E1 amendment, A2, hours through 10-23T01 sealed |
| 10-23 19:29:53Z | 5, 10 | H5 `end_ms`: no new buys; delete the `21 0` and `47 …` crons; shadow restart window (14-day limit ~10-25T07Z) |
| 10-24 ~03:00Z / 10-26 12:00Z | 9a | EXP-025 Look 2 earliest / deadline (only if Look 1 did not pass) |
| 10-30 01:00Z | 9b | EXP-022 look 2 data end = last instant for a late look 1 |

Added since that file (tipauth, shadow job #810, read from its restart instant 2026-10-12T09:06:45Z with `/data/mal/hunt-1008/ops/tipauth-read.cmd`):

| Instant | Item | Action |
| --- | --- | --- |
| 10-12 ~10:07Z | tipauth | +1 h read (rss rule 1.0 GB) |
| 10-12 ~15:07Z | tipauth | +6 h read |
| 10-13 ~09:07Z | tipauth | +24 h read; item-4 count tool frozen before data accrues |
| (old: 10-13 ~07Z) | union rss | The old "+48 h union rss re-read" targeted #735, which is cancelled. It is retargeted to #810: the rss reads are the +1 h, +6 h and +24 h reads above |

Seal-window drafts for each gap: `/data/mal/hunt-1008/ops/seal-window-drafts/INDEX.md`. It lists 9 manager decisions and 11 document disagreements, to be put to one quant-proof ruling at the 10-13 09:17Z gaps-day.

Tipauth path (not a fixed date): after >=24 h and >=10 no-gap pv triggers with ws >=1 (expect ~48 h), the phase-2 PR, DEC-024 amendment, decision-change account, QP on the final head, Helm reinstall. Live ~10-15/16.

## 5. Owed

- The count: tipauth reads at +1 h, +6 h, +24 h, then the item-4 count list (items 1-10 in comment 6121123397). C5 is three conditions: `orphaned_by_drop_n == 0`, `tipauth_nosettle == 0`, `dropped_first_slot > cover_lo`.
- Phase-2 package (section 1), including the owner's verbatim Q&A in the amendment and the switch-off rule for tipauth-only trades.
- Merge #611 after QP OK and Reviewer OK on the PR at its final head.
- Owner: the F1f RELEASE.json OK before 10-16; a wake-up update (H5 status, fix timeline ~10-15/16 with Helm needed, what the searches found).
- Records PR (this one) then `git -C ~/MAL pull --ff-only` on fast-0 (as a job) so the Console shows it; copy any result.v1 record to `~/data/results/`.
- EXP-013 recount feasibility (`revive-exp013/`) and the seal-window drafts (`/data/mal/hunt-1008/ops/seal-window-drafts/INDEX.md`) were still pending at 09:10Z.
- The research-0 watchdogs (disk, CPU guard, report sync) were background loops of the old session; verify they exist after the reboot (`pgrep -af 'df --output=pcent'`, `pgrep -af loadavg`) and restart if not.
- Record every future mid-week shadow or runner restart in LAB_STATE.

## 6. Rules that bite

- **The 10-16 FINAL** runs as written at about 10-16T02Z. It is reported compromised, and `exp012_forward` runs without `--strict-lines`. The (e') dry run is due before 10-15T23Z.
- **EXP-022 seal (section 9).** No outcome of a counted CAP-PICK hour is opened, priced or printed from any source except the sealed look. From 10-16T01, H5 (live and shadow) must not trade or price CAP-PICK picks: the pick_oracle reads only a boolean after the FINAL and fails closed. Before 10-16 the runner is read only through `runner_timing_read.py`; a breach means the read is reported compromised.
- **EXP-024 declared observation.** Canary and shadow outcomes in Look 1's window [10-10, 10-16) are watched live. Look 1 is read and reported as written.
- **Gate changes need the owner's dated decision.** Any later change to H5's trade filter is judged on mechanism and counts only (DEC-024 note, #600). A pre-approval does not execute itself.
- **Pinned exit gates.** Post QP OK on the exact head and a Reviewer verdict ON THE PR before every merge, workflow verdicts too. Merge in a SEPARATE call with `--match-head-commit`; never `; gh pr merge` after a checkout or edit.
- **Shadow restart procedure.** Executor flat first (open 0, pending 0 via the `--status` job), cancel the old job, resubmit its exact command as a new MiScusi job (clean checkout, 0 porcelain lines, then extend the limit to 13 d), check the start record fields, write the LAB_STATE row.
- **Hunts use exploration tape only.** Sealed blocks fresh-0802, fresh-0808 and fresh-0828 are never read.
- **Reserve convention:** PumpSwap rows are PRE-trade with V; bonding rows are POST-trade.
- **Jobs run under `sh`.** No bash-isms in job commands; name `bash` explicitly where a script needs it.
- **Memory on `mal-fast-0`:** one heavy replay at a time. MiScusi refuses jobs of 2 GB or more, or 2 CPUs or more, there.
- **Subagents.** Builders stop at about 40 turns and reviewers at about 20; resume them with SendMessage. Builder agents cannot call MiScusi tools, so the manager submits jobs. Reviewers use their own scratch worktree. Agent types `reviewer` and `quant-proof` can return empty inside workflows; use default agents with a tool budget and a forced verdict.
- **Never write a realized P&L number** in a status line. Check drop-best-date totals before telling the owner anything is positive.
- **Never print or commit** keys, the OpenRouter key, or the DB password. Do not touch `ufw`, `sshd`, cloudflared, or `/opt/miscusi`.
