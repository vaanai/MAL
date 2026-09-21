# EXP-003 — Post-create marks onto sealed observe

Scout lock. **No live capital**, **no JEV**, **no sealed-row rewrite**, **no bonk/mayhem reclassification**. Marks are enrich / side JSONL so [EXP-002](EXP-002-evaluate-runner-v0.md) Δ_exec / lift can leave **INCOMPLETE**.

| Field | Value |
| --- | --- |
| **ID** | `EXP-003` |
| **Status** | In progress (coverage CLI + **RPC backfill producer** `tools.exp003_rpc_backfill`; trade WS producer v0.1 still local). |
| **Owner seat** | Scout (sourcing + wiring). Proof re-runs EXP-002 once coverage is READY. |
| **Locked** | 2026-09-21 (method + honesty). |
| **Depends on** | Sealed JSONL from [observe](../observe/). Law: [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md), matrix enrich-forward. Brief: [POST-CREATE-MARKS-BRIEF.md](../ARTIFACTS/POST-CREATE-MARKS-BRIEF.md). Prior: [EXP-001](EXP-001-regime-stage-mislabel.md) PASS; [EXP-002](EXP-002-evaluate-runner-v0.md) tooling ready, scoring N/A-heavy. |
| **Hypothesis** | Honest post-create price ticks at **1s / 5s / 15s / 30s / 60s** can be attached to sealed bonding creates **without** mutating `ingest_hot` / `regime_id` / `knowable_at_t`, at enough non-N/A coverage that EXP-002 kill gates are **evaluable** (not vacuous INCOMPLETE from missing marks). |
| **Method** | v0: RPC historical ticks from `bondingCurveKey`/`mint` on **existing** local JSONL → `type=outcome_mark` side file. v0.1: PumpPortal `subscribeTokenTrade` TTL (forward). Join = last tick with `T < t_mark ≤ T+H`. CLI coverage: `python -m tools.exp003_marks`. |
| **As-of-T** | Evaluate/decision clock stays the **sealed create** (`T = t_ws` in EXP-002). Marks are **outcomes after T**, never decode inputs. No Dexscreener/Birdeye on this path. No lookahead (`t_mark > T+H` discarded). |
| **Regime labels** | Unchanged parent `regime_id`. Marks do not stamp or revise stage/quote/fee. |
| **Windows** | **1s / 5s / 15s / 30s / 60s** (primary coverage = **60s**, same as EXP-002 kill). Bonus: +2s / +10s / +5m and peak/DD when tick density allows. |
| **Kill-attempt** | See §7. |
| **Result** | Pending local producer + coverage CLI on Vaan JSONL. |
| **Conclusion** | Pending. Does not authorize live capital or EXP-002 PASS. |

---

## 1. Scope lock

| Item | Lock |
| --- | --- |
| **Name** | EXP-003 — post-create marks onto sealed observe |
| **Unit** | Side `outcome_mark` rows joined to sealed `ingest_hot` creates |
| **Population** | Same bonding creates as EXP-002 (`type=ingest_hot`, `txType=create`, `stage=bonding`, valid `t_ws`) |
| **Producer v0** | RPC historical (backfill). CLI: `python -m tools.exp003_rpc_backfill` (Vaan PC; public `SOLANA_RPC_URL`). |
| **Producer v0.1** | PumpPortal trade WS TTL. Metered; local API key only. |
| **Composer / Fast** | Fast **OFF** |

---

## 2. Population (creates)

Same as EXP-002 §2. Source: `data/observe/observe-YYYY-MM-DD.jsonl` (gitignored). Known local files: `observe-2026-09-20.jsonl`, `observe-2026-09-21.jsonl`.

If JSONL is absent, do not substitute cloud data, live WS, or Dexscreener.

---

## 3. Mark population (ticks)

Each mark is a **new** JSONL object ([schema](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md) `observe_mark_v0`).

