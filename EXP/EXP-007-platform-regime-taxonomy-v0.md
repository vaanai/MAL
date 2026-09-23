# EXP-007 — Platform-regime taxonomy v0 (bonding-curve version / fees / graduation / pair asset)

> **Status: Proposed, not run** — **Registration only** (Helm/Vaan **authorized docs-only** Discovery pick **2026-09-23**). No Oracle sealed measure; no observe-wiring or label-encoder CLI in this PR. Scout soft PASS pending; **Proof GATE** required before any scored stamp or promotion claim. **Merge ≠ authorize-run.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-007-platform-regime-taxonomy-v0` |
| **Owner seat** | **Scout** (platform-regime label ownership); **Proof** (enforces that sealed measures stratify / gate by these labels) |
| **Source** | [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md) §1–§5 (bonding lineage, fees, graduation, quote); [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), [DEC-004](../DEC/DEC-004-regime-id-encoding.md), [REGIME-AT-INGEST-MATRIX.md](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md) |
| **Status** | **Proposed, not run** — taxonomy scope + attach law + falsifier bundle for **later** measures only |
| **Paper-only** | Yes — no live keys, no trading API, no evaluate promotion from registration |
| **Pick lock** | **Helm/Vaan 2026-09-23** — Discovery next: **platform-regime taxonomy v0** registration (this PR). Parent Scout L3 / Graph sealed stamps stay **EXP-005** / **EXP-005b** / **EXP-004** / **EXP-004b** — do **not** reassign. **EXP-006** fill-sim harness stays **Proposed, not run** — do **not** implement fill-sim in this PR. |
| **Depends on** | Same **day-aligned sealed courier** spine as EXP-004 / EXP-005 family: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` only — **no cross-day marks join**; sealed rows carry `regime_id` / `regime_gate_key` per existing observe schema |
| **CLI** | **None in this PR** — future label audit / mislabel extension requires **separate Helm re-auth** after docs merge (EXP-001 tooling is **stage/regime_id** sample only — not platform-dimension coverage) |

---

## 0. Honesty — what Discovery already stamped (soft watches ≠ promote)

Parent Scout sealed measures on **2026-09-20/21** courier days (Oracle **2026-09-23**):

| Stamp | Status | Read for EXP-007 |
| --- | --- | --- |
| [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) **L3_PACKET_V0** | **DIRECTIONAL_WATCH** | Soft watch only — **I4 / S1** already requires `regime_gate_key` stratification; EXP-007 **does not** restate L3 pass or promote decode |
| [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) **L3_minus_v2** | **DIRECTIONAL_WATCH (residual)** | Soft watch only — residual arms use same S1 law; **no** residual promote |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) **EvaluateRulesV2** | **INCOMPLETE closed** | Cohort reference only — **no retune** |
| [EXP-001](EXP-001-regime-stage-mislabel.md) | **PASS closed** (tooling) | Mislabel sample on stamped `regime_id` + **stage** — **not** a full platform-dimension audit |

**EXP-007 does not:** restate Graph **EXP-004** / **EXP-004b** arm stamps; revive ordinal / **NH-G3a** / **NH-Index** / **H-G2** burst; implement **EXP-006** fill-sim; include X account-quality brief (runner-up **parked**); authorize Discovery decode promotion; or claim that soft **DIRECTIONAL_WATCH** clears evaluate failure.

**Registration role:** Make **platform-regime dimensions** — **bonding-curve version / instruction lineage**, **fee schedule**, **graduation behavior**, **pair / quote asset** — **first-class, enumerable labels** on sealed observe rows, with **knowable-at-T attach law** and **Proof-enforced stratification** for all future sealed measures on the courier spine.

---

## 1. Platform-regime dimensions v0 (first-class labels)

Canonical enum detail lives in [REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md). EXP-007 locks **which keys** are in scope for phase-0 **platform-regime** gating (subset of full `regime_id`; encoded per [DEC-004](../DEC/DEC-004-regime-id-encoding.md)):

| Dimension | `regime_id` keys (v0) | Knowable-at-T source | Notes |
| --- | --- | --- | --- |
| **Bonding-curve version / lineage** | `venue`, `instr`, `trade_iface` | **RPC** program id + ix discriminator on create (or `instr=unknown_until_rpc` at WS-only T with explicit unverified flag) | Distinguish `create` vs `create_v2`, legacy vs v2 trade iface — program upgrade falsifier |
| **Fee schedule** | `fee` | **RPC** `Global` / per-coin sharing config at or before decision **T** | `fee=unverified` allowed at create WS — never blank; no invented bps tables in git |
| **Graduation behavior** | `stage`, `market` | WS coarse stage + **RPC** `complete`, migration WS, pool program id | PumpSwap vs legacy Raydium paths are **distinct** regimes; threshold from on-chain completion — no hard-coded SOL folklore |
| **Pair / quote asset** | `quote` | **RPC** `quote_mint` on bonding curve; WS default `quote=wsol_assumed` until confirmed | USDC-quoted and `quote=other` are first-class — not folded into “SOL meme” bucket |

