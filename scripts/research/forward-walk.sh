#!/usr/bin/env bash
# DEC-016 forward walk (MiScusi job on mal-research-0). Runs only after the owner OKs DEC-016.
# Submit: miscusi_job_submit machine=mal-research-0 params={"start":"YYYY-MM-DDTHH"} resumable=true
#   command: bash scripts/research/forward-walk.sh   (time limit 10080 min; resubmit to continue; idempotent)
# Takes one Helius lock slot (blocking), walks each complete hour >= START about 5 min after it ends,
# then runs `tools.exp012_forward verify` on it (writes D/verify.jsonl with sha256).
# ~18k getBlock credits per hour at 1 credit each (~0.43M/day), about 37.5 min per hour at --rps 8, after the
# 200 ms slot step (SIMD-0525, epoch 1053, ~2026-10-09T14:30Z; it was ~13.4k credits, ~0.32M/day before). Output /data/mal/blocks/forward-1002.
set -u
export PYTHONPATH="$PWD" PYTHONUNBUFFERED=1; PY=/data/mal/venv/bin/python
D=/data/mal/blocks/forward-1002; START="${MISCUSI_PARAM_START:?start hour YYYY-MM-DDTHH}"; mkdir -p $D
[ -f $D/credit-probe.json ] || echo '{"confirmed_credits_per_getblock":1}' > $D/credit-probe.json
echo '{"pct":1,"note":"waiting for Helius slot (blocking)"}' > $MISCUSI_PROGRESS
L=""; while [ -z "$L" ]; do for i in 4 3 2 1; do exec 9>/data/mal/locks/helius-$i.lock; if flock -n 9; then L=$i; break; fi; exec 9>&-; done; [ -n "$L" ] || sleep 15; done
echo "holding helius slot $L"
set -a; . /var/lib/mal/backfill/helius.env; set +a
while :; do
  NOWH=$(date -u +%Y-%m-%dT%H); MIN=$(date -u +%M)
  LAST=$($PY -c "from datetime import datetime,timedelta;print((datetime.strptime('$NOWH','%Y-%m-%dT%H')-timedelta(hours=1)).strftime('%Y-%m-%dT%H'))")
  if [ "$MIN" -ge 5 ]; then
    for H in $($PY -c "
from datetime import datetime,timedelta
s=datetime.strptime('$START','%Y-%m-%dT%H'); e=datetime.strptime('$LAST','%Y-%m-%dT%H')
while s<=e: print(s.strftime('%Y-%m-%dT%H')); s+=timedelta(hours=1)"); do
      if grep -q "\"hour\": *\"$H\"" $D/verify.jsonl 2>/dev/null && $PY - "$D" "$H" <<'PYEOF'
import json,sys
d,h=sys.argv[1],sys.argv[2]; last=None
for l in open(d+"/verify.jsonl"):
    r=json.loads(l)
    if r.get("hour")==h: last=r
sys.exit(0 if last and not last.get("issues") else 1)
PYEOF
      then continue; fi
      U=$($PY -c "from datetime import datetime,timedelta;print((datetime.strptime('$H','%Y-%m-%dT%H')+timedelta(hours=1)).strftime('%Y-%m-%dT%H:00:00Z'))")
      $PY -m tools.pump_history_backfill --until $U --hours 1 --out $D --credits-file $D/credit-probe.json --credit-cap 12000000 --max-bytes 322122547200 --rps 8 --lookup-rps 2 --workers 8 || echo "walk error $H"
      $PY -m tools.exp012_forward verify --walk-dir $D --hour $H > /dev/null || echo "verify not OK $H"
    done
    C=$($PY -c "import json;print(json.load(open('$D/checkpoint.json')).get('credits_used'))" 2>/dev/null)
    echo "{\"pct\":50,\"note\":\"forward walk through $LAST, credits $C\"}" > $MISCUSI_PROGRESS
  fi
  sleep $(( 60 * ( (65 - $(date -u +%-M)) % 60 ) + 30 ))
done
