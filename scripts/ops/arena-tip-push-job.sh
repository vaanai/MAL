#!/bin/sh
# arena-tip-push-job.sh - PUSH the arena tip writer's hour files from mal-fast-0 to mal-research-0, byte for byte (QP-RULING-1101-arena-tip-reader B1-B5; HV7 tip plan, Transport B).
# POSIX sh only (MiScusi runs jobs under sh). PAPER ONLY: no key, no wallet, no RPC, 0 Helius credits. ISOLATED: the whole transport is tools/arena_tip_push.py; delete it and this script if QP
# rules that the engine must run on fast-0 (plan option A).
# Run on mal-fast-0 as ONE long MiScusi job, AFTER the writer job. Suggested: machine mal-fast-0, mem_mb 256, cpus 1, resumable true (both ends keep their state in their dirs: a restart
# resumes from the receiver's local sizes and the sender's .push/verified.json), a time limit that covers the window.
# It dials `ssh mal-research-0` (Cloudflare Access; the path tape_mirror's pull uses, in the same direction) and execs `tools/arena_tip_push.py receive` there FROM $REMOTE_REPO, a checkout pinned
# at THIS job's commit (checked below: same HEAD, same bytes of every file the receiver imports, no modified tracked file and nothing untracked under tools/ on either side).
# The derived copy on research-0 lives on /data2 (never /data/mal: the walk-2 walker's disk). The job keeps the sender's exit status; a cancel's SIGTERM is forwarded to it.
#
# Environment:
#   REMOTE_REPO   absolute path of a MAL checkout on research-0 at this job's commit                                     required
#   REMOTE_DST    the derived dir on research-0; must be under /data2/mal/arena-tip-feed/                                default /data2/mal/arena-tip-feed/hv7
#   SRC           the writer's out dir on fast-0; must be under $HOME/mal-data                                           default $HOME/mal-data/arena-tipfeed/tape
#   SSH_HOST      default mal-research-0
#   RETAIN_H      receiver retention by hour name                                                                         default 72
#   BACKFILL_H    closed hours fetched on the first connect (<= RETAIN_H)                                                 default 72
#   MIN_FREE_PCT  integer: the receiver writes nothing below this free-space percentage (state disk_reserve)                default 20
#   PY            python3 (stdlib + zstd CLI)                                                                             default python3
#   DRY=1         print the command and the checks' inputs; run nothing (no ssh, no mkdir).
# Exit: 0 clean stop; 2 refused here, or a receiver MISMATCH / a local source problem (fail closed: a human); 3 the receiver refused (dst not allowed, another receiver holds the lock).
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
DRY=${DRY:-0}
REMOTE_REPO=${REMOTE_REPO:-}
REMOTE_DST=${REMOTE_DST:-/data2/mal/arena-tip-feed/hv7}
SRC=${SRC:-$HOME/mal-data/arena-tipfeed/tape}
SSH_HOST=${SSH_HOST:-mal-research-0}
RETAIN_H=${RETAIN_H:-72}
BACKFILL_H=${BACKFILL_H:-72}
MIN_FREE_PCT=${MIN_FREE_PCT:-20}
PY=${PY:-python3}
REQUIRE_HOST=mal-fast-0

