#!/bin/sh
# The r4-b LONG DRY run (EXP-027 confirmation family, DEC-032 Am.8 Erratum 4): the host on the fast-0 tip tape, trigger certified, to E. MiScusi job on mal-fast-0
# (plain sh, rc kept, no pipe on the host's stdout). Conditions: QP on #1160 (qp-1160.md) M1 and M5; QP-RULING-1031-r4b-pc3 section 2.4, 2.5, 4 P4/P6.
# Usage: sh scripts/research/r4b-dry-long.sh <change_id> [max_marks]     (run from the install tree's root; submit at HH:05Z, see below)
#
# What it does, in order:
#   1. refuses to start if ANY r4b_host_glue process is running (M5: two hosts must never write one declog) or if this change_id already has a declog/out/host.log;
#   2. -I preflight (imports of the host's modules);
#   3. records the M1 start facts to $D/m1-start.txt: the user slice's MemoryMax/MemoryHigh/MemoryCurrent (before the scope exists), then, once the scope is up, its
#      memory.max (expect 3670016000 = 3500M) and memory.swap.max (expect 0), the host's niceness (expect 19) and its pid. A scope-limit, swap or nice mismatch is fail-closed: M1_ABORT, the scope is stopped, exit 8. The slice line (M1_SLICE) is recorded, not enforced;
#   4. runs the host in its own scope: MemoryMax=3500M, MemorySwapMax=0, the whole process under `nice -n 19` (pc3 2.4), never --no-ionice (ionice -c3 is inert under
#      the disk scheduler `none`; the records say so). The host's stdout/stderr go to $D/host.log; the rc is the host's rc.
# The reservation of this MiScusi job is 1.9G (fast-0 MiScusi ceiling 2G); the scope is the hard cap, 3500M (M-a'). Say so in the job name, the LAB_STATE start row and the
# notebook entry (M2). Count 3.5 GB for it before admitting any other fast-0 job (M3).
# Start timing (pc3 2.5, P6): submit at HH:05Z; the catch-up (about 37 min, budget 50) ends before HH+1:00Z; HH+1:00Z is the settle mark; rely on marks from HH+2:00Z.
# Launch scripts/research/r4b-dry-sidecar.sh as its own job >= 30 min BEFORE this one. Stop procedure: r4b-dry-stop.sh (M5).
# Output dir: $HOME/mal-data/r4b-dry/<change_id>. The decision log stays sealed: names and counts only.
V="$HOME/mal-venvs/r4b"
CHANGE_ID="${1:?usage: r4b-dry-long.sh <change_id> [max_marks]}"
MAX="${2:-}"
case "$CHANGE_ID" in
    ''|-*|.*|*[!A-Za-z0-9._-]*) echo "r4b-dry-long: bad change_id (letters, digits, . _ - only; no leading - or .)" >&2; exit 2 ;;
esac
case "$MAX" in
    *[!0-9]*) echo "r4b-dry-long: max_marks must be a positive integer" >&2; exit 2 ;;
    0) echo "r4b-dry-long: max_marks must be a positive integer" >&2; exit 2 ;;
esac
D="$HOME/mal-data/r4b-dry/$CHANGE_ID"
UNIT="r4b-dry-$CHANGE_ID"
SCOPE_MEM_MAX=3670016000   # MemoryMax=3500M in bytes (3500 * 1048576)
M1_WAIT_S="${R4B_M1_WAIT_S:-120}"
CGROOT="${R4B_CGROUP_ROOT:-/sys/fs/cgroup}"   # test hook only

# 0. the user systemd bus (systemd-run --user and systemctl --user need XDG_RUNTIME_DIR in this job's environment)
systemctl --user show -p Version > /dev/null 2>&1 || { echo "r4b-dry-long: no user systemd manager reachable (XDG_RUNTIME_DIR?); nothing started" >&2; exit 5; }

# 1. M5: one host at a time, one declog per change_id
LEFT=$(ps -eo pid,args | grep '[r]4b_host_glue')
if [ -n "$LEFT" ]; then
    echo "r4b-dry-long: REFUSED (M5): an r4b_host_glue process is already running (two hosts must never write one declog):" >&2
    echo "$LEFT" | cut -c1-100 >&2
    exit 6
fi
for P in declog out host.log m1-start.txt; do
    if [ -e "$D/$P" ]; then
        echo "r4b-dry-long: REFUSED: $D/$P exists; a restart is a NEW run with a new change_id" >&2
        exit 6
    fi
done

# 2. preflight (-I: no cwd, no user site)
[ -f tools/r4b_host_glue.py ] || { echo "r4b-dry-long: run from the install tree's root (tools/r4b_host_glue.py not found)" >&2; exit 5; }
mkdir -p "$D" || exit 5
"$V/bin/python" -I -c 'import sys; sys.path.insert(0, "."); import tools.r4b_live, tools.r4b_host_glue, tools.r4b_tip_reader; print("PREFLIGHT_OK")' || exit 5

