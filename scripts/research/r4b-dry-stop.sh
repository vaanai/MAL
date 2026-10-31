#!/bin/sh
# M5 stop procedure of the r4-b LONG DRY run (QP #1160 M5): for ANY stop rule, in this order, all before any restart:
#   1. cancel the MiScusi job of the host (a cancelled job's scope child survives the cancel, lab note 2026-10-17);
#   2. sh scripts/research/r4b-dry-stop.sh <change_id>      (this script: stops the scope, then checks that no r4b_host_glue process is left);
#   3. only on PASS, restart as a NEW run with a new change_id (never the same one: two hosts writing one declog break the run), recorded in LAB_STATE.
# Never kills a process by hand and never touches the follower. Exit 0 and a PASS line, or exit 1 and a FAIL line with the leftover pids.
CHANGE_ID="${1:?usage: r4b-dry-stop.sh <change_id>}"
case "$CHANGE_ID" in
    ''|-*|.*|*[!A-Za-z0-9._-]*) echo "r4b-dry-stop: bad change_id" >&2; exit 2 ;;
esac
UNIT="r4b-dry-$CHANGE_ID.scope"
WAIT_S="${R4B_STOP_WAIT_S:-30}"
systemctl --user stop "$UNIT"
src=$?
echo "r4b-dry-stop: systemctl --user stop $UNIT rc=$src"
i=0
while [ "$i" -lt "$WAIT_S" ]; do
    LEFT=$(ps -ww -eo pid,args | grep '[r]4b_host_glue')
    [ -z "$LEFT" ] && break
    sleep 1
    i=$((i + 1))
done
if [ -z "$LEFT" ]; then
    echo "R4B_STOP PASS change_id=$CHANGE_ID: no r4b_host_glue process left (scope stop rc=$src)"
    exit 0
fi
echo "R4B_STOP FAIL change_id=$CHANGE_ID: an r4b_host_glue process is still running after ${WAIT_S}s (scope stop rc=$src); do NOT restart:"
echo "$LEFT" | cut -c1-100
exit 1
