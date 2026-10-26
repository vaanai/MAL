import sys, os, json, base64, struct
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call

from solders.pubkey import Pubkey
B58 = "4y6pru6YvC7"
PID = "MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F"
DISC = bytes([23, 183, 248, 55, 96, 216, 172, 96])
ep = sys.argv[1] if len(sys.argv) > 1 else None
r = call("getProgramAccounts", [PID, {"encoding": "base64", "withContext": True,
         "filters": [{"memcmp": {"offset": 0, "bytes": B58}}]}], ep=ep)
slot = r["context"]["slot"]; rows = []
for a in r["value"]:
    d = base64.b64decode(a["account"]["data"][0]); o = 8
    mint, creator, rta, vault = [str(Pubkey(d[o+32*i:o+32*i+32])) for i in range(4)]; o += 128
    vsol, vtok, rsol, rtok, supply, cslot = struct.unpack_from("<6Q", d, o); o += 48
    bump = d[o]; o += 1
    mhs, = struct.unpack_from("<q", d, o); o += 8
    cfe = d[o]; o += 1
    cfo, pfo = struct.unpack_from("<2Q", d, o); o += 16
    v0, = struct.unpack_from("<Q", d, o); o += 8
    al, = struct.unpack_from("<H", d, o); o += 2
    thr, = struct.unpack_from("<Q", d, o); o += 8
    shape, = struct.unpack_from("<q", d, o); o += 8
    fees = struct.unpack_from("<6H", d, o); o += 12
    rows.append(dict(curve=a["pubkey"], mint=mint, creator=creator, vsol=vsol, vtok=vtok, rsol=rsol, rtok=rtok,
                     supply=supply, created_slot=cslot, creator_fees_owed=cfo, protocol_fees_owed=pfo,
                     v0=v0, alpha_bps=al, thr=thr, shape=shape, fees=fees, data_len=len(d), lamports=a["account"]["lamports"]))
json.dump(dict(slot=slot, n=len(rows), rows=rows), open(os.path.join(os.environ["P49_OUT"], "curves.json"), "w"))
print("slot", slot, "curves", len(rows))