refuse() { echo "REFUSED: $*"; exit 2; }
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; return 0; }
plain_abs() { case "$1" in /*) ;; *) return 1 ;; esac; case "$1" in *[!A-Za-z0-9._/-]*) return 1 ;; esac; case "/$1/" in */../*) return 1 ;; esac; return 0; }

[ -n "$REMOTE_REPO" ] || refuse "REMOTE_REPO is required (a research-0 checkout pinned at this job's commit)"
plain_abs "$REMOTE_REPO" || refuse "REMOTE_REPO must be an absolute path of [A-Za-z0-9._/-] without .."
plain_abs "$REMOTE_DST" || refuse "REMOTE_DST must be an absolute path of [A-Za-z0-9._/-] without .."
plain_abs "$SRC" || refuse "SRC must be an absolute path of [A-Za-z0-9._/-] without .."
case "$SSH_HOST" in ''|*[!A-Za-z0-9.-]*) refuse "SSH_HOST must be a plain host alias" ;; esac
case "$REMOTE_DST" in /data2/mal/arena-tip-feed/?*) ;; *) refuse "REMOTE_DST must be under /data2/mal/arena-tip-feed/ (never /data/mal)" ;; esac
case "$SRC" in "$HOME/mal-data"/?*) ;; *) refuse "SRC must be under $HOME/mal-data" ;; esac
is_int "$RETAIN_H" && is_int "$BACKFILL_H" && is_int "$MIN_FREE_PCT" || refuse "RETAIN_H, BACKFILL_H and MIN_FREE_PCT must be non-negative integers"
{ [ "$BACKFILL_H" -ge 1 ] && [ "$BACKFILL_H" -le "$RETAIN_H" ]; } || refuse "BACKFILL_H must be 1..RETAIN_H"
{ [ "$MIN_FREE_PCT" -ge 1 ] && [ "$MIN_FREE_PCT" -le 50 ]; } || refuse "MIN_FREE_PCT must be 1..50"

set -- nice -n 19 ionice -c3 "$PY" -u tools/arena_tip_push.py send --src "$SRC" --ssh-host "$SSH_HOST" --remote-repo "$REMOTE_REPO" --remote-dst "$REMOTE_DST" \
  --retain-hours "$RETAIN_H" --backfill-hours "$BACKFILL_H" --min-free-pct "$MIN_FREE_PCT"
if [ "$DRY" = 1 ]; then echo "DRY=1: would check host=$REQUIRE_HOST, zstd, the pinned receive code at $SSH_HOST:$REMOTE_REPO, then run: $*"; exit 0; fi

[ "$(hostname)" = "$REQUIRE_HOST" ] || refuse "runs on $REQUIRE_HOST only"
command -v zstd >/dev/null 2>&1 || refuse "the zstd CLI is required"
command -v "$PY" >/dev/null 2>&1 || refuse "python not found: $PY"
FILES="tools/arena_tip_push.py tools/arena_tip_tape_writer.py tools/arena_tip_adapter.py tools/arena_tip_reader.py tools/arena_zst.py tools/tape_mirror.py tools/decision_forwarder.py"
STATE="git rev-parse HEAD && sha256sum $FILES && git status --porcelain --untracked-files=no && git status --porcelain --untracked-files=all -- tools"
LOCAL=$(sh -c "$STATE") || refuse "cannot read the job checkout"
REMOTE=$(ssh -o BatchMode=yes -o ConnectTimeout=30 "$SSH_HOST" "cd $REMOTE_REPO && $STATE") || refuse "cannot read the receive checkout $SSH_HOST:$REMOTE_REPO"
[ "$LOCAL" = "$REMOTE" ] || refuse "the receive checkout is not this job's commit, or its files differ or are dirty (job, then receive, below)
$LOCAL
---
$REMOTE"
[ "$(printf '%s\n' "$LOCAL" | wc -l)" -eq 8 ] || refuse "the job checkout is dirty (a modified tracked file, or an untracked file under tools/)"
[ -d "$SRC" ] || refuse "SRC does not exist yet: start the writer job first"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) src=$SRC dst=$SSH_HOST:$REMOTE_DST retain_h=$RETAIN_H backfill_h=$BACKFILL_H"
printf '%s\n' "$LOCAL" | sed -n '2,8p' | sed 's/^/JOB pin /'
"$@" &
PID=$!
trap 'kill -TERM "$PID" 2>/dev/null' TERM INT
wait "$PID"
RC=$?
trap - TERM INT
if kill -0 "$PID" 2>/dev/null; then wait "$PID"; RC=$?; fi
echo "JOB end $(date -u +%FT%TZ) rc=$RC"
exit "$RC"
