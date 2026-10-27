"""P93 step 2: fixed sample (400 graduated explore-0814 creates, stratified by UTC create-day, seed 1) and outcome-free name/symbol cards.
Reads grad.json (mint list + create_ts; no outcome) and the creates for name/symbol only. Usage: p93_sample.py OUTDIR"""
import sys, json, glob, subprocess, hashlib, datetime
import numpy as np
OUT = sys.argv[1]
g = json.load(open("/data2/mal/p93/grad.json"))
ct = g["create_ts"]
day = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d")
days = {}
for m in sorted(ct): days.setdefault(day(ct[m]), []).append(m)
N = 400; tot = sum(len(v) for v in days.values())
raw = {d: N * len(v) / tot for d, v in days.items()}
q = {d: int(np.floor(x)) for d, x in raw.items()}
for d in sorted(raw, key=lambda d: (-(raw[d] - q[d]), d))[: N - sum(q.values())]: q[d] += 1
rng = np.random.default_rng(1)
sample = []
for d in sorted(days): sample += sorted(rng.choice(days[d], q[d], replace=False).tolist())
assert len(sample) == N and len(set(sample)) == N
want = set(sample); nm = {}
for f in sorted(glob.glob("/data/mal/clean-view/explore-0814/*/creates/creates-*.jsonl.zst")):
    for l in subprocess.run(["zstd", "-dc", f], capture_output=True, text=True).stdout.splitlines():
        try: d = json.loads(l, strict=False)
        except ValueError: continue
        if d["mint"] in want and d["mint"] not in nm: nm[d["mint"]] = (d["name"], d["symbol"])
assert len(nm) == N
order = rng.permutation(N)
cards = [{"id": "c%03d" % (k + 1), "name": nm[sample[i]][0], "symbol": nm[sample[i]][1]} for k, i in enumerate(order)]
keymap = {c["id"]: sample[i] for c, i in zip(cards, order)}
json.dump(keymap, open(f"{OUT}/keymap.json", "w"))  # NOT shown to raters
sizes = [67, 67, 67, 67, 66, 66]; p = 0
for b, s in enumerate(sizes, 1):
    with open(f"{OUT}/batch_{b}.jsonl", "w") as fh:
        for c in cards[p:p + s]: fh.write(json.dumps(c, ensure_ascii=False, separators=(",", ":")) + "\n")
    p += s
print("quota by day", {d: q[d] for d in sorted(q)})
print("sample sha256", hashlib.sha256(json.dumps(sample).encode()).hexdigest())
for b in range(1, 7): print("batch", b, hashlib.sha256(open(f"{OUT}/batch_{b}.jsonl", "rb").read()).hexdigest()[:16])
