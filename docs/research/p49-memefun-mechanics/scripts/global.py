import sys, os, json, base64, struct
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
from solders.pubkey import Pubkey
PID = Pubkey.from_string("MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F")
g, _ = Pubkey.find_program_address([b"global"], PID)
print("global", g)
r = call("getAccountInfo", [str(g), {"encoding": "base64"}])
d = base64.b64decode(r["value"]["data"][0])
o = 8
admin = Pubkey(d[o:o+32]); o += 32
fee = Pubkey(d[o:o+32]); o += 32
def cfg(d, o):
    v0, = struct.unpack_from("<Q", d, o); o += 8
    a, = struct.unpack_from("<H", d, o); o += 2
    thr, = struct.unpack_from("<Q", d, o); o += 8
    shape, = struct.unpack_from("<q", d, o); o += 8
    fees = struct.unpack_from("<6H", d, o); o += 12
    return dict(initial_virtual_sol=v0, drain_alpha_bps=a, fee_tier_threshold_lamports=thr, curve_shape=shape,
                creator_fee_bps_low=fees[0], protocol_fee_bps_low=fees[1], lp_fee_bps_low=fees[2],
                creator_fee_bps_high=fees[3], protocol_fee_bps_high=fees[4], lp_fee_bps_high=fees[5]), o
c, o = cfg(d, o)
out = dict(global_pda=str(g), owner=r["value"]["owner"], data_len=len(d), admin=str(admin), fee_recipient=str(fee), config=c, slot=r["context"]["slot"])
print(json.dumps(out, indent=1))
json.dump(out, open(os.path.join(os.environ["P49_OUT"], "global.json"), "w"), indent=1)
