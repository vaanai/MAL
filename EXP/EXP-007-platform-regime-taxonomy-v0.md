# EXP-007 — Platform-regime taxonomy v0 (bonding-curve version / fees / graduation / pair asset)

> **Status: INCOMPLETE (scored audit stamp)** — Oracle sealed **coverage / knowable-at-T** audit **2026-09-23** (Helm **authorized run 2026-09-23**). Scout soft PASS pending; **Proof GATE** on **wiring** / encoder land remains separate. **Merge of this PR ≠ authorize continuous observe-wiring** or RPC enrich promotion.

| Field | Value |
| --- | --- |
| **ID** | `EXP-007-platform-regime-taxonomy-v0` |
| **Owner seat** | **Scout** (platform-regime label ownership); **Proof** (enforces stratify / gate on measures) |
| **Source** | [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) §1–§5; [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), [DEC-004](../DEC/DEC-004-regime-id-encoding.md), [REGIME-AT-INGEST-MATRIX.md](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md) |
| **Status** | **INCOMPLETE (scored)** — taxonomy **registration** + sealed **stratify-only** audit stamped; RPC-resolved platform slice **not** on book |
| **Paper-only** | Yes — sealed read-only audit; no live keys, no evaluate promotion |
| **Pick lock** | **Helm 2026-09-23** — sealed observe audit on courier **2026-09-20/21**. Parent EXP-005 / EXP-005b / EXP-004 stamps unchanged. **No EXP-002c retune**; **no densify**; **no Graph lane thaw**. |
| **Depends on** | Day-aligned sealed courier: `observe-2026-09-{20,21}.jsonl` only — **no cross-day join**; **no marks** required for this audit |
| **CLI** | [`tools/exp007_regime_audit.py`](../tools/exp007_regime_audit.py) — `python -m tools.exp007_regime_audit` |

---

## 0. Honesty — what Discovery already stamped (soft watches ≠ promote)

| Stamp | Status | Read for EXP-007 |
| --- | --- | --- |
| [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) **L3_PACKET_V0** | **DIRECTIONAL_WATCH** | Soft watch — S1 `regime_gate_key` law unchanged |
| [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) **L3_minus_v2** | **DIRECTIONAL_WATCH (residual)** | Soft watch — no residual promote |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) | **INCOMPLETE closed** | Cohort reference — **no retune** |
| [EXP-001](EXP-001-regime-stage-mislabel.md) | **PASS closed** (tooling) | Stage/WS mislabel sample — **not** this platform coverage audit |

**This audit does not:** restate Graph arms; revive ordinal / **NH-G3a** / **NH-Index** / **H-G2**; implement fill-sim; add X ingest; authorize Discovery decode promotion; or claim soft watches cleared evaluate failure.

**Wiring still Proposed:** Label **encoder** / RPC enrich append path / continuous observe promotion — **not** authorized by this audit merge alone.

---

## 1. Platform-regime dimensions v0 (first-class labels)

Canonical enum: [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md). Audit checks **presence + knowable-at-T flags** on sealed rows (not independent RPC truth).

| Dimension | `regime_id` keys (v0) | `knowable_at_t` |
| --- | --- | --- |
| **Bonding-curve version / lineage** | `venue`, `instr` | `venue`, `instr` (`pending_rpc` allowed) |
| **Fee schedule** | `fee` | `fee` (`unverified` allowed) |
| **Graduation behavior** | `stage` (row + regime_id), `market` | stage from row; market in regime_id |
| **Pair / quote asset** | `quote` | `quote`, `quote_verified` |

**`regime_gate_key`:** EXP-004 policy — full `regime_id` string. Both courier days: **`regime_gate_keys_n = 1`** (single homogeneous WS-default platform slice).

---

## 2. Hypothesis (coverage + attach law)

| Field | Content |
| --- | --- |
| **Claim** | Every bonding `ingest_hot` create on the courier spine carries **non-blank** platform-regime tokens with matching `knowable_at_t`, enabling mandatory stratification on future measures. |
| **This run** | **Structural** coverage + consistency only — **no** EXP-001-style mislabel reconstruction, **no** invented fee bps. |

---

## 3. Kill / falsifiers (gates — this audit)

| ID | Result (cross-day) |
| --- | --- |
| **K-blank** | **PASS** — no empty `instr` / `fee` / `quote` without explicit unverified tokens |
| **K-knowable-at-t** | **PASS** — `knowable_at_t` present on 100% eligible creates |
| **K-id-kat-consistency** | **PASS** — `regime_id` aligns with `knowable_at_t` for quote/instr/fee/venue |
| **K-platform-rpc-resolved** | **INCOMPLETE** — 100% `instr=pending_rpc`, `fee=unverified`, `quote=wsol_assumed`; `quote_verified=false` |
| **K-platform-mislabel** | **N/A** — not sampled (EXP-001 tooling separate) |
| **K-leak** | **N/A** — no RPC timestamp audit in stratify report |
| **K-measure-skip** | **N/A** — `regime_gate_keys_n=1` both days (S1 collapse falsifier not applicable) |

