#!/bin/sh
# floor_forward_ab.sh END_HOUR [WORK_DIR]   e.g. floor_forward_ab.sh 2026-11-12T20
# Paper A/B of the live FLOOR rule (l1-floor-v2, file untouched) vs the vault1.0 variant (--set Q_STAR=1.0e9) over tape 2026-11-02T01Z .. END_HOUR (inclusive, closed .zst hours),
# priced at send 160000 lamports per send (token-ATA rent is refunded, not a cost), stakes 0.10 and 0.25, flat and pressure legs. Output: WORK_DIR/ab.json, WORK_DIR/ab.md.
# POSIX sh (MiScusi jobs run under sh). Both replays run concurrently (2 cpus, 16G). Reads the research-0 hv7 derived copy via the existing tapecopy (FLOOR LANE READERS (c)/(d)).
# No incremental mode: a rerun replays from 2026-11-02T01 (a replay of ~10 days took 2 h 17 m; two concurrent runs on 2 cpus are expected to take about the same to ~1.3x).
set -eu
END_HOUR=${1:?end hour like 2026-11-12T20}
WORK=${2:-/data2/mal/scratch/floor-forward-ab/$END_HOUR}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
mkdir -p "$WORK/shim"
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$WORK/shim/zstd"; chmod +x "$WORK/shim/zstd"
PATH="$WORK/shim:$PATH"; export PATH
TO_MS=$("$PY" "$REPO/scripts/research/floor_forward_ab.py" link --end-hour "$END_HOUR" --out "$WORK/tapelink")
FROM_MS=$("$PY" -c 'from datetime import datetime,timezone;print(int(datetime(2026,11,2,1,tzinfo=timezone.utc).timestamp()*1000))')
echo "from_ms=$FROM_MS to_ms=$TO_MS end_hour=$END_HOUR work=$WORK"
rm -rf "$WORK/base" "$WORK/vault"
cd "$REPO"
"$PY" scripts/research/floor_nov_paper.py --root "$WORK/base" --tape-dir "$WORK/tapelink" --from-ms "$FROM_MS" --to-ms "$TO_MS" --send 160000 > "$WORK/base.log" 2>&1 &
P1=$!
"$PY" scripts/research/floor_nov_paper.py --root "$WORK/vault" --tape-dir "$WORK/tapelink" --from-ms "$FROM_MS" --to-ms "$TO_MS" --send 160000 --set Q_STAR=1.0e9 > "$WORK/vault.log" 2>&1 &
P2=$!
R1=0; R2=0
wait $P1 || R1=$?
wait $P2 || R2=$?
echo "base exit $R1, vault exit $R2"
[ "$R1" = 0 ] && [ "$R2" = 0 ] || exit 1
"$PY" scripts/research/floor_nov_pnl.py "$WORK/base/ledger" "$WORK/pnl-base.json" total 1p9 --to-ms "$TO_MS"
"$PY" scripts/research/floor_nov_pnl.py "$WORK/vault/ledger" "$WORK/pnl-vault.json" total 1p9 --to-ms "$TO_MS"
"$PY" scripts/research/floor_forward_ab.py table --base "$WORK/pnl-base.json" --variant "$WORK/pnl-vault.json" --out-json "$WORK/ab.json" --out-md "$WORK/ab.md" --end-hour "$END_HOUR"
echo "done: $WORK/ab.md"
