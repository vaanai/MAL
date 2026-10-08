# Manager handoff 2026-10-08 ~13:00Z (manager9 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- the **10-08 audit note** [ARTIFACTS/lab/audit-2026-10-08/](../ARTIFACTS/lab/audit-2026-10-08/README.md): the judge (`capv_JUDGE.md`) first, then `SYNTHESIS.md`;
- [LAB_STATE.md](../LAB_STATE.md);
- DEC-016 Amendments 2, 5 §7 and 6;
- DEC-014's 2026-10-07 block-budget amendment;
- the [EXP-021 Part 1 pre-registration](../EXP/EXP-021-part1-prereg.md) and its freeze-pin amendment;
- memory notes `audit-2026-10-08`, `feedback-reserve-convention-brief`, `probe-stopped-next-live`, `feedback-top-day-concentration`, `feedback-pinned-exit-gates`, `feedback-real-layout-precount`.

## Where things stand

- **The owner's audit is delivered.** It is the claude.ai artifact "MAL Profit Audit", plus the Console event `audit-1008`.
- **The fast-entry retail thesis is refuted at MAL's ~1.3–1.6 s event-to-landing.** CAP-PICK is the single remaining candidate: EXP-012 picks + seed×1.15 min_out + 300 s wall-clock cap + 55k per send.
  - The judge's inferred estimate: Aug–Sep ≈ +1.5% per attempt, live leg (≈ +1.3 flat / ≈ +1.1 pressure at 0.1 SOL). October ≈ +0.3 to +0.5% (flat ≈ +0.3), P(≤0) about 40%, below running cost at 0.1–0.25 SOL.
  - P(pass) about 10% (5–20%) with DEC-021's trade-level p; about 5–7% with the day-level p proposed in O2 [inferred, judge §2.5].
  - Top-day concentration: per-block top-day shares are 63–78%. On P1, the latest pre-October block (out-of-fold on adjacent days, its threshold cut on the same scores), ex-best-day is −0.29 SOL (live leg, tradable picks) and −0.034 SOL at the deciding cell; oracle is −0.985%.
  - The plan is a kill test whose read costs 0 credits (the walk-2 tape is expected at about 18k credits per hour). The live build (A5) and the canaries (A6/A7) wait.
- **Owner decisions O1–O9 are pending** (Console for_you `fy-20261008-1029-audit`). Do not start anything below that needs them. The recommendations below are the manager's, not the owner's answers. Numbering: Console O1–O5 = SYNTHESIS O1–O5; Console O6 (wind-down) = SYNTHESIS/judge O7; SYNTHESIS O6 (close EXP-009) is not in the Console list; Console O7–O9 are new.
  - **O1:** pause the EXP-021 screen (recommended: yes). Until answered, do **not** run the screen.
  - **O2:** a DEC-021 amendment for a CAP-PICK walk-2 arm with a day-level p (recommended: yes, if the owner wants the test).
  - **O3:** count `[10-10T00, 10-31T00)`, sealed until the 10-16 FINAL is written (second owner of forward-1002 hours `[10-10, 10-16)`). The CAP-PICK Part 1, with all 10 judge fixes, must merge before then. If that is not possible, count from 10-16T01 and run the A11 October report-only check. The judge says not to rush an under-specified pre-registration.
  - **O4/O5:** live canaries and trial terms (recommended: defer until a look is not futile).
  - **O6:** pre-agree the wind-down (recommended: yes).
  - **O7:** the same-slot sniper arena (recommended: not now).
  - **O8:** the real monthly bill split.
  - **O9:** Helm excludes `/data/mal/audit-1008` from the nightly backup.

## First things to do

