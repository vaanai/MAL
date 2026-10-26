#!/usr/bin/env python3
"""Stats for momo_price.py output. Usage: momo_stats.py IN.json STAKE_SOL
Per-trade net in SOL. Flat-15% fail: expected net = 0.85*net - 0.15*SEND/1e9 (fill fails, one send fee lost).
Bootstrap: 1,000 draws, seed 1, 5th percentile of the mean."""
import json, sys, random, collections, statistics as st

d = json.load(open(sys.argv[1]))
S = float(sys.argv[2])
SENDSOL = 160_000 / 1e9
rows = d["rows"]
print("signals/day", d["signals_by_day"], "unclosed", d["unclosed"], "closed trades", len(rows))


def fail(x):
    return 0.85 * x * S - 0.15 * SENDSOL


def boot(v):
    rnd = random.Random(1)
    n = len(v)
    m = sorted(sum(v[rnd.randrange(n)] for _ in range(n)) / n for _ in range(1000))
    return m[49]


for key in ("net_tier", "net_flat"):
    gross = [r[key] * S for r in rows]          # SOL per trade after fees/impact/send, no fail
    v = [fail(r[key]) for r in rows]           # flat-15% model
    top3 = sorted(v)[:-3]
    days = collections.defaultdict(float)
    for r, x in zip(rows, v):
        days[r["day"]] += x
    print(key, "n", len(v), "mean SOL (no fail)", sum(gross) / len(gross), "| flat15 mean", sum(v) / len(v),
          "CI90 lo", boot(v), "| total", sum(v), "ex-top3", sum(top3),
          "| days+", sum(1 for x in days.values() if x > 0), "/", len(days))
tier_cheap = [r for r in rows if r["fi"] < 0.0125]
print("fee in: min/median/max ppm", min(r["fi"] for r in rows), st.median(r["fi"] for r in rows), max(r["fi"] for r in rows))
print("median entry quote depth SOL", st.median(r["q0"] for r in rows), "median mcap", st.median(r["mc"] for r in rows))
g = [(r["net_tier"] + 0) for r in rows]
print("median net_tier %", 100 * st.median(g), "share net_tier>0", sum(1 for x in g if x > 0) / len(g))
# gross-edge proxy: net_flat - net_tier recovers fee delta; zero-cost gross from net_tier + fees
rt = [r["fi"] + r["fo"] for r in rows]
print("mean round-trip fee (tier)", sum(rt) / len(rt), "vs flat 0.025; mean fee saving", 0.025 - sum(rt) / len(rt))
print("mean gross-before-fee approx (net_tier + rt fee + send)", sum(r["net_tier"] + r["fi"] + r["fo"] for r in rows) / len(rows) + 2 * SENDSOL / S)
