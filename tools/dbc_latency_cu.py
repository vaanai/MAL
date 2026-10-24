"""P15: compute units consumed by Meteora DBC swap txs (public getTransaction on exploration-hour signatures; read-only)."""
import json, random, subprocess, sys, time, urllib.request
import numpy as np
RPC = "https://api.mainnet-beta.solana.com"
f, n = sys.argv[1], int(sys.argv[2])
sigs = []
for l in subprocess.run(["zstd", "-dc", f], capture_output=True).stdout.decode().splitlines():
    r = json.loads(l)
    if r.get("type") == "trade" and r.get("ix") in ("Swap", "Swap2"):
        sigs.append((r["signature"], r["ix"], r["side"]))
random.seed(1)
out = []
for s, ix, side in random.sample(sigs, min(n * 2, len(sigs))):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [s, {"encoding": "json", "maxSupportedTransactionVersion": 0}]}).encode()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"content-type": "application/json"}), timeout=15))["result"]
    except Exception:
        time.sleep(1); continue
    time.sleep(0.15)
    if not r: continue
    out.append((r["meta"]["computeUnitsConsumed"], ix, side, len(r["transaction"]["message"]["accountKeys"])))
    if len(out) >= n: break
cu = np.array([o[0] for o in out])
print(json.dumps({"n": len(cu), "cu_p10_p50_p90_max": [int(np.percentile(cu, q)) for q in (10, 50, 90)] + [int(cu.max())],
                  "by_ix_side": {f"{i}/{s}": [int(np.median([o[0] for o in out if o[1] == i and o[2] == s])), sum(1 for o in out if o[1] == i and o[2] == s)] for i, s in sorted({(o[1], o[2]) for o in out})},
                  "accts_median": float(np.median([o[3] for o in out]))}))
