#!/bin/sh
# floor_holdcap_run.sh: MiScusi job body for the FLOOR hold-cap screen (docs/research/floor-holdcap/DECLARE.md). Runs under sh on mal-research-0. PAPER only; in-sample; not a gate read.
# Makes three manifests (0444) from the osb-a manifest A.json that differ only in book_id, title, out_root and rule_overrides.HOLD_S (absent / 43200 / 21600), makes three 0700 out roots,
# runs osb_follow.py for each book in turn (start_hour 2026-11-03T03, --max-hours 369 = end 2026-11-18T12:00Z; the E2 held-out window starts there and is never read),
# each under systemd-run --user --scope -p MemoryMax=8G, then floor_holdcap_read.py --wallet-sim.
# stdout: manifest sha256, the processed-hour count and decisions md5 per book, and the reader's summary lines. Book output (ledger, tape, status) is never printed; osb_follow's own output goes to a log file.
set -eu
REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="${PY:-/data/mal/venv/bin/python}"
BASE="${HOLDCAP_BASE:-/data2/mal/scratch/floor-holdcap}"
SRC="${HOLDCAP_SRC_MANIFEST:-/data2/mal/scratch/floor-open-sim/manifests/A.json}"
MAX_HOURS=369
START_HOUR="2026-11-03T03"

mkdir -p "$BASE/manifests" "$BASE/logs"
chmod 0700 "$BASE"

make_manifest() { # id hold_s(or "none") -> writes $BASE/manifests/<id>.json (0444)
  id="$1"; hold="$2"; dest="$BASE/manifests/$id.json"
  out="$BASE/out-$id"
  mkdir -p "$out"; chmod 0700 "$out"; setfacl -b -k "$out"
  if [ -e "$dest" ]; then chmod u+w "$dest"; fi
  "$PY" -I - "$SRC" "$dest" "$id" "$hold" "$out" "$START_HOUR" <<'EOF'
import json, sys
src, dest, bid, hold, out, start = sys.argv[1:7]
m = json.load(open(src))
m["book_id"] = bid
m["title"] = "FLOOR hold-cap screen: %s (in-sample dry copy of osb-a)" % bid
m["out_root"] = out
m["start_hour"] = start
ov = dict(m.get("rule_overrides") or {})
ov.pop("HOLD_S", None)
if hold != "none":
    ov["HOLD_S"] = int(hold)
m["rule_overrides"] = ov
json.dump(m, open(dest, "w"), indent=1, sort_keys=True)
EOF
  chmod 0444 "$dest"
  echo "manifest $id sha256=$(sha256sum "$dest" | cut -d' ' -f1)"
}

make_manifest holdcap-control24 none
make_manifest holdcap-h12 43200
make_manifest holdcap-h6 21600

for id in holdcap-control24 holdcap-h12 holdcap-h6; do
  systemd-run --user --scope -q -p MemoryMax=8G nice -n 19 ionice -c3 \
    "$PY" "$REPO/scripts/research/osb_follow.py" "$BASE/manifests/$id.json" --once --max-hours "$MAX_HOURS" > "$BASE/logs/$id.log" 2>&1 \
    || { echo "osb_follow $id exit=$?"; exit 1; }
  out="$BASE/out-$id"
  echo "book $id hours_processed=$(wc -l < "$out/md5-by-hour.jsonl") decisions_md5=$(cut -d' ' -f1 "$out/decisions.md5" | head -c 32)"
done

"$PY" "$REPO/scripts/research/floor_holdcap_read.py" "$BASE/out-holdcap-control24" "$BASE/out-holdcap-h12" "$BASE/out-holdcap-h6" --wallet-sim