**Overall stamp:** **INCOMPLETE** — platform dimensions **stamped** on book; **RPC-resolved** lineage/fees/quote **not** landed. Honest wiring readiness gap, not a taxonomy registration failure.

---

## 4. Sealed measure (executed 2026-09-23)

| Step | What |
| --- | --- |
| 1 | Oracle `mal-core-vnic` — `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` |
| 2 | Population: bonding `ingest_hot` creates (`is_bonding_create` — same as EXP-004/005 spine) |
| 3 | CLI + gitignored artifacts `/var/lib/mal/paper/_exp007-oracle-2026-09-20_21/_exp007-oracle-*` |
| 4 | Stratify report only — coverage rates, value counts, day splits |

**Authorization:** Helm **2026-09-23** sealed observe audit (this PR). **Does not** authorize continuous observe-wiring, encoder production promote, or X / Layer-4 ingest.

---

## 5. Soft locks (held)

Paper-only; no densify; no EXP-002c retune; no Discovery promote from soft watches; ordinal / **NH-G3a** / **NH-Index** / **H-G2 revive** **parked**; Graph / Scout seat ownership unchanged; **merge ≠ authorize-run** for **wiring** / continuous observe.

---

## 6. Cross-links

- Enum: [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md)
- Schema: [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
- Oracle access: [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)
- [LAB_STATE.md](../LAB_STATE.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md) · [EXP/README.md](README.md)

---

## 7. Result / conclusion (Oracle sealed audit 2026-09-23)

**Host:** `mal-core-vnic` — inputs `/var/lib/mal/sealed/jsonl/`; artifacts `/var/lib/mal/paper/_exp007-oracle-2026-09-20_21/` (summary JSON, report MD, row audit JSONL). **Repo CLI:** `python -m tools.exp007_regime_audit`.

**Cross-day overall:** **INCOMPLETE** (CLI exit **1** — expected for INCOMPLETE stamp).

### Day splits (eligible bonding creates)

| Courier day | eligible_n | regime_id parse | knowable_at_t present | regime_gate_keys_n | overall |
| --- | ---: | ---: | ---: | ---: | --- |
| **2026-09-20** | **14795** | 100% | 100% | **1** | **INCOMPLETE** |
| **2026-09-21** | **18101** | 100% | 100% | **1** | **INCOMPLETE** |

### Platform value counts (both days — homogeneous)

| Field | Stamped value | Share |
| --- | --- | ---: |
| `regime_id.instr` / `knowable_at_t.instr` | `pending_rpc` | 100% |
| `regime_id.fee` / `knowable_at_t.fee` | `unverified` | 100% |
| `regime_id.quote` / `knowable_at_t.quote` | `wsol_assumed` | 100% |
| `regime_id.market` | `bonding_curve` | 100% |
| `regime_id.venue` | `pump_program` | 100% |
| `row.stage` | `bonding` | 100% |
| `knowable_at_t.quote_verified` | `false` | 100% |

### Gates (per day — identical)

| Gate | 2026-09-20 | 2026-09-21 |
| --- | --- | --- |
| **K-blank** | PASS | PASS |
| **K-knowable-at-t** | PASS | PASS |
| **K-id-kat-consistency** | PASS | PASS |
| **K-platform-rpc-resolved** | INCOMPLETE | INCOMPLETE |

### Limitations (honest)

- **WS-only defaults** on entire courier book — no `regime_enrich` RPC refinement in sealed spine for these days.
- **`trade_iface`** not present in `observe_hot_v0` `regime_id` — bonding lineage audited as **`venue` + `instr`** only.
- **Stratify-only** — does not prove on-chain fee config or instruction discriminator correctness (EXP-001 / future RPC audit).
- **Single `regime_gate_key`** — EXP-004 / EXP-005 S1 collapse falsifier **not triggered**; multi-regime measures still must stratify when `regime_gate_keys_n > 1` on other days.

**Conclusion:** Platform-regime taxonomy **v0 is present and consistent** on sealed creates for **2026-09-20/21**, with explicit unverified tokens. **RPC-resolved** platform labeling remains **INCOMPLETE** — encoder / enrich wiring stays **Proposed** and needs **separate Helm re-auth**. **No Discovery promote.** Scout soft PASS pending; **Proof GATE** before promotion wording on **wiring**.
