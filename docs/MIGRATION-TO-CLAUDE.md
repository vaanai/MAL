# Migration to Claude

Repo and docs only. This checklist is what the owner or Helm must do **outside** the repository. Nothing here changes a host, a systemd unit, Cloudflare, or a secret.

The pull request that adds this file does not merge itself. At the time it was opened, Helm merged after review; since 2026-09-27 the Claude manager session on `mal-fast-0` merges instead ([DEC-013](../DEC/DEC-013-claude-manager-merges.md)).

## Secrets to create

Done 2026-09-27. Claude has full sudo on `mal-fast-0` and a read-only account on `mal-core-0` over `ssh mal-core-0` (Cloudflare Access, its own key and service token).

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

Done 2026-09-27, for the `mal-fast-0`-hosted manager session. Pick one place and install the secrets there:

- The owner's machine, with `~/.ssh/config` as in [tools/oracle_ssh_smoke.md](../tools/oracle_ssh_smoke.md) (`ProxyCommand cloudflared access ssh --hostname %h`), or
- A Claude Code session that has the three neutral env vars and `scripts/mal-core/agent-ssh.sh`.

`cloudflared` itself still reads `CLOUDFLARE_ACCESS_CLIENT_ID` and `CLOUDFLARE_ACCESS_CLIENT_SECRET`. On an always-on machine, map the neutral names onto those in the shell profile. The helper does that mapping internally. Do not install a second tunnel.

## Cloudflare

Reuse the existing Access service token and the existing hostnames:

- `ssh.tradervaan.com` → `mal-core-0`
- `ssh-fast.tradervaan.com` → `mal-fast-0`

Do not change tunnel ingress, Access policies, or `cloudflared` config on the hosts as part of this move.

## GitHub

Done 2026-09-27. Claude has permission to push branches and open pull requests on `vaanai/MAL`. Workers do not need permission to merge; the Claude manager session on `mal-fast-0` opens, reviews, and merges ([DEC-013](../DEC/DEC-013-claude-manager-merges.md)). Helm no longer merges MAL PRs.

Branch names from Claude: `claude/<topic>`. One topic each.

## Helius key

Already on the hosts. Never commit it.

| Host | Path |
| --- | --- |
| `mal-core-0` | `/var/lib/mal/backfill/helius.env` |
| `mal-fast-0` | `/var/lib/mal/fast-listener/helius.env` |

The fast backfill unit and the mint-authority listener load the fast path. Oracle backfill and the funding graph load the Oracle path. Mode 600. A missing file is a stop, not a guess.

Autoscaling is about 20M extra credits (owner setting, see [CLAUDE.md](../CLAUDE.md)). Report credits when a job uses that headroom.

## Cursor timers to recreate on mal-fast-0

Source note: [ARTIFACTS/lab/cursor-timers.md](../ARTIFACTS/lab/cursor-timers.md). Both timers live in the Cursor coordinator, not on either host. The owner cancels the Cursor timers only after these Claude jobs are verified.

Host timers are separate. `mal-laya-v0.timer` (04:15 UTC) and `mal-attention-daily.timer` (04:45 UTC) are **disabled until 2026-10-05**. The `mal-attention` poller stays up. The 01:20 `mal-migrate-direct-oos.timer` is **under test; may move to mal-fast-0**. Healthcheck stays every 5 minutes. Do not re-enable the disabled timers from this checklist, and do not start a second copy of a host timer.

### mal-daily-scoreboard-review

- Type: recurring cron. Schedule: `0 5 * * *` (05:00 UTC daily).
- Opened 2026-09-25T08:41:20Z. Expires 2026-10-02T08:41:20Z.
- Writes: `ARTIFACTS/daily/<date>.md`, then `docs/ops/notes.md`.
- Exact prompt:

> Daily MAL check: the LAYA v0 retrain/scoreboard timer runs at 04:15 UTC on mal-core. Delegate a short read-only worker (composer-2.5, fast off) to pull the latest daily scoreboard, tape health stats (trades/min, % creates covered, lag, disk), and wallet leaderboard summary into /cursor/stores/bc-82916b18-bbea-44a9-abce-601cba99bd97/internal/daily/<date>.md, then decide next steps and update notes.md. Message the user only if a signal meets the promotion criterion, something breaks, or a decision is needed.

- Claude equivalent on `mal-fast-0` (same prompt; write `ARTIFACTS/daily/<date>.md` and `docs/ops/notes.md`; also cover mal-fast-0 health). Subagent: `host-ops`, read-only.

