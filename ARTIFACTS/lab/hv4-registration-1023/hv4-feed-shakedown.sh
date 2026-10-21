#!/bin/sh
# hv4-feed-shakedown.sh (manager12, 2026-10-19). PAPER only, POSIX sh, no xtrace. Two modes, each its own MiScusi job:
#   feed  : the research-0 arena feed (tools.arena_feed, public RPC only, 0 Helius credits) writing /data/mal/arena/tape, with the
#           DEC-031 25% disk reserve (the feed pauses itself below it; walk 2 keeps its 20% room). Long-running.
#   stub  : ONE Haiku v4 STUB shakedown (StubModel: no API call, no key read, every answer abstains) into its own registry/ledger/root
#           under /data/mal/arena/_shakedown-hv4-1019 (the real bots.json and ledger stay untouched); ends at HV4_STUB_END_MS.
#           Its logs are counts (scans, cards, setups, stub calls); there is no trade. Start it after >= 125 min of tape (warm-up).
# Run at the commit to test (the checkout must be clean: hv4-runner-job.sh refuses otherwise).
set -u
case "$-" in *x*) echo "refusing xtrace" >&2; exit 2;; esac
MODE="${1:?mode feed|stub}"
date -u
HEAD_SHA=$(git rev-parse HEAD); echo "head=$HEAD_SHA mode=$MODE"
df -h /data/mal | tail -1
PY=/data/mal/arena/venv-hv4/bin/python
TAPE=/data/mal/arena/tape
SK="${HV4_SK:-/data/mal/arena/_shakedown-hv4-1019b}"
mkdir -p "$TAPE" "$SK"
case "$MODE" in
  feed)
    exec systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 "$PY" -m tools.arena_feed --out-dir "$TAPE" \
      --ws-url wss://api.mainnet-beta.solana.com --ws-url wss://public.rpc.solanavibestation.com --sockets-pumpswap 3 --sockets-pumpfun 3 ;;
  stub)
    : "${HV4_STUB_END_MS:?HV4_STUB_END_MS is required}"
    RESTART=0
    if [ -s "$SK/bots.json" ]; then
      # a restart of the same shakedown: reuse the registered clock (a new clock for an existing bot_id is refused) and skip registration
      CLK=$("$PY" -I -c "import json,sys;c={json.loads(l)['clock_start_ms'] for l in open(sys.argv[1]) if l.strip()};assert len(c)==1,c;print(c.pop())" "$SK/bots.json") || exit 6
      RESTART=1
    else
      CLK=$(( $(date +%s) * 1000 + 120000 ))
    fi
    export HV4_FROZEN_SHA="$HEAD_SHA" HV4_CLOCK_START_MS="$CLK" HV4_FEED_DELAY_SLOTS=7 HV4_REPO="$(pwd)" HV4_ROOT="$SK/root" \
      HV4_TAPE="$TAPE" HV4_LEDGER="$SK/ledger" HV4_REGISTRY="$SK/bots.json" PYTHON="$PY" HV4_END_MS="$HV4_STUB_END_MS"
    export MAL_HV4_RPC="${MAL_HV4_RPC:-helius}"; echo "chain rpc: $MAL_HV4_RPC restart=$RESTART clock_start_ms=$CLK"
    if [ "$RESTART" -eq 0 ]; then
      HV4_MODE=register sh scripts/research/hv4-runner-job.sh; R=$?
      echo "register_rc=$R at $(date -u +%FT%TZ)"
      [ "$R" -eq 0 ] || exit "$R"
      sleep 130
    fi
    HV4_MODE=stub exec systemd-run --user --scope -q -p MemoryMax="${HV4_STUB_MEM:-6G}" -p MemorySwapMax=0 sh scripts/research/hv4-runner-job.sh ;;
  *) echo "mode must be feed or stub" >&2; exit 2 ;;
esac
