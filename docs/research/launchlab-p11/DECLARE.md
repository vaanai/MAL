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