```cron
0 5 * * * cd "$HOME/mal" && claude --agent host-ops -p 'Daily MAL check: the LAYA v0 retrain/scoreboard timer runs at 04:15 UTC on mal-core. Delegate a short read-only worker to pull the latest daily scoreboard, tape health stats (trades/min, % creates covered, lag, disk), and wallet leaderboard summary into ARTIFACTS/daily/<date>.md, then decide next steps and update docs/ops/notes.md. Also check mal-fast-0. Message the user only if a signal meets the promotion criterion, something breaks, or a decision is needed.'
```

### migrate-direct-oos-5day

- Type: one-shot. Opened 2026-09-27T13:50:06Z. Delay 112000 s, so it fires about **2026-09-28T20:57Z**.
- Reads `ARTIFACTS/lab/migrate-direct-oos.md`. Writes the keep-or-kill call into `docs/ops/notes.md` and messages the owner. No parameter changes.
- Exact prompt:

> Five out-of-sample days for the frozen migrate-direct cell (spec internal/migrate-direct-prereg.md, frozen 2026-09-27T13:06:36Z) were expected around 2026-09-28 20:00Z (Oracle covers 22 Sep, the fast box walks 21 Sep backward). Delegate one short composer-2.5 worker to read /cursor/stores/bc-82916b18-bbea-44a9-abce-601cba99bd97/internal/migrate-direct-oos.md, confirm with the host that the table is current, and report per fail model: n, distinct days, days positive, net mean, bootstrap 90% CI lower bound, ex-top-3, fill rate. No parameter changes. Then decide: if it passes the promotion rule under both fail models on out-of-sample days, message the owner proposing a tiny live calibration (capped hot wallet on the owner's machine, never on a server) and wait for approval. If it clearly fails, kill the cell in notes and message the owner briefly. If it is still under-sampled, let the backfill continue and set another check.

- Claude equivalent: one shot at 2026-09-28 20:57 UTC. Subagent: `quant-proof`. Read `ARTIFACTS/lab/migrate-direct-oos.md` and `ARTIFACTS/lab/migrate-direct-prereg.md`. Confirm the table on the host. Do not refit.

```cron
57 20 28 9 * cd "$HOME/mal" && claude --agent quant-proof -p 'Five out-of-sample days for the frozen migrate-direct cell (spec ARTIFACTS/lab/migrate-direct-prereg.md, frozen 2026-09-27T13:06:36Z) were expected around 2026-09-28 20:00Z. Read ARTIFACTS/lab/migrate-direct-oos.md, confirm with the host that the table is current, and report per fail model: n, distinct days, days positive, net mean, bootstrap 90% CI lower bound, ex-top-3, fill rate. No parameter changes. If it passes the promotion rule under both fail models, message the owner proposing a tiny live calibration (capped hot wallet on the owner machine, never on a server) and wait for approval. If it clearly fails, kill the cell in docs/ops/notes.md and message the owner briefly. If it is still under-sampled, let the backfill continue and set another check.'
```

Remove that cron line after it has run once. The 01:20 host oneshot is still under a lag test and may move to `mal-fast-0`; do not add a second scorer beside it.

## What this repo now holds

- Store `docs/*.md` under `docs/` and `docs/research/`
- Store `internal/*.md` and the latency-curve JSON under `ARTIFACTS/lab/`
- Daily briefs under `ARTIFACTS/daily/`
- `notes.md` and `archived.md` under `docs/ops/`
- Two **text** patch files under `ARTIFACTS/patches/`. There were **no binary patch files** to skip.

## What this move could not capture

- **Running Cursor agents.** They are not in the repo. Close or ignore them after Helm has the PR.
- **Cursor Runtime Secret values.** Names are documented. Values were not in the store and must be re-entered under the neutral names.
- **The Cursor daily 05:00 UTC review** and the **one-shot OOS check around 2026-09-28 20:57Z**. Recreate them on `mal-fast-0` as in the section above. The owner cancels the Cursor timers only after those Claude jobs are verified.
- **Inbox event logs** under the project store (`inbox/github_pull_request_pr/**/*.jsonl`). Agent transcripts, not lab evidence. Left out on purpose.
- **Live host state newer than the 2026-09-27 notes.** This checklist does not SSH. `runner-status.json`, credit counters, and sealed hours move after the notes. Read them on the host when you need a newer number.
- **`/opt/miscusi` on `mal-fast-0`.** Separate project, `vaanai/MiScusi`, led by the same Claude manager. Not part of MAL. Do not migrate it into this repo and do not modify it from MAL work; changes to it follow the MiScusi repo's own deploy docs.
