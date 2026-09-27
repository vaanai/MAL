# SSH to mal-core-0 and mal-fast-0

Tool-neutral runbook. No secret values, no private key material, no DB passwords. Tests and `--dry-run` do not open a connection.

| | |
| --- | --- |
| **As-of** | 2026-09-27 |
| **Status** | Access path **LIVE** since 2026-09-23 (Oracle). Fast hostname added with `mal-fast-0`. |
| **Paper-only** | Yes. No trading, wallet, or X keys on either host. |

## Hosts

| `--host` | Access hostname | Machine | Host key (ED25519) |
| --- | --- | --- | --- |
| `core` | `ssh.tradervaan.com` | `mal-core-0` (`mal-core-vnic`, aarch64) | `SHA256:Hy68mL6wisJ2t+z/JDcSNATPlyA8sudv4Za7A2Y8Ejs` |
| `fast` | `ssh-fast.tradervaan.com` | `mal-fast-0` (x86_64) | `SHA256:q5o6Bf1Vo83LtQhwdSQfIL5D4mkRMjELjaL//Y8XXzQ` |

These hostnames are Cloudflare Access gates. They are not a public `:22` listener. **Stop on a host-key mismatch.** Do not use `StrictHostKeyChecking=no`.

Postgres stays on localhost on Oracle. Do not publish it. Do not open port 22 to the world. `/opt/miscusi` on the fast box is unrelated. Do not touch it.

## Secrets (names only)

Set in the environment. Never commit the values, never print them, never pass them as CLI arguments.

| Neutral name | Legacy name still accepted | Meaning |
| --- | --- | --- |
| `MAL_SSH_KEY_B64` | `CURSOR_CLOUD_AGENT_SSH_KEY` | Base64 of the dedicated agent OpenSSH private key |
| `CF_ACCESS_CLIENT_ID` | `CLOUDFLARE_ACCESS_CLIENT_ID` | Access service-token id |
| `CF_ACCESS_CLIENT_SECRET` | `CLOUDFLARE_ACCESS_CLIENT_SECRET` | Access service-token secret |

If both names in a row are set, the neutral name wins. `cloudflared` still reads the legacy Cloudflare names. The helper exports them from whichever name you set. It does not echo them.

Optional client-key pin: `MAL_SSH_KEY_FINGERPRINT` (legacy: `MAL_CURSOR_KEY_FINGERPRINT`). Unset means any key that decodes is accepted, and the **host** key pin is the stop. The old Cursor deploy key fingerprint was `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`. A new key will not match it.

## Agent helper

```bash
scripts/mal-core/agent-ssh.sh --host core --dry-run
scripts/mal-core/agent-ssh.sh --host fast --dry-run
scripts/mal-core/agent-ssh.sh --host core hostname
scripts/mal-core/agent-ssh.sh --host fast -- systemctl --user is-active mal-fast-create.service
```

`--dry-run` prints host, access hostname, pinned fingerprint, and which **names** resolved. It does not connect and it does not decode the key.

Smoke expect:

- core: `hostname` → `mal-core-vnic`, `whoami` → `ubuntu`, `uname -m` → `aarch64`
- fast: `hostname` → `mal-fast-0`, `whoami` → `ubuntu`, `uname -m` → `x86_64`

The helper scans the host key through the local Access tunnel and exits before SSH if the SHA256 fingerprint is not the pin for that host.

## Always-on machine (`~/.ssh/config`)

Print the snippet:

```bash
scripts/mal-core/agent-ssh.sh --ssh-config
```

Shape:

```
Host mal-core ssh.tradervaan.com
  HostName ssh.tradervaan.com
  User ubuntu
  ProxyCommand cloudflared access ssh --hostname %h
  IdentitiesOnly yes
  IdentityFile ~/.ssh/mal_agent

Host mal-fast ssh-fast.tradervaan.com
  HostName ssh-fast.tradervaan.com
  User ubuntu
  ProxyCommand cloudflared access ssh --hostname %h
  IdentitiesOnly yes
  IdentityFile ~/.ssh/mal_agent
```

In the shell profile, map neutral token names onto the names `cloudflared` reads:

```bash
export CLOUDFLARE_ACCESS_CLIENT_ID="${CF_ACCESS_CLIENT_ID:-$CLOUDFLARE_ACCESS_CLIENT_ID}"
export CLOUDFLARE_ACCESS_CLIENT_SECRET="${CF_ACCESS_CLIENT_SECRET:-$CLOUDFLARE_ACCESS_CLIENT_SECRET}"
```

After the first connection, `ssh-keygen -lf` on the known_hosts entry must match the pin in the table above. If it does not, delete the known_hosts line and stop. Do not accept the new key.

## On the Oracle host after login

- Health: `/var/lib/mal/eng/healthcheck.sh` (repo: `scripts/mal-core/healthcheck.sh`)
- Layout: `/var/lib/mal/eng/BOOTSTRAP.md`
- Postgres: localhost only. `sudo -u postgres psql` if you must. Never guess the `mal_app` password.

The fast box has no Postgres for MAL and no copy of that healthcheck timer. See [docs/HOSTS.md](../docs/HOSTS.md).

## Failures to escalate

- Host-key fingerprint mismatch
- Access policy no longer accepts the service token
- Missing secrets (owner / Helm — not a guessed value)
- Anyone proposing to publish Postgres or open `:22`
