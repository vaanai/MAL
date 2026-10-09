#!/bin/sh
# DEC-016 Amendment 8 forward-1002ev (MiScusi job on mal-research-0): a second getBlock follower over the LAST WEEK of
# the DEC-016 forward window, walked WITH the event-V decoder (--event-v, #467), so every PumpSwap row carries its
# virtual quote reserve V. forward-1002 (job #382, scripts/research/forward-walk.sh at 2bd45f1) was walked without
# --event-v, so its PumpSwap rows have no V and the raw blocks were not kept. EXP-024 Look 1 (H5-BOOSTFLOOR) needs V
# on forward-1002 hours; tools/forward_v_join.py joins it from this walk by (slot, signature, event_index), after an
# hourly integrity check (this walk's rows with the V fields dropped must md5-match forward-1002's rows).
# Based on forward-walk2.sh (walk 2, forward-1016), which stays as it is; forward-walk.sh (#382) is not touched.
#
# Submit (pin gitRef to a commit that holds this file; the job runs that tree):
#   miscusi_job_submit machine=mal-research-0 resumable=true params={"start":"2026-10-09T00"}
#     resources: memMb 6144, cpus 1, timeLimitSec 604800 (10080 min)   [walk 2's sizing; walk 1 ran the same way]
#     command:   sh scripts/research/forward-walk-ev.sh
#   Idempotent: a resubmit resumes from D/checkpoint.json and D/verify.jsonl. It ends by itself (exit 0) once hour
#   2026-10-16T00 is walked and every hour START..STOP is verified OK.
#
# What it does: takes one Helius lock slot (blocking, 4 flock slots helius-{4,3,2,1}.lock at rps 10 each, the walkers'
# pattern; this walk uses --rps 8), walks each complete hour >= START about 5 min after it ends with --event-v, then
# runs `tools.exp012_forward verify --strict-lines` on it (#466; appends to D/verify.jsonl with sha256; a NUL /
# non-JSON line or a truncated zstd stream makes the hour NOT OK). Hours START.. that are already complete are caught
# up first, one hour after another, until the walk follows the chain.
#
# Window. START 2026-10-09T00 (the first hour walked), STOP 2026-10-16T01 (exclusive: the last hour walked is
# 2026-10-16T00, which ends exactly where forward-1002's counted pool ends). 169 hours. EXP-024 Look 1 reads
# [2026-10-09T23, 2026-10-16T01); the earlier hours of this walk are slack that costs about 23 hours of credits and
# also give the V join an hourly cross-check against forward-1002 from the first hour of the walk. START and STOP
# are pins: the optional job params `start` and `stop` must equal them, any other value is refused before anything
# is created.
#
# Differences from walk 2 (forward-walk2.sh):
#   1. D=/data/mal/blocks/forward-1002ev, with a `.nobackup` file (re-downloadable bulk data, docs/HOSTS.md). It never
#      touches forward-1002 or forward-1016.
#   2. START/STOP are the pins above, not 2026-10-16T01 and an open end. The job ends itself after the last hour.
#   3. The credit cap is 3,600,000 (below).
#   4. POSIX sh only: no arrays, no [[ ]], no ${x//y}, no PIPESTATUS, no BASH_VERSINFO. Tested under dash and bash.
#   5. If hours are still not verified OK three passes after STOP, it writes an alert and exits 7 (walk 2 loops forever).
#
# Walker exit 3 is fatal as in walk 2: the walker exits 3 when it refuses to resume (a short or NUL-holed file, or an
# --event-v toggle on a partial hour; tools/pump_history_backfill.py, REFUSAL_EXIT). The wrapper writes one line to
# D/alerts.jsonl and one to $MISCUSI_PROGRESS, prints ALERT and exits 3, walking no later hour. Recovery is a manager
# decision; the hour then goes to the getTransaction fallback of tools/forward_v_join.py. Do not delete the file or
# the checkpoint to get past it. Any other walker failure prints "walk error HOUR" and is counted per hour (reset
# when that hour's walker exits 0); three consecutive failures on one hour write an alert and exit 4.
#
# Exit codes: 0 done (every hour START..STOP verified; or the test hook's last pass), 2 refused start/stop or test
# hook in a job, 3 walker refused, 4 three consecutive failures on one hour, 5 credit cap / unreadable credit state /
# the unverified-hour list failing three passes in a row, 6 Helius env file missing, unreadable or without a key
# (alert kind helius_env; nothing walked), 7 hours still unverified three passes after STOP.
#
# Credit cap arithmetic (same basis as forward-walk2.sh and DEC-016 Amendment 6 (f)): 1 credit per getBlock, about
# 13.4k credits an hour before the 200 ms slot step (epoch 1053, ~2026-10-09T14:34Z) and about 18k after it.
#   hours 2026-10-09T00..14 (15 h, the step lands in hour 14): 15 x 13.4k = 0.20M (upper)
#   hours 2026-10-09T15..2026-10-16T00 (154 h):                 154 x 18k = 2.77M
#   walked total about 2.97M; plus account lookups for pools created before START (getMultipleAccounts) and any
#   refusal spend. CAP_TOTAL 3,600,000 is about 21% above that; the owner approved about 3.0M for the walk.
# Same cap bookkeeping as walk 2: checkpoint.json credits_used is the cumulative spend; a refused call (exit 3) never
# writes the checkpoint, so spend against the cap is checkpoint + sum(refusals.jsonl). The walker is passed
# --credit-cap CAP_TOTAL - sum(refusals), so its own check equals the true total vs CAP_TOTAL. Fewer than
# HOUR_RESERVE credits left under the cap writes an alert and exits 5 instead of starting a partial hour.
# This uses the plan's credits; no Helius autoscaling credits beyond the owner's approval are assumed.
#
# Secrets: the Helius env is sourced and never printed. No set -x, no env dump, and no alert text carries it.
#
# Test hook (the same one forward-walk2.sh has): FW2_TEST_ROOT (with FW2_TEST_PY, FW2_TEST_PASSES) points D, the lock
# dir and the Helius env at a temp dir and ends the job after FW2_TEST_PASSES hourly passes. It exists for
# tools/test_forward_walk_ev_wrapper.py and is never set in a job: with MISCUSI_JOB_ID also set the script refuses
# at the top (exit 2).
set -u
PYTHONPATH="$PWD"; PYTHONUNBUFFERED=1; export PYTHONPATH PYTHONUNBUFFERED

