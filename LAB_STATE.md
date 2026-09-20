# MAL Lab State

Compact reload for managers (Grok bots). **As-of:** 2026-09-20. No live-trading claims.

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

## Universe

- **Primary:** Pump.fun bonding curve / PumpSwap on Solana
- **Deferred:** Other chains, non-meme strategies

## Active decisions

- Lean four seats override org chart → see [DEC-001](DEC/DEC-001-lean-four-override.md)
- Memory-first, no DB phase 0, local-first → see [DEC-002](DEC/DEC-002-memory-first-no-db-local.md)
- Regime-at-ingest v0 (working law) → see [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md), encoding [DEC-004](DEC/DEC-004-regime-id-encoding.md)
- Hot-packet clocks, `Δ_exec`, precompute as-of, create-spine provenance → see [DEC-005](DEC/DEC-005-hot-packet-clocks-and-provenance.md) (council 2026-09-20)
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

1. **EXP-001:** 24h WS capture with `python -m observe`; inventory key diff vs [PUMPPORTAL-PAYLOAD-INVENTORY.md](ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md)
2. **Proof:** mis-label audit on 100 random creates per [EXP-001](EXP/EXP-001-24h-ws-capture.md)
3. Observe-wiring **landed:** [observe/client.py](observe/client.py), [OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
4. Hot-packet JSON spec (capped graph + market spine) aligned to matrix backfill rules
5. Log experiments per [EXP/README.md](EXP/README.md) with regime labels and 1s/5s/15s/30s/60s windows
6. **Hard defer:** Birdeye paid; **Dexscreener debug enrich only** (not spine) — [API brief](ARTIFACTS/API-COST-LATENCY-BRIEF.md)
7. Defer X API until social hypothesis has a cheap proxy or manual sample set
8. Managers reload this file + latest [ARTIFACTS/SUMMARY.md](ARTIFACTS/SUMMARY.md) each session

## Pointers

- Decisions: `DEC/`
- Experiments: `EXP/`
- Research: `ARTIFACTS/`
- API/cost/latency: [ARTIFACTS/API-COST-LATENCY-BRIEF.md](ARTIFACTS/API-COST-LATENCY-BRIEF.md)
- Regime at ingest: [ARTIFACTS/REGIME-AT-INGEST-MATRIX.md](ARTIFACTS/REGIME-AT-INGEST-MATRIX.md), [ARTIFACTS/REGIME-ENUM-V0.md](ARTIFACTS/REGIME-ENUM-V0.md), [ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md](ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md)
