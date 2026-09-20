# DEC-001 — Lean four seats override org chart

| Field | Value |
| --- | --- |
| **Status** | Active |
| **Decider** | Vaan |
| **Date** | 2026-09-20 (recorded) |

## Decision

Operational lab structure uses **four lean seats**, not the broader Research / Strategy / Ops org chart in older docs:

| Seat | Mandate (summary) |
| --- | --- |
| **Helm** | Direction, priorities, decision packets, constitution compliance |
| **Scout** | Observation ingest, regime ID at ingest, market spine |
| **Graph** | Relationship graph hypothesis, capped as-of-T scores |
| **Proof** | Experiments, backtests, kill-attempts, promotion evidence |

## Rationale

- Smaller coordination surface for phase 0 (no DB, local-first, workers ephemeral).
- Maps cleanly to pipeline: Scout → Graph → Proof, with Helm integrating risk gate and exec policy later.
- Avoids duplicating “research” and “strategy” as separate persistent agents before there is measured edge.

## Implications

- Workers (e.g. Cursor agents) produce artifacts; they do **not** replace Helm/Scout/Graph/Proof as persistent managers.
- Any doc referencing Research/Strategy/Ops as **runtime** roles is **superseded** for execution planning; keep only if useful as skill taxonomy.

## Review trigger

Revisit when: (a) live exec is enabled, or (b) team adds a fifth persistent seat for compliance/ops.
