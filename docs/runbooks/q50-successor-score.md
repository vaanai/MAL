# Q50 successor score (audit M3)

Problem: the daily score loop (#1425, now #1543) (`h5-q50-daily-score`) passes ONE dir, `$HOME/data/arena/h5-q50-2026-10-24`. If the shadow #1419 dies early, the successor #1421 writes to
`$HOME/data/arena/h5-q50-$(date -u +%F)-succ` (date = the successor's start day, so read the real name from #1421's first log line `out=...`), and #1425 never scores it.
Q50 dirs only: the wrapper refuses a dir whose start records are not Q50. Source: `Q50-EXEC-1024.md` STEP Q50-SCORE ("a `-succ` dir is added as a second `--shadow-dir` by the manager").

## When to submit

- #1419 is dead (job record not running) AND #1421 started, or `SHADOW_STATUS_AGE_S` in the #1425 log is large for two days running. Check `ls $HOME/data/arena | grep h5-q50` for the real succ dir name.
- Not before the succ dir exists. Do not cancel #1425 until the new job has produced its first `score_rc=0` (two scorers for one book only duplicate counts-only output; they write nothing to the book).
- Cancel with `miscusi_job_cancel`, then `ps` to check no scope child survived (lesson 10-17).

## Job spec (replaces #1425)

label `h5-q50-daily-score-2dir`; machine mal-fast-0; memory 1.5 GB; cpus 0.5; time limit 14 d (platform max; the loop ends itself at END); git_ref `659f33e85abf487001729829aef60478e7e86281`; resumable true; after: none.
Same body as #1425 with ONE edit: `SD2` is set to the real succ dir and the score line carries both `--shadow-dir` arguments (primary first). Replace `SUCC_NAME` before submitting.

```sh
set -u; date -u; git rev-parse HEAD
PIN=659f33e85abf487001729829aef60478e7e86281
[ "$(hostname)" = mal-fast-0 ] || { echo "REFUSED host"; exit 1; }
[ "$(git rev-parse HEAD)" = "$PIN" ] || { echo "REFUSED head"; exit 1; }
[ "$(sha256sum tools/arena_h5_score_q50.py | cut -d' ' -f1)" = 4ab3e909537b96fb6eb3089d3f156798869028c14125ba0cc8dfa318c6860a9c ] || { echo "REFUSED wrapper sha"; exit 1; }
[ "$(sha256sum tools/arena_h5_score.py | cut -d' ' -f1)" = dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006 ] || { echo "REFUSED scorer sha"; exit 1; }
sha256sum tools/arena_h5_score.py tools/arena_h5_score_q50.py
PY=/home/claude/venvs/c1nf-shadow/bin/python; SD="$HOME/data/arena/h5-q50-2026-10-24"; SD2="$HOME/data/arena/SUCC_NAME"
[ -d "$SD2" ] || { echo "REFUSED no succ dir $SD2"; exit 1; }
END=$(date -u -d 2026-11-06T01:00:00Z +%s)
while :; do now=$(date -u +%s); t=$(date -u -d "$(date -u +%F)T00:20:00Z" +%s); [ "$t" -le "$now" ] && t=$((t + 86400)); [ "$t" -gt "$END" ] && { echo "end reached"; exit 0; }; sleep $((t - now)); echo "== $(date -u +%FT%TZ) daily score"; echo "SHADOW_STATUS_AGE_S=$(( $(date -u +%s) - $(stat -c %Y "$SD/h5-shadow-status.json" 2>/dev/null || echo 0) ))"; echo "SUCC_STATUS_AGE_S=$(( $(date -u +%s) - $(stat -c %Y "$SD2/h5-shadow-status.json" 2>/dev/null || echo 0) ))"; $PY -m tools.arena_h5_score_q50 score --shadow-dir "$SD" --shadow-dir "$SD2"; echo "score_rc=$?"; done
```

Replaces the live loop #1543 when a succ dir exists; same command as #1543 except the second dir (head 659f33e8 = #884, DEC-031 Am.11 item 5: the loop runs at the item-1 tool commit; new wrapper sha, added scorer sha check). Conventions: pinned head and wrapper sha, `$PY` = c1nf-shadow venv, first scorer line prints both sha256, `date -u -d` loop to 00:20Z daily, END 2026-11-06T01:00Z, status files by mtime only (never content), `score_rc` echoed.
The repeated `--shadow-dir` is valid: `tools/arena_h5_score.py:992` declares it `action="append"` (usage line 54).

## Verify (counts only)

1. Right after submit, run once as a dry run in a one-shot job (same pin/sha lines) with `score --dry-run --shadow-dir "$SD" --shadow-dir "$SD2"; echo RC=$?`.
2. Expected: one JSON of counts; `books["h5v1q50-boostfloor-a1"]` has `registered: true`; `refused_dirs` empty (a dir listed with `started_before_registration`, `sha_mismatch`, `q_star_mismatch`, `rule_mismatch` or `rule_sha_mismatch` = the book is not counting: stop, call QP, do not fix by hand); `RC=0`.
3. Never open rows, ledgers, bots.json or reports. If the run is refused, do not sum by hand: report to QP.
4. Record the new job id and the succ dir name in the next records PR and a notebook entry.