**`regime_gate_key`:** Stable hash or canonical string derived from the **platform-regime slice** used for S1 stratification in EXP-004 / EXP-005 — must include at minimum **`stage` + `quote` (verified or assumed)** and **`instr` resolved or explicitly `unknown_until_rpc`** when present on the sealed row. Scout owns the encoder; Proof rejects measures that merge incompatible platform regimes.

**Out of v0 platform slice (unchanged parked law):** bonk/mayhem reclass expansion; external `market=external` spine; Dexscreener/Birdeye-derived tags; trading API `pool` strings as regime truth (routing ≠ on-chain venue per enum §5).

---

## 2. Hypothesis (taxonomy + attach law — falsifiable, not yet measured)

| Field | Content |
| --- | --- |
| **Claim** | Stamping the four **platform-regime** dimensions on every sealed `ingest_hot` create row — with **knowable-at-T** evidence rules and explicit `unverified` / `unknown_until_rpc` flags — yields a **finite, enumerable** label space such that (a) **mis-label rate** on stratified samples stays within EXP-001 / DEC-003 review bands, and (b) downstream sealed measures (**Graph, Scout L3, future fill-sim**) can **mandatorily stratify** by `regime_gate_key` without **future leakage** or silent cross-regime merges. |
| **What it is not** | Live program upgrade oracle; fee lift invention; graduation timing prediction; evaluate retune; Discovery promotion; densified marks for power. |
| **Knowable-at-T** | Labels on the sealed packet at ingest **T** use only WS fields + RPC reads completed **≤ T** (or async enrich as **new** append-only rows per matrix — never in-place rewrite of hot ingest). Marks remain **outcomes only** (`T < t_mark ≤ T+H`). |
| **Scout owns labels** | Scout defines enum keys, WS→field map updates, and `regime_gate_key` composition. |
| **Proof enforces respect** | Any sealed measure report without per-`regime_gate_key` reads when population spans multiple platform regimes → **method violation** (same spirit as **K-S1-collapse** in EXP-004 / EXP-005). |
| **Windows (when measures authorized)** | **1s / 5s / 15s / 30s / 60s** — primary read **60s** (family contract). Taxonomy registration itself has **no** outcome windows. |
| **Population** | Bonding `ingest_hot` creates on sealed courier days; bonk/mayhem **parked** (S5). **Full detect book** ([DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)). |

### Attach law v0 (sealed observe rows — **not implemented** in this PR)

| Step | Rule |
| --- | --- |
| **Hot create** | Non-empty `regime_id` per DEC-003 minimum components + platform keys in §1 when knowable; else explicit `*_unverified` / `unknown_until_rpc`. |
| **Enrich** | Optional `type=regime_enrich` append-only rows refine `instr`, `fee`, `quote` — **never** replace the sealed ingest line used as unit for mislabel audits. |
| **Gate key** | `regime_gate_key` stamped on decode/precompute features used by EXP-004 / EXP-005 — derived from platform slice, not from marks or post-T buyers. |
| **Stratify** | All authorized measures report priced_n and gates **per `regime_gate_key`** when `regime_gate_keys_n > 1`. |

---

## 3. Kill / falsifiers (gates only — no invented lift thresholds)

| ID | Worthless / kill if… |
| --- | --- |
| **K-platform-mislabel** | Stratified sample hard-disagree rate on any **platform-regime** key in §1 exceeds **1%** (DEC/enum review) or **5%** (wiring block) — same bands as [EXP-001](EXP-001-regime-stage-mislabel.md). |
| **K-leak** | Any label uses RPC/enrich with timestamp **> T**, or uses horizon marks / post-T tape to set platform regime at create **T**. |
| **K-merge** | Sealed measure pools rows across distinct `quote` or `instr` lineages without documented gate — **S1 / platform collapse** forbidden. |
| **K-blank** | `fee`, `quote`, or `instr` left empty without an explicit unverified token — violates DEC-003 non-empty law. |
| **K-upgrade-unstamped** | Observed on-chain program behavior change (new default quote, new graduation venue) with **no** new enum value + `first_observed_at` stamp in lab memory — silent relabel. |
| **K-measure-skip** | Oracle or local sealed measure ships aggregate-only outcomes when `regime_gate_keys_n > 1` — Proof **GATE** fail. |
| **K-watch-promote** | Any report treats **DIRECTIONAL_WATCH** (EXP-005 / EXP-005b) as cleared by “better regime tags” without a new authorized measure. |

