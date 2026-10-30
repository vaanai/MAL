#!/bin/sh
# hv4-c0-a3-carry.sh: copy the A3 guard's two files into the bridge's root-owned A3 dir (POSIX sh, no key, prints only names and PASS/FAIL).
#   A3_CARRY_SRC=/home/claude/data/structure-monitor   the A3 loop's output dir (scripts/ops/a3-daily.sh A3_OUT)
#   A3_CARRY_DST=/var/lib/mal-hv4-c0/a3                root:root 0755; files are written root:root 0644
# ORDER MATTERS: a3-applied.jsonl is copied FIRST, daily.jsonl second. The loop writes the record, then the guard's line. If the applied copy were newer than the
# record copy the bridge would read "guard ahead" (tools/hv4_live_c0.a3_problem: a3_guard_ahead = a HARD latch, manager-cleared); the other order can only
# produce "guard pending" (a refusal that clears itself). A file that does not end in a newline (a write in progress) is not carried; the old copy stays.
# The copy keeps the source mtime (the bridge's guard-lag bound counts from the record's mtime). An absent, empty, linked or torn source is a FAIL, never a
# silent skip, and the copy that is already there is left alone (so staleness still shows as a stale record, 36 h, not as a fresh copy).
SRC=${A3_CARRY_SRC:-/home/claude/data/structure-monitor}
DST=${A3_CARRY_DST:-/var/lib/mal-hv4-c0/a3}
rc=0
ends_nl() { [ "$(tail -c 1 -- "$1" | wc -l)" -eq 1 ]; }
for name in a3-applied.jsonl daily.jsonl; do
  s="$SRC/$name"
  if [ -L "$s" ] || [ ! -f "$s" ] || [ ! -s "$s" ]; then echo "FAIL a3_carry $name source_missing_empty_or_link"; rc=1; continue; fi
  t="$DST/.$name.carry.$$"
  ok=0
  for try in 1 2; do
    if cp --preserve=timestamps -- "$s" "$t" 2>/dev/null && ends_nl "$t"; then ok=1; break; fi
    sleep 2
  done
  if [ "$ok" -ne 1 ]; then rm -f -- "$t"; echo "FAIL a3_carry $name copy_failed_or_torn"; rc=1; continue; fi
  if chmod 0644 -- "$t" && mv -f -- "$t" "$DST/$name"; then echo "PASS a3_carry $name"; else rm -f -- "$t"; echo "FAIL a3_carry $name install_failed"; rc=1; fi
done
exit "$rc"