| Rule | Detail |
| --- | --- |
| Include | Positive `price_proxy` (or `marketCapSol` / `vSolInBondingCurve`); parseable `t_mark`; non-UNK `mint`; `parent_signature` = create signature |
| Source | `rpc_tx` \| `rpc_account_poll` \| `pumpportal_ws_trade` |
| Exclude | Dexscreener/Birdeye fields; ticks with `t_mark ≤ T` (entry) or `t_mark > T+H` when rolling up horizon *H*; failed txs (`err != null`) unless noted |
| Void a mark row | Missing join keys, unparseable `t_mark`, non-finite `price_proxy ≤ 0` — drop from series, count in coverage `marks_void_n` |

**Horizon N/A (create stays in the detect book):** no tick in `(T, T+H]`. Dead coins are honest N/A, not 0%.

---

## 4. As-of-H join (no future leak)

```text
usable(H) = { ticks | T < t_mark ≤ T + H }
mark(H)   = last(usable(H)) by (t_mark, source_line)
else N/A
```

`T` is the parent create `t_ws` (EXP-002). Do **not** use first tick **after** `T+H`. Do **not** fill horizons from the create row at `T`.

Shared helper: `tools/marks.py` (`select_last_as_of`). EXP-002 `--marks` and mixed `outcome_mark` lines use the same rule.

---

## 5. Where marks land (wiring)

| Path | Role |
| --- | --- |
| `data/observe/observe-*.jsonl` | **Unchanged** sealed detect book |
| `data/observe/marks-YYYY-MM-DD.jsonl` | Append-only ticks (gitignored, same glob) |
| `data/observe/_exp003_coverage.json` | Coverage summary (gitignored `_exp003_*`) |

**Do not** append marks into the observe file as `ingest_hot`. Optional: `outcome_mark` lines in a **separate** file passed to EXP-002 `--marks`.

### Field names (`type=outcome_mark`)

| Field | Required | Notes |
| --- | --- | --- |
| `schema_version` | yes | `observe_mark_v0` |
| `type` | yes | `outcome_mark` |
| `mint` | yes | Join |
| `parent_signature` | yes | Create signature |
| `t_mark` | yes | ISO-8601 UTC |
| `source` | yes | `rpc_tx` \| `rpc_account_poll` \| `pumpportal_ws_trade` |
| `price_proxy` | yes* | Finite `>0`; *or* derive from `marketCapSol` / `vSolInBondingCurve` |
| `t_decision` | no | Copy of parent `t_ws` |
| `signature` | no | Trade / tx signature |
| `commitment` | no | `confirmed` typical for RPC; `processed` for WS |
| `price_field` | no | Which vendor field fed `price_proxy` |
| `txType` | no | `buy` / `sell` when known |
| `solAmount`, `tokenAmount`, `vSolInBondingCurve`, `vTokensInBondingCurve`, `marketCapSol` | no | For later paper `Δ_exec` / path stats |

### How EXP-002 reads them

```text
python -m tools.exp002_paper_runner observe-….jsonl --marks marks-….jsonl
```

Runner merges `outcome_mark` ticks into the mint price series (`t_mark` as time) and applies last-at-or-before. Evaluate still uses **only** the sealed create at `T` ([DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)).

---

## 6. Success

| Criterion | Target |
| --- | --- |
| Honesty | Coverage + EXP-002 use last-at-or-before; zero ticks with `t_mark > T+H` in a horizon cell |
| Sealed rows | Parent `ingest_hot` bytes unchanged (new files / new mark lines only) |
| Evaluable EXP-002 | Coverage `READY`: ≥ **20** bonding creates with a **60s** ok mark (enough for EXP-002 `MIN_PRICED_FOR_KILL=10` per arm if labels split). Stretch: 1s/5s/15s/30s also ≥20 ok |
| Full-book intent | After subsample proves the method, extend toward **all** bonding creates (DEC-007) — subsample READY unblocks a **scored** EXP-002 run, not promotion |

`Δ_exec` **not** a success gate here. Marks make **gross** lift computable; cost model is a follow-on on the paper book (`fee_assumed`, never a silent sealed-row fee upgrade).

---

## 7. Kill-attempt

