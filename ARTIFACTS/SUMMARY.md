# Manager summary

**As-of:** 2026-09-30 ~07:30Z. `main` through [#164](https://github.com/vaanai/MAL/pull/164) (`b9ddb3c`). **Paper only.** Full page: [LAB_STATE.md](../LAB_STATE.md). Current manager handoff: [docs/HANDOFF.md](../docs/HANDOFF.md).

- **North star:** profit via tape → honest simulator → signals → forward paper → gated live. Nothing is live.
- **Hosts:** Oracle `mal-core-0` (archive + training + forward paper) and OVH `mal-fast-0` (fast listeners + backfill, the focus). [HOSTS.md](../docs/HOSTS.md). `/opt/miscusi` on the fast box is the separate `vaanai/MiScusi` project; MAL work does not modify it.
- **Frozen migrate-direct cell is dead.** Formal FAIL, 2026-09-28T21:00Z one-shot: pooled 0.5 SOL n=3,622 over 5 days, flat net **−0.0906%** (3/5 days positive), pressure net **−0.1682%** (2/5), every CI lower bound < 0. Not refit. Both Oracle and fast-box scorers for it are stopped.
- **Backfill:** three walkers on `mal-fast-0` covering `[2026-09-09T12, 2026-09-19T01)` in three blocks (1,505,168 of a planned ~4.5M credits at 05:03Z). See LAB_STATE's "What is running."
- **Oracle forward-paper runner:** code `d7485d2` since restart #3 (2026-09-29T00:00:20Z), now restarting **daily at 00:00:00Z** via `mal-runner-daily-restart`. Memory growth down to ~300 MB/h post-#134 (`tx_order_entries` plateaus ~800k).
- **Current research:** EXP-011 is **pre-registered** (#164; frozen entry model md5 `eb218934…`, threshold 0.8012). Its holdout `[2026-09-09T12, 2026-09-15T12)` is read **once** by `tools/exp011_score.py` after walker C completes (~2026-09-30T19–20Z). **Priority from 2026-09-30:** build the MAL Console ([docs/console-plan.md](../docs/console-plan.md)).
- **Forward void:** **2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z**. Clean clock **2026-09-28T00:00:00Z**. Kill review **2026-10-05T05:00:00Z**, single read, `tools/kill_review.py`, on a snapshot.
- **Gate:** ≥100 OOS trades, ≥5 UTC days with a majority positive, 90% CI lower bound of mean SOL/trade > 0, still positive after dropping the top 3, under both flat 15% and pressure scale 1, plus Holm–Bonferroni across the 9 kill-review books. Then owner approval.
- **Open PRs:** only [#90](https://github.com/vaanai/MAL/pull/90) (keep).
- **Workflow:** manager plans, workers implement via PRs, the Claude manager merges after review ([DEC-012](../DEC/DEC-012-tool-neutral-manager-workers.md), [DEC-013](../DEC/DEC-013-claude-manager-merges.md)).
- **History:** the 2026-09-27/28/29 manager-session narrative moved to [ARTIFACTS/daily/2026-09-28-manager-session.md](daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-27-claude-handoff.md](daily/2026-09-27-claude-handoff.md).
