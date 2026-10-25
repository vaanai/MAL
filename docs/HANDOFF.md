# MAL manager handoff — manager15 (written 2026-10-24T03:02Z, from date -u)

Read this first, then the END block of the manager state log (memory note `push-1024-manager15`; copy at /data/mal/hunt-1008/ops/manager15/) and the latest MiScusi notebook entries. No book has cleared the promotion gate. Profit so far: 0.

## 0. Owner direction (current)
- Goals: $20–50/day first ("1 SOL every couple of days, then stack"), ~$200/day by end of November, recover ~$1,500. The gate is unchanged. API cap $200 to 11-11. Helius: ~20M autoscale credits; report every use (job, credits, what got faster).
- 10-23 standing OK: a C0 live canary may start as soon as a book's gate read PASSes (DEC-032 preconditions). Not covered: wallet funding, Helm install, C1+ step-ups. A pre-approvable C0→C1→C2 ladder (DEC-032 amendment draft + hunt29/ladder/OWNER-SUMMARY.md) is being drafted for the owner.
- Manager decides rules/DEC/lanes and tells the owner after. On a MiScusi "prepare a hand-off" notice: prepare a COMPACT, not a hand-off, unless the owner asks for a real hand-off.
- Project job limit is now 64 (Helm, 10-24).

## 1. Books and clocks
| Book | Clock | State |
|---|---|---|
| Q50 `h5v1q50-boostfloor-a1` (frozen a5f41370) | 2026-10-25T00Z, H 14, end 11-06T00Z | Registered 10-24 00:20:15Z (P0 46/25/35/day). Shadow #1419 (+succ #1421), daily score #1425 (00:20Z), day-1 dry #1424 (10-25 06:30Z, counts only). Records #842 merged 03a31e35. First possible gate read ~10-30. |
| H5 a2 v1/v2 | — | ENDED NOT_DECIDABLE: A3 guard HALT synthetic_share_high (8/19) at 10-22T19:10:05Z, before their 10-23 clock. Jobs cancelled. Not revived (rate 9/day cannot reach 100 before the 11-06 end-seal). |
| HV4 (9 Haiku bots, frozen 9ed4c5f) | 2026-10-25T00Z | P0 A1 #1450 (days 10-21/22/23) and A2 #1451 PASS (CHECKS ALL OK). X-rule STOP → QP-RULING-1024-xrule (CLEAN v2) → #1454 X=120 → C2 #1455 THR_SHA256 da13ccc2. Records #779 head 7e523e7d (partly filled). Registration night 10-24 21:17Z (STEP-3-4-5-6.md). |
| FLOOR l1-floor-v2 (frozen 5e12f8f5) | NOT registered for 10-26 (A2 floor FAIL, rate_att_lo 6.0 < 12.5/day; notebook n_27eIzN61qHjZwQ; N not spent; no slip). The dated steps below are the superseded plan, not active clocks | DEC-031 Am.9 + Erratum 1. 10-24: post P0-FIXED-FIRST-AM9.md (13:00–23:00Z) + smoke S1–S3 after the 12:10Z feed hand-off. 10-25: A1 ≥01:10:30Z, A2-v3, L3, G1 ≥22:05Z, DR1 ≥22:10Z, E3 23:00–23:45Z (E3_LEAD_S=900), E4 + D1 after. Records #811 needs QP + reviewer. Pack hunt24/floor-1026/ (read EXEC-AM9-1026-ERRATUM.md first). Note: P7 fresh-0903 "~55% smaller" holds only with n 62, CI90 lo < 0 (flat −0.0032 / pressure −0.0047), pressure ex-top-3 −0.065; in-sample, winner's curse; not confirmed. |

