# P22 REPORT: p20-wick-bid-zero-latency latency-0 bound, DEAD

MiScusi job #1532 (24 GB, 10 min, script sha in DECLARE Amendment 1), exploration blocks explore-0814, fresh-0903, exp011-0909 (28 UTC days), 0 Helius credits. Full table: TABLE.md, summary.json. Censored (no exit print): see censored.json.

Rule declared in advance: DEAD if the pooled flat-15% net mean is <= 0 in D8-300 and D15-300 at 0.1 and 0.25 SOL.

Pooled, net (25 bps each way, 505,000-lamport sends), flat 15%, mean SOL per trade (the latency-0 UPPER BOUND, not a fill model):

| cell | stake | n | days | days positive | mean | CI90 lo | ex-top-3 total | ex-best-day total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D8-300 | 0.10 | 336,648 | 28 | 0 | -0.02075 | -0.02097 | -7004.7 | -6945.3 |
| D8-300 | 0.25 | 336,648 | 28 | 0 | -0.05049 | -0.05102 | -17042.9 | -16916.3 |
| D15-300 | 0.10 | 87,282 | 28 | 0 | -0.03158 | -0.03201 | -2770.5 | -2733.0 |
| D15-300 | 0.25 | 87,282 | 28 | 0 | -0.07756 | -0.07864 | -6804.8 | -6715.5 |

All four are negative, so the idea is DEAD under the declared rule. Every one of the 120 summary rows has a negative mean, gross included (largest, least negative: -0.0132 SOL/trade); every block is negative; 0 positive days in the pooled rows. Pressure model results are in TABLE.md and are slightly less negative (e.g. D8-300 0.1: -0.0192) but still negative. Caveats: events in one pool overlap and each counts as a trade, so n and the CIs overstate independence; the totals are sums of trades, not a book. Even a perfect zero-latency fill at the sell's post-trade price loses, because a large single sell on PumpSwap is followed on average by further decline (drains), which a resting bid would also absorb.
