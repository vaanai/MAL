# Manager handoff 2026-10-06 ~16:30Z (manager8)

Replace this page at the next handoff; don't append. Read it first. Then read:
- [LAB_STATE.md](../LAB_STATE.md);
- [CONSTITUTION.md](../CONSTITUTION.md);
- DEC-016 Amendments 4–5, **especially Am.5 §7**;
- DEC-019, DEC-020 §7 and DEC-021;
- the [EXP-016 plan](../EXP/EXP-016-rug-veto-plan.md) §11–§13 (items 1–12);
- the memory notes `state-2026-10-04`, `feedback-real-layout-precount`, `feedback-realistic-primary`, `rug-filter-priority` and `pumpswap-virtual-reserve`.

## Owner direction (10-06)

- **Push for profit; no setbacks.**
  - Every critical-path merge gets quant-proof.
  - Every try-spending run first gets a real-layout count-only pass (owner's reviewer, 10-06).
- **Rug filter (EXP-016)** in parallel with the 10-16 work.
- **Challengers.** The owner asked whether to run more than 3 at a time. Manager's answer (to the owner, 10-06):
  - widen the cheap exploration-screen stage to 6–10 ideas per batch;
  - keep confirmation one-shot and narrow, with the α split only over the confirmed ones;
  - keep forward races at 3 or fewer (DEC-021 power table);
  - add fresh confirmation blocks with the Helius credits, about 1.4M per 6-day block;
  - draw new edge from new information (rug signals, creator funding, speed, size), not more retrains.
- **Open for the owner** (Console for_you): the 10-16 merge ceiling caps only *unexplained* V0 moves. The owner asked to "refuse if too many pools move". Quant-proof accepts this on technical grounds. If the owner wants a hard ceiling on explained moves as well, add it before 10-16.

## 10-16 FINAL read: all tools are in place

- **Finding (#400).** V0 = V + A + B changes only on PumpSwap Deposit/Withdraw: `V0 ← floor(V0·S_after/S_before)`, where S is the LP supply. That is exact on 6/6 pools and 24 ops. Lab note: `ARTIFACTS/lab/pumpswap-v0-lp-law-2026-10-06.md`.
- **DEC-016 Am.5 §7, final** (#400, #403, #407, #409, plus records #410 and #412):
  - (0) deadline: met;
  - (a) LP history with completeness checks and retries;
  - (b) LP-law merge: unexplained or unresolved pools above 0.1% → refuse, and such pools are written null;
  - (c) entry-slot V0, worse-of across the hold, per leg and per k;
  - (c′) the entered set is (A)'s, candidates are per trade, up to 64 combinations, every k;
  - (d) sensitivity lines, with any flip going to `live_blockers`;
  - (e) LP supply and slots recorded on fetch;
  - (e′) a **dry-run merge before 10-15T23:00Z**.
- **Tools merged with quant-proof OK:**
  - #402 `tools/pumpswap_lp_history.py` + vmap `lphist`/`diffs`/`merge --dry-run` (merge `4c5da8c`);
  - #405 vbook (`5da3349`);
  - #411 sensitivity (`3355ad1`).
- **Live smokes** (pool fields and tx logs only; **none is the (B) run**; every lphist run is logged in the notebook):
  - #288, #290, #294: 100/100 fresh pools resolved and 6/6 LP-active pools resolved;
  - the dry-run merge from #2 to #3 had 0 unexplained and 0 unresolved pools.
  - Two blockers were caught only by the smokes, and both are fixed: truncated MigrateV2 logs (fixed by self-CPI decode) and short accounts.
- **Snapshots v2:** #2 (job #259) and #3 (job #286, `f07bdffb…bbca`, 54,666 pools). Over 7 h only the 6 LP-active pools moved.
  - **Take more snapshots every day or two** until 10-15, using the same command as #286. New snapshots now carry a `.fetch.json` sidecar.
- **10-16 order:**
  1. FINAL (A).
  2. `pools --final-out-dir`.
  3. `fetch --new`.
  4. `diffs` and `lphist` on moved pools.
  5. `merge` with the snapshots, `--snapshot-fetch` and `--lphist`.
  6. `validate`.
  7. `vbook lphist-entered` (one run only; the first completed run counts).
  8. vbook with `--final-fetch-map`, `--snapshot` and `--lphist`.
  9. Runner latency export.
  10. Sensitivity with the same LP inputs.
  - Still needed by DEC-016 Am.3: the k(p50)/k(p90) values and the slot_ms export sha written into the DEC, and the runner downtime file.

## EXP-016 rug veto (the owner's priority)

- **Merged:** #395 (screen), #404 (constancy tool with reserve, dust rule and retries), #406 (pre-read fixes), #408 (`data/tries.jsonl` now has EXP-013's and EXP-015's tries, 97 lines).
- #406 contains:
  - P1B creates loaded from day files, with create slot = first bonding print;
  - pre-tape and gap caps;
  - `--precount`;
  - data-quality limits checked before `started`;
  - a compact, fail-safe row store at 245 B/row.
- **Real layout:**
  - `--precount` at 8779aee **ran out of memory at 48 GB** (job #295).
  - The compact store measured 3.14M rows in 812 MB (job #298, cancelled).
  - **Job #299 (`--precount` at f8e3e7e) is running.** Log: `/data/mal/ops/exp016-precount5.log`; counts go to `/data/mal/exp016-precount-*/precount.json`.
  - Watch `rows_stored_whole` and `unknown_row_keys`.
- **Next, in order:**
  1. Review the #299 counts. Adjust the data-quality limits only with a dated §13 note, before the pin.
  2. Pin `VMAP_EXP016_SHA256` = sha of `/data/mal/pumpswap-virtual/pool_v_exp016.json`, `1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec` (job #289: 226,073 pools, 0 null). Do it in a reviewed commit.
  3. `--emit-constancy-sample`.
  4. `tools.exp016_constancy` job (≤5 rps).
  5. `--guards-only`, then a full `--precount` with the constancy file.
  6. The screen itself, as one 48 GB MiScusi job (6 tries).
- **Expected constancy refusal on honest data:** about 0.1%. 0.24% of band pools are LP-active, and 9 pools have pending above 0.002 SOL.
- **Confirmation block fresh-0802** (walkers #248 and #249 running, #250 queued) is reserved for EXP-016.

## Live probe (real money; DEC-019)

- 57/90 attempts, realized −0.167875 SOL, on faa3192.
- **Paused by the runner's daily cap since 08:13Z**; it resumes at 00:00Z.
- Hard end 10-12T00Z.
- Hourly monitor: a session cron at :17 (command in job #293). Recreate it if this session ends. manager7's cron is deleted.
- **Live entry latency on faa3192** (job #285): k_mig p50 5, p90 6, max 15. About 3 of those slots are tip-follower block lag.

## Gotchas found today

- Solana **tx version 1** exists. `migration_stream_probe` and `fast_grad_stream` request v0 and may miss events. The tape tools use v1.
- `fast_tip_follower` caches V per pool once, so paper rows on LP-active pools carry a stale V. Live is unaffected.
- Builders hit about 40 turns; resume them with SendMessage. Precount and screen jobs need real-layout memory checks.
- fast-0 "idle" notices: decline them, because the probe and runner live there.

## Clocks

- Forward walk #71: **resubmit by 10-09T15Z** with params `{"start":"2026-10-02T15"}`, resumable, 10080 min.
- Early-arm shadow read: about 10-07T18Z. Also evaluate the processed `migrate` trigger: 264/287 covered, about 1 s lead. Fix its v0 tx request.
- Probe end 10-12T00Z, followed by the DEC-019 §7 lab note.
- (e′) dry run before 10-15T23Z.
- FINAL read about 10-16T02Z.
