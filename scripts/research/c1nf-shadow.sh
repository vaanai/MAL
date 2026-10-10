#!/bin/sh
# C1-NF paper shadow as a MiScusi job (POSIX sh: no bashisms, no PIPESTATUS; jobs run under sh).
#
#   live    C1NF_MODEL_MANIFEST=/path/models.json sh scripts/research/c1nf-shadow.sh [--max-seconds 600]
#   live    C1NF_MODEL=/path/model.txt C1NF_MODEL_SHA256=<64 hex> sh scripts/research/c1nf-shadow.sh
#   replay  C1NF_MODEL_MANIFEST=... sh scripts/research/c1nf-shadow.sh --replay-from 2026-09-04T12 --replay-hours 48 --decide-from 2026-09-05T12
#
# Environment (all optional except the model pin):
#   C1NF_OUT         out dir, default $HOME/data/c1nf-shadow (zstd for closed hours; picks stay plain for the executor)
#   C1NF_TIP_DIR     tip-tape dir, default /var/lib/mal/sealed/fast-trades-tip
#   C1NF_GAPS_FILE   the tip follower's gaps.jsonl (optional)
#   C1NF_LEDGER_ROOT tools/c1nf_wallet_ledger.py output root (asof/asof-<day>)
#   C1NF_PICK_ORACLE module:callable for the EXP-022 section 9 seal; absent = fail closed from 2026-10-16T01Z
#   C1NF_PYTHON      python with numpy + lightgbm (live); replay uses C1NF_REPLAY_PYTHON (pandas + pyarrow) with lightgbm added from C1NF_LGB_SITE
#
# Refuses to start on a model sha256 mismatch (exit 3). No key, no transaction, no RPC. Not a forward-walk, forward-paper or runner reader.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT="${C1NF_OUT:-$HOME/data/c1nf-shadow}"
MIN_FREE_KB="${C1NF_MIN_FREE_KB:-5000000}"

if [ -z "${C1NF_MODEL_MANIFEST:-}" ] && { [ -z "${C1NF_MODEL:-}" ] || [ -z "${C1NF_MODEL_SHA256:-}" ]; }; then
  echo "refusing: set C1NF_MODEL_MANIFEST, or C1NF_MODEL and C1NF_MODEL_SHA256" >&2
  exit 2
fi

mkdir -p "$OUT" || { echo "refusing: cannot create $OUT" >&2; exit 2; }
FREE_KB=$(df -Pk "$OUT" | awk 'NR==2 {print $4}')
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -lt "$MIN_FREE_KB" ]; then
  echo "refusing: only $FREE_KB KB free under $OUT (need $MIN_FREE_KB)" >&2
  exit 3
fi

MODE=live
for a in "$@"; do
  case "$a" in
    --replay-from|--replay-from=*) MODE=replay ;;
  esac
done

if [ "$MODE" = replay ]; then
  PY="${C1NF_REPLAY_PYTHON:-/data/mal/audit-1008/venv/bin/python}"
  LGB_SITE="${C1NF_LGB_SITE:-/data/mal/venv/lib/python3.12/site-packages}"
  PP="$REPO:$LGB_SITE"
else
  PY="${C1NF_PYTHON:-python3}"
  PP="$REPO"
fi

set -- "$@" --out-dir "$OUT"
[ -n "${C1NF_MODEL_MANIFEST:-}" ] && set -- "$@" --model-manifest "$C1NF_MODEL_MANIFEST"
[ -n "${C1NF_MODEL:-}" ] && [ -z "${C1NF_MODEL_MANIFEST:-}" ] && set -- "$@" --model "$C1NF_MODEL" --model-sha256 "$C1NF_MODEL_SHA256"
[ -n "${C1NF_LEDGER_ROOT:-}" ] && set -- "$@" --ledger-root "$C1NF_LEDGER_ROOT"
if [ "$MODE" = live ]; then
  [ -n "${C1NF_TIP_DIR:-}" ] && set -- "$@" --tip-dir "$C1NF_TIP_DIR"
  [ -n "${C1NF_GAPS_FILE:-}" ] && set -- "$@" --gaps-file "$C1NF_GAPS_FILE"
  [ -n "${C1NF_PICK_ORACLE:-}" ] && set -- "$@" --pick-oracle "$C1NF_PICK_ORACLE"
fi

echo "c1nf-shadow: mode=$MODE out=$OUT python=$PY"
cd "$REPO" || exit 2
PYTHONPATH="$PP" exec "$PY" -m tools.c1nf_shadow "$@"
