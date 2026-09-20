# Manager summary (≤20 lines)

**As-of:** 2026-09-20

- Lab memory skeleton merged: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- Phase-0 **blocking** APIs: PumpPortal WS (`subscribeNewToken` / migration free) + light Solana RPC (public or Helius free).
- **Defer:** X API, gRPC/Yellowstone, dedicated nodes, Jito/send paths, PumpPortal trading API.
- **Cost:** free-only **~$0/mo** infra; light paid **~$49–90/mo** (RPC tier + optional Birdeye); serious latency **$500+/mo** class — not now.
- PumpPortal FAQ: processed WS often **&lt;100 ms** behind gRPC (NYC); vendor claim — log `t_ws` vs RPC in EXP.
- Public RPC: ~100 req/10s/IP — fine for spot checks, not heavy backfill ([Solana clusters doc](https://solana.com/docs/references/clusters)).
- Leave free RPC when measured 429/timeouts or p95 backfill &gt;2s breaks hot packet (see brief §4).
- X: **$200+/mo** tier history; pay-per-use emerging — social hypothesis waits.
- Full detail: [API-COST-LATENCY-BRIEF.md](API-COST-LATENCY-BRIEF.md).
- Next: Scout wires single WS + JSONL; Proof defines latency EXP with 1s–60s windows.
