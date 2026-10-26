"""P49 public RPC helper with a call ledger and a hard cap."""
import json, time, urllib.request, os, sys
OUT = os.environ.get("P49_OUT")
LEDGER = os.path.join(OUT, "p49_calls.jsonl")
CAP = 500
EP = os.environ.get("P49_EP", "https://solana-rpc.publicnode.com")
def ncalls():
    try:
        return sum(1 for _ in open(LEDGER))
    except FileNotFoundError:
        return 0
def call(method, params, ep=None):
    ep = ep or EP
    n = ncalls()
    if n >= CAP:
        raise SystemExit(f"call cap {CAP} reached")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(ep, data=body, headers={"Content-Type": "application/json", "User-Agent": "mal-p49"})
    t0 = time.time(); err = None; res = None
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
    except Exception as e:
        err = repr(e)[:200]
    with open(LEDGER, "a") as f:
        f.write(json.dumps({"n": n + 1, "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ep": ep, "method": method, "ok": err is None and res is not None and "error" not in res, "err": err or (res or {}).get("error"), "s": round(time.time() - t0, 3)}) + "\n")
    time.sleep(0.25)
    if err:
        raise RuntimeError(err)
    if "error" in res:
        raise RuntimeError(json.dumps(res["error"])[:300])
    return res["result"]
