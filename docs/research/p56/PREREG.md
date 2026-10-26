# P56 pre-statement (written before any hypothesis was priced)

Written 2026-10-26 (`date -u` at write time recorded in the commit). Exploration only. Not a registration, not a promote.

## Data
`/data2/mal/clean-view/fresh-0903/w{1,2,3}/trades` = `[2026-09-03T12, 2026-09-09T12)`. HOLDOUT_LEDGER: spent by EXP-012's read 2026-10-01 and moved to the exploration pool. No October hour, no arena tape, no meta-capture, no DBC, no HV4/Q50 output is touched. Filter: venue pumpswap, quote wSOL, mint ends `pump`. Market cap = quote_SOL x 1e9 / base_UI, from the print's PRE-trade reserves (PumpSwap convention).

## Hypothesis family (one rule, one cell, no grid)
Momentum continuation on deep cheap-tier pools. Cited family: lib-c-L4.5 "deep-pool-cheap-tier" (parked; deep whale pools at 3,000 ppm lost -0.969% per trade) and lib-c-Q2.14. Honest note: the library has no row that died *mainly* on fees (most died on gross edge, see lib-a-A4, lib-b-C5). This is the closest "fees were the suspect" family, and the rule is the simplest signal that has a gross-edge test.

- Universe at the signal print: pre-trade mcap >= 9,820 SOL (tier index >= 6, fee <= 0.95%) and pool quote depth >= 400 SOL.
- Signal: price now / price at the last print at least 150 slots (about 60 s) ago - 1 >= +3%. Per-pool cooldown 1,500 slots.
- Entry: first print with slot >= signal slot + 8 (feed delay D = 7 slots plus one, DEC-031 style). Hold: first print with slot >= entry slot + 150.
- Stake 0.25 SOL (0.1 and 0.5 reported as sensitivity only). Fill: constant product on the print's pre-trade reserves with our own trade applied to the exit reserves; buy fee on input, sell fee on output, fee ppm from `tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` at each leg's mcap. Send fee 160,000 lamports per leg (DEC-031 Am.8).
- Control, same signals: the same trades with a flat 12,500 ppm fee on both legs (what a flat-fee sim would charge). The fee-tier effect is the difference.
- Fail model: flat 15% (fill fails, one send fee lost). The pressure-fail model is NOT implemented here.

## Kill lines
1. KILL if fewer than 3 candidate entries per UTC day.
2. KILL if the lower 90% CI bound of mean net SOL per trade (1,000 bootstrap draws, seed 1, 5th percentile) is <= 0 under the flat-15% model. Because the pressure model is harsher, a flat-model failure kills both; a flat-model pass would still need the pressure model and every other gate item and is only CONTINUE-to-QP.
3. Report total after removing the top 3 trades and per-day sign. A positive in-sample mean here is a hypothesis only.
