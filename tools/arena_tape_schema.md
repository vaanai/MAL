# Arena tape schema (`arena_tape_v1`)

Written by `tools/arena_feed.py`, read by `tools/arena_state.py`, the arena paper engine and the arena bots. Paper only. The feed uses public RPC
(`logsSubscribe`), spends 0 Helius credits and holds no keys.

## Files

| Path (in `--out-dir`, default `/data/mal/arena/tape`) | What |
| --- | --- |
| `tape-<YYYY-MM-DDTHH>.jsonl` | The OPEN hour (and, for 2 minutes, the hour just closed). The hour is the UTC hour of the row's `t_recv_ms`. One strict JSON object per line, flushed per line (`tools/tape_lines.py` counts them clean). Append-only. |
| `tape-<YYYY-MM-DDTHH>.jsonl.zst` | A CLOSED hour. 120 s after the hour ends the feed compacts it in a background thread (ingest never waits): `zstd -9` to `<file>.zst.tmp`, fsync, rename, fsync the directory, then a full decompress is compared with the source (bytes, lines, sha256) and ONLY THEN the `.jsonl` is deleted. A mismatch removes the `.zst` and keeps the `.jsonl` (retried on 3 scans). The decompressed bytes are identical to the `.jsonl`. While both exist, readers use the `.jsonl`. The writer refuses every hour whose end plus 120 s has passed, whether or not its `.zst` exists yet: a row for such an hour goes to the current hour's file instead (`rows_late_to_current_hour` in the status), so nothing appends to a file the compactor may take. A killed process can leave a half-written last line; when the writer reopens an hour file it ends such a line with a newline (`torn_lines_ended`), so the next row never fuses with it (the torn line itself stays one bad line). A `*.zst.tmp` left by a killed run is deleted at the next start. Needs the `zstd` CLI (the feed refuses to start without it; `--no-compress` turns compaction off). |
| `arena-feed-status.json` | Atomic (tmp + rename), rewritten every 60 s and at stop. Rows per minute per program, last slot, socket states, gap count, RSS. |
| `arena-feed-errors.log` | Exception class, message[:200] and slot only. Capped at 2 MB. |
| `.arena-feed.lock` | `flock(LOCK_EX\|LOCK_NB)` held for the process life. A second instance exits with code 3. |

## Common keys (every row)

`v` (1), `type`, `prog`, `slot`, `t_recv_ms`, `signature`, `event_index`.

- `type`: `trade`, `create`, `complete`, `pool_create`, `pool_info`, `gap`, `hb`, `start`, `stop`, and `takeover` (v2 hand-off only).
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

`kind`, `data_loss` (bool), `from_ms`, `to_ms`, plus kind fields. **Every gap row carries its span `[from_ms, to_ms]`** in `t_recv_ms`, both ends
inclusive. Rules per program, as in `tools/h5_shadow.py` (`GAP_SLOTS = 30`, `SILENCE_S = 20`):

