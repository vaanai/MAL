# MAL manager handoff — manager14 (rewritten 2026-10-23T20:00Z from date -u)

Read this first. Then read the END of manager14's state log (/data/mal/hunt-1008/ops/manager13/push-1022-manager14.md, a copy of the memory file) and the latest MiScusi notebook entries. No book has cleared the promotion gate. Profit so far: 0.

## 0. Owner direction (current)
- Goals: $20–50/day first, ~$200/day by end of November, recover ~$1,500. The gate is unchanged. 1 SOL to start, more with confidence. API cap $200 to 11-11.
- **10-23 15:00Z, standing OK:** start a C0 live canary as soon as a book's gate read PASSes, provided DEC-032's preconditions hold. The owner's line is quoted verbatim in DEC-032 Am.1 (OWNER_CANARY_STANDING_OK). The per-book DEC copy adds `OWNER_CANARY_CONFIRMED: bot_id=<id>`. Tell the owner in one line when it is used.
- **Not covered by the standing OK:** wallet funding (the owner sends a fresh ~0.5 SOL wallet), Helm's install plus a 1 h dry run, C1 step-ups, and stakes above C0. Line these up as soon as a gate read looks close.
- The manager decides rule, DEC and lane questions and tells the owner afterwards.
- **Push direction:** run new strategies in parallel and keep the slow books (FLOOR, G4) moving. Time is crucial: nearly a month with no profit.
- **Helm, done 10-23:**
  - C1-NF was never live; it is STOPped and disabled.
  - The probe live.conf and the H5 live.conf were moved to /root/disabled/.
  - The probe/H5 wallet reconciles at 0.183342695 SOL and stays put. FUNDED_SOL stays 0.298688847.

## 1. Books and clocks
| Book | Clock | State |
|---|---|---|
| H5 a2 v1+v2 (frozen 1f7a89c) | 2026-10-23T00Z | Counting. **CHECK 10-24 ~00:25Z** that the daily score #1344 counts 10-23's triggers (the day-1 dry run showed skipped_after_window 3 and triggers_seen 0); if not, ask QP. Successors #1332–#1334: check on 11-04. |
| HV4, 9 bots (frozen 9ed4c5f) | 2026-10-25T00Z | Pack haiku-v4/t0-1025/. Records PR #779. Steps on 10-24, see §2. |
| Q50 (h5v1q50-boostfloor-a1) | 2026-10-25T00Z | Sheet hunt24/l3-boost/Q50-EXEC-1024.md. G7 at 10-24 00:12Z. |
| FLOOR l1-floor-v2 (frozen 5e12f8f5) | 2026-10-26T00Z (one slip to 10-27) | See below. |
| G4 v1 | after FLOOR | Port PR #806; equivalence 12/13 on w7. |

**FLOOR detail:**
- **Rules:** DEC-031 Am.9 MERGED 6f431ae3: H = 12 d in one ≤14 d job, P0 rate ≥ 12.5/day on 3 days, a 72 h zero-gap check, an hour_health dry run ≤ 5%, and the feed hand-off on 10-24.
- **Execution sheet:** hunt24/floor-1026/EXEC-AM9-1026.md (953da6bf), with scripts A2-p0count-v3, G1-gapcheck-am9, DR1-hourhealth-dryrun, E3-register-v2 and E4-run-v2.
- **Open:**
  - **D-A:** PumpSwap gap rows at 10-22T19/T20 fail item 5 if E3 must be before 22:00Z. Proposed: an erratum moving the deadline to 23:45Z (E3_LEAD_S=87300).
  - **D-B:** FLOOR is priced at 505,000 per send (Am.8 item 3). Am.9 item 9's wording needs an erratum.
  - **D-E:** the DR1 thresholds JSON.
  - The QP ruling is at hunt24/floor-1026/QP-RULING-1023-am9-erratum.md. Status: see the state log END.
- **10-25 order:**
  - A1 at ≥ 01:10:30Z, then A2-v3.
  - G1 22:05Z, DR1 22:10Z.
  - E3 23:10Z, then E4 and D1 23:20Z.
