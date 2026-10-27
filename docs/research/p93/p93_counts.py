"""P93 step 1: counts only. Create rows (name/symbol coverage) per block; explore-0814 graduation counts. No outcome is read.
Usage: p93_counts.py OUT_creates.json"""
import sys, glob, json, subprocess, collections
R = "/data/mal/clean-view"
BAD = [0]
def rows(pat):
    for f in sorted(glob.glob(pat)):
        p = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE, text=True)
        for l in p.stdout:
            try: yield json.loads(l, strict=False)
            except ValueError: BAD[0] += 1
        p.wait()
out = {}
for b in ["explore-0814", "fresh-0903", "exp011-0909"]:
    n = nn = ns = nb = 0; has_uri = 0; seen = set(); dup = 0; keys = collections.Counter()
    for d in rows(f"{R}/{b}/*/creates/creates-*.jsonl.zst"):
        if d["mint"] in seen: dup += 1; continue
        seen.add(d["mint"]); n += 1
        nn += bool((d.get("name") or "").strip()); ns += bool((d.get("symbol") or "").strip())
        nb += bool((d.get("name") or "").strip() and (d.get("symbol") or "").strip())
        has_uri += ("uri" in d)
    print(b, "unique_creates", n, "dup_rows", dup, "name_nonempty", nn, "symbol_nonempty", ns, "both", nb, "share_both", nb / n, "rows_with_uri_field", has_uri)
    out[b] = n; print("  unparseable lines so far", BAD[0])
# explore-0814: eligible creates and graduation (complete event) counts
cr = {}
for d in rows(f"{R}/explore-0814/*/creates/creates-*.jsonl.zst"):
    cr.setdefault(d["mint"], d)
comp = {}
for d in rows(f"{R}/explore-0814/*/migrations/migrations-*.jsonl.zst"):
    if d.get("type") == "complete": comp.setdefault(d["mint"], d["event_ts"])
import datetime
END = int(datetime.datetime(2026, 8, 28, 12, tzinfo=datetime.timezone.utc).timestamp())
elig = [m for m, d in cr.items() if d["event_ts"] + 86400 <= END and (d.get("name") or "").strip() and (d.get("symbol") or "").strip()]
grad = [m for m in elig if m in comp and comp[m] <= cr[m]["event_ts"] + 43200]
print("explore-0814 creates", len(cr), "completes_in_block", len(comp), "eligible(create+24h<=block end, name&symbol)", len(elig), "graduated<=12h", len(grad))
bd = collections.Counter(datetime.datetime.fromtimestamp(cr[m]["event_ts"], datetime.timezone.utc).strftime("%Y-%m-%d") for m in grad)
print("by day", dict(sorted(bd.items())))
json.dump({"grad": sorted(grad), "create_ts": {m: cr[m]["event_ts"] for m in grad}, "complete_ts": {m: comp[m] for m in grad}}, open(sys.argv[1], "w"))
