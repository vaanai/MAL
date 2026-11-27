#!/bin/sh
# H capture job per QP-RULING-1127-ce1-successor-H-capture R1-R9.
# Replaces #3460. Submit as a MiScusi job; reads only R7 fields of the board.
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec /usr/bin/python3 -I -B "$REPO/scripts/ops/osb_h_capture.py" \
  --board-dir /home/claude/data/open-sim \
  --out-dir /data/mal/hunt-1008/ops/holdout-H-1202 \
  --from 2026-12-02T02:00:00Z \
  --until 2026-12-02T12:00:00Z \
  --pairs osb-ce1:osb-a,osb-c:osb-a \
  --poll-s 60 \
  --expect-board-version-prefix osb_board.py=0343d90f02fde0a79c2bdc62a5c316c5ea97f511
