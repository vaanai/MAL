"""P93 label: survival from explore-0814 pumpswap rows (pre-trade quote_reserve). Usage: p93_label.py OUT.json
Prefilters the hourly trade files with grep -F on the 400 sampled mints; parses only matching pumpswap rows."""
import sys, json, glob, subprocess
OUT = sys.argv[1]
km = json.load(open("/data2/mal/p93/keymap.json")); g = json.load(open("/data2/mal/p93/grad.json"))
mints = set(km.values()); open("/data2/mal/p93/mints.txt", "w").write("\n".join(sorted(mints)) + "\n")
ct = g["create_ts"]; ser = {m: [] for m in mints}
for f in sorted(glob.glob("/data/mal/clean-view/explore-0814/*/trades/trades-*.jsonl.zst")):
    p = subprocess.run(f"zstd -dc {f} | grep -F -f /data2/mal/p93/mints.txt", shell=True, capture_output=True, text=True).stdout
    for l in p.splitlines():
        try: d = json.loads(l, strict=False)
        except ValueError: continue
        m = d.get("mint")
        if m in mints and d.get("venue") == "pumpswap" and d.get("quote_reserve") is not None:
            if ct[m] <= d["event_ts"] <= ct[m] + 86400: ser[m].append((d["slot"], d.get("tx_index") or 0, d.get("event_index") or 0, d["quote_reserve"]))
lab = {}
for m, s in ser.items():
    if not s: lab[m] = None; continue
    s.sort(); q = [x[3] for x in s]; peak = max(q); fin = q[-1]
    lab[m] = {"peak": peak, "final": fin, "n": len(q), "y": int(fin >= 0.5 * peak)}
json.dump(lab, open(OUT, "w"))
v = [x for x in lab.values() if x]
print("labelled", len(v), "excluded(no pumpswap row in window)", sum(x is None for x in lab.values()), "survive", sum(x["y"] for x in v), "base_rate", sum(x["y"] for x in v) / len(v))
