import json, sys, struct, base64
sys.path.insert(0, ".")
from rpc import b58d, b58e
IDL = "/data/mal/hunt-1008/swarm-1013/w2-pump-idl/raw/"
names = {}
for f, pid in [("idl_pump.json","6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"),("idl_pump_amm.json","pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"),("idl_pump_fees.json","pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")]:
    d = json.load(open(IDL+f))
    for x in d["instructions"]: names[(pid, bytes(x["discriminator"]))] = x["name"]
EV = bytes([227,190,215,206,176,180,165,132])
def keys_of(r):
    m = r["transaction"]["message"]; la = r["meta"].get("loadedAddresses") or {}
    return m["accountKeys"] + la.get("writable",[]) + la.get("readonly",[])
def decode(r):
    m = r["transaction"]["message"]; K = keys_of(r)
    out = {"slot": r["slot"], "time": r["blockTime"], "err": r["meta"]["err"], "signer": K[0], "ixs": [], "events": []}
    for ix in m["instructions"]:
        pid = K[ix["programIdIndex"]]; data = b58d(ix["data"]); acc = [K[i] for i in ix["accounts"]]
        nm = names.get((pid, data[:8]), pid[:8] if pid.startswith("Compute") else f"{pid[:8]}:{data[:8].hex()}")
        if nm.startswith("Compute"): continue
        e = {"name": nm}
        if nm == "distribute_fee_to_holders":
            n = struct.unpack_from("<I", data, 8)[0]; amts = list(struct.unpack_from(f"<{n}Q", data, 12))
            e.update(mint=acc[2], holder_rewards=acc[3], hr_token=acc[4], quote_mint=acc[5], amounts=amts,
                     owners=[acc[11+2*i] for i in range(n)], atas=[acc[12+2*i] for i in range(n)])
        else:
            e["accounts"] = acc[:12]
        out["ixs"].append(e)
    for l in r["meta"]["logMessages"] or []:
        if l.startswith("Program data: "):
            b = base64.b64decode(l[14:])
            if b[:8] == EV:
                ts, = struct.unpack_from("<q", b, 8); rec, tot = struct.unpack_from("<QQ", b, 80)
                out["events"].append({"ts": ts, "mint": b58e(b[16:48]), "quote": b58e(b[48:80]), "recipients": rec, "total": tot})
    return out
if __name__ == "__main__":
    from tx import get
    for s in sys.argv[1:]: print(json.dumps(decode(get(s)))[:1500])
