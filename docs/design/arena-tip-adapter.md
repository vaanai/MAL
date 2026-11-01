# DESIGN: arena tip adapter (QP-RULING-1101-hv4-reads section 4.1 (B))

Status: code PR, nothing deployed, nothing run on real data. No book has cleared the promotion gate. This note says what the adapter does, how its output reaches an engine,
how to measure the feed delay, and what records QP items 1, 5 and 6 still need. It measures nothing about any book.

## 1. What was built

| Piece | File |
| --- | --- |
| Adapter, source (status gate), feed-delay helper | `tools/arena_tip_adapter.py` |
| Tests (mapping, gaps, skips, refusals, real-follower hour rollover, mutations) | `tools/test_arena_tip_adapter.py` |
| md5 decision-equivalence harness + job script + tests | `scripts/research/arena_tip_equiv.py`, `scripts/research/arena-tip-equiv.sh`, `tools/test_arena_tip_equiv.py` |

The reader is `tools/r4b_tip_reader.TipReader`, imported, not changed (checkpoint-then-drain, hour floor 2026-10-16T01 and start - 53 h, archive and path refusals, decoder
pin from status.json failing closed). The adapter adds no file access of its own. **Question for QP:** that module lives under the `r4b_` name, and `arena_paper.py` says no
arena process may use `tools/r4b_*.py` until EXP-027's read (DEC-031 section 3.1). The reader holds no model and no feature (it is a blob-pinned file reader), but the ruling for item 1
should say so in words, or ask for a rename/factor PR with its own proof (a change to the r4b files needs one).

## 2. Mapping and its rules

- trade: `prog` by venue; the walker row's own keys minus the tape's `TRADE_DROP`, `source` and the tip-only singular `virtual_quote_reserve`. Event V (`virtual_quote_reserves`) stays.
- create / complete: as the tape's. A walker `migration` row becomes the tape's `pool_create` (pool, mint, quote_mint): the live tape's `pool_create` comes from CreatePool, which the walker decoder does not emit.
- Schema delta against `arena_tape_v1`: trade, create, complete, pool_create rows carry `tx_index` and `block_time` (the schema says "there is no block_time or tx_index"; this is the point of the source); gap rows carry `src: "tip"`.
- Order: slot order; every trade has `tx_index`, so `ArenaEngine.order_key` is `(slot, 0, tx_index, event_index, signature)` on every print (arena_paper.py:912-917). A row with no `tx_index` is dropped and marked; see the finding below.
- Gaps: every `gaps.jsonl` row and the reader's `tip_bad_line` / `hour_file_missing` become one row `type gap, prog "all", data_loss true, kind tip_gap_<reason>, from_slot, to_slot, missed_slots, [from_ms, to_ms]`, emitted before the first row of slot >= `from_slot`, not before the slots below it are released. `to_ms` is clamped between the last row emitted and the next row.
- Skipped slots: counted, never a row, never a gap.
- Not emitted: `hb`, `start`, `stop`, `pool_info`.

**Finding (needs a number before use).** PumpSwap rows that come back through `resolve_unresolved` (an account lookup for a pool whose CreatePool was not seen) carry no `tx_index`
(`tools/pump_history_backfill.py`, `rows_from_block`: the unresolved record is appended before `tx_index` is set). Passed on, they would put the receive-order key form into a pool that otherwise has the
tx_index form, which `order_key` forbids. The adapter drops them and writes a `tip_gap_bad_row_tx_index` data_loss gap. How often that happens on the real tape is **not measured**; the
pre-clock smoke (item 6) must count it. If it is more than rare, the answer is a different fix (resolve the position in the block), not a threshold.

**Open: hour health.** `arena_tape_schema.md` calls an hour with fewer than 60 `hb` rows per program bad. The adapter writes no `hb` rows. Whether `tools/arena_hour_health.py` (pinned 227f92be) marks every tip hour bad was **not checked**; the Am.6 thresholds for this source (item 5) need a rule that does not rest on `hb`.

## 3. Transport: where the engine runs

The adapter is streaming and holds almost no state (a pending-gap list and two integers). The cost is the engine's pool history, which is **not measured** here.

