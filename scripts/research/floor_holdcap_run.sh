#!/bin/sh
# floor_holdcap_run.sh: MiScusi job body for the FLOOR hold-cap screen (docs/research/floor-holdcap/DECLARE.md). Runs under sh on mal-research-0. PAPER only; in-sample; not a gate read.
# Makes three manifests (0444) from the osb-a manifest A.json that differ only in book_id, title, out_root, seed_from_hour, start_hour and rule_overrides.HOLD_S (absent / 43200 / 21600),
# makes three 0700 out roots under /data2/mal/scratch/floor-open-sim (osb_follow.guard_paths), runs osb_follow.py for each book in turn FROM A CLEAN DETACHED WORKTREE AT THE PASS COMMIT
# 457795f4 (SEALS-BRIEF line (k); refused otherwise), each under systemd-run --user --scope -p MemoryMax=8G, then floor_holdcap_read.py (from THIS checkout, the PR's commit) with --wallet-sim.
# Window: seed_from_hour 2026-11-02T01, start_hour 2026-11-10T10, --max-hours 194 = end 2026-11-18T12:00Z; the E2 held-out window starts there and is never read.
# Env: BOARD_BEST_OF (required; the board's best-of-N at the run instant), START_WALLET_SOL (default 3.96808 = DEC-035 Am.14: 0.75 + 8 slots x 0.40226), SIM_REPO, HOLDCAP_BASE, HOLDCAP_SRC_MANIFEST.
# HOLDCAP_CHECK_ONLY=1: only the hour-count check of the existing out roots (used by the test); no manifest, no follower, no reader.
# stdout: manifest sha256, the processed-hour count and the last decision md5 per book, INCOMPLETE lines, and the reader's summary lines. Book output (ledger, tape, status) is never printed;
# osb_follow's own output goes to a log file. A book with fewer than 194 hours prints INCOMPLETE and the reader is not run (an unequal book is not compared).
set -eu
REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="${PY:-/data/mal/venv/bin/python}"
BASE="${HOLDCAP_BASE:-/data2/mal/scratch/floor-open-sim/holdcap}"
SRC="${HOLDCAP_SRC_MANIFEST:-/data2/mal/scratch/floor-open-sim/manifests/A.json}"
SIM_REPO="${SIM_REPO:-/data2/mal/wt/osb-457795f4}"
SIM_SHA=457795f4d0cb252656fd48f795d3abd617d5c380
START_WALLET_SOL="${START_WALLET_SOL:-3.96808}"
MAX_HOURS=194
SEED_HOUR="2026-11-02T01"
START_HOUR="2026-11-10T10"
IDS="holdcap-control24 holdcap-h12 holdcap-h6"

check_hours() { # id -> prints the book line; sets INCOMPLETE=1 when fewer than MAX_HOURS hours were processed
  id="$1"; log="$BASE/$id/md5-by-hour.jsonl"
  if [ -f "$log" ]; then hrs=$(wc -l < "$log"); else hrs=0; fi
  last=none
  if [ "$hrs" -gt 0 ]; then last=$("$PY" -I -c 'import json,sys; print(str(json.loads(open(sys.argv[1]).read().strip().split("\n")[-1]).get("decision_md5","none"))[:32])' "$log" 2>/dev/null || echo none); fi
  echo "book $id hours_processed=$hrs of $MAX_HOURS last_decision_md5=$last"
  if [ "$hrs" -lt "$MAX_HOURS" ]; then INCOMPLETE=1; echo "INCOMPLETE book $id hours_processed=$hrs < $MAX_HOURS"; fi
}

INCOMPLETE=0
if [ "${HOLDCAP_CHECK_ONLY:-0}" = 1 ]; then
  for id in $IDS; do check_hours "$id"; done
  if [ "$INCOMPLETE" -ne 0 ]; then echo "INCOMPLETE: a book with fewer hours than the others is not compared; reader not run"; exit 1; fi
  exit 0
fi

: "${BOARD_BEST_OF:?BOARD_BEST_OF (the board's best-of-N at the run instant) is required}"

# the follower runs only from a clean detached worktree at the PASS commit (SEALS-BRIEF line (k))
[ "$(git -C "$SIM_REPO" rev-parse HEAD 2>/dev/null)" = "$SIM_SHA" ] || { echo "REFUSED: $SIM_REPO is not at $SIM_SHA"; exit 2; }
if git -C "$SIM_REPO" symbolic-ref -q HEAD >/dev/null 2>&1; then echo "REFUSED: $SIM_REPO is not a detached HEAD"; exit 2; fi
[ -z "$(git -C "$SIM_REPO" status --porcelain 2>/dev/null)" ] || { echo "REFUSED: $SIM_REPO is not clean"; exit 2; }

mkdir -p "$BASE"
chmod 0700 "$BASE"; setfacl -b -k "$BASE"
mkdir -p "$BASE/manifests" "$BASE/logs"

make_manifest() { # id hold_s(or "none") -> writes $BASE/manifests/<id>.json (0444)
  id="$1"; hold="$2"; dest="$BASE/manifests/$id.json"
  out="$BASE/$id"
  mkdir -p "$out"; chmod 0700 "$out"; setfacl -b -k "$out"
  if [ -e "$dest" ]; then chmod u+w "$dest"; fi
  "$PY" -I - "$SRC" "$dest" "$id" "$hold" "$out" "$SEED_HOUR" "$START_HOUR" <<'EOF'
import json, sys
src, dest, bid, hold, out, seed, start = sys.argv[1:8]
m = json.load(open(src))
m["book_id"] = bid
m["title"] = "FLOOR hold-cap screen: %s (in-sample dry copy of osb-a)" % bid
m["out_root"] = out
m["seed_from_hour"] = seed
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

for id in $IDS; do
  systemd-run --user --scope -q -p MemoryMax=8G nice -n 19 ionice -c3 \
    "$PY" "$SIM_REPO/scripts/research/osb_follow.py" "$BASE/manifests/$id.json" --once --max-hours "$MAX_HOURS" > "$BASE/logs/$id.log" 2>&1 \
    || { echo "osb_follow $id exit=$?"; exit 1; }
  check_hours "$id"
done
if [ "$INCOMPLETE" -ne 0 ]; then echo "INCOMPLETE: a book with fewer hours than the others is not compared; reader not run"; exit 1; fi

"$PY" "$REPO/scripts/research/floor_holdcap_read.py" "$BASE/holdcap-control24" "$BASE/holdcap-h12" "$BASE/holdcap-h6" --wallet-sim --start-wallet-sol "$START_WALLET_SOL" --board-best-of "$BOARD_BEST_OF"
