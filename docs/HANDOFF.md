# Manager handoff 2026-10-06 ~04:20Z (manager5, session 2, on mal-research-0 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- [LAB_STATE.md](../LAB_STATE.md), which is stale since 10-05. Update it early: probe numbers, DEC-020/021/018 decisions, backcheck refusal, EXP-015 merged.
- [CONSTITUTION.md](../CONSTITUTION.md)
- DEC-018 (Amendment 1), DEC-019, DEC-020 (§7), DEC-021
- EXP-015 plan v2 (§11 items 8–11)
- EXP-013 plan (Am.7)
- EXP-012-backcheck-0814 (Result)
- the memory notes: `state-2026-10-04` (current state), `feedback-realistic-primary`, `feedback-probe-limits-honesty`, `execution-probe`, `research0-memory`, `profit-focus`

## Owner direction and decisions (10-06)

- **Push for profit, keep live running, research in parallel on research-0.** The owner's reviewers (Warden, the "grok bots") review merged PRs. Treat their notes as information: verify each claim and fix what holds. They caught real problems on #342, #344, #351 and #359 today.
- **DEC-020: Option A (#347).** The 0.25 SOL step waits for the EXP-012 FINAL forward read (~10-16T02Z). No new funding before then. The same-day package is in DEC-020 §7:
  - wallet ~0.35 SOL now; needs ~0.90 at the start (≥0.55 new, ~0.69 if the probe uses its cap room);
  - cap 0.35; LOSS_ALERT_SOL 0.25;
  - Helm's root lockdown is a precondition (§7 item 7).
- **DEC-021: approved; switch delegated to the manager (#357).** A challenger replaces the champion when §6(a)–(e) and §7 all hold, without asking the owner. Before any switch, post a notebook decision and a Console entry with every number. The re-pin is Helm's. A swap never changes size or wallet.
- **DEC-018 Am.1 (#357):** the size ladder is 0.05 probe → 0.25 (after the read) → 0.5. Each rung's size and funding is still an owner yes. The **owner delegated the stop levels to the manager.** At rung 2:
  - DEC-020 caps;
  - no daily cap;
  - divergence stop after 10 (entry/exit vs quote < −200 bps; executor);
  - landing-fail stop (>30% after 10; executor);
  - **manager check:** run `tools/probe_sim_calibration.py` every 5 dec020 trades. If the live−sim residual is < −0.0075 SOL/trade, the **manager places STOP** under DEC-018 Am.1 and tells the owner and Helm the same hour.
- Both owner questions are closed. None are open.

## LIVE PROBE (real money)

| Item | Value |
| --- | --- |
| Build | pinned `faa319227eee420319eed06e85774f64dd2273b1` (unchanged since 10-05T23:09:56Z) |
| Limits | 90 attempts, 0.05 SOL, max 3 open, loss cap 0.25, 500k priority, hard end **2026-10-12T00Z** |
| Last check | 03:33Z (job #227): **43/90, realized −0.068014 SOL**, 0 open, healthy |
| faa3192 group | +0.085348 SOL over 15 attempts (too few to read as edge). All 36 closed at 01:21Z totalled −174,249,232 lamports (job #208). Never call the record "break-even"; it was a loss (memory `feedback-realistic-primary`). |
| Calibration | job #209: exits agree 35/36 (8/8 faa3192). Lab note `probe-calibration-2026-10-06.md` (#353). **Next calibration is due at ≥45 attempts.** |
| Monitor | session cron at :17 (it dies with the session; **recreate it**). Same command as job #227. Alert at −0.20. Helm's `mal-probe-watch.timer` is the durable alarm. |

Never read `/etc/mal-probe`, `/etc/mal-probe-rpc` or `/run/credentials`. Re-pins are Helm's.

**Merged, NOT installed:**
- #348: the dec020 limits profile. `DEC020_END_MS = None` refuses until the owner's end is committed; 15-line manifest.
- #358: the rung-2 divergence and landing-fail stops. A malformed state latches `dec020_state_invalid`, which blocks buys and never exits.

Install only after the 10-16 PASS, the owner's funding and the end instant. Helm re-pins at 0 open, after a dry-run replay old vs new (0 `spend_over_size`). The --status output now ends with the profile/precheck lines; Helm should confirm the watch script greps fields.

## Research state (exploration; nothing is gate evidence)

- **EXP-012 backcheck on explore-0814 (#342/#346): REFUSED_AFTER_READ** (job #207, #359/#360).
  - 1 primary trade was on a no-V pool: 30 no-V pools, 29 absent from pool_v_0814 plus 1 closed account.
  - No reading; **not rerun**. 6 tries are logged (explore-0814 count 7).
  - Scratch is sealed at `/data/mal/exp012-backcheck-0814.refused-job207`. Don't open its rows.
- **Root cause, shared by everything:** the V maps were built from migrated mints' pools only. This also refused **EXP-013 #225** at its pre-pass (1.59% / 675 pools; no outcome, no try spent).
- **Fix in flight:** a complete map, `pool_v_0909.json`. Chain:
  1. #221: walker-c re-walk of 4 resumed EXP-011 hours (running).
  2. #222: install the 6 re-walked hours, verify and dedupe; expect 0 flagged.
  3. #224: 0909 = 0814 + EXP-011 pools + 30 pools.
  4. **#228**: every PumpSwap pool printed in all research views, fetched into 0909. It writes `/data/mal/ops/still-no-v-pools.json`.
  5. **#226**: the closed-account pool `7WQAs8wA…`'s implied V from its own buys, via pumpswap_virtual_history. Add it to 0909 by hand, outcome-blind, BEFORE pinning.
- **Then pin:**
  - one reviewed commit sets `VMAP_0909_SHA256` in `tools/exp015_screen.py` ("PENDING_JOB_224"), and `VMAP_0909_SHA256` plus `V_SHA256` in the EXP-013 table and screen ("PENDING_JOB_228", PR #364);
  - update the tests that assert the placeholders;
  - add the sha and pool counts to the EXP-015 §11 item 11 and EXP-013 Am.7 notes.
- **PR #364** (EXP-013 pin switch, pricing-only): a reviewer is checking it. Merge on MERGE plus your own rc=0 at the reviewed sha.
- **EXP-013 screen:** rerun `PYTHONPATH=$PWD bash scripts/research/exp013-screen-run.sh <new RUN_ID>` after the pin (40 GB, 4 CPU). It has 3 tries, none spent.
- **EXP-015 screen** (#356/#361/#363 merged; plan v2 #352, pinned 77b4582):
  - prerequisites: the 0909 pin and the EXP-011 clean views (P4) from #222;
  - first run `--guards-only`, then the real run as a MiScusi job: 48 GB, 8 CPU, `--max-workers 8` (tape passes capped at 4), out dir `/data/mal/exp015-screen` (chmod 700);
  - the run command is in the #356 PR body;
  - tries are spent at `started`; RUN.lock means a hard kill refuses any rerun, so you'd need a manager ruling;
  - to join DEC-021 walk 2 it must PASS fresh-0808 (Part 2, not built) and register **before 2026-10-16T01**.
- **Fee vs landing study v2 (#355, job #216):** observational. CU-price buckets; the deciding read is on no-tip buys. Extended to a 5h30m limit with a 60k credit cap. When it finishes, write a lab note. Any fee change is a DEC-019 amendment for the owner and Helm.
- **Grad stream (#167/#200):** lead p50 924 ms, coverage 23.4% at threshold 0.90. **#205** re-measures at 0.70 and ends about 07:10Z. Then run `tools.grad_stream_compare` **on research-0**, because it OOMs on fast-0: copy `/var/lib/mal/fast-grad-stream/out-t70` plus the tip-tape hours, and take the window from #205's output `window.txt`.
- **Early-arm shadow read:** due about **10-07T18Z** (see the 10-05 handoff notes in the notebook).
- **Job #71** (forward walk): **resubmit by about 10-09T15Z**, with the same command and params `{"start":"2026-10-02T15"}`, resumable, 10080 min.
- **EXP-012 FINAL forward read:** about 10-16T02Z. Then quant-proof, the V book (Am.4), Am.3. If it PASSes, prepare the DEC-020 same-day package.
- **Tries:** data/tries.jsonl has 80 lines. 9-day pool cumulative 74; explore-0814 7.

## Next steps, in order

1. Recreate the hourly probe monitor cron, and run the probe calibration at ≥45 attempts.
2. Read the #364 review, then merge.
3. Watch #221 → #222 → #224 → #228 / #226. Check `still-no-v-pools.json`, add the closed-pool V, chmod a-w, record the sha256.
4. One pin commit for EXP-013 and EXP-015 (review it), then the EXP-013 rerun, then EXP-015 `--guards-only`, then the EXP-015 run.
5. About 07:10Z: the #205 compare on research-0, plus a notebook entry.
6. #216 done: lab note.
7. Update LAB_STATE.md and the Console (data/console.json, then a fast-0 `git -C ~/MAL pull`) with today's decisions and results.
8. 10-07T18Z early-arm read; 10-09T15Z #71 resubmit; 10-12T00Z probe end (DEC-019 §7 lab note by build, then quant-proof); 10-16 read.

## Gotchas (new today)

- **Deciding cells use the most realistic measured costs** (exit lag 2, the haircut). Optimistic variants are report-only. Check this before any one-shot read.
- **Run `date -u` before writing a timestamp.** Never say "launched" before the job has started. Owner-facing size or funding text keeps the DEC's caveats.
- **Stage files by name, never `git add -A`.** A stray out.txt was committed in #353 and removed in #362. Write pytest output to the scratchpad.
- **Never bulk-remove worktrees by pattern.** I force-removed the previous manager5 session's scratch worktree by mistake.
- MiScusi `after` only starts when the dependency **succeeds**. A refused or failed dependency leaves the dependant queued forever, so cancel and resubmit.
- Gate merges on pytest's own rc, never through `| tail`.
- The seal: never read the runner's P&L files. Probe fills are fine.
