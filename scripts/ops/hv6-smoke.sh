#!/bin/sh
# HV6 item 7b: the real-model smoke (Am.6 style). POSIX sh. PAPER only. THIS CALLS THE ANTHROPIC API (budget cap <= 0.50 USD, claude-haiku-5-5) AND HELIUS
# (MAL_HV4_RPC=helius, <= 1,000 calls/h). DO NOT RUN until the manager has pinned the smoke in a notebook entry (blocking list, counts script sha256
# = the sha256 of tools/hv6_smoke.py, instant, month-to-date API spend) and the P0 count (item 7a) is done at the same final sha.
#
# Usage: HV6_FINAL_SHA=<sha> sh scripts/ops/hv6-smoke.sh REPO TAG [HOURS]
#   REPO   a CLEAN checkout at the final sha (HV6_FINAL_SHA, 40 hex, required in the environment)
#   TAG    fresh name; the smoke dir is /data/mal/arena/haiku-v6/smoke/<TAG> (refused if it exists)
#   HOURS  default 2 (at most 4)
# The API key is read at run time from /var/lib/mal/anthropic/anthropic.env into the child's environment only; it is never echoed, logged or passed on a command line.
# Writes ONLY under <HV6_ROOT>/smoke/<TAG>/. The production root, the real registry (/data/mal/arena/bots.json) and every HV4/Q50 path are never touched.
# FEED_DELAY_SLOTS must equal the value the registered runner job will use (HV4's Am.2 value is 7; the registration pack pins it).
# Submit as a MiScusi job with memory >= 8 GB (MemoryMax); the runner peaked at 2.7 GB in the real-layout replay (#1640). One heavy job at a time on mal-research-0 (QP #940 s2).
set -eu
FINAL=${HV6_FINAL_SHA:-}   # the registered final sha (the playbook-fields merge commit), REQUIRED in the environment: no default
case "$FINAL" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) : ;; *) echo '{"refused":"HV6_FINAL_SHA_required"}'; exit 2;; esac
[ "${#FINAL}" = 40 ] || { echo '{"refused":"HV6_FINAL_SHA_must_be_40_hex"}'; exit 2; }
KEYFILE=/var/lib/mal/anthropic/anthropic.env
if [ $# -lt 2 ] || [ $# -gt 3 ]; then echo '{"hv6_smoke":"refused","why":"usage_REPO_TAG_[HOURS]"}'; exit 2; fi
REPO=$1; TAG=$2; HOURS=${3:-2}
case "$TAG" in *[!A-Za-z0-9._-]*|"") echo '{"hv6_smoke":"refused","why":"bad_tag"}'; exit 2;; esac
case "$TAG" in *probe*) echo '{"hv6_smoke":"refused","why":"bad_tag"}'; exit 2;; esac
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOL="$HERE/../../tools/hv6_smoke.py"
[ -f "$TOOL" ] || { echo '{"hv6_smoke":"refused","why":"tool_missing"}'; exit 2; }
[ -d "$REPO" ] || { echo '{"hv6_smoke":"refused","why":"repo_missing"}'; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
if [ "$HEAD_SHA" != "$FINAL" ]; then echo "{\"hv6_smoke\":\"refused\",\"why\":\"repo_not_at_final_sha\",\"head\":\"$HEAD_SHA\"}"; exit 2; fi
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then echo '{"hv6_smoke":"refused","why":"dirty_repo"}'; exit 2; fi
[ -r "$KEYFILE" ] || { echo '{"hv6_smoke":"refused","why":"key_file_not_readable"}'; exit 2; }
SMK=${HV6_ROOT:-/data/mal/arena/haiku-v6}/smoke/$TAG
mkdir -p "$(dirname "$SMK")"
echo "{\"hv6_smoke\":\"job\",\"sha\":\"$FINAL\",\"smk\":\"$SMK\",\"hours\":\"$HOURS\",\"tool_sha256\":\"$(sha256sum "$TOOL" | cut -d' ' -f1)\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset MAL_HV4_CHAIN_STATE MAL_ARENA_TEST || true
MAL_HV4_RPC=helius
export MAL_HV4_RPC
set -a
. "$KEYFILE"
set +a
exec /data/mal/venv/bin/python -I "$TOOL" --repo "$REPO" --smk "$SMK" --hours "$HOURS" --cap-usd 0.50 --expect-sha "$FINAL" --regime-dir "${HV6_ROOT:-/data/mal/arena/haiku-v6}/regime" --feed-delay-slots "${FEED_DELAY_SLOTS:-7}"
