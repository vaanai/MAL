import json, os, time, bisect, collections
OUT = os.environ["P49_OUT"]
c = json.load(open(os.path.join(OUT, "curves.json")))
a = sorted((int(k), v) for k, v in json.load(open(os.path.join(OUT, "anchors.json"))).items())
ks = [k for k, _ in a]
def t_of(s):
    i = bisect.bisect_left(ks, s)
    if i < len(ks) and ks[i] == s: return a[i][1], True
    i = max(1, min(i, len(ks) - 1)); (s0, t0), (s1, t1) = a[i-1], a[i]
    return t0 + (t1 - t0) * (s - s0) / (s1 - s0), False
rows = c["rows"]
for r in rows:
    r["t"], r["t_exact"] = t_of(r["created_slot"])
    r["day"] = time.strftime("%Y-%m-%d", time.gmtime(r["t"]))
    r["traded"] = r["rtok"] != r["supply"]
by = collections.defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    b = by[(r["day"], r["shape"])]; b[0] += 1; b[1] += r["traded"]; b[2] += r["rsol"] / 1e9
print("day shape creates traded real_sol_now")
for k in sorted(by): print(k, by[k][0], by[k][1], round(by[k][2], 3))
L = "2026-10-26T14:18:00Z"; Lt = 1793024280
Lt = time.mktime(time.strptime(L, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
print("launch epoch", Lt)
post = [r for r in rows if r["t"] >= Lt]
print("created >= 14:18Z 10-26:", len(post), "traded:", sum(r["traded"] for r in post))
print("traded curves (any day), sorted by real SOL:")
for r in sorted([r for r in rows if r["traded"]], key=lambda r: -r["rsol"]):
    print(f'  {time.strftime("%m-%dT%H:%M", time.gmtime(r["t"]))}{"" if r["t_exact"] else "~"} shape={r["shape"]} R={r["rsol"]/1e9:.3f} tok_sold={(r["supply"]-r["rtok"])/r["supply"]*100:.2f}% cfo={r["creator_fees_owed"]/1e9:.3f} pfo={r["protocol_fees_owed"]/1e9:.3f} mint={r["mint"][:8]} creator={r["creator"][:6]}')
json.dump(rows, open(os.path.join(OUT, "curves_timed.json"), "w"))