# The test hook never runs in a real MiScusi job (MISCUSI_JOB_ID is set there): refuse before anything is created.
if [ -n "${FW2_TEST_ROOT:-}" ] && [ -n "${MISCUSI_JOB_ID+set}" ]; then
  echo "refused: FW2_TEST_ROOT is a test hook and is set in a MiScusi job (MISCUSI_JOB_ID is set); unset it." >&2
  exit 2
fi

START_PIN=2026-10-09T00
STOP_PIN=2026-10-16T01      # exclusive
FINAL_HOUR=2026-10-16T00    # the last hour walked: STOP_PIN minus one hour
START="${MISCUSI_PARAM_START-$START_PIN}"
STOP="${MISCUSI_PARAM_STOP-$STOP_PIN}"
if [ "$START" != "$START_PIN" ]; then
  echo "refused: start='$START'. DEC-016 Amendment 8 pins the forward-1002ev start at $START_PIN; this wrapper takes no other value." >&2
  exit 2
fi
if [ "$STOP" != "$STOP_PIN" ]; then
  echo "refused: stop='$STOP'. DEC-016 Amendment 8 pins the forward-1002ev stop at $STOP_PIN (exclusive); this wrapper takes no other value." >&2
  exit 2
fi
if [ -z "${MISCUSI_PROGRESS:-}" ]; then echo "refused: MISCUSI_PROGRESS not set" >&2; exit 2; fi
PROGRESS="$MISCUSI_PROGRESS"

