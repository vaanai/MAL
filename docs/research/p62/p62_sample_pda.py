"""P62 step 1: counts-only feasibility of the fee-sharing label.

Samples N coins from the exploration-block creates tape (explore-0814 / fresh-0903 /
exp011-0909 clean views; Aug/Sep only), derives the fee-program SharingConfig PDA
(seeds "sharing-config" + mint, program pfeeUx...), and asks PUBLIC RPC
getSignaturesForAddress for it. Writes sig counts, first-sig block time and
first-tx instruction names. No trade/price/outcome is read. 0 Helius credits.
"""
import glob, json, random, subprocess, sys, time, urllib.request
from solders.pubkey import Pubkey

FEE = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
RPC = "https://api.mainnet-beta.solana.com"
ROOTS = ["explore-0814", "fresh-0903", "exp011-0909"]
OUT = sys.argv[1]
N = int(sys.argv[2])


def rpc(method, params, tries=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(RPC, body, {"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if "error" in r:
                time.sleep(1 + i)
                continue
            return r["result"]
        except Exception:
            time.sleep(1 + 2 * i)
    return None


def creates_files():
    fs = []
    for r in ROOTS:
        fs += glob.glob(f"/data/mal/clean-view/{r}/*/creates/creates-*.jsonl.zst")
        fs += glob.glob(f"/data/mal/clean-view/{r}/*/*/creates/creates-*.jsonl.zst")
    return sorted(set(fs))


def main():
    rnd = random.Random(1)
    fs = creates_files()
    print("create files", len(fs))
    picks = rnd.sample(fs, min(len(fs), N))
    rows = []
    for f in picks:
        lines = subprocess.run(["zstd", "-dc", f], capture_output=True, text=True).stdout.splitlines()
        if not lines:
            continue
        c = json.loads(rnd.choice(lines))
        if c.get("venue") != "pump_bonding":
            continue
        mint = c["mint"]
        pda, _ = Pubkey.find_program_address([b"sharing-config", bytes(Pubkey.from_string(mint))], FEE)
        rows.append({"file": f.split("/clean-view/")[1], "mint": mint, "creator": c["creator"],
                     "create_ts": c["event_ts"], "pda": str(pda)})
    print("coins", len(rows))
    for r in rows:
        sigs = rpc("getSignaturesForAddress", [r["pda"], {"limit": 1000}])
        time.sleep(0.2)
        if sigs is None:
            r["n_sigs"] = None
            continue
        r["n_sigs"] = len(sigs)
        if sigs:
            old = sigs[-1]
            r["first_sig_ts"] = old.get("blockTime")
            r["first_sig"] = old["signature"]
            r["first_err"] = old.get("err") is not None
    json.dump(rows, open(OUT, "w"))
    ok = [r for r in rows if r["n_sigs"] is not None]
    print("queried ok", len(ok), "with any sig", sum(1 for r in ok if r["n_sigs"] > 0))


main()
