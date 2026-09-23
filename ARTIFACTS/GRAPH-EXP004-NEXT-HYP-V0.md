# Graph EXP-004 — next-hypothesis path v0 (post H-G2@60s kill)

| | |
| --- | --- |
| **Status** | **Proposed** — docs only; **not run** |
| **As-of** | 2026-09-23 |
| **Audience** | Graph / Scout / Proof / Helm |
| **Locks** | No Discovery promotion; H-G2 kill intact; no mark densify; no cross-day join; no EXP-002c retune; sealed knowable-at-T; follow = feature/veto/select/enrich (S3); free create = weak links (DEC-005) |
| **Pick (2026-09-23)** | **EXP-004b** registered for **NH-G1a** — [EXP-004b-nh-g1a-ordinal-prior-mint-v0.md](../EXP/EXP-004b-nh-g1a-ordinal-prior-mint-v0.md) (**Scored 2026-09-23** — cross-day **KILL**). **Ordinal lane parked**; **NH-G3a not next** (pick lock: G3a only if G1a stays soft — **new** council pick required); **NH-Index** parked. |
| **Primary inputs** | [EXP-004-graph-creator-recurrence-v0.md](../EXP/EXP-004-graph-creator-recurrence-v0.md) §7; [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) §4; Oracle courier re-score stamped on `main` @ `cc84dac` (PR #32) |

---

## 1. What was measured (Oracle day-aligned courier)

**Method:** Offline `python -m tools.exp004_graph_discovery` on **day-aligned** sealed `observe-YYYY-MM-DD.jsonl` + `marks-YYYY-MM-DD.jsonl` only (2026-09-20 and 2026-09-21). Population = full bonding-create detect book in each day file; outcomes = **existing** EXP-003 marks joined by signature/mint; graph features = weak create-spine precompute per `regime_gate_key` with strict `T_prior < T_decision`.

**Windows scored:** 1s / 5s / 15s / 30s / 60s per EXP contract; **primary kill read = 60s** (`MIN_PRICED_FOR_KILL=10`).

| Day | CLI exit | Overall | population_n | priced_60s_n (book) | H-G1 @60s | H-G2 @60s | H-G3 @60s | H-G4 @60s |
| --- | ---: | --- | ---: | ---: | --- | --- | --- | --- |
| 2026-09-20 | 0 | DIRECTIONAL_WATCH | 13029 | 102 | DIRECTIONAL_NON_KILL | **KILL_NO_SEPARABLE_ARM** | DIRECTIONAL_NON_KILL | INCOMPLETE |
| 2026-09-21 | 0 | DIRECTIONAL_WATCH | 15487 | 122 | DIRECTIONAL_NON_KILL | **KILL_NO_SEPARABLE_ARM** | DIRECTIONAL_NON_KILL | INCOMPLETE |

*(Table cites stamped Oracle rows in EXP-004 §7; this brief does **not** restate mean_return_pct or lift — those live in gitignored host reports `/var/lib/mal/paper/_exp004-oracle-marks-2026-09-{20,21}_*`.)*

**Killed (primary falsifier):** **H-G2** — creator **burst cluster** arm (`burst_count >= 2` within default **3600s** lookback, same `regime_gate_key`). Both days: **`KILL_NO_SEPARABLE_ARM` @ 60s** — positive burst cohort fails **both** `separable_vs_spine` (positive mean ≤ full-book spine mean at priced_n floor) **and** `no_lift_vs_random` (same-n random baseline). This is the EXP-004 **hard kill** for the burst / temporal-cluster lane.

**Passed soft watch (not promotion):** **H-G1** (`prior_mint_count > 0` vs novel creator) and **H-G3** (`creator_buyer_recurrence_weak` on repeated `initialBuy`+`solAmount` pattern) each **`DIRECTIONAL_NON_KILL` @ 60s** both days — gates `separable_vs_spine` and `no_lift_vs_random` **PASS** at the priced floor, but EXP stamps **DIRECTIONAL_WATCH** overall because H-G2 killed and council law treats this as **directional signal taxonomy only**, not scored lift proof or decode promotion.

**Incomplete (honest empty arm):** **H-G4** — `early_wallet_dt_min_seconds` unset on create-only spine (`cluster_tier=weak_creator_only`); positive arm empty → **INCOMPLETE**, not fabricated zeros.

**Not rescored / do not join:** Prior observe-only run on `observe-2026-09-23` (priced_60s_n=2) remains **INCOMPLETE**; **forbidden** to attach 20/21 marks onto 09-23 observe.

---

## 2. Why H-G2@60s kill redirects the graph lane (mechanism)

**Feature definition (as implemented):** For each create at `T_decision`, `burst_count` = number of **prior** creates by the same `traderPublicKey` in the same `regime_gate_key` whose timestamps fall in `(T − burst_lookback_s, T)` (default lookback **3600s**). H-G2 positive arm = `burst_count >= 2` (≥2 prior creates in window — i.e. third-or-more create in a rolling hour, regime-local).

**Kill mechanism:** At **60s** horizon with priced_n above floor, burst-positive rows do **not** earn higher mean outcome than (a) the **full-book spine** nor (b) a **same-n random** draw from the book. Under EXP gate algebra that is **`KILL_NO_SEPARABLE_ARM`** — the graph hypothesis “**recent creator launch burst** is a separable paper arm” is falsified on two independent days at the primary window.

**What that allows vs forbids:**

| Still allowed (next slice) | Parked as primary graph bet |
| --- | --- |
| **H-G1 marginals** — repeat vs first-time creator in regime (I3 / prior-mint index) | **H-G2 burst** as incremental decode feature or “cluster” story |
| **H-G3 weak recurrence** — cross-mint **funding-pattern** proxy, not wallet mirror (I1) | Treating burst as redundant tweak of H-G1 without a **new** falsifier |
| **Regime-stratified** re-checks (S1) — collapse per `regime_gate_key` / `instr=` without cross-regime merge | **H-G4** on create-only spine until trade/RPC enrich tier exists |
| **H-G5 collinearity** check vs spine reserves (taxonomy; not lift claim) | Evaluate promotion, Layer-3 follow lists, Postgres graph theater |

**Literal redirect:** Graph Discovery v0 should **stop spending falsifier budget on temporal burst clusters** on the free create spine and instead **refine Index arms that survived soft watch** (ordinal prior-mint, pattern-conditioned recurrence) or **park** until `graph_enrich` subsample (buyer pubkey / Δt) — not widen burst knobs or densify marks to recover H-G2.

---

## 3. Candidate next hypotheses (1–3) — Proposed, not run

Each candidate is a **child measurement** (new EXP id or EXP-004 appendix) reusing sealed JSONL + existing marks + `exp004` precompute unless noted. **No invented lift thresholds**; same gates at **1/5/15/30/60s**, primary read **60s**.

### NH-G1a — Ordinal **prior-mint depth** (refine I3 / H-G1)

| Field | Content |
| --- | --- |
| **Claim** | Outcome separation, if any, lives in **graded** repeat-creator history (`prior_mint_count ∈ {1,2,3+}`) within `regime_gate_key`, not the binary `>0` arm that passed soft watch. |
| **Knowable-at-T** | `prior_mint_count`, `creator_age_seconds`, `book_t0` stamp (censored book age); same weak `traderPublicKey`. |
| **Falsifier** | No ordinal bucket beats spine **and** random at **60s** on both courier days; or effect **vanishes** when split per `regime_gate_key` (S1 collapse). |
| **Cheapest sealed path** | Re-score with **stratified arms** in a thin CLI flag or post-process on existing `graph_snapshot_v0` sidecars — **no new feeds**; same day-aligned observe+marks files. |
| **Regime note** | Scout **I4 / S1** — report per-gate priced_n; bonk/mayhem excluded (S5). |

### NH-G3a — **Pattern-conditioned** weak recurrence (refine I1 / H-G3)

| Field | Content |
| --- | --- |
| **Claim** | Weak “buyer” signal is **not** generic recurrence but **matched** `initialBuy`+`solAmount` **and** minimum `prior_mint_count ≥ 2` before `T` (reduces single-fluke repeats). |
| **Knowable-at-T** | `recurrence_weak` plus caps on pattern hash count; still **no** distinct buyer wallet (DEC-005 weak tier). |
| **Falsifier** | Tightened positive arm **INCOMPLETE** (priced_n < 10) **or** `FAIL_NO_LIFT_VS_SPINE` / dual fail like H-G2; weak vs would-be strong tier **inversion** if later enrich disagrees (H-G3 falsifier). |
| **Cheapest sealed path** | Arm selector change + re-run `exp004` on 2026-09-20/21 courier JSONL; optional `graph_snapshot_v0` audit lines only. |
| **Regime note** | Follow framing stays **S3** — scalar for veto/select, not mirror wallet. |

### NH-Index — **Regime-gated “repeat creator” index** (I3 + I4 bundle, no burst)

| Field | Content |
| --- | --- |
| **Claim** | A **single capped index** (e.g. weighted sum of normalized `prior_mint_count` + `creator_age_seconds` bucket, **zero weight on burst**) stratifies the book vs spine at 60s without claiming H-graph promotion. |
| **Knowable-at-T** | ≤5 scalars in `graph_snapshot_v0` packet (GRAPH-DISCOVERY G2 cap); `t_precompute_as_of ≤ T_decision`. |
| **Falsifier** | Index positive tertile fails `separable_vs_spine` **and** `no_lift_vs_random` @ 60s on **both** days; or index **collinear** with H-G5 spine reserves (attribute only). |
| **Cheapest sealed path** | Offline index column in sidecar + one scoring pass; **no** RPC, **no** cross-day state. |
| **Regime note** | Taxonomy aligns Scout Index **I3/I4**; burst term **explicitly excluded** post H-G2 kill. |

**Explicitly not proposed here:** H-G2 lookback retune, H-G4 on create-only spine, H-G6 migration-at-create, Layer-4 X clusters, smart-wallet mirror lists.

---

## 4. PARK list (do not spend until DEC/council unparks)

| Item | Reason |
| --- | --- |
| **Discovery / graph promotion** into decode or evaluate | EXP-004 **no promotion**; DIRECTIONAL_WATCH ≠ lift proof |
| **Mark densify** for power | S6 / EXP-002c / EXP-003 law |
| **Cross-day join** (20/21 marks on 09-23 observe or mixed-day state) | Sealed day alignment; PR #32 stamp |
| **H-G2 burst** as live graph feature | **KILL_NO_SEPARABLE_ARM** @ 60s both days — primary falsifier |
| **H-G4 early-wallet Δt** on create-only spine | Empty arm INCOMPLETE; needs trade/RPC `graph_enrich` subsample |
| **X / social Layer 4** cluster seeds | S7 |
| **Live keys, paid wallet index, metered trade WS** | Cheap-first; Gate-1 |
| **EXP-002c retune** | Confounds graph discovery |
| **Postgres / Neo4j graph product** | JSONL-first until a scored child EXP passes |
| **Single-wallet copy / mirror follow** | S3 / DEC-005 |

---

## 5. Suggested council sequencing (cheap)

1. **NH-G1a** — **scored** ([EXP-004b](../EXP/EXP-004b-nh-g1a-ordinal-prior-mint-v0.md) cross-day **KILL**). **Ordinal lane parked**; pick lock does **not** chain NH-G1a → NH-G3a after kill.
2. **NH-G3a / NH-Index** — **not** auto-sequenced from G1a outcome; any NH-G3a run needs a **new** council pick (G3a was **only if** G1a stayed soft).
3. Register further children as **EXP-004c** / **EXP-005** one-pager (hypothesis + falsifier only) — **still Graph seat**. **Done:** EXP-004b NH-G1a — **Scored 2026-09-23**.
4. Re-run courier CLI on **same** 2026-09-20/21 sealed files before any new observe days (already done for EXP-004b).
5. With ordinal **KILL** + H-G2 burst **KILL**, **pause** graph lift lane unless council picks a new falsifier; keep L1 spine + DISCOVERY taxonomy; do not open Layer-3 follow.

---

## 6. Sources

- [EXP-004-graph-creator-recurrence-v0.md](../EXP/EXP-004-graph-creator-recurrence-v0.md)
- [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) §4 (H-G1…G6), §9 (EXP-004 landed)
- [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](DISCOVERY-WALLET-FOLLOW-SIGNALS.md) (I1–I4, Park)
- [LAB_STATE.md](../LAB_STATE.md) Next work §6 (EXP-004 courier stamp)
- [SUMMARY.md](SUMMARY.md) (manager one-liner)
