# Shared by kill-review-1005-snapshot.sh and kill-review-1005-score.sh (sourced, not run).
# 2026-10-05T05:00:00Z forward-paper kill review (DEC-014). See docs/runbooks/kill-review-2026-10-05.md.
#
# Test hooks (KR_NOW_EPOCH, KR_ROOT, KR_*_CMD, KR_SRC_*, ...) are read ONLY when KR_TEST_MODE=1.
# In a real run they are ignored, so nothing can fake the clock or redirect the data root by accident.

KR_INSTANT_EPOCH=1791176400          # 2026-10-05T05:00:00Z
KR_INSTANT_ISO=2026-10-05T05:00:00Z
KR_WINDOW_START_MS=1790553600000     # 2026-09-28T00:00:00Z (clean clock)
KR_WINDOW_END_MS=1791176400000       # the review instant
KR_PRESSURE_FROM_MS=1790640000000    # 2026-09-29T00:00:00Z (DEC-014 Amendment 3)

if [ "${KR_TEST_MODE:-}" = "1" ]; then
  KR_NOW="${KR_NOW_EPOCH:-$(date -u +%s)}"
  KR_ROOT="${KR_ROOT:-/data/mal/kill-review-1005}"
else
  KR_NOW="$(date -u +%s)"
  KR_ROOT=/data/mal/kill-review-1005
  unset KR_CORE_CMD KR_RESEARCH_CMD KR_SRC_PAPER KR_SRC_TAPE KR_SRC_CREATES KR_SRC_RESTARTS \
        KR_MIN_FREE_GB KR_PY KR_TAPE_FIRST_HOUR KR_TAPE_LAST_HOUR KR_CREATES_FIRST_DAY KR_CREATES_LAST_DAY
fi
KR_SNAP="$KR_ROOT/snap"
KR_OUT="$KR_ROOT/out"

kr_die() { echo "kill-review-1005: $*" >&2; exit 1; }

# Refuse before the review instant. Runs before anything is read, created or copied.
kr_guard_time() {
  if [ "$KR_NOW" -lt "$KR_INSTANT_EPOCH" ]; then
    echo "kill-review-1005: refusing: now=$KR_NOW is before the review instant $KR_INSTANT_ISO ($KR_INSTANT_EPOCH). Nobody reads positions.jsonl before then (DEC-014 single read)." >&2
    exit 3
  fi
}

# Copy named outputs to $MISCUSI_OUTPUT_DIR when set. Missing files are reported, not fatal.
kr_publish() {
  [ -n "${MISCUSI_OUTPUT_DIR:-}" ] || return 0
  mkdir -p "$MISCUSI_OUTPUT_DIR"
  local f
  for f in "$@"; do
    if [ -f "$f" ]; then cp "$f" "$MISCUSI_OUTPUT_DIR/"; else echo "kill-review-1005: missing output $f" >&2; fi
  done
}
