# MAL Lab State

Compact reload for managers. **As-of:** 2026-09-27 UTC. `main` through [#112](https://github.com/vaanai/MAL/pull/112) (`ede0fa3`). **Paper only.** Two hosts: Oracle `mal-core-0` and OVH `mal-fast-0`. How to reach them: [docs/HOSTS.md](docs/HOSTS.md).

## Objective

**Profit.** North star: durable **SOL after fees** on Pump.fun / Solana meme flow.

Path: full trade tape → honest simulator → signals → forward paper → gated live. Live starts only after a book clears the promotion gate **and** the owner approves.

Edge is information plus modest latency versus humans and copy-traders, not MEV / Jito / colocated snipers. **LAYA** in this repo is our LightGBM entry and exit models behind a rules risk gate ([docs/research/laya-engine-options.md](docs/research/laya-engine-options.md)).

## Hard fences

- Paper only. **No trading keys, wallet keys, or X keys** on either host. Signing stays off-host until live is authorized.
- Postgres **localhost-only**. Never guess or commit the DB password.
- **Port 22 is never public.** SSH for agents is Cloudflare Access. **Stop on a host-key mismatch.**
- Do not touch `/opt/miscusi` on `mal-fast-0`. It is an unrelated app.
- Sealed **JSONL** is the provenance spine. Postgres is ops/state only ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md)).
- GitHub is the source of truth. Lab notes that used to live only in the project store are in this repo (`docs/`, `ARTIFACTS/lab/`, `ARTIFACTS/daily/`).

Non-negotiables: [CONSTITUTION.md](CONSTITUTION.md). Workflow: [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md).

## Hosts (2026-09-27)

| Host | Role | SSH |
| --- | --- | --- |
| `mal-core-0` | Oracle, aarch64, archive + training. Resized to 4 OCPU / ~24 GB (was the 2/12 Always Free envelope). | `ssh.tradervaan.com` |
| `mal-fast-0` | OVH Frankfurt, x86_64. Fast listeners. **Main focus now.** | `ssh-fast.tradervaan.com` |

Detail, units, logs, and what is safe to restart: [docs/HOSTS.md](docs/HOSTS.md).

## Decisions index

