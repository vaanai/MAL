#!/bin/sh
# P18 slot-close: 72 h md5 decision-equivalence on DBC exploration hours [2026-10-14T00, 2026-10-17T00) only. Counts and md5s, nothing priced.
# usage: sh eqjob.sh REPO_DIR OUT_DIR     (run as ONE MiScusi job; the two engine runs are sequential, ~20 min each, peak RSS about 1.1 GB)
# Pass: both compares print "identical": true with md5 5053b05c3ff0446c1a07b692f15a3430 (the #1670 baseline) and late_after_close 0.
set -eu
cd "$1"
PY=${PY:-/data/mal/venv/bin/python}
python_run() { "$PY" -m tools.dbc_p18_equiv "$@"; }
python_run run --prune 1 --signals 1 --out "$2/s1"
python_run run --prune 1 --signals 2 --out "$2/s2"
python_run compare "$2/s1" "$2/s2" --expect-md5 5053b05c3ff0446c1a07b692f15a3430
