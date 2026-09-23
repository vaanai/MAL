# EXP-007c — Fee dimension knowable-at-T (Discovery stamp)

> **Status:** Helm-authorized **2026-09-23** follow-on to [EXP-007b](EXP-007b-platform-regime-rpc-enrich-v0.md). Diagnose **why fee stayed 100% `unverified`** on the **reused** capped RPC enrich sample; add minimal **Global + bonding-curve fee** decode; re-stamp **K-fee-knowable-at-t** on enriched overlay only. **Merge ≠ authorize** continuous observe-wiring.

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

## Result / conclusion

**Claim (fee knowable-at-T on reused 007b sample):** _Pending Oracle restamp in PR branch — code path lands; stamp table updated after `mal-core-vnic` run._

| Courier day | rows | fee `global_100bps` | fee `creator_dynamic` | fee `unverified` | **K-fee-knowable-at-t** |
| --- | ---: | ---: | ---: | ---: | --- |
| **2026-09-20** | 500 | TBD | TBD | TBD | TBD |
| **2026-09-21** | 500 | TBD | TBD | TBD | TBD |

**Sealed full-book fee:** **INCOMPLETE** (100% `unverified` WS default) — unchanged.

**Soft locks:** merge ≠ continuous observe-wiring; no Discovery promote; no invented fee bps; EXP-007b instr/quote gaps unchanged.

---

## Cross-links

- [EXP-007](EXP-007-platform-regime-taxonomy-v0.md) · [EXP-007b](EXP-007b-platform-regime-rpc-enrich-v0.md)
- [LAB_STATE.md](../LAB_STATE.md) · [EXP/README.md](README.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
