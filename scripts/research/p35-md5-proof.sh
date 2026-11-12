#!/bin/sh
# p35-md5-proof.sh: the P35 md5 decision-equivalence replay proofs on the QP-pinned arena slice [2026-10-20T00Z, 2026-10-25T00Z) (POSIX sh).
# PAPER only. No key, no network. A MiScusi job on mal-research-0, run from the repo root at the PR's head (a clean checkout).
#   sh scripts/research/p35-md5-proof.sh ckpt <tag>   #957: checkpoint writing changes no md5; fresh-process restores at 10-22T10Z / 10-23T10Z
#   sh scripts/research/p35-md5-proof.sh host <tag>   #962: HostV3 = the registered HostV2; D=0 hand-offs = continuous; D=30 s = listed handoff_down only
# Scratch: /data2/mal/p35/bp1/<mode>-<tag>/ (new, mode 0700). The tool links ONLY the slice's hour files by name, prints md5s and counts, and deletes every
# ledger / decision log / table / checkpoint inside this job (it prints the DELETED line). Walk-2, in-sample hours for FLOOR: never OOS.
# MAL_ARENA_TEST=1 is set ONLY so the scratch registry may carry a past clock; the real bots.json is never opened.
set -u
MODE="${1:-}"; TAG="${2:-}"
case "$MODE" in ckpt|host) ;; *) echo "usage: $0 ckpt|host <tag>" >&2; exit 2;; esac
case "$TAG" in ""|*[!A-Za-z0-9._-]*) echo "p35-md5-proof: tag must be [A-Za-z0-9._-]+" >&2; exit 2;; esac
PY="${P35_PY:-/data/mal/venv/bin/python}"
[ -f tools/p35_md5_proof.py ] || { echo "p35-md5-proof: run from the repo root" >&2; exit 2; }
[ -z "$(git status --porcelain 2>/dev/null)" ] || { echo "p35-md5-proof: the checkout is not clean" >&2; exit 2; }
SCRATCH="/data2/mal/p35/bp1/$MODE-$TAG"
[ ! -e "$SCRATCH" ] || { echo "p35-md5-proof: $SCRATCH exists (a proof runs on a new directory)" >&2; exit 2; }
mkdir -p /data2/mal/p35/bp1 && chmod 700 /data2/mal/p35/bp1
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) p35-md5-proof mode=$MODE head=$(git rev-parse HEAD) scratch=$SCRATCH py=$PY"
MAL_ARENA_TEST=1 exec "$PY" -m tools.p35_md5_proof "$MODE" --scratch "$SCRATCH"
