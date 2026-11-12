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

Extra rows interleave with entries and exits, so the decision-log chain (seq, hash) of later rows differs from a flag-off run; the entry and exit content does not.

## Rollback
Remove the keys (or set them false) and restart the host with the old config. A flagged checkpoint loads (extra fields ignored); its `shadow_complete` host_down spans are treated as unflagged, so every pool is refused for BLOCK_MS after them (conservative).

## Not covered / residual
- A row that was table-updated but not yet decided at the instant of a kill -9 (microseconds) is neither in the log nor shadowed (the table is past it); v1 covered it with host_down. Open question for QP.
- Positions open at a restart lose the host take-profit (resume_reopen), as in v1.
- The proof (P0-P2) on real tape has not been run.
