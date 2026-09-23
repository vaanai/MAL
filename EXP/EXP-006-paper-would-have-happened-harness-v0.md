# EXP-006 — Paper “would-have-happened” fill harness v0

> **Status: INCOMPLETE (scored stamp)** — Oracle sealed paper fill-sim **2026-09-23** (Helm/Vaan **authorized run 2026-09-23 ~10:08 PT**). Scout soft PASS pending; **Proof GATE** required before promotion language. **Merge ≠ Discovery promote.**

| Field | Value |
| --- | --- |
| **ID** | `EXP-006-paper-would-have-happened-harness-v0` |
| **Owner seat** | **Scout + Proof** (paper book + execution realism above marks) |
| **Source** | [PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md) **P0** — in-lab fill-sim on sealed JSONL + marks |
| **Status** | **INCOMPLETE (scored)** — P0 CLI landed; Oracle **2026-09-20/21** sealed measure stamped; **no Discovery promote** |
| **Paper-only** | Yes — no live keys, no trading API, no broker on `mal-core-0` |
| **Pick lock** | **Helm/Vaan 2026-09-23** — unpark fill-sim for EXP-006; sealed paper would-have-happened on courier days **2026-09-20** and **2026-09-21** only |
| **Depends on** | Day-aligned sealed courier: `observe-2026-09-{20,21}.jsonl` + matching `marks-*.jsonl` — **no cross-day marks join** |
| **CLI** | [`tools/exp006_paper_fill_sim.py`](../tools/exp006_paper_fill_sim.py) — `python -m tools.exp006_paper_fill_sim` |

---

## 0. Honesty — watch lists ≠ promote

Parent soft watches on **2026-09-20/21** (unchanged):

| Stamp | Status | EXP-006 use |
| --- | --- | --- |
| [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) **L3_PACKET_V0** | **DIRECTIONAL_WATCH** | Optional attribution cohort **L3_full** only |
| [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) **L3_minus_v2** | **DIRECTIONAL_WATCH (residual)** | **Primary** paper cohort |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) **EvaluateRulesV2** runners | **INCOMPLETE closed** | **Reference** cohort **v2_runner** — no retune |

**EXP-006 does not** promote L3, residual, or v2 runners; does not clear EXP-002c adverse lift; does not densify marks or join cross-day.

---

## 1. Hypothesis (paper harness)

| Field | Content |
| --- | --- |
| **Claim** | Knowable-at-T **P0 fill-sim** on soft-watch cohorts separates **marks-only gross** from **Δ_exec-aware** paper outcomes on the same sealed book. |
| **Fill model v0 (implemented)** | `t_fill = t_decision + 500ms` (**documented constant**); price = last mark with `t_mark ≤ t_fill`; **0.1 SOL** notional; `fee_model_id=pump_assumed_bps_v0` @ **125 bps**; curve slip proxy + **500 bps** cap / optional `sim_reject`; horizons **1s…60s** anchored at **t_decision** (EXP-003 law). |
| **Side stream** | Append-only `paper_fill` JSONL (`paper_fill_v0`) — gitignored courier paths |

---

## 2. Kill / falsifiers (gates)

| ID | Result this run |
| --- | --- |
| **K-fill-leak** | **PASS** (audit) — fill ticks `t_mark ≤ t_fill` |
| **K-eval-drift** | **PASS** — evaluate packet / v2 / L3 rules unchanged |
| **K-lift-rescue** | **Mixed** — primary **L3_minus_v2** **FAIL** @60s **2026-09-20**; **PASS** **2026-09-21** (relative ε=0.5% documented) |
| **K-cross-day** | **PASS** — independent day runs only |
| **K-watch-promote** | **PASS** (process) — stamp is not promote |
| **K-thin-cohort** | **PASS** @60s primary (priced_n **14** / **11** ≥ floor **10**) |
| **K-v2-adverse** | **PASS** (process) — no claim to clear EXP-002c **FAIL** |
| **K-S1-collapse** | **not triggered** — single `regime_gate_key` on book; collapse falsifier **false** both days |

**Primary read:** **60s** **L3_minus_v2** — marks-only vs paper **Δ_exec**; fill rate **100%** (all cohort rows got a mark at or before `t_fill` on these days).

---

## 3. Sealed measure (executed)

