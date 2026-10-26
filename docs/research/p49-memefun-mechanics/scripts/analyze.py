import json, os, collections
OUT = os.environ["P49_OUT"]
c = json.load(open(os.path.join(OUT, "curves.json")))
rows = c["rows"]; slot = c["slot"]
def V(R, v0, a, shape):
    if shape == 1:
        A = a * R; o = 2 * v0 * 10000
        if A >= o: return R
        l = 2 * o; n = 10000 * l
        return v0 + R - (A * (l - A) + n - 1) // n
    t = 10000 * v0 // a
    if R > t: return R
    return v0 + (10000 - a) * R // 10000
cfgs = collections.Counter((r["v0"], r["alpha_bps"], r["thr"], r["shape"], tuple(r["fees"])) for r in rows)
print("configs:"); [print("  ", k, v) for k, v in cfgs.most_common()]
mism = [r for r in rows if V(r["rsol"], r["v0"], r["alpha_bps"], r["shape"]) != r["vsol"]]
print("V(R) formula mismatches vs on-chain vsol:", len(mism), "of", len(rows))
for r in mism[:5]: print("   ", r["curve"], r["rsol"], r["vsol"], V(r["rsol"], r["v0"], r["alpha_bps"], r["shape"]))
slots = sorted(r["created_slot"] for r in rows)
print("created_slot min/max", slots[0], slots[-1], "snapshot slot", slot)
rs = sorted((r["rsol"] / 1e9 for r in rows), reverse=True)
print("top 15 real SOL:", [round(x, 3) for x in rs[:15]])
for th in [0.1, 1, 5, 10, 20, 33.35, 50, 85, 100, 166.744, 350]:
    print(f"  curves with real_sol >= {th}: {sum(1 for x in rs if x >= th)}")
print("total real SOL in curves:", round(sum(rs), 3))
print("supply values:", collections.Counter(r["supply"] for r in rows).most_common(3))
print("rtok == supply (never bought):", sum(1 for r in rows if r["rtok"] == r["supply"]))
print("creators distinct:", len(set(r["creator"] for r in rows)), "top:", collections.Counter(r["creator"] for r in rows).most_common(5))
print("fees owed (SOL) creator / protocol:", sum(r["creator_fees_owed"] for r in rows)/1e9, sum(r["protocol_fees_owed"] for r in rows)/1e9)
