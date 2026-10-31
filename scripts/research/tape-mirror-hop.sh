#!/bin/sh
# tape-mirror hop measurement (a short MiScusi job on mal-fast-0). SYNTHETIC rows only: a writer on mal-research-0 appends synthetic lines (one write
# per line, like the arena feed) to a scratch tape dir, with one closed hour (backfill path) and one hour that is compacted mid-run (live -> closed
# path); the puller mirrors it to mal-fast-0 over the production path (ssh mal-research-0, serve run from the pinned checkout $1). Then the sha256 of
# every file is compared on both hosts, the lag rows are printed, and both scratch dirs are removed. The real arena tape is never read.
# Usage: sh scripts/research/tape-mirror-hop.sh <MAL checkout on research-0 at this job's commit> [seconds, default 240] [lines per second, default 200]
set -u
RPP="$1"
DUR="${2:-240}"
RATE="${3:-200}"
case "$RPP" in /*) ;; *) echo "REFUSED: the remote checkout must be an absolute path"; exit 2 ;; esac
case "$RPP$DUR$RATE" in *[!A-Za-z0-9._/-]*) echo "REFUSED: plain paths and integers only"; exit 2 ;; esac
RSRC=/home/claude/scratch/tape-mirror-hop/src
LDST="$HOME/scratch/tape-mirror-hop/dst"
OUT="${MISCUSI_OUTPUT_DIR:-$HOME/scratch/tape-mirror-hop-out}"
mkdir -p "$OUT"
echo "host=$(hostname) start=$(date -u +%Y-%m-%dT%H:%M:%SZ) head=$(git rev-parse HEAD)"
RH=$(ssh -o BatchMode=yes mal-research-0 "git -C $RPP rev-parse HEAD") || { echo "REFUSED: cannot read the serve checkout"; exit 2; }
[ "$RH" = "$(git rev-parse HEAD)" ] || { echo "REFUSED: the serve checkout is at $RH, not this commit"; exit 2; }
rm -rf "$LDST"
mkdir -p "$LDST"
cat > "$OUT/writer.py" <<'PY'
import json, os, sys, time
from datetime import datetime, timezone
sys.path.insert(0, os.getcwd())
from tools.arena_zst import compact_file
src, dur, rate = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
H = 3_600_000
def hk(ms): return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
def line(i, t): return (json.dumps({"synthetic": True, "i": i, "t_recv_ms": t, "pad": "x" * 380}) + "\n").encode()
now = int(time.time() * 1000)
old = os.path.join(src, f"tape-{hk(now - 3 * H)}.jsonl")
with open(old, "ab") as fh:
    fh.write(b"".join(line(i, now - 3 * H) for i in range(20_000)))
compact_file(old)
prev = os.path.join(src, f"tape-{hk(now - H)}.jsonl")
with open(prev, "ab") as fh:
    fh.write(b"".join(line(i, now - H) for i in range(2_000)))
t0, i, late, closed, fh, cur = time.monotonic(), 0, False, False, None, None
while time.monotonic() - t0 < dur:
    t = int(time.time() * 1000)
    name = os.path.join(src, f"tape-{hk(t)}.jsonl")
    if name != cur:
        if fh:
            fh.close()
        fh, cur = open(name, "ab", buffering=0), name
    fh.write(line(i, t))
    i += 1
    el = time.monotonic() - t0
    if not late and el > 30:
        with open(prev, "ab") as p:
            p.write(b"".join(line(j, now - H) for j in range(100)))
        late = True
    if not closed and el > 60:
        compact_file(prev)
        closed = True
    time.sleep(max(0.0, i / rate - (time.monotonic() - t0)))
fh.close()
print(json.dumps({"lines": i, "prev_compacted": closed}))
PY
ssh -o BatchMode=yes mal-research-0 "cd $RPP && rm -rf $RSRC && mkdir -p $RSRC && exec python3 - $RSRC $DUR $RATE" < "$OUT/writer.py" > "$OUT/writer.log" 2>&1 &
WPID=$!
sleep 3
MISCUSI_RESULT= python3 -u tools/tape_mirror.py pull --ssh-host mal-research-0 --remote-repo "$RPP" --src "$RSRC" --dst "$LDST" --max-s $((DUR + 15)) 2> "$OUT/pull.stderr"
echo "pull rc=$?"
wait $WPID
echo "writer rc=$? $(cat "$OUT/writer.log")"
ssh -o BatchMode=yes mal-research-0 "cd $RSRC && sha256sum tape-*" | sort > "$OUT/src.sha"
(cd "$LDST" && sha256sum tape-*) | sort > "$OUT/dst.sha"
if cmp -s "$OUT/src.sha" "$OUT/dst.sha"; then SAME=1; else SAME=0; fi
echo "files src=$(wc -l < "$OUT/src.sha") dst=$(wc -l < "$OUT/dst.sha") sha256_equal=$SAME"
cp "$LDST/.mirror/lag.jsonl" "$LDST/.mirror/events.jsonl" "$LDST/.mirror/status.json" "$OUT/" 2>/dev/null
python3 - "$OUT" "$SAME" <<'PY'
import json, os, sys
out, same = sys.argv[1], sys.argv[2] == "1"
lag = [json.loads(x) for x in open(os.path.join(out, "lag.jsonl"))]
ev = [json.loads(x) for x in open(os.path.join(out, "events.jsonl"))]
for r in lag:
    print("LAG", json.dumps(r, sort_keys=True))
kinds = {}
for e in ev:
    kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
print("EVENTS", json.dumps(kinds, sort_keys=True))
full = [r for r in lag if r["frames"] >= 100]
def col(k): return sorted(r[k] for r in full if r[k] is not None)
m = {"sha256_equal": same, "minutes": len(full), "frames": sum(r["frames"] for r in lag), "bytes": sum(r["bytes"] for r in lag),
     "minute_new_p50_ms": col("lag_new_p50_ms"), "minute_new_p90_ms": col("lag_new_p90_ms"), "minute_new_max_ms": col("lag_new_max_ms"),
     "minute_old_p90_ms": col("lag_old_p90_ms"), "minute_old_max_ms": col("lag_old_max_ms"), "closed": kinds.get("closed", 0),
     "disconnects": kinds.get("disconnect", 0)}
print("METRICS", json.dumps(m, sort_keys=True))
p = os.environ.get("MISCUSI_RESULT")
if p:
    json.dump({"ok": same and kinds.get("closed", 0) == 2, "metrics": m, "summary": "tape-mirror hop research-0 -> fast-0, synthetic tape"}, open(p, "w"))
PY
rm -rf "$HOME/scratch/tape-mirror-hop"
ssh -o BatchMode=yes mal-research-0 'rm -rf /home/claude/scratch/tape-mirror-hop; ls -d /home/claude/scratch/tape-mirror-hop 2>&1'
ssh -o BatchMode=yes mal-research-0 'pgrep -fa "[t]ape-mirror-hop" || echo no-remote-process'
echo "end=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
