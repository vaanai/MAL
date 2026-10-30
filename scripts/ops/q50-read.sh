#!/bin/sh
# q50-read.sh [--setup | --check | --wait-only | --postread] - the unattended DEC-031 gate read of h5v1q50-boostfloor-a1 (Q50). POSIX sh (MiScusi runs jobs under sh). PAPER ONLY.
# Pack: /data/mal/hunt-1008/ops/manager18/Q50-READ-PACK-1030.md (pins, citations, refusal paths, disclosures). Written 2026-10-30 by the manager18 q50-read-pack worker.
#
# Runs on mal-fast-0 from a MiScusi checkout of the commit that holds this file (cwd = that checkout's root). The read tools never run from that checkout: the script makes
# a fresh git worktree of a31d9864829d1f0645fef8be095eb8df575c2d00 for this run only (removed at exit; DEC-031 Am.18 item 6.1; docs/HANDOFF.md section 1 Q50 row) and runs every tool from there,
# with the books v2 byte-copy (sha256 819c699d...).
#
# Default mode (the read), in order; each step refuses (exit 2, nothing read) on any doubt:
#   1. Preflight: host, worktree at the pin (HEAD, tools/ tree hash, clean, 13 file sha256 pins), python imports resolve inside the worktree, books v2 copy (sha256, read-only),
#      params file (N, c, shadow dirs), applied log readable, no output file exists, no a2 read-instant.json.
#   2. Wait for the Amendment 11 write-once record <ledger>/h5v1q50-boostfloor-a1/q50-read-instant.json (written by the 00:20Z score loop #1543 when B qualifies,
#      or ENDED at the end-seal). Only six fields are read: rule, bot_id, status, read_instant_ms, boundary_ms, clock_start_ms/end_seal_ms. Polls every 300 s,
#      until 2026-11-06T03:00Z (the 11-06T00:20Z score run must record READ or ENDED).
#   3. Wait for DEC-031 Am.4 item 1.6 (a): an applied A3 decision (a3-applied.jsonl line) with run_utc >= the boundary (B, or the end-seal for ENDED), reading only run_utc and
#      verdict. Refused if any applied verdict since the Q50 registration is not NONE/PASS (a window end may apply), or two consecutive applied decisions since the
#      registration are more than 48 h apart (the dead-man may have ended the book). Waits up to boundary + 48 h.
#   4. Avoids the score loop's window [00:10Z, 01:00Z). Re-runs every preflight check, then the ONE deciding read: python -m tools.arena_h5_read (the default book set of
#      the books file: Q50 beside the two a2 siblings, DEC-031 Am.5 item 4.3) with --json. stdout/stderr/json go to write-once files (noclobber, then a-w).
#   5. Only if Q50's STATUS is PASS: tools.arena_h5_legs (worst-in-slot, DEC-032 section 1 item 2) and tools.arena_h5_inband (DEC-033 section 11 item 3; --quote-source
#      shadow-wsol; only the shadow dirs the scorer counted). An in-band FAIL blocks C0. Skipped (exit 5) if the h5-q50-* dirs on disk differ from the params' SHADOW_DIR set.
#   6. The job log gets ONLY: verdict tokens, flags, booleans, instants, structure counts of the applied log, the output files' sha256, and (on a refusal) the refusal
#      text with every digit masked. No count, no figure, no reason text of a NOT_DECIDABLE.
# --setup      makes the out dir and the books v2 byte-copy (only while the live books file is still v2), then stops. --check: preflight plus names/existence, then stops.
# --wait-only  steps 1 to 3, then exit 0 (for a light waiter job; the read job is chained `after` it). --postread: step 5 only, after a PASS read whose post-read was skipped.
# Exit: 0 read recorded (and the post-read done or not applicable) / setup, check or wait done; 2 refused (no read recorded); 3 the read tool ran but printed no Q50 status
#       (manager item); 4 test mode only: would wait or sleep; 5 read recorded, PASS, post-read skipped or refused (manager item).
#
# Test mode (Q50R_TEST=1 ONLY, synthetic fixtures): Q50T_NOW_MS (fake clock, ms; required), Q50T_ARENA, Q50T_LEDGER, Q50T_APPLIED, Q50T_OUT, Q50T_BOOKS, Q50T_PARAMS,
#   Q50T_PY, Q50T_POLLS (polls of 1 s before "would wait", default 0). Every test path must resolve outside /home/claude/data, /data/mal/arena and /var/lib/mal (path
#   check only, nothing opened). In production none of these variables is read (they are read only between the TEST MODE markers below), and production refuses if any
#   Q50T_* or Q50R_TEST variable is set at all.
set -u
SCRIPT_SHA=$(sha256sum "$0" | cut -c1-64)
MODE=read
case "${1:-}" in
  '') ;;
  --setup) MODE=setup;;
  --check) MODE=check;;
  --wait-only) MODE=wait;;
  --postread) MODE=postread;;
  *) echo "REFUSED: usage: q50-read.sh [--setup | --check | --wait-only | --postread]"; exit 2;;
esac
[ "$#" -le 1 ] || { echo "REFUSED: at most one argument"; exit 2; }

