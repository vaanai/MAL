# MAL Lab State

Compact reload for managers. **As-of:** 2026-09-29 ~05:00Z. `main` through [#157](https://github.com/vaanai/MAL/pull/157) (`22e7a7b`). **Paper only.** Two hosts: Oracle `mal-core-0` and OVH `mal-fast-0`. How to reach them: [docs/HOSTS.md](docs/HOSTS.md). History that used to live here is in [ARTIFACTS/daily/2026-09-28-manager-session.md](ARTIFACTS/daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-28.md](ARTIFACTS/daily/2026-09-28.md).

## Objective

**Profit.** North star: durable **SOL after fees** on Pump.fun / Solana meme flow.

Path: full trade tape → honest simulator → signals → forward paper → gated live. Live starts only after a book clears the promotion gate **and** the owner approves.

Edge is information plus modest latency versus humans and copy-traders, not MEV / Jito / colocated snipers.

## Hard fences

- Paper only. **No trading keys, wallet keys, or X keys** on either host. Signing stays off-host until live is authorized.
- Postgres **localhost-only**. Never guess or commit the DB password.
- **Port 22 is never public.** SSH for agents is Cloudflare Access. **Stop on a host-key mismatch.**
- `/opt/miscusi` on `mal-fast-0` is a separate project, `vaanai/MiScusi`, led by the same Claude manager. Do not modify it from MAL work.
- Sealed **JSONL** is the provenance spine. Postgres is ops/state only ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md)).
- GitHub is the source of truth for lab notes (`docs/`, `ARTIFACTS/lab/`, `ARTIFACTS/daily/`).

Non-negotiables: [CONSTITUTION.md](CONSTITUTION.md). Workflow: [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md), [DEC-013](DEC/DEC-013-claude-manager-merges.md).

## Hosts

| Host | Role |
| --- | --- |
| `mal-core-0` | Oracle, aarch64, archive + training + forward paper. |
| `mal-fast-0` | OVH Frankfurt, x86_64. Fast listeners + the backward backfill. **Main focus.** |

Detail, units, logs, memory limits, and what is safe to restart: [docs/HOSTS.md](docs/HOSTS.md).

## Decisions index

| DEC | Topic |
| --- | --- |
| [DEC-001](DEC/DEC-001-lean-four-override.md) | Lean four seats |
| [DEC-002](DEC/DEC-002-memory-first-no-db-local.md) | Memory-first; JSONL spine |
| [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md) / [DEC-004](DEC/DEC-004-regime-id-encoding.md) | Regime-at-ingest v0 |
| [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md) | detect → decode → evaluate → runners |
| [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md) | Full detect book |
| [DEC-008](DEC/DEC-008-stack-phase-gates.md) | Stack phase gates |
| [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md) / [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md) | Oracle host and autonomy |
| [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) | Cloudflare Tunnel + Access (**LIVE**) |
| [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md) | Manager plans, workers open PRs, tool-neutral workflow |
| [DEC-013](DEC/DEC-013-claude-manager-merges.md) | Claude manager merges (not Helm); Helm keeps ufw/sshd/tunnel/Access/Oracle admin |
| [DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md) | Holdout ledger (one owner per historical block); Holm–Bonferroni multiplicity correction; single read at kill review; pressure-leg start instant (Amendment 3) |

## What is running

| System | Where | Status |
| --- | --- | --- |
| Oracle forward-paper runner | Oracle `mal-forward-paper.service` | Code `d7485d2` since restart #3, **2026-09-29T00:00:20Z**. Restarts **daily at 00:00:00Z** via the claude timer `mal-runner-daily-restart` on `mal-fast-0` ([#137](https://github.com/vaanai/MAL/pull/137), [#155](https://github.com/vaanai/MAL/pull/155)), logged to `/home/claude/reports/runner-restarts.jsonl`. Every restart resets in-memory `WalletState`/`by_creator`; open positions settle offline. Memory growth ~300 MB/h post-#134, `tx_order_entries` plateaus ~800k. Oracle cgroup `MemoryHigh` 10G / `MemoryMax` 12G. |
| Backward backfill, 3 walkers | `mal-fast-0` | `mal-fast-backfill` (walker 1): floored at 2026-09-15T12, cap 2.3M, 1,307,207 credits used, 93 hours sealed. `mal-fast-backfill-b`: `[2026-09-12T12, 2026-09-15T12)`, cap 1.1M. `mal-fast-backfill-c`: `[2026-09-09T12, 2026-09-12T12)`, cap 1.1M. Combined 1,505,168 of a planned ~4.5M credits at 05:03Z. |
| Fast listeners | `mal-fast-0` | `mal-fast-create`, `mal-fast-public-logs`, `mal-fast-pre-create` up. Early-trade and full fast tape installed, not running. |
| Claude schedules | `mal-fast-0`, user `claude` | `mal-daily-review` 05:00 UTC, `mal-runner-daily-restart` 00:00 UTC. `mal-oos-check` was a one-shot for the 2026-09-28 21:00Z read; it already ran and is not recurring. |
| Frozen migrate-direct scorers | Oracle + `mal-fast-0` | **Stopped.** `mal-migrate-direct-oos.timer` disabled by Helm on Oracle; `mal-fast-oos-score` stopped on `mal-fast-0`. The cell is dead — no more credits go to it. |
| LAYA / attention-daily timers | Oracle | **Disabled until 2026-10-05.** |
| Healthcheck | Oracle `mal-healthcheck.timer` every 5 min | `/var/lib/mal/eng/healthcheck.sh` |

## Current research state

