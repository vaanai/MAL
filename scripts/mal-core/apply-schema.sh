#!/usr/bin/env bash
# Apply meme_core ops/state stub migrations via postgres peer (no password).
# If this cannot run, record BLOCKED:needs_db_password — do not guess.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SQL="${MAL_SCHEMA_SQL:-${ROOT}/sql/meme_core/001_ops_state_stubs.sql}"

if [[ ! -f "${SQL}" ]]; then
  echo "apply-schema: missing ${SQL}" >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "apply-schema: psql not on PATH" >&2
  exit 1
fi

if sudo -n -u postgres psql -d meme_core -v ON_ERROR_STOP=1 -f "${SQL}"; then
  echo "apply-schema: applied ${SQL} as postgres (peer) on meme_core"
  exit 0
fi

echo "apply-schema: BLOCKED:needs_db_password (postgres sudo peer failed; will not guess mal_app password)" >&2
exit 2
