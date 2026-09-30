# Manager session — 2026-09-28 evening through 2026-09-29 (early)

Manager: the Claude session on `mal-fast-0` ([DEC-013](../../DEC/DEC-013-claude-manager-merges.md)). Continues [ARTIFACTS/daily/2026-09-28.md](2026-09-28.md) (the 2026-09-28 daytime note). This is a facts note, historical narrative moved out of [LAB_STATE.md](../../LAB_STATE.md) to keep that page current-only. Current numbers live in LAB_STATE, CLAUDE.md, and docs/HOSTS.md — this page is not the place to re-derive them.

## Forward-paper memory leak, full chain

1. **2026-09-27 22:44Z–22:48Z:** Helm restart after the runner hit ~5.3 GB RSS / ~2 GB swap, pinned at the old 5G `memory.high`. Checkout fast-forwarded to `bc7a0c6`. Clean week starts here.
2. **Restart #1, 2026-09-28T03:56:52Z → `d3015ba`** ([#123](https://github.com/vaanai/MAL/pull/123)): GC thresholds + freeze-at-start + `_Wallet` compaction + live mem-census.
3. **Restart #2, 2026-09-28T14:25:02Z → `f687fad`** ([#124](https://github.com/vaanai/MAL/pull/124) periodic `gc.freeze()`; [#125](https://github.com/vaanai/MAL/pull/125) pre-boot dead-mint scan + createless timeout bounding `self.early`).
4. **18:37Z census** (docs/HANDOFF.md's original content, since superseded): `#125` worked — `early_prints_buffered` stayed flat instead of running to ~426k in 2h. But RSS still grew **~441 MB/h**, leading suspect `wallets` (142,732 wallets, 452,659 per-mint entries, 308,951 `holds` entries, all unbounded). Projected to hit the `MemoryHigh` 10G ceiling around 2026-09-29 12:00–13:00Z — would not have lasted to the kill review.
5. **[#129](https://github.com/vaanai/MAL/pull/129) (`wallets-bound`)**: exact memory cuts to the `wallets` container, decision-neutral (md5 replay proof against the staged profile slice). Brought growth down from ~441 MB/h.
6. **[#134](https://github.com/vaanai/MAL/pull/134) (`txorder-prune`)**: found and pruned a second unbounded per-print leak in `TxOrder` bookkeeping, rate-limited the prune scan to every 10 minutes ([#134](https://github.com/vaanai/MAL/pull/134) review follow-up). Net result after both fixes: **RSS growth fell from ~430 MB/h to ~300 MB/h**; the `tx_order_entries` mem-census field now plateaus around **800k**.
7. **Restart #3, 2026-09-29T00:00:20Z → `d7485d2`**, the first run of the new daily-restart timer ([#137](https://github.com/vaanai/MAL/pull/137)); see "Restart #3" below.

Every restart above resets in-memory `WalletState` (`bots`/`snipers`/`leaders`/`creators`) and `by_creator` cross-mint history to empty; the funding graph (external `funding-*.jsonl`) is unaffected. The kill review must account for every restart boundary, not just the clean-clock start.

## Frozen migrate-direct cell — killed

Interim read, 2026-09-28 evening (fast OOS 0.5 SOL, snapshot 17:18Z): n = 3,005, flat net mean −0.27%, pressure net mean −0.24%, both 90% CI lower bounds < 0. The owner's reviewer estimated the remaining trades would need about **+1.17%** average to pass. The fast OOS scorer (`mal-fast-oos-score`) was stopped; no more Helius credits went to this cell.

**Formal verdict, 2026-09-28 night, from the 21:00Z one-shot** (`/home/claude/reports/oos-check/2026-09-28.md`, re-derived from raw per-host data): pooled 0.5 SOL, n = 3,622 over 5 UTC days — flat net **−0.0906%** (3/5 days positive), pressure net **−0.1682%** (2/5). Every per-host 90% CI lower bound is < 0. Fast ex-top-3 is **−4.298 SOL** (flat) and **−4.354 SOL** (pressure). The 0.05 SOL size is worse. **The cell is dead. It is not refit.**

Backward-walker credits stop going to migrate-direct entirely. Walker 1 (`mal-fast-backfill`) was re-floored at **2026-09-15T12** (the lower bound of the EXP-009 block) via drop-in `range.conf` (`MAL_FAST_BACKFILL_HOURS=156`, cap raised 2.0M → 2.3M), restarted 2026-09-28T20:11Z at 1,060,800 credits used, to finish that block instead. A second (then third) walker followed — see "Backfill" in LAB_STATE and docs/HOSTS.md for the current state.

## EXP-009 scorer window breach

The fast OOS scorer, running with no hour floor, kept scoring newly sealed backward hours past EXP-009's declared 2026-09-19T01:00:00Z cut, and read hours **2026-09-18T23** and **2026-09-19T00** — inside EXP-009's holdout window. This is disclosed in EXP-009 Amendment 3, which also corrects that file's earlier "no separate exclusion step is required" non-overlap claim (it was wrong: the frozen scorer's own window had no floor). The scorer was stopped at **2026-09-28T19:00:50Z**. Both hours are excluded from EXP-009 scoring and moved to the exploration pool. This is what motivated [docs/HOLDOUT_LEDGER.md](../../docs/HOLDOUT_LEDGER.md) and [DEC-014](../../DEC/DEC-014-holdout-ledger-and-multiplicity.md) (one owner per historical block, plus the Holm–Bonferroni multiplicity correction for the 9-book kill review).

## Kill-review tooling and rules landed this session

- `tools/kill_review.py` ([#140](https://github.com/vaanai/MAL/pull/140), [#144](https://github.com/vaanai/MAL/pull/144), [#149](https://github.com/vaanai/MAL/pull/149)): the designated 2026-10-05T05:00:00Z scorer. Runs once, at or after that instant, on a snapshot — never on a live, growing `positions.jsonl`.
- `tools/forward_paper_pressure_stamp.py` ([#144](https://github.com/vaanai/MAL/pull/144), [#149](https://github.com/vaanai/MAL/pull/149)): prices the counterfactual pressure-fail fills. If it can't cover a book (missing `pressure_scale_1_pnl_lamports`, a `pressure_error`, an unresolved restart-orphan, or a `settle_failed` row), that book is NOT_DECIDABLE — the pressure leg is never relaxed (DEC-014 Amendment 2).
- **DEC-014 Amendment 3** (pressure-leg start date): the pressure leg is scored over full UTC days beginning at or after the first 00:00:00Z runner restart on code ≥ `d7485d2` — i.e. **2026-09-29T00:00:00Z**. The flat leg still counts from the 2026-09-28T00:00:00Z clean clock. Each leg must clear the gate on its own; a leg with fewer than 5 eligible days is NOT_DECIDABLE, not a pass. [#145](https://github.com/vaanai/MAL/pull/145) logs the fill a flat-fail miss discarded, for audit.
- `tools/forward_paper_settle_orphans.py` ([#138](https://github.com/vaanai/MAL/pull/138), plus the ladder-branch/per-orphan-isolation fix in [#141](https://github.com/vaanai/MAL/pull/141)): restarts drop in-memory open positions — 655 of 86,464 opens across all books since the forward-paper log began (#138's read-only count) — and these are settled offline. This does not fix in-process forgetting; the runner's own warm start on restart is a separate, still-open problem.

## Exploration sequence (exploration pool only — none of this is a promote)

- **Exits** ([#139](https://github.com/vaanai/MAL/pull/139), concentration check [#151](https://github.com/vaanai/MAL/pull/151)): trailing stops led pooled results, but `trail_30_act20` pressure total was +7.2 SOL against ex-top-3 **−8.0 SOL** (one +16.6 SOL moonshot, falling per-day means). EXP-010 was never pre-registered; its reserved block `[2026-09-09T12, 2026-09-15T12)` was released unread.
- **Fee tiers** ([#142](https://github.com/vaanai/MAL/pull/142)): a dead end. 98.6% of fills at the migrate trigger already pay the top tier; waiting for cheaper tiers costs more gross than it saves.
- **Entry model, three rounds** ([#146](https://github.com/vaanai/MAL/pull/146) 3 days, [#152](https://github.com/vaanai/MAL/pull/152) 5.7 days / 3 of 12 folds robust, [#156](https://github.com/vaanai/MAL/pull/156) 9 days / B3): the top-5 features were stable across every held-out day from the first round on (pre-migration price return, nearby buy SOL, mcap at T, time to migrate, same-slot buys) — signal, but the first two rounds were not an edge on ex-top-3. Round B3 (9 held-out days), after a leakage ablation dropping `same_slot_buys`/`nearby_buy_sol`, gives flat **+6.22%** (CI lo +3.85%), pressure **+3.59%** (CI lo +2.06%), 9/9 days positive, ex-top-3 +24.97/+14.17 SOL. The fast-box slice alone is the weakest cut, +1.24% pressure after ablation. **This is exploration on the exploration pool, not a confirmation read** — see EXP-011 below for the pre-registered follow-up.
- **Lesson carried forward:** the typical migrate entry loses; exits only harvest rare tails. The lever is entry selection. Always check ex-top-3 before calling an exploration cell a candidate.

## Holdout ledger corrections

- [#147](https://github.com/vaanai/MAL/pull/147) reserved the already-seen Oracle live tape (2026-09-25T07→09-28T00) for EXP-010. That broke ledger rule 4 (no reserving already-read data), and [#148](https://github.com/vaanai/MAL/pull/148) reverted it same day: the block returned to the exploration pool.
- [#150](https://github.com/vaanai/MAL/pull/150) fixed EXP-009's block at `[2026-09-15T12, 2026-09-19T01)` — about 2.5 eligible days, which is why EXP-009 is scored as a **screen** (a pass earns a forward trial, never a promote), not a confirmation test.
- [#151](https://github.com/vaanai/MAL/pull/151) (exits concentration check) also released EXP-010's reserved block back to the pool once the exits lane was killed on ex-top-3.
- [#157](https://github.com/vaanai/MAL/pull/157) reserved `[2026-09-09T12, 2026-09-15T12)` for the next confirmation test (walked by walkers B + C), ahead of walker B sealing it — this is the block EXP-011 reads.

Current ledger state: [docs/HOLDOUT_LEDGER.md](../../docs/HOLDOUT_LEDGER.md) (owned by EXP-011, not edited by this PR).

## Restart #3 (mid-week, daily timer's first run)

**2026-09-29T00:00:20Z**, by the daily-restart timer ([#137](https://github.com/vaanai/MAL/pull/137)). Code `d7485d2` (Oracle `src` was fast-forwarded by Helm from `28dfa6a` at about 2026-09-28T20:08Z; rollback target `28dfa6a`). Pre: RSS 4,216,524 KB, lag 180 ms. Post: PID 109728, RSS 217,748 KB, lag 9 ms; the mem-census now carries `tx_order_entries`. Decision-neutral per the md5 proofs in [#134](https://github.com/vaanai/MAL/pull/134)/[#145](https://github.com/vaanai/MAL/pull/145). Resets in-memory `WalletState`/`by_creator`; open positions settled offline ([#141](https://github.com/vaanai/MAL/pull/141)/[#143](https://github.com/vaanai/MAL/pull/143)). This is the restart that starts the pressure-leg clock (DEC-014 Amendment 3). From now on the runner restarts daily at 00:00:00Z, logged to `/home/claude/reports/runner-restarts.jsonl` on `mal-fast-0` ([#155](https://github.com/vaanai/MAL/pull/155)).

## Open asks to Helm made this session

1. Fast-forward Oracle `src` to `d7485d2`. Done.
2. Export the Oracle in-sample backfill hours 2026-09-22T00→09-25T07 (sealed hour files only, never `helius.env`) to a path the `claude` account can read. Done — `/home/claude/data/oracle-insample-2026-09-22_25`, sha256-verified.
3. Disable `mal-migrate-direct-oos.timer` on Oracle — it kept scoring the dead cell at 01:20Z and competed for CPU with the runner. Done; Claude's Oracle account can restart units but not disable them.

## mal-fast-0 memory incidents (2026-09-29)

- **~02:15Z, OOM:** caused by the manager running two 3-worker replay jobs at once (~3–4 GB per worker). It killed `cloudflared` and Remote Control; Helm rebooted the box at ~03:07Z.
- **~04:27Z, hang:** caused by a `MemoryHigh` throttle on the `claude` slice; Helm rebooted again.
- **New limits, set after the second incident:** `user-1002.slice` (claude) `MemoryMax` 15G with no `MemoryHigh`; `claude-remote` `MemoryMin` 1G; `user-1000.slice` (ubuntu/backfills) `MemoryMax` 8G; ssh/docker OOM-protected; heavy claude jobs do not auto-restart.
- **Rule going forward:** one heavy replay at a time, at most 2 workers, stream rows to disk, and check `systemctl show user-1002.slice -p MemoryCurrent` before starting a new one. This is now in CLAUDE.md's Operating notes.

## Pointers

- Current state: [LAB_STATE.md](../../LAB_STATE.md)
- Memory investigation detail: [ARTIFACTS/lab/forward-paper-memory-2026-09-27.md](../lab/forward-paper-memory-2026-09-27.md)
- Exploration lab notes: [ARTIFACTS/lab/exploration-entry-model-2026-09-28.md](../lab/exploration-entry-model-2026-09-28.md), [ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.md](../lab/exploration-entry-model-b2-2026-09-28.md), [ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](../lab/exploration-entry-model-b3-2026-09-28.md)
- Ledger: [docs/HOLDOUT_LEDGER.md](../../docs/HOLDOUT_LEDGER.md)
- 2026-09-28 daytime note: [ARTIFACTS/daily/2026-09-28.md](2026-09-28.md)