| Step | What |
| --- | --- |
| 1 | Oracle `mal-core-vnic` @ `/var/lib/mal/sealed/jsonl/observe-2026-09-{20,21}.jsonl` + `marks-*.jsonl` |
| 2 | Watch cohorts: **L3_minus_v2**, **v2_runner**, **L3_full** (attribution) |
| 3 | CLI + gitignored `paper_fill` side records under `/var/lib/mal/paper/_exp006-oracle-2026-09-{20,21}_*` |
| 4 | No invented lift tables in git — gate taxonomy + priced_n / fill rates below |

**Authorization:** Helm/Vaan **2026-09-23** explicit fill-sim unpark (this PR). Registration merge **≠** authorize-run alone.

---

## 4. Soft locks (held)

Paper-only; no live keys; no cross-day join; no densify; no EXP-002c retune; no Discovery promote; ordinal / NH-G3a / NH-Index / H-G2 **parked**; EXP-007 untouched.

---

## 5. Cross-links

- [PAPER-TRADING-SURFACE-BRIEF.md](../ARTIFACTS/PAPER-TRADING-SURFACE-BRIEF.md)
- [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) / [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md)
- [LAB_STATE.md](../LAB_STATE.md) · [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md) · [EXP/README.md](README.md)

---

## 6. Result / conclusion (Oracle sealed paper measure 2026-09-23)

**Host:** `mal-core-vnic` — inputs `/var/lib/mal/sealed/jsonl/`; artifacts `/var/lib/mal/paper/_exp006-oracle-2026-09-{20,21}_*` (summary JSON, report MD, `paper_fills.jsonl`). **Repo CLI:** `python -m tools.exp006_paper_fill_sim`.

**Cross-day overall:** **INCOMPLETE** — primary **L3_minus_v2** **K-lift-rescue FAIL** on **2026-09-20** @60s; **PASS** on **2026-09-21**; sparse marks → high-variance means (host JSON only — **not** lift proof).

### Primary cohort **L3_minus_v2** @60s

| Day | CLI exit | day overall | cohort_n | fill_rate | priced_n @60s (marks / paper Δ_exec) | K-thin-cohort | K-lift-rescue @60s | K-fill-leak |
| --- | ---: | --- | ---: | ---: | ---: | --- | --- | --- |
| 2026-09-20 | 1 | **FAIL** | 1175 | 1.0 | **14** / **14** | PASS | **FAIL** | PASS |
| 2026-09-21 | 0 | **DIRECTIONAL_NON_KILL** | 1380 | 1.0 | **11** / **11** | PASS | PASS | PASS |

### Reference **v2_runner** @60s (EXP-002c read-only)

| Day | cohort_n | priced_n @60s | K-lift-rescue @60s |
| --- | ---: | ---: | --- |
| 2026-09-20 | 5781 | 44 | PASS |
| 2026-09-21 | 6281 | 52 | PASS |

### Attribution **L3_full** @60s

| Day | cohort_n | priced_n @60s | K-lift-rescue @60s |
| --- | ---: | ---: | --- |
| 2026-09-20 | 2325 | 22 | **FAIL** |
| 2026-09-21 | 2578 | 18 | PASS |

### Horizons (primary cohort priced_n — marks-only vs paper Δ_exec priced_n)

| Day | 1s | 5s | 15s | 30s | 60s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-20 | 9 / 9 | 12 / 12 | 12 / 12 | 13 / 13 | 14 / 14 |
| 2026-09-21 | 1 / 1 | 10 / 10 | 11 / 11 | 11 / 11 | 11 / 11 |

(Each cell: marks-only priced_n / paper Δ_exec priced_n — same rows when fill **filled**.)

### Limitations (honest)

- **Assumed fees** (`pump_assumed_bps_v0`) — no on-chain `feeConfig` snapshot in this run.
- **Fill rate 100%** — every watch-list row had ≥1 mark at or before `t_fill`; unfilled / `sim_reject` rates **0** on these days (not universal law).
- **K-lift-rescue** on **2026-09-20** flags paper Δ_exec **above** marks-only on thin **L3_minus_v2** — treat as **audit / sparse-mark variance**, not execution alpha or promote.
- **EXP-002c** adverse-selection **FAIL** remains; paper layer does not rescue evaluate.

**Conclusion:** P0 fill-sim **implemented and sealed** on courier **2026-09-20/21**. Primary stamp **INCOMPLETE** cross-day due to **K-lift-rescue** split + sparse horizons at 1s. **No Discovery promote.** Scout soft PASS pending; **Proof GATE** before any promotion wording.
