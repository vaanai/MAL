# DEC-004 — `regime_id` encoding and stage vocabulary

| Field | Value |
| --- | --- |
| **Status** | Active (working law; amends [DEC-003](DEC-003-regime-at-ingest-v0.md) §implementation detail) |
| **Decider** | Vaan (via manager review) |
| **Date** | 2026-09-20 (recorded) |
| **Supersedes** | Open choice in [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) (“slug vs pipe”) |

## Decision

### 1) `regime_id` encoding

- **Format:** single string, **pipe-separated** `key=value` tokens.
- **Separator:** `|` (pipe). No spaces around `=`.
- **Canonical key order** (omit nothing in v0 hot rows; use explicit `UNK` only where noted for *stage* on the top-level `stage` field, not inside `regime_id` components):

  1. `env`
  2. `source`
  3. `stream`
  4. `stage`
  5. `quote`
  6. `commitment`
  7. `venue`
  8. `instr`
  9. `fee`
  10. `market`

- **Example (new token create at T):**

  ```text
  env=mainnet|source=pumpportal_ws|stream=subscribeNewToken|stage=bonding|quote=wsol_assumed|commitment=processed|venue=pump_program|instr=pending_rpc|fee=unverified|market=bonding_curve
  ```

- **Example (migration event at T, pool program not yet RPC-decoded):**

  ```text
  env=mainnet|source=pumpportal_ws|stream=subscribeMigration|stage=migrating|quote=wsol_assumed|commitment=processed|venue=pump_program|instr=pending_rpc|fee=unverified|market=pumpswap_pending
  ```

- Scout **MUST** emit identical encoding in `regime_id` and mirror coarse tags in `knowable_at_t` on the hot JSONL row per [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md).

### 2) Stage vocabulary (unified)

Only these **top-level `stage` values** are valid on hot observe rows in phase 0:

| `stage` | When to stamp |
| --- | --- |
| `bonding` | `subscribeNewToken` with `txType=create` (or equivalent creation event) |
| `bonding_complete` | **RPC-only** at ingest unless a future WS field is inventory-proven; not inferred from curve fill on create row |
| `migrating` | `subscribeMigration` with `txType=migration` |
| `pumpswap` | Migration + **RPC** confirms PumpSwap pool program (async enrich or ingest window RPC) |
| `legacy_raydium` | Migration + **RPC** confirms legacy Raydium pool program |

If WS `txType` or stream classification is missing or unknown, set top-level `stage` to `UNK` and still emit non-empty `regime_id` with `stage=UNK` in the composite string.

### 3) Non-spine enrich (unchanged)

- **Birdeye paid:** hard **Defer** (DEC-003).
- **Dexscreener:** **debug enrich only** — never merged into hot spine or `regime_id`.

## Rationale

- Pipe `key=value` is grep-friendly, stable for EXP diffs, and matches the illustrative composite in REGIME-ENUM-V0.
- A single stage enum removes drift between matrix, enum doc, and Scout code.

## Review trigger

Same as DEC-003: &gt;1% mis-label on stratified Proof sample, vendor schema change, or program upgrade adding mandatory dimensions.

## Overturn path

New DEC or enum v1; do not rewrite sealed JSONL rows.
