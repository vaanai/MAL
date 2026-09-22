# Manager summary (≤20 lines)

**As-of:** 2026-09-22

- **Product:** 3 layers (new-coin / entity-graph / filtered smart-wallet) + **LAYA** → risk gate → exec (deferred). **X = layer 4 later** (not discovery). Paper first; free-first.
- **DEC-009:** phase-0 continuous host = Oracle **Always Free** `mal-core-0` (**VM.Standard.A1.Flex, 2 OCPU / 12 GB, aarch64** — **not 4/24**). **Pending Vaan provision — nothing created.** Not a paid VPS. BOM: [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md).
- **Storage:** GitHub SoT for DEC/EXP/LAB_STATE. Sealed **JSONL** = EXP/knowable-at-T spine day-1. On-box Postgres = Layer-2 cache/ops aid — **do not** force-migrate marks/EXP provenance day-1. [DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) amended by DEC-009.
- **Council soft (not blockers):** aarch64 binary gaps escalate to Helm **post-create**; no trading/X keys on host; bonk/mayhem parked.
- **EXP-002c:** **INCOMPLETE closed** — `--rules v2` + existing marks, seed 1; population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈63.18% (below 70% floor). Runner priced_n=96 mean≈0.385; reject priced_n=888 mean≈3202.76; random priced_n=410 mean≈2500.58. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Proof stamp **INCOMPLETE** (primary: reject_rate_band). **Do not promote.** Next: **DISCOVERY** then EXP-002d (new kill-list hyp) or **pause** — no OPTIMIZE-to-gate. [EXP-002c](../EXP/EXP-002c-rules-v2-adverse-selection.md).
- **EXP-002b:** **FAIL closed** — **FAIL_NO_LIFT_VS_RANDOM**. Denser marks are **not** the fix.
- **EXP-003:** Subsample marks exist; book-wide N/A expected. Reuse existing marks; densify only after a non-adverse directional hyp needs power.
- **Next infra:** provision per BOM, then cut over observe/paper. Laptop = operator + data courier until cutover. A1 capacity miss → **STOP** (no paid shapes).
- Reload `LAB_STATE.md` + this SUMMARY each session (also `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`).
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — [`tools.exp001_mislabel`](../tools/exp001_mislabel.py).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API, bonk/mayhem reclass expansion.
- **Starter stack:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md); Laya vs VPS [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md) (Always Free host ≠ paid VPS).
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