## 2. A3 guard (fixed 10-24)
- DEC-031 Amendment 10 (main 024ee714) + code #840 (main 79a93e94): pooled synthetic z-test (p0 75/339, phi 2.0, z > 3.0902).
- The guard runs from tag `a3-guard-pin-1024` = 0ec7fae8 (136cd98 + the check tool and tests) — NEVER from main (main's h5_shadow.py blob fails frozen_guard). Loop #1446 (07:10Z/19:10Z) + successor #1447; dead-man #1319/#1333 stay at 136cd98. First official run 00:57:40Z green, A3_FLAGS_OK.
- Check every guard run's applied log; a HALT now ends Q50.

## 3. Live path (dry; code-complete for Q50)
- Merged 10-24: #836 #838 #839 #843 #846 #847 (live entry, install pack, sell-and-close rescue, tie-out exporter, watchdog, exit-gap writer), #851 EXP-024 note (do not edit after 10-25T00Z), #852 FEED_NOTE flip (Q50 only).
- INSTALL SHA e2471186383c698825d8ada4bcaa7ba635568bc8; Q50 md5 decision-equivalence EQUAL there (#1457). Manifest on fast-0 ~/h5-arena-manifest-e2471186….txt (sha256 cc61c094…).
- HELM-SHEET (hunt29/p1/HELM-SHEET.md, sha256 e403583b…) sent to the owner: keyless dry install + 1 h dry run. Re-pinned job scripts in hunt29/p1/jobs/.
- Still before a first send: Helm install + dry run; chain proofs (hunt29/p1/CHAIN-PROOFS.md); DEC copy fills (hunt29/p1/DEC-copy-h5v1q50-boostfloor-a1.md); Q50 gate PASS; owner line + funding (~0.5 SOL fresh wallet).
- FLOOR (not registered for 10-26) has a dry executor (#837, draft) and a C0 concurrency proposal (8 open × 0.02 SOL) in hunt29/p2/DESIGN.md §7.

## 4. Push 10-24 (hunt29/PUSH-PLAN-1024.md) — results
- P3 exits (#844): none beat baseline. P4 four new high-rate ideas (#853): all FAIL. P5 stack model (#841): FLOOR+H5 ≈ $29/$43/$59 a day at 1/2/5 SOL, in-sample screen (not evidence; best-of-N, winner's curse; a zero or negative true edge is fully consistent with these numbers; Q50 has no edge figure), $10–27 at half edge. P7 FLOOR on fresh-0903 (#849; kit PR #848): not confirmed: n 62 (under 100), CI90 lower bound below 0 under both models (flat -0.0032, pressure -0.0047), pressure ex-top-3 -0.065 SOL, kit verdict KILLED/NOT_DECIDABLE; it neither confirms nor refutes F0. FLOOR's 10-26 registration did not happen (A2 floor FAIL, rate_att_lo 6.0 < 12.5/day; notebook n_27eIzN61qHjZwQ). P9 (#855): library exhausted; G4 v1 not registered.
- New venues: P8 live recorder #1453 (LaunchLab + Meteora DBC, PR #850). P10 LaunchLab Helius backfill #1461 (09-24..10-24, cap 3M credits, PR #856); LaunchLab hours [10-17, 10-24) reserved holdout. P11 LaunchLab pricer + screens (+ CPMM graduation idea; manager decides the CPMM fetch). P12 Meteora DBC decode + 3-day sampled backfill (cap 3M; DBC [10-17, 10-24) reserved). P13 census (#858): next venues = LaunchLab-graduated CPMM pools, then DBC-migrated DAMM v2 pools.
- P6 latency reprice job #1438 (~5 h): when it prints ALLDONE, run hunt29/p6/analyze.py (or resume the P6 agent) and write the table + desk note.
- Open lane PRs (records/library; review + merge later): #837 #841 #844 #848 #849 #850 #853 #855 #856 #858.

## 5. Fixed instants (session crons; list /data/mal/hunt-1008/ops/manager13/crons-manager15-full.json — RECREATE after a restart)
- 10-24 12:09Z feed hand-off (window 12:10–12:25Z only; QP-RULING-1023-feed-handover.md) → notebook + new job id (FLOOR G1 needs it).
- 10-24 12:35Z HV4 L8 sampler/table jobs + fill #779 (A1/A2 ids n_1xX8_J7pzL9KKw / n_KGQY28O-u2Fmzw, L3 #1389, L6 dry run #1452) → QP + reviewer on #779.
- 10-24 21:17Z HV4 registration night.
- Recurring: hourly :23, disk :37/2h, 00:23 digest, 03:23 tape archive, 06:41 monitor, 09:13 housekeeping, 12:17 H5 health.

## 6. Environment notes
- /data/mal/venv lacks certifi (11 arena test files cannot collect) and pandas.
- MiScusi cancel leaves systemd-run scope children alive: ps-check after cancels.
- Stacked PRs that get squash-merged must be rebased with an identical patch-id before merging (done for #843 #847 #839 #852).

## 7. Update 2026-10-24 07:2xZ (read this first)
- **Stake ladder:** DEC-032 Amendment 2 merged (560bb6c8). The owner's OWNER_LADDER_PREAPPROVED line was received verbatim (notebook n_p5ZEchJioV1W7A) and recorded in hunt29/p1/DEC-copy-h5v1q50-boostfloor-a1.md. Ladder code merged: #861 fcb1575b, #862 f5676fe6, #864 0526d5cd. C1 stays locked until a reviewed PR sets the c/report pins after a confirmation read. Still to build before C1: Helm tie-out sign-offs, owner deposit addresses, executor chain total, halt history, trailing-30 parity, stake-priced twins.
- **INSTALL SHA is now 0526d5cd** (md5 EQUAL #1481; manifest on fast-0 ~/h5-arena-manifest-0526d5cd….txt, sha256 5299a3c4…; HELM-SHEET 0dc7597e).
- **Helm channel:** the owner told Helm to work with the manager on the keyless dry install.
  - Helm writes hunt29/p1/HELM-REPLY-1024.md; the manager answers in HELM-ASK-*.md in the same dir.
  - HELM-ASK-1024a.md asks Helm to redo section D at 0526d5cd and to run the dry run with the old /etc/mal-h5/LIVE_OK and TIER untouched (no key, mode dryrun).
  - ASK VAAN to OK moving those two files to /root/disabled/ before any live step. Keys, wallets, funding, LIVE_OK/TIER and go-live need Vaan's own OK.
  - structure-monitor opened (#1490).
  - Manager cron :13/:43 checks the folder.
- **LaunchLab closed:** all 13 cells FAIL (n_ZiijNtAh5w2RBA). Backfill #1461 cancelled at 1.82M counted credits.
- **Meteora DBC:**
  - m10 (D1 early momentum) was +0.021/+0.016 SOL/attempt in-sample on 10-14..16.
  - The one-look holdout read #1494 was NOT_DECIDABLE (tool defect: every EvtSwap3 row was mislabelled a hook pool). QP ruling 2a34dfc0 (hunt29/dbc/QP-RULING-1024-dbc-m10-swap3.md).
  - **ONE LAST m10 attempt:** a pre-registered backfill confirmation on [2026-10-25T00Z, 2026-11-01T00Z). P17 (PR #868, Swap3 pricer) is preparing REGISTRATION.md and a window-locked read tool.
  - **TAG dbc-m10-forward-v1 BEFORE 10-25T00Z**, after QP + reviewer on #868; otherwise the window shifts.
  - Fetch each day only after it closes (~0.34M counted credits/day). A PASS only allows a live forward paper book (P18 is designing it on fast-0).
  - Hours 10-17..10-23 are spent for m10/D1. D2/D3/D4/D6 all FAIL on Swap3.
- **P6** (#867): speed does not revive the pump lines. **P15** (#866): the DBC entry leg on fast-0 is p50 ~1.1–1.3 s. **P16** DAMM v2 fetch #1492 is running (strand + post-graduation pricing).
- **Helius counted credits this push:** LaunchLab ~1.9M, DBC sample 1.16M, DBC holdout 2.47M (+ probes). About 5.6M of ~20M.
