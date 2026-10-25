# MAL manager handoff — manager15 → manager16 (written 2026-10-25T18:2xZ, from date -u)

Read this first. Then read the last END block of the manager state log: memory note `push-1024-manager15`, copy at /data/mal/hunt-1008/ops/manager15/. Then read the latest MiScusi notebook entries.

No book has cleared the promotion gate. Profit so far: 0. Nothing trades real SOL.

## 0. Owner direction (current)
- **Goals:** $20–50/day first, then stack; ~$200/day by end of November; recover ~$1,500. The owner may add 1–2 SOL once results are consistent. The gate is unchanged.
- **Spending limits:** API cap $200 to 11-11. Helius ~20M autoscale credits; report every use (job, credits, what got faster).
- **Live OK (10-23):** standing OK for a **C0 live canary** as soon as a book's gate read PASSes (DEC-032 preconditions). The DEC-032 Am.2 ladder C0→C1→C2 is merged, and the owner's OWNER_LADDER_PREAPPROVED line is notebook n_p5ZEchJioV1W7A. Not covered: wallet funding, Helm install, keys, LIVE_OK/TIER. Those need Vaan's own OK.
- **Decisions:** the manager decides rules, DECs and lanes and tells the owner afterwards (memory feedback-decide-and-tell).
- **Hand-off notices:** on a MiScusi "prepare a hand-off" notice, prepare a COMPACT. Do a real hand-off only when the owner asks, as for this one (10-25 18:0xZ).
- **Owner push 10-24:** keep finding new ideas ("don't rely on these four"); capture changes fast (regimes, redeploys, mechanics); feed signals to Haiku bots; keep the forward tests clean ("no 'Critical finding!'").

## 1. Books and clocks (all paper)
| Book | Clock / window | Read | State |
|---|---|---|---|
| **Q50** `h5v1q50-boostfloor-a1` (frozen a5f41370) | 2026-10-25T00Z, H 14, end-seal 11-06T00Z | Am.11 instant: 100 counted trades; ~11-01 if ~15/day | Shadow #1419 (+succ #1421); daily score #1543 at 00:20Z; applied-alert #1540; read tools #884 merged. Day 1 dry: 10 triggers, 6 refused (base_breaks), 4 counted. Disclosed in #897. |
| **HV4** (9 Haiku bots `hv4-{CON,BIG}-{L,F,P,X,R}`, frozen 9ed4c5ff) | 2026-10-25T00Z, H 14, END 11-08T13:05Z | ≥100 trades on ≥5 days per book; earliest ~10-30 | Runner #1561 → seg2 #1562; E5 reviewers #1564–#1569 (00:05Z); E6 digests #1554/#1555 (+#1559/#1560); L8 sampler/table #1517–#1520. Records #779 + #899 merged. |
| **DBC m10** (tag dbc-m10-forward-v1-am1 = 7d5be9e5) | forward [10-25T00Z, 11-01T00:10Z) | ONE read ≥ 11-01T00:10Z, option A | Daily fetch F #1546 (cap 3.7M). C1 #1574 PASS (n_fVBsdSlwY-bFfA), C2 #1576 PASS (n_Hoa7VZxhtSCN2w); read MemoryMax ≈ 10 GiB. #890 has its reviewer verdict, posted after merge; that is a disclosed lapse (n_-XNt3WqY3L0sCQ). In-sample causal +0.0122 SOL/trade flat: best of many cells, not evidence. |
| FLOOR | — | — | CLOSED on both clocks: A2 rate 6.0/day < 12.5; N not spent. Seals lifted (L0 n_cuTE-bRvYIfCWA, QP-RULING-1025-seal-lift b04a57eb). A revival needs f1–f5 (a new amendment; clock ≥ merge + 75 h). PR #811 is retitled "not registered"; convert it or close it. |

Other: walk 2 #1036 (r4-b, gate #1289 → #1290 on 11-03); EXP-025 Look 2 #1287 at 10-26T12:05Z (expected NOT_DECIDABLE R8); Kalshi recorders (paused line, passive).

## 2. Seals in force (list them all in every tape-touching brief)
- **HV4/Q50:** ledgers, fills, prices, exits, positions, P&L, decision-row contents, bots.json, attempt counts, score/preview/am6_read output (only the scheduled digest sha is allowed), #1561's status view and log tail. Q50: Am.11 item 5 and the Am.11/13/14 lists; the EXP-024 Look-2 seal.
- **DBC:** forward window [10-25T00Z, 11-01T00:10Z) and fwdv1/. LaunchLab holdout [10-17, 10-24). D1 hours 10-17..10-24, all D1 variants.
- **Arena tape:** usable for counts and creates under QP rulings (radar r2–r5; meta-arena C1–C8). Never join it to HV4/Q50 outputs before their reads.

