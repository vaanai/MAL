"""Extract LaunchLab PoolCreateEvent rows (successful txs) from a p36_capture gzip JSONL into [{sig, pool, slot}] (P36, counts only)."""
import gzip
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
out, seen, n_notice, n_failed = [], set(), 0, 0
for line in gzip.open(src, "rt"):
    d = json.loads(line)
    if d.get("k") == "notice" and d.get("prog") == "launchlab":
        n_notice += 1
        n_failed += bool(d.get("failed"))
    if d.get("k") == "row" and d.get("venue") == "launchlab" and d.get("type") == "create" and not d.get("failed") and d["signature"] not in seen:
        seen.add(d["signature"])
        out.append({"sig": d["signature"], "pool": d["pool"], "slot": d["slot"], "config": d.get("config")})
json.dump(out, open(dst, "w"), indent=1)
print(json.dumps({"creates": len(out), "notices": n_notice, "failed": n_failed}))
