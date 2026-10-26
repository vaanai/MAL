"""P55 step 1: does the exploration trade tape carry BoostBuyAndBurnEvent rows or boost_vault_remaining?

Streams clean-view exploration hours only (explore-0814, fresh-0903, exp011-0909). Counts only; no outcome.
Usage: python3 p55_coverage.py [step]   (step = scan every step-th hour file; 1 = all)
Counts rows (lines) and rows that mention boost / vault / burn / remaining, case-insensitive, via grep -c.
"""
import glob
import json
import subprocess
import sys

BLOCKS = ["explore-0814", "fresh-0903", "exp011-0909"]
step = int(sys.argv[1]) if len(sys.argv) > 1 else 1
files = []
for b in BLOCKS:
    files += sorted(glob.glob(f"/data/mal/clean-view/{b}/*/trades/trades-*.jsonl.zst"))
tot = {"files_total": len(files), "step": step, "files_scanned": 0, "rows": 0, "boost": 0, "vault": 0, "burn": 0, "remaining": 0}


def count(f, pat):
    cmd = f"zstd -dc {f} | grep -a -c -i {pat}" if pat else f"zstd -dc {f} | wc -l"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


for f in files[::step]:
    tot["files_scanned"] += 1
    tot["rows"] += count(f, None)
    for k in ("boost", "vault", "burn", "remaining"):
        tot[k] += count(f, k)
print(json.dumps(tot))
