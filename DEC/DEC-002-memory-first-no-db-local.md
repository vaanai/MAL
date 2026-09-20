# DEC-002 — Memory first; no DB; local-first; managers only persistent

| Field | Value |
| --- | --- |
| **Status** | Active |
| **Decider** | Vaan (lab policy) |
| **Date** | 2026-09-20 (recorded) |

## Decision

1. **Memory first** — Durable lab state lives in-repo (`LAB_STATE.md`, `DEC/`, `EXP/`, `ARTIFACTS/`, `CONSTITUTION.md`). Managers reload compact state instead of re-deriving from chat.
2. **No database in phase 0** — No Postgres/Redis/etc. until a measured need (volume, query patterns, multi-writer concurrency).
3. **Managers sole persistent agents** — Grok bots (Helm/Scout/Graph/Proof) hold continuity; Cursor/worker runs produce PRs and artifacts then exit.
4. **Local-first runtime** — Primary dev/observe on home PC; **WSL2 + Docker** when containerized stack is needed. Cloud pods only after metrics justify cost/latency.
5. **GitHub is source of truth** — Decisions, experiments, and briefs merge via PR; no shadow wiki.

## Rationale

- Avoids infra spend and schema churn before hypotheses are killed or promoted.
- Makes “knowable-at-T” auditable via immutable decision packets and git history.
- Matches soft ~$60/mo team LLM budget separate from market-data infra.

## Out of scope (phase 0)

- Managed DB, time-series DB, or cloud-only ingest
- Secrets in repo (use local env only)

## Review trigger

Promote storage when **any** of: >GB/day raw ingest retained locally, multi-seat concurrent writes, or replay queries exceed flat-file practicality — document in a new DEC.
