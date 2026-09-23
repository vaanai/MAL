# EXP-004b — NH-G1a ordinal prior-mint depth (Graph child measurement)

> **Status: Proposed, not run** — hypothesis registration only. **No CLI execution, no scored result, no lift claim** in this document.

| Field | Value |
| --- | --- |
| **ID** | `EXP-004b-nh-g1a-ordinal-prior-mint-v0` |
| **Parent** | [EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md) (H-G1 binary arm) |
| **Source brief** | [GRAPH-EXP004-NEXT-HYP-V0.md](../ARTIFACTS/GRAPH-EXP004-NEXT-HYP-V0.md) §3 **NH-G1a** |
| **Owner seat** | Graph (measurement); Proof re-stamps when data READY |
| **Pick lock** | **Helm/Vaan 2026-09-23** — register **EXP-004b** for **NH-G1a** first. **NH-G3a** only if G1a stays soft watch / no ordinal kill. **NH-Index** parked. Still **Graph seat**; docs-only until operator run. |
| **Depends on** | Same sealed inputs as EXP-004 §7: day-aligned `observe-2026-09-20.jsonl` + `marks-2026-09-20.jsonl` and `observe-2026-09-21.jsonl` + `marks-2026-09-21.jsonl`; existing `exp004` precompute / optional `graph_snapshot_v0` sidecars — **no new feeds** |

---

## Parent context (EXP-004 day-aligned courier, not re-scored here)

Oracle re-score **2026-09-23** on sealed 2026-09-20/21 observe+marks ([EXP-004 §7](EXP-004-graph-creator-recurrence-v0.md)):

| Outcome @ 60s | H-G1 (`prior_mint_count > 0`) | H-G2 (burst) | H-G3 (weak recurrence) |
| --- | --- | --- | --- |
| 2026-09-20 / 2026-09-21 | **DIRECTIONAL_NON_KILL** (soft watch) | **KILL_NO_SEPARABLE_ARM** | **DIRECTIONAL_NON_KILL** (soft watch) |

- **H-G2 kill intact** — burst / temporal-cluster lane is falsified; do not retune lookback or spend falsifier budget there.
- **H-G1 soft watch only** — binary repeat-vs-novel passed `separable_vs_spine` and `no_lift_vs_random` at the priced floor; overall stamp remains **DIRECTIONAL_WATCH** (H-G2 killed). **Not** scored lift proof or Discovery promotion.
- **No invented lift** — mean_return_pct and arm lifts live only in gitignored host reports; this EXP does not restate them.

---

## 1. Hypothesis (NH-G1a)

| Field | Content |
| --- | --- |
| **Claim** | Outcome separation, if any, lives in **graded** repeat-creator history within `regime_gate_key` — ordinal buckets `prior_mint_count ∈ {0, 1, 2, 3+}` (or equivalent stratified arms) — not only the binary `prior_mint_count > 0` arm that earned soft watch under parent **H-G1** (Scout **I3** / Graph **I3**). |
| **Knowable-at-T** | `prior_mint_count`, `creator_age_seconds`, `book_t0` stamp (censored book age); same weak `traderPublicKey` spine as EXP-004; prior rows strictly `T_prior < T_decision`; marks outcomes only (`T < t_mark ≤ T+H`). |
| **Falsifier** | No ordinal bucket beats **full-book spine** **and** **same-n random** at **60s** on **both** courier days; **or** any apparent bucket effect **vanishes** when split per `regime_gate_key` (Scout **S1** collapse). Below `MIN_PRICED_FOR_KILL=10` per required arm → **INCOMPLETE** (honest empty arm). |
| **Windows** | **1s / 5s / 15s / 30s / 60s** — primary kill read **60s** (same contract as EXP-004). |
| **Gates** | `separable_vs_spine`, `no_lift_vs_random`, `positive_negative_parity` analog across ordinal arms vs spine — **no new lift thresholds**. |

---

## 2. Cheapest sealed execution path (when council authorizes run — not this PR)

1. Re-score **stratified arms** on the **same** day-aligned 2026-09-20/21 observe+marks only (population and join law unchanged from EXP-004).
2. Prefer thin arm selector (CLI flag or post-process on existing `graph_snapshot_v0` sidecars) — **no new feeds**, **no RPC**, **no cross-day state**.
3. Report per `regime_gate_key` priced_n (S1); bonk/mayhem excluded (S5).
4. Exit / stamp semantics align with parent EXP-004; Proof re-stamp on host artifacts only.

**Explicitly out of scope for this path:** mark densify, cross-day join (e.g. 20/21 marks on `observe-2026-09-23`), EXP-002c retune, evaluate promotion, runner changes, Fast mode.

---

## 3. Soft locks (council law — do not violate on run)

| Lock | Rule |
| --- | --- |
| **H-G2 kill** | Burst cluster lane stays **killed**; no lookback retune to “recover” H-G2 |
| **No densify** | S6 / EXP-003 / EXP-002c law — no mark densification for power |
| **No cross-day join** | Sealed day alignment; do not attach 20/21 marks to 09-23 observe |
| **No EXP-002c retune** | Graph discovery must not confound evaluate rules |
| **No Discovery promotion** | DIRECTIONAL_WATCH / NON_KILL ≠ decode or evaluate promote |
| **No invented lift** | Report gates and taxonomy only; no fabricated means |
| **Follow ≠ mirror** | S3 / DEC-005 — feature / veto / select / enrich; never wallet mirror lists |
| **This registration** | **Proposed, not run** until operator executes sealed re-score |

**Parked (not EXP-004b):** NH-Index bundle; NH-G3a unless G1a remains soft after run; H-G4 on create-only spine; Layer-4 X clusters.

---

## 4. Sequencing (post pick lock)

1. **EXP-004b / NH-G1a** (this doc) — first falsifier budget.
2. **NH-G3a** — pattern-conditioned weak recurrence ([GRAPH-EXP004-NEXT-HYP-V0.md](../ARTIFACTS/GRAPH-EXP004-NEXT-HYP-V0.md) §3) **only if** ordinal G1a stays soft or INCOMPLETE without kill.
3. **NH-Index** — parked until council unparks.

---

## 5. Cross-links

- Lab: [LAB_STATE.md](../LAB_STATE.md) Next work §6 (EXP-004 courier stamp + EXP-004b pick)
- Manager: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Registry: [EXP/README.md](README.md)
- Next-hyp path: [ARTIFACTS/GRAPH-EXP004-NEXT-HYP-V0.md](../ARTIFACTS/GRAPH-EXP004-NEXT-HYP-V0.md) §3 NH-G1a
- Discovery brief: [ARTIFACTS/GRAPH-DISCOVERY-V0.md](../ARTIFACTS/GRAPH-DISCOVERY-V0.md) §9
- Scout taxonomy (I3 / S1): [ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md)

---

## 6. Result / conclusion (placeholder)

| Field | Value |
| --- | --- |
| **Result** | *Not run — no metrics.* |
| **Conclusion** | *Pending operator sealed re-score; do not update LAB_STATE promotion fields from this file alone.* |