- **Frozen migrate-direct cell is dead.** Formal FAIL from the 2026-09-28T21:00Z one-shot: pooled 0.5 SOL n=3,622 over 5 days, flat net −0.0906% (3/5 days positive), pressure net −0.1682% (2/5), every CI lower bound < 0. It is not refit. Detail: [ARTIFACTS/daily/2026-09-28-manager-session.md](ARTIFACTS/daily/2026-09-28-manager-session.md), [ARTIFACTS/lab/migrate-direct-oos.md](ARTIFACTS/lab/migrate-direct-oos.md).
- **EXP-011 (candidate confirmation, pre-registration in progress):** the frozen, leakage-ablated S2 entry model at a fixed threshold, read once on the holdout block `[2026-09-09T12, 2026-09-15T12)` walked by walkers B + C, due about **2026-09-30T03Z**. A pass earns a forward book after 2026-10-05, not live.
- **EXP-009 (creator gate) is a SCREEN**, not a confirmation test: k = 1 ([#154](https://github.com/vaanai/MAL/pull/154)), block `[2026-09-15T12, 2026-09-19T01)` via walker 1, about 2.5 eligible days. A pass earns a forward trial, never a promote.
- **Exploration entry-model B3** (9 held-out days, [#156](https://github.com/vaanai/MAL/pull/156)): the S2 classifier, tp50_sl30, top 10%, passed the pre-stated screen; after the leakage ablation (dropping `same_slot_buys`/`nearby_buy_sol`) it gives flat +6.22% (CI lo +3.85%), pressure +3.59% (CI lo +2.06%), 9/9 days positive, ex-top-3 +24.97/+14.17 SOL. Fast-box slice alone is weakest, +1.24% pressure after ablation. **This is exploration, not a promote** — EXP-011 is the confirmation test.
- **Exits are dead:** the trailing stop was killed by concentration ([#151](https://github.com/vaanai/MAL/pull/151)) — ex-top-3 −8.0 SOL despite a positive pooled total.
- **Fee tiers are a dead end** ([#142](https://github.com/vaanai/MAL/pull/142)) at the migrate trigger: 98.6% of fills already pay the top tier.
- **Lesson:** the typical migrate entry loses; exits only harvest rare tails. Entry selection is the lever. Always check ex-top-3 before calling a cell a candidate.

## Kill review — 2026-10-05T05:00:00Z

Single read, once, at or after that instant, on a snapshot — never on a live, growing `positions.jsonl`. Scorer: `tools/kill_review.py`, with `tools/forward_paper_settle_orphans.py` for restart-dropped opens and `tools/forward_paper_pressure_stamp.py` for the pressure leg.

- Flat leg counts full UTC days from the 2026-09-28T00:00:00Z clean clock.
- Pressure leg counts full UTC days from **2026-09-29T00:00:00Z** (first daily restart on code ≥ `d7485d2`, DEC-014 Amendment 3).
- Each leg must clear the gate on its own; fewer than 5 eligible days on a leg is NOT_DECIDABLE, not a pass.
- If the pressure stamp can't cover a book (missing pnl field, a `pressure_error`, an unresolved restart-orphan, or `settle_failed`), that book is NOT_DECIDABLE (DEC-014 Amendment 2).
- Holm–Bonferroni across the 9 books (DEC-014). No new forward books during the kill-review week.

## Promotion gate

At least **100** out-of-sample trades, at least **5** distinct UTC days with a majority of those days positive, lower **90%** CI bound of mean SOL per trade **> 0**, and total SOL still positive after removing the top 3 trades. The book must clear that bar under **both** the flat 15% fail rate and the pressure-fail model at slope scale 1. Bootstrap: 1,000 draws, seed 1. The lower bound is the 5th percentile of those means.

**Multiplicity ([DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md)):** when k ≥ 2 books or cells are read together at one review, a book promotes only if it also passes a Holm–Bonferroni step-down at family α = 0.05 on the one-sided bootstrap test, under both fail models.

Live bar, still required after the gate: about 7 days of forward paper, then tiny size, and an explicit owner yes. The owner's dollar target is not evidence. See [CLAUDE.md](CLAUDE.md).

## Holdout ledger

[docs/HOLDOUT_LEDGER.md](docs/HOLDOUT_LEDGER.md), adopted by [DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md): one owner per historical block, non-owner reads disclosed in the owner's `EXP-###` file, rows written before hours are sealed or read.

## Forward-paper stale-fill void and clean clock

Rows with a decision time in **2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z** do not count for promotion (the runner was behind the tape; [#102](https://github.com/vaanai/MAL/pull/102) drops those fills and charges a flat 15% miss).

Clean clock: **2026-09-28T00:00:00Z**. Kill review: **2026-10-05T05:00:00Z**.

## Open PRs

[#90](https://github.com/vaanai/MAL/pull/90): keep. `JobQueue` on `main` is still unbounded; the stale-drop and credit cap in this draft are not in the tree.

## Next work

1. When walkers B + C finish (~2026-09-30T03Z), run the EXP-011 one-shot scorer (`tools/exp011_score.py`, still to be built if not already merged) on the reserved block, once.
2. Score the EXP-009 screen once walker 1 finishes (~2026-09-30T01Z).
3. The 2026-10-05T05:00Z kill review with `tools/kill_review.py`, on a snapshot, once.
4. Lane D (a learned filter on early bonding-curve entries, branch `claude/explore-early-entry-model`, streaming fix in place, not yet run to completion) — run alone, ≤2 workers.
5. After 2026-10-05: fold the 09-28 forward-paper data into the exploration pool; if EXP-011 passes, add an EXP-011 forward book; start the warm-start / live-readiness track.
6. Buy further fresh ≥6-day holdout blocks (~2M credits each) as candidates need them.

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Manager handoff (current): [docs/HANDOFF.md](docs/HANDOFF.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
