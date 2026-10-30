#!/bin/sh
# hv4-c0-a3-carry.sh: copy the A3 guard's two files into the bridge's root-owned A3 dir (POSIX sh, no key, prints only names and PASS/FAIL).
#   A3_CARRY_SRC=/home/claude/data/structure-monitor   the A3 loop's output dir (scripts/ops/a3-daily.sh A3_OUT)
#   A3_CARRY_DST=/var/lib/mal-hv4-c0/a3                root:root 0755; files are written root:root 0644
# ORDER MATTERS (tools/hv4_live_c0.a3_problem: an applied line NEWER than the record is a3_guard_ahead, a HARD latch, manager-cleared; an applied line OLDER
# than the record is a3_guard_pending, a refusal that clears itself):
#   1. SNAPSHOT a3-applied.jsonl first, daily.jsonl second, into private temp files. The loop writes the record, then the guard's line, so a later record snapshot
#      can only be as new or newer than the applied snapshot, never older.
#   2. REFUSE a pair whose applied run_utc is later than the record's (nothing installed; the old copies stay).
#   3. INSTALL daily.jsonl (the record) FIRST, then a3-applied.jsonl, each with an atomic rename. Between the two the bridge can only see a new record with the
#      old applied line, i.e. "pending", never "ahead" (reviewer part B on #1097: the old order installed the applied log before the record).
# A file that does not end in a newline (a write in progress) is not carried; the old copy stays. The copy keeps the source mtime (the bridge's guard-lag bound
# counts from the record's mtime). An absent, empty, linked or torn source is a FAIL, never a silent skip, and the copies already there are left alone (so
# staleness still shows as a stale record, 36 h, not as a fresh copy).
SRC=${A3_CARRY_SRC:-/home/claude/data/structure-monitor}
DST=${A3_CARRY_DST:-/var/lib/mal-hv4-c0/a3}
rc=0
ends_nl() { [ "$(tail -c 1 -- "$1" | wc -l)" -eq 1 ]; }
last_run_utc() { grep -v '^[[:space:]]*$' -- "$1" | tail -n 1 | sed -n 's/.*"run_utc"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'; }
cleanup() { rm -f -- "$DST/.a3-applied.jsonl.carry.$$" "$DST/.daily.jsonl.carry.$$"; }
trap cleanup EXIT
snap() {  # $1 = name; copies $SRC/$1 to $DST/.$1.carry.$$ (two tries), or prints the FAIL line and returns 1
  s="$SRC/$1"
  t="$DST/.$1.carry.$$"
  if [ -L "$s" ] || [ ! -f "$s" ] || [ ! -s "$s" ]; then echo "FAIL a3_carry $1 source_missing_empty_or_link"; return 1; fi
  for try in 1 2; do
    if cp --preserve=timestamps -- "$s" "$t" 2>/dev/null && ends_nl "$t"; then chmod 0644 -- "$t" && return 0; fi
    sleep 2
  done
  echo "FAIL a3_carry $1 copy_failed_or_torn"
  return 1
}
snap a3-applied.jsonl || exit 1
snap daily.jsonl || exit 1
app=$(last_run_utc "$DST/.a3-applied.jsonl.carry.$$")
rec=$(last_run_utc "$DST/.daily.jsonl.carry.$$")
if [ -z "$app" ] || [ -z "$rec" ]; then echo "FAIL a3_carry pair run_utc_unreadable"; exit 1; fi
newest=$(printf '%s\n%s\n' "$app" "$rec" | LC_ALL=C sort | tail -n 1)
if [ "$app" != "$rec" ] && [ "$newest" = "$app" ]; then echo "FAIL a3_carry pair applied_ahead_of_record (nothing installed)"; exit 1; fi
for name in daily.jsonl a3-applied.jsonl; do  # the record first, then the applied log
  if mv -f -- "$DST/.$name.carry.$$" "$DST/$name"; then echo "PASS a3_carry $name"; else echo "FAIL a3_carry $name install_failed"; rc=1; break; fi
done
exit "$rc"
