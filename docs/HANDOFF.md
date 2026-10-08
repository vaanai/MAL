# Manager handoff 2026-10-08 ~20:20Z (manager9 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- **H5 (the live candidate):** `/data/mal/hunt-1008/h5-work/PLAN.md`, `/data/mal/hunt-1008/h5-flows/{RULE,REPORT,VERIFY}.md`, `/data/mal/hunt-1008/JUDGE.md`. The PRs are listed below.
- **EXP-022 CAP-PICK Part 1:** [EXP/EXP-022-cap-pick-part1-prereg.md](../EXP/EXP-022-cap-pick-part1-prereg.md) §0, §2.1, §3, §9, §12, §17, and **Amendment 1**.
- **Memory notes:** `owner-goal-october`, `h5-boostfloor`, `feedback-fan-out`, `exp022-cap-pick`, `openrouter-key`, `audit-2026-10-08`, `feedback-seal-outcome-counts`, `long-jobs-via-miscusi`.
- **MiScusi notebook, 10-08 evening:**
  - H5: n_aKkhDDVDD8NWzA, n_XAOeNSejKdoCqA, n_hZAyYYgXzwukSQ;
  - E0 and scorer: n__JG6lTs4tLlSjQ, n_XhHuUT1hSpMCkw, n_YFa8N7PGPyraOg;
  - hunts: n_cvvYNpFkW0uNOQ, n_qWUXcU6JLYp7cA, n_DJQLZZS9jA-TjA.

## Where things stand

### Owner mandate and decisions (10-08)
- **Goal.** $1000 of REAL income by 10-31, with $400 as the minimum. The owner says "7 SOL in the wallet" from a 1 SOL start.
  - Full autonomy. Fan out many parallel tests.
  - $0 extra budget is unchanged. The sniper route is closed.
- **H5 live, owner-approved.**
  - **Canary.** About 0.25 SOL. The owner sends it to the Helm-held probe wallet on fast-0 **when the manager asks**. The ask comes after the executor is reviewed and EXP-024 is merged.
  - **Gate override, H5 only.** The owner answered "Yes, full 1 SOL" in session to scaling to 1 SOL BEFORE the formal EXP-024 read passes. The conditions are that live execution matches the sim and that the shadow and canary results are not negative, at about 10-12..14. It goes into DEC-024 as `OWNER_OVERRIDE_CONFIRMED`.
  - **Every other book keeps the gate.**
- **OpenRouter.** The key is at `/var/lib/mal/openrouter/openrouter.env` (research-0) with a $3 cap. The runner hard-stops at $1.50. Never print it.
- **LAYA.** Optimize the after-graduation books into a consistent base strategy that runs while short-lived edges rotate.

### H5 BOOST-floor: the one real candidate
- **Rule.** After a non-mayhem graduation, if a sell drains the pool to Q = real quote + V ≤ 40 SOL within 0–300 s of the first print while BOOST still has budget, buy at 1.3 s and sell at s0 + 330 s.
- **Sep confirmation,** read once, 21 days, n 1,124, 0.25 SOL:
  - flat +13.221% [CI90 +8.281], 17/21 days;
  - press +11.434% [+7.165];
  - the verifier reproduced it.
- **Decaying:** +26.7 → +11.7 → +5.4 → +6.8%.
- **October risks:**
  - BOOST's last slice comes earlier, median 337 s (n 8), so the 330 s exit sits near the cliff;
  - since 09-30, v2 trades keep fees in the vault, so per-print V is needed;
  - 200 ms slots from about 10-09T14:30Z.
- **Judge prior** that it is still positive in October: 0.35. Net if real, after haircuts: +1.4..+3.1% per attempt.
- **One BOOST switch-off kills H5, H4 and CAP-PICK together.**

