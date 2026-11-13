#!/bin/sh
# floor_killcheck.sh RUN_NAME --extract FILE --extract-sha256 SHA [more args for floor_killcheck.py]   (mal-research-0, step B of the FLOOR kill-line check)
# POSIX sh (MiScusi jobs run under sh). Out dir: /data2/mal/scratch/floor-killcheck/RUN_NAME (must not exist). Counts and order ids on stdout. One reference replay.
# The tape symlinks need the zstd shim of the proof wrapper (-f).
set -eu
NAME=${1:?run name}
shift
case "$NAME" in ''|*[!A-Za-z0-9._-]*) echo "REFUSED: run name must be [A-Za-z0-9._-]"; exit 2 ;; esac
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
BASE=/data2/mal/scratch/floor-killcheck
mkdir -p "$BASE/.shim" "$BASE/in"
chmod 0750 "$BASE/in"
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$BASE/.shim/zstd"
chmod +x "$BASE/.shim/zstd"
PATH="$BASE/.shim:$PATH"
export PATH
cd "$REPO"
exec "$PY" scripts/ops/floor_killcheck.py --out "$BASE/$NAME" "$@"
