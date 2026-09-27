---
cursor:
  subagentId: "bc-0a2c37cf-eaab-5e7d-9c18-b96559be6cea"
---

# Fast create listener, 2026-09-27

Paper only. Oracle observe, tape, backfill, and training were not moved and were not restarted. The box was not rebooted. Cloudflare Tunnel and Access were not changed. Port 22 was not opened. No trading keys or wallets were written. No secret values are in this note.

## Listener

Up. User unit `mal-fast-create.service` on `mal-fast-0`, enabled, `Restart=always`, `NRestarts=0`, original PID still running at the end of the window.

| | |
| --- | --- |
| Socket | PumpPortal `wss://pumpportal.fun/api/data`, `subscribeNewToken` only |
| Unit | `MemoryMax=1G` (1073741824), `Nice=0`, memory accounting on |
| RSS at 09:03 UTC | 17.0 MiB current, 17.3 MiB peak |
| Log | `helius_skipped reason=no_narrow_create_filter credits_spent=0 trip=20000` |
| Venv | Rebuilt on the box. `/usr/bin/python3.12` is ELF x86-64. websockets 13.1. Oracle site-packages were not copied. |
| Output | `/var/lib/mal/sealed/fast-create/fast-create-2026-09-27.jsonl` |

Slot and vendor time were absent on every row. PumpPortal create frames in this window did not carry `slot`, `timestamp`, or `blockTime`. Each row still has `mint` and `t_recv_ms` (unix ms at websocket recv).

Left running. It is the free PumpPortal socket and memory stayed near 17 MiB.

## Host smoke

`ssh-fast.tradervaan.com` → `mal-fast-0`, user `ubuntu`, Ubuntu 24.04, kernel `6.8.0-136-generic`, x86_64. Deploy-key fingerprint matched `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`. NTP synchronized, timezone UTC.

| | |
| --- | --- |
| Cores | 8 |
| Memory | 24026872 kB total (~22.9 GiB), ~22.3 GiB available at smoke |
| Disk `/` | 193 GiB, 2% used (~2.1 GiB used) |

Trading-key scan of `/home/ubuntu`, `/var/lib/mal`, `/opt`, and `/root`: no wallet, keypair, or helius filenames, and no JSON file with a 64-int keypair shape. `/home/ubuntu/.ssh` contains only `authorized_keys`. Contents were not printed. No Helius env file was written.

`:22` was already bound on `0.0.0.0` and `::` before install. `ssh.service` was not enabled by this pass. It was left as found.

`apt-get install` of `python3-venv` ran `needrestart`. It reported the running kernel up to date and deferred service restarts. Nothing was rebooted.

## Helius

No credits spent. The socket was not opened.

A create alarm cannot name the new mint ahead of time. `preprocessedSubscribe` filtered to the pump program is one account and still program-wide (the logs firehose was ~172k credits/hour). There is no account list well under the 5,000 cap that still sees unknown creates, so this pass skipped Helius. `CreditCounter` trips at 20,000 and `helius_filter_decision` refuses program ids and lists longer than 64. Neither is wired to a socket. Beta `wss://beta.helius-rpc.com` was not contacted. No staked send, no Jito tips.

## Compare with Oracle `mal-observe`

Oracle `mal-observe.service` stayed `active`, `NRestarts=0`, start `Sat 2026-09-26 00:39:01 UTC`. Read-only scan of `/var/lib/mal/sealed/jsonl/observe-2026-09-27.jsonl`. Oracle receive time is `t_ws`. Fast receive time is `t_recv_ms`.

Window: `2026-09-27T08:25:43.793Z` through `2026-09-27T08:55:43.675Z` (30.0 minutes). 507 fast rows, 499 unique mints. Repeat mints use the first fast `t_recv_ms`. Each mint is paired to the closest Oracle `t_ws`. All 499 pairs land inside 96 ms, so none were dropped as a different event.

