"""Fetch the three programdata accounts (pump, PumpSwap, fees) and the three pinned config accounts over public RPC (0 credits).
Programdata address comes from each program account (UpgradeableLoaderState::Program tag 2). Raw bytes of the programdata go off-repo
(/data2/mal/scratch/a3-repin-1114/pd/*.bin); the config accounts are only hashed. Structure only.
Run from this directory: python3 -I s01_fetch_programdata.py"""
import base64, hashlib, json, struct, time, urllib.request
from pathlib import Path
RPC = "https://api.mainnet-beta.solana.com"
PROG = {"pump": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pumpswap": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "fees": "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"}
CFG = {"bonding_fee_config": "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt", "pumpswap_fee_config": "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx", "pumpswap_global_config": "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"}
A58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b):
    n = int.from_bytes(b, "big"); s = ""
    while n:
        n, r = divmod(n, 58); s = A58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


out = Path("/data2/mal/scratch/a3-repin-1114/pd"); out.mkdir(parents=True, exist_ok=True)


def get(addr):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [addr, {"encoding": "base64", "commitment": "finalized"}]}).encode()
    for attempt in range(8):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"content-type": "application/json"}), timeout=120))
            return r["result"]["context"]["slot"], r["result"]["value"]
        except Exception as e:
            print("retry", addr, e); time.sleep(3 * (attempt + 1))
    raise SystemExit("rpc failed")


res = {"programdata": {}, "config": {}}
for name, pid in PROG.items():
    _, v = get(pid); raw = base64.b64decode(v["data"][0]); tag = struct.unpack("<I", raw[:4])[0]; assert tag == 2
    addr = b58(raw[4:36]); time.sleep(0.6)
    ctx, v = get(addr); raw = base64.b64decode(v["data"][0]); time.sleep(0.6)
    (out / f"{name}.bin").write_bytes(raw)
    tag, slot, has = struct.unpack("<IQB", raw[:13])
    res["programdata"][name] = dict(program=pid, account=addr, ctx_slot=ctx, len=len(raw), sha256=hashlib.sha256(raw).hexdigest(), tag=tag, deploy_slot=slot,
                                   has_authority=has, elf_sha256_from_45=hashlib.sha256(raw[45:]).hexdigest())
for name, addr in CFG.items():
    ctx, v = get(addr); raw = base64.b64decode(v["data"][0]); time.sleep(0.6)
    res["config"][name] = dict(account=addr, ctx_slot=ctx, len=len(raw), sha256=hashlib.sha256(raw).hexdigest())
pins = json.load(open("../../../tools/pump_structure_pins.json"))
for name, d in res["programdata"].items():
    p = pins["programs"][name]; d["pin_deploy_slot"] = p["deploy_slot"]; d["pin_sha256"] = p["sha256"]
    d["equals_pin"] = (p["sha256"] == d["sha256"] and p["deploy_slot"] == d["deploy_slot"])
for name, d in res["config"].items():
    p = pins["accounts"][name]; d["pin_sha256"] = p["sha256"]; d["equals_pin"] = (p["sha256"] == d["sha256"] and p["len"] == d["len"])
print(json.dumps(res, indent=1))
Path("out/s01_programdata.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
