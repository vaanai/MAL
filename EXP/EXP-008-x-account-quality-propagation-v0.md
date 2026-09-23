# EXP-008 — X account-quality / propagation brief v0 (Layer 4 additive)

> **Status: Proposed, not run** — **Registration only** (Helm/Vaan **authorized docs-only** Discovery pick **2026-09-23**). No Oracle sealed measure; no X API keys on host; no scraper, poll, or social CLI in this PR. Scout soft PASS pending; **Proof GATE** required before any scored stamp or promotion claim. **Merge ≠ authorize-run.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-008-x-account-quality-propagation-v0` |
| **Owner seat** | **Scout** (X / social Layer-4 brief ownership); **Proof** (enforces stratify/gate + knowable-at-T on any future sealed social measure) |
| **Source** | [LAB_STATE.md](../LAB_STATE.md) H-social; [STARTER-STACK-OPTIONS-BRIEF.md](../ARTIFACTS/STARTER-STACK-OPTIONS-BRIEF.md) §2 (reassess accelerator); [API-COST-LATENCY-BRIEF.md](../ARTIFACTS/API-COST-LATENCY-BRIEF.md); [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md) **S7** |
| **Status** | **Proposed, not run** — hypothesis + feature taxonomy + attach law + falsifier bundle for **later** Layer-4 measures only |
| **Paper-only** | Yes — no live keys, no trading API, no evaluate promotion from registration |
| **Pick lock** | **Helm/Vaan 2026-09-23** — Discovery next: **X account-quality / propagation** registration (this PR). **Continuous on-chain observe** (PumpPortal WS → sealed JSONL) remains **first** — X is **additive Layer 4**, not discovery spine. Parent Scout L3 / Graph sealed stamps stay **EXP-005** / **EXP-005b** / **EXP-004** / **EXP-004b** — do **not** reassign. **EXP-006** fill-sim harness stays **Proposed, not run** — do **not** implement fill-sim in this PR. **EXP-007** platform-regime labels are **mandatory** stratification inputs for any future social measure on the courier spine. |
| **Depends on** | Mint **already on sealed observe spine** at decision **T** ([CONSTITUTION.md](../CONSTITUTION.md) social path); day-aligned sealed courier when measures are authorized: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` only — **no cross-day marks join** |
| **CLI** | **None in this PR** — future allowlisted poll / export ingest requires **separate Helm re-auth** after docs merge (**no X keys on `mal-core-0`** in phase 0) |

---

## 0. Honesty — what Discovery already stamped (soft watches ≠ promote)

Parent sealed measures on **2026-09-20/21** courier days (Oracle **2026-09-23**) are unchanged by this registration:

| Stamp | Status | Read for EXP-008 |
| --- | --- | --- |
| [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) **L3_PACKET_V0** | **DIRECTIONAL_WATCH** | Soft watch only — L3 decode at **T** does **not** include X; EXP-008 does **not** restate L3 pass or promote decode |
| [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) **L3_minus_v2** | **DIRECTIONAL_WATCH (residual)** | Soft watch only — residual arms unchanged |
| [EXP-007](EXP-007-platform-regime-taxonomy-v0.md) | **Proposed, not run** | Future social measures **must** stratify by Scout **platform-regime** `regime_gate_key` when population spans regimes — **Proof GATE** |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) **EvaluateRulesV2** | **INCOMPLETE closed** | Cohort reference only — **no retune** |

**EXP-008 does not:** restate Graph **EXP-004** / **EXP-004b** arm stamps; revive ordinal / **NH-G3a** / **NH-Index** / **H-G2** burst; implement **EXP-006** fill-sim; authorize Discovery decode promotion; claim **H-social** is proven; or treat soft **DIRECTIONAL_WATCH** as cleared by social features.

**Registration role:** Define **account-quality** and **propagation / engagement-structure** signals on X as **optional, additive Layer-4** inputs for **reassess and latency-aware prioritization** on mints **already** on the observe spine — **not** mint discovery, not a substitute for L1–L3 precompute.

---

## 1. Layer-4 feature slice v0 (account-quality + propagation — taxonomy only)

Canonical cost / defer law: [API-COST-LATENCY-BRIEF.md](../ARTIFACTS/API-COST-LATENCY-BRIEF.md). EXP-008 locks **which signal families** are in scope for phase-0 **documentation** (no invented numeric lift tables in git):

