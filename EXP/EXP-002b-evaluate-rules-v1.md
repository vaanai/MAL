# EXP-002b — Evaluate→runner rules v1 (stricter paper book)

Council lock (Proof / Scout / Helm). Same sealed detect book, horizons, and kill gates as [EXP-002](EXP-002-evaluate-runner-v0.md); **evaluate rules v1** replaces vacuous v0 defaults so local runs produce a real **reject** cohort (~**70–95%** reject, **5–30%** runners — calibrate on Vaan JSONL).

| Field | Value |
| --- | --- |
| **ID** | `EXP-002b` |
| **Status** | **FAIL closed** (Proof scored with marks). |
| **Owner seat** | Proof |
| **Locked** | 2026-09-21 (rules v1 defaults + CLI `--rules v1`) |
| **Depends on** | [EXP-002](EXP-002-evaluate-runner-v0.md) pipeline + kill machinery; population unchanged |
| **Hypothesis** | A **stricter rules-only** filter on knowable-at-T sealed creates still yields a **runner** cohort with **gross** lift vs random same-n at primary horizon, while **reject** returns **differ** from runners (selection-bias falsifier). |
| **Method** | Full bonding-create book → **rules v1** → paper stamp at `t_ws` → same horizon marks as EXP-002. CLI: `python -m tools.exp002_paper_runner --rules v1` (archive; default CLI is v2 / [EXP-002c](EXP-002c-rules-v2-adverse-selection.md)). |
| **As-of-T** | Sealed row at `t_ws` only: top-level create fields + `ws_payload` + `knowable_at_t` honesty. **No** RPC, enrich rows, JEV, LLM, or future marks in evaluate. |
| **Windows** | Same as EXP-002; **primary kill horizon: 60s** |
| **Kill-attempt** | Unchanged from EXP-002 §8 (no lift vs random; reject≈runner parity ≤5% relative on gross when priced_n≥10 per arm; else **INCOMPLETE**) |
| **Result** | ~**81%** reject; runner **60s** mean ~**3%** vs random ~**20–30%** on marked subsample; parity **PASS** |
| **Conclusion** | **FAIL_NO_LIFT_VS_RANDOM** — do **not** promote v1; follow [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) |

---

## 1. Why EXP-002b exists

**EXP-002 local run (rules v0):** population **32,896** bonding creates; **100%** labeled `runner` (**0** rejects). Kill gates: **no_lift FAIL**, parity **INCOMPLETE**, overall **INCOMPLETE**. Formal stamp **INCOMPLETE** — tooling validated, **vacuous filter**; do **not** promote v0 as an alpha hypothesis test.

**EXP-002b** keeps the same book and paper machinery but applies **rules v1** so reject/runner arms are thick enough to exercise parity and lift gates.

---

## 2. Population (unchanged)

Identical to [EXP-002 §2](EXP-002-evaluate-runner-v0.md#2-population): `ingest_hot` bonding creates with valid `t_ws`; migrations excluded.

---

## 3. Evaluate rules v1 (defaults)

Implemented in `tools/exp002_paper_runner.py` as `EvaluateRulesV1`. Reproduce with `--rules v1`; outputs prefix `_exp002b`.

| Rule | Default | Reject reason key | Rationale (knowable at T) |
| --- | --- | --- | --- |
| **Bonding + identity + honesty** | ON | (same as v0) | Pipeline integrity; no lookahead |
| **Bonk-pool soft exclude** | **ON** | `bonk_pool_excluded` | Bonk-launchpad / name heuristics on sealed `pool`, payload platform fields, `regime_id`, name+symbol — reduces non-Pump.fun-ish noise |
| **Metadata present** | **ON** | `missing_metadata_name` / `symbol` / `uri` | Empty or missing metadata correlates with spam / broken launches |
| **Zero creator buy** | **ON** (if field present) | `zero_sol_amount`, `zero_initial_buy` | Create spam with no skin-in-game |
| **Creator SOL band** | **0.5 – 2.5** SOL | `below_min_sol_amount`, `above_max_sol_amount` | Hypothesis: very low or very high initial `solAmount` is not the microstructure edge under test |
| **Initial buy token band** | **≥ 1** (when present) | `below_min_initial_buy`, `above_max_initial_buy` | Drop dust buys; cap absurd token amounts |
| **marketCapSol band** | **28 – 34** SOL | `missing_market_cap_sol`, below/above | Pump.fun creates cluster near ~30 SOL; narrow band keeps a small runner slice |
| **vSolInBondingCurve band** | **29 – 33.5** SOL | `missing_v_sol_in_bonding_curve`, below/above | Reserve snapshot consistency with cap band |
| **Pass** | — | — | Label **`runner`** when no reject reasons |

**Calibration:** After first local run, inspect `evaluate_reasons` histogram in `_exp002b_book.jsonl`. Target **70–95%** reject; adjust bands via PR (documented constants in `RULES_V1_DEFAULTS`) — not silent drift.

**CLI overrides:** `--include-bonk-pool`, `--exclude-bonk-pool`, `--min-market-cap-sol` (min cap only). Other v1 knobs: code constants until a follow-on flag EXP.

---

## 4. Runner, horizons, kill criteria

Same as [EXP-002 §5–8](EXP-002-evaluate-runner-v0.md). `Δ_exec` remains **N/A** from JSONL alone.

---

## 5. Local CLI runbook

### PowerShell (Windows, repo root)

```powershell
Set-Location C:\Users\vivaa\dev\MAL
python -m tools.exp002_paper_runner `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --rules v1 `
  --seed 1 `
  --output-dir data\observe `
  --prefix _exp002b
```

(Prefix defaults to `_exp002b` when `--rules v1`.)

### WSL / Linux / macOS

```bash
cd /path/to/MAL
python3 -m tools.exp002_paper_runner \
  data/observe/observe-2026-09-20.jsonl \
  data/observe/observe-2026-09-21.jsonl \
  --rules v1 \
  --seed 1 \
  --output-dir data/observe \
  --prefix _exp002b
```

**Re-run vacuous v0 (archive / tooling only):**

```bash
python3 -m tools.exp002_paper_runner ... --rules v0 --prefix _exp002
```

**Tests:**

```bash
python3 -m unittest tools.test_exp002_paper_runner
```

**Exit codes:** `0` PASS; `2` FAIL_NO_LIFT or FAIL_SELECTION_BIAS; `3` INCOMPLETE; `1` missing files.

---

## 6. Result / conclusion

| Field | Value |
| --- | --- |
| **Result** | Scored on Vaan sealed JSONL + EXP-003 marks (300-create RPC subsample). |
| **Conclusion** | **FAIL_NO_LIFT_VS_RANDOM** (adverse runner selection). Denser marks are **not** the fix. |
