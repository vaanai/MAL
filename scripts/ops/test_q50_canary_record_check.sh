#!/bin/sh
# Probe for scripts/ops/q50-canary-record-check.sh. Clones the current repo (so the executor and blob 1dd08c83 exist), then commits variants of DEC-033. Local only; needs the PYTHON of the check.
# usage: sh scripts/ops/test_q50_canary_record_check.sh
set -u
SRC=$(git rev-parse --show-toplevel) || exit 2
T=$(mktemp -d "${TMPDIR:-/tmp}/q50crt.XXXXXX") || exit 2
trap 'rm -rf "$T"' EXIT INT TERM
PY=${PYTHON:-}; [ -n "$PY" ] || { [ -x /data/mal/venv/bin/python ] && PY=/data/mal/venv/bin/python || PY=python3; }
git clone -q --shared "$SRC" "$T/w" || exit 2
cd "$T/w" && git config user.email t@example.invalid && git config user.name t && git checkout -q -b t "$(git -C "$SRC" rev-parse HEAD)" || exit 2
F=DEC/DEC-033-h5v1q50-canary.md; BOT=h5v1q50-boostfloor-a1; FAIL=0
commit() { git add -A && git commit -q -m "$1" --allow-empty && git rev-parse HEAD; }
run() { # name, expected rc, ref, extra
  out=$(PYTHON=$PY sh "$SRC/scripts/ops/q50-canary-record-check.sh" "$3" ${4:-} 2>&1); rc=$?
  if [ "$rc" = "$2" ]; then echo "PASS $1 rc=$rc"; else echo "FAIL $1 rc=$rc expected $2"; echo "$out" | tail -6; FAIL=1; fi; }
mod() { "$PY" - "$@" <<'PYEOF'
import re, sys
f, how = sys.argv[1], sys.argv[2]
s = open(f).read()
s = re.sub(r"<FILL|FILL:(?:gate-read|review|owner)(?![\w-])", "DONE", s)
if how in ("clean", "extra"): s += "\nOWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1\n"
if how == "extra": s += "\nstray POSTX:not_in_the_table\n"
if how == "noposx": s = re.sub(r"POSTX:[a-z0-9_]+", "DONE", s)
if how == "noposxclean": s = re.sub(r"POSTX:[a-z0-9_]+", "DONE", s) + "\nOWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1\n"
open(f, "w").write(s)
PYEOF
}
run "current DEC-033 (open markers, no owner line)" 3 HEAD
mod $F clean; C=$(commit clean)
run "filled copy with owner line and matching POSTX set" 0 "$C"
run "same, after R: POSTX tokens must be gone" 3 "$C" --after-r
git reset -q --hard HEAD~1; mod $F noposxclean; R=$(commit afterR)
run "after R, no POSTX" 0 "$R" --after-r
run "after R tree, stage-2 rule: empty declared table fails" 3 "$R"
git reset -q --hard HEAD~1; mod $F extra; E=$(commit extra)
run "POSTX token outside the table" 3 "$E"
git reset -q --hard HEAD~1; mod $F clean; sed 's/h5v1q50-boostfloor-a1/other-book/' $F > DEC/DEC-024-x-canary.md; O=$(commit otherbook)
run "other book's canary record present is allowed" 0 "$O"
cp $F DEC/DEC-034-dup-canary.md; D=$(commit dup)
run "second record naming the same book" 3 "$D"
git rm -q DEC/DEC-034-dup-canary.md; git commit -q -m rm; chmod +x $F; M=$(commit mode)
run "mode 100755" 3 "$M"
run "bad ref" 2 deadbeef
exit $FAIL
