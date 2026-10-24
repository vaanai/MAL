#!/bin/sh
# P18 build 1: m10 decision-equivalence replay on Swap2-era exploration hours [2026-10-14T00, 2026-10-16T04) only. Counts and md5s, nothing priced.
set -eu
cd "$1"
exec python3 -m tools.dbc_fwd --replay --start 2026-10-14T00 --end 2026-10-16T04 --out "$2"
