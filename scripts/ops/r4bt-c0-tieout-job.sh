#!/bin/sh
# MiScusi job wrapper: the daily wallet tie-out of the dedicated r4bt-c0 wallet (DEC-032 s6; QP-RULING-1106 build item 6). POSIX sh. Run it on mal-fast-0.
# usage: r4bt-c0-tieout-job.sh DAY      (DAY = the UTC day label YYYY-MM-DD of the run; the window itself is slot-based, from the previous signed-off context slot)
#
# SCHEDULE (document, not enforced here): one MiScusi scheduled job per UTC day at 00:30Z, `sh scripts/ops/r4bt-c0-tieout-job.sh $(date -u -d yesterday +%F)`,
# resumable false, memory 1 GB, limit 30 min. Also run it by hand at every halt, at the end of the C0 and before every tier step (DEC-032 s6). Never a session cron.
# 00:30Z, not 00:00Z, so an order whose send straddles midnight has resolved (the exporter leaves a write-ahead send with no resolution out; that is a false halt, and
# a rerun after it resolved is the fix). A day's result file is never overwritten: move a failed one aside on purpose before a rerun.
#
# Reads only: the executor's fill rows, order ledger and attempts file (paths below), the named-addresses file, public RPC finalized reads. No key, no wallet file, no send.
# The RPC: a keyed URL is read from RPC_ENV_FILE (a file of KEY=VALUE lines that sets R4BT_TIEOUT_RPC; default unset = the plain public URL). The URL is never echoed.
#
# Env (all required unless a default is shown):
#   MISCUSI_OUTPUT_DIR   where r4bt-tieout-<day>.json and fetch-<day>/ are written
#   R4BT_WALLET          the dedicated r4bt-c0 wallet public address
#   R4BT_STATE_DIR       the executor state dir holding r4bt-order-ledger.jsonl and r4bt-attempts.jsonl
#   R4BT_FILLS           the executor port's fill rows (jsonl)
#   R4BT_NAMED           named.json {"deposit_from": [...], "withdraw_to": [...]}: the owner's and Helm's addresses only
#   R4BT_TIEOUT_STATE    dir holding TIEOUT-STATE.json (default $MISCUSI_OUTPUT_DIR/../r4bt-tieout-state); the first run also needs FIRST_BASELINE and FIRST_AFTER_SLOT
#   MAL_REPO (default $HOME/MAL), PY (default /data/mal/venv/bin/python or python3), RPC_ENV_FILE (optional), STOP_ON_FAIL=1 (write STOP-tieout in the state dir on FAIL),
#   PLAN_ONLY=1 (print the command, run nothing)
# Last line: "r4bt-c0-tieout <day> PASS" or "FAIL" (exit 0 / 1); 2 = refused input, 3 = fetch failed.
set -u
REFUSED() { echo "REFUSED: $*"; exit 2; }
DAY=${1:-}
[ -n "$DAY" ] || REFUSED "usage: r4bt-c0-tieout-job.sh YYYY-MM-DD"
printf '%s' "$DAY" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' || REFUSED "DAY must be YYYY-MM-DD"
OUT=${MISCUSI_OUTPUT_DIR:-}
[ -n "$OUT" ] || REFUSED "MISCUSI_OUTPUT_DIR is not set (run this as a MiScusi job)"
for v in R4BT_WALLET R4BT_STATE_DIR R4BT_FILLS R4BT_NAMED; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || REFUSED "$v is not set"
done
REPO=${MAL_REPO:-${HOME:-/nonexistent}/MAL}
[ -d "$REPO" ] || REFUSED "MAL_REPO $REPO is not a directory"
for f in "$R4BT_FILLS" "$R4BT_NAMED" "$R4BT_STATE_DIR/r4bt-order-ledger.jsonl" "$R4BT_STATE_DIR/r4bt-attempts.jsonl"; do
  [ -f "$f" ] || REFUSED "missing input file: $f"
done
PY=${PY:-}
if [ -z "$PY" ]; then
  for c in /data/mal/venv/bin/python python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import solders' >/dev/null 2>&1; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || REFUSED "no python with solders"
unset HELIUS_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY 2>/dev/null || true
TSTATE=${R4BT_TIEOUT_STATE:-$OUT/../r4bt-tieout-state}
mkdir -p "$TSTATE" || REFUSED "cannot create $TSTATE"

RPCARG=""
if [ -n "${RPC_ENV_FILE:-}" ]; then
  [ -f "$RPC_ENV_FILE" ] || REFUSED "RPC_ENV_FILE is not a file"
  set -a; . "$RPC_ENV_FILE"; set +a
  [ -n "${R4BT_TIEOUT_RPC:-}" ] || REFUSED "RPC_ENV_FILE does not set R4BT_TIEOUT_RPC"
  RPCARG="--rpc-env R4BT_TIEOUT_RPC"
fi
FIRST=""
if [ ! -f "$TSTATE/TIEOUT-STATE.json" ]; then
  [ -n "${FIRST_BASELINE:-}" ] && [ -n "${FIRST_AFTER_SLOT:-}" ] || REFUSED "first run: set FIRST_BASELINE and FIRST_AFTER_SLOT (0 and 0 for a fresh wallet)"
  FIRST="--first-baseline $FIRST_BASELINE --first-after-slot $FIRST_AFTER_SLOT"
fi
STOP=""
[ "${STOP_ON_FAIL:-}" = 1 ] && STOP="--stop-on-fail"

CMD="$PY -m tools.r4bt_c0_tieout --wallet $R4BT_WALLET --day $DAY --state-dir $TSTATE --fills $R4BT_FILLS --order-ledger $R4BT_STATE_DIR/r4bt-order-ledger.jsonl --attempts $R4BT_STATE_DIR/r4bt-attempts.jsonl --named $R4BT_NAMED --out-dir $OUT $FIRST $STOP $RPCARG"
if [ "${PLAN_ONLY:-}" = 1 ]; then echo "PLAN: $CMD"; exit 0; fi
cd "$REPO" || REFUSED "cannot cd to $REPO"
# shellcheck disable=SC2086
$CMD
rc=$?
exit $rc
