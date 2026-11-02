#!/bin/sh
# Fast-0 staging job for the tip hour-health table (QP on #1191, conditions S1 to S8). POSIX sh: MiScusi jobs run under sh. BYTE COPIES ONLY.
#
# Copies, as byte-exact files, to research-0 $DEST (default /data2/mal/tip-health/fast-inputs):
#   $OUTDIR/.writer/counts.jsonl      the writer's per-closed-hour counts log (agent-readable: counts, kinds, progs, slot spans, timings)
#   $OUTDIR/.writer/silence.jsonl     staged as an EMPTY file while it does not exist on fast-0 (the writer creates it lazily)
#   $OPS/<name> for each name in $SAMPLERS  the follower sampler segments (listed literally; a segment not yet written is skipped and logged)
#   $OPS/tip-pool-cache-daily.jsonl   the daily pool_cache recorder (QP-RULING-1102 R2 (b))
# NEVER staged (refused in code): .writer/state.json, any tape-* or other file of the writer's out dir, any follower tip file, gaps.jsonl, anything under
# /var/lib/mal/sealed, fast-trades-tip or tip-tape-archive. No parsing, no projection.
#
# Per pass and file: snapshot the source once (the sha256 of the copy is the sha256 of that snapshot, i.e. of the source as read in the same pass), upload to a tmp name,
# verify the remote sha256 equals it, rename. The files are append-only: the staged file must be a byte-prefix of the new snapshot; if not (or if the staged file is longer),
# ALERT and STOP with exit 3 - the staged file is never overwritten. Logs only names, sizes, sha256s and the copy instant.
#
# Env: OUTDIR (default $HOME/mal-data/arena-tipfeed/tape), OPS ($HOME/mal-data/ops), SAMPLERS (default the three segments below), DEST, RSH (command prefix that runs
#      one program on the destination host; default `ssh -o BatchMode=yes mal-research-0`), PERIOD (seconds, capped at 300), ONCE=1 (one pass), END (UTC ISO, loop end).
# Run it as its own MiScusi job on mal-fast-0, chained across the 14-day limit, under its own small MemoryMax at nice 19 / ionice idle. The fixed --unit name makes systemd
# refuse a second scope, and a flock single-instance lock ($LOCK, default ${XDG_RUNTIME_DIR:-/tmp}/tip-hour-inputs-stage.lock) refuses a second copy (exit 9, ALREADY_RUNNING)
# even when a MiScusi cancel left the old scope alive:
#   systemd-run --user --scope --unit=tip-hour-inputs-stage -q -p MemoryMax=64M nice -n 19 ionice -c3 env PERIOD=240 sh scripts/ops/tip-hour-inputs-stage.sh
# Runbook check (exactly ONE line must show; two means a stale copy, stop the older with `systemctl --user stop tip-hour-inputs-stage.scope` or kill its pid):
#   ps -eo pid,etimes,args | grep '[t]ip-hour-inputs-stage.sh'
# SIGTERM/SIGINT exit at once (143/130) and remove the snapshot dir; a pass already running finishes its current command first.
set -u
umask 027
OUTDIR=${OUTDIR:-$HOME/mal-data/arena-tipfeed/tape}
OPS=${OPS:-$HOME/mal-data/ops}
SAMPLERS=${SAMPLERS:-"tip-health-1101.jsonl tip-health-1102.jsonl tip-health-1107.jsonl"}
DEST=${DEST:-/data2/mal/tip-health/fast-inputs}
RSH=${RSH:-"ssh -o BatchMode=yes mal-research-0"}
PERIOD=${PERIOD:-240}
[ "$PERIOD" -gt 300 ] && PERIOD=300

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

case "$OUTDIR$OPS$DEST" in
  *sealed*|*fast-trades-tip*|*tip-tape-archive*|*/var/lib/mal*)
    echo "REFUSED path under a sealed tree"
    exit 4
    ;;
