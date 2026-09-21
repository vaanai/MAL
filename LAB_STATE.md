# MAL Lab State

Compact reload for managers (Grok bots). **As-of:** 2026-09-21. No live-trading claims.

## Objective

Build a **knowable-at-T** observation and decision pipeline for **Pump.fun / Solana** meme alpha: continuous market observation → capped hot packet → deterministic risk gate → (later) execution. Prove durable edge before scaling infra or spend.

## Architecture spine

| Stage | Role |
| --- | --- |
| Continuous observation | Always-on ingest of market + (optional) social signals |
| Relationship graph | First-class hypothesis; not yet proven for PnL |
| Precompute | As-of-T features, regime tags, graph scores |
| Hot packet | Regime-tagged market spine + capped graph scores |
| X (Twitter) | **Reassess-only** timing accelerator — not primary discovery |
| Risk gate | Deterministic, immutable decision packets |
| Exec | Deferred until phase-0 observation + backtest gates pass |

## Pipeline (working law)

**detect** (sealed creates) → **decode** (knowable-at-T packet) → **evaluate** (filter on packet only) → **runners** (paper promotion stamp only). Locked in [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md).

- **Decode** — regime + capped as-of-T graph when evidence exists; X attach when present (reassess-only).
- **Evaluate** — packet-only; strictness / JEV-vs-rules are EXP knobs on the paper book, not early locks.
- **Runners** — pretend-buy at runner time; horizons 1s/5s/15s/30s/60s and +2s/+10s/+5m/peak/drawdown; costs in `Δ_exec` (see [DEC-005](DEC/DEC-005-hot-packet-clocks-and-provenance.md) when merged).
- **Full detect book** — outcomes for rejects and runners; evaluate labels never delete history; filter kill on reject/runner parity after costs → [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **No live capital** until Proof kill-attempt under costs; **cheap-first** unchanged.

## Universe

- **Primary:** Pump.fun bonding curve / PumpSwap on Solana
- **Deferred:** Other chains, non-meme strategies

## Active decisions

- Lean four seats override org chart → see [DEC-001](DEC/DEC-001-lean-four-override.md)
- Memory-first, no DB phase 0, local-first → see [DEC-002](DEC/DEC-002-memory-first-no-db-local.md)
- Regime-at-ingest v0 (working law) → see [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md), encoding [DEC-004](DEC/DEC-004-regime-id-encoding.md)
- Pipeline paper path detect→decode→evaluate→runners → [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full detect book / anti-selection-bias kill → [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Hot-packet clocks / `Δ_exec` → [DEC-005](DEC/DEC-005-hot-packet-clocks-and-provenance.md) (draft PR #8; cross-link only until merge)
- Non-negotiables → [CONSTITUTION.md](CONSTITUTION.md)

## Open hypotheses

| ID | Hypothesis | Kill signal |
| --- | --- | --- |
| H-edge | Durable edge exists in Pump.fun microstructure after fees/slippage | Backtest + paper live show no lift vs baseline |
| H-social | X adds value vs cost for **reassess** (not discovery) | Measured lift < API + LLM cost at target windows |
| H-graph | Relationship graph improves PnL vs spine-only | A/B at 1s/5s/15s/30s/60s shows no stable lift |
| H-jev | JEV-like packet beats simple rule baseline | Simple rules match or beat on same as-of-T constraints |
| H-survival | Backtest signals survive live observation regime | Live metrics diverge beyond tolerance |
| H-rpc | Free / light RPC + PumpPortal WS sufficient for phase-0 observe-only | Missed events, 429s, or stale commitment block hypotheses |

## Superseded ideas

- Twitter-primary discovery
- Buy-on-Twitter-alone
- Giant research brief per event (use registry + capped artifacts)
- Buy infra first (measure first; see API brief)
- Hermes as **live trading** manager (managers remain Grok; workers produce artifacts)

## Infra posture (phase 0)

- **Local-first:** home PC / WSL2 + Docker when ready
- **Source of truth:** GitHub (this repo)
- **No database** in phase 0 — markdown + JSON artifacts
- **Cloud:** only if measured need (latency, uptime, volume)

## Next work

1. **EXP-003 marks (Scout / Vaan local):** Attach post-create ticks without rewriting sealed rows. Brief: [ARTIFACTS/POST-CREATE-MARKS-BRIEF.md](ARTIFACTS/POST-CREATE-MARKS-BRIEF.md). EXP: [EXP/EXP-003-post-create-marks.md](EXP/EXP-003-post-create-marks.md). **Producer:** `python -m tools.exp003_rpc_backfill` (public `SOLANA_RPC_URL`, subsample 300). **Coverage:** `python -m tools.exp003_marks` with `marks-*.jsonl`.
2. **EXP-002b re-run (Proof):** After coverage `READY`, `python -m tools.exp002_paper_runner … --rules v1 --marks data/observe/marks-….jsonl`. Runbook: [EXP/EXP-002b-evaluate-rules-v1.md](EXP/EXP-002b-evaluate-rules-v1.md); [EXP-002](EXP/EXP-002-evaluate-runner-v0.md) §11.3. **EXP-001:** **PASS closed** — mislabel CLI [`tools.exp001_mislabel`](tools/exp001_mislabel.py); optional archive run on same JSONL.
4. Observe-wiring **landed:** [observe/client.py](observe/client.py), [OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md) (`ingest_hot` + `outcome_mark`)
5. Hot-packet JSON spec (capped graph + market spine) aligned to matrix backfill rules
6. **Hard defer:** Birdeye paid; **Dexscreener debug enrich only** (not spine; not EXP-003 `source`) — [API brief](ARTIFACTS/API-COST-LATENCY-BRIEF.md)
7. Defer X API until social hypothesis has a cheap proxy or manual sample set
8. Managers reload this file + latest [ARTIFACTS/SUMMARY.md](ARTIFACTS/SUMMARY.md) each session

## Pointers

- Decisions: `DEC/`
- Experiments: `EXP/`
- Local EXP-001 CLI: `python -m tools.exp001_mislabel`
- Local EXP-002/002b CLI: `python -m tools.exp002_paper_runner` (`--rules v0` | `v1`, default **v1**; `--marks` for EXP-003 ticks)
- Local EXP-003 RPC backfill: `python -m tools.exp003_rpc_backfill`- Local EXP-003 coverage: `python -m tools.exp003_marks`
- Research: `ARTIFACTS/` (marks: [POST-CREATE-MARKS-BRIEF.md](ARTIFACTS/POST-CREATE-MARKS-BRIEF.md))
- API/cost/latency: [ARTIFACTS/API-COST-LATENCY-BRIEF.md](ARTIFACTS/API-COST-LATENCY-BRIEF.md)
- Regime at ingest: [ARTIFACTS/REGIME-AT-INGEST-MATRIX.md](ARTIFACTS/REGIME-AT-INGEST-MATRIX.md), [ARTIFACTS/REGIME-ENUM-V0.md](ARTIFACTS/REGIME-ENUM-V0.md), [ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md](ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md)
