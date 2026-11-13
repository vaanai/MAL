#!/bin/sh
# floor_stage1_proof.sh p0|p1|p2 OUT_NAME [extra args for floor_stage1_proof.py]
# POSIX sh (MiScusi jobs run under sh). Output dir: /data2/mal/scratch/floor-stage1-proof/OUT_NAME (must not exist). Counts and md5 only on stdout.
# One replay at a time (p0/p1 run two sequential replays, p2 the reference, phase A and 9 restart cases). Reads the research-0 hv7 closed hours through symlinks (zstd shim: -f).
set -eu
CMD=${1:?p0|p1|p2}
NAME=${2:?out name}
shift 2
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
BASE=/data2/mal/scratch/floor-stage1-proof
mkdir -p "$BASE/.shim"
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$BASE/.shim/zstd"
chmod +x "$BASE/.shim/zstd"
PATH="$BASE/.shim:$PATH"
export PATH
cd "$REPO"
exec "$PY" scripts/ops/floor_stage1_proof.py "$CMD" --out "$BASE/$NAME" "$@"
