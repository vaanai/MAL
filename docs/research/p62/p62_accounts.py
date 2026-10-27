"""P62 step 1b: does the SharingConfig PDA exist for the sampled coins (CURRENT state, not
causal) and what do shareholders / admin_revoked look like. Public RPC, 0 Helius credits.
SharingConfig layout (idl_pump_fees.json): disc8, bump u8, version u8, status u8, mint 32,
admin 32, admin_revoked u8, shareholders vec<(pubkey32, u16)>.
"""
import base64, collections, json, struct, time, urllib.request

RPC = "https://api.mainnet-beta.solana.com"
rows = json.load(open("docs/research/p62/sample_pda.json"))


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


for i in range(0, len(rows), 50):
    chunk = rows[i:i + 50]
    res = rpc("getMultipleAccounts", [[r["pda"] for r in chunk], {"encoding": "base64"}])
    for r, v in zip(chunk, res["value"]):
        if v is None:
            r["acct"] = None
            continue
        b = base64.b64decode(v["data"][0])
        o = 8
        bump, ver, status = b[o], b[o + 1], b[o + 2]
        o += 3 + 32
        admin = b[o:o + 32]
        o += 32
        revoked = b[o]
        o += 1
        n = struct.unpack_from("<I", b, o)[0]
        o += 4
        sh = [(b[o + 34 * k:o + 34 * k + 32].hex()[:8], struct.unpack_from("<H", b, o + 34 * k + 32)[0]) for k in range(n)]
        r["acct"] = {"owner": v["owner"], "status": status, "revoked": revoked, "n_sh": n,
                     "creator_is_sole_or_in": any(True for _ in sh), "len": len(b)}
    time.sleep(0.5)
json.dump(rows, open("docs/research/p62/sample_pda_acct.json", "w"))
ex = [r for r in rows if r["acct"]]
print("sampled", len(rows), "PDA exists", len(ex), "absent", len(rows) - len(ex))
print("owner", collections.Counter(r["acct"]["owner"][:6] for r in ex))
print("n_shareholders", collections.Counter(r["acct"]["n_sh"] for r in ex))
print("admin_revoked", collections.Counter(r["acct"]["revoked"] for r in ex))
print("status", collections.Counter(r["acct"]["status"] for r in ex))
