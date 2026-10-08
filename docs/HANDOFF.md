# Manager handoff 2026-10-07 ~16:10Z (manager9 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- [LAB_STATE.md](../LAB_STATE.md);
- DEC-016 Amendment 5 **§7** (the 10-16 (B) rules);
- DEC-014's 2026-10-07 block-budget amendment;
- DEC-020 §9 (size alone is not a profit path);
- the [EXP-021 plan](../EXP/EXP-021-rug-signals-in-selector-plan.md), Amendments 1–3, and its Part 1 pre-registration once merged;
- the probe closing note `ARTIFACTS/lab/probe-final-2026-10-07.md`, as corrected in #445;
- memory notes `probe-stopped-next-live`, `feedback-top-day-concentration`, `feedback-pinned-exit-gates`, `feedback-real-layout-precount`.

## First things to do

1. **Become the inbox reader:** `miscusi_worker_start` with `inbox: true`.
2. **Recreate the session crons.** Crons die with the session.
   - **Daily decommissioned-probe check at 12:17Z.** Job on `mal-fast-0`, role ops, 200 MB, 3 min. Use the same command as job #362. Alert if the executor or the watch timer is active or enabled, the STOP file is missing, attempts > 62, or realized ≠ −0.210755. **Weekly, also run a research-0 job:** `tools.probe_rent_audit` (the command from job #363); alert if `balance_now_lamports` ≠ 0.
   - **Daily tip-tape archive at 03:23Z.** Job on `mal-fast-0`, 800 MB, 240 min. Use the same command as job #352. Expect `mismatched=0`. fast-0 retention starts deleting tape at 10-08 06Z.
