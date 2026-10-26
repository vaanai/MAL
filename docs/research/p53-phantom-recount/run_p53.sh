#!/bin/sh
# P53 phantom recount (QP-RULING-1026-phantom-rows item 4). Counts only, closed arena hours 2026-10-19T08 .. 2026-10-24T23.
# No price, fill, exit or outcome. No HV4/Q50/HV6 output, ledger, shadow, canary, DBC or meta-capture path is opened.
# Usage: run_p53.sh STEP   STEP = mark | p44 | qsink_day | qsink_netflow | qsink_k0census | burn
# Each counter step runs the counter of record UNCHANGED (sha256-pinned) twice through p53_run.py:
#   --off  (nothing skipped; its output sha256 must equal the original output's: wrapper equivalence)
#   --drops (the lines marked by the HV6 filter, tools/hv6_phantom.py, are skipped)
set -u
date -u +%FT%TZ; hostname
[ "$(hostname)" = mal-research-0 ] || { echo "REFUSED host"; exit 1; }
STEP=${1:?step}
D=docs/research/p53-phantom-recount
T=/data/mal/arena/tape
H23=/data/mal/hunt-1008/push-1016/hunt23
SHARE=/data/mal/hunt-1008/push-1016/p53-phantom-recount
DROPS=$SHARE/p53_drops.json
O=${MISCUSI_OUTPUT_DIR:-$(mktemp -d)}
mkdir -p "$SHARE" "$O"
PY=python3
FAIL=0

hours() {  # hours DAY FROM TO -> file list
  h=$2; while [ "$h" -le "$3" ]; do printf ' %s/tape-%sT%02d.jsonl.zst' "$T" "$1" "$h"; h=$((h + 1)); done
}

check() {  # check NAME OFF_FILE ORIGINAL_FILE
  a=$(sha256sum "$2" | cut -d' ' -f1); b=$(sha256sum "$3" | cut -d' ' -f1)
  if [ "$a" = "$b" ]; then echo "EQUIV_OK $1 $a"; else echo "EQUIV_FAIL $1 off=$a orig=$b"; FAIL=1; fi
}

skipcheck() {  # skipcheck LOG: every fully read file skipped exactly its marked lines
  $PY -c "
import json,sys
r=[json.loads(x) for x in open(sys.argv[1])]
bad=[x for x in r if x['skipped']!=x['expected']]
print('SKIPLOG files', len(r), 'skipped', sum(x['skipped'] for x in r), 'mismatch', len(bad))
sys.exit(1 if bad else 0)" "$1" || FAIL=1
}

run2() {  # run2 NAME SHA OUT_OFF OUT_ON SCRIPT ARGS... (ARGS use @OUT@ for the output path)
  name=$1; sha=$2; off=$3; on=$4; shift 4
  script=$1; shift
  aoff=$(echo "$*" | sed "s#@OUT@#$off#g"); aon=$(echo "$*" | sed "s#@OUT@#$on#g")
  echo "RUN off $name"; P53_SKIPLOG=$O/skip-off-$name.jsonl $PY $D/p53_run.py --off --sha256 "$sha" "$script" $aoff > "$O/stdout-off-$name.txt" 2> "$O/stderr-off-$name.txt" || { echo "RC_FAIL off $name"; tail -5 "$O/stderr-off-$name.txt"; FAIL=1; }
  echo "RUN on $name"; P53_SKIPLOG=$O/skip-on-$name.jsonl $PY $D/p53_run.py --drops "$DROPS" --sha256 "$sha" "$script" $aon > "$O/stdout-on-$name.txt" 2> "$O/stderr-on-$name.txt" || { echo "RC_FAIL on $name"; tail -5 "$O/stderr-on-$name.txt"; FAIL=1; }
  [ -f "$O/skip-on-$name.jsonl" ] && skipcheck "$O/skip-on-$name.jsonl"
}

