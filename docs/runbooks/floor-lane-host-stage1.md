# FLOOR lane host, guard stage 1 (config-gated, default off)

Code: `tools/floor_lane_host.py`. Tests: `tools/test_floor_lane_host_stage1.py`, golden in `tools/test_floor_lane_host.py`. Basis: `floor-gap-guard-v2-design.md` and `QP-RULING-1112-floor-guard-stage1.md` (the ruling wins). Nothing here says the rule makes money. NOT installed; install needs DEC-035 Am.7 and Helm, and a replay proof first (QP conditions 1, 2, 11, 12).

## Config keys (host config JSON, both absent = false = today's guard, byte-identical)

| Key | Effect when `true` |
| --- | --- |
| `status_span_no_loss` | (a) A gap span is ignored by the gap guard only if it matches a raw gap row this host captured in `_on_row` with type gap, src `writer`, prog `all`, kind `tip_gap_status_unhealthy`, reason `status_unhealthy`, data_loss true, missed_slots 0, from_slot == to_slot, silent_ms == to_ms - from_ms <= 120 000 (match on prog, kind, from_ms, to_ms; ArenaState drops `src`). Reader-problem, writer-hole, every adapter span, and a span reloaded from a checkpoint without a captured row still refuse. |
| `cheap_restart` | (b) After a restart the frozen rule is run silently (empty book, `now_ms = t_recv + 10 s`, no log, no engine, no table write) on trade rows the table applied in that call and that were not dispatched, from the last checkpoint tick - 60 s to the host_down span's final end; each Buy blocks its pool for BLOCK_MS (why `restart_shadow`). If the shadow is complete the host_down span is flagged `shadow_complete` and no longer refuses every pool. (c) Blocks are rebuilt from all `entry_refused_gap` rows with trigger_ms + BLOCK_MS > now. |

`shadow_complete` needs all of: valid checkpoint; ArenaState start <= shadow_from - 60 s; the table rebuild (`note_rebuild`) applied no row or ended <= shadow_from - 60 s; catch_up without a fatal; no new hook errors in the warm-up; no unparseable refusal row. Otherwise the span keeps the v1 meaning (every pool refused for BLOCK_MS) and the start-evidence row says why (`fallback`: `downtime_exceeds_warmup`, `rebuild_unknown`, `rebuild_covers_downtime`, `hook_errors_in_warmup`, `refusal_row_unparseable`, `shadow_error`).

## Switch-on rules for `status_span_no_loss` (QP conditions 4, 5)
The writer's span row does not say which status code caused it. Turn the flag on only after the manager's counts-only check of the writer hour-health `status_failures` codes (only `status_stale`, `status_missing`, `status_ts_missing` for every hour with a status span since S); repeat daily while on. Any other code (`decoder_blobs_mismatch`, `decoder_blobs_not_pinned`, `event_v_off`) means lane STOP, then flag off. Flag off as well whenever the writer, adapter or reader blob changes (the 11-15 tip hand-off included) until QP re-rules. No follower decoder change while on.

## Evidence rows (decision log; the bridge ignores unknown kinds, `tools/arena_live_bridge.py:926-946`, `tools/floor_arena_executor.py:232-253`)
- `entry_gap_span_ignored`: pool, trigger_ms/slot, `spans` (src, prog, kind, reason, from/to ms and slot, missed_slots, silent_ms), `then` = allow or refuse.
- `lane_restart_shadow` (once, after the warm-up, when `cheap_restart` and a checkpoint was loaded): hwm_ms, down_from/to_ms, warm_start_ms, shadow_from_ms, rebuild_to_ms, rebuild_rows, shadow_complete, fallback, n_shadow_rows/buys/pools, shadow_pools (<= 50), blocked_pools, blocked_by_why, reblocked_from_refusal_rows, refusal_rows_unparseable.
- `lane_restart_shadow_failed`: a shadow exception after that row; the span flag is cleared (fail closed).
- Refusals by a shadow block read `pool_blocked_after_restart_shadow`; by a rebuilt block `pool_blocked_after_refusal_row`.
- The start row's `binding` gains `guard: {status_span_no_loss, cheap_restart}` only when a flag is on. The lane checkpoint gains the same optional `guard` field; the schema name stays `floor_lane_host_ckpt_v1`.

Extra rows interleave with entries and exits, so the decision-log chain (seq, hash) of later rows differs from a flag-off run. With a flag on the host also makes more entries by design (refused -> entered only); the rows v1 writes for the same trigger are unchanged in content.

## Rollback
Remove the keys (or set them false) and restart the host with the old config. A flagged checkpoint loads (extra fields ignored); its `shadow_complete` host_down spans are treated as unflagged, so every pool is refused for BLOCK_MS after them (conservative).

## Not covered / residual
- A row that was table-updated but not yet decided at the instant of a kill -9 (microseconds) is neither in the log nor shadowed (the table is past it); v1 covered it with host_down. Open question for QP.
- Positions open at a restart lose the host take-profit (resume_reopen), as in v1.
- The proof (P0-P2) on real tape has not been run.

## Previous-process end mark and the deploy restart (QP BLOCKER on PR 1332)
After an in-process exception (for example a decision-log write failure on a Buy row) `run()`'s `finally` blocks pickle a pool table that already holds the row being decided, and the restart skips it, so the shadow cannot see that Buy. Therefore `LaneHost.run` writes `"ended": "exception"` (or `"end"` on a normal return) into the final lane checkpoint when a flag is on, and the cheap restart falls back to the 24 h refuse-all (`fallback: unclean_exit`) on `"exception"`. A kill (SIGKILL/SIGTERM; no handler, no `finally`) leaves a flagged checkpoint without the mark: `prev_end: "signal"`, cheap restart allowed (the pickle then predates the in-flight row, so the warm-up re-applies and shadows it). `prev_end` is in `lane_start` info and in the `lane_restart_shadow` row (`exception`, `end`, `signal`, `unknown`).

`in_doubt` (QP case iv): with `cheap_restart` on, any table save OUTSIDE the periodic one in `step()` (the finally saves of `HostV2.run`, a direct call) first writes `"ended": "in_doubt"` to the lane checkpoint (atomic, fsynced, directory fsynced); if that write fails no pickle is written. `run()`'s finally then overwrites it with `end` or `exception`. The restart falls back to the 24 h refuse-all (`unclean_exit`) on `exception` or `in_doubt`. With the flags off nothing is added and `save_table` is main's.

A checkpoint not written with `cheap_restart: true` (no `guard`, or `guard.cheap_restart` false) reads `prev_end: "unknown"`; `signal` is read only from a checkpoint whose `guard.cheap_restart` is true; the code cannot tell how that process ended, and the v1 code has the same finally-pickle path.

Numbered steps for the manager before the first start with `cheap_restart: true` from ANY checkpoint not written with `cheap_restart: true` (a v1 host, or a run with only `status_span_no_loss` on; both read `prev_end: unknown`, because only a cheap_restart run has the `in_doubt` protection):
1. The manager record (counts only) must show that the v1 host's main process was terminated by a signal (systemd `code=killed`, `status=15/TERM` or `9/KILL`), NOT an exit status of 2 (the exception path of `main`), and that run's journal has no `floor_lane_host: HostFatal|HostError|LogChainError` line and no Traceback. Without that record the first start keeps `cheap_restart` false (flags off, 24 h refuse-all) and `cheap_restart` is switched on at a later restart, which then reads a flagged checkpoint.
2. Record that statement in LAB_STATE with the stop and start instants.
3. Only then set `cheap_restart` true and restart; check the `lane_restart_shadow` row (`prev_end`, `fallback`, `shadow_complete`).
