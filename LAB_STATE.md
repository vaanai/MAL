# MAL Lab State

Compact reload for managers. **As-of:** 2026-10-03 ~06:00Z. `main` through [#259](https://github.com/vaanai/MAL/pull/259) (`166ec84`). **Paper only.** Two hosts: Oracle `mal-core-0` and OVH `mal-fast-0`. How to reach them: [docs/HOSTS.md](docs/HOSTS.md). History that used to live here is in [ARTIFACTS/daily/2026-09-28-manager-session.md](ARTIFACTS/daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-28.md](ARTIFACTS/daily/2026-09-28.md).

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
| `mal-fast-0` | OVH Frankfurt, x86_64. Fast listeners + trade-tape trial. **Main focus.** |

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
| EXP-012 forward walk (MiScusi job #71) | `mal-research-0` | DEC-016, owner-approved, running from 2026-10-02T15. Output `/data/mal/blocks/forward-1002`. 13 hours sealed through 10-03T03 with 0 issues, about 13.5k credits/hour. **Resubmit before about 10-09T15Z.** |
| Expansion walkers `explore-0814` (MiScusi jobs) | `mal-research-0` | w1–w4 sealed and verified, 0 flagged, 0 duplicates; clean views in `/data/mal/clean-view/explore-0814/w1..w4`. Credits: w2 475,326, w3 474,978, w4 451,197. w5 (#72), w6 (#49) and w7 (#50) running, with verify jobs #85–#87 chained after them. |
| Fast trade tape **trial**, two sockets (DEC-015 §2.2) | `mal-fast-0`, user unit `mal-fast-trade-tape` | On `ca542c5` with `--sockets 2` since 2026-10-02T13:39:04Z. Coverage against chain: **99.282%** over `[10-02T16, 18)` (#249; Oracle 82.062%); **100.000%** over `[10-03T00, 02)` (job #89; Oracle 95.671%). Feed measurement, not a forward book. 2-day retention applies. |
| Oracle forward-paper runner | Oracle `mal-forward-paper.service` | Code `d7485d2`. Restarts **daily at 00:00:00Z** via the claude timer `mal-runner-daily-restart` on `mal-fast-0` ([#137](https://github.com/vaanai/MAL/pull/137), [#155](https://github.com/vaanai/MAL/pull/155)), logged to `/home/claude/reports/runner-restarts.jsonl`. The 10-03 restart was ok, `head_sha` d7485d2. Each restart resets in-memory state; open positions settle offline. |
| EXP-012 runner on `mal-fast-0` | `mal-fast-0` | **Nothing installed yet.** The deploy kit ([#231](https://github.com/vaanai/MAL/pull/231)) and the heartbeat sampler ([#259](https://github.com/vaanai/MAL/pull/259)) install after the 2026-10-05T05:00Z kill review. |
| Fast listeners | `mal-fast-0` | `mal-fast-create`, `mal-fast-public-logs`, `mal-fast-pre-create` up. Early-trade and full fast tape installed, not running. |
| Claude schedules | `mal-fast-0`, user `claude` | `mal-daily-review` 05:00 UTC, `mal-runner-daily-restart` 00:00 UTC. `mal-oos-check` was a one-shot for the 2026-09-28 21:00Z read; it already ran and is not recurring. |
| Frozen migrate-direct scorers | Oracle + `mal-fast-0` | **Stopped.** The cell is dead — no more credits go to it. |
| LAYA / attention-daily timers | Oracle | **Disabled until 2026-10-05.** |
| Healthcheck | Oracle `mal-healthcheck.timer` every 5 min | `/var/lib/mal/eng/healthcheck.sh` |

## Current research state

- **EXP-012:** one-shot PASS (replay), as above. Forward book per DEC-016: one FINAL read over `[2026-10-06T00, 2026-10-16T00)`, at about 10-16T02Z. No peeking (Amendment 2).
  - **Amendment 3 ([#256](https://github.com/vaanai/MAL/pull/256))** fixes the live-support rules before any runner row exists. Live is supported only if the re-score at the measured fast-0 latency k(p50), with the owner's trial terms, clears the full gate under both fail models; at k(p90), mean > 0 and ex-top-3 > 0 are required. Runner-vs-scorer rows 0–6 must also hold.
  - Tooling merged: [#257](https://github.com/vaanai/MAL/pull/257) (latency export, replay rows 0–2), [#258](https://github.com/vaanai/MAL/pull/258) (forward sensitivity re-score), [#259](https://github.com/vaanai/MAL/pull/259) (heartbeat sampler and downtime).
  - The owner was asked for the trial terms (due 10-15T12Z). Defaults: 0.5 SOL, 3 concurrent, 500k lamports, no tip.
- **DEC-017:** no secondaries. All three candidates failed ([#242](https://github.com/vaanai/MAL/pull/242)).
- **EXP-013 graduation classifier (exploration):** [plan](EXP/EXP-013-graduation-classifier-plan.md), Amendments 1–5. Amendment 5 ([#252](https://github.com/vaanai/MAL/pull/252), quant-proof reviewed) fixes how each screen item is computed.
  - Tooling merged: [#244](https://github.com/vaanai/MAL/pull/244), [#251](https://github.com/vaanai/MAL/pull/251) (model plus nested LODO), [#253](https://github.com/vaanai/MAL/pull/253) (screen), [#254](https://github.com/vaanai/MAL/pull/254) (run script).
  - The screen runs **exactly once**, after 2026-10-04T12:00Z, on the 9-day pool plus every explore-0814 view verified by then (`scripts/research/exp013-screen-run.sh`).
  - The debug table build (#88, table only, no model) took 51 min for 9 days plus w1: 59,821 rows.
  - A PASS leads to a pre-registration on the backup block `[2026-08-28T12, 2026-09-03T12)` with k = 1. A FAIL closes the family. The honest prior is about 20% or less.
- **Frozen migrate-direct cell is dead** (formal FAIL, 2026-09-28T21:00Z one-shot, both fail models, every CI lower bound < 0). Not refit. [Detail](ARTIFACTS/lab/migrate-direct-oos.md).
- **EXP-011: closed NOT_DECIDABLE (2026-09-30).** The read aborted on a sealed-but-empty hour; holdout spent. Walker integrity bugs were the cause. [Result](EXP/EXP-011-migrate-entry-model-prereg.md).
- **EXP-009 (creator gate)** is a screen, k = 1 ([#154](https://github.com/vaanai/MAL/pull/154)); paused by the owner while the Console is built.
- **Exploration entry-model B3** ([#156](https://github.com/vaanai/MAL/pull/156)): exploration only, not a promote. Clean-data re-check: [note](ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md).
- **Exits are dead** ([#151](https://github.com/vaanai/MAL/pull/151)); **fee tiers are a dead end** ([#142](https://github.com/vaanai/MAL/pull/142)).
- **Lesson:** the typical migrate entry loses; entry selection is the lever. Always check ex-top-3.

## Kill review — 2026-10-05T05:00:00Z

Runbook: [docs/runbooks/kill-review-2026-10-05.md](docs/runbooks/kill-review-2026-10-05.md) ([#255](https://github.com/vaanai/MAL/pull/255)): snapshot job on mal-fast-0 → manifest sha → score job on research-0.

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

1. 2026-10-04T12Z: the EXP-013 single screen run.
2. 2026-10-05T05Z: the kill review, following the runbook.
3. After the kill review: install the fast-0 runner, observe and heartbeat (start the heartbeat by hand and check its pid before enabling), then the 2-day lag probation.
4. Daily: forward-walk verify and the two-socket coverage check.
5. About 10-09T15Z: resubmit forward walk #71.
6. About 10-16T02Z: the EXP-012 FINAL read → quant-proof → sensitivity re-score → runner comparison → owner.
7. *(Paused.)* EXP-009 screen scorer; Lane D (`claude/explore-early-entry-model`, needs bounded windows). Console: [docs/HANDOFF.md](docs/HANDOFF.md) §B–C.

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Manager handoff (current): [docs/HANDOFF.md](docs/HANDOFF.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
