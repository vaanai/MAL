#!/bin/sh
# MiScusi job command (one-shot, ~1 min): is the s1 shadow's log "clean" for at least 1 h before the clock (QP-RULING-1024-successor R10.2; DEC-031 Amendment 18 item 8), WITHOUT opening
# its sealed job log or status view. POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it. Run it at about 2026-11-05T23:05Z or later (>= 1 h after a 22:00Z start) and before
# 2026-11-06T00:00Z; again after any doubt.
#   sh scripts/ops/q50s1-clean-log.sh --job-uid j_XXXX [--out-dir DIR]
# --job-uid: the MiScusi id of the s1 shadow job (j_...; from miscusi_job_list, never from its status view). The job log $HOME/.miscusi/jobs/<uid>/log is read by
# tools/q50s1_health.py clean-log: only lines that START with "q50s1-shadow: " (the pinned start script's own lines) are printed; every other line is matched against fixed patterns and
# counted. --out-dir: the book's dir (default $ARENA/h5-q50s1-2026-11-05): prefix counts of start/stop/error/hb rows, hb rows' trailing t_ms, gap rows' kind and flags_pools, the
# status file's mtime, the errors file's size. Scope: `systemctl --user show q50s1-shadow.scope` ActiveState and ActiveEnterTimestamp only.
# It is also the Am.22 item 10 key check before the clock (QP C2): counts only, never an argv or environment printed: the key string in the book's start row(s), in
# /proc/<pid>/cmdline of every process in the scope's cgroup, HELIUS_API_KEY= in their /proc/<pid>/environ, and in the user journal since the scope start; plus
# `findmnt -no OPTIONS /proc` (printed). Any count that is not 0, or cannot be read, is NOT_CLEAN. The one path the launcher's redaction does not cover is S*'s own
# h5-shadow-errors.log (an exception message written to a file), which is counted too.
# Exit: 0 CLEAN, 1 NOT_CLEAN, 2 args or unreadable input, 6 host.
# Tests only: Q50S1_CGROUP_ROOT (default /sys/fs/cgroup; a wrong path reads nothing and fails closed).
# Env (optional): Q50S1_HOME (s1's own HOME, default /home/claude/s1home; root = $Q50S1_HOME/data/arena, DEC-031 Amendment 22 item 9), Q50S1_JOBS_DIR (default $HOME/.miscusi/jobs).
set -eu
set -f
[ -n "${HOME:-}" ] || { echo "q50s1-clean-log: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
S1HOME="${Q50S1_HOME:-/home/claude/s1home}"   # s1's own HOME: every s1 python that checks an arena root runs with HOME=$S1HOME (DEC-031 Am.22 item 9)
ARENA="$S1HOME/data/arena"   # s1's own arena root: the pinned scorer and S*'s h5_shadow accept only /data/mal/arena (HV4's on fast-0, #1097) or $HOME/data/arena
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
case "$(realpath -m "$ARENA")" in "$(realpath -m "$REALHOME/data/arena")"|"$(realpath -m /data/mal/arena)") echo "q50s1-clean-log: $ARENA is Q50's or HV4's root, never s1's (Amendment 22 item 9)" >&2; exit 2 ;; esac
JOBS="${Q50S1_JOBS_DIR:-$HOME/.miscusi/jobs}"
UNIT=q50s1-shadow
JUID=""
OUT="$ARENA/h5-q50s1-2026-11-05"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --job-uid) [ "$#" -ge 2 ] || { echo "q50s1-clean-log: --job-uid needs a value" >&2; exit 2; }; JUID="$2"; shift 2 ;;
    --out-dir) [ "$#" -ge 2 ] || { echo "q50s1-clean-log: --out-dir needs a value" >&2; exit 2; }; OUT="$2"; shift 2 ;;
    *) echo "q50s1-clean-log: usage: q50s1-clean-log.sh --job-uid j_XXXX [--out-dir DIR]" >&2; exit 2 ;;
  esac
