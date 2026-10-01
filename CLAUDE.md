# MAL

Pump.fun / Solana research lab. GitHub `vaanai/MAL` is the source of truth. This file points at the docs. It does not copy them.

## North star

**Profit**, in SOL after fees.

The path is fixed: trade tape → honest simulator → signals → forward paper → gated live.

Live trading does not start until a book clears the promotion gate and the owner approves. Speed matters. The standard does not drop to buy speed.

## Reload order

Read these at the start of a session, in this order:

0. [docs/HANDOFF.md](docs/HANDOFF.md) — current manager handoff, while it exists. Remove this line and the file once it goes stale.
1. [LAB_STATE.md](LAB_STATE.md)
2. [CONSTITUTION.md](CONSTITUTION.md)
3. [ARTIFACTS/SUMMARY.md](ARTIFACTS/SUMMARY.md)
4. The active files in [DEC/](DEC/)
5. [docs/HOSTS.md](docs/HOSTS.md)

Then the one experiment or host you are touching. Daily briefs are in [ARTIFACTS/daily/](ARTIFACTS/daily/). Lab notes are in [ARTIFACTS/lab/](ARTIFACTS/lab/).

## Hard rules

- Paper only on both hosts. No trading keys, wallet keys, or X keys.
- Postgres stays on localhost. Never print or commit the DB password.
- Port 22 is never opened to the world. SSH is Cloudflare Access.
- Stop on a host-key mismatch. Pins are in [docs/HOSTS.md](docs/HOSTS.md).
- `/opt/miscusi` on `mal-fast-0` is a separate project, `vaanai/MiScusi`, led by the same Claude manager. Do not modify it from MAL work. Changes to it follow that repo's own deploy docs.
- Do not commit `.env` files, API keys, or private keys. The Helius key on the hosts is `/var/lib/mal/backfill/helius.env` (Oracle) and `/var/lib/mal/fast-listener/helius.env` (fast). It stays there.
- Do not edit `ufw`/firewall, `sshd_config`, cloudflared config, or Cloudflare Access. Those, plus the Oracle server's admin, are Helm's. Requests go to the owner, who relays to Helm.
- Workers do not merge. The manager merges after review ([DEC-013](DEC/DEC-013-claude-manager-merges.md)).

## Promotion gate

A book promotes only if all of these hold, under **both** the flat 15% fail model and the pressure-fail model at slope scale 1:

- at least 100 out-of-sample trades
- at least 5 distinct UTC days, with a majority of those days positive
- lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile)
- total SOL still positive after removing the top 3 trades

The first positive in-sample cell does **not** clear this gate. Read the paragraph in LAB_STATE before claiming otherwise. Winner's curse applies: it was the best of 972 cells, the CI lower bound is below 0, and the out-of-sample book is still small.

Forward-paper rows from 2026-09-25T19:00:00Z through 2026-09-27T06:58:12Z are void. Clean clock 2026-09-28T00:00:00Z. Kill review 2026-10-05T05:00:00Z.

## How to work

- Branch: `claude/<topic>`. One topic per branch. Small pull requests.
- Workers open the PR and stop. The manager (the Claude session on `mal-fast-0`) reviews and merges, with a `reviewer` pass and a `quant-proof` pass where the PR touches an edge claim.
- Commit and push often, at a green unit test, not at the end of a long uncommitted pile.
- Workers implement. The manager plans. See [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md) and [DEC-013](DEC/DEC-013-claude-manager-merges.md).
- Subagents in [.claude/agents/](.claude/agents/): `builder`, `host-ops`, `quant-proof`, `reviewer`.
- Commands: `/status`, `/new-experiment`.

`quant-proof` reviews any claimed edge against the gate before the PR text says the book is positive.

## Hosts

| Host | Use |
| --- | --- |
| `mal-core-0` | Oracle, aarch64. Archive, tape, training, forward paper. |
| `mal-fast-0` | OVH Frankfurt, x86_64. Fast listeners and the backward backfill. Main focus. |

