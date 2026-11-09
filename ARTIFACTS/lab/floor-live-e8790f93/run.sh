#!/bin/sh
# Reproduce the e879 clamp review. Must import tools from a tree at e8790f93
# (blob 6f353716), not from this review branch's 3710 executor.
set -eu
ROOT="${E879_ROOT:-/tmp/mal-e879}"
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
python3 -m pytest "$HERE/test_floor_live_e879_clamps.py" -q
