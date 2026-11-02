#!/bin/sh
# MiScusi job command: the daily decision-log sha256 digest of `h5v1q50-boostfloor-s1t` (DEC-031 section 1.2: "Each book's decision-log sha256 goes into a MiScusi notebook entry at least once per UTC
# day"). It mirrors scripts/ops/q50s1-digest.sh (s1's) and digest #1349 (ops/manager13/cmds/h5-digest-v2.sh) with s1t's host, root and dir glob. POSIX sh only. Paper only. NOT SUBMITTED by the PR
# that adds it.
#   sh scripts/ops/s1t-digest.sh loop        digests yesterday's UTC day at once (catch-up), then daily at 00:10Z for D = yesterday, until END (default 2026-12-01T00:30:00Z)
#   sh scripts/ops/s1t-digest.sh once D      one UTC date D (YYYY-MM-DD), for a missed day (D = 2026-12-01 holds the 00:00 to 00:15Z run-out: run it by hand after 2026-12-01T00:15Z)
# Runs on mal-research-0 (the s1t shadow's host). Dirs: $ARENA/h5-s1t-book-<YYYYMMDDTHHMMSSZ> under s1t's own root (DEC-031 Amendment 24 item 7; $S1T_HOME/data/arena, default
# /home/claude/s1thome): the book's out dirs only (segment 1 from 2026-11-06T22:55Z and segment 2 from the 2026-11-20T21:00Z hand-off), never the smoke's out dir (a different HOME, h5-smoke-s1t-*,
# sealed). Per dir and date:
#   DIGEST <D> dir <name> files <n> first <file> last <file> entry_pv_sha256 <sha> boost_slice_sha256 <sha> whole_sha256 <sha> whole_lines <n> at <utc>
# Hashes, file names and ONE line count of the concatenated hour files (every record type together, so it carries no trigger, pool, excluded or decision count; Amendment 24 item 6 and the
# SEALS-BRIEF S1T READERS line: no per-kind count is printed before s1t's first read, whose output prints them). Unlike #1349 there are no pv_lines, trigger_lines or boost_slice_lines.
# Lines are selected by fixed prefix ('{"type":"trigger","variant":"pv",' and '{"type":"boost_slice",'), never parsed, and no line is printed. The manager copies `grep '^DIGEST <D> '` of this
# job's log into the notebook (ops/DIGEST-COPY-COMMANDS.md pattern). No job status view of this job is called.
# Platform: a job lives at most 14 d and an `after` job starts only after a successful end, so two segments, each ending itself with exit 0: A submitted 2026-11-07 after 00:10Z with
# S1T_DIGEST_END=1795134600 (2026-11-20T00:30Z; D = 2026-11-06 catch-up to D = 2026-11-19), B `after` A with the default END (2026-12-01T00:30Z; its catch-up repeats D = 2026-11-19, then
# D = 2026-11-20 to D = 2026-11-30, the last counted date before E = 2026-12-01T00Z).
# Env (optional): S1T_HOME (s1t's own HOME, default /home/claude/s1thome; root = $S1T_HOME/data/arena), S1T_DIGEST_END (epoch, <= the default 1796085000).
# Exit: 0 ok / end reached, 2 args or refused root or END, 6 host.
set -eu
END_MAX=1796085000   # 2026-12-01T00:30:00Z
[ -n "${HOME:-}" ] || { echo "s1t-digest: HOME is not set" >&2; exit 2; }
S1THOME="${S1T_HOME:-/home/claude/s1thome}"   # s1t's own HOME: every s1t python that checks an arena root runs with HOME=$S1THOME (the pinned scorer accepts only $HOME/data/arena)
case "$S1THOME" in /*) ;; *) echo "s1t-digest: S1T_HOME must be an absolute path" >&2; exit 2 ;; esac
ARENA="$S1THOME/data/arena"
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
for other in "$REALHOME/data/arena" /data/mal/arena /home/claude/s1home/data/arena /home/claude/s1tsmoke/data/arena; do
  [ "$(realpath -m "$ARENA")" != "$(realpath -m "$other")" ] || { echo "s1t-digest: $ARENA is Q50's, HV4's, s1's or the smoke's root, never s1t's (Amendment 24 item 7)" >&2; exit 2; }
done
[ "$(hostname)" = mal-research-0 ] || { echo "s1t-digest: the s1t book runs on mal-research-0 (this is $(hostname))" >&2; exit 6; }
END="${S1T_DIGEST_END:-$END_MAX}"
case "$END" in ''|*[!0-9]*) echo "s1t-digest: bad END" >&2; exit 2 ;; esac
[ "$END" -le "$END_MAX" ] || { echo "s1t-digest: END after 2026-12-01T00:30Z" >&2; exit 2; }
if [ "${1:-}" = loop ] && [ $((END - $(date -u +%s))) -gt 1208700 ]; then echo "s1t-digest: END is more than 14 d - 15 min after now: a MiScusi job lives at most 14 d: an END more than 14 d minus 15 min after this job's start is refused (exit 2)" >&2; exit 2; fi
P_PV='{"type":"trigger","variant":"pv",'
P_BS='{"type":"boost_slice",'

digest_day() {
  D="$1"
  n_dirs=0
  for SD in "$ARENA"/h5-s1t-book-2*; do
    [ -d "$SD" ] || continue
    case "$(basename "$SD")" in h5-s1t-book-20[0-9][0-9][0-1][0-9][0-3][0-9]T[0-2][0-9][0-5][0-9][0-5][0-9]Z) ;; *) continue ;; esac
    n_dirs=$((n_dirs + 1))
    FILES="$(cd "$SD" && ls h5-shadow-"$D"T??.jsonl 2>/dev/null || true)"
    N="$(printf '%s\n' "$FILES" | awk 'NF {n++} END {print n + 0}')"
    if [ "$N" -eq 0 ]; then echo "DIGEST $D dir $(basename "$SD") files 0 at $(date -u +%FT%TZ)"; continue; fi
    FIRST="$(printf '%s\n' "$FILES" | awk 'NF {print; exit}')"
    LAST="$(printf '%s\n' "$FILES" | awk 'NF {l = $0} END {print l}')"
    # shellcheck disable=SC2086
    E="$(cd "$SD" && cat $FILES | grep -F "$P_PV" | sha256sum | cut -c1-64)"
    # shellcheck disable=SC2086
    B="$(cd "$SD" && cat $FILES | grep -F "$P_BS" | sha256sum | cut -c1-64)"
    # shellcheck disable=SC2086
    W="$(cd "$SD" && cat $FILES | sha256sum | cut -c1-64)"
    # shellcheck disable=SC2086
    WL="$(cd "$SD" && cat $FILES | wc -l | tr -d ' ')"
    echo "DIGEST $D dir $(basename "$SD") files $N first $FIRST last $LAST entry_pv_sha256 $E boost_slice_sha256 $B whole_sha256 $W whole_lines $WL at $(date -u +%FT%TZ)"
  done
  [ "$n_dirs" -gt 0 ] || echo "DIGEST $D no s1t dir under $ARENA at $(date -u +%FT%TZ)"
}

case "${1:-}" in
  once)
    [ "$#" -eq 2 ] || { echo "s1t-digest: usage: s1t-digest.sh once YYYY-MM-DD" >&2; exit 2; }
    case "$2" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]) ;; *) echo "s1t-digest: bad date '$2'" >&2; exit 2 ;; esac
    digest_day "$2"; exit 0 ;;
  loop)
    [ "$#" -eq 1 ] || { echo "s1t-digest: usage: s1t-digest.sh loop" >&2; exit 2; } ;;
  *) echo "s1t-digest: usage: s1t-digest.sh loop | once YYYY-MM-DD" >&2; exit 2 ;;
esac
echo "s1t-digest: loop start $(date -u +%FT%TZ); END $(date -u -d "@$END" +%FT%TZ)"
[ "$(date -u +%s)" -le "$END" ] || { echo "end reached"; exit 0; }
digest_day "$(date -u -d yesterday +%F)"
while :; do
  now="$(date -u +%s)"
  t="$(date -u -d "$(date -u +%F)T00:10:00Z" +%s)"
  [ "$t" -gt "$now" ] || t=$((t + 86400))
  if [ "$t" -gt "$END" ]; then [ "$END" -le "$now" ] || sleep $((END - now)); echo "end reached $(date -u +%FT%TZ)"; exit 0; fi
  sleep $((t - now))
  digest_day "$(date -u -d yesterday +%F)"
done
