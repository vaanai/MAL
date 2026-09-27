# Claude-scheduled jobs (mal-fast-0, user `claude`)

Rebuilds the two Cursor cloud timers named in
[docs/MIGRATION-TO-CLAUDE.md](../../docs/MIGRATION-TO-CLAUDE.md) and specified
exactly in `ARTIFACTS/lab/cursor-timers.md` (on `main`, added by PR #115), as
systemd `--user` timers for the `claude` user. Both jobs are **read-only**:
they gather facts about the host with plain shell commands in the wrapper
script (never via the LLM's own tool calls), then run a headless
`claude -p` session whose only tools are `Read`/`Grep`/`Glob` plus
`Write`/`Edit` scoped to that job's own report directory and the shared
`INDEX.md`. Neither job can restart a unit, touch `/opt/miscusi`, edit
`ufw`/`sshd`/`cloudflared`, read a `.env` or `helius.env`, or push to git.

| File | Purpose |
| --- | --- |
| [daily-review.prompt.md](daily-review.prompt.md) | Static prompt for the 05:00 UTC daily review |
| [daily-review.sh](daily-review.sh) | Wrapper: gathers facts, renders the prompt, runs headless claude, writes the report |
| [mal-daily-review.service](mal-daily-review.service) | oneshot unit, `Type=oneshot`, 20-minute `TimeoutStartSec` |
| [mal-daily-review.timer](mal-daily-review.timer) | `OnCalendar=*-*-* 05:00:00 UTC`, `Persistent=false` |
| [oos-check.prompt.md](oos-check.prompt.md) | Static prompt for the one-shot OOS check |
| [oos-check.sh](oos-check.sh) | Wrapper: reads the frozen cell's report/manifest, renders the prompt, runs headless claude, writes the report |
| [mal-oos-check.service](mal-oos-check.service) | oneshot unit |
| [mal-oos-check.timer](mal-oos-check.timer) | `OnCalendar=2026-09-28 21:00:00 UTC`, `Persistent=false` |
| [install.sh](install.sh) | Symlinks the four unit files into `~/.config/systemd/user/` and runs `daemon-reload`. Does **not** enable or start the timers. |

## Reports

Each run writes:

- `/home/claude/reports/daily-review/<UTC date>.md` (or `TEST-<date>.md`
  for a manual `--test` run) — first line `STATUS: OK` or `STATUS: ATTENTION`.
- `/home/claude/reports/oos-check/<UTC date>.md` (or `TEST-<date>.md`) —
  first line `VERDICT: PASS`, `VERDICT: FAIL`, or `VERDICT: UNDER-SAMPLED`.
- One appended line each to `/home/claude/reports/INDEX.md`.
- A `.log` (wrapper + claude session log) and a `.facts.txt` (the exact
  facts block handed to claude) next to each report, same stamp.

## Install (already done once on this box; safe to re-run)

```bash
ops/claude-schedules/install.sh
```

This only symlinks units and runs `systemctl --user daemon-reload`. The
timers are loaded but **inactive** until a manager runs:

```bash
systemctl --user enable --now mal-daily-review.timer
systemctl --user enable --now mal-oos-check.timer
```

## Manual test (timers still disabled)

```bash
ops/claude-schedules/daily-review.sh --test
ops/claude-schedules/oos-check.sh --test
```

Each should exit 0 and leave a sensible `TEST-<date>.md` report under its
report directory.

## Why the wrapper gathers facts instead of the LLM

The headless session has no Bash tool at all — it only reads files and
writes inside its own report directory. All host/unit/file inspection
(`systemctl --user show`, `cat report.json`, `df`, the Oracle SSH probe,
etc.) happens in the wrapper script under `set -euo pipefail`, using the
same read-only commands a human would run, and is pasted into the prompt as
a `FACTS` block. This keeps the permission surface small and auditable: the
only way either job can affect anything outside its own report directory is
if the wrapper script itself is edited.

## Notes

- `mal-core-0` (Oracle) is reachable read-only over plain `ssh mal-core-0`
  (Cloudflare Access, a dedicated key, configured for user `claude`;
  `~/.ssh/config` has the `Host mal-core-0` block). `sudo -n` on Oracle is
  restricted by sudoers to `systemctl status/list-timers/list-units/show/cat`
  (plus a `claude-journalctl` wrapper) — both wrappers use exactly those,
  nothing else, always under a `timeout`. If Oracle stops answering, the
  daily job now treats that as `STATUS: ATTENTION` (it used to be an
  expected no-credentials gap; it no longer is).
- The one-shot OOS check pools Oracle's and the fast box's
  `migrate-direct-oos*/report.json` using the exact method
  `ARTIFACTS/lab/migrate-direct-oos.md` already demonstrates (trade-weighted
  mean; union of days; sum of days-positive) — n and distinct days pool
  exactly, but the 90% CI lower bound and ex-top-3 total do **not** combine
  linearly from the two per-host summaries (verified against the doc's own
  worked numbers), so the prompt reports those two per host only and never
  fabricates a pooled figure for them. A verdict of `PASS` requires an
  actual pooled CI lower bound, not an invented one — see
  `oos-check.prompt.md` for the full decision rule.
- `loginctl show-user claude -p Linger` is checked and reported by the
  daily job; this repo does not change it.
- Neither job proposes or authorizes live trading, even on a PASS verdict
  from the OOS check — see [CLAUDE.md](../../CLAUDE.md) ("Promotion gate"
  and "Live bar").
- **A permission leak was found and fixed while building this**: running
  `claude -p` from inside `/home/claude/MAL` let the repo's own
  `.claude/settings.json` (`"allow": ["Read","Edit","Write",...]`
  unrestricted) merge into and override the restrictive `--allowedTools`
  passed on the command line, so the headless job could write anywhere in
  the repo despite the flag. Fix (see the comment above the `claude`
  invocation in each `.sh`): run with cwd = `/home/claude/reports` (not the
  repo), `--setting-sources ""` to exclude project/user settings entirely,
  `--add-dir "$ROOT"` for read-only repo access, and cwd-**relative**
  `Edit()` patterns (`./daily-review/<stamp>.md`, `./INDEX.md` — absolute
  paths were unreliable even for in-scope targets in testing). Verified by
  hand: writes to the repo, to the other job's report directory, and to
  absolute paths were all denied in the final configuration; the two
  intended relative writes succeeded every time.