- **Before then:** the smoke S1–S3 on 10-24 at ~14:00Z, after the feed hand-off. The P0 fixed-first entry goes out 10-24 between 13:00Z and 23:00Z.
- **Also owed:** the H-pin records PR (#811 update), the L3 test job script, and the C9/C5/Q2 items listed in EXEC §A.

## 2. Fixed instants (session crons; FULL prompts in /data/mal/hunt-1008/ops/manager13/crons-manager14-full.json — RECREATE at session start with CronCreate)
- **10-24 00:12Z:** Q50 G7.
- **10-24 01:12Z:** HV4 P0 (A1→A2) and the X job.
- **10-24 12:09Z, FEED HANDOVER:** window 12:10–12:25Z only, per QP-RULING-1023-feed-handover.md (sha256 170bd3bb).
  - Cancel #1207 (j_rw-Xx119yREwew).
  - Check that `pgrep` is empty. A MiScusi cancel leaves scope children alive.
  - Resubmit the same `sh /data/mal/hunt-1008/ops/hv4-feed-shakedown.sh feed` at f5bd26e7 with a 14 d limit.
  - Check there is one process_restart ≤ 600 s.
  - Notebook it, including the new job id; FLOOR's G1 needs it.
- **10-24 12:35Z:** HV4 L8 sampler/table jobs, and #779 to QP and the reviewer.
- **10-24 21:17Z:** HV4 registration night (STEP-3-4-5-6.md): register before 23:00Z, Step 4 at 23:40Z.
- **Recurring:**
  - hourly at :23: Job A lag and lanes;
  - every 2 h at :37: disk;
  - 00:23: digest copy (#1349);
  - 03:23: tip-tape archive;
  - 06:41: structure monitor;
  - 09:13: housekeeping;
  - 12:17: H5 health (record only).
- **Later:**
  - Schedule HV4's in-clock feed restart at ~11-07T11:50–11:58Z, because the new feed job ends at the 14 d cap. The old "#1207 successor before 11-02" plan is superseded by the 10-24 hand-off.
  - #817 ends 10-26 and is not extended.

## 3. Live path (all dry-only, LIVE_BUILT=False)
- **Merged 10-23:** #793 and #815–#832.
  - H5 arena executor with C0 guards and the p155 fee pin;
  - parity P1–P7 with the STOP contract;
  - the tie-out and fetch tools, worst-gap and worst-in-slot;
  - the sell simulate, the twin writer and the flip read;
  - exec replay plus the rpc-label rewrite.
- **md5 decision-equivalence:** EQUAL for all three H5 books (a2 v1 #1410, a2 v2 #1411, Q50 #1412; rolling-sps inputs #1408/#1409).
- **DEC-032 template:** merged; Am.1 2a131bf6 carries the owner's standing line.
- **Still blocking a live send** (hunt28/l1a/QP-RULING-1023-wiring.md, rulings 7/8):
  - the live-entry PR (LIVE_BUILT=True);
  - re-pin and run the twin-writer and tie-out host jobs (hunt28/l1f, l1g);
  - the per-book DEC copy text (T1, the decision_chain substitute, the V-band subset, the +26 bps bias, constant p 0.289, the fee gap, the md5 scope);
  - FEED_NOTE_MERGED;
  - rent 0 on chain;
  - a gate PASS;
  - the owner's funding;
  - Helm's install plus a 1 h dry run.
- HV4's bots have no live executor; only the H5-family books do.

## 4. Push 10-23 results (hunt28/PUSH-PLAN-1023.md)
- **L1 (live tools):** done, see §3.
- **L2:** the gap reclassifier #813 does not unblock 24 h holds; it is low priority.
- **L3 reprice at 160k:** only FLOOR revives, and it was already positive at 505k. Library rows are in #833, which needs a reviewer and then a merge.
- **L4:** four new screens, all FAIL.
- **L5:** HV5 #814 waits for HV4.
- **Next ideas:**
  - a strategy hunt focused on exits;
  - G4 v1 and the H2-PS kit-form reprice;
  - set an executor to exactly 155,000, so that new books get Am.8's 160k price.

## 5. Environment notes
- /data/mal/venv lacks `certifi`, so 11 arena test files cannot collect there.
- `scripts/mal-core/agent-ssh.sh` fails here (missing env), and mal-fast-0 does not resolve from research-0. Helm verifies fast-box units.
- **Open drafts:** #779, #806, #810/#811 (FLOOR records, to update for Am.9), #812 (this file), #813, #814, #833, #798.
- **Scratch to delete later:** /data2/mal/scratch/g4v1/w1hours and /data2/mal/scratch/hunt26 (~11 GB).
