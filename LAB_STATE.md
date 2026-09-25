# MAL Lab State

Compact reload for managers. **As-of:** 2026-09-25 UTC. `main` @ `dc1357f`. **Paper-only** — no live-trading claims. **Host:** Oracle Always Free `mal-core-0` (`mal-core-vnic`, 2 OCPU / 12 GB aarch64). Cursor↔Oracle access **LIVE** ([DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)).

**Open draft PRs (merge stack in order):** [#73](https://github.com/vaanai/MAL/pull/73) trade tape · [#74](https://github.com/vaanai/MAL/pull/74) honest rescore addendum · [#75](https://github.com/vaanai/MAL/pull/75) wallet leaderboard · [#76](https://github.com/vaanai/MAL/pull/76) paper tape scoreboard · [#77](https://github.com/vaanai/MAL/pull/77) signal scan · [#78](https://github.com/vaanai/MAL/pull/78) LAYA v0 (LightGBM).

## Objective

**Profit.** North star: durable **SOL after fees** on Pump.fun / Solana meme flow, not repo completeness.

- **Edge** = information + modest latency vs **humans and copy-traders** (FOMO/GMGN-class apps), **not** MEV / Jito / colocated snipers. Be smarter, not fastest.
- **LAYA** (in this repo) = our **LightGBM** entry + exit models behind a **rules risk gate** — not Convai’s `laya` package ([engine brief](ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md); Project store: `laya-engine-options.md`).
- **Path:** full trade tape → honest paper simulator → signal search → walk-forward LAYA → forward paper → gated tiny live (owner approval only).

## Hard fences ([DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md))

- Postgres **localhost-only**; never guess or commit the DB password.
- **Never** open SSH `:22` to the world; **never** weaken Cloudflare Tunnel / Access.
- **No trading keys** and **no X keys** on `mal-core-0`. Signing stays off-host when live is authorized.
- Fingerprint mismatch or missing Runtime Secret → **stop and escalate**.
- Sealed **JSONL** = provenance spine; Postgres = ops/state only ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md), [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md)).

**Host access:** [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md) · data root `/var/lib/mal` · remote hostname `mal-core-vnic`.

## Decisions index

| DEC | Topic |
| --- | --- |
| [DEC-001](DEC/DEC-001-lean-four-override.md) | Lean four seats |
| [DEC-002](DEC/DEC-002-memory-first-no-db-local.md) | Memory-first; JSONL spine |
| [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md) / [DEC-004](DEC/DEC-004-regime-id-encoding.md) | Regime-at-ingest v0 |
| [DEC-005](DEC/DEC-005-hot-packet-clocks-and-provenance.md) | Hot-packet clocks / `Δ_exec` (draft) |
| [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md) | detect → decode → evaluate → runners |
| [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md) | Full detect book |
| [DEC-008](DEC/DEC-008-stack-phase-gates.md) | Stack phase gates (draft) |
| [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md) / [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md) | Always Free host |
| [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) | CF Tunnel + Access (**LIVE**) |

Non-negotiables: [CONSTITUTION.md](CONSTITUTION.md).

## What is live on the host (2026-09-25)

