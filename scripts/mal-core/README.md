# mal-core-0 scripts (paper-only)

Host bootstrap helpers. **No secrets in this tree.**

| Script | Where it runs | Purpose |
| --- | --- | --- |
| [agent-ssh.sh](agent-ssh.sh) | Cursor Cloud Agent | Access TCP + SSH; SHA256-pin cloudflared; token via env; pinned host keys |
| [mal-core-known_hosts](mal-core-known_hosts) | Cursor Cloud Agent | SSH host keys for `127.0.0.1:2222` (`StrictHostKeyChecking=yes`) |
| [bootstrap_mal_core.sh](bootstrap_mal_core.sh) | `mal-core-0` as `ubuntu` | Dirs, schema, venv, user unit, health |
| [apply-schema.sh](apply-schema.sh) | host | `001_ops_state_stubs.sql` via postgres peer |
| [healthcheck.sh](healthcheck.sh) | host | hostname / disk / pg_isready / JSONL writable |
| [observe-jsonl.sh](observe-jsonl.sh) | host | `python -m observe` → `/var/lib/mal/sealed/jsonl` |
| [mal-observe.service](mal-observe.service) | host (systemd --user) | Same observe path, linger-enabled |

Runbook: [tools/oracle_ssh_smoke.md](../../tools/oracle_ssh_smoke.md). On-host note: `/var/lib/mal/eng/BOOTSTRAP.md`.
