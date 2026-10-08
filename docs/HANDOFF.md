# Manager handoff 2026-10-08 ~13:30Z (manager9 → next manager)

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
  - Honest odds, from the judge: about +1.5% per attempt Aug–Sep, about +0.3% to +0.5% for October, P(pass) about 10%.
  - The plan is a zero-credit kill test. The live build (A5) and the canaries (A6/A7) wait.
- **Owner decisions O1–O9 are pending** (Console for_you `fy-20261008-1029-audit`). Do not start anything below that needs them.
  - **O1:** pause the EXP-021 screen. Until answered, do **not** run the screen.
  - **O2:** a DEC-021 amendment for a CAP-PICK walk-2 arm with a day-level p.
  - **O3:** count from 10-10T00Z. The CAP-PICK Part 1, with all 10 judge fixes, must merge before then. If that is not possible, count from 10-16T01 and run the A11 October report-only check. The judge says not to rush an under-specified pre-registration.
  - **O4/O5:** defer.
  - **O6:** pre-agree the wind-down.
  - **O7:** sniper arena, not now.
  - **O8:** the real monthly bill split.
  - **O9:** Helm excludes `/data/mal/audit-1008` from the nightly backup.

## First things to do

1. **Become the inbox reader:** `miscusi_worker_start` with `inbox: true`.
2. **Recreate the session crons.** Crons die with the session.
   - **Daily pump structure monitor at 06:41Z.**
     - A MiScusi job on `mal-research-0`, role ops, 300 MB, 20 min.
     - Command: `/data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`.
     - Read `halt.flags`, `warn.flags`, `halt.all_evaluated` in the newest line. "HALT: none" with rules not evaluated is not an all-clear. If a core rule (`boost_share_low`, `boost_last_slice_early`) is not evaluated on two consecutive days, treat that as a halt.
     - Expect `ms_per_slot_moved` at epoch 1053, about 2026-10-09T14:30Z (the 200 ms step).
     - The first run, #383, came back with no halt, all rules evaluated, InitBoost 12/12, last slice median 341.5 s, budget 17.586 SOL, pins unchanged.
   - **Daily decommissioned-probe check at 12:17Z.** A job on `mal-fast-0`, role ops, 200 MB, 3 min, with the same command as job #362.
     - Alert if the executor or the watch timer is active or enabled, the STOP file is missing, attempts > 62, or realized ≠ −0.210755.
     - **Weekly (next 10-14),** also run `tools.probe_rent_audit` on research-0 (the job #363 command). Alert if `balance_now_lamports` ≠ 0.
   - **Daily tip-tape archive at 03:23Z.** A job on `mal-fast-0`, 800 MB, 240 min, with the same command as job #352. Expect `mismatched=0`.
   - **One-shot slot-step check after 2026-10-09T15:00Z.** Confirm that the forward walk's hours after epoch 1053 verify under the 19,500 bound. Read the slot spans only.
3. **Heavy jobs go on research-0 only.** That host has about 96 GB schedulable, and research-0 runs one heavy job at a time. fast-0 jobs stay ≤1.9 GB. Cap audit-style scripts with `systemd-run --user --scope -q -p MemoryMax=3G nice -n 19 …`.

## Running

| Job | What | Notes |
|---|---|---|
| #382 | DEC-016 forward walk, forward-1002 from 2026-10-02T15, on `2bd45f1` | Resumable, 7-day limit. Credits are about 18k per hour after the 200 ms step. Never open its outputs before the FINAL. |
| #371 | DEC-022 Phase A, the processed migrate stream (timing only) | Ends about 10-08T20:30Z. **Do not extend it.** Phase B is stopped per the audit (stop list). |
| builder | `claude/cap-pick-score`, a draft: CAP-PICK scorer port, phase 1 (reproduce the audit book on lab loaders) | Do not merge before the owner's O2/O3. The full reproduction runs as a MiScusi job on research-0. |

**Done 10-08:**
- EXP-021 `--freeze` #376 finished, and its pins are merged in #458. The screen is not run.
- The structure monitor (#456) and v1 transactions (#455) are merged.

## Rules that bite

- **The 10-16 FINAL** runs as written at about 10-16T02Z. The order is in DEC-016 Am.5 §7.
  - It **will be reported compromised** (Am.2).
  - It gives no live support through Am.3, because no conservative-k tool exists (Am.6 (g) D).
  - Per the audit, add no new FINAL tooling or amendments.
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

## After 10-16

- Re-run the exact probe calibration with the patched tool on the tip tape. Live − sim is +0.84% after the reserve-convention fix, not +1.38%.
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
