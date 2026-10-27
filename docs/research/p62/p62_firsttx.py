"""P62 step 1c: for the sampled coins whose SharingConfig exists, is the config created in the
coin's create tx (causal from block 0) or later? Reads the oldest PDA-touching tx logs
from public RPC (0 Helius credits)."""
import collections, json, time, urllib.request

RPC = "https://api.mainnet-beta.solana.com"
rows = json.load(open("docs/research/p62/sample_pda_acct.json"))


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(6):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"Content-Type": "application/json"}), timeout=30))
            if "error" not in r:
                return r["result"]
        except Exception:
            pass
        time.sleep(1 + 2 * i)


tag = collections.Counter()
for r in rows:
    if not r["acct"]:
        continue
    # find the first tx that runs the fee program's create instruction: scan oldest 5 sigs
    sigs = rpc("getSignaturesForAddress", [r["pda"], {"limit": 1000}]) or []
    found = None
    for s in sigs[::-1][:5]:
        t = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1}])
        time.sleep(0.2)
        logs = (t or {}).get("meta", {}).get("logMessages", [])
        if any("CreateFeeSharingConfig" in l for l in logs):
            found = (s["blockTime"] - r["create_ts"], any("Instruction: CreateV2" in l or "Instruction: Create" == l[-18:] or "Instruction: Create" in l for l in logs))
            break
    r["cfg_create"] = found
    tag["found_in_oldest5" if found else "not_found"] += 1
    print(r["mint"][:8], r["acct"]["n_sh"], found)
print(tag)
json.dump(rows, open("docs/research/p62/sample_pda_acct.json", "w"))