done
case "$JUID" in j_*) ;; *) echo "q50s1-clean-log: --job-uid must be a MiScusi job id j_..." >&2; exit 2 ;; esac
case "$JUID" in *[!A-Za-z0-9_-]*) echo "q50s1-clean-log: bad job id" >&2; exit 2 ;; esac
case "$(basename "$OUT")" in h5-q50s1-20*) ;; *) echo "q50s1-clean-log: '$OUT' is not an s1 book dir" >&2; exit 2 ;; esac
case "$OUT" in *..*) echo "q50s1-clean-log: refusing '$OUT'" >&2; exit 2 ;; esac
[ "$(hostname)" = mal-fast-0 ] || { echo "q50s1-clean-log: runs on mal-fast-0 (this is $(hostname))" >&2; exit 6; }
ACT="$(systemctl --user show "$UNIT.scope" -p ActiveState --value 2>/dev/null || echo unknown)"
SINCE_TXT="$(systemctl --user show "$UNIT.scope" -p ActiveEnterTimestamp --value 2>/dev/null || echo "")"
SINCE=""
if [ -n "$SINCE_TXT" ]; then SINCE="$(date -u -d "$SINCE_TXT" +%s 2>/dev/null || echo "")"; fi
A=no
[ "$ACT" = active ] && A=yes
CGROOT="${Q50S1_CGROUP_ROOT:-/sys/fs/cgroup}"
KEYVAR="${Q50S1_KEY_VAR:-HELIUS_API_KEY}"   # the keyed provider's variable name (the same Q50S1_KEY_VAR as the start); counted in /proc/<pid>/environ
case "$KEYVAR" in ''|*[!A-Za-z0-9_]*) echo "q50s1-clean-log: bad Q50S1_KEY_VAR" >&2; exit 2 ;; esac
CG="$(systemctl --user show "$UNIT.scope" -p ControlGroup --value 2>/dev/null || echo "")"
PIDS=""
if [ -n "$CG" ] && [ -r "$CGROOT$CG/cgroup.procs" ]; then PIDS="$(cat "$CGROOT$CG/cgroup.procs" 2>/dev/null | tr '\n' ' ')"; fi
KC=unreadable; KE=unreadable
if [ -n "$(printf '%s' "$PIDS" | tr -d ' ')" ]; then
  KC=0; KE=0
  for p in $PIDS; do
    if [ -r "/proc/$p/cmdline" ]; then KC=$((KC + $(python3 -I "$REPO/tools/q50s1_health.py" count-keys < "/proc/$p/cmdline"))); else KC=unreadable; break; fi
    if [ -r "/proc/$p/environ" ]; then KE=$((KE + $(tr '\0' '\n' < "/proc/$p/environ" | grep -c -F "$KEYVAR=" || true) + $(python3 -I "$REPO/tools/q50s1_health.py" count-keys < "/proc/$p/environ"))); else KE=unreadable; break; fi
  done
fi
KJ=unreadable
if [ -n "$SINCE" ] && journalctl --user -n 0 --no-pager >/dev/null 2>&1; then KJ="$(journalctl --user --since "@$SINCE" --no-pager -o cat 2>/dev/null | python3 -I "$REPO/tools/q50s1_health.py" count-keys)"; fi
case "$KJ" in ''|*[!0-9]*) KJ=unreadable ;; esac
PM="$(findmnt -no OPTIONS /proc 2>/dev/null | tr ' ' ',' || true)"
echo "q50s1-clean-log: scope processes: $(printf '%s' "$PIDS" | wc -w) (pids are not printed with their argv)"
echo "q50s1-clean-log: $(date -u +%FT%TZ) scope $UNIT.scope ActiveState=$ACT ActiveEnterTimestamp='$SINCE_TXT' job $JUID out $(basename "$OUT")"
exec python3 -I "$REPO/tools/q50s1_health.py" clean-log --out-dir "$OUT" --job-log "$JOBS/$JUID/log" --scope-active "$A" --scope-since-epoch "${SINCE:-x}" \
  --cmdline-key-count "$KC" --environ-key-count "$KE" --journal-key-count "$KJ" --proc-mount "${PM:--}"
