# `meme_core` schema (ops/state)

PostgreSQL on `mal-core-0` is the **ops/state** layer (tokens, wallets, relationships, paper, ops metadata). **Sealed JSONL** remains the provenance / EXP spine. Do **not** migrate provenance SoT into Postgres.

| | |
| --- | --- |
| Database | `meme_core` |
| App role | `mal_app` (non-superuser; cannot create databases or roles) |
| Bind | localhost / Unix socket only |
| Password | Owner-held. **Never** in git, docs, chat, or this tree |

## Apply (preferred: no password)

On `mal-core-0` as `ubuntu` (NOPASSWD sudo → `postgres` peer):

```bash
sudo -u postgres psql -d meme_core -v ON_ERROR_STOP=1 < sql/meme_core/001_ops_state_stubs.sql
```

Or: `scripts/mal-core/apply-schema.sh` after checkout.

If sudo/postgres peer is unavailable **and** the `mal_app` password is not in the agent environment, **stop**. Record `BLOCKED:needs_db_password`. Do not guess.

## Migrations

| File | Intent |
| --- | --- |
| [001_ops_state_stubs.sql](001_ops_state_stubs.sql) | Stub tables + `ubuntu` SELECT-only peer role for inspect/health |

Paper only. No live trading state.