| Family | Example fields (v0 names TBD at implement) | Knowable-at-T rule | Notes |
| --- | --- | --- | --- |
| **Account quality** | Account age band, follower/following ratio band, verified-type flag, bot-heuristic score band (if labeled offline) | Snapshot from X or **manual export** with `t_obs ≤ T` on the **post** tied to reassess | Quality scores **never** mint the candidate; mint must exist on spine first |
| **Propagation structure** | Repost/quote depth band, unique author reach band, velocity band (posts/min in window ending **≤ T**) | Counts use only posts with `created_at ≤ T`; window length **documented**, not tuned to marks | Cashtag / `$TICKER` polls **allowlisted** to spine mints only ([STARTER-STACK-OPTIONS-BRIEF.md](../ARTIFACTS/STARTER-STACK-OPTIONS-BRIEF.md)) |
| **Engagement shape** | Reply ratio band, like/view ratio band (when present in export) | Same `created_at ≤ T` law | No NLP sentiment model in v0 registration |
| **Attach point** | `x_reassess_v0` side packet on decode | Filled only when social evidence exists **≤ T**; absent = explicit `x_attach=none` | **Reassess-only** — does not change evaluate packet constants |

**Out of v0 slice:** broad cashtag firehose; social cluster seeds on graph spine (**S7**); wallet lists derived from X without on-chain corroboration; LLM narrative scores without separate EXP; trading API pool strings as social truth.

**Platform-regime respect (EXP-007):** Any future sealed measure that joins social features to outcomes **must** report gates **per `regime_gate_key`** when `regime_gate_keys_n > 1` — same **K-S1-collapse** spirit as EXP-004 / EXP-005.

---

## 2. Hypothesis (Layer-4 additive latency — falsifiable, not yet measured)

| Field | Content |
| --- | --- |
| **Claim** | For mints **already** on the sealed observe spine at **T**, stamping **account-quality** and **propagation-structure** features from X (or manual export) with strict **knowable-at-T** rules yields **incremental** reassess value — e.g. faster re-prioritization of decode/evaluate attention or veto clarity — **without** replacing L1–L3 precompute and **without** discovering mints earlier than on-chain WS. |
| **What it is not** | Twitter-primary discovery; buy-on-social-alone; live X pipeline on host; fee/lift invention; EXP-002c retune; Discovery promotion; densified marks for power; Graph ordinal revival. |
| **Additive law** | Continuous **market observation** (sealed JSONL ingest) remains **first**. Layer 4 may only **reassess** or **re-prioritize** known mints — [CONSTITUTION.md](../CONSTITUTION.md), [LAB_STATE.md](../LAB_STATE.md) H-social. |
| **Knowable-at-T** | Social rows used at **T** carry `t_obs ≤ T` (or `created_at ≤ T` for counted posts). Horizon marks remain **outcomes only** (`T < t_mark ≤ T+H`). No future post counts, no engagement after **T**, no marks-used-as-features. |
| **Windows (when measures authorized)** | **1s / 5s / 15s / 30s / 60s** — primary read **60s** (family contract). Registration itself has **no** outcome windows. |
| **Population** | Bonding `ingest_hot` creates on sealed courier days; bonk/mayhem **parked** (S5). **Full detect book** ([DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)). |
| **H-social link** | Lab open hypothesis **H-social**: X adds value vs cost for **reassess** — kill if measured lift **<** API + scoring cost at target windows ([LAB_STATE.md](../LAB_STATE.md)); EXP-008 does **not** pre-commit lift thresholds. |

### Attach law v0 (decode side — **not implemented** in this PR)

| Step | Rule |
| --- | --- |
| **Spine first** | `mint` present on sealed `ingest_hot` (or documented enrich) **before** any X query keyed to that mint. |
| **Allowlist poll** | Queries scoped to `from:allowlist` and/or cashtag **for spine mint only** — no global pump firehose. |
| **Side record** | Proposed append-only `x_reassess` JSONL (gitignored courier model) — never rewrite hot ingest. |
| **Stratify** | Authorized measures report priced_n and gates **per `regime_gate_key`** when platform regimes differ (EXP-007). |

---

## 3. Kill / falsifiers (gates only — no invented lift thresholds)

| ID | Worthless / kill if… |
| --- | --- |
| **K-social-discovery** | Any workflow uses X (or export) to **mint the candidate list** before on-chain create appears on sealed observe — violates Layer-4 / constitution. |
| **K-leak** | Social feature uses posts, counts, or engagement with timestamp **> T**, or uses horizon marks / post-T tape to set features at create **T**. |
| **K-spine-skip** | Reassess packet attached without a spine `mint` and sealed `t_decision` anchor. |
| **K-cost-blind** | Report claims H-social **PASS** without documenting API/scrape **$/reassess** and compare window (no invented lift % in git). |
| **K-S1-collapse** | Apparent social effect exists only in pooled aggregate when `regime_gate_keys_n > 1` — forbidden (EXP-007 / EXP-005 law). |
| **K-watch-promote** | Any report treats **DIRECTIONAL_WATCH** (EXP-005 / EXP-005b) or Graph watches as cleared by “better social tags” without a new authorized measure. |
| **K-keys-host** | X API tokens or scraper credentials placed on **`mal-core-0`** without council exception — phase-0 **forbidden**. |
| **K-lift-rescue** | Social features used to **clear** EXP-002c adverse lift **FAIL** without new hypothesis — **forbidden** (002c stays **INCOMPLETE closed**). |

