#!/usr/bin/env bash
# DEC-021 walk 2 (MiScusi job on mal-research-0): the getBlock follower whose hours EXP-022 CAP-PICK counts from
# 2026-10-16T01 (EXP/EXP-022-cap-pick-part1-prereg.md, DEC-021 Amendment 1). Based on forward-walk.sh (walk 1,
# forward-1002, MiScusi #382), which stays as it is.
#
# Submit so the job STARTS before 2026-10-16T01:00Z (EXP-022 section 10 P3), e.g. at 00:30Z:
#   miscusi_job_submit machine=mal-research-0 params={"start":"2026-10-16T01"} resumable=true
#   command: bash scripts/research/forward-walk2.sh   (time limit 10080 min, memory ~6 GB; resubmit weekly;
#   idempotent: a resubmit resumes from D/checkpoint.json and D/verify.jsonl)
#
# What it does: takes one Helius lock slot (blocking), walks each complete hour >= START about 5 min after it ends
# with the event-V decoder (--event-v, #467), then runs `tools.exp012_forward verify --strict-lines` on it (#466;
# appends to D/verify.jsonl with sha256; a NUL / non-JSON line or a truncated zstd stream makes the hour NOT OK).
#
# How it differs from walk 1:
#   1. Output D=/data/mal/blocks/forward-1016 (the name EXP-022 section 9 and the HOLDOUT_LEDGER row give), with its
#      own checkpoint.json, verify.jsonl, refusals.jsonl and alerts.jsonl. It never touches forward-1002.
#   2. START must be 2026-10-16T01 (EXP-022 pins it with EXP022_COUNT_START). Any other value is refused before
#      anything is created.
#   3. Every walker call carries --event-v; every verify call carries --strict-lines.
#   4. Walker exit 3 is fatal. The walker exits 3 when it refuses to resume (a short or NUL-holed file, or an
#      --event-v toggle on a partial hour; tools/pump_history_backfill.py, REFUSAL_EXIT). The wrapper then writes
#      one line to D/alerts.jsonl and one to $MISCUSI_PROGRESS, prints ALERT on stdout and exits 3. It does not
#      walk any later hour. Recovery is a manager decision (name the hour not decidable, or re-walk it once from a
#      fresh directory, EXP-022 section 10). Do not delete the file or the checkpoint to get past it. A resubmit
#      meets the same refusal on the same hour and fails again, by design.
#   5. Any other walker failure prints "walk error HOUR" as before and is counted per hour. The count resets when
#      that hour's walker exits 0. Three consecutive failures on the same hour (one try per hourly pass) write
#      an alert and exit 4.
#   6. The credit cap is sized for walk 2 and the refusals are added in (below). Fewer than HOUR_RESERVE credits left
#      under the cap writes an alert and exits 5 instead of starting a partial hour.
#
# Exit codes: the loop never ends by itself in a job (the time limit or a cancel ends it; 0 only under the test hook),
# 2 refused start, bash or test hook in a job, 3 walker refused, 4 three consecutive failures on one hour, 5 credit
# cap, unreadable credit state or the unverified-hour list failing three passes in a row, 6 Helius env file missing,
# unreadable or without a key (alert kind helius_env; nothing walked).
#
# Credit cap arithmetic (SYNTHESIS A8, ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md:393). After the 200 ms slot step
# (epoch 1053, ~2026-10-09T14:34Z) walk 2 costs about 410-430k credits a day at 1 credit per getBlock (13.4k a
# an hour before the step, +26-34%, about 18k an hour). Run length: the counted window [10-16T01, 11-06T01) is 21
# days; the 22nd day covers the exit hour and a late or resubmitted day. Upper rate, 25% margin:
#   430,000 x 22 days = 9,460,000;  x 1.25 = 11,825,000  (CAP_TOTAL, below)
# The walker's checkpoint.json credits_used is the cumulative spend. A refused call (exit 3) never writes the
# checkpoint, so its spend lives only in D/refusals.jsonl (credits_spent_in_hour_before_refusal). Spend against the
# cap is therefore checkpoint + sum(refusals). The wrapper passes the walker --credit-cap CAP_TOTAL - sum(refusals),
# which makes the walker's own check (checkpoint vs cap) equal to the true total vs CAP_TOTAL.
# This uses the plan's credits. No Helius autoscaling credits are assumed (A8: 0 extra credits).
#
# Secrets: the Helius env is sourced and never printed. No set -x, no env dump, and no alert text carries it.
#
# Test hook: FW2_TEST_ROOT (with FW2_TEST_PY, FW2_TEST_PASSES) points D, the lock dir and the Helius env at a temp
# dir and ends the job after FW2_TEST_PASSES hourly passes. It exists for tools/test_forward_walk2_wrapper.py and
# is never set in a job: with MISCUSI_JOB_ID also set the script refuses at the top (exit 2).
set -u
export PYTHONPATH="$PWD" PYTHONUNBUFFERED=1

