#!/bin/sh
# A3 applied/verdict alert loop, mal-fast-0, ONE MiScusi job (POSIX sh). Every 10 min: python3 -I tools/a3_applied_alert.py (reads only the three A3 flag files).
# ALERT lines go to the MiScusi job log. The first ALERT line ends the job with exit 3 (MiScusi notifies on job end); a clean run loops to its limit and exits 0.
# Env: A3_REPO=$HOME/MAL, A3_PY=/usr/bin/python3, A3_ALERT_EVERY=600, A3_ALERT_UNTIL=<epoch s, default now + 14 d>.
A3_REPO=${A3_REPO:-$HOME/MAL}
A3_PY=${A3_PY:-/usr/bin/python3}
EVERY=${A3_ALERT_EVERY:-600}
UNTIL=${A3_ALERT_UNTIL:-$(( $(date -u +%s) + 14 * 86400 ))}
cd "$A3_REPO" || { echo "ALERT A3_ALERT_JOB cannot cd $A3_REPO"; exit 1; }
echo "A3_ALERT_JOB start $(date -u +%FT%TZ) head $(git rev-parse HEAD) every ${EVERY}s"
while :; do
  "$A3_PY" -I tools/a3_applied_alert.py
  rc=$?
  [ "$rc" -ne 0 ] && { echo "A3_ALERT_JOB exit 3 (first ALERT, rc $rc): MiScusi notifies on job end"; exit 3; }
  [ "$(date -u +%s)" -ge "$UNTIL" ] && { echo "A3_ALERT_JOB end reached"; exit 0; }
  sleep "$EVERY"
done
