# Migration to Claude

Repo and docs only. This checklist is what the owner or Helm must do **outside** the repository. Nothing here changes a host, a systemd unit, Cloudflare, or a secret.

The pull request that adds this file does not merge itself. Helm merges after review.

## Secrets to create

Use these names. The SSH helper still accepts the old Cursor names if they are already set. When both are set, the neutral name wins.

| Neutral name | Replaces | What it is |
| --- | --- | --- |
| `MAL_SSH_KEY_B64` | `CURSOR_CLOUD_AGENT_SSH_KEY` | Base64 of the dedicated agent OpenSSH private key. Not the owner's personal key. |
| `CF_ACCESS_CLIENT_ID` | `CLOUDFLARE_ACCESS_CLIENT_ID` | Cloudflare Access service-token id. Reuse the existing token. |
| `CF_ACCESS_CLIENT_SECRET` | `CLOUDFLARE_ACCESS_CLIENT_SECRET` | Cloudflare Access service-token secret. Reuse the existing token. |

Do not invent new Access policies. Do not put the token in git, in a PR, or in this file.

Optional client-key pin, only if you want the helper to refuse a different private key: `MAL_SSH_KEY_FINGERPRINT`. The historical Cursor deploy key was `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`. A new Claude key will not match that, so leave the pin unset unless you are still using that key.

Host-key pins are already in [docs/HOSTS.md](HOSTS.md). They are public fingerprints, not secrets. Stop on a mismatch.

## Where Claude runs

Pick one place and install the secrets there:

- The owner's machine, with `~/.ssh/config` as in [tools/oracle_ssh_smoke.md](../tools/oracle_ssh_smoke.md) (`ProxyCommand cloudflared access ssh --hostname %h`), or
- A Claude Code session that has the three neutral env vars and `scripts/mal-core/agent-ssh.sh`.

`cloudflared` itself still reads `CLOUDFLARE_ACCESS_CLIENT_ID` and `CLOUDFLARE_ACCESS_CLIENT_SECRET`. On an always-on machine, map the neutral names onto those in the shell profile. The helper does that mapping internally. Do not install a second tunnel.

## Cloudflare

Reuse the existing Access service token and the existing hostnames:

- `ssh.tradervaan.com` → `mal-core-0`
- `ssh-fast.tradervaan.com` → `mal-fast-0`

Do not change tunnel ingress, Access policies, or `cloudflared` config on the hosts as part of this move.

## GitHub

Claude needs permission to push branches and open pull requests on `vaanai/MAL`. It does not need permission to merge. Helm merges after review.

Branch names from Claude: `claude/<topic>`. One topic each.

## Helius key

Already on the hosts. Never commit it.

| Host | Path |
| --- | --- |
| `mal-core-0` | `/var/lib/mal/backfill/helius.env` |
| `mal-fast-0` | `/var/lib/mal/fast-listener/helius.env` |

The fast backfill unit and the mint-authority listener load the fast path. Oracle backfill and the funding graph load the Oracle path. Mode 600. A missing file is a stop, not a guess.

Autoscaling is about 20M extra credits (owner setting, see [CLAUDE.md](../CLAUDE.md)). Report credits when a job uses that headroom.

## Recreate these schedules

They were Cursor timers, not cron on the hosts. They are not in git as running jobs.

| Job | When | What |
| --- | --- | --- |
| Daily review | **05:00 UTC** every day | Reload LAB_STATE, check both hosts, say whether anything promotes. The 2026-09-27 review promoted nothing. |
| One-shot OOS check | about **2026-09-28 21:00Z** | Read the fast-box migrate-direct report after the backward hours that were projected to land near 2026-09-28 20:00Z. Do not refit the cell. |

Put both on cron (or the Claude scheduler) wherever Claude actually runs. The in-host timers stay as they are: Oracle LAYA 04:15 UTC, attention-daily 04:45 UTC, migrate-direct OOS 01:20 UTC, healthcheck every 5 minutes. Do not duplicate those by restarting them from here.

## What this repo now holds

- Store `docs/*.md` under `docs/` and `docs/research/`
- Store `internal/*.md` and the latency-curve JSON under `ARTIFACTS/lab/`
- Daily briefs under `ARTIFACTS/daily/`
- `notes.md` and `archived.md` under `docs/ops/`
- Two **text** patch files under `ARTIFACTS/patches/`. There were **no binary patch files** to skip.

## What this move could not capture

- **Running Cursor agents.** They are not in the repo. Close or ignore them after Helm has the PR.
- **Cursor Runtime Secret values.** Names are documented. Values were not in the store and must be re-entered under the neutral names.
- **The Cursor daily 05:00 UTC review** and the **one-shot OOS check around 2026-09-28 21:00Z**. Recreate them as cron, as above.
- **Inbox event logs** under the project store (`inbox/github_pull_request_pr/**/*.jsonl`). Agent transcripts, not lab evidence. Left out on purpose.
- **Live host state newer than the 2026-09-27 notes.** This checklist does not SSH. `runner-status.json`, credit counters, and sealed hours move after the notes. Read them on the host when you need a newer number.
- **`/opt/miscusi` on `mal-fast-0`.** Unrelated app. Not part of MAL. Do not migrate it and do not touch it.
