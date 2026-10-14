"""Step 13 (round 1, OFFLINE): copy the per-attempt public-RPC log of round 0 into out/ and summarise it.
The log holds one line per real network attempt: instant, script tag, method, http status, response bytes. No key, no URL, no payload.
out/s05_graduations.json records 6 attempts because it was written by a cache-served rerun; the 107 for step 5 are in this log."""
import json
import shutil
from collections import Counter

from common import CALL_LOG, OUT

shutil.copyfile(CALL_LOG, OUT / "rpc_calls.log")
rows = [l.split() for l in open(CALL_LOG).read().splitlines() if l.strip()]
s = dict(
    n_attempts=len(rows), first=rows[0][0], last=rows[-1][0],
    by_script=dict(Counter(r[1] for r in rows)), by_method=dict(Counter(r[2] for r in rows)), by_http=dict(Counter(r[3] for r in rows)),
    by_script_and_http={f"{k[0]} {k[1]}": v for k, v in Counter((r[1], r[3]) for r in rows).items()},
    bytes_total=sum(int(r[4].split("=")[1]) for r in rows),
    note="round 1 made no Solana RPC call: this log has the same attempts as at the end of round 0",
)
print(json.dumps(s, indent=1))
(OUT / "s13_rpc_log_summary.json").write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