if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then echo "refused: forward-walk2.sh needs bash >= 4" >&2; exit 2; fi

# The test hook never runs in a real MiScusi job (MISCUSI_JOB_ID is set there): refuse before anything is created.
if [ -n "${FW2_TEST_ROOT:-}" ] && [ -n "${MISCUSI_JOB_ID+set}" ]; then
  echo "refused: FW2_TEST_ROOT is a test hook and is set in a MiScusi job (MISCUSI_JOB_ID is set); unset it." >&2
  exit 2
fi

COUNT_START=2026-10-16T01
START="${MISCUSI_PARAM_START:-}"
if [ "$START" != "$COUNT_START" ]; then
  echo "refused: start='$START'. EXP-022 pins the walk-2 start at $COUNT_START (EXP022_COUNT_START); this wrapper takes no other value." >&2
  exit 2
fi
PROGRESS="${MISCUSI_PROGRESS:?MISCUSI_PROGRESS not set}"

if [ -n "${FW2_TEST_ROOT:-}" ]; then
  D="$FW2_TEST_ROOT/walk2"; LOCKDIR="$FW2_TEST_ROOT/locks"; HELIUS_ENV="$FW2_TEST_ROOT/helius.env"
  PY="${FW2_TEST_PY:-python3}"; TEST_PASSES="${FW2_TEST_PASSES:-1}"
  echo "TEST MODE (FW2_TEST_ROOT is set): output $D, $TEST_PASSES pass(es)"
else
  D=/data/mal/blocks/forward-1016; LOCKDIR=/data/mal/locks; HELIUS_ENV=/var/lib/mal/backfill/helius.env
  PY=/data/mal/venv/bin/python; TEST_PASSES=0
fi

CAP_PER_DAY=430000; CAP_DAYS=22
CAP_TOTAL=$(( CAP_PER_DAY * CAP_DAYS * 125 / 100 ))   # 11,825,000
HOUR_RESERVE=20000    # about one hour at 18k credits; below this under the cap, stop instead of starting an hour
MAX_FAILS=3

mkdir -p "$D"
[ -f "$D/credit-probe.json" ] || echo '{"confirmed_credits_per_getblock":1}' > "$D/credit-probe.json"

# alert KIND HOUR RC MESSAGE. MESSAGE carries no quote or backslash. One JSON line to D/alerts.jsonl, one (with pct and
# note, which is what the job panel reads) to $MISCUSI_PROGRESS, and ALERT on stdout.
alert() {
  local kind=$1 hour=$2 rc=$3 msg=$4 line
  line="\"utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"kind\":\"$kind\",\"hour\":\"$hour\",\"rc\":$rc,\"msg\":\"$msg\""
  echo "{$line}" >> "$D/alerts.jsonl"
  echo "{\"pct\":50,\"note\":\"ALERT $kind $hour: $msg\",$line}" > "$PROGRESS"
  echo "ALERT $kind hour=$hour rc=$rc: $msg"
}

