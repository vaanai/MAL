#!/bin/sh
# Probe for scripts/ops/q50-canary-record-check.sh on a throwaway repo. Local only.
set -u
SRC=$(git rev-parse --show-toplevel) || exit 2
T=$(mktemp -d "${TMPDIR:-/tmp}/q50crc.XXXXXX") || exit 2
trap 'rm -rf "$T"' EXIT INT TERM
git init -q "$T/r" && cd "$T/r" && git config user.email t@example.invalid && git config user.name t && mkdir DEC || exit 2
FAIL=0
BOT=h5v1q50-boostfloor-a1
mk() { git add -A && git commit -q -m "$1" --allow-empty && git rev-parse HEAD; }
run() { out=$(sh "$SRC/scripts/ops/q50-canary-record-check.sh" "$2" 2>&1); rc=$?
  if [ "$rc" = "$3" ]; then echo "PASS $1 rc=$rc"; else echo "FAIL $1 rc=$rc expected $3"; echo "$out"; FAIL=1; fi; }
# other book's record (allowed), plus a template that must not count
printf 'OWNER_CANARY_CONFIRMED: bot_id=other-book\n' > DEC/DEC-024-x-canary.md
printf 'OWNER_CANARY_CONFIRMED: bot_id=%s\n<FILL:x>\n' "$BOT" > DEC/DEC-032-arena-canary-template.md
X0=$(mk none); run "no record for the book" "$X0" 3
printf 'x\nOWNER_CANARY_CONFIRMED: bot_id=%s\nbody `FILL:owner_line` is a field name\n| `FILL:days` | n |\n' "$BOT" > DEC/DEC-033-q-canary.md
X1=$(mk clean); run "clean record (field names only)" "$X1" 0
printf 'OWNER_CANARY_CONFIRMED: bot_id=%s\n| a | FILL:gate-read |\n' "$BOT" > DEC/DEC-033-q-canary.md
X2=$(mk fillcolon); run "FILL:gate-read open" "$X2" 3
printf 'OWNER_CANARY_CONFIRMED: bot_id=%s\nx FILL:review.\n' "$BOT" > DEC/DEC-033-q-canary.md; X3=$(mk r); run "FILL:review open" "$X3" 3
printf 'OWNER_CANARY_CONFIRMED: bot_id=%s\nx FILL:owner\n' "$BOT" > DEC/DEC-033-q-canary.md; X4=$(mk o); run "FILL:owner open" "$X4" 3
printf 'OWNER_CANARY_CONFIRMED: bot_id=%s\n<FILL x>\n' "$BOT" > DEC/DEC-033-q-canary.md; X5=$(mk a); run "old <FILL form open" "$X5" 3
printf -- '- OWNER_CANARY_CONFIRMED: bot_id=%s\n' "$BOT" > DEC/DEC-033-q-canary.md; X6=$(mk b); run "bulleted line is not bare" "$X6" 3
printf 'OWNER_CANARY_CONFIRMED: bot_id=%s\n' "$BOT" > DEC/DEC-033-q-canary.md; cp DEC/DEC-033-q-canary.md DEC/DEC-034-dup-canary.md
X7=$(mk dup); run "two records for one book" "$X7" 3
rm DEC/DEC-034-dup-canary.md; chmod +x DEC/DEC-033-q-canary.md; X8=$(mk mode); run "mode 100755" "$X8" 3
run "bad ref" deadbeef 2
exit $FAIL
