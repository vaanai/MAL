# Manager handoff 2026-10-12 ~09:30Z (manager10 -> next manager)

Replace this page at the next handoff; don't append. Read it first. Sources for every line are named in the records PR body. Nothing here is an edge claim, and no realized P&L number is written here on purpose.

Then read, as needed:
- **Seal-window steps and GAPS:** `/data/mal/hunt-1008/ops/SEAL-WINDOW-OPS-1012.md` (its DEADLINE TABLE is copied below).
- **Session crons (all prompts on disk):** `/data/mal/hunt-1008/ops/recurring-crons-1011.json` and `/data/mal/hunt-1008/ops/oneshot-crons-1012.json`.
- **H5:** `/data/mal/hunt-1008/h5-work/PLAN.md`, [DEC/DEC-024-h5-live-canary.md](../DEC/DEC-024-h5-live-canary.md), [EXP/EXP-024-h5-boostfloor-part1-prereg.md](../EXP/EXP-024-h5-boostfloor-part1-prereg.md).
- **EXP-022 CAP-PICK Part 1:** [EXP/EXP-022-cap-pick-part1-prereg.md](../EXP/EXP-022-cap-pick-part1-prereg.md).
- **Tipauth count list and C1-C6:** PR #610 comment 6121123397 (QP OK, copyable block).

## 1. Where things stand (2026-10-12 ~09:30Z)

**H5 is live at T0** (0.02 SOL per trade). As of 09:06Z 10-12: 7 attempts, 7 sells landed, no STOP/HALT. 20 landed buys are needed before T1. End `2026-10-23T19:29:53Z`. Enforce loop job #729, shadow job #810.

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

**Open PRs.** #611 (docs/test follow-ups to #603; in review, QP CHANGES at c728d4f1 was the latest verdict I could see, merge only with QP OK and Reviewer OK on the PR at the exact head). #608 (C1-NF 14c, parked with C1-NF), #471, #465, #90 parked. #603 merged as `848bd3a`.

**Follow-ups to #603 (owed, in #611):** pin FIRST_DAY in `test_sigterm_while_waiting...` (it fails on main since 10-12T00:21Z), Option B runbook wording (3 items in QP comment 6120551060), and check the v2 latch for `boost_before_sell_gt_15pct` before any Option B (V2_REPORT_ONLY lists it though DEC-028 section 4 1(a) makes it v2's primary halt).

## 3. Research results 10-12 (none is an edge claim)

- Search A, exits on H5 entries: no survivor. `/data/mal/hunt-1008/search-a-h5-runner/REPORT.md`.
- Search B, what is new in October: 5 hypotheses, H10 dropped. `/data/mal/hunt-1008/search-b-october-new/REPORT.md`.
- SB-H1 September count: THIN. 67 cheap-fee boosted custom-pair pools on 11 dates. Parked to the FINAL. `/data/mal/hunt-1008/search-h1-sept/REPORT.md`.
- Search C, other venues: no BOOST-like forced buyer. `/data/mal/hunt-1008/search-c-other-venues/REPORT.md`.
- Strategy library and nearest-to-live shortlist: `/data/mal/hunt-1008/library/` (first pass had errors, corrected; verify before quoting).
- F1f quote-coin Step 1: loader frozen 08:31:41Z, the real run is at the FINAL (one-shot 10-16 03:07Z). **It needs the owner's OK for RELEASE.json (and the F1 ledger release) before 10-16.** Ask the owner.

## 4. Next days: DEADLINE TABLE (UTC, from SEAL-WINDOW-OPS-1012.md)

| Instant | Item | Action |
| --- | --- | --- |
| 10-12 (today) | A3 | Check a 10-12 run exists in `daily.jsonl`; run the monitor if not |
| daily 06:41Z to 11-06 | A3 | Structure monitor job (research-0, 300 MB, 20 min); read `halt.flags` one by one |
| 10-12 ~10:07Z / ~15:07Z | tipauth | +1 h and +6 h reads (rss rule 1.0 GB) |
| 10-13 ~09:07Z | tipauth | +24 h read; item-4 count tool frozen before data accrues |
| 10-13 09:17Z | gaps-day | Resolves the open specs: FINAL job out-dir, the 4 FINAL_WRITTEN marker paths, (e') spec, exporter spec, EXP-024 Look 1 chain and pre-read amendment draft, EXP-025 run-or-record, EXP-022 P4 read tool builder; walk 2 runs regardless (EXP-022 Am.2 item 3) |
| 10-13 ~07Z | union | rss re-read at about +48 h |
| 10-14 10:07Z | (e') | Dry-run `merge --dry-run` on the ledgered V-map snapshots |
| 10-14 11:23Z | #382 | Verify/extend #382 (own limit ~10-15T08:10Z) and #433 |
| 10-15 06:41Z | P2 | P2 run: six flags for EXP-022, five for H5 |
| 10-15 07:17Z | P2 | Read P2; if withdrawn decide walk 2, keep exporter and markers, record it |
| 10-15 21:37Z | exporter | Verify the shadow start record `cap_pick_oracle 1 True` (now #810); `mkdir -p ~/data/cap-pick-oracle`; submit the one exporter job; extend its limit |
| **10-15 23:00Z** | (e') | **Hard deadline**: pass recorded in DEC-016, or the failure and counts recorded |
| 10-16 00:00Z | A11 | Last instant for a DEC-016 A11 or slot-span amendment (none planned) |
| 10-16 00:23-00:30Z | walk 2 | `bash scripts/research/forward-walk2.sh`, `{"start":"2026-10-16T01"}`, resumable, 10080 min, ~6 GB; needs a free Helius slot |
| 10-16 00:43Z | executor | Confirm f873aac is the live build (`--status`) |
| **10-16 01:00Z** | seal | Walk 2 must have started; seal window opens; every H5 buy refused until markers and exporter |
| 10-16 ~02:00-02:10Z | FINAL | `score` then `report` (no `--strict-lines`); reported compromised |
| right after the FINAL row | markers | h5 (`sudo touch`), job-user `cap-pick-oracle`, c1nf, research-0 `/data/mal/exp012-forward/FINAL_WRITTEN`; record `date -u` in LAB_STATE |
| 10-16 ~03:00Z | check | Both markers, `picks.jsonl` 644, oracle `fresh: true`; #433 closes (credits, completion amendment); DEC-016 Am.9 Log line |
| 10-16 06:13Z | E1 | E1 job (pinned command, fresh out-dir); n >= 20 |
| 10-16 06:41Z | A3 | Run dated 10-16 (Look 1 condition (f)) |
| 10-17 02:00Z on | EXP-025 | Look 1 tools may start |
| **10-17 12:00Z** | EXP-024 | **Hard deadline** Look 1 (after it: NOT_DECIDABLE) |
| 10-17 ~22:00Z | #662 | Limit; chain a successor or stop |
| **10-18 12:00Z** | EXP-025 | **Hard deadline** Look 1 |
| 10-18 12:07Z | crons | Renew the recurring session crons |
| 10-21 12:07Z | limits | Extend #810, the exporter and walk 2 |
| 10-23 01:00Z | EXP-022 | Look 1 data end; needs the P4 read tool merged (not built), E1 amendment, A2 |
| 10-23 19:29:53Z | H5 | `end_ms`: no new buys; shadow restart window (14-day limit) |
| 10-24 / 10-26 | EXP-025 | Look 2 earliest / deadline (only if Look 1 did not pass) |
| 10-30 01:00Z | EXP-022 | Look 2 data end |

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
