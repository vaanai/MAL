# MAL Lab State

Compact reload for managers (Grok bots). **As-of:** 2026-09-23. No live-trading claims. **`mal-core-0` is provisioned and verified.** Cursor↔Oracle access **LIVE** ([DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)): CF Access Service Auth + Runtime Secrets; smoke passed 2026-09-23; remote hostname **`mal-core-vnic`**; paper-only.

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
- Memory-first; GitHub SoT; sealed **JSONL** = provenance/event spine; on-box Postgres = **ops/state** (DEC-002 **amended**) → [DEC-002](DEC/DEC-002-memory-first-no-db-local.md), [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md), [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md)
- Regime-at-ingest v0 (working law) → see [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md), encoding [DEC-004](DEC/DEC-004-regime-id-encoding.md)
- Pipeline paper path detect→decode→evaluate→runners → [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full detect book / anti-selection-bias kill → [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Stack spend phase gates (cheap-first; local Laya open hypothesis) → [DEC-008](DEC/DEC-008-stack-phase-gates.md) (draft); brief [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md)
- Oracle Always Free host **`mal-core-0` live** (**2 OCPU / 12 GB**, Phoenix AD-1, **provisioned + verified**) → [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md), handoff [ORACLE-PHASE0-HANDOFF.md](ARTIFACTS/ORACLE-PHASE0-HANDOFF.md), BOM [ORACLE-ALWAYS-FREE-BOM-v0.md](ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md)
- Cursor↔Oracle access **LIVE** (CF Tunnel + Access Service Auth + dedicated agent key via Runtime Secrets; smoke 2026-09-23; hostname `mal-core-vnic`; paper-only) → [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)
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

## Open research (no pick)

- **Trading / wallet execution surface** (Axiom, Phantom, other) — may affect design. Research before live or a real paper-trading venue. **Do not invent a pick** in Lab memory; if a Cursor research run is launched, **DM Vaan the status card**.
- **Real paper-trading utility** for meme coins (beyond “would have” marks) — owner-offered **option**. **Ask before adopting.**

## Superseded ideas

- Twitter-primary discovery
- Buy-on-Twitter-alone
- Giant research brief per event (use registry + capped artifacts)
- Buy infra first (measure first; see API brief)
- Hermes as **live trading** manager (managers remain Grok; workers produce artifacts)
- Laptop-only 24/7 observe host (superseded by DEC-009/010 Always Free `mal-core-0`, **now provisioned**)
- No database forever in phase 0 (on-box Postgres OK as **ops/state**; JSONL remains provenance/EXP spine)
- Human PC as a **permanent** Cursor→Oracle networking hop (DEC-011: CF Tunnel + Access **LIVE** 2026-09-23)
- Sharing the owner personal SSH key with agents; public Postgres; SSH `:22` to the world (DEC-011 rejected)
- Cursor My Machines **on-box** as phase-0 **default** (DEC-011 **park** — aarch64/resource risk)

## Infra posture (phase 0)

- **Primary continuous host (LIVE):** Oracle Always Free **`mal-core-0`** — Phoenix **AD-1**, **`VM.Standard.A1.Flex`, 2 OCPU / 12 GB, aarch64**, Ubuntu 24.04 Minimal, 2 GB swap. Boot 50 GB; **`mal-core-data` 150 GB @ `/var/lib/mal`**. VCN `mal-vcn` / subnet `mal-public` / IGW `mal-igw` / NSG `mal-core-nsg`. SSH break-glass = owner home IP only; agents use **Access TCP** (no public app ports). **Not** a paid VPS. **Not** 4 OCPU / 24 GB. Remote hostname **`mal-core-vnic`**.
- **Postgres:** **16.15**, DB **`meme_core`**, role **`mal_app`** (non-superuser), **localhost only**, data dir `/var/lib/mal/postgresql/16/main`. Password = owner only (never in repo). Ops/state stub schema: [sql/meme_core/](sql/meme_core/).
- **Laptop:** operator console + **data courier**. Must **not** be a permanent Cursor→Oracle networking dependency. Access path: [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (**LIVE** 2026-09-23 — CF Access Service Auth + Runtime Secrets; smoke passed; hostname `mal-core-vnic`). Runbook: [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md).
- **Lab memory SoT:** GitHub (`DEC/`, `EXP/`, `LAB_STATE.md`, `ARTIFACTS/`). Seat agents keep **direction**; git holds **ops detail**.
- **Provenance spine:** sealed **JSONL** (append-only). Postgres = **ops/state** (token/wallet/relationship/derived/paper/ops) — **not** a mandatory provenance replacement.
- **Workflow:** Grok plans/reviews/integrates → Cursor implements/tests (aggressively). **DM Vaan a Cursor status card** on every Cursor launch.
- **Autonomy:** routine ops OK; **escalate** before billing, leaving Always Free, weakening security, paid RPC/GPU/extra VMs/OKE/NAT/LB/Autonomous DB, public Postgres, trading keys / X creds on host, or capital access.
- **Idle reclaim:** legitimate ingest/monitor/paper workload — **never fake keep-alive**.
- **Secrets:** no trading keys and **no X keys** on the host. Paper only. Agents never get trading capital. Bonk/mayhem **parked**.
- **Last Oracle host health (read-only):** **2026-09-23T15:33:46Z** — Cursor agent via DEC-011; checks **PASS** (SSH smoke, `/var/lib/mal` layout, Postgres localhost, `mal-observe` active, health script `ok`); sealed JSONL `observe-2026-09-23.jsonl` **8313** lines / **~13.9 MiB**; schema migration **2026-09-23 07:58:11 UTC**.

## Next work

1. **EXP-002c:** **INCOMPLETE closed** — do **not** promote. Local `--rules v2` + existing marks, seed 1 (PR #18 @ `842cb21`; 26/26 tests OK). population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈**63.18%** → reject_rate_band **INCOMPLETE** (below 70% floor). Runner priced_n=96 mean_return_pct≈**0.385**; reject priced_n=888 mean≈**3202.76**; random priced_n=410 mean≈**2500.58**. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Proof stamp **INCOMPLETE** (primary: reject_rate_band). Soft/directional lift FAIL again. **Next:** **DISCOVERY** first on dual failure (v2 too permissive; survivors still adverse vs random) — then **EXP-002d** only with a **new kill-list hyp**, or **pause**. Do **not** tighten constants to hit 70–95% (OPTIMIZE-to-gate). Soft denser marks still not the fix. Scout wallet/follow **taxonomy**: [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md). [EXP-002c](EXP/EXP-002c-rules-v2-adverse-selection.md). **EXP-002b** remains **FAIL closed** (**FAIL_NO_LIFT_VS_RANDOM**).
2. **Cursor↔Oracle access (DEC-011) LIVE.** Smoke **2026-09-23** (`mal-core-vnic`, aarch64, paper-only) via CF Access Service Auth + Runtime Secrets. Host bootstrap (this memory): `/var/lib/mal` layout, `meme_core` ops/state stubs, sealed JSONL ingest path, health script. Runbook [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md); on-host [BOOTSTRAP.md](ARTIFACTS/ORACLE-HOST-BOOTSTRAP.md). **Do not** expose Postgres; **do not** open `:22` to the world; **do not** put DB passwords in git. [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md).
3. **EXP-003 marks (unchanged law):** Subsample marks exist; book-wide N/A expected. Reuse existing marks for paper EXPs. Densify only after a non-adverse directional hyp needs power — never as lift rescue. [EXP-003](EXP/EXP-003-post-create-marks.md).
4. **EXP-001:** **PASS closed** — mislabel CLI [`tools.exp001_mislabel`](tools/exp001_mislabel.py).
5. Observe-wiring **landed:** [observe/client.py](observe/client.py), [OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
6. Hot-packet JSON spec (capped graph + market spine) aligned to matrix backfill rules; **Graph DISCOVERY brief:** [GRAPH-DISCOVERY-V0.md](ARTIFACTS/GRAPH-DISCOVERY-V0.md) (creator recurrence / weak links first; EXP-004 proposed, not run)
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
- Research: `ARTIFACTS/` (marks: [POST-CREATE-MARKS-BRIEF.md](ARTIFACTS/POST-CREATE-MARKS-BRIEF.md); wallet/follow Discovery: [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md))
- API/cost/latency: [ARTIFACTS/API-COST-LATENCY-BRIEF.md](ARTIFACTS/API-COST-LATENCY-BRIEF.md)
- Laya vs VPS stack compare: [ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md) (Always Free `mal-core-0` is the phase-0 host experiment, not a paid VPS)
- Always Free BOM: [ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md](ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md)
- Phase-0 handoff (live inventory + laws): [ARTIFACTS/ORACLE-PHASE0-HANDOFF.md](ARTIFACTS/ORACLE-PHASE0-HANDOFF.md)
- Cursor↔Oracle access (DEC-011 **LIVE**): [DEC/DEC-011-cursor-oracle-access-cf-tunnel.md](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), runbook [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
- Host bootstrap (paper): [ARTIFACTS/ORACLE-HOST-BOOTSTRAP.md](ARTIFACTS/ORACLE-HOST-BOOTSTRAP.md), [sql/meme_core/](sql/meme_core/)
- Engineering decision log: [ARTIFACTS/ENGINEERING-DECISION-LOG.md](ARTIFACTS/ENGINEERING-DECISION-LOG.md)
- Regime at ingest: [ARTIFACTS/REGIME-AT-INGEST-MATRIX.md](ARTIFACTS/REGIME-AT-INGEST-MATRIX.md), [ARTIFACTS/REGIME-ENUM-V0.md](ARTIFACTS/REGIME-ENUM-V0.md), [ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md](ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md)
