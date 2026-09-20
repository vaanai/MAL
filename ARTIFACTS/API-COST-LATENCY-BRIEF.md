# API, Cost, and Latency Brief — Phase 0 (Pump.fun / Solana)

| | |
| --- | --- |
| **Research as-of** | 2026-09-20 |
| **Scope** | Observe-only phase 0; no DB; local/WSL; no trading bot |
| **Universe** | Pump.fun / Solana first |

Prices and limits change; this brief cites primary vendor pages and flags **needs measurement** where lab benchmarks are required.

---

## 1) Phase-0 API connections

Legend: **Blocking** = needed to start observe-only spine. **Optional** = enriches hypotheses later. **Defer** = not phase 0.

| Connection | Role | Phase 0 | Notes & sources |
| --- | --- | --- | --- |
| **PumpPortal WebSocket** `wss://pumpportal.fun/api/data` | Real-time new token, migration, trades | **Blocking (observe)** | `subscribeNewToken` and `subscribeMigration` documented as **free**; trade/account streams metered (0.01 SOL / 10k WS messages) and need funded API key. Single WS connection; reconnect logic required. FAQ: processed commitment, often **&lt;100 ms behind gRPC** from NYC. [Real-time data](https://pumpportal.fun/data-api/real-time/), [FAQ](https://pumpportal.fun/FAQ/) |
| **PumpPortal HTTPS** (trade/local tx) | Execution | **Defer** | Phase 0 is observe-only; Lightning API uses dedicated nodes/Jito paths — paid problem for **exec**, not observation. [Trading API](https://pumpportal.fun/trading-api/) |
| **Solana public RPC** `https://api.mainnet-beta.solana.com` | Account/tx reads, confirmation | **Blocking (light use)** | Free, rate-limited (~100 req/10s/IP, 40/single RPC/10s). Not for production volume. [Clusters / rate limits](https://solana.com/docs/references/clusters), [RPC overview](https://solana.com/docs/rpc) |
| **Helius** (free tier) | Higher headroom RPC + optional LaserStream WSS (standard) | **Optional (recommended light paid alt)** | Free: 1M credits/mo, 10 RPC req/s. Developer $49/mo: 50 req/s, enhanced WS extensions. [Pricing](https://www.helius.dev/pricing), [Plans doc](https://www.helius.dev/docs/billing/plans) |
| **QuickNode** free trial | Private RPC endpoint | **Optional** | 10M credits, 15 req/s trial; paid from $49/mo. [Pricing](https://www.quicknode.com/pricing) |
| **Triton One** PAYG | RPC + Yellowstone gRPC (Dragon’s Mouth) | **Defer** | $125 min deposit, $0.08/GB + $10/M calls; streaming included. Phase 0 observe via PumpPortal + free RPC first. [Pricing](https://triton.one/pricing) |
| **Dexscreener REST** `api.dexscreener.com` | Pair metadata, search, liquidity context | **Optional** | No API key; ~60 or 300 req/min per route. Paid tier mentioned at checkout only. [API reference](https://docs.dexscreener.com/api/reference), [Terms](https://docs.dexscreener.com/api/api-terms-and-conditions) |
| **Birdeye Data** | Token/wallet/trade index, WS on higher tiers | **Optional** | Free Standard: 30k compute units/mo, 1 rps. Lite $39+ for production-ish polling. [Pricing](https://docs.birdeye.so/docs/pricing), [CU costs](https://docs.birdeye.so/docs/compute-unit-cost) |
| **X (Twitter) API** | Social reassess | **Defer (phase 0)** | Official tiers historically **$200/mo Basic**, **$5k/mo Pro**, enterprise **~$42k/mo**; pay-per-use beta with per-endpoint pricing (verify in Developer Console). Too expensive for H-social until proxy/manual sampling. [TechCrunch on pay-per-use](https://techcrunch.com/2025/10/21/x-is-testing-a-pay-per-use-pricing-model-for-its-api/), [Pay-per-use preview doc](https://x-preview.mintlify.app/x-api/getting-started/pricing) |
| **Jito / private relays / bundles** | Transaction landing | **Defer** | Relevant when **exec** and speed-to-fill matter; PumpPortal documents Jito paths for trades, not phase-0 observe. |
| **Yellowstone / Geyser gRPC** (Helius LaserStream, Triton, self-hosted) | Sub-slot streaming, program filters | **Defer** | Helius mainnet gRPC from **Business $499/mo**; dedicated nodes **~$2,900+/mo** cited on Helius pricing. [Helius pricing](https://www.helius.dev/pricing), [LaserStream](https://www.helius.dev/docs/laserstream) |

### Minimum phase-0 stack (recommended)

1. **PumpPortal WS** — `subscribeNewToken` (+ `subscribeMigration` if testing graduation hypothesis).
2. **Solana RPC** — public endpoint for spot checks **or** Helius free tier if 429s appear.
3. **Local logs** — JSONL/markdown per EXP; no third-party index required to start.

Everything else is hypothesis-driven optional enrichment.

---

## 2) Cost estimate (monthly ballpark, USD)

**LLM ~$60/mo is separate** (constitution); below is market-data / RPC only.

### A) Free-only path

| Line item | Est. |
| --- | --- |
| PumpPortal new token / migration WS | $0 |
| Solana public RPC | $0 |
| Dexscreener (polite polling) | $0 |
| Birdeye Standard (sparse calls) | $0 |
| **Total infra** | **~$0/mo** |

**Risks:** RPC 429/403, WS disconnects, no SLA, cannot sustain high `getTransaction` volume.

### B) Light paid path (observe + moderate RPC)

| Line item | Est. |
| --- | --- |
| Helius Developer **or** QuickNode Build | **~$49/mo** |
| PumpPortal metered trade WS (if watching many mints’ trades) | Variable; e.g. 100k trade msgs ≈ **0.1 SOL** (~$15–25 if SOL ~$150–250) — **needs measurement** |
| Birdeye Lite (optional index) | **$39/mo** |
| Dexscreener | $0 |
| **Total infra** | **~$49–90/mo** (+ metered SOL if heavy trade WS) |

### C) “Serious latency” path (exec / competitive streaming — **not phase 0**)

| Line item | Est. |
| --- | --- |
| Helius Business (mainnet LaserStream gRPC) | **$499/mo** |
| Helius Professional / QuickNode Scale+ | **$999/mo** tier class |
| Shred add-ons (Helius listing) | **$800–1,000/mo per IP** (vendor page) |
| Dedicated Solana node (Helius / providers) | **~$2,900+/mo** |
| Triton dedicated gRPC | Custom quote |
| X API Pro / Enterprise | **$5k–42k+/mo** class |
| Colocation / bare-metal near validators | **$ hundreds–thousands/mo** + ops time |
| **Total infra** | **~$500–5,000+/mo** before colo; **needs measurement** per strategy |

Separate buckets when budgeting:

- **RPC HTTP** — credits/req/s (Helius, QuickNode)
- **Indexing / enriched APIs** — Birdeye CUs, Helius enhanced tx (100 credits/call class)
- **Websockets** — PumpPortal (free tier + SOL metered trades), Birdeye WS on Premium+
- **Social** — X dominates cost if used at scale
- **Streaming gRPC** — plan tier + bandwidth (Triton **$0.08/GB** cited vs credit models)

---

## 3) Latency / endpoint gap vs Pump.fun

Pump.fun events appear on-chain and via aggregators. Phase 0 likely path: **PumpPortal processed WS** + occasional RPC fetch.

### Evidence-backed buckets

| Path | Typical role | Latency / gap (indicative) | Source quality |
| --- | --- | --- | --- |
| PumpPortal WS (processed) | New mints, migrations, trades | FAQ: **&lt;100 ms behind gRPC** when client in NYC | Vendor FAQ |
| Public Solana RPC polling | Tx/account after event | **100ms–multiple seconds** depending on method/commitment; rate limits add jitter | Solana docs; **needs measurement** for MAL workload |
| Helius LaserStream / Yellowstone gRPC | Filtered tx/account streams | Vendor/marketing: “ultra-low latency”; third-party benchmarks show **~3 ms p50 deltas** between providers in controlled tests — not MAL-specific | [LaserStream docs](https://www.helius.dev/docs/laserstream), [Shreder vs Helius benchmark](https://shreder.xyz/benchmarks/shreder-fastlane-vs-helius-laserstream/) (vendor; treat as directional) |
| Self-hosted / dedicated gRPC + colo | Fastest observation + exec | **Sub-100 ms slot-to-client** claimed by some providers in EU colo; highly deployment-dependent | **Needs measurement** (Subglow/Triton marketing comparisons) |
| Jito bundles / private relays | **Execution** landing, not discovery | Improves inclusion vs public mempool; cost in tips + infra | Defer until exec DEC |

### What becomes a paid problem later

When **speed-to-correct-decision** (not just “see mint eventually”) matters for PnL:

1. **Trade WS metered costs** — watching thousands of CAs on PumpPortal
2. **RPC rate limits** — `getTransaction` / `getSignaturesForAddress` heavy backfills
3. **Commitment level** — `processed` vs `confirmed` for gate consistency
4. **gRPC vs WS** — shaving tens of ms for competing on same tick
5. **Colocation** — client geography vs PumpPortal FAQ (NYC reference)
6. **Send path** — staked RPC, Jito, priority fees (QuickNode/Helius send limits scale with tier)

### Metrics to collect in lab (phase 0)

Record on each ingest (no secrets):

- `t_ws` — PumpPortal message receipt
- `t_rpc_confirm` — same signature via RPC (if fetched)
- `t_decision` — hot packet emit time
- **Δ_ws_rpc**, **Δ_decision_ws** at 1s/5s/15s/30s/60s windows
- Count **429/403**, WS disconnects/hour

Without these logs, vendor latency claims stay **unknown** for MAL.

---

## 4) Recommendations

### Phase-0 minimum (aligns with constitution)

1. **PumpPortal** single WS: `subscribeNewToken` (+ migration if needed).
2. **Solana RPC:** start **public**; switch to **Helius free** on throttle.
3. **Skip** X API, Birdeye paid, gRPC, dedicated nodes, Jito send path.
4. **Optional:** Dexscreener for human/debug enrichment only (respect 60/300 rpm).
5. **Log latency** metrics above into EXP registry before any infra upgrade DEC.

### Defer

- X/Twitter official API until H-social has manual or scraped **sample** with labeled regimes
- Birdeye/Business indexing unless graph/scout hypothesis needs CU-heavy wallet graphs
- Helius Business+ / Triton dedicated / colo until observe-only logs show **missed decisions** correlated with Δ latency
- PumpPortal Lightning trading API until risk gate + paper/live DEC

### When to leave free RPC (decision criteria)

Upgrade (typically **~$49/mo** tier) when **measured** over ≥7 days:

- **&gt;1%** of target events followed by failed RPC fetch (429/timeout) **or**
- Backfill lag **&gt;2s p95** at `confirmed` for gate-relevant reads **or**
- Scout cannot maintain capped hot packet rate during peak Pump.fun hours

Document outcome in `DEC/` + `EXP-xxx`.

### When to consider gRPC / serious latency (**post phase 0**)

- Paper/live shows PnL sensitivity to **&lt;500 ms** decision delay **and**
- PumpPortal+free RPC p95 gap exceeds strategy tolerance **and**
- Metered trade WS SOL cost &lt; expected edge (explicit arithmetic in EXP)

---

## Sources (primary)

- PumpPortal: https://pumpportal.fun/data-api/real-time/ , https://pumpportal.fun/FAQ/ , https://pumpportal.fun/trading-api/
- Solana clusters/RPC: https://solana.com/docs/references/clusters , https://solana.com/docs/rpc , https://solana.com/docs/tools/production-readiness
- Helius: https://www.helius.dev/pricing , https://www.helius.dev/docs/laserstream
- QuickNode: https://www.quicknode.com/pricing
- Triton: https://triton.one/pricing
- Dexscreener: https://docs.dexscreener.com/api/reference
- Birdeye: https://docs.birdeye.so/docs/pricing
- X API pricing context: https://techcrunch.com/2025/10/21/x-is-testing-a-pay-per-use-pricing-model-for-its-api/ , https://x-preview.mintlify.app/x-api/getting-started/pricing

## Unknown / needs measurement

- MAL-specific p50/p95 **Δ_ws_rpc** during peak meme hours (WSL vs bare metal)
- Total SOL burn on PumpPortal metered streams for Scout subscription patterns
- Whether Dexscreener lag vs PumpPortal WS matters for any hypothesis at 1s–60s windows
- X pay-per-use effective $/read at reassess-only volumes (console pricing moves)