BOT=h5v1q50-boostfloor-a1
A2_BOTS="h5v1-boostfloor-a2 h5v2-boostclock-a2"
PIN_HEAD=a31d9864829d1f0645fef8be095eb8df575c2d00
PIN_TOOLS_TREE=699a28f7ec9e4d5719b6287e042ecb2a845dd8cd
BOOKS_V2_SHA=819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0
RULE='DEC-031 Amendment 11 item 1 (counted trades, QP-RULING-1024-q50-m1)'
CLOCK_MS=1792886400000          # 2026-10-25T00:00:00Z (EXP-024 Am.10 Note (a) pins; DEC-031 Am.5 item 4.1)
END_MS=1793923200000            # 2026-11-06T00:00:00Z, the end-seal (DEC-031 Am.11 item 2)
REG_MS=1792801215395            # registered_at_ms (ARTIFACTS/lab/arena-records-2026-10-24-q50.md:8)
INSTANT_DEADLINE_MS=1793934000000   # 2026-11-06T03:00:00Z: the 11-06T00:20Z score run records READ or ENDED by then
A3_WAIT_MS=172800000            # wait for the A3 guard line up to boundary + 48 h
DEADMAN_MS=172800000            # tools/a3_halt.py DEADMAN_S = 48 h
DAY_MS=86400000
PINS="arena_h5_read.py=725cabf9ea1b577131afb0ed301cc539c6cb91fd55c6be70d2ee36c5c1f64f9b
arena_h5_legs.py=1635f864bdddc08fb25fb04064aa9b700a49bb7987db384c8fe90b8f4a87b549
arena_h5_inband.py=adde2049cb79902737330b3f39e75e8b6c16d795249e0bdb63cfebd3d0a0c3fe
arena_h5_books.py=880c487673a3b81ecdb24d3fa6b1e79944c5ce5ee041dbaa7791482e0a365718
arena_h5_q50_instant.py=3b62be7a40058220f3f5d9177299b08ff948c9f1f15c8d5a0543098ed524140a
arena_h5_score.py=dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006
arena_h5_cost.py=b20d51ceb939822ce813ca48b14348e61873277fef54a3d71af660e37fe03328
arena_score.py=c4859323622794f5f97a131eb32256c905ca2ab6c1adf8ab21568fc2a98f27f3
arena_stack.py=a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778
arena_report_legs.py=65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b
h5_arena_gates.py=15d6ad94c09789d4cb1ada5b89fd1697307804cd9ae97f2de892d0a662d51864
arena_paper.py=9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514
paper_curve_math.py=019c64c317d98d8b11d1319d8202ca72d49df3fee12b983cf3c6f407753fcafa"
OUTS="q50-read.out q50-read.err q50-read.json q50-legs.out q50-legs.err q50-legs.json q50-inband.out q50-inband.err q50-inband.json"
DISCLOSURE='The deciding CI90 is a trade-level bootstrap (DEC-031 §2; EXP-024 §7 item 3; arena_score.cell_verdict at a31d9864). Trades on the same UTC date are not independent, so this interval is likely narrower than a date-cluster interval. The date-cluster figure is not computed by the registered read and decides nothing.'

refuse() { echo "REFUSED: $*"; exit 2; }

