import sys, os, json, collections, time
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
rows = json.load(open(os.path.join(OUT, "curves_timed.json")))
T0 = 1792972800  # 2026-10-26T00:00Z
sel = sorted([r for r in rows if r["t"] >= T0], key=lambda r: r["t"])
res = []
for r in sel:
    sigs = []; before = None
    for _ in range(3):
        p = {"limit": 1000}
        if before: p["before"] = before
        page = call("getSignaturesForAddress", [r["curve"], p])
        sigs += page
        if len(page) < 1000: break
        before = page[-1]["signature"]
    sigs.sort(key=lambda s: s["slot"])
    cs = r["created_slot"]
    def code(s):
        e = s.get("err")
        if not e: return None
        try: return e["InstructionError"][1]["Custom"]
        except Exception: return json.dumps(e)
    rec = dict(mint=r["mint"], curve=r["curve"], created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["t"])), created_slot=cs,
               n_sigs=len(sigs), truncated=len(sigs) >= 3000,
               fail_6001_offsets=[s["slot"] - cs for s in sigs if code(s) == 6001],
               fail_6001_dt=[(s.get("blockTime") or 0) - int(r["t"]) for s in sigs if code(s) == 6001],
               other_fail=collections.Counter(str(code(s)) for s in sigs if s.get("err") and code(s) != 6001),
               first_ok_after_create=[(s["slot"] - cs, (s.get("blockTime") or 0) - int(r["t"])) for s in sigs if not s.get("err") and s["slot"] > cs][:3],
               ok_in_create_slot=sum(1 for s in sigs if not s.get("err") and s["slot"] == cs),
               rsol=r["rsol"] / 1e9)
    res.append(rec)
    print(json.dumps(rec))
json.dump(res, open(os.path.join(OUT, "cooldown.json"), "w"), indent=1)
