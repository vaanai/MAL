# EXP-006 — Paper “would-have-happened” fill harness v0 (Proposed, not run)

> **Status: Proposed, not run** — **Registration only** (Helm/Vaan **authorized docs-only** Discovery next pick **2026-09-23**). No Oracle sealed measure; no CLI implementation in this PR. Scout soft PASS pending; **Proof GATE** required before any scored stamp or promotion claim. **Merge ≠ authorize-run.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-006-paper-would-have-happened-harness-v0` |
| **Owner seat** | **Scout + Proof** (paper book + execution realism above marks) |
| **Source** | [PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md) **P0** — in-lab fill-sim on sealed JSONL + marks |
| **Status** | **Proposed, not run** — hypothesis + harness scope + watch-list cohort law only |
| **Paper-only** | Yes — no live keys, no trading API, no broker on `mal-core-0`, no evaluate promotion from registration |
| **Pick lock** | **Helm/Vaan 2026-09-23** — Discovery next: **paper would-have-happened harness** registration (this PR). Scout L3 / Graph sealed stamps stay **EXP-005** / **EXP-005b** / **EXP-004** / **EXP-004b** — do **not** reassign. |
| **Depends on** | Same **day-aligned sealed courier** spine as EXP-004 / EXP-005 / EXP-005b: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` only — **no cross-day marks join** (e.g. 20/21 marks on `observe-2026-09-23`) |
| **CLI** | **None in this PR** — future `tools.paper_fill_sim` or extension of `exp002_paper_runner` requires **separate Helm re-auth** after docs merge |

---

## 0. Honesty — what Discovery already stamped (watch lists ≠ promote)

Parent Scout sealed measures on **2026-09-20/21** courier days (Oracle **2026-09-23**):

| Stamp | Status | Read for EXP-006 |
| --- | --- | --- |
| [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) **L3_PACKET_V0** | **DIRECTIONAL_WATCH** | Soft watch only — sep+random **PASS** @60s both days; **~46–50%** L3∩EXP-002c **v2 runners** (**K-L3-cohort** / **M2**) |
| [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) **L3_minus_v2** | **DIRECTIONAL_WATCH (residual)** | Soft watch only — primary residual sep+random **PASS** @60s both days; **thin** residual priced_n (**14** / **11**); **L3_intersect_v2** **INCOMPLETE** |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) **EvaluateRulesV2** runners | **INCOMPLETE closed** | **Cohort reference only** — adverse lift **FAIL**; **no retune**; v2 runner signatures define overlap subtraction, not a promote path |

**EXP-006 does not:** restate Graph **EXP-004** / **EXP-004b** arm stamps; revive ordinal / **NH-G3a** / **NH-Index** / **H-G2** burst; unpark external paper brokers; authorize Discovery decode promotion; or claim that residual **DIRECTIONAL_WATCH** clears evaluate failure.

**Harness role:** On the **same sealed days**, replay **paper fill-surface** “would-have-happened” outcomes (latency, fees, slippage, optional sim reject) for **soft-watch candidate cohorts only** — compare **Δ_exec-aware** paper PnL vs **marks-only gross** runners — **without** changing evaluate packet or v2 constants.

**Parked (optional):** Host **attestation** gap for forward `L_ms` / fail-rate calibration — same defer as parent EXP-005 paper law; no new feeds in registration PR.

---

## 1. Hypothesis (paper harness — falsifiable, not yet measured)

| Field | Content |
| --- | --- |
| **Claim** | A **knowable-at-T** **P0 fill-simulator** ([PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md) §Recommended day-1) applied to **soft-watch candidate cohorts** on sealed courier days can produce **honest would-have-happened** fill stamps (`t_fill`, fees, slippage, optional `sim_reject`) such that **marks-only gross** runner reads are **not** mistaken for **Δ_exec-aware** paper outcomes — without rescuing EXP-002c lift or promoting L3 decode. |
| **What it is not** | Live PumpPortal / Axiom / wallet mirror; densified marks for power; EXP-002c retune; Discovery promotion; mirror-wallet L3; Graph arm revival. |
| **Candidate cohorts (watch list only)** | **(A)** EXP-002c **v2 runner** signatures on the day-aligned book (**reference** — not “promote runners”). **(B)** EXP-005b primary **L3_minus_v2** residual signatures (**soft-watch residual** — not “promote L3”). Optional **(C)** full **L3_PACKET_V0** for continuity with parent EXP-005 — **attribution only**, same non-promote law. |
| **Knowable-at-T** | Evaluate/decode inputs unchanged at **T** ([DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)). Fill inputs: ticks/account snapshots with `t_obs ≤ t_fill`; horizon marks unchanged (`T < t_mark ≤ T+H`). Fee reads ≤ `t_fill`. |
| **Windows** | **1s / 5s / 15s / 30s / 60s** — primary read **60s** (same contract as EXP-004 / EXP-005 family). |
| **Population** | Bonding `ingest_hot` creates on **each sealed courier day** independently; bonk/mayhem **parked**. **Full detect book** labels preserved ([DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)). |

### Proposed fill model v0 (P0 law — from paper brief; **not implemented** here)

| Step | Rule |
| --- | --- |
| **Intent** | `t_intent` = sealed `t_decision` (or documented `decode_latency_ms` when measured — **not** fitted to lift). |
| **Fill time** | `t_fill` = `t_intent + L_ms` — **documented constant or distribution**; calibration requires separate Helm approval (may use Owner-local micro-live — **parked**). |
| **Price @ fill** | Last `outcome_mark` or trade tick with `t_mark ≤ t_fill`; if none → `status=unfilled`. |
| **Size** | Fixed paper notional in SOL (documented in future CLI EXP — **not** per-mint tuned). |
| **Fees / slip** | Pump protocol + creator bps + curve delta for `size_sol`; cap `max_slippage_bps`; stamp `fee_model_id` — no invented lift tables in git. |
| **Horizons** | Paper PnL at same mark windows; **Δ_exec** = gross − fees − slip penalty on **filled** rows only. |
| **Side stream** | Proposed `paper_fill` / `paper_order` side JSONL ([PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md) §Proposed side-record) — append-only, gitignored courier model. |

