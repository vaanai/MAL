#!/bin/sh
# MiScusi job wrapper: HV4 C0 sell + base-ATA close simulate through the hv4_c0 bridge's PINNED builder (DEC-032 s2 checklist / s4, FILL:simulate_ref for hv4_c0).
# POSIX sh. Run it on mal-fast-0 (not research-0). usage: hv4-c0-sell-simulate-job.sh GIT_SHA
#
# What it does: exports the tree at GIT_SHA (git archive, so the pinned code is exactly that commit), then runs tools/hv4_c0_sell_simulate.py four times on public
# RPC (1 request/s, simulateTransaction only, NOTHING SENT, no key): for one Token mint and one Token-2022 mint, the default mode (throwaway signer + funded
# public payer, unsigned) and --holder mode (public holder, full balance + balance-1 control). Each run builds BOTH rungs (0.85 at 55,000; 0.65 at 150,000)
# through arena_live_bridge.make_live_port(profile hv4_c0). The records are written to $MISCUSI_OUTPUT_DIR with a .sha256 next to each; the sha256 lines and a
# SHA256SUMS file are printed/written at the end. Last line: "hv4-c0-sell-simulate VERDICT: PASS" or "FAIL" (exit 0 / 1). A PASS is a simulate, not a landing,
# a price or a P&L claim. Expect 5 to 20 minutes (1 req/s, holder search); give the job a 1 hour limit and well under 2 GB.
#
# Env: MISCUSI_OUTPUT_DIR (required), MAL_REPO (default $HOME/MAL), PY (a python with solders AND numpy; else a short candidate list is tried),
#      RPC (plain public URL, no query string, no key; default https://api.mainnet-beta.solana.com), PLAN_ONLY=1 (print the four commands, run nothing).
#
# Mints: public chain data, September 2026 graduations, the two pools the Q50 N4 simulate pre-declared (no re-selection, no seal touched: none is a counted HV4
# hour, an October pool or a DBC window). Sources: DEC/DEC-033-h5v1q50-canary.md, item "N4 re-runs at X" (Token DB3kaj.../6Btazse7..., Token-2022 5tMa323P.../AqjAELrJ...)
# and section 9a FILL:simulate_ref (one Token and one Token-2022 mint with s0 in September 2026); tools/q50_n4_rerun.py (the rule); full addresses: the manager17
# state note /data/mal/hunt-1008/ops/manager17/state-push-1026-manager17.md (N4 picks: Token s0 2026-09-20T08:50:03Z, Token-2022 s0 2026-09-20T00:17:28Z; pick
# jobs #1719/#1721), cross-checked offline by tools/test_hv4_c0_sell_simulate.py against tools/fixtures/pumpswap/t22_mayhem_accounts.json (the pool addresses and
# each pool's base_mint) and against the canonical-pool derivation. The Token-2022 pool is a MAYHEM pool (pool byte 243 = 1), so the mayhem fee-recipient path of the
# pinned builder is exercised. s0 was NOT re-verified by this change (no RPC was called).
set -u
REFUSED() { echo "REFUSED: $*"; exit 1; }
TOKEN_MINT=DB3kajxPUXuWSAfMZcx9KaPeFpty9QWDsBPLBrBtpump
TOKEN_POOL=6Btazse7vut1UMryY51XqsqP15ZkE9MY7LpitbFs5gig
T22_MINT=5tMa323PWSgYSFKSGDQBmK8wVtV9JFz8ePP59cbYpump
T22_POOL=AqjAELrJfTtxZnwQby7NDuc1e359tBjRXNRCqJjSmfLs

X=${1:-}
[ -n "$X" ] || REFUSED "usage: hv4-c0-sell-simulate-job.sh GIT_SHA (40 lowercase hex)"
printf '%s' "$X" | grep -Eq '^[0-9a-f]{40}$' || REFUSED "GIT_SHA must be 40 lowercase hex"
OUT=${MISCUSI_OUTPUT_DIR:-}
[ -n "$OUT" ] || REFUSED "MISCUSI_OUTPUT_DIR is not set (run this as a MiScusi job)"
RPC=${RPC:-https://api.mainnet-beta.solana.com}
case "$RPC" in *'?'*|*'@'*|*api-key*|*API-KEY*) REFUSED "RPC must be a plain public URL (no query string, no key)";; esac
REPO=${MAL_REPO:-${HOME:-/nonexistent}/MAL}
[ -d "$REPO" ] || REFUSED "MAL_REPO $REPO is not a directory"
unset HELIUS_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY 2>/dev/null || true

