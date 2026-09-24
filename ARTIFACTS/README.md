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
| [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md) | Proposed paper scoreboard over local `paper_evaluate_hot_packet_v0` stamps and a synthetic sealed-day expectation + [paper-scoreboard-sealed-fixture-v0.schema.json](paper-scoreboard-sealed-fixture-v0.schema.json) |
| [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md) | Proposed paper batch: synthetic day-aligned observe JSONL → hot-packet → paper-evaluate → paper-scoreboard; sealed book stays incomplete + [paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) |
| [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md) | Proposed dry-run receipt: operator-local host JSONL shape for that batch; sealed book stays incomplete; `host_jsonl_read` does not close the book + [paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) |
| [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) | Proposed schema-align pass: parent paper schemas refuse the dishonest shapes the #49–#53 CLIs already refuse. No new runtime object. `schema_looser_than_cli` closed |
| [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md) | Proposed capture of #53 dry-run refuse and receipt outcomes. Book stays incomplete. `measure.kind=none`. No host extract |
| [PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md](PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md) | Proposed fill-sim bind on validated `paper_evaluate_hot_packet_v0`; EXP-006 vocabulary only; book stays incomplete + [paper-fill-sim-hot-packet-evaluate-v0.schema.json](paper-fill-sim-hot-packet-evaluate-v0.schema.json) |
| [PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md) | Proposed fill-sim scoreboard over local `paper_fill_sim_hot_packet_evaluate_v0` stamps and a synthetic sealed-day expectation; Soft GATE **PASS** (#58) + [paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json](paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json) |
| [PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md) | Proposed fill-sim batch: synthetic day-aligned observe JSONL → hot-packet → evaluate → fill-sim bind → fill-sim scoreboard; sealed book stays incomplete; Soft GATE **PASS** (Formal #59 squash `4664363`) + [paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) |
| [PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md) | Proposed dry-run receipt: operator-local host JSONL shape for that fill-sim batch; sealed book stays incomplete; Soft GATE **PASS** (Formal #60 squash `d3b5554`) + [paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) |
| [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) | Proposed fill-sim schema-align pass: #57–#60 JSON Schemas refuse the dishonest shapes those CLIs already refuse. Soft GATE **PASS** (Formal #61 squash `677e88b`). `schema_looser_than_cli` closed for watched fill-sim shapes. No new runtime object |
| [PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md) | Proposed capture of #60 fill-sim dry-run refuse and receipt outcomes. Book stays incomplete. Soft GATE **PASS** (Formal #62 squash `a1a3f26`) + [paper-fill-sim-host-local-dry-run-receipt-capture-v0.schema.json](paper-fill-sim-host-local-dry-run-receipt-capture-v0.schema.json) |
| [PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md) | Proposed LAYA precompute surround: fill-sim scoreboard digests ± optional #59 batch rollup; fixtures only; Soft GATE **PASS** (Formal #63 squash `171cd61`) + [paper-laya-precompute-fill-sim-surround-packet-v0.schema.json](paper-laya-precompute-fill-sim-surround-packet-v0.schema.json) |
| [PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md) | Proposed LAYA precompute surround (non-fill-sim spine): #51 scoreboard digests ± optional #52 batch rollup; fixtures `fixtures/paper_laya_precompute_surround_packet_v0/`; CLI `python -m tools.paper_laya_precompute_surround_packet_v0`; Soft GATE **PASS** Formal stamp [PR #64](https://github.com/vaanai/MAL/pull/64) (squash pending) + [paper-laya-precompute-surround-packet-v0.schema.json](paper-laya-precompute-surround-packet-v0.schema.json); [LAB_STATE.md](../LAB_STATE.md) §11o |
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
