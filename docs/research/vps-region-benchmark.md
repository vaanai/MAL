---
cursor:
  subagentId: "bc-eaa48045-1f88-5c55-b5c7-517adfb30c94"
---

# VPS region — phase 1 feed and send

**As-of 2026-09-27.** Research only. No order placed. Prices are the OVH US public catalog and the Cherry product page fetched that day.

**Buy month-to-month in Frankfurt (Limburg), because the phase-1 feed and the staked send both land on `beta.helius-rpc.com`, and that anycast address is on TeraSwitch Frankfurt (`fra2`) at 1.4 ms from an OVH Frankfurt probe.**

**Runner-up:** Cherry Servers Cloud VDS 2 in **Amsterdam**, €29/month promo or €49 list, 4 dedicated vCores on 2 physical cores, 16 GB, 100 GB NVMe. Amsterdam is not an OVH VPS-4 region. The Cherry page fetched the same day showed **0 in stock** in Amsterdam (and in Frankfurt). Chicago was in stock and is the wrong city.

## What phase 1 actually calls

Phase 1 is the Helius Developer plan already paid ($49). No LaserStream gRPC, no shred seat, no dedicated node, no Jito tip until a later bundle.

| Role | Call | Hostname | Where a Frankfurt client lands | Where an Ashburn client lands |
| --- | --- | --- | --- | --- |
| **Feed** | `preprocessedSubscribe` | `wss://beta.helius-rpc.com` only | TeraSwitch `e36.er100a-fra2-bb.teraswitch.com`, then `198.13.128.10` at **0.85 ms** | TeraSwitch `e33.er100b-ewr2-bb.teraswitch.com`, then the same IP at **7.07 ms** |
| **Send** | `sendTransaction`, staked by default on paid plans, 1 credit | Same `https://beta.helius-rpc.com` | Same Frankfurt anycast site | Same Newark anycast site |

