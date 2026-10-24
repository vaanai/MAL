#!/usr/bin/env python3
"""P18 measurement (public RPC, read-only, no keys, 0 Helius credits): DBC tx rate and getTransaction payload size / inner-event presence."""
import json, sys, time, urllib.request, random
URL = "https://api.mainnet-beta.solana.com"
DBC = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
def rpc(m, p):
    req = urllib.request.Request(URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
    return json.loads(b), len(b)
sigs = []
before = None
for _ in range(5):
    p = {"limit": 1000}
    if before: p["before"] = before
    j, _ = rpc("getSignaturesForAddress", [DBC, p]); r = j["result"]
    if not r: break
    sigs += r; before = r[-1]["signature"]; time.sleep(0.5)
ok = [s for s in sigs if not s.get("err")]
span = sigs[0]["blockTime"] - sigs[-1]["blockTime"]
out = {"n_sigs": len(sigs), "n_ok": len(ok), "span_s": span, "ok_per_s": len(ok) / max(span, 1), "all_per_s": len(sigs) / max(span, 1)}
sample = random.Random(1).sample(ok, min(120, len(ok)))
sizes, inner_ev, lat = [], 0, []
for s in sample:
    t = time.time()
    try:
        j, n = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
    except Exception as e:
        time.sleep(2); continue
    lat.append(time.time() - t)
    if j.get("result"):
        sizes.append(n)
        inn = j["result"]["meta"].get("innerInstructions") or []
        if any(i for g in inn for i in g["instructions"] if len(i.get("data", "")) > 150): inner_ev += 1
    time.sleep(0.25)
sizes.sort(); lat.sort()
q = lambda a, f: a[int(f * (len(a) - 1))] if a else None
out.update({"tx_fetched": len(sizes), "bytes_mean": sum(sizes) / max(len(sizes), 1), "bytes_p50": q(sizes, .5), "bytes_p90": q(sizes, .9),
            "with_long_inner_ix_data": inner_ev, "fetch_s_p50": q(lat, .5), "fetch_s_p90": q(lat, .9)})
print(json.dumps(out)); 
import os
if os.environ.get("MISCUSI_RESULT"): open(os.environ["MISCUSI_RESULT"], "w").write(json.dumps({"ok": True, "metrics": out, "summary": json.dumps(out)}))
