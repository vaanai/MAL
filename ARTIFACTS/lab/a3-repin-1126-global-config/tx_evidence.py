#!/usr/bin/env python3
"""A3 re-pin 1126: evidence for the UpdateFeeConfig tx (QP-RULING-1126 Q1 item 3)."""
import json, sys, time, urllib.request, datetime

SIG = "3CPjvGX4JoEZzm8jjSovbk1ACxmVCXsVhNoSE29CWzutjmWBnJJjM32EjcdNS2U4XzxLGbq8cjw8mpZp1zuHc5Yy"
GC = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
ADMIN = "FFWtrEQ4B4PKQoVuHYzZq8FabGkVatYzDpEVHsK5rrhF"
PAMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RPC = "https://api.mainnet-beta.solana.com"


def rpc():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": [
        SIG, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]}).encode()
    req = urllib.request.Request(RPC, body, {"Content-Type": "application/json", "User-Agent": "mal-research/1.0"})
    for i in range(8):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if r.get("result"): return r["result"]
            print("no result", r.get("error"), file=sys.stderr)
        except Exception as e:
            print("retry", i, type(e).__name__, file=sys.stderr)
        time.sleep(2 ** i)
    raise SystemExit("rpc failed")


def main():
    t = rpc(); m = t["meta"]; msg = t["transaction"]["message"]
    keys = list(msg["accountKeys"]) + m.get("loadedAddresses", {}).get("writable", []) + m.get("loadedAddresses", {}).get("readonly", [])
    utc = datetime.datetime.fromtimestamp(t["blockTime"], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("slot", t["slot"]); print("blockTime utc", utc); print("meta.err", m["err"])
    lines = [l for l in m["logMessages"] if "Instruction: UpdateFeeConfig" in l]
    print("log line:", lines)
    inner_hits = []
    for grp in m.get("innerInstructions", []):
        for ix in grp["instructions"]:
            if keys[ix["programIdIndex"]] == PAMM:
                accts = [keys[a] for a in ix["accounts"]]
                inner_hits.append(GC in accts)
    outer_hits = [GC in [keys[a] for a in ix["accounts"]] for ix in msg["instructions"] if keys[ix["programIdIndex"]] == PAMM]
    print("PumpSwap inner ix count", len(inner_hits), "targets GlobalConfig", inner_hits)
    print("PumpSwap outer ix targeting GlobalConfig", outer_hits)
    print("GlobalConfig in account keys", GC in keys, "| pAMM in keys", PAMM in keys)
    print("admin FFWtrEQ4 in account keys", ADMIN in keys)
    ok = (t["slot"] == 473891616 and m["err"] is None and bool(lines) and ADMIN in keys
          and (any(inner_hits) or any(outer_hits)))
    print("BAR", "PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
