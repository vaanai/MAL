import json, collections
rows = json.load(open("docs/research/p62/sample_pda.json"))
ok = [r for r in rows if r["n_sigs"] is not None]
print("n", len(rows), "ok", len(ok))
c = collections.Counter(min(r["n_sigs"], 5) for r in ok)
print("n_sigs (5 = 5+):", sorted(c.items()))
w = [r for r in ok if r["n_sigs"] > 0]
d = [r["first_sig_ts"] - r["create_ts"] for r in w if r.get("first_sig_ts")]
d.sort()
q = lambda p: d[int(p * (len(d) - 1))]
print("first_sig - create secs: min", d[0], "p10", q(.1), "p50", q(.5), "p90", q(.9), "max", d[-1])
print("first before/at create:", sum(1 for x in d if x <= 0), "within 60s after:", sum(1 for x in d if 0 < x <= 60),
      "later:", sum(1 for x in d if x > 60))
print("first_err:", sum(1 for r in w if r.get("first_err")))
print("by month:", collections.Counter(__import__("time").strftime("%Y-%m", __import__("time").gmtime(r["create_ts"])) for r in ok))
json.dump([r for r in w][:6], open("/dev/stdout", "w"), indent=0)
