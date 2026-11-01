#!/bin/sh
# HV7 tip-source P0 COUNT: hours (and half-hours) with >= 3 screened candidates on 3 closed UTC days of the research-0 receiver copy, built from H0. COUNTS ONLY.
# POSIX sh (MiScusi jobs run under sh). PAPER only: no model call, no API, no engine, no price, no outcome. The pinned reader of the derived copy for this use is
# tools/hv7_tip_p0_count.py (DEC-031 Amendment 23 item 9: "the pinned tip-source P0 counter (counts only)"); nothing else opens /data2/mal/arena-tip-feed/hv7.
# DO NOT RUN until (1) the 3 days are fixed in a notebook entry (the latest 3 full closed days before the registration; for T0 2026-11-07: 2026-11-03,2026-11-04,2026-11-05) and
# the notebook id is passed here, (2) 11-05's hours are closed and receiver-verified, (3) H0 is read from the writer's status.json (first hour of the continuous history),
# (4) the tree is the F1 sha and clean, (5) `systemctl show user-1002.slice -p MemoryCurrent` fits.
#
# Usage:  sh scripts/ops/hv7-tip-p0.sh <days> <notebook_ref> <h0 YYYY-MM-DDTHH> [drill_id]
#   e.g.  sh scripts/ops/hv7-tip-p0.sh 2026-11-03,2026-11-04,2026-11-05 <notebook id> <H0> tip-p0-f1
# Submit (mal-research-0; request 16 GB; the tool needs the whole history from H0 in one ArenaState, size it from the receiver's hour sizes first):
#   miscusi_job_submit(command="sh scripts/ops/hv7-tip-p0.sh <days> <notebook_ref> <h0>", device=mal-research-0, memory_gb=16, resumable=false)
# Output: one JSON line on stdout (per-day counts, the runner's screen counters incl. u2_age_unknown, the rule booleans, u2_undercount_days). Pass line: hourly mean >= 150/14 = 10.714285714285714 a day.
set -eu
DAYS="${1:?usage: hv7-tip-p0.sh <days> <notebook_ref> <h0 YYYY-MM-DDTHH> [drill_id]}"
REF="${2:?usage: hv7-tip-p0.sh <days> <notebook_ref> <h0 YYYY-MM-DDTHH> [drill_id]}"
H0="${3:?usage: hv7-tip-p0.sh <days> <notebook_ref> <h0 YYYY-MM-DDTHH> [drill_id]}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"hv7_tip_p0":"refused","why":"dirty_tree"}'
  exit 2
fi
ID="${4:-tip-p0-$(date -u +%Y%m%dT%H%M%SZ)}"
echo "{\"hv7_tip_p0\":\"job\",\"sha\":\"$SHA\",\"days\":\"$DAYS\",\"h0\":\"$H0\",\"notebook_ref\":\"$REF\",\"drill_id\":\"$ID\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY OPENROUTER_API_KEY HELIUS_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
exec systemd-run --user --scope -q -p MemoryMax=16G -p MemorySwapMax=0 nice -n 19 ionice -c3 timeout 14400 \
  /data/mal/venv/bin/python -s -B -m tools.hv7_tip_p0_count --days "$DAYS" --notebook-ref "$REF" --h0 "$H0" --drill-id "$ID"
