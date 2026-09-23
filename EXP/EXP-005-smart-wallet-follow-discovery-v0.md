# EXP-005 — Smart-wallet / follow Discovery v0 (Scout L3 packet)

> **Status: DIRECTIONAL_WATCH** — Oracle sealed re-score **2026-09-23** (Helm-authorized sealed measure; Scout soft PASS pending; **Proof GATE** required). Paper-only. **No promotion.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-005-smart-wallet-follow-discovery-v0` |
| **Owner seat** | **Scout** (L3 follow path + Index taxonomy measurement) |
| **Source** | [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md) (Discovery taxonomy ARTIFACT) |
| **Status** | **DIRECTIONAL_WATCH** (Oracle day-aligned 2026-09-20/21; cross-day soft watch — **not** lift proof) |
| **Paper-only** | Yes — no live keys, no trading API, no evaluate promotion from this measurement |
| **Pick lock** | **Helm/Vaan 2026-09-23** — Discovery next: Scout **EXP-005** sealed measure (this PR). Graph sibling measurement stays **EXP-004** / **EXP-004b** — do **not** reassign. |
| **Depends on** | Same sealed courier spine as EXP-004 §7: day-aligned `observe-2026-09-20.jsonl` + `marks-2026-09-20.jsonl` and `observe-2026-09-21.jsonl` + `marks-2026-09-21.jsonl`; `exp004` precompute / `graph_snapshot_v0` scalars — **no new feeds** |
| **CLI** | `python -m tools.exp005_smart_wallet_follow` (same day-aligned `--marks` as EXP-004) |

---

## 0. Honesty — what Graph already closed (do not re-litigate here)

Oracle day-aligned re-score **2026-09-23** on sealed 2026-09-20/21 ([EXP-004](EXP-004-graph-creator-recurrence-v0.md)):

| Arm @ 60s | 2026-09-20 / 2026-09-21 |
| --- | --- |
| **H-G1** (`prior_mint_count > 0`) | **DIRECTIONAL_NON_KILL** (soft watch — not lift proof) |
| **H-G2** (burst) | **KILL_NO_SEPARABLE_ARM** both days — **stay killed** |
| **H-G3** (weak recurrence) | **DIRECTIONAL_NON_KILL** (soft watch) |
| **H-G4** (early-wallet Δt) | **INCOMPLETE** (empty / create-only arm) |

Child **EXP-004b** NH-G1a ordinal buckets @60s: **cross-day KILL** — **ordinal lane parked**; **NH-G3a**, **NH-Index**, and any **H-G2 revive** remain **parked** ([EXP-004b](EXP-004b-nh-g1a-ordinal-prior-mint-v0.md)).

**EXP-005 does not:** revive ordinal depth, NH-G3a, NH-Index, fill-sim, or H-G2 burst measurement. It does **not** claim Discovery promotion or restate Graph arm stamps.

**What remains falsifiable for Scout:** whether an **L3 follow packet** — **S3** select / veto / enrich on capped as-of-T **Index I1–I4** scalars (mapped to **H-G1…H-G4** + **S1**), **never mirror-wallet** — identifies a **different** runner cohort than the EXP-002c **modal adverse slice** on the **full detect book**, with separable outcomes vs spine-only at priced horizons. Graph scored **marginal H-G arms**; Scout measures the **paper→gated L3 path** falsifier bundle ([DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md) §7–§8, **M2**).

---

## 1. Hypothesis (Scout L3 follow path)

| Field | Content |
| --- | --- |
| **Claim** | Capped **as-of-T** wallet/cluster features used as **L3 follow signals** (**S3**: veto / select / enrich on the decode packet) — grounded in **I1** (H-G3 weak recurrence), **I3** (H-G1 prior-mints / key age), **I4** (S1 regime gate), and **honest-empty I2** (H-G4 pre-T Δt when sealed timing exists) — can **select or veto** a runner subset that is **not** redundant with the EXP-002c v2 runner modal cohort and shows **separable_vs_spine** **and** **no_lift_vs_random** at **60s** on **both** courier days, **per `regime_gate_key`** (S1). |
| **What it is not** | Mirror wallet X; single-wallet PnL chase; RPC funding trees; post-T buyer tape at create **T**; ordinal prior-mint buckets; H-G2 burst arms; Discovery promotion. |
| **Knowable-at-T (S2)** | Sealed create spine + offline precompute strictly `T_prior < T_decision`; graph scalars stamped `t_precompute_as_of ≤ T_decision`. **Marks are outcomes only** (`T < t_mark ≤ T+H`). No marks, post-T buyers, RPC trees, or X in decode. |
| **Index ↔ Graph map** | **I3↔H-G1**, **I1↔H-G3 weak**, **I2↔H-G4** (pre-T window only), **I4↔S1** — day-1 law from Discovery ARTIFACT §1. |
| **Windows** | **1s / 5s / 15s / 30s / 60s** — primary kill read **60s** (same contract as EXP-004 / DEC-006). |
| **Population** | Bonding `ingest_hot` creates on sealed days; bonk/mayhem **parked** (S5). **Full detect book** labels ([DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)). |

### L3 packet rule v0 (CLI `L3_PACKET_V0`)

| Step | Rule |
| --- | --- |
| **SELECT** | **(I1)** `creator_buyer_recurrence_weak` **OR** **(I3)** `prior_mint_count > 0` |
| **VETO** | **(H-G2)** `burst_count >= 2` — killed Graph lane; **veto only** (not scored as revive arm) |
| **I2 / H-G4** | Honest-empty on create-only spine — missing `early_wallet_dt` does **not** veto |
| **I4 / S1** | Kill reads stratified per `regime_gate_key`; precompute never cross-regime merges |
| **S3** | Never mirror-wallet / alpha-wallet lists |

---

## 2. Kill / falsifiers (full detect book; no invented lift)

Map to Discovery ARTIFACT §8 + Graph gates. **No fabricated mean_return_pct or lift thresholds.**

| ID | Worthless if… |
| --- | --- |
| **K-L3-cohort** | L3 packet-selected cohort **overlaps** EXP-002c v2 runner set without outcome separation — same **modal launch snapshot** failure mode as **K-HG2** / **M2** (attribute; do **not** retune v2). |
| **K-HG1** | I3 / H-G1 stratification shows **no separable arm** at any of 1/5/15/30/60s; or **INCOMPLETE** priced coverage; or effect **vanishes** under S1 split — *(Graph already soft-watch @60s; Scout L3 kill requires **cohort + packet** falsifier, not re-scoring Graph binary arm alone)* |
| **K-HG3** | I1 / H-G3 weak implemented as **follow-wallet** or cross-regime merge (S1 fail) |
| **K-HG4** | I2 / H-G4 uses post-T tape at create **T**; or fake zero when Δt **not ready** inside 1s from sealed rows (S4) |
| **K-I4** | I4 / S1 stage flags mix bonding vs graduated or smuggle bonk/mayhem (S5) |
| **K-copy** | Any L3 rule is **mirror wallet X** / alpha-wallet list without L1+L2 caps (S3) |
| **K-leak** | Decode uses marks, post-T buyers, RPC trees, or X (S2/S7) |
| **K-spine-random** | At **60s** on **both** sealed days: packet cohort fails **separable_vs_spine** **or** **no_lift_vs_random** vs same-n random; or `priced_n < MIN_PRICED_FOR_KILL=10` → **INCOMPLETE** (honest empty arm) |
| **K-S1-collapse** | Any apparent L3 effect **vanishes** when split per `regime_gate_key` |

**Primary kill horizon:** **60s**, both **2026-09-20** and **2026-09-21**, regime-split mandatory (S1).

---

## 3. Sealed measure plan (executed 2026-09-23)

| Step | What | Not |
| --- | --- | --- |
| 1 | Day-aligned sealed JSONL only: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` (EXP-004 courier law) | Cross-day join (e.g. 20/21 marks on `observe-2026-09-23`) |
| 2 | Reuse **`python -m tools.exp004_graph_discovery`** precompute; Scout arm = **`L3_PACKET_V0`** on those scalars + spine fields | New feeds; RPC wallet crawl; hot-packet backfill |
| 3 | Stratify **L3 packet cohort** vs **full-book spine** and vs **random same-n**; report **overlap** with EXP-002c v2 runner signatures (**K-L3-cohort** attribution) | Tightening EXP-002c constants |
| 4 | Gates: `separable_vs_spine`, `no_lift_vs_random`; `priced_n` floor **10** | Invented lift numbers in git |
| 5 | Per-window reads 1s/5s/15s/30s/60s; **kill stamp @60s** | Mark densify for power (S6) |

