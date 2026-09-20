# DEC-006 — Detect → decode → evaluate → runners (paper path)

| Field | Value |
| --- | --- |
| **Status** | Active (working law; council 2026-09-20 PT) |
| **Decider** | Council alignment (Helm / Scout / Graph / Proof) |
| **Date** | 2026-09-20 (recorded) |
| **Context** | Names the phase-0 promotion path before any live-capital DEC; complements [DEC-003](DEC-003-regime-at-ingest-v0.md), [DEC-004](DEC-004-regime-id-encoding.md) |
| **Related** | `Δ_exec` and sealed decision clock → [DEC-005](DEC-005-hot-packet-clocks-and-provenance.md) (merge via draft PR when landed) |

## Decision

### Pipeline vocabulary (working law)

| Stage | Role |
| --- | --- |
| **Detect** | Sealed creates (and defined observe events) — immutable ingest rows; no gate on what gets recorded. |
| **Decode** | Knowable-at-T **packet**: regime + capped as-of-T graph when evidence exists; **X attach when present** ([CONSTITUTION.md](../CONSTITUTION.md) reassess-only). |
| **Evaluate** | Filter on **that packet only** — not raw WebSocket replay or post-hoc enrich on the same row. |
| **Runners** | **Paper promotion** only: pretend-buy at runner time; mark outcome horizons **1s / 5s / 15s / 30s / 60s** plus **+2s / +10s / +5m / peak / drawdown**; include fees, slippage, and latency in **`Δ_exec`** (align with [DEC-005](DEC-005-hot-packet-clocks-and-provenance.md) intent when present). |

### Promotion and spend discipline

- **No live capital** until winner traits survive **Proof kill-attempt** under costs.
- **Evaluate strictness** and **JEV vs rules** are EXP knobs on the **paper book** — not early council locks.
- **Cheap-first / measure-before-pay** stands ([CONSTITUTION.md](../CONSTITUTION.md) §9, [API brief](../ARTIFACTS/API-COST-LATENCY-BRIEF.md)).

### Mapping to lab seats (informative)

- Scout: detect + decode spine; Graph: capped graph in decode packet; Proof: evaluate→runner EXPs and kill tests; Helm: DEC packets and promotion policy.

## Rationale

- Separates **recording** (full detect book) from **selection** (evaluate) from **counterfactual PnL** (runners), so EXPs do not conflate ingest completeness with filter quality.
- Paper runners with explicit horizons and `Δ_exec` keep H-edge / fee-slippage discipline before any exec DEC.

## Review trigger

- First evaluate→runner EXP reports systematic filter lift that vanishes under [DEC-007](DEC-007-full-detect-book-anti-selection-bias.md) reject-cohort checks.
- Hot-packet or exec DEC changes runner horizon set or fill model — amend via new DEC section, not silent metric drift.

## Overturn path

New DEC or exec-phase DEC; do not rewrite sealed detect rows or delete evaluate labels in place.
