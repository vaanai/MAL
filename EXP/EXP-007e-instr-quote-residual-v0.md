# EXP-007e — instr / quote residual diagnosis + knowable-at-T restamp

> **Status:** Oracle sealed measure **`mal-core-vnic`** **2026-09-23** on reused EXP-007b/c/d **≤500/day** sample (courier **2026-09-20/21**). **K-platform-rpc-resolved PASS** on enriched overlay after decode fixes; sealed full book **INCOMPLETE** (dual read). **Merge ≠ observe-wiring ≠ enum production lock.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-007e-instr-quote-residual-v0` |
| **Owner seat** | **Scout** (instr/quote decode); **Proof** (K-platform-rpc-resolved on enriched sample) |
| **Depends on** | `_exp007d-oracle-*` enrich + sealed observe **2026-09-20/21** — **no resample / no densify** |
| **CLI** | [`tools/exp007e_instr_quote_rescore.py`](../tools/exp007e_instr_quote_rescore.py), [`tools/exp007e_residual_diagnose.py`](../tools/exp007e_residual_diagnose.py), [`tools/exp007_instr_quote_resolve.py`](../tools/exp007_instr_quote_resolve.py), [`tools/exp007_regime_audit.py`](../tools/exp007_regime_audit.py) |
| **Oracle runbook** | [`tools/exp007e_oracle_run.md`](../tools/exp007e_oracle_run.md) |

---

## Hypothesis

The **instr / quote** residual blocking **K-platform-rpc-resolved** on the capped enrich sample is **diagnosable** at decision **T** without new subsampling, and gaps where a decode path already existed but was **wrong or unused** can be closed by honest RPC re-decode on the **same** parent signatures.

**Not claimed:** continuous observe-wiring, encoder promote, or production enum lock for `global_95bps` / `launchlab_init`.

---

## Diagnosis (EXP-007d baseline on enriched sample)

| Residual bucket | Mechanism (host evidence) | Share (indicative, day 20) |
| --- | --- | --- |
| **`instr_log_gap` → LaunchLab** | PumpPortal stream includes **Raydium LaunchLab** creates (`LanMV9sAd…`); logs use `InitializeWithToken2022`, not `Create`/`CreateV2` | **62/500** (12.4%) |
| **`quote_mint_invalid_bytes`** | Bonding-curve `quote_mint` read at **wrong offset** (81 vs **83**) → system-program pubkey counted as verified `quote=other` | **~310/500** false “verified” |
| **`quote_mint_decode_gap`** | WS `bondingCurveKey` sometimes **bogus PDA** (`BwWK17…`); curve read fails though **create_v2** lineage is knowable from tx logs | **55/500** |
| **Fee** | Already **PASS** under Proposed `global_95bps` (EXP-007d) — not re-litigated here | **0%** `unverified` |

**Residual taxonomy** (side metadata `residual_taxonomy_v0`, not regime_id): `launchlab_resolved`, `resolved_ok`, `quote_mint_decode_gap`, `instr_log_gap`, `rpc_tx_miss`, etc.

---

## Method (knowable-at-T)

1. Load **EXP-007d** `regime_enrich` rows (fee labels preserved).
2. Re-run [`resolve_platform_at_t`](../tools/exp007_rpc_enrich.py) on matching sealed observe parents (`create` `blockTime` / `minContextSlot` law unchanged).
3. Fixes landed in shared helpers [`tools/exp007_instr_quote_resolve.py`](../tools/exp007_instr_quote_resolve.py):
   - `quote_mint` at offset **83** + reject invalid mints
   - **LaunchLab** venue + `instr=launchlab_init` + quote from `transferChecked` (exclude launch token by amount)
   - **`create_v2` + pump_program** ⇒ `quote=wsol` **verified** when curve missing/invalid but instr lineage is knowable from tx (incl. bad WS `bondingCurveKey`)
4. Re-score [`tools/exp007_regime_audit`](../tools/exp007_regime_audit.py) with `--enrich-jsonl` (**K-platform-rpc-resolved**, **K-fee-knowable-at-t**, leak gate).

---

## Gates (enriched sample)

| ID | Threshold | Role |
| --- | --- | --- |
| **K-platform-rpc-resolved** | ≤5% `instr` pending/unknown; ≥90% `quote_verified` | Primary composite (fee already satisfied via 007d) |
| **K-fee-knowable-at-t** | ≤5% `fee=unverified` | Regression guard (fee preserved from 007d) |
| **K-leak-enriched** | 0 leak rejects | Unchanged |
| **Sealed full book** | Still **INCOMPLETE** on RPC slice | Dual read honest |

---

## Oracle sealed measure

| Step | What |
| --- | --- |
| Host | `mal-core-vnic` ([DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)) |
| Input | `/var/lib/mal/paper/_exp007d-oracle-2026-09-{20,21}_regime_enrich.jsonl` |
| Artifacts | `/var/lib/mal/paper/_exp007e-oracle-*` (gitignored) |

---

## Result / conclusion (Oracle sealed 2026-09-23)

**Host:** `mal-core-vnic` — [`tools/exp007e_oracle_run.md`](../tools/exp007e_oracle_run.md).

| Courier day | enrich_n | instr pending/unknown % | quote_verified % | **K-platform-rpc-resolved** (enriched) | **K-fee-knowable-at-t** |
| --- | ---: | ---: | ---: | --- | --- |
| **2026-09-20** | **500** | **0%** | **100%** | **PASS** | **PASS** |
| **2026-09-21** | **500** | **0.2%** (1× `unknown_until_rpc`) | **99.8%** | **PASS** | **PASS** |

**Cross-day enriched composite:** **PASS** on **K-platform-rpc-resolved** (instr/quote thresholds met on reused sample).  
**Cross-day sealed full book:** **INCOMPLETE** (unchanged — no enrich overlay on book).

**Residual (day 21):** 1 row remains `instr=unknown_until_rpc` / `quote=wsol_assumed` — logged as `instr_log_gap` in taxonomy (not enough tx evidence at T without new enum).

**Conclusion:** EXP-007e closes the **knowable-at-T** instr/quote gap on the **capped enrich sample** by fixing decode bugs (quote offset, LaunchLab lineage, bogus WS curve key fallback for `create_v2` SOL pairs). **This stamp does not authorize** continuous observe-wiring, encoder production promote, or **Proposed** enum production lock (`global_95bps`, `launchlab_init`). **No Discovery promote.**

---

## Cross-links

- [EXP-007b](EXP-007b-platform-regime-rpc-enrich-v0.md) · [EXP-007d](EXP-007d-fee-global-95bps-enum-v0.md)
- [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md)
- [LAB_STATE.md](../LAB_STATE.md) · [EXP/README.md](README.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
