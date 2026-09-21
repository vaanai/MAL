# Manager summary (≤20 lines)

**As-of:** 2026-09-21

- **New:** [EXP-002](../EXP/EXP-002-evaluate-runner-v0.md) — first **evaluate→runner** EXP (rules-only paper book); CLI `python -m tools.exp002_paper_runner`.
- Lab memory: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — mislabel audit tooling [`tools.exp001_mislabel`](../tools/exp001_mislabel.py); does not start promote ladder.
- **EXP-002 next for Vaan:** run paper book on local `observe-2026-09-20` + `observe-2026-09-21` JSONL; expect many horizon **N/A** until post-create marks exist in JSONL.
- **Regime-at-ingest v0:** [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), [DEC-004](../DEC/DEC-004-regime-id-encoding.md).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API.
- **Starter stack options:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md).
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