3. **Heavy jobs go on research-0 only.**
   - research-0 has about 96 GB schedulable; MiScusi schedules on declared memory.
   - EXP-016/021 tape passes need **72 GB with 3 workers** (#326 hit OOM at 56).
   - fast-0 jobs stay ≤1.9 GB. Decline fast-0 idle notices.
4. **Ollama on research-0 is off** (Helm). Restart with `cd /opt/miscusi && sudo docker compose start ollama` only when research is idle.
5. **Daily monitors: pump structure monitor.** Run it as a daily MiScusi job on `mal-research-0`, role ops, 300 MB, 20 min, from the repo root.
   - Command: `/data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`.
   - Public RPC only, 0 Helius credits, about 150–250 calls. It appends one JSON line per run. It exits 0 even on a HALT, 2 only when the RPC is unreachable, and 1 if the line cannot be written (the summary, with any HALT, is printed first).
   - Read `halt.flags` and `warn.flags` in the newest line. A HALT means a pinned config or program changed, BOOST is off or changed, or the synthetic-migration share is high. Re-pin with `--write-pins tools/pump_structure_pins.json` only after reviewing the change.
   - **"HALT: none" is not an all-clear when rules were not evaluated.** The summary says `HALT: none (N rules not evaluated)` and the line carries `halt.n_not_evaluated` / `halt.all_evaluated`. The `rules_not_evaluated` and `stage_errors` WARNs are not an all-clear either. If a core rule (`boost_share_low` or `boost_last_slice_early`) is not evaluated on two consecutive days, treat that as a halt until someone has checked BOOST by hand.
   - Expect the `ms_per_slot_moved` WARN at epoch 1053 (about 2026-10-09T14:30Z, the 200 ms step).

## Owner direction (10-06 / 10-07)

- **Push to profit.** Run more ideas and challengers, but no setbacks.
- **Quant-proof on every critical-path merge, including lab notes whose conclusions feed a decision.** #443 merged without it and had to be corrected in #445.
- **Rug signals go into the model as inputs** (EXP-021), not as a separate veto layer.
- **"Tell me when we're ready to go live."** That needs four things:
  - a book that clears a fresh-block confirmation;
  - a new reviewed build;
  - the owner's 1 SOL;
  - the gate.
- **Owner usage was ~75% of weekly on 10-07.** Run one or two builders at a time, with targeted reviews.
- **External reviewers are Helm, Lyra and Warden.** Verify their claims, then act.

## 10-16 FINAL read: every tool is merged

- **The rule.** DEC-016 Am.5 §7 is complete: #400, #403, #407, #409, plus the records #410 and #412.
  - V0 changes only on LP Deposit/Withdraw: V0 ← floor(V0·S_after/S_before).
  - Each trade is priced at its entry-slot V0, taking the worse of entry and exit when an LP event falls inside the hold.
  - Candidates are per trade. The entered set is (A)'s. This applies at every k.
  - Unexplained or unresolved pools above 0.1% → refuse.
- **The tools.**
  - #402 LP history and merge (`4c5da8c`);
  - #405 vbook (`5da3349`);
  - #411 sensitivity at k(p50)/k(p90) (`3355ad1`).
- **Still to do before 10-16:**
  - **(e′) dry-run merge before 10-15T23:00Z.** Use `merge --dry-run` with the latest snapshot standing in for the final; it needs `--snapshot-fetch` for snapshots #2 and #3.
  - **More V snapshots** every 1–2 days until 10-15. Use the same command as job #286. New snapshots carry `.fetch.json` sidecars.
  - **DEC-016 Am.3 inputs:** write in k(p50)/k(p90) and the slot_ms export sha, and the runner downtime file.
- **The 10-16 order is in DEC-016 Am.5 §7** and in the previous version of this file (#413).
  - lphist-entered runs once; only the first completed run counts.
  - vbook and sensitivity both take `--vmap-merge-meta --final-fetch-map --snapshot … --lphist …`.
- **Settled (Helm and owner, 10-06):** the ceiling stays as merged. It counts only *unexplained* V0 moves; there is no ceiling on explained LP-rescale moves.

## Research state (10-07 ~16Z)

**Closed or failed at realistic costs:**
- the earlier filters on EXP-012's picks: EXP-013, EXP-015, #191, EXP-018 and EXP-019;
- **EXP-017** (regime gate and score sizing). Its C0 size totals came from one date, 2026-08-21;
- **EXP-014 v2**: SCREEN NONE, family closed.

**EXP-020 (report-only), k × stake:**
- Paired k2 − k6 stays positive under both slot bounds: end-of-slot +0.75 to +1.02% of stake at 0.5 SOL, with the CI90-date lower bound above 0.
- Every kept book is negative once the best date is dropped under the end bound.
- Speed is a real relative lever but not profit on its own. Live k p50 is 5 on the current build, so k2 is not reachable yet.

**Probe close (#443, corrected by #445):**
- Exit lag 2 is a fair primary and 5 the pessimistic leg. The 5 comes from a 500 bps entry-gap cutoff chosen after seeing the tail, so also report the all-trade stop p90 of **10** as a labelled stress leg.
- 9 of 55 fixed-build trades (16%) show sim-entry vs live-fill drift (price fell 10–15% between read and landing). That is an entry-model calibration item.
- True round-trip cost is about 4.44% / 2.79% / 2.59% at 0.05 / 0.25 / 0.5 SOL, before price impact (+22.9 / +51.6 bps on entry at 0.25 / 0.5).

**EXP-021 (main bet), in order:**
1. Done: tool #438, Amendment 3 + V-map pin #442, constancy sample #354, constancy fetch #355 (199 of 208 readable).
2. Final precount **#356** (72 GB) is running. Check its `would_refuse`. The constancy floor is 200 checked pools; if 199 trips it, extend the reserve and refetch.
3. The **Part 1 pre-registration + `--freeze` mode** is being written by a builder (branch `claude/exp021-part1`). Merge it with quant-proof **before** the screen.
4. Then the screen, once (1 try), with V-map pin 1f3e772d…62ec and `--v-constancy-json /data/mal/exp021-constancy/constancy.json`, P1B excluded.
5. A block claim needs DEC-014's budget, p < 0.025 / m.

Before the 10-16 FINAL read, runner side files (arm-audit, heartbeat) are read only through tools/runner_timing_read.py (timing allowlist; per-day counts refused).

**Sealed, unread confirmation blocks:** fresh-0828, fresh-0808 and fresh-0802 (all verified 144/144 h). One stays in reserve.

**Later (Lyra 10-07):**
- **The paper/live selection gap.** 13 of 61 live trips were mints that paper skipped. The paper twin needs paper decisions from 10-02 onward, which are sealed, so do it right after the 10-16 read unless the owner grants a probe-mints-only exception. The k of the live buys can be done now from the tip tape.
- **fast-0 latency drift.** All over-5 s decisions came 18–24 h after the 00:00Z restart.
- **The dead fast pre-create listener** (since 10-02).
- **Owed labels:** the 1,346,200-lamport transfer, the first-N-after-migration cut, and the token-fee line.

## Live probe (DEC-019): STOPPED and decommissioned

- **Stopped:** 2026-10-07T01:10:50Z, by Helm on the owner's decision. Final: 62/90 attempts, −0.210755 SOL, 0 open, faa3192. The wallet tied to the lamport at 0.298774 SOL (#445).
- **Withdrawn:** 2026-10-07T15:48Z. Helm sent 298,768,781 lamports to the owner's `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi`, tx `3h1Fw9SpZ1H4YQZJhTiY38w9ZdBeVycomw7AWLmywRRwZu2UxC6ef8bGViooq9v8aXRww454HyyCb4ELN2BGiMDg`. The wallet is now **0 lamports on chain**, with no token accounts (job #363): 298,768,781 sent + 5,000 fee.
- **Current state:** `mal-probe-executor` is inactive and disabled, `mal-probe-watch.timer` is disabled, and the STOP file stays in place (job #362).
- **Restart:** Helm does every step, and only after the owner approves a new reviewed build and a gated book:
  1. fund the wallet;
  2. install the reviewed build with an md5 replay proof;
  3. enable and start `mal-probe-executor`;
  4. re-enable `mal-probe-watch.timer`;
  5. remove STOP last.
- **Withdrawal guard:** `probe_withdraw` only sends to `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi` (#415).

## Ops

- **Waiting on the owner/Helm** (console for_you, 10-07): stop the Oracle attn book and observe.attention, retire the V-less books, and move runner-restarts.jsonl so mal-ro can read it.
- **research-0 cleanup:** done by Helm. Use `blocks-clean/` or `clean-view/` paths.
- **GitHub hiccups:** use `timeout 60 … </dev/null` and retry.

## Clocks

| When | What |
|---|---|
| ~10-07T18:07Z | Early-arm shadow read (session cron). Evaluate the processed `migrate` trigger and the tx-v0 request fix. Read labels and timing only. |
| Before 10-09T14Z | Resubmit forward walk #71 on main with #454 merged (params `{"start":"2026-10-02T15"}`, resumable, 10080 min). |
| Every 1–2 days until 10-15 | V snapshots (job #286 command). |
| Before 10-15T23Z | (e′) dry run. |
| ~10-16T02Z | FINAL. |
