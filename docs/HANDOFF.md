# MAL manager handoff: manager16 → manager17 (written 2026-10-26T16:39Z, from date -u)

Read this first. Then read the last END block of memory note `push-1025-manager16` (and the log lines after it), then the latest MiScusi notebook entries.

No book has cleared the promotion gate. Profit so far: 0. Nothing trades real SOL.

## 0. Owner direction (current)
- **Goals:** $20–50/day first, then stack; ~$200/day by end of November. The gate is unchanged.
- **Spend:** Anthropic API $200 to 11-11. HV6 spend approved ≤ $3/day (K1, notebook n_dTJO1xRvneLeUA, verbatim). About $80 headroom for testing.
- **Live:** standing OK (10-23) for a DEC-032 C0 canary after a gate PASS. The owner has about 1 SOL ready in Phantom (10-26). Wallet funding, Helm install and the owner's own OK line are still needed at that time. We hold no keys.
- **Kalshi:** removed (owner 10-25). Jobs cancelled, data kept.
- **Hand-offs:** the owner prefers compacts. This hand-off was requested by the owner on 10-26 at about 16:3xZ.
- **Owner 10-26 order (items 1–7), in progress:** HV6 playbook/template field-name fix (§2), HV4 #712 disclosure only, ledger rows (§5).