Delta = Oracle `t_ws` minus fast `t_recv_ms`. Negative means Oracle received the create first.

| | ms |
| --- | --- |
| n | 499 |
| p50 / median | **−70** |
| p90 | **−63** |
| min | −96 |
| max | −50 |
| mean | −71.2 |

Oracle was earlier on all 499 mints. Fast PumpPortal p50 advantage versus Oracle is **−70 ms** (about 70 ms behind). p50 and the median are the same linear percentile on this odd count.

NTP at measurement time: fast host offset **+12.7 ms** versus `ntp.ubuntu.com` (6 packets); Oracle offset **+20 µs** versus the hypervisor NTP. Shifting the fast stamps by that offset moves the median to about **−57 ms**. The gap is path, not a 70 ms clock error.

## Repo

PR: https://github.com/vaanai/MAL/pull/105 merged `2026-09-27T09:07:11Z` (`d71e6c4`). Tests: `python3 -m unittest tools.test_fast_create_listener` — 11 OK.

## Chain probe (mint authority)

`TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM` is the pump.fun mint authority. On a live CreateV2 transaction, `initializeMint2` and `mintTo` use it as mint authority, and `setAuthority` (`mintTokens`) revokes it in the same transaction. Of the last 40 signatures for that account, 39 are CreateV2 and one is a failed SOL transfer with no pump instruction. No legacy `Instruction: Create` showed up in that sample. Ordinary later trades on three new mints do not include it: Buy 0/6, Sell 0/14. Create transactions often bundle the first buy in the same signature. That is the create transaction, not a later trade. The filter is one account, not the pump program.

30-minute probe from `mal-fast-0`, `2026-09-27T09:13:03Z` to `09:43:04Z` (1800.8 s), beside the existing PumpPortal unit. Oracle `mal-observe` stayed active (`NRestarts=0`, same start as before). Delta is Oracle `t_ws` minus fast `t_recv_ms`. Positive means the fast host was first. Pairs are the same chain signature. All matched pairs are inside 5 s. p50 and the median are the same linear percentile.

| Stream | n | p50 / median | p90 | slot-to-recv p50 | slot-to-recv p90 | Credits |
| --- | --- | --- | --- | --- | --- | --- |
| PumpPortal `subscribeNewToken` | 472 | **−59** | −51 | — | — | 0 |
| (a) public `logsSubscribe` processed | 464 | **−42** | +66 | 981 | 1412 | 0 |
| (b) Helius `logsSubscribe` on `beta.helius-rpc.com` | 464 | **+106** | +118 | 850 | 1264 | **111.7** |
| (c) Helius `preprocessedSubscribe` | 464 | **+109.5** | +124 | 844 | 1260 | **63.2** |

Chain rows were 628 CreateV2 signatures (public and Helius logs, same set). Preprocessed delivered those 628 plus 4 extras. 464 of the 628 matched an Oracle create signature. The other 164 were not on the PumpPortal observe tape. PumpPortal on this host in the same window: 472 mints, all matched Oracle, p50 −59 ms.

Slot-to-recv is `t_recv_ms` minus `getBlockTime` (unix seconds) for all 592 slots. The block time is one-second resolution, so the level is coarse. The gap between streams matches the Oracle deltas: preprocessed leads public logs by about 146 ms on the same signatures, and leads Helius logs by about 3 ms.

Credit meter, published rates, neither trip fired (cap 20,000; also a 100-message / 10 s rate trip, which did not fire):

- (b) LaserStream standard method: 2 credits per 0.1 MB. 633 messages, 5,584,493 bytes, **111.6899** credits.
- (c) 0.1 credit per delivered transaction. 632 messages, **63.2** credits.
- Helius total this window: **174.9** credits. Both Helius sockets closed at 09:43:04. No Helius process remains.

Fastest stream is (c). Linear projection of this half hour: 63.2 × 86400 / 1800.823 ≈ **3,032 credits/day**.

