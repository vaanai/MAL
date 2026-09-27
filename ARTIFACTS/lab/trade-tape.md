---
cursor:
  subagentId: "bc-bc62c5da-bf6f-569d-92a7-54a2aab434ca"
---

# Trade tape — public RPC logsSubscribe

**Window:** 2026-09-25 06:58:37Z–07:30:01Z (31.4 min) on `mal-core-vnic`, then a disk follow-up from 07:48:49Z (hourly zstd, still running). Paper only. No vendor signup, no keys. The volume was not grown.

PR: https://github.com/vaanai/MAL/pull/73 (`cursor/trade-tape-34ca`).

## Source choice

| Option | Verdict |
| --- | --- |
| PumpPortal `subscribeTokenTrade` | Ruled out. Not a firehose (`keys[]` only). Full tape ~$3.5k–$10.5k/mo at 1–3M msgs/day ([data-budget](../../docs/research/data-budget-options.md)). |
| Public RPC `logsSubscribe` | **Used.** `$0`. `wss://api.mainnet-beta.solana.com`, commitment `confirmed`, mentions `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` and `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`. |
| Helius free | Not used. 1M credits and 2 credits / 0.1 MB would last hours, not a month, at this bandwidth. |
| Helius Developer $49 | Adapter is in the repo (`--source helius_tx`) and exits without `HELIUS_API_KEY`. Same decoder on `transactionSubscribe`. Not contacted. |
| Anaxer Pro $99 | Pre-decoded all-trades. Not contacted. |

Before deploy, three confirmed slots on the public RPC matched `getBlock` pump-program transaction counts exactly: 50/50, 37/37, 49/49. `publicnode` lagged ~10s and overlapped the official socket on only ~46% of signatures in a skewed 30s window, so it was not used.

Decoders were not copied. `haccer/pumpfun-research` has no LICENSE file. `yegor104/pumpfun-tracker` says MIT in `package.json` but the public `tracker.ts` is a simulated demo. Field order is the public pump IDL plus the TradeEvent prefix already in `tools/exp003_rpc_backfill.py`.

## What each row has

