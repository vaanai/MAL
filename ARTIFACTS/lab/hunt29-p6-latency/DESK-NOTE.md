# P6 LATENCY: is buying speed worth money? (hunt29, 2026-10-24)

Exploration screen numbers on already-mined August/September blocks, in-sample, best-of-many cells. **Not gate evidence. No line here is claimed to make money**; quant-proof decides. Declaration: DECLARE.md (sha256 e1bdea8c9a3a2089a7d5f3b31e5003e72df163421ce66e44401e35ecea1aaf76, 2026-10-24T00:47:37Z). Full table: TABLE.md.

## 1. Measured: the entry leg barely moves these lines
Only the deciding entry leg changed (1.9 / 1.0 / 0.6 / 0.3 s, k = 10 / 5 / 3 / 2 slots); send 160,000, V, both fail models, impact, rent 0, 0.25 SOL, feed delay 7 slots (about 1.5 s, NOT varied) and exit lag held fixed. Controls (1.9 s) reproduce the archived n, means, ex-top-3 and totals for all 9 lines (rel. 1e-9); only the bootstrap CI90 lower bound of the four hunt28 l4 cells differs, by 0.02% to 1.64% (RNG stream, same trips), so CI columns for those lines are not byte-equal to the archive.

| line | trades/day | flat SOL/trade 1.9 -> 1.0 -> 0.6 -> 0.3 s | gain 1.9 -> 0.3 s, flat / press (SOL/trade) | gain in SOL/day (flat, my arithmetic) | zero crossing, both models |
|---|---|---|---|---|---|
| FLOOR v2 | 13.3 | +0.01605 +0.01614 +0.01648 +0.01654 | +0.00049 / +0.00044 | +0.0065 | positive at every leg; none in grid |
| REVIVE r2h | 97.3 | -0.00296 -0.00271 -0.00258 -0.00253 | +0.00043 / +0.00040 | +0.042 | negative at every leg |
| REVIVE r6h | 32.6 | -0.00367 -0.00331 -0.00319 -0.00318 | +0.00049 / +0.00047 | +0.016 | negative at every leg |
| REVIVE r12h | 15.9 | -0.00276 -0.00229 -0.00209 -0.00195 | +0.00081 / +0.00071 | +0.013 | negative at every leg |
| w2-quiet R4 | 14.9 | +0.00070 +0.00069 +0.00068 +0.00067 | -0.00003 / +0.00009 | -0.0004 | positive at every leg, ex-top-3 negative at every leg |
| w2-quiet R9 | 40.8 | -0.00056 -0.00055 -0.00055 -0.00055 | +0.00001 / +0.00009 | +0.0004 | negative at every leg |
| w2b-strand DS-C1-g50 | 38.0 | +0.00386 +0.00383 +0.00383 +0.00386 | 0.00000 / +0.00001 | -0.0001 | positive at every leg, ex-top-3 about -11 SOL at every leg |
| BREADTH b5 | 3274 | -0.00887 -0.00876 -0.00872 -0.00867 | +0.00021 / +0.00018 | +0.67 | negative at every leg (-28 SOL/day at 0.3 s) |
| SELLABS s1 | 2863 | -0.00704 -0.00702 -0.00700 -0.00697 | +0.00007 / +0.00006 | +0.20 | negative at every leg (-20 SOL/day at 0.3 s) |

