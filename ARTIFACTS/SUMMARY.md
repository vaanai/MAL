# Manager summary

**As-of:** 2026-09-29 ~05:00Z. `main` through [#158](https://github.com/vaanai/MAL/pull/158) (`dfb6d05`). **Paper only.** Full page: [LAB_STATE.md](../LAB_STATE.md). Current manager handoff: [docs/HANDOFF.md](../docs/HANDOFF.md).

- **North star:** profit via tape → honest simulator → signals → forward paper → gated live. Nothing is live.
- **Hosts:** Oracle `mal-core-0` (archive + training + forward paper) and OVH `mal-fast-0` (fast listeners + backfill, the focus). [HOSTS.md](../docs/HOSTS.md). `/opt/miscusi` on the fast box is the separate `vaanai/MiScusi` project; MAL work does not modify it.
- **Frozen migrate-direct cell is dead.** Formal FAIL, 2026-09-28T21:00Z one-shot: pooled 0.5 SOL n=3,622 over 5 days, flat net **−0.0906%** (3/5 days positive), pressure net **−0.1682%** (2/5), every CI lower bound < 0. Not refit. Both Oracle and fast-box scorers for it are stopped.
- **Backfill:** three walkers on `mal-fast-0` covering `[2026-09-09T12, 2026-09-19T01)` in three blocks (1,505,168 of a planned ~4.5M credits at 05:03Z). See LAB_STATE's "What is running."
- **Oracle forward-paper runner:** code `d7485d2` since restart #3 (2026-09-29T00:00:20Z), now restarting **daily at 00:00:00Z** via `mal-runner-daily-restart`. Memory growth down to ~300 MB/h post-#134 (`tx_order_entries` plateaus ~800k).
- **Current research:** EXP-011 is a candidate **pending pre-registration** (a frozen, leakage-ablated S2 entry model). Its holdout `[2026-09-09T12, 2026-09-15T12)` may not be read until the pre-reg and `tools/exp011_score.py` are merged on `main`. It is read once. Exploration B3 (#156) is not a promote.
- **Forward void:** **2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z**. Clean clock **2026-09-28T00:00:00Z**. Kill review **2026-10-05T05:00:00Z**, single read, `tools/kill_review.py`, on a snapshot.
- **Gate:** ≥100 OOS trades, ≥5 UTC days with a majority positive, 90% CI lower bound of mean SOL/trade > 0, still positive after dropping the top 3, under both flat 15% and pressure scale 1, plus Holm–Bonferroni across the 9 kill-review books. Then owner approval.
- **Open PRs:** only [#90](https://github.com/vaanai/MAL/pull/90) (keep).
- **Workflow:** manager plans, workers implement via PRs, the Claude manager merges after review ([DEC-012](../DEC/DEC-012-tool-neutral-manager-workers.md), [DEC-013](../DEC/DEC-013-claude-manager-merges.md)).
- **History:** the 2026-09-27/28/29 manager-session narrative moved to [ARTIFACTS/daily/2026-09-28-manager-session.md](daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-27-claude-handoff.md](daily/2026-09-27-claude-handoff.md).
