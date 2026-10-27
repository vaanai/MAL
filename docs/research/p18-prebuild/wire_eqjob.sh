#!/bin/sh
# P18 prebuild: 72 h md5 decision-equivalence of the wiring (tools/dbc_p18_live.py) on DBC exploration hours [2026-10-14T00, 2026-10-17T00) only. Counts and md5s, nothing priced.
# usage: sh wire_eqjob.sh REPO_DIR OUT_DIR   (ONE MiScusi job, same shape as docs/research/p18-slot-close/eqjob.sh, #1680: ~20 min per run, peak RSS about 1.1 GB; MemoryMax 4G)
# Pass: both compares print "identical": true with md5 5053b05c3ff0446c1a07b692f15a3430 (the #1670 baseline), late_after_close 0, cfg_gate sights 0 (no fetch needed on the tape).
set -eu
cd "$1"
PY=${PY:-/data/mal/venv/bin/python}
"$PY" -m tools.dbc_p18_wire_equiv run --lag 2 --out "$2/lag2"
"$PY" -m tools.dbc_p18_wire_equiv run --lag 1 --out "$2/lag1"
"$PY" -m tools.dbc_p18_equiv compare "$2/lag2" "$2/lag1" --expect-md5 5053b05c3ff0446c1a07b692f15a3430
