# Manager summary (≤20 lines)

**As-of:** 2026-09-23

- **Product:** 3 layers (new-coin / entity-graph / filtered smart-wallet) + **LAYA** → risk gate → exec (deferred). **X = layer 4 later**. Paper first; free-first. Plans = directions; profit is the goal.
- **DEC-010:** `mal-core-0` **LIVE** (Phoenix AD-1, **2 OCPU / 12 GB A1**, Ubuntu 24.04, `/var/lib/mal` 150 GB). Postgres **16.15** `meme_core`/`mal_app` localhost. Handoff: [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md).
- **DEC-011:** Cursor↔Oracle access **LIVE** — CF Access Service Auth + Runtime Secrets; smoke **2026-09-23**; hostname **`mal-core-vnic`**; paper-only. Runbook [oracle_ssh_smoke.md](../tools/oracle_ssh_smoke.md). Bootstrap [ORACLE-HOST-BOOTSTRAP.md](ORACLE-HOST-BOOTSTRAP.md). My Machines on-box **parked**. **Do not** expose Postgres or open `:22`. [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md).
- **Storage:** GitHub SoT. Sealed **JSONL** = provenance/event spine. Postgres = **ops/state** — **not** a provenance replacement. Password/secrets **never** in repo. [DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) amended by DEC-009/010.
- **Workflow:** Grok plan/review/integrate; Cursor implement/test (aggressive). **DM Vaan a Cursor status card** on every launch. Escalate before spend/security/public Postgres/keys/capital. No fake idle keep-alive.
- **Paper trading (above marks):** [PAPER-TRADING-SURFACE-BRIEF.md](PAPER-TRADING-SURFACE-BRIEF.md) — day-1 **custom fill-sim on JSONL + marks**; external paper venues **parked**; broker code **not** in artifact PR.
- **EXP-004:** Oracle **day-aligned** courier re-score **2026-09-23** — overall **DIRECTIONAL_WATCH** (exit 0) on 2026-09-20/21 sealed observe+marks; priced_60s_n **102** / **122**; H-G2 **KILL** @60s both days; **no promotion**. Prior observe-only 09-23 run **INCOMPLETE**. [EXP-004](../EXP/EXP-004-graph-creator-recurrence-v0.md).
- **EXP-004b:** Oracle **NH-G1a** ordinal re-score **2026-09-23** on same sealed 20/21 JSONL — cross-day **KILL** (no bucket sep+random @60s **both** days; `bucket_3plus` soft **20 only**); H-G2 kill intact; host `_exp004b-oracle-*`. [EXP-004b](../EXP/EXP-004b-nh-g1a-ordinal-prior-mint-v0.md).
- **EXP-005:** Scout smart-wallet / follow L3 one-pager **Proposed, not run** (docs-only draft; no scored result). [EXP-005](../EXP/EXP-005-smart-wallet-follow-discovery-v0.md).
- **EXP-002c:** **INCOMPLETE closed** — `--rules v2` + existing marks, seed 1; population_n=32896, runner_n=12112, reject_n=20784, reject_pct≈63.18% (below 70% floor). Runner priced_n=96 mean≈0.385; reject priced_n=888 mean≈3202.76; random priced_n=410 mean≈2500.58. Gates: reject_rate_band **INCOMPLETE**; no_lift_vs_random **FAIL**; reject_runner_parity_after_costs **PASS**. Proof stamp **INCOMPLETE**. **Do not promote.** Next: **DISCOVERY** — score EXP-004 on courier JSONL; Scout taxonomy [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](DISCOVERY-WALLET-FOLLOW-SIGNALS.md); then EXP-002d or **pause**. [EXP-002c](../EXP/EXP-002c-rules-v2-adverse-selection.md).
- **EXP-002b:** **FAIL closed** — **FAIL_NO_LIFT_VS_RANDOM**. Denser marks are **not** the fix.
- **EXP-003:** Subsample marks exist; book-wide N/A expected. Reuse existing marks; densify only after a non-adverse directional hyp needs power.
- Reload `LAB_STATE.md` + this SUMMARY each session (also `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`).
- **Pipeline:** detect→decode→evaluate→runners [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **EXP-001:** **PASS closed** — [`tools.exp001_mislabel`](../tools/exp001_mislabel.py).
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X **reassess only**. No trading/X keys on host; no agent capital.
- **Hard defer:** Birdeye paid, Dexscreener on spine, live capital, PumpPortal trading API, bonk/mayhem reclass expansion.
- Cloud agents cannot read Vaan's JSONL — local CLI only for EXP scoring.