Reading, in words: no line crosses zero anywhere on the grid under either fail model. Lines positive at 1.9 s stay positive at every faster leg and lines negative stay negative. The whole move 1.9 -> 0.3 s is worth 0.0000 to +0.0008 SOL per trade (about 3% of FLOOR v2's mean; REVIVE gains 0.0004 to 0.0008 on means of -0.002 to -0.004). The ex-top-3 sign of every line is the same at all four legs, and days-positive counts move by at most 1-2 days. FLOOR v2 (24 h hold, take profit at 1.5x) and the slow-state lines are structurally latency-insensitive; the two fast-flow ideas I expected to be latency-bound (BREADTH b5, SELLABS s1) gain 0.0002 and 0.0001 SOL per trade against means 40 to 100 times larger in magnitude. Caveat: the feed delay (7 slots) is held, so this is the landing leg only; a faster feed would also cut the delay and is not measured here.

## 2. What a feed or landing upgrade would buy, in leg seconds (desk read; no spending, no sign-ups)
Every number is labelled with its source. "Vendor" = vendor claim, no independent test.

Baseline: MAL entry leg 1.3-1.9 s (library lib-a-A30); P15 measured MAL's DBC entry leg on mal-fast-0 at p50 about 1.1-1.3 s, p90 about 1.9-2.1 s (PR #866, as given by manager15, not re-read here). So the 1.9 s kit leg is about the measured p90 and 1.0 s is about the measured p50 minus 0.1-0.3 s. The kit's 1.9 s is therefore conservative, and the 1.9 -> 1.0 column is the realistic upper bound of "a typical day, p90 landing becomes p50 landing".

| Upgrade | Cost per month (source) | Leg effect (source) | Realistic leg |
|---|---|---|---|
| Staked send on `mainnet.helius-rpc.com` (already on Developer plan) | $0 extra, 1 credit per send (docs/research/vps-options.md s3) | no measured figure; SWQoS reserves leader capacity for staked peers (vendor) | already in use; no change counted |
| Helius Sender | 0 credits; tip minimum 0.001 SOL per send, about +0.84M lamports over the 160k per send, i.e. about 0.67% of a 0.25 SOL round trip if the tip replaces the fee (round45 s(f), my arithmetic: 2 x 840,000 / 0.25e9) | vendors claim median 0 slots; no independent benchmark (speed-route report, hunt5) | unmeasured; a tip of about 0.0017 SOL per round trip is about 3.4 times the 0.00049 SOL per trade FLOOR v2 gain from 1.9 to 0.3 s |
| Helius preprocessed feed | 0.1 credit per message, all paid plans (vps-options s3) | shreds about 8 ms ahead of processed (vendor); estimate p50 0.3-0.7 s feed, p99 under 1.5 s from Frankfurt (vps-options, labelled an estimate) | feed side, about -0.5 to -0.9 s vs 1.3-1.5 s public logs; not a leg change |
| Helius LaserStream gRPC | $499 Business / $999 Professional (vps-options s3) | audit measured the processed stream p50 1,012 ms ahead of the tip follower (audit-2026-10-08/SYNTHESIS.md:186); LaserStream WebSocket about 200 ms faster than Agave (vendor) | up to about 1.0 s on the FEED delay, not on landing; kit D = 7 slots (about 1.5 s) |
| Helius preconfirmations | 10 credits per message, 300-credit minimum per subscription per slot with messages (hunt13/helius-preconf, official) | 5-50 ms ahead of shreds (vendor) | negligible (0.01-0.1 slot) |
| Helius shreds | $1,000 per IP ($800 Professional) (vps-options) | sub-100 ms feed (vps-options) | feed only; out of budget |
| Triton shreds / Yellowstone | from $450; Yellowstone $0.08/GB, about $500/month at 200 GB/day (vps-options) | gRPC class | feed only |
| Jito ShredStream | shut down 2026-09-05 (vps-options, docs.jito.wtf) | n/a | none |
| DoubleZero Edge | launch-era $30-100 per device per epoch, valid through May 2026 only; no current price (speed-route) | vendor: 1.15 ms p50 ahead (RPC Fast Frankfurt test) | feed only; price unknown |
| Nozomi / Lunar / RPC Fast landing services | tip minimum 0.001 SOL per send (speed-route) | vendor: median 0 slots, p99 1 slot; RPC Fast about 89% slot 0 on 240+ migrations (vendor) | unverified; the cheap test is about 60 buys of 0.01 SOL, two arms, cap 0.15 SOL (round45 s(f); needs owner go and a DEC-019 Amendment 3) |
| Frankfurt bare metal | EUR 140-240 app tiers (vendor), EUR 580-980 validator-class (vendor) (speed-route) | vendor: under 0.2 ms to shred sources | already in Frankfurt (OVH, mal-fast-0) |
| Packages | mid $499; max about $2,000-5,600 per month; break-even 0.08-0.13 and 0.33-1.44 SOL/day (round45 s(f), SOL $130-200) | | |

Best realistic leg: about 1.0-1.1 s measured p50 today; a paid upgrade could at most move the typical landing to roughly 0.6-1.0 s (vendor-claimed, unverified) and the feed by up to about 1.0 s (measured, LaserStream class). The cheapest candidate that touches the landing leg is Sender / a landing service at a 0.001 SOL tip per send, about 0.67 pp of a 0.25 SOL round trip. The cheapest feed candidate that is measured is LaserStream gRPC at $499 per month (about 2.5-3.8 SOL per month at SOL $130-200, 0.08-0.13 SOL/day, round45).

## 3. Answer
On these nine lines, buying landing speed is not worth money: the best case gain from a 1.9 -> 0.3 s leg is +0.0065 SOL/day on FLOOR v2 and +0.013 to +0.042 SOL/day on the REVIVE cells, which stay negative (-0.03 to -0.25 SOL/day at 0.3 s). The $499 package (0.08-0.13 SOL/day) exceeds every line's gain except the two giants, BREADTH b5 (+0.67) and SELLABS s1 (+0.20), whose totals are -28 and -20 SOL/day. What speed could still matter is a different, un-priced mechanism (first-seconds entries, speed-route-1/2), and the feed delay (7 slots held). Open: the feed delay D as a variable; an independent landing benchmark.
