# MAL

Meme coin alpha lab — **Pump.fun / Solana first**, observe-before-act, memory-first phase 0.

## Quick reload (managers)

1. [LAB_STATE.md](LAB_STATE.md) — objective, spine, hypotheses, next work  
2. [CONSTITUTION.md](CONSTITUTION.md) — non-negotiables  
3. [ARTIFACTS/SUMMARY.md](ARTIFACTS/SUMMARY.md) — latest research digest  
4. Active decisions in [DEC/](DEC/)

## Repository layout

| Path | Purpose |
| --- | --- |
| `LAB_STATE.md` | Compact current lab state |
| `CONSTITUTION.md` | Rules: knowable-at-T, capped hot packet, kill-attempt, infra discipline |
| `DEC/` | Immutable decision records (`DEC-xxx-*.md`) |
| `EXP/` | Experiment registry format + future `EXP-xxx` files |
| `ARTIFACTS/` | Research briefs and manager summaries |

## Phase 0

- **Observe-only** — no trading bot in this repo yet  
- **No database** — markdown/JSON in git ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md))  
- **Local-first** — home PC / WSL2+Docker when needed  
- **Persistent agents:** Grok managers (Helm / Scout / Graph / Proof); Cursor workers ship artifacts via PR  

## Research

- [API, cost & latency brief](ARTIFACTS/API-COST-LATENCY-BRIEF.md) — phase-0 API matrix, monthly cost tiers, latency gaps vs Pump.fun  

## Seats (override legacy org chart)

See [DEC-001](DEC/DEC-001-lean-four-override.md): **Helm, Scout, Graph, Proof** — not Research/Strategy/Ops as runtime roles.
