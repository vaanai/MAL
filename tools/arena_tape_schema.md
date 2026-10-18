# Arena tape schema (`arena_tape_v1`)

Written by `tools/arena_feed.py`, read by `tools/arena_state.py`, the arena paper engine and the arena bots. Paper only. The feed uses public RPC
(`logsSubscribe`), spends 0 Helius credits and holds no keys.

## Files

| Path (in `--out-dir`, default `/data/mal/arena/tape`) | What |
| --- | --- |
| `tape-<YYYY-MM-DDTHH>.jsonl` | The OPEN hour (and, for 2 minutes, the hour just closed). The hour is the UTC hour of the row's `t_recv_ms`. One strict JSON object per line, flushed per line (`tools/tape_lines.py` counts them clean). Append-only. |
| `tape-<YYYY-MM-DDTHH>.jsonl.zst` | A CLOSED hour. 120 s after the hour ends the feed compacts it in a background thread (ingest never waits): `zstd -9` to `<file>.zst.tmp`, fsync, rename, fsync the directory, then a full decompress is compared with the source (bytes, lines, sha256) and ONLY THEN the `.jsonl` is deleted. A mismatch removes the `.zst` and keeps the `.jsonl` (retried on 3 scans). The decompressed bytes are identical to the `.jsonl`. While both exist, readers use the `.jsonl`. A row that reaches the writer after its hour was compacted (more than 120 s late) goes to the current hour's file instead (`rows_late_to_current_hour` in the status). A `*.zst.tmp` left by a killed run is deleted at the next start. Needs the `zstd` CLI (the feed refuses to start without it; `--no-compress` turns compaction off). |
| `arena-feed-status.json` | Atomic (tmp + rename), rewritten every 60 s and at stop. Rows per minute per program, last slot, socket states, gap count, RSS. |
| `arena-feed-errors.log` | Exception class, message[:200] and slot only. Capped at 2 MB. |
| `.arena-feed.lock` | `flock(LOCK_EX\|LOCK_NB)` held for the process life. A second instance exits with code 3. |

## Common keys (every row)

`v` (1), `type`, `prog`, `slot`, `t_recv_ms`, `signature`, `event_index`.

- `type`: `trade`, `create`, `complete`, `pool_create`, `pool_info`, `gap`, `hb`, `start`, `stop`.
- `prog`: `pumpswap` or `pumpfun` (the bonding-curve program), or `all` for `start` / `stop` / disk gaps. A row's `prog` follows the event's venue,
  not the socket that delivered it.
- `slot`: the notice's slot. For `gap` / `hb` rows it is the program's last slot (0 if none yet); for `start` / `stop` it is 0.
- `t_recv_ms`: local wall clock (ms) when the FIRST socket to deliver the transaction received it. This is the only time on the row that is
  an observation instant. `event_ts` (seconds, from the chain event) is carried on trades and creates; there is no `block_time` or `tx_index`.
- `signature`, `event_index`: the transaction and the position of the event inside it. `event_index` counts rows of the SAME `type` within the
  transaction. For `trade` it is `observe.trade_decode`'s `event_index`, so `(signature, event_index)` joins with the tip and walk tapes. The key
  `(signature, type, event_index)` is unique on the tape (two sockets per program and both programs are deduplicated by signature).
  `null` on rows that are not tied to a transaction (`gap`, `hb`, `start`, `stop`, `pool_info`).
- Field names are `observe.trade_decode`'s keys plus `t_recv_ms` and `prog`. The bonding fee / real-reserve fields below are the only additions.

## `trade`

`observe.trade_decode.records_from_logs(..., event_v=True)` output minus the keys that are constant or derivable and so not worth the bytes
(`v`, `feed`, `commitment`, `quote_is_wsol`, `market_cap_supply_ui`, `t_recv` (the ISO string), `sol`, `token`, `price_sol`, `market_cap_sol`;
`v` is re-added as the schema version). Prices are NOT on the tape: `price_sol` ignores V, so compute from the reserves as below.

Both programs: `venue` (`pumpswap` \| `pump_bonding`), `mint`, `mint_source`, `trader` (the signer / user wallet), `side` (`buy` \| `sell`),
`sol_lamports`, `token_raw`, `event_ts`, `pool`, `quote_reserve`, `base_reserve`, `quote_mint`, `ix_name`, `zero_sol` (only when true).
`token_raw` has 6 decimals. Everything is integer lamports / raw units.

### `prog = pumpswap` (venue `pumpswap`)