| ID | Condition | Meaning |
| --- | --- | --- |
| K1 | Method requires rewriting `ingest_hot` / `regime_id` / `knowable_at_t` | Kill this EXP shape; fix-forward only |
| K2 | After a documented local RPC subsample (N≥200 creates or full book if smaller) **60s ok_n < 20** for honest reasons other than “producer not run” | Marks path does not unblock EXP-002; iterate source (not Dexscreener-on-spine) |
| K3 | Default join is at-or-after (`t_mark > T+H` used) | Honesty kill |
| K4 | Spine ticks sourced from Dexscreener/Birdeye | Constitution / DEC-003 kill |

Coverage CLI `overall=INCOMPLETE` when **no marks file** is **not** a kill (expected on cloud / before producer).

---

## 8. Limitations

- `blockTime` resolution ~1s → 1s horizon often N/A even when 5s is ok.
- Public RPC 429s; full detect-book backfill may need Helius **free** (measure first).
- Trade WS does not backfill Sep 20–21; needs key + 0.02 SOL (local).
- Zero-trade mints → N/A forever (honest).
- `Δ_exec` still N/A until Proof’s fee/slippage assumption.
- Peak/drawdown need ticks inside +5m; v0 subsample may skip +5m to save RPC.

---

## 9. Out of scope (explicit)

- Live capital / PumpPortal **trading** API
- JEV / LLM evaluate
- Bonk-regime / mayhem reclassification (inventory stays PARKED)
- Rewriting sealed observe rows or backfilling `regime_id`
- Dexscreener or Birdeye as mark `source`
- Observe-client subscribe changes in **this** PR (v0.1 sketch only)
- PumpPortal trade WS TTL producer (v0.1) — separate follow-on

---

## 10. Local runbook

**Tests (no network):**

```bash
python3 -m unittest tools.test_marks tools.test_exp003_marks tools.test_exp003_rpc_backfill
```

**Producer (v0 RPC backfill, Vaan PC):** subsample bonding creates, paginate `getSignaturesForAddress` on `bondingCurveKey` (else `mint`) until `blockTime < T`, `getTransaction` with `maxSupportedTransactionVersion: 1` (`confirmed`/`finalized`), append `outcome_mark` to `data/observe/marks-YYYY-MM-DD.jsonl`. Public RPC via `SOLANA_RPC_URL` (default mainnet-beta public URL). Subsample default **300** (`--seed` reproducible). Progress logs use ASCII only.

**Price decode (v0.1 fix):** Mainnet `getTransaction` JSON does **not** include PumpPortal WS fields (`marketCapSol`, `vSolInBondingCurve`) on the tx object — walking the response always yielded `no_price`. The producer decodes pump.fun **Anchor CPI events** from `meta.logMessages` (`Program data:` base64): `TradeEvent` / `CreateEvent` per [pump IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json). `price_proxy` prefers **`marketCapSol`** when `CreateEvent.token_total_supply` is present (`virtual_sol_reserves * token_total_supply / virtual_token_reserves`, lamports → SOL); else **`vSolInBondingCurve`** = `virtual_sol_reserves / 1e9` (same proxies as EXP-002). Undecodable txs increment `no_price` — never fabricated.

```powershell
$env:SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
python -m tools.exp003_rpc_backfill `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --output-dir data\observe `
  --sample 300 `
  --seed 1 `
  --window-s 60 `
  --commitment confirmed
```

**Coverage (stdlib; no network):**

```powershell
python -m tools.exp003_marks `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --marks data\observe\marks-2026-09-20.jsonl `
          data\observe\marks-2026-09-21.jsonl `
  --output-dir data\observe --prefix _exp003
```

**Then Proof (EXP-002):**

```powershell
python -m tools.exp002_paper_runner `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --marks data\observe\marks-2026-09-20.jsonl `
          data\observe\marks-2026-09-21.jsonl `
  --rules v1
```

**Exit codes:** coverage CLI `0` READY / `3` INCOMPLETE / `1` missing JSONL; RPC producer `0` ok / `1` bad args / `2` RPC failure.

---

## 11. Result / conclusion

| Field | Value |
| --- | --- |
| **Result** | _Pending — producer + coverage on Vaan JSONL._ |
| **Conclusion** | _Pending. Does not close EXP-002 or start exec._ |