| DEC | Topic |
| --- | --- |
| [DEC-001](DEC/DEC-001-lean-four-override.md) | Lean four seats |
| [DEC-002](DEC/DEC-002-memory-first-no-db-local.md) | Memory-first; JSONL spine |
| [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md) / [DEC-004](DEC/DEC-004-regime-id-encoding.md) | Regime-at-ingest v0 |
| [DEC-005](https://github.com/vaanai/MAL/pull/8) | Hot-packet clocks / `Δ_exec`. **Unmerged draft.** No file in `DEC/`. Do not treat it as law. Recommendation: close #8. |
| [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md) | detect → decode → evaluate → runners |
| [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md) | Full detect book |
| [DEC-008](DEC/DEC-008-stack-phase-gates.md) | Stack phase gates |
| [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md) / [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md) | Oracle host and autonomy |
| [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) | Cloudflare Tunnel + Access (**LIVE**) |
| [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md) | Manager plans, workers open PRs, Helm merges |

## What is running

| System | Where | Status |
| --- | --- | --- |
| Observe, trade tape, attention, funding graph, forward paper | Oracle user units | Live collectors and the paper runner. See HOSTS. |
| LAYA timer | Oracle `mal-laya-v0.timer` 04:15 UTC | **Disabled until 2026-10-05.** The frozen job still spiked runner lag to about 12.5 s. [#106](https://github.com/vaanai/MAL/pull/106) (merged) is what it runs if re-enabled: `tools.laya_frozen_nightly` (cached pre-freeze fit, forward holdout append, backward holdout append, scoreboard). Exploratory retrain and `mig15 --deploy` stay skipped. CPUQuota 25%, MemoryMax 11G, MemorySwapMax 0, 256 MiB chunks. The scan pauses when `lag_ms` is over 3000 and resumes under 2000. |
| Attention daily | Oracle `mal-attention-daily.timer` 04:45 UTC | **Disabled until 2026-10-05.** `attention-daily.sh` exits before that date. The `mal-attention` poller keeps running. |
| Pump backfill | Oracle, CPU cap 50% ([#104](https://github.com/vaanai/MAL/pull/104)) | Covers **2026-09-22T00Z–2026-09-25T07Z**. Does not walk into the fast-box range. |
| Frozen migrate-direct score | Oracle `mal-migrate-direct-oos.timer` **01:20 UTC** | **Under test; may move to mal-fast-0.** Scores the frozen cell only. Does not change live size ceilings. |
| Healthcheck | Oracle `mal-healthcheck.timer` every 5 min | `/var/lib/mal/eng/healthcheck.sh` |
| Fast listeners | `mal-fast-0` | `mal-fast-create`, `mal-fast-public-logs`, `mal-fast-pre-create` left up. Early-trade and the full fast tape unit are installed and **not** left running. |
| Fast backfill + OOS score | `mal-fast-0` | Walks **back from 2026-09-21T23Z**. Hard cap **+2,000,000** credits ([#111](https://github.com/vaanai/MAL/pull/111), [#112](https://github.com/vaanai/MAL/pull/112)). |

**Helius.** Developer plan is in use. Backfill ledger when the fast OOS run opened: 957,741 used of 4,000,000. The fast run has its own +2M cap. Autoscaling headroom is an owner setting, recorded in [CLAUDE.md](CLAUDE.md), not a measured edge.

## Backfill split

No hour is on both lists ([ARTIFACTS/lab/migrate-direct-oos.md](ARTIFACTS/lab/migrate-direct-oos.md), snapshot 2026-09-27T13:48Z).

| Host | Covers | Stop |
| --- | --- | --- |
| Oracle | 2026-09-22T00:00Z through 2026-09-25T07:00Z | After hour 2026-09-22T00. At the snapshot, inside partial 2026-09-22T08. |
| `mal-fast-0` | 2026-09-21T23 backward | +2,000,000 credits, 240 hours, 40 GiB, or disk free under 30%. At the snapshot, 2026-09-21T23 was sealed and 2026-09-21T22 was in progress. |

After the worker restart the fast box sealed about **11.7 slots/s**, about **3.1 history hours per wall hour**.

## Forward-paper stale-fill void

Rows with a decision time in **2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z** do not count for promotion. The runner was behind the tape (recv→decision past the 5s cap). [#102](https://github.com/vaanai/MAL/pull/102) drops those stale fills and charges a flat 15% miss. [#103](https://github.com/vaanai/MAL/pull/103) fixed the exit-scan walk that held a full core and kept the lag from clearing.

Clean clock (first UTC midnight after that guard-live instant): **2026-09-28T00:00:00Z**. Kill review: **2026-10-05T05:00:00Z**.

With the [#104](https://github.com/vaanai/MAL/pull/104) backfill cap (50% of one core), forward-paper lag was **197 ms** about 2 minutes after the capped start and **39 ms** about 10 minutes after (07:38Z). Both under the 5s cap.

## Latency curve ([#107](https://github.com/vaanai/MAL/pull/107), merged)

The curve is **flat**. On buy-every-create, hold 30s, end-of-slot, direct, 0.05 SOL, gross moves from **−20.3% at slot+4 to −19.7% at slot+1** (+0.56 pp, about **+0.19 pp gross per slot**). That is under the fee floor. Speed alone does not clear fees.

Fee audit ([ARTIFACTS/lab/fee-audit-2026-09-27.md](ARTIFACTS/lab/fee-audit-2026-09-27.md)): the scorer fee math matches the chain. A graduated round trip at 410.88 SOL buy / 616.32 SOL sell is **0.00 points** off `paper_curve_math.py`. [#110](https://github.com/vaanai/MAL/pull/110) adds reporting-only `fee_sensitivity` columns (direct vs portal, priority 0.0001 / 0.0003 / 0.001). Promotion still reads `pnl_lamports`.

## First positive run

The first positive paper result is the in-sample cell `migrate` × `tp50_sl30` × slot+1 start (optimistic), route direct (`portal_fee_ppm = 0`), size 0.05 SOL, on sealed backfill hours 2026-09-22T10 through 2026-09-25T06 (`block_time_end` ≤ 2026-09-25T06:58:00Z). It was the best of 972 in-sample cells, so it carries winner's-curse risk. At the grid priority of 0.001 SOL/side it is not positive: n = 2,947 (of 2,950 migrations; 3 censored), 4 day buckets of which 0 are positive, gross mean +1.882231% (the curve note rounds the same figure to +1.88%), flat-15% net mean −1.947283% (curve note −1.95%), pressure-scale-1 net mean −2.16%, flat 90% CI lower bound −2.78% (−0.00139 SOL/trade). At priority 0.0001 SOL/side the same cell is flat net +0.470013% and pressure net −0.013%, still not both models. The in-sample holdout at the measured slot-+1 landed-buy priorities (same attempts file, n = 2,947, fill 40.35%) is positive at the median priority and not at the 75th percentile, and every 90% CI lower bound is below 0. At priority 0.000058 SOL (slot-+1 p50): flat net +0.583%, flat CI lower −0.245%, pressure net +0.087%, pressure CI lower −0.424%, ex-top-3 +0.387 SOL, days positive 3/4. At priority 0.0005 SOL (slot-+1 p75): flat net −0.604%, flat CI lower −1.431%, pressure net −0.969%, pressure CI lower −1.481%, ex-top-3 −1.360 SOL, days positive 1/4. The frozen out-of-sample test (locked 2026-09-27T13:06:36Z) uses that p75 priority, 0.0005 SOL/side, with 0.5 SOL primary. The 2026-09-27T13:48Z snapshot pools Oracle hour 2026-09-22T09 and fast hour 2026-09-21T23: 0.5 SOL n = 60, days 2, days positive 1, fill 16.7%, flat net +0.51%, flat CI lower −3.22%, flat ex-top-3 −0.606 SOL, pressure net +0.56%, pressure CI lower −1.62%, pressure ex-top-3 −0.333 SOL. The OOS book is small. It does not clear the promotion gate. Evidence: `ARTIFACTS/lab/latency-curve-2026-09-27.md`, `ARTIFACTS/lab/latency-curve-2026-09-27.json`, `ARTIFACTS/lab/fee-audit-2026-09-27.md`, `ARTIFACTS/lab/migrate-direct-prereg.md`, `ARTIFACTS/lab/migrate-direct-oos.md`.

### Promotion gate

Unchanged, from the frozen cell spec:

At least **100** out-of-sample trades, at least **5** distinct UTC days with a majority of those days positive, lower **90%** CI bound of mean SOL per trade **> 0**, and total SOL still positive after removing the top 3 trades. The book must clear that bar under **both** the flat 15% fail rate and the pressure-fail model at slope scale 1. Bootstrap: 1,000 draws, seed 1. The lower bound is the 5th percentile of those means.

Live bar, still required after the gate: about 7 days of forward paper, then tiny size, and an explicit owner yes. The owner's dollar target is not evidence. See [CLAUDE.md](CLAUDE.md).

## Open PRs: keep / close

Read 2026-09-27 with `gh`. **Recommendation only. Do not close them from a worker.**

| PR | Call | Reason |
| --- | --- | --- |
| [#4](https://github.com/vaanai/MAL/pull/4) | close | EXP-001 is already PASS-closed on main. This draft locks the same experiment. |
| [#5](https://github.com/vaanai/MAL/pull/5) | close | The certifi TLS context is already in `observe/client.py` on main. |
| [#8](https://github.com/vaanai/MAL/pull/8) | close | DEC-005 never landed. The hot-packet chain it served is frozen. Do not merge the 2026-09-20 draft. |
| [#10](https://github.com/vaanai/MAL/pull/10) | close | The sealed-row stamp CLI is not on main, and EXP-001 is already closed. |
| [#15](https://github.com/vaanai/MAL/pull/15) | close | `tools/exp003_rpc_backfill.py` is already on main. |
| [#22](https://github.com/vaanai/MAL/pull/22) | close | DEC-011 is already merged and the tunnel is live. This draft still says the path is pending. |
| [#90](https://github.com/vaanai/MAL/pull/90) | keep | `JobQueue` on main is still unbounded. The stale-drop and credit cap in this draft are not in the tree. |
| [#106](https://github.com/vaanai/MAL/pull/106) | merged | Merged 2026-09-27. Skips the exploratory 04:15 LAYA retrain until 2026-10-05. Both that timer and `mal-attention-daily.timer` are disabled until then because runner lag still spiked to about 12.5 s. |

These were the open PRs at the handoff. #106 has since merged. #73–#112 in the notes are merged.

## Frozen

**PRs #49–#72** (hot-packet fixture / receipt / schema-lock chain) are on `main` and **frozen** for return work. New measurements use the tape, the curve scorer, and forward paper.

## Next work

1. Let the fast box finish the backward OOS hours inside the +2M credit cap. Do not refit the frozen cell.
2. Score forward paper only from the **2026-09-28T00:00:00Z** clean clock. Kill review **2026-10-05T05:00:00Z**.
3. Keep [#90](https://github.com/vaanai/MAL/pull/90) until review. [#106](https://github.com/vaanai/MAL/pull/106) is merged; leave `mal-laya-v0.timer` and `mal-attention-daily.timer` disabled until 2026-10-05. Helm merges.
4. Move fast. Hold the promotion gate. Report Helius credits when autoscaling is used.

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- Checklist that used to be the store notes: [docs/ops/notes.md](docs/ops/notes.md), [docs/ops/archived.md](docs/ops/archived.md)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
