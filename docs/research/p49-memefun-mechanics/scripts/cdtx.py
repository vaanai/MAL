import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
curve = "2YTyvT4SFtKT73viH6QyQdAqq4QBdpa7SDSqXPguiNkb"; cs = 461760634
sigs = sorted(call("getSignaturesForAddress", [curve, {"limit": 1000}]), key=lambda s: s["slot"])
json.dump(sigs, open(os.path.join(OUT, "cd_curve_sigs.json"), "w"))
def code(s):
    try: return s["err"]["InstructionError"][1]["Custom"]
    except Exception: return None
fails = [s for s in sigs if code(s) == 6001]
oks = [s for s in sigs if not s.get("err") and s["slot"] > cs]
pick = [fails[0], fails[8], fails[20]] + oks[:2] + [o for o in oks if o["slot"] - cs > 80][:2]
out = []
for s in pick:
    t = call("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
    m = t["meta"]; msg = t["transaction"]["message"]
    signer = [k["pubkey"] for k in msg["accountKeys"] if k.get("signer")]
    out.append(dict(sig=s["signature"][:12], off=s["slot"] - cs, err=s.get("err"), signers=signer,
                    logs=[l for l in m["logMessages"] if "MEME4" in l or "Program log" in l or "AnchorError" in l][:12]))
for o in out: print(json.dumps(o, indent=0)[:2500]); print('---')
