# EXP-007b — Platform-regime RPC enrich + audit re-score

> **Status:** Helm-authorized **2026-09-23** follow-on to [EXP-007](EXP-007-platform-regime-taxonomy-v0.md) sealed audit (**INCOMPLETE** on **K-platform-rpc-resolved**). Thin **knowable-at-T** RPC enrich on capped courier creates, then **re-run** [`tools/exp007_regime_audit`](../tools/exp007_regime_audit.py) with enriched overlay. **Merge ≠ authorize** continuous observe-wiring or encoder production promote.

| Field | Value |
| --- | --- |
| **ID** | `EXP-007b-platform-regime-rpc-enrich-v0` |
| **Owner seat** | **Scout** (enrich rows + platform labels); **Proof** (gates on stratify audit) |
| **Depends on** | Day-aligned sealed courier `observe-2026-09-{20,21}.jsonl` only — **no cross-day join**; parent EXP-007 structural gates |
| **CLI** | [`tools/exp007_rpc_enrich.py`](../tools/exp007_rpc_enrich.py), [`tools/exp007_regime_audit.py`](../tools/exp007_regime_audit.py) (`--enrich-jsonl`) |
| **Oracle runbook** | [`tools/exp007b_oracle_run.md`](../tools/exp007b_oracle_run.md) |

---

## Hard caps (code + docs)

| Cap | Enforcement |
| --- | --- |
| **≤500 creates/day** | `MAX_SAMPLE_PER_DAY` + `cap_sample_n()` in enrich CLI |
| **Marks cohort intersection** | Optional `--marks` shrinks pool before cap |
| **RPC rate limits** | `SolanaRpcClient` throttle + backoff (shared with EXP-003) |
| **Knowable-at-T** | Create `blockTime` must be ≤ `t_ws` + slack; bonding-curve read uses `minContextSlot` from create tx |
| **Paper-only** | Append-only `type=regime_enrich` under `/var/lib/mal/paper/` or `data/observe/_exp007b_*` (gitignored) |
| **No sealed rewrite** | Never mutate `ingest_hot` JSONL |

**Out of scope:** continuous observe-wiring; densify marks; EXP-002c retune; Graph ordinal / NH-G3a / NH-Index / H-G2 thaw; X ingest; Discovery promote from soft watches.

---

## Method

1. Subsample eligible bonding creates (same spine as EXP-007).
2. For each sample row at decision **T** (`t_ws`): `getTransaction(create_sig)` → `instr`; `getAccountInfo(bonding_curve, minContextSlot=create_slot)` → `quote` / graduation `complete` flag.
3. Append `regime_enrich_v0` JSONL (`regime_id_enriched`, `knowable_at_t_enriched`, `rpc_meta`).
4. Re-run regime audit with `--enrich-jsonl` — reports **sealed book** (unchanged) plus **enriched sample** gates.

### Gates (enriched sample)

| ID | Role |
| --- | --- |
| **K-blank** / **K-knowable-at-t** / **K-id-kat-consistency** | Same structural law on overlay rows |
| **K-platform-rpc-resolved** | **PASS** when enriched sample has ≤5% `instr` pending/unknown and ≥90% `quote_verified` (fee may remain `unverified` — no invented bps) |
| **K-leak-enriched** | **FAIL** if any overlay row used RPC after **T** |

**Sealed full-book K-platform-rpc-resolved** remains **INCOMPLETE** (expected) — honest dual read.

---

## Oracle sealed measure

| Step | What |
| --- | --- |
| Host | `mal-core-vnic` ([DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)) |
| Inputs | `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` |
| Artifacts | `/var/lib/mal/paper/_exp007b-oracle-2026-09-{20,21}_*` (gitignored on host) |

**Stamp:** Oracle **`mal-core-vnic`** **2026-09-23** — host `/var/lib/mal/paper/_exp007b-oracle-*` (gitignored).

---

## Result / conclusion (Oracle sealed 2026-09-23)

**Host:** `mal-core-vnic` — enrich + audit via [`tools/exp007b_oracle_run.md`](../tools/exp007b_oracle_run.md). Public mainnet RPC (`SOLANA_RPC_URL`); throttle/backoff under 429 load.

| Courier day | enrich_n (cap 500) | leak_reject_n | sealed overall | enriched sample overall | K-platform-rpc-resolved (enriched) |
| --- | ---: | ---: | --- | --- | --- |
| **2026-09-20** | **500** | **0** | **INCOMPLETE** | **INCOMPLETE** | **INCOMPLETE** (instr pending **12.4%**; quote_verified **76.4%**) |
| **2026-09-21** | **500** | **0** | **INCOMPLETE** | **INCOMPLETE** | **INCOMPLETE** (instr pending **16.0%**; quote_verified **70.0%**) |

**Cross-day sealed overall:** **INCOMPLETE** (unchanged — full book WS defaults).  
**Cross-day enriched overall:** **INCOMPLETE** — RPC overlay improves instr/quote; fee was **100%** `unverified` pre-007c (no Global fetch) — see [EXP-007c](EXP-007c-fee-knowable-at-t-v0.md).

| Gate (enriched sample) | 2026-09-20 | 2026-09-21 |
| --- | --- | --- |
| **K-blank** / **K-knowable-at-t** / **K-id-kat-consistency** | PASS | PASS |
| **K-leak-enriched** | PASS | PASS |
| **K-platform-rpc-resolved** | INCOMPLETE | INCOMPLETE |

**Conclusion:** EXP-007b lands **honest, knowable-at-T** RPC enrich on a **500/day capped** subsample (`regime_enrich` append-only) and re-scores the stratify audit. **Wiring readiness** on platform keys remains **INCOMPLETE** on both full book and enriched sample — no fabricated PASS. **Merge ≠ authorize** continuous observe-wiring or encoder production promote. **No Discovery promote.**

---

## Cross-links

- Parent audit: [EXP-007](EXP-007-platform-regime-taxonomy-v0.md)
- Enum: [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md)
- Fee stamp follow-on: [EXP-007c](EXP-007c-fee-knowable-at-t-v0.md)
- [LAB_STATE.md](../LAB_STATE.md) · [EXP/README.md](README.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
