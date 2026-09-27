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
