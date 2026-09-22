# DEC-002 — Memory first; no DB; local-first; managers only persistent

> **Amended by [DEC-009](DEC-009-oracle-always-free-phase0-host.md) (2026-09-22).** Memory-first, GitHub as DEC/EXP/`LAB_STATE` SoT, and Grok managers as the only persistent agents **remain**. **No-database / laptop-only-forever for continuous runtime is superseded:** intended primary 24/7 host is Oracle Always Free `mal-core-0` (**VM.Standard.A1.Flex, 2 OCPU / 12 GB**, **pending provision**); on-box Postgres is allowed as a **Layer-2 cache / continuous ops aid**. **Sealed JSONL remains the EXP / knowable-at-T spine day-1** — do **not** force-migrate observe marks or EXP provenance into Postgres on day-1. Original decision text below is **historical**.

| Field | Value |
| --- | --- |
| **Status** | Active (**amended by DEC-009**) |
| **Decider** | Vaan (lab policy) |
| **Date** | 2026-09-20 (recorded); amended 2026-09-22 |

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