| kind | when | `data_loss` | span | extra fields |
| --- | --- | --- | --- | --- |
| `slot_jump` | a notice more than 30 slots above the program's high-water slot | true | from the `t_recv_ms` of the notice that set the high-water slot, to this notice | `from_slot`, `to_slot`, `missed_slots` |
| `silence` | no notice from the program for more than 20 s (the process start counts as the last sign of life) | true | **written twice**: open at detection (`closed` false, `to_ms` = detection time), closed when the feed delivers again (`closed` true, `to_ms` = that notice's `t_recv_ms`). Same `from_ms` (the last notice): one span | `closed`, `silent_ms` |
| `feed_restart` | the program's source was rebuilt in a live process | true | **written twice**: open at the restart (`from_ms` = last notice before the outage), closed at the first notice of the new source. The restart resets the slot mark, so no `slot_jump` follows | `closed`, `reason` |
| `socket_reconnect` | the program's socket reconnect counters advanced | true only when EVERY socket of that program was down or silent at a common instant (`observe.link_state`); **false when a peer covered the drop** | the common outage when link state knows it, else the look time | `delta_reconnects`, `per_socket_delta`, `delta_closes`, `delta_rejections`, `delta_errors`, `sockets`, `common_down_ms` |
| `process_restart` (prog `all`) | a new process started and an earlier run left a tape or a status file (a kill, OOM, MiScusi cancel, crash or a clean stop: the window until this start is a window with no feed) | true | the previous run's last `t_recv_ms` (the newest `.jsonl` tail, or the status file's `updated_ms` if newer) to this process's start | `count`, `clean_stop` (the last row was a `stop` row), `torn_tail` (the last line was half-written; the writer ends it with a newline), `prev_pid` |
| `decode_error` / `write_error` / `consume_error` | rows were lost: a notice that did not decode, a row the writer could not write (an `OSError`), or an unexpected exception while handling a notice. A burst is coalesced into ONE record once it has been quiet for 1 s, and written when the writer works again | true | first to last lost row | `count`, `from_slot`, `to_slot` |
| `disk_reserve` (prog `all`) | written once, when free space is back at or above `--min-free-pct` plus 2 points (default 25 + 2) after a pause; nothing is written during the pause | true | `paused_since_ms` to the resume | `reason` (`disk_reserve`), `paused_since_ms`, `paused_ms`, `rows_dropped`, `free_pct`, `min_free_pct`, `resume_margin_pct` |

**ORDER (the engine assumes it; tested in `tools/test_arena_gap_order.py`).** A gap row is written BEFORE the first trade row after it. Any gap a notice
reveals (`slot_jump`, a closed `silence` or `feed_restart`, a `socket_reconnect`) is written before that notice's own rows. A reconnect is checked on the
first notice after it, not by the 5 s housekeeping tick, so no trade can overtake its gap row. As a property of one file: no trade row that sits before
a gap row has `t_recv_ms >= gap.to_ms`. (An OPEN `silence` / `feed_restart` record is written at detection, by definition before the feed delivers again;
`process_restart` is written right after the `start` row, before any trade of the new process.)

**Is a decision on the first row after a gap inside the span?** Yes. The span is inclusive and its `to_ms` is the `t_recv_ms` of the first
notice after the gap (a closed `silence` / `feed_restart`, a `slot_jump`) or of this process's start (`process_restart`), so a row with
`t_recv_ms == to_ms` is inside it. `tools/arena_state.ArenaState.gap_spans(t0, t1, prog)` / `has_gap(t0, t1, prog)` answer "does [decision, exit] hold a gap?"
for the last 24 h, counting the program's own gaps and the `all` gaps. A window just BEFORE a decision that holds a gap (a 60-min signal window) is not
marked on any row: ask `has_gap` for that window too.

Which kinds the arena engine treats as excluding is the engine's decision, not the feed's: the feed marks `data_loss` false only for a `socket_reconnect`
that a peer socket covered (the tape is then complete as far as the feed can tell, but a disconnect did happen).

An hour with a `data_loss` gap, or with fewer than 60 `hb` rows per program, is a bad hour for any consumer that needs a complete tape. A process that
exits non-zero (exit code 4: writes failing for 120 s, or a core task ended) is restarted by its supervisor and the restart writes a `process_restart`.

## `takeover` (prog `all`; written only by `tools/arena_feed_v2.py`, P35)

When a v2 standby takes the tape over from a running writer and proves continuity for BOTH programs, it writes ONE `takeover` row instead of `start` +
`process_restart`. Its fields are listed in docs/research/p35-longbook-handoff/DESIGN.md section 2.1:
- `cover_from_ms`, `old_last_data_ms`, `continuity` (per program);
- `dedup` (`buffered`, `dropped_dup`, `written_missed`, `buffer_overflow`);
- `agreement` (`n_old`, `only_old` per program);
- `standby_reconnects_in_overlap`, `thresholds`, `counter_base`, `overlap_min_s`;
- `prev_pid`, `pid`, `code_sha`, `lock_acquired_ms`, `old_stop_ms`.

