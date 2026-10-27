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
OPENRE = r"<FILL|FILL:(?:gate-read|review|owner)(?![\w-])"
if how == "prose":  # the DEC's own prose must not trip the check: no hit in the Changes section or the fee / seal_status / alert_target rows
    lines = s.split("\n"); bad = []; sect = False
    for n, l in enumerate(lines, 1):
        if l.startswith("## Changes from the template"): sect = True
        elif l.startswith("## ") and sect: sect = False
        if (sect or re.match(r"^\| `FILL:(fee|seal_status|alert_target)` ", l)) and re.search(OPENRE, l): bad.append(n)
    if bad: print("prose lines trip the marker regex:", bad); sys.exit(1)
    sys.exit(0)
s = re.sub(r"<FILL|FILL:(?:gate-read|review|owner)(?![\w-])", "DONE", s)
if how in ("clean", "extra"): s += "\nOWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1\n"
if how == "extra": s += "\nstray POSTX:not_in_the_table\n"
def drop_postx(t): return "\n".join(l if re.match(r"^\s*\| `POSTX:", l) else re.sub(r"POSTX:[a-z0-9_]+", "DONE", l) for l in t.split("\n"))
if how == "noposxclean": s = drop_postx(s) + "\nOWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1\n"
if how == "notable": s = re.sub(r"POSTX:[a-z0-9_]+", "DONE", s) + "\nOWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1\n"
open(f, "w").write(s)
PYEOF
}
if mod $F prose; then echo "PASS prose (Changes section, fee, seal_status, alert_target rows) has no marker-regex hit"; else echo "FAIL prose trips the marker regex"; FAIL=1; fi
run "current DEC-033 (open markers, no owner line)" 3 HEAD
mod $F clean; C=$(commit clean)
run "filled copy with owner line and matching POSTX set" 0 "$C"
run "same, after R: POSTX tokens outside the table must be gone" 3 "$C" --after-r
out=$(PYTHON=$PY Q50_CONTROL_BLOB=0000000000000000000000000000000000000000 sh "$SRC/scripts/ops/q50-canary-record-check.sh" "$C" 2>&1); rc=$?
if [ "$rc" = 3 ]; then echo "PASS missing positive-control blob fails (rc 3)"; else echo "FAIL missing control rc=$rc"; echo "$out"|tail -3; FAIL=1; fi
git reset -q --hard HEAD~1; mod $F noposxclean; R=$(commit afterR)
run "after R, POSTX only in the declared table" 0 "$R" --after-r
run "after R tree under the stage-2 rule: set equals table, passes" 0 "$R"
git reset -q --hard HEAD~1; mod $F notable; N=$(commit notable)
run "after R, table removed too" 0 "$N" --after-r
run "table removed, stage-2 rule (empty declared table)" 3 "$N"
git reset -q --hard HEAD~1; mod $F noposxclean; R=$(commit afterR)
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