`preprocessedSubscribe` is documented only on the Gatekeeper host, all paid plans, 0.1 credit per message, account filter required ([subscribe](https://www.helius.dev/docs/preprocessed-transactions/preprocessed-subscribe), [overview](https://www.helius.dev/docs/preprocessed-transactions/overview)). The endpoints page lists `beta.helius-rpc.com` with `mainnet.helius-rpc.com` as a mainnet URL that uses staked connections by default ([endpoints](https://www.helius.dev/docs/api-reference/endpoints)). One hostname covers the feed and the send. The old `staked.helius-rpc.com` name is deprecated and, from this probe, is the same Cloudflare pair as `mainnet`.

There is no region pin on the Developer plan. Helius says shared RPC URLs go to the closest server, and that a specific region is an enterprise ask ([RPC FAQ](https://www.helius.dev/docs/faqs/rpc)). Gatekeeper “terminates connections at geographically distributed edge locations” ([Gatekeeper](https://www.helius.dev/docs/gatekeeper)). The city is whichever edge the VPS is closest to.

## Measurements (2026-09-27)

DNS is one A record everywhere it was checked. check-host request `4db33efbke05` returned only `198.13.128.10` from Austria, Australia, Brazil, Canada, Switzerland, Germany (three nodes), Spain, Finland, France, Hong Kong, and the other nodes in that result. It is not a geo-DNS name with a different IP per city.

ipinfo labels `198.13.128.10` as Pittsburgh, TeraSwitch (AS20326). The traceroute says otherwise. The same IP is announced in more than one city:

| Probe | Path to `198.13.128.10` | RTT |
| --- | --- | --- |
| Frankfurt, Oracle | last named hop `e36.er100a-fra2-bb.teraswitch.com` | **0.85 ms** (traceroute), ping avg **1.004 ms** |
| Frankfurt, OVH | ping only | **1.445 ms** avg (min 1.440, max 1.452) |
| Frankfurt, SYNLINQ | ping | **0.608 ms** avg |
| Falkenstein, Hetzner | ping | **5.783 ms** avg |
| Nuremberg, netcup | ping | **3.503 ms** avg |
| Amsterdam, LeaseWeb | ping | **0.817 ms** avg |
| Amsterdam, Oracle | traceroute | **1.14 ms** |
| London, Oracle | ping | **1.125 ms** avg |
| New York, DigitalOcean | ping | **1.632 ms** avg |
| Ashburn, Oracle (AWS us-east-1, this environment’s egress) | last named hop `e33.er100b-ewr2-bb.teraswitch.com` | **7.05 ms** avg |

Globalping ids: ping `2vCWP1Bnwo0NouZAP00021D9I` and `2zNyW2Z3xA97aBb9P00021D9H`, traceroute `20U25ZNZsXJpgJXnG00021D9J`. HTTPS GET of `https://beta.helius-rpc.com/` returns 401 at the edge (`zid` header, no `cf-ray`). Globalping `2lfcfdABuJbsg6PCb00021D9J`: Frankfurt Oracle TCP 1 ms, first byte 1 ms; Ashburn Oracle TCP 6 ms, first byte 7 ms. The 401 is generated at the local edge.

`mainnet.helius-rpc.com` is Cloudflare, not that anycast IP. One GET each (Globalping `2QhJGdVEAu74gMgdJ00021D9J`, plus a curl from this Ashburn VM):

| Probe | `cf-ray` colo | TCP | First byte |
| --- | --- | --- | --- |
| Frankfurt, Oracle | **FRA** | 1 ms | 10 ms |
| Amsterdam, DELUXHOST | **AMS** | 9 ms | 15 ms |
| Ashburn, Oracle | **IAD** | 1 ms | 63 ms |
| This VM, Ashburn AWS | **IAD** (`a418a9d52d3ad6e5-IAD`) | not split out | not split out |

Use `beta` for phase 1. `mainnet` is the other documented staked host, and from Frankfurt its Cloudflare edge is FRA. The 63 ms first byte from Ashburn is one GET of a 404, not a ping.

Helius’s own RPC city list, which is not a pin you can select: Tokyo, Singapore, Pittsburgh, Newark, Salt Lake City, Los Angeles, Vancouver, Dublin, London, Amsterdam, Frankfurt ([RPC FAQ](https://www.helius.dev/docs/faqs/rpc)). Newark and Frankfurt match the two TeraSwitch edges measured above.

### Not measured

- No box was ordered, so nothing was timed from inside OVH Limburg (`DE`) or Vint Hill (`US-EAST-VA`). The “Frankfurt, OVH” row is a Globalping probe tagged Frankfurt on the OVH network, at 1.445 ms. The Limburg building-to-`fra2` hop was not measured on its own.
- No Helius API key was used. `preprocessedSubscribe` message delay behind the edge was not measured. The numbers above are ICMP, traceroute, and the TLS edge that answers 401.
- ShredStream UDP was not probed.
- RIPE Atlas was not queried. The figures are Globalping and check-host, plus DNS, ipinfo, and one curl from Ashburn.

## OVH VPS-4, what you can actually order

US public catalog `https://api.us.ovhcloud.com/1.0/order/catalog/public/vps?ovhSubsidiary=US`, catalog id 3701, plan `vps-2027-model4` / `vps-2027-model4-eu`. Product blob: 8 vCores, 24 GB, 200 GB NVMe, 3 Gbps, unlimited traffic, KVM.

| Term | Price |
| --- | --- |
| Month-to-month renew | **$27.50** |
| 6-month | $26.12 |
| 12-month | $23.37 |
| Local storage addon | $0 |
| Linux | $0 |
| Automated backup, required family, cheapest (1-day) | **$1.20** |
| Automated backup, 7-day | $5.90 |
| Snapshot, optional | $1.70 |

Month-to-month with the cheapest required backup is **$28.70**. Skip the snapshot. That is inside the $25–30 new-cash band. With the existing $49 Developer plan and at most $25 of credit overage, all-in is about **$78–103**.

Datacenter codes on that plan:

| Code | Where | VPS-4? |
| --- | --- | --- |
| `DE` | Germany. OVH’s location table calls this **Frankfurt (Limburg)** ([locations](https://us.ovhcloud.com/about/global-infrastructure/locations/)). | **Yes. This is the order.** |
| `UK` | London (Erith) | Yes, same price. A London Oracle probe saw the feed IP at 1.125 ms. A London OVH probe saw the Frankfurt Jito engine at 15.3 ms. The measured OVH attachment that sits on `fra2` is the `DE` code. |
| `US-EAST-VA` | Washington DC (Vint Hill, Virginia) | Yes, same price. The Ashburn probe, the next county over, reached the feed via Newark `ewr2` at 7.05 ms, and `mainnet` terminated at Cloudflare IAD. |
| `US-WEST-OR` | Hillsboro, Oregon | Yes. Farther from both measured edges. |
| `GRA`, `SBG`, `EU-WEST-RBX`, `WAW`, `EU-SOUTH-MIL` | Gravelines, Strasbourg, Roubaix, Warsaw, Milan | Yes. Falkenstein was 5.8 ms and Nuremberg 3.5 ms to the feed IP, both slower than the Frankfurt probes. |
| `EU-WEST-LZ-AMS` | Amsterdam local zone | **No.** It appears only on `vps-2025-model1.LZ-eu` and `vps-2027-model2.LZ-eu` (4 vCores, 8 GB, 75 GB, $10 month-to-month). Not a VPS-4. |
| `US-EAST-LZ-NYC` | New York local zone | No. Same two small local-zone plans, US list only. |

## Jito, for later bundles only

Phase 1 does not tip. When bundles start, the send host is a block engine, not Helius. Current table on [low-latency send](https://docs.jito.wtf/lowlatencytxnsend/), fetched 2026-09-27:

| Location | URL | Resolved from Ashburn, ipinfo city |
| --- | --- | --- |
| Global | `https://mainnet.block-engine.jito.wtf` | `64.130.51.81`, Newark. This resolver only; other cities were not checked. |
| Amsterdam | `https://amsterdam.mainnet.block-engine.jito.wtf` | `64.130.52.210`, Amsterdam |
| Dublin | `https://dublin.mainnet.block-engine.jito.wtf` | `64.130.61.36`, Crumlin (Dublin) |
| Frankfurt | `https://frankfurt.mainnet.block-engine.jito.wtf` | `64.130.50.88`, Frankfurt |
| London | `https://london.mainnet.block-engine.jito.wtf` | `64.130.63.171`, London |
| New York | `https://ny.mainnet.block-engine.jito.wtf` | `64.130.51.81`, Newark |
| Salt Lake City | `https://slc.mainnet.block-engine.jito.wtf` | `64.130.53.56`, not re-geolocated beyond the name |
| Singapore | `https://singapore.mainnet.block-engine.jito.wtf` | `202.8.11.169` |
| Tokyo | `https://tokyo.mainnet.block-engine.jito.wtf` | `202.8.9.22` |

Ping, same day:

| Path | Avg |
| --- | --- |
| Frankfurt Oracle → `frankfurt.mainnet.block-engine.jito.wtf` | **0.802 ms** |
| Frankfurt OVH → same | **2.297 ms** |
| Ashburn Oracle → same | **83.0 ms** |
| Amsterdam DELUXHOST → same | **13.4 ms** |
| London OVH → same | **15.3 ms** (a first London sample was 78 ms; the repeat was 15.3 and 15.1 ms) |
| Ashburn Oracle → `ny.mainnet.block-engine.jito.wtf` | **7.28 ms** |
| Amsterdam LeaseWeb → NY engine | **72.8 ms** |
| Amsterdam Oracle → `amsterdam.mainnet.block-engine.jito.wtf` | **1.53 ms** |
| Ashburn Oracle → Amsterdam engine | **80.3 ms** |

From the Limburg order, the later bundle URL is `https://frankfurt.mainnet.block-engine.jito.wtf`. The Frankfurt OVH probe reached it in 2.3 ms. Minimum bundle tip on that doc is 1,000 lamports. That spend stays off until bundles exist.

**ShredStream.** The live page [Low Latency Block Updates (Shredstream)](https://docs.jito.wtf/lowlatencytxnfeed/) still carries this notice, fetched 2026-09-27: “The service will be completely shut down in 60 days (September 5, 2026).” That date is past. The page has not been rewritten into the past tense, and it still documents the proxy. UDP was not tested. Do not plan on ShredStream. The block-engine HTTPS hosts answered (`server: jito-block-engine`).

## Enhanced WebSocket

Included on Developer. The plans table calls it LaserStream WSS extensions (`transactionSubscribe`, enhanced `accountSubscribe`); standard methods are on every plan ([plans](https://www.helius.dev/docs/billing/plans), [websockets](https://www.helius.dev/docs/rpc/websocket)). Hosts are `wss://mainnet.helius-rpc.com` and `wss://beta.helius-rpc.com`. There is no separate region list. It is a post-execution stream, not `preprocessedSubscribe`. It uses the same two hostnames, so it does not move the city. Mainnet gRPC stays Business ($499) and is not on Gatekeeper.

Helius Sender (`fra-sender.helius-rpc.com` and the other regional sender names) is a different product, with a required tip. Phase 1 does not use it. For the record, `fra-sender.helius-rpc.com` resolved to `64.130.50.91`, ipinfo Frankfurt, TeraSwitch, the same network as the Frankfurt block engine.

## Runner-up, Amsterdam

OVH will not sell a VPS-4 in Amsterdam. The only Amsterdam code in the catalog is the local zone on the 8 GB plans.

Cherry Cloud VDS 2, [product page](https://www.cherryservers.com/pricing/virtual-servers/g1-4-16gb-100nv-ded), fetched 2026-09-27: Amsterdam €29/month promo, €49 list, €0.084/hour, 4 vCores at 2.1 GHz on 2 physical cores, 16 GB, 100 GB NVMe. **Amsterdam 0 in stock. Frankfurt 0 in stock.** Lithuania 11, Chicago 294, Singapore 119. An Amsterdam LeaseWeb probe reached the phase-1 feed IP in 0.817 ms, and an Amsterdam Oracle probe reached the Amsterdam block engine in 1.53 ms. That is why it is the runner-up, and why it is not the order while the count is zero.

## Sources

- Helius `preprocessedSubscribe`: https://www.helius.dev/docs/preprocessed-transactions/preprocessed-subscribe and https://www.helius.dev/docs/preprocessed-transactions/overview
- Helius endpoints (staked by default, `beta` and `mainnet`): https://www.helius.dev/docs/api-reference/endpoints
- Helius RPC cities and closest-server routing: https://www.helius.dev/docs/faqs/rpc
- Gatekeeper: https://www.helius.dev/docs/gatekeeper
- Plans, LaserStream WSS on Developer, gRPC mainnet on Business: https://www.helius.dev/docs/billing/plans and https://www.helius.dev/docs/rpc/websocket
- Jito block engines: https://docs.jito.wtf/lowlatencytxnsend/
- Jito ShredStream sunset notice, 5 Sep 2026: https://docs.jito.wtf/lowlatencytxnfeed/
- OVH US catalog, 2026-09-27: `https://api.us.ovhcloud.com/1.0/order/catalog/public/vps?ovhSubsidiary=US` (catalog 3701). Locations: https://us.ovhcloud.com/about/global-infrastructure/locations/
- Cherry: https://www.cherryservers.com/pricing/virtual-servers/g1-4-16gb-100nv-ded
- Probes: Globalping measurement ids cited inline; check-host DNS `4db33efbke05`; ipinfo on the resolved addresses.