**Still out of scope:** ordinal / NH-G3a / NH-Index / fill-sim / H-G2 revive / evaluate promotion / Fast mode.

---

## 4. Soft locks (council law)

| Lock | Rule |
| --- | --- |
| **No invented lift** | Gates and taxonomy only in repo; host reports gitignored |
| **No EXP-002c retune** | L3 Discovery must not confound evaluate rules |
| **No Discovery promotion** | DIRECTIONAL_WATCH / NON_KILL ≠ decode or evaluate promote |
| **Paper-only** | No live capital, no trading API keys on host |
| **Follow ≠ mirror (S3)** | Feature / veto / select / enrich only |
| **No densify** | S6 / EXP-003 / EXP-002c law |
| **H-G2 stay killed** | No burst lookback retune or H-G2 arm revival |
| **Parked lanes** | Ordinal / **NH-G3a** / **NH-Index** / fill-sim unpark / **H-G2 revive** — **parked** |
| **Graph seat** | EXP-004 / EXP-004b measurement ownership unchanged |
| **Merge ≠ authorize run** | Git merge of EXP-005 registry/docs **does not** authorize sealed measure; **this Helm-authorized Oracle run** (2026-09-23) is the scored stamp. Scout soft PASS pending; **Proof GATE** required before promotion claims. |

