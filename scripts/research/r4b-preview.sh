#!/bin/sh
# r4b-preview.sh: the daily r4-b preview job (DEC-031 Amendment 31, Errata 1 and 2). Paper only. Run by the manager as a MiScusi job on mal-research-0, from a clean
# detached tree at the manifest's pin_commit. Agents never run it.
#   usage: sh scripts/research/r4b-preview.sh <manifest> <manifest_sha256> <as_of ISO on the hour, e.g. 2026-11-17T00:00:00Z>
# Schedule (the runbook states it): one run a day at about 03:20Z with as_of = that day 00:00Z. as_of must be at least about 3 h before now: the tool refuses until the
# tape hour of as_of + 2 h is closed. The tool refuses unless the checkout is clean at the manifest's pin_commit, and reads only the declog copy, the price keeper and the
# manifest; it writes outputs only under the out dir and its temporary ledger only under the work dir.
set -eu
if [ "$#" -ne 3 ]; then
    echo "usage: r4b-preview.sh <manifest> <manifest_sha256> <as_of>" >&2
    exit 2
fi
cd "$(dirname "$0")/../.."
exec systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 nice -n 19 /data/mal/venv/bin/python -I tools/r4b_preview.py run --manifest "$1" --manifest-sha256 "$2" --as-of "$3"
