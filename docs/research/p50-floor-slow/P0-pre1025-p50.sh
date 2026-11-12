# P50 (FLOOR-SLOW registration staging). Informational P0 count of the frozen FLOOR v2 rule on arena tape days BEFORE 2026-10-25T00Z. COUNTS ONLY. POSIX sh.
# NOT the binding P0: QP-RULING-1025-seal-lift (3) f4 requires a fresh pinned P0 on days after the revival amendment's merge, at the book's new sha S (P35 B-P2).
# Pre-declared before the run (2026-10-26, manager17 lane P50):
#   days  = 2026-10-20,2026-10-21,2026-10-22,2026-10-23,2026-10-24 (the 5 full closed UTC days before 10-25; 10-19 starts at 08:05Z and is excluded)
#   rate  = mean att_lo over those days (the tool's rate_att_lo; all 5 must be OK, else the rate is reported as NOT_DECIDABLE)
#   H     = min(90, max(14, ceil(150 / rate)))  (DEC-031 Am.4 item 2.2)
#   kill  = rate < 4.0 a day kills lane P50 (P32 AUDIT lane 1 kill rule (a))
#   also printed (comparison only): mean att_lo over 10-22..10-24 (A2 #1531 printed rate_att_lo = 6.0 on those days)
# Tape hours read: table from the first tape hour (2026-10-19T08) to 2026-10-24T23 inclusive. No hour >= 2026-10-25T00Z is opened (the tool stops at end of max(days)).
# Tool: tools/floor_p0_count.py at 9a403a39 (sha256 e6186ac7...), repo HEAD must be 5e12f8f5 (the tool's PINNED_SHA), clean.
set -u; date -u; hostname
F=5e12f8f597571d6d5191a7ba38125c67ad723b7d
TOOL_COMMIT=9a403a39b6450b5fcd60c5816867ce2ec75ed403
TOOL_SHA=e6186ac737d7ca7fcf6be7d369ca25f96b0744ceb18eca3648099546204705b9
DAYS=2026-10-20,2026-10-21,2026-10-22,2026-10-23,2026-10-24
OUTD=/data/mal/hunt-1008/push-1016/hunt30/p50/p0-pre1025
SCR=/data2/mal/scratch/p50-floor-p0
PY=/data/mal/venv/bin/python
H=$(git rev-parse HEAD); echo "head=$H"; [ "$H" = "$F" ] || { echo "head check FAILED"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "REFUSED: dirty checkout"; exit 1; }
[ "$(hostname)" = mal-research-0 ] || { echo "REFUSED host"; exit 1; }
[ ! -e "$OUTD/p0-count.out" ] || { echo "REFUSED: this count already ran (no re-count)"; exit 1; }
mkdir -p "$OUTD" "$SCR" || exit 1
rm -f "$SCR/floor_p0_count.py"
for R in "$(pwd)" /home/claude/.miscusi/repos/vaanai/MAL; do
  git -C "$R" show "$TOOL_COMMIT:tools/floor_p0_count.py" > "$SCR/floor_p0_count.py" 2>/dev/null && [ -s "$SCR/floor_p0_count.py" ] && break
done
GOT=$(sha256sum "$SCR/floor_p0_count.py" | cut -c1-64); echo "tool sha256=$GOT"; [ "$GOT" = "$TOOL_SHA" ] || { echo "REFUSED: tool sha256 differs from the pin"; exit 1; }
free -m | head -2
START=$(date +%s)
nice -n 19 ionice -c3 "$PY" -I "$SCR/floor_p0_count.py" --repo "$(pwd)" --tape-dir /data/mal/arena/tape --days "$DAYS" --out "$OUTD/p0-count.out" > /dev/null 2> "$OUTD/p0-count.stderr"
RC=$?
echo "tool_rc=$RC wall_s=$(( $(date +%s) - START )) (0 ok, 1 a day failed, 2 tool error)"
tail -n 1 "$OUTD/p0-count.stderr" | cut -c1-200
[ -s "$OUTD/p0-count.out" ] || { echo "STOP: no output file"; exit 2; }
echo "output_file_sha256=$(sha256sum "$OUTD/p0-count.out" | cut -c1-64)"
"$PY" -I - "$OUTD/p0-count.out" "$F" "$TOOL_SHA" "$DAYS" > "$OUTD/p50-summary.txt" <<'PYEOF'
import json, math, os, sys
path, F, TOOL, DAYS = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(",")
L = open(path).read().rstrip("\n").split("\n")
rows = [json.loads(x) for x in L[:-1]]; hdr = rows[0]; bad = []
if hdr.get("repo_sha") != F: bad.append("header repo_sha")
if hdr.get("tool_sha256") != TOOL: bad.append("header tool_sha256")
if hdr.get("windows") != DAYS: bad.append("header windows")
print("HEADER table_from=%s lock_warmup_from=%s rule_sha256=%s table_sha256=%s" % (hdr.get("table_from"), hdr.get("lock_warmup_from"), hdr.get("rule_sha256"), hdr.get("table_sha256")))
day = {r["day"]: r for r in rows if r.get("kind") == "day"}
for d in DAYS:
    r = day.get(d)
    if r is None: bad.append("missing day %s" % d); continue
    print("DAY %s status=%s reason=%s signals=%s carried_lock_signals=%s att_lo=%s att_hi=%s" % (d, r["status"], r["reason"], r["signals"], r["carried_lock_signals"], r["att_lo"], r["att_hi"]))
    if r["status"] != "OK": bad.append("day %s %s" % (d, r["status"]))
v = next((r for r in rows if r.get("kind") == "verdict"), {})
rate = v.get("rate_att_lo"); hi = v.get("avg_att_hi")
last3 = [day[d]["att_lo"] for d in ("2026-10-22", "2026-10-23", "2026-10-24") if d in day]
H = (lambda r: min(90, max(14, math.ceil(150 / r))) if r and r > 0 else None)
ok = not bad
print("RATE (pre-declared: mean att_lo over 10-20..10-24, all 5 OK): %s" % (repr(rate) if ok else "NOT_DECIDABLE %s" % bad))
print("H = min(90, max(14, ceil(150/rate))) = %s ; ceil(150/rate) = %s" % (H(rate) if ok else None, (math.ceil(150 / rate) if ok and rate else None)))
print("avg_att_hi (report only) = %r ; H at att_hi = %s" % (hi, H(hi)))
print("comparison only: mean att_lo 10-22..10-24 = %r (A2 #1531 printed 6.0)" % (sum(last3) / len(last3) if len(last3) == 3 else None))
print("KILL (rate < 4.0/day, P32 lane 1 (a)): %s" % ("NOT_DECIDABLE" if not ok else ("KILL" if rate < 4.0 else "no kill")))
print("DISCLOSURE:", v.get("disclosure"))
res = os.environ.get("MISCUSI_RESULT")
if res:
    json.dump({"ok": ok, "metrics": {"rate_att_lo": rate, "avg_att_hi": hi, "H": H(rate) if ok else None, "att_lo_per_day": v.get("att_lo_per_day"), "att_hi_per_day": v.get("att_hi_per_day")},
               "summary": "P50 informational FLOOR P0 10-20..10-24: rate_att_lo=%r H=%s (counts only, not the binding P0)" % (rate, H(rate) if ok else None)}, open(res, "w"))
sys.exit(0 if ok else 4)
PYEOF
PRC=$?
cat "$OUTD/p50-summary.txt"; echo "summary_rc=$PRC tool_rc=$RC at $(date -u +%FT%TZ)"
chmod -R a-w "$OUTD"
[ "$RC" -le 1 ] && exit "$PRC"; exit "$RC"
