# MAL Lab State

Compact reload for managers. **As-of:** 2026-09-30 ~20:50Z. `main` through [#164](https://github.com/vaanai/MAL/pull/164) (`b9ddb3c`). **Paper only.** Two hosts: Oracle `mal-core-0` and OVH `mal-fast-0`. How to reach them: [docs/HOSTS.md](docs/HOSTS.md). History that used to live here is in [ARTIFACTS/daily/2026-09-28-manager-session.md](ARTIFACTS/daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-28.md](ARTIFACTS/daily/2026-09-28.md).

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
| [DEC-015](DEC/DEC-015-forward-paper-on-fast.md) | New forward-paper books (after the 10-05 kill review) run on `mal-fast-0`; preconditions: all four runner inputs, trade-tape coverage, equivalence replay, own memory slice, lag probation; runner-as-service exception owner-confirmed |

## Latest confirmation result

**EXP-012 one-shot read: gate PASS (2026-10-01), backward simulated holdout only.**
- n = 451 over 7 UTC days (two half-days), 6/7 positive under both fail models.
- Flat mean 0.0349 SOL/trade (CI90 lower bound 0.0187); pressure 0.0205 SOL/trade (CI90 lower bound 0.0110).
- Ex-top-3: +14.04 SOL flat, +8.22 SOL pressure.
- **Not money made, not live-eligible.**
- Caveats:
  - The block is favourable to the unfiltered base trade (flat +1.45%, CI lo +0.42%, against −0.09% in the migrate-direct OOS fail).
  - Most of the lift is predicted fills (98.9% vs 28.0%), and the lift among fills is not significant.
  - Returns fall toward the present day.
- Next: an EXP-012 forward-paper book on `mal-fast-0` after the 2026-10-05T05:00:00Z kill review ([DEC-015](DEC/DEC-015-forward-paper-on-fast.md)). It must clear the gate on its own forward data, and then get owner approval, before live. Details: [EXP-012](EXP/EXP-012-migrate-entry-model-refreeze-prereg.md) Result.

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
- **EXP-011: closed NOT_DECIDABLE (2026-09-30).**
  - What happened: the one-shot read started at 20:25:56Z and aborted before computing anything, on a sealed-but-empty hour (2026-09-11T03). No outcome was observed. The holdout `[2026-09-09T12, 2026-09-15T12)` is spent and not re-read (`quant-proof` review). The frozen model and threshold are retired, per the pre-registration's §8.
  - Root cause: data-integrity bugs in `tools/pump_history_backfill.py`. There is a backwards slot range that seals an empty hour; resumed hours get **exact duplicate rows** (one exploration hour had 2,239,050 rows, 1,265,054 unique); and resumes probably lose held rows.
  - These affect resumed hours in all three fast walkers, including some exploration-pool hours B3 and EXP-011 trained on.
  - The B3 entry-selection idea goes back to exploration on deduplicated data. Any new confirmation needs a fresh block, sealed by the fixed walker and verified by `tools/backfill_verify.py`. Detail: [EXP-011 Result](EXP/EXP-011-migrate-entry-model-prereg.md).
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

**Multiplicity ([DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md)):** when k ≥ 2 candidate books or cells are read together at one review (e.g. the 9 forward books on 2026-10-05), a book promotes only if it also passes a Holm–Bonferroni step-down at family α = 0.05 on the one-sided bootstrap p-value (share of bootstrap means ≤ 0), under both fail models: the smallest p is compared to α/k (0.05/9 ≈ 0.0056 for k = 9), the next to α/(k−1), and so on. Multiplicity-tested reads use **10,000** bootstrap draws, seed 1. Reference cells are not in k, and a single pre-registered primary cell is k = 1.

Live bar, still required after the gate: about 7 days of forward paper, then tiny size, and an explicit owner yes. The owner's dollar target is not evidence. See [CLAUDE.md](CLAUDE.md).

## Holdout ledger

[docs/HOLDOUT_LEDGER.md](docs/HOLDOUT_LEDGER.md), adopted by [DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md): one owner per historical block, non-owner reads disclosed in the owner's `EXP-###` file, rows written before hours are sealed or read.

## Forward-paper stale-fill void and clean clock

Rows with a decision time in **2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z** do not count for promotion (the runner was behind the tape; [#102](https://github.com/vaanai/MAL/pull/102) drops those fills and charges a flat 15% miss).

Clean clock: **2026-09-28T00:00:00Z**. Kill review: **2026-10-05T05:00:00Z**.

## Open PRs

[#90](https://github.com/vaanai/MAL/pull/90): keep. `JobQueue` on `main` is still unbounded; the stale-drop and credit cap in this draft are not in the tree.

## Next work

1. **Data integrity first.**
   - Fix the backfill walker (PR `claude/backfill-integrity`).
   - Deduplicate the exploration pool with `tools/backfill_verify.py`, and check the Oracle exports too.
   - Re-fetch the bad hours.
   - Re-run the B3 entry-model exploration on clean data (on mal-research-0). If it still clears its screen, pre-register a new experiment on a fresh ≥6-day block walked by the fixed walker (~2M credits).
1b. **MAL Console** (owner priority from 2026-09-30; plan [docs/console-plan.md](docs/console-plan.md)). The MAL-side contracts are merged (#166–#169, #171), and so are guidebook docs 1–3 (#170, #172). Console v1 is built in `vaanai/mal-console` (#1–#5). The template runners are not wired yet. The next steps are MiScusi setup (owner + Grokbot), then research-0 online, then deploy at `console.tradervaan.com`. See [docs/HANDOFF.md](docs/HANDOFF.md) §B–C.
2. *(Paused by the owner while the Console is built.)* The EXP-009 screen scorer (walker 1 floored at 2026-09-15T12; it needs a scorer built per its pre-reg).
3. The 2026-10-05T05:00Z kill review with `tools/kill_review.py`, on a snapshot, once.
4. *(Paused.)* Lane D (early bonding-curve entry filter, branch `claude/explore-early-entry-model`): needs bounded windows (`chunk_plan`) before it runs, on mal-research-0 once that's up.
5. After 2026-10-05: fold the 09-28 forward-paper data into the exploration pool; if EXP-012's one-shot read passes (EXP-011 closed NOT_DECIDABLE), add an EXP-012 forward book on `mal-fast-0` per [DEC-015](DEC/DEC-015-forward-paper-on-fast.md); start the warm-start / live-readiness track.
6. Buy further fresh ≥6-day holdout blocks (~2M credits each) as candidates need them.

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Manager handoff (current): [docs/HANDOFF.md](docs/HANDOFF.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
