# Manager handoff 2026-10-05 ~16Z (manager4 on mal-research-0 → next manager)

Replace this page at the next handoff; don't append. Read it first, then [LAB_STATE.md](../LAB_STATE.md) (refreshed 10-05 #302/#303/#306), [CONSTITUTION.md](../CONSTITUTION.md), DEC-015, DEC-016 (Amendments 1–4), DEC-019, and the memory notes `execution-probe`, `exp012-latency-binding`, `console-sync`, `helius-plan-limits`, `profit-focus`.

## Owner direction

> "Push to profitability… be smart… test ideas we genuinely think will work… work like our lives depend on this."

The owner approved a **0.5 SOL live execution probe** (DEC-019) running alongside paper. EXP-012 is the only candidate. The FINAL forward read is about 10-16T02Z.

## THE LIVE PROBE IS RUNNING WITH REAL MONEY

| Item | Value |
| --- | --- |
| Unit | `mal-probe-executor` on fast-0, user `mal-live`, **live since 2026-10-05T14:46:17Z** (drop-in `/etc/systemd/system/mal-probe-executor.service.d/live.conf`, installed by Helm) |
| Code | Deployed src commit `8a6849b4a9dba468c2a1c32cc9106aeba9ea1dc4` (Helm verified 261/261 files) |
| Wallet | `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`, funded 509,528,770 lamports. Key root:root 0400 at `/etc/mal-probe/probe-wallet.json`, delivered via LoadCredential. **Never touch it.** The withdraw address is held by Helm. |
| Limits | 30 buys of 0.05 SOL, ≤ 3 open, stop at 0.25 SOL realized loss, 4 days. STOP file (no new buys, exits continue) / HALT file, both in `/var/lib/mal-live/` |
| Status at 16:22Z (job #149) | **6/30 attempts, all landed at k = 11–15 from migrate (17–22 from complete), 5 sl / 1 tp, realized −0.100536 SOL, 0 open.** Per-buy data: `ARTIFACTS/lab/probe-live-2026-10-05.md` (#310). |

**Key finding, the first live data.** The two buys landed **15 and 14 slots after the migration** (job #141). EXP-012's edge on exploration data survives only about 8 slots (#292).
- **Cause:** the paper runner writes the `enter` row only at its *simulated* fill (`decision + applied_latency_ms` ≈ 1.9 s, `tools/forward_paper.py` `_queue`/`_fill_one`), and the executor acts on that row. The latency is counted twice.
- **Fix: PR #307** (below).

**PAUSED.** At the owner's request, with Helm's machine restarting, the manager placed `/var/lib/mal-live/STOP` at **2026-10-05T16:30:06Z** (job #150). The executor confirms `stop_file_present=True`. 6/30 used, −0.100536 SOL, 0 open. New buys resume only at the end of the deploy block, when STOP is removed.

**Hourly monitor.** This session's cron dies with it. The next session must **recreate the monitor**: a read-only MiScusi job on fast-0 every hour that reads the unit's state/NRestarts/DropInPaths, `--status` with `scripts/mal-fast/probe-executor-live.json` (run as mal-live from `/var/lib/mal/fast-forward/src`), journal lines for halt/stuck/abandon/alert/error, and live fill rows. **Alert the owner** on: a restart, the unit down, a stuck or abandoned sell, any halt, realized loss ≥ 0.15 SOL, or errors. **Never read `/etc/mal-probe` or `/run/credentials`.**

**Commitment to the owner and Helm.** Do NOT redeploy fast-0 src (`install-fast-forward-paper.sh`) while non-pinned live code holds the key without telling them first. Helm's auditd key `malprobe-code` alerts on any write to `/var/lib/mal/fast-forward/{src,venv}`.

## In flight

| Item | State / next action |
| --- | --- |
| **PR #307** (merged `9fa57ea`) | The runner writes `intents.jsonl` at decision time and the executor acts on it. Expected saving is about 6 slots. md5 job #143: EQUIVALENT with intents off and on (95,948 / 18,030 rows; 103 intents). **Not deployed yet.** |
| **PR #311** (merged → `8333ef1`) | Fixes the pinned installer: venv symlinks failed its final check, so it always aborted half-installed. It adds `check-probe-exec-tree.sh`, checks run before any move, rollback on every move, and a corrected keyless dry-run command (mktemp under /run, EnvironmentFile). Security review: merge. |
| **DEPLOY at `8333ef1c018d8211de5b1f454b9f795cb8dbb61d`** (owner + Helm; see the block below) | Not started. STOP is in place since 16:30:06Z (job #150). |
| **Job #127** (done) | Migration-stream probe, 6 h. 282 migrations; 217 matched with the tip follower's `complete` by mint. Stream earlier by p50 212 ms / p90 996 ms (first in 162 of 217). But the migrate tx lands p50 **3 slots after** `complete`. **Conclusion:** the migration-authority stream alone gains only about 0.2 s on the trigger. The real gain (about 1.4 s, estimated) needs processed subscriptions to **near-graduation bonding curves** to catch `complete` and the final trades. Notebook entry posted. |
| **Job #71** (research-0) | DEC-016 forward walk forward-1002. **Resubmit by about 10-09T15Z** (same command, params `{"start":"2026-10-02T15"}`, resumable, 10080 min). |
| Owner question `q_YBNB8Qi1lR_WjQ` | DEC-018: five live-trial decisions, due about 10-14. |

## Live probe status and next pin (manager5, 10-05 ~20:30Z)

**Running:** pinned `a25eb17` since 17:53Z. At 20:26Z (job #178): 21/30 attempts, realized −0.072382 SOL, 1 open. Helm placed STOP for the re-pin.

**Wallet (job #176, 20:16Z):**
- 365,595,877 lamports, from a 509,528,770 deposit.
- By build: `8a6849b` 6 trades −100,536,289 lamports; `a25eb17` 13 closed +8,622,236.

**Next pin: `7004b168f17ef063aff69d8b85c2ec43936194c6`** (DEC-019 Amendment 1 + 1a, plus #324).
- Live config: 90 attempts, loss cap **250,000,000**, priority **500,000**, hard end **2026-10-12T00:00Z**.
- Size 0.05 SOL and max 3 open are unchanged.
- #324: exits no longer double count our own buy; ret was overstated by a mean 0.0010595, n = 19.
- `f87eb48` was never installed.
- Manifest is in the PR #325 thread and the notebook. The paper runner is unchanged.
- Helm re-pins with the same steps as before. After that, the manager:
  - runs the evidence job (13/13 hashes, tree check, fence verdict `pinned`);
  - points the monitor at `7004b16`, with the loss alert at **0.20 SOL**.

**Wallet floor (DEC-019 Am.1a):** about 0.10 SOL worst case (cap hit and 3 open positions at zero); about 0.20 SOL in a realistic bad case.

**Landing baseline at 500k (a25eb17):** 14/14 buys landed 1–3 slots after the state read; 0 more than 3; 0 expiries.

**Monitoring:**
- The hourly monitor is a session cron, so it dies with the session. Recreate it with the pinned `--status`, expecting `current` = the installed pin.
- A durable root timer with Discord alerts has been requested from Helm.
- Hard limits are enforced in code either way.

**Measured today (notebook):**
- **Entry:** a25eb17 lands 5–6 slots after migrate, vs 11–15 on 8a6849b.
- **Exits:** sell decision→send ~30 ms; fills about −11 to −16 bps against the quote.
- **Live vs sim** (`tools/probe_sim_calibration.py`, #322): exit decisions match on 18/19; median pnl gap −1,062,125 lamports. This checks execution, not edge.

**Running research:**
- Job #167: grad-stream 6 h measurement. It ends about 00:30Z and then compares itself to the tip tape.
- Job #177: EXP-012 operating point v2, exploration only. Threshold × k paired against frozen; size and fee are sensitivity only, per the quant-proof blockers.
- 48 h early-arm shadow: `arm-audit.jsonl`.
- No sim result can support a live size above 0.05 SOL (quant-proof).

## State on fast-0 (paper; DEC-015)

- **Tip follower** `mal-fast-tip-follower`: getBlock, parallel fetch (#297), rps 15, 8 workers. **Coverage against chain: 100.000% over [10-05T06,10)** (job #131, #303). Lag 0–3 slots, block-lag p50 about 1.8 s. **About 560k Helius credits/day**; getSlot polling is about half of that, a follow-up to cut it. Memory anon is stable at about 759 MB under a 1 GB limit (0 OOM).
- **Runner** `mal-fast-forward-paper`: started 10-05T05:31:40Z **on probation** (2 days, rows don't count). Reads the tip tape, `pumpswap_virtual: require` (#288, #296). Daily restart and heartbeat timers on. anon grows about 29 MB/h.
- **Seal:** never read runner P&L (`positions.jsonl`, `runner-status*`, `pnl-daily`); only counts, lag and heartbeat.

## Today's results (all in LAB_STATE and the notebook)

- **Kill review (single read, job #121):** PROMOTE none, KILL none, all 8 scored books NOT_DECIDABLE (incomplete). Every mean is below 0 with CI90 entirely below 0 under both fail models; 0/8 days positive (flat), 0/7 (pressure). Priced V-less. These books get no further work.
- **Latency × V (#292, exploration):** pressure-mean CI90 lower bound is 0.01114 at k=1, 0.00117 at k=8, and below 0 at k ≥ 12.
- **Dry-run evidence (#304, #306):**
  - The executor reads state about 250 ms after the paper runner's booked entry.
  - 3 of 20 dry buys simulated 121–196 bps short of the quote. The cause: confirmed-state quote against processed-state simulation, with the market moving in 1–2 slots.
- **Helius (memory `helius-plan-limits`):** the Developer plan includes processed `transactionSubscribe`. Filtering on the migration authority `39azUYF…` costs about 6–18k credits/month (estimate).

## Next steps, in order

1. Check `q_TtklEqCl3wlMfA` (the pause decision), and recreate the hourly probe monitor.
2. Job #143 → merge #307 → coordinate the deploy with the owner and Helm (order above).
3. Plan the processed-commitment fast path from #127. A migration-authority stream alone is not enough (+0.2 s); subscribe to near-graduation bonding curves (processed `transactionSubscribe`, accountInclude = curves past about 90% progress, refreshed from the tip tape) to catch `complete` and the final trades. Measure on paper first. Do it on paper first, with no runner change mid-window without md5 proof and a recorded restart.
4. When the probe ends (30 attempts, 4 days, loss cap or STOP): the DEC-019 §7 lab note, then quant-proof. Join live against paper by mint, reporting the matched and live-only sets. Count the executor-vs-paper latency gap against paper. Withdrawal is Helm's.
5. Cut tip-follower credits (getSlot polling).
6. About 10-09T15Z: resubmit #71. About 10-16T02Z: EXP-012 FINAL → quant-proof → V book (Am.4) → Am.3 at the **measured live k** → owner.

## Gotchas

- MiScusi job shells are `sh`: no `${s:0:10}`; use python.
- Use `/data/mal/venv/bin/python` in jobs on research-0.
- Reviewers may leave `~/.miscusi/repos/vaanai/MAL` detached; `git checkout main` before working.
- Builders stop at about 40 turns; resume them with SendMessage. A worktree deleted after merge cannot be resumed; start a new builder.
- quant-proof enforces "never round favorably". Build tables from raw JSON with floor.
- The Console reads a one-way mirror of fast-0 `~/MAL` (memory `console-sync`). Update `data/console.json` via a PR, then `git -C ~/MAL pull` on fast-0.