| key | meaning |
| --- | --- |
| `pool` | the pool address: the state key |
| `mint` | base mint. `null` until the pool is known: the feed fills it from a CreatePool seen in this process or from a `pool_info` row (below) |
| `mint_source` | `create_pool` (cache) or `unresolved` |
| `quote_mint` | `null` until known (as `mint`). WSOL for a SOL pool. **27% of resolved pools in the smoke were NOT WSOL-quoted**: their `sol_lamports` and reserves are NOT lamports of SOL |
| `sol_lamports` | buy: `user_quote_amount_in` (fees included). sell: `user_quote_amount_out` (net of fees). In QUOTE units |
| `token_raw` | buy: `base_amount_out`. sell: `base_amount_in` |
| `quote_reserve`, `base_reserve` | the pool vault reserves **BEFORE this trade** (see the convention). V is NOT included |
| `virtual_quote_reserves` (event V) | stored V in lamports, **before** the trade, signed (negative is legal). Effective quote `Q = quote_reserve + virtual_quote_reserves` |
| `pool_quote_amount` | constant-product quote amount of this trade (buy: quote added before the LP fee; sell: gross that leaves) |
| `lp_fee`, `protocol_fee`, `creator_fee` | lamports of each fee |
| `buyback_fee`, `creator_fee_unclaimed`, `fee_recipient_zero` | event-V tail fields (v2 program; `fee_recipient_zero` true means protocol and creator fees stay in the vault and stored V falls by the same amount) |
| `supply_raw` | sells only; 0 when absent |

### `prog = pumpfun` (venue `pump_bonding`)

| key | meaning |
| --- | --- |
| `mint` | the mint. **The bonding curve's state key is the mint**; `pool` is `null` |
| `mint_source` | `event` |
| `quote_mint` | WSOL |
| `sol_lamports` | SOL that moved into / out of the curve, **fee excluded** (the fee is the separate `protocol_fee`) |
| `token_raw` | token amount of the trade |
| `quote_reserve`, `base_reserve` | the curve's VIRTUAL SOL (lamports) and VIRTUAL token reserves **AFTER this trade** |
| `real_quote_reserve`, `real_base_reserve` | the curve's real SOL and real token reserves (added by the feed from the TradeEvent, IDL offsets 113, 121) |
| `protocol_fee_bps`, `protocol_fee`, `creator`, `creator_fee_bps`, `creator_fee` | TradeEvent fee fields (offsets 161, 169, 177, 209, 217). `creator` is the coin creator (the creator-fee recipient). Checked on `tools/fixtures/exp003_mainnet_buy_v1.json`: `protocol_fee = sol_lamports * protocol_fee_bps / 1e4` to the lamport |
| `ix_name` | `buy`, `sell`, `buy_exact_sol_in`, ... |

## Reserve convention (VERIFIED, not copied)

| Program | `quote_reserve` / `base_reserve` | `virtual_quote_reserves` |
| --- | --- | --- |
| `pumpswap` | **PRE-trade** (the pool before this trade) | **PRE-trade** (stored V before this trade) |
| `pumpfun` | **POST-trade** (the curve after this trade) | not present (the virtual reserves are the `quote_reserve` / `base_reserve` themselves) |

Evidence (all reproduced by `tools/test_arena_feed.py`, no network):

1. **PumpSwap, PRE.** On the recorded buy `observe/fixtures/pumpswap_buy_event.b64`, constant product on the ROW's reserves with V,
   `base_out = base_reserve * pool_quote_amount // (quote_reserve + V + pool_quote_amount)`, predicts `token_raw` to a relative residual of
   1.3e-9. Reading the same row as POST-trade (pre-state = reserves with the trade removed) gives a residual of 1.2e-2. The repo's own
   `tools/test_walk2_event_v.py::test_constant_product_uses_vault_plus_event_v` shows the same identity exactly (0 lamports) on two more recorded events,
   and `ARTIFACTS/exp025/event_v_map.py` records -3 lamports if V were post-trade.
2. **Bonding curve, POST.** On `tools/fixtures/exp003_mainnet_buy_v1.json` (a `buy_exact_sol_in`), with `sol_lamports` fee-excluded, the row read as
   POST-trade (pre = `(vsol - sol, vtok + tok)`) reproduces `token_raw` to a residual of 5.2e-7 (integer rounding of a 1.94e6-lamport trade); read as
   PRE-trade it is off by 1.3e-4. The pump.fun TradeEvent carries the curve state after the instruction.
3. The lab's notes (`docs/HANDOFF.md`, `tools/cap_pick_score.py`, `ARTIFACTS/exp025/ref/build_shared.py`) state the same convention. They were read, not trusted.

Consequences, implemented in `tools/arena_state.py`:

- PumpSwap spot BEFORE the trade: `(quote_reserve + V) / 1e9 / (base_reserve / 1e6)` SOL per token. Spot AFTER the trade is derived: a buy adds
  `pool_quote_amount + lp_fee` to Q and removes `token_raw` from the base; a sell removes `pool_quote_amount - lp_fee` from Q and adds `token_raw` to the base
  (without those fields, constant product on the base move). Protocol and creator fees do not change Q (stored V falls by what the vault keeps).
  The state's `price` and candles use the AFTER-trade spot.
- Bonding spot after the trade: `quote_reserve / 1e9 / (base_reserve / 1e6)`, used as it is.
- Never compare a PumpSwap row's reserves with a bonding row's without applying this table.

