# Post-create marks sourcing brief (Scout)

| | |
| --- | --- |
| **Research as-of** | 2026-09-21 |
| **Owner seat** | Scout (market/regime) |
| **Unblocks** | [EXP-002](../EXP/EXP-002-evaluate-runner-v0.md) Δ_exec / lift (today: sparse / N/A-heavy horizons) |
| **EXP** | [EXP-003](../EXP/EXP-003-post-create-marks.md) |
| **Lab locks** | Sealed `ingest_hot` + `knowable_at_t` **immutable**; marks = enrich / side table; no live capital; Dexscreener debug-only; bonk/mayhem inventory **PARKED** |

Phase-0 observe JSONL is **local** (`data/observe/observe-YYYY-MM-DD.jsonl`, gitignored). Cloud has no capture. Design for **Vaan re-run on his files**.

---

## Problem

EXP-002 paper outcomes look for a post-create **price proxy** (`marketCapSol` / `vSolInBondingCurve`) on later JSONL rows with the same `mint`. Capture is **`subscribeNewToken` + `subscribeMigration` only**, so bonding creates almost never have a 1s–60s path. Kill gates stay **INCOMPLETE** (`priced_n < 10` / arm). `Δ_exec` is N/A from sealed rows alone ([DEC-005](../DEC/DEC-005-hot-packet-clocks-and-provenance.md) draft PR #8: economic outcome, not land-only).

**As-of-H law (v0):** for decision `T` (EXP-002: `T = t_ws` on the create; DEC-005 `T_decision` when that DEC merges) and horizon `H`, the mark used must satisfy **`T < t_mark ≤ T+H`**. Last such tick wins. A tick **after** `T+H` is a **future leak**. The create row at `T` is **entry**, not a horizon mark (using it would paint dead coins as 0% instead of N/A).

Current EXP-002 helper `_nearest_mark_at_or_after` takes the first tick **`≥ T+H`** — leaky **and** sparse. Wiring switches horizon join to **last-at-or-before**.

---

## Options

| ID | Source | Backfills existing JSONL? | Honesty | Cost (phase 0) | Completeness at 1s–60s |
| --- | --- | --- | --- | --- | --- |
| **A** | PumpPortal **`subscribeTokenTrade`** (already in [inventory](PUMPPORTAL-PAYLOAD-INVENTORY.md) §4) | **No** (forward only) | Trade `t_ws` (or payload time if present). Same socket as observe; subscribe `keys=[mint]` on create, **unsubscribe** after window. | Metered **0.01 SOL / 10k msgs** + API key + wallet **≥0.02 SOL**. Free create/migration streams stay $0. [Real-time docs](https://pumpportal.fun/data-api/real-time/) | Dense ticks **if** subscribed before first trades. Concurrent mint cap **needs measurement**. Vendor: **one** WS connection. |
| **B** | **RPC historical** on sealed `bondingCurveKey` / `mint`: `getSignaturesForAddress` then `getTransaction` (`maxSupportedTransactionVersion: 1`) for txs with `blockTime` in `(T, T+H]` | **Yes** | `blockTime` (unix **seconds**) as `t_mark`; drop `t_mark > T+H`. Commitment `confirmed`/`finalized` (this RPC **rejects `processed`**). Price from pump.fun **`Program data:` logs** (TradeEvent / CreateEvent), not top-level tx JSON — see [EXP-003 §10](../EXP/EXP-003-post-create-marks.md). | **$0** public RPC until 429s ([clusters](https://solana.com/docs/references/clusters): ~40 req / 10s / method class). Helius free: 1M credits/mo, 10 rps — upgrade only on measured throttle ([API brief](API-COST-LATENCY-BRIEF.md)). | Completes **past** book. Full-day create volume × (1 sig-list + *k* txs) can 429; **subsample-first**. Live **account poll** at `T+H` does **not** reconstruct Sep 20–21. |
| **C** | Dexscreener REST / candles | Partial, laggy | **Not** knowable-at-T for spine. Pair index lag vs create often **≫ 1s**. | $0; rate-limited | Debug only ([DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md)). **Forbidden** as EXP-002 price path. |

**Not options (phase 0):** Birdeye paid, gRPC/Yellowstone, PumpPortal trading API, inventing prices, rewriting `ingest_hot`.

---

## Join key

| Field | Role |
| --- | --- |
| **`mint`** | Primary join to the sealed create (EXP-002 series key today). |
| **`parent_signature`** | Create `signature` — provenance; disambiguates if a mint were reused (should not happen). |
| **`t_mark`** | Observation time of the tick (not `t_ws` of the create). |
| **`t_decision`** | Copied parent `t_ws` for audit (optional on row; consumer uses parent). |

Side artifact, not a mutation: `data/observe/marks-YYYY-MM-DD.jsonl` (gitignored) **or** extra paths via `--marks`. Schema: [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md) `type=outcome_mark`.

---

## Recommendation — v0 cheapest **complete** path

**B then A.** EXP-002’s population is **already-sealed** local JSONL. Only **RPC historical (B)** can attach 1s–60s marks to those rows without a new capture.

1. **v0 (unblock EXP-002 now):** Offline RPC backfill on Vaan’s PC from sealed `bondingCurveKey` (else `mint`). Write **`outcome_mark` ticks** (not in-place edits). **Subsample** 200–500 bonding creates first (public RPC); scale toward full detect book if 429s stay rare. Join = last-at-or-before. Coverage CLI: `python -m tools.exp003_marks`.
2. **v0.1 (forward, cheaper per live mint):** Same observe process, **`subscribeTokenTrade`** TTL 60s (5m if peak/DD needed), unsubscribe, append marks JSONL. Do **not** put the API key in git. Skip until v0 coverage is **READY** or a new capture day is the scoring book.
3. **Never v0:** Dexscreener on the paper path (C).

**`Δ_exec`:** Marks unlock **gross** lift at horizons. `Δ_exec` stays N/A until Proof stamps a **documented paper fee/slippage** on the book (DEC-005). Do not silently set `fee=global_100bps` on sealed creates (`fee=unverified` stays). Optional later: assume 100 bps **only** in the paper summary, labeled `fee_assumed`.

**Bonk / mayhem:** PARKED. Marks join `mint` / `parent_signature` regardless of regime; no reclassification.

---

## What Vaan runs locally

Cloud cannot see JSONL. PowerShell runbook (ASCII logs):

```text
python -m unittest tools.test_marks tools.test_exp003_marks tools.test_exp003_rpc_backfill tools.test_exp002_paper_runner

set SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
python -m tools.exp003_rpc_backfill ^
  data\observe\observe-2026-09-20.jsonl data\observe\observe-2026-09-21.jsonl ^
  --output-dir data\observe --sample 300 --seed 1 --window-s 60

python -m tools.exp003_marks ^
  data\observe\observe-2026-09-20.jsonl data\observe\observe-2026-09-21.jsonl ^
  --marks data\observe\marks-2026-09-20.jsonl data\observe\marks-2026-09-21.jsonl

python -m tools.exp002_paper_runner ^
  data\observe\observe-2026-09-20.jsonl data\observe\observe-2026-09-21.jsonl ^
  --marks data\observe\marks-2026-09-20.jsonl data\observe\marks-2026-09-21.jsonl ^
  --rules v1
```

Without marks files, coverage is **INCOMPLETE** (expected). RPC producer uses **public** cluster URL only (no API keys in git); subsample on 429 before full book.

---

## Needs measurement

- Trades per mint in 60s / 5m (*k* for RPC cost).
- Public-RPC 429 rate on subsample vs full book.
- PumpPortal max concurrent `subscribeTokenTrade` keys on one socket.
- `blockTime` 1s granularity vs 1s horizon (expect some 1s N/A even when 5s is ok).
- Dead-create rate (zero post-create txs → honest N/A).

## Sources

- PumpPortal real-time: https://pumpportal.fun/data-api/real-time/
- Solana `getSignaturesForAddress`: https://solana.com/docs/rpc/http/getsignaturesforaddress
- [API-COST-LATENCY-BRIEF.md](API-COST-LATENCY-BRIEF.md), [PUMPPORTAL-PAYLOAD-INVENTORY.md](PUMPPORTAL-PAYLOAD-INVENTORY.md), [REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md)
