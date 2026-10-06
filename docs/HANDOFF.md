# Manager handoff 2026-10-06 ~13:15Z (manager7 → next manager)

Replace this page at the next handoff; don't append. Read it first, then read:
- [LAB_STATE.md](../LAB_STATE.md) (updated 10-06 ~05:50Z);
- [CONSTITUTION.md](../CONSTITUTION.md);
- DEC-016 Amendments 4–5, DEC-018 Am.1, DEC-019, DEC-020 §7, DEC-021;
- [EXP-016 plan](../EXP/EXP-016-rug-veto-plan.md) (§11–§13);
- the memory notes `state-2026-10-04`, `rug-filter-priority`, `feedback-realistic-primary`, `feedback-probe-limits-honesty`, `pumpswap-virtual-reserve`, `profit-focus`.

## Owner direction (10-06)

- **Push for profit; keep live running.** No new funding or size step before the 10-16 read (DEC-020 Option A).
- **Rug filter.** After live gap losses of −76.5% and −95.5%, the owner asked for a real rug filter and a tilt toward big winners. Work on challengers **in parallel** with the 10-16 work.
- **No "critical errors" that set us back.** Every merge on the critical path gets quant-proof and is merged only when it clears.
- **Autonomy.** Owner questions: none open. The inbox reader is manager7; the successor should call `miscusi_worker_start` with `inbox: true`.

## Must do immediately

1. **Recreate the hourly probe monitor** as a session cron at :17, with the same command as job #233 or #275. Crons die with the session. Alert at realized ≤ −0.20 (notebook finding plus a message to the owner).
2. **Recreate the V0-constancy check** (it was a one-shot cron at 13:53Z and dies with this session) if it hasn't run.
   - Re-fetch the pool set of forward snapshot #2, `/data/mal/pumpswap-virtual/forward-1002/work-20261006T074427Z/pools.json`, into a NEW work dir with `tools.exp012_forward_vmap fetch --new`.
   - Compare `v_base`, per pool, between the new `*.detail.json` and snapshot #2's `vmap.json.detail.json`.
   - Any difference means merge would refuse on 10-16. Investigate before then.

## LIVE PROBE (real money; DEC-019)

