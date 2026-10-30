#!/bin/sh
# HV7 Am.6 item 1.3: the real-model smoke. POSIX sh. PAPER only. THIS CALLS THE ANTHROPIC API (spend cap 0.10 USD, claude-haiku-5-5) AND HELIUS
# (MAL_HV4_RPC=helius, <= 1,000 calls/h). DO NOT RUN until the manager has pinned the smoke in a notebook entry (blocking list, the tool and wrapper
# sha256, this PR head, instant, month-to-date API spend; pack section 3d).
#
# Usage: HV7_FINAL_SHA=<sha> sh scripts/ops/hv7-smoke.sh REPO ID
#   REPO  a CLEAN checkout at the final sha (HV7_FINAL_SHA, 40 hex, required in the environment)
#   ID    fresh name; the smoke dir is /data/mal/arena/haiku-v7/smoke/<ID> (refused if it exists)
# Runs 31 ranker periods at the runner's real instants (H:02 UTC, one per hour) after an 8 min warm-up plus one 14 min prefetch window (about 32-33 h wall). The API key is read at run time from /var/lib/mal/anthropic/anthropic.env
# into the child's environment only; it is never echoed, logged or passed on a command line.
# Writes ONLY under <HV7_ROOT>/smoke/<ID>/. The production root, the real registry (/data/mal/arena/bots.json) and every HV4/HV6/Q50 path are never touched.
# FEED_DELAY_SLOTS must equal the value the registered runner job will use (the pack pins it).
# Submit as a MiScusi job with memory >= 8 GB. One heavy job at a time on mal-research-0.
set -eu
FINAL=${HV7_FINAL_SHA:-}
case "$FINAL" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) : ;; *) echo '{"refused":"HV7_FINAL_SHA_required"}'; exit 2;; esac
[ "${#FINAL}" = 40 ] || { echo '{"refused":"HV7_FINAL_SHA_must_be_40_hex"}'; exit 2; }
case "$FINAL" in *[!0-9a-f]*) echo '{"refused":"HV7_FINAL_SHA_must_be_40_hex"}'; exit 2;; esac
KEYFILE=/var/lib/mal/anthropic/anthropic.env
if [ $# -ne 2 ]; then echo '{"hv7_smoke":"refused","why":"usage_REPO_ID"}'; exit 2; fi
REPO=$1; TAG=$2
case "$TAG" in *[!A-Za-z0-9._-]*|"") echo '{"hv7_smoke":"refused","why":"bad_id"}'; exit 2;; esac
case "$TAG" in *probe*) echo '{"hv7_smoke":"refused","why":"bad_id"}'; exit 2;; esac
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOL="$HERE/../../tools/hv7_smoke.py"
[ -f "$TOOL" ] || { echo '{"hv7_smoke":"refused","why":"tool_missing"}'; exit 2; }
[ -d "$REPO" ] || { echo '{"hv7_smoke":"refused","why":"repo_missing"}'; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
if [ "$HEAD_SHA" != "$FINAL" ]; then echo "{\"hv7_smoke\":\"refused\",\"why\":\"repo_not_at_final_sha\",\"head\":\"$HEAD_SHA\"}"; exit 2; fi
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then echo '{"hv7_smoke":"refused","why":"dirty_repo"}'; exit 2; fi
[ -r "$KEYFILE" ] || { echo '{"hv7_smoke":"refused","why":"key_file_not_readable"}'; exit 2; }
SMK=${HV7_ROOT:-/data/mal/arena/haiku-v7}/smoke/$TAG
mkdir -p "$(dirname "$SMK")"
echo "{\"hv7_smoke\":\"job\",\"sha\":\"$FINAL\",\"smk\":\"$SMK\",\"tool_sha256\":\"$(sha256sum "$TOOL" | cut -d' ' -f1)\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset MAL_HV4_CHAIN_STATE MAL_ARENA_TEST || true
MAL_HV4_RPC=helius
export MAL_HV4_RPC
set -a
. "$KEYFILE"
set +a
exec /data/mal/venv/bin/python -I "$TOOL" --repo "$REPO" --smk "$SMK" --cap-usd 0.10 --expect-sha "$FINAL" --periods 31 --warmup-min 8 --feed-delay-slots "${FEED_DELAY_SLOTS:-7}"
