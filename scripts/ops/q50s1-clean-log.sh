#!/bin/sh
# MiScusi job command (one-shot, ~1 min): is the s1 shadow's log "clean" for at least 1 h before the clock (QP-RULING-1024-successor R10.2; DEC-031 Amendment 18 item 8), WITHOUT opening
# its sealed job log or status view. POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it. Run it at about 2026-11-05T23:05Z or later (>= 1 h after a 22:00Z start) and before
# 2026-11-06T00:00Z; again after any doubt.
#   sh scripts/ops/q50s1-clean-log.sh --job-uid j_XXXX [--out-dir DIR]
# --job-uid: the MiScusi id of the s1 shadow job (j_...; from miscusi_job_list, never from its status view). The job log $HOME/.miscusi/jobs/<uid>/log is read by
# tools/q50s1_health.py clean-log: only lines that START with "q50s1-shadow: " (the pinned start script's own lines) are printed; every other line is matched against fixed patterns and
# counted. --out-dir: the book's dir (default $ARENA/h5-q50s1-2026-11-05): prefix counts of start/stop/error/hb rows, hb rows' trailing t_ms, gap rows' kind and flags_pools, the
# status file's mtime, the errors file's size. Scope: `systemctl --user show q50s1-shadow.scope` ActiveState and ActiveEnterTimestamp only.
# Exit: 0 CLEAN, 1 NOT_CLEAN, 2 args or unreadable input, 6 host.
# Env (optional): Q50S1_ARENA (s1's own root, DEC-031 Amendment 22 item 9; default /data/mal/arena), Q50S1_JOBS_DIR (default $HOME/.miscusi/jobs).
set -eu
set -f
[ -n "${HOME:-}" ] || { echo "q50s1-clean-log: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ARENA="${Q50S1_ARENA:-/data/mal/arena}"
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
echo "q50s1-clean-log: $(date -u +%FT%TZ) scope $UNIT.scope ActiveState=$ACT ActiveEnterTimestamp='$SINCE_TXT' job $JUID out $(basename "$OUT")"
exec python3 -I "$REPO/tools/q50s1_health.py" clean-log --out-dir "$OUT" --job-log "$JOBS/$JUID/log" --scope-active "$A" --scope-since-epoch "${SINCE:-x}"
