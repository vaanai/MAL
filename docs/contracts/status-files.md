# Status files contract (console-plan.md §9.4, §9.6)

Read-only per-box facts for the MAL Console's Machines/Home/Data/Spend
screens. Collector: `tools/mal_status.py`. Credit log: `tools/mal_credit_log.py`.

## Files

| Path | Written by | Cadence |
| --- | --- | --- |
| `<out-dir>/mal-fast-0.json` | `mal_status.py` (local mode) | every 1 min |
| `<out-dir>/mal-core-0.json` | `mal_status.py --remote core` | every 5 min |
| `<out-dir>/mal-research-0.json` | `mal_status.py --remote research` | every 5 min (unit not installed by the PR that added it) |
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
head_sha, post_lag_ms}, units:[...], units_meta{wall_ms, subprocesses},
errors:[...]`. `units` is described under "`units`" below.

`anon_mb` is `memory.stat`'s `anon` line — real use, never page cache
(`current_mb`/`memory.current` includes cache and is reported separately;
do not use it as "memory in real use"). `max_mb` is `null` when
`memory.max` reads the literal `max` (no ceiling).

## Schema `status.v1` (Oracle, `--remote core`)

`schema_version, host, generated_utc, reachable, load1, load5, load15,
memory, disk:[{mount:"/var/lib/mal", ...}], runner_status{lag_ms,
stale_cap_ms, fail_rate, ts}, health_latest (verbatim
health-latest.json), processes{mal-forward-paper, mal-observe,
mal-trade-tape, mal-attention, mal-funding-graph: bool}, units:[...],
errors:[...]`.
On SSH failure: `reachable: false` plus a scrubbed `error` and `units: []`, never a crash. One SSH
round trip per run: `ssh -o BatchMode=yes mal-core-0 python3 -` with a
small embedded read-only snippet on stdin (no systemctl call — presence is
a `/proc/*/cmdline` scan, since Claude's Oracle account is read-only).
The same scan feeds `units` (see below).

## Schema `status.v1` (mal-research-0, `--remote research`)

`schema_version, host, generated_utc, reachable, uptime_s, load1, load5,
load15, memory{mem_total_mb, mem_available_mb}, units:[...], units_meta,
errors:[...]`. One SSH round trip: `ssh -o BatchMode=yes mal-research-0
python3 - --units-json --unit-sources research`, with `tools/mal_status.py`
itself piped on stdin, so the unit collector is the same code as the local
one. `systemctl` is allowed there (system scope, plus the `claude` user
manager that runs `mal-walker-w1/w2/w3`). On SSH failure: `reachable: false`
plus a scrubbed `error`, `units: []`, never a crash. Slices are skipped; only `mal-*.service` and
`mal-*.timer` are listed.

## `units` (every status file)

One entry per `mal-*` service and timer, for the Console's "running now"
panel. Every key is always present; a value that cannot be read is `null`.

```json
{
  "name": "mal-fast-create.service",
  "owner_user": "ubuntu",
  "scope": "user",
  "kind": "service",
  "active_state": "active",
  "sub_state": "running",
  "since": "2026-09-29T04:27:17Z",
  "uptime_s": 162179,
  "memory": {"bytes": 55304192, "source": "cgroup memory.current (includes page cache)"},
  "last_log": "Started mal-fast-create.service - MAL phase-1 create alarm ...",
  "last_log_ts": "2026-09-29T04:27:17Z",
  "last_log_age_s": 162179
}
```

| Key | Meaning |
| --- | --- |
| `name` | systemd unit name, `mal-*.service` or `mal-*.timer`. |
| `owner_user` | Extra key beyond the panel's list. Manager that owns the unit: `root` (system scope), `claude`, or `ubuntu` (read via `sudo -n -u ubuntu`). On Oracle it is `ubuntu` by convention (HOSTS.md), not read from the process. |
| `scope` | `"system"` or `"user"`. |
| `kind` | `"service"` or `"timer"`. |
| `active_state`, `sub_state` | systemd's `ActiveState`/`SubState`. On Oracle they are **inferred from `/proc`**: `active`/`running` if a process matches, else `inactive`/`dead`. Oracle timers are not visible (no systemctl) and are not listed. |
| `since` | ISO-UTC of `ActiveEnterTimestamp`: the last time the unit entered `active`. For an inactive unit it is the previous run's start, not a current uptime; `null` if systemd reports none or a non-UTC zone. On Oracle: the earliest `/proc/<pid>/stat` start time of the matched pids (`btime` + starttime / `CLK_TCK`). |
| `uptime_s` | Seconds since `since`, only while `active_state` is `active`; else `null`. |
| `memory` | `{bytes, source}` or `null`. `source` is one of two literal labels: `"cgroup memory.current (includes page cache)"` (read from `/sys/fs/cgroup<ControlGroup>/memory.current`; not "real" use, it counts page cache) or `"process RSS (VmRSS, sum of matched pids)"` (Oracle; shared pages are counted once per process, so a multi-process unit can over-count). Timers and inactive services have no cgroup, so `null`. |
| `last_log` | Last log line, **always passed through `scrub_log_line`**. Local and research: `journalctl [--user] -u <unit> -n 1 -o short-iso` (message only, host/ident prefix dropped). Oracle: last complete line of the last ~4 KB of the unit's log file (`null` if that window holds no newline, never a mid-line fragment) (`observe.log`, `trade-tape.log`, `attention.log`, `funding-graph.log` under `/var/lib/mal/logs/`). `mal-forward-paper` has no log file (journal only), so it is `null` there. |
| `last_log_ts` | ISO-UTC of that line (journal timestamp; on Oracle the log file's mtime, i.e. the last write, not a parsed timestamp). |
| `last_log_age_s` | Seconds between collection time and `last_log_ts`. |

`units_meta` (`{wall_ms, subprocesses}`) is the cost of the last unit
collection on that host (not present for Oracle, which is one SSH round trip).
Local budget: per manager one `systemctl list-units 'mal-*' --all
--no-legend --plain` and one batched `systemctl show <units…> -p
Id,ActiveState,SubState,ActiveEnterTimestamp,ControlGroup`, plus one
`journalctl -n 1` per unit. mal-fast-0 queries three managers (system,
`claude` user, `ubuntu` user via sudo); mal-research-0 two.

### `scrub_log_line`

Pure function in `tools/mal_status.py`, applied to every `last_log`, and
also to the remote `errors` list and the SSH `error` text, before anything
is written. Steps, in order:

1. Cap the input at 4096 characters, so scrub time is bounded. When the
   cap fires, the trailing run of key-alphabet characters (at least the last
   64 characters) is dropped before scrubbing and the line ends with `…`, so
   a half-cut secret cannot survive. Every regex is bounded or linear; tests assert
   hostile 48,000-character lines scrub in under 50 ms.
2. Percent-decode (up to twice), so `Bearer%20key`, `api%5Fkey%3D…` and
   `hex%2Fhex` are seen as their decoded form. Control characters become
   spaces.
3. Replace each of these with `[redacted]` (exactly this list):
   - dashed UUIDs (Helius keys);
   - `Authorization: …` and `Cookie:` / `Set-Cookie:` (to end of line);
   - `X-…-Key: <value>` headers;
   - `Bearer <value>`;
   - `scheme://user:pass@` URL credentials;
   - any `http(s)/ws(s)` URL whose host contains `helius`, whole URL (so
     path keys and query strings go);
   - any URL query value of 20+ characters;
   - `HELIUS_API_KEY <value>` (space-separated);
   - `name=value`, `name: value` and JSON `"name":"value"` forms (quotes
     optional around name and value; quoted values may contain spaces and
     may be unterminated, to a 512-character limit) for `api-key`/
     `api_key`/`apikey`, `client_secret`, `secret_key`, `private_key`,
     `auth_token`, `secret`, `password`/`passwd`, `access_token`, `token`,
     `auth` and bare `key`, matched case-insensitively. The name may be
     glued to a prefix of up to 40 letters, digits, `_` or `-` (`PGPASSWORD`,
     `db-password`, `1password`, `x_api_key`, `secretKey`); the prefix is
     redacted with the value. So `monkey: 5` is redacted too;
   - `mck_…` and `sk-…` keys, also when glued to other text (`xmck_…`);
   - seed phrases: after `mnemonic`, `seed phrase`, `seed_phrase` or `seed words`, the rest of the line is redacted; `seed`, `pw` and `pwd` are also key names;
   - any run of 32+ characters from `[A-Za-z0-9+/_-]` plus up to two `=`
     (base64, base64url, base58 mints/signatures, hex).
4. Truncate to 200 characters plus `…`. Redaction comes first so a secret
   cut by the truncation cannot leave a prefix.

Known cost: the long-run rule also redacts harmless things of that shape (a
44-character mint address, a UUID-shaped id, a long `/var/lib/mal/…` path).
That is intended. It is still a pattern filter: a secret in a shape not
listed above, or a short low-entropy one with no key name, would pass. The
collector never opens an env file.

### Collection deadline

`collect_units` stops after about 30 s: remaining `journalctl` calls are
skipped (`last_log` null, state and memory kept), unqueried managers are
skipped, and one `units: 30s deadline reached; …` entry goes into `errors`,
so the file is always written inside `TimeoutStartSec`.

## Install (manager step, not run by this PR)

```bash
~/MAL/ops/claude-schedules/install.sh   # links all claude units, including the status ones
systemctl --user enable --now mal-status.timer
systemctl --user enable --now mal-status-core.timer
systemctl --user enable --now mal-status-research.timer   # new; needs the ssh mal-research-0 path
```

`mal-status.service` also runs `mal_credit_log.py` (second `ExecStart=` in
the same oneshot) so the Spend screen's log stays current on the same
1-minute cadence. None of the timers restarts anything, writes under
`/var/lib/mal`, or reads a secret/env file.
