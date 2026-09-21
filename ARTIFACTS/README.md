# Artifacts

Research briefs, decision packet exports, and manager summaries land here. **Git is the archive** (phase 0).

## Conventions

- **Briefs:** `TOPIC-BRIEF.md` (evidence, URLs, as-of date, unknowns flagged)
- **Summaries:** `SUMMARY.md` — ≤20 lines for manager reload (overwrite with latest session pointer)
- **Decision exports:** `DEC-PACKET-YYYYMMDD-xxx.json` — immutable once merged
- **No secrets** — API keys, wallet keys, and env files stay local

## Current artifacts

| File | Purpose |
| --- | --- |
| [API-COST-LATENCY-BRIEF.md](API-COST-LATENCY-BRIEF.md) | Phase-0 API connections, cost tiers, latency gap vs Pump.fun |
| [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md) | JEV path, X feeds, create feeds, starter stack options for Vaan |
| [PUMPPORTAL-PAYLOAD-INVENTORY.md](PUMPPORTAL-PAYLOAD-INVENTORY.md) | PumpPortal WS field inventory (WS / RPC / unknown) |
| [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md) | Phase-0 regime taxonomy (draft, EXP-revisable) |
| [REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md) | WS vs RPC, knowable-at-T, hot-packet stamp rules |
| [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md) | Options for 1s–60s post-create ticks on sealed creates (unblock EXP-002) |
| [SUMMARY.md](SUMMARY.md) | Short manager digest of latest research |

## Provenance

Each brief should include:

- **Research as-of:** calendar date
- **Primary sources:** links to vendor docs/pricing
- **Needs measurement:** items that require lab benchmarks, not vendor marketing
