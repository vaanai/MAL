---
cursor:
  subagentId: "bc-52567b6c-6062-57f4-8ed5-f88e97db9e7d"
---

# MAL data budget — 2026-09-25

Web research only. Prices move; cite vendor pages before buying. Edge = **complete tape**, not sub-10ms. Host: Phoenix Always Free (2/12). gRPC/colo stay parked unless a stack below fails completeness.

**Volume used here:** ~15–34k pump.fun creates/day ([MAL EXP-004](https://github.com/vaanai/MAL) 13–15k; [Anaxer live stats](https://anaxer.com/pumpfun-api) ~34k). Trades **~1–3M/day** ([Aug 2026 press: >1M tx/day, ~$347M 30d avg](https://cryptobriefing.com/pumpfun-app-50m-daily-volume/); [Sep 2025 paper 2–5M](https://arxiv.org/html/2602.14860v1)). SOL **~$117** (2026-09-25). PumpPortal meter: **0.01 SOL / 10k msgs** = **1 SOL / 1M msgs**.

## Vendor snapshot (full pump.fun tape)

| Source | ~$/mo at our volume | Live | Historical | Latency | Limits / burn | Gotcha |
| --- | --- | --- | --- | --- | --- | --- |
| **Public RPC** `logsSubscribe` | $0 | Raw program logs if it holds | No | 100ms–s | 100 req/10s, 40/method, 100MB/30s | 429s; not production ([clusters](https://solana.com/docs/references/clusters)) |
| **PumpPortal** | Creates/migrations **$0**. Full trades **~30–90 SOL ≈ $3.5k–$10.5k** | Creates, migrations, **watchlist** trades | None | FAQ <100ms behind gRPC **from NYC** | 1 WS; 5k addrs/msg; key+0.02 SOL since May 1 2026 | **Not a firehose** — `keys[]` of mints/wallets. Full tape unaffordable. Phoenix extra RTT. Reconnects 1006/502 already seen. [docs](https://pumpportal.fun/data-api/real-time/) [fees](https://pumpportal.fun/fees/) |
| **Anaxer** | Free: creates+grads. Starter $39: 10 mints. **Pro $99: all trades** | Decoded creates, grads, **all** bonding+PumpSwap (+other DEX) trades; wallet, side, amounts, slot, ts | REST **7-day** window | Vendor **<450ms** from confirm | Flat; Pro 2.5M REST/mo | New vendor (2026). **No curve-progress ticks.** Wallet transfers “coming soon”. [api](https://anaxer.com/pumpfun-api) |
| **Helius** | Free 1M cr / 10 rps. **Dev $49 / 10M / 50 rps**. Biz **$499** (mainnet gRPC). Extra **$5/M cr**. Stream **~$100/TB** (20 cr/MB) | Free: std WSS. Dev: `transactionSubscribe` + webhooks + parsed events (beta free until 2026-09-21). gRPC replay 48h only on Biz+ | Archival RPC; `getTransactionsForAddress` 10 cr/100 txs | WSS tens–hundreds ms; gRPC faster (unmeasured here) | Webhook **1 cr/event** → 1–3M/day **burns 30–90M cr** ($150–$400+ overage). Filtered tx stream ~0.2–0.6 TB may **fit Dev 10M** | Don’t webhook the program. Decode txs yourself. gRPC is $499. [pricing](https://www.helius.dev/pricing) [credits](https://www.helius.dev/docs/billing/credits) [plans](https://www.helius.dev/docs/billing/plans) |
| **QuickNode** | Build **$49**. gRPC **$499 add-on** or Scale **$499** | RPC + Streams/webhooks | Archive on plan | Similar paid-RPC | gRPC **10 accounts/stream** on Scale | Account cap kills “all mints”. [pricing](https://www.quicknode.com/pricing) |
| **Triton** | **$125** prepaid/12mo. Stream **$0.08/GB** (~$16–$80 at 0.2–1 TB). RPC **+$10/M** | Yellowstone/Riptide/Fumarole **included** (no tier gate) | Old Faithful genesis | gRPC class | Filter or bandwidth explodes | Best **raw complete** tape per dollar. You decode. [pricing](https://triton.one/pricing) |
| **Shyft** | Free: 10 rps, **no gRPC**. **Build $199** unmetered gRPC | Yellowstone; 150k tx filters | Slot replay ~150 | gRPC class | Flat; 10 conns | No credit surprises. Still decode. [pricing](https://shyft.to/solana-rpc-grpc-pricing) |
| **Chainstack** | Growth **$49** + gRPC **$49–$149** | Geyser; ShredStream default | Archive on paid | gRPC class | **50 accounts/stream** | Same mint-cap trap. [pricing](https://chainstack.com/pricing/) |
| **Alchemy** | Free 30M CU. PAYG ~$0.45–0.53/M CU. gRPC **$75/TB** (not on free) | Yellowstone if PAYG | Full archive | gRPC ~ms class (vendor) | Broad filters → TB bills; spend cap **doesn’t** cover gRPC | Cheap **if** program-filtered. [gRPC](https://www.alchemy.com/solana-grpc) [support](https://support.alchemy.com/articles/5952597732-how-is-solana-grpc-yellowstone-streaming-priced) |
| **Syndica** | Free 10M RPC. Scale ~**$199** | ChainStream JSON-RPC WS | Partial | Fastest-wins WS | Enterprise opaque | Not pump-decoded. [overview](https://www.alchemy.com/overviews/solana-rpc) |
| **Bitquery** | Pro **$99** (DEX streams). Scale **$299**. Solana archive **+$210–$400** | Decoded pump.fun + PumpSwap (gRPC/WS) | Self-serve = **30d trades / 4–8h chain**. Archive add-on | gRPC <300ms (vendor) | Stream-min + GB overages | History **not** in the $99–$299 sticker. [pricing](https://bitquery.io/pricing) [pump API](https://bitquery.io/products/pumpfun-api) |
| **Solana Tracker** | Advanced **€50** REST. Datastream **€397** Premium | Indexed launches/trades/holders | REST history | Sub-second confirm (vendor) | WS only at Premium | Over our $300 cap. [docs](https://docs.solanatracker.io/pricing) |
| **Birdeye** | Lite **$39**. Premium **$199** (WS) | Indexed token/wallet/WS | Some | App-class, not Geyser | CU burn at full tape | Hard-deferred for spine. [pricing](https://birdeye.so/data-api/pricing) |
| **Dune** | Free 2.5k cr. Analyst **$75**. Plus **$399** | No | `dex_solana.trades` `project='pumpdotfun'` | Minutes | Export credits | Research, not recorder. [credits](https://docs.dune.com/resources/credits-billing/how-credits-work) |
| **Flipside** | — | — | Sold to SonarX May 2026; Flipspace shut Jun 17 | — | — | Skip. [SonarX](https://www.sonarx.com/blog/sonarx-acquires-flipside-crypto-blockchain-data-business) |
| **BigQuery** | 1 TB/mo free, then **~$6.25/TB** | No (~1–6h lag) | Public `crypto_solana_*` | Hours | Scan cost | Raw warehouse, not decoded pump ticks. |

**Also seen:** PumpFunData hourly parquet (docs 402; not budgetable). PumpDev WS funded by **0.25% trade fees** — skip.

## Ranked stacks

### 1) ~$0 — keep ingest, sample outcomes
**Buy:** nothing. **Run:** PumpPortal `subscribeNewToken` + `subscribeMigration` (already live) **+ Anaxer free** creations/graduations (slot+timestamp that Portal often lacks) **+** public RPC **+ Helius free** (1M cr, 10 rps) for capped `getTransaction` **+** Dune free / BigQuery 1 TB for sample SQL.

**Get:** every create + migration (two independent taps). Light marks. Sparse SQL history.

**Miss:** **every trade**, wallet tape, relationship graph, precise on-chain ts/slot on Portal-only rows, reliable backfill. Public `logsSubscribe` will 429 at 1–3M trades/day.

### 2) ~$50 — RPC headroom + DIY live tape
**Buy:** [Helius Developer $49](https://www.helius.dev/pricing). Keep Portal + Anaxer free creates.

**Get:** 50 rps, 10M credits, `transactionSubscribe` **filtered to pump.fun + PumpSwap programs** (likely **fits 10M cr** at ~0.2–0.6 TB; overage $5/M). Archival + `getTransactionsForAddress` for **selected** wallets. Webhooks OK for a **watchlist**, not the program.

**Miss:** decoded side/amount/price (you parse). **No** 7-day+ decoded history. **No** mainnet gRPC/48h replay. Full-program webhooks **blow the budget**. Curve internals only if you decode accounts.

### 3) ~$150–300 — cheap complete **live** tape
**Buy:** **Anaxer Pro $99** (unfiltered decoded trades) **+ Helius Developer $49** = **~$148**. Optional: Dune Analyst $75 or BigQuery overage for multi-month SQL. **Do not** add Portal metered trades.

**Get:** every create/grad; **every bonding + PumpSwap trade** (wallet, side, amounts, slot, ts) with a flat bill; 7-day REST holes; Helius for wallet history, marks, second live tap. Phoenix ARM is fine (WS, not colo).

**Miss:** genesis/year-scale **tick** backfill (Anaxer 7d; Dune/BQ are lagged/aggregated). **SOL/token transfer graph** (Anaxer transfers not shipped). Bonding **progress ticks**. Vendor-new risk on Anaxer.

**Alts in-band (raw, you decode):** Triton PAYG ~$50–150 stream; Shyft $199 unmetered gRPC; Alchemy gRPC ~$15–75/TB. **Out of band:** Helius Biz $499, QN gRPC $499, Tracker €397, Portal full tape $3.5k+.

## Pick: cheap but most data

**Anaxer Pro + Helius Developer (~$148/mo).** Only self-serve combo that buys a **decoded full live tape** without SOL-metering or $499 gRPC, and still gives RPC/archival for wallets. Measure Anaxer vs Portal on the same mint for 48h before cancelling Portal. If Anaxer drops events, swap the live leg to **Triton PAYG program-filtered gRPC** (still decode) and keep Helius.

Stay off Portal trade WS and Helius program webhooks — both price like a firehose. Historical **completeness** still needs a later warehouse (Dune/BQ/Bitquery archive), not this month’s recorder.
