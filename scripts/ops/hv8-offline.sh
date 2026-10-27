#!/bin/sh
# HV8 offline stage, pinned command lines (C3). Run under sh as a MiScusi job: sh scripts/ops/hv8-offline.sh <adapter|count|dump|labels>
# Paper only, 0 API. Sets: HV8_MANIFEST_SHA for `labels` (the ratings manifest sha256 that RATINGS-PIN.md carries).
set -eu
cd "$(dirname "$0")/../.."
PY=/data/mal/venv/bin/python
ROOT=/data2/mal/hv8-train
BLK=/data/mal/clean-view/explore-0814
check_report() {
  "$PY" -I -c '
import json, sys
r = json.load(open("/data2/mal/hv8-train/tape/adapter_report.json"))
ok = r.get("preamble_rows") == 0 and r.get("first_hour") == "2026-08-14T12" and r.get("last_hour") == "2026-08-28T11"
print(json.dumps({"adapter_report_check": "ok" if ok else "STOP_BACK_TO_QP", "preamble_rows": r.get("preamble_rows"), "first_hour": r.get("first_hour"), "last_hour": r.get("last_hour")}))
sys.exit(0 if ok else 3)'
}
case "${1:-}" in
  adapter)
    "$PY" -B -m tools.arena_clean_adapter --block "$BLK/w1" "$BLK/w2" "$BLK/w3" "$BLK/w4" "$BLK/w5" "$BLK/w6" "$BLK/w7" --out "$ROOT/tape"
    check_report ;;
  count)
    check_report
    "$PY" -B -m tools.hv8_dump --tape "$ROOT/tape" --scratch "$ROOT/scratch-count" --count-only ;;
  dump)
    check_report
    "$PY" -B -m tools.hv8_dump --tape "$ROOT/tape" --scratch "$ROOT/scratch-dump" --out "$ROOT/dump" ;;
  labels)
    : "${HV8_MANIFEST_SHA:?set HV8_MANIFEST_SHA to the pinned ratings manifest sha256}"
    check_report
    "$PY" -B -m tools.hv8_labels --dump "$ROOT/dump" --ratings "$ROOT/ratings" --manifest-sha "$HV8_MANIFEST_SHA" --tape "$ROOT/tape" --scratch "$ROOT/scratch-labels" --out "$ROOT/labels" --feed-delay-slots 7 --sps 0.368 --sensitivity ;;
  *) echo "usage: sh scripts/ops/hv8-offline.sh adapter|count|dump|labels" >&2; exit 64 ;;
esac
