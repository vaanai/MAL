#!/bin/sh
# HV6 regime-block producer (DESIGN section 3, R3/R4): a host-side loop that writes <HV6_ROOT>/regime/block-<as_of_ms>.json (+ radar-<as_of_ms>.jsonl, the exact bytes
# read) by calling tools/hv6_regime_block.py block. POSIX sh. PAPER only; reads the radar out-dir and the pinned radar files, writes only under the regime copy dir.
# Never reads drill/, HV4/Q50 output or any tape.
#
# RUN IT AS A MISCUSI JOB (miscusi_job_submit, resumable: true), as every long-running HV6 job: no standalone systemd --user unit, nohup or tmux. The Console and the
# owner see MiScusi jobs only.
#
# Cadence (matches the design): (1) NIGHTLY block with as_of = 00:02:00Z of each UTC day (before the 00:05Z review); written as soon as the loop is past that
# instant (a late start still writes it, with the same as_of, so the bytes are the same); (2) INTRADAY block with as_of = now whenever radar.jsonl has a NEW record
# (its size changed since the last block). The radar writes one record per run (about 07:11Z / 07:40Z daily).
# A block is built only when radar.jsonl ends in a newline AND its size is unchanged across one STABLE_S wait (a torn line would make the builder emit `absent`,
# and a write-once nightly block could never be rewritten). If a built block is nevertheless `absent` because of a parse problem, it is removed and retried on the
# next poll. Polls every POLL_S (default 60 s).
#
# Pins (R3): pins.json holds the REGISTERED pins (the env values below, written once, read-only). A pin that differs from the registered one, a changed pins file or a
# radar file whose sha differs is NEVER a refusal to start: the loop keeps running and the builder writes `absent` blocks with `pin_mismatch` from the change instant,
# so the last `ok` block cannot stay in force. The script refuses only on a missing value, a relative or missing path, or (outside tests) an unpinned directory.
#
# REQUIRED environment:
#   HV6_RADAR_CODE            absolute path of the radar code the radar JOB runs (e.g. <merge checkout>/tools/change_radar.py)
#   HV6_RADAR_CODE_SHA256     its REGISTERED sha256
#   HV6_RADAR_SEALS           absolute path of tools/change_radar_seals.json the radar job runs (registered sha256 5a2f8979...ca61, held by the builder)
#   HV6_RADAR_WATCH           absolute path of the watch file the radar job runs
#   HV6_RADAR_WATCH_SHA256    its REGISTERED sha256
# Optional: HV6_RADAR_OUT_DIR (must be /data/mal/change-radar-arena1), HV6_REGIME_DIR (must be /data/mal/arena/haiku-v6/regime), POLL_S, STABLE_S (default 2),
#   HV6_PYTHON (default /data/mal/venv/bin/python), HV6_BLOCK_ONCE=1 (one pass then exit), HV6_BLOCK_NOW=<unix seconds> (tests), HV6_BLOCK_TEST=1 (tests only:
#   lifts the two directory checks).
# Usage (from the repo checkout at the registration sha):  sh scripts/ops/hv6-regime-block-job.sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
PINNED_OUT=/data/mal/change-radar-arena1
PINNED_REGIME=/data/mal/arena/haiku-v6/regime
PINNED_SEALS_SHA=5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61
PY=${HV6_PYTHON:-/data/mal/venv/bin/python}
OUT=${HV6_RADAR_OUT_DIR:-$PINNED_OUT}
RDIR=${HV6_REGIME_DIR:-$PINNED_REGIME}
POLL_S=${POLL_S:-60}
STABLE_S=${STABLE_S:-2}
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
[ -x "$PY" ] || refuse python_missing
if [ "${HV6_BLOCK_TEST:-}" != 1 ]; then
  [ "$OUT" = "$PINNED_OUT" ] || refuse out_dir_not_pinned
  [ "$RDIR" = "$PINNED_REGIME" ] || refuse regime_dir_not_pinned
fi
sha() { sha256sum "$1" | cut -d' ' -f1; }
unset ANTHROPIC_API_KEY MAL_HV4_CHAIN_STATE || true
mkdir -p "$RDIR"
PINS="$RDIR/pins.json"
NEWPINS=$(printf '{"radar_code_path":"%s","radar_code_sha256":"%s","seals_path":"%s","seals_sha256":"%s","watch_path":"%s","watch_sha256":"%s","out_dir":"%s","venue_tape":""}\n' \
  "$HV6_RADAR_CODE" "$HV6_RADAR_CODE_SHA256" "$HV6_RADAR_SEALS" "$PINNED_SEALS_SHA" "$HV6_RADAR_WATCH" "$HV6_RADAR_WATCH_SHA256" "$OUT")
