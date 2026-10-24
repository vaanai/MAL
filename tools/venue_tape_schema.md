# Venue tape schema (`venue_tape` v1)

Written by `tools/venue_tape.py`. Public RPC `logsSubscribe` only (0 Helius credits, no keys). Paper/research only.

Layout under `--out-dir` (default `/data2/mal/venue-tape`): `<venue>/tape-<YYYY-MM-DDTHH>.jsonl` (open hour; `.jsonl.zst` after the hour ends + 120 s,
verified by `tools/arena_zst.compact_file`), `venue-tape-status.json` (every 60 s), `.venue-tape.lock`. Disk guard: below 20% free nothing is written
(`disk_pause` row, then `disk_resume` with `rows_dropped`).

Common keys: `v`, `venue`, `type`, `slot`, `t_recv_ms` (local receive ms of the first socket to deliver), `signature`, `event_index` (per signature and
type). Flags when true: `failed`, `via_cpi` (program called by a router), `logs_truncated`.

| venue | type | content |
| --- | --- | --- |
| launchlab | `trade` | IDL `TradeEvent`: `pool`, `total_base_sell`, `virtual_base`, `virtual_quote`, `real_base_before/after`, `real_quote_before/after`, `amount_in`, `amount_out`, `protocol_fee`, `platform_fee`, `creator_fee`, `share_fee`, `side`, `pool_status` (fund/migrate/trade), `exact_in`; `ix` |
| launchlab | `create` | `pool`, `creator`, `config`; the rest of `PoolCreateEvent` kept raw (`params_undecoded_b64`) |
| launchlab | `ix` | program ran but logged no decodable event (failed tx, claim, migrate, truncated logs); `ix` = instruction names |
| any | `event_undecoded` | unknown discriminator / odd length / bad base64: `reason`, `disc`, `data_b64` |
| meteora_dbc | `ix` | one per transaction: instruction names (`Swap`, `Swap2`, `Swap3`, `InitializeVirtualPool*`, `Migration*`), `amounts_undecoded: true`. DBC events are self-CPI and are not in the logs |
| all | `start` `stop` `hb` | process records |
| all | `gap` / `gap_end` | `reason` `socket_reconnect` (socket index, new reconnects) or `silence` (no notice for the venue for `silence_s`; `gap_end` carries `start_ms`, `end_ms`) |

NOT in the logs, so absent: signer/trader, mint (LaunchLab `trade` has only the pool), SOL-vs-token orientation beyond `side`, DBC amounts and reserves,
the LetsBonk-versus-other-platform label. Prices are not stored. The notice stream is `confirmed` and public-RPC-lossy; gap rows say where it was not
watched, but a dropped notice inside a live socket is not detectable.
