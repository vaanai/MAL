#!/usr/bin/env bash
# Permission + symlink check for a pinned probe-executor tree or venv (DEC-019). Used by
# install-probe-executor-pinned.sh on the STAGED tree/venv before the irreversible moves and again on the
# final locations. Usage: check-probe-exec-tree.sh <dir> [<dir> ...]; exit 0 only if every dir passes.
#  1. every entry (symlinks included, find -P: the link itself) is owned by root;
#  2. no non-symlink entry is group/world-writable (a symlink's lstat mode is always 0777, so it is skipped);
#  3. every symlink resolves (readlink -f) to an existing path inside its checked dir, or to the system
#     python (/usr/bin/python3*, /usr/lib/python3*). Anything else is refused.
# Fails closed: if find itself fails (mid-walk error) the dir is refused.
# Test mode: MAL_TREE_CHECK_TEST_UID=<uid> replaces the expected owner uid (0). Refused when running as root.
set -euo pipefail
OWNER=0
if [ -n "${MAL_TREE_CHECK_TEST_UID:-}" ]; then
  [ "$(id -u)" -ne 0 ] || { echo "refusing: test mode is not allowed as root" >&2; exit 1; }
  OWNER="$MAL_TREE_CHECK_TEST_UID"
fi
[ "$#" -ge 1 ] || { echo "usage: $0 <dir>..." >&2; exit 2; }
rc=0
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
for dir in "$@"; do
  if [ ! -d "$dir" ] || [ -L "$dir" ]; then echo "refusing: $dir is not a real directory" >&2; rc=1; continue; fi
  real="$(readlink -f "$dir")"
  if ! find "$dir" \( \( ! -uid "$OWNER" \) -o \( ! -type l -perm /022 \) \) -print -quit > "$OUT"; then
    echo "refusing: find failed while walking $dir" >&2; rc=1; continue
  fi
  bad="$(cat "$OUT")"
  if [ -n "$bad" ]; then
    echo "refusing: $bad is not owned by uid $OWNER or is group/world-writable (under $dir)" >&2
    rc=1
  fi
  if ! find "$dir" -type l -print0 > "$OUT"; then
    echo "refusing: find failed while listing symlinks under $dir" >&2; rc=1; continue
  fi
  while IFS= read -r -d '' link; do
    target="$(readlink -f "$link" || true)"
    if [ -z "$target" ] || [ ! -e "$target" ]; then
      echo "refusing: symlink $link does not resolve" >&2; rc=1; continue
    fi
    case "$target" in
      "$real"/*|/usr/bin/python3*|/usr/lib/python3*) ;;
      *) echo "refusing: symlink $link -> $target points outside $dir" >&2; rc=1 ;;
    esac
  done < "$OUT"
done
exit "$rc"
