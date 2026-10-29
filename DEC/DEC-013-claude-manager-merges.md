# DEC-013 — Claude manager merges; Helm's remaining scope

| Field | Value |
| --- | --- |
| **Status** | Active (working law); Amendment 1 (2026-10-29) below |
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

**Decider:** Vaan (owner), in chat on 2026-10-29, recorded in notebook n_O1kgzjdWc7wqBQ: "I think yes to all of the decisions for me looks good. Get started on the management cleanup. Let's just make sure it doesn't compromise anything and make our efficiency worse. It should be better." This approves decisions 2 and 3 of the manager's context plan v2 (host file `/data/mal/hunt-1008/ops/manager17/CONTEXT-COST-PLAN-1029-v2.md`).

**Why:** review rounds were the second-largest manager cost. Many records-only PRs got two separate agents in each of 2–4 rounds.

### 1. Both verdict lines on every PR (supersedes the QP clause of Decision 1)
Every PR needs both lines on the PR at the exact head before merge:
- `QP VERDICT at <sha>: OK`
- `REVIEWER VERDICT at <sha>: APPROVE`

This has been practice since 10-12 (#621). Decision 1 asked for QP only "where the PR touches a claimed edge", so this is stricter. The merge check is unchanged: grep both lines, check the posted body matches the PR, check the base is main, then `--match-head-commit`. The tier below sets only **who** writes the two lines.

### 2. Tiers
- **T1, separate QP and reviewer agents (as before), is the default.** A PR is T1 if its diff contains **any** T1 trigger, whatever the file type. A mixed PR is T1. The T1 triggers:
  1. any code or script (tools/, scripts/, tests, configs), including ops scripts;
  2. any edit under DEC/ or EXP/ other than a pure typo or link fix;
  3. edits to docs/HOLDOUT_LEDGER.md, any seals text (including SEALS-BRIEF and the HANDOFF seals section), a read rule, a registration, the promotion gate, or a live stage or limit;
  4. in docs/HANDOFF.md or LAB_STATE.md: any change to seals, clocks, deadlines, read instants or go-live conditions;
  5. any claimed edge, any sentence that says or implies a book is positive or made money (CLAUDE.md), any read result or NOT_DECIDABLE finding, and any number that decides a read, gate or kill.
- **T0, one combined agent** writing both lines as two separate comments, applies only when the diff has **no** T1 trigger. Examples:
  - records and docs: lab notes, daily briefs, idea-library rows, Console data, and LAB_STATE or HANDOFF edits outside item 4 above;
  - KILL write-ups that carry no deciding number;
  - pure typo or link fixes.

  This is exactly the scope the owner approved in plan v2 ("records, docs and KILL write-ups"). Widening T0 needs the owner's word.
- **T0 still does both jobs.** The combined agent:
  - does the reviewer's diff review;
  - checks every quoted number or claim against its source (including Console data/console.json against its lab note);
  - re-derives the tier from the diff's file list and content. If it finds any T1 trigger, it posts no verdict lines, says so, and the PR moves to T1.
- **Who sets the tier:** the manager names it in the review brief. The PR author or builder never sets or claims a tier. The combined agent is never the PR's author or builder.
- **When unsure, T1.**
- **Unchanged:** the promotion gate, workers-do-not-merge, the exact-head rule, and the DEC-032/DEC-033 go-live checks.

### 3. Agent model tiers
- **Haiku 5.5:** mechanical agents, such as copies, counts, catalogues, link checks and triage tables. They never write a verdict line.
- **Sonnet 5.5:** builders on small, well-specified changes.
- **Opus:** every verdict line (T0 combined passes and both T1 passes), QP rulings, design, registrations and the manager.

The manager may move a task up a tier. A task that decides a read, a seal or the gate never moves down to cut cost.

### 4. Common brief
Agent briefs may cite the host file `/data/mal/hunt-1008/ops/AGENT-BRIEF-COMMON.md` instead of repeating the common rules. That file is operational: this DEC does not depend on its contents. The binding seals stay in SEALS-BRIEF, docs/HANDOFF.md and the DEC/EXP texts, and any edit to that file's seals text is treated as a T1 matter.