### Other hunt results (exploration tape; JUDGE.md, JUDGE-3.md)
- **Dead:** H1 pre-graduation, H3 hours, H6 census, G2 momentum, G1 post-BOOST dip, L2, L3 primary, LAYA v0, and the LLM trader (Claude −7.57%; selection lift p 0.19).
- **Weak:** H4 mid-BOOST; L1 RULE B (+1.28% flat / +0.81% press; fails under rent and cost stress).
- **H2 wallets:** one wallet (4XWC649w…) carries it.
- **Leads, unverified:** the L1 pool-entry sub-book; L3's hourly top-10.
- **G4 survivors:** reported promising and was being verified when hunt-2 was stopped and resumed. Check hunt-2's results.

### Disk incident (10-08)
- Hunts wrote per-branch tape copies and research-0 hit 81%. Fixed by stopping hunts, Helm's cleanup (56 GB) and `.nobackup` on `/data/mal/hunt-1008` and `/data/mal/audit-1008/{tape,work,tmp}`.
- **Rules for all hunts:**
  - use the shared layer `/data/mal/hunt-shared` (job #393, 9.2 GB, zstd) and no tape copies;
  - a 5 GB budget each;
  - `systemd-run MemoryMax` with `nice 19 ionice -c3`;
  - at most 4 at a time per workflow.
- **Small reports** are copied to `/data/mal/hunt-reports` (backed up).
- **`rm -rf` is denied to this session.** Write a script and ask the owner or Helm to run it.

## In flight at handoff (all on research-0 unless noted)

**Workflows.** Their results land in files even if this session ends. Check them.

| Task id | What | Output |
| --- | --- | --- |
| w9v7nomhy (wf_84831245-793) | Hunt-2: 12 graduated-token and other theses, resumed under the disk rules | /data/mal/hunt-1008/JUDGE-2.md |
| w9b4m533o (wf_0d1a560e-ebe) | Hunt-4: LAYA-filter cascades, analog trader, 9 strategy-tree leaves | /data/mal/hunt-1008/JUDGE-4.md, STRATEGY-TREE.md |
| wo1wq4khh (wf_f304b5f5-877) | LAYA after-graduation consistency optimization: walk-forward harness, 4 rounds, freeze top 2 | /data/mal/hunt-1008/laya-opt/REPORT.md, FROZEN-*.md |

**Builder agents.** If one is gone, check its branch; resume it or start a fresh builder.

| Branch / PR | What | State |
| --- | --- | --- |
| #477 `claude/h5-shadow` | H5 live shadow detector (fast-0, public RPC, no keys) | Head **ff31026**, 86 tests. Replay matched the frozen triggers 72/72 on 09-20, and 11/11 again on hour 09-20T20 at ff31026. The reviewer's SHOULD-FIX 1–7 and the nits are applied: the seal hook `suppress_outcome` fails closed from 10-16T01; there is an exit strip; the sps fit is ready in about 8 s; gap records name each reconnect cause; reject records list both mints. Smoke #395 (old head) passed, with 3/3 CreatePool rejected as non-WSOL (maybe genuine non-WSOL pools; reject records will show) and 3 reconnects in 2 min. **Smoke #396** (10 min, 3 sockets, ff31026) was submitted at handoff: check the reject mints, the triggers, `unannounced_fresh` and reconnects. Next: a quick delta review, then the long MiScusi job on fast-0 (`bash scripts/research/h5-shadow.sh`, `H5_SOCKETS=3`, resumable, ≤1.9 GB, out `$HOME/data/h5-shadow`). **Do not score its outcomes until EXP-024 is merged.** |
| `claude/h5-executor` (PR pending) | H5 live executor on the probe_executor core | 102 tests pushed (d45cfe4). The reconcile tool is being committed. Dry-run by default. LIVE needs a LIVE_OK file, a config flag and EXP-024 merged on main. 0.02 SOL stake, max 2 open, 30/day, stops 0.08 daily / 0.12 total. Live-halt rules. Seal guard (pick_oracle) from 10-16T01. Then reviewer, then ask the owner to fund. |
| #478 `claude/exp024-h5-prereg` | EXP-024 Part 1 plus DEC-023 (family), DEC-024 (canary and owner override), DEC-021 Am.2, DEC-016 Am.7, EXP-012 Am.3, EXP-022 Am.2, ledger | Builder is adding the declared-observation rule and the filled `OWNER_OVERRIDE_CONFIRMED` line. Then quant-proof on the final head. **MUST MERGE BEFORE 2026-10-10T00:00Z** (otherwise refile from 10-11T00, and after that Look 1 is dropped). Cron 7b7a30bc fires at 10-09 18:13Z as a reminder. |
| #476 `claude/h5-boostfloor-score` | H5 scorer port (32/32 cells and every trade reproduced) | Draft. Still needs a forward mode, V(t) pricing, the correction and the day-level t for Look 1 (by 10-16T00Z) |
| #479 `claude/cap-pick-exp022-mode` | `--exp022` mode in cap_pick_score: constants and `exp022_universe()` with walk2 and exploration adapters | Quant-proof OK-WITH-EDITS on 86355b3 (comment 6068166437). Edits done at **be7cdb2**, 229 tests:
  - the exploration source pins `--vmap` to `/data/mal/pumpswap-virtual/pool_v_0909.json`;
  - `--hour-sph-json` must be absent (tape-only hours; an unmeasurable hour refuses);
  - bad-reserves picks stay `status=attempt` with `priced=false`.
  **Next: a quant-proof re-check of 86355b3..be7cdb2, then merge.** The read-tool (P4) items are listed in the PR body. |
| `claude/cap-pick-e0-exp022` (PR pending) | E0 harness switched to `--exp022 --exp022-source exploration --book picks`, U from universe.csv | Builder also fixes a broken test and runs the **real-layout count-only precount on 08-20** (booleans only) |

**MiScusi jobs**

| Job | What | Notes |
| --- | --- | --- |
| #382 | Forward walk 1 (forward-1002) | **Extend or resubmit before ~10-15T11Z** (cron a676221a). Never open its outputs before the FINAL. |
| #391 | T2 B90 full run (#471), 5 configs | Report-only. Read the picks P2–P4 scopes against the §7 kill rule in `ARTIFACTS/lab/cap-pick-t2-boost-exit-predeclare-2026-10-08.md` |
| #394 | OpenRouter arm of the LLM trader | **Done, dead** (notebook n_o6OGSKeiRBB5Rg). Nemotron free −6.64% flat (n 90). DeepSeek paid −6.28% flat (n 40). Both FAIL. The named arm produced no file. Spend $0.08. The LLM-trader line is closed. |

## EXP-022 (CAP-PICK) path to 10-16T01Z

**Merged.** #461 scorer (7c2e587), #462 gate replay (blob 7d208874), #473 harness, #474 Amendment 1. The #387 kill check came back not a kill.

**Remaining, in order:**
1. **Merge #479**, after its edits and a quant-proof re-check.
2. **Merge the E0 harness PR**, after review and quant-proof.
3. **Official E0** on main as a **28 GB MiScusi job**: `run` with the pinned view explore-0814 and day 2026-08-20, no `--dry-run`. Then `check e0.json`.
4. **E0 record amendment,** in a dated EXP-022 amendment with quant-proof. It must include:
   - the four blobs plus every imported module blob (from `e0.json imported_module_blobs`) and the venv versions;
   - `FROZEN.md5`;
   - the first-boot staging hours: forward-1002 creates 10-14T21..10-15T23 (27 staged, 26 opened) plus the feed hour 10-16T00, all inside the buffer;
   - every difference between the exploration and walk2 adapters (from #479's body);
   - the pick_oracle and seal.
5. **Walk 2** submitted at about 10-16T00:30Z (cron 01c21bbd). Run `bash scripts/research/forward-walk2.sh` with params `{"start":"2026-10-16T01"}`, resumable, 10080 min, about 6 GB.
6. **A clean A3 monitor run** before 10-16T01 (the daily cron).
7. **Before 10-23T01:**
   - the read tool (P4), including the open items in #479's body and the notebook entry n_XhHuUT1hSpMCkw;
   - E1 (cron 3d374658);
   - A2.

## First things to do in a new session
1. Run `miscusi_worker_start` with `inbox: true`, name `manager9`.
2. **Recreate the crons with CronCreate.** They are session-only, so they are gone.
   - **Daily structure monitor, 06:41Z.** Research-0, ops, 300 MB, 20 min: `/data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`. Read `halt.*`, `warn.*` and the new `watch.*` (#470). A halt before counting withdraws EXP-022.
   - **Daily decommissioned-probe check, 12:17Z.** Fast-0, the job #385 command. **Update it before the H5 canary is funded**, because it alerts when the probe wallet is active.
   - **Daily tip-tape archive, 03:23Z.** Fast-0, the job #352 command.
   - **One-shots:**
     - 10-09 15:13Z: the slot step;
     - 10-09 18:13Z: the EXP-024 merge check;
     - 10-13 09:17Z: EXP-022 readiness;
     - 10-14 10:07Z: (e′) dry run;
     - 10-14 11:23Z: extend #382;
     - 10-14 12:41Z: probe rent audit;
     - 10-16 00:23Z: submit walk 2;
     - 10-16 06:13Z: E1.
3. **Check the watchdogs on research-0** (they died with the session):
   - **Disk:** warn at 86%; at 90%, SIGSTOP the hunt processes.
   - **CPU:** renice hunt processes to 19 when load is 48 or more.
   - **Reports:** sync `/data/mal/hunt-1008` small files to `/data/mal/hunt-reports` every 10 min.
   Restart them as background loops (see this session's commands in the transcript), or as a cron.
4. **Collect results:** JUDGE-2, JUDGE-4, laya-opt REPORT, job #391, job #394. Report them to the owner.

## Rules that bite
- **The 10-16 FINAL** runs as written at about 10-16T02Z. It is reported compromised, and `exp012_forward` runs without `--strict-lines`. The (e′) dry run is due before 10-15T23Z.
- **EXP-022 seal (§9).** No outcome of a counted CAP-PICK hour is opened, priced or printed from any source except the sealed look. From 10-16T01, H5 (live and shadow) must not trade or price CAP-PICK picks: the pick_oracle reads only a boolean after the FINAL and fails closed.
- **EXP-024 declared observation.** The canary and shadow outcomes in Look 1's window [10-10, 10-16) are watched live. Look 1 is always read and reported as written, and nothing it shows changes EXP-024.
- **Hunts use exploration tape only.** Sealed blocks fresh-0802, fresh-0808 and fresh-0828 are never read.
- **Reserve convention:** PumpSwap rows are PRE-trade with V; bonding rows are POST-trade.
- **Subagents.** Builders stop at about 40 turns and reviewers at about 20; resume them with SendMessage. Builder-type agents can't call MiScusi tools, so the manager submits jobs. A reviewer once detached another agent's worktree, so always tell reviewers to use their own scratch worktree.

## Clocks
| When | What |
| --- | --- |
| ~10-09T14:30Z | 200 ms slots |
| 10-09 | H5 executor review; shadow long run; ask the owner for the 0.25 SOL once EXP-024 is merged and the executor is reviewed |
| **before 10-10T00:00Z** | **EXP-024 bundle merged (#478)** |
| ~10-10..15 | H5 canary live (0.02 SOL stakes) |
| ~10-12..14 | Scale H5 to 1 SOL per the owner override, if the conditions hold |
| before ~10-15T11Z | Extend #382 |
| before 10-15T23Z | (e′) dry run |
| before 10-16T00Z | #476 forward mode for EXP-024 Look 1 |
| before 10-16T01Z | E0 and its record amendment; clean monitor; walk 2 submitted |
| ~10-16T02Z | FINAL |
| ~10-16T07Z..10-17T12Z | EXP-024 Look 1 |
| 10-23 / 10-30 / 11-06 T01 | EXP-022 looks |
| 10-31 | Owner's income goal |
