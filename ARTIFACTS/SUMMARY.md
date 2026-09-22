# Manager summary (≤20 lines)

**As-of:** 2026-09-22

- **EXP-002c:** **INCOMPLETE closed** — `--rules v2` + existing marks, seed 1; population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈63.18% (below 70% floor). Runner priced_n=96 mean≈0.385; reject priced_n=888 mean≈3202.76; random priced_n=410 mean≈2500.58. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Proof stamp **INCOMPLETE** (primary: reject_rate_band). **Do not promote.** Next: **DISCOVERY** then EXP-002d (new kill-list hyp) or **pause** — no OPTIMIZE-to-gate. [EXP-002c](../EXP/EXP-002c-rules-v2-adverse-selection.md).
- **EXP-002b:** **FAIL closed** — **FAIL_NO_LIFT_VS_RANDOM**. Denser marks are **not** the fix.
- **EXP-003:** Subsample marks exist; book-wide N/A expected. Reuse existing marks; densify only after a non-adverse directional hyp needs power.
- Reload `LAB_STATE.md` + this SUMMARY each session (also `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`).
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — [`tools.exp001_mislabel`](../tools/exp001_mislabel.py).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API, bonk/mayhem reclass expansion.
- **Starter stack:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md); Laya vs VPS [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md).
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