What follows it:
- The old writer's `stop` row precedes it.
- The rows the old writer missed follow it, with their own (earlier) `t_recv_ms`.
- Then the new writer is live.

Readers that do not know the type ignore it: `ArenaState.apply`, `ArenaEngine.on_row` and `arena_hour_health` (tested in
`tools/test_arena_feed_v2.py`). If continuity is NOT proven, the v2 writer writes exactly v1's restart rows instead: a `start` row and a
`process_restart` gap with `reason` `handover_unproven` and the failed conditions. `tools/feed_handover_proof.py seam` checks a seam.

## `hb`, `start`, `stop`

- `hb`: one per program per minute: `rows_last_min` (data rows in the last completed minute), `notices`, `gaps`, `sockets_up`, `rss_mb`.
- `start`: `schema`, `code_sha`, redacted `argv` (URLs keep scheme and host only), `out_dir`, `pid`, `sockets`, `commitment`, `keys: "none"`, `sends: "none"`.
- `stop`: final `rows_by_prog_type`, `gaps`, `lines_written`, `rows_dropped_disk`, `decode_errors`.

## What the tape is not

- Commitment is `confirmed` from `logsSubscribe`. A transaction can be dropped by the public node (see the gap rules) and the tape is not chain truth.
- Loss is marked, not hidden: a decode error, a write error and a process restart each become a `data_loss` gap row (see `gap`). The one thing a gap row
  cannot mark is a loss the public node never delivered; that shows as a `slot_jump` or a `silence` only when the gap is wide enough.
- A failed transaction produces no row (it still counts toward the program's slot clock and silence rule).
- No block position (`tx_index`) and no `block_time`. Order within a slot is arrival order, not chain order.
- Rows from the two programs are interleaved by arrival; `t_recv_ms` can go back by a few milliseconds between rows.
- Size, measured on the 120 s smoke tape of 2026-10-18 about 03:15Z (a Sunday morning, one sample; the day's rate varies): 11,482,016 bytes
  (14,118 rows) uncompressed, 2,162,633 bytes after `compact_file` (zstd -9): ratio 5.31. At that rate that is about 0.34 GB per hour and 8.27 GB per
  day uncompressed in the open hour, and about 1.56 GB per day once compacted (8.27 / 5.31). Level 3 gave 4.74 and level 19 gave 5.66 on the same
  data. The ratio of a full hour is not measured (a longer file may compress a little better).
- Disk reserve (DEC-031 rev 2 s3.8, "Walk 2 keeps its room"): no arena write may take the out-dir filesystem's free space below `--min-free-pct`
  (default 25). Free space is `shutil.disk_usage(out-dir).free / total`, the way the walk-2 walker measures it. It is checked before every hour
  file opens and at least every 60 s. Below the reserve the feed writes NO row of any type (`hb`, `start`, `stop` and gap rows included) and opens no
  file; the only trace is a flag in `arena-feed-status.json` (`writing_paused`, `disk_reserve`). When space allows again it writes ONE gap record,
  `kind` and `reason` `disk_reserve`, with `paused_since_ms`, `paused_ms`, `rows_dropped`, `free_pct`, `min_free_pct`. Compaction is an arena write
  too: it runs only while the free space, less half the source file's size (the `.zst` is about a fifth of it), stays at or above the reserve;
  otherwise the closed hour stays a `.jsonl` and the scan is retried (`compaction.skipped_disk` in the status).
  Hysteresis: after a pause the feed resumes only at the reserve plus 2 points, because the feed and the walker both write and free space hovers.
  **Writes that remain below the reserve, by design:** `arena-feed-status.json` (about 2 KB, the flag), the lock file and the directory (created at the
  first start, before any disk reading, so a second instance can be refused). **Writes that can still happen above it:** when an hour file is already
  open, rows are written for up to 60 s after free space crosses the reserve (the periodic check is every 60 s; the check before opening a file covers
  the hour boundary). The error log is gated like a row. Compaction cannot run below the reserve either, so the feed cannot recover by itself: space
  has to be freed by someone else.
