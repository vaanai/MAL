import json, time, urllib.request, os, sys
URL = "https://api.mainnet-beta.solana.com"
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.log")
CAP = 2000
def ncalls():
    try: return sum(1 for _ in open(LOG))
    except FileNotFoundError: return 0
def call(method, params):
    n = ncalls()
    if n >= CAP: raise SystemExit("call cap reached")
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    for attempt in range(6):
        with open(LOG, "a") as f: f.write(f"{time.time():.0f} {method}\n")
        try:
            req = urllib.request.Request(URL, body, {"content-type":"application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            time.sleep(0.35)
            if "error" in r:
                if r["error"].get("code") in (429, -32429): time.sleep(5*(attempt+1)); continue
                raise RuntimeError(r["error"])
            return r["result"]
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(5*(attempt+1)); continue
            raise
    raise RuntimeError("retries exhausted")
A = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58e(b):
    n = int.from_bytes(b, "big"); s = ""
    while n: n, r = divmod(n, 58); s = A[r] + s
    return "1"*(len(b)-len(b.lstrip(b"\0"))) + s
def b58d(s):
    n = 0
    for c in s: n = n*58 + A.index(c)
    b = n.to_bytes((n.bit_length()+7)//8, "big")
    return b"\0"*(len(s)-len(s.lstrip("1"))) + b
