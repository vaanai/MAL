---
cursor:
  subagentId: "bc-cfe28e2e-5230-5c74-a2b0-29a37aab8334"
---

# Pump.fun history backfill

**As of:** 2026-09-25 12:24Z. Paper only. No vendor account created, no spend.

Loader: [PR #80](https://github.com/vaanai/MAL/pull/80) (`cursor/pump-history-backfill-8334` → `cursor/trade-tape-34ca`). Live recorder and `mal-trade-tape.service` were not edited.

## Route

Public mainnet `getBlock` (`https://api.mainnet-beta.solana.com`, `maxSupportedTransactionVersion: 1`, commitment `confirmed`) is the complete $0 path that needs no account. `getFirstAvailableBlock` is 0. Blocks from this morning and from 28 days ago still return `Program data:` logs, so the same decoder as the live tape rebuilds trades, creates, completes, and PumpSwap migrations, including curve reserves.

| Candidate | Verdict |
| --- | --- |
| Public archival RPC `getBlock` | **Used.** $0, no key. Logs present. Rate limit is the cost: ~1.5 blocks/s sustained here. |
| Google BigQuery public Solana | Not queried. Dataset `bigquery-public-data.crypto_solana_mainnet_us` exists. Sandbox is $0 with no card, 1 TB/month, then about **$6.25/TB**. Needs an owner Google account. `log_messages` and a one-hour scan size were not proven. |
| Dune | Not complete. Free is view-only after a 14-day trial (2,500 credits). Export is 20 credits/MB. `dex_solana.trades` has trader, amounts, slot, time, and signature, and does not have curve reserves. |
| Flipside | Shut. Sold to SonarX; Flipspace closed 2026-06-17. |
| Helius free historical | Not used. `HELIUS_API_KEY` is not in the agent environment. Free is 1M credits / 10 rps. `getTransactionsForAddress` is Developer+ only. |
| Old Faithful CAR / publicnode | CAR files are epoch-sized and blow the 40 GB cap. `publicnode` returned HTTP 403. |

Do not run this loader on the host. The live tape already holds `logsSubscribe` on the same public RPC IP.

## Proof hour

Window **2026-09-25 07:00:00Z inclusive to 08:00:00Z exclusive** (`block_time` 1790319600–1790323200). Slots 450278864–450292391 (13,527). Empty slots 0, RPC errors 0, unresolved PumpSwap pools dropped 0. Wall time **9,053 s** (~1.49 blocks/s). Wire ~13.9 GB. Compressed output **138 MB**.

| File | Rows | sha256 |
| --- | --- | --- |
| `/var/lib/mal/backfill/trades/trades-2026-09-25T07.jsonl.zst` | 957,399 (bonding 115,338, PumpSwap 842,061) | `d4bb1ea024cc0bbcc302db9e895c4bd9a63d4fe50412912bb0b7a162c8b7e617` |
| `/var/lib/mal/backfill/creates/creates-2026-09-25T07.jsonl.zst` | 1,056 | `b252a609e945f845421ae07cf8bef20efe69e57fddc57fef4a6ce0e2be47f336` |
| `/var/lib/mal/backfill/migrations/migrations-2026-09-25T07.jsonl.zst` | 80 (40 complete + 40 migration) | `a00de08d9ed32a957390e4159ce600046ca4b74e626660c4f5e68daad84e26a0` |
| `/var/lib/mal/backfill/stats-2026-09-25T07.json` | — | `4ad582c656719a746281554b0af95a9ea470339b1d5b1096c032d8c344b96752` |

Creates: 949 WSOL, 55 USDC (`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`), 52 other quote mints. Native SOL on create, complete, and migration events is the zero pubkey; the loader maps that to WSOL. Every migration-file row is WSOL. Non-SOL quotes are kept.

### Against the live tape

Live index: unique `(signature, event_index)` from `trades-2026-09-25.jsonl.zst` plus `trades-2026-09-25T07.jsonl.zst`, slots in the hour. **942,018** live keys, **957,399** backfill keys, **942,016** in both.

Fair compare ignores `quote_mint` / `quote_is_wsol` when the live row is null (quote stamping landed only at the 08:37:11Z restart, so this hour’s live PumpSwap rows often lack them). Identity and amounts (`venue`, mint, trader, side, lamports, token raw, reserves, slot, `event_ts`, pool):

| | |
| --- | --- |
| Match | **941,859 / 942,016 (99.983%)** |
| Mismatch | **157** |
| Only live | **2** |
| Only backfill | **15,383** (4,775 `zero_sol`) |

The recorder that keeps `zero_sol` started at block time **1790322529** (~07:48:49Z).

| Slice | Live rows | Only in backfill |
| --- | --- | --- |
| Before 1790322529 | 755,569 (bonding 85,858, PumpSwap 669,711, `zero_sol` 0) | 15,184 (4,773 `zero_sol`) |
| From 1790322529 | 186,449 (bonding 23,198, PumpSwap 163,251, `zero_sol` 1,332) | 199 (2 `zero_sol`) |

96,074 live PumpSwap rows had a null `quote_mint`. Backfill filled the quote from the pool account. The 157 mismatches are event-index shifts, not amount drift: the early live build dropped a leading zero-SOL bonding event, so the next event in that transaction was stored at index 0. Checked signature `4Uiscuz27L7RSQHSFtmw2Skju22qXN6aBnBRWDcPxRy4kXS4gxWVFE5QNtmbmWCqGKRc6aRpbjVYkMNTnhzHirCV`: backfill index 0 is a zero-SOL bonding sell (`GVpLNQvFWfvmaFjkb7naqZxfU6ds98ai5Gh9MYJYpump`, 0 lamports); index 1 is the PumpSwap sell the live tape stored at index 0 (mint `EPjFWdd5…`, 816,257,116 lamports, pool `Gf7sXMoP8iRw4iiXmJ1nq4vxcRycbGXy5RL8a8LnTd3v`).

Smoke match, slot 450289874: bonding sell mint `zudtxPu5TWPiTiuPu6ywngxvu5jLfNwuvCv3fque1qz`, trader `FFWz3afFp6jNLACoyfAxCQVP7LVPQSwZupTKnTFRXSeh`, 700,675,995 lamports, signature `5z2suyq6BxWBadKJCiYCQuxsSuiYYmWE4ximbBSFS9Wv4EnCg2NVqaPUeC8JRXJVznEiydBfb979qKresR3bCUPg`, `block_time` = `event_ts` = 1790322529.

Create sample: mint `8Ty2mh5gi8nP9Cdvia4Ven9kjnMK2uc2rynMWtahpump`, trader = creator `3uT4VQCvaabRcPy4Mv8Q3FbHkJFmQdYgTA5Ho2sikJ5Y`, slot 450278902, signature `53R2F2fr2CkPhExL5bhHWVHBpjVkSgoGf7N3occW9LcFAfkCMPDUgwwNPwZiFnbUcY2B7cwLcRwJh4bo7GZcozNb`, quote WSOL, `source=backfill`, `t_recv` null.

## Schema and disk

Trades go through `stored_trade` (bonding v1, PumpSwap v2) then `source=backfill`, `feed=public_rpc_getblock`, `block_time` set, `t_recv` and `t_recv_ms` null. Creates and migrations are separate files, not mixed into trades. Failed transactions are skipped. Hourly zstd JSONL. The retention guard only deletes `trades-*` and `pool-mints-*` inside `/var/lib/mal/sealed/trades`, so `/var/lib/mal/backfill` is outside it.

`/dev/sdb` is 157,396,975,616 bytes. After the upload: used 1.98 GB, available 147,342,155,776 (**93.6% free**). Backfill directory **138 MB**. The 20% free reserve (~31.5 GB) is not the binding limit. The 40 GB cap is. Measured compressed trades are **137 MB/hour → 3.2 GiB/day**, so the cap holds about **12 days** of the full schema (bonding + PumpSwap + creates + migrations). PumpSwap is 88% of rows. Four weeks of full PumpSwap does not fit 40 GB. Four weeks of bonding plus creates and migrations would, and was not split out.

## Why only one hour is loaded

One chain-hour took **2.5 hours** of wall clock. A chain-day is on the order of **60 hours** from this agent, and two weeks is many weeks of fetching. Public RPC has no dollar quota; 429s and the 100 MB / 30s byte budget are what slow it. Disk would still accept ~12 recent full days. Continuing past the proof hour would not finish a day inside this run, and a second `getBlock` client must not share the host IP.

Next free slice, from the agent, most recent first:

`python3 -m tools.pump_history_backfill --until 2026-09-25T07:00:00Z --hours N --out /tmp/mal-backfill`

Then copy sealed hours to `/var/lib/mal/backfill`. Idempotent when `stats-YYYY-MM-DDTHH.json` already exists.

## If weeks are needed sooner

Stopped before any spend. No API key was created.

1. **Keep the public RPC loader.** $0. ~12 full days fit the disk cap. ~60 h wall per day at the rate measured here.
2. **BigQuery sandbox, if the owner creates a Google account and a GCP project (no billing required for the sandbox).** $0 until 1 TB scanned in the month, then **$6.25 per TB**. Do a dry-run of one hour and confirm `log_messages` (inner instructions have been incomplete in past public-dataset reports) before any query that bills. Not proven in this run.
3. **Helius, if the owner creates `HELIUS_API_KEY`.** Free: 1M credits, 10 rps. Developer: **$49 / month, 10M credits, 50 rps**. The credits page prices standard `getBlock` at **1 credit**; a secondary llms.txt summary said 10. Treat 1 as the page price and confirm with one metered call before a bulk job. At 1 credit, free covers ~3.1 days of blocks and Developer covers ~31 days. `getTransactionsForAddress` is not on the free plan (10 credits / 100 txs on paid plans) and is the wrong shape for a full-chain hour.

Live tape at 12:24Z: user unit `mal-trade-tape.service` **active**, PID 48407, started 08:37:11Z, `NRestarts=0`, still appending `trades-2026-09-25T12.jsonl`. An earlier system-bus `systemctl` check reported the unit missing; the unit lives on the user bus. It was not stopped.

## Helius prep (key not here yet)

`HELIUS_API_KEY` was absent, so no Helius call was made and nothing was written on the host. The loader is ready for the minute the secret lands.

- RPC URL is built from `HELIUS_API_KEY` (`https://mainnet.helius-rpc.com/?api-key=…`). An explicit `--rpc` still wins, which is how the public test ran. The URL is redacted out of errors and is not printed.
- Bulk Helius exits 2 until `--credits-per-getblock` or `confirmed_credits_per_getblock` in `credit-probe.json`. That blocks a paid run before the cost is checked.
- Credit hard cap default **7,000,000**. Each RPC attempt reserves one unit of the confirmed price before it is sent. Public RPC uses price 0, so the cap does not stop it.
- Hours walk newest first. A checkpoint (`checkpoint.json`) records the next slot and JSONL byte offsets every 25 slots. A sealed `stats-*.json` is skipped. Disk stop is the 40 GiB directory cap and the 20% free reserve.
- Host unit `mal-pump-backfill.service` is separate from the live tape. `install-helius-backfill-env.sh` writes `/var/lib/mal/backfill/helius.env` mode 600 and does not start the unit. Not run, because the key is absent.

Public RPC check at 0.5 req/s, slots 450289874–450289875: first call checkpointed after 83 trades; resume added the second slot (153 trades, 0 duplicate keys). Every row has `source=backfill`, null receive time, and `block_time`. A third start skipped the sealed hour. `--probe-credits 1` against public RPC returned no credit header (`observed_credits_per_getblock: null`).

Published Helius price for historical `getBlock` is **1 credit** ([credits](https://www.helius.dev/docs/billing/credits)). Using the proof hour (13,527 blocks, 137 MB compressed trades):

| Assumed cost | Days in 7M credits | Days in 40 GiB | What binds |
| --- | --- | --- | --- |
| 1 credit (published, not measured on our key) | 21.6 | 12.5 | disk |
| 10 credits (the old conflict) | 2.2 | 12.5 | credits |

Do not start the paid run until the key is present, `pump-backfill.sh probe` has been run, and the dashboard delta for those calls is written to `confirmed_credits_per_getblock`. At the published 1 credit, about **12 days** fit the disk cap and stay under 7M.

## Free-plan attempt (2026-09-25 16:57Z)

Owner is on the Helius free plan (1M credits, 10 req/s), not Developer. Requested cap is **600k** credits at **≤ 6 rps**, with the funding enricher held at 2 rps so the combined rate stays ≤ 8.

Before any `helius.env` existed, a user drop-in was installed and **only** `mal-funding-graph.service` was restarted:

`/home/ubuntu/.config/systemd/user/mal-funding-graph.service.d/rps.conf` sets `MAL_FUNDING_RPS=2`. The process came back `active` and logged `funding_graph rpc=public rps=2.0`. `mal-trade-tape`, `mal-observe`, `mal-forward-paper`, and `mal-attention` stayed active. `choose_rps` keeps an explicit `--rps`, so when the env file appears later the enricher stays at 2/s instead of the built-in 5/s.

`HELIUS_API_KEY` is not in this run. Injected runtime secrets are only `CLOUDFLARE_ACCESS_CLIENT_ID`, `CLOUDFLARE_ACCESS_CLIENT_SECRET`, and `CURSOR_CLOUD_AGENT_SSH_KEY`. The key is not on the host either. `helius.env` was not created, the installer was not run, and no Helius call was made. Probe and the 600k backfill are waiting on the secret being injected into this agent.

## Helius free run live (2026-09-25 17:01–17:17Z)

- **Key:** runtime `HELIUS_API_KEY` present; installed to `/var/lib/mal/backfill/helius.env` (mode **600**, owner **ubuntu**) via `install-helius-backfill-env.sh` (stdin, not argv). Host `~/mal` was missing PR #80 loader files; synced `tools/pump_history_backfill.py` + `scripts/mal-core/{pump-backfill.sh,mal-pump-backfill.service}` from agent checkout before install.
- **Probe:** `pump-backfill.sh probe 5` → 5/5 OK. No `credit*` response headers on `getBlock` (agent + host). **Confirmed `confirmed_credits_per_getblock=1`** (published historical price; dashboard not queried).
- **Unit:** `mal-pump-backfill.service` **active** (`NRestarts=0`). Drop-in `mal-pump-backfill.service.d/limits.conf`: `MAL_BACKFILL_CREDIT_CAP=600000`, `MAL_BACKFILL_RPS=6`, `MAL_BACKFILL_LOOKUP_RPS=3`, `MAL_BACKFILL_UNTIL=2026-09-25T17:00:00Z`, 40 GiB byte cap default. `mal-funding-graph` still **public @ 2 rps**; tape / observe / forward-paper / attention untouched.
- **15 min sample:** ~**158 blocks/min** (~2 404 credits, ~1 credit/getBlock). Hour `2026-09-25T16` **partial** (2 375/13 828 slots, 191 693 trades, 0 RPC errors). Backfill dir **~274 MB** (+~130 MB in window). `/dev/sdb` **93% free**.
- **Cap projection:** **~1.8 chain-days** before 600 k credits; **~3 days** before 40 GiB at observed compress rate (credits bind first). Errors: none in checkpoint or log tail.