## 1. Books and clocks (paper)
| Book | Read | State |
|---|---|---|
| **Q50** | Earliest boundary 2026-10-30T00Z (Am.11 count tool; no early read). End-seal 11-06. | C0 path ready (#907): seal COVERED (QP-RULING-1025-q50-seal-live), default fee 55k/55k/150k, twin double-count ~40 bps to declare before the first send. |
| **HV4** | DEC-031 Am.15 (#915) is in force. Instant job #1591 runs daily at 01:30Z from 10-30; the B ≤ 11-08 read runs once at ≥ B + 24 h with --record. | **Liveness OK** (n_p88cjtwYfoX1pg): Haiku and RISK calls succeed, but the 7 LLM arms placed no buys on day 1. Likely over-skip from the stale percent/field names in the frozen playbook (#712; disclosure in PR #941). They may end NOT_DECIDABLE; the X arms carry the read. |
| **DBC m10** | One confirmation read ≥ 11-01T00:10Z (option A; ~10 GiB). | Fetch F #1546 is running. A confirmation only. |
| **HV6** | Not yet registered. Target clock 2026-10-30T00Z, D = 12 / H = 12, halt 1794355200000. | See §2. |

## 2. HV6: the critical path to the 10-30 clock
- **Code:** main 5071e29a (#939). Item 6 PASSED there: drills #1642 (log md5 b5e4f17e) and real-layout #1643 (md5 1ba56097 in all 3 runs; RSS 2,665 MB; scan p95 480 ms; scans_cap_bound 0).
- **BUT** the owner ordered the playbook/template field-name fix: builder on branch `claude/hv6-playbook-fields`. GEN_v0 and the review templates must use the build_card names and units (fractions, f.impact_rt_*, f.quote_sol / f.quote_eff_sol, f.age_s), with a WATCH example in dotted fractions and a field-existence test. Also `dd_ath` (QP H1 on #941; the builder was told). This **moves the final sha**.
- **Steps after that fix merges** (QP + reviewer verified on the PR):
  1. Re-run item 6 at the new merge: `sh scripts/ops/hv6-item6-replay.sh` (4 GB), then `sh scripts/ops/hv6-real-layout-replay.sh 2026-10-21,2026-10-22,2026-10-23 real-<sha8>-r16g-<date>` (16 GB, 12 h, chained after). PASS rule: notebook n_O823KsA43vx5iQ.
  2. Pack PR **#940** (`claude/hv6-registration-b`): the pack agent is applying QP's 8 text changes (comment 6421493435). It re-pins every sha to the new merge and adds the playbook changed-values row. It includes the **real-model reviewer smoke** (L, L-nr, P nightly; L and P regime).
  3. **P0** (`scripts/ops/hv6-p0-count.sh`, ≥ 8 GB, days 10-22..25, fixed-first notebook entry first). Then the **smoke** on mal-research-0, with the regime producer already running, ≤ $0.50. FAIL if STALE > 5%, chunk p95 > 8 s, or refusals > 5% of cards.
  4. QP OK and reviewer verdict on the pack's exact head; registration notebook entry; `tools/hv6_register --sha <final> --clock-start-ms 1793318400000 --live-registry --confirm REGISTER-HV6-FIVE-BOOKS` (once); genesis; runner (`--entries-halt-ms 1794355200000`, venv python, ≥ 6 GB); 3 nightly reviewers at 00:05Z (last 11-10); regime producer job.
  5. All HV6 jobs run from ONE clean checkout `/data2/mal/wt/hv6-final` at the final sha.
- **Regime feed:**
  - stable radar worktree `/data2/mal/wt/radar-b4a0f0d5` (shas 0f5279b3 / 5a2f8979 / 23838d1c);
  - radar successor **#1647**, chained after #1577, 12 daily runs, keeps looping on alerts;
  - producer `scripts/ops/hv6-regime-block-job.sh`: never set HV6_BLOCK_TEST / HV6_BLOCK_NOW / HV6_BLOCK_NOW_FILE / HV6_BLOCK_ONCE; start before T0; no restart between 00:00 and 00:10Z.
- **If the clock slips past 10-30:** the owner must give a line covering T0 + 12 d, or the contrasts are declared NOT_DECIDABLE before the clock.

## 3. Research lanes this push (all notebooked)
- **Killed:**
  - P27 bonding-curve entries;
  - P28-H Haiku narrative scorer (AUC 0.53 vs numeric 0.83);
  - P28-N CALL-SYNC and SIZE-SKEW;
  - P30 lane-G numeric grid (all ~−3%);
  - **P29 keeper-absorb** (−3.26%/trade, 0 of 11 OOS days, n_stiJP8F2MsITUQ; 11 days spent for keeper-tag rules).
- **Parked:** FLOOR long-H (#905, QP NO: restart budget 0).
- **Found nothing:** P31 forced-flow census.
- **Helm on bot keepers (HELM-REPLY-1025-2):** only Axiom leaves a fill fingerprint. Not opened as a lane.
- **Next lanes, from the P32 audit (#937):**
  - FLOOR-SLOW, which needs gapless feed and runner hand-off infra and is usable after HV4 END 11-08;
  - DBC-P18 staging, conditional on 11-01;
  - a COST-OFFSET desk audit (fee tiers, creator fees, cashback; owner policy check).
- **Honest outlook (audit):** $9–22/day if everything holds; probability-weighted $2–8/day over the next month.

## 4. Seals (list them in every brief: /data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md)
- HV4, Q50 and DBC seals as in the previous handoff §2.
- **meta-capture is SEALED** (/data/mal/meta-capture/SEALED.txt). Contacts were filed as a low-severity breach in #927. The hourly meta-lag check is **suspended**; use #1587 job status only.
- HV6 drill/ and smoke/ roots are never an input.

## 5. Open PRs
- **Ready to merge after QP:**
  - **#941**, ledger rows (DBC holdout + #1494, m10 forward, LaunchLab) and the HV4 #712 disclosure. Reviewer APPROVE (6421572285). **QP CHANGES at 95093533** (comment 6421648194): ledger items L1–L6 and disclosure items H1–H3. The most serious is L1: the read is `scripts/research/dbc-m10-read-job.sh A|B`, never `tools/dbc_fwd_read.py` run as a program. H1 adds `dd_ath` (card = 1 − price/ath, a fraction) as a 4th mismatch. Fix, then QP re-checks the text diff, then the reviewer, then merge.
  - this hand-off PR (claude/handoff-manager16-1026).
- **In progress:**
  - **#940**, HV6 pack;
  - `claude/hv6-playbook-fields`, owner fix, builder running.
- **Records/draft PRs to batch-review or merge:** #909, #916, #928, #929, #932, #933, #934, #935, #937. Library rows to update: P29 → explored-no-survivor.

## 6. Fixed instants (host-side jobs)
- **Daily:**
  - 00:05Z HV4 E5 reviewers; 00:10 / 00:15 / 23:50Z digests; 00:20Z Q50 score;
  - 01:10Z HV4 D3 table; 01:30Z HV4 instant #1591 from 10-30;
  - 07:10 / 19:10Z A3 #1446; 07:40Z radar #1577, then #1647; 13:10Z dead-man.
- **11-07:** feed hand-off Job H #1584 at 11:50Z, then C #1585. **Owed:** append the 11-07T12:05Z gap to /data/mal/hunt-1008/push-1016/hunt30/hv4-read/restart-gaps.txt and notebook it before the 11-08T01:30Z #1591 run.
- **Manager session crons:** recreate from the memory note (hourly push check e893386e uses job status only for meta; disk; 00:23 digest; 01:21 HV4 table; 03:23 archive; 06:41 A3 monitor; 09:13 housekeeping; 12:17 H5 health; :47 Helm).
- **Daily HV4 notebook entry:**
  - D3 table file and last-line sha, plus excluded hours;
  - E6 decision_log_sha256 per bot, via a sha-only job that greps '^hv4-' and prints the decision_log_sha256 field.

## 7. Ops notes and lessons
- **Merge gate:** QP OK and reviewer verdict both ON THE PR at the exact head. A QP comment's first line may be '---', so grep the body.
- **Force-push is denied:** open a replacement PR from a `-b` branch.
- **Builders:**
  - sometimes leave commits unpushed (verify the remote head);
  - share the scratchpad (use unique file names);
  - hit their turn caps (resume them).
- **Memory alarms:** look at cgroup anon vs file memory before worrying about a job at MemoryMax.
- **H5 health 10-26:** `wallet_gap` alert, likely a stale FUNDED_SOL. Asked the owner whether there was a withdrawal or the 10-22 fee-arm tests explain it (n_0imWpNPkzPodCA). Waiting for an answer.
