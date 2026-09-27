#!/usr/bin/env bash
# MAL daily review, headless Claude Code, read-only.
#
# Gathers a small set of read-only facts about mal-fast-0 (this box) and
# about mal-core-0 (Oracle, reachable read-only over plain `ssh mal-core-0`
# via Cloudflare Access with a dedicated key for user claude) directly in
# this script — not via the LLM's own tool calls — then hands them to a
# headless `claude -p`
# session that only has Read/Grep/Glob and Write/Edit scoped to its own
# report directory and the shared INDEX.md. Never runs a restart, never
# reads a secret/.env file, never pushes to git.
#
# Usage: daily-review.sh [--test]
#   --test   write to reports/daily-review/TEST-<date>.md instead of
#            reports/daily-review/<date>.md, for a manual dry run with the
#            timer still disabled.
set -euo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

TEST_MODE=0
for arg in "$@"; do
  case "${arg}" in
    --test) TEST_MODE=1 ;;
    *) echo "daily-review.sh: unknown argument ${arg}" >&2; exit 2 ;;
  esac
done

UTC_DATE="$(date -u +%F)"
UTC_NOW="$(date -u +%FT%TZ)"
REPORTS_ROOT="/home/claude/reports"
REPORT_DIR="${REPORTS_ROOT}/daily-review"
INDEX_FILE="${REPORTS_ROOT}/INDEX.md"
mkdir -p "${REPORT_DIR}"
touch "${INDEX_FILE}"

if [[ "${TEST_MODE}" == "1" ]]; then
  STAMP="TEST-${UTC_DATE}"
else
  STAMP="${UTC_DATE}"
fi
REPORT_FILE="${REPORT_DIR}/${STAMP}.md"
LOG_FILE="${REPORT_DIR}/${STAMP}.log"
FACTS_FILE="${REPORT_DIR}/${STAMP}.facts.txt"
PROMPT_FILE="${REPORT_DIR}/${STAMP}.prompt.txt"

# Everything from here on is logged to the report dir, not just the journal.
exec >"${LOG_FILE}" 2>&1

echo "daily-review.sh starting at ${UTC_NOW} (test_mode=${TEST_MODE})"

UBUNTU_UID="$(id -u ubuntu 2>/dev/null || echo 1000)"

# --- Gather facts (read-only) -------------------------------------------

{
  echo "Run date (UTC): ${UTC_NOW}"
  echo "Test mode: ${TEST_MODE}"
  echo "Your working directory for this session: ${REPORTS_ROOT}"
  echo "Report file to write (relative to your working directory, use exactly this string with Write): daily-review/${STAMP}.md"
  echo "Index file to append to (relative to your working directory, use exactly this string with Edit): INDEX.md"
  echo "(Do not use absolute paths for either file — this session's write permission is scoped to these two relative paths only.)"
  echo
  echo "### loginctl show-user claude -p Linger"
  loginctl show-user claude -p Linger 2>&1 || echo "(loginctl check failed)"
  echo

  echo "### mal-fast-0 unit status (systemctl --user, as ubuntu)"
  for unit in mal-fast-create mal-fast-public-logs mal-fast-pre-create mal-fast-backfill mal-fast-oos-score; do
    echo "-- ${unit}.service --"
    sudo -n -u ubuntu env "XDG_RUNTIME_DIR=/run/user/${UBUNTU_UID}" \
      systemctl --user show "${unit}.service" \
      -p ActiveState -p SubState -p NRestarts -p ActiveEnterTimestamp 2>&1 \
      || echo "(status check failed for ${unit})"
  done
  echo

  echo "### /var/lib/mal/paper/migrate-direct-oos-fast/report.json"
  cat /var/lib/mal/paper/migrate-direct-oos-fast/report.json 2>&1 \
    || echo "(report.json not readable)"
  echo

  echo "### Sealed data freshness (mal-fast-0)"
  for d in fast-create fast-public fast-pre-create; do
    echo "-- sealed/${d} --"
    ls -la --time-style=full-iso "/var/lib/mal/sealed/${d}/" 2>&1 \
      || echo "(listing failed for ${d})"
  done
  echo

  echo "### Disk free"
  df -h /var/lib/mal / 2>&1 || echo "(df failed)"
  echo

  echo "### Backfill progress (/var/lib/mal/backfill-fast/checkpoint.json)"
  cat /var/lib/mal/backfill-fast/checkpoint.json 2>&1 \
    || echo "(checkpoint.json not readable)"
  echo "Credit cap: +2,000,000 (hard cap for this backward run, see docs/HOSTS.md)"
  echo "Hour count on disk:"
  ls /var/lib/mal/backfill-fast/stats-*.json 2>/dev/null | wc -l || echo 0
  echo

  echo "### mal-core-0 (Oracle) reachability"
  echo "command: ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 true"
  set +e
  timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 true </dev/null >/dev/null 2>&1
  ORACLE_RC=$?
  set -e
  echo "exit_code: ${ORACLE_RC}"
  if [[ "${ORACLE_RC}" != "0" ]]; then
    echo "interpretation: Oracle did NOT answer this time. Oracle is expected to be reachable read-only (Cloudflare Access, own key, user claude) — an unreachable result here is a real anomaly, not the old no-creds gap. Treat as STATUS: ATTENTION."
  else
    echo "interpretation: Oracle answered. Gathering read-only facts below."
    echo
    echo "### Oracle facts (single ssh session, read-only, sudo-restricted to status/list-timers/list-units/show/cat)"
    timeout 45 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 bash -s </dev/null <<'REMOTE' 2>&1
set +e
echo "-- ubuntu systemd --user overall (Failed count) --"
sudo -n systemctl --user -M ubuntu@ status
echo
echo "-- LAYA / attention-daily timers (expected: disabled until 2026-10-05) --"
sudo -n systemctl --user -M ubuntu@ status mal-laya-v0.timer mal-attention-daily.timer mal-laya-v0.service mal-attention-daily.service
echo
echo "-- migrate-direct-oos timer + last run result --"
sudo -n systemctl --user -M ubuntu@ status mal-migrate-direct-oos.timer mal-migrate-direct-oos.service
echo
echo "-- healthcheck timer + service --"
sudo -n systemctl --user -M ubuntu@ status mal-healthcheck.timer mal-healthcheck.service
echo
echo "-- other known Oracle units (observe/tape/attention/funding-graph/forward-paper/pump-backfill) --"
sudo -n systemctl --user -M ubuntu@ status mal-observe.service mal-trade-tape.service mal-attention.service mal-funding-graph.service mal-forward-paper.service mal-pump-backfill.service mal-pump-backfill-resume.service
echo
echo "-- ubuntu list-timers --all --"
sudo -n systemctl --user -M ubuntu@ list-timers --all
echo
echo "-- root/system-level failed units --"
sudo -n systemctl list-units --failed --all
echo
echo "-- /var/lib/mal/logs/health-latest.json --"
cat /var/lib/mal/logs/health-latest.json 2>&1
echo
echo "-- /var/lib/mal/paper/forward-paper/runner-status.json (forward-paper lag) --"
cat /var/lib/mal/paper/forward-paper/runner-status.json 2>&1
echo
echo "-- disk --"
df -h /var/lib/mal / 2>&1
REMOTE
    SSH_RC=$?
    echo "(remote fact-gathering ssh session exit code: ${SSH_RC})"
  fi
} >"${FACTS_FILE}" 2>&1

