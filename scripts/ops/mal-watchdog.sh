#!/bin/sh
# MiScusi job wrapper (sh). Run from the repo root.
exec /usr/bin/python3 -I scripts/ops/mal_watchdog.py --loop --duration-s "${DURATION_S:-1209600}" \
  --config "${WD_CONFIG:-/data/mal/hunt-1008/ops/watchdog/config.json}" \
  --state-dir "${WD_STATE:-/data/mal/hunt-1008/ops/watchdog/}"
