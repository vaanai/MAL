#!/bin/sh
# Reproduce the f68c presend review. Must import tools from a tree at f68cf56f
# (blob 556d1562). e879 reproduction uses E879_ROOT (default /tmp/mal-e879-live).
set -eu
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="${F68C_ROOT:-$(CDPATH= cd -- "$HERE/../../.." && pwd)}"
export E879_ROOT="${E879_ROOT:-/tmp/mal-e879-live}"
cd "$ROOT"
python3 -m pytest "$HERE/test_floor_live_f68c_presend.py" -q
