import base64, struct, sys
sys.path.insert(0, ".")
from rpc import call, b58e
G = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
r = call("getAccountInfo", [G, {"encoding":"base64"}])
d = base64.b64decode(r["value"]["data"][0]); print("owner", r["value"]["owner"], "len", len(d))
o = 8
def pk():
    global o; v = b58e(d[o:o+32]); o += 32; return v
def u64():
    global o; v = struct.unpack_from("<Q", d, o)[0]; o += 8; return v
def b():
    global o; v = d[o]; o += 1; return v
out = {}
out["initialized"]=b(); out["authority"]=pk(); out["fee_recipient"]=pk()
for k in ["ivtr","ivsr","irtr","supply","fee_bps"]: out[k]=u64()
out["withdraw_authority"]=pk(); out["enable_migrate"]=b(); out["pool_migration_fee"]=u64(); out["creator_fee_bps"]=u64()
out["fee_recipients"]=[pk() for _ in range(7)]
out["set_creator_authority"]=pk(); out["admin_set_creator_authority"]=pk(); out["create_v2_enabled"]=b(); out["whitelist_pda"]=pk()
out["reserved_fee_recipient"]=pk(); out["mayhem"]=b(); out["reserved"]=[pk() for _ in range(7)]; out["is_cashback_enabled"]=b()
out["buyback"]=[pk() for _ in range(8)]; out["buyback_bps"]=u64(); out["ivqr"]=u64(); out["wl_quote"]=[pk()]
out["creator_fee_configurable"]=b(); out["max_configurable_creator_fee_bps"]=u64(); out["holder_reward_claim_authority"]=pk(); out["is_holder_reward_enabled"]=b(); out["max_curve_depth"]=b()
for k in ["holder_reward_claim_authority","is_holder_reward_enabled","max_configurable_creator_fee_bps","creator_fee_configurable","creator_fee_bps","fee_bps"]: print(k, out[k])
print("bytes used", o, "of", len(d))
