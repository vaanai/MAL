#!/bin/sh
# floor_killcheck_extract.sh NAME --from-ms N --to-ms N   (mal-fast-0, step A of the FLOOR kill-line check; a manager job, uses sudo -n)
# 1. sudo python3 -I floor_killcheck_extract.py: reads ONLY entry rows and lane_restart_shadow rows of the live decision log in the window; writes $HOME/mal-data/ops/killcheck/NAME.jsonl (new file).
# 2. rsync (ssh BatchMode, the route of arena-tip-push-job.sh) to mal-research-0:/data2/mal/scratch/floor-killcheck/in/ with --ignore-existing (never overwrite).
# 3. checks the sha256 on the far side. Prints OUT_SHA256 and REMOTE_SHA256 for step B's --extract-sha256. POSIX sh. No pool id is printed.
set -eu
NAME=${1:?name}
shift
case "$NAME" in ''|*[!A-Za-z0-9._-]*) echo "REFUSED: name must be [A-Za-z0-9._-]"; exit 2 ;; esac
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-python3}
ROOT=/var/lib/mal-live/floor-lane/decisions-root
OUT="$HOME/mal-data/ops/killcheck/$NAME.jsonl"
DST=/data2/mal/scratch/floor-killcheck/in
SSH_HOST=${SSH_HOST:-mal-research-0}
[ "$(hostname)" = "mal-fast-0" ] || { echo "REFUSED: runs on mal-fast-0 only"; exit 2; }
cd "$REPO"
sudo -n "$PY" -I scripts/ops/floor_killcheck_extract.py --decision-root "$ROOT" --out "$OUT" "$@"
[ -f "$OUT" ] || { echo "REFUSED: no extract written"; exit 2; }
rsync -e "ssh -o BatchMode=yes" --ignore-existing --chmod=F0644 --rsync-path="mkdir -p $DST && rsync" "$OUT" "$SSH_HOST:$DST/"
LOCAL=$(sha256sum "$OUT" | cut -d' ' -f1)
REMOTE=$(ssh -o BatchMode=yes "$SSH_HOST" "sha256sum $DST/$NAME.jsonl" | cut -d' ' -f1)
echo "OUT_SHA256=$LOCAL"
echo "REMOTE_SHA256=$REMOTE"
[ "$LOCAL" = "$REMOTE" ] || { echo "REFUSED: the far copy differs from the near one (or an older file of that name exists there)"; exit 2; }
echo "EXTRACT_PUSHED $NAME.jsonl"
