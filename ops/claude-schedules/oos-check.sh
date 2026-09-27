#!/usr/bin/env bash
# MAL one-shot migrate-direct OOS check, headless Claude Code, read-only.
#
# Reads the frozen cell's out-of-sample report/manifest from mal-fast-0 disk
# and, read-only over ssh, from mal-core-0 (Oracle) — never via the LLM's own
# shell access — and hands both to a headless `claude -p` session that only
# has Read/Grep/Glob and Write/Edit scoped to its own report directory and
# the shared INDEX.md. Does not refit the cell, does not run scoring code,
# does not propose live trading. Per ARTIFACTS/lab/migrate-direct-oos.md, no
# sealed hour is ever on both hosts' lists, so the two reports describe
# disjoint hours and the verdict must consider both, not just this host's.
#
# Usage: oos-check.sh [--test]
#   --test   write to reports/oos-check/TEST-<date>.md instead of
#            reports/oos-check/<date>.md, for a manual dry run with the
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
    *) echo "oos-check.sh: unknown argument ${arg}" >&2; exit 2 ;;
  esac
done

UTC_DATE="$(date -u +%F)"
UTC_NOW="$(date -u +%FT%TZ)"
REPORTS_ROOT="/home/claude/reports"
REPORT_DIR="${REPORTS_ROOT}/oos-check"
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

exec >"${LOG_FILE}" 2>&1

echo "oos-check.sh starting at ${UTC_NOW} (test_mode=${TEST_MODE})"

OOS_DIR="/var/lib/mal/paper/migrate-direct-oos-fast"

# --- Gather facts (read-only) -------------------------------------------

{
  echo "Run date (UTC): ${UTC_NOW}"
  echo "Test mode: ${TEST_MODE}"
  echo "Your working directory for this session: ${REPORTS_ROOT}"
  echo "Report file to write (relative to your working directory, use exactly this string with Write): oos-check/${STAMP}.md"
  echo "Index file to append to (relative to your working directory, use exactly this string with Edit): INDEX.md"
  echo "(Do not use absolute paths for either file — this session's write permission is scoped to these two relative paths only.)"
  echo
  echo "### ${OOS_DIR}/report.json"
  cat "${OOS_DIR}/report.json" 2>&1 || echo "(report.json not readable)"
  echo
  echo "### ${OOS_DIR}/manifest.json"
  cat "${OOS_DIR}/manifest.json" 2>&1 || echo "(manifest.json not readable)"
  echo
  echo "### ${OOS_DIR}/lag.json"
  cat "${OOS_DIR}/lag.json" 2>&1 || echo "(lag.json not readable)"
  echo
  echo "### mal-fast-oos-score.service status"
  UBUNTU_UID="$(id -u ubuntu 2>/dev/null || echo 1000)"
  sudo -n -u ubuntu env "XDG_RUNTIME_DIR=/run/user/${UBUNTU_UID}" \
    systemctl --user show mal-fast-oos-score.service \
    -p ActiveState -p SubState -p NRestarts -p ActiveEnterTimestamp 2>&1 \
    || echo "(status check failed)"
  echo

  echo "### mal-core-0 (Oracle) reachability"
  echo "command: ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 true"
  set +e
  timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 true </dev/null >/dev/null 2>&1
  ORACLE_RC=$?
  set -e
  echo "exit_code: ${ORACLE_RC}"
  if [[ "${ORACLE_RC}" != "0" ]]; then
    echo "interpretation: Oracle did not answer. Score this run from the mal-fast-0 report above only, and say plainly in the report that the Oracle side of the pooled book could not be read this time (do not invent Oracle numbers)."
  else
    echo "interpretation: Oracle answered. Oracle's own migrate-direct-oos report follows."
    echo
    echo "### Oracle /var/lib/mal/paper/migrate-direct-oos/report.json"
    timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
      'cat /var/lib/mal/paper/migrate-direct-oos/report.json' </dev/null 2>&1 \
      || echo "(Oracle report.json not readable)"
    echo
    echo "### Oracle /var/lib/mal/paper/migrate-direct-oos/manifest.json"
    timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
      'cat /var/lib/mal/paper/migrate-direct-oos/manifest.json' </dev/null 2>&1 \
      || echo "(Oracle manifest.json not readable)"
    echo
    echo "### Oracle mal-migrate-direct-oos.timer/service status"
    timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 mal-core-0 \
      'sudo -n systemctl --user -M ubuntu@ status mal-migrate-direct-oos.timer mal-migrate-direct-oos.service' \
      </dev/null 2>&1 || echo "(Oracle timer status check failed)"
  fi
} >"${FACTS_FILE}" 2>&1

echo "facts gathered at ${FACTS_FILE}:"
cat "${FACTS_FILE}"

# --- Render the prompt and run headless Claude ----------------------------

{
  cat "${ROOT}/ops/claude-schedules/oos-check.prompt.md"
  echo
  echo "## FACTS"
  echo
  echo '```'
  cat "${FACTS_FILE}"
  echo '```'
} > "${PROMPT_FILE}"

echo "invoking headless claude, prompt file ${PROMPT_FILE}"

# See the matching comment in daily-review.sh: cwd must be the shared
# reports root (not the MAL checkout) with --setting-sources "" and tight
# cwd-relative Edit() patterns, or the MAL repo's own permissive
# .claude/settings.json leaks broad Write/Edit access into this session.
set +e
( cd "${REPORTS_ROOT}" && timeout 1200 /home/claude/.local/bin/claude \
  -p "$(cat "${PROMPT_FILE}")" \
  --model claude-sonnet-5 \
  --setting-sources "" \
  --add-dir "${ROOT}" \
  --allowedTools "Read" "Edit(./oos-check/${STAMP}.md)" "Edit(./INDEX.md)" \
  --disallowedTools "Read(**/.env)" "Read(**/.env.*)" "Read(**/helius.env)" "Read(**/*credentials*)" \
  --permission-prompts none \
  --max-budget-usd 2 \
  --no-session-persistence \
  --output-format text )
CLAUDE_RC=$?
set -e

echo "claude exited ${CLAUDE_RC}"

if [[ ! -s "${REPORT_FILE}" ]]; then
  echo "oos-check.sh: WARNING — claude did not write ${REPORT_FILE}" >&2
  {
    echo "VERDICT: UNDER-SAMPLED"
    echo
    echo "## Summary"
    echo
    echo "The headless claude session (exit code ${CLAUDE_RC}) did not produce"
    echo "a report file at the expected path. See ${LOG_FILE} for the session"
    echo "transcript and ${FACTS_FILE} for the gathered facts. This placeholder"
    echo "verdict is a fallback only, not a real read of the data."
  } > "${REPORT_FILE}"
  echo "- ${UTC_DATE} oos-check: VERDICT=UNDER-SAMPLED — claude session did not write a report (exit ${CLAUDE_RC}), see ${LOG_FILE}" >> "${INDEX_FILE}"
  exit 1
fi

echo "oos-check.sh done, report at ${REPORT_FILE}"
exit 0