echo "facts gathered at ${FACTS_FILE}:"
cat "${FACTS_FILE}"

# --- Render the prompt and run headless Claude ----------------------------

{
  cat "${ROOT}/ops/claude-schedules/daily-review.prompt.md"
  echo
  echo "## FACTS"
  echo
  echo '```'
  cat "${FACTS_FILE}"
  echo '```'
} > "${PROMPT_FILE}"

echo "invoking headless claude, prompt file ${PROMPT_FILE}"

# IMPORTANT: run from /home/claude/reports, not the MAL checkout. Claude
# Code's file-write sandbox only trusts the *working directory* by default;
# an --add-dir grants Read there but --setting-sources "" (below, needed to
# stop the MAL repo's own permissive .claude/settings.json "allow": ["Write",
# "Edit", ...] from leaking into this headless session) also strips the
# trust that would let Write/Edit reach an added directory. Keeping cwd at
# the shared reports root, with tight relative Edit() patterns, is what
# actually confines this job to its own report file plus the shared index —
# verified by hand: absolute-path allow patterns and cross-job/cross-repo
# writes were denied in manual testing, cwd-relative ones were not.
set +e
( cd "${REPORTS_ROOT}" && timeout 1200 /home/claude/.local/bin/claude \
  -p "$(cat "${PROMPT_FILE}")" \
  --model claude-sonnet-5 \
  --setting-sources "" \
  --add-dir "${ROOT}" \
  --allowedTools "Read" "Edit(./daily-review/${STAMP}.md)" "Edit(./INDEX.md)" \
  --disallowedTools "Read(**/.env)" "Read(**/.env.*)" "Read(**/helius.env)" "Read(**/*credentials*)" \
  --permission-prompts none \
  --max-budget-usd 2 \
  --no-session-persistence \
  --output-format text )
CLAUDE_RC=$?
set -e

echo "claude exited ${CLAUDE_RC}"

if [[ ! -s "${REPORT_FILE}" ]]; then
  echo "daily-review.sh: WARNING — claude did not write ${REPORT_FILE}" >&2
  {
    echo "STATUS: ATTENTION"
    echo
    echo "## Summary"
    echo
    echo "The headless claude session (exit code ${CLAUDE_RC}) did not produce"
    echo "a report file at the expected path. See ${LOG_FILE} for the session"
    echo "transcript and ${FACTS_FILE} for the gathered facts."
  } > "${REPORT_FILE}"
  echo "- ${UTC_DATE} daily-review: STATUS=ATTENTION — claude session did not write a report (exit ${CLAUDE_RC}), see ${LOG_FILE}" >> "${INDEX_FILE}"
  exit 1
fi

echo "daily-review.sh done, report at ${REPORT_FILE}"
exit 0
