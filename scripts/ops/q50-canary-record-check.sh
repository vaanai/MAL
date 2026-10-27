#!/bin/sh
# Q50 canary record check at X (replaces checklist s8.3a's "grep <FILL"; QP-PRERULING-1027-xorder Q3). Records-only tool; does NOT edit tools/h5_arena_executor.py.
# usage: sh scripts/ops/q50-canary-record-check.sh <ref> [--after-r] [bot_id]      run in a git checkout that holds <ref>
#   PYTHON=<python with the executor's deps (solders ...)>; default /data/mal/venv/bin/python if present, else python3.
# Checks, on the tree at <ref> (exported with git archive; nothing in the worktree is read):
#   (a) canary_record_refusal(bot_id, tree) from the tree's OWN tools/h5_arena_executor.py returns None;
#   (b) among non-template, regular, mode-100644 files matching the executor's CANARY_DEC_GLOB, exactly one has a CANARY_OWNER_RE match for bot_id, and it is DEC/DEC-033-h5v1q50-canary.md
#       (DEC-024/026/028 match the glob too and are allowed);
#   (c) in that file: no open marker (<FILL, or FILL:gate-read|review|owner not followed by a word char or '-'); the set of POSTX:<name> tokens equals the declared table
#       (table rows start with "   | `POSTX:" ); with --after-r (records PR R merged) there must be NO POSTX token outside the declared table rows (the table may stay);
#   (d) positive control, MANDATORY: the same checks must FAIL on blob 1dd08c837660c778c5c26315f1b607faa8ce2f49 (the unfilled copy); a missing or non-blob object (shallow clone) is a FAIL, never a skip;
#   (e) prints the path and the blob sha256.
# rc 0 = all pass; 3 = a check failed; 2 = usage/git/import error.
set -u
[ "$#" -ge 1 ] || { echo "usage: $0 <ref> [--after-r] [bot_id]" >&2; exit 2; }
PY=${PYTHON:-}; [ -n "$PY" ] || { [ -x /data/mal/venv/bin/python ] && PY=/data/mal/venv/bin/python || PY=python3; }
exec "$PY" - "$@" <<'PYEOF'
import hashlib, io, re, subprocess, sys, tarfile, tempfile
from pathlib import Path
args = sys.argv[1:]
ref = args.pop(0)
after_r = "--after-r" in args
args = [a for a in args if a != "--after-r"]
bot = args[0] if args else "h5v1q50-boostfloor-a1"
DEC033 = "DEC/DEC-033-h5v1q50-canary.md"
import os
CONTROL_BLOB = os.environ.get("Q50_CONTROL_BLOB", "1dd08c837660c778c5c26315f1b607faa8ce2f49")  # full id; the env override exists for the test only
OPEN = re.compile(r"<FILL|FILL:(?:gate-read|review|owner)(?![\w-])")
POSTX = re.compile(r"POSTX:[a-z0-9_]+")
ROW = re.compile(r"^\s*\| `POSTX:")
def git(*a, raw=False):
    r = subprocess.run(["git", *a], capture_output=True)
    if r.returncode: raise RuntimeError(r.stderr.decode().strip())
    return r.stdout if raw else r.stdout.decode()
def markers(text):
    return [i + 1 for i, l in enumerate(text.split("\n")) if OPEN.search(l)]
def postx_checks(text):
    used = set(POSTX.findall(text))
    declared = set()
    for l in text.split("\n"):
        if ROW.match(l): declared |= set(POSTX.findall(l.split("|")[1]))
    return used, declared
def md(text, label):
    """checks (c) on a text; returns list of failure strings"""
    f = []
    m = markers(text)
    if m: f.append(f"{label}: {len(m)} open FILL marker lines (first: {m[:8]})")
    used, declared = postx_checks(text)
    if after_r:
        body = "\n".join(l for l in text.split("\n") if not ROW.match(l))
        left = set(POSTX.findall(body))
        if left: f.append(f"{label}: POSTX tokens remain after R outside the declared table: {sorted(left)}")
    elif used != declared or not declared:
        f.append(f"{label}: POSTX set != declared table; only in text {sorted(used - declared)}, only in table {sorted(declared - used)}")
    return f
try:
    git("rev-parse", "--verify", "-q", ref + "^{commit}")
    tar = git("archive", ref, "tools", "observe", "DEC", raw=True)
except RuntimeError as e:
    print("q50-canary-record-check: git error:", e, file=sys.stderr); sys.exit(2)
fails = []
with tempfile.TemporaryDirectory(prefix="q50crc.") as td:
    tarfile.open(fileobj=io.BytesIO(tar)).extractall(td)
    sys.path.insert(0, td)
    try:
        from tools.h5_arena_executor import canary_record_refusal, CANARY_DEC_GLOB, CANARY_OWNER_RE
    except Exception as e:
        print("q50-canary-record-check: cannot import the executor of", ref, ":", repr(e), file=sys.stderr); sys.exit(2)
    # (a)
    r = canary_record_refusal(bot, Path(td))
    if r is not None: fails.append(f"(a) canary_record_refusal = {r}")
    # (b)
    modes = {}
    for line in git("ls-tree", "-r", ref, "--", "DEC").split("\n"):
        if line: meta, name = line.split("\t", 1); modes[name] = meta.split()[0]
    hits = []
    for f in sorted(Path(td, "DEC").glob(CANARY_DEC_GLOB)):
        rel = "DEC/" + f.name
        if "template" in f.name or f.is_symlink() or not f.is_file() or modes.get(rel) != "100644": continue
        if bot in CANARY_OWNER_RE.findall(f.read_text()): hits.append(rel)
    if hits != [DEC033]: fails.append(f"(b) files whose owner line names {bot}: {hits}, need exactly [{DEC033}]")
    text = Path(td, DEC033).read_text() if Path(td, DEC033).exists() else ""
    # (c)
    if text: fails += md(text, "(c)")
    else: fails.append("(c) DEC-033 missing")
    # (e)
    print("path:", DEC033); print("blob sha256:", hashlib.sha256(text.encode()).hexdigest())
# (d) positive control (mandatory)
try:
    if git("cat-file", "-t", CONTROL_BLOB).strip() != "blob": raise RuntimeError("not a blob")
    ctl = git("cat-file", "-p", CONTROL_BLOB)
    cf = md(ctl, "control")
    if not markers(ctl) or not cf: fails.append("(d) positive control did NOT fail on blob " + CONTROL_BLOB)
    else: print("(d) positive control fails on", CONTROL_BLOB, "as required:", cf[0][:90])
except RuntimeError as e:
    fails.append("(d) positive control blob " + CONTROL_BLOB + " missing or not a blob (" + str(e)[:80] + "): the control is mandatory")
for x in fails: print("FAIL", x)
print("q50-canary-record-check:", "FAIL" if fails else "PASS")
sys.exit(3 if fails else 0)
PYEOF