That window wrote `/var/lib/mal/sealed/trades/trades-YYYY-MM-DD.jsonl` (now `trades-2026-09-25.jsonl.zst`). From 07:48:49Z the recorder writes hourly `trades-YYYY-MM-DDTHH.jsonl` and zstd-seals it when the hour closes. PumpSwap rows on disk are v2. Bonding rows stay v1. See [Disk follow-up](#disk-follow-up).

mint, trader, side, `sol_lamports` / `token_raw`, `price_sol`, `market_cap_sol` (assumes 1e9 UI supply when the event has no supply), slot, signature, `event_index`, `t_recv` (`...sssZ`), `t_recv_ms` (unix ms, stamped when the websocket frame arrives, before JSON parse), `event_ts` (on-chain seconds). `feed=public_rpc_logs`.

PumpSwap mint comes from a `CreatePoolEvent` in the same logs or one cached `getMultipleAccounts` on the pool. This run: `create_pool` 441,125, `pool_account` 2,064, bonding `event` 53,097, `unresolved` 0. Pool lookups did not 429.

## 31-minute measurement

| Metric | Result |
| --- | --- |
| Trades | 496,286 (15,806/min) |
| Bonding / PumpSwap | 53,097 (1,691/min) / 443,189 (14,115/min) |
| Unique wallets | 100,028 |
| Unique mints | 2,801 (1,244 bonding) |
| New creates with a bonding trade | **420 / 462 (90.9%)** of PumpPortal `subscribeNewToken` rows with `t_ws` in 06:59–07:28Z |
| Reconnects / ws close / idle | **0 / 0 / 0** |
| systemd restarts | 0 |
| Slot jumps &gt; 2 | **0** (heartbeat `slot_jumps` stayed 0; slots advanced 450278777 → 450285530) |
| Failed notifications | 262,482 / 908,618 (28.9%). Dropped on purpose (`err != null`). |
| Receive vs event second (bonding) | min 0.58s, p50 1.31s, p90 1.72s, p99 1.99s. n=53,097. Zero bad timestamps after the sanity filter. |
| Receive vs `getBlockTime` | 1.31s, 1.67s, 1.70s on three slots (both clocks are 1s). |
| CPU | ~6.7% of one core (129 CPU-seconds / 32 min). Box is 2 OCPU. |
| RAM | process RSS **42 MB**. cgroup **443 MB** (includes page cache of the file it writes). |
| Disk | 411 MB in 31.4 min → **18.8 GB/day** at this rate. `/var/lib/mal` is a 147 GB volume, 139 GB free after the run → about **7 days** until full if the rate holds. PumpSwap is 89% of rows; bonding-only would be ~2.0 GB/day. |

The 42 creates with no bonding trade in this window were not a measured websocket hole. One sampled miss (`7xmy42…pump`, landed, `err=null`) is a `CreateV2` whose only `TradeEvent` has `sol_amount=0` and `virtual_sol_reserves=0`. The decoder in that build dropped it. 7/42 portal rows had `solAmount=0`. The follow-up below keeps those rows with `zero_sol: true` and a null price.

`event_ts` is whole seconds, so the 0.6–2.0s lag band is `fractional second + delay`. The floor (~0.6s) is the lower bound on public-RPC delay. p99 is still at the 2s copy-EV cliff from [smart-wallet research](smart-wallet-research.md), not under it. Every row still has millisecond `t_recv_ms`, so a follow-latency backtest can add 0.5s / 2s / 5s on top of receive time. This feed is for humans and copy-traders, not same-slot snipers.

## Pay or not

**Do not pay on this window.** The public socket stayed up, did not skip more than 2 slots between notifications, and priced 91% of new creates. The cost that showed up is disk, not a vendor bill.

Pay later only if a longer run shows reconnects, slot holes, or create coverage falling. Next cheapest complete tapes, still not purchased: Helius Developer **$49** (program-filtered `transactionSubscribe`, we decode; the flag is already there) or Anaxer Pro **$99** (pre-decoded). PumpPortal stays out.

## Disk follow-up

Restarted only `mal-trade-tape.service` at 2026-09-25 07:48:49Z onto hourly files, zstd, a slim PumpSwap row, and `Restart=always`. `mal-observe.service` was not restarted (active since 2026-09-23 07:57:53Z, `NRestarts=0`). New process: `NRestarts=0`, 0 reconnects, 0 slot jumps, `hold=0`, `dropped=0` through 08:08:49Z.

### What is stored now

| Stream | On disk |
| --- | --- |
| Bonding | v1 row, including `zero_sol: true` when `sol_amount` is 0 or virtual SOL is 0. Price and market cap are null when reserves cannot price the curve. |
| PumpSwap | v2: `venue`, `mint`, `trader`, `side`, `sol_lamports`, `token_raw`, `quote_reserve`, `base_reserve`, `price_sol`, `pool`, `slot`, `signature`, `event_index`, `t_recv_ms`, `event_ts`, `quote_mint`, `quote_is_wsol`. `zero_sol` only when set. The quote mint is resolved once per pool (CreatePool in the same logs, or one cached `getMultipleAccounts`) and stamped on every later row. Rows still missing it are not sealed. |
| Pools | `pool-mints-YYYY-MM-DDTHH.jsonl`, same hourly seal. |

Price path uses `price_sol` plus the two reserves. Wallet PnL uses trader, side, lamports, and token raw. Graph edges use trader, mint, pool, slot, and signature. Market cap for a WSOL curve is still `quote_reserve * 1e9 / base_reserve / 1e9` when supply is the usual 1e9 UI tokens.

CreateV2 zero-SOL trades are no longer dropped. In the new hour, about 6% of bonding rows are `zero_sol`. The scored stats window (creates from 07:48–07:58Z, matched through 08:08Z) was **143 / 150 (95.3%)**. The first stats row has `creates: 0` on purpose: coverage is the previous 10 minutes, so a create at the edge of the window is not marked missing.

### GB/day

Two 10-minute windows after the restart, trades writer only:

| Window (UTC) | Trades/min | Raw |
| --- | --- | --- |
| 07:48:49–07:58:49 | 16,797 | 13.97 GB/day |
| 07:58:49–08:08:49 | 16,279 | 13.42 GB/day |

Average raw **13.7 GB/day** (was 18.8 on the verbose row). zstd -3 on the sealed slim hour `trades-2026-09-25T07.jsonl.zst` is ratio **3.844** (103 MiB → 26.8 MiB). Compressed rate **3.6 GB/day**. Pool-mint files add about 8 MB/day compressed.

The old verbose day file compressed at **4.903** (619 MiB → 126 MiB) and the raw file was removed. zstd alone on that schema would have been about 3.8 GB/day. Slimming the PumpSwap row is a further cut to 3.6 GB/day; most of the repeated keys were already compressible. Hourly seal is what makes the compression safe: the open hour stays plain JSONL.

`/var/lib/mal` is filesystem 157.4 GB, about 149 GB free after the seal (1% used). At 3.6 GB/day the 20% free line is about 33 days out, versus about 7 days at 18.8 GB/day raw.

### Retention and the disk guard

`keep_days = floor((0.80 × volume − bytes that are not tape) / bytes_per_day)` after a sealed hour that the process held for at least 50 minutes, or after two sealed hours whose spans were not recorded (restart). At 3.6 GB/day and the small non-tape footprint (observe JSONL, logs, Postgres), that is about **35 days**. Until then, only the free-space rule runs.

The guard deletes only `trades-*` and `pool-mints-*` (`.jsonl` or `.jsonl.zst`) in `/var/lib/mal/sealed/trades`. It does not delete `stats-*`, observe JSONL, or anything outside that directory, and it does not follow symlinks. The open hour and a file zstd is still reading are kept. If free space is still under 20% after that, the process **holds new writes and stays up** (`Restart=always` would otherwise refill the disk). It resumes at 25% free.

### Oracle block volume

Not provisioned. `lsblk`: `sda` 53,687,091,200 bytes (50 GiB boot) + `sdb` 161,061,273,600 bytes (150 GiB `mal-core-data` on `/var/lib/mal`) = 214,748,364,800 bytes = **200 GiB**. That is the Always Free block-volume cap, and boot counts (DEC-009, DEC-010). The data volume cannot grow inside Always Free without shrinking the boot volume or leaving the free tier.

### Monitor

`Restart=always` on `mal-trade-tape.service`. Every 10 minutes, `stats-YYYY-MM-DD.jsonl` in the tape directory (not compressed, not deleted by retention):

| t (UTC) | trades/min | bonding / PumpSwap | creates with a bonding trade | reconnects | lag p50 / p99 |
| --- | --- | --- | --- | --- | --- |
| 07:58:49 | 16,797 | 20,929 / 147,067 | n/a (first window) | 0 | 1.37s / 7.32s |
| 08:08:49 | 16,279 | 17,677 / 145,121 | 143/150 (95.3%) | 0 | 1.34s / 12.98s |

p50 matches the earlier 1.3s. p99 in these two windows is worse than the first 31 minutes (1.99s): hundreds of bonding rows arrived 5–13s after their event second while the socket stayed up. That tail is the public RPC, not a dropped decode. Copy-EV backtests should keep using `t_recv_ms`.

Restarted the tape again at 08:37:11Z so PumpSwap rows carry the quote. In the first fresh slice after subscribe (9,117 PumpSwap rows): **0 missing `quote_mint` or `quote_is_wsol`**. 5,375 are WSOL-quoted and all have `price_sol`. 3,742 are some other quote, with a null SOL price. Pool cache seeded 5,672 pools from `pool-mints-*`. Observe was not restarted.

## Helius shadow (2026-09-25 20:14:59Z, stopped)

Developer plan is live. Shadow only: Helius `logsSubscribe` on the same two programs, directory `/var/lib/mal/sealed/trades-helius-shadow`. `mal-trade-tape` was not restarted (pid 48407). `transactionSubscribe` was not used: both streams are 2 credits per 0.1 MB, and full transactions are larger than logs.

Stopped after ~4 minutes. Socket receive was 580 MB, about **172k credits/hour** (2 credits / 0.1 MB). The feed budget is ~4k credits/hour. A 30-minute run would have been ~85k credits without changing the result. The process is stopped.

Same-window overlap (242s, clipped to the shadow's last row): Helius had 81,180 trades vs public 82,007. Helius matched **99.0%** of public trades and added 1. Creates with a bonding trade: Helius 79/90 (87.8%), public 80/90 (88.9%). Bonding lag: Helius p50 **1.21s** p99 **1.81s**; public p50 **1.51s** p99 **3.85s**. Matched bonding rows arrived on Helius ~0.20s earlier (p50).

**Stay on the public tape.** Do not switch and do not run both. At this rate the 3M credit feed budget lasts about 17 hours.
