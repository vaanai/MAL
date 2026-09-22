# MAL Lab State

Compact reload for managers (Grok bots). **As-of:** 2026-09-22. No live-trading claims.

## Objective

Build a **knowable-at-T** observation and decision pipeline for **Pump.fun / Solana** meme alpha: **3 intelligence layers** (new-coin, entity/relationship/graph, filtered smart-wallet follow) → precompute → **LAYA** → **risk gate** → (later) execution. **X = layer 4 later** (cherry-on-top, not discovery). **Paper first.** Prove durable edge before scaling infra or spend.

## Architecture spine

| Stage | Role |
| --- | --- |
| Layer 1 — new-coin | Create-time market spine (token, creator, curve/holders/velocity, safety) |
| Layer 2 — entity / graph | Persistent profiles + relationship graph (hypothesis; not yet proven for PnL) |
| Layer 3 — smart-wallet follow | Filtered follow set; never blind copy — combine with L1/L2 before LAYA |
| Precompute | As-of-T features, regime tags, graph scores (ready **before** decision) |
| LAYA | Real-time decision on **precomputed** features (rules now; local model later) |
| Hot packet | Regime-tagged market spine + capped graph scores |
| Risk gate | Deterministic, immutable decision packets |
| Exec | Deferred until phase-0 observation + backtest gates pass |
| X (Twitter) | **Layer 4 later** — reassess-only cherry-on-top; **not** primary discovery; **no X keys on host in phase 0** |

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
- Memory-first; GitHub SoT; JSONL = EXP spine; on-box Postgres = Layer-2 cache (DEC-002 **amended**) → [DEC-002](DEC/DEC-002-memory-first-no-db-local.md), [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md)
- Regime-at-ingest v0 (working law) → see [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md), encoding [DEC-004](DEC/DEC-004-regime-id-encoding.md)
- Pipeline paper path detect→decode→evaluate→runners → [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full detect book / anti-selection-bias kill → [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Stack spend phase gates (cheap-first; local Laya open hypothesis) → [DEC-008](DEC/DEC-008-stack-phase-gates.md) (draft); brief [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md)
- Oracle Always Free phase-0 host (`mal-core-0`, **2 OCPU / 12 GB**, **pending provision**) → [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md), BOM [ORACLE-ALWAYS-FREE-BOM-v0.md](ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md)
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
- Laptop-only 24/7 observe host (superseded by DEC-009 Always Free `mal-core-0`, pending provision)
- No database forever in phase 0 (on-box Postgres OK as Layer-2 cache; JSONL remains EXP spine)

## Infra posture (phase 0)

- **Primary continuous host:** Oracle Always Free **`mal-core-0`** — **`VM.Standard.A1.Flex`, 2 OCPU / 12 GB, aarch64** (**pending Vaan provision; nothing created**). **Not** a paid VPS. **Not** 4 OCPU / 24 GB.
- **Laptop:** operator console + **data courier** until cutover (no longer the intended 24/7 observe host once `mal-core-0` is up).
- **Lab memory SoT:** GitHub (`DEC/`, `EXP/`, `LAB_STATE.md`, `ARTIFACTS/`).
- **EXP / knowable-at-T spine:** sealed **JSONL** (append-only) — **do not** force-migrate observe marks or EXP provenance into Postgres on day-1.
- **On-box Postgres 16:** allowed as **Layer-2 cache / continuous ops aid** (not Autonomous DB; not EXP SoT).
- **Paid cloud:** only if measured need (DEC-008 rungs). Always Free capacity miss → **STOP**, escalate Helm/Vaan — do not pick paid shapes.
- **Secrets:** no trading keys and **no X keys** on the host in phase 0. Bonk/mayhem **parked**.

## Next work

1. **EXP-002c:** **INCOMPLETE closed** — do **not** promote. Local `--rules v2` + existing marks, seed 1 (PR #18 @ `842cb21`; 26/26 tests OK). population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈**63.18%** → reject_rate_band **INCOMPLETE** (below 70% floor). Runner priced_n=96 mean_return_pct≈**0.385**; reject priced_n=888 mean≈**3202.76**; random priced_n=410 mean≈**2500.58**. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Proof stamp **INCOMPLETE** (primary: reject_rate_band). Soft/directional lift FAIL again. **Next:** **DISCOVERY** first on dual failure (v2 too permissive; survivors still adverse vs random) — then **EXP-002d** only with a **new kill-list hyp**, or **pause**. Do **not** tighten constants to hit 70–95% (OPTIMIZE-to-gate). Soft denser marks still not the fix. [EXP-002c](EXP/EXP-002c-rules-v2-adverse-selection.md). **EXP-002b** remains **FAIL closed** (**FAIL_NO_LIFT_VS_RANDOM**).
2. **Host provision (DEC-009):** Vaan provisions Oracle Always Free per [BOM](ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md) (**2 OCPU / 12 GB** A1 — **STOP** if capacity missing). Then cut over observe/paper to `mal-core-0`. Laptop stays operator + data courier until cutover. aarch64 binary gaps escalate to Helm **after** create (not a veto).
3. **EXP-003 marks (unchanged law):** Subsample marks exist; book-wide N/A expected. Reuse existing marks for paper EXPs. Densify only after a non-adverse directional hyp needs power — never as lift rescue. [EXP-003](EXP/EXP-003-post-create-marks.md).
4. **EXP-001:** **PASS closed** — mislabel CLI [`tools.exp001_mislabel`](tools/exp001_mislabel.py).
5. Observe-wiring **landed:** [observe/client.py](observe/client.py), [OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
6. Hot-packet JSON spec (capped graph + market spine) aligned to matrix backfill rules
7. **Hard defer:** Birdeye paid; **Dexscreener debug enrich only**; bonk/mayhem reclass **parked**
8. Defer X API until social hypothesis has a cheap proxy or manual sample set (**no X keys on `mal-core-0`**)
9. Managers reload this file + latest [ARTIFACTS/SUMMARY.md](ARTIFACTS/SUMMARY.md) each session

## Pointers

- Decisions: `DEC/`
- Experiments: `EXP/`
- Local EXP-001 CLI: `python -m tools.exp001_mislabel`
- Local EXP-002/002b/002c CLI: `python -m tools.exp002_paper_runner` (`--rules v0` | `v1` | `v2`, default **v2**; `--marks` for EXP-003 ticks)
- Local EXP-003 RPC backfill: `python -m tools.exp003_rpc_backfill`
- Local EXP-003 coverage: `python -m tools.exp003_marks`
- Research: `ARTIFACTS/` (marks: [POST-CREATE-MARKS-BRIEF.md](ARTIFACTS/POST-CREATE-MARKS-BRIEF.md))
- API/cost/latency: [ARTIFACTS/API-COST-LATENCY-BRIEF.md](ARTIFACTS/API-COST-LATENCY-BRIEF.md)
- Laya vs VPS stack compare: [ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md) (Always Free `mal-core-0` is the phase-0 host experiment, not a paid VPS)
- Always Free BOM: [ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md](ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md)
- Regime at ingest: [ARTIFACTS/REGIME-AT-INGEST-MATRIX.md](ARTIFACTS/REGIME-AT-INGEST-MATRIX.md), [ARTIFACTS/REGIME-ENUM-V0.md](ARTIFACTS/REGIME-ENUM-V0.md), [ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md](ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md)
