# Manager summary (≤20 lines)

**As-of:** 2026-09-21

- **EXP-002:** **INCOMPLETE closed** — v0 vacuous on local book (0% reject); tooling OK. Next: [EXP-002b](../EXP/EXP-002b-evaluate-rules-v1.md) **`--rules v1`** (default).
- Lab memory: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — mislabel audit tooling [`tools.exp001_mislabel`](../tools/exp001_mislabel.py); does not start promote ladder.
- **EXP-002b next for Vaan:** `python -m tools.exp002_paper_runner` (default v1) on `observe-2026-09-20` + `observe-2026-09-21` JSONL; expect reject cohort **70–95%**; horizons often **N/A** until post-create marks exist.
- **Regime-at-ingest v0:** [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), [DEC-004](../DEC/DEC-004-regime-id-encoding.md).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API.
- **Starter stack options:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md).
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
