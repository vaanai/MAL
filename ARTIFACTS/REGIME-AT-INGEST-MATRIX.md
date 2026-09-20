# Regime-at-ingest matrix (v0)

| | |
| --- | --- |
| **As-of** | 2026-09-20 |
| **Pairs with** | [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md), [PUMPPORTAL-PAYLOAD-INVENTORY.md](PUMPPORTAL-PAYLOAD-INVENTORY.md), [CONSTITUTION.md](../CONSTITUTION.md) |

**Goal:** For each regime dimension, define (1) WS-only vs RPC-verify path, (2) **knowable-at-T** rules, (3) what to **stamp on the hot packet at ingest**, and (4) what must **never** be backfilled into a stamped packet (fix forward with a new row/packet).

**Hot packet** here means the first immutable observe record emitted by Scout for an event (JSONL line or capped JSON artifact). Constitution: regime at ingest, knowable-at-T, immutable decision packets.

---

## Matrix

| Dimension | WS-only at T? | RPC required? | Knowable at T (v0 rule) | Stamp on hot packet at ingest | Do NOT backfill later into same packet |
| --- | --- | --- | --- | --- | --- |
| **Event identity** (`signature`, `mint`) | Yes — if present in WS | Recommended confirm | T = `t_ws`; signature/mint treated as true when WS delivers | `signature`, `mint`, `t_ws`, `stream` | Replacing `signature`/`mint` after RPC mismatch — emit **new** correction packet with link `supersedes=` |
| **Stream type** | Yes | No | Known from subscription handler | `stream`, `txType` (raw WS) | Changing `stream` retroactively |
| **Commitment level** | Assumed | Optional lag measure | T assumes FAQ `processed` | `commitment=processed`, `source=pumpportal_ws` | Upgrading to `confirmed` on same row — new row with `t_rpc_confirm` |
| **Stage** (`bonding` / `migrating` / `pumpswap`) | Partial — migration WS; create⇒bonding | Yes for `bonding_complete`, pool program | At create T: `stage=bonding` if `txType=create`; at migration T: `stage=migrating` + `pool`; refine with RPC in **same** ingest only if RPC completes before emit deadline | `stage`, `pool` (if migration), coarse reserves from WS | Setting `stage=pumpswap` on create row without migration event; patching stage after packet sealed |
| **Quote asset** | No | Yes (`quote_mint`) | Not knowable from free WS create alone | `quote=wsol_assumed` + `quote_verified=false` | Flipping `quote_verified` true on sealed packet — use child `enrich` packet |
| **Instruction lineage** (`create` vs `create_v2`, trade v2) | No | Yes | Not at first WS byte unless txType encodes it (UNK) | `instr=pending_rpc` | Backfilling `instr=` on sealed ingest row |
| **Fee regime** | No | Yes (Global + per-coin) | Default uncertain at create | `fee=unverified` or `fee=global_100bps` if RPC cached | Changing fee tag after trades executed under old label |
| **Bonding reserves** | Yes — snapshot fields | Yes — reconcile | WS numbers are **as-of processed T** | `vSolInBondingCurve`, `vTokensInBondingCurve`, `marketCapSol` (if present) with `reserves_source=ws` | Overwriting WS reserves with RPC-corrected values in-place — append `reserves_rpc` child or new snapshot id |
| **Creator wallet** | Yes — `traderPublicKey` | Yes — `creator` in CreateEvent | WS key is knowable-at-T as **reported** | `traderPublicKey` + `creator_verified=false` | Replacing creator pubkey silently |
| **Metadata** (`name`, `symbol`, `uri`) | Yes | Optional fetch | Knowable-at-T as WS strings | Copy verbatim | Editing name/symbol after seal (metadata can change off-chain) |
| **Program / venue** | No | Yes | Pump program id constant for universe | `venue=pump_program` (policy) + `venue_verified=false` until RPC | N/A |
| **PumpSwap pool economics** | Partial — `pool` pubkey on migration | Yes — decode pool | At migration T: pool address from WS | `pool`, `market=pumpswap_pending` until RPC | Pool address correction → new packet |
| **Latency observability** | Yes (local clock) | Optional | Always knowable | `t_ws`; optional `t_rpc_start`/`t_rpc_end` if sync RPC in ingest window | Fabricating `t_ws` post hoc |
| **Regime composite** | Derived | Mixed | Must be **non-empty** at emit | `regime_id` string per [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md) | Empty → violate constitution; filling `regime_id` after emit |
| **Dexscreener enrich** | N/A (debug) | HTTP | **Not** knowable-at-T for spine | **Do not stamp** on hot packet in v0 | Any Dex pair stats on spine packet |
| **Birdeye enrich** | N/A (deferred) | HTTP | **Defer** paid index | **Do not stamp** | N/A phase 0 |

---

## Ingest timing rules (v0)

1. **Emit deadline:** Scout may wait **≤500 ms** after `t_ws` for a single RPC round-trip (`getTransaction` or bonding curve account) before emitting; if RPC pending, emit with `*_verified=false` flags and schedule async enrich **as new records**.
2. **Knowable-at-T:** Fields stamped without verification must be labeled (`*_assumed`, `*_verified=false`) so Proof can filter EXPs.
3. **Migration vs create:** Never infer migration from curve fill alone on the create packet; `subscribeMigration` (or RPC `complete`) is required to move `stage` past `bonding`.
4. **Processed vs confirmed:** Risk gate (future) may require `confirmed`; observe phase logs both when measured — do not rewrite ingest rows.

---

## What async enrich MAY add (new artifacts only)

| Allowed | Mechanism |
| --- | --- |
| RPC-verified quote, instr, fee | New JSONL line: `type=regime_enrich`, `parent_signature=...` |
| `t_rpc_confirm`, Δ latency | New metric row linked by `signature` |
| Graph scores | Separate capped packet per [CONSTITUTION.md](../CONSTITUTION.md) §5 |
| Human debug (Dexscreener) | `type=debug_dexscreener`, never merged into hot spine |

---

## Unblocks observe-wiring checklist

- [x] Field inventory — [PUMPPORTAL-PAYLOAD-INVENTORY.md](PUMPPORTAL-PAYLOAD-INVENTORY.md)
- [x] Regime enum v0 — [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md)
- [x] This matrix
- [ ] Scout implements WS client + JSONL schema (next PR)
- [ ] Proof EXP: mis-label rate on 100 random creates

Until the last two items pass, **observe-wiring** is schema-unblocked but not code-complete.

## Sources

- [CONSTITUTION.md](../CONSTITUTION.md)
- [API-COST-LATENCY-BRIEF.md](API-COST-LATENCY-BRIEF.md)
- PumpPortal FAQ (processed commitment): https://pumpportal.fun/FAQ/
- Pump program state: https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md