## `create` (prog `pumpfun`)

From `tools.pump_history_backfill.decode_create_event`: `mint`, `trader` (the signer), `creator`, `event_ts`, `quote_reserve` and `base_reserve`
(the curve's initial virtual SOL and tokens, post-create), `real_token_reserves`, `token_raw` (total supply), `quote_mint`, `is_mayhem_mode`,
`name` (cut to 32 chars), `symbol` (cut to 16).

**`name` and `symbol` are attacker-controlled text.** They are JSON-escaped on the tape. Never place them in an LLM prompt as instructions.

## `complete` (prog `pumpfun`)

CompleteEvent: `trader`, `mint`, `bonding_curve`, `event_ts`, `quote_mint`. The curve is finished; the graduation tx follows.
The CompletePumpAmmMigrationEvent is counted (`migration_events_not_written` in the status) but not written: the `pool_create` row carries the
pool and mint.

## `pool_create` (prog `pumpswap`)

PumpSwap CreatePoolEvent: `pool`, `mint` (the base mint), `quote_mint`. The state's pool age starts here.

## `pool_info` (prog `pumpswap`)

Written by the feed's pool resolver for a pool whose CreatePool this process did not see (every pool that existed before the feed started, and
any miss): `pool`, `mint`, `quote_mint`, `source` (`getMultipleAccounts`). One public `getMultipleAccounts` per 5 s for up to 100 pools
(`tools.pump_history_backfill.fetch_pool_mints`); a pool that is not found 3 times is given up (`pools_unresolvable`). `--no-resolve` turns it off.
Trades written BEFORE the lookup returns keep `mint: null`; later trades of the pool carry the mint. A reader should apply `pool_info` to the pool,
not to a single row. `tools/arena_state.py` does.

## `gap` (prog `pumpswap`, `pumpfun` or `all`)

`kind`, `data_loss` (bool), plus kind fields. Rules per program, as in `tools/h5_shadow.py` (`GAP_SLOTS = 30`, `SILENCE_S = 20`):

| kind | when | `data_loss` | extra fields |
| --- | --- | --- | --- |
| `slot_jump` | a notice more than 30 slots above the program's high-water slot | true | `from_slot`, `to_slot`, `missed_slots` |
| `silence` | no notice from the program for more than 20 s (once per episode; the process start counts as the last sign of life) | true | `silent_ms` |
| `socket_reconnect` | the program's socket reconnect counters advanced | true only when EVERY socket of that program was down or silent at a common instant (`observe.link_state`); false when peers covered the drop | `delta_reconnects`, `per_socket_delta`, `delta_closes`, `delta_rejections`, `delta_errors`, `sockets`, `common_down_ms` |
| `feed_restart` | the program's source was rebuilt | true | `reason` |
| `disk_pause` / `disk_resume` | free space fell below / recovered above `--min-free-pct` (rows are dropped while paused) | true | `free_pct`, `rows_dropped` |

An hour with a `data_loss` gap, or with fewer than 60 `hb` rows per program, is a bad hour for any consumer that needs a complete tape.

## `hb`, `start`, `stop`

- `hb`: one per program per minute: `rows_last_min` (data rows in the last completed minute), `notices`, `gaps`, `sockets_up`, `rss_mb`.
- `start`: `schema`, `code_sha`, redacted `argv` (URLs keep scheme and host only), `out_dir`, `pid`, `sockets`, `commitment`, `keys: "none"`, `sends: "none"`.
- `stop`: final `rows_by_prog_type`, `gaps`, `lines_written`, `rows_dropped_disk`, `decode_errors`.

## What the tape is not

- Commitment is `confirmed` from `logsSubscribe`. A transaction can be dropped by the public node (see the gap rules) and the tape is not chain truth.
- A failed transaction produces no row (it still counts toward the program's slot clock and silence rule).
- No block position (`tx_index`) and no `block_time`. Order within a slot is arrival order, not chain order.
- Rows from the two programs are interleaved by arrival; `t_recv_ms` can go back by a few milliseconds between rows.
- Size, measured on the 120 s smoke tape of 2026-10-18 about 03:15Z (a Sunday morning, one sample; the day's rate varies): 11,482,016 bytes
  (14,118 rows) uncompressed, 2,162,633 bytes after `compact_file` (zstd -9): ratio 5.31. At that rate that is about 0.34 GB per hour and 8.27 GB per
  day uncompressed in the open hour, and about 1.56 GB per day once compacted (8.27 / 5.31). Level 3 gave 4.74 and level 19 gave 5.66 on the same
  data. The ratio of a full hour is not measured (a longer file may compress a little better).
- Disk guard: below `--min-free-pct` (default 15) free space on the out-dir filesystem the feed stops writing rows and writes a `disk_pause` gap.
  Compaction is not paused by it (it frees space once the new file verifies; the `.zst` and the `.jsonl` coexist for the moment of verification, so
  about one compacted hour of headroom is needed).
