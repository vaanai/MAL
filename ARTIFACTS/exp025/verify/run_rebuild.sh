#!/bin/sh
# C1-NF VERIFY rebuild (JUDGE-4 3.3.1): 01_wallet_daily -> 10_meta -> 11_passA per day (2 workers) -> 12_passC (2 chunks) -> 14_export -> 16_confirm.
# Pinned scripts copied to ./scripts with path-only patches (PATCHES.diff). Exploration tape only. POSIX sh (no bash-isms).
V=/data/mal/hunt-1008/c1nf-verify
PY=/data/mal/audit-1008/venv/bin/python
MLPY=/data/mal/venv/bin/python
cd $V || exit 1
mkdir -p logs out/grid out/cand out/candx out/mout ml wl tmp_duck
prog() { [ -n "$MISCUSI_PROGRESS" ] && printf '{"pct":%s,"note":"%s"}' "$1" "$2" > "$MISCUSI_PROGRESS"; echo "[$(date -u +%FT%TZ)] $2"; }
waitroom() {
  while :; do
    m=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
    l=$(awk '{print int($1)}' /proc/loadavg)
    if [ "$m" -ge 30 ] && [ "$l" -le 40 ]; then return 0; fi
    echo "waiting: memavail ${m}G load ${l}"; sleep 60
  done
}

waitroom
prog 2 "01_wallet_daily"
$PY scripts/01_wallet_daily.py > logs/01_wallet.log 2>&1 || { echo FAIL01; tail -5 logs/01_wallet.log; exit 1; }
prog 15 "10_meta"
$PY scripts/10_meta.py > logs/10_meta.log 2>&1 || { echo FAIL10; tail -5 logs/10_meta.log; exit 1; }
ls $V/../c1-cascade-postgrad/logs/A_*.log | sed 's#.*/A_##; s#\.log##' | sort > work/days.txt
prog 18 "11_passA x $(wc -l < work/days.txt) days, 2 workers"
cat work/days.txt | xargs -P 2 -I{} sh -c '
  while :; do m=$(awk "/MemAvailable/{print int(\$2/1048576)}" /proc/meminfo); l=$(awk "{print int(\$1)}" /proc/loadavg); [ "$m" -ge 30 ] && [ "$l" -le 40 ] && break; sleep 60; done
  [ -f out/cand/{}.parquet ] || '"$PY"' scripts/11_passA.py {} > logs/A_{}.log 2>&1; tail -1 logs/A_{}.log'
# retry any day that failed (sequential)
for d in $(cat work/days.txt); do
  if [ ! -f out/cand/$d.parquet ]; then waitroom; echo "retry $d"; $PY scripts/11_passA.py $d > logs/A_$d.log 2>&1; tail -1 logs/A_$d.log; fi
done
for d in $(cat work/days.txt); do [ -f out/cand/$d.parquet ] || { echo "MISSING cand $d"; exit 1; }; done
prog 60 "12_passC"
DISC=$(grep '^2026-08' work/days.txt | tr '\n' ' ')
CONF=$(grep '^2026-09' work/days.txt | tr '\n' ' ')
$PY scripts/12_passC.py $DISC > logs/C_disc.log 2>&1 &
P1=$!
$PY scripts/12_passC.py $CONF > logs/C_conf.log 2>&1
wait $P1
for d in $(cat work/days.txt); do [ -f out/candx/$d.parquet ] || { echo "MISSING candx $d"; exit 1; }; done
prog 75 "14_export"
$PY scripts/14_export.py ml/disc.npz $DISC > logs/export_disc.log 2>&1 || { echo FAILEXP; exit 1; }
$PY scripts/14_export.py ml/conf.npz $CONF > logs/export_conf.log 2>&1 || { echo FAILEXP; exit 1; }
cat logs/export_disc.log logs/export_conf.log
prog 80 "16_confirm (pinned scorer, rule.json)"
cp $V/../c1-cascade-postgrad/ml/rule.json ml/rule.json
$MLPY scripts/16_confirm.py ml/rule.json ml/disc.npz ml/conf.npz ml/confirm_primary.json > logs/confirm_primary.log 2>&1 || { echo FAIL16; tail -20 logs/confirm_primary.log; exit 1; }
cat logs/confirm_primary.log
prog 100 "rebuild done"
du -sh $V