if [ -f "$PINS" ]; then
  # the registered pins stay as written; a different environment is a change that the builder reports as `absent` + pin_mismatch from now on
  [ "$(cat "$PINS")" = "$NEWPINS" ] || echo '{"hv6_regime_block_job":"pins_differ_from_registered","effect":"absent_blocks_with_pin_mismatch"}'
else
  printf '%s\n' "$NEWPINS" > "$PINS"
  chmod 444 "$PINS"
fi
[ "$(sha "$HV6_RADAR_CODE")" = "$HV6_RADAR_CODE_SHA256" ] || echo '{"hv6_regime_block_job":"radar_code_sha_differs","effect":"absent_blocks_with_pin_mismatch"}'
[ "$(sha "$HV6_RADAR_WATCH")" = "$HV6_RADAR_WATCH_SHA256" ] || echo '{"hv6_regime_block_job":"watch_sha_differs","effect":"absent_blocks_with_pin_mismatch"}'
[ "$(sha "$HV6_RADAR_SEALS")" = "$PINNED_SEALS_SHA" ] || echo '{"hv6_regime_block_job":"seals_sha_differs","effect":"absent_blocks_with_pin_mismatch"}'
RADAR="$OUT/radar.jsonl"
size() { if [ -f "$RADAR" ]; then wc -c < "$RADAR" | tr -d ' '; else echo 0; fi; }
ends_nl() { [ ! -s "$RADAR" ] || [ "$(tail -c 1 "$RADAR" | od -An -c | tr -d ' ')" = '\n' ]; }
stable() {  # the file ends in a newline AND its size is unchanged across one STABLE_S wait
  ends_nl || return 1
  S1=$(size)
  sleep "$STABLE_S"
  [ "$(size)" = "$S1" ] && ends_nl
}
parse_absent() {  # $1 = as_of_ms; true when the block just written is `absent` for a parse reason (bad records, no pin mismatch)
  "$PY" - "$RDIR/block-$1.json" <<'PYEOF'
import json, sys
b = json.load(open(sys.argv[1]))
sys.exit(0 if (b.get("radar_status") == "absent" and not b.get("pin_mismatch") and (b.get("input") or {}).get("n_bad_records", 0) > 0) else 1)
PYEOF
}
build() {  # $1 = as_of_ms; 0 = a usable block (or an absent one for a pin reason) is in place, 1 = retry on the next poll
  "$PY" -m tools.hv6_regime_block block --as-of-ms "$1" --out-dir "$OUT" --venue-tape '' --copy-dir "$RDIR" --pins "$PINS" \
    --radar-code "$HV6_RADAR_CODE" --seals "$HV6_RADAR_SEALS" --watch "$HV6_RADAR_WATCH" > /dev/null
  if parse_absent "$1"; then
    rm -f "$RDIR/block-$1.json" "$RDIR/radar-$1.jsonl"
    echo "{\"hv6_regime_block_job\":\"retry\",\"as_of_ms\":$1,\"why\":\"absent_parse\"}"
    return 1
  fi
  echo "{\"hv6_regime_block_job\":\"block\",\"as_of_ms\":$1}"
}
LAST=-1
echo "{\"hv6_regime_block_job\":\"start\",\"regime_dir\":\"$RDIR\",\"out_dir\":\"$OUT\",\"poll_s\":$POLL_S}"
while :; do
  NOW=${HV6_BLOCK_NOW:-$(date -u +%s)}
  DAY0=$(( NOW / 86400 * 86400 ))
  NIGHT=$(( (DAY0 + 120) * 1000 ))
  if [ "$NOW" -ge $(( DAY0 + 120 )) ] && [ ! -f "$RDIR/block-$NIGHT.json" ]; then
    if stable; then build "$NIGHT" || true; else echo '{"hv6_regime_block_job":"wait","why":"radar_file_not_stable"}'; fi
  fi
  SZ=$(size)
  if [ "$SZ" != "$LAST" ]; then
    if stable; then
      if build $(( NOW * 1000 )); then LAST=$SZ; fi   # LAST moves only after a block was kept: a torn or parse-absent attempt is retried
    else
      echo '{"hv6_regime_block_job":"wait","why":"radar_file_not_stable"}'
    fi
  fi
  [ "${HV6_BLOCK_ONCE:-}" = 1 ] && exit 0
  sleep "$POLL_S"
done
