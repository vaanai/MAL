# EXP-005b — L3 residual vs EXP-002c v2-runner overlap falsifier (Scout)

> **Status: DIRECTIONAL_WATCH (residual)** — Oracle sealed re-score **2026-09-23** (Helm/Vaan **explicit sealed-measure re-auth** for EXP-005b; docs #38 merge `bf31d49` was registration only). Paper-only. Scout soft PASS pending; **Proof GATE** required before any promotion claim.

| Field | Value |
| --- | --- |
| **ID** | `EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0` |
| **Owner seat** | **Scout** (L3 follow path; residual cohort falsifier) |
| **Parent / context** | [EXP-005-smart-wallet-follow-discovery-v0.md](EXP-005-smart-wallet-follow-discovery-v0.md) (**DIRECTIONAL_WATCH**, Oracle 2026-09-20/21) |
| **Status** | **DIRECTIONAL_WATCH (residual)** — primary **L3_minus_v2** sep+random **PASS** @60s both courier days; **not** lift proof; **no Discovery promotion** |
| **Paper-only** | Yes — no live keys, no trading API, no evaluate promotion from this measurement |
| **Pick lock** | **Helm/Vaan 2026-09-23** — Discovery next falsifier after EXP-005 **#37** stamp: does L3 add anything **beyond** the EXP-002c v2-runner slice? Graph sibling measurement stays **EXP-004** / **EXP-004b** — do **not** reassign. |
| **Depends on** | Same sealed courier spine as EXP-005: day-aligned `observe-2026-09-20.jsonl` + `marks-2026-09-20.jsonl` and `observe-2026-09-21.jsonl` + `marks-2026-09-21.jsonl`; `exp004` precompute / `graph_snapshot_v0` scalars; EXP-002c **EvaluateRulesV2** runner labels on the same rows (**reference only** — **no retune**) |
| **CLI** | [`tools/exp005_smart_wallet_follow`](../tools/exp005_smart_wallet_follow.py) with `--residual-falsifier` (primary arm **L3_minus_v2**) |

---

## 0. Motivation — honest kill after EXP-005 DIRECTIONAL_WATCH

Oracle sealed re-score **2026-09-23** stamped parent [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) **DIRECTIONAL_WATCH** on sealed **2026-09-20/21** courier days: full **L3_PACKET_V0** cohort passed **separable_vs_spine** and **no_lift_vs_random** @60s on both days at thin **packet priced_n** — **not** lift proof and **no Discovery promotion**.

Soft watches that block a naive “L3 works” read:

| Watch | Evidence (parent EXP-005 §6; host reports gitignored) |
| --- | --- |
| Thin priced subsample | Packet **priced_n @60s** well above floor but still sparse vs population |
| **K-L3-cohort** / **M2** | ~**46–50%** of L3 packet signatures are also EXP-002c **v2 runners**; Jaccard(L3, v2 runners) ~**0.16** — high modal-slice overlap |

**Residual falsifier purpose:** If L3 signal is mostly the same cohort as evaluate **rules v2** survivors, Scout should **kill or watch-honestly** the **non-v2** L3 tail — not retune EXP-002c or promote decode. This EXP measures **L3 minus v2-runner overlap**, with an optional **L3∩v2** contrast arm for attribution only.

Cross-links: [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md) (**K-L3-cohort**, **M2**), [EXP-002c-rules-v2-adverse-selection.md](EXP-002c-rules-v2-adverse-selection.md) (v2 runner set definition; **INCOMPLETE closed** — cohort reference only).

---

## 1. Knowable-at-T population and packet law (unchanged from EXP-005)

| Item | Rule |
| --- | --- |
| **Population** | Bonding `ingest_hot` creates on sealed courier days; bonk/mayhem **parked** (S5). Same graph precompute book as EXP-004 / EXP-005. |
| **L3_PACKET_V0** | **SELECT** **(I1 ∨ I3):** `creator_buyer_recurrence_weak` **OR** `prior_mint_count > 0`. **VETO** **(H-G2):** `burst_count >= 2` — killed Graph lane; veto only, not revive. **Never mirror-wallet** (S3). |
| **I2 / H-G4** | Honest-empty on create-only spine — missing `early_wallet_dt` does **not** veto. |
| **I4 / S1** | Kill reads stratified per `regime_gate_key`; no cross-regime merges. |
| **As-of-T (S2)** | Sealed create spine + offline precompute strictly `T_prior < T_decision`; marks **outcomes only** (`T < t_mark ≤ T+H`). No marks, post-T buyers, RPC trees, or X in decode. |
| **Windows** | **1s / 5s / 15s / 30s / 60s** — primary kill read **60s** (DEC-006 / EXP-004 contract). |

**EXP-002c v2 runner set (reference):** For each sealed row in the scored book, apply `EvaluateRulesV2` from [`tools.exp002_paper_runner`](../tools/exp002_paper_runner.py) (`--rules v2`); label `runner` → v2-runner signature. **Do not** change v2 constants or gates for this falsifier.

---

## 2. Residual arms (documented for sealed measure)

All arms are subsets of **L3_PACKET_V0** signatures on the same day-aligned book.

| Arm ID | Definition | Role |
| --- | --- | --- |
| **L3_minus_v2** | `{ sig ∈ L3_PACKET_V0 : sig ∉ v2_runner }` | **Primary residual falsifier** — “L3-only” tail after removing EXP-002c v2 runners. Synonym: **L3_only** (non-v2 L3 packet). |
| **L3_intersect_v2** (optional contrast) | `{ sig ∈ L3_PACKET_V0 : sig ∈ v2_runner }` | **Attribution only** — explains how much of parent EXP-005 pass lives in the modal v2 slice. **Not** a Discovery promote path. |
| **L3_full** (reference) | `L3_PACKET_V0` | Parent EXP-005 arm; re-report overlap metrics for continuity. |

**Comparators (each arm @ each horizon):**

| Comparator | Use |
| --- | --- |
| **Full-book spine** | `separable_vs_spine` gate |
| **Same-n random** | Drawn from full book with fixed seed law (EXP-004 / EXP-005) — `no_lift_vs_random` gate |
| **Overlap metrics** | Report **Jaccard** and **%** of arm vs v2 runners (and vs full L3) — same taxonomy as parent `exp002c_runner_overlap` block |

**Pre-measure sanity kills (no Oracle spend):**

- Residual arm **empty** on either day → **INCOMPLETE** / structural kill (nothing left to falsify).
- Residual arm **non-separable** vs complement after v2 removal on paper book → document; sealed measure still required for Proof stamp if authorized.

---

## 3. Kill criteria (gates only — no invented lift thresholds)

Map to Discovery ARTIFACT §8 + parent EXP-005 gates. **No fabricated mean_return_pct or lift floors in git.**

| ID | Kill / falsifier |
| --- | --- |
| **K-residual-primary** | **L3_minus_v2** fails **separable_vs_spine** **or** **no_lift_vs_random** @**60s** on **either** sealed day **2026-09-20** or **2026-09-21** |
| **K-residual-thin** | Residual arm `priced_n < MIN_PRICED_FOR_KILL` (same floor as EXP-004 / EXP-005: **10**) → **INCOMPLETE** (honest thin packet — not a promote) |
| **K-residual-empty** | **L3_minus_v2** arm_n = 0 after v2 subtraction on a courier day → **INCOMPLETE** / **KILL_RESIDUAL_EMPTY** (L3 adds no non-v2 cohort) |
| **K-residual-nonsep** | Residual cohort cannot separate from spine at **60s** after v2 strip (pre-check or sealed) → supports **kill** of “L3 beyond v2” claim |
| **K-S1-collapse** | Any apparent residual pass **vanishes** under every `regime_gate_key` split (same falsifier as EXP-005) |
| **K-spine-random** | Inherited law: fail sep **or** random vs spine / same-n random at primary horizon |

**Success language (still not promote):** If **L3_minus_v2** clears **separable_vs_spine** and **no_lift_vs_random** @60s on **both** courier days with honest priced floors and **no S1 collapse**, stamp **DIRECTIONAL_WATCH** (residual) or **DIRECTIONAL_NON_KILL** per Proof taxonomy — **watch only**, **not** Discovery promotion and **not** evaluate runner promotion.

**Optional contrast arm:** **L3_intersect_v2** reported for attribution; failure there does **not** alone kill L3 if residual passes — overlap is expected given parent overlap table.

---

## 4. Sealed measure plan (**executed 2026-09-23**)

| Step | What | Not |
| --- | --- | --- |
| 1 | Day-aligned sealed JSONL only: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` (EXP-004 / EXP-005 courier law) | Cross-day join (e.g. 20/21 marks on `observe-2026-09-23`) |
| 2 | Reuse **`python -m tools.exp004_graph_discovery`** precompute; build EXP-005 scored book | New feeds; RPC wallet crawl |
| 3 | Score **L3_minus_v2** (primary), optional **L3_intersect_v2**, reference **L3_full**; comparators spine + same-n random | Tightening EXP-002c constants |
| 4 | Gates: `separable_vs_spine`, `no_lift_vs_random`; `priced_n` floor **10**; per-window reads 1s…60s; **kill stamp @60s** | Invented lift numbers in git |
| 5 | S1 stratification by `regime_gate_key`; overlap Jaccard / % vs v2 runners | Mark **densify** for power |
| 6 | Host-only reports under `/var/lib/mal/paper/_exp005b-oracle-2026-09-{20,21}_*` (gitignored) | Committing host means or lifts |

**Authorization:** **Helm/Vaan 2026-09-23 explicit sealed-measure re-auth** for EXP-005b (this run). Registration merge **#38** (`bf31d49`) **≠** authorize-run. Scout soft PASS pending; **Proof GATE** before promotion claims.

**Still out of scope:** ordinal / **NH-G3a** / **NH-Index** / fill-sim / **H-G2 revive** / evaluate promotion / Fast mode.

---

## 5. Soft locks (council law)

| Lock | Rule |
| --- | --- |
| **No invented lift** | Gates and taxonomy only in repo; host reports gitignored |
| **No EXP-002c retune** | v2 runner set is **read-only** reference for subtraction |
| **No Discovery promotion** | Residual watch ≠ decode or evaluate promote |
| **Paper-only** | No live capital, no trading API keys on host |
| **Follow ≠ mirror (S3)** | Feature / veto / select / enrich only |
| **No densify** | S6 / EXP-003 / EXP-002c law |
| **H-G2 stay killed** | No burst lookback retune or H-G2 arm revival |
| **Parked lanes** | Ordinal / **NH-G3a** / **NH-Index** / fill-sim unpark / **H-G2 revive** — **parked** |
| **Graph seat** | EXP-004 / EXP-004b measurement ownership unchanged |
| **Merge ≠ authorize run** | Docs registration does not authorize Oracle; **this Helm re-auth run** is the scored stamp |

---

## 6. Cross-links

- Parent result: [EXP-005-smart-wallet-follow-discovery-v0.md](EXP-005-smart-wallet-follow-discovery-v0.md)
- Evaluate v2 reference: [EXP-002c-rules-v2-adverse-selection.md](EXP-002c-rules-v2-adverse-selection.md)
- Discovery taxonomy: [ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md)
- Lab: [LAB_STATE.md](../LAB_STATE.md)
- Manager: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Registry: [EXP/README.md](README.md)
- Paper fill harness (Proposed, not run): [EXP-006-paper-would-have-happened-harness-v0.md](EXP-006-paper-would-have-happened-harness-v0.md) — **L3_minus_v2** on soft-watch candidate list only
- Graph measurement: [EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md)
- Book law: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)

---

## 7. Result / conclusion (Oracle sealed re-score 2026-09-23)

**Authorization:** Helm/Vaan **2026-09-23** explicit EXP-005b sealed-measure re-auth (not docs-only #38). **Inputs:** `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl`. **Host artifacts (gitignored):** `/var/lib/mal/paper/_exp005b-oracle-2026-09-{20,21}_*`. **Repo CLI:** `python -m tools.exp005_smart_wallet_follow --residual-falsifier`.

### Primary arm **L3_minus_v2** @60s (per day)

| Day | CLI exit | overall @60s | population_n | priced_60s_n (book) | L3_minus_v2 arm_n | residual priced_n @60s | separable_vs_spine | no_lift_vs_random | S1 collapse | H-G2 ref @60s |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 2026-09-20 | 0 | **DIRECTIONAL_NON_KILL** | 13029 | 102 | 1175 | 14 | **PASS** | **PASS** | false | **KILL_NO_SEPARABLE_ARM** (kill intact) |
| 2026-09-21 | 0 | **DIRECTIONAL_NON_KILL** | 15487 | 122 | 1380 | 11 | **PASS** | **PASS** | false | **KILL_NO_SEPARABLE_ARM** (kill intact) |

**Cross-day overall (primary):** **DIRECTIONAL_WATCH (residual)** — both days soft non-kill at 60s on **L3_minus_v2**; **not** scored lift proof; **no Discovery promotion**.

### Reference **L3_full** and contrast **L3_intersect_v2** @60s (attribution)

| Day | L3_full sep / random | L3_full priced_n @60s | L3_intersect_v2 overall | L3_intersect_v2 priced_n @60s |
| --- | --- | ---: | --- | ---: |
| 2026-09-20 | PASS / PASS | 22 | **INCOMPLETE** (thin) | 8 |
| 2026-09-21 | PASS / PASS | 18 | **INCOMPLETE** (thin) | 7 |

**L3_intersect_v2** is attribution-only; **INCOMPLETE** at thin priced_n does **not** overturn residual primary gates.

### Overlap (unchanged parent attribution; no retune)

| Day | L3_minus_v2_n | L3∩v2_runner_n | % of L3 that are v2 runners | Jaccard (L3 vs v2 runners) |
| --- | ---: | ---: | ---: | ---: |
| 2026-09-20 | 1175 | 1150 | ~49.5% | ~0.165 |
| 2026-09-21 | 1380 | 1198 | ~46.5% | ~0.156 |

### Soft watches (Proof — not promote)

| Watch | Read |
| --- | --- |
| **Thin residual priced_n** | Primary residual **priced_n @60s** (14 / 11) is above floor **10** but **thinner** than parent L3_full packet (22 / 18) — same sparse-mark regime as EXP-005 |
| **K-L3-cohort / M2** | ~half of L3 packet remains v2-modal overlap; residual pass does **not** clear evaluate adverse-selection failure |
| **Contrast arm thin** | **L3_intersect_v2** **INCOMPLETE** on priced floor — expected under S6; not a promote path |
| **DIRECTIONAL ≠ promote** | **DIRECTIONAL_WATCH (residual)** is Scout falsifier taxonomy only; **Proof GATE** still required |

**Conclusion:** **L3_minus_v2** passes **separable_vs_spine** and **no_lift_vs_random** @60s on **both** courier days with honest priced floors; **K-S1-collapse** **not** triggered; **K-residual-empty** **not** triggered. Does **not** authorize evaluate changes or Discovery decode promotion. Parent modal overlap remains a **cohort falsifier risk** even when residual gates PASS.

**Parked:** ordinal / NH-G3a / NH-Index / fill-sim / H-G2 revive.
