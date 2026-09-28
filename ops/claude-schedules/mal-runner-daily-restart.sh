#!/usr/bin/env bash
# MAL daily 00:00 UTC forward-paper runner restart (mal-core-0 / Oracle).
#
# Keeps the Oracle forward-paper runner's process lifetime to one UTC day,
# so each day is one process and the runner never approaches its 10G memory
# ceiling. The ONLY write action anywhere in this script is the single
# `systemctl --user -M ubuntu@ restart mal-forward-paper` call below —
# everything before and after it is a plain read-only ssh command, the same
# kind a human would run by hand. mal-core-0 is a read-only account for
# `claude`; this script does not touch ufw/sshd/cloudflared and does not
# read any .env/helius.env.
#
# Appends exactly one JSON line to /home/claude/reports/runner-restarts.jsonl
# per run, on success or failure, with fields:
#   restart_utc, pre {rss_kb, head_sha, lag_ms},
#   post {pid, start, rss_kb, lag_ms}, ok, error (only when ok=false).
#
# On any failure this still writes that line (best-effort fields, ok=false,
# a non-empty error string) and then exits non-zero, so a failed run is
# visible in the log file without ever leaving the report silently blank.
set -euo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

REPORTS_ROOT="/home/claude/reports"
REPORT_FILE="${REPORTS_ROOT}/runner-restarts.jsonl"
mkdir -p "${REPORTS_ROOT}"

RESTART_UTC="$(date -u +%FT%TZ)"
FWD_DIR="/var/lib/mal/paper/forward-paper"

echo "mal-runner-daily-restart.sh starting at ${RESTART_UTC}"

ERRORS=()

fail() {
  echo "mal-runner-daily-restart.sh: $1" >&2
  ERRORS+=("$1")
}

# --- Pre-restart state (read-only) ----------------------------------------

PRE_MEM_RAW="$(timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
  "tail -1 ${FWD_DIR}/mem-census.jsonl" 2>&1)" \
  || { fail "pre mem-census read failed: ${PRE_MEM_RAW}"; PRE_MEM_RAW=""; }

PRE_STATUS_RAW="$(timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
  "cat ${FWD_DIR}/runner-status.json" 2>&1)" \
  || { fail "pre runner-status read failed: ${PRE_STATUS_RAW}"; PRE_STATUS_RAW=""; }

# Note: a bare `git rev-parse HEAD` here fails with "dubious ownership"
# because /var/lib/mal/paper/forward-paper/src is owned by the `ubuntu`
# service account while `claude` is a separate read-only account on this
# host. `-c safe.directory=...` is a per-invocation override only — it does
# not write anything to Oracle (no `git config --global`, no state left
# behind) and keeps this a plain read.
PRE_HEAD_SHA="$(timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
  "git -c safe.directory=${FWD_DIR}/src -C ${FWD_DIR}/src rev-parse HEAD" 2>&1)" \
  || { fail "pre head sha read failed: ${PRE_HEAD_SHA}"; PRE_HEAD_SHA=""; }

PRE_RSS="$(printf '%s' "${PRE_MEM_RAW}" | jq -r '.rss_kb_proc // empty' 2>/dev/null || true)"
PRE_LAG="$(printf '%s' "${PRE_STATUS_RAW}" | jq -r '.lag_ms // empty' 2>/dev/null || true)"

echo "pre: rss_kb=${PRE_RSS:-unknown} head_sha=${PRE_HEAD_SHA:-unknown} lag_ms=${PRE_LAG:-unknown}"

# --- Restart: the only write action in this script -------------------------

RESTART_OK=1
if ! timeout 30 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
  "sudo -n systemctl --user -M ubuntu@ restart mal-forward-paper"; then
  fail "restart command failed"
  RESTART_OK=0
fi

echo "restart command issued, ok=${RESTART_OK}"

# --- Post-restart state (read-only, after a 90s settle) --------------------

sleep 90

