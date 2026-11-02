#!/bin/sh
# HV7 tip-source P0 COUNT: hours (and half-hours) with >= 3 screened candidates on 3 closed UTC days of the research-0 receiver copy, built from H0. COUNTS ONLY.
# POSIX sh (MiScusi jobs run under sh). PAPER only: no model call, no API, no engine, no price, no outcome. The pinned reader of the derived copy for this use is
# tools/hv7_tip_p0_count.py (DEC-031 Amendment 23 item 9: "the pinned tip-source P0 counter (counts only)"); nothing else opens /data2/mal/arena-tip-feed/hv7.
# DO NOT RUN until (1) the 3 days are fixed in a notebook entry (the latest 3 full closed days before the registration; for T0 2026-11-07: 2026-11-03,2026-11-04,2026-11-05) and
# the notebook id is passed here, (2) 11-05's hours are closed and receiver-verified, (3) H0 is read from the writer's status.json (first hour of the continuous history),
# (4) the tree is the F1 sha and clean, (5) `systemctl show user-1002.slice -p MemoryCurrent` fits.
#
# REGISTERED PINS (QP, #1172): days 2026-11-03,2026-11-04,2026-11-05; H0 2026-10-29T09 (1793264400000, from the writer status: run 704ecfa8ed91, history_from 2026-10-29T09);
# all three days are u2_undercount_days (H0 + 7 d = 2026-11-05T09Z); pass line hourly mean >= 150/14 = 10.714285714285714 a day.
#
# SIZING (P4; names and sizes of the receiver copy only, read 2026-11-02T00:25Z, no file opened): 88 tape files, 87 closed .jsonl.zst totalling 8,961.0 MB (mean 103.0 MB, median 100.6,
# p90 128.9, max 138.8 MB per hour), first hour 2026-10-29T09, last closed 2026-11-02T00 (the open hour was 251.1 MB at about 00:25Z, so roughly 0.6 GB of rows an hour, about 6x the zst size).
# The job reads H0 .. 2026-11-05T23 = 207 hour files, about 21.3 GB compressed at the mean (24 GB at p90). Rows: QP-RULING-1102 section 1 counts 63,654,500 trades in 86 closed hours =
# 740,175 an hour, so about 153M trades in 207 hours. TIME is NOT measured: at an assumed 25,000 rows/s (no run exists; an hour of rows is about 30 s) the pass is about 1.7 h, plus
# the 3 x 144 screens; the job timeout is 6 h (about 3.5x). MEMORY is NOT measured: ArenaState holds only live pools (6 h idle eviction, about 10 KB a key per arena_state.py), HV7 on the
# public tape was about 3.6 GB (plan), the tip has complete data so the key count may be higher; the scope cap is 16 GB, which kills the job (MemorySwapMax=0) rather than swap. The first
# run is the measurement: if it is killed or times out, re-size from its logged hour and rerun; nothing is lost but time. Disk: no scratch beyond the small drill root under HV7_ROOT.
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
exec systemd-run --user --scope -q -p MemoryMax=16G -p MemorySwapMax=0 nice -n 19 ionice -c3 timeout 21600 \
  /data/mal/venv/bin/python -s -B -m tools.hv7_tip_p0_count --days "$DAYS" --notebook-ref "$REF" --h0 "$H0" --drill-id "$ID"
