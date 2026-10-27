#!/bin/sh
# P18 prebuild: 72 h md5 decision-equivalence of the wiring (tools/dbc_p18_live.py) on DBC exploration hours [2026-10-14T00, 2026-10-17T00) only. Counts and md5s, nothing priced.
# usage: sh wire_eqjob.sh REPO_DIR OUT_DIR   (ONE MiScusi job, same shape as docs/research/p18-slot-close/eqjob.sh, #1680: ~20 min per run, peak RSS about 1.1 GB; MemoryMax 4G, time limit 3 h)
# REPO_DIR MUST be a worktree at the registration-candidate sha (after the #1067 fix); re-run at any later sha. Not submitted by the builder.
# Pass (tools.dbc_p18_wire_equiv check, QP #1067): both runs md5 5053b05c3ff0446c1a07b692f15a3430 with the #1670 head (seq 1174), hours_streamed 72, decisions_live 551, late_after_close 0,
# late_rows 0, cfg_gate sights 0 and rpc_calls 0, wire.notifications > 0, slot_close.signals_advanced > 0, wire.code_sha256 == the sha256 of the files in REPO_DIR. Any miss = exit 1.
set -eu
cd "$1"
PY=${PY:-/data/mal/venv/bin/python}
"$PY" -m tools.dbc_p18_wire_equiv run --lag 2 --out "$2/lag2"
"$PY" -m tools.dbc_p18_wire_equiv run --lag 1 --out "$2/lag1"
"$PY" -m tools.dbc_p18_equiv compare "$2/lag2" "$2/lag1" --expect-md5 5053b05c3ff0446c1a07b692f15a3430
"$PY" -m tools.dbc_p18_wire_equiv check "$2/lag2" "$2/lag1"
