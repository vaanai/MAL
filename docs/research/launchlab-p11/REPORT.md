# P11 LaunchLab screens (2026-10-24)

Screen numbers on exploration data only, not evidence. Holdout hours 2026-10-17T00..10-24T00 never opened. No sentence here says a strategy makes money.

## Curve and fee (measure job #1465, 241 hours 10-07..10-16, 1,222,631 trade rows, 110,673 pools)
- Constant product with virtual reserves: in = virtual_quote + real_quote, out = virtual_base - real_base. Buy: fee off amount_in, then base_out = out*net/(in+net). Sell: gross = in*base/(out+base), fee off gross.
- Reproduces 99.89% of 626,741 exact-in buys and 99.98% of 594,807 exact-in sells to the atom (99.96% / 100% within 1). 1,083 exact-out rows not checked.
- Fee: median 1.25% per side (buy and sell), top rates 1.25%, 1.5%, 1.05%, tail to 5.75%. Round trip about 2.5% before impact (pump.fun about 2%).
- Curve parameters are not one set (85,626 triples). Standard: 30.0009 SOL virtual, 1.073e15 base, 793.1e12 for sale (migration ends near 85 SOL real quote). Other pools: virtual quote 9e13..1.3e14, 0.9 SOL, 8 SOL; some with real quote in the millions of SOL. The tape has no quote mint, so non-SOL quotes cannot be separated; universe = total_base_sell 793.1e12 and virtual_quote 20e9..40e9.
- Slot time 0.22 s (4.5-5 slots/s): the 1.9 s leg is about 9 slots. Migrate ix rows: 264 in 241 h (~26/day); create rows 10,653.

## Screens, block A (10-07..10-16, 10 days), deciding leg 1.9 s / 0.25 SOL / rent 0 / 160k sends
Attempts per day are after cooldown. Means are SOL per trip. Every cell: ex-top-3 and ex-best-day negative under both fail models, 0 stranded.
| cell | n | /day | flat mean | pressure mean | pos days (flat) | label |
|---|---|---|---|---|---|---|
| m4 | 572 | 57 | -0.0085 | -0.0072 | 0/10 | FAIL |
| m6 | 351 | 35 | -0.0068 | -0.0057 | 4/10 | FAIL |
| m10 | 148 | 15 | -0.0064 | -0.0052 | 3/10 | FAIL |
| ms30 | 277 | 28 | -0.0101 | -0.0083 | 1/10 | FAIL |
| ms50 | 109 | 11 | -0.0013 | -0.0007 | 5/10 | FAIL |
| ms70 | 52 | 5 | -0.0012 | -0.0015 | 3/10 | FAIL |
| bb1 | 1323 | 132 | -0.0055 | -0.0046 | 1/10 | FAIL |
| bb2 | 462 | 46 | -0.0021 | -0.0016 | 4/10 | FAIL |
| bb4 | 66 | 7 | -0.0076 | -0.0050 | 2/10 | FAIL |
| c3 | 683 | 68 | -0.0104 | -0.0091 | 0/10 | FAIL |
| c4 | 504 | 50 | -0.0100 | -0.0087 | 0/10 | FAIL |
| c6 | 357 | 36 | -0.0117 | -0.0103 | 0/10 | FAIL |
Sensitivities (flat mean): 1.0 SOL stake worse in every cell (e.g. ms50 -0.0046); rent charged and 3.4 s exit lag change signs only for ms50 under the lag (+0.0016, no other test passes). Block B (09-24..10-06) not priced: no cell survived A.

## Open
- I5 GRAD-FLOOR not priced: no CPMM tape; fetch spec in IDEAS.md (probe 5 pools first, <= ~5,000 credits).
- The backfill still writes 09-24..10-06; those days are unread.
- No haircut applied (none measured on this venue), no trader/mint on the tape, fee ladder per pool is last-observed.
