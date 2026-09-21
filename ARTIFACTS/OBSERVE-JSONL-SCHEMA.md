# Observe hot JSONL schema (v0)

| | |
| --- | --- |
| **As-of** | 2026-09-21 |
| **Producer** | [observe/client.py](../observe/client.py) |
| **Encoding law** | [DEC-004](../DEC/DEC-004-regime-id-encoding.md) |

**Path:** `data/observe/observe-YYYY-MM-DD.jsonl` (UTC date of `t_ws`). One JSON object per line, append-only.

## Row shape (`type=ingest_hot`)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | string | yes | `observe_hot_v0` |
| `type` | string | yes | `ingest_hot` |
| `t_ws` | string (ISO-8601 UTC) | yes | Wall-clock at WebSocket message receipt (latency anchor) |
| `t_event` | string (ISO-8601 UTC) or `null` | yes | Event time from WS payload when knowable-at-T (`timestamp` or `blockTime`); **`null` if absent** — never synthesized |
| `stream` | string | yes | `subscribeNewToken` \| `subscribeMigration` |
| `source` | string | yes | `pumpportal_ws` |
| `commitment` | string | yes | `processed` (FAQ assumption) |
| `stage` | string | yes | `bonding` \| `bonding_complete` \| `migrating` \| `pumpswap` \| `legacy_raydium` \| `UNK` |
| `regime_id` | string | yes | Pipe `key=value` per DEC-004 |
| `txType` | string | yes | Raw WS or `UNK` |
| `signature` | string | yes | Or `UNK` if absent |
| `mint` | string | yes | Or `UNK` if absent |
| `knowable_at_t` | object | yes | Unverified flags per matrix |
| `ws_payload` | object | yes | Verbatim parsed WS object |
| `ws_fields_unknown` | string[] | yes | Keys not in payload inventory |
| `traderPublicKey`, metadata, reserves | various | no | Copied from WS when present on create |
| `pool` | string | no | Migration events when present |

## `knowable_at_t` (v0 defaults)

```json
{
  "quote": "wsol_assumed",
  "quote_verified": false,
  "instr": "pending_rpc",
  "fee": "unverified",
  "venue": "pump_program",
  "venue_verified": false,
  "creator_verified": false,
  "reserves_source": "ws"
}
```

`reserves_source` is `UNK` on migration rows until inventory proves reserve fields.

## Dual timestamps (latency-as-data)

- **`t_ws`:** always set at ingest (local receipt).
- **`t_event`:** copied from vendor payload only when `timestamp` or `blockTime` is present; numeric values normalized to ISO-8601 UTC. If neither field is in the payload, **`t_event` is JSON `null`** (typical for current PumpPortal create stream per inventory).
- Proof / EXP may compute `t_ws − t_event` only on rows where `t_event` is non-null; RPC `blockTime` enrich uses a separate packet per matrix.

## Example line (truncated)

```json
{"schema_version":"observe_hot_v0","type":"ingest_hot","t_ws":"2026-09-20T12:00:00.123+00:00","stream":"subscribeNewToken","source":"pumpportal_ws","commitment":"processed","stage":"bonding","regime_id":"env=mainnet|source=pumpportal_ws|stream=subscribeNewToken|stage=bonding|quote=wsol_assumed|commitment=processed|venue=pump_program|instr=pending_rpc|fee=unverified|market=bonding_curve","txType":"create","signature":"…","mint":"…","knowable_at_t":{"quote":"wsol_assumed","quote_verified":false,"instr":"pending_rpc","fee":"unverified","venue":"pump_program","venue_verified":false,"creator_verified":false,"reserves_source":"ws"},"ws_payload":{},"ws_fields_unknown":[]}
```

## Forbidden on hot row

- Dexscreener or Birdeye fields
- Retroactive edits (fix forward with new line / enrich type per matrix)
- Post-create outcome ticks (those are `type=outcome_mark`, never patched onto this object)

---

## Sibling row shape (`type=outcome_mark`) — EXP-003

Post-create price ticks for paper horizons. **New lines only** (side file `data/observe/marks-YYYY-MM-DD.jsonl` preferred). Join: `mint` + `parent_signature` (create `signature`). Consumer: last tick with **`T < t_mark ≤ T+H`** ([POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md), [EXP-003](../EXP/EXP-003-post-create-marks.md)).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | string | yes | `observe_mark_v0` |
| `type` | string | yes | `outcome_mark` |
| `mint` | string | yes | Join to sealed create |
| `parent_signature` | string | yes | Create `signature` |
| `t_mark` | string (ISO-8601 UTC) | yes | Tick time (RPC `blockTime` or trade receipt) |
| `source` | string | yes | `rpc_tx` \| `account_state` \| `rpc_account_poll` \| `pumpportal_ws_trade` — **not** Dexscreener/Birdeye |
| `decode_path` | string | no | `program_log` \| `bonding_curve_account` — RPC stratification (EXP-003) |
| `price_proxy` | number | yes* | Finite `>0`; *else* `marketCapSol` or `vSolInBondingCurve` |
| `t_decision` | string or omit | no | Copy of parent `t_ws` (audit) |
| `signature` | string | no | Trade/tx signature |
| `commitment` | string | no | `confirmed` typical for RPC |
| `price_field` | string | no | Which field fed `price_proxy` |
| `txType`, `solAmount`, `tokenAmount`, reserves | various | no | Optional for later paper `Δ_exec` |

Example:

```json
{"schema_version":"observe_mark_v0","type":"outcome_mark","mint":"…","parent_signature":"…","t_mark":"2026-09-21T12:00:05.000+00:00","source":"rpc_tx","price_proxy":1.25,"t_decision":"2026-09-21T12:00:00.000+00:00","commitment":"confirmed","price_field":"vSolInBondingCurve"}
```