---

## 5. Cross-links

- Lab: [LAB_STATE.md](../LAB_STATE.md) Next work (EXP-005 stamp)
- Manager: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Registry: [EXP/README.md](README.md)
- Discovery taxonomy: [ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md)
- Graph brief: [ARTIFACTS/GRAPH-DISCOVERY-V0.md](../ARTIFACTS/GRAPH-DISCOVERY-V0.md)
- Graph measurement: [EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md)
- Graph ordinal child (parked lane): [EXP-004b-nh-g1a-ordinal-prior-mint-v0.md](EXP-004b-nh-g1a-ordinal-prior-mint-v0.md)
- Book law: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Marks: [EXP-003-post-create-marks.md](EXP-003-post-create-marks.md)
- Evaluate baseline overlap reference: [EXP-002c-rules-v2-adverse-selection.md](EXP-002c-rules-v2-adverse-selection.md) (**INCOMPLETE closed** — cohort reference only)

---

## 6. Result / conclusion (Oracle sealed re-score 2026-09-23)

**Authorization:** Helm/Vaan **2026-09-23** sealed measure (not docs-only merge). **Inputs:** `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` (courier lines unchanged from EXP-004 §7). **Host artifacts (gitignored):** `/var/lib/mal/paper/_exp005-oracle-2026-09-{20,21}_*`. **Repo CLI:** [`tools/exp005_smart_wallet_follow.py`](../tools/exp005_smart_wallet_follow.py).

| Day | CLI exit | overall @60s | population_n | priced_60s_n (book) | L3 packet arm_n | packet priced_n @60s | separable_vs_spine | no_lift_vs_random | S1 collapse | H-G2 ref @60s |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 2026-09-20 | 0 | **DIRECTIONAL_NON_KILL** | 13029 | 102 | 2325 | 22 | **PASS** | **PASS** | false | **KILL_NO_SEPARABLE_ARM** (kill intact) |
| 2026-09-21 | 0 | **DIRECTIONAL_NON_KILL** | 15487 | 122 | 2578 | 18 | **PASS** | **PASS** | false | **KILL_NO_SEPARABLE_ARM** (kill intact) |

**Cross-day overall:** **DIRECTIONAL_WATCH** (both days soft non-kill at 60s; **not** scored lift proof; **no Discovery promotion**).

**EXP-002c v2 runner overlap (K-L3-cohort attribution, no retune):**

| Day | overlap_l3∩v2_runner_n | pct of L3 packet that are v2 runners | Jaccard (L3 vs v2 runners) |
| --- | ---: | ---: | ---: |
| 2026-09-20 | 1150 | ~49.5% | ~0.165 |
| 2026-09-21 | 1198 | ~46.5% | ~0.156 |

High overlap with the v2 modal runner slice remains a **cohort falsifier risk** (**M2** / **K-L3-cohort**) even when sep+random gates PASS at thin priced_n — Proof should treat **DIRECTIONAL_WATCH** as soft, not decode promotion.

**Conclusion:** L3 **packet** cohort (`L3_PACKET_V0`) passes **separable_vs_spine** and **no_lift_vs_random** @60s on **both** courier days with honest priced floors; **S1 collapse** falsifier **not** triggered. **Does not** clear EXP-002c adverse-selection failure or authorize evaluate changes. **Parked:** ordinal / NH-G3a / NH-Index / fill-sim / H-G2 revive.

**Next falsifier (Proposed, not run):** [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) — score **L3_minus_v2** (L3 packet signatures not in EXP-002c v2 runner set) on the same sealed 2026-09-20/21 courier days; **merge ≠ authorize run**.
