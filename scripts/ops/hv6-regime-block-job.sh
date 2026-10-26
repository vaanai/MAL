#!/bin/sh
# HV6 regime-block producer (DESIGN section 3, R3/R4): a host-side loop that writes <HV6_ROOT>/regime/block-<as_of_ms>.json (+ radar-<as_of_ms>.jsonl, the exact bytes
# read) by calling tools/hv6_regime_block.py block. POSIX sh (MiScusi runs jobs under sh). PAPER only; reads the radar out-dir and the pinned radar files, writes
# only under the regime copy dir. Never reads drill/, HV4/Q50 output or any tape.
#
# Cadence (matches the design): (1) NIGHTLY block with as_of = 00:02:00Z of each UTC day (before the 00:05Z review); written as soon as the loop is past that
# instant (a late start still writes it, with the same as_of, so the bytes are the same); (2) INTRADAY block with as_of = now whenever radar.jsonl has a NEW record
# (its size changed since the last block) - the radar writes one record per run (about 07:11Z / 07:40Z daily), so this is what the intraday regime review reads.
# Polls every POLL_S (default 60 s). Existing identical files are fine (write-once); different bytes for the same as_of refuse.
#
# REQUIRED environment (no defaults for the pins; a missing or mismatching pin refuses to start):
#   HV6_RADAR_CODE            absolute path of the radar code the radar JOB runs (e.g. <merge checkout>/tools/change_radar.py)
#   HV6_RADAR_CODE_SHA256     its sha256 (registered)
#   HV6_RADAR_SEALS           absolute path of tools/change_radar_seals.json the radar job runs; sha256 must be 5a2f8979...ca61
#   HV6_RADAR_WATCH           absolute path of the watch file the radar job runs
#   HV6_RADAR_WATCH_SHA256    its sha256 (registered)
# Optional: HV6_RADAR_OUT_DIR (must be /data/mal/change-radar-arena1), HV6_REGIME_DIR (must be /data/mal/arena/haiku-v6/regime), POLL_S,
#   HV6_BLOCK_ONCE=1 (one pass then exit), HV6_BLOCK_NOW=<unix seconds> (tests), HV6_BLOCK_TEST=1 (tests only: lifts the two directory checks).
# Usage (from the repo checkout at the registration sha):  sh scripts/ops/hv6-regime-block-job.sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
PINNED_OUT=/data/mal/change-radar-arena1
PINNED_REGIME=/data/mal/arena/haiku-v6/regime
PINNED_SEALS_SHA=5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61
OUT=${HV6_RADAR_OUT_DIR:-$PINNED_OUT}
RDIR=${HV6_REGIME_DIR:-$PINNED_REGIME}
POLL_S=${POLL_S:-60}
refuse() { echo "{\"hv6_regime_block_job\":\"refused\",\"why\":\"$1\"}"; exit 2; }
for v in HV6_RADAR_CODE HV6_RADAR_CODE_SHA256 HV6_RADAR_SEALS HV6_RADAR_WATCH HV6_RADAR_WATCH_SHA256; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || refuse "missing_$v"
done
case "$HV6_RADAR_CODE$HV6_RADAR_SEALS$HV6_RADAR_WATCH" in *'"'*|*'\'*) refuse bad_path_char ;; esac
for p in "$HV6_RADAR_CODE" "$HV6_RADAR_SEALS" "$HV6_RADAR_WATCH"; do
  case "$p" in /*) : ;; *) refuse path_not_absolute ;; esac
  [ -f "$p" ] || refuse missing_file
done
if [ "${HV6_BLOCK_TEST:-}" != 1 ]; then
  [ "$OUT" = "$PINNED_OUT" ] || refuse out_dir_not_pinned
  [ "$RDIR" = "$PINNED_REGIME" ] || refuse regime_dir_not_pinned
fi
sha() { sha256sum "$1" | cut -d' ' -f1; }
[ "$(sha "$HV6_RADAR_CODE")" = "$HV6_RADAR_CODE_SHA256" ] || refuse radar_code_sha_mismatch
[ "$(sha "$HV6_RADAR_WATCH")" = "$HV6_RADAR_WATCH_SHA256" ] || refuse watch_sha_mismatch
[ "$(sha "$HV6_RADAR_SEALS")" = "$PINNED_SEALS_SHA" ] || refuse seals_sha_mismatch
unset ANTHROPIC_API_KEY MAL_HV4_CHAIN_STATE || true
mkdir -p "$RDIR"
PINS="$RDIR/pins.json"
NEWPINS=$(printf '{"radar_code_path":"%s","radar_code_sha256":"%s","seals_path":"%s","seals_sha256":"%s","watch_path":"%s","watch_sha256":"%s","out_dir":"%s","venue_tape":""}\n' \
  "$HV6_RADAR_CODE" "$HV6_RADAR_CODE_SHA256" "$HV6_RADAR_SEALS" "$PINNED_SEALS_SHA" "$HV6_RADAR_WATCH" "$HV6_RADAR_WATCH_SHA256" "$OUT")
if [ -f "$PINS" ]; then
  [ "$(cat "$PINS")" = "$NEWPINS" ] || refuse pins_changed_in_window   # a pin that changes during a window is a new registration, never an edit
else
  printf '%s\n' "$NEWPINS" > "$PINS"
  chmod 444 "$PINS"
fi
block() {  # $1 = as_of_ms
  python3 -m tools.hv6_regime_block block --as-of-ms "$1" --out-dir "$OUT" --venue-tape '' --copy-dir "$RDIR" --pins "$PINS" \
    --radar-code "$HV6_RADAR_CODE" --seals "$HV6_RADAR_SEALS" --watch "$HV6_RADAR_WATCH" > /dev/null
  echo "{\"hv6_regime_block_job\":\"block\",\"as_of_ms\":$1}"
}
size() { if [ -f "$OUT/radar.jsonl" ]; then wc -c < "$OUT/radar.jsonl" | tr -d ' '; else echo 0; fi; }
LAST=-1
echo "{\"hv6_regime_block_job\":\"start\",\"regime_dir\":\"$RDIR\",\"out_dir\":\"$OUT\",\"poll_s\":$POLL_S}"
while :; do
  NOW=${HV6_BLOCK_NOW:-$(date -u +%s)}
  DAY0=$(( NOW / 86400 * 86400 ))
  NIGHT=$(( (DAY0 + 120) * 1000 ))
  if [ $(( NOW )) -ge $(( DAY0 + 120 )) ] && [ ! -f "$RDIR/block-$NIGHT.json" ]; then
    block "$NIGHT"
  fi
  SZ=$(size)
  if [ "$SZ" != "$LAST" ]; then
    block $(( NOW * 1000 ))   # the first pass, then every new radar record
    LAST=$SZ
  fi
  [ "${HV6_BLOCK_ONCE:-}" = 1 ] && exit 0
  sleep "$POLL_S"
done
