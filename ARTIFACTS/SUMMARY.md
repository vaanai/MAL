# Manager summary (≤20 lines)

**As-of:** 2026-09-21

- **New:** [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md) + [EXP-003](../EXP/EXP-003-post-create-marks.md) — post-create ticks onto sealed observe (unblock EXP-002 Δ_exec/lift).
- **v0 path:** RPC historical subsample on Vaan’s JSONL → side `outcome_mark` JSONL; last tick with `T < t_mark ≤ T+H`. Trade WS is **v0.1 forward** (does not backfill). Dexscreener **not** spine.
- **Vaan:** `python -m tools.exp003_rpc_backfill` (public RPC, subsample) -> `python -m tools.exp003_marks` -> EXP-002 `--rules v1 --marks`. Runbook: [EXP-003](../EXP/EXP-003-post-create-marks.md) §10.
- Lab memory: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed**. **EXP-002:** tooling ready; scoring N/A-heavy until EXP-003 marks.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, JEV, bonk/mayhem reclass.
- Cloud agents cannot read Vaan's JSONL — local CLI only.