1. **Become the inbox reader:** `miscusi_worker_start` with `inbox: true`.
2. **Recreate the session crons.** Crons die with the session.
   - **Daily pump structure monitor at 06:41Z.**
     - A MiScusi job on `mal-research-0`, role ops, 300 MB, 20 min.
     - Command: `/data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`.
     - Read `halt.flags`, `warn.flags`, `halt.all_evaluated` in the newest line. "HALT: none" with rules not evaluated is not an all-clear. If a core rule (`boost_share_low`, `boost_last_slice_early`) is not evaluated on two consecutive days, treat that as a halt.
     - **What a halt does:** CAP-PICK counting and any live trading are suspended the same day; hours after the trigger are excluded and the read can end NOT_DECIDABLE, with no retune and no re-read (SYNTHESIS A3, line 281). Before counting starts, a halt means withdraw (judge §5).
     - Expect `ms_per_slot_moved` at epoch 1053, about 2026-10-09T14:30Z (the 200 ms step).
     - The first run, MiScusi job #383 (not PR #383), came back with no halt, all rules evaluated, InitBoost 12/12, last slice median 341.5 s, budget 17.586 SOL, pins unchanged (job #383 log).
   - **Daily decommissioned-probe check at 12:17Z.** A job on `mal-fast-0`, role ops, 200 MB, 3 min, with the same command as job #362.
     - Alert if the executor or the watch timer is active or enabled, the STOP file is missing, attempts > 62, or realized ≠ −0.210755.
     - **Weekly (next 10-14),** also run `tools.probe_rent_audit` on research-0 (the job #363 command). Alert if `balance_now_lamports` ≠ 0.
   - **Daily tip-tape archive at 03:23Z.** A job on `mal-fast-0`, 800 MB, 240 min, with the same command as job #352. Expect `mismatched=0`.
   - **One-shot slot-step check after 2026-10-09T15:00Z.** Confirm that the forward walk's hours after epoch 1053 verify under the 19,500 bound. Read the slot spans only.
3. **Heavy jobs go on research-0 only.** That host has about 96 GB schedulable, and research-0 runs one heavy job at a time. fast-0 jobs stay ≤1.9 GB. Cap audit-style scripts with `systemd-run --user --scope -q -p MemoryMax=3G nice -n 19 …`.

## Running

| Job | What | Notes |
|---|---|---|
| #382 | DEC-016 forward walk, forward-1002 from 2026-10-02T15, on `2bd45f1` | Resumable, 7-day limit. Credits are expected at about 18k per hour after the 200 ms step (DEC-016 Am.6 (f)); 1,872,724 cumulative on the walk since 10-02T15, including #71. Never open its outputs before the FINAL. |
| #371 | DEC-022 Phase A, the processed migrate stream (timing only) | Ends about 10-08T20:30Z. **Do not extend it.** Phase B is stopped per the audit (stop list). |
| builder | `claude/cap-pick-score`, a draft: CAP-PICK scorer port, phase 1 (reproduce the audit book on lab loaders) | Do not merge before the owner's O2/O3. The full reproduction runs as a MiScusi job on research-0. |
| builder | `claude/cap-pick-gate-replay`, a draft: the live EXP-012 gate (`Exp012Online`) replayed over exploration tape, compared with the offline picks the audit scored (judge §4 item 1) | Kill check, 0 credits. If the CAP-PICK scorer cannot match the live gate's pick set (md5 decision-equivalence, judge §4 item 1), CAP-PICK is withdrawn before counting (judge §5). The known 60-minute skip (8.0% of P2–P4 picks) is restated (+3.492), not a kill. |

**Done 10-08:**
- EXP-021 `--freeze` #376 finished, and its pins are merged in #458. The screen is not run.
- The structure monitor (#456) and v1 transactions (#455) are merged.

## Rules that bite

- **The 10-16 FINAL** runs as written at about 10-16T02Z. The order is in DEC-016 Am.5 §7.
  - It **will be reported compromised** (Am.2).
  - It gives no live support through Am.3 (a), because no conservative-k tool exists (Am.6 (g) D).
  - lphist-entered runs once; only the first completed run counts (Am.5 §7 (a)). The full 10-16 order is in Am.5 §7 and in the #413 version of this file.
  - Per the audit, add no new FINAL tooling. The only DEC-016 amendments still possible: the A11 report-only leg (Option Y, merged before the FINAL), and an Am.6 (d) bound change if spans pass 19,500 or the step comes late.
  - **The (e′) dry run is still required by DEC-016** before 2026-10-15T23:00Z: `merge --dry-run` with the latest snapshot standing in for the final map, plus `--snapshot-fetch` for snapshots #2 and #3. Record its merge meta sha256 and commit in DEC-016. If it fails, the failure is recorded and the read proceeds as written.
  - Extra V snapshots (the job #286 command) only reduce closed-pool nulls in (B). They are optional.
- **Seal.** Before the FINAL, runner side files are read only through `tools/runner_timing_read.py` (timing only). Never open forward-walk or forward-paper P&L, positions, decisions or intents rows. A breach is reported as compromising the read; never soften that.
- **Sealed blocks.** fresh-0802 (named by EXP-021 Part 1), fresh-0808 and fresh-0828 stay unread unless a merged pre-registration names the block.
- **Reserve convention.** PumpSwap tape rows are PRE-trade; bonding-curve rows are POST-trade. Price = (vault quote + V)/base. Verify conventions in code before any multi-agent brief.
- **Fees.** 125 bps per leg below 420 SOL mcap on PumpSwap and the bonding curve.
- **BOOST.** 17.585 SOL in about 29 slices, about 12 s apart, ending about 337–345 s after migrate. It can be switched off by `toggle_boost`. CAP-PICK depends on it.
- **v1 transactions.** RPC calls must pass `maxSupportedTransactionVersion: 1`. With 0, a single v1 tx fails the whole `getBlock`. `tools/probe_live.py:568` still sends 0; the executor is stopped and that file is out of scope.

## Live probe (DEC-019): stopped and decommissioned

- **Stopped** 2026-10-07T01:10:50Z, final 62/90 attempts, −0.210755 SOL.
- **Withdrawn** 10-07T15:48Z to the owner's `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi`. The wallet is 0 lamports on chain.
- **Restart** needs Helm for every step, and only after the owner approves a new reviewed build and a gated book:
  1. fund;
  2. reviewed build with an md5 replay;
  3. enable and start the executor;
  4. re-enable the watch timer;
  5. remove STOP last.
- **Ping the owner only when all four are ready:** a fresh-block confirmation, a reviewed build, the 1 SOL, and the gate.

## Calibration E1 (right after the 10-16 FINAL, before the first CAP-PICK look)

- Re-run the probe calibration exactly, with the audit's patched tool (`/data/mal/audit-1008/work/g_sim_live_calibration_rerun/g_probe_sim_calibration_fixed.diff`, applied in the worker PR `claude/probe-calib-convention`, being opened; the code may merge now), on the 10-05..07 tip tape. Its pre-registered predictions are in `g_sim_live_calibration_rerun.md` §7.
- O2 (pending) would name faa3192 via E1 as DEC-021's §4 calibration set. Without it, condition (c) is NOT_DECIDABLE (SYNTHESIS line 282). The earliest CAP-PICK look is day 7 (10-17 under O3), so running E1 after the FINAL is in time.
- The audit's transfer estimate is live − sim +0.84% (CI90 −1.37% to +3.20%), not +1.38%, until E1.
- **Why after the FINAL:** E1 prices the probe's 61 live round trips (the statistic uses the 55 fixed-build trips) on the tip tape. The probe traded the paper runner's EXP-012 decisions, and faa3192's trips fall inside the FINAL window `[10-06T00, 10-16T00)`. DEC-016 Am.3's seal extension bars "pricing the exported entered mints from any outside source" (DEC-016:162), and it does not say whether that covers mints reached another way. E1 never opens the export or a runner file, and earlier calibration runs (job #230, #443/#445) were not recorded as breaches. Still, running E1 before the FINAL would rest on a reading that is not written in the DEC, so it waits. Do not join E1 output to the export or to any runner decision. Under Option Y (count from 10-16T01), run E1 after the A11 report-only read as well, because A11 covers `[10-06, 10-16)`.

## After 10-16

- Decoder, tip follower and runner on per-event V, with an md5 replay. Drop the executor's 300 s V cache.
- Lyra's items (10-07): the paper/live selection gap needs post-10-02 paper decisions, which are sealed until the read. Also: fast-0 latency drift, the dead fast pre-create listener, and the owed labels.
- Clean up `/data/mal/audit-1008/tmp` and bulky `work/` intermediates. Keep `reports/`, the scripts and `tape/`.

## Ops

- **Waiting on the owner/Helm** (console for_you, 10-07): stop the Oracle attn book and observe.attention, retire the V-less books, and move runner-restarts.jsonl so mal-ro can read it.
- **GitHub hiccups:** use `timeout 60 … </dev/null` and retry.

## Clocks

| When | What |
|---|---|
| ~10-08T20:30Z | DEC-022 Phase A #371 ends. Do not extend it. |
| ~10-09T14:30Z | Epoch 1053, the 200 ms step. Expect the monitor's `ms_per_slot_moved` WARN. |
| 10-10T00Z | Last moment for a CAP-PICK Part 1 merge if the owner chooses O3 (count from 10-10). |
| 10-14 | Weekly probe rent audit. |
| Before 10-15T23:00Z | DEC-016 (e′) dry run, recorded in DEC-016. |
| ~10-16T02Z | FINAL (as written; compromised). |
| After the FINAL is written | E1 calibration run (before the first CAP-PICK look). |
