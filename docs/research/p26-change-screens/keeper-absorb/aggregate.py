"""Aggregate counts_w{1,2,3}.json (hour -> counts) into per-day and per-hour-of-day tables. Counts only."""
import json, collections, statistics, sys

d = {}
for w in ("w1", "w2", "w3"):
    d.update(json.load(open(f"counts_{w}.json")))
hours = sorted(d)
print("hours", len(hours), hours[0], hours[-1])
day = collections.defaultdict(lambda: collections.Counter())
dayh = collections.Counter()
hod = collections.defaultdict(lambda: collections.Counter())
res_pass = collections.defaultdict(list)
for h in hours:
    v = d[h]; dd = h[:10]
    dayh[dd] += 1
    for k in ("trades", "sells", "clusters", "clusters_pass"):
        day[dd][k] += v[k]; hod[h[11:]][k] += v[k]
    for ven, n in v["clusters_by_venue"].items():
        day[dd]["cl_" + ven] += n
    for ven, n in v["sells_by_venue"].items():
        day[dd]["sell_" + ven] += n
    day[dd]["sell_mints_hoursum"] += v["sell_mints"]
    day[dd]["cluster_mints_hoursum"] += v["cluster_mints"]
    for ven, q, ok in v["cluster_reserves"]:
        res_pass[dd].append((ven, q, ok))
print("\nper UTC day (hours present) : trades sells sell% clusters clusters_pass | cluster venues | sell venues")
rate = {}
for dd in sorted(day):
    c = day[dd]; hh = dayh[dd]
    print(dd, hh, c["trades"], c["sells"], f"{100*c['sells']/c['trades']:.1f}%", c["clusters"], c["clusters_pass"],
          {k[3:]: v for k, v in c.items() if k.startswith("cl_")}, {k[5:]: v for k, v in c.items() if k.startswith("sell_") and k != "sell_mints_hoursum"})
    if hh == 24:
        rate[dd] = c["clusters_pass"]
print("\nfull-day clusters_pass:", rate, "median", statistics.median(rate.values()) if rate else None)
print("\nper hour of day (sum over 6 days): sells clusters clusters_pass")
for h in sorted(hod):
    print(h, hod[h]["sells"], hod[h]["clusters"], hod[h]["clusters_pass"])
# reserves of clusters
allr = [q for dd in res_pass for _, q, _ in res_pass[dd]]
allr.sort()
n = len(allr)
if n:
    print("\ncluster quote reserve SOL quantiles (n=%d): p10 %.1f p50 %.1f p90 %.1f" % (n, allr[n//10], allr[n//2], allr[9*n//10]))
    print("share passing bar: %.3f" % (sum(1 for dd in res_pass for _, _, ok in res_pass[dd] if ok) / n))
    pv = collections.Counter(v for dd in res_pass for v, _, ok in res_pass[dd] if ok)
    print("passing by venue", dict(pv))