# 3. M1 facts known before the scope exists
SLICE="user-$(id -u).slice"
unlimited() { case "$1" in infinity|'') return 0 ;; esac; [ "${#1}" -gt 18 ]; }   # "infinity" or a 2^64-1 style number
limit_ok() { if unlimited "$1"; then return 0; fi; case "$1" in *[!0-9]*) return 1 ;; esac; [ "$1" -ge "$2" ]; }
SHOW=$(systemctl show "$SLICE" -p MemoryMax -p MemoryHigh -p MemoryCurrent)
S_MAX=$(echo "$SHOW" | sed -n 's/^MemoryMax=//p')
S_HIGH=$(echo "$SHOW" | sed -n 's/^MemoryHigh=//p')
S_CUR=$(echo "$SHOW" | sed -n 's/^MemoryCurrent=//p')
case "$S_CUR" in ''|*[!0-9]*) S_CUR_N=0 ;; *) S_CUR_N=$S_CUR ;; esac
NEED=$((SCOPE_MEM_MAX + S_CUR_N))
{
    echo "change_id=$CHANGE_ID"
    echo "recorded_pre_start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "install_head=$(git rev-parse HEAD 2>/dev/null)"
    echo "scope_unit=$UNIT.scope scope_MemoryMax=3500M scope_MemorySwapMax=0 nice=19 reservation=1.9G (fast-0 MiScusi ceiling 2G)"
    echo "slice=$SLICE"
    echo "slice_MemoryMax=$S_MAX"
    echo "slice_MemoryHigh=$S_HIGH"
    echo "slice_MemoryCurrent=$S_CUR"
    if limit_ok "$S_MAX" "$NEED" && limit_ok "$S_HIGH" "$NEED"; then
        echo "M1_SLICE PASS (no slice limit below 3.5 GB plus current use = $NEED bytes)"
    else
        echo "M1_SLICE FAIL (a slice limit is below 3.5 GB plus current use = $NEED bytes; the scope can be throttled or killed by the slice)"
    fi
} > "$D/m1-start.txt"

# the recorder: waits for the scope, appends the scope's own cgroup limits and the host's niceness, prints the file to the job log. Killed when the host ends.
record_m1() {
    i=0
    CG=''
    HPID=''
    HNI=''
    while [ "$i" -lt "$M1_WAIT_S" ]; do
        CG=$(systemctl --user show "$UNIT.scope" -p ControlGroup | sed -n 's/^ControlGroup=//p')
        LINE=$(ps -eo pid,ni,args | grep '[r]4b_host_glue.py run' | grep -e "--change-id $CHANGE_ID " | head -1)
        if [ -n "$CG" ] && [ -r "$CGROOT$CG/memory.max" ] && [ -n "$LINE" ]; then
            set -- $LINE
            HPID=$1
            HNI=$2
            break
        fi
        sleep 1
        i=$((i + 1))
    done
    {
        echo "recorded_scope_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        if [ -z "$HPID" ]; then
            echo "M1_INCOMPLETE the scope or the host process was not seen within ${M1_WAIT_S}s (control_group='$CG')"
        else
            MM=$(cat "$CGROOT$CG/memory.max")
            MS=$(cat "$CGROOT$CG/memory.swap.max")
            MH=$(cat "$CGROOT$CG/memory.high")
            echo "scope_control_group=$CG"
            echo "scope_memory_max=$MM"
            echo "scope_memory_swap_max=$MS"
            echo "scope_memory_high=$MH"
            echo "host_pid=$HPID"
            echo "host_nice=$HNI"
            BAD=0
            if [ "$MM" = "$SCOPE_MEM_MAX" ]; then echo "M1_SCOPE_MEMORY_MAX PASS ($MM)"; else echo "M1_SCOPE_MEMORY_MAX FAIL (got $MM, expected $SCOPE_MEM_MAX)"; BAD=1; fi
            if [ "$MS" = "0" ]; then echo "M1_SCOPE_SWAP_MAX PASS (0)"; else echo "M1_SCOPE_SWAP_MAX FAIL (got $MS, expected 0)"; BAD=1; fi
            if [ "$HNI" = "19" ]; then echo "M1_NICE PASS (19)"; else echo "M1_NICE FAIL (got $HNI, expected 19)"; BAD=1; fi
            if [ "$BAD" = 1 ]; then echo "M1_ABORT the scope's limits are not the registered ones: stopping the scope now (M5), exit 8"; fi
        fi
    } >> "$D/m1-start.txt"
    # fail closed: the scope facts are exact equalities, so a mismatch ends the run at once (no mark has been counted yet, the catch-up is still running). The slice line is
    # judgement (page cache counts in MemoryCurrent) and stays a recorded line for the manager.
    if grep -q '^M1_ABORT' "$D/m1-start.txt"; then systemctl --user stop "$UNIT.scope"; fi
    cat "$D/m1-start.txt"
}

# 4. the host. --max-marks counts LIVE marks only (a mark during the catch-up never counts); none = run until stopped (E).
if [ -n "$MAX" ]; then set -- --max-marks "$MAX"; else set --; fi
record_m1 &
REC=$!
systemd-run --user --scope -q --unit="$UNIT" -p MemoryMax=3500M -p MemorySwapMax=0 nice -n 19 "$V/bin/python" -I tools/r4b_host_glue.py run \
    --source tip --tip-dir /var/lib/mal/sealed/fast-trades-tip --tip-state-dir /var/lib/mal/fast-tip-follower \
    --key-file /var/lib/mal/fast-listener/helius.env --declog-root "$D/declog" --out "$D/out" --commitment processed --daily-credit-cap 50000 --trigger certified \
    --change-id "$CHANGE_ID" --catch-up-hours 52 "$@" > "$D/host.log" 2>&1
rc=$?
kill "$REC" 2>/dev/null
wait "$REC" 2>/dev/null
tail -25 "$D/host.log"
echo "r4b-dry-long: host rc=$rc"
if grep -q '^M1_ABORT' "$D/m1-start.txt"; then
    echo "r4b-dry-long: M1_ABORT: the scope was stopped because its limits or niceness were not the registered ones; this is a failed start (exit 8), see $D/m1-start.txt; confirm with r4b-dry-stop.sh" >&2
    exit 8
fi
exit $rc
