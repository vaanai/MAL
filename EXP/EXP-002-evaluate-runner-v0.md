# EXP-002 — Evaluate→runner v0 (rules-only paper book)

Council lock (Proof / Scout / Helm). **No live capital**, **no paid APIs**, **no JEV/LLM evaluate** in v0. Local CLI in `tools/` so Vaan runs on his PC (`C:\Users\vivaa\dev\MAL`); cloud agents cannot read sealed JSONL.

| Field | Value |
| --- | --- |
| **ID** | `EXP-002` |
| **Status** | Tooling ready. Scoring pending local CLI run on sealed bonding creates. |
| **Owner seat** | Proof (evaluate + paper outcomes). Scout supplies detect book via `python -m observe`. |
| **Locked** | 2026-09-21 (method + rules v0). |
| **Depends on** | Population: local JSONL from [observe](../observe/). Law: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md), [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md). Prior gate: [EXP-001](EXP-001-regime-stage-mislabel.md) **PASS closed** (regime/stage mislabel tooling; promote ladder not started by EXP-001). |
| **Hypothesis** | A **tiny rules-only** evaluate filter on knowable-at-T sealed creates selects a **runner** cohort whose **gross** paper returns at primary horizon **lift** vs a **random same-size** sample from the full bonding-create book, and **reject** cohort returns **differ** from runner cohort (selection-bias falsifier). |
| **Method** | Full detect book on bonding creates → rules v0 labels `runner` \| `reject` → paper promotion stamp at `T = t_ws` → outcome marks at fixed horizons when post-create price proxies exist in JSONL; honest **N/A** otherwise. CLI: `python -m tools.exp002_paper_runner`. |
| **As-of-T** | Evaluate uses only sealed row at `t_ws`: `stage`, `signature`, `mint`, `knowable_at_t`, optional `ws_payload` fields for honesty / bonk toggle. No RPC, no enrich rows, no lookahead labels. |
| **Regime labels** | Population fixed `stage=bonding` creates; stamped `regime_id` not re-scored (EXP-001 scope). |
| **Windows** | **1s / 5s / 15s / 30s / 60s** plus **+2s / +10s / +5m** and **peak / max drawdown** within +5m when price path exists. **Primary kill horizon:** **60s** (override via `--primary-horizon`). |
| **Kill-attempt** | (1) **No lift:** runner mean ≤ random baseline mean at primary horizon (same runner count, seed-drawn from full book). (2) **Selection bias:** reject cohort mean ≈ runner cohort mean at primary horizon (relative parity ≤5% on gross returns; `Δ_exec` N/A in v0). Gates **INCOMPLETE** when priced_n &lt; 10 per arm. |
| **Result** | Pending local run (outputs under `data/observe/_exp002_*`, gitignored). |
| **Conclusion** | Pending. Paper promotion only — no exec DEC. |

---

## 1. Scope lock

| Item | Lock |
| --- | --- |
| **Name** | EXP-002 — evaluate→runner v0 |
| **Evaluate** | **Rules-only** filter (not JEV, not LLM) |
| **Runner** | Paper promotion stamp at evaluate decision time (`t_ws`); pretend-buy |
| **Full detect book** | Every eligible bonding create retained; evaluate never deletes history |
| **Outcomes** | Same horizon marks for `runner` and `reject` labels |
| **Migrations** | Out of v0 population |
| **Composer / Fast** | Composer 2.5; Fast mode **OFF** |

---

## 2. Population

**Bonding creates** from sealed observe JSONL.

| Rule | Detail |
| --- | --- |
| Source | `data/observe/observe-YYYY-MM-DD.jsonl` (gitignored) |
| Include | `type=ingest_hot`, `txType=create`, **`stage=bonding`**, valid `t_ws` |
| Exclude | Migrations; void rows (missing `t_ws`); non-create packets |
| Known local capture (Vaan PC) | Same files as EXP-001 (~15k + ~19k lines); creates are a subset |

If JSONL is absent on the machine running the CLI, do not substitute cloud data or live RPC.

---

## 3. Decode packet (v0)

For this EXP, **decode** is the sealed hot row itself (no separate hot-packet file). Evaluate reads:

- Top-level `stage`, `signature`, `mint`, `t_ws`, `knowable_at_t`
- `ws_payload` only for honesty checks and optional bonk-pool heuristic

Graph scores and X attach are **out of scope** for rules v0.

---

## 4. Evaluate rules v0 (explicit, tiny, killable)

Default CLI behavior unless flags noted.

| Rule | Action |
| --- | --- |
| **Bonding stage** | `reject` if `stage != bonding` (defensive; population already filters bonding) |
| **Identity** | `reject` if `signature` or `mint` missing or `UNK` |
| **knowable_at_t honesty** | `reject` if hot row asserts verified/unlocked fields without WS evidence (e.g. `quote_verified=true` without `quote_mint` in `ws_payload`; `instr` not `pending_rpc`; `venue_verified=true`; `creator_verified=true` without `traderPublicKey`; `fee` not `unverified`) |
| **Bonk pool soft exclude** | **Default OFF.** With `--exclude-bonk-pool`, `reject` when sealed `pool` / payload platform fields / name+symbol contain `bonk` (documented heuristic only) |
| **Min market cap** | **Default OFF** (`--min-market-cap-sol 0`). When set &gt;0, `reject` if entry `marketCapSol` / `vSolInBondingCurve` proxy below threshold |
| **Pass** | Label **`runner`** when no reject reasons |

