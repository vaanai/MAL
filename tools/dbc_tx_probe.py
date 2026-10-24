#!/usr/bin/env python3
"""dbc_tx_probe: read-only getTransaction of sampled DBC swap signatures -> compute units, fee, account count, hook-program invocation (P17; paper only, read-only key)."""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parent.parent))

import argparse, fcntl, json, sys, time
from pathlib import Path

from tools import dbc_decode as dd
from tools import venue_backfill as vb


def summarize(tx: dict, hook_programs: set) -> dict:
    meta = tx.get("meta") or {}
    keys = dd.account_keys(tx)
    msg = (tx.get("transaction") or {}).get("message") or {}
    top = msg.get("instructions") or []
    inner = [i for g in meta.get("innerInstructions") or [] for i in g.get("instructions") or []]
    progs = {keys[i["programIdIndex"]] for i in list(top) + inner if i.get("programIdIndex") is not None and i["programIdIndex"] < len(keys)}
    cb = "ComputeBudget111111111111111111111111111111"
    return {"cu": meta.get("computeUnitsConsumed"), "fee": meta.get("fee"), "n_keys": len(keys), "n_top_ix": len(top), "n_inner_ix": len(inner),
            "hook_invoked": bool(progs & hook_programs), "has_compute_budget": cb in progs, "err": meta.get("err") is not None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigs", required=True, help="JSON list of {signature, group, hook_programs?}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hook-programs", default="")
    ap.add_argument("--env-file", default=vb.HELIUS_ENV)
    ap.add_argument("--lockdir", default="/data/mal/locks")
    a = ap.parse_args(argv)
    items = json.loads(Path(a.sigs).read_text())
    hooks = set(filter(None, a.hook_programs.split(",")))
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
    out = []
    for it in items:
        try:
            tx = call("getTransaction", [it["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])
            out.append({**it, **(summarize(tx, hooks) if tx else {"missing": True})})
        except Exception as exc:  # one bad signature must not lose the sample
            out.append({**it, "missing": True, "error": str(exc)[:120]})
    Path(a.out).write_text(json.dumps(out, indent=0))
    print(json.dumps({"requested": len(items), "returned": sum(1 for o in out if not o.get("missing")), "rpc_calls": len(items)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