NTP during the probe: fast host **+19.7 ms** versus `ntp.ubuntu.com`; Oracle **−67 µs**. That does not change which stream won.

On the same 464 mints, public logs arrived **19 ms** ahead of PumpPortal at the median (270 of 464). It beats PumpPortal and loses to Oracle (p50 −42). Left running as user unit `mal-fast-public-logs.service` (`--public-only`, `MemoryMax=1G`, `Nice=0`, `Restart=always`, no EnvironmentFile). Output `/var/lib/mal/sealed/fast-public/`. PumpPortal `mal-fast-create.service` still running. Helius stays off. The mode-600 env file remains at `/var/lib/mal/fast-listener/helius.env` and is not loaded.

## Mint-authority preprocessed, kept

`mal-fast-pre-create.service` is enabled and running. It is `preprocessedSubscribe` on `TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM` only. `MemoryMax=1G`, `Nice=0`, `Restart=on-failure`. The key is still only in `/var/lib/mal/fast-listener/helius.env` (mode 600), loaded by `EnvironmentFile`. Output `/var/lib/mal/sealed/fast-pre-create/`. Each row has mint, bonding curve, slot, signature, and `t_recv_ms`.

The credit counter is a UTC-day cap of **10,000**. State file `/var/lib/mal/fast-listener/pre-create-credits.json` (mode 600). At the end of this pass: **83.9** credits, not tripped. A trip closes the socket, logs, and does not open it again until the next UTC date, including after a process restart. PumpPortal and public logs were not stopped.

PR: https://github.com/vaanai/MAL/pull/108 merged `2026-09-27T10:19:06Z` (`b7f5bae`). Tests: `python3 -m unittest tools.test_fast_helius_pre tools.test_fast_create_listener` — 30 OK. Diff is listener, unit, install, and tests.

## Migration account

`39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg` (pump global withdraw authority) is one account. 1,000 recent signatures spanned 11.7 hours, **1.42/min**. Successes are Migrate / MigrateV2. A sampled mint's later Buy and Sell did not include it. About 32% of the history is failed transactions, including one failed BuyV2, so it is noisier than the mint authority, but it is not the trade firehose. Added on the early-trade socket only. The probe wrote **34** graduation rows.

## Early-trade probe, stopped

For each create, the bonding curve was subscribed with `preprocessedSubscribe` for 120 seconds, then dropped. Cap 5,000 accounts; the probe peaked at **57**. Buy and Sell rows are sealed with `observe.trade_decode` on one public `logsSubscribe` of the pump program ($0). `t_recv_ms` on those rows is the preprocessed receive time. Instruction limits are not stored as fill amounts. v1 transactions (version byte `0x81`) and `buy_exact_quote_in_v2` are decoded. Preprocessed filters still refuse program ids.

30.0 minutes, `2026-09-27T10:17:15Z` through about `10:47:15Z` (1,800.01 s). Hard trip 20,000 credits. It did not fire.

| | |
| --- | --- |
| Credits | **13,921.0** (139,210 messages × 0.1) |
| Mints with at least one attributed message | 491 |
| Credits per mint, median | **2.9** |
| Credits per mint, mean | 28.1 |
| Projected credits/day | 13921.0 × 86400 / 1800.01 = **668,204** |
| Creates in the window | 577 rows, 554 mints |
| Fast signatures | 138,250 |
| Sealed decoder rows | 28,797 |
| Graduations | 34 |
| Accounts at once, max | 57 |

Oracle `mal-trade-tape` stayed active (`NRestarts=0` before the read, still active after). Read-only scan of `trades-2026-09-27T10.jsonl`. `mal-observe` stayed active. No tape process was restarted.

Comparison is first-120-second tape prints for mints created in the window, excluding the create transaction itself (that signature is on the mint-authority socket before the curve subscription exists). Delta is Oracle tape `t_recv_ms` minus fast `t_recv_ms`. Pairs with `|delta| > 5s` stay in the coverage count and are left out of the median.

