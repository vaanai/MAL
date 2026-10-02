#!/usr/bin/env bash
# Sourced by the fast-host installers. Pins everything they install to one commit.
# Expects: ROOT (repo checkout), log(), die() defined by the caller.

# DEC-015 section 2.7 code floor: the sha must descend from this.
FLOOR_COMMIT="d7485d2"

# verify_commit <sha>: sets COMMIT to the full sha or dies.
#   - sha is a commit
#   - sha is an ancestor of (or equal to) origin/main
#   - sha descends from the floor commit
#   - the working tree has no tracked changes (nothing is read from it, but a dirty
#     tree means the operator is not looking at what is being installed)
verify_commit() {
  local want="$1" full
  [[ -n "${want}" ]] || die "--commit <sha> is required"
  full="$(git -C "${ROOT}" rev-parse --verify --quiet "${want}^{commit}")" || die "not a commit: ${want}"
  git -C "${ROOT}" rev-parse --verify --quiet origin/main >/dev/null || die "no origin/main ref; fetch first"
  git -C "${ROOT}" merge-base --is-ancestor "${full}" origin/main \
    || die "${full} is not an ancestor of origin/main"
  git -C "${ROOT}" merge-base --is-ancestor "${FLOOR_COMMIT}" "${full}" \
    || die "${full} is not a descendant of the code floor ${FLOOR_COMMIT}"
  if [[ -n "$(git -C "${ROOT}" status --porcelain --untracked-files=no)" ]]; then
    die "working tree is dirty; commit or discard first"
  fi
  COMMIT="${full}"
  log "commit ${COMMIT} ok (ancestor of origin/main, descends from ${FLOOR_COMMIT}, clean tree)"
}

# commit_file <path>: print the file as of ${COMMIT}.
commit_file() { git -C "${ROOT}" show "${COMMIT}:$1"; }
