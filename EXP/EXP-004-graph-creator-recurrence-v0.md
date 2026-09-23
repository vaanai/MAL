# EXP-004 — Graph creator-recurrence v0 (Discovery measurement)

Graph seat. **Paper-only.** Measurement layer for **H-graph** — regime-gated, capped **as-of-T** graph features vs **spine-only** baseline on the **full detect book** at **1s / 5s / 15s / 30s / 60s**. **No evaluate promotion.** **No invented lift.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-004-graph-creator-recurrence-v0` |
| **Status** | **DIRECTIONAL_WATCH** (Oracle day-aligned courier re-score 2026-09-23; see §7) |
| **Owner seat** | Graph (measurement); Proof re-stamps when data READY |
| **Locked** | 2026-09-23 (Helm/Vaan morning go) |
| **Depends on** | Sealed `ingest_hot` JSONL; existing EXP-003 `--marks`; [GRAPH-DISCOVERY-V0.md](../ARTIFACTS/GRAPH-DISCOVERY-V0.md); Scout taxonomy cross-link [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md) (Index I1–I4 — **no Scout EXP stub**) |
| **Hypothesis (in scope)** | **H-G1…H-G4** as-of-T from Graph brief §4 — creator age/prior-mints, burst, weak creator↔buyer recurrence, optional early-wallet Δt. **Not** H-G5/H-G6 lift claims. |
| **Method** | Offline single-pass precompute per `regime_gate_key` (`regime_id` string); emit optional `graph_snapshot_v0` sidecar; join **existing** marks; stratify graph arms vs spine + random same-n. CLI: `python -m tools.exp004_graph_discovery`. |
| **As-of-T** | `T_decision = t_ws`; prior rows strictly `T_prior < T_decision`; marks **outcomes only** (`T < t_mark ≤ T+H`). Latency `t_ws` vs `t_event` logged when present — not rewritten. |
| **Regime labels** | Every snapshot carries parent `regime_id` + `regime_gate_key`; **no cross-regime merge** (S1). |
| **Windows** | **1s / 5s / 15s / 30s / 60s** — primary kill read: **60s** |
| **Kill-attempt** | Per H-G: **separable_vs_spine** and **no_lift_vs_random** at priced arms; **INCOMPLETE** below `MIN_PRICED_FOR_KILL=10`; H-G4 empty arms honest (create-spine). **Soft fence:** no densify-for-power / no EXP-002c retune. |
| **Result** | Oracle **2026-09-23 (prior):** observe-only on host; priced_60s_n=2 → **INCOMPLETE**. **Courier re-score (same day):** sealed 2026-09-20 + 2026-09-21 observe/marks on `mal-core-vnic` @ `bdc004a` → `/var/lib/mal/paper/_exp004-oracle-marks-2026-09-{20,21}_*` (gitignored). |
| **Conclusion** | **No promotion.** Primary **60s:** H-G2 **KILL_NO_SEPARABLE_ARM** both days; H-G1/H-G3 **DIRECTIONAL_NON_KILL** at 60s (soft watch — **not** scored lift proof). H-G4 **INCOMPLETE** (empty positive arm). **No densify / no invented join** with 2026-09-23 observe. |

---

## 1. Scope lock

| Item | Lock |
| --- | --- |
| **Population** | Bonding `ingest_hot` creates; bonk/mayhem **parked** excluded from graph precompute (S5) |
| **Evidence** | **weak_ws** create spine only day-1 ([DEC-005](../DEC/DEC-005-hot-packet-clocks-and-provenance.md) weak `traderPublicKey`) |
| **Outcomes** | EXP-003 `outcome_mark` or later ingest ticks — same join law as EXP-002 |
| **Forbidden** | Live keys; RPC wallet crawl as requirement; evaluate rule changes; H-G5/H-G6 scored lift; Scout owning this EXP id |

---

## 2. Hypotheses scored (H-G1…H-G4)

| ID | Arm (positive) | Falsifier ref |
| --- | --- | --- |
| **H-G1** | `prior_mint_count > 0` (repeat creator in regime gate) | K-HG1 |
| **H-G2** | `burst_count >= 2` in lookback (default 3600s knob) | K-HG2 |
| **H-G3** | `creator_buyer_recurrence_weak` (matching `initialBuy`+`solAmount` on prior mint) | K-HG3 |
| **H-G4** | `early_wallet_dt_min_seconds` set | K-HG4 — **expect INCOMPLETE** on create-only JSONL |

**Taxonomy only (not scored here):** H-G5 reserve collinearity; H-G6 migration retrospective.

---

## 3. Gates (Proof contract)

| Gate | Rule |
| --- | --- |
| `no_lift_vs_random` | Positive arm mean > random same-n at horizon, else **FAIL** (or **INCOMPLETE** if priced_n < 10) |
| `separable_vs_spine` | Positive arm mean > full-book spine mean, else **FAIL** |
| `positive_negative_parity` | Analog of reject↔runner parity — arms must differ (>5% relative) or **FAIL** |
| `priced_n` floor | `MIN_PRICED_FOR_KILL=10` per required arm — else **INCOMPLETE** |
| **Densify fence** | No mark densification to rescue lift (S6 / EXP-002c law) |

---

## 4. CLI

```bash
python -m tools.exp004_graph_discovery data/observe/observe-2026-09-21.jsonl \
  --marks data/observe/marks-2026-09-21.jsonl \
  --write-snapshots data/observe/graph-2026-09-21.jsonl
