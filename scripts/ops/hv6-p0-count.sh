#!/bin/sh
# HV6 item 7a: the P0 count job (DESIGN section 10 S1-S7). POSIX sh. PAPER only: counts only, no model, no key, no RPC, no engine, no price.
# Reads /data/mal/arena/tape (whole closed UTC days in [2026-10-20T00Z, job start), never 10-19); writes only <HV6_ROOT>/p0/. Prints counts and a sha256 line.
# DO NOT RUN before the manager has (1) recorded the ledger check, (2) written the fixed-first notebook entry (QP ruling sha256, text, day list, command, job id)
# and saved its text to FIXED_FIRST_FILE, which must name every day.
#
# Usage: HV6_FINAL_SHA=<sha> sh scripts/ops/hv6-p0-count.sh FIXED_FIRST_FILE DAYS REPO
#   FIXED_FIRST_FILE  the notebook text, e.g. /data/mal/arena/haiku-v6/p0/fixed-first-<id>.txt (its sha256 goes into the report header)
#   DAYS              comma-separated, >= 3, e.g. 2026-10-22,2026-10-23,2026-10-24,2026-10-25 (prefer the latest 4-5; from 10-21 so the 6 h warm-up is in range)
#   REPO              a CLEAN checkout at the final sha (HV6_FINAL_SHA, 40 hex, required in the environment) (e.g. git worktree add --detach /data2/mal/wt/hv6-final <sha>)
# The tool file is taken from THIS checkout (the registration PR branch); the rule code (hv4_cards, hv6_cards, hv6_runner, arena_state) from REPO.
# Submit as a MiScusi job with memory >= 8 GB (MemoryMax), resumable false; one heavy job at a time on mal-research-0.
set -eu
FINAL=${HV6_FINAL_SHA:-}   # the registered final sha (the playbook-fields merge commit), REQUIRED in the environment: no default
case "$FINAL" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) : ;; *) echo '{"refused":"HV6_FINAL_SHA_required"}'; exit 2;; esac
[ "${#FINAL}" = 40 ] || { echo '{"refused":"HV6_FINAL_SHA_must_be_40_hex"}'; exit 2; }
if [ $# -ne 3 ]; then echo '{"hv6_p0":"refused","why":"usage_FIXED_FIRST_FILE_DAYS_REPO"}'; exit 2; fi
FF=$1; DAYS=$2; REPO=$3
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOL="$HERE/../../tools/hv6_p0_count.py"
[ -f "$TOOL" ] || { echo '{"hv6_p0":"refused","why":"tool_missing"}'; exit 2; }
[ -f "$FF" ] || { echo '{"hv6_p0":"refused","why":"fixed_first_file_missing"}'; exit 2; }
[ -d "$REPO" ] || { echo '{"hv6_p0":"refused","why":"repo_missing"}'; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
if [ "$HEAD_SHA" != "$FINAL" ]; then echo "{\"hv6_p0\":\"refused\",\"why\":\"repo_not_at_final_sha\",\"head\":\"$HEAD_SHA\"}"; exit 2; fi
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then echo '{"hv6_p0":"refused","why":"dirty_repo"}'; exit 2; fi
OUTDIR=${HV6_ROOT:-/data/mal/arena/haiku-v6}/p0
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$OUTDIR/p0-$(printf %s "$FINAL" | cut -c1-10)-$STAMP.jsonl"
mkdir -p "$OUTDIR"
echo "{\"hv6_p0\":\"job\",\"sha\":\"$FINAL\",\"days\":\"$DAYS\",\"fixed_first_sha256\":\"$(sha256sum "$FF" | cut -d' ' -f1)\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"out\":\"$OUT\"}"
unset ANTHROPIC_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC MAL_ARENA_TEST || true
exec /data/mal/venv/bin/python -I "$TOOL" --repo "$REPO" --days "$DAYS" --fixed-first "$FF" --out "$OUT" --expect-sha "$FINAL"
