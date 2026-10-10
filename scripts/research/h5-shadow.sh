#!/usr/bin/env bash
# H5-BOOSTFLOOR v1 live SHADOW detector as a MiScusi job on mal-fast-0. Paper only: no keys, no transactions, 0 Helius credits.
#
# Submit (miscusi_job_submit): machine mal-fast-0, command `bash scripts/research/h5-shadow.sh`, resumable true (no checkpoint is needed: a pool
# is followed for 400 s and a restart shows up as a gap record), 1 CPU, memory 1.9 GB (the process uses tens of MB), a long time limit.
# Output: $HOME/data/h5-shadow/h5-shadow-<UTC hour>.jsonl, h5-shadow-status.json and h5-shadow-errors.log (the dir is created by the job; no sudo).
#
# Env (all optional):
#   H5_PYTHON       python with websockets + certifi (default: the listener venv /var/lib/mal/fast-listener/.venv/bin/python)
#   H5_OUT_DIR      output dir (default $HOME/data/h5-shadow; only $HOME/data/h5-shadow*, /var/lib/mal/h5-shadow* or /tmp/* is accepted; no "..")
#   H5_WS_URLS      comma-separated public websocket URLs (default: the public mainnet-beta endpoint). Free endpoints only.
#   H5_SOCKETS      redundant public logsSubscribe sockets (default 3; fewer than 3 is refused, see H5_ALLOW_FEW_SOCKETS). Their records feed the
#                   live executor: with 2 sockets any reconnect of the peer during a 3-10 s pause of the other flags every open pool, which
#                   starves the canary. All sockets share one default endpoint, so prefer 2+ distinct endpoints in H5_WS_URLS.
#   H5_ALLOW_FEW_SOCKETS  set to 1 to allow H5_SOCKETS below 3 (smoke tests and manual runs whose records feed nothing)
#   H5_LOOK2_OBSERVED  set to exactly EXP-024-Am2 to start the shadow with EXP-024 Amendment 2's declared observation of Look 2's added window
#                   (pools with s0 in [2026-10-16T00Z, 2026-11-06T00Z) write outcomes). Unset or empty keeps the H5 seal: pools with s0 >= 2026-10-16T00Z
#                   get trigger records only, and pools with s0 >= 2026-11-06T00Z get them even with the flag. Any other value is refused.
#                   CAP-PICK picks (from 2026-10-16T01Z) stay sealed whatever this says. A MiScusi resume or re-run must set the variable again:
#                   the daily check alerts when the newest start record of a shadow running past 2026-10-16T00Z shows h5_look2.observed false.
#   H5_BX10_ENABLE  set to exactly EXP-026 to write the report-only bx10 exit variant (outcome_bx10 records) into the hourly shadow files. Unset,
#                   empty or any other value ("1", "exp-026", ...) keeps it OFF: no outcome_bx10 record, no bx10 computation, no counter, and every
#                   other record is the same as without bx10 (a wrong value is not refused; it counts as off, and the start record's "bx10" says
#                   so). The draft EXP-026 wants v2's outcomes in a withheld store and an EXP-024 "G-v2" amendment merged first: do not set it
#                   before then. Passed to the detector as --bx10-enable only when the variable is set. A resume or re-run must set it again.
#   CAP_PICK_LIVE, CAP_PICK_REPLAY, CAP_PICK_FINAL_MARKER, CAP_PICK_STALE_S
#                   the CAP-PICK pick oracle (tools/cap_pick_oracle.py; passed through unchanged). CAP_PICK_LIVE is the exporter's picks.jsonl,
#                   CAP_PICK_REPLAY the replay booleans (os.pathsep lists), CAP_PICK_FINAL_MARKER the marker the manager writes after the DEC-016
#                   FINAL (the same path the exporter job waits on). Without CAP_PICK_LIVE and CAP_PICK_FINAL_MARKER every pool with s0 from
#                   2026-10-16T01Z is sealed (no trigger record: the executor buys nothing). CAP_PICK_STALE_S may only tighten the 60 s limit.
#                   The start record's seal.oracle says which one ran ("cap_pick_oracle" or "stub_always_true"). A resume must set them again.
#   H5_MAX_SECONDS  stop after this many seconds (smoke test: H5_MAX_SECONDS=120 H5_OUT_DIR=/tmp/h5-smoke bash scripts/research/h5-shadow.sh)
#   H5_SYN_SOCKETS  logsSubscribe sockets on the pump.fun program for the synthetic-migration class (side feed, default 1; the RPC fallback covers a miss)
#   H5_RPC_URL      public RPC for the synthetic-class fallback (default https://api.mainnet-beta.solana.com; Helius URLs are refused)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
[ -n "${HOME:-}" ] || { echo "h5-shadow: HOME is not set" >&2; exit 2; }
PY="${H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
OUT="${H5_OUT_DIR:-$HOME/data/h5-shadow}"
case "$OUT" in
  *..*) echo "h5-shadow: refusing out dir $OUT" >&2; exit 2 ;;
  "$HOME"/data/h5-shadow|"$HOME"/data/h5-shadow/*|/var/lib/mal/h5-shadow|/var/lib/mal/h5-shadow/*|/tmp/*) ;;
  *) echo "h5-shadow: refusing out dir $OUT" >&2; exit 2 ;;
esac
SOCKETS="${H5_SOCKETS:-3}"
case "$SOCKETS" in
  ''|*[!0-9]*) echo "h5-shadow: refusing H5_SOCKETS=$SOCKETS (not a number)" >&2; exit 2 ;;
esac
if [ "$SOCKETS" -lt 3 ] && [ "${H5_ALLOW_FEW_SOCKETS:-}" != "1" ]; then
  echo "h5-shadow: refusing H5_SOCKETS=$SOCKETS: fewer than 3 sockets false-flags pools on ordinary reconnects (set H5_ALLOW_FEW_SOCKETS=1 to override)" >&2
  exit 2
fi
if [ -n "${H5_LOOK2_OBSERVED:-}" ] && [ "$H5_LOOK2_OBSERVED" != "EXP-024-Am2" ]; then
  echo "h5-shadow: refusing H5_LOOK2_OBSERVED=$H5_LOOK2_OBSERVED (the only accepted value is EXP-024-Am2; unset it to keep the H5 Look-2 seal)" >&2
  exit 2
fi
[ -x "$PY" ] || { echo "h5-shadow: no python at $PY" >&2; exit 2; }
"$PY" -c 'import websockets, certifi' 2>/dev/null || { echo "h5-shadow: websockets/certifi missing in $PY" >&2; exit 3; }
mkdir -p "$OUT" 2>/dev/null || true
[ -w "$OUT" ] || { echo "h5-shadow: out dir $OUT is not writable by $(id -un)" >&2; exit 4; }
cd "$ROOT"
set -- --out-dir "$OUT" --sockets "$SOCKETS"
SYN_SOCKETS="${H5_SYN_SOCKETS:-1}"
case "$SYN_SOCKETS" in
  ''|*[!0-9]*) echo "h5-shadow: refusing H5_SYN_SOCKETS=$SYN_SOCKETS (not a number)" >&2; exit 2 ;;
esac
set -- "$@" --syn-sockets "$SYN_SOCKETS"
if [ -n "${H5_RPC_URL:-}" ]; then
  set -- "$@" --rpc-url "$H5_RPC_URL"
fi
if [ -n "${H5_WS_URLS:-}" ]; then
  OLDIFS="$IFS"; IFS=","
  for u in $H5_WS_URLS; do set -- "$@" --ws-url "$u"; done
  IFS="$OLDIFS"
fi
if [ -n "${H5_LOOK2_OBSERVED:-}" ]; then
  set -- "$@" --h5-look2-observed "$H5_LOOK2_OBSERVED"
fi
if [ -n "${H5_BX10_ENABLE:-}" ]; then
  set -- "$@" --bx10-enable "$H5_BX10_ENABLE"
fi
if [ -n "${H5_MAX_SECONDS:-}" ]; then
  set -- "$@" --max-seconds "$H5_MAX_SECONDS"
fi
BX10=off
if [ "${H5_BX10_ENABLE:-}" = "EXP-026" ]; then BX10=on; fi
echo "h5-shadow: rule H5-BOOSTFLOOR v1, out=$OUT, look2_observed=${H5_LOOK2_OBSERVED:-no}, bx10=$BX10, python=$PY, head=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
exec env PYTHONPATH="$ROOT" "$PY" -m tools.h5_shadow "$@"