---

## 2. Kill / falsifiers (gates only — no invented lift thresholds)

| ID | Worthless / kill if… |
| --- | --- |
| **K-fill-leak** | Any fill input uses `t_obs > t_fill` or horizon marks with `t_mark > T+H`. |
| **K-eval-drift** | Harness changes evaluate packet, v2 constants, or L3_PACKET_V0 rules to “help” fills. |
| **K-lift-rescue** | Δ_exec-aware paper outcomes **systematically improve** lift vs marks-only on the **same** watch cohort without an documented fee/latency model change — audit as **leakage or overfit** ([PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md) review trigger). |
| **K-cross-day** | Join marks or fills across courier days (forbidden courier law). |
| **K-watch-promote** | Any report treats **DIRECTIONAL_WATCH** or residual watch as **decode/evaluate promote**. |
| **K-thin-cohort** | Watch cohort `priced_n @60s` below EXP-004 floor (**10**) → **INCOMPLETE** (honest thin book — not a promote). |
| **K-v2-adverse** | Harness claimed to **clear** EXP-002c **no_lift_vs_random** **FAIL** without new hypothesis — **forbidden** (002c stays **INCOMPLETE closed**). |
| **K-S1-collapse** | Apparent fill effect vanishes under every `regime_gate_key` split when stratification is run. |

**Primary read (when authorized):** **60s** paper Δ_exec vs marks-only gross on **L3_minus_v2** and **v2 runner** watch cohorts, **per sealed day**, regime-split mandatory.

---

## 3. Proposed sealed measure plan (**not executed**)

| Step | What | Not |
| --- | --- | --- |
| 1 | Day-aligned sealed JSONL only: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` | Cross-day join |
| 2 | Build watch-list signature sets: **v2 runners** (read-only rules), **L3_minus_v2**, optional **L3_full** | Retune v2; change L3_PACKET_V0 |
| 3 | Run **P0 fill-sim v0** per [PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md); emit gitignored `paper_fill` side records | Live keys; PumpPortal trade API |
| 4 | Compare marks-only vs paper Δ_exec @ 1s…60s; report unfilled / `sim_reject` rates | Invented mean_return_pct in git |
| 5 | Gates: **K-fill-leak**, **K-lift-rescue**, **K-thin-cohort**, **K-S1-collapse** | Mark densify for power |
| 6 | Host reports under `/var/lib/mal/paper/_exp006-oracle-2026-09-{20,21}_*` (gitignored) | Committing host means |

**Authorization:** **None** for this PR. Future Oracle or Owner-local batch requires **explicit Helm/Vaan sealed-measure re-auth** (same law as EXP-005 / EXP-005b: docs merge **≠** authorize-run).

**Still out of scope:** ordinal / **NH-G3a** / **NH-Index** / fill-sim **implementation PR** (separate); **H-G2 revive**; evaluate promotion; Fast mode; mirror-wallet.

---

## 4. Soft locks (council law)

| Lock | Rule |
| --- | --- |
| **Proposed, not run** | This file is registration — **no scored stamp** |
| **No invented lift** | Gates and taxonomy only in repo; host reports gitignored |
| **No EXP-002c retune** | v2 runners = **read-only** watch-list reference |
| **No Discovery promotion** | Watch lists ≠ decode or evaluate promote |
| **Paper-only** | No live capital, no trading API keys on host |
| **Follow ≠ mirror (S3)** | L3 cohorts remain feature/veto/select — not mirror-wallet |
| **No densify** | S6 / EXP-003 / EXP-002c law |
| **H-G2 stay killed** | No burst lookback retune or H-G2 arm revival |
| **Parked lanes** | Ordinal / **NH-G3a** / **NH-Index** / external paper broker unpark / **H-G2 revive** — **parked** |
| **Graph / Scout seats** | EXP-004 / EXP-004b / EXP-005 / EXP-005b ownership unchanged |
| **Merge ≠ authorize run** | Git merge of EXP-006 registration **does not** authorize fill-sim measure or CLI land |

---

## 5. Cross-links

- Paper surface brief (P0 spec): [ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md)
- Parent L3 stamp: [EXP-005-smart-wallet-follow-discovery-v0.md](EXP-005-smart-wallet-follow-discovery-v0.md)
- Residual falsifier stamp: [EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md)
- Evaluate v2 reference: [EXP-002c-rules-v2-adverse-selection.md](EXP-002c-rules-v2-adverse-selection.md)
- Marks law: [EXP-003-post-create-marks.md](EXP-003-post-create-marks.md)
- Book law: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Lab: [LAB_STATE.md](../LAB_STATE.md)
- Manager: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Registry: [EXP/README.md](README.md)

---

## 6. Result / conclusion

**Result:** **N/A** — **Proposed, not run.** No Oracle CLI exit code; no host artifacts.

**Conclusion:** EXP-006 registers the **would-have-happened paper harness** scope for sealed **2026-09-20/21** courier days, anchored on **soft-watch candidates** (**EXP-002c v2 runners**, **EXP-005b L3_minus_v2**) — **not** Discovery promote paths. Implementation and sealed scoring require **follow-on Helm re-auth** after this docs merge. Scout soft PASS pending; **Proof GATE** before any promotion language.