**Primary read (when authorized):** Platform-dimension mislabel audit on sealed **2026-09-20/21** create book + proof that EXP-004 / EXP-005 stratification blocks are **replayable** with expanded platform keys — **not** outcome lift.

---

## 4. Proposed sealed measure plan (**not executed**)

| Step | What | Not |
| --- | --- | --- |
| 1 | Day-aligned sealed JSONL only: `observe-2026-09-{20,21}.jsonl` | Cross-day join |
| 2 | Inventory coverage: % creates with each §1 key populated vs `unverified` | Invent fee bps or graduation SOL constants |
| 3 | Extend or sibling mislabel CLI vs independent RPC/WS reconstruction (knowable-at-T) | Use marks as ground truth |
| 4 | Replay EXP-004 / EXP-005 **stratification tables** with platform-expanded `regime_gate_key` — attribution only | Retune L3 or Graph arms |
| 5 | Gates: **K-platform-mislabel**, **K-leak**, **K-merge**, **K-measure-skip** | Densify marks for power |
| 6 | Host reports under `/var/lib/mal/paper/_exp007-oracle-2026-09-{20,21}_*` (gitignored) | Committing host means |

**Authorization:** **None** for this PR. Future Oracle or observe-wiring land requires **explicit Helm/Vaan sealed-measure re-auth** (docs merge **≠** authorize-run).

**Still out of scope:** ordinal / **NH-G3a** / **NH-Index** / **EXP-006** fill-sim **implementation**; **H-G2 revive**; evaluate promotion; Fast mode; X account-quality brief; mirror-wallet.

---

## 5. Soft locks (council law)

| Lock | Rule |
| --- | --- |
| **Proposed, not run** | This file is registration — **no scored stamp** |
| **Scout owns labels** | Enum + attach law changes go through Scout seat + git SoT |
| **Proof enforces respect** | Measures must stratify / gate by platform `regime_gate_key` when applicable |
| **No invented lift** | Gates and taxonomy only in repo; host reports gitignored |
| **No EXP-002c retune** | v2 runners remain **read-only** reference |
| **No Discovery promotion** | Soft watches ≠ decode or evaluate promote |
| **Paper-only** | No live capital, no trading API keys on host |
| **No densify** | S6 / EXP-003 / EXP-002c law |
| **EXP-006 parked** | Fill-sim harness registration unchanged — **no** measure/CLI in EXP-007 PR |
| **Parent watches soft** | EXP-005 / EXP-005b **DIRECTIONAL_WATCH** — not promote paths |
| **Parked lanes** | Ordinal / **NH-G3a** / **NH-Index** / external paper broker / **H-G2 revive** / X account-quality — **parked** |
| **Graph / Scout seats** | EXP-004 / EXP-004b / EXP-005 / EXP-005b ownership unchanged |
| **Merge ≠ authorize run** | Git merge of EXP-007 registration **does not** authorize label encoder land or Oracle audit |

---

## 6. Cross-links

- Platform enum (detail): [ARTIFACTS/REGIME-ENUM-V0.md](../ARTIFACTS/REGIME-ENUM-V0.md)
- Regime-at-ingest matrix: [ARTIFACTS/REGIME-AT-INGEST-MATRIX.md](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md)
- Encoding: [DEC-004](../DEC/DEC-004-regime-id-encoding.md); working law [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md)
- Prior mislabel tooling: [EXP-001-regime-stage-mislabel.md](EXP-001-regime-stage-mislabel.md)
- S1 stratification exemplars: [EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md), [EXP-005-smart-wallet-follow-discovery-v0.md](EXP-005-smart-wallet-follow-discovery-v0.md)
- Paper harness (separate lane): [EXP-006-paper-would-have-happened-harness-v0.md](EXP-006-paper-would-have-happened-harness-v0.md)
- Observe schema: [ARTIFACTS/OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)
- Lab: [LAB_STATE.md](../LAB_STATE.md)
- Manager: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Registry: [EXP/README.md](README.md)

---

## 7. Result / conclusion

**Result:** **N/A** — **Proposed, not run.** No Oracle CLI exit code; no host artifacts.

**Conclusion:** EXP-007 registers **platform-regime taxonomy v0** — bonding-curve version, fees, graduation, pair asset as **first-class labels** on sealed observe rows, with Scout ownership, Proof enforcement, and falsifiers for a **future** sealed audit. Implementation and scoring require **follow-on Helm re-auth** after this docs merge. Scout soft PASS pending; **Proof GATE** before any promotion language.
