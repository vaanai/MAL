"""Per-event-type table, post vs pre, against tools/a3_redeploy_pins.json: disc_len (observed blob lengths incl. trailing bytes) and trailing_bytes.
Offline, from out/s06_parity*.json. Pre = P + P2 + M_pre (summed); post = A + M. Structure only."""
import collections, json
from pathlib import Path
O = Path("out"); PIN = json.load(open("../../../tools/a3_redeploy_pins.json")); DOCS = Path("/data2/mal/scratch/a3-repin-1114/docs")
disc = {}
for k in ("pump", "pump_amm", "pump_fees"):
    for e in json.load(open(DOCS / f"{k}.main.json"))["events"]:
        disc[f"{k}.{e['name']}"] = bytes(e["discriminator"]).hex()
def load(names):
    out = []
    for n in names:
        p = O / n
        if p.exists(): out.append(json.load(open(p)))
    return out
groups = {"post": load(["s06_parity.json"]), "pre": load(["s06_parity_pre.json", "s06_parity_pre2.json", "s06_parity_premig.json"])}
rows = {}
for g, js in groups.items():
    for j in js:
        for ev, n in j["events"].items():
            r = rows.setdefault(ev, {}).setdefault(g, {"n": 0, "trail8": 0, "lens": collections.Counter()})
            r["n"] += n
        for ev, n in j["trail8"].items():
            rows.setdefault(ev, {}).setdefault(g, {"n": 0, "trail8": 0, "lens": collections.Counter()})["trail8"] += n
        for ev, lens in j["layout_len"].items():
            r = rows.setdefault(ev, {}).setdefault(g, {"n": 0, "trail8": 0, "lens": collections.Counter()})
            for L, n in lens.items(): r["lens"][int(L)] += n
table, problems = [], []
for ev in sorted(rows):
    short = ev.split(".", 1)[1]; d = disc.get(ev)
    trail_pin = PIN["trailing_bytes"].get(short) if ev.startswith(("pump.", "pump_amm.")) else None
    lp = PIN["disc_len"].get(d)
    rec = {"event": ev, "disc": d, "trailing_pin": trail_pin, "disc_len_pin": lp}
    for g in ("post", "pre"):
        r = rows[ev].get(g)
        if r:
            rec[g] = {"n": r["n"], "n_trailing8": r["trail8"], "len_min": min(r["lens"]) if r["lens"] else None, "len_max": max(r["lens"]) if r["lens"] else None}
            if trail_pin is not None:
                want8 = trail_pin == [8]
                if (want8 and r["trail8"] != r["n"]) or (not want8 and r["trail8"] > 0): problems.append(f"{g} {ev}: n={r['n']} trailing8={r['trail8']} pin={trail_pin}")
            if lp and r["lens"] and (min(r["lens"]) < lp["min_len"] or max(r["lens"]) > lp["max_len"]): problems.append(f"{g} {ev}: lens {min(r['lens'])}..{max(r['lens'])} outside disc_len pin {lp}")
    # post-vs-pre trailing consistency for events with no pin (control)
    if trail_pin is None and "post" in rec and "pre" in rec:
        if (rec["post"]["n_trailing8"] > 0) != (rec["pre"]["n_trailing8"] > 0): problems.append(f"{ev}: trailing8 post {rec['post']['n_trailing8']}/{rec['post']['n']} vs pre {rec['pre']['n_trailing8']}/{rec['pre']['n']} (no pin)")
    table.append(rec)
(O / "s12_pins_table.json").write_text(json.dumps({"table": table, "deviations": problems}, indent=1))
for t in table: print(t["event"], "pin", t["trailing_pin"], "post", t.get("post"), "pre", t.get("pre"))
print("DEVIATIONS", json.dumps(problems, indent=1))
