# DEC-012 — Tool-neutral workflow: manager plans, workers open PRs, Helm merges

| Field | Value |
| --- | --- |
| **Status** | Active (working law) |
| **Decider** | Vaan (owner moving day-to-day management off Cursor) |
| **Date** | 2026-09-27 |
| **Amends** | [CONSTITUTION.md](../CONSTITUTION.md) rules 11–14; [DEC-010](DEC-010-oracle-phase0-handoff-autonomy.md) §3 and §4 |
| **Does not amend** | Promotion gate, paper-only fence, JSONL vs Postgres, Cloudflare Access path ([DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md)), host-key pins |
| **Handoff** | [CLAUDE.md](../CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](../docs/MIGRATION-TO-CLAUDE.md), [docs/HOSTS.md](../docs/HOSTS.md) |

## Decision

1. **The repo is the source of truth.** Lab notes, daily briefs, and research options that lived in the project store are in git (`docs/`, `ARTIFACTS/lab/`, `ARTIFACTS/daily/`). Agents reload git, not a side store.

2. **Workflow is tool-neutral.** The manager plans. Workers implement via pull requests. **Helm merges after review.** Workers do not merge. One topic per PR. Branch names from Claude are `claude/<topic>`.

3. **Model-vendor rules are dropped.** Constitution rule 11 no longer names Composer 2.5 or Cursor Grok, and no longer has a Fast-mode switch. Seat roles (Helm, Scout, Graph, Proof) stay.

4. **The Cursor status-card rule is withdrawn.** DEC-010 §4 required a DM to Vaan with a Cursor-agent status card on every launch. That rule is gone. The pull request is the record.

5. **SSH is tool-neutral.** `scripts/mal-core/agent-ssh.sh --host core|fast` accepts `MAL_SSH_KEY_B64`, `CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET`, and still accepts the old Cursor / Cloudflare env names. Host keys stay pinned. A mismatch stops the session.

## What the old text said (kept here, not deleted from history)

CONSTITUTION rule 11 was: "Composer 2.5 / Cursor Grok preferred for lab agents; Fast mode OFF unless imperative and logged."

CONSTITUTION rule 13 was: "Managers (Grok) are the only persistent agents. Workers produce PRs/artifacts and leave."

DEC-010 §3 was: "Grok managers plan and review. Cursor implements and tests. Grok integrates. Cursor workers remain ephemeral; Grok seats remain the persistent managers."

DEC-010 §4 was: "Whenever any manager launches a Cursor agent — even after group-chat collab — DM Vaan a Cursor-agent status card."

Those sentences are historical. They are not instructions.

## Review trigger

- A tool change that would put trading keys on a host, open port 22, or publish Postgres.
- Helm delegating merges to a worker. That needs a new DEC.

## Overturn path

New DEC. Default remains: manager plans, workers open PRs, Helm merges, paper only, promotion gate unchanged.
