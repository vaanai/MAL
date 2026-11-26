#!/usr/bin/env python3
"""A3 re-pin 1126: byte diff of old PumpSwap GlobalConfig (repo fixture) vs ONE finalized read.
Exit non-zero unless diff ranges == [[57, 312]] and new sha256 starts a4c67d6dc665973c."""
import base64, hashlib, json, sys, urllib.request, datetime, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PUBKEY = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
RPC = "https://api.mainnet-beta.solana.com"
OLD_SHA = "a698009a419d0b8c56ff257626d15cba305c4989a602fa86e65ca252b2c200f4"
NEW_PREFIX = "a4c67d6dc665973c"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b):
    n = int.from_bytes(b, "big"); s = ""
    while n:
        n, r = divmod(n, 58); s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def fetch():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                       "params": [PUBKEY, {"encoding": "base64", "commitment": "finalized"}]}).encode()
    req = urllib.request.Request(RPC, body, {"Content-Type": "application/json", "User-Agent": "mal-research/1.0"})
    for i in range(6):
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            print("retry", i, type(e).__name__, file=sys.stderr); time.sleep(2 ** i)
    raise SystemExit("rpc failed")


def decode(b):
    u64 = lambda o: int.from_bytes(b[o:o + 8], "little")
    return {"admin": b58(b[8:40]), "lp_fee_bps": u64(40), "protocol_fee_bps": u64(48),
            "disable_flags": b[56],
            "protocol_fee_recipients": [b58(b[57 + 32 * i:89 + 32 * i]) for i in range(8)],
            "coin_creator_fee_bps": u64(313),
            "admin_set_coin_creator_authority": b58(b[321:353])}


def ranges(a, b):
    out, s = [], None
    for i in range(len(a)):
        if a[i] != b[i]:
            if s is None: s = i
            e = i
        elif s is not None:
            out.append([s, e]); s = None
    if s is not None: out.append([s, e])
    return out


def main():
    fx = json.loads((REPO / "tools/fixtures/pumpswap/global_config.json").read_text())
    old = base64.b64decode(fx["data_b64"])
    osha = hashlib.sha256(old).hexdigest()
    print("old fixture sha256", osha, "len", len(old), "slot", fx["slot"])
    assert osha == OLD_SHA and len(old) == 949, "old fixture mismatch"
    r = fetch()
    slot = r["result"]["context"]["slot"]
    new = base64.b64decode(r["result"]["value"]["data"][0])
    utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nsha = hashlib.sha256(new).hexdigest()
    print("read slot", slot, "utc", utc, "owner", r["result"]["value"]["owner"])
    print("new sha256", nsha, "len", len(new))
    if len(new) != len(old):
        print("LENGTH DIFFERS"); return 1
    rg = ranges(old, new)
    print("diff ranges (inclusive)", rg, "changed bytes", sum(e - s + 1 for s, e in rg))
    outside = all(old[i] == new[i] for i in range(len(old)) if not any(s <= i <= e for s, e in rg))
    print("bytes outside ranges equal:", outside)
    do, dn = decode(old), decode(new)
    for k in do:
        print(f"{k}: old={do[k]} new={dn[k]}" + ("" if do[k] == dn[k] else "  CHANGED"))
    ok = rg == [[57, 312]] and nsha.startswith(NEW_PREFIX) and outside
    print("BAR", "PASS" if ok else "FAIL")
    print("FULL_NEW_SHA256", nsha)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
