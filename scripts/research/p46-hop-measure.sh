#!/bin/sh
# P46 hop measurement (MiScusi job on mal-fast-0). SYNTHETIC rows only. Pulls over the real ssh path fast-0 -> mal-research-0 (Cloudflare
# Access), measures append-on-research-0 -> visible-on-fast-0, then removes the scratch dirs on both hosts.
# Usage: sh scripts/research/p46-hop-measure.sh <remote MAL checkout path on research-0>
set -eu
RPP="$1"
OUT="${MISCUSI_OUTPUT_DIR:-$HOME/scratch/p46-out}"
mkdir -p "$OUT" "$HOME/scratch"
echo "host=$(hostname) user=$(whoami) start=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 tools/decision_forwarder.py measure --ssh-host mal-research-0 --remote-dir '~/scratch/p46-transport-hop' \
  --remote-pythonpath "$RPP" --local-dir "$HOME/scratch/p46-transport-hop" --out "$OUT/hop.json" > "$OUT/hop.stdout" || echo "measure rc=$?"
echo "end=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rm -rf "$HOME/scratch/p46-transport-hop"
echo "cleanup check (expect no dir, no process):"
ssh -o BatchMode=yes mal-research-0 'ls -d ~/scratch/p46-transport-hop 2>&1; pgrep -fa "p46-transport-hop" || echo no-remote-process'
ls -d "$HOME/scratch/p46-transport-hop" 2>&1 || true
python3 - "$OUT/hop.json" <<'PY'
import json, os, sys
r = json.load(open(sys.argv[1]))
m = {"vis_p50_ms": r["append_done_to_visible_ms"]["p50"], "vis_p90_ms": r["append_done_to_visible_ms"]["p90"],
     "vis_p99_ms": r["append_done_to_visible_ms"]["p99"], "vis_max_ms": r["append_done_to_visible_ms"]["max"],
     "n": r["append_done_to_visible_ms"]["n"], "rtt_p50_ms": r["echo_rtt_ms"]["p50"], "offset_err_ms": r["clock_offset_err_ms"],
     "bytes_equal": r["bytes_equal"]}
print(json.dumps(m))
p = os.environ.get("MISCUSI_RESULT")
if p:
    json.dump({"ok": bool(r["bytes_equal"]), "metrics": m, "summary": "p46 hop research-0 -> fast-0, synthetic log"}, open(p, "w"))
PY
