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
| Status at 15:21Z (job #140) | 2/30 attempts, both landed, both exited sl within about 30 s, realized −0.039502 SOL, 0 open |

**Key finding, the first live data.** The two buys landed **15 and 14 slots after the migration** (job #141). EXP-012's edge on exploration data survives only about 8 slots (#292).
- **Cause:** the paper runner writes the `enter` row only at its *simulated* fill (`decision + applied_latency_ms` ≈ 1.9 s, `tools/forward_paper.py` `_queue`/`_fill_one`), and the executor acts on that row. The latency is counted twice.
- **Fix: PR #307** (below).

**Owner question OPEN:** `q_TtklEqCl3wlMfA`, whether to pause new buys (Helm: `sudo touch /var/lib/mal-live/STOP`) until #307 is deployed. I recommended **yes**. Check its answer first.

**Hourly monitor.** This session's cron dies with it. The next session must **recreate the monitor**: a read-only MiScusi job on fast-0 every hour that reads the unit's state/NRestarts/DropInPaths, `--status` with `scripts/mal-fast/probe-executor-live.json` (run as mal-live from `/var/lib/mal/fast-forward/src`), journal lines for halt/stuck/abandon/alert/error, and live fill rows. **Alert the owner** on: a restart, the unit down, a stuck or abandoned sell, any halt, realized loss ≥ 0.15 SOL, or errors. **Never read `/etc/mal-probe` or `/run/credentials`.**

**Commitment to the owner and Helm.** Do NOT redeploy fast-0 src (`install-fast-forward-paper.sh`) while non-pinned live code holds the key without telling them first. Helm's auditd key `malprobe-code` alerts on any write to `/var/lib/mal/fast-forward/{src,venv}`.

## In flight

| Item | State / next action |
| --- | --- |
| **PR #307** `claude/runner-enter-intents` @ `d0883df466b5d2aedfeefc4dae4d9e732d2593c0` | **Runner writes `intents.jsonl` at decision time; the executor acts on it.** Expected saving about 1.9–2.5 s, about 6 slots. Reviewer and security review: **merge-ready** (second pass). Includes the missing-signals-file startup refusal and the exact-pinned-command fence (`scripts/mal-fast/probe-dropin-fence.py`). **Waiting on md5 equivalence job #143** (`j_pMdYuamPoMRtVA`, research-0). It must print `EQUIVALENT` for intents off, and identical decisions/positions md5 with intents on. Then merge. |
| **Deploy #307** (after merge) | **Order matters** (runbook `docs/runbooks/probe-executor.md` §2b/§2c).<br>1. Owner/Helm: STOP new buys, wait for open = 0.<br>2. Helm: switch live to the **root-owned pinned executor** (PR #305) **at the #307 merge sha**. Run `install-probe-executor-pinned.sh <sha> <manifest>` from a fresh root clone, swap `live.conf` for the pinned drop-in, daemon-reload. Give Helm the full sha, the manifest of the 11 installed files, and the sha256 of the pinned drop-in and installer, computed from git at that sha (see #305 for the file list).<br>3. Me: redeploy the runner, which is now allowed because the fence sees the pinned command. `install-fast-forward-paper.sh --commit <sha>`, then restart `mal-fast-forward-paper`, then `test -s /var/lib/mal/paper/fast-forward-paper/intents.jsonl`.<br>4. Helm: start the executor (it refuses live without the signals file), then remove STOP.<br>Record the runner restart in LAB_STATE (a mid-week change) and do it **before 10-06T00Z** if possible, when the forward window opens. |
| **Job #127** (done) | Migration-stream probe, 6 h. 282 migrations; 217 matched with the tip follower's `complete` by mint. Stream earlier by p50 212 ms / p90 996 ms (first in 162 of 217). But the migrate tx lands p50 **3 slots after** `complete`. **Conclusion:** the migration-authority stream alone gains only about 0.2 s on the trigger. The real gain (about 1.4 s, estimated) needs processed subscriptions to **near-graduation bonding curves** to catch `complete` and the final trades. Notebook entry posted. |
| **Job #71** (research-0) | DEC-016 forward walk forward-1002. **Resubmit by about 10-09T15Z** (same command, params `{"start":"2026-10-02T15"}`, resumable, 10080 min). |
| Owner question `q_YBNB8Qi1lR_WjQ` | DEC-018: five live-trial decisions, due about 10-14. |

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
