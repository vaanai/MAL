# DESIGN: arena tip adapter (QP-RULING-1101-hv4-reads section 4.1 (B))

Status: code PR #1165, round 2 (after QP and reviewer CHANGES at 451bac48). Nothing deployed, nothing run on a real tip file. No book has cleared the promotion gate. This note says what the
adapter does, how its output reaches an engine, how to measure the feed delay, and what records QP items 1, 5 and 6 still need. It measures nothing about any book.

## 1. What was built

| Piece | File |
| --- | --- |
| Adapter, source (status gate), feed-delay helper | `tools/arena_tip_adapter.py` |
| Vendored tip reader (byte copy of `tools/r4b_tip_reader.py` under a non-r4b name) | `tools/arena_tip_reader.py` |
| Tests | `tools/test_arena_tip_adapter.py`, `tools/test_arena_tip_equiv.py` |
| md5 decision-equivalence harness + job script | `scripts/research/arena_tip_equiv.py`, `scripts/research/arena-tip-equiv.sh` |

**Reader, DEC-031 section 3 item 1.** The item is a file-name rule ("no arena process opens `tools/r4b_*.py`"), so the adapter does not import `tools/r4b_tip_reader.py`. `tools/arena_tip_reader.py` is its
copy, the `tools/arena_stack.py` pattern: a header (source blob `78ad1857...`, last commit `8e4d93aa`, "do not edit by hand") and then the source file byte for byte.
`test_the_vendored_reader_is_the_r4b_reader_byte_for_byte` (a test, not an arena process) recomputes the git blob of the source and compares the text; it fails the day the r4-b lane changes its file, and
the copy is then re-vendored and re-reviewed. No r4b file is changed, so the r4-b DRY install is untouched, and that lane stays free to change its reader.

## 2. Mapping and its rules

- trade: `prog` by venue; the walker row's own keys minus the tape's `TRADE_DROP`, `source` and the tip-only singular `virtual_quote_reserve`. Event V (`virtual_quote_reserves`) stays.
- create / complete: as the tape's. A walker `migration` row becomes the tape's `pool_create` (pool, mint, quote_mint). **On this source `pool_create` exists for migration pools only** (the walker decoder emits no CreatePool rows), unlike the public tape; ArenaState consumers that age a pool from `pool_create` see no row for a pool created outside a migration.
- Gaps: every `gaps.jsonl` row and the reader's `tip_bad_line` / `hour_file_missing` become one row `type gap, data_loss true, kind tip_gap_<reason>` (built in the adapter, never truncated: `tip_gap_skip_contradicted_refetch`), `from_slot`, `to_slot`, `missed_slots`, `[from_ms, to_ms]`, `src "tip"`. Placed before the first row of slot >= `from_slot`, not before the lower slots are released. **prog:** `"all"` for block-level loss (`backlog_jump`, `unfetchable`, `skip_contradicted`, `skip_contradicted_refetch`, `tip_bad_line`, `hour_file_missing`, `unparsable`); `"pumpswap"` for `unresolved_dropped`, `mixed_tx_index` and any bad PumpSwap row; the row's venue prog for any other bad row. Emitted kinds: those eight plus `tip_gap_mixed_tx_index` and `tip_gap_bad_row_{tx_index,fields,venue,slot,kind,migration_type}`.
- Skipped slots: counted, never a row, never a gap.
- Not emitted: `hb`, `start`, `stop`, `pool_info`.

