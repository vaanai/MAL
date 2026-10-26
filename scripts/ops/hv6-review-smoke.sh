#!/bin/sh
# HV6 reviewer smoke (QP #940 change 6): one REAL nightly-review call each for L, L-nr, P and one REAL regime-review call each for L, P, on a synthetic regime block
# written into the smoke root. POSIX sh. PAPER only. THIS CALLS THE ANTHROPIC API (about 0.01 USD in total; extra cap 0.10 USD above the day's spend so far).
# Run it AFTER the runner smoke (scripts/ops/hv6-smoke.sh) on the SAME tag. DO NOT RUN until the manager has pinned it in the smoke's notebook entry.
#
# Usage: HV6_FINAL_SHA=<sha> sh scripts/ops/hv6-review-smoke.sh REPO TAG
#   REPO  a CLEAN checkout at the final sha (HV6_FINAL_SHA, 40 hex, required in the environment)
#   TAG   the tag of the runner smoke: /data/mal/arena/haiku-v6/smoke/<TAG>/root must exist
# Writes only under <HV6_ROOT>/smoke/<TAG>/root/ (manifests, replies, validator files, a real regime/ dir with the synthetic block, review-smoke-report.json).
# The API key is read at run time from /var/lib/mal/anthropic/anthropic.env into the child's environment only; never echoed.
# Submit as a MiScusi job on mal-research-0, memory >= 4 GB, resumable false.
set -eu
FINAL=${HV6_FINAL_SHA:-}
case "$FINAL" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) : ;; *) echo '{"refused":"HV6_FINAL_SHA_required"}'; exit 2;; esac
[ "${#FINAL}" = 40 ] || { echo '{"refused":"HV6_FINAL_SHA_must_be_40_hex"}'; exit 2; }
KEYFILE=/var/lib/mal/anthropic/anthropic.env
if [ $# -ne 2 ]; then echo '{"hv6_review_smoke":"refused","why":"usage_REPO_TAG"}'; exit 2; fi
REPO=$1; TAG=$2
case "$TAG" in *[!A-Za-z0-9._-]*|"") echo '{"hv6_review_smoke":"refused","why":"bad_tag"}'; exit 2;; esac
case "$TAG" in *probe*) echo '{"hv6_review_smoke":"refused","why":"bad_tag"}'; exit 2;; esac
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOL="$HERE/../../tools/hv6_review_smoke.py"
[ -f "$TOOL" ] || { echo '{"hv6_review_smoke":"refused","why":"tool_missing"}'; exit 2; }
[ -d "$REPO" ] || { echo '{"hv6_review_smoke":"refused","why":"repo_missing"}'; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
if [ "$HEAD_SHA" != "$FINAL" ]; then echo "{\"hv6_review_smoke\":\"refused\",\"why\":\"repo_not_at_final_sha\",\"head\":\"$HEAD_SHA\"}"; exit 2; fi
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then echo '{"hv6_review_smoke":"refused","why":"dirty_repo"}'; exit 2; fi
[ -r "$KEYFILE" ] || { echo '{"hv6_review_smoke":"refused","why":"key_file_not_readable"}'; exit 2; }
SMK=${HV6_ROOT:-/data/mal/arena/haiku-v6}/smoke/$TAG
echo "{\"hv6_review_smoke\":\"job\",\"sha\":\"$FINAL\",\"smk\":\"$SMK\",\"tool_sha256\":\"$(sha256sum "$TOOL" | cut -d' ' -f1)\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset MAL_HV4_CHAIN_STATE MAL_ARENA_TEST || true
set -a
. "$KEYFILE"
set +a
exec /data/mal/venv/bin/python -I "$TOOL" --repo "$REPO" --smk "$SMK" --expect-sha "$FINAL"
