# Oracle host bootstrap checklist (paper-only)

Use with [ORACLE-HOST-BOOTSTRAP.md](ORACLE-HOST-BOOTSTRAP.md) and [tools/oracle_ssh_smoke.md](../tools/oracle_ssh_smoke.md). Tick in PRs / EDL — **no secrets**.

## Access (DEC-011)

- [x] Runtime Secrets present on Cursor Cloud Agent (`CURSOR_CLOUD_AGENT_SSH_KEY`, `CLOUDFLARE_ACCESS_CLIENT_ID`, `CLOUDFLARE_ACCESS_CLIENT_SECRET`) — **values never in git**
- [x] `cloudflared` 2026.9.1 Access TCP → `127.0.0.1:2222` with `--id/--secret`
- [x] SSH `ubuntu@127.0.0.1:2222`; hostname `mal-core-vnic`; `aarch64`; smoke 2026-09-23
- [x] Deploy-key fingerprint `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`
- [x] Temp key + temp `cloudflared` cleaned after the session
- [ ] Helm morning review of LIVE access + this bootstrap

## Host layout

- [x] `/var/lib/mal/{sealed/jsonl,paper,logs,run,backups,eng}` exist; `ubuntu`-owned
- [x] `/var/lib/mal/postgresql/` left `postgres`-owned
- [x] `/var/lib/mal/eng/BOOTSTRAP.md` present (no secrets)

## Postgres

- [x] `001_ops_state_stubs.sql` applied via `sudo -u postgres psql -d meme_core` **or** documented `BLOCKED:needs_db_password`
- [x] `pg_isready -h 127.0.0.1 -p 5432` accepting
- [x] Still localhost-only; no public/tunneled DB hostname

## JSONL + health

- [x] Observe venv at `/home/ubuntu/mal/.venv` with `requirements-observe.txt`
- [x] User unit `mal-observe.service` installed (started only if venv works)
- [x] `/var/lib/mal/eng/healthcheck.sh` exits 0; log under `/var/lib/mal/logs`

## Fences

- [ ] No trading/X keys on host
- [ ] No DB password in git
- [ ] No public ports added
- [ ] EXP-002c **not** promoted / not optimized-to-gate
