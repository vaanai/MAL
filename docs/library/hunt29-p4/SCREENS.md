# P4 NEW SCREENS results (hunt29, 2026-10-24)

Screen numbers on exploration data only (553 h, four blocks, 25 data days). Not gate evidence; no sentence says a strategy makes money. Pre-declaration DECLARE.md (+AMEND-1), sha256 in DECLARE.sha256 (taken 2026-10-24T00:22:48Z, before any count or price). Kit tools/arena_screen.py @ origin/main 91544819 via run_kit.py, send = 160,000 (meta.stack.send=160000). 1.9 s, 0.25 SOL, rent 0, strand rule total, V band, age >= 360 s. Jobs: counts-only #1429, priced #1445 (mal-research-0, 16 GB, exit 0). d120 dropped unpriced by the 10/day bar (6.2/day).

Multiplicity: 4 ideas x 3 variants = 12 declared, 11 priced. All 11 FAIL, so no winner's-curse adjustment is needed. The causal_probe was not run: no cell is positive under either fail model.

| cell | label | trades/day | flat15 mean % | pressure mean % | flat SOL/day | pressure SOL/day | ex-top3 flat / press (SOL) | days+ flat / press | 1.0 SOL flat / press mean % | impact 0.25 / 1.0 SOL % | exits |
|---|---|---|---|---|---|---|---|---|---|---|---|
| f2 | FAIL | 2744.2 | -2.75 | -2.31 | -18.84 | -15.86 | -473.3 / -398.6 | 0/24 / 0/24 | -2.65 / -2.22 | 0.24 / 0.96 | {'time': 20011, 'tp': 23324, 'sl': 25270, 'trail': 0} |
| f3 | FAIL | 1777.2 | -2.85 | -2.40 | -12.65 | -10.65 | -317.7 / -267.7 | 0/24 / 0/24 | -2.75 / -2.31 | 0.23 / 0.92 | {'time': 9849, 'tp': 16473, 'sl': 18109, 'trail': 0} |
| f5 | FAIL | 970.7 | -3.02 | -2.55 | -7.33 | -6.18 | -184.9 / -155.9 | 0/24 / 0/24 | -2.92 / -2.46 | 0.21 / 0.85 | {'time': 3398, 'tp': 9861, 'sl': 11008, 'trail': 0} |
| w1 | FAIL | 133.5 | -1.39 | -1.43 | -0.46 | -0.48 | -31.0 / -28.2 | 3/24 / 2/24 | -1.29 / -1.33 | 1.29 / 5.16 | {'time': 3162, 'tp': 176, 'sl': 0, 'trail': 0} |
| w6 | FAIL | 80.4 | -0.91 | -1.05 | -0.18 | -0.21 | -21.6 / -19.6 | 2/24 / 2/24 | -0.74 / -0.90 | 1.29 / 5.15 | {'time': 1892, 'tp': 118, 'sl': 0, 'trail': 0} |
| w24 | FAIL | 31.4 | -4.94 | -4.44 | -0.39 | -0.35 | -10.4 / -9.3 | 1/20 / 1/20 | -4.59 / -4.14 | 1.28 / 5.13 | {'time': 734, 'tp': 52, 'sl': 0, 'trail': 0} |
| k10 | FAIL | 23.8 | -4.99 | -4.40 | -0.30 | -0.26 | -8.1 / -7.0 | 5/25 / 3/25 | -4.89 / -4.31 | 0.13 / 0.53 | {'time': 101, 'tp': 0, 'sl': 167, 'trail': 326} |
| k25 | FAIL | 50.5 | -3.81 | -3.71 | -0.48 | -0.47 | -14.1 / -13.1 | 3/25 / 2/25 | -3.62 / -3.46 | 0.09 / 0.35 | {'time': 561, 'tp': 0, 'sl': 307, 'trail': 394} |
| k50 | FAIL | 86.1 | -2.71 | -2.48 | -0.58 | -0.53 | -16.2 / -14.5 | 4/25 / 3/25 | -2.55 / -2.26 | 0.11 / 0.45 | {'time': 1167, 'tp': 0, 'sl': 442, 'trail': 543} |
| d30 | FAIL | 140.6 | -1.79 | -1.54 | -0.63 | -0.54 | -18.9 / -16.2 | 3/25 / 3/25 | -1.66 / -1.41 | 0.37 / 1.48 | {'time': 2904, 'tp': 0, 'sl': 611, 'trail': 0} |
| d60 | FAIL | 21.2 | -1.33 | -1.19 | -0.07 | -0.06 | -3.1 / -2.7 | 6/24 / 6/24 | -1.24 / -1.08 | 0.16 / 0.64 | {'time': 453, 'tp': 0, 'sl': 77, 'trail': 0} |
| d120 | DROPPED (6.2/day, under the 10/day bar) | | | | | | | | | | |

## Reading
- FLUSH (f2/f3/f5): 970 to 2,744 attempts/day, flat -2.75% to -3.02%, 0 of 24 days positive in every cell; stricter is worse. Take profit (+12%) fires on about a third of exits and the 0.92 stop on about 37%: after a stop-out cascade the pool keeps drifting. The fee floor alone (about 2.5%) accounts for the whole loss; there is no bounce edge.
- WAKE (w1/w6/w24): shallow pools, 31 to 134 per day. w6 is the least negative cell of the lane (flat -0.91%, pressure -1.05%) but 2 of 24 days positive and ex-top-3 -21.6 SOL. w24 -4.9%. Take profit +50% fires on 5 to 7% of exits. Impact is 1.3% at 0.25 SOL and 5.2% at 1.0 SOL even with V, so a bigger stake is worse.
- RANKUP (k10/k25/k50): 24 to 86 per day, -2.7% to -5.0%, 3 to 5 of 25 days positive; tighter top-K is worse (the list shows the pool after the move).
- DROUGHT (d30/d60): 141 and 21 per day, flat -1.79% and -1.33%, 3 and 6 of 25 days positive, ex-top-3 negative. No sign of a mechanical buyer; stops fire on 17 to 15% of exits.
- Nothing is positive under either fail model, so no sentence in this lane supports a book. Closest to zero: w6, d60, w1 (all ex-top-3 negative).

## Not run
DIAMOND hands (hunt25 A-HC-h60 already priced, -8.3% at 160k; the library row hunt23-c-7 still says untested and is stale), Mayhem-graduate universe (0 in-band pools in the kit), creator-fee / unlock flows (no tape row), hold-time stratification (descriptive).

## Caveats
Same four blocks as hunt23/24/26/28. FLUSH cost basis is the pool's own tape since the block start (a wallet that bought before the block has no basis and is never a loss-cut seller). WAKE and DROUGHT clocks start at the block start for pools first seen late. Per-attempt pricing with a per-pool cooldown and no concurrency cap.