if [ -n "${FW2_TEST_ROOT:-}" ]; then
  D="$FW2_TEST_ROOT/walkev"; LOCKDIR="$FW2_TEST_ROOT/locks"; HELIUS_ENV="$FW2_TEST_ROOT/helius.env"
  PY="${FW2_TEST_PY:-python3}"; TEST_PASSES="${FW2_TEST_PASSES:-1}"
  echo "TEST MODE (FW2_TEST_ROOT is set): output $D, $TEST_PASSES pass(es)"
else
  D=/data/mal/blocks/forward-1002ev; LOCKDIR=/data/mal/locks; HELIUS_ENV=/var/lib/mal/backfill/helius.env
  PY=/data/mal/venv/bin/python; TEST_PASSES=0
fi

CAP_TOTAL=3600000
HOUR_RESERVE=20000    # about one hour at 18k credits; below this under the cap, stop instead of starting an hour
MAX_FAILS=3
END_PASSES=3          # passes after STOP with hours still unverified before exit 7

mkdir -p "$D"
[ -f "$D/.nobackup" ] || : > "$D/.nobackup"
[ -f "$D/credit-probe.json" ] || echo '{"confirmed_credits_per_getblock":1}' > "$D/credit-probe.json"

# alert KIND HOUR RC MESSAGE. MESSAGE carries no quote or backslash. One JSON line to D/alerts.jsonl, one (with pct and
# note, which is what the job panel reads) to $MISCUSI_PROGRESS, and ALERT on stdout.
alert() {
  a_line="\"utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"kind\":\"$1\",\"hour\":\"$2\",\"rc\":$3,\"msg\":\"$4\""
  printf '%s\n' "{$a_line}" >> "$D/alerts.jsonl"
  printf '%s\n' "{\"pct\":50,\"note\":\"ALERT $1 $2: $4\",$a_line}" > "$PROGRESS"
  echo "ALERT $1 hour=$2 rc=$3: $4"
}

