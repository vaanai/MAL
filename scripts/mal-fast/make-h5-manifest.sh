#!/usr/bin/env bash
# Print the install manifest for install-h5-executor-pinned.sh at a commit: "<sha256>  <repo path>" for every path the installer
# reads. Run it in the MANAGER'S OWN clone (a different machine from the one Helm installs on is better still), so the manifest is
# independent of the clone the root installer runs from. It reads blobs with `git show <sha>:<path>` and takes the path lists from
# the installer AS COMMITTED at that sha (nothing from the working tree).
#   scripts/mal-fast/make-h5-manifest.sh <full-40-char-sha> > h5-manifest.txt
set -euo pipefail
COMMIT="${1:?usage: $0 <full-sha>}"
case "$COMMIT" in *[!0-9a-f]*|"") echo "commit must be a lowercase hex sha" >&2; exit 1 ;; esac
[ "${#COMMIT}" -eq 40 ] || { echo "commit must be the full 40-char sha" >&2; exit 1; }
REPO="$(git rev-parse --show-toplevel)"
INST=scripts/mal-fast/install-h5-executor-pinned.sh
G=(git -c core.attributesFile=/dev/null -C "$REPO")
SRC="$("${G[@]}" show "$COMMIT:$INST")"
var() { printf '%s\n' "$SRC" | sed -n "s/^$1=\"\\(.*\\)\"\$/\\1/p"; }
MODULES="$(var MODULES)"
EXTRA="$(var EXTRA)"
BASE_UNIT_SRC="$(var BASE_UNIT_SRC)"
BASE_UNIT_CHECK="$(var BASE_UNIT_CHECK)"
[ -n "$MODULES" ] && [ -n "$EXTRA" ] && [ -n "$BASE_UNIT_SRC" ] && [ -n "$BASE_UNIT_CHECK" ] || { echo "cannot read the path lists from $INST at $COMMIT" >&2; exit 1; }
PATHS="$MODULES"
for e in $EXTRA; do PATHS="$PATHS ${e%%:*}"; done
PATHS="$PATHS $BASE_UNIT_SRC $BASE_UNIT_CHECK"
for f in $PATHS; do
  printf '%s  %s\n' "$("${G[@]}" show "$COMMIT:$f" | sha256sum | cut -d' ' -f1)" "$f"
done
