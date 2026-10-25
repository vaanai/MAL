# P30 lane-G numeric grid: result (exploration only, no edge claim, no book has cleared the promotion gate)

Pre-registered in DECLARE.md (0afb0ec4) before any pricing. Train = explore-0814 only (15 UTC days, 336 hours). Job #1618 count-only, job #1626 priced (code a31168cb, rule sha256 2b19bc59b355...).
Outputs: /data/mal/hunt-1008/push-1016/hunt30/p30-laneg/{count,train}/ (results.json, TABLE.md); full table in TRAIN_TABLE.md.

## Verdict: TRAIN KILL. fresh-0903 was not touched (validation: not run).

* 48 cells declared; 12 are empty by construction (falling-price shapes with net-buy flow >= +10% never occur), 36 cells traded (n from 1,059 to 30,632 per cell; hundreds of trades/day).
* **Mean SOL per trade is negative in all 36 cells under both fail models** (flat 15% and pressure slope 1; 1.9 s, 0.25 SOL, rent 0, strand `total`). Best by min(flat, pressure): `BNC-F1-A1|X2`
  (n 1,059; flat -0.0081, pressure -0.0069 SOL/trade, about -3% of stake; trade-level CI90 LB -0.0112 / -0.0095; 2 of 15 days positive). Cells with mean > 0 under both models: 0; passing the kill filter: 0.
* Totals ex-top-3 and ex-best-day are strongly negative everywhere (a loss spread over all trades, not a few outliers). The kit's own kill (worst-in-slot) also marks every cell killed.
* Selection correction: best cell's rank is moot (nothing positive). White reality check over the 36 traded cells (date-cluster, B = 10,000, seed 1): p = 1.000 flat and 1.000 pressure (max observed mean is negative).
* Capacity line: no survivor, so none. Gross read: these exits on this universe cost roughly 3% to 4% of stake per round trip with no signal from 15/60-min price shape, flow or volume acceleration.

## What this does and does not say
Measured: simple price-shape/flow/volume entries on a lane-G-like universe (V-band pools, age known from the block, not HV4's chain vetoes), minute scan, in August only, with these two exits.
Not measured: October regime, chain/holder/narrative features, discretionary exits, other horizons, pools without a known V. The Haiku arms' numeric baseline on this universe is therefore a loss, not a floor of zero.
