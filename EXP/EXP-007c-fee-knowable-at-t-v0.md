# EXP-007c — Fee dimension knowable-at-T (Discovery stamp)

> **Status:** **INCOMPLETE (scored)** — Oracle **`mal-core-vnic`** **2026-09-23** fee restamp on reused EXP-007b sample.

| Field | Value |
| --- | --- |
| **ID** | `EXP-007c-fee-knowable-at-t-v0` |
| **Owner seat** | **Scout** (fee tag law); **Proof** (gate on enriched sample) |
| **Depends on** | Same EXP-007b **500/day** `regime_enrich` on courier **2026-09-20/21** — **no new densify / no resample** |
| **CLI** | [`tools/exp007c_fee_at_t.py`](../tools/exp007c_fee_at_t.py) (fee restamp), [`tools/exp007_regime_audit.py`](../tools/exp007_regime_audit.py) (`K-fee-knowable-at-t`), enrich path in [`tools/exp007_rpc_enrich.py`](../tools/exp007_rpc_enrich.py) |
| **Oracle runbook** | [`tools/exp007c_oracle_run.md`](../tools/exp007c_oracle_run.md) |

---

## Diagnosis (EXP-007b root cause)

| Layer | Finding |
| --- | --- |
| **Regime law** | [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) §2 — `fee=global_100bps` requires **RPC** read of Pump **`Global`** (`fee_basis_points`) or per-coin **`creator_dynamic`** via bonding-curve extension / holder-reward flags. |
| **EXP-007b enricher** | `resolve_platform_at_t` fetched create **tx logs** + **bonding curve** for `instr` / `quote` but set `fee = "unverified"` **without any Global or fee-extension fetch** (hard-coded placeholder). |
| **Audit gate** | **K-platform-rpc-resolved** (EXP-007b) intentionally allows fee unverified while instr/quote improve — fee was **out of scope** for that gate’s PASS branch. |
| **Not the cause** | Wrong program IDL for instr/quote, leak-at-T, or sealed-book rewrite — **leak_reject_n=0** on Oracle sample; instr/quote partially resolved. |

**Knowable-at-T fee path (minimal):**

1. `getAccountInfo(Global PDA, minContextSlot=create_slot)` → `fee_basis_points == 100` → `fee=global_100bps` for standard SOL/USDC pairs.
2. Bonding-curve account extension → `is_holder_reward` / `creator_fee_bps > 0` → `fee=creator_dynamic`.
3. Else remain `fee=unverified` (never fabricate bps).

---

## Hard caps (unchanged from EXP-007b)

| Cap | Enforcement |
| --- | --- |
| **Reuse 007b sample** | Fee restamp reads existing `_exp007b-oracle-*_regime_enrich.jsonl` parent set only |
| **≤500 rows/day** | No new subsample |
| **Knowable-at-T** | Global + curve reads use create `minContextSlot`; tx `blockTime` ≤ `t_ws` + slack |
| **Paper-only** | Side JSONL under `/var/lib/mal/paper/` — gitignored |
| **Sealed full book** | **K-platform-rpc-resolved** / fee on book remain **INCOMPLETE** (dual read honest) |

**Out of scope:** densify; continuous observe-wiring; EXP-002c retune; X / Graph; live keys.

---

## Gates (enriched sample)

| ID | Role |
| --- | --- |
| **K-fee-knowable-at-t** | **PASS** when enriched overlay has **≤5%** `fee=unverified`; else **INCOMPLETE** |
| **K-platform-rpc-resolved** | Unchanged EXP-007b composite (instr + quote + fee mix) |
| **K-leak-enriched** | Unchanged |

---

## Oracle sealed measure

| Step | What |
| --- | --- |
| Host | `mal-core-vnic` ([DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)) |
| Inputs | Existing `/var/lib/mal/paper/_exp007b-oracle-2026-09-{20,21}_regime_enrich.jsonl` + sealed observe |
| Artifacts | `/var/lib/mal/paper/_exp007c-oracle-*` (gitignored) |

**Stamp:** See **Result** below — filled from Oracle run when host artifacts exist.

---

## Result / conclusion (Oracle sealed 2026-09-23)

**Host:** `mal-core-vnic` — fee restamp on reused `_exp007b-oracle-*` sample via [`tools/exp007c_oracle_run.md`](../tools/exp007c_oracle_run.md). Public mainnet RPC; throttle under load.

**Claim (fee knowable-at-T on reused 007b sample):** **INCOMPLETE** — RPC fee **decode lands** in `rpc_meta.fee_resolve`, but v0 **`regime_id.fee` enum** stays mostly **`unverified`** (honest; no invented bps).

| Courier day | rows | `global_100bps` | `creator_dynamic` | `unverified` | residual unverified % | **K-fee-knowable-at-t** |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| **2026-09-20** | **500** | **0** | **72** (14.4%) | **428** | **85.6%** | **INCOMPLETE** |
| **2026-09-21** | **500** | **0** | **53** (10.6%) | **447** | **89.4%** | **INCOMPLETE** |

**What blocked enum PASS (not missing RPC wiring):**

| Blocker | Evidence |
| --- | --- |
| **EXP-007b never fetched Global** | Pre-007c enrich hard-coded `fee=unverified` — fixed in enrich + restamp path |
| **Global bps ≠ enum `global_100bps`** | At create `minContextSlot`, Pump `Global.fee_basis_points == **95**` — **428/500** (20) and **447/500** (21) rows stay `unverified` with `global.fee_basis_points_nonstandard_95` |
| **Holder-reward slice** | **72** / **53** rows per day → `fee=creator_dynamic` (`bonding_curve.is_holder_reward`) |
| **Sealed full book** | Unchanged **100%** `fee=unverified` — dual read honest |

**Cross-day enriched overall:** **INCOMPLETE** (`K-fee-knowable-at-t` + **K-platform-rpc-resolved** unchanged from EXP-007b instr/quote mix).

**Conclusion:** Fee dimension is **partially knowable-at-T** on the same capped sample (holder-reward + decoded Global bps in side metadata), but **v0 fee enum PASS** remains **INCOMPLETE** until REGIME-ENUM admits observed Global bps or on-chain returns 100 bps — **do not fabricate `global_100bps`**. **Merge ≠ authorize** continuous observe-wiring. **No Discovery promote.**

---

## Cross-links

- [EXP-007](EXP-007-platform-regime-taxonomy-v0.md) · [EXP-007b](EXP-007b-platform-regime-rpc-enrich-v0.md)
- [LAB_STATE.md](../LAB_STATE.md) · [EXP/README.md](README.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
