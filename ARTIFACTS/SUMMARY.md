# Manager summary (≤20 lines)

**As-of:** 2026-09-21

- **New:** [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md) + [EXP-003](../EXP/EXP-003-post-create-marks.md) — post-create ticks onto sealed observe (unblock EXP-002 lift).
- **v0 path:** RPC historical subsample on Vaan’s JSONL → side `outcome_mark` JSONL; last tick with `T < t_mark ≤ T+H`. Trade WS is **v0.1 forward** (does not backfill). Dexscreener **not** spine.
- **Vaan:** `python -m tools.exp003_rpc_backfill` (public `SOLANA_RPC_URL`, subsample) → `python -m tools.exp003_marks` → EXP-002/002b `--rules v1 --marks`. Runbook: [EXP-003](../EXP/EXP-003-post-create-marks.md) §10.
- **EXP-002b:** **INCOMPLETE closed** pre-marks (~81% reject); re-score after marks **READY**.- Lab memory: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — mislabel audit tooling [`tools.exp001_mislabel`](../tools/exp001_mislabel.py).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API, bonk/mayhem reclass.
- **Starter stack options:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md).
- **Laya vs VPS / spend gates:** [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VPS-BRIEF.md); [DEC-008](../DEC/DEC-008-stack-phase-gates.md) (draft gates, default cheap).
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
