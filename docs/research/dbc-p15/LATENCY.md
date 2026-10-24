# P15 DBC latency: can MAL see and land a Meteora DBC swap inside the 2.5 s line? (2026-10-24, DRAFT, measurement only)

QP ruling (m10, flat mean per attempt by entry leg; in-sample (Swap2-era exploration 2026-10-14T00Z..10-16T04Z), screen numbers from the best cell, winner's curse applies, not evidence): 0.4 s +0.0302, 1.9 s +0.0218, 2.5 s +0.0141, 3.4 s +0.0020 (fail), 5.0 s -0.0033. QP stop line: over 2.5 s.
Windows and the DBC D1 hold-out: every latency window below is on 2026-10-24 UTC. The research-0 recorder #1453 tape starts at hour 2026-10-24T01 (the 02:00-04:00Z window is 2026-10-24T02:00Z..04:00Z, hours T02 and T03; detect-r0-0224-02-03h.json is that window, file name 0224 = 10-24). The 04:16-04:26Z window is 2026-10-24T04:16Z..04:26Z (detect-r0-window-0416-0426.json; the sampler job #1489 ran in the same minutes, first commit 2026-10-24 04:16Z). All are at or after 2026-10-24T00Z, so no DBC D1 hour in [2026-10-17, 2026-10-24) was read. The one CU sample hour, 2026-10-15T10, is exploration and outside D1.
No trading, no keys, public RPC only, no paid credits. Tools: tools/dbc_latency_detect.py, dbc_latency_sampler.py, dbc_latency_cu.py. Job #1489 (mal-fast-0, 1 GB, background).

## 1. Detection lag (swap on chain -> MAL sees the notice)
Method: lag = t_recv - fitted block time of the notice's slot. getBlockTime has 1 s resolution and the chain timestamp is itself noisy, so it is sampled at
anchor slots (every 60-200 slots) and fitted piecewise-linear. Fit residual sd 0.47 s (10-min window) and 0.93 s (2 h): an error bar of that size sits on every
tail number below, and a block time is not the exact instant the leader produced the block. Fitted slot time 0.211-0.212 s (the 0.4 s in older notes is stale).
Swaps only (ix name starts with Swap, not failed).

| Source (same public wss, api.mainnet-beta) | Window | n swaps | commitment | p50 | p90 |
| --- | --- | ---: | --- | ---: | ---: |
| research-0, recorder #1453 (2 sockets, deduped) | 04:16-04:26Z, same 985 slots | 1,957 | confirmed | 0.89 s | 2.54 s |
| research-0, recorder #1453 | 02:00-04:00Z | 26,662 | confirmed | 1.64 s | 4.63 s (p99 6.7) |
| mal-fast-0, job #1489 | 04:16-04:26Z | 1,958 | processed | 0.49 s | 0.76 s |
| mal-fast-0, job #1489 | 04:16-04:26Z | ~1,950 | confirmed | ~0.67 s (implied: processed p50 + 0.19) | 0.98 s (p75 0.86) |

- Same-socket processed vs confirmed on fast-0 (1,954 signature pairs): confirmed arrives 70 / 187 / 340 ms (p10 / p50 / p90) after processed.
- Host gap: in the same 10 minutes the research-0 recorder was 0.2 s slower at p50 and 1.5 s slower at p90 than fast-0 at the same commitment. The 2 h research-0 figures are worse still (busier hours and a noisier fit); treat 0.9-1.6 s p50 and 2.5-4.6 s p90 as the research-0 range.
- The recorder subscribes at commitment confirmed (the observe.trade_source default). A processed feed on the same socket saves a median 0.19 s.
- Not measured: Helius/gRPC feeds (no paid credits), and whether the arena feed on research-0 has the same tail as the recorder (it uses the same public-wss family; not verified).

## 2. Landing lag (send -> landed), from repo records
- Probe, pump stack, mal-fast-0 (ARTIFACTS/lab/probe-final-2026-10-07.md, 61 round trips): decision -> send p50 221 ms, p90 453 ms (faa3192); landed 2 slots after the executor's pool-state read at p50, 3 at p90, 5 max. At 0.212 s a slot: about 0.42 s p50, 0.64 s p90 (the probe note's "about 800 ms" assumed 0.4 s slots). The probe's send -> confirm 1.18 s is when the executor SAW the landing at confirmed; it is an upper bound, not the landing time.
- Fee arm 2026-10-23 (ARTIFACTS/lab/feearm-landing-2026-10-23.md, 150 buys): landed buys slots_between mean 2.20 at 155,000 priority (1:6, 2:50, 3:17, 4:2) vs 2.44 at 500,000. Priority fee does not buy earlier landing here; 4/300 sends failed (slippage).
- DBC: same Solana slots and same send path, so no structural difference. CU: 60 sampled DBC Swap/Swap2 transactions (exploration hour 2026-10-15T10, public getTransaction, other bots' txs): consumed p10 / p50 / p90 / max = 48.9k / 53.7k / 65.5k / 72.9k, 17 accounts median. Well under a default 200k limit, so CU does not slow inclusion. The DBC landing leg is carried over from pump measurements; no DBC send has ever been made (no executor), so it is an assumption, not a measurement.

## 3. Decision-to-landing estimate against the marks
Leg = chain time of the signal print -> landed, i.e. detection + decision (0.22 / 0.45 s) + landing (0.42 / 0.64 s). p90 is the SUM of component p90s: it overstates the true p90 of the total (components are not perfectly correlated) but the fit noise above cuts the other way. A range, not a result.

| Setup | p50 total | p90 total (sum of p90s) | vs 1.9 s | vs 2.5 s stop |
| --- | ---: | ---: | --- | --- |
| fast-0, processed feed | 0.49+0.22+0.42 = 1.1 s | 0.76+0.45+0.64 = 1.9 s | p50 well under; p90 at | p90 under |
| fast-0, confirmed feed | 0.67+0.22+0.42 = 1.3 s | 0.98+0.45+0.64 = 2.1 s | p50 under; p90 over | p90 under |
| research-0 public wss, confirmed (10-min window) | 0.89+0.22+0.42 = 1.5 s | 2.54+0.45+0.64 = 3.6 s | p50 under | p90 OVER |
| research-0 public wss, confirmed (2 h) | 1.64+0.22+0.42 = 2.3 s | 4.63+0.45+0.64 = 5.7 s | p50 over 1.9 | p90 over, p50 near the line |

Reading: a live book would send from mal-fast-0 (Frankfurt). On that host the typical DBC entry leg is about 1.1-1.3 s, which beats the sim's 1.9 s, and the sum of p90s stays under 2.5 s. The research-0 feed alone would not clear the line (its tail is 2.5-4.6 s), so research-0 is a paper/arena host, not the DBC trigger host. Caveats: one 10-min fast-0 window (a quiet period; 985 swap slots), no DBC send ever made, block-time fit noise 0.5-0.9 s, and the sim's 1.9 s is counted from 1 s-resolution tape block time, so the mean of the real leg (about 1.2-1.5 s from fast-0) landing under 1.9 s does not by itself prove the curve's value applies: the +0.0218 to +0.0141 band is the right place to read, and a tail beyond 3 s would go negative. This note is a latency measurement, not an edge claim.

## 4. What would cut it (costs from docs/research only)
- Processed commitment on the listener: free, measured -0.19 s median here.
- Frankfurt staked send: already on the Developer plan (mainnet.helius-rpc.com, sendTransaction 1 credit; docs/research/vps-options.md). Helius Sender documented at 0 credits, "confirm it is enabled on this key". Jito bundle tip minimum 1,000 lamports, bundles only, not needed for a single swap. The fee arm shows priority 155k vs 500k does not change landing slots.
- LaserStream WebSocket: vendor claim about 200 ms faster than Agave websockets (2 credits / 0.1 MB, firehose-credit problem). LaserStream gRPC $499 (Business) / $999; Triton Yellowstone $0.08 per GB with $125 deposit; preprocessedSubscribe 0.1 credit per message, account filter required. A DBC-only filtered stream is the only shape that fits; unmeasured here.
- Biggest lever is the host: moving the trigger from research-0 to mal-fast-0 removes about 0.2 s at p50 and 1.5 s at p90 in the same-window comparison.
