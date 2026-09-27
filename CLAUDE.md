# MAL

Pump.fun / Solana research lab. GitHub `vaanai/MAL` is the source of truth. This file points at the docs. It does not copy them.

## North star

**Profit**, in SOL after fees.

The path is fixed: trade tape → honest simulator → signals → forward paper → gated live.

Live trading does not start until a book clears the promotion gate and the owner approves. Speed matters. The standard does not drop to buy speed.

## Reload order

Read these at the start of a session, in this order:

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
- Do not touch `/opt/miscusi` on `mal-fast-0`.
- Do not commit `.env` files, API keys, or private keys. The Helius key on the hosts is `/var/lib/mal/backfill/helius.env` (Oracle) and `/var/lib/mal/fast-listener/helius.env` (fast). It stays there.
- Do not edit `ufw`, `sshd_config`, or cloudflared config.
- Do not merge. Helm merges after review.

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
- Open the PR and stop. Helm reviews and merges.
- Commit and push often, at a green unit test, not at the end of a long uncommitted pile.
- Workers implement. The manager plans. See [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md).
- Subagents in [.claude/agents/](.claude/agents/): `builder`, `host-ops`, `quant-proof`, `reviewer`.
- Commands: `/status`, `/new-experiment`.

`quant-proof` reviews any claimed edge against the gate before the PR text says the book is positive.

## Hosts

| Host | Use |
| --- | --- |
| `mal-core-0` | Oracle, aarch64. Archive, tape, training, forward paper. |
| `mal-fast-0` | OVH Frankfurt, x86_64. Fast listeners and the backward backfill. Main focus. |

Connect with `scripts/mal-core/agent-ssh.sh --host core|fast`. Dry-run first. Read-only unless a manager has approved a restart. Details: [docs/HOSTS.md](docs/HOSTS.md). Migration steps that are **not** in git: [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md).

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
| Kill review | 2026-10-05T05:00:00Z |
| Oracle OOS timer | 01:20 UTC, frozen cell only |
| One-shot OOS read | about 2026-09-28 21:00Z, after the fast box's backward hours |

The fast backfill walks back from 2026-09-21T23Z and stops at +2M credits. Oracle's backfill stays inside 2026-09-22T00Z–2026-09-25T07Z. Do not let the two ranges meet.

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

## Evidence for the current cell

Read these before changing the frozen migrate-direct test. The cell was locked at 2026-09-27T13:06:36Z. Do not retune it on the out-of-sample hours.

- [ARTIFACTS/lab/latency-curve-2026-09-27.md](ARTIFACTS/lab/latency-curve-2026-09-27.md) and the JSON grid beside it
- [ARTIFACTS/lab/fee-audit-2026-09-27.md](ARTIFACTS/lab/fee-audit-2026-09-27.md)
- [ARTIFACTS/lab/migrate-direct-prereg.md](ARTIFACTS/lab/migrate-direct-prereg.md)
- [ARTIFACTS/lab/migrate-direct-oos.md](ARTIFACTS/lab/migrate-direct-oos.md)

The in-sample cell is `migrate` × `tp50_sl30` × slot+1 start, direct, 0.05 SOL. The frozen OOS priority is the slot-+1 p75, 0.0005 SOL/side, with 0.5 SOL as the primary size. Numbers belong in LAB_STATE. Copy them from the files. Do not round them up.

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
