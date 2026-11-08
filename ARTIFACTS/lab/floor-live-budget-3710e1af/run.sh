#!/bin/sh
# Reproducible FLOOR live fee/lifetime + dry-to-live init tests. Synthetic only; no network, no keys.
set -eu
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$ROOT"
HEAD="$(git rev-parse HEAD)"
echo "HEAD=$HEAD (review target 3710e1af8cf434faca570046de896a7702464709)"
export PYTHONPATH="$ROOT"
python3 -m pytest \
  ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_budget_safety.py \
  ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_init_cutover.py \
  -q "$@"
