#!/usr/bin/env bash
# Step 1 of the 2026-10-05T05:00:00Z kill review: SNAPSHOT. MiScusi job on mal-fast-0 (streaming only, tiny RSS).
# Reads Oracle (mal-core-0) READ-ONLY with ssh+cat, streams each file through fast-0 to
# mal-research-0:/data/mal/kill-review-1005/snap/, hashes the bytes in flight, then writes MANIFEST.sha256 on
# research-0 from the bytes on disk and requires the two to agree. Never writes on Oracle.
# Refuses before 2026-10-05T05:00:00Z. Refuses to run into an existing snapshot (immutable once copied).
# Runbook: docs/runbooks/kill-review-2026-10-05.md
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/kill-review-1005-common.sh"
kr_guard_time

CORE_CMD="${KR_CORE_CMD:-ssh -o BatchMode=yes mal-core-0}"
RES_CMD="${KR_RESEARCH_CMD:-ssh -o BatchMode=yes mal-research-0}"
SRC_PAPER="${KR_SRC_PAPER:-/var/lib/mal/paper/forward-paper}"
SRC_TAPE="${KR_SRC_TAPE:-/var/lib/mal/sealed/trades}"
SRC_CREATES="${KR_SRC_CREATES:-/var/lib/mal/sealed/jsonl}"
SRC_RESTARTS="${KR_SRC_RESTARTS:-/home/claude/reports/runner-restarts.jsonl}"   # local on fast-0
MIN_FREE_GB="${KR_MIN_FREE_GB:-80}"
# Tape: one day of margin before the window (mints created before 09-28 that trade in it) through the hour ending at the instant.
TAPE_FIRST_HOUR="${KR_TAPE_FIRST_HOUR:-2026-09-27T00}"
TAPE_LAST_HOUR="${KR_TAPE_LAST_HOUR:-2026-10-05T04}"
CREATES_FIRST_DAY="${KR_CREATES_FIRST_DAY:-2026-09-27}"
CREATES_LAST_DAY="${KR_CREATES_LAST_DAY:-2026-10-05}"

[ -f "$SRC_RESTARTS" ] || kr_die "missing local restarts log $SRC_RESTARTS"
$RES_CMD "test ! -e '$KR_SNAP/MANIFEST.sha256'" || kr_die "snapshot already exists at research-0:$KR_SNAP (immutable; not overwriting)"
$RES_CMD "mkdir -p '$KR_SNAP/tape' '$KR_SNAP/creates'"
FREE_GB=$($RES_CMD "df -P -BG '$KR_SNAP' | tail -1 | awk '{print \$4}' | tr -dc 0-9")
[ "$FREE_GB" -ge "$MIN_FREE_GB" ] || kr_die "research-0 has ${FREE_GB} GB free at $KR_SNAP, need $MIN_FREE_GB"

WIRE="$(mktemp -d)"; trap 'rm -rf "$WIRE"' EXIT
: > "$WIRE/wire.sha256"

# Drop a trailing partial line (the file was still being appended to when read), streaming.
TRIM_PY='import sys
i, o, tail = sys.stdin.buffer, sys.stdout.buffer, b""
while True:
    c = i.read(1 << 20)
    if not c:
        break
    c = tail + c
    k = c.rfind(b"\n")
    if k < 0:
        tail = c
        continue
    o.write(c[: k + 1])
    tail = c[k + 1 :]'

# stream_file SRC_ABS DEST_REL KIND: KIND "src" = read on Oracle, "local" = read on this host. trim=1 trims a partial last line.
stream_file() {
  local kind="$1" src="$2" rel="$3" trim="$4" n=0
  rm -f "$WIRE/h"
  local reader
  if [ "$kind" = src ]; then reader=( $CORE_CMD "cat '$src'" ); else reader=( cat "$src" ); fi
  if [ "$trim" = 1 ]; then
    "${reader[@]}" | python3 -c "$TRIM_PY" | tee >(sha256sum | cut -c1-64 > "$WIRE/h") \
      | $RES_CMD "mkdir -p \"\$(dirname '$KR_SNAP/$rel')\" && cat > '$KR_SNAP/$rel'"
  else
    "${reader[@]}" | tee >(sha256sum | cut -c1-64 > "$WIRE/h") \
      | $RES_CMD "mkdir -p \"\$(dirname '$KR_SNAP/$rel')\" && cat > '$KR_SNAP/$rel'"
  fi
  while [ "$(wc -c < "$WIRE/h" 2>/dev/null || echo 0)" -lt 64 ] && [ "$n" -lt 100 ]; do sleep 0.1; n=$((n+1)); done
  local h; h="$(tr -d '[:space:]' < "$WIRE/h")"
  [ "${#h}" -eq 64 ] || kr_die "no wire hash for $src"
  echo "$h  ./$rel" >> "$WIRE/wire.sha256"
  echo "copied $rel $h"
}

