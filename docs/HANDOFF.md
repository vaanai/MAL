# Manager handoff 2026-10-05 ~23:45Z (manager5 on mal-research-0 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read, in this order:
- [LAB_STATE.md](../LAB_STATE.md)
- [CONSTITUTION.md](../CONSTITUTION.md)
- DEC-016 (Amendments 1–4), DEC-018, DEC-019 (with Amendments 1/1a and the notes "Mark source" and "Entry drift logging"), DEC-020
- the memory notes `state-2026-10-04` (current state), `feedback-probe-limits-honesty`, `probe-exit-leak`, `execution-probe`, `exp012-latency-binding`, `research0-memory`, `profit-focus`

## Owner direction (10-05)

- Keep live trading and learn from it. Push for profit. Run research in parallel.
- Research-0 has plenty of memory, so request 32–64 GB for tape jobs.
- The owner plans to add **1 SOL** (timing unknown) for a 0.25 SOL size step. That step needs DEC-020 and the owner's explicit choice.
- The owner's external reviewer (Warden/Helm side) reviews merged PRs and sends findings. Treat them as recommendations. They have caught real problems on #307, #321, #330 and #333. **Fix before relying on a change.**

## LIVE PROBE (real money): state

| Item | Value |
| --- | --- |
| Unit | `mal-probe-executor` on fast-0, user `mal-live`, **pinned `faa319227eee420319eed06e85774f64dd2273b1`** (root-owned `/usr/local/lib/mal-probe-exec`), live since **2026-10-05T23:09:56Z**, STOP removed 23:10:32Z |
| Contents | #331: the tp/sl mark is the post-buy V-priced spot from our own buy tx's `postTokenBalances`, falling back to the fill price. #332: log-only `drift_vs_seed`, with no skip. Plus everything earlier: #307 intents, #314 fence/allowlist/root RPC env, #315 fast exits, #324 no own-buy double count. |
| Limits | 90 attempts, 0.05 SOL, max 3 open, **loss cap 0.25 SOL** realized, priority 500k/side, **hard end 2026-10-12T00:00Z** (DEC-019 Am.1 + 1a) |
| At resume | 28/90 attempts, **−0.153362 SOL**, 0 open; about 0.097 SOL of cap room |
| By build, never pooled | 8a6849b: 6 trades, −100,536,289 lamports. a25eb17: 15 closed, +27,606,913. 7004b16: 7 closed, −80,432,850, including the C71Lk8Ko rug at −47,168,631. faa3192: new. |
| Rollback | `7004b16` dir kept. Pointer swap per the runbook. Helm does it. |
| Never installed | a0bea86 (#330 snapshot mark), 64d4a97 (#331 alone), f87eb48 (150k / 0.35) |
| Watchdog | **Helm's durable `mal-probe-watch.timer`** (root, :07/:22/:37/:52) posts to Discord on: unit down, NRestarts rising, HALT, stuck/abandoned sell, loss ≥ 0.20 SOL, `current` ≠ EXPECTED_SHA (faa3192), or a status failure |
| Manager monitor | Session cron. **Recreate it now.** Hourly read-only job, pinned `--status`, expects `current` = faa3192, alerts at 0.20. Every ~8–10 new faa3192 buys, run `tools/probe_sim_calibration.py` (as in job #187) and report the faa3192 group. |

**Never** read `/etc/mal-probe`, the contents of `/etc/mal-probe-rpc`, or `/run/credentials`. Re-pins are Helm's job: send him the full sha, the manifest (13 lines for the current pin; 15 for a build that includes #348, see the runbook section 2b-dec020), and the steps. Every executor change needs:
- a review;
- a security review if it touches keys or units;
- a calibration replay of closed trades if it changes exits;
- a DEC-019 note;
- **merging only on pytest's own exit code**. #334 merged with a failing test through a `| tail` pipe; #335 fixed it.

## What 10-05 established (numbers in the notebook and lab notes)

**Execution fixes, measured live:**
- Entry now lands 5–6 slots after migration, down from 11–15.
- Sell decision→send is about 30 ms, down from about 1 s.
- Sell fills land about −11 to −16 bps vs quote, down from −326 to −780.

**Live vs sim** ([probe-calibration-2026-10-05.md](../ARTIFACTS/lab/probe-calibration-2026-10-05.md)): exit decisions match 27/28. The one miss is the old 5 s-poll bug. The buy-tx mark agrees best. This is an execution check, not edge evidence.

**Operating point** (#327, exploration):
- Keep threshold 0.8031.
- At k=6, 0.05 SOL and 505k, the pressure mean is 0.00058, CI90 [−0.00012, 0.00131]: about break-even.
- At a 155k fee it is 0.00110, CI90 [0.00040, 0.00182].
- Fees are about 2% of a 0.05 SOL round trip, the probe's binding cost.
- No simulation can support a size above 0.05 (quant-proof).

**Exit re-check** under V, k=6, exit lag 2 ([exp012-exit-veto-2026-10-05.md](../ARTIFACTS/lab/exp012-exit-veto-2026-10-05.md)): keep tp50/sl30. The nested advantage is 0.00000.

**Entry veto drift_gt_25:** quant-proof **FAIL**. 40/46 vetoes were sim misses, scored as fee refunds. It is now log-only.

**Sim 15% migration-price cap vs live:** 5/28 live trades fall outside the cap and made money. No guard.

**Cumulative tries on the 9 exploration days:** **68** in data/tries.jsonl. The running rug study (#336) adds 6, for 74.

## In flight

| Item | State / next action |
| --- | --- |
| **Job #167**: grad-stream 6 h measurement (fast-0, `/var/lib/mal/fast-grad-stream/out`, code 07e07c0) | Ends about 00:30Z, then runs `grad_stream_compare`. Read coverage, lead_ms and rollback. This decides step B (processed near-graduation curve stream → arm earlier). Plan: notebook "Entry-latency plan". |
| **Job #191**: rug-risk exploration (PR #336 @ acad400, `/data/mal/exp012-rug`) | Running on research-0. It counts filled trades only, so misses are never a gain. When done: lab note, append its 6 tries to data/tries.jsonl, then **quant-proof before any live use**. Merge #336 after review. |
| **Early-arm shadow** (runner `arm-audit.jsonl`, on since 10-05T17:44Z) | Read the 48 h agreement and `skipped_migrated` about 10-07T18Z. Early sign: 3/7 too late on confirmed tape. Next step PR-A2 (the executor buys when the pool PDA appears) needs a re-pin plus Helm. Use the per-candidate pass flags and choose the candidate second from when the pool appears (notebook). |
| **DEC-020** (size step 0.25 SOL, proposal, corrected in #337) | Owner question **`q_ZXcPehfeVHPG3w`** (A: after a forward-read PASS, recommended; B: 20 attempts now; neither). Replay job #190: about 16 trades before the 0.35 cap at the median, about 7 before 0.20. The 0.05 probe stops during the step. Nothing trades until the owner answers **and** funds. |
| **DEC-018** owner question `q_YBNB8Qi1lR_WjQ` | Five live-trial decisions, due about 10-14. Still open. |
| **Job #71** (DEC-016 forward walk forward-1002, research-0) | **Resubmit by about 10-09T15Z.** Same command, params `{"start":"2026-10-02T15"}`, resumable, 10080 min. |
| **EXP-012 FINAL forward read** | About **2026-10-16T02Z**. Then quant-proof, then the V book (Am.4), then Am.3 at the measured live k. Then DEC-020 option A, if the owner chose it. |
| Old PRs | #278 (EXP-015 plan, draft), #269 (EXP-014, do not merge before EXP-013's screen), #90 (old draft). Untouched. |

## Next steps, in order

1. Recreate the hourly probe monitor (the cron above).
2. About 00:30Z: read job #167. Write the result to the notebook. If lead_ms is material and coverage high, plan B2 (the runner arm reads grad rows), paper first, with md5 replay proof.
3. Job #191 done: lab note, tries, quant-proof.
4. After about 8–10 faa3192 buys: run the calibration on the faa3192 group (mark_source, mark_shift_bps, drift_vs_seed, exit agreement).
5. About 10-07T18Z: read the early-arm shadow, then decide on PR-A2.
6. About 10-09T15Z: resubmit job #71.
7. When the probe ends (90 attempts, the cap, or 10-12T00Z): the DEC-019 §7 lab note, grouped by build, then quant-proof. Withdrawal is Helm's.

## Gotchas

- MiScusi job shells are `sh`: no `~-`, no `${s:0:N}`. Job #164 printed false MISMATCHes for this reason.
- Gate merges on `rc=$?` from pytest, never on `| tail`.
- `tools/test_dec015_fast_kit.py` has 22 fixture errors in this checkout, on main too (fixture "nothing to commit"). This is pre-existing and environmental.
- The Console reads a mirror of fast-0 `~/MAL` (memory `console-sync`).
- Builders stop at about 40 turns. Resume them with SendMessage. Reviewers stop at 20–30 turns; ask them to write their report.
- The seal: never read the runner's P&L files (`positions.jsonl`, `runner-status*`, `pnl-daily`). Probe fills are fine. For forward-window tape, simulate only mints the probe traded.