| System | Status |
| --- | --- |
| PumpPortal observe (`subscribeNewToken` / migration) | Live since 2026-09-23 (~1k+ events/h) |
| **Trade tape** (`mal-trade-tape.service`) | **Live** — public RPC `logsSubscribe`, pump + PumpSwap; ~3.6 GB/day compressed; retention ~35d (#73) |
| Wallet leaderboard / signal scan / LAYA v0 | Batch jobs on tape; daily LAYA timer enabled (#75–#78) |
| Execution / live wallet | **Not** on host |

**Data spend:** **$0** for now. Public RPC tape is sufficient; Helius Developer **$49** or Anaxer Pro **$99** only if completeness fails (see Project `data-budget-options.md`).

## Architecture spine

| Layer | Role |
| --- | --- |
| L1 — new coin | Create-time spine + tape-priced outcomes |
| L2 — wallets | Own-tape leaderboard, follower-wave timing, vetoes (not blind copy of public boards) |
| L3 — graph | Creator/coordination signals — **hypothesis**; mainly rug/farm veto until PnL proof |
| Precompute | Knowable-at-**T** features from observe + tape only |
| **LAYA** | LightGBM entry (+ exit) on precomputed packet; rules stay **risk gate** |
| Exec | Deferred until promotion + owner live approval |

**X (Twitter):** layer 4 later; reassess-only; no X keys on host in phase 0.

**Pipeline law** ([DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md)): sealed creates → decode packet → evaluate (packet-only) → paper runners. Full detect book ([DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md)): score rejects and runners; no deleting history.

## Evidence (honest, after fees)

Planning round-trip cost ≈ **3.5%** (curve ~1.25%/side + PumpPortal Local 0.5%/side + priority/rent) — see Project `execution-stack-options.md`. A +2% mark is a loss.

| Lane | Verdict |
| --- | --- |
| Rules **v1 / v2** (EXP-002b/c) | **KILL** — no lift vs random; adverse selection (#74 rescore @ 350 bps) |
| EXP-005 **L3** wallet watch | **KILL** cross-day; n≈11–22 priced — do not promote |
| **Buy every create** (paper tape, T+1s, hold_30s) | **Loses** — median ≈ −0.002 SOL/trade on 0.05 SOL size (#76) |
| Signal scan rules (follow / crowd / clean) | **No promote** — OOS median ≤ baseline; migrate n=13 was a tiny lead only (#77) |
| **LAYA v0** walk-forward | **No edge** — OOS median stuck at fee-loss mode (−0.001829 SOL); top decile raises win rate, not median (#78) |

**No promoted signal yet.**

### Promotion bar (strategy / model)

Before any `authorize_run` or live wording:

1. **Robust expectancy:** bootstrap CI on mean/median SOL **> 0** after the honest fee stack.
2. **Not one trade:** survives removing the best trade.
3. **Day stability:** positive on **most** calendar days in the test window.
4. Beat **buy-all** and random subsamples on **median** SOL at the same entry clock — not mean-only.

### Live bar (owner approval required)

~**7 days** forward paper on the tape simulator, positive after fees → **tiny** live calibration (≤0.05 SOL/trade, ~1 SOL float cap, daily loss halt, kill switch, signer **off** host). Explicit owner OK only.

**Hold / exit:** no fixed hold; record full price path; score exit grid 30s–30m + TP/trailing rules for LAYA exit training (Project `project-context.md`).

## Frozen — do not extend

**PRs #49–#72** (hot-packet → paper batch → fill-sim → LAYA **fixture / receipt / schema-lock** chain) are **on `main` but abandoned** for return work: they cannot produce profit; **freeze** further fixture/receipt-lock PRs unless reopening is explicitly authorized.

Historical contracts remain in `ARTIFACTS/` for reference; new work uses **tape-backed** tools (#73–#78), not synthetic sealed-day scoreboards.

## Next work (direction)

1. **Merge #73–#78** (stacked) — tape, rescore docs, scoreboard, leaderboard, scan, LAYA v0.
2. **Accumulate tape** — multi-day book; fix/verify `quote_is_wsol` on hourly PumpSwap rows; watch public-RPC lag tail (p99 can exceed copy window).
3. **Daily scoreboard** — forward metrics vs buy-all; add bootstrap / leave-one-out / day-split gates to LAYA reporting.
4. **Signal search** — treat migrate + crowd features as **model inputs**, not hard rules; re-test when n ≫ 100 OOS.
5. **Optional spend** — Helius $49 only on measured gap (reconnects, slot holes, create coverage); not Portal trade firehose.
6. **Parked:** paid gRPC/Jito races, Birdeye spine, bonk/mayhem, X on host, Convai LAYA on hot path.

## Infra (phase 0)

- **VM:** `VM.Standard.A1.Flex` 2/12, Phoenix AD-1; `/var/lib/mal` on 150 GB data volume (~200 GiB Always Free block cap).
- **Postgres 16** `meme_core` / `mal_app` — localhost; stubs [sql/meme_core/](sql/meme_core/).
- **Laptop:** courier + owner console — not a permanent agent hop.
- **Autonomy:** routine observe/tape/paper OK; escalate before billing, leaving Always Free, weakening security, or capital access.

## Open hypotheses (unchanged kill signals)

| ID | Hypothesis | Kill |
| --- | --- | --- |
| H-edge | Edge after honest fees | Paper + forward paper show no median lift |
| H-graph | Graph beats spine-only | No stable lift at decision horizons |
| H-social | X worth cost | Lift < API + LLM cost |
| H-jev | JEV-like packet beats rules | Rules/trees match on same as-of-T constraints |
| H-rpc | Free RPC + Portal WS enough | Missed events / 429s block measurement |

## Pointers

- Plan context: Project store `docs/plan.md`, `docs/project-context.md`
- Internal results (2026-09-25): Project `internal/trade-tape.md`, `rescore.md`, `paper-sim.md`, `wallet-leaderboard.md`, `signal-scan.md`, `laya-v0.md`
- Experiments: `EXP/` · Decisions: `DEC/` · Briefs: `ARTIFACTS/`
- Observe: [observe/client.py](observe/client.py), [OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
- Phase-0 handoff: [ORACLE-PHASE0-HANDOFF.md](ARTIFACTS/ORACLE-PHASE0-HANDOFF.md)
- Key CLIs: `tools.exp002_paper_runner`, `tools.exp003_*`, `tools.exp004*`, `tools.exp005*`, `tools.exp006_paper_fill_sim`, `tools.exp007*`; tape stack in PRs #73–#78 (`wallet_leaderboard`, `paper_tape_scoreboard`, `paper_signal_scan`, `laya_v0`)
- Managers reload this file each session; detail lives in git + Project store, not chat.
