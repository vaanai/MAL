# Manager summary (≤20 lines)

**As-of:** 2026-09-23

- **Product:** 3 layers (new-coin / entity-graph / filtered smart-wallet) + **LAYA** → risk gate → exec (deferred). **X = layer 4 later**. Paper first; free-first. Plans = directions; profit is the goal.
- **DEC-010:** `mal-core-0` **LIVE** (Phoenix AD-1, **2 OCPU / 12 GB A1**, Ubuntu 24.04, `/var/lib/mal` 150 GB). Postgres **16.15** `meme_core`/`mal_app` localhost. **No agent SSH yet.** Handoff: [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md).
- **Storage:** GitHub SoT. Sealed **JSONL** = provenance/event spine. Postgres = **ops/state** — **not** a provenance replacement. Password/secrets **never** in repo. [DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) amended by DEC-009/010.
- **Workflow:** Grok plan/review/integrate; Cursor implement/test (aggressive). **DM Vaan a Cursor status card** on every launch. Escalate before spend/security/public Postgres/keys/capital. No fake idle keep-alive.
- **Next infra:** recommend Cursor↔Oracle access (My Machines / Tailscale-on-Oracle / Cloudflare Tunnel) **with tradeoffs**; owner implements; then bootstrap dirs/schema/ingest. Human PC **must not** be a permanent hop.
- **Open (no pick):** trading/wallet surface (Axiom / Phantom / …); optional real paper-trading utility — **ask first**.
- **EXP-002c:** **INCOMPLETE closed** — `--rules v2` + existing marks, seed 1; population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈63.18% (below 70% floor). Runner priced_n=96 mean≈0.385; reject priced_n=888 mean≈3202.76; random priced_n=410 mean≈2500.58. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Proof stamp **INCOMPLETE**. **Do not promote.** Next: **DISCOVERY** then EXP-002d (new kill-list hyp) or **pause**. [EXP-002c](../EXP/EXP-002c-rules-v2-adverse-selection.md).
- **EXP-002b:** **FAIL closed** — **FAIL_NO_LIFT_VS_RANDOM**. Denser marks are **not** the fix.
- **EXP-003:** Subsample marks exist; book-wide N/A expected. Reuse existing marks; densify only after a non-adverse directional hyp needs power.
- Reload `LAB_STATE.md` + this SUMMARY each session (also `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`).
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — [`tools.exp001_mislabel`](../tools/exp001_mislabel.py).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**. No trading/X keys on host; no agent capital.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API, bonk/mayhem reclass expansion.
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
