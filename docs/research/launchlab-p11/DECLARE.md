# P11 DECLARE (written before any count or price)

Window: exploration hours [2026-09-24T00, 2026-10-17T00) of /data2/mal/venue-tape-backfill/launchlab/ only. Hours [2026-10-17T00, 2026-10-24T00) are a RESERVED holdout:
the code refuses those paths; only file counts are ever taken. No pump/PumpSwap data is used here.

Pricing (tools/launchlab_price.py, tools/launchlab_screen.py): decision at the signal print's slot; entry lands on the state after all prints with slot <= decision + k,
k = round(1.9 s / slot_s) (slot_s per UTC hour from the tape's own slot/block_time, 0.4 s fallback); exits land the same 1.9 s after their decision (a harsher 3.4 s
exit sensitivity is reported only). Curve: constant product with virtual reserves; buy fee taken from amount_in, sell fee from gross output; fee rates are the pool's last
observed rate (measured from the tape). Our own impact is applied at stake (0.25 SOL deciding, 1.0 SOL also priced), our buy re-injected into the exit state.
Slippage guard: exec price / decision spot > 1.15 = not filled, one send paid. Sends 160,000 lamports each, 2 per filled trip, rent 0 deciding (assumption: the sell closes the
token ATA and refunds it; the rent-charged leg 2,039,280 lamports per filled trip is reported beside it). Fail models: flat 15% and pressure (slope scale 1, intercept refit per cell, mean p 0.289),
via tools.arena_stack.fail_legs. Strand: a position whose sell lands past the pool's last on-curve print after a migration is priced stranded (stake + buy send + rent lost); reported counts.
Stats: mean SOL/trip, total, ex-top-3, drop-best-day, per UTC day, trade-level CI90 lower (1,000 draws, seed 1, 5th pct) and date-clustered CI90 lower. Gate numbers (n >= 100, >= 5 days
majority positive, CI lo > 0, ex-top-3 > 0) are reported as exploration only; no sentence says a cell makes money.

Cells: the 12 in IDEAS.md (I1 m4/m6/m10, I2 ms30/ms50/ms70, I3 bb1/bb2/bb4, I4 c3/c4/c6). Counts first; < 3 attempts/day average = dropped unpriced.
Labels (applied after pricing, not tuned): SCREEN-PASS-CANDIDATE = both fail models mean > 0, ex-top-3 > 0, ex-best-day > 0, trade-level and date CI90 lo > 0, n >= 100, >= 5 dates majority positive, at 1.9 s / 0.25 SOL / rent 0; MARGINAL =
positive mean and ex-top-3 on both fail models but one of n / CI / days short; otherwise FAIL.

## AMENDMENT 1 (2026-10-24, after the MEASURE step on 241 exploration hours 10-07..10-16, before any attempt count or price)
Measured (job #1465, measure.json): the constant-product curve with the fee taken from input (buy) / gross output (sell) reproduces 99.89% of 626,741 exact-in buys and 99.98% of 594,807 exact-in sells EXACTLY
(integer), 99.96% / 100% within 1 atom. Fee rate is not 1.5%: median buy 1.25%, median sell 1.25%, tail up to 5.75% (per-pool, kept as the pool's last observed rate). Curve parameters are NOT one set:
85,626 distinct (virtual_quote, virtual_base, total_base_sell) triples; the standard (30.0009 SOL, 1.073e15, 793.1e12) is the most common, others have virtual_quote 9e13-1.3e14 or 0.9-8 SOL, and some pools show real quote
in the millions of SOL (non-SOL quote atoms or test pools). The tape has no quote mint, so the quote asset is unknown. Slot time measured 0.22 s (4.5-5 slots/s), not 0.4 s: k(1.9 s) = ~9 slots.
Therefore the universe is narrowed (a universe definition, not a rule tuning): total_base_sell within 1e9 atoms of 793.1e12 AND virtual_quote in [20e9, 40e9] atoms. A non-SOL quote with a similar atom magnitude cannot be excluded; said in the report.
Slot time fallback range corrected to [0.1, 0.6] s (the 0.3 floor in the first code was wrong for this tape). The priced run uses the FULL exploration window once the backfill has written it (one pricing look, not a partial-window look); counts on the partial window are counts only.
