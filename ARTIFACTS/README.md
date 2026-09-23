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
| [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md) | Local Laya vs VPS-first vs phase-0; Always Free `mal-core-0` is the chosen host experiment (not a paid VPS) |
| [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md) | Phase-0 Always Free envelope + as-built names (**2 OCPU / 12 GB** A1); **provisioned** |
| [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md) | Durable Phase-0 handoff: live inventory, JSONL vs Postgres, autonomy; access **LIVE** [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) |
| [ORACLE-HOST-BOOTSTRAP.md](ORACLE-HOST-BOOTSTRAP.md) | Host layout, schema stubs, observe JSONL path, Cursor reconnect (no secrets) |
| [ORACLE-BOOTSTRAP-CHECKLIST.md](ORACLE-BOOTSTRAP-CHECKLIST.md) | Repeatable bootstrap checklist |
| [ENGINEERING-DECISION-LOG.md](ENGINEERING-DECISION-LOG.md) | Persistent what/why/tested/rollback log (newest first) |
| [PUMPPORTAL-PAYLOAD-INVENTORY.md](PUMPPORTAL-PAYLOAD-INVENTORY.md) | PumpPortal WS field inventory (WS / RPC / unknown) |
| [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md) | Phase-0 regime taxonomy (draft, EXP-revisable) |
| [REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md) | WS vs RPC, knowable-at-T, hot-packet stamp rules |
| [HOT-PACKET-V0.md](HOT-PACKET-V0.md) | Proposed paper hot-packet contract (L1 spine, regime locks, capped graph slots) + [hot-packet-v0.schema.json](hot-packet-v0.schema.json) |
| [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md) | Proposed paper evaluate→runners stamp on `hot_packet_v0` only + [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json) |
| [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md) | Options for 1s–60s post-create ticks on sealed creates (unblock EXP-002) |
| [PAPER-TRADING-SURFACE-BRIEF.md](PAPER-TRADING-SURFACE-BRIEF.md) | Paper / fill-sim surfaces vs marks-only path; day-1 P0 recommendation (no broker impl) |
| [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) | Graph Layer-2 discovery: inventory, v0 slice vs theater, H-G hypotheses, **EXP-004** §9 (tooling landed) |
| [DISCOVERY-WALLET-FOLLOW-SIGNALS.md](DISCOVERY-WALLET-FOLLOW-SIGNALS.md) | Scout wallet / follow signal taxonomy for paper→gated (Index/Park; S1–S7 / H-G aligned) |
| [SUMMARY.md](SUMMARY.md) | Short manager digest of latest research |

## Provenance

Each brief should include:

- **Research as-of:** calendar date
- **Primary sources:** links to vendor docs/pricing
- **Needs measurement:** items that require lab benchmarks, not vendor marketing
