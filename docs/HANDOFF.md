# Manager handoff 2026-09-28

The owner is moving to a new manager session. This page plus [LAB_STATE.md](../LAB_STATE.md), [CONSTITUTION.md](../CONSTITUTION.md), [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md), [DEC/](../DEC/), and [docs/HOSTS.md](HOSTS.md) should be sufficient for a clean pickup. This file is meant to be short-lived — remove the [CLAUDE.md](../CLAUDE.md) reload-order link to it once it is stale.

## Pending, time-boxed (UTC)

**(a) ~18:30Z — Oracle mem-census read.** Read `/var/lib/mal/paper/forward-paper/mem-census.jsonl` since restart #2 (2026-09-28T14:25:02Z, `f687fad`, #124+#125):

- RSS growth rate (MB/h).
- `early_prints_buffered` (should now be flat — #125's fix).
- `dead_mints` / `dead_prints_dropped` (should be climbing steadily, not flat).
- `wallets` entry counts (the largest remaining unbounded container — see [ARTIFACTS/lab/forward-paper-memory-2026-09-27.md](../ARTIFACTS/lab/forward-paper-memory-2026-09-27.md)).
- `gc` `max_pause_ms` and `freeze_count` (the #123/#124 fix).

Count `runner_lag` healthcheck failures in `/var/lib/mal/logs/health.jsonl` since 14:25Z. Project when RSS reaches the `MemoryHigh` 10G cgroup ceiling. **If the projection does not last to the 2026-10-05T05:00:00Z kill review**, build a decision-neutral `wallets` bound with an md5 replay-equivalence proof, the same pattern as [#123](https://github.com/vaanai/MAL/pull/123)/[#125](https://github.com/vaanai/MAL/pull/125).

**(b) 21:00Z — one-shot OOS read.** The systemd one-shot `mal-oos-check` (user `claude` on `mal-fast-0`) writes `/home/claude/reports/oos-check/2026-09-28.md`, first line `VERDICT`.

**(c) After reading (b) — fast-backfill credit-extension decision.** The +2M cap binds around 2026-09-29 18:00–19:00Z (~148 of a 240-hour target; the full 240 h needs ~1.25M more credits, out of the owner's ~20M autoscale headroom). Extend only if the book is under-sampled but not clearly dead. If it clearly fails under **both** fail models (flat 15% and pressure scale 1), recommend killing the frozen migrate cell instead of extending. **Never refit the frozen cell.** `quant-proof` reviews any edge claim before it goes in a PR or note.

**(d) 05:00Z daily review — already automatic.** Runs unattended → `/home/claude/reports/daily-review/<date>.md` and `INDEX.md`.

## Update 2026-09-28 18:37Z — (a) done, action needed

Measured since restart #2 (14:25:02Z → 18:37Z, 4.17 h, 51 census rows):

- `early_prints_buffered` 107 → 4,159 (was ~426k in 2 h before #125). **#125 works.** `dead_mints` 50,747 → 61,025; `dead_prints_dropped` 1,471,344.
- GC `max_pause_ms` 205 (was 2,035 before #124), 24 freezes. `runner_lag` healthcheck fails since 14:25Z: 2 (was ~3 per 2 h).
- **RSS still grows ~441 MB/h** (154 MB → 1,993 MB, linear: 1,118 MB at 2.08 h). The early buffer was not the dominant live cost. Leading suspect: `wallets` — 142,732 wallets, 452,659 per-mint entries, 308,951 `holds` entries in 4.2 h, all unbounded.
- Projection: RSS reaches `MemoryHigh` 10G around **2026-09-29 12:00–13:00Z**. It will not last to the kill review.

Next manager, in order:
1. **Stopgap before ~2026-09-29 10:00Z:** restart `mal-forward-paper` (`sudo -n systemctl --user -M ubuntu@ restart mal-forward-paper.service`) if no fix is deployed by then. Record it as restart #3 in LAB_STATE (resets cross-mint veto memory).
2. **Real fix:** a decision-neutral `wallets` bound (per-(wallet, mint) entries and `holds` for mints no book can act on and no feature reads), with the md5 replay proof on the staged 3.8 h slice (`/home/claude/profile-data/`; baseline decisions md5 `6b2f7786f8372c84a87ae13b6e273756`, positions `9b1ce744ef42222fe3a8cc352d35e3a0`). Check every read site of `_Wallet` fields (`_leader_ok`, `is_bot`/`is_sniper`, creator features). Measure with the live census after deploy.
3. Alternatively or additionally, the restart-neutral warm start (Open threads) makes periodic restarts a safe fallback.

## Open threads

- [#90](https://github.com/vaanai/MAL/pull/90): keep. `JobQueue` on `main` is still unbounded.
- Restart-neutral warm start: rebuild in-memory state from the tape since the clean-clock start on process boot, so a future restart no longer resets `WalletState`/`by_creator` cross-mint history.
- `wallets` growth: the largest remaining unbounded container (see (a) above).
- Deploy provenance on `mal-fast-0`: `/home/ubuntu/mal-oos` is not a git checkout. `mal-fast-create` is deployed by copying a file into `/var/lib/mal/eng` with a `.bak` backup, not a checkout either.
- Cursor timers cancellation: asked of the owner, not yet confirmed done.

## Host how-to (verified this session)

**Oracle (`mal-core-0`):**

- `ssh mal-core-0` — Claude's own read-only account (user `claude`, not `ubuntu`).
- Restart a unit: `sudo -n systemctl --user -M ubuntu@ restart <mal-unit>` (same pattern for `reload`/`try-restart`/`status`/`show`).
- Journal: `journalctl _UID=1001` (ubuntu's systemd `--user` units log under ubuntu's uid).
- Cgroup memory: `/sys/fs/cgroup/user.slice/user-1001.slice/user@1001.service/mal.slice/mal-batch.slice/mal-forward-paper.service/`.
- Runner code update: Claude cannot write files as `ubuntu`. The owner or Helm fast-forwards `/var/lib/mal/paper/forward-paper/src` to a `main` SHA, then Claude restarts the service.

**`mal-fast-0`:** the five `mal-fast-*` units run as `ubuntu`: `sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/$(id -u ubuntu) systemctl --user ...`. Claude's own timers (`mal-daily-review.timer`, `mal-oos-check.timer`) run as `claude`: plain `systemctl --user ...`, no `sudo -u` needed.

## Pointers

- Full state: [LAB_STATE.md](../LAB_STATE.md)
- Hosts detail: [docs/HOSTS.md](HOSTS.md)
- 2026-09-28 facts: [ARTIFACTS/daily/2026-09-28.md](../ARTIFACTS/daily/2026-09-28.md)
- Memory investigation: [ARTIFACTS/lab/forward-paper-memory-2026-09-27.md](../ARTIFACTS/lab/forward-paper-memory-2026-09-27.md)
