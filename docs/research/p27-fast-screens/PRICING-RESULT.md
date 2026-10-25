# P27 pricing result (job #1589, explore-0814, in-sample; rule frozen in PRICING-DECLARE.md at 9f20d402)

BC-LATE: NOT PRICED. Parked as the dead pre-graduation bonding-entry family (G1 r7, A16, A5, I3).
SERIAL-CREATOR: KILLED by all three kill rules under both fail models. Not an edge; no book has cleared the promotion gate.

| | flat 15% | pressure-fail slope 1 |
|---|---|---|
| n filled (of 43,059 decisions; 14,962 unfilled) | 28,097 | 28,097 |
| mean SOL/trade (0.25 SOL stake) | -0.01257 | -0.01046 |
| CI90 lower bound (1000 draws, seed 1) | -0.01425 | -0.01178 |
| total SOL / ex-top-3 | -353.12 / -361.56 | -293.82 / -301.37 |
| positive days of 13 / ex-best-day total | 0 / -344.75 | 0 / -286.00 |
| trades/day x net edge x 0.25 SOL | 2161 x -0.01257 = -27.2 SOL/day | 2161 x -0.01046 = -22.6 SOL/day |
| placebo (progress-matched non-serial, n 28,097) mean, CI90 lo | -0.01368, -0.01496 | -0.01128, -0.01229 |
| serial minus placebo, mean, CI90 lo | +0.00112, -0.00093 | +0.00082, -0.00078 |

By-day (flat, n / SOL): 08-15 1569/-20.27; 08-16 1701/-28.78; 08-17 1585/-8.37; 08-18 2010/-14.93; 08-19 2716/-31.45; 08-20 2634/-33.27; 08-21 2580/-21.16; 08-22 2095/-10.91; 08-23 1670/-30.21; 08-24 1733/-32.48; 08-25 2212/-29.14; 08-26 2572/-38.11; 08-27 3020/-54.03. Pressure by-day is in serial_result.json.
Caveats: 242 trades exited at the final curve state after completion (PumpSwap exit not modelled); 116 placebo cells reused a single candidate; 14,962 serial decisions never filled (no curve print >= 5 slots later, mostly the curve ended or went quiet); send cost 0.0001 SOL is an assumption; flag history is left-censored at the pool start.
