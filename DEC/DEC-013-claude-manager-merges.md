# DEC-013 — Claude manager merges; Helm's remaining scope

| Field | Value |
| --- | --- |
| **Status** | Active (working law) |
| **Decider** | Vaan (owner) |
| **Date** | 2026-09-27 |
| **Amends** | [CONSTITUTION.md](../CONSTITUTION.md) rule 11; [DEC-012](DEC-012-tool-neutral-manager-workers.md) §2 |
| **Does not amend** | Promotion gate, paper-only fence, one-topic-per-PR, workers-do-not-merge, JSONL vs Postgres |
| **Handoff** | [CLAUDE.md](../CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](../docs/MIGRATION-TO-CLAUDE.md), [docs/HOSTS.md](../docs/HOSTS.md) |

## Context

DEC-012 (2026-09-27) made the workflow tool-neutral but kept "Helm merges after review." The owner has now moved day-to-day PR ownership on `vaanai/MAL` to the Claude manager session on `mal-fast-0`.

## Decision

1. The Claude manager session on `mal-fast-0` owns PRs on `vaanai/MAL`: it opens, reviews, and merges them, running a `reviewer` pass on every PR and a `quant-proof` pass where the PR touches a claimed edge. Workers still open PRs and do not merge.
2. Helm no longer merges MAL PRs.
3. Helm keeps ufw/firewall, sshd/SSH exposure, the Cloudflare tunnel and Access, and the Oracle (`mal-core-0`) server's admin. Requests for those go to the owner, who relays to Helm.
4. Claude has full sudo on `mal-fast-0`, with the same fences as before: never touch ufw/iptables/nft, sshd, cloudflared, or Cloudflare. On `mal-core-0` Claude has a read-only account over `ssh mal-core-0` (Cloudflare Access, its own key and service token). Restarts and other changes on Oracle still go through the owner or Helm.
5. `/opt/miscusi` on `mal-fast-0` is no longer a bare "unrelated app, do not touch." It is a separate project, `vaanai/MiScusi`, led by the same Claude manager. MAL work must not modify it; changes to it follow the MiScusi repo's own deploy docs.
6. Cursor may still take work occasionally; sync stays through GitHub.

## Consequences

- CONSTITUTION rule 11, CLAUDE.md, `docs/MIGRATION-TO-CLAUDE.md`, `docs/HOSTS.md`, and `LAB_STATE.md` drop "Helm merges" language in favor of "the manager merges."
- DEC-012 is superseded in part: its "Helm merges after review" clause is replaced by this DEC. The rest of DEC-012 (repo as source of truth, tool-neutral SSH, dropped model-vendor rules) stands.
- Workers still do not merge. One topic per PR still holds. The promotion gate, paper-only fence, and host fences are unchanged.

## Overturn path

New DEC. Default remains: manager plans, workers open PRs, the Claude manager merges after review, paper only, promotion gate unchanged.

## Amendment 1 (2026-10-29): review tiers and agent model tiers

**Decider:** Vaan (owner), chat 2026-10-29 ~15:3xZ: "I think yes to all of the decisions for me looks good. Get started on the management cleanup. Let's just make sure it doesn't compromise anything and make our efficiency worse. It should be better." This approves decisions 2 and 3 of `/data/mal/hunt-1008/ops/manager17/CONTEXT-COST-PLAN-1029-v2.md` (notebook n_O1kgzjdWc7wqBQ).

**Why:** review rounds were the second-largest manager cost. Many records-only PRs got two separate agents in each of 2–4 rounds.

### 1. Review tiers
Every PR still needs both verdict lines on the PR at the exact head before merge:
- `QP VERDICT at <sha>: OK`
- `REVIEWER VERDICT at <sha>: APPROVE`

The merge check (grep of those lines, body matches the PR, base is main, `--match-head-commit`) is unchanged. The tier sets who writes the two lines.

- **T0, one combined pass.** One agent writes both lines, as two separate comments. It must do both jobs: the reviewer's diff review and QP's check of every claim and number against its source. T0 covers PRs that change **only**:
  - records, notes, lab notes, daily briefs, the idea library, the Console data;
  - docs/HANDOFF.md;
  - KILL or NOT_DECIDABLE write-ups;
  - ops scripts that no book, read, registration or live path runs.
- **T1, separate QP and reviewer agents, as before.** T1 covers any PR that touches:
  - code that a book, runner, shadow, scorer, read, gate check, executor or live path runs;
  - a read rule, registration, seal, the promotion gate, a DEC or EXP binding clause, or a live stage or limit;
  - any sentence that says a book made money.
- **When unsure, T1.** The manager names the tier in the review brief. A T0 reviewer that finds T1 content says so and stops; the PR moves to T1.
- **Unchanged:** the gate, workers-do-not-merge, the exact-head rule, and DEC-032/DEC-033 go-live checks.

### 2. Agent model tiers
- **Haiku 5.5:** mechanical agents, such as copies, counts, catalogues, link checks and triage tables.
- **Sonnet 5.5:** builders on small, well-specified changes, and T0 passes on docs-only PRs.
- **Opus:** QP rulings, T1 reviews, design, registrations and the manager.

The manager may move any task up a tier. A task never moves down to cut cost when it decides a read, a seal or the gate.

### 3. Common brief
Agent briefs cite `/data/mal/hunt-1008/ops/AGENT-BRIEF-COMMON.md` (seals, hard rules, mechanics, report-to-file with a ≤5-line reply) instead of repeating it.