Connect with `scripts/mal-core/agent-ssh.sh --host core|fast`, or directly with `ssh mal-core-0` (Cloudflare Access, Claude's own key and service token; read-only account). Dry-run first. Claude has full sudo on `mal-fast-0` — still never touching `ufw`/firewall, `sshd`, `cloudflared`, or Cloudflare. `mal-core-0` access is read-only; restarts and other changes on Oracle go through the owner or Helm. Details: [docs/HOSTS.md](docs/HOSTS.md). Migration steps that are **not** in git: [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md).

## Owner notes

These are the owner's instructions. They are not measurements.

- Helius autoscaling is set to about **20M extra credits**. Use it when it clearly speeds the work up or makes the system better. Report what was used (which job, how many credits, what got faster).
- The owner expects to clear about **$300** in profit once real trading starts, and much more monthly after a couple more weeks. That is the **owner's target**, not evidence. Nothing goes live until a book clears the promotion gate and the owner approves.
- Move fast toward profit. Keep the bar high: honest fees, out-of-sample days, and the gate above.

## This week's clocks

Do not score the void as if it were live.

| Clock | Instant |
| --- | --- |
| Stale-fill void | 2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z |
| Clean forward paper | 2026-09-28T00:00:00Z |
| Pressure-leg start (DEC-014 Amendment 3) | 2026-09-29T00:00:00Z |
| Daily runner restart | every day, 00:00:00Z |
| EXP-011 one-shot read (pre-reg + scorer merged in #164; read once when walker C completes) | about 2026-09-30T19–20Z |
| Kill review | 2026-10-05T05:00:00Z |

Three backward walkers run on `mal-fast-0`, splitting `[2026-09-09T12, 2026-09-19T01)` into non-overlapping blocks: `mal-fast-backfill` (walker 1) floored at 2026-09-15T12, `mal-fast-backfill-b` covering `[2026-09-12T12, 2026-09-15T12)`, and `mal-fast-backfill-c` covering `[2026-09-09T12, 2026-09-12T12)`. Each has its own credit cap. No hour is claimed by two walkers.

## When you use a subagent

| Agent | Use it for |
| --- | --- |
| `builder` | One code change and its tests |
| `host-ops` | Read-only SSH and health. Restarts need a manager |
| `quant-proof` | Any sentence that says a book made money |
| `reviewer` | The diff, before the PR |

## What a PR contains

- A `claude/<topic>` branch, one topic.
- Tests for the behavior you changed.
- A body that says what was measured and what was not.
- No secret, no `.env`, no host-key private material.
- No request for the reviewer to merge it for you.

Push when the tests you ran are green. Do not sit on a day of uncommitted edits.

## Operating notes

- Builder agents stop at ~40 turns. Give them tight scopes and ask for WIP pushes rather than one long uncommitted run.
- Use worktree isolation for parallel agents.
- **Every long-running job is a MiScusi job** (`miscusi_job_submit`; `resumable: true` when it checkpoints): walkers, backfills, replays and heavy builds. No standalone `systemd --user` units, nohup or tmux for new work: the owner and the Console see only MiScusi jobs (state, log, memory). Delegate code tasks the owner should follow with `miscusi_delegate`. Owner instruction, 2026-10-01. Exception: the research-0 walkers `mal-walker-w1/w2/w3` for `[2026-09-03T12, 2026-09-09T12)` finish as user units.
- Oneshot systemd services show `ActiveState=activating` while running — don't wait on `is-active`.
- PR merge/close and `mal-*` restarts are pre-approved in the permissions file.
- Every runner change must carry an md5 decision-equivalence replay proof before deploy.
- Record every mid-week runner restart in LAB_STATE.
- `mal-fast-0` memory: one heavy replay job at a time, at most 2 workers, stream rows to disk, and check `systemctl show user-1002.slice -p MemoryCurrent` before starting a new one. Two concurrent multi-worker replay jobs caused an OOM and a reboot on 2026-09-29.
- Never add forward books during a kill-review week.

## Current candidate

The frozen migrate-direct cell (`migrate` × `tp50_sl30` × slot+1 start, direct) is **dead**: formal FAIL from the 2026-09-28T21:00Z one-shot, both fail models, every CI lower bound below 0. It is not refit. No more Helius credits go to it. See [LAB_STATE.md](LAB_STATE.md) for the numbers.

The candidate is **EXP-011**, **pre-registered** in [#164](https://github.com/vaanai/MAL/pull/164): a frozen, leakage-ablated S2 entry-selection model at a fixed threshold. Its holdout `[2026-09-09T12, 2026-09-15T12)` is read **exactly once**, by `tools/exp011_score.py`, after both walkers finish. Evidence:

- [EXP/EXP-011-migrate-entry-model-prereg.md](EXP/EXP-011-migrate-entry-model-prereg.md) (pre-registration, merged; frozen artifacts in `ARTIFACTS/exp011/`)
- [ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md) (the exploration-pool B3 result the pre-registration follows)
- The [#156](https://github.com/vaanai/MAL/pull/156) PR audit comment

The B3 exploration result is not a promote by itself — it is exploration-pool evidence on data EXP-011 does not reuse. Numbers belong in LAB_STATE. Copy them from the files. Do not round them up.

## Credits

If a job uses Helius autoscaling (about 20M extra credits), the PR or the daily note records: which unit, how many credits, and what got faster or more complete. "I turned it up" without a number is not a report.

## Where the long docs are

| Topic | File |
| --- | --- |
| Current state | [LAB_STATE.md](LAB_STATE.md) |
| Rules | [CONSTITUTION.md](CONSTITUTION.md) |
| Hosts | [docs/HOSTS.md](docs/HOSTS.md) |
| Leaving Cursor | [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md) |
| Plan and context | [docs/plan.md](docs/plan.md), [docs/project-context.md](docs/project-context.md) |
| Research options | [docs/research/](docs/research/) |
| Experiments | [EXP/](EXP/) |
| SSH | [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md) |