# Sets CP (checkpoint credits_used) and REF (sum of refusals.jsonl credits_spent_in_hour_before_refusal).
# An unreadable checkpoint is fatal (the cap cannot be enforced); a bad refusals line is skipped with a note on stderr.
read_credits() {
  rc_out=$("$PY" - "$D" <<'PYEOF'
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
  CP=${rc_out% *}; REF=${rc_out#* }
}

# list_todo LAST: sets TODO to the hours START..LAST whose last verify line is not OK under the same strict rule the
# read tool applies (tools.exp012_forward.verified_hours with strict bad lines). Returns 1 if the listing failed.
list_todo() {
  TODO=$("$PY" - "$D" "$START" "$1" <<'PYEOF'
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
  )
}

# fails_get HOUR sets FV; fails_set HOUR N. The per-hour consecutive failure count lives in a shell variable named
# from the hour (digits and T only), because sh has no arrays.
fails_get() { eval "FV=\${F_$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'):-0}"; }
fails_set() { eval "F_$(printf '%s' "$1" | tr -c 'A-Za-z0-9' '_')=$2"; }

echo '{"pct":1,"note":"waiting for Helius slot (blocking)"}' > "$PROGRESS"
L=""; while [ -z "$L" ]; do for i in 4 3 2 1; do exec 9>"$LOCKDIR/helius-$i.lock"; if flock -n 9; then L=$i; break; fi; exec 9>&-; done; [ -n "$L" ] || sleep 15; done
echo "holding helius slot $L"
# Helius key guard (before the walk loop, so no credit is spent). The walker reads HELIUS_API_KEY and strips it; an
# empty key makes it fall back to the public RPC, so an unset, empty or all-blank key is refused here. The env file
# is checked before it is sourced, and first sourced in a subshell with all output off: a shell echoes the offending
# line of a malformed file, and that line is the key. Nothing below prints the file or the key.
helius_env_refuse() {
  alert helius_env - 6 "$1; job stopped before any walk"
  exec 9>&-    # release the Helius slot
  exit 6
}
if [ ! -f "$HELIUS_ENV" ] || [ ! -r "$HELIUS_ENV" ]; then helius_env_refuse "Helius env file missing or unreadable"; fi
KEYOK=$( ( set +u; set -a; . "$HELIUS_ENV" >/dev/null 2>&1 || exit 1
           [ -n "$(printf '%s' "${HELIUS_API_KEY-}" | tr -d '[:space:]')" ] && echo ok ) 2>/dev/null )
if [ "$KEYOK" != ok ]; then helius_env_refuse "Helius env file gave no key (unset, empty or unparseable)"; fi
unset KEYOK
set +u; set -a; . "$HELIUS_ENV" >/dev/null 2>&1; set +a; set -u   # +u: an unset $VAR in the file would exit silently under -u

PASS=0; END_LEFT=0; TODO_FAILS=0
while :; do
  NOWH=$(date -u +%Y-%m-%dT%H); MIN=$(date -u +%M)
  LASTINFO=$("$PY" -c "
from datetime import datetime, timedelta
now = datetime.strptime('$NOWH', '%Y-%m-%dT%H'); fin = datetime.strptime('$FINAL_HOUR', '%Y-%m-%dT%H')
last = now - timedelta(hours=1)
print(min(last, fin).strftime('%Y-%m-%dT%H'), 1 if last >= fin else 0)")
  LAST=${LASTINFO% *}; ATEND=${LASTINFO#* }
  if [ "$MIN" -ge 5 ]; then
    # A listing failure walks nothing this pass.
    if list_todo "$LAST"; then TODO_FAILS=0; else
      TODO=""; TODO_FAILS=$(( TODO_FAILS + 1 ))
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
        fails_get "$H"; fails_set "$H" $(( FV + 1 )); fails_get "$H"
        echo "walk error $H rc=$RC consecutive=$FV/$MAX_FAILS"
        if [ "$FV" -ge "$MAX_FAILS" ]; then
          alert walker_failed "$H" "$RC" "walker failed 3 passes in a row on this hour; job stopped"
          exit 4
        fi
      else
        fails_set "$H" 0
      fi
      "$PY" -m tools.exp012_forward verify --walk-dir "$D" --hour "$H" --strict-lines > /dev/null || echo "verify not OK $H"
    done
    read_credits
    printf '%s\n' "{\"pct\":50,\"note\":\"forward-1002ev through $LAST, credits $(( CP + REF )) of $CAP_TOTAL (checkpoint $CP + refused $REF)\"}" > "$PROGRESS"
    if [ "$ATEND" = 1 ]; then
      # The last hour (FINAL_HOUR) is complete. Done when every hour START..FINAL_HOUR is verified OK.
      if list_todo "$FINAL_HOUR" && [ -z "$TODO" ]; then
        printf '%s\n' "{\"pct\":100,\"note\":\"forward-1002ev complete: $START..$FINAL_HOUR verified, credits $(( CP + REF )) of $CAP_TOTAL\"}" > "$PROGRESS"
        echo "done: every hour $START..$FINAL_HOUR is verified OK"
        exit 0
      fi
      END_LEFT=$(( END_LEFT + 1 ))
      if [ "$END_LEFT" -ge "$END_PASSES" ]; then
        alert unverified_at_stop - 7 "hours up to $FINAL_HOUR still not verified OK after $END_PASSES passes past the stop; job stopped"
        exit 7
      fi
    fi
  fi
  PASS=$(( PASS + 1 ))
  if [ "$TEST_PASSES" -gt 0 ] && [ "$PASS" -ge "$TEST_PASSES" ]; then exit 0; fi
  sleep $(( 60 * ( (65 - $(date -u +%-M)) % 60 ) + 30 ))
done