TEST=0
# >>> TEST MODE (the only lines that read a Q50T_* or Q50R_TEST variable)
if [ "${Q50R_TEST:-0}" = 1 ]; then
  TEST=1
  NOW_FAKE=${Q50T_NOW_MS:-}
  case "$NOW_FAKE" in ''|*[!0-9]*) refuse "test mode needs Q50T_NOW_MS (digits)";; esac
  ARENA=${Q50T_ARENA:-}; APPLIED=${Q50T_APPLIED:-}; OUT_DIR=${Q50T_OUT:-}
  [ -n "$ARENA" ] && [ -n "$APPLIED" ] && [ -n "$OUT_DIR" ] || refuse "test mode needs Q50T_ARENA, Q50T_APPLIED and Q50T_OUT"
  LEDGER=${Q50T_LEDGER:-$ARENA/ledger}; BOOKS_COPY=${Q50T_BOOKS:-$OUT_DIR/a3-books-v2-copy.json}; PARAMS=${Q50T_PARAMS:-$OUT_DIR/q50-read-params.txt}
  LIVE_BOOKS=$ARENA/a3-books.json
  PY=${Q50T_PY:-/data/mal/venv/bin/python}; POLLS_LEFT=${Q50T_POLLS:-0}; POLL_S=1
  case "$POLLS_LEFT" in ''|*[!0-9]*) refuse "Q50T_POLLS must be digits";; esac
  for p in "$ARENA" "$LEDGER" "$(dirname "$APPLIED")" "$OUT_DIR" "$(dirname "$BOOKS_COPY")" "$(dirname "$PARAMS")"; do
    r=$(cd "$p" 2>/dev/null && pwd -P) || refuse "test path missing: $p"
    case "$r" in /home/claude/data|/home/claude/data/*|/data/mal/arena|/data/mal/arena/*|/var/lib/mal|/var/lib/mal/*) refuse "test mode must not touch real paths ($r)";; esac
  done
  echo "TEST MODE (Q50R_TEST=1): fake clock $NOW_FAKE, synthetic fixture paths"
fi
# <<< TEST MODE
if [ "$TEST" = 0 ]; then
  [ "$(hostname)" = mal-fast-0 ] || refuse "production runs only on mal-fast-0 (the Q50 ledger, shadow dir and applied log are there)"
  env | cut -d= -f1 | grep -q -E '^(Q50T_|Q50R_TEST$)' && refuse "a test-mode variable is set in production"
  [ "${MAL_ARENA_TEST:-}" = 1 ] && refuse "MAL_ARENA_TEST=1 is never set in a production read"
  ARENA=/home/claude/data/arena
  LEDGER=$ARENA/ledger
  LIVE_BOOKS=$ARENA/a3-books.json
  APPLIED=/home/claude/data/structure-monitor/a3-applied.jsonl
  OUT_DIR=/home/claude/data/q50-read
  BOOKS_COPY=$OUT_DIR/a3-books-v2-copy.json
  PARAMS=$OUT_DIR/q50-read-params.txt
  PY=/home/claude/venvs/c1nf-ledger/bin/python3
  POLL_S=300
  POLLS_LEFT=0
fi
INSTANT=$LEDGER/$BOT/q50-read-instant.json
WT=$OUT_DIR/wt-a31d9864.$$   # this run's own worktree (a waiter job and a read job never share one); removed by the EXIT trap
O=$OUT_DIR

now_ms() { if [ "$TEST" = 1 ]; then echo "$NOW_FAKE"; else echo "$(date -u +%s)000"; fi; }
utc_of_ms() { date -u -d "@$(($1 / 1000))" +%FT%TZ; }
sha() { sha256sum "$1" | cut -c1-64; }
poll_sleep() {
  if [ "$TEST" = 1 ]; then
    [ "$POLLS_LEFT" -gt 0 ] || { echo "WOULD WAIT for $1 (test clock $(utc_of_ms "$(now_ms)"))"; exit 4; }
    POLLS_LEFT=$((POLLS_LEFT - 1))
  fi
  sleep "$POLL_S"
}
mask() { tr '0-9' '#' | tr -d '\r' | head -n 1 | cut -c1-400; }

# ---------------------------------------------------------------- setup (one-time, before the submit)
if [ "$MODE" = setup ]; then
  mkdir -p "$OUT_DIR" || refuse "cannot create $OUT_DIR"
  if [ -e "$BOOKS_COPY" ]; then
    echo "books copy exists: $BOOKS_COPY sha256 $(sha "$BOOKS_COPY") (not rewritten)"
  else
    [ "$(sha "$LIVE_BOOKS")" = "$BOOKS_V2_SHA" ] || refuse "the live books file is not v2 any more: no copy is made (DEC-031 Am.18 item 6.1 needs the copy taken before the swap)"
    ( set -C; cat "$LIVE_BOOKS" > "$BOOKS_COPY" ) || refuse "cannot create $BOOKS_COPY"
    chmod 0444 "$BOOKS_COPY"
    [ "$(sha "$BOOKS_COPY")" = "$BOOKS_V2_SHA" ] || refuse "copy sha differs"
    echo "books copy written: $BOOKS_COPY sha256 $BOOKS_V2_SHA mode 0444"
  fi
  [ -e "$PARAMS" ] && echo "params file present: $PARAMS sha256 $(sha "$PARAMS")" || echo "params file absent: $PARAMS (the manager writes it; see the pack)"
  echo "SETUP done $(date -u +%FT%TZ)"
  exit 0
fi

# ---------------------------------------------------------------- preflight
JOBREPO=$(pwd -P)
is_posint() { case "$1" in ''|*[!0-9]*|0*) return 1;; esac; return 0; }
is_tok() { case "$1" in ''|*[!A-Za-z0-9_.-]*) return 1;; esac; return 0; }
is_p() {
  case "$1" in ''|*[!0-9.]*|*.*.*) return 1;; 0|0.[0-9]*|1|1.) return 0;; 1.*) case "${1#1.}" in *[!0]*) return 1;; esac; return 0;; esac
  return 1
}
parse_params() {
  [ -f "$PARAMS" ] && [ -r "$PARAMS" ] || refuse "params file missing: $PARAMS (the manager writes it before the submit; see the pack)"
  P_N=; P_C=; P_CT=; XARGS=; SDIRS=; NPR=0; NOP=0
  while IFS= read -r ln || [ -n "$ln" ]; do
    case "$ln" in ''|'#'*) continue;; esac
    k=${ln%%=*}; v=${ln#*=}
    [ "$k" != "$ln" ] || refuse "params: a line has no '='"
    case "$k" in
      N_BOOKS) is_posint "$v" && [ -z "$P_N" ] || refuse "params: N_BOOKS must be one positive integer"; P_N=$v;;
      C) is_posint "$v" && [ -z "$P_C" ] || refuse "params: C must be one positive integer"; P_C=$v;;
      CELLS_TOTAL) is_posint "$v" && [ -z "$P_CT" ] || refuse "params: CELLS_TOTAL must be one positive integer"; P_CT=$v;;
      PRIOR_READ)
        b=${v%%=*}; t=${v#*=}
        [ "$b" != "$v" ] && is_tok "$b" && is_tok "$t" || refuse "params: PRIOR_READ must be BOT=TOKEN ([A-Za-z0-9_.-])"
        [ "$b" != "$BOT" ] || refuse "params: PRIOR_READ cannot name $BOT (it is the book read here)"
        XARGS="$XARGS --prior-read $b=$t"; NPR=$((NPR + 1));;
      OTHER_P)
        b=${v%%=*}; t=${v#*=}
        [ "$b" != "$v" ] && is_tok "$b" && is_p "$t" || refuse "params: OTHER_P must be BOT=P with P a decimal in [0, 1]"
        [ "$b" != "$BOT" ] || refuse "params: OTHER_P cannot name $BOT"
        XARGS="$XARGS --other-p $b=$t"; NOP=$((NOP + 1));;
      SHADOW_DIR)
        case "$v" in "$ARENA"/h5-q50-20[0-9][0-9]-[01][0-9]-[0-3][0-9]|"$ARENA"/h5-q50-20[0-9][0-9]-[01][0-9]-[0-3][0-9]-succ) ;;
          *) refuse "params: SHADOW_DIR must be $ARENA/h5-q50-<date> or $ARENA/h5-q50-<date>-succ";; esac
        [ -d "$v" ] || refuse "params: SHADOW_DIR $v is not a directory"
        case " $SDIRS " in *" $v "*) refuse "params: SHADOW_DIR $v given twice";; esac
        SDIRS="${SDIRS:+$SDIRS }$v";;
      *) refuse "params: unknown key";;
    esac
  done < "$PARAMS"
  [ -n "$P_N" ] && [ -n "$P_C" ] && [ -n "$SDIRS" ] || refuse "params need N_BOOKS, C and at least one SHADOW_DIR"
  [ "$P_N" -ge $((1 + NPR)) ] || refuse "params: N_BOOKS is smaller than the books read (Q50 plus the PRIOR_READ books); the tool would refuse after the read"
  MARGS="--n-books $P_N --c $P_C${P_CT:+ --cells-total $P_CT}$XARGS"
  PARAMS_SHA=$(sha "$PARAMS")
}
check_wt() {
  [ -d "$WT" ] || refuse "worktree $WT missing"
  [ "$(git -C "$WT" rev-parse HEAD 2>/dev/null)" = "$PIN_HEAD" ] || refuse "worktree $WT is not at $PIN_HEAD"
  [ "$(git -C "$WT" rev-parse --show-toplevel 2>/dev/null)" = "$(cd "$WT" && pwd -P)" ] || refuse "worktree $WT is not a git worktree root"
  [ "$(git -C "$WT" rev-parse HEAD:tools 2>/dev/null)" = "$PIN_TOOLS_TREE" ] || refuse "tools/ tree of $WT is not $PIN_TOOLS_TREE"
  [ -z "$(git -C "$WT" status --porcelain --untracked-files=no 2>/dev/null)" ] || refuse "worktree $WT has local changes"
  for kv in $PINS; do
    f=${kv%%=*}; s=${kv#*=}
    [ "$(sha "$WT/tools/$f")" = "$s" ] || refuse "tools/$f in $WT is not sha256 $s"
  done
}
preflight() {
  git -C "$JOBREPO" rev-parse --git-dir >/dev/null 2>&1 || refuse "cwd is not a git checkout"
  git -C "$JOBREPO" cat-file -e "$PIN_HEAD^{commit}" 2>/dev/null || git -C "$JOBREPO" fetch -q origin "$PIN_HEAD" >/dev/null 2>&1 || true
  git -C "$JOBREPO" cat-file -e "$PIN_HEAD^{commit}" 2>/dev/null || refuse "the checkout does not contain $PIN_HEAD (a shallow clone?)"
  [ -d "$OUT_DIR" ] || refuse "out dir missing: $OUT_DIR (run --setup first)"
  [ ! -e "$WT" ] || refuse "worktree path $WT exists"
  trap 'git -C "$JOBREPO" worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
  git -C "$JOBREPO" worktree add --detach "$WT" "$PIN_HEAD" >/dev/null 2>&1 || refuse "git worktree add $WT $PIN_HEAD failed"
  check_wt
  IMP=$(cd "$WT" && "$PY" -E -B -c 'import os, numpy, tools.arena_h5_read as R, tools.arena_h5_legs as L, tools.arena_h5_inband as I
print(os.path.realpath(R.__file__))' 2>/dev/null) || refuse "$PY cannot import numpy and the read tools from $WT"
  [ "$IMP" = "$(cd "$WT" && pwd -P)/tools/arena_h5_read.py" ] || refuse "the read tools did not import from $WT"
  [ -f "$BOOKS_COPY" ] && [ "$(sha "$BOOKS_COPY")" = "$BOOKS_V2_SHA" ] || refuse "books v2 copy $BOOKS_COPY missing or not sha256 $BOOKS_V2_SHA"
  [ ! -w "$BOOKS_COPY" ] || refuse "books v2 copy is writable (chmod a-w it)"
  parse_params
  [ -r "$APPLIED" ] || refuse "applied log not readable: $APPLIED"
  [ -d "$LEDGER" ] || refuse "ledger root missing: $LEDGER"
  for a in $A2_BOTS; do
    [ -e "$LEDGER/$a/read-instant.json" ] && refuse "$a has a read-instant.json: the default book set would read it in this job (a manager and QP item)"
  done
  return 0
}
check_fresh() {  # $1 = read | postread
  if [ "$1" = read ]; then
    for f in $OUTS; do [ -e "$O/$f" ] && refuse "$O/$f exists: the one read of $BOT was already started (one read per bot_id, DEC-031 Am.11 item 4; a re-run is a manager decision)"; done
  else
    for f in q50-read.out q50-read.json; do [ -e "$O/$f" ] || refuse "--postread needs the read outputs ($O/$f missing)"; done
    for f in q50-legs.out q50-legs.err q50-legs.json q50-inband.out q50-inband.err q50-inband.json; do [ -e "$O/$f" ] && refuse "$O/$f exists: the post-read already ran"; done
  fi
  return 0
}

# ---------------------------------------------------------------- the instant record (six fields) and the applied log (run_utc and verdict)
inst_state() {
  [ -e "$INSTANT" ] || { echo ABSENT; return 0; }
  "$PY" -I -c 'import json, sys
p, bot, rule, clock, end, day = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
try:
    r = json.load(open(p))
except Exception:
    print("BAD unparseable"); sys.exit(0)
def isint(x):
    return isinstance(x, int) and not isinstance(x, bool)
if not isinstance(r, dict):
    print("BAD the record is not an object")
elif r.get("rule") != rule or r.get("bot_id") != bot:
    print("BAD the record is not an Amendment 11 record of this book")
elif r.get("clock_start_ms") != clock or r.get("end_seal_ms") != end:
    print("BAD the record clock or end-seal is not the registered one")
elif r.get("status") == "READ":
    b = r.get("read_instant_ms")
    if isint(b) and b % day == 0 and clock < b <= end and r.get("boundary_ms") == b and r.get("open_before_read") == 0:
        print("READ", b)
    else:
        print("BAD READ record with an instant that is not a 00:00Z boundary in (clock, end-seal]")
elif r.get("status") == "ENDED_NOT_DECIDABLE":
    print("ENDED", end) if r.get("read_instant_ms") is None and r.get("boundary_ms") == end else print("BAD ENDED record malformed")
else:
    print("BAD status is not READ or ENDED_NOT_DECIDABLE")' "$INSTANT" "$BOT" "$RULE" "$CLOCK_MS" "$END_MS" "$DAY_MS" 2>/dev/null || echo "BAD the record check failed"
}
a3_state() {  # $1 = boundary ms (0 = summary only)
  "$PY" -I -c 'import json, sys
from datetime import datetime, timezone
path, bound, reg, gmax = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
iso = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def ms(t):  # tools/a3_halt.utc_ms, the dead-man parse
    return int(datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
try:
    text = open(path, encoding="utf-8").read()
except OSError:
    print("BAD applied log unreadable"); sys.exit(0)
lines = text.split("\n")
torn = not text.endswith("\n") and text != ""
if lines and lines[-1] == "":
    lines.pop()
recs = []
for i, ln in enumerate(lines):
    if not ln.strip():
        continue
    try:
        d = json.loads(ln)
        t, v = ms(d["run_utc"]), d["verdict"]
        if not isinstance(v, str):
            raise ValueError
    except Exception:
        if torn and i == len(lines) - 1:
            print("WAIT the last applied line is still being written"); sys.exit(0)
        print("BAD applied log line %d is not a {run_utc, verdict} line" % (i + 1)); sys.exit(0)
    recs.append((t, v))
since = sorted(x for x in recs if x[0] >= reg)
hard = [v for t, v in since if v not in ("NONE", "PASS")]
if bound == 0:
    last = max(recs) if recs else None
    print("newest applied run_utc=%s verdict=%s; since the Q50 registration: %d lines, %d not NONE/PASS" % (iso(last[0]) if last else "none", last[1] if last else "none", len(since), len(hard)))
    sys.exit(0)
if hard:
    print("BAD an applied verdict %s since the Q50 registration: a window end may apply (DEC-031 Am.4 item 1.6 (b)); QP rules before any read" % hard[0]); sys.exit(0)
guard = [x for x in since if x[0] >= bound]
if not guard:
    print("WAIT none"); sys.exit(0)
g = guard[0]
upto = [t for t, v in since if t <= g[0]]
seq = [reg] + upto
mg = max(b - a for a, b in zip(seq, seq[1:]))
if mg > gmax:
    print("BAD two applied A3 decisions since the Q50 registration are %.3f h apart (> 48 h): the dead-man may have ended the book (DEC-031 Am.4 item 1.8)" % (mg / 3.6e6)); sys.exit(0)
npass = sum(1 for t, v in since if t <= g[0] and v == "PASS")
print("READY run_utc=%s verdict=%s lines_since_registration=%d pass_lines=%d max_gap_h=%.3f" % (iso(g[0]), g[1], len(upto), npass, mg / 3.6e6))' "$APPLIED" "$1" "$REG_MS" "$DEADMAN_MS" 2>/dev/null || echo "BAD the applied-log check failed"
}
wait_instant() {
  NB=0
  while :; do
    S=$(inst_state)
    case "$S" in
      "READ "*|"ENDED "*) break;;
      ABSENT) NB=0;;
      "BAD unparseable") NB=$((NB + 1)); [ "$NB" -ge 3 ] && refuse "instant record $INSTANT stays unparseable";;
      *) refuse "instant record $INSTANT: ${S#BAD }";;
    esac
    [ "$(now_ms)" -lt "$INSTANT_DEADLINE_MS" ] || refuse "no Amendment 11 record by $(utc_of_ms "$INSTANT_DEADLINE_MS"): the 11-06T00:20Z score run did not record READ or ENDED (a manager item)"
    poll_sleep "the Amendment 11 record $INSTANT"
  done
  IKIND=${S%% *}; BOUND=${S#* }
  INSTANT_SHA=$(sha "$INSTANT")
}
wait_a3() {
  A3_DEADLINE=$((BOUND + A3_WAIT_MS))
  while :; do
    S=$(a3_state "$BOUND")
    case "$S" in
      "READY "*) break;;
      "WAIT "*) ;;
      *) refuse "A3 (DEC-031 Am.4 item 1.6 (a)): ${S#BAD }";;
    esac
    [ "$(now_ms)" -lt "$A3_DEADLINE" ] || refuse "no applied A3 decision with run_utc >= $(utc_of_ms "$BOUND") by $(utc_of_ms "$A3_DEADLINE"): no read (DEC-031 Am.4 item 1.6 (a); a manager item)"
    poll_sleep "an applied A3 decision with run_utc >= $(utc_of_ms "$BOUND")"
  done
  A3_LINE=${S#READY }
}
shadow_set_ok() {  # the h5-q50-* dirs on disk (names only) equal the params SHADOW_DIR set
  ON_DISK=
  for d in "$ARENA"/h5-q50-*; do [ -d "$d" ] && ON_DISK="${ON_DISK:+$ON_DISK }$d"; done
  A=$(printf '%s\n' $ON_DISK | sort | tr '\n' ' '); Bs=$(printf '%s\n' $SDIRS | sort | tr '\n' ' ')
  [ "$A" = "$Bs" ]
}

# ---------------------------------------------------------------- the post-read (PASS only): worst-in-slot legs and the in-band restatement
postread() {
  if ! shadow_set_ok; then
    echo "POSTREAD SKIPPED: the h5-q50-* dirs on disk ($(for d in $ON_DISK; do basename "$d"; done | tr '\n' ' ')) differ from the params SHADOW_DIR set ($(for d in $SDIRS; do basename "$d"; done | tr '\n' ' ')): the in-band tool must get only the dirs the scorer counted (DEC-033 section 11 item 3 (e)); the manager sets SHADOW_DIR, then runs --postread"
    return 5
  fi
  SARGS=; for d in $SDIRS; do SARGS="$SARGS --shadow-dir $d"; done
  PR=0
  ( set -C; : > "$O/q50-legs.out" && : > "$O/q50-legs.err" ) || refuse "cannot create the legs output files (exist?)"
  echo "LEGS start $(date -u +%FT%TZ)"
  ( cd "$WT" && exec "$PY" -E -B -m tools.arena_h5_legs --bot "$BOT" --ledger-root "$LEDGER" --books-file "$BOOKS_COPY" --read-instant "$BOUND" --i-am-the-read $SARGS \
      --json "$O/q50-legs.json" ) >> "$O/q50-legs.out" 2>> "$O/q50-legs.err"
  LRC=$?
  if [ -f "$O/q50-legs.json" ]; then
    "$PY" -I -c 'import json, sys
r = json.load(open(sys.argv[1]))
g = r.get("dec032_worst_in_slot") or {}
print("LEGS tool_exit=%s worst_in_slot_measured=%s end_consistency_ok=%s dec032_measured=%s dec032_not_proposed_wis_le_0=%s" % (sys.argv[2], r.get("worst_in_slot_measured"),
      (r.get("end_check") or {}).get("ok"), g.get("measured"), g.get("not_proposed_mean_le_0") if g.get("measured") else "n/a"))' "$O/q50-legs.json" "$LRC" 2>/dev/null || echo "LEGS tool_exit=$LRC (json unreadable)"
  else
    echo "LEGS REFUSED by the tool, tool_exit=$LRC (digits masked): $(mask < "$O/q50-legs.err")"; PR=5
  fi
  [ "$LRC" -eq 0 ] || [ "$LRC" -eq 4 ] || PR=5
  ( set -C; : > "$O/q50-inband.out" && : > "$O/q50-inband.err" ) || refuse "cannot create the in-band output files (exist?)"
  echo "INBAND start $(date -u +%FT%TZ)"
  ( cd "$WT" && exec "$PY" -E -B -m tools.arena_h5_inband --ledger-root "$LEDGER" $SARGS --books-file "$BOOKS_COPY" --bot "$BOT" --read-instant "$BOUND" --i-am-the-read \
      --quote-source shadow-wsol --out "$O/q50-inband.json" ) >> "$O/q50-inband.out" 2>> "$O/q50-inband.err"
  IRC=$?
  if [ "$IRC" -eq 0 ] && [ -f "$O/q50-inband.json" ]; then
    "$PY" -I -c 'import json, sys
r = json.loads(open(sys.argv[1]).readline())
main = bool(r.get("in_band_gate_all_pass"))
own = r.get("in_band_own_refit_report_only")
ownp = None if own is None else all(v["gate_pass"] for v in own.values())
cells = r.get("in_band") or {}
co = (not main) and all(m.startswith(("n ", "days ")) for v in cells.values() for m in v.get("gate_misses", []))
print("INBAND tool_exit=0 quote_source=%s in_band_gate_all_pass=%s own_refit_all_pass=%s misses_count_only=%s" % (r["band"]["quote_source"], main, ownp, co))
print("INBAND VERDICT PASS (in-sample restatement; C0 still needs QP, DEC-033 in force and the owner line)" if main else
      "INBAND VERDICT FAIL: C0 is blocked (QP-PRERULING-1027-xorder Q4; DEC-033 section 11 item 3 (d))" + (" [in-band NOT_DECIDABLE: n or days]" if co else ""))
if ownp is not None and ownp != main:
    print("INBAND NOTE the in-band own-refit verdict differs from the used intercept (DEC-033 section 11 item 3 (a)): QP quotes both, never the kinder alone")' "$O/q50-inband.json" 2>/dev/null || { echo "INBAND json unreadable"; PR=5; }
  else
    echo "INBAND REFUSED by the tool, tool_exit=$IRC (digits masked): $(mask < "$O/q50-inband.err")"; PR=5
  fi
  return "$PR"
}
outputs_done() {
  for f in $OUTS; do
    [ -f "$O/$f" ] || continue
    chmod a-w "$O/$f"
    echo "output $O/$f sha256 $(sha "$O/$f")"
  done
}

# ---------------------------------------------------------------- main
preflight
echo "JOB start $(date -u +%FT%TZ) mode=$MODE host=$(hostname) script=$SCRIPT_SHA job_head=$(git -C "$JOBREPO" rev-parse HEAD) worktree=$WT head=$PIN_HEAD tools_tree=$PIN_TOOLS_TREE"
echo "PINS $(for kv in $PINS; do printf '%s ' "${kv%%=*}=$(printf '%s' "${kv#*=}" | cut -c1-12)"; done)(full sha256 in the pack; all checked)"
echo "BOOKS v2 copy $BOOKS_COPY sha256 $BOOKS_V2_SHA; PARAMS $PARAMS sha256 $PARAMS_SHA N_BOOKS=$P_N C=$P_C CELLS_TOTAL=${P_CT:-none} PRIOR_READ=$NPR OTHER_P=$NOP SHADOW_DIR=$(for d in $SDIRS; do basename "$d"; done | tr '\n' ' ')"

if [ "$MODE" = check ]; then
  echo "INSTANT $(inst_state | sed 's/^READ \([0-9]*\)$/READ B=\1/')"
  echo "A3 $(a3_state 0)"
  for f in $OUTS; do [ -e "$O/$f" ] && echo "output exists: $O/$f"; done
  shadow_set_ok && echo "shadow dirs on disk equal the params SHADOW_DIR set" || echo "shadow dirs on disk differ from the params SHADOW_DIR set (the post-read would be skipped)"
  echo "CHECK OK $(date -u +%FT%TZ)"
  exit 0
fi

if [ "$MODE" = postread ]; then
  check_fresh postread
  S=$(inst_state); [ "${S%% *}" = READ ] || refuse "--postread needs a READ instant record"
  BOUND=${S#READ }
  S=$(a3_state "$BOUND"); case "$S" in "READY "*) ;; *) refuse "A3: ${S#BAD }";; esac
  ST=$("$PY" -I -c 'import json, sys
b = json.load(open(sys.argv[1]))["books"].get(sys.argv[2]) or {}
print(b.get("fail_label") or b.get("status") or "none")' "$O/q50-read.json" "$BOT" 2>/dev/null)
  [ "$ST" = PASS ] || refuse "--postread runs only after a PASS read"
  postread; RC=$?
  outputs_done
  echo "JOB done $(date -u +%FT%TZ)"
  exit "$RC"
fi

check_fresh read
wait_instant
echo "INSTANT status=$IKIND boundary=$(utc_of_ms "$BOUND") ($BOUND) file_sha256=$INSTANT_SHA"
wait_a3
echo "A3 guard (DEC-031 Am.4 item 1.6 (a)): $A3_LINE"
if [ "$MODE" = wait ]; then
  echo "READY $(date -u +%FT%TZ): the read job (chained after this one) may run"
  exit 0
fi

# avoid the score loop's run window [00:10Z, 01:00Z) (it writes the ledger at 00:20Z)
while :; do
  SOD=$(( ($(now_ms) / 1000) % 86400 ))
  [ "$SOD" -ge 600 ] && [ "$SOD" -lt 3600 ] || break
  [ "$TEST" = 1 ] && { echo "WOULD SLEEP until 01:00Z (test clock $(utc_of_ms "$(now_ms)"))"; exit 4; }
  sleep 60
done

# re-check everything right before the read
check_wt
parse_params
[ "$(sha "$BOOKS_COPY")" = "$BOOKS_V2_SHA" ] || refuse "books v2 copy changed"
[ "$(inst_state)" = "$IKIND $BOUND" ] && [ "$(sha "$INSTANT")" = "$INSTANT_SHA" ] || refuse "the instant record changed during the wait"
S=$(a3_state "$BOUND"); [ "$S" = "READY $A3_LINE" ] || refuse "A3 state changed: ${S#BAD }"
for a in $A2_BOTS; do [ -e "$LEDGER/$a/read-instant.json" ] && refuse "$a has a read-instant.json (a manager and QP item)"; done
check_fresh read

# ---------------------------------------------------------------- the one deciding read
( set -C; : > "$O/q50-read.out" && : > "$O/q50-read.err" ) || refuse "cannot create the read output files (exist?)"
echo "READ start $(date -u +%FT%TZ) read_instant=$BOUND"
( cd "$WT" && exec "$PY" -E -B -m tools.arena_h5_read --ledger-root "$LEDGER" --books-file "$BOOKS_COPY" --read-instant "$BOUND" --i-am-the-read $MARGS \
    --json "$O/q50-read.json" ) >> "$O/q50-read.out" 2>> "$O/q50-read.err"
RRC=$?
echo "READ end $(date -u +%FT%TZ) tool_exit=$RRC"
RES=
[ -f "$O/q50-read.json" ] && RES=$("$PY" -I -c 'import json, sys
rep, bot = json.load(open(sys.argv[1])), sys.argv[2]
b = rep.get("books", {}).get(bot)
print("STATUS " + ((b.get("fail_label") or b.get("status")) if b else "none"))
if b:
    if b.get("status") == "PASS" and b.get("not_proposed_for_canary"):
        print("FLAG NOT PROPOSED FOR CANARY (a deciding sub-cell is not positive ex best day)")
    if b.get("passes_only_at_rent_0"):
        print("FLAG passes only at rent 0: this book has FAILED its read (DEC-031 Am.2 item 10)")
    if b.get("status") == "NOT_DECIDABLE" and b.get("final"):
        print("FLAG ENDED NOT_DECIDABLE at the end-seal (DEC-031 Am.11 item 2): final, counts only")
for k, why in sorted(rep.get("refused", {}).items()):
    print("SIDE %s %s" % (k, "REFUSED_NO_RECORDED_INSTANT" if "no recorded read instant" in why else "REFUSED_OTHER"))
m = rep.get("multiplicity") or {}
print("MULTIPLICITY N=%s c=%s books_read_with_verdicts=%d" % (m.get("N"), m.get("c"), len(m.get("books_read_with_verdicts") or {})))
if bot in rep.get("refused", {}):
    print("QREFUSED " + rep["refused"][bot])' "$O/q50-read.json" "$BOT" 2>/dev/null)
ST=$(printf '%s\n' "$RES" | sed -n 's/^STATUS //p' | head -n 1)
OUTST=$(sed -n "s/^BOT $BOT  STATUS \\(.*\\)  exit rule .*\$/\\1/p" "$O/q50-read.out" | head -n 1 | sed 's/ (screen; not a promotion)$//')
if [ -z "$ST" ] || [ "$ST" = none ]; then
  QR=$(printf '%s\n' "$RES" | sed -n 's/^QREFUSED //p' | head -n 1)
  if [ -n "$QR" ]; then echo "REFUSED by the tool for $BOT (digits masked): $(printf '%s\n' "$QR" | mask)"
  else
    L1=$(grep -m1 -F "$BOT" "$O/q50-read.err"); [ -n "$L1" ] || L1=$(head -n 1 "$O/q50-read.err")
    echo "REFUSED by the tool (digits masked): $(printf '%s\n' "$L1" | mask)"
  fi
  outputs_done
  echo "NO READ RECORDED for $BOT: nothing printed a verdict; a re-run is a manager decision after QP reads the first line of q50-read.err"
  [ -n "$OUTST" ] && exit 3
  exit 2
fi
echo "VERDICT bot=$BOT STATUS $ST"
printf '%s\n' "$RES" | grep -E '^(FLAG|SIDE|MULTIPLICITY) '
[ "$OUTST" = "$ST" ] && echo "CHECK the stdout STATUS line equals the JSON status" || echo "CHECK MISMATCH: the stdout STATUS line differs from the JSON status (a manager item)"
echo "DISCLOSURE (QP-RULING-1029-q50-gate-unit ruling (iii), verbatim): \"$DISCLOSURE\""
RC=0
if [ "$ST" = PASS ]; then
  postread; RC=$?
else
  echo "LEGS and INBAND not run: the read STATUS is $ST, so there is no canary path (DEC-032 section 1 item 1)"
fi
outputs_done
echo "JOB done $(date -u +%FT%TZ)"
exit "$RC"
