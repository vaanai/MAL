# Status files contract (console-plan.md §9.4, §9.6)

Read-only per-box facts for the MAL Console's Machines/Home/Data/Spend
screens. Collector: `tools/mal_status.py`. Credit log: `tools/mal_credit_log.py`.

## Files

| Path | Written by | Cadence |
| --- | --- | --- |
| `<out-dir>/mal-fast-0.json` | `mal_status.py` (local mode) | every 1 min |
| `<out-dir>/mal-core-0.json` | `mal_status.py --remote core` | every 5 min |
| `<log-path>` (default `/home/claude/data/credits/helius-credits.jsonl`) | `mal_credit_log.py` | every 1 min, only on change or ≥1h since last line per job |

`<out-dir>` default is `/var/lib/mal/status`; `claude` cannot write there
without sudo, so the installed units use `/home/claude/data/status` instead
— pass `--out-dir` to point anywhere else.

## Schema `status.v1` (fast host, local mode)

`schema_version, host, generated_utc, uptime_s, load1, load5, load15,
cpu_count, memory{mem_total_mb, mem_available_mb, cgroups:[{name, path,
anon_mb, current_mb, max_mb}]}, disk:[{mount, total_gb, used_gb, free_gb,
pct}], services:[{name, owner_user, state}], walkers:[{name, sealed,
total_hours_in_checkpoint, oldest_sealed, newest_sealed, credits_used,
stop_reason?}], pre_create_credits, last_runner_restart{restart_utc, ok,
head_sha, post_lag_ms}, errors:[...]`.

`anon_mb` is `memory.stat`'s `anon` line — real use, never page cache
(`current_mb`/`memory.current` includes cache and is reported separately;
do not use it as "memory in real use"). `max_mb` is `null` when
`memory.max` reads the literal `max` (no ceiling).

## Schema `status.v1` (Oracle, `--remote core`)

`schema_version, host, generated_utc, reachable, load1, load5, load15,
memory, disk:[{mount:"/var/lib/mal", ...}], runner_status{lag_ms,
stale_cap_ms, fail_rate, ts}, health_latest (verbatim
health-latest.json), processes{mal-forward-paper, mal-observe,
mal-trade-tape, mal-attention, mal-funding-graph: bool}, errors:[...]`.
On SSH failure: `reachable: false` plus `error`, never a crash. One SSH
round trip per run: `ssh -o BatchMode=yes mal-core-0 python3 -` with a
small embedded read-only snippet on stdin (no systemctl call — presence is
a `/proc/*/cmdline` scan, since Claude's Oracle account is read-only).

Every section is independently try/excepted; failures land in `errors`,
never abort the write.

## Credit log line

One JSONL line per job that changed or is stale: `{ts_utc, job
(walker_1|walker_b|walker_c|pre_create), credits_used, delta_since_last
(null on first line for that job), cap}`. Written under an `fcntl` lock so
two collectors racing each other never interleave or double-append.

## Install (manager step, not run by this PR)

```bash
~/MAL/ops/claude-schedules/install.sh   # links all claude units, including these four
systemctl --user enable --now mal-status.timer
systemctl --user enable --now mal-status-core.timer
```

`mal-status.service` also runs `mal_credit_log.py` (second `ExecStart=` in
the same oneshot) so the Spend screen's log stays current on the same
1-minute cadence. Neither timer restarts anything, writes under
`/var/lib/mal`, or reads a secret/env file.
