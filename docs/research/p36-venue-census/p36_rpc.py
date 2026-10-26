"""Tiny public-RPC client with a hard call budget and a call ledger (P36). Public api.mainnet-beta only; refuses URLs that look keyed."""
from __future__ import annotations

import json
import os
import time
import urllib.request

URL = os.environ.get("P36_RPC", "https://api.mainnet-beta.solana.com")
if "helius" in URL.lower() or "api-key" in URL.lower() or "?" in URL:
    raise SystemExit("p36_rpc: public unkeyed RPC only")
LEDGER = os.environ.get("P36_LEDGER", "p36_calls.jsonl")
BUDGET = int(os.environ.get("P36_BUDGET", "1500"))
SPACING_S = float(os.environ.get("P36_SPACING", "0.45"))
_last = [0.0]


def calls_used() -> int:
    try:
        with open(LEDGER) as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def rpc(method: str, params: list, tag: str = ""):
    if calls_used() >= BUDGET:
        raise RuntimeError(f"p36 RPC budget {BUDGET} reached")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    err = None
    for attempt in range(5):
        wait = SPACING_S - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        with open(LEDGER, "a") as f:
            f.write(json.dumps({"t": int(time.time()), "m": method, "tag": tag, "attempt": attempt}) + "\n")
        try:
            req = urllib.request.Request(URL, data=body, headers={"content-type": "application/json", "user-agent": "mal-p36"})
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.load(r)
            if "error" in d:
                err = d["error"]
                if d["error"].get("code") in (429, -32429) or "rate" in str(d["error"]).lower():
                    time.sleep(5 * (attempt + 1)); continue
                return {"error": d["error"]}
            return d.get("result")
        except Exception as e:  # noqa: BLE001
            err = str(e)
            time.sleep(5 * (attempt + 1))
    return {"error": err}


def get_tx(sig: str, tag: str = ""):
    return rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}], tag)


def sigs(addr: str, tag: str = "", **opt):
    o = {"limit": 1000, "commitment": "confirmed"}
    o.update(opt)
    return rpc("getSignaturesForAddress", [addr, o], tag)
