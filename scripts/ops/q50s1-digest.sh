#!/bin/sh
# MiScusi job command: the daily decision-log sha256 digest of `h5v1q50-boostfloor-s1` (DEC-031 section 1.2: "Each book's decision-log sha256 goes into a MiScusi notebook entry at least
# once per UTC day"). It mirrors digest #1349 (ops/manager13/cmds/h5-digest-v2.sh), whose dir globs (h5-2026-10-18, h5-2026-10-22*, h5-q50-*) do not match the s1 dirs and which ends
# 2026-11-08. POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it.
#   sh scripts/ops/q50s1-digest.sh loop        digests yesterday's UTC day at once (catch-up), then daily at 00:10Z for D = yesterday, until END (default 2026-12-01T00:30:00Z)
#   sh scripts/ops/q50s1-digest.sh once D      one UTC date D (YYYY-MM-DD), for a missed day
# Dirs: $ARENA/h5-q50s1-2* under s1's own root (DEC-031 Amendment 22 item 9; $Q50S1_HOME/data/arena); the book's dated dirs only, never a smoke dir h5-smoke-*. Per dir and date:
#   DIGEST <D> dir <name> files <n> first <file> last <file> entry_pv_sha256 <sha> boost_slice_sha256 <sha> whole_sha256 <sha> at <utc>
# Hashes and file names only: unlike #1349 it prints no line counts (the s1 seal: no attempt counts). Lines are selected by fixed prefix ('{"type":"trigger","variant":"pv",' and
# '{"type":"boost_slice",'), never parsed. The manager copies `grep '^DIGEST <D> '` of this job's log into the notebook (ops/DIGEST-COPY-COMMANDS.md pattern).
# Platform: a job lives at most 14 d, and an `after` job starts only after a successful end, so two segments, each ending itself with exit 0: A submitted 2026-11-06 after 00:10Z
# with Q50S1_DIGEST_END=1794961800 (2026-11-18T00:30Z; D = 2026-11-05 catch-up to D = 2026-11-17), B `after` A with the default END (2026-12-01T00:30Z; its catch-up repeats
# D = 2026-11-17, then D = 2026-11-18 to D = 2026-11-30, the last counted date before E = 2026-12-01T00Z).
# Env (optional): Q50S1_HOME (s1's own HOME, default /home/claude/s1home; root = $Q50S1_HOME/data/arena, DEC-031 Amendment 22 item 9), Q50S1_DIGEST_END (epoch, <= the default 1796085000).
set -eu
END_MAX=1796085000   # 2026-12-01T00:30:00Z
[ -n "${HOME:-}" ] || { echo "q50s1-digest: HOME is not set" >&2; exit 2; }
S1HOME="${Q50S1_HOME:-/home/claude/s1home}"   # s1's own HOME: every s1 python that checks an arena root runs with HOME=$S1HOME (DEC-031 Am.22 item 9)
ARENA="$S1HOME/data/arena"   # s1's own arena root: the pinned scorer and S*'s h5_shadow accept only /data/mal/arena (HV4's on fast-0, #1097) or $HOME/data/arena
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
case "$(realpath -m "$ARENA")" in "$(realpath -m "$REALHOME/data/arena")"|"$(realpath -m /data/mal/arena)") echo "q50s1-digest: $ARENA is Q50's or HV4's root, never s1's (Amendment 22 item 9)" >&2; exit 2 ;; esac
END="${Q50S1_DIGEST_END:-$END_MAX}"
case "$END" in ''|*[!0-9]*) echo "q50s1-digest: bad END" >&2; exit 2 ;; esac
[ "$END" -le "$END_MAX" ] || { echo "q50s1-digest: END after 2026-12-01T00:30Z" >&2; exit 2; }
if [ "${1:-}" = loop ] && [ $((END - $(date -u +%s))) -gt 1208700 ]; then echo "q50s1-digest: END is more than 14 d - 15 min after now: a MiScusi job lives at most 14 d: an END more than 14 d minus 15 min after this job's start is refused (exit 2)" >&2; exit 2; fi
P_PV='{"type":"trigger","variant":"pv",'
P_BS='{"type":"boost_slice",'

digest_day() {
  D="$1"
  n_dirs=0
  for SD in "$ARENA"/h5-q50s1-2*; do
    [ -d "$SD" ] || continue
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
    echo "DIGEST $D dir $(basename "$SD") files $N first $FIRST last $LAST entry_pv_sha256 $E boost_slice_sha256 $B whole_sha256 $W at $(date -u +%FT%TZ)"
  done
  [ "$n_dirs" -gt 0 ] || echo "DIGEST $D no s1 dir under $ARENA at $(date -u +%FT%TZ)"
}

case "${1:-}" in
  once)
    [ "$#" -eq 2 ] || { echo "q50s1-digest: usage: q50s1-digest.sh once YYYY-MM-DD" >&2; exit 2; }
    case "$2" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]) ;; *) echo "q50s1-digest: bad date '$2'" >&2; exit 2 ;; esac
    digest_day "$2"; exit 0 ;;
  loop)
    [ "$#" -eq 1 ] || { echo "q50s1-digest: usage: q50s1-digest.sh loop" >&2; exit 2; } ;;
  *) echo "q50s1-digest: usage: q50s1-digest.sh loop | once YYYY-MM-DD" >&2; exit 2 ;;
esac
echo "q50s1-digest: loop start $(date -u +%FT%TZ); END $(date -u -d "@$END" +%FT%TZ)"
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
