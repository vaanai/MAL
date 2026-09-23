# Cursor → mal-core-0 SSH smoke (DEC-011 LIVE)

Operator runbook for **Cursor Cloud agents**. No secret values, no private key material, no DB passwords.

| | |
| --- | --- |
| **As-of** | 2026-09-23 |
| **Status** | **LIVE** — smoke passed 2026-09-23 |
| **Remote hostname** | `mal-core-vnic` |
| **SSH user** | `ubuntu` (NOPASSWD sudo) |
| **Arch** | `aarch64` |
| **Paper-only** | Yes. No trading/wallet keys. No X API keys on host. |

## Path

```
Cursor Cloud Agent
  → cloudflared access tcp (CF Access Service Auth)
  → 127.0.0.1:2222
  → SSH ubuntu@127.0.0.1
  → mal-core-0 (mal-core-vnic)
```

Access TCP hostname (Cloudflare Access **gated**, **not** a public `:22` listener): `ssh.tradervaan.com`.

Owner break-glass remains home-IP `:22` + owner personal key. Agents **must not** use the owner personal key. **Do not** open `:22` to the world. **Do not** expose Postgres (including as a tunnel hostname).

## Runtime Secrets (names only)

Set in the Cursor Cloud Agent environment. **Never** commit values, never print them, never pass them on a command line you will paste into git/chat.

| Secret name | Meaning |
| --- | --- |
| `CURSOR_CLOUD_AGENT_SSH_KEY` | Base64 of the dedicated agent OpenSSH private PEM (not the owner personal key) |
| `CLOUDFLARE_ACCESS_CLIENT_ID` | Cloudflare Access **service token** id |
| `CLOUDFLARE_ACCESS_CLIENT_SECRET` | Cloudflare Access **service token** secret |

Expected deploy-key fingerprint (identity check, not a secret):

`SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`

Lab placeholder name: **`mal-cursor`**. Key comment observed at smoke: `cursor-cloud-agent` (ED25519).

## Procedure (agent hop is x86_64; Oracle is aarch64)

1. Download **cloudflared 2026.9.1** to `/tmp` (Linux amd64 on Cursor Cloud; do not install a daemon on the agent).
2. Base64-decode `CURSOR_CLOUD_AGENT_SSH_KEY` to a **mode 600** tempfile. Confirm fingerprint with `ssh-keygen -lf`. **Do not cat the key.**
3. Start Access TCP:

   ```bash
   /tmp/cloudflared access tcp \
     --hostname ssh.tradervaan.com \
     --url 127.0.0.1:2222 \
     --id "$CLOUDFLARE_ACCESS_CLIENT_ID" \
     --secret "$CLOUDFLARE_ACCESS_CLIENT_SECRET"
   ```

4. SSH:

   ```bash
   ssh -i /path/to/temp-key -p 2222 \
     -o IdentitiesOnly=yes -o BatchMode=yes \
     ubuntu@127.0.0.1
   ```

5. Smoke expect: `hostname` → `mal-core-vnic`; `whoami` → `ubuntu`; `uname -m` → `aarch64`. Marker used 2026-09-23: `CURSOR_ORACLE_SSH_OK`.

6. **Cleanup after every remote session:** delete the tempfile key; stop `cloudflared access tcp`; remove the temp binary in `/tmp` if you downloaded one. Do not leave key material in `/tmp`.

Helper (same rules): [`scripts/mal-core/agent-ssh.sh`](../scripts/mal-core/agent-ssh.sh).

## On-host after login

- Health: `/var/lib/mal/eng/healthcheck.sh` (also in this repo: `scripts/mal-core/healthcheck.sh`)
- Layout + reconnect (no secrets): `/var/lib/mal/eng/BOOTSTRAP.md`
- Postgres: localhost only (`meme_core` / `mal_app`). Apply schema via `sudo -u postgres psql` — **never** guess `mal_app` password. If blocked: `BLOCKED:needs_db_password`.

## Failures to escalate (do not workaround)

- Access policy no longer Service Auth for the service token
- Fingerprint mismatch
- Proposal to publish Postgres or open `:22`
- Missing Runtime Secrets (owner / Helm — not a code guess)