POST_STATUS_RAW=""
POST_PS_RAW=""
if [[ "${RESTART_OK}" == "1" ]]; then
  POST_STATUS_RAW="$(timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
    "cat ${FWD_DIR}/runner-status.json" 2>&1)" \
    || { fail "post runner-status read failed: ${POST_STATUS_RAW}"; POST_STATUS_RAW=""; }

  # `-C python` alone (as in the plan) also matches four unrelated long-
  # running observe/tape/graph daemons on this box, so a bare `ps -C python`
  # can't tell which row is the runner. pgrep -f selects the forward-paper
  # PID first (still read-only), then ps reads exactly the four columns
  # (pid, lstart, etime, rss) for that one process.
  POST_PS_RAW="$(timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
    "PID=\$(pgrep -f 'tools.forward_paper serve' | head -1); ps -o pid,lstart,etime,rss -p \"\$PID\"" 2>&1)" \
    || { fail "post ps read failed: ${POST_PS_RAW}"; POST_PS_RAW=""; }
else
  fail "skipped post-restart reads because the restart command failed"
fi

POST_LAG="$(printf '%s' "${POST_STATUS_RAW}" | jq -r '.lag_ms // empty' 2>/dev/null || true)"

POST_PID=""
POST_START=""
POST_RSS=""
if [[ -n "${POST_PS_RAW}" ]]; then
  DATA_ROW="$(printf '%s\n' "${POST_PS_RAW}" | tail -n +2 | tail -1)"
  if [[ -n "${DATA_ROW}" ]]; then
    # Fixed column layout from `ps -o pid,lstart,etime,rss`: $1=pid,
    # $2..$6=lstart ("Dow Mon DD HH:MM:SS YYYY", 5 space-separated tokens),
    # $7=etime (unused here), $NF=rss.
    POST_PID="$(printf '%s\n' "${DATA_ROW}" | awk '{print $1}')"
    POST_RSS="$(printf '%s\n' "${DATA_ROW}" | awk '{print $NF}')"
    POST_START="$(printf '%s\n' "${DATA_ROW}" | awk '{print $2, $3, $4, $5, $6}')"
  else
    fail "post ps read returned no data row (pgrep may have found no forward-paper process)"
  fi
fi

echo "post: pid=${POST_PID:-unknown} start=${POST_START:-unknown} rss_kb=${POST_RSS:-unknown} lag_ms=${POST_LAG:-unknown}"

# --- Write the report line --------------------------------------------------

OK="true"
ERROR_STR=""
if [[ "${#ERRORS[@]}" -gt 0 ]]; then
  OK="false"
  ERROR_STR="$(IFS='; '; echo "${ERRORS[*]}")"
fi

jq -n \
  --arg restart_utc "${RESTART_UTC}" \
  --arg pre_rss "${PRE_RSS}" \
  --arg pre_head_sha "${PRE_HEAD_SHA}" \
  --arg pre_lag "${PRE_LAG}" \
  --arg post_pid "${POST_PID}" \
  --arg post_start "${POST_START}" \
  --arg post_rss "${POST_RSS}" \
  --arg post_lag "${POST_LAG}" \
  --argjson ok "${OK}" \
  --arg error "${ERROR_STR}" \
  '{
    restart_utc: $restart_utc,
    pre: {
      rss_kb: (if $pre_rss == "" then null else ($pre_rss | tonumber) end),
      head_sha: (if $pre_head_sha == "" then null else $pre_head_sha end),
      lag_ms: (if $pre_lag == "" then null else ($pre_lag | tonumber) end)
    },
    post: {
      pid: (if $post_pid == "" then null else ($post_pid | tonumber) end),
      start: (if $post_start == "" then null else $post_start end),
      rss_kb: (if $post_rss == "" then null else ($post_rss | tonumber) end),
      lag_ms: (if $post_lag == "" then null else ($post_lag | tonumber) end)
    },
    ok: $ok
  } + (if $error == "" then {} else { error: $error } end)' \
  >> "${REPORT_FILE}"

echo "mal-runner-daily-restart.sh done, appended to ${REPORT_FILE}, ok=${OK}"

if [[ "${OK}" == "true" ]]; then
  exit 0
else
  exit 1
fi