| Option | How | For | Against |
| --- | --- | --- | --- |
| A. Engine on fast-0, adapter in-process | `ArenaTipSource.poll()` feeds `ArenaEngine.on_row` in the same process | No tape copy, no mirror lag, no new seal beyond the tip entry; the follower's files never leave fast-0 | Shares the 2 GB-class headroom with the tip follower and listeners (one heavy job, at most 2 workers, `systemctl show user-1002.slice -p MemoryCurrent` first; the 2026-09-29 OOM); the r4-b DRY host is already there |
| B. Adapter output mirrored to research-0 (pattern of `tools/tape_mirror.py`, #1147) | Adapter writes `tape-<hour>.jsonl` on fast-0; research-0 dials fast-0 and pulls it | Engine on research-0 (121 GB), next to the other books | `tape_mirror` serves research-0's tape to fast-0; this is the reverse direction and a new source allowlist (a `tape_mirror` change, own proof). The output is a derived copy of the sealed tip tape: it needs its own SEALS-BRIEF entry. Mirror lag enters the latency budget (measure it from `.mirror/lag.jsonl`, never from rows) |

Recommendation: do the 24 h pre-clock smoke (item 6) as option A with `ArenaEngine` for the first books (HV6, HV7: few pools, no 24 h holds), report anon RSS and the follower's `lag_slots` /
`backlog_jumps` before, during and after, and decide B only if the memory rule forbids A. B is not worth a second seal entry before that number exists.

## 4. Feed delay (QP item 4)

`StackConfig.feed_delay_slots` is required and HV4's 7 is for the public feed. For the tip, `t_recv_ms` is the arrival of the getBlock response, so the delay is `t_recv_ms - blockTime x 1000`.
**Correction to the ruling's wording:** `checkpoint.json` holds `last_done`, `last_t_recv_ms`, `gaps`, `skipped` and `writing`; it has no block lag. The lag is in `status.json`: `lag_ms` (last block), `block_lag_ms_p50`, `block_lag_ms_p90`, `lag_slots`. Neither file is under `/var/lib/mal/sealed`; agents may read both.

Method (`read_lag_sample`, `feed_delay_slots`): sample both files every 15 s (the heartbeat) for at least 24 h; the per-book value is `ceil(p90 of block_lag_ms_p90 / (sps x 1000))`, the p90 rule of `tools/arena_feed_delay.py` (the manager's ruling there: p90, not the median). `sps` is the slots-per-second of the same window
(least squares of block time on slot, as in `arena_feed_delay`, or the engine `SlotClock`); `block_time` has one-second resolution, so the lag carries up to +1 s of quantisation, in the conservative direction. Pin the result per book as `--feed-delay-slots` with its date. As a scale check only, LAB_STATE row 115 (10-10) quotes p50 1,784 ms and p90 2,149 ms; at 0.4 s per slot that would be a d of 6. That figure is three weeks old and must be re-measured.

## 5. Records list for QP items 1, 5 and 6

**Item 1: QP ruling and SEALS-BRIEF entry** (r4-b pattern, QP-RULING-1031-r4b-history 2.1 conditions 1-6)
1. QP ruling naming the pinned arena adapter/engine host (install sha) as a tip-tape reader: files `trades-`, `creates-`, `migrations-`, `skipped-slots-<hour>.jsonl` and `gaps.jsonl` only; hours >= max(2026-10-16T01, start - 53 h); read-only; the code refusals; decoder pin from status.json failing closed; nice 19 / ionice idle (`lower_io_priority`, refused at start when it fails); the follower, its unit and tree untouched. State that the DEC-031 3.1 question of section 1 is answered.
2. SEALS-BRIEF entry (manager) and the matching AGENT-BRIEF-COMMON section 2 line: only the follower, its archive job, the pinned r4-b DRY host and this host read the tip files; agents read `/var/lib/mal/fast-tip-follower/{status,checkpoint}.json`; any output file of the adapter (option B) is sealed as a derived copy; each book's decision log and ledger sealed as its registration says.
3. HOLDOUT_LEDGER changelog line and a Forward walk 2 status-cell note ("never a back door"; walk-2-hour information, DEC-030 section 2 R1; EXP-024 Look-2 seal to 2026-11-14 and D2/D3 apply).
4. Read ACL for the job user on that one dir if needed, recorded in LAB_STATE row 115 with `date -u`; LAB_STATE row for the host's job (start, sha, source config).
5. P-C0 re-read by the manager: status.json lag, p50/p90, gaps, backlog_jumps, decoder pins, `trade_event_v`, unit start time.

**Item 5: DEC-031 amendment for books on this source**
1. Section 1.4 gap kinds of the source: `tip_gap_backlog_jump`, `_unfetchable`, `_skip_contradicted`, `_skip_contradicted_refetch`, `_unresolved_dropped`, `_tip_bad_line`, `_hour_file_missing`, `_unparsable`, and `tip_gap_bad_row_{tx_index,fields,venue,slot,kind,migration_type}`, all `prog "all"`. Skipped slots are not loss.
2. Am.6 hour thresholds for the source (see "Open: hour health"): a rule that does not rest on `hb` rows; the 10% hours cap and 5% trades cap are unchanged text.
3. The source pinned in each registration: adapter sha, reader sha, the three EXP027_DECODER_REF blobs, `LAG_MS`-independent `feed_delay_slots` with its measurement date, and the engine restart rule (an adapter-host restart restarts the engine, so open positions are `engine_restart`).
4. Disclosure line for every read: walk-2-hour information; the tip's `tx_index` form of order keys; the dropped-row count.

**Item 6: per book**
1. New `bot_id`, P0, H and clock (N + 1 each); the clock must not fall in a kill-review week (check LAB_STATE "Now").
2. Pre-clock smoke of at least 24 h on the new source that meets section 4.4 of the ruling: per traded program, lambda = data_loss gap rows per second over the 24 closed hours before the clock, `1 - exp(-lambda x T_max) <= 0.025`. Tip gaps are `prog "all"`, so lambda is one number for both programs. A pinned counter reads gap rows only. Outcome-blind; written as a records line before the clock.
3. The smoke also reports: dropped-row count by code, anon RSS peak, follower `lag_slots` / `backlog_jumps` before, during and after, rows per minute, the feed-delay measurement of section 4.
4. md5 equivalence at the PR head (`scripts/research/arena-tip-equiv.sh`, exploration hours): decisions and trades md5 equal, config md5 `5579235fd66e7a69bf56a09c8ee5d7f7`.

## 6. Not measured, not proved

The md5 equivalence has **not** been run on exploration data (only on a synthetic stream in the tests). No real tip, tape, mirror or ledger file was opened. Coverage 100.000% is LAB_STATE row 115's number (job #131, dated). Adapter throughput on a real hour, engine memory on this source, the share of dropped no-`tx_index` rows, and hour health are all open.