| Item | Value |
| --- | --- |
| Build | pinned `faa319227eee420319eed06e85774f64dd2273b1` |
| Status at 12:35Z (job #275) | **57/90 attempts, realized −0.167875 SOL**, 0 open, healthy. That is 0.032 above the −0.20 alert and 0.082 from the 0.25 cap. Hard end 2026-10-12T00Z. |
| Paused | Since 08:13Z. The runner's paper EXP-012 **ceiling** ledger hit its `daily_loss_cap`, and intents come only from that ledger (`forward_paper._intent`). Signals resume at the 00:00Z daily restart. Designed, not a fault (notebook; seal disclosure: skip labels were read, no amounts). |
| Calibration (job #264, 57 attempts) | faa3192: 28 closed, **28/28 exits agree**, live −14,007,586 vs sim −27,038,518 lamports, live−sim mean +465,390 / median +34,540. 13 tp, 15 sl; 3 gap losses (27v59dSy, AWkPgsKg, 3BiUnVxR, −128M total; the sim reproduces each). On faa3192 it is a small **loss**, about −0.0005 SOL/trade. Never call it break-even. |
| Duplicate monitor | Stopped (manager5 deleted its cron). |

## Key result today (exploration context, not gate evidence)

**Frozen EXP-012 at realistic costs** (V, k=6, exit lag 2, haircut, 0.05 SOL, 505k fee) on 27 non-P1 dates: n=2,349, **flat −0.00044 / pressure −0.00054 SOL per trade** (EXP-015 report.json, bar 3).
- This matches the live faa3192 record.
- At 0.05 SOL the champion is about flat to slightly losing, because the fixed fee (~2% of size) eats the small edge.
- Size is the only fee lever, and DEC-020 §1 is right that no simulation shows 0.25 works. The 10-16 FINAL plus Am.3(a) at 0.5 SOL trial terms is the decision point.

## 10-16 FINAL read: tooling merged and reviewed

**Merged after 4 quant-proof rounds:**
- #374 `tools/exp012_forward_vmap.py`;
- #375 `tools/exp012_forward_vbook.py`, book (B), single-use per window;
- #378 `exp012_forward_sensitivity.py` with V; Am.3(a) on (B) needs a (B) PASS;
- #376 DEC-016 Amendment 5, which records the commits.

**V parser fixes:**
- #383 (merge `6300915`): signed V, `parse_virtual_detail`, base-V constancy at merge, zero-V report counts V ≤ 0.
- #386: wording, "null pools are V0=0, not closed".
- Lab note #384, `ARTIFACTS/lab/pumpswap-v-layout-2026-10-06.md`: **stored V = V0 − A − B** (pending counters). The 321 "null" pools are V0 = 0 pools.

**Forward V snapshot #2** (job #259, fixed decoder):
- 52,697 pools, 0 null;
- sha `f8547a29…453c`, detail sha `1fca63ea…dbe`;
- in `/data/mal/pumpswap-virtual/forward-1002/snapshots-v2/`.
- Snapshot #1 (#245) predates the fix and is unusable.
- Pending on V0≈17.58 pools: 9 above 0.01 SOL, max 0.1319 SOL (≤ ~0.1% price error).
- **Take more snapshots** every day or two until 10-15, with the same commands as #259 and a new work dir each time.

**10-16 order:**
1. FINAL (A).
2. `pools --final-out-dir`.
3. `fetch --new` (new file, after the cutoff).
4. `merge` with the v2 snapshots and `--pools`.
5. `validate --final-out-dir`.
6. vbook (`--vmap-merge-meta`).
7. Runner latency export.
8. Sensitivity on (B) with `--vbook-report`.

Still needed before 10-16 (DEC-016 Am.3): k(p50)/k(p90) and the slot_ms export sha written into DEC-016, and the runner downtime file.

**Not deployed:** #387 (merged code). The tip follower stamps signed V. Previously V0=0 pool rows got ~1.8e19, though fresh-migration EXP-012 entries are unaffected. Deploy needs an md5 decision-equivalence replay plus a recorded restart (CLAUDE.md), so pick a window. The live probe executor runs from its pinned copy and is unaffected.

**Simulator finding** (notebook): `latency_curve` never reads `pool`, so fills **and** the migration clock can come from a foreign PumpSwap pool. EXP-016 filters rows; EXP-012 reads are unchanged. Open: an outcome-blind count of foreign-first mints on exploration data, to size it and disclose before 10-16 if material.

## Challengers

| Family | State |
| --- | --- |
| EXP-013 | **Screen FAIL**, job #236 (#388). Closed. Its block 0828 stays reserved, unread. |
| EXP-015 | **Screen FAIL**, job #242 (#390). 0/6 bars on 3 configs. Closed. fresh-0808 released, unread. |
| EXP-016 rug veto | Plan merged #381 after 5 quant-proof rounds; prior ≈15% screen / ≈5% confirm. Post-pin edits #393. PR 1 (label, features, P2 filter) merged **#392** after 3 rounds. **PR 2 (screen)** in progress, see below. |
| EXP-014 | PR #269 open (DO NOT MERGE, it was waiting on EXP-013). Needs a confirmation block: 0808 and 0828 are both reserved with no owner now. 0802 goes to EXP-016 first (§8). |

**Lab-wide α (#382):** the four confirmation families each need p ≤ 0.0125. With EXP-013 and EXP-015 closed, keep 0.0125 (it only ever tightens).

**EXP-016 PR 2:**
- The builder agent hit its turn limit and was told to push WIP to branch `claude/exp016-screen` as a DRAFT PR and continue. Check `gh pr list`.
- It must have quant-proof before merge.
- Then build the fixed-parser V maps for P1–P4 and 0802 (pool-field only), pin their sha by a reviewed commit, run `--guards-only`, then the screen as one MiScusi job (48 GB).

**fresh-0802 walk** (ledger #380, reserved for EXP-016 first):
- w1 #248 at 19/48 hours (171,623 credits);
- w2 #249 at 14/48 (124,708);
- w3 #250 queued after w1.
- About 1.4M credits in total.
- Then verify (both modes), dedupe and a clean view, counts and hashes only, the same as fresh-0808.

## Other clocks

- **Forward walk #71:** through 10-06T10, 1.24M credits. **Resubmit by 10-09T15Z** with the same command and params `{"start":"2026-10-02T15"}`, resumable, 10080 min.
- **Early-arm shadow read:** about 10-07T18Z. Fold in the grad-stream result:
  - at threshold 0.70 (#205/#253), coverage of `complete` is 24.7%;
  - processed `migrate` covers 264/287 with a p50 lead of 1,012 ms. This is the better arm trigger to evaluate.
- **Probe hard end:** 10-12T00Z. Then the DEC-019 §7 lab note by build, plus quant-proof. Include the daily-cap coupling.
- **EXP-012 FINAL:** about 10-16T02Z.

## Gotchas (new today)

- **Test isolation.** `tools/conftest.py` (#385) restores `MAL_PSV_*`/`MAL_EXP015_*` env after each test. `test_dec015_fast_kit` errors on research-0 (git identity in its temp repo); that is environmental.
- **Use the venv for tests.** Builders sometimes use system python with no pytest. Tell them to use `/data/mal/venv/bin/python -m pytest`.
- **MiScusi jobs run under `sh`.** `${PIPESTATUS[0]}` fails there; use `> log; RC=$?`.
- **Memory slots.** With two 48 GB jobs running, small jobs queue for memory. Request ≤ 3.5 GB for light jobs.
- **Prompt filter.** A PreToolUse hook blocks commands containing the word "secrets".
- **fast-0 "idle" notices.** Decline them; fast-0 runs the probe and runner (≤ 1.9 GB jobs only).
- **Docs to correct.** Docs written before #383 call the null pools "closed accounts". #386 fixed the main ones; fix others when you see them.