# 1. positions.jsonl (live and growing: trimmed), the book config, the restarts log. settlements.jsonl does not exist yet; the score step writes it.
stream_file src "$SRC_PAPER/positions.jsonl" positions.jsonl 1
stream_file src "$SRC_PAPER/forward-paper.json" forward-paper.json 0
stream_file local "$SRC_RESTARTS" runner-restarts.jsonl 0

# 2. tape and creates, from a directory listing taken once.
LIST_T="$($CORE_CMD "ls '$SRC_TAPE'")"
LIST_C="$($CORE_CMD "ls '$SRC_CREATES'")"
hours="$(python3 - "$TAPE_FIRST_HOUR" "$TAPE_LAST_HOUR" <<'PY'
import sys
from datetime import datetime, timedelta
s, e = (datetime.strptime(a, "%Y-%m-%dT%H") for a in sys.argv[1:3])
while s <= e:
    print(s.strftime("%Y-%m-%dT%H")); s += timedelta(hours=1)
PY
)"
n_t=0
for H in $hours; do
  f="$(printf '%s\n' "$LIST_T" | grep -E "^trades-$H\.jsonl(\.zst|\.gz)?$" | head -1 || true)"
  if [ -z "$f" ]; then echo "kill-review-1005: WARN no tape file for hour $H" >&2; echo "$H" >> "$WIRE/missing-tape-hours"; continue; fi
  case "$f" in *.jsonl) trim=1 ;; *) trim=0 ;; esac
  stream_file src "$SRC_TAPE/$f" "tape/$f" $trim; n_t=$((n_t+1))
done
days="$(python3 - "$CREATES_FIRST_DAY" "$CREATES_LAST_DAY" <<'PY'
import sys
from datetime import datetime, timedelta
s, e = (datetime.strptime(a, "%Y-%m-%d") for a in sys.argv[1:3])
while s <= e:
    print(s.strftime("%Y-%m-%d")); s += timedelta(days=1)
PY
)"
n_c=0
for D in $days; do
  f="$(printf '%s\n' "$LIST_C" | grep -E "^observe-$D\.jsonl(\.zst)?$" | head -1 || true)"
  if [ -z "$f" ]; then echo "kill-review-1005: WARN no creates file for $D" >&2; continue; fi
  case "$f" in *.jsonl) trim=1 ;; *) trim=0 ;; esac
  stream_file src "$SRC_CREATES/$f" "creates/$f" $trim; n_c=$((n_c+1))
done
[ "$n_t" -gt 0 ] || kr_die "no tape files copied"
[ "$n_c" -gt 0 ] || kr_die "no creates files copied"

# 3. Manifest on research-0 from the bytes on disk; compare with the in-flight hashes; then make the tree read-only.
$RES_CMD "cd '$KR_SNAP' && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256 && chmod -R a-w ."
$RES_CMD "cat '$KR_SNAP/MANIFEST.sha256'" > "$WIRE/disk.sha256"
sort "$WIRE/wire.sha256" > "$WIRE/wire.sorted"; sort "$WIRE/disk.sha256" > "$WIRE/disk.sorted"
diff -u "$WIRE/wire.sorted" "$WIRE/disk.sorted" || kr_die "in-flight hashes differ from disk hashes: copy corrupted; remove $KR_SNAP on research-0 and rerun"
echo "snapshot ok: $(wc -l < "$WIRE/disk.sha256") files, tape=$n_t creates=$n_c"
[ ! -f "$WIRE/missing-tape-hours" ] || { echo "missing tape hours:"; cat "$WIRE/missing-tape-hours"; }

if [ -n "${MISCUSI_OUTPUT_DIR:-}" ]; then
  mkdir -p "$MISCUSI_OUTPUT_DIR"
  cp "$WIRE/disk.sha256" "$MISCUSI_OUTPUT_DIR/snapshot-MANIFEST.sha256"
  [ ! -f "$WIRE/missing-tape-hours" ] || cp "$WIRE/missing-tape-hours" "$MISCUSI_OUTPUT_DIR/snapshot-missing-tape-hours.txt"
fi
