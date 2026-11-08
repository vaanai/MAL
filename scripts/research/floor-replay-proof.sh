#!/bin/sh
# floor-replay-proof.sh: real-block decision-equivalence proof for the FLOOR lane host (DEC-035 lane, floor-lane-1). PAPER / replay only: no key, no wallet, no send.
# Runs the lane host (tools/floor_lane_host.py replay) and the arena paper host (HostV2, bot l1-floor-v2) on the same closed tape slice and the same table seed, then compares.
#
#   sh scripts/research/floor-replay-proof.sh FROM_MS TO_MS SEED_FROM_HOUR [TAPE_DIR]
#   e.g. sh scripts/research/floor-replay-proof.sh 1793872800000 1793959200000 2026-10-28T01
#
# Outputs under ${FLOOR_REPLAY_OUT:-/data2/mal/floor-replay}/<stamp>/{lane,paper}; <stamp> is fresh (an existing one is refused). Prints counts, md5s, refusal counts.
# Prints RESULT {"ok":..,"verdict":"PASS"|"FAIL","metrics":{..}} and writes that JSON (without the word RESULT) to $MISCUSI_RESULT when set. Runs under sh (MiScusi jobs): no bash-isms.
set -u

if [ "$#" -lt 3 ]; then
  echo "usage: $0 FROM_MS TO_MS SEED_FROM_HOUR [TAPE_DIR]" >&2
  exit 64
fi
FROM_MS=$1
TO_MS=$2
SEED=$3
TAPE=${4:-/data/mal/arena/tape}
OUT_BASE=${FLOOR_REPLAY_OUT:-/data2/mal/floor-replay}
PY=${FLOOR_REPLAY_PY:-/data/mal/venv/bin/python}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
STAMP=${FLOOR_REPLAY_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}
OUT=$OUT_BASE/$STAMP

case "$OUT" in
  /data/mal/arena|/data/mal/arena/*) echo "refusing an output root inside /data/mal/arena" >&2; exit 64 ;;
esac
if [ -e "$OUT" ]; then
  echo "output root $OUT exists; a replay never reuses one" >&2
  exit 64
fi
mkdir -p "$OUT/lane" "$OUT/paper" || exit 1

fail() {
  # $1 = reason; write a FAIL result and stop
  echo "FAIL: $1" >&2
  if [ -n "${MISCUSI_RESULT:-}" ]; then
    printf '{"ok":false,"verdict":"FAIL","metrics":{"error":"%s","out":"%s"}}\n' "$1" "$OUT" > "$MISCUSI_RESULT"
  fi
  exit 1
}

cd "$REPO" || exit 1
# zstd 1.5.5 ignores a symlink given to `zstd -dc` ("is a symbolic link, ignoring", exit 1), and tools/floor_lane_host.rebuild_table_since feeds the table seed through a temp dir of
# symlinks. A shim first on PATH adds -f for this process tree only; no tool file is edited. (Finding for the manager: the same hazard hits `run --since-hour` wherever zstd >= 1.5.)
mkdir -p "$OUT/shim" || exit 1
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$OUT/shim/zstd"
chmod 755 "$OUT/shim/zstd"
PATH="$OUT/shim:$PATH"
export PATH
printf '{"root":"%s/lane","tape_dir":"%s","clock_start_ms":%s}\n' "$OUT" "$TAPE" "$FROM_MS" > "$OUT/lane-config.json"
echo "== out $OUT  from_ms=$FROM_MS to_ms=$TO_MS seed=$SEED tape=$TAPE head=$(git rev-parse --short HEAD)"

echo "== lane: scratch register + replay"
"$PY" scripts/research/floor_replay_proof.py register --registry "$OUT/lane/bots.json" --from-ms "$FROM_MS" > "$OUT/lane-register.log" 2>&1 || fail "lane register"
"$PY" tools/floor_lane_host.py replay --config "$OUT/lane-config.json" --from-ms "$FROM_MS" --to-ms "$TO_MS" --seed-from-hour "$SEED" > "$OUT/lane.log" 2>&1 || { tail -n 5 "$OUT/lane.log" >&2; fail "lane replay"; }
tail -n 1 "$OUT/lane.log" | cut -c1-600

echo "== paper: HostV2 l1-floor-v2 (stack v2), same block and seed"
"$PY" scripts/research/floor_replay_proof.py paper --root "$OUT/paper" --tape-dir "$TAPE" --from-ms "$FROM_MS" --to-ms "$TO_MS" --seed-from-hour "$SEED" > "$OUT/paper.log" 2>&1 || { tail -n 5 "$OUT/paper.log" >&2; fail "paper replay"; }
tail -n 1 "$OUT/paper.log" | cut -c1-600

echo "== compare"
"$PY" scripts/research/floor_replay_proof.py compare --lane "$OUT/lane" --paper "$OUT/paper" > "$OUT/compare.json"
RC=$?
cat "$OUT/compare.json"
if [ ! -s "$OUT/compare.json" ]; then
  fail "compare produced no output"
fi
echo "RESULT $(cat "$OUT/compare.json")"
if [ -n "${MISCUSI_RESULT:-}" ]; then
  cp "$OUT/compare.json" "$MISCUSI_RESULT"
fi
exit "$RC"
