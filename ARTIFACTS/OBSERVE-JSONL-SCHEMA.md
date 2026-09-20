# Observe hot JSONL schema (v0)

| | |
| --- | --- |
| **As-of** | 2026-09-20 |
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