case "$STEP" in
mark)
  FILES="$(hours 2026-10-19 8 23)"
  for d in 20 21 22 23 24; do FILES="$FILES$(hours 2026-10-$d 0 23)"; done
  N=0; for f in $FILES; do [ -f "$f" ] || { echo "NOT_DECIDABLE missing $f"; exit 1; }; N=$((N + 1)); done
  [ "$N" -eq 136 ] || { echo "REFUSED file count $N"; exit 1; }
  echo "filter_sha256 $(sha256sum tools/hv6_phantom.py | cut -d' ' -f1)"
  $PY $D/p53_mark.py "$O/p53_drops.json" $FILES 2> "$O/mark.stderr" || { echo "mark failed"; tail -5 "$O/mark.stderr"; exit 1; }
  cp "$O/p53_drops.json" "$DROPS"; sha256sum "$DROPS"
  ;;
p44)
  P44=$D/inputs/p44_count.py; SIG=$D/inputs/agent_buyback_signer_sigs_1021_1026.txt
  [ "$(sha256sum $SIG | cut -d' ' -f1)" = b1027804a5a8117d5497c364b53748e12d92cfa49ccaf012401724d560de8c32 ] || { echo "REFUSED sigfile sha"; exit 1; }
  FILES=""; for d in 21 22 23 24; do FILES="$FILES$(hours 2026-10-$d 0 23)"; done
  run2 p44 56d5a21878624971a0b1b1244772d784ea19bd4bc4d2fc53638e7ddad450a7a8 "$O/off-p44_counts.json" "$O/on-p44_counts.json" $P44 @OUT@ $SIG $FILES
  check p44 "$O/off-p44_counts.json" /data/mal/hunt-1008/push-1016/p44-revive-sweep/p44_counts.json
  ;;
qsink_day)
  for d in 20 21 22 23 24; do
    run2 day$d c291cf2b98e655c4936035fb85cca6c158c2e11900ec2594b886e8c2833f18cc "$O/off-day-2026-10-$d.json" "$O/on-day-2026-10-$d.json" $H23/w3-qsink/scripts/qsink_count_day.py --day 2026-10-$d --out @OUT@
    check day$d "$O/off-day-2026-10-$d.json" $H23/w3-qsink/out/day-2026-10-$d.json
  done
  ;;
qsink_netflow)
  for d in 20 21 22 23 24; do
    run2 nf$d 6bea86e9068062cbbd4b278aba5c4b1acf12ac2eca0f33076ae0d23412929e6a "$O/off-netflow2-2026-10-$d.json" "$O/on-netflow2-2026-10-$d.json" $H23/w3-qsink/scripts/qsink_netflow_day_v2.py --day 2026-10-$d --out @OUT@
    check nf$d "$O/off-netflow2-2026-10-$d.json" $H23/w3-qsink/out/netflow2-2026-10-$d.json
  done
  ;;
qsink_k0census)
  run2 k0 c291cf2b98e655c4936035fb85cca6c158c2e11900ec2594b886e8c2833f18cc "$O/off-k0-W.json" "$O/on-k0-W.json" $H23/w3-qsink/scripts/qsink_count_day.py --k0 --out @OUT@
  check k0 "$O/off-k0-W.json" $H23/w3-qsink/out/k0-W.json
  run2 census e16582cabbebe046f8c18ea73f8b42f2db27558c1165e0a12b734bfe90597f77 "$O/off-qsink_counts.json" "$O/on-qsink_counts.json" $H23/w3-qsink/scripts/qsink_count.py @OUT@ $(hours 2026-10-19 8 21)
  check census "$O/off-qsink_counts.json" $H23/w3-qsink/out/qsink_counts.json
  ;;
burn)
  mkdir -p "$O/off-burn" "$O/on-burn"
  run2 burn 180a55dfe2cc6d5d27f184010f31150aab4f9890411bf3858537a51af5a80cde "$O/off-burn" "$O/on-burn" $H23/w3-burn/scripts/plan2_count.py @OUT@
  check burn "$O/off-burn/plan2_counts.json" $H23/w3-burn/out2/plan2_counts.json
  ;;
*) echo "unknown step $STEP"; exit 1 ;;
esac
mkdir -p "$SHARE/$STEP"; cp -r "$O"/. "$SHARE/$STEP/"
echo "FAIL=$FAIL"
printf '{"ok":%s,"summary":"P53 step %s (counts only; equivalence FAIL=%s)"}\n' "$([ $FAIL -eq 0 ] && echo true || echo false)" "$STEP" "$FAIL" > "${MISCUSI_RESULT:-/dev/null}"
date -u +%FT%TZ
[ "$FAIL" -eq 0 ]
