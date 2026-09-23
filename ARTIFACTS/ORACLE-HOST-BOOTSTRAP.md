# mal-core-0 host bootstrap (paper-only)

On-host copy lives at **`/var/lib/mal/eng/BOOTSTRAP.md`**. No secrets, no DB passwords, no private keys.

| | |
| --- | --- |
| **As-of** | 2026-09-23 |
| **Host** | Oracle Always Free `mal-core-0` (Phoenix AD-1, 2 OCPU / 12 GB, aarch64) |
| **uname -n** | `mal-core-vnic` |
| **Access** | DEC-011 **LIVE** — Cloudflare Access Service Auth + Cursor Runtime Secrets |
| **Smoke** | 2026-09-23 Cursor Cloud Agent → `cloudflared` 2026.9.1 Access TCP → SSH `ubuntu@127.0.0.1:2222` |
| **Mode** | **Paper only.** No live trading. No wallet/trading keys. No X API keys. |

North star (unchanged): **3 intelligence layers** → precompute → **LAYA** → **risk gate** → exec deferred. Sealed **JSONL** = provenance/event spine. Postgres = **ops/state**.

---

## Layout created under `/var/lib/mal`

Data volume `mal-core-data` (150 GB, ext4 @ `/var/lib/mal`). PostgreSQL data dir was already present and is **not** moved.

| Path | Owner | Purpose |
| --- | --- | --- |
| `/var/lib/mal/postgresql/` | `postgres` | Existing PG 16 data (`…/16/main`). Do not chown. |
| `/var/lib/mal/sealed/jsonl/` | `ubuntu` | Append-only observe JSONL (`observe-YYYY-MM-DD.jsonl`) |
| `/var/lib/mal/paper/` | `ubuntu` | Paper books / marks (stubs) |
| `/var/lib/mal/logs/` | `ubuntu` | Health JSONL + observe service logs |
| `/var/lib/mal/run/` | `ubuntu` | Runtime / pid placeholders |
| `/var/lib/mal/backups/` | `ubuntu` | Future pg dumps (not yet scheduled) |
| `/var/lib/mal/eng/` | `ubuntu` | This note, health/observe/apply scripts |

Repo checkout + venv: **`/home/ubuntu/mal`** (observe client). Not a trading stack.

---

## How Cursor reconnects (no secrets)

1. Runtime Secrets on the **Cursor Cloud Agent** (not on this host, not in git):
   - `CURSOR_CLOUD_AGENT_SSH_KEY` — base64 of dedicated agent OpenSSH PEM
   - `CLOUDFLARE_ACCESS_CLIENT_ID` / `CLOUDFLARE_ACCESS_CLIENT_SECRET` — Access service token
2. On the agent: download **cloudflared 2026.9.1** (SHA256-pinned) to `/tmp` → `access tcp --hostname ssh.tradervaan.com --url 127.0.0.1:2222` with Access Service Auth via `TUNNEL_SERVICE_TOKEN_*` env (not `--id/--secret` on argv).
3. Decode the key to a **mode 600** tempfile. Expect fingerprint `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`.
4. `ssh -i <tempkey> -p 2222 -o IdentitiesOnly=yes ubuntu@127.0.0.1`
5. Expect `hostname` = `mal-core-vnic`, `whoami` = `ubuntu`, `uname -m` = `aarch64`.
6. **Cleanup:** delete tempfile key; stop Access TCP; remove temp `cloudflared` binary.

Full runbook: repo `tools/oracle_ssh_smoke.md`. Helper: `scripts/mal-core/agent-ssh.sh`.

This hostname `ssh.tradervaan.com` is **Access-gated**. It is **not** public SSH. Owner break-glass remains home-IP `:22`. Do **not** weaken Cloudflare Access/Tunnel. Do **not** publish Postgres.

---

## Postgres (ops/state stubs)

- Version **16.15**, DB **`meme_core`**, role **`mal_app`**, **localhost only**.
- Migration: `sql/meme_core/001_ops_state_stubs.sql` (tokens, wallets, relationships, paper_positions, ops_meta, schema_migrations).
- Applied as `sudo -u postgres psql` via **stdin** (the `postgres` OS user cannot read `/home/ubuntu`):

  ```bash
  sudo -u postgres psql -d meme_core -v ON_ERROR_STOP=1 < sql/meme_core/001_ops_state_stubs.sql
  ```

  **No password in git.** If that path dies: `BLOCKED:needs_db_password` (file `SCHEMA-BLOCKED.txt` in this directory).
- Convenience: Postgres role **`ubuntu`** is **LOGIN + SELECT-only** via local **peer** (narrower than ubuntu’s existing NOPASSWD sudo). App writes stay `mal_app`.

---

## Sealed JSONL ingest

- Output dir: `/var/lib/mal/sealed/jsonl`
- Run script: `/var/lib/mal/eng/observe-jsonl.sh` (or `scripts/mal-core/observe-jsonl.sh`)
- User systemd unit: `~/.config/systemd/user/mal-observe.service` (`mal-observe.service`)
  - `systemctl --user status mal-observe`
  - Linger enabled so the unit can survive SSH logout (`loginctl enable-linger ubuntu`)
- Client: existing `python -m observe` (PumpPortal free WS: `subscribeNewToken` + `subscribeMigration`). **No paid RPC. No API key.**

SSH non-interactive sessions: `export XDG_RUNTIME_DIR=/run/user/$(id -u)` before `systemctl --user`.

---

## Health

```bash
/var/lib/mal/eng/healthcheck.sh
```

Writes `/var/lib/mal/logs/health.jsonl` and `health-latest.json`. Checks: hostname, disk on `/var/lib/mal`, `pg_isready -h 127.0.0.1 -p 5432`, JSONL dir writable.

---

## Hard fences (unchanged)

- No live trading, no wallet/trading private keys, no X API keys on host
- No paid infra, no public app ports, no exposing Postgres
- Do not weaken Cloudflare Access/Tunnel
- Do not optimize EXP-002c to hit gates; no EXP promotion
- Do not print secrets or private key material
- Do not put DB passwords in git
