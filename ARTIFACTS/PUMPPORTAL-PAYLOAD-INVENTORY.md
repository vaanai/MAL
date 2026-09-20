# PumpPortal WebSocket payload inventory (phase 0)

| | |
| --- | --- |
| **As-of** | 2026-09-20 |
| **Endpoint** | `wss://pumpportal.fun/api/data` (optional `?api-key=` for metered streams) |
| **Primary sources** | [Real-time data API](https://pumpportal.fun/data-api/real-time/), [FAQ](https://pumpportal.fun/FAQ/) |
| **Secondary (field names)** | Community examples; **not** an official PumpPortal JSON schema — treat as **needs RPC verify** until MAL logs confirm |

PumpPortal documents **subscribe/unsubscribe methods** and commitment level (`processed`, FAQ) but **does not publish a formal inbound message schema**. This inventory separates **documented client envelopes**, **commonly reported server fields**, and **on-chain fields** useful for RPC verification ([pump-public-docs](https://github.com/pump-fun/pump-public-docs)).

**Legend**

| Tag | Meaning |
| --- | --- |
| **WS** | Field reported in PumpPortal WS JSON (community or MAL capture; not vendor-schema-backed) |
| **RPC** | Confirm via Solana RPC / program account or tx logs |
| **UNK** | Not documented; do not assume |

---

## 1) Connection and client → server envelopes

| Field | Direction | Required | Tag | Notes / source |
| --- | --- | --- | --- | --- |
| `method` | Client → server | Yes | **WS** | e.g. `subscribeNewToken`, `subscribeMigration`, `unsubscribeNewToken` — [real-time docs](https://pumpportal.fun/data-api/real-time/) |
| `keys` | Client → server | For trade subs | **WS** | Array of mint or wallet pubkeys for `subscribeTokenTrade` / `subscribeAccountTrade` |
| `api-key` (query) | URL | Metered only | **WS** | Trade/account streams require funded wallet per vendor; new token + migration are **free** without key — [real-time docs](https://pumpportal.fun/data-api/real-time/) |

**Operational (not payload fields):** single WS connection, reconnect on disconnect, `processed` commitment (~&lt;100 ms vs gRPC NYC per FAQ) — [FAQ](https://pumpportal.fun/FAQ/).

---

## 2) Free observe stream: `subscribeNewToken`

**Subscribe:** `{"method":"subscribeNewToken"}` — [real-time docs](https://pumpportal.fun/data-api/real-time/).

**Unsubscribe:** `{"method":"unsubscribeNewToken"}`.

### 2a) Commonly reported creation event fields (`txType` ≈ `create`)

| Field | Tag | Notes |
| --- | --- | --- |
| `txType` | **WS** | Reported value `"create"` in community examples; vendor does not enumerate — **RPC** match to create/create_v2 instruction |
| `signature` | **WS** | Solana transaction signature; **RPC** `getTransaction` for slot/time/logs |
| `mint` | **WS** | Token mint pubkey |
| `traderPublicKey` | **WS** | Signer/creator wallet in examples; on-chain `CreateEvent` has `user` / `creator` — **RPC** disambiguate |
| `name` | **WS** | Metadata; aligns with `CreateEvent.name` — [events in pump-public-docs](https://github.com/pump-fun/pump-public-docs) |
| `symbol` | **WS** | Metadata |
| `uri` | **WS** | Metadata URI |
| `bondingCurveKey` | **WS** | Bonding curve account; PDA `["bonding-curve", mint]` per [PUMP_PROGRAM_README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md) — **RPC** verify |
| `initialBuy` | **WS** | Token amount of creator initial buy in examples — **RPC** verify units |
| `solAmount` | **WS** | SOL spent on initial buy in examples |
| `vTokensInBondingCurve` | **WS** | Virtual token reserves snapshot; maps to bonding curve `virtual_token_reserves` — **RPC** |
| `vSolInBondingCurve` | **WS** | Virtual SOL reserves snapshot; maps to `virtual_sol_reserves` / quote reserves — **RPC** |
| `marketCapSol` | **WS** | Derived metric in SOL; definition not in PumpPortal docs — **RPC** recompute from curve state |

### 2b) On-chain fields **not** in typical WS create JSON (stamp via RPC if needed for regime)

| Field | Tag | Source |
| --- | --- | --- |
| `quote_mint` | **RPC** | Bonding curve `quote_mint`; SOL pairs use wrapped SOL mint per [pump-public-docs README](https://github.com/pump-fun/pump-public-docs/blob/main/README.md) |
| `token_program` / Token2022 vs SPL | **RPC** | `create` vs `create_v2` — [pump-public-docs](https://github.com/pump-fun/pump-public-docs) |
| `isMayhemMode` | **RPC** | `CreateEvent` — third-party SDK notes; **UNK** on WS |
| `is_holder_reward` / `is_cashback_enabled` | **RPC** | Holder rewards / deprecated cashback — [pump-public-docs README](https://github.com/pump-fun/pump-public-docs/blob/main/README.md) |
| `timestamp` (on-chain) | **RPC** | `CreateEvent.timestamp`; WS may lack — **UNK** on WS |
| `slot` / `blockTime` | **RPC** | From `getTransaction` only |
| Program ID `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | **RPC** | [PUMP_PROGRAM_README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md) |

### 2c) Lab ingest envelope (MAL-added, not from PumpPortal)

| Field | Tag | Notes |
| --- | --- | --- |
| `t_ws` | MAL | Wall-clock receipt — [API brief §3](API-COST-LATENCY-BRIEF.md) |
| `regime_id` | MAL | Required at ingest — [CONSTITUTION.md](../CONSTITUTION.md), [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md) |
| `stream` | MAL | e.g. `pumpportal.subscribeNewToken` |
| `commitment_assumed` | MAL | `processed` per FAQ |

---

## 3) Free observe stream: `subscribeMigration`

**Subscribe:** `{"method":"subscribeMigration"}` — [real-time docs](https://pumpportal.fun/data-api/real-time/).

**Note:** Some third-party code uses non-documented method names (e.g. `subscribeRaydiumMigrations`). Phase 0 uses **only** vendor-documented `subscribeMigration`.

### 3a) Commonly reported migration event fields (`txType` ≈ `migration`)

| Field | Tag | Notes |
| --- | --- | --- |
| `txType` | **WS** | Reported `"migration"` in integrations; vendor schema **UNK** |
| `mint` | **WS** | Graduated token |
| `pool` | **WS** | Destination pool pubkey; post-2025 default graduation target is **PumpSwap** per [pump-public-docs](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md) — **RPC** decode pool owner/program to label `pumpswap` vs legacy Raydium |
| `signature` | **WS** | **RPC** verify migrate instruction |
| `traderPublicKey` | **UNK** | Sometimes present on other event types; not confirmed for migration |

### 3b) RPC-only migration context

| Field | Tag | Notes |
| --- | --- | --- |
| Bonding curve `complete` flag | **RPC** | Bonding curve account state — [PUMP_PROGRAM_README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md) |
| Migrate instruction idempotency | **RPC** | `migrate` permissionless once curve complete |
| PumpSwap `Pool` / `virtual_quote_reserves` | **RPC** | [PumpSwap docs](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md) |

---

## 4) Related streams (phase 0: **not** free observe spine)

Documented for completeness; **metered** and require API key + wallet ≥0.02 SOL — [real-time docs](https://pumpportal.fun/data-api/real-time/).

### `subscribeTokenTrade` / `subscribeAccountTrade`

| Field | Tag | Notes |
| --- | --- | --- |
| `txType` | **WS** | `"buy"` / `"sell"` reported |
| `mint` | **WS** | |
| `solAmount` | **WS** | |
| `isBuy` | **WS** | |
| `vSolInBondingCurve` | **WS** | Curve fill progress on bonding trades |
| `vTokensInBondingCurve` | **UNK** | Sometimes paired with SOL reserves in create events; trade messages **needs measurement** |
| `tokenAmount` | **UNK** | **needs measurement** |
| `signature` | **WS** | Common |
| `traderPublicKey` | **WS** | Common |
| `marketCapSol` | **WS** | Reported on some trade events — **needs measurement** |

**Array batches:** Third-party clients note PumpPortal may send **JSON arrays** of events in one WS frame — **needs measurement** on MAL connect.

---

## 5) Gaps and EXP actions

1. **Capture 24h JSONL** from `subscribeNewToken` + `subscribeMigration` on lab WS client; diff keys vs this table → update inventory or file `EXP-xxx`.
2. For each `signature`, spot-check **RPC** `getTransaction` (commitment `processed` then `confirmed`) and compare reserves to WS numbers.
3. Do **not** treat Dexscreener or Birdeye fields as substitutes for WS/RPC spine — [API brief](API-COST-LATENCY-BRIEF.md).

## Sources

- PumpPortal: https://pumpportal.fun/data-api/real-time/ , https://pumpportal.fun/FAQ/
- Pump.fun program: https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md
- PumpSwap: https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md