```

Outputs (gitignored): `data/observe/_exp004_summary.json`, `_exp004_report.md`.

Exit codes: `0` scored evaluable; `1` INCOMPLETE gates; `2` DATA BLOCKER.

---

## 5. Scout cross-link (taxonomy only)

[DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md): I1 recurrence, I3 prior-mints, I4 regime gate, Park list — **follow = feature/veto/select/enrich**, never mirror wallet.

---

## 6. Sources

- Brief: [GRAPH-DISCOVERY-V0.md](../ARTIFACTS/GRAPH-DISCOVERY-V0.md)
- Book law: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Marks: [EXP-003](EXP-003-post-create-marks.md)

---

## 7. Oracle day-aligned runs (EXP-004 courier, 2026-09-23)

Day-aligned sealed JSONL only (`observe-YYYY-MM-DD` + `marks-YYYY-MM-DD`). EXP-003 RPC backfill marks from laptop courier; **not** attached to `observe-2026-09-23.jsonl`.

| Day | CLI exit | overall | population_n | priced_60s_n (floor 10) | H-G1 @60s | H-G2 @60s | H-G3 @60s | H-G4 @60s |
| --- | ---: | --- | ---: | ---: | --- | --- | --- | --- |
| 2026-09-20 | 0 | DIRECTIONAL_WATCH | 13029 | 102 | DIRECTIONAL_NON_KILL | KILL_NO_SEPARABLE_ARM | DIRECTIONAL_NON_KILL | INCOMPLETE |
| 2026-09-21 | 0 | DIRECTIONAL_WATCH | 15487 | 122 | DIRECTIONAL_NON_KILL | KILL_NO_SEPARABLE_ARM | DIRECTIONAL_NON_KILL | INCOMPLETE |

Sealed inputs on host: marks **3356** / **3439** lines; observe **15324** / **18862** lines (unchanged `observe-2026-09-23` ingest).

**Next hypotheses (docs only):** Post H-G2@60s kill — candidate Index refinements and PARK list: [GRAPH-EXP004-NEXT-HYP-V0.md](../ARTIFACTS/GRAPH-EXP004-NEXT-HYP-V0.md) (**Proposed**, not run; no promotion). **Pick lock (2026-09-23):** child [EXP-004b NH-G1a](EXP-004b-nh-g1a-ordinal-prior-mint-v0.md) — ordinal prior-mint depth (**Scored 2026-09-23**, cross-day **KILL**); **ordinal lane parked** — NH-G3a **not next** per lock (new council pick for any G3a).