**Primary read (when authorized):** Attribution-only join of manual or offline **allowlisted** X exports to sealed **2026-09-20/21** spine — coverage, leak audit, and optional reassess timing delta — **not** outcome lift invention.

---

## 4. Proposed sealed measure plan (**not executed**)

| Step | What | Not |
| --- | --- | --- |
| 1 | Day-aligned sealed JSONL only: `observe-2026-09-{20,21}.jsonl` | Cross-day join |
| 2 | Manual / offline labeled X sample for spine mints (owner export — **not** on-host keys) | Live poll CLI on Oracle |
| 3 | Stamp `x_reassess_v0` features with knowable-at-T audit | Cashtag firehose |
| 4 | Replay stratification with EXP-007 `regime_gate_key` — attribution only | Retune L3 or Graph |
| 5 | Gates: **K-social-discovery**, **K-leak**, **K-S1-collapse**, **K-cost-blind** | Densify marks for power |
| 6 | Host reports under `/var/lib/mal/paper/_exp008-oracle-2026-09-{20,21}_*` (gitignored) | Committing host means |

**Authorization:** **None** for this PR. Future Oracle, export ingest, or allowlisted poll requires **explicit Helm/Vaan sealed-measure re-auth** (docs merge **≠** authorize-run).

**Still out of scope:** ordinal / **NH-G3a** / **NH-Index** / **EXP-006** fill-sim **implementation**; **H-G2 revive**; evaluate promotion; Fast mode; mirror-wallet; graph social edges (**S7**).

---

## 5. Soft locks (council law)

| Lock | Rule |
| --- | --- |
| **Proposed, not run** | This file is registration — **no scored stamp** |
| **Scout owns Layer-4 brief** | Feature taxonomy + attach law changes go through Scout seat + git SoT |
| **Proof enforces respect** | Future measures stratify by `regime_gate_key` (EXP-007) and pass knowable-at-T audit |
| **Observe first** | Sealed JSONL ingest is discovery spine; X is **additive** reassess only |
| **No invented lift** | Gates and taxonomy only in repo; host reports gitignored |
| **No EXP-002c retune** | v2 runners remain **read-only** reference |
| **No Discovery promotion** | Soft watches ≠ decode or evaluate promote |
| **Paper-only** | No live capital, **no X keys on host** |
| **No densify** | S6 / EXP-003 / EXP-002c law |
| **EXP-006 parked** | Fill-sim harness — **no** measure/CLI in EXP-008 PR |
| **Parent watches soft** | EXP-005 / EXP-005b **DIRECTIONAL_WATCH** — not promote paths |
| **Parked lanes** | Ordinal / **NH-G3a** / **NH-Index** / external paper broker / **H-G2 revive** — **stay parked** |
| **Graph / Scout seats** | EXP-004 / EXP-004b / EXP-005 / EXP-005b ownership unchanged |
| **Fast OFF** | [CONSTITUTION.md](../CONSTITUTION.md) §10 — no Fast mode exception for this registration |
| **Merge ≠ authorize run** | Git merge of EXP-008 registration **does not** authorize social ingest, keys, or Oracle measure |

---

## 6. Cross-links

- Social / cost law: [ARTIFACTS/STARTER-STACK-OPTIONS-BRIEF.md](../ARTIFACTS/STARTER-STACK-OPTIONS-BRIEF.md) §2; [ARTIFACTS/API-COST-LATENCY-BRIEF.md](../ARTIFACTS/API-COST-LATENCY-BRIEF.md)
- Scout S7 (no graph social seeds): [ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md](../ARTIFACTS/DISCOVERY-WALLET-FOLLOW-SIGNALS.md)
- Platform-regime stratify: [EXP-007-platform-regime-taxonomy-v0.md](EXP-007-platform-regime-taxonomy-v0.md)
- Parent L3 (unchanged): [EXP-005-smart-wallet-follow-discovery-v0.md](EXP-005-smart-wallet-follow-discovery-v0.md)
- Paper harness (separate lane): [EXP-006-paper-would-have-happened-harness-v0.md](EXP-006-paper-would-have-happened-harness-v0.md)
- Observe schema: [ARTIFACTS/OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
- Lab: [LAB_STATE.md](../LAB_STATE.md)
- Manager: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Registry: [EXP/README.md](README.md)

---

## 7. Result / conclusion

**Result:** **N/A** — **Proposed, not run.** No Oracle CLI exit code; no host artifacts.

**Conclusion:** EXP-008 registers **X account-quality / propagation** as **additive Layer-4** reassess scope on the sealed observe spine, with Scout ownership, EXP-007 stratification law, and falsifiers for a **future** sealed audit. Implementation, keys, and scoring require **follow-on Helm re-auth** after this docs merge. Scout soft PASS pending; **Proof GATE** before any promotion language.
