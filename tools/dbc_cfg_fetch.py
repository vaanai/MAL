#!/usr/bin/env python3
"""dbc_cfg_fetch: read-only getMultipleAccounts of Meteora DBC config accounts (paper/research only; read-only RPC key file, no wallet, no sends).
Input: JSON list of config pubkeys.  Output: {pubkey: {owner, lamports, space, data_b64}} .  ~1 credit per account assumed; the credit count is printed."""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parent.parent))

import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

from tools import venue_backfill as vb


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", required=True, help="JSON list of account pubkeys")
    ap.add_argument("--out", required=True)
    ap.add_argument("--env-file", default=vb.HELIUS_ENV)
    ap.add_argument("--lockdir", default="/data/mal/locks")
    ap.add_argument("--max-keys", type=int, default=200)
    a = ap.parse_args(argv)
    keys = json.loads(Path(a.keys).read_text())[: a.max_keys]
    url = vb.make_url(vb.read_key(a.env_file))
    held = None
    while held is None:
        for i in (4, 3, 2, 1):
            fh = open(Path(a.lockdir) / f"helius-{i}.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = fh
                break
            except OSError:
                fh.close()
        if held is None:
            time.sleep(15)
    call = vb.http_rpc(url, vb.Pacer(4.0))
    out, calls = {}, 0
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        res = call("getMultipleAccounts", [chunk, {"encoding": "base64", "commitment": "finalized"}])
        calls += 1
        for k, v in zip(chunk, res["value"]):
            if v:
                out[k] = {"owner": v["owner"], "lamports": v["lamports"], "space": v.get("space"), "data_b64": v["data"][0]}
    Path(a.out).write_text(json.dumps(out, sort_keys=True))
    print(json.dumps({"requested": len(keys), "returned": len(out), "rpc_calls": calls}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
