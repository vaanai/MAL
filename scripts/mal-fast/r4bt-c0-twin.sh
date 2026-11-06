#!/bin/sh
# r4bt-c0-twin.sh: job wrapper of the r4-b-T C0 paper twin writer (tools/r4bt_c0_twin.py; QP-RULING-1106-r4bt-c0 section 2). POSIX sh. PAPER ONLY, no credential.
# Runs the twin writer at nice 19 / ionice idle, once or in a loop, on mal-fast-0 only. It refuses (exit 2, nothing started) unless:
#   * the host is mal-fast-0;
#   * tools/arena_paper.py has the pinned sha256 (DEC-031 Am.27 item 24);
#   * tools/r4bt_c0_twin.py equals the sha256 the install pinned in R4BT_C0_TWIN_SHA256;
#   * the tape dir is the fast-0 derived copy, and every input path below is set.
# Environment (set by the C0 unit's drop-in, never on the command line):
#   R4BT_C0_REPO          the install tree at W (default: this script's repo)
#   R4BT_W_SHA            W, 40 hex
#   R4BT_C0_TWIN_SHA256   pinned sha256 of tools/r4bt_c0_twin.py
#   R4BT_FEED_DELAY_SLOTS d (the registered whole number of slots)
#   R4BT_C0_MANIFEST      the receiver's per-file sha256 manifest of the derived copy
#   R4BT_C0_ORDERS        the C0 executor's order ledger (sent orders are read, others skipped)
#   R4BT_C0_STATE_DIR     the C0 state dir: twins.jsonl and twins.run.json are written here
#   R4BT_C0_INTERVAL      seconds between runs in loop mode (default 300)
# Usage: r4bt-c0-twin.sh --check | --once | --loop
PIN_ENGINE=9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514
TAPE_DIR="${R4BT_C0_TAPE_DIR_TEST:-/home/claude/mal-data/arena-tipfeed/tape}"

refuse() { echo "REFUSED: $*" >&2; exit 2; }

mode="${1:-}"
case "$mode" in --check|--once|--loop) ;; *) refuse "usage: $0 --check|--once|--loop" ;; esac

host="${R4BT_C0_TWIN_HOST_OVERRIDE:-$(hostname 2>/dev/null)}"
[ "$host" = "mal-fast-0" ] || refuse "host is '$host', not mal-fast-0"

repo="${R4BT_C0_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
[ -f "$repo/tools/arena_paper.py" ] || refuse "no tools/arena_paper.py under $repo"
got="$(sha256sum "$repo/tools/arena_paper.py" | cut -d' ' -f1)"
[ "$got" = "$PIN_ENGINE" ] || refuse "tools/arena_paper.py sha256 $got is not the pin $PIN_ENGINE"

for v in R4BT_W_SHA R4BT_C0_TWIN_SHA256 R4BT_FEED_DELAY_SLOTS R4BT_C0_MANIFEST R4BT_C0_ORDERS R4BT_C0_STATE_DIR; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || refuse "$v is not set"
done
tgot="$(sha256sum "$repo/tools/r4bt_c0_twin.py" | cut -d' ' -f1)"
[ "$tgot" = "$R4BT_C0_TWIN_SHA256" ] || refuse "tools/r4bt_c0_twin.py sha256 $tgot is not the installed pin"
[ -d "$TAPE_DIR" ] || refuse "the derived copy $TAPE_DIR does not exist"
case "$R4BT_FEED_DELAY_SLOTS" in ''|*[!0-9]*) refuse "R4BT_FEED_DELAY_SLOTS must be a whole number" ;; esac

if [ "$mode" = "--check" ]; then echo "OK: pins and host verified"; exit 0; fi

run_once() {
  nice -n 19 ionice -c3 "${R4BT_C0_PYTHON:-python3}" -I tools/r4bt_c0_twin.py --tape-dir "$TAPE_DIR" --manifest "$R4BT_C0_MANIFEST" --orders "$R4BT_C0_ORDERS" \
    --out-dir "$R4BT_C0_STATE_DIR" --feed-delay-slots "$R4BT_FEED_DELAY_SLOTS" --w-sha "$R4BT_W_SHA"
}

cd "$repo" || refuse "cannot cd to $repo"
if [ "$mode" = "--once" ]; then run_once; exit $?; fi
while :; do
  run_once || echo "r4bt-c0-twin: run failed (the previous run record ages; the executor's twin gate refuses on stale)" >&2
  sleep "${R4BT_C0_INTERVAL:-300}"
done
