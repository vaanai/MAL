#!/usr/bin/env bash
# Paper/monitor stub: SSHable agents run this on mal-core-0.
# Checks hostname, data-volume disk, Postgres localhost ping, JSONL dir writable.
# Writes JSONL + latest JSON under /var/lib/mal/logs. No secrets.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
LOG_DIR="${MAL_LOG_DIR:-${MAL_ROOT}/logs}"
JSONL_DIR="${MAL_JSONL_DIR:-${MAL_ROOT}/sealed/jsonl}"
mkdir -p "${LOG_DIR}" "${JSONL_DIR}"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
host="$(hostname 2>/dev/null || echo UNKNOWN)"
ok=true

disk_json="null"
if df_line="$(df -P "${MAL_ROOT}" 2>/dev/null | awk 'NR==2 {print}')"; then
  # filesystem 1024-blocks used available capacity mount
  disk_json="$(awk -v line="${df_line}" 'BEGIN {
    n = split(line, a, /[[:space:]]+/)
    fs=a[1]; blocks=a[2]; used=a[3]; avail=a[4]; cap=a[5]; mnt=a[6]
    gsub(/%/, "", cap)
    printf "{\"filesystem\":\"%s\",\"blocks_1k\":%s,\"used_1k\":%s,\"avail_1k\":%s,\"use_pct\":%s,\"mount\":\"%s\"}", fs, blocks, used, avail, cap, mnt
  }')"
else
  ok=false
fi

pg_host="unknown"
if command -v pg_isready >/dev/null 2>&1; then
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    pg_host="accepting"
  else
    pg_host="not_accepting"
    ok=false
  fi
else
  pg_host="pg_isready_missing"
  ok=false
fi

pg_peer="skipped"
if command -v psql >/dev/null 2>&1; then
  if psql -d meme_core -Atqc "SELECT 1" >/dev/null 2>&1; then
    pg_peer="ok"
  else
    pg_peer="fail"
    # Peer role ubuntu is convenience; localhost ping is the gate.
  fi
fi

jsonl_writable=false
probe="${JSONL_DIR}/.health-write-probe.$$"
if (umask 022; : > "${probe}") 2>/dev/null; then
  rm -f "${probe}"
  jsonl_writable=true
else
  ok=false
fi

if [[ "${ok}" == true ]]; then status="ok"; else status="fail"; fi

python3 - "${LOG_DIR}" "${ts}" "${host}" "${status}" "${disk_json}" "${pg_host}" "${pg_peer}" "${jsonl_writable}" "${JSONL_DIR}" <<'PY'
import json, os, sys
log_dir, ts, host, status, disk_raw, pg_host, pg_peer, jsonl_w, jsonl_dir = sys.argv[1:]
try:
    disk = json.loads(disk_raw)
except json.JSONDecodeError:
    disk = None
rec = {
    "schema_version": "mal_health_v0",
    "type": "host_health",
    "ts": ts,
    "hostname": host,
    "status": status,
    "paper_only": True,
    "disk": disk,
    "postgres": {"localhost_5432": pg_host, "peer_select": pg_peer},
    "jsonl": {"dir": jsonl_dir, "writable": jsonl_w == "true"},
}
line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False)
path = os.path.join(log_dir, "health.jsonl")
latest = os.path.join(log_dir, "health-latest.json")
with open(path, "a", encoding="utf-8") as fh:
    fh.write(line + "\n")
with open(latest, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
print(line)
sys.exit(0 if status == "ok" else 1)
PY
