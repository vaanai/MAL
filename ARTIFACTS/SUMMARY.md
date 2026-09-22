# Manager summary (≤20 lines)

**As-of:** 2026-09-22

- **EXP-002b:** **FAIL closed** — **FAIL_NO_LIFT_VS_RANDOM** (~81% reject; runner ~3% vs random ~20–30% @60s on marked subsample). **Do not promote** rules v1; denser marks are **not** the adverse-selection fix.
- **EXP-002c:** Rules v2 landed (drop v1 sweet-spot bands). Vaan: `python -m tools.exp002_paper_runner … --rules v2 --marks` (reuse existing marks JSONL). [EXP-002c](../EXP/EXP-002c-rules-v2-adverse-selection.md).
- **EXP-003:** Post-create `outcome_mark` side JSONL + RPC subsample producer; join `T < t_mark ≤ T+H`. [EXP-003](../EXP/EXP-003-post-create-marks.md).
- Lab memory: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — [`tools.exp001_mislabel`](../tools/exp001_mislabel.py).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API, bonk/mayhem reclass expansion.
- **Starter stack:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md); Laya vs VPS [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md).
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
