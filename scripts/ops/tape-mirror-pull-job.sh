#!/bin/sh
# tape-mirror-pull-job.sh - mirror the research-0 arena tape (/data/mal/arena/tape) to mal-fast-0, byte for byte, for the r4-b DRY host (PR #1143).
# POSIX sh only (MiScusi runs jobs under sh). PAPER ONLY: no key, no wallet, no RPC, 0 Helius credits.
# Run on mal-fast-0 as ONE long MiScusi job (never a session cron, nohup or tmux). Suggested: machine mal-fast-0, mem_mb 256, cpus 1, resumable true
# (the mirror dir IS the state: a restart trims its own torn tail and resumes from the local sizes), a time limit that covers the DRY run.
# What it runs: python3 -u tools/tape_mirror.py pull, which dials `ssh mal-research-0` (Cloudflare Access, the path the P46 forwarder measured) and
# execs tools/tape_mirror.py serve there FROM $REMOTE_REPO, a checkout pinned at THIS job's commit (checked below: same HEAD, same bytes of the two
# serve-side files, both clean). Python is exec'd, so a MiScusi cancel's SIGTERM reaches the puller (state `stopped`, no orphaned writer).
#
# Environment:
#   REMOTE_REPO   absolute path of a MAL checkout on research-0 at this job's commit                       required
#   SSH_HOST      default mal-research-0
#   SRC           the tape dir on research-0                                                             default /data/mal/arena/tape
#   DST           the mirror dir on fast-0; must be under $HOME/mal-data                                 default $HOME/mal-data/arena-mirror/tape
#   RETAIN_H      hours kept (by hour name)                                                              default 72
#   BACKFILL_H    closed hours fetched on the first connect (<= RETAIN_H; r4-b warm-up needs 52)          default 64
#   MIN_FREE_PCT  integer: nothing is written below this free-space percentage (state disk_reserve)       default 12
#   PY            python3 with the repo importable (stdlib only)                                         default python3
#   DRY=1         print the checks' inputs and the command; run nothing (no ssh, no mkdir).
# Exit: 0 clean stop; 2 refused here (or a mismatch stop by the puller); 3 the puller refused (another instance holds the lock, bad arguments).
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
DRY=${DRY:-0}
REMOTE_REPO=${REMOTE_REPO:-}
SSH_HOST=${SSH_HOST:-mal-research-0}
SRC=${SRC:-/data/mal/arena/tape}
DST=${DST:-$HOME/mal-data/arena-mirror/tape}
RETAIN_H=${RETAIN_H:-72}
BACKFILL_H=${BACKFILL_H:-64}
MIN_FREE_PCT=${MIN_FREE_PCT:-12}
PY=${PY:-python3}
REQUIRE_HOST=mal-fast-0

refuse() { echo "REFUSED: $*"; exit 2; }
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; return 0; }
plain_abs() { # absolute, only [A-Za-z0-9._/-], no ".." component
  case "$1" in /*) ;; *) return 1 ;; esac
  case "$1" in *[!A-Za-z0-9._/-]*) return 1 ;; esac
  case "/$1/" in */../*) return 1 ;; esac
  return 0
}

[ -n "$REMOTE_REPO" ] || refuse "REMOTE_REPO is required (a research-0 checkout pinned at this job's commit)"
plain_abs "$REMOTE_REPO" || refuse "REMOTE_REPO must be an absolute path of [A-Za-z0-9._/-] without .."
plain_abs "$SRC" || refuse "SRC must be an absolute path of [A-Za-z0-9._/-] without .."
plain_abs "$DST" || refuse "DST must be an absolute path of [A-Za-z0-9._/-] without .."
case "$SSH_HOST" in ''|*[!A-Za-z0-9.-]*) refuse "SSH_HOST must be a plain host alias" ;; esac
case "$DST" in "$HOME/mal-data"/?*) ;; *) refuse "DST must be under $HOME/mal-data" ;; esac
case "$DST" in /data/mal/arena|/data/mal/arena/*) refuse "DST must not be under /data/mal/arena" ;; esac
is_int "$RETAIN_H" && is_int "$BACKFILL_H" && is_int "$MIN_FREE_PCT" || refuse "RETAIN_H, BACKFILL_H and MIN_FREE_PCT must be non-negative integers"
{ [ "$BACKFILL_H" -ge 1 ] && [ "$BACKFILL_H" -le "$RETAIN_H" ]; } || refuse "BACKFILL_H must be 1..RETAIN_H"
[ "$RETAIN_H" -le 336 ] || refuse "RETAIN_H must be at most 336 (14 days; size the disk first)"
{ [ "$MIN_FREE_PCT" -ge 1 ] && [ "$MIN_FREE_PCT" -le 50 ]; } || refuse "MIN_FREE_PCT must be 1..50"

set -- "$PY" -u tools/tape_mirror.py pull --ssh-host "$SSH_HOST" --remote-repo "$REMOTE_REPO" --src "$SRC" --dst "$DST" \
  --retain-hours "$RETAIN_H" --backfill-hours "$BACKFILL_H" --min-free-pct "$MIN_FREE_PCT"
if [ "$DRY" = 1 ]; then echo "DRY=1: would check host=$REQUIRE_HOST, zstd, and the pinned serve code at $SSH_HOST:$REMOTE_REPO, then run: $*"; exit 0; fi

[ "$(hostname)" = "$REQUIRE_HOST" ] || refuse "runs on $REQUIRE_HOST only"
command -v zstd >/dev/null 2>&1 || refuse "the zstd CLI is required (closed hours are verified and read through it)"
command -v "$PY" >/dev/null 2>&1 || refuse "python not found: $PY"
FILES="tools/tape_mirror.py tools/decision_forwarder.py"
LOCAL=$(git rev-parse HEAD && sha256sum $FILES && git status --porcelain -- $FILES) || refuse "cannot read the job checkout"
REMOTE=$(ssh -o BatchMode=yes -o ConnectTimeout=30 "$SSH_HOST" "cd $REMOTE_REPO && git rev-parse HEAD && sha256sum $FILES && git status --porcelain -- $FILES") \
  || refuse "cannot read the serve checkout $SSH_HOST:$REMOTE_REPO"
[ "$LOCAL" = "$REMOTE" ] || refuse "the serve checkout is not this job's commit, or its serve files differ or are dirty (job, then serve, below)
$LOCAL
---
$REMOTE"
[ "$(printf '%s\n' "$LOCAL" | wc -l)" -eq 3 ] || refuse "the serve files are dirty in the job checkout"
mkdir -p "$DST" || refuse "cannot create DST"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) dst=$DST retain_h=$RETAIN_H backfill_h=$BACKFILL_H min_free_pct=$MIN_FREE_PCT"
df -B1 --output=size,avail,pcent "$DST" | tail -1 | sed 's/^/JOB disk size_avail_pcent /'
exec "$@"
