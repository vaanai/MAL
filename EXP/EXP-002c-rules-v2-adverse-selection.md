# EXP-002c — Evaluate rules v2 (anti-adverse-selection)

Council lock (Proof / Scout / Helm). Same sealed detect book, horizons, marks join, and kill gates as [EXP-002](EXP-002-evaluate-runner-v0.md). **Evaluate rules v2** replaces [EXP-002b](EXP-002b-evaluate-rules-v1.md) **rules v1** after a scored **FAIL_NO_LIFT_VS_RANDOM** on the marked subsample.

| Field | Value |
| --- | --- |
| **ID** | `EXP-002c` |
| **Status** | Tooling ready. Local scoring pending (reuse EXP-003 marks JSONL). |
| **Owner seat** | Proof |
| **Locked** | 2026-09-22 (rules v2 defaults + CLI `--rules v2`) |
| **Depends on** | EXP-002 pipeline; EXP-003 side marks; EXP-002b **FAIL closed** (do not promote v1) |
| **Hypothesis** | **Rules v1 adversarially selects** a thin “sweet-spot” cohort (tight `marketCapSol` / `vSolInBondingCurve` / `solAmount` bands) whose **60s gross** returns are **worse than random** same-n, while parity vs rejects can still pass. A **smaller rules-only v2** that **drops** those band gates and keeps integrity floors still yields a runner slice with **non-adverse** directional lift vs random (kill-attempt), without denser marks. |
| **Method** | Full bonding-create book → **rules v2** → paper stamp at `t_ws` → same horizon marks as EXP-002 (`--marks` from RPC subsample). CLI: `python -m tools.exp002_paper_runner --rules v2` (prefix `_exp002c`). |
| **As-of-T** | Sealed create row at `t_ws` only (top-level + `ws_payload` + `knowable_at_t`). No RPC at evaluate, no JEV/LLM, no mark prices in evaluate. |
| **Windows** | Same as EXP-002; **primary kill horizon: 60s** |
| **Kill-attempt** | Unchanged: no lift vs random; reject≈runner parity ≤5% relative on gross when priced_n≥10 per arm; else **INCOMPLETE** |
| **Result** | Pending local re-run on Vaan JSONL + existing `marks-*.jsonl` |
| **Conclusion** | Pending |

---

## 1. Why EXP-002c exists (002b closed)

**EXP-002b (rules v1, Proof run with marks):**

| Metric | Value |
| --- | --- |
| Reject rate | ~**81%** |
| Runner **60s** mean (priced subsample) | ~**3%** gross |
| Random same-n **60s** mean | ~**20–30%** gross |
| Parity vs reject | **PASS** (arms differ) |
| **no_lift_vs_random** | **FAIL** |
| **Overall** | **FAIL_NO_LIFT_VS_RANDOM** |

**Do not promote** rules v1 or treat **denser marks** as the fix for adverse selection. Marks were honest (`T < t_mark ≤ T+H`); the evaluate filter selected a losing runner cohort.

**Soft bonk/mayhem** heuristics stay **parked** — no new launchpad knobs in v2.

---

## 2. Rules v1 kill-list (drop / invert / replace)

| v1 feature | Verdict | v2 action |
| --- | --- | --- |
| Narrow **marketCapSol** band **28–34** | **DROP** | Floor only (`min_market_cap_sol`); no ceiling sweet-spot |
| Narrow **vSolInBondingCurve** band **29–33.5** | **DROP** | Not used for pass/fail (reserve snapshot not a runner gate) |
| **max_sol_amount** 2.5 | **DROP** | High creator SOL not auto-rejected |
| **max_initial_buy** cap | **DROP** | Absurd-token cap removed; dust floor kept |
| **min_sol_amount** 0.5 | **REPLACE** | Lower floor **0.25** SOL (skin-in-game without v1’s hot-band coupling) |
| Zero-buy reject | **KEEP** | Still reject `zero_sol_amount` / `zero_initial_buy` when fields present |
| Metadata required | **KEEP** | `name` / `symbol` / `uri` |
| Bonk-pool soft exclude | **KEEP** (no expansion) | Same heuristic as v1; `--include-bonk-pool` override only |
| Identity + `knowable_at_t` honesty | **KEEP** | Base pipeline integrity |
| Missing `marketCapSol` | **KEEP** | `missing_market_cap_sol` |
| — | **ADD** | `missing_price_proxy` if both `marketCapSol` and `vSolInBondingCurve` absent |
| — | **ADD** | `above_extreme_market_cap_sol` — reject only **very high** create cap (default **> 50** SOL), inverse of v1’s “must sit in hot band” |

---

## 3. Rules v2 explicit set (defaults)

Implemented as `EvaluateRulesV2` in `tools/exp002_paper_runner.py` (`RULES_V2_DEFAULTS`).

| Rule | Default | Reject reason |
| --- | --- | --- |
| Bonding + identity + honesty | ON | (same as v0/v1) |
| Bonk-pool soft exclude | ON | `bonk_pool_excluded` |
| Metadata | ON | `missing_metadata_*` |
| Zero creator buy | ON | `zero_sol_amount`, `zero_initial_buy` |
| Creator SOL floor | **≥ 0.25** | `below_min_sol_amount` |
| Initial buy dust floor | **≥ 1** | `below_min_initial_buy` |
| marketCapSol floor | **≥ 20** | `below_min_market_cap_sol`, `missing_market_cap_sol` |
| Extreme high cap at create | **> 50** SOL | `above_extreme_market_cap_sol` |
| Price proxy present | ON | `missing_price_proxy` |
| **Pass** | — | Label **`runner`** when no reject reasons |

**Calibration target:** **70–95%** reject on full book (adjust floors via PR constants, not silent drift). After first v2 run, inspect `evaluate_reasons` in `_exp002c_book.jsonl`.

**Calibration plan (Vaan):**

1. Re-run with **existing** `--marks` from the **same ~300 create RPC subsample** (no new mark density).
2. If overall is **not** adverse (directional non-FAIL on lift) but priced_n is thin → Proof may request denser marks for **power only**, not to “fix” v1 selection.

---

## 4. Local CLI runbook

```bash
cd /path/to/MAL
python3 -m tools.exp002_paper_runner \
  data/observe/observe-2026-09-20.jsonl \
  data/observe/observe-2026-09-21.jsonl \
  --rules v2 \
  --marks data/observe/marks-….jsonl \
  --seed 1 \
  --output-dir data/observe \
  --prefix _exp002c
```

Archive **v1** (002b reproduction only):

```bash
python3 -m tools.exp002_paper_runner ... --rules v1 --prefix _exp002b
```

**Tests:** `python3 -m unittest tools.test_exp002_paper_runner`

**Exit codes:** `0` PASS; `2` FAIL_NO_LIFT or FAIL_SELECTION_BIAS; `3` INCOMPLETE; `1` missing files.

---

## 5. Result / conclusion

| Field | Value |
| --- | --- |
| **Result** | _Pending — local CLI with sealed JSONL + existing marks._ |
| **Conclusion** | _Pending. Does not authorize live capital or promote v1._ |
