"""QP-RULING-1121a item (ii) (b): coin_creator of MAYHEM pools created BEFORE the 2026-11-20 deploy (this review's M_pre, and the 1115 review's M_pre, M, M2, all
before 2026-11-20T19:27:54Z), read NOW from the pool account (IDL Pool type, pump_amm) against the CreatePoolEvent coin_creator carried in the s11 rows.
Also: the is_mayhem_mode byte (offset 243) of the post pool whose coin_creator differed (s11 c3 new10). One getMultipleAccounts pass. Counts only; no address printed.
Run: /data/mal/venv/bin/python s34_coin_creator_mayhem.py"""
import base64, json, sys, os, collections
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s06_parity.py").read(); exec(src.split("acc = dict(")[0])
from common import LClient
S = {"1121 M_pre": json.load(open("out/s11_migration_economics.json")), "1115": json.load(open("../program-upgrade-1115/out_stage2/s11_migration_economics.json"))}
pools = {}
for lab, d in S.items():
    for g in ("pre", "post"):
        if lab == "1121 M_pre" and g == "post": continue
        for r in d[g]["rows"]:
            if r.get("is_mayhem_mode") and r.get("coin_creator"): pools[r["pool"]] = (f"{lab}/{g}", r["coin_creator"])
c = LClient(max_calls=40, min_interval=0.5); addrs = sorted(pools); res = collections.Counter(); detail = []
T = TYPES["pump_amm"]["Pool"]["type"]
def parse(buf):
    off = 8; o = {}
    for f in T["fields"]: o[f["name"]], off = walk("pump_amm", f["type"], buf, off)
    return o
for i in range(0, len(addrs), 90):
    ch = addrs[i:i + 90]; v = c.call("getMultipleAccounts", [ch, {"encoding": "base64", "commitment": "finalized"}])
    for a, x in zip(ch, v["value"]):
        if x is None: res["unreadable"] += 1; continue
        buf = base64.b64decode(x["data"][0]); p = parse(buf); res["read"] += 1
        res["mayhem_byte_243_true"] += int(len(buf) > 243 and buf[243] == 1); res["is_mayhem_mode_field_true"] += int(bool(p.get("is_mayhem_mode")))
        eq = p["coin_creator"] == pools[a][1]; res["coin_creator_equal"] += int(eq); res["coin_creator_differs"] += int(not eq); res[f"differs|{pools[a][0]}"] += int(not eq)
# the post pool with the difference
new10 = json.load(open("out/s11_migration_economics.json"))["c3_pool_accounts"]["new10"]
diff = [p for p in new10 if p.get("coin_creator_equals_event") is False]
res["post_new10_differing_pools"] = len(diff); res["post_differing_is_mayhem_mode_byte"] = [p["is_mayhem_mode"] for p in diff]
Path("out/s34_coin_creator_mayhem.json").write_text(json.dumps({"counts": dict(res), "n_pools": len(addrs)}, indent=1)); print(dict(res), len(addrs))
