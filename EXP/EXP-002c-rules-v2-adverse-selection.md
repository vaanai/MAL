# EXP-002c — Evaluate rules v2 (anti-adverse-selection)

Council lock (Proof / Scout / Helm). Same sealed detect book, horizons, marks join, and kill gates as [EXP-002](EXP-002-evaluate-runner-v0.md). **Evaluate rules v2** followed a **DISCOVERY** pass on why [EXP-002b](EXP-002b-evaluate-rules-v1.md) **rules v1** failed, then a minimal **rules-only** v2 proposal. Local scoring is **INCOMPLETE closed** — **do not promote** v1 or v2; **do not** use denser marks to retune evaluate; **do not** OPTIMIZE-to-gate.

| Field | Value |
| --- | --- |
| **ID** | `EXP-002c` |
| **Status** | **INCOMPLETE closed** (Proof scored). **Do not promote.** |
| **Owner seat** | Scout (constraints) + Proof (kill gates) |
| **Locked** | 2026-09-22 (council hard constraints + rules v2 defaults). Scored local `--rules v2` + existing marks, seed 1 (tooling [PR #18](https://github.com/vaanai/MAL/pull/18) @ `842cb21`; 26/26 tests OK). |
| **Depends on** | EXP-002 pipeline; EXP-003 side marks; EXP-002b **FAIL closed** |
| **Hypothesis** | After removing v1’s adverse **sweet-spot** gates (and all **parked** bonk/mayhem/pool features), a **regime-aligned**, **knowable-at-T-only** v2 filter still yields a **70–95%** reject book with **non-adverse** runner lift vs random at **60s** (kill-attempt). |
| **Method** | **Full detect book** (every bonding create labeled; rejects keep outcomes) → **rules v2** → paper stamp at `t_ws` → marks as **outcome meter only**. CLI: `python -m tools.exp002_paper_runner --rules v2` (`_exp002c`). |
| **As-of-T** | Sealed create at `t_ws`: top-level fields, `ws_payload`, `knowable_at_t`, `regime_id`. **No** mark/outcome fields, post-T social, future liquidity, Dexscreener/Birdeye, or RPC-at-evaluate. |
| **Windows** | Same as EXP-002; **primary kill horizon: 60s** |
| **Kill-attempt** | **70–95%** reject rate on full book; **no_lift_vs_random**; reject↔runner **parity** (≤5% relative gross); priced_n≥10 per arm or **INCOMPLETE**; **no promote** on incomplete marks |
| **Result** | population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈**63.18%**. reject_rate_band 70–95%: **INCOMPLETE** (below 70% floor). Runner priced_n=96 mean_return_pct≈**0.385**; reject priced_n=888 mean≈**3202.76**; random priced_n=410 mean≈**2500.58**. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Overall **INCOMPLETE**. |
| **Conclusion** | Proof stamp **INCOMPLETE** (primary: reject_rate_band). Soft/directional lift **FAIL** again. **Do not promote.** Do **not** tighten constants to hit 70–95% (OPTIMIZE-to-gate). **DISCOVERY** first on dual failure (v2 too permissive; survivors still adverse vs random), then **EXP-002d** only with a **new kill-list hyp** — or **pause**. Soft denser marks still not the fix. |

---

## 0. Council hard constraints (Scout + Proof)

### Scout (evaluate feature law)

| # | Constraint |
| --- | --- |
| 1 | **Bonk/mayhem PARKED** — do **not** use `is_mayhem_mode`, `pool=bonk`, name/symbol bonk heuristics, or related WS unknowns as **selection** features in v2. (v1 bonk soft-exclude is **historical only**, not carried forward.) |
| 2 | **Regime-aware** — **gate** on sealed `stage=bonding` **and** `regime_id` containing `stage=bonding` (+ `market=bonding_curve` when present). **Stratify** in summary JSON by `regime_id` stage; no silent mix of bonding vs graduated populations. |
| 3 | **Knowable-at-T only** — no mark/outcome fields, no post-T social, no future liquidity as evaluate inputs. |
| 4 | **Full detect book** — every eligible create gets `runner` \| `reject` **and** horizon outcomes; rejects are never dropped for convenience. |
| 5 | **Dexscreener/Birdeye forbidden** on spine evaluate features (unchanged lab law). |
| 6 | **Marks = outcome meter only** — denser marks are **not** a retune lever for adverse v1; soft denser marks only **after** a new hypothesis scores. |
| 7 | **Social/X** out of scope unless already stamped knowable-at-T on sealed rows (none in v2). |

### Proof (kill + must-avoid)

| Item | Rule |
| --- | --- |
| Kill criteria | Unchanged: **70–95%** reject; **no_lift_vs_random**; reject↔runner parity; knowable-at-T only; **no promote** on **INCOMPLETE** marks/gates. |
| Must-avoid | Baking bonk/mayhem/pool into rules; **densifying marks** to rescue lift; **optimizing** v2 constants **before** documenting **DISCOVERY** on why v1 was adverse; **OPTIMIZE-to-gate** (tightening constants just to hit 70–95%) after v2 scored **INCOMPLETE**. |
| Marks cadence | Re-run with **existing** `--marks` first; denser marks only if a **non-adverse** directional result needs power. |

---

## 1. EXP-002b closed (evidence)

| Metric | Value |
| --- | --- |
| Reject rate | ~**81%** (inside 70–95% band) |
| Runner **60s** mean (priced subsample) | ~**3%** gross |
| Random same-n **60s** mean | ~**20–30%** gross |
| Parity vs reject | **PASS** |
| **no_lift_vs_random** | **FAIL** |
| **Overall** | **FAIL_NO_LIFT_VS_RANDOM** |

Parity **PASS** shows rejects and runners **differ** — the failure mode is **runner cohort underperformance vs random**, not “filter does nothing.” Marks join was honest (`T < t_mark ≤ T+H`).

---

## 2. DISCOVERY — why rules v1 was adverse (before v2 design)

**Order of work:** this section is **discovery**, not optimization. v2 constants are proposed only **after** attributing v1 failure modes.

### 2.1 Mechanism: conjunctive “launch median” sweet spot

v1 required **simultaneous** pass on:

- `marketCapSol ∈ [28, 34]`
- `vSolInBondingCurve ∈ [29, 33.5]`
- `solAmount ∈ [0.5, 2.5]`
- plus metadata, zero-buy, and (in v1) bonk soft-exclude

**Effect:** runners are not “high conviction creates” — they are creates that **look like the modal Pump.fun launch snapshot** at T. On the marked subsample, that modal slice **underperformed** a random same-n draw at **60s** (~3% vs ~20–30%). This is **adverse selection within the runner arm**, not missing marks.

### 2.2 What v1 reject reasons did *not* explain

- **Reject rate ~81%** — filter was active; gates were **evaluable**.
- **Parity PASS** — reject cohort mean ≠ runner mean; the book is not vacuous.
- **Lift FAIL** — conditional on passing v1, **remaining** runners were still the wrong side of random.

### 2.3 Parked features (not causal fix path)

| v1 knob | DISCOVERY note | v2 stance |
| --- | --- | --- |
| Bonk-pool / name heuristics | May skew population mix; **not** the documented sweet-spot mechanism for lift FAIL | **DROP** — bonk/mayhem **PARKED** per Scout |
| `is_mayhem_mode` / pool WS unknowns | Not reliable at T; forbidden as selection | **Never** in v2 |
| Narrow bands | **Primary adverse mechanism** | **DROP** ceilings and vSol band; floors only + extreme-high cap guard |

### 2.4 Regime / population integrity

Population filter is `stage=bonding` creates only. **Risk:** `regime_id` drift (e.g. `stage=pumpswap` on a bonding-tagged row) would **mix regimes** without an explicit gate. v2 adds a **regime_id bonding gate** and summary **stratification** — not a launchpad feature.

### 2.5 DISCOVERY kill-list (v1 features → action)

| v1 feature | Verdict | Rationale |
| --- | --- | --- |
| Narrow **marketCapSol** 28–34 | **DROP** | Selected modal launch; adverse @60s |
| Narrow **vSolInBondingCurve** 29–33.5 | **DROP** | Coupled to modal snapshot; redundant with cap band |
| **max_sol_amount** 2.5 | **DROP** | Excluded non-modal creator SOL that random sampling still hit |
| **max_initial_buy** | **DROP** | Not implicated in sweet-spot; simplify |
| **min_sol_amount** 0.5 | **REPLACE** → **0.25** floor | Keep skin-in-game without modal coupling |
| Bonk soft-exclude | **DROP** (parked) | Scout law; not adverse-selection root cause |
| Zero-buy / metadata / honesty | **KEEP** | Integrity, knowable-at-T |
| Missing cap / price proxy | **KEEP** | No evaluate without sealed proxy |

---

## 3. Rules v2 proposal (post-DISCOVERY)

Implemented as `EvaluateRulesV2` (`RULES_V2_DEFAULTS`). **No** bonk/mayhem/pool selection. **Regime bonding gate** ON.

| Rule | Default | Reject reason |
| --- | --- | --- |
| Bonding population + identity + `knowable_at_t` honesty | ON | (base) |
| **Regime gate** | ON | `missing_regime_id`, `regime_id_stage_not_bonding`, `regime_id_stage_mismatch_row_stage`, `regime_id_market_not_bonding_curve` |
| Metadata `name` / `symbol` / `uri` | ON | `missing_metadata_*` |
| Zero creator buy (when fields present) | ON | `zero_sol_amount`, `zero_initial_buy` |
| Creator SOL floor | **≥ 0.25** | `below_min_sol_amount` |
| Initial buy dust floor | **≥ 1** | `below_min_initial_buy` |
| `marketCapSol` floor | **≥ 20** | `below_min_market_cap_sol`, `missing_market_cap_sol` |
| Extreme high cap at create | **> 50** SOL | `above_extreme_market_cap_sol` |
| Price proxy present | ON | `missing_price_proxy` |
| **Pass** | — | **`runner`** |

**Summary JSON** adds `regime_stratification`, `reject_rate_pct`, and gate `reject_rate_band` (**70–95%** → else **INCOMPLETE** for kill attempt).

### Calibration plan (Vaan)

1. **Done:** `python -m tools.exp002_paper_runner … --rules v2 --marks <existing marks>`, seed 1, on the same subsample. See §5 — overall **INCOMPLETE**; do **not** retune v2 constants to enter 70–95%.
2. If a **later** hyp is **directionally non-adverse** but arms are thin → Proof may authorize **denser marks for power only** — not to rescue lift or to hit the reject-rate band.

---

## 4. Local CLI runbook

```bash
python3 -m tools.exp002_paper_runner \
  data/observe/observe-2026-09-20.jsonl \
  data/observe/observe-2026-09-21.jsonl \
  --rules v2 \
  --marks data/observe/marks-….jsonl \
  --seed 1 \
  --output-dir data/observe \
  --prefix _exp002c
```

Archive v1 (002b reproduction only): `--rules v1 --prefix _exp002b`.

**Tests:** `python3 -m unittest tools.test_exp002_paper_runner`

---

## 5. Result / conclusion (closed)

Local run: `--rules v2` + existing EXP-003 marks, seed 1. Tooling: 26/26 tests OK; rules v2 merged [PR #18](https://github.com/vaanai/MAL/pull/18) @ `842cb21`.

| Metric | Value |
| --- | --- |
| population_n | 32896 |
| runner_n | 12112 |
| reject_n | 20784 |
| reject_pct | ≈**63.18%** |
| reject_rate_band (70–95%) | **INCOMPLETE** (below 70% floor) |
| runner priced_n / mean_return_pct | 96 / ≈**0.385** |
| reject priced_n / mean | 888 / ≈**3202.76** |
| random priced_n / mean | 410 / ≈**2500.58** |
| no_lift_vs_random | **FAIL** |
| reject_runner_parity_after_costs | **PASS** |
| **Overall / Proof stamp** | **INCOMPLETE** (primary: reject_rate_band) |

Soft/directional lift **FAIL** again (runners adverse vs random on the marked subsample). **Do not promote** rules v2.

**Next law:** do **not** tighten v2 constants just to hit 70–95% (OPTIMIZE-to-gate). **DISCOVERY** first on the dual failure: v2 is **too permissive** (reject below floor) **and** survivors are still **adverse vs random**. Then **EXP-002d** only with a **new kill-list hyp** — or **pause**. Soft denser marks still not the fix. Does not authorize live capital.
