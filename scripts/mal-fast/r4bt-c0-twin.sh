#!/bin/sh
# r4bt-c0-twin.sh: job wrapper of the r4-b-T C0 paper twin writer (tools/r4bt_c0_twin.py; QP-RULING-1106-r4bt-c0 section 2). POSIX sh. PAPER ONLY, no credential.
# Runs the twin writer at nice 19 / ionice idle, once or in a loop, on mal-fast-0 only. It refuses (exit 2, nothing started) unless:
#   * the host is mal-fast-0, and on mal-fast-0 no test override (R4BT_C0_TWIN_HOST_OVERRIDE, R4BT_C0_TAPE_DIR_TEST) is set;
#   * tools/arena_paper.py has the pinned sha256 (DEC-031 Am.27 item 24);
#   * tools/r4bt_c0_twin.py equals the sha256 the install pinned in R4BT_C0_TWIN_SHA256;
#   * every variable below is set and valid (the tool itself pins every loaded tools.* module to its blob at W and checks --w-sha).
# Environment (set by the C0 unit's drop-in, never on the command line; the drop-in must NOT set R4BT_C0_TWIN_HOST_OVERRIDE, R4BT_C0_TAPE_DIR_TEST or R4BT_C0_TWIN_TEST):
#   R4BT_C0_REPO          the C0 INSTALL tree (a descendant of W; this tool is not at W) (default: this script's repo)
#   R4BT_W_SHA            W = 17e3b05a7696900cda7fcf93e4af4008d8fa0184
#   R4BT_C0_TWIN_SHA256   pinned sha256 of tools/r4bt_c0_twin.py
#   R4BT_FEED_DELAY_SLOTS d (the registered whole number of slots)
#   R4BT_C0_ORDERS        the C0 executor's order ledger (sent orders are read, others skipped)
#   R4BT_C0_STATE_DIR     the C0 state dir: twins.jsonl and twins.run.json are written here (never inside the tape dir)
#   R4BT_C0_PYTHON        the venv python (no default: the system python3 lacks the lab packages)
#   R4BT_C0_INTERVAL      seconds between runs in loop mode, a positive whole number (default 300)
# The derived copy is read in place: /home/claude/mal-data/arena-tipfeed/tape (closed tape-*.jsonl.zst, .writer/counts.jsonl, .push/verified.json). The writer run covering
# the C0 must keep verified hours >= 400 h (--keep-verified-h 400); --check-tape prints the oldest present hour name against the needed first hour and refuses if later.
# Usage: r4bt-c0-twin.sh --check | --check-tape | --once | --loop
PIN_ENGINE=9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514
TAPE_DIR="${R4BT_C0_TAPE_DIR_TEST:-/home/claude/mal-data/arena-tipfeed/tape}"

refuse() { echo "REFUSED: $*" >&2; exit 2; }

mode="${1:-}"
case "$mode" in --check|--check-tape|--once|--loop) ;; *) refuse "usage: $0 --check|--check-tape|--once|--loop" ;; esac

realhost="$(hostname 2>/dev/null)"
if [ "$realhost" = "mal-fast-0" ] && { [ -n "${R4BT_C0_TWIN_HOST_OVERRIDE:-}" ] || [ -n "${R4BT_C0_TAPE_DIR_TEST:-}" ] || [ -n "${R4BT_C0_TWIN_TEST:-}" ]; }; then
  refuse "a test override is set on mal-fast-0: production cannot be pointed elsewhere"
fi
host="${R4BT_C0_TWIN_HOST_OVERRIDE:-$realhost}"
[ "$host" = "mal-fast-0" ] || refuse "host is '$host', not mal-fast-0"

repo="${R4BT_C0_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
[ -f "$repo/tools/arena_paper.py" ] || refuse "no tools/arena_paper.py under $repo"
got="$(sha256sum "$repo/tools/arena_paper.py" | cut -d' ' -f1)"
[ "$got" = "$PIN_ENGINE" ] || refuse "tools/arena_paper.py sha256 $got is not the pin $PIN_ENGINE"

for v in R4BT_W_SHA R4BT_C0_TWIN_SHA256 R4BT_FEED_DELAY_SLOTS R4BT_C0_ORDERS R4BT_C0_STATE_DIR R4BT_C0_PYTHON; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || refuse "$v is not set"
done
tgot="$(sha256sum "$repo/tools/r4bt_c0_twin.py" | cut -d' ' -f1)"
[ "$tgot" = "$R4BT_C0_TWIN_SHA256" ] || refuse "tools/r4bt_c0_twin.py sha256 $tgot is not the installed pin"
[ -d "$TAPE_DIR" ] || refuse "the derived copy $TAPE_DIR does not exist"
case "$R4BT_FEED_DELAY_SLOTS" in ''|*[!0-9]*) refuse "R4BT_FEED_DELAY_SLOTS must be a whole number" ;; esac
interval="${R4BT_C0_INTERVAL:-300}"
case "$interval" in ''|*[!0-9]*|0|00*) refuse "R4BT_C0_INTERVAL must be a positive whole number of seconds" ;; esac

if [ "$mode" = "--check" ]; then echo "OK: pins and host verified"; exit 0; fi

cd "$repo" || refuse "cannot cd to $repo"
if [ "$mode" = "--check-tape" ]; then
  nice -n 19 ionice -c3 "$R4BT_C0_PYTHON" -I tools/r4bt_c0_twin.py --check --tape-dir "$TAPE_DIR" --orders "$R4BT_C0_ORDERS"
  exit $?
fi

run_once() {
  nice -n 19 ionice -c3 "$R4BT_C0_PYTHON" -I tools/r4bt_c0_twin.py --tape-dir "$TAPE_DIR" --orders "$R4BT_C0_ORDERS" \
    --out-dir "$R4BT_C0_STATE_DIR" --feed-delay-slots "$R4BT_FEED_DELAY_SLOTS" --w-sha "$R4BT_W_SHA"
}

if [ "$mode" = "--once" ]; then run_once; exit $?; fi
while :; do
  run_once || echo "r4bt-c0-twin: run failed (the previous run record ages; the executor's twin gate refuses on stale)" >&2
  sleep "$interval"
done
