# Cursor timers (exact, as of 2026-09-27T17:20Z)

Both live in the Cursor coordinator conversation, not on either host. Each one wakes the coordinator. The coordinator then delegates a worker, and the worker writes to the store path shown. On a Claude setup, replace the store path with the repo path `ARTIFACTS/daily/` and the worker with a Claude subagent. Recreate them on `mal-fast-0` as in [docs/MIGRATION-TO-CLAUDE.md](../../docs/MIGRATION-TO-CLAUDE.md). The owner cancels the Cursor timers only after those Claude jobs are verified.

## mal-daily-scoreboard-review

- Type: recurring cron
- Schedule: `0 5 * * *` (05:00 UTC daily)
- Opened 2026-09-25T08:41:20Z. Expires 2026-10-02T08:41:20Z (Cursor subscriptions expire after about 7 days).
- Writes: `ARTIFACTS/daily/<date>.md`, then the coordinator updates `docs/ops/notes.md`.
- Prompt (repo-path form):

> Daily MAL check: the LAYA v0 retrain/scoreboard timer runs at 04:15 UTC on mal-core. Delegate a short read-only worker (composer-2.5, fast off) to pull the latest daily scoreboard, tape health stats (trades/min, % creates covered, lag, disk), and wallet leaderboard summary into ARTIFACTS/daily/<date>.md, then decide next steps and update docs/ops/notes.md. Message the user only if a signal meets the promotion criterion, something breaks, or a decision is needed.

- What the worker actually checked on 2026-09-27:
  - tape trades/min, create→bonding %, chain→recv p50/p99, disk free
  - unit states on mal-core-0
  - forward-paper `runner-status.json` lag_ms vs the 5000 ms cap
  - the per-book forward shadow scoreboard, excluding the void window
  - the LAYA and attention-daily outcomes
  - the leaderboard
  It did not yet cover mal-fast-0; a Claude version should add it.

## migrate-direct-oos-5day

- Type: one-shot
- Opened 2026-09-27T13:50:06Z. Delay 112,000 s, so it fires about 2026-09-28T20:57Z.
- Writes: reads `ARTIFACTS/lab/migrate-direct-oos.md`. Kills or keeps the cell in `docs/ops/notes.md` and messages the owner.
- Prompt (repo-path form):

> Five out-of-sample days for the frozen migrate-direct cell (spec ARTIFACTS/lab/migrate-direct-prereg.md, frozen 2026-09-27T13:06:36Z) were expected around 2026-09-28 20:00Z (Oracle covers 22 Sep, the fast box walks 21 Sep backward). Delegate one short composer-2.5 worker to read ARTIFACTS/lab/migrate-direct-oos.md, confirm with the host that the table is current, and report per fail model: n, distinct days, days positive, net mean, bootstrap 90% CI lower bound, ex-top-3, fill rate. No parameter changes. Then decide: if it passes the promotion rule under both fail models on out-of-sample days, message the owner proposing a tiny live calibration (capped hot wallet on the owner's machine, never on a server) and wait for approval. If it clearly fails, kill the cell in docs/ops/notes.md and message the owner briefly. If it is still under-sampled, let the backfill continue and set another check.

## Not Cursor timers (already on the hosts)

- mal-core-0: `mal-laya-v0.timer` 04:15 UTC is **disabled until 2026-10-05** (runner lag still spiked to about 12.5 s). `mal-attention-daily.timer` 04:45 UTC is **disabled until 2026-10-05**; the `mal-attention` poller stays up. `mal-migrate-direct-oos.timer` 01:20 UTC is **under test; may move to mal-fast-0**. Healthcheck every 5 min.
- The source of truth is the repo units under `scripts/mal-core/`.
