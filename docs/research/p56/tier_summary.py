#!/usr/bin/env python3
"""Summarise tier_counts.py output: per-day averages by tier group. Usage: tier_summary.py IN.json"""
import json, sys

d = json.load(open(sys.argv[1]))
days = sorted({int(k.split("|")[0]) for k in d})
n = len(days)
print("UTC days (incl. partial edge days):", n)
names = {0: "T0 0-420 (1.25%)", 1: "T1 420-1470 (1.20%)", 2: "T2-5 1470-9820 (1.15-1.00%)",
         3: "T6-15 9820-58940 (0.95-0.50%)", 4: "T16+ >=58940 (<=0.475%)"}
grp = {}
for k, v in d.items():
    day, t = map(int, k.split("|"))
    g = 0 if t == 0 else 1 if t == 1 else 2 if t <= 5 else 3 if t <= 15 else 4
    a = grp.setdefault(g, [0, 0, 0, []])
    a[0] += v[0]; a[1] += v[1]; a[2] += v[2]; a[3] += v[3]
for g in sorted(grp):
    a = grp[g]; dp = sorted(a[3])
    print(names[g], "| trades/day", round(a[0] / n, 1), "| SOL vol/day", round(a[1] / 1e9 / n, 1),
          "| pool-days/day", round(a[2] / n, 1),
          "| quote depth SOL p10/p50/p90", round(dp[len(dp) // 10], 1), round(dp[len(dp) // 2], 1), round(dp[9 * len(dp) // 10], 1))
for day in days:
    print(day, {g: sum(v[2] for k, v in d.items() if int(k.split("|")[0]) == day and ((0 if int(k.split("|")[1]) == 0 else 1 if int(k.split("|")[1]) == 1 else 2 if int(k.split("|")[1]) <= 5 else 3 if int(k.split("|")[1]) <= 15 else 4) == g)) for g in range(5)})
