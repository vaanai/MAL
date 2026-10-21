"""Fetch the two redeployed programdata accounts over public RPC (0 credits), save off-repo, print sha256 + slot.
Run: python3 -I s01_fetch_programdata.py   (writes /data2/mal/scratch/a3-repin-1021/pd/*.bin)"""
import base64, hashlib, json, struct, time, urllib.request
from pathlib import Path
RPC = "https://api.mainnet-beta.solana.com"
PD = {"pump": "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu", "pumpswap": "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc"}
out = Path("/data2/mal/scratch/a3-repin-1021/pd"); out.mkdir(parents=True, exist_ok=True)
res = {}
for name, addr in PD.items():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [addr, {"encoding": "base64", "commitment": "finalized"}]}).encode()
    for attempt in range(6):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"content-type": "application/json"}), timeout=120))
            break
        except Exception as e:
            print("retry", name, e); time.sleep(3 * (attempt + 1))
    v = r["result"]["value"]; raw = base64.b64decode(v["data"][0])
    (out / f"{name}.bin").write_bytes(raw)
    # UpgradeableLoaderState::ProgramData: u32 tag(3), u64 slot, Option<Pubkey> (1 + 32)
    tag, slot, has = struct.unpack("<IQB", raw[:13])
    res[name] = dict(account=addr, ctx_slot=r["result"]["context"]["slot"], len=len(raw), sha256=hashlib.sha256(raw).hexdigest(), tag=tag, deploy_slot=slot, has_authority=has,
                     elf_sha256_from_45=hashlib.sha256(raw[45:]).hexdigest())
print(json.dumps(res, indent=1))
Path("out/s01_programdata.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
