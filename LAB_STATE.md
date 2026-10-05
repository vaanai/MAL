# MAL Lab State

Compact reload for managers. **As-of:** 2026-10-05 ~09:45Z (after the kill review). `main` through [#301](https://github.com/vaanai/MAL/pull/301) (`8a6849b`). **Paper only.** Two hosts: Oracle `mal-core-0` and OVH `mal-fast-0`. How to reach them: [docs/HOSTS.md](docs/HOSTS.md). History that used to live here is in [ARTIFACTS/daily/2026-09-28-manager-session.md](ARTIFACTS/daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-28.md](ARTIFACTS/daily/2026-09-28.md).

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
- **V correction (2026-10-04, [#281](https://github.com/vaanai/MAL/pull/281), [#285](https://github.com/vaanai/MAL/pull/285); a correction analysis, not a new read).**
  - PumpSwap prices on quote vault + V, the virtual quote reserve (about 17.58 SOL on about 69% of pools). Our paper pricing ignored it.
  - Re-priced, the same 451 entries still meet every gate condition. Flat mean 0.02526; flat CI90 lower bound 0.00928; pressure CI90 lower bound 0.00604; 5/7 days positive.
  - Live support is judged on the V book (DEC-016 Amendment 4).
- **Entry latency under V (exploration, [#292](https://github.com/vaanai/MAL/pull/292)).** Pressure mean / CI90 lower bound by entry slot k:
  - k = 1: 0.01866 / 0.01114
  - k = 8: 0.00769 / 0.00117
  - k ≥ 12: every lower bound is below 0.
  - Entry speed is the largest measured sensitivity.

## What is running

As of 2026-10-05 ~09:45Z.

| System | Where | Status |
| --- | --- | --- |
| EXP-012 forward walk (MiScusi #71) | `mal-research-0` | DEC-016, through about 10-05T04, 837k credits so far. **Resubmit before about 10-09T15Z.** |
| getBlock tip follower `mal-fast-tip-follower` | `mal-fast-0`, system unit | The runner's feed (DEC-015 2.2, owner option A). Parallel fetch ([#297](https://github.com/vaanai/MAL/pull/297)), 8 workers, rps 15. Restarted 10-05 ~05:27Z on `d0109f7`. Since then: lag 2–3 slots, block-lag p50 about 1.7–1.9 s, 0 backlog jumps. Every PumpSwap row carries `virtual_quote_reserve` ([#288](https://github.com/vaanai/MAL/pull/288)). **About 560k Helius credits/day measured** (getSlot polling is about half). |
| Fast-0 paper runner `mal-fast-forward-paper` (EXP-012 book) | `mal-fast-0`, system unit | **Started 2026-10-05T05:31:40Z, on probation (rows do not count).** Reads the tip tape (`pumpswap_virtual: require`, [#296](https://github.com/vaanai/MAL/pull/296)). Heartbeat ok. Daily-restart and heartbeat timers enabled. Mid-week start, recorded here. |
| DEC-019 probe executor `mal-probe-executor` | `mal-fast-0`, user `mal-live` | **Dry run since 2026-10-05T05:35:47Z** (no key, no live drop-in). Executor stages ~0.12 s; on chain → ready-to-send about 2.2–2.3 s (n = 2 early read). Wallet created by Helm, pubkey `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`, **not funded**. |
| Migration stream probe (MiScusi #127) | `mal-fast-0`, transient unit | 6 h measurement of a processed `transactionSubscribe` on the pump migration authority against the tip follower. First 3 events: about 0.7–1.5 s earlier on the same slot. |
| Oracle forward-paper runner | Oracle `mal-forward-paper.service` | Code `d7485d2`, daily 00:00Z restart via the claude timer on `mal-fast-0`. Its 9 books were read at the kill review (below). |
| Fast listeners / two-socket public tape trial | `mal-fast-0` | Superseded as the runner's feed by the tip follower. The public tape failed coverage on 10-03 (93.869%). |
| Claude schedules | `mal-fast-0`, user `claude` | `mal-daily-review` 05:00 UTC, `mal-runner-daily-restart` 00:00 UTC. |
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

### Result (read 2026-10-05, single read)

**VERDICT: PROMOTE = none, KILL = none, NOT_DECIDABLE = all 8 Holm-family books.**
- `buy_all` is the reference book, not a candidate.
- Holm k = 8, 10,000 draws, seed 1.
- Every book is INCOMPLETE: open orphans with no close or settlement, and some `settle_failed`. Under DEC-014 Amendment 2 that is NOT_DECIDABLE, not a measured KILL.

**The point estimates are one-sided.** Every book has a negative mean with the CI90 entirely below 0 under both fail models, and 0 positive flat days out of 8. Values are floored, so a loss is never shown smaller.

| Book | n | flat mean | flat CI90 | press mean | press CI90 | days + (flat) | orphans | settle failed | status |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | --- |
| laya_0.6 | 135,542 | −0.00350 | [−0.00369, −0.00329] | −0.00311 | [−0.00329, −0.00293] | 0/8 | 478 | 0 | NOT_DECIDABLE |
| laya_0.7 | 54,395 | −0.00357 | [−0.00380, −0.00333] | −0.00319 | [−0.00337, −0.00299] | 0/8 | 465 | 2 | NOT_DECIDABLE |
| migrate_hold_30s | 5,747 | −0.00256 | [−0.00318, −0.00188] | −0.00227 | [−0.00268, −0.00184] | 0/8 | 502 | 0 | NOT_DECIDABLE |
| migrate_tp50_sl30 | 5,746 | −0.00331 | [−0.00384, −0.00277] | −0.00262 | [−0.00295, −0.00226] | 0/8 | 412 | 2 | NOT_DECIDABLE |
| attn_first_hold_60m | 3,123 | −0.02250 | [−0.02459, −0.02048] | −0.01904 | [−0.02054, −0.01739] | 0/8 | 112 | 20 | NOT_DECIDABLE |
| t30_top1_hold30 | 2,439 | −0.00320 | [−0.00377, −0.00263] | −0.00288 | [−0.00331, −0.00246] | 0/8 | 416 | 0 | NOT_DECIDABLE |
| buyers8_top5_ladder2x | 1,775 | −0.00719 | [−0.00857, −0.00578] | −0.00541 | [−0.00651, −0.00427] | 0/8 | 162 | 3 | NOT_DECIDABLE |
| mig15_top20_tp50_sl30 | 998 | −0.00563 | [−0.00699, −0.00426] | −0.00516 | [−0.00624, −0.00405] | 0/8 | 259 | 3 | NOT_DECIDABLE |

**Provenance:**
- Snapshot manifest sha256: `399bf566f15a228360f59129f2ddf38a99658151da5ed0ea5f5b1ea5ceaa4e1c` (209 files: 197 tape hours, 9 creates days; nothing missing).
- Jobs:
  - #108: snapshot.
  - #118: score, out of memory at 32 GB in the pressure stamp, before the read.
  - #120: blocked by a read-only leftover; its partial output was kept at `/data/mal/kill-review-1005/out.oom-118`.
  - **#121**: the read. Pressure stamp in 4 mint chunks, [#299](https://github.com/vaanai/MAL/pull/299); its output is identical for any chunk count by test.
- Settlements are byte-identical across #118 and #121.
- `kill_review.json` sha256 `c66d6a5a798d77fe6fe0563a9319efeb7aa22a8ef5f9d76615f6f9cd66fdfde3`. Totals: 329 settled offline, 30 settle failures, 2,806 open orphans.

**Settle failures.** 428 of 470 orphan failures came from before 09-27T06:58Z, outside the window, mostly `no_tape_for_mint`. The tape was copied from 09-27 by design. Inside the window:
- 21 `censored_tape_too_short` (exits past 05:00Z);
- 17 `tokens_mismatch`;
- 2 slippage;
- 2 venue.

**Caveat: all 9 books are priced without V.** The PumpSwap books (migrate_tp50_sl30, migrate_hold_30s, mig15_top20_tp50_sl30) cannot be promoted on these numbers in any case.

**Manager reading.** None of the 8 shows an edge. They get no more work; the formal status stays NOT_DECIDABLE. EXP-012 remains the only candidate.

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

1. **Probe:** let the 6 h keyless dry run finish (about 11:36Z). If it is clean, post the live-config and drop-in sha256 for Helm, then owner funding (0.5 SOL), then Helm enables the live drop-in.
2. **About 11Z:** tip-follower coverage against forward-1002 over `[10-05T06, 08)`. This must pass before any runner row counts. Then the 2-day lag probation.
3. **Latency:** read the migration-stream probe (#127). If the gain holds, design the processed fast path, probably near-graduation subscriptions so features stay complete. Do it without changing the runner inside the forward window unless it comes with an md5 proof and a recorded restart.
4. **Credits:** cut the tip follower's getSlot polling, about half of its 560k/day.
5. **About 10-09T15Z:** resubmit forward walk #71.
6. **About 10-16T02Z:** EXP-012 FINAL read → quant-proof → V book (Am.4) → Am.3 at measured k → owner.
7. **Low priority:** EXP-013/014 need V-pricing amendments before their single screens.

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Manager handoff (current): [docs/HANDOFF.md](docs/HANDOFF.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