## 3. A3 guard (Q50)
- Loop #1446 (07:10Z/19:10Z) + successor #1447, from tag a3-guard-pin-1024 = 0ec7fae8. NEVER run it from main. Dead-man #1319/#1333.
- A HALT ends Q50. The pre-built successor is S* = tag q50-succ-base-1 = 5c2ab7c6 (md5 EQUAL; DEC-031 Am.14 policy; playbook #891). It turns a halt into a new clock in hours. QP survival to 10-30 is ~0.38.

## 4. Live path (dry)
- Q50 keyless dry run PASS on fast-0 at 0526d5cd (HELM-REPLY-1024). The old LIVE_OK/TIER files were moved to /root/disabled (Vaan OK, HELM-REPLY-1024-2). Executor stopped and disabled, no key.
- Before a first send: Q50 gate PASS → QP-checked live.json with the DEC-032 C0 values (0.05 SOL, p155 fee pin) → Helm installs → owner funding (~0.5 SOL) and the owner's line.
- HV4 live adapter and a DBC live paper book (P18 PRs 2–4) are not built. Build them only if a read PASSes.

## 5. Fixed instants: host-side MiScusi jobs (no session needed)
- **00:05Z** HV4 reviewers. **00:10Z / 00:15Z / 23:50Z** digests. **00:20Z** Q50 score. **01:10Z** HV4 D3 table.
- **07:10Z / 19:10Z** A3. **07:40Z** radar #1577 (arena on; fresh out-dir; C1–C4 of QP on #900). **13:10Z** dead-man.
- **10-26T02:01Z** gate #1583 → feed hand-off Job H #1584 (FH_START_IF_NONE=1) + post-check C #1585 for the 11-07T11:50–11:58Z hand-off (dry run #1582 OK; n_ZsEFLtRLpAXd1w). Check H's submit-time log after 02:01Z.
- **10-26T12:05Z** EXP-025 Look 2 #1287.

**Manager steps (session crons; recreate from /data/mal/hunt-1008/ops/manager13/crons-manager15-full.json):**
- **Daily:**
  - 01:21Z: notebook the HV4 L8 FINAL table sha, the excluded-hour count, and the E6 decision_log_sha256 per bot (DEC-031 Am.6 2.6). The first one is due 10-26.
  - 00:23Z digest copy; 03:23Z tape archive; 06:41Z A3 structure monitor; 09:13Z housekeeping; 12:17Z H5 health.
- **Hourly / every 2 h:** :23 push check (meta lag now on #1580); :37/2h disk guard; :47 Helm channel.

## 6. Owed (by date)
- **10-26:** first HV4 daily-table notebook; confirm Job H's log after 02:01Z.
- **By 10-27:** meta-capture backfill concurrency fix (PR) → rerun [10-25T11, 16) (#1581 was too slow, cancelled; n_LFPCcz-NMrzWOw). Low priority, research archive.
- **10-27..10-31:** DBC G gap passes after daily fetches, only when F is not fetching. Keep G-STATE.json/G-LOG.md with the read record.
- **11-01T00:10Z:** DBC READ job (`scripts/research/dbc-m10-read-job.sh A <GiB>`, after F and the last G pass, ~10 GiB).
- **~10-30 → 11-02:** first HV4 and Q50 gate reads with QP. A PASS → C0 canary path (§4). Owed for HV4: an attempts-vs-trades read rule (its own QP ruling) and the live adapter.
- **Fee model:** disclose at every read that the creator fee is 0.5 bp below chain at the five PumpSwap tiers ≥ 54,030 SOL mcap.
- **Ideas:** keep a P-lane running (P26 parked both rows; the idea library has 45 untested). Radar alerts → change playbook.

## 7. Jobs and infra notes
- **Job A meta-capture #1580** (#902 b0fb62ca): PumpPortal + arena create source, public RPC (0 Helius), 14 d. The old #1288 was cancelled at 15:43Z; its scope survived the cancel and needed `systemctl --user stop mal-meta-capture.scope`.
- **MiScusi cancel leaves systemd-run scope children alive:** ps-check after every cancel. Careful: `pgrep -f <text>` also matches your own shell; use `ps … | awk` with an exclusion.
- **research-0 memory:** HV4 runner 45 GB; MiScusi job memory is tight (a 24 GB job could not start today).
- **Merge gate:** QP OK + reviewer verdict, both ON THE PR at the exact head, verified with `gh pr view N --json comments`. Two lapses (10-24): ten PRs, then #890, merged without both verdicts. Late verdicts were posted and verified for all.
- **Helius on fast-0:** claude has a read ACL on /var/lib/mal/fast-listener/helius.env (Helm, HELM-REPLY-1024-3/-4). Note it in a HELM-ASK file before using it.
- **Helm channel:** /data/mal/hunt-1008/push-1016/hunt29/p1/ (HELM-REPLY-*/HELM-ASK-*); nothing new since 10-24 19:48Z.
