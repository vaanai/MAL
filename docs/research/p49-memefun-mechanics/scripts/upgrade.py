import sys, os, json, base64, struct, time
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
from solders.pubkey import Pubkey
OUT = os.environ["P49_OUT"]
p = call("getAccountInfo", ["MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F", {"encoding": "base64"}])["value"]
d = base64.b64decode(p["data"][0]); pd = str(Pubkey(d[4:36]))
q = call("getAccountInfo", [pd, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 45}}])["value"]
e = base64.b64decode(q["data"][0]); slot, = struct.unpack_from("<Q", e, 4)
auth = str(Pubkey(e[13:45])) if e[12] == 1 else None
a = call("getAccountInfo", [auth, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])["value"] if auth else None
bt = call("getBlockTime", [slot])
out = dict(program_owner=p["owner"], programdata=pd, last_deploy_slot=slot, last_deploy_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bt)), upgrade_authority=auth, authority_owner=(a or {}).get("owner"), authority_exists=a is not None)
print(json.dumps(out, indent=1)); json.dump(out, open(os.path.join(OUT, "upgrade.json"), "w"), indent=1)
