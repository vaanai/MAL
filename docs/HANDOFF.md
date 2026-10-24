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
| FLOOR l1-floor-v2 (frozen 5e12f8f5) | 2026-10-26T00Z (one slip to 10-27) | DEC-031 Am.9 + Erratum 1. 10-24: post P0-FIXED-FIRST-AM9.md (13:00–23:00Z) + smoke S1–S3 after the 12:10Z feed hand-off. 10-25: A1 ≥01:10:30Z, A2-v3, L3, G1 ≥22:05Z, DR1 ≥22:10Z, E3 23:00–23:45Z (E3_LEAD_S=900), E4 + D1 after. Records #811 needs QP + reviewer. Pack hunt24/floor-1026/ (read EXEC-AM9-1026-ERRATUM.md first). Note: P7 found FLOOR's in-sample edge ~55% smaller on unseen fresh-0903. |

## 2. A3 guard (fixed 10-24)
- DEC-031 Amendment 10 (main 024ee714) + code #840 (main 79a93e94): pooled synthetic z-test (p0 75/339, phi 2.0, z > 3.0902).
- The guard runs from tag `a3-guard-pin-1024` = 0ec7fae8 (136cd98 + the check tool and tests) — NEVER from main (main's h5_shadow.py blob fails frozen_guard). Loop #1446 (07:10Z/19:10Z) + successor #1447; dead-man #1319/#1333 stay at 136cd98. First official run 00:57:40Z green, A3_FLAGS_OK.
- Check every guard run's applied log; a HALT now ends Q50.

## 3. Live path (dry; code-complete for Q50)
- Merged 10-24: #836 #838 #839 #843 #846 #847 (live entry, install pack, sell-and-close rescue, tie-out exporter, watchdog, exit-gap writer), #851 EXP-024 note (do not edit after 10-25T00Z), #852 FEED_NOTE flip (Q50 only).
- INSTALL SHA e2471186383c698825d8ada4bcaa7ba635568bc8; Q50 md5 decision-equivalence EQUAL there (#1457). Manifest on fast-0 ~/h5-arena-manifest-e2471186….txt (sha256 cc61c094…).
- HELM-SHEET (hunt29/p1/HELM-SHEET.md, sha256 e403583b…) sent to the owner: keyless dry install + 1 h dry run. Re-pinned job scripts in hunt29/p1/jobs/.
- Still before a first send: Helm install + dry run; chain proofs (hunt29/p1/CHAIN-PROOFS.md); DEC copy fills (hunt29/p1/DEC-copy-h5v1q50-boostfloor-a1.md); Q50 gate PASS; owner line + funding (~0.5 SOL fresh wallet).
- FLOOR has a dry executor (#837, draft) and a C0 concurrency proposal (8 open × 0.02 SOL) in hunt29/p2/DESIGN.md §7.

## 4. Push 10-24 (hunt29/PUSH-PLAN-1024.md) — results
- P3 exits (#844): none beat baseline. P4 four new high-rate ideas (#853): all FAIL. P5 stack model (#841): FLOOR+H5 ≈ $29/$43/$59 a day at 1/2/5 SOL on paper (in-sample), $10–27 at half edge. P7 FLOOR on fresh-0903 (#849; kit PR #848): same sign, ~55% smaller. P9 (#855): library exhausted; G4 v1 not registered.
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
