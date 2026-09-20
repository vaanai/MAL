# Regime taxonomy v0 (phase 0 draft)

| | |
| --- | --- |
| **Status** | **v0 — subject to EXP revision**; not promoted law until [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md) accepted |
| **As-of** | 2026-09-20 |
| **Owner seat** | Scout (ingest labels); Proof (kill tests on mis-label rate) |

**Purpose:** Satisfy constitution **regime ID at ingest** ([CONSTITUTION.md](../CONSTITUTION.md) §4) with a finite, enumerable set of dimensions that can be stamped on the hot packet **at observation time T** without paid indexers.

**Format:** `regime_id` is a stable **pipe-separated `key=value`** string per [DEC-004](../DEC/DEC-004-regime-id-encoding.md). Example composite:

```text
env=mainnet|source=pumpportal_ws|stream=subscribeNewToken|stage=bonding|quote=wsol_assumed|commitment=processed|venue=pump_program|instr=pending_rpc|fee=unverified|market=bonding_curve
```

**Stage vocabulary (unified):** `bonding` | `bonding_complete` | `migrating` | `pumpswap` | `legacy_raydium` (plus `UNK` only when WS classification fails).

---

## 1) Bonding-curve program / instruction lineage

| Tag | Meaning | How to know at T |
| --- | --- | --- |
| `venue=pump_program` | Pump bonding program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | **RPC** tx program id; default for PumpPortal create stream |
| `instr=create` | Legacy SPL create | **RPC** instruction discriminator / logs |
| `instr=create_v2` | Token2022 / extended create | **RPC** — [pump-public-docs](https://github.com/pump-fun/pump-public-docs/blob/main/README.md) |
| `trade_iface=legacy` | `buy` / `sell` | **RPC** on first trade |
| `trade_iface=v2` | `buy_v2` / `sell_v2` / `buy_exact_quote_in_v2` | **RPC** — unified account layout per pump-public-docs |

**v0 default at new-token WS:** `venue=pump_program` + `instr=unknown_until_rpc` (must resolve before hot packet promotes past observe-only log).

---

## 2) Fee regime

| Tag | Meaning | How to know at T |
| --- | --- | --- |
| `fee=global_100bps` | Global `fee_basis_points == 100` (1%) on bonding trades | **RPC** `Global` account or event logs — [PUMP_PROGRAM_README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md) |
| `fee=creator_dynamic` | Per-coin creator fee / holder-reward routing | **RPC** `sharing_config`, `is_holder_reward` flags — [README holder rewards](https://github.com/pump-fun/pump-public-docs/blob/main/README.md) |
| `fee=pumpswap_pool` | PumpSwap pool fee parameters | **RPC** pool state after migration |

**v0 rule:** At **create** ingest, stamp `fee=global_100bps` only if RPC/read confirms; else `fee=unverified` (still tagged — never blank).

---

## 3) Graduation / migration rules

| Tag | Meaning | How to know at T |
| --- | --- | --- |
| `stage=bonding` | Active bonding curve; not complete | WS curve reserves + **RPC** `complete==false` |
| `stage=bonding_complete` | Curve filled; migrate may be pending | **RPC** bonding curve complete flag |
| `stage=migrating` | Migrate tx observed | WS `subscribeMigration` and/or **RPC** migrate ix |
| `stage=pumpswap` | Liquidity on PumpSwap AMM | WS `pool` + **RPC** pool owner program |
| `stage=legacy_raydium` | Older Raydium graduation path | **RPC** pool program id — treat as distinct regime for backtests |

**Rule change note:** Program docs: graduation migrates to **PumpSwap** (not Raydium-only) — [PUMP_PROGRAM_README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md). Third-party summaries mention March 2025 default; **EXP must timestamp** when MAL first observes PumpSwap vs Raydium pools on migration WS.

**Threshold:** Market-cap / reserve threshold is defined on-chain (curve completion), not in PumpPortal WS docs — **RPC** only; do not hard-code `69 SOL` without measurement.

---

## 4) Quote / pair asset

| Tag | Meaning | How to know at T |
| --- | --- | --- |
| `quote=wsol` | SOL-quoted meme (wrapped SOL mint in v2 interface) | **RPC** `quote_mint == So111...` or default for legacy coins — [README](https://github.com/pump-fun/pump-public-docs/blob/main/README.md) |
| `quote=usdc` | USDC-quoted bonding | **RPC** `quote_mint` on bonding curve |
| `quote=other` | Future quote mints | **RPC** |

**v0:** PumpPortal create WS does **not** expose `quote_mint` — default tag `quote=wsol_assumed` until RPC confirms.

---

## 5) Trading venue stage (bonding vs PumpSwap vs off-pump)

| Tag | Meaning | How to know at T |
| --- | --- | --- |
| `market=bonding_curve` | Trades on pump bonding curve | WS `txType` buy/sell + reserves fields |
| `market=pumpswap` | Post-migration AMM | Migration event + **RPC** |
| `market=external` | Raydium / other via `pool` param on trade APIs | **Defer** for phase-0 observe spine |

PumpPortal trading API `pool` values (`pump`, `pump-amm`, `raydium`, …) describe **execution routing**, not observe ingest — [trading API](https://pumpportal.fun/trading-api/). Regime tags follow **on-chain venue**, not API pool strings.

---

## 6) Other Pump environment shifts (v0 watch list)

| Shift | Regime impact | Detection |
| --- | --- | --- |
| Holder rewards coins | `fee=creator_dynamic`, holder payout | `is_holder_reward` on create — [README](https://github.com/pump-fun/pump-public-docs/blob/main/README.md) |
| Cashback deprecated | No new `cashback` tag after cutoff | `create_v2` rejects cashback |
| PumpSwap `virtual_quote_reserves` | Quoting/indexing | Pool account + events — [PUMP_SWAP_README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md) |
| Mayhem mode (legacy) | Optional flag on some creates | **RPC** `CreateEvent` |
| WS commitment `processed` | Latency vs confirmed gate | Stamp `commitment=processed` on every PumpPortal row — [FAQ](https://pumpportal.fun/FAQ/) |

---

## 7) v0 minimum `regime_id` for observe-wiring

Before hot-packet promotion, each ingest row MUST include at least:

| Component | Required value (v0) |
| --- | --- |
| `env` | `mainnet` (PumpPortal is mainnet-only per FAQ) |
| `source` | `pumpportal_ws` |
| `stream` | `subscribeNewToken` \| `subscribeMigration` |
| `stage` | from §3 (WS-derivable coarse stage allowed at T; refine via RPC async) |
| `quote` | `wsol_assumed` or RPC-verified |
| `commitment` | `processed` |

**Revision:** Any EXP showing &gt;1% mis-label rate on `stage` or `quote` triggers enum v1 + DEC or EXP overturn.

## Sources

- https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md
- https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md
- https://github.com/pump-fun/pump-public-docs/blob/main/README.md
- https://pumpportal.fun/data-api/real-time/
- https://pumpportal.fun/FAQ/
