# Claude manager handoff — 2026-09-27

Manager: the Claude session on `mal-fast-0` ([DEC-013](../../DEC/DEC-013-claude-manager-merges.md)). This is a facts note, not a new measurement; the promotion gate and existing OOS numbers in [LAB_STATE.md](../../LAB_STATE.md) are unchanged.

## PRs

Merged: [#115](https://github.com/vaanai/MAL/pull/115), [#116](https://github.com/vaanai/MAL/pull/116) (Cursor handoff, LAYA timer hold), [#117](https://github.com/vaanai/MAL/pull/117), [#118](https://github.com/vaanai/MAL/pull/118), [#119](https://github.com/vaanai/MAL/pull/119), [#120](https://github.com/vaanai/MAL/pull/120). Closed without merge (stale, per the keep/close table): [#114](https://github.com/vaanai/MAL/pull/114) (cancelled), [#5](https://github.com/vaanai/MAL/pull/5), [#8](https://github.com/vaanai/MAL/pull/8), [#10](https://github.com/vaanai/MAL/pull/10), [#15](https://github.com/vaanai/MAL/pull/15), [#22](https://github.com/vaanai/MAL/pull/22). Still open: [#90](https://github.com/vaanai/MAL/pull/90) (keep).

## #117 — mal-fast-create reconnect crash

`mal-fast-create` was crashing about every 4h (11:45, 15:35, 19:35 UTC) on an uncaught `websockets` `InvalidStatusCode` (HTTP 502) during the PumpPortal reconnect, each time leaving a 9–12 s create gap. Fix deployed to `/var/lib/mal/eng/fast_create_listener.py` on `mal-fast-0` (backup `.bak-20260927-pre117`), unit restarted 22:55Z.

## Oracle forward-paper memory leak

The forward-paper runner grew from the 06:58Z start to about 5.3 GB RSS plus about 2 GB swap by ~21:50Z, pinned at the cgroup `memory.high` 5G ceiling, 57,889 high events, and stuck in D state. That produced about 85 `runner_lag`/`runner_silent` healthcheck failures in roughly 24h, peaking near 50 s. Helm restarted the service 22:44Z (4.9 GB → 218 MB RSS, 2 GB → 200 MB swap). The runner's own checkout, `/var/lib/mal/paper/forward-paper/src`, was then updated `264d1b9` → `bc7a0c6` (includes [#120](https://github.com/vaanai/MAL/pull/120), plus reviewed decision-neutral commits: `fee_sensitivity` reporting, LAYA yield-on-lag) and restarted 22:48Z; RSS was 139 MB at 22:49Z. A reviewer pass found no change to decisions, fills, fees, or booked PnL, and no resume/schema break.

[#120](https://github.com/vaanai/MAL/pull/120) bounds latency lists, cooldown maps, and orphan early prints. It probably does not remove the dominant growth — the leading suspect is per-mint history retained forever, some of it read cross-mint by `funding_graph`/LAYA features. A replay-profiling investigation to measure the dominant container is in progress. Memory is being tracked from the 139 MB baseline.

**Clean-clock consequence:** the clean week (from 2026-09-28T00:00:00Z) starts on runner code `bc7a0c6`. The pre-restart lag breaches above were before the clean clock and do not touch the void window already recorded in [CLAUDE.md](../../CLAUDE.md).

## Oracle nightly

`mal-laya-v0.timer` and `mal-attention-daily.timer` stay disabled until 2026-10-05 — no change from the existing LAB_STATE entry. No 04:15 LAYA run tonight.

## Claude schedules ([#119](https://github.com/vaanai/MAL/pull/119), `ops/claude-schedules/`)

`mal-daily-review` (05:00 UTC) and `mal-oos-check` (one-shot, 2026-09-28 21:00Z) are installed as systemd `--user` timers for user `claude` on `mal-fast-0`: headless `claude -p`, no shell tools, reports under `/home/claude/reports/`. Plan is to enable them after Cursor's 2026-09-28 05:00Z run, then the owner cancels the Cursor timers.

## Access

Claude on Oracle (`mal-core-0`) is the read-only account over `ssh mal-core-0`: may restart/reload/try-restart ubuntu's `mal-*` user units, nothing else; cannot write files as `ubuntu` — code deploys to Oracle go through the owner or Helm. On `mal-fast-0`, Claude has full sudo minus the standing fences (ufw/iptables/nft, sshd, cloudflared, Cloudflare, `/opt/miscusi`).

## Fast backfill

At about 22 sealed hours: about 306k of the +2,000,000 credit cap used, about 13.5k credits per backfilled hour, about 21–22 minutes of wall time per hour. At that rate the 2M cap binds around 148 hours, near 2026-09-29 18:00–19:00Z — before the 240-hour target. Extending to 240 hours would cost about 1.25M more credits; that extension is a decision pending tomorrow's OOS read.

## OOS snapshot (~2026-09-27T21:40Z)

Both books are still one UTC day each — **under-sampled, no verdict**:

- Fast box, 0.5 SOL: n ≈ 868–920, all on 2026-09-21, 0/1 days positive.
- Oracle, 0.5 SOL: n = 142 (hours 2026-09-22T06–09), flat net −0.476%, flat CI lower −3.149%, 0/1 days positive.

Neither clears the promotion gate. This is in addition to, not a replacement for, the 2026-09-27T13:48Z pooled snapshot already in LAB_STATE.

## Next work

1. One-shot OOS read, 2026-09-28 21:00Z.
2. Decide the backfill credit extension.
3. Forward-paper dominant-memory fix with a replay equivalence proof.
4. Score the clean week from 2026-09-28T00:00Z; kill review 2026-10-05T05:00Z.
5. Deploy provenance on `mal-fast-0` — `/home/ubuntu/mal-oos` is not a git checkout.