No hidden ML. Change rules only via new EXP or documented flag — not silent code drift.

---

## 5. Runner (paper promotion)

- **Decision time:** `T = t_ws` on the sealed create row.
- **Entry price proxy:** `marketCapSol`, else `vSolInBondingCurve` from row or `ws_payload` (first finite &gt;0).
- **Capital:** none — paper stamp only.
- **`Δ_exec`:** **N/A** in v0 when fills/fees cannot be reconstructed from JSONL alone (CLI sets `delta_exec_status=na`; do not invent fees).

---

## 6. Outcome horizons

Seconds from **evaluate T** (`t_ws`):

| Mark | Offset |
| --- | --- |
| 1s, 5s, 15s, 30s, 60s | 1, 5, 15, 30, 60 |
| +2s, +10s, +5m | 2, 10, 300 |
| Peak / max drawdown | Path over **[T, T+5m]** using available marks |

**Price path source (v0):** later sealed JSONL rows with the same `mint` and a price proxy (`marketCapSol` or `vSolInBondingCurve`) and `t_ws` ≥ T. Typical phase-0 capture is **create + migration only**, so **most post-create horizons will be N/A** until trade/subscribe enrich or a follow-on EXP attaches marks — **do not invent prices**.

Return at horizon H (when mark exists):

```text
return_pct = (price_H / price_0 - 1) * 100
```

---

## 7. Full detect book + reporting

For **every** population row the CLI writes:

- `evaluate_label`: `runner` \| `reject`
- `evaluate_reasons`: list (empty for runners)
- Horizon outcomes (ok or **N/A**)
- Peak/drawdown and `Δ_exec` status

**Confusion matrix** (summary JSON): evaluate label × gross outcome bucket (`positive_gross` \| `non_positive_gross` \| `na`) at primary horizon.

**Random baseline:** sample size = runner count, uniform from full book (seeded), same horizon mean for kill (1).

---

## 8. Kill criteria

| ID | Condition | Summary gate | CLI `overall` |
| --- | --- | --- | --- |
| K1 | Runner mean ≤ random mean at primary horizon | `no_lift_vs_random` **FAIL** | `FAIL_NO_LIFT_VS_RANDOM` |
| K2 | Reject mean ≈ runner mean (relative diff ≤ **5%** on gross) | `reject_runner_parity_after_costs` **FAIL** | `FAIL_SELECTION_BIAS_PARITY` |
| — | Insufficient priced rows (&lt; **10** per arm) | gates **INCOMPLETE** | `INCOMPLETE` |
| Pass | K1 pass and K2 pass | both **PASS** | `PASS` |

Costs: when `Δ_exec` is N/A, kill (2) uses **gross** returns only — documented limitation; follow-on EXP should attach `Δ_exec` before exec promotion.

---

## 9. Limitations

- No post-create marks in JSONL → horizons **N/A** (expected on current WS subscriptions).
- `Δ_exec` **N/A** — no fee/slippage model from sealed rows alone.
- Bonk-pool toggle is a soft string heuristic, default off.
- Rules v0 is intentionally weak; purpose is pipeline + kill machinery, not production alpha.

Follow-on: EXP with trade stream or RPC enrich for marks; optional rules v1 thresholds.

---

## 10. Out of scope

- Live trading, PumpPortal trading API, paid indexers
- JEV / LLM evaluate
- Migration cohort
- Rewriting sealed ingest rows or dropping rejects from the book
- Observe-wiring changes (except shared JSONL loaders with EXP-001)

---

## 11. Local CLI runbook (Windows / WSL)

Offline. Repo + sealed JSONL only.

**Outputs (gitignored):**

| File | Contents |
| --- | --- |
| `data/observe/_exp002_book.jsonl` | Full detect book with labels + outcomes |
| `data/observe/_exp002_book.csv` | Flattened horizons for spreadsheets |
| `data/observe/_exp002_summary.json` | Cohort means, confusion matrix, PASS/FAIL vs kill gates |

### WSL / Linux / macOS (from repo root)

```bash
cd /path/to/MAL
python3 -m tools.exp002_paper_runner \
  data/observe/observe-2026-09-20.jsonl \
  data/observe/observe-2026-09-21.jsonl \
  --seed 1 \
  --output-dir data/observe \
  --prefix _exp002
```

### Windows (PowerShell, from repo root)

```powershell
Set-Location C:\Users\vivaa\dev\MAL
python -m tools.exp002_paper_runner `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --seed 1 --output-dir data\observe --prefix _exp002
```

Optional flags: `--exclude-bonk-pool`, `--min-market-cap-sol 30`, `--primary-horizon 60s`.

**Exit codes:** `0` PASS; `2` FAIL_NO_LIFT or FAIL_SELECTION_BIAS; `3` INCOMPLETE; `1` missing files.

**Tests (no live JSONL):**

```bash
python3 -m unittest tools.test_exp002_paper_runner
```

---

## 12. Result / conclusion

| Field | Value |
| --- | --- |
| **Result** | _Pending — local CLI not yet run against Vaan's sealed JSONL._ |
| **Conclusion** | _Pending. Does not authorize live capital._ |