if ! git -C "$REPO" cat-file -e "$X^{commit}" 2>/dev/null; then
  git -C "$REPO" fetch -q origin || REFUSED "git fetch failed"
  git -C "$REPO" cat-file -e "$X^{commit}" 2>/dev/null || REFUSED "$X is not in $REPO"
fi
git -C "$REPO" cat-file -e "$X:tools/hv4_c0_sell_simulate.py" 2>/dev/null || REFUSED "$X lacks tools/hv4_c0_sell_simulate.py"

if [ "${PLAN_ONLY:-}" = 1 ]; then
  PY=${PY:-python3}
else
  PY=""
  for c in "${PY:-}" /var/lib/mal/fast-forward/venv/bin/python /var/lib/mal/fast-listener/.venv/bin/python /data/mal/venv/bin/python python3; do
    [ -n "$c" ] || continue
    if "$c" -c 'import solders, numpy' 2>/dev/null; then PY=$c; break; fi
  done
  [ -n "$PY" ] || REFUSED "no python with solders and numpy found; set PY=/path/to/python (fast-0 had none on 2026-10-27, job #1722)"
fi

mkdir -p "$OUT" || REFUSED "cannot create $OUT"
SRC=$(mktemp -d "${TMPDIR:-/tmp}/hv4c0ss.XXXXXX") || REFUSED "mktemp failed"
trap 'rm -rf "$SRC"' EXIT INT TERM
git -C "$REPO" archive -o "$SRC/tree.tar" "$X" || REFUSED "git archive failed"
mkdir "$SRC/tree" && tar -x -f "$SRC/tree.tar" -C "$SRC/tree" || REFUSED "tar failed"
[ -f "$SRC/tree/tools/hv4_c0_sell_simulate.py" ] || REFUSED "export lacks the tool"
echo "hv4-c0-sell-simulate: git_sha=$X python=$PY rpc_host=$(printf '%s' "$RPC" | cut -d/ -f3) utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

FAIL=0
run_one() {  # label mint pool [extra args]
  label=$1; mint=$2; pool=$3; shift 3
  f="$OUT/hv4-c0-sell-simulate-$label.json"
  if [ "${PLAN_ONLY:-}" = 1 ]; then
    echo "PLAN: (cd <export of $X> && $PY -B -m tools.hv4_c0_sell_simulate --mint $mint --pool $pool --rpc $RPC --git-sha $X --out $f $*)"
    return 0
  fi
  ( cd "$SRC/tree" && "$PY" -B -m tools.hv4_c0_sell_simulate --mint "$mint" --pool "$pool" --rpc "$RPC" --git-sha "$X" --out "$f" "$@" )
  rc=$?
  echo "hv4-c0-sell-simulate: $label rc=$rc"
  [ "$rc" -eq 0 ] || FAIL=1
  return 0
}
run_one token-default "$TOKEN_MINT" "$TOKEN_POOL"
run_one token-holder "$TOKEN_MINT" "$TOKEN_POOL" --holder
run_one token2022-default "$T22_MINT" "$T22_POOL"
run_one token2022-holder "$T22_MINT" "$T22_POOL" --holder

if [ "${PLAN_ONLY:-}" = 1 ]; then
  echo "hv4-c0-sell-simulate: plan only, nothing run"
  exit 0
fi
( cd "$OUT" && sha256sum hv4-c0-sell-simulate-*.json > hv4-c0-sell-simulate-SHA256SUMS ) 2>/dev/null
for f in "$OUT"/hv4-c0-sell-simulate-*.json; do
  [ -f "$f" ] || continue
  echo "sha256 $(sha256sum "$f" | cut -d' ' -f1) $(basename "$f")"
done
if [ "$FAIL" -eq 0 ]; then echo "hv4-c0-sell-simulate VERDICT: PASS"; exit 0; fi
echo "hv4-c0-sell-simulate VERDICT: FAIL"
exit 1
