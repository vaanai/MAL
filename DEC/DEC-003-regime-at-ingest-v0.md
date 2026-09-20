# DEC-003 — Regime-at-ingest v0 (working law)

| Field | Value |
| --- | --- |
| **Status** | Active (working law until EXP overturn) |
| **Decider** | Vaan (via manager review) |
| **Date** | 2026-09-20 (recorded) |
| **Supersedes** | Informal “tag later” ingest — violates [CONSTITUTION.md](../CONSTITUTION.md) §4 |

## Decision

1. **Regime taxonomy v0** in [ARTIFACTS/REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) is the canonical phase-0 enum for Scout ingest and hot-packet stamping.
2. **Regime-at-ingest matrix** in [ARTIFACTS/REGIME-AT-INGEST-MATRIX.md](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md) governs WS vs RPC, knowable-at-T, and forbidden backfills.
3. **PumpPortal payload inventory** in [ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md](../ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md) is the reference for observe stream fields until vendor publishes a schema.
4. **`regime_id` encoding and stage vocabulary** are locked in [DEC-004](DEC-004-regime-id-encoding.md) (pipe `key=value`; hot JSONL per [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)).
5. Every hot observe packet MUST carry non-empty `regime_id` and explicit unverified flags where RPC has not completed in the ingest window.
6. **Birdeye paid tiers:** hard **Defer** for phase-0 spine (no paid CU budget); free Standard remains non-spine only per [API brief](../ARTIFACTS/API-COST-LATENCY-BRIEF.md).
7. **Dexscreener:** **debug enrich only** — never on hot spine packet.

## Rationale

- Scout domain review: memory + API spine PASS; observe-wiring was BLOCKED without regime-at-ingest mapping.
- Keeps knowable-at-T auditable and avoids silent retroactive labels on immutable packets.

## Review trigger

- EXP shows &gt;1% wrong `stage` or `quote` labels on stratified sample, **or**
- PumpPortal publishes official JSON schema diverging from inventory, **or**
- Pump.fun program upgrade adds mandatory new regime dimension (e.g. new quote asset default).

## Overturn path

New `EXP-xxx` evidence + replacement DEC or enum v1 artifact; do not edit sealed ingest rows in place.
