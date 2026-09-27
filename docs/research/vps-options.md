---
cursor:
  subagentId: "bc-664f0d2b-c3b6-5754-860c-884270cae4c8"
---

# VPS for MAL — buy Frankfurt, not a bigger Virginia box

**As-of 2026-09-27.** Research only. No order placed. Prices are vendor pages that day unless a date is on the citation. Re-check stock and the Helius meter before paying.

## Recommendation

**Buy an OVHcloud VPS-4 in Frankfurt (Limburg), not US East.** 8 shared vCores, 24 GB RAM, 200 GB NVMe, unlimited traffic, up to 3 Gbps. **$23.37/month** on a 12-month term, or **$27.50** month-to-month ([OVH US VPS](https://us.ovhcloud.com/vps/), [VPSBenchmarks trial 18 Sep 2026](https://www.vpsbenchmarks.com/trials/ovhcloud_us_performance_trial_18Sep2026)).

**Data feed: do not buy a new firehose.** Keep the public `logsSubscribe` tape on Oracle. On the Helius Developer plan already paid ($49, 10M credits), add **`preprocessedSubscribe` only for a wallet watchlist and for creates you already get free from PumpPortal**. Cap any credit overage at **$25**. A program-wide bonding stream is the experiment, not the default: one measured hour was ~1,700 bonding trades/min, which extrapolates to ~7M credits/month at 0.1 credit/message and blows the 3M live-feed budget.

**Monthly cost of this decision: about $28–53.** That is the VPS plus at most $25 of Helius overage. **All-in with the existing Helius Developer plan: about $77–102.** LaserStream ($499), Helius shreds ($800–1,000/IP), and Triton shreds (from $450) stay off the list.

**Runner-up:** Cherry Servers Cloud VDS 2 in **Amsterdam**, €29/month promo or €49 list, 4 dedicated vCores on 2 physical cores, 16 GB, 100 GB NVMe ([Cherry](https://www.cherryservers.com/pricing/virtual-servers/g1-4-16gb-100nv-ded)). Dedicated cores in a real Solana city. On 2026-09-27 Amsterdam and Frankfurt showed **0 in stock**; Chicago was in stock and is the wrong city. Do not buy Chicago to “have a server today.”

Virginia does not fix fills. The public tape’s 1.3–1.5 s p50 is confirmation lag and RPC queueing. Ashburn to Frankfurt is ~93 ms. Moving the box saves that RTT and nothing like a second.

## Comparison

| Option | Where | CPU | RAM | Price | Verdict |
| --- | --- | --- | --- | --- | --- |
| **OVH VPS-4** | Frankfurt (Limburg) | 8 shared vCores, x86 | 24 GB | $23–28/mo | **Buy.** Right metro, enough RAM for light side projects. |
| OVH VPS-4 | Vint Hill, Virginia | same | 24 GB | same | Same machine, wrong continent. ~1–5 ms from the Oracle Ashburn box. |
| Cherry Cloud VDS 2 | Amsterdam | 4 dedicated vCores (2 physical) | 16 GB | €29 promo / €49 | **Runner-up** when AMS is in stock. |
| Hetzner CX43 | Falkenstein, Nuremberg, Helsinki only | 8 shared | 16 GB | €16.49 | Right idea, **not orderable** (sold out since Jun 2026). No US East on this line. |
| Hetzner CPX42 | Germany / Finland | 8 shared AMD | 16 GB | ~€70 | Over the ceiling. Ashburn Cloud is this pricier line, not CX. |
| Hetzner AX41-LTD / AX42 | Falkenstein or Helsinki | dedicated | 64 GB class | €57–97 + setup on newer SKUs | Bare metal near Germany, no data budget left, no US East. |
| Contabo Cloud VPS M | EU or US | 6 shared | 16 GB | ~$15 | Cheap and oversold. Skip for a feed that cares about tails. |
| Vultr high frequency | NJ, Chicago, FRA, AMS | 4–8 vCPU | 16 GB | ~$96 | Good cities, over the ceiling. |
| Latitude.sh vm.small | several metros | 4 vCPU VM | 16 GB | $69 | Still a VM, over the ceiling. Metal is more. |
| Teraswitch bare metal | Chicago, Amsterdam | dedicated | — | above $60; no 16 GB SKU in budget | Hosts a large share of stake. Right building, wrong bill. |

Leftover money goes to the Helius meter, not to a 32 GB box. The tape process measured on 2026-09-25 was ~7% of one core and 42 MB RSS.

## 1. Where the chain and the relays sit

Stake is in Western Europe, not Ashburn.

[Validators Solutions, updated 19 Sep 2026](https://validators.solutions/en/validators/countries/): Frankfurt ~109 validators and **28% of stake**, Amsterdam ~77 validators and **14%**, New York ~4%, Ashburn ~3%, Chicago ~1%. Leader-slot pages the same week put Frankfurt near **29–35%** of slots. A Sep 2026 snapshot put Europe at **73%** of leader slots and Frankfurt plus Amsterdam near **half of blocks** ([CryptoBriefing, early Sep 2026](https://cryptobriefing.com/europe-solana-leader-slots-germany-dominance/)). [Glassnode’s Solana latency notes, Sep 2026](https://latency.glassnode.com/solana/about): Frankfurt and Amsterdam produce close to half of slots; Teraswitch alone hosts close to **30%** of stake; Jito’s block engines run on Teraswitch; a Chicago probe saw **0.965 ms** to a validator in the same building. That sub-millisecond figure is colocation with the small Chicago stake, not with the leaders.

**Jito block engines** ([docs.jito.wtf](https://docs.jito.wtf/lowlatencytxnsend/), fetched 2026-09-27): Amsterdam, Dublin, Frankfurt, London, New York, Salt Lake City, Singapore, Tokyo. No Ashburn, no Chicago, no Virginia. **Jito ShredStream shut down 5 Sep 2026.** Do not plan on it.

**Helius LaserStream** regions ([gRPC docs](https://www.helius.dev/docs/laserstream/grpc)): Newark (EWR), Pittsburgh, Salt Lake, Los Angeles, London, Amsterdam, Frankfurt, Tokyo, Singapore. Mainnet gRPC is Business **$499** or Professional **$999**. Developer is devnet gRPC only ([plans](https://www.helius.dev/docs/billing/plans)).

**Triton:** Dragon’s Mouth / Riptide gRPC is pay-as-you-go at **$0.08/GB** after a **$125** prepaid deposit (12 months, not a monthly fee). Shred streaming is **from $450/month** or **$0.18 per 15 minutes** per IP per data centre, in New York, London, Amsterdam, Frankfurt, and Tokyo ([triton.one/pricing](https://www.triton.one/pricing), [shred guide, updated 26 Aug 2026](https://blog.triton.one/getting-started-with-triton-shred-streaming/)).

**DoubleZero** (Glassnode, as of Jun 2026): on the order of half of staked SOL. Exchange points sit in the same metros as the validators. It does not pull the network to Virginia.

### Realistic RTT

These are network RTTs, not chain-to-receive lag.

| Path | RTT | Why it matters |
| --- | --- | --- |
| Ashburn ↔ Frankfurt | **~93 ms** | [AWS us-east-1 to eu-central-1](https://www.economize.cloud/resources/aws/latency/us-east-1-vs-eu-central-1/). This is Oracle or Vint Hill to the main leaders. |
| Ashburn ↔ Dublin / London | **~70–80 ms** | us-east-1 to eu-west-1 ~70 ms on the same tables. Amsterdam is in that band, a bit under Frankfurt. |
| Ashburn ↔ Newark / New York | **~5–10 ms** | Helius EWR and Jito NY. Close to Virginia. Far from most leaders. |
| Ashburn ↔ Chicago | **~20–25 ms** | Next US metro. Chicago stake is ~1%. |
| Frankfurt ↔ Amsterdam | **~7–12 ms** | Same continent. A Frankfurt or Amsterdam box reaches both leader clusters and both Jito engines in one RTT. |
| Inside Frankfurt or Amsterdam | **under ~2 ms**, and under 1 ms if you are on the same bare-metal fabric | Teraswitch colo. Not available at this budget. |

OVH’s US East region is **Vint Hill, Virginia** (`us-east-vin`), opened 2018, marketed as Washington DC. The Frankfurt region is **Limburg** (`eu-central` / Frankfurt (Limburg)), also a 1-AZ site ([OVH locations](https://us.ovhcloud.com/about/global-infrastructure/locations/), [region table](https://www.ovhcloud.com/en/about-us/global-infrastructure/expansion-regions-az/)). Limburg is in the Frankfurt metro. Vint Hill is the next county over from Ashburn. Buying US East puts the new box next to the free box you already have.

Hetzner Cloud does have Ashburn, but only on the regular (CPX) and dedicated (CCX) lines. The cheap CX/CAX line is Falkenstein, Nuremberg, and Helsinki only, and it was **not orderable** as of early September 2026 ([Hetzner cloud](https://www.hetzner.com/cloud), [price table as of 4 Sep 2026](https://agentdeals.dev/hetzner-pricing-2026)).

## 2. Is OVH VPS-4 the right box?

**The Frankfurt one is good enough. The Virginia one is not.**

- **Shared, not dedicated.** VPSBenchmarks lists Shared CPU yes, Dedicated CPU no. The 18 Sep 2026 US trial (that one was **Oregon**, not Virginia) reported the KVM CPUID as Intel Haswell. Treat the core as a shared vCore with possible steal. OVH’s own Intel VPS copy says vCores on a shared isolated host, versus a dedicated server for unshared hardware.
- **Noisy neighbours.** Real, and they show up as jitter, not as a lack of RAM. This job does not need 8 quiet cores. It needs a stable path to Frankfurt. A 24 GB shared box is the right trade. Cherry’s dedicated vCores are the upgrade if Amsterdam comes back in stock and you still want them.
- **Network.** Own backbone, anti-DDoS included, up to 3 Gbps, unlimited traffic outside Asia-Pacific. Fine for a websocket. It is not a Teraswitch cross-connect. Expect a few milliseconds to Frankfurt peers, versus ~93 ms from Vint Hill.
- **x86.** Oracle is aarch64. Do not copy the venv. Rebuild it. LightGBM and the rest of the Python stack have x86 wheels. Side projects that assume ARM images need a check; most do not.
- **Resize trap.** OVH says a VPS ordered **outside US regions cannot be upgraded in place**. Frankfurt is that case. VPS-4 is already 24 GB, which is the size you asked for. If you outgrow it, order another box and move. Do not buy Vint Hill just to keep the resize button.
- **Disk.** 200 GB. The priced tape is ~3.6 GB/day compressed. Do not put 35 days of PumpSwap on this disk. Oracle keeps the archive.

Hetzner CX43 (8 shared, 16 GB, €16.49, Germany or Finland) would have been the price winner and is close enough to Frankfurt (Falkenstein/Nuremberg are single-digit to low-teens milliseconds from FRA). It is out of stock. The orderable 16 GB shared plan, CPX42, is about €70 and breaks the $60 cap before any data. Dedicated Hetzner (AX line, Falkenstein/Helsinki, no US) starts around €57–97 and spends the whole budget on hardware the listener will not use.

Contabo’s ~$15 16 GB VPS loses on the thing we are buying: a tail that is already 7–16 s on a public RPC. Vultr (~$96), Latitude ($69), ERPC shared gRPC (hundreds of euros; Burst is **€398/month**, [4 May 2026](https://erpc.global/en/news/2026/05/04/erpc-geyser-grpc-burst-plan-202605/)), and Teraswitch metal are the correct cities at the wrong price.

## 3. The feed, not the box

Today, chain-to-receive on public `logsSubscribe` at `confirmed` is **p50 1.3–1.5 s**, **p99 from ~2 s to 7–16 s** (31 min window p99 1.99 s; later 10 min windows p99 7–13 s; [trade tape, 25 Sep 2026](../../ARTIFACTS/lab/trade-tape.md)). A 4-minute Helius `logsSubscribe` shadow was **p50 1.21 s / p99 1.81 s**, about **0.2 s** ahead of public, at **~172k credits/hour**. That is ~124M credits/month. The Developer plan is 10M. The shadow was stopped. Full-program `transactionSubscribe` is the same meter (2 credits per 0.1 MB) on a fatter payload.

What is actually inside the budget:

| Feed | Price | What you should expect vs 1.3–1.5 s / 2–16 s |
| --- | --- | --- |
| Public `logsSubscribe` on Oracle | $0 | Keep it. It is the priced tape. |
| PumpPortal `subscribeNewToken` | $0 | Creates only. Vendor FAQ: under 100 ms behind gRPC from NYC. Measure it. This is the create alarm, not the trade tape. |
| Helius `preprocessedSubscribe` | **0.1 credit/message**, all paid plans, account filter required, max 5,000 accounts ([overview](https://www.helius.dev/docs/preprocessed-transactions/overview)). Helius also still documents an older shred product at 2 credits/0.1 MB on Professional only. **Meter the first hour and kill it if the burn looks like the logs firehose.** | Helius says decoded shreds arrive **~8 ms before `processed`**, and they do **not** include logs, balances, or errors. Versus public `confirmed`, the win is the commitment step, not 8 ms. **Plan on p50 about 0.3–0.7 s and p99 under ~1.5 s if the host is in Frankfurt and the filter stays narrow. That is an estimate. Sub-100 ms p50 is the shred product we are not buying.** |
| Helius LaserStream WebSocket | Same 2 credits/0.1 MB. Included on Developer for extensions. | Vendor: **~200 ms** faster than Agave websockets. That turns 1.3 s into ~1.1 s. Same credit problem as the rejected firehose. |
| Helius LaserStream gRPC | $499 / $999 | Out. |
| Helius shred seat | $1,000/IP, $800 on Pro | Out. |
| Triton Riptide / Yellowstone | $0.08/GB, $125 deposit | Right product, wrong shape for the full tape. The logs shadow was ~200 GB/day. At $0.08/GB that is on the order of **$500/month**. A capped, filtered stream is the only version that fits. Do not turn on an unfiltered program subscribe. |
| Triton shreds | from $450/mo | Out. |
| Jito ShredStream | shut down 5 Sep 2026 | Gone. |

**Send path, when live exists.** It does not exist yet. Paper sends are simulated.

- **Staked send is already on the Developer plan.** `mainnet.helius-rpc.com` uses staked connections by default for paid plans. `sendTransaction` is **1 credit**. The old `staked.helius-rpc.com` host is deprecated. Stake-weighted QoS reserves most leader TPU capacity for staked peers ([Helius endpoints](https://www.helius.dev/docs/api-reference/endpoints), [Glassnode](https://latency.glassnode.com/solana/about)). This is the send to use. It does not add a tip.
- **Jito tips are not required** for a single direct buy. They are required for bundles. Minimum bundle tip is **1,000 lamports**, and Jito says that minimum loses when the auction is hot ([low-latency send](https://docs.jito.wtf/lowlatencytxnsend/)). A tip is extra SOL on top of the priority fee and on top of the 1.25% venue fee. Point sends at `frankfurt.mainnet.block-engine.jito.wtf` from the Limburg box. Helius Sender is documented at **0 credits** and races validators plus Jito; confirm it is enabled on this Developer key before counting on it.
- **Priority fee does not go away.** The paper constant is 0.001 SOL per side. Geography does not change it.
- **The signer is not on this server.** An unsigned intent still has to travel to the owner’s machine and the signed transaction has to travel back. If that machine is in the US, add ~90 ms each way on top of a Frankfurt box. That is acceptable next to 1.3 s. It is not free.

## 4. What speed actually buys

The honest paper gross edge is about **+0.2% per trade** before fees (`laya_0.7` +0.19%, `migrate_tp50_sl30` +0.20% on the honest-latency slice). The round-trip floor at 0.05 SOL is **7.46%**: **3.46%** portal plus venue, **4%** from 0.001 SOL priority each side. At 0.5 SOL the priority slice falls to 0.4% and the floor is **~3.86%**. Direct instructions drop the 0.5%/side portal fee and leave venue 1.25%/side, about **2.48%** round trip, roughly **1 percentage point** under the portal path. None of those rates are latency.

| Piece | Does a faster feed reduce it? |
| --- | --- |
| **Entry price** | Only on trades decided in the first 1–2 seconds. Followers are in at about **p50 180 ms**. Copy EV is gone by about **2 s**. At a 1.4 s receive you are behind that crowd. Seeing the event at 0.3–0.7 s can change the curve price you would have paid. **How many percent is unmeasured.** +0.2% at the slow clock is not evidence of a 7% edge at a fast clock. |
| **Slippage** | Same mechanism. You slip less only if you land before the curve moves. It is not a separate discount. |
| **Failed sends** | A staked send, and Jito if you choose to tip, can cut misses. The paper’s fail mix is a model, not a measured landing log. Tips and priority fees are the cost of landing, so this can **raise** fees while cutting fails. A dropped send is cheaper than a revert that burns the priority fee. |
| **The fees themselves** | **No.** Portal, venue, and the 0.001 SOL priority constant do not shrink because the packet arrived sooner. Direct routing removes portal. A smaller priority fee is a choice that may increase fails. Neither one is a VPS. |

**Newly testable under ~300 ms receive, and dead at ~1.4 s:**

- Entering in the first second after create, while the median follower is still arriving (~180 ms) and before copy EV dies (~2 s).
- Following a watched wallet in the same or next slot. Copy bots are already in within about a second. A 1.4 s tape is a replay of their fill, not a signal.

**Not newly saved:** 30 s holds, migration rules, and LAYA at the current clock. Those already had a chance to show gross edge and topped out near +0.2%. A Frankfurt RTT does not turn that into 7%. Use the faster clock to **paper** the first-second strategies. Kill them if gross at that clock is still near zero. Do not go live on the move.

## 5. Migration off Oracle

Oracle stays up. It is free, it has the disk, and the priced tape is already there. The VPS is a second host for the fast clock and for light side projects, not a cutover weekend.

**Move, or rather start fresh on the VPS**

- A new listener for PumpPortal creates plus Helius `preprocessedSubscribe` on the watchlist. New unit. Do not point it at the two-program logs firehose.
- Nothing else until that listener’s `t_recv` on the same signatures beats Oracle by the amount you expect.

**Leave on Oracle**

- `mal-trade-tape.service` — public `logsSubscribe`, the complete priced record.
- `mal-observe.service` — until an A/B says the EU create socket is actually earlier.
- `mal-forward-paper.service` — stays on the Oracle tape until the fast clock has been papered.
- `mal-funding-graph.service`, `mal-pump-backfill-resume.service` — backfill and archive. Latency does not matter.
- `mal-laya-v0.timer` (04:15 UTC) and `mal-attention-daily.timer` (04:45 UTC) — batch. Training spikes should not share a CPU with the listener.
- `mal-attention.service` — light, and not on the fill path.
- Postgres, `/var/lib/mal/sealed`, the 150 GB data volume. Keep the ~35 day tape here.

**Downtime:** none if you do it this way. Run both for a day, join on signature, and only then let forward-paper read the fast timestamps. Do not stop `mal-trade-tape` to “migrate” it.

**Keys:** no trading key on the VPS, same rule as Oracle. The server emits an unsigned intent. The owner’s machine signs. No key material in systemd, env files, or disk on either host. Helius and SSH secrets stay in root-only or credential files, not in git.

**Practical:** new x86 venv, do not rsync `site-packages` from aarch64. Copy code, not the database. Keep the Cloudflare tunnel pattern; do not open port 22. Cap side projects with the same memory limits used on Oracle so a toy job cannot stall the listener.

## Sources

- OVH VPS 2027 prices and the no-upgrade rule outside the US: https://us.ovhcloud.com/vps/ (fetched 2026-09-27). Vint Hill and Frankfurt (Limburg): https://us.ovhcloud.com/about/global-infrastructure/locations/ and https://www.ovhcloud.com/en/about-us/global-infrastructure/expansion-regions-az/. Shared CPU: https://www.vpsbenchmarks.com/hosters/ovhcloud_us/plans/vps-4-2027 and the 18 Sep 2026 trial.
- Hetzner locations and the unavailable cost-optimized line: https://www.hetzner.com/cloud. CX43 €16.49 and US CPX prices: https://agentdeals.dev/hetzner-pricing-2026 (page dated from a 4 Sep 2026 check). Dedicated list: https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/ (15 Jun 2026).
- Cherry Cloud VDS 2, stock checked 2026-09-27: https://www.cherryservers.com/pricing/virtual-servers/g1-4-16gb-100nv-ded. Latitude vm.small $69: https://www.latitude.sh/pricing. Vultr 16 GB ~$96 and Contabo ~$15: third-party 2026 comparisons (https://findstack.com/resources/16gb-ram-vps, https://cloudpricecheck.com/compare/hetzner-cloud-servers-vs-vultr-compute), not quotes to order from.
- Stake and cities: https://validators.solutions/en/validators/countries/ (19 Sep 2026), https://cryptobriefing.com/europe-solana-leader-slots-germany-dominance/ (early Sep 2026), https://latency.glassnode.com/solana/about (Sep 2026).
- Jito regions, 1,000-lamport minimum, ShredStream sunset 5 Sep 2026: https://docs.jito.wtf/lowlatencytxnsend/ and https://docs.jito.wtf/lowlatencytxnfeed/.
- Helius plans, LaserStream regions, preprocessed 0.1 credit/message, staked send: https://www.helius.dev/docs/billing/plans, https://www.helius.dev/docs/laserstream/grpc, https://www.helius.dev/docs/preprocessed-transactions/overview, https://www.helius.dev/docs/api-reference/endpoints.
- Triton: https://www.triton.one/pricing and https://blog.triton.one/getting-started-with-triton-shred-streaming/ (26 Aug 2026).
- Ashburn–Frankfurt RTT ~93 ms: https://www.economize.cloud/resources/aws/latency/us-east-1-vs-eu-central-1/.
- MAL measurements: `../../ARTIFACTS/lab/trade-tape.md` (25 Sep 2026), `../../ARTIFACTS/lab/gross-edge-2026-09-27.md`, `../project-context.md`.