# Sets CP (checkpoint credits_used) and REF (sum of refusals.jsonl credits_spent_in_hour_before_refusal).
# An unreadable checkpoint is fatal (the cap cannot be enforced); a bad refusals line is skipped with a note on stderr.
read_credits() {
  local out
  out=$("$PY" - "$D" <<'PYEOF'
import json, sys
d = sys.argv[1]
cp = 0
try:
    with open(d + "/checkpoint.json", encoding="utf-8") as fh:
        cp = int(json.load(fh).get("credits_used") or 0)
except FileNotFoundError:
    pass
ref = 0
try:
    with open(d + "/refusals.jsonl", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                ref += int(json.loads(line).get("credits_spent_in_hour_before_refusal") or 0)
            except (ValueError, TypeError, AttributeError):
                print("refusals.jsonl line %d unreadable, not counted" % n, file=sys.stderr)
except FileNotFoundError:
    pass
print(cp, ref)
PYEOF
  ) || { alert credit_state - 1 "cannot read credits_used from checkpoint.json"; exit 5; }
  CP=${out% *}; REF=${out#* }
}

echo '{"pct":1,"note":"waiting for Helius slot (blocking)"}' > "$PROGRESS"
L=""; while [ -z "$L" ]; do for i in 4 3 2 1; do exec 9>"$LOCKDIR/helius-$i.lock"; if flock -n 9; then L=$i; break; fi; exec 9>&-; done; [ -n "$L" ] || sleep 15; done
echo "holding helius slot $L"
# Helius key guard (before the walk loop, so no credit is spent). The walker reads HELIUS_API_KEY and strips it; an
# empty key makes it fall back to the public RPC, so an unset, empty or all-blank key is refused here. The env file
# is checked before it is sourced. Sourcing runs with stderr off: bash echoes the offending line of a malformed
# file, and that line is the key. Nothing below prints the file or the key.
helius_env_refuse() {
  alert helius_env - 6 "$1; job stopped before any walk"
  exec 9>&-    # release the Helius slot
  exit 6
}
if [ ! -f "$HELIUS_ENV" ] || [ ! -r "$HELIUS_ENV" ]; then helius_env_refuse "Helius env file missing or unreadable"; fi
set +u; set -a; . "$HELIUS_ENV" 2>/dev/null; set +a; set -u   # +u: under set -u an unset $VAR in the file would exit 1 silently
KEYCHK=${HELIUS_API_KEY-}; KEYCHK=${KEYCHK//[[:space:]]/}
if [ -z "$KEYCHK" ]; then helius_env_refuse "Helius env file gave no key (unset or empty)"; fi
unset KEYCHK

declare -A FAILS=()
PASS=0
while :; do
  NOWH=$(date -u +%Y-%m-%dT%H); MIN=$(date -u +%M)
  LAST=$("$PY" -c "from datetime import datetime,timedelta;print((datetime.strptime('$NOWH','%Y-%m-%dT%H')-timedelta(hours=1)).strftime('%Y-%m-%dT%H'))")
  if [ "$MIN" -ge 5 ]; then
    # Hours START..LAST whose last verify line is not OK under the same strict rule the read tool applies
    # (tools.exp012_forward.verified_hours with strict bad lines). A failure here walks nothing this pass.
    if TODO=$("$PY" - "$D" "$START" "$LAST" <<'PYEOF'
import sys
from datetime import datetime, timedelta
from pathlib import Path
from tools.exp012_forward import verified_hours
d, s, e = sys.argv[1], sys.argv[2], sys.argv[3]
ok = verified_hours(Path(d), True)
t = datetime.strptime(s, "%Y-%m-%dT%H")
end = datetime.strptime(e, "%Y-%m-%dT%H")
while t <= end:
    h = t.strftime("%Y-%m-%dT%H")
    if h not in ok:
        print(h)
    t += timedelta(hours=1)
PYEOF
    ); then TODO_FAILS=0; else
      TODO=""; TODO_FAILS=$(( ${TODO_FAILS:-0} + 1 ))
      echo "todo error (verified_hours failed) $TODO_FAILS/$MAX_FAILS; nothing walked this pass"
      if [ "$TODO_FAILS" -ge "$MAX_FAILS" ]; then alert todo_failed - 1 "could not list unverified hours 3 passes in a row"; exit 5; fi
    fi
    for H in $TODO; do
      read_credits
      if [ $(( CAP_TOTAL - CP - REF )) -lt "$HOUR_RESERVE" ]; then
        alert credit_cap "$H" 0 "credit cap reached: checkpoint $CP + refused $REF of $CAP_TOTAL"
        exit 5
      fi
      U=$("$PY" -c "from datetime import datetime,timedelta;print((datetime.strptime('$H','%Y-%m-%dT%H')+timedelta(hours=1)).strftime('%Y-%m-%dT%H:00:00Z'))")
      "$PY" -m tools.pump_history_backfill --until "$U" --hours 1 --out "$D" --credits-file "$D/credit-probe.json" --credit-cap $(( CAP_TOTAL - REF )) --max-bytes 322122547200 --rps 8 --lookup-rps 2 --workers 8 --event-v
      RC=$?
      if [ "$RC" -eq 3 ]; then
        alert walker_refused "$H" 3 "walker refused to resume (data hole or event-V toggle); job stopped, hour not walked, see refusals.jsonl"
        exit 3
      fi
      if [ "$RC" -ne 0 ]; then
        FAILS[$H]=$(( ${FAILS[$H]:-0} + 1 ))
        echo "walk error $H rc=$RC consecutive=${FAILS[$H]}/$MAX_FAILS"
        if [ "${FAILS[$H]}" -ge "$MAX_FAILS" ]; then
          alert walker_failed "$H" "$RC" "walker failed 3 passes in a row on this hour; job stopped"
          exit 4
        fi
      else
        FAILS[$H]=0
      fi
      "$PY" -m tools.exp012_forward verify --walk-dir "$D" --hour "$H" --strict-lines > /dev/null || echo "verify not OK $H"
    done
    read_credits
    echo "{\"pct\":50,\"note\":\"walk 2 through $LAST, credits $(( CP + REF )) of $CAP_TOTAL (checkpoint $CP + refused $REF)\"}" > "$PROGRESS"
  fi
  PASS=$(( PASS + 1 ))
  if [ "$TEST_PASSES" -gt 0 ] && [ "$PASS" -ge "$TEST_PASSES" ]; then exit 0; fi
  sleep $(( 60 * ( (65 - $(date -u +%-M)) % 60 ) + 30 ))
done
