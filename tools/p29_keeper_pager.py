"""P29: page the pump.fun app keeper's public signature history (counts-only input). Public RPC, 0 Helius.

Usage: python3 p29_keeper_pager.py OUT.jsonl [STOP_BLOCKTIME] [MAX_CALLS]
Each output line: {"sig","slot","bt","err","kind","ver","memo_other"}; kind in SL/TP/OT (pump-limit create) or "" (no pump-limit memo).
Resumable: continues from the last line's sig. Calls getSignaturesForAddress only (no getTransaction).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

KEEPER = "64fcNUtAAockLpMJZ1DudHCMHJhLRBmgrmGvbrwmQzQw"
RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_STOP = 1788436800  # 2026-09-03T12:00:00Z
MEMO_RE = re.compile(r"pump-limit:v(\d+):([a-z_]+):")


def classify(memo):
    """-> (kind, ver). kind '' when the memo has no pump-limit tag."""
    if not memo:
        return "", ""
    m = MEMO_RE.search(memo)
    if not m:
        return "", ""
    k = {"stop_loss": "SL", "take_profit": "TP"}.get(m.group(2), "OT")
    return k, m.group(1)


def rpc(params, tries=8):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": params}).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(RPC, body, {"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            if "result" in d:
                return d["result"]
            err = d.get("error")
        except Exception as e:  # class only
            err = type(e).__name__
        time.sleep(min(60, 2 ** i))
    raise RuntimeError("rpc_failed:" + str(err)[:80])


def main(argv):
    out = argv[1]
    stop = int(argv[2]) if len(argv) > 2 else DEFAULT_STOP
    max_calls = int(argv[3]) if len(argv) > 3 else 2500
    before = None
    if os.path.exists(out) and os.path.getsize(out):
        with open(out, "rb") as f:
            f.seek(max(0, os.path.getsize(out) - 4096))
            last = f.read().decode().strip().splitlines()[-1]
        before = json.loads(last)["sig"]
    calls = 0
    status = "max_calls"
    with open(out, "a") as f:
        while calls < max_calls:
            p = {"limit": 1000, "commitment": "finalized"}
            if before:
                p["before"] = before
            rows = rpc([KEEPER, p])
            calls += 1
            if not rows:
                status = "history_exhausted"
                break
            for r in rows:
                kind, ver = classify(r.get("memo"))
                f.write(json.dumps({"sig": r["signature"], "slot": r["slot"], "bt": r.get("blockTime"),
                                    "err": 1 if r.get("err") else 0, "kind": kind, "ver": ver}) + "\n")
            f.flush()
            before = rows[-1]["signature"]
            bt = rows[-1].get("blockTime") or 0
            if calls % 25 == 0:
                print("calls", calls, "oldest_bt", bt, flush=True)
            if bt and bt < stop:
                status = "reached_stop"
                break
            time.sleep(0.3)
    print("DONE", status, "calls", calls, "oldest_bt", bt if calls else None, flush=True)


if __name__ == "__main__":
    main(sys.argv)
