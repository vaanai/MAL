# EXP-001 — 24h PumpPortal WS capture + Proof mis-label sample

| Field | Value |
| --- | --- |
| **Status** | Planned (observe client landed; capture not yet run) |
| **Owner** | Scout (capture) / Proof (mis-label audit) |
| **As-of** | 2026-09-20 |
| **Depends on** | [observe/client.py](../observe/client.py), [DEC-004](../DEC/DEC-004-regime-id-encoding.md) |

## Hypothesis

PumpPortal free WebSocket streams (`subscribeNewToken`, `subscribeMigration`) deliver sufficient fields at **processed** commitment to stamp **knowable-at-T** `regime_id` on every hot row without paid indexers, and WS-reported keys match [PUMPPORTAL-PAYLOAD-INVENTORY.md](../ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md) within measurable drift.

## Method

1. **Environment:** Local WSL2 or Linux host; `python -m observe` with default `wss://pumpportal.fun/api/data` (no API key).
2. **Duration:** **24 hours** continuous capture (single WS connection, both subscriptions on same socket per vendor guidance).
3. **Output:** Append-only JSONL under `data/observe/observe-YYYY-MM-DD.jsonl` per [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md).
4. **Logging:** Structured stderr logs (`ingest_sealed`, reconnects); no secrets.
5. **Post-capture inventory diff:** Union all keys in `ws_payload` vs inventory tables; file inventory PR if new keys appear.
6. **RPC spot-check (optional same EXP):** For a subsample of signatures, `getTransaction` at `processed` and compare reserves to WS numbers (not required to complete 24h clock).

## Fields logged (hot row)

| Field | Purpose |
| --- | --- |
| `t_ws` | Knowable-at-T WebSocket receipt time (always set) |
| `t_event` | Vendor event time when `timestamp` / `blockTime` present in payload; else `null` |
| `stream`, `source`, `commitment` | Spine provenance |
| `stage`, `regime_id` | Regime-at-ingest (DEC-004) |
| `signature`, `mint`, `txType` | Event identity |
| `knowable_at_t` | Explicit unverified flags |
| `ws_payload` | Verbatim vendor JSON |
| `ws_fields_unknown` | Keys not in inventory → track UNK |

## Success criteria

| Criterion | Target |
| --- | --- |
| Uptime | ≥95% of 24h window connected (reconnects allowed) |
| Hot row coverage | 100% of classified create/migration events get `regime_id` ≠ empty |
| Inventory drift | ≤5% of events carry non-empty `ws_fields_unknown` **or** documented in inventory update |
| Disconnect rate | No vendor ban; ≤1 reconnect / 15 min sustained average |

## Proof mis-label plan (n = 100 creates)

**Goal:** Measure wrong `stage` or `quote` labels on stratified create sample (DEC-003 review trigger: &gt;1%).

1. **Sample:** Uniform random **100** rows where `stream=subscribeNewToken` and `txType=create` from the 24h JSONL (seed recorded in EXP appendix).
2. **For each row:**
   - Fetch `getTransaction(signature, commitment=processed)` via light RPC.
   - Confirm tx includes Pump program create/create_v2; record actual instruction lineage → compare to `knowable_at_t.instr` (still `pending_rpc` on hot row is **not** a mis-label; mis-label = wrong `stage` or asserted `quote_verified=true` without evidence).
   - Read bonding curve account: `complete` flag; if true on create row timestamp window, flag **stage** mis-label.
   - Read `quote_mint` on curve; compare to `quote=wsol_assumed` default (mis-label if non-SOL quote stamped as wsol without `*_assumed` path).
3. **Score:** `mislabel_rate = (# mislabeled) / 100`. **Kill / review:** if `mislabel_rate > 0.01`, open enum v1 + DEC review.
4. **Artifact:** Proof appends `EXP/EXP-001-proof-mislabel.md` (or `ARTIFACTS/` capped summary) with table of signature, verdict, notes.

## Kill signals

- Sustained WS disconnects or empty stream &gt;30 min during capture.
- &gt;10% of creates missing `signature` or `mint` in WS payload.
- Proof mis-label rate &gt;1% on the 100-create sample.

## Notes

- No Birdeye paid, no Dexscreener on spine, no trading API.
- Sealed rows are immutable; corrections are new packets only.
