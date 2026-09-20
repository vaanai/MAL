# Manager summary (≤20 lines)

**As-of:** 2026-09-20

- Lab memory skeleton: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Regime-at-ingest v0:** inventory, enum, matrix + [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md); encoding locked [DEC-004](../DEC/DEC-004-regime-id-encoding.md).
- **Observe-wiring (phase 0):** `python -m observe` → JSONL [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md); 24h capture pending (Vaan); [EXP-001](../EXP/EXP-001-regime-stage-mislabel.md) mislabel sample locked (Proof).
- Phase-0 **blocking** APIs: PumpPortal WS (`subscribeNewToken` / `subscribeMigration` free) + light Solana RPC.
- **Hard defer:** Birdeye paid; X API; gRPC/Yellowstone; dedicated nodes; Jito/send; PumpPortal trading API.
- **Dexscreener:** debug enrich only — never on regime-tagged hot spine.
- **Cost:** free-only **~$0/mo** infra; light paid **~$49/mo** RPC class — not serious latency tier.
- PumpPortal FAQ: processed WS often **&lt;100 ms** behind gRPC (NYC); log `t_ws` vs RPC in EXP.
- Public RPC: ~100 req/10s/IP — spot checks only ([Solana clusters](https://solana.com/docs/references/clusters)).
- Constitution: every ingest row needs `regime_id`; no backfill into sealed packets ([matrix](REGIME-AT-INGEST-MATRIX.md)).
- Full API detail: [API-COST-LATENCY-BRIEF.md](API-COST-LATENCY-BRIEF.md).
- Next: Vaan starts 24h `python -m observe`; Proof scores [EXP-001](../EXP/EXP-001-regime-stage-mislabel.md) (sealed packet only); hot-packet spec.
