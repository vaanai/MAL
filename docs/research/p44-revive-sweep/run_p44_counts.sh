#!/bin/sh
# P44 revive-trigger sweep: counts only on closed arena tape hours 2026-10-21T00..2026-10-24T23 (96 files).
# No price, fill, exit or outcome. No ledger, bots.json, shadow, canary, HV4/Q50/DBC or meta-capture path is opened.
# G0 (the H5 / EXP-024 V-band WSOL universe) is dropped at first print by the counter (P44 change 2).
set -u
date -u +%FT%TZ; hostname
[ "$(hostname)" = mal-research-0 ] || { echo "REFUSED host"; exit 1; }
D=docs/research/p44-revive-sweep
echo "counter_sha256 $(sha256sum $D/p44_count.py | cut -d' ' -f1)"
echo "sigfile_sha256 $(sha256sum $D/data/agent_buyback_signer_sigs_1021_1026.txt | cut -d' ' -f1)"
T=/data/mal/arena/tape
FILES=""; N=0
for d in 21 22 23 24; do for h in 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23; do
  f="$T/tape-2026-10-${d}T${h}.jsonl.zst"
  [ -f "$f" ] || { echo "NOT_DECIDABLE missing $f"; exit 1; }
  FILES="$FILES $f"; N=$((N + 1)); done; done
[ "$N" -eq 96 ] || { echo "REFUSED file count $N"; exit 1; }
O=${MISCUSI_OUTPUT_DIR:-$(mktemp -d)}
python3 $D/p44_count.py "$O/p44_counts.json" $D/data/agent_buyback_signer_sigs_1021_1026.txt $FILES
RC=$?; echo "counter_rc=$RC"
[ "$RC" -eq 0 ] || exit 1
mkdir -p /data/mal/hunt-1008/push-1016/p44-revive-sweep
cp "$O/p44_counts.json" /data/mal/hunt-1008/push-1016/p44-revive-sweep/p44_counts.json
sha256sum "$O/p44_counts.json"
printf '{"ok":true,"summary":"P44 counts written (counts only, 96 closed arena hours 10-21..10-24, G0 not tracked)"}\n' > "${MISCUSI_RESULT:-/dev/null}"
date -u +%FT%TZ
