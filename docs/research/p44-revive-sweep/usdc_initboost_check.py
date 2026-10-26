#!/usr/bin/env python3
"""P44: InitBoost on USDC-quoted pump-mint PumpSwap pools (structure only, public RPC, 0 Helius).
Input: pool_create rows (one JSON per line) whose quote_mint is USDC, taken from the arena tape hours 2026-10-21T00..10-24T23 with
  for each hour: zstd -dc tape-<hour>.jsonl.zst | grep -F '"type":"pool_create"' | grep -F EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
For each pump mint, the pool_create signature (the migrate tx) is fetched with getTransaction and its logs are searched for
'Instruction: InitBoost'. Prints per-day counts. No price, no trade row. Usage: usdc_initboost_check.py POOL_CREATES_JSONL"""
import collections, datetime as dt, json, sys, time, urllib.request

URL = "https://api.mainnet-beta.solana.com"


def rpc(m, p):
    for i in range(6):
        try:
            req = urllib.request.Request(URL, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                         headers={"content-type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if "result" in r:
                return r["result"]
        except Exception:
            pass
        time.sleep(2 + 2 * i)
    return "ERR"


def main(path):
    c = collections.Counter(); calls = 0
    for line in open(path):
        r = json.loads(line)
        d = dt.datetime.fromtimestamp(r["t_recv_ms"] / 1000, dt.timezone.utc).strftime("%m-%d")
        if not r["mint"].endswith("pump"):
            c[(d, "nonpump")] += 1; continue
        tx = rpc("getTransaction", [r["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}]); calls += 1
        if tx in ("ERR", None):
            c[(d, "fetch_fail")] += 1; continue
        logs = tx["meta"].get("logMessages") or []
        c[(d, "initboost" if any("Instruction: InitBoost" in x for x in logs) else "no_initboost")] += 1
        time.sleep(0.4)
    print("calls", calls); print(sorted(c.items()))


if __name__ == "__main__":
    main(sys.argv[1])
