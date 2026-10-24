# P14 METEORA DBC IDEAS (lane P14, push 10-24)

Venue: Meteora Dynamic Bonding Curve (program dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN). Tape: P12, tools/venue_backfill_dbc.py, exploration hours
[2026-10-14T00, 2026-10-17T00) (72 hours, 3 UTC days). Reserved holdout [10-17T00, 10-24T00): never fetched or opened here (code refuses it).
Curve: constant liquidity in sqrt-price space; fees per trade, per config (89% of trades 25 bps, young pools up to ~3%). Tape rows carry pool, config, signer, side, amounts,
fees, post sqrt price, quote_reserve_post and migration_threshold. Progress p = quote_reserve_post / migration_threshold (0..1, config agnostic).

Hypothesis common to all (one sentence, not a claim): DBC round-trip fees are about 0.5% (25 bps a side) instead of ~2.5% (pump.fun / LaunchLab), so a rule that was near zero
on those venues because of fees might not be negative here at MAL's 1.9 s latency. Counter-hypothesis kept in view: the fee is low because the pools are bot-dominated
(many wash-looking 0.1 SOL round trips by the same few signers), which would leave nothing for a 1.9 s follower.

Common universe U (row level): SOL-quoted pools; all swap modes stay in the price chain, but a POOL containing any transfer-hook or EvtSwap3 row is excluded from signals
(not validated); config fee mode known; migration_threshold >= 5 SOL (spam pools with tiny thresholds are out); a signal is armed only if the state after the signal print lets
a 0.25 SOL buy move the spot by at most 2% (capacity bar), entry progress p <= 0.85, one warm-up hour (no signal before 2026-10-14T01), and the pool has not completed its curve.

## D1 EARLY-MOMENTUM
Trigger on a BUY print with post p in [0.02, 0.35]: within the last 60 s of the pool, buys >= N, net buy SOL (buy quote in minus sell quote out) >= S, and the post-trade spot is up
between +5% and +40% from the window's first pre-trade spot. Variants (N, S): m4 = (4, 1.0 SOL), m6 = (6, 2.0), m10 = (10, 4.0). Exit: sl 0.90, trail 0.90 on the running peak
(peak starts at the decision spot), time 600 s. Cooldown 600 s per pool.

## D2 MILESTONE
Trigger on a BUY print that is the first in its pool to cross progress milestone m (pre-trade p < m <= post-trade p), with net buy SOL >= 1.0 over the last 120 s. Variants: ms30, ms50, ms70.
Exit: tp 1.15, sl 0.90, time 900 s. One look per pool per milestone (a failed flow condition at the first crossing is not retried).

## D3 BIG-BUY FOLLOW
Trigger on a BUY print with quote in >= M SOL whose own price impact is between +2% and +25% and post p in [0.02, 0.80]. Variants: bb1 (1 SOL), bb2 (2 SOL), bb4 (4 SOL).
Exit: sl 0.90, trail 0.90, time 300 s. Cooldown 120 s per pool.

## D4 CAPITULATION BOUNCE
Trigger on a SELL print with post p >= 0.05: within the last 30 s, sell prints >= K, sell quote out >= S, and the post-trade spot is down >= d from the window's highest post-trade
spot (window peak includes the pre-trade spot of the first print in the window). Variants (K, S, d): c3 = (3, 1.0, 10%), c4 = (4, 2.0, 15%), c6 = (6, 4.0, 20%).
Exit: tp 1.10, sl 0.92, time 180 s. Cooldown 120 s per pool.

(D1 to D4 are the four P11 LaunchLab rules re-expressed on the DBC curve: P11 priced them all FAIL at ~1.25% per side; this lane asks only whether 0.25% per side changes that.)

## Rate bar
>= 3 attempts/day averaged over the 3 exploration days after cooldown (counts first; a cell below the bar is dropped unpriced and said so). 4 ideas x 3 variants = 12 cells; nothing else
is tried; no parameter changes after a count.

## D5 COMPLETION HOLD (post curve_complete) — NOT PRICED (tape lacks it)
On curve_complete the pool migrates to Meteora DAMM v2 (a different program). The DBC tape has the curve_complete row (6,541 in 3 days, about 2,180/day) but NOT the post-migration
pool's swaps. A FLOOR-style hold after completion needs: (1) the migrate transaction per completed pool (signature on the `ix` rows) -> getTransaction (jsonParsed, accounts +
inner instructions) -> the DAMM v2 pool address; (2) getTransactionsForAddress(pool) for 24 h + 1 h from migration, (3) a DAMM v2 swap decoder (not written). Cost unknown until a
5-pool probe (~5,000 credits assumed at 50/page, 10 per getTransaction). Not counted or priced here. If fetched, it needs its own DECLARE.

## Capacity count (asked by the manager)
Trades per UTC day (SOL-quoted, all rows) in a pool state where a 0.25 SOL buy would move the spot by <= 2% (computed from the state after each print, own fee included), reported next to
the all-trades count, by threshold band. Counts only; no price.