esac
for s in $SAMPLERS; do
  case "$s" in
    */*|*..*|*state.json*|*tape-*|*gaps.jsonl*) echo "REFUSED sampler name $s"; exit 4 ;;
    tip-health-*.jsonl) ;;
    *) echo "REFUSED sampler name $s (only tip-health-<tag>.jsonl)"; exit 4 ;;
  esac
done

command -v flock > /dev/null || { echo "REFUSED flock not found (no single-instance lock)"; exit 1; }
LOCK=${LOCK:-${XDG_RUNTIME_DIR:-/tmp}/tip-hour-inputs-stage.lock}
exec 9> "$LOCK" || exit 1
flock -n 9 || { echo "ALREADY_RUNNING lock=$LOCK at=$(now)"; exit 9; }

TMPD=$(mktemp -d) || exit 1
SLP=
cleanup() {
  [ -n "$SLP" ] && kill "$SLP" 2> /dev/null
  rm -rf "$TMPD"
}
# the handlers must EXIT (a bare trap on TERM would let the loop go on); EXIT runs cleanup once
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# stage_one <source path> <dest name> <req|opt>   rc: 0 ok/skipped, 1 retry next pass, 2 prefix violation (stop)
stage_one() {
  src=$1
  name=$2
  snap="$TMPD/$name"
  if [ -f "$src" ]; then
    cp "$src" "$snap" || { echo "ALERT copy_failed $name at=$(now)"; return 1; }
  elif [ "$name" = silence.jsonl ]; then
    : > "$snap"
  elif [ "$3" = req ]; then
    echo "ALERT source_missing $name at=$(now)"
    return 1
  else
    echo "SKIP_ABSENT $name at=$(now)"
    return 0
  fi
  n=$(wc -c < "$snap" | tr -d ' ')
  sha=$(sha256sum "$snap" | cut -d' ' -f1)
  dst="$DEST/$name"
  $RSH test -e "$dst"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    dn=$($RSH stat -c %s "$dst")
    dsha=$($RSH sha256sum "$dst" | cut -d' ' -f1)
    if [ -z "$dn" ] || [ -z "$dsha" ]; then
      echo "ALERT transport_failed $name at=$(now)"
      return 1
    fi
    if [ "$dn" -gt "$n" ]; then
      echo "ALERT staged_longer_than_source $name staged_bytes=$dn src_bytes=$n at=$(now)"
      return 2
    fi
    psha=$(head -c "$dn" "$snap" | sha256sum | cut -d' ' -f1)
    if [ "$psha" != "$dsha" ]; then
      echo "ALERT not_a_byte_prefix_extension $name staged_bytes=$dn staged_sha256=$dsha src_prefix_sha256=$psha at=$(now)"
      return 2
    fi
    if [ "$dn" -eq "$n" ]; then
      echo "same $name bytes=$n sha256=$sha at=$(now)"
      return 0
    fi
  elif [ "$rc" -ne 1 ]; then
    echo "ALERT transport_failed $name at=$(now)"
    return 1
  fi
  tmp="$DEST/.$name.tmp$$"
  if ! $RSH tee "$tmp" < "$snap" > /dev/null; then
    echo "ALERT upload_failed $name at=$(now)"
    return 1
  fi
  csha=$($RSH sha256sum "$tmp" | cut -d' ' -f1)
  if [ "$csha" != "$sha" ]; then
    $RSH rm -f "$tmp"
    echo "ALERT copy_sha_mismatch $name src_sha256=$sha copy_sha256=$csha at=$(now)"
    return 1
  fi
  if ! $RSH mv -f "$tmp" "$dst"; then
    echo "ALERT rename_failed $name at=$(now)"
    return 1
  fi
  echo "staged $name src_bytes=$n src_sha256=$sha copy_sha256=$csha at=$(now)"
  return 0
}

one_pass() {
  worst=0
  $RSH mkdir -p "$DEST" || { echo "ALERT transport_failed mkdir at=$(now)"; return 1; }
  stage_one "$OUTDIR/.writer/counts.jsonl" counts.jsonl req; r=$?
  [ "$r" -eq 2 ] && return 2
  [ "$r" -gt "$worst" ] && worst=$r
  stage_one "$OUTDIR/.writer/silence.jsonl" silence.jsonl opt; r=$?
  [ "$r" -eq 2 ] && return 2
  [ "$r" -gt "$worst" ] && worst=$r
  for s in $SAMPLERS; do
    stage_one "$OPS/$s" "$s" opt; r=$?
    [ "$r" -eq 2 ] && return 2
    [ "$r" -gt "$worst" ] && worst=$r
  done
  stage_one "$OPS/tip-pool-cache-daily.jsonl" tip-pool-cache-daily.jsonl opt; r=$?
  [ "$r" -eq 2 ] && return 2
  [ "$r" -gt "$worst" ] && worst=$r
  return "$worst"
}

if [ -n "${ONCE:-}" ]; then
  one_pass
  r=$?
  [ "$r" -eq 2 ] && exit 3
  exit "$r"
fi
while :; do
  if [ -n "${END:-}" ] && [ "$(date -u +%s)" -ge "$(date -u -d "$END" +%s)" ]; then
    break
  fi
  one_pass
  r=$?
  if [ "$r" -eq 2 ]; then
    echo "STOPPED prefix violation at=$(now)"
    exit 3
  fi
  # sleep in the background WITHOUT the lock fd, and wait: a signal interrupts `wait` at once (a foreground sleep would delay the trap by up to PERIOD)
  sleep "$PERIOD" 9>&- &
  SLP=$!
  wait "$SLP"
  SLP=
done
