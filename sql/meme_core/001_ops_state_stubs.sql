-- MAL meme_core ops/state stubs (paper-only).
-- Provenance/event spine remains sealed JSONL — this is NOT a provenance replacement.
--
-- Apply on mal-core-0 without the mal_app password:
--   sudo -u postgres psql -d meme_core -v ON_ERROR_STOP=1 < sql/meme_core/001_ops_state_stubs.sql
-- If sudo/postgres peer is unavailable, do not guess a password: record BLOCKED:needs_db_password.

BEGIN;

-- Tables owned by mal_app (database owner). Superuser SET ROLE works; mal_app cannot CREATE ROLE.
SET ROLE mal_app;

CREATE TABLE IF NOT EXISTS schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes TEXT
);

-- Current token state (ops/lookup — not sealed ingest history).
CREATE TABLE IF NOT EXISTS tokens (
  mint TEXT PRIMARY KEY,
  creator TEXT,
  name TEXT,
  symbol TEXT,
  stage TEXT,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Wallet / entity profiles (Layer-2 stubs).
CREATE TABLE IF NOT EXISTS wallets (
  address TEXT PRIMARY KEY,
  label TEXT,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Relationship graph edges (wallets, creators, tokens, …). Exact graph impl may evolve.
CREATE TABLE IF NOT EXISTS relationships (
  id BIGSERIAL PRIMARY KEY,
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  rel_type TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  UNIQUE (src, dst, rel_type)
);

-- Paper-trading state (would-have / paper marks — not live capital).
CREATE TABLE IF NOT EXISTS paper_positions (
  id BIGSERIAL PRIMARY KEY,
  mint TEXT NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ,
  side TEXT NOT NULL DEFAULT 'paper_buy',
  qty NUMERIC,
  notes TEXT,
  extra JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Ops / runtime metadata (health stamps, schema version, flags).
CREATE TABLE IF NOT EXISTS ops_meta (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tokens_creator_idx ON tokens (creator);
CREATE INDEX IF NOT EXISTS tokens_last_seen_idx ON tokens (last_seen_at);
CREATE INDEX IF NOT EXISTS wallets_last_seen_idx ON wallets (last_seen_at);
CREATE INDEX IF NOT EXISTS relationships_src_idx ON relationships (src);
CREATE INDEX IF NOT EXISTS relationships_dst_idx ON relationships (dst);
CREATE INDEX IF NOT EXISTS relationships_rel_type_idx ON relationships (rel_type);
CREATE INDEX IF NOT EXISTS paper_positions_mint_idx ON paper_positions (mint);

INSERT INTO ops_meta (key, value)
VALUES (
  'bootstrap',
  jsonb_build_object(
    'version', 1,
    'as_of', '2026-09-23',
    'paper_only', true,
    'migration', '001_ops_state_stubs'
  )
)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = now();

INSERT INTO schema_migrations (id, notes)
VALUES ('001_ops_state_stubs', 'tokens/wallets/relationships/paper/ops stubs; paper-only')
ON CONFLICT (id) DO NOTHING;

RESET ROLE;

-- OS user ubuntu → Postgres role ubuntu via local peer (SELECT-only).
-- ubuntu already has NOPASSWD sudo (can become postgres); this path is narrower for health/inspect.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ubuntu') THEN
    CREATE ROLE ubuntu LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END
$$;

GRANT CONNECT ON DATABASE meme_core TO ubuntu;
GRANT USAGE ON SCHEMA public TO ubuntu;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ubuntu;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ubuntu;
ALTER DEFAULT PRIVILEGES FOR ROLE mal_app IN SCHEMA public
  GRANT SELECT ON TABLES TO ubuntu;
ALTER DEFAULT PRIVILEGES FOR ROLE mal_app IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ubuntu;

COMMIT;
