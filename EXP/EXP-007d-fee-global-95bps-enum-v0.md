# EXP-007d — `global_95bps` enum proposal + fee gate re-score (Discovery stamp)

> **Status:** **Proposed enum extension** — Oracle re-score on reused EXP-007b/c sample (fill **Result** after host run).

| Field | Value |
| --- | --- |
| **ID** | `EXP-007d-fee-global-95bps-enum-v0` |
| **Owner seat** | **Scout** (fee tag law); **Proof** (gate on enriched sample) |
| **Depends on** | Same EXP-007b **500/day** enrich + EXP-007c fee restamp on courier **2026-09-20/21** — **no new densify / no resample** |
| **CLI** | [`tools/exp007d_fee_rescore.py`](../tools/exp007d_fee_rescore.py) (decode-only re-label), [`tools/exp007_regime_audit.py`](../tools/exp007_regime_audit.py) (`K-fee-knowable-at-t`), classify law in [`tools/exp007_fee_resolve.py`](../tools/exp007_fee_resolve.py) |
| **Oracle runbook** | [`tools/exp007d_oracle_run.md`](../tools/exp007d_oracle_run.md) |

---

## Diagnosis (EXP-007c residual)

EXP-007c showed **~86–89%** `fee=unverified` on the enriched sample **not** because Global was unreadable, but because on-chain `Global.fee_basis_points == **95**` while REGIME-ENUM v0 only admitted `global_100bps` at exactly **100** — honest `unverified` + side metadata `global.fee_basis_points_nonstandard_95`.

**EXP-007d proposal:** Add **`fee=global_95bps`** when RPC at create `minContextSlot` confirms **95** bps. **Do not** map 95 → `global_100bps`. **Proposed** label — merge of this stamp **≠** production enum lock or continuous observe-wiring.

---

## Hard caps (unchanged)

| Cap | Enforcement |
| --- | --- |
| **Reuse 007b/c sample** | Decode-only rescore reads `_exp007c-oracle-*` `rpc_meta.fee_resolve` |
| **≤500 rows/day** | No new subsample |
| **Knowable-at-T** | No new RPC; fields already captured at T in 007c restamp |
| **Paper-only** | Side JSONL under `/var/lib/mal/paper/` — gitignored |
| **Sealed full book** | Fee on book stays **100%** `unverified` — dual read honest |

**Out of scope:** densify; continuous observe-wiring; EXP-002c retune; X / Graph; live keys.

---

## Gates (enriched sample)

| ID | Role |
| --- | --- |
| **K-fee-knowable-at-t** | **PASS** when enriched overlay has **≤5%** `fee=unverified`; else **INCOMPLETE** |
| **K-platform-rpc-resolved** | Unchanged EXP-007b composite |
| **K-leak-enriched** | Unchanged |

---

## Oracle sealed measure

| Step | What |
| --- | --- |
| Host | `mal-core-vnic` ([DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)) |
| Inputs | `/var/lib/mal/paper/_exp007c-oracle-2026-09-{20,21}_regime_enrich.jsonl` + sealed observe |
| Artifacts | `/var/lib/mal/paper/_exp007d-oracle-*` (gitignored) |

---

## Result / conclusion (Oracle sealed — update from host)

**Claim (under Proposed `global_95bps` on reused sample):** _Pending Oracle run — expect **K-fee-knowable-at-t PASS** both days if 007c residual was entirely 95 bps + `creator_dynamic` (0% other unverified)._

| Courier day | rows | `global_95bps` | `global_100bps` | `creator_dynamic` | `unverified` | residual unverified % | **K-fee-knowable-at-t** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| **2026-09-20** | **500** | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| **2026-09-21** | **500** | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

**Sealed full book:** unchanged **INCOMPLETE** on fee (100% WS `unverified`).

**What remains blocked after stamp:** continuous observe-wiring; encoder production promote; other non-95/100 Global bps without new enum rows; instr/quote gaps on **K-platform-rpc-resolved**.

**Conclusion:** _Fill after Oracle._ **Merge ≠ authorize** wiring or enum production lock without Helm/Proof.

---

## Cross-links

- [EXP-007](EXP-007-platform-regime-taxonomy-v0.md) · [EXP-007b](EXP-007b-platform-regime-rpc-enrich-v0.md) · [EXP-007c](EXP-007c-fee-knowable-at-t-v0.md)
- [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) §2 (**Proposed** `global_95bps`)
- [LAB_STATE.md](../LAB_STATE.md) · [EXP/README.md](README.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
