# DEC-005 — Hot-packet clocks, Δ_exec, precompute as-of, create-spine provenance

| Field | Value |
| --- | --- |
| **Status** | Active (soft council agreement; working law for packet timing and provenance) |
| **Decider** | Council alignment (Helm / Scout / Graph / Proof) |
| **Date** | 2026-09-20 (council agreement recorded) |
| **Context** | Follow-on to [STARTER-STACK-OPTIONS-BRIEF](../ARTIFACTS/STARTER-STACK-OPTIONS-BRIEF.md) (starter-stack brief, PR #7) |
| **Amends** | Latency metric definitions in starter-stack brief §1.3; complements [DEC-003](DEC-003-regime-at-ingest-v0.md), [REGIME-AT-INGEST-MATRIX](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md) |

## Decision

### 1) Sealed decision clock

- **Candidate time `T`** for knowable-at-T features, JEV inputs, and risk-gate replay is the **sealed decision clock** stamped on the hot packet.
- **Rule:** `T_decision = t_event` when `t_event` is non-null (vendor event time copied per [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)); when `t_event` is **`null`**, **`T_decision = t_ws`** (WebSocket receipt at seal time).
- **Forbidden:** Using a **later** timestamp—async RPC enrich time, hot-packet emit time if emitted after the ingest window, or any post-seal wall clock—as the decision clock for rows where `t_event` was absent at seal.
- **Rationale:** PumpPortal create stream often lacks vendor `timestamp` / `blockTime` ([PUMPPORTAL-PAYLOAD-INVENTORY.md](../ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md)); anchoring on `t_ws` keeps EXP windows and as-of-T precompute auditable without retroactive “better” clocks.

### 2) `Δ_exec` — executable outcome, not land-only

- **`Δ_exec`** measures time from **risk-gate out** to **executable outcome at decision size**, including **slippage, fees, and route constraints** (bonding vs pool), not merely **transaction inclusion** or **slot land**.
- **Land-only** metrics (`t_land - t_gate_out`) may be logged separately (e.g. `Δ_land`) for infra tuning; they **do not** satisfy `Δ_exec` for kill tests, paper exec, or starter-stack latency tables once exec is in scope.
- **Phase 0:** paper or sim may stand in for fills; the definition still requires **economic** outcome at capped size, aligned with [LAB_STATE.md](../LAB_STATE.md) H-edge / fee-slippage discipline.
- **Note:** Starter-stack brief §1.3 used a land-centric placeholder; **this DEC supersedes that row** for lab metric naming going forward.

### 3) Precompute as-of stamp on the hot packet

- Every **hot packet** (immutable spine row per event) MUST carry an explicit **precompute as-of** field: wall time or logical clock through which rolling state was valid for features merged into that packet (lab name: `t_precompute_as_of` or equivalent in the hot-packet spec).
- **Rule:** All precompute features on the packet (curve stats, creator histograms, capped graph scores) MUST be computable from state updated **strictly before** `T_decision` (§1); the stamp documents the latest precompute commit included.
- **Forbidden:** Silent use of post-`T_decision` trades, RPC backfill, or enrich rows to populate spine fields without a **new** packet row.

### 4) Weak create-spine links ≠ RPC/history provenance

- Fields present on the **free PumpPortal create feed** (`traderPublicKey`, reserves, `initialBuy`, etc.) are **weak create-spine links**: knowable-at-T as **reported**, not verified on-chain history ([REGIME-AT-INGEST-MATRIX.md](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md) creator row).
- **RPC-verified** creator, funding source, fee routing, and instruction decode constitute **strong provenance**; they live in ingest-window RPC, async enrich **packets**, or `knowable_at_t.*_verified` flags—not as silent upgrades to the sealed hot row.
- **Forbidden:** Backfilling unverified creator wallet, funding path, or fee regime into the **same** sealed hot packet after RPC contradicts or refines WS; emit a **new** record with `supersedes=` / link per matrix.

## Rationale

- PR #7 options brief separated observe, packet, JEV, gate, and exec clocks; council soft agreement removes ambiguity before hot-packet schema work and EXP latency columns.
- Prevents “looks faster on paper” by sliding `T` forward after enrich, and prevents spine PnL leaks from treating WS creator keys as ground truth without flags.

## Review trigger

- Hot-packet JSON spec lands with conflicting field names; reconcile in spec + optional DEC amend.
- Exec DEC defines live fill telemetry; may add required `Δ_exec` sub-fields without changing §2 intent.
- EXP shows systematic clock skew between `t_ws` and RPC `blockTime` on stratified creates—revisit §1 with evidence, not ad hoc rewrites of sealed rows.

## Overturn path

New DEC or exec-phase DEC; do not edit sealed JSONL / hot packets in place.