| | |
| --- | --- |
| Oracle signatures | 39,668 |
| Also seen on the fast trade socket | 28,376 |
| Coverage | **71.5%** |
| Paired for the median | 27,346 |
| Median advantage | **+436 ms** |
| p10 / p90 | +308 / +627 ms |
| Fast first | 27,346 / 27,346 paired |

**Not left running.** The projection is above 100,000 credits/day and coverage is below 95%. `mal-fast-early-trade.service` is installed and disabled. The probe unit is gone. PumpPortal, public logs, and the mint-authority preprocessed listener are still up.

## Why the early-trade coverage was 71.5%

The paid curve socket was not reopened. The miss set is the same 11,292 later-trade signatures (39,668 on the Oracle tape, 28,376 also on the fast socket). Classes are mutually exclusive and sum to 11,292. A public `getTransaction` sample of the non-PumpSwap misses (22 of 23 returned) had `err=null`, and the bonding curve recorded at create was in the static account keys.

| Class | Signatures | Share of misses |
| --- | ---: | ---: |
| PumpSwap pool. The bonding-curve account is not in the transaction | 9,903 | 87.7% |
| Bonding trades after that curve's last delivery, still inside the 120s window | 743 | 6.6% |
| Slots before the first delivered slot on that curve | 298 | 2.6% |
| Curve address known, socket delivered nothing for that mint (7 mints) | 187 | 1.7% |
| Same slot as the first delivery; subscribe landed mid-slot | 149 | 1.3% |
| Missed while that curve was still delivering | 12 | 0.1% |
| Failed transactions | 0 | 0 |
| CreateV2 / Token-2022 curve mismatch | 0 | 0 |

Every create in the window was `create_v2`. Bonding-curve coverage on those mints was 28,366 / 29,755 (95.3%). The curve layout is not the gap. The 71.5% figure is the PumpSwap trades in the first 120 seconds, which a bonding-curve filter cannot see. Of the 9,913 PumpSwap signatures in the window, 10 also touched the subscribed curve.

## Public program-wide tape, stopped

`mal-fast-trade-tape` ran on `mal-fast-0` from `2026-09-27T10:58:26.586Z` for 1,800 s. Public `logsSubscribe`, confirmed, pump.fun and PumpSwap, the same decoder and sealed schema as Oracle. `MemoryMax=1G` (about 308 MB in use), `Nice=0`, reconnects 0, restarts 0, disk hold did not trip, `keep_days=7`. Oracle `mal-trade-tape` and `mal-observe` stayed active and were only read. Clock offset during the run: fast host +1.1 ms, Oracle +2 µs.

Window signatures, earliest `t_recv_ms` per signature. Delta is Oracle `t_recv_ms` minus fast `t_recv_ms`. Positive means the fast host was first. One pair with `|delta| > 5s` does not move the median or p90.

| | |
| --- | --- |
| Oracle signatures | 391,141 |
| Also on the fast tape | 391,049 |
| Coverage | **99.98%** |
| Median advantage | **+128 ms** |
| p90 | +231 ms |
| Fast first | 378,844 / 391,049 |
| Slot-to-recv, 80 slots | fast p50 1,120 ms / p90 1,440 ms; Oracle p50 1,256 ms / p90 1,635 ms |

Block time is one-second resolution. The slot-to-recv gap matches the signature median.

**Not left running.** Coverage clears 95%. The median lead is under 150 ms. `mal-fast-trade-tape.service` is installed and disabled. PumpPortal, public create logs, and the mint-authority preprocessed listener stayed up. The paid early-trade unit stayed disabled.

PR: https://github.com/vaanai/MAL/pull/109 merged `2026-09-27T11:31:12Z` (`e2c63b1`). Tests: `python3 -m unittest tools.test_fast_public_tape observe.test_trade_store observe.test_trade_decode tools.test_fast_helius_pre tools.test_fast_create_listener` — 57 tests, 2 skipped, OK.
