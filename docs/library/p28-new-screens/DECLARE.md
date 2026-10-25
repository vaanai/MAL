# P28 new screens: DECLARE (counts only, before any count was run)

Written 2026-10-25 before `tools/p28_counts.py` ran on any hour. Desk + counts only. No price, no outcome, no P&L is read or computed.
Nothing here says a book makes money; no book has cleared the promotion gate.

Blocks (exploration pool per docs/HOLDOUT_LEDGER.md; read through `/data/mal/clean-view` and `tools.arena_screen.check_block_path`, never a sealed hour):
- `explore-0814`: 2026-08-24T12 to 2026-08-28T12 (96 h, workers w2+w1).
- `fresh-0903`: first 96 sorted hours (spent confirmation block, now exploration pool).
Not touched: arena tape contents, forward-1016, meta-capture, any HV4/Q50/HV6/DBC output.

Universe for every cell: WSOL PumpSwap rows with V in [17.5e9, 17.7e9] lamports (kit map `pool_v_0814`, then `pool_v_0909`).
Age gate: pool first seen in the block at least 7,200 s before the decision (so the first 2 h of a block never trigger).
Depth: Qeff = (quote_reserve PRE-trade + V) / 1e9 SOL. One-way entry impact at stake s = s / (Qeff + s). Reported share of triggers with
one-way impact <= 1.5% (about 3% round trip) at s = 0.25, 0.5, 1.0.

Cells:
- PRESET (`pre_r1`): >= 3 distinct traders each with a BUY whose sol_lamports is an exact multiple of 1e8 (0.1 SOL) inside 60 s on one pool; cooldown 300 s.
  Base-rate check: share of in-universe buys that are an exact multiple of 1e6 lamports. KILL: share <= 0.2% (twice the 0.1% chance level) means the app-preset signature is not on the tape.
- CALL-SYNC (`cs_k5`, `cs_k8`, `cs_k12`): in one aligned 2-slot bucket (slot // 2), >= K distinct buying traders and >= 2.0 SOL gross buys; cooldown 300 s per pool.
- SIZE-SKEW (`ss_r3`, `ss_r5`): over the last six 300 s bins (30 min) of a pool: >= 10 buys and >= 10 sells, mean buy size / mean sell size >= 3 (or 5); checked at each bin end; cooldown 1,800 s.
  Mirror count `ss_veto` (ratio <= 1/3) reported as a veto flag count only.
- GRAD-GLUT (`glut`): per UTC hour, number of pools whose first block print is in the universe band (first 24 h of a block excluded). Report the hourly series
  summary and the count of hours >= 2x and <= 0.5x the trailing 24 h median.

Bars (applied to the counts only):
- ADVANCE to a frozen-rule pricing step: >= 10 triggers per day per cell on both blocks, and >= 40% of triggers with one-way impact <= 1.5% at 0.25 SOL.
- PARK: fewer than 10 per day, or impact share below 40%.
- A priced step, if any, needs its own DECLARE, QP review and the kit's pressure/flat fail models; it is in-sample on exploration data, never gate evidence.