**Rows without `tx_index` (QP on #1165).** PumpSwap prints that come back through `resolve_unresolved` carry none (`rows_from_block` appends the unresolved record before `tx_index` is set; the walker does the same, so exploration data has them too). They **pass through** without the key. `ArenaEngine.order_key` gives them the receive-order form `(slot, 1, t_recv_ms, signature, event_index)`, which is deterministic here because the follower stamps one `t_recv_ms` on every row of a slot. Across slots the two forms coexist (slot is the first element and `0` vs `1` decides before any mixed types meet; a test feeds one pool both forms through the engine). Inside one (pool, slot) only the order of two or more *transactions* is imprecise (lexical by signature): an order imprecision, not data loss. A (pool, slot) group that mixes the two forms cannot come from the decoder; if one appears the adapter writes a `pumpswap` data_loss gap there. Counted in `stats`: `rows_no_tx_index`, `pool_slots_no_tx_index`, `pool_slots_multi_sig`, `pool_slots_mixed`. QP's trigger: if `pool_slots_multi_sig` is above 1 an hour outside the first hour after a follower start, QP re-rules before any clock. A bonding trade without `tx_index` cannot come from the decoder and is still dropped and marked.

**Schema delta against what `tools/arena_feed.py` writes** (pinned by `test_the_output_delta_against_the_feed_is_exactly_the_documented_one`, from the real decoder). The tip rows are the walker's *slim* rows (`observe/trade_store.stored_trade`, one of the three pinned decoder blobs), so these keys are **not on the tip tape and cannot be produced by the adapter**: PumpSwap `pool_quote_amount`, `lp_fee`, `protocol_fee`, `creator_fee`, `mint_source` (and `supply_raw` on sells that carry it); bonding `real_quote_reserve`, `real_base_reserve`, `protocol_fee_bps`, `protocol_fee`, `creator`, `creator_fee_bps`, `creator_fee`. Effect: `ArenaState.pumpswap_post` falls back to constant product on the base move (arena_state.py:80-92), so the state's price and candles differ slightly from the public tape's (the test shows the two results differ); `ArenaEngine` pricing reads reserves and V only and is unaffected. Restoring them needs a change to the pinned decoder blobs or the follower (a new pin, a restart, its own proof): a decision for QP and the manager, not an adapter change. Added over the feed: `tx_index` and `block_time` on data rows (except the rows above), `src` on gap rows. Order: a `complete` or `pool_create` comes before its slot's trades (the feed writes them after the trade of the same transaction).

**Hour health.** `tools/arena_hour_health.py` reads tape-hour files, and option A writes none (QP: `hb` rows never count there). The Am.6 thresholds for this source (item 5) need a pinned, outcome-blind per-hour check on the host's own counts log. Not built here.

## 3. Transport: where the engine runs

The adapter is streaming and holds almost no state (a pending-gap list, two integers, one slot's pool groups). The cost is the engine's pool history, **not measured** on this source.

| Option | How | For | Against |
| --- | --- | --- | --- |
| A. Engine on fast-0, adapter in-process | `ArenaTipSource.poll()` feeds `ArenaEngine.on_row` in the same process | No tape copy, no mirror lag, no new seal beyond the tip entry; the follower's files never leave fast-0 | Shares headroom with the tip follower and listeners (one heavy job, at most 2 workers, `systemctl show user-1002.slice -p MemoryCurrent` first; the 2026-09-29 OOM); the r4-b DRY host is already there |
| B. Adapter output mirrored to research-0 (pattern of `tools/tape_mirror.py`, #1147) | Adapter writes `tape-<hour>.jsonl` on fast-0; research-0 dials fast-0 and pulls it | Engine on research-0 (121 GB), next to the other books | `tape_mirror` serves research-0's tape to fast-0; this is the reverse direction and a new source allowlist (a `tape_mirror` change, own proof). The output is a derived copy of the sealed tip tape: its own SEALS-BRIEF entry. Mirror lag enters the latency budget (from `.mirror/lag.jsonl`, never from rows) |

Recommendation: do the 24 h pre-clock smoke as option A with `ArenaEngine` for the first book (HV7: 60 min `T_max`), report anon RSS and the follower's `lag_slots` / `backlog_jumps` before, during and after with *both* tip readers running, and decide B only if the memory rule forbids A.

## 4. Feed delay (QP item 4, corrected by QP on #1165)

`StackConfig.feed_delay_slots` is required; HV4's 7 is for the public feed. The delay is `t_recv_ms - blockTime x 1000`. **The block lag is in `status.json`** (`lag_ms`, `block_lag_ms_p50`, `block_lag_ms_p90` over the last 200 blocks, `lag_slots`); `checkpoint.json` holds `last_done`, `last_t_recv_ms`, `gaps`, `skipped`, `writing` only. Neither file is under `/var/lib/mal/sealed`; agents may read both.

Method (`read_lag_sample`, `feed_delay_slots`): sample both files every 15 s for at least 24 h; `d = ceil((p90 of block_lag_ms_p90 + p90 host ingest lag) / (sps x 1000))`. The host ingest lag is the wall clock at `on_row` minus the row's `t_recv_ms` (timing only; checkpoint-then-drain polling adds delay before a bot sees a row); option B adds the mirror lag. `sps` as in `tools/arena_feed_delay.py`. `block_time` has one-second resolution (up to +1 s of quantisation, conservative). Pin `d` per book with its date. Scale check only: LAB_STATE row 115 (10-10) quotes p50 1,784 ms and p90 2,149 ms; at 0.4 s per slot that is d of 6 before ingest lag. Three weeks old, to be re-measured.

## 5. Records list for QP items 1, 5 and 6

**Item 1: QP ruling and SEALS-BRIEF entry** (r4-b pattern, QP-RULING-1031-r4b-history 2.1 conditions 1-6)
1. QP ruling naming the pinned arena adapter/engine host (install sha) as a tip-tape reader: files `trades-`, `creates-`, `migrations-`, `skipped-slots-<hour>.jsonl` and `gaps.jsonl` only; hours >= max(2026-10-16T01, start - 53 h); read-only; the code refusals; decoder pin from status.json failing closed; nice 19 / ionice idle (`lower_io_priority`, per thread: `start()` on the polling thread before other threads exist); follower, unit and tree untouched. The host's code path is the vendored reader (section 1).
2. The host writes a per-hour counts log (names and counts only; agents may read it): gap rows by kind and prog with slot ranges, data rows per program, longest data-row silence per program, the no-`tx_index` counts, dropped-row counts. A latched reader problem, or an unhealthy status for longer than a pinned time, becomes a hard gap (process_restart class) for open positions; a host restart is `engine_restart`. Bots get no call on catch-up rows (warm-up is state only); decisions only after `caught_up` and only on rows of hours >= 2026-11-01T00Z (EXP-024 Look-2 to 2026-11-14 on October hours, from any source). The r4-b DRY host and this host may read at the same time, both at idle priority; the smoke shows the follower's `lag_slots` and `backlog_jumps` unaffected with both running.
3. SEALS-BRIEF entry (manager) and the matching AGENT-BRIEF-COMMON section 2 line; each tip-source book's ledgers and decision rows sealed like other arena books; any adapter output file (option B) sealed as a derived copy; no join of arena outputs to r4-b's decision log before EXP-027's read; this tape is a near byte-copy of walk 2's content.
4. HOLDOUT_LEDGER changelog line and a Forward walk 2 status-cell note ("never a back door"; walk-2-hour information, DEC-030 section 2 R1). Read ACL if needed, in LAB_STATE row 115 with `date -u`; a LAB_STATE row for the host's job; the manager's P-C0 re-read.
5. **Now, before more code:** a job on fast-0 that reads only `status.json` / `checkpoint.json` every 15 s for >= 24 h: the P-C0 figures, the feed-delay samples, and the rates of `gaps`, `backlog_jumps`, `unresolved_dropped` (lambda_tip). It decides feasibility: section 4.4 of the ruling needs zero tip data_loss gaps in the 24 closed hours before the clock for any `T_max` >= 0.6 h.

**Item 5: DEC-031 amendment for books on this source:** the no-`tx_index` rule and its counts; the exact emitted gap kind strings and the prog of each (section 2); the section 1.4 gap kinds; hour health from the host counts log, with thresholds on data_loss spans and on data-row silence per program; `pool_create` = migration pools only; the schema delta (section 2); the source pinned in each registration (adapter sha, vendored reader sha, the three EXP027_DECODER_REF blobs, `feed_delay_slots` with its date, the restart rule); the walk-2-hour disclosure; the section 4.4 counter = the counts log's data_loss gap rows (a prog `"all"` row counts for both programs).

**Item 6: per book:** new `bot_id`, P0, H and clock (N + 1), not in a kill-review week (LAB_STATE "Now"); `T_max` from the code (HV7 `X_EXIT` 60 min, HV6 `max_hold_min` 720); the >= 24 h smoke on this source meeting section 4.4 (HV7's real-model smoke can double as it at the final sha), reporting dropped-row counts by code, anon RSS peak, follower lag before/during/after, rows per minute, the feed-delay measurement; per-book decision equivalence (the synthetic `FrozenOrders` bot proves the adapter, not a book); and the md5 equivalence of `scripts/research/arena-tip-equiv.sh` at the final head (config md5 `5579235fd66e7a69bf56a09c8ee5d7f7`).

## 6. Not measured, not proved

The equivalence has not been run on a full exploration hour or six (see the PR body for the truncated local run). No tip, tape, mirror or ledger file was opened. Coverage 100.000% is LAB_STATE row 115's number (job #131, dated). Adapter throughput on a real hour, engine memory on this source, the tip's own rate of rows without `tx_index` (the exploration proxy is a cold-cache hour: 861 PumpSwap rows in 2026-08-16T12), mirror lag, and hour health are open.
