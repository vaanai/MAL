#!/bin/sh
# P-C3 of QP-RULING-1031-r4b-history: the r4-b DRY host on the fast-0 tip tape, a few LIVE marks, counts only. MiScusi job on mal-fast-0 (plain sh, rc kept, no pipe).
# Usage: sh scripts/research/r4b-tip-smoke.sh <change_id> [live_marks]    (default 3; --max-marks counts LIVE marks only: a mark that falls during the catch-up is
# recorded as abandoned and does not count). Output dir: $HOME/mal-data/r4b-dry/<change_id>. The decision log stays sealed: names and counts only.
V="$HOME/mal-venvs/r4b"
CHANGE_ID="${1:?usage: r4b-tip-smoke.sh <change_id> [live_marks]}"
MAX="${2:-3}"
D="$HOME/mal-data/r4b-dry/$CHANGE_ID"
mkdir -p "$D" || exit 5
"$V/bin/python" -I -c 'import sys; sys.path.insert(0, "."); import tools.r4b_live, tools.r4b_host_glue, tools.r4b_tip_reader; print("PREFLIGHT_OK")' || exit 5
"$V/bin/python" -I tools/r4b_host_glue.py run --source tip --tip-dir /var/lib/mal/sealed/fast-trades-tip --tip-state-dir /var/lib/mal/fast-tip-follower \
    --key-file /var/lib/mal/fast-listener/helius.env --declog-root "$D/declog" --out "$D/out" --commitment processed --daily-credit-cap 50000 --trigger certified \
    --change-id "$CHANGE_ID" --catch-up-hours 52 --max-marks "$MAX" > "$D/smoke.log" 2>&1
rc=$?
tail -25 "$D/smoke.log"
exit $rc
