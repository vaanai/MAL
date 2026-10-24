# P8 VENUES (desk, 2026-10-24 ~01:30Z)

Record first: **Raydium LaunchLab** (full trade decode) and **Meteora DBC** (instruction-level only). Both by public `logsSubscribe` (api.mainnet-beta + solanavibestation,
as arena_feed does: 0 Helius credits, no key; arena_feed refuses Helius URLs, venue_tape does too).

| Venue | Program id (executable on mainnet, checked via getAccountInfo 10-24) | Source of the format |
|---|---|---|
| Raydium LaunchLab (LetsBonk is a platform on it) | LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj | official IDL raydium-io/raydium-idl (address field matches) |
| Meteora DBC | dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN | executable check + live logs only; DBC IDL not fetched (404 at the guessed path); ids below are from my memory, verified only as executable programs |
| Raydium CPMM (LaunchLab graduation) | CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C | executable check only |
| Meteora DAMM v2 (DBC graduation) | cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG | executable check only |

## Formats
- LaunchLab: Anchor `emit!` -> `Program data`. `TradeEvent` = pool, 13 u64 (total_base_sell, virtual_base/quote, real_base/quote before and after, amount_in/out,
  protocol/platform/creator/share fee), direction, pool_status, exact_in: pre AND post reserves, as needed. Decoded from the IDL; checked on live logs
  (quote reserve delta = amount_in - fees). `PoolCreateEvent` gives pool/creator/config. Signer and mint are NOT in the logs.
- DBC: events are self-CPI; logs hold only instruction names (Swap/Swap2, InitializeVirtualPool*, MigrationDammV2). No amounts, reserves or signer. Reserves would need
  getTransaction inner instructions (public RPC, rate-limited) or a paid source: not done.
- CPMM / DAMM v2 not recorded: "mentions" subscriptions there are router noise (90 s sample: CPMM 458, DAMM v2 7,413 notices, mostly aggregator swaps); a post-graduation
  tape needs a per-pool filter from the migration rows. Next step once a screen needs it.

## Rates seen (90 s and 7 min samples, one afternoon, so indicative only)
| | notices/s | rows/day | note |
|---|---|---|---|
| LaunchLab | ~0.1 | ~12k rows, roughly 5k trades | quiet venue; 27 trades in 7 min |
| Meteora DBC | ~25 | ~2.2M ix rows (~590 MB raw, ~60 MB zst) | ~28% of swap txs fail |
LaunchLab undercount: the recorder's logsSubscribe rate (~5k trades/day) is an undercount. The Helius backfill of the same venue (#857 REPORT, job #1465) has 1,222,631 trade rows in 241 h, about 122k per day, roughly 25x more. The recorder was not measured against the backfill, so its capture rate is unknown until the two tapes are compared. A forward book fed by this recorder would see only part of the venue's flow; do not size one from the 5k figure.
Launches/day and graduations/day: not measured yet; they come out of the tape (`create` rows, Migration ix rows) in the first days.

## Limits
Public RPC at `confirmed` drops notices silently; gap rows only mark reconnects/silence. solanavibestation answered 429 to the second socket in the smoke (the arena feed #1207 uses the same IP and host), so the effective redundancy may be one socket. No sentence here says either venue is profitable.
