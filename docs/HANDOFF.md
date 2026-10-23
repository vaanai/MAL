# MAL manager handoff — manager14 (written 2026-10-23T10:19:38Z from date -u)

Read this first, then the END blocks of manager14's state log (/data/mal/hunt-1008/ops/manager13/push-1022-manager14.md, a copy of the memory file) and the latest MiScusi notebook entries. No book has cleared the promotion gate. Profit so far: 0.

## 0. Owner direction (current)
- Goals: $20–50/day first; ~$200/day by end of November; recover ~$1,500. Gate unchanged. 1 SOL to start; more with confidence. API $200 cap to 11-11.
- 10-22/10-23: all-out push; anything honestly profitable goes (rare/small-size OK, stack books, multi-instance); manager decides rule/DEC/lane questions and TELLS the owner after; live sends, wallets, keys, spending and Helm installs still need the owner's own OK.
- Next push (owner, 10-23): new parallel lanes while the queued books run.

## 1. Books and clocks
| Book | Clock | State |
|---|---|---|
| H5 a2 v1+v2 (frozen 1f7a89c) | 2026-10-23T00Z | counting; day-1 dry #1342 OK (n_OntI7srVm3Vq4A). CHECK 10-24T00:20Z daily score #1344 counts 10-23 triggers (dry run showed skipped_after_window 3, triggers_seen 0) — else ask QP |
| HV4 9 bots (frozen 9ed4c5f) | 2026-10-25T00Z | covered by DEC-031 Am.6/Am.7. Smoke PASS (QP ADDENDUM 4), L3 GREEN #1389, pack haiku-v4/t0-1025/ (QP-RULING-1023-pack cf422f41 applied), records PR #779 (head 71c6b048+). P0 fixed-first n_qPU6rSVkg86cXw. Disclosures: risk why_words invalid 33/144; all LLM entry verdicts model_skip in smoke (throughput risk) |
| Q50 (h5v1q50-boostfloor-a1) | 2026-10-25T00Z | sheet hunt24/l3-boost/Q50-EXEC-1024.md (G7 cron 10-24 00:12Z) |
| FLOOR l1-floor-v2 | ON HOLD (was 10-26) | C4(iii) PASS (n_K0r6QAxV4sGcTg). QP-RULING-1023-floor (6aa422a4; n_Kln9WNQr12_bHQ): BLOCKERS (d) 14-day job cap → handovers exclude open 24 h holds (>5% cap); C11 public-feed PumpSwap data_loss gaps (10/48 seen hours) would exclude nearly all 24 h holds. P0 tool PR #810 (K1–K6 fixes in progress); records PR #811; needs own H-pin amendment; 10-21 not a P0 day |
| G4 v1 | after FLOOR | port PR #806; equivalence on the 08-14T12 slice in progress |

## 2. Fixed instants (session crons; list /data/mal/hunt-1008/ops/manager13/crons-manager14.json — RECREATE at session start)
- 10-24 00:12Z Q50 G7; 01:12Z HV4 P0 (A1→A2) + X job; 12:35Z HV4 L8 sampler/table + #779 to QP/reviewer; 21:17Z HV4 registration night (register < 23:00Z, Step 4 23:40Z).
- Recurring: hourly :23 Job A lag; disk :37/2h; 00:23 digest copy (#1349); 03:23 tip-tape archive (job peaked at its 1,990 MB cap 10-23 — watch); 06:41 structure monitor; 09:13 housekeeping; 12:17 H5 health (record only).
- Owed: arena feed #1207 ends ~11-03 → successor before ~11-02 (HV4 window to 11-08T13:05Z); a2 successors #1332–#1334 check 11-04; #817 ends 10-26 (not extended; n_BcwmijeXdgRh2g).

## 3. Rules changed this push (all QP + reviewer, merged)
- DEC-031 Am.6 (90ab73dc): smoke-then-clock for new paper books; outcome-blind per-hour data-integrity exclusions; caps. Tools: arena_hour_health (227f92be), arena_am6_exclusions, arena_am6_cut, arena_am6_read (every covered read goes ONLY through it). Am.7 (c3467607): HV4 H pin.
- Fee-arm probe PASS (ARTIFACTS/lab/feearm-landing-2026-10-23.md): 155,000 priority lands as well and no slower than 500,000. DEC-031 Am.8 MERGED 2835eb45: 160,000/send for NEW books whose executor pins exactly 155,000 buy+sell; TODAY NO EXECUTOR QUALIFIES (H5 55k/150k, bridge+probe 500k); flip-disclosure read code owed before the first covered clock. QP finding: tools/h5_executor.py sends 55,000 (150,000 escalated) (n_IOUX4gYGPieCVw).
- DEC-032 canary template MERGED 6345934a (TEMPLATE, not in force): owner-ask text, C0 limits (≤0.05 SOL stake, 2 open, daily stop 0.10, wallet floor funded−0.25 binding), parity = ≥100 trips + CI90 lo of mean d ≥ −100 bps.

## 4. Next push candidates
0. INFRA (unblocks FLOOR, G4 and every long-hold book). Design: hunt27/infra/DESIGN.md (n_ORRs-tK8Y4qGUQ). Skew test CONFIRMED (hunt27/infra/SKEW-TEST.md, n finding): PumpSwap slot_jump gaps are endpoint skew (212/212 covered; ≤0.25% slots possibly lost). Do NOT change/restart the live feed or tools/arena_feed.py before HV4 ends (pinned; window to 11-08). Next: QP ruling on a pinned post-hoc gap reclassifier (true uncovered ranges) for long-hold books' exclusions; warm-start same-sha handover (md5 replay proof) for >14-day books; pump.fun gaps untested.
1. (#808, #809 merged.) Set a canary executor to exactly 155,000 (unlocks Am.8) + build the Am.8 flip-read code; FLOOR to 10-26; G4 equivalence → port checks → Am.6 pack.
2. Live path: #793 H5 executor finish, parity soft halts, tie-out tool, worst-in-slot in scorer.
3. Re-price the parked candidates at 160,000 (patient-stack sweep hunt26/patient-stack/REPORT.md used 35,000) — exploration only.
4. HV5: fix risk why_words + LLM over-skipping seen in the smoke (new sha, later clock).

## 5. Environment notes
- /data/mal/venv lacks `certifi`: 11 arena test files (candles, disk_reserve, errors, feed, gap_order, gaps, restart, state, state_gaps, state_robust, zst) cannot collect there.
- Open drafts: #779 HV4 records (fills on 10-24), #806 G4 port, #810 FLOOR P0 counter (QP re-review owed), #811 FLOOR records, #798 hunt26 library rows (review+merge), #793 H5 executor, #812 this handoff.
