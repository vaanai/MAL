# P16 DECLARE: DBC graduations (strand re-pricing + D5 post-graduation hold), written before any pricing

Counts that preceded this file: strand list job #1491 (attempt and `ended` counts of the frozen P14 cells, no price); probe of 5 migrated pools (1,250 assumed credits) used only to validate the decoder and
the pool account index. The fetch job #1492 was submitted before this file; it prices nothing. No DAMM price, no P&L of any kind has been computed.

Data: exploration hours only (DBC [2026-10-14T00, 2026-10-17T00)); DAMM v2 history of each migrated pool from its migration to min(+24 h, 2026-10-17T00). The holdout directory
meteora_dbc_holdout/ is never opened (tools.dbc_price refuses it; the DAMM tool refuses migrations outside the window). Screen only, 3 UTC days: never a gate.

Pool identification (validated on real txs): DAMM `initialize_pool_with_dynamic_config` inner instruction, pool = accounts[7] (= DBC MigrationDammV2 accounts[4]); accounts[6] is the constant
pool authority. Swap events: EvtSwap2 self-CPI, 180-byte payload; curve reproduced (first swap from EvtInitializePool sqrt price and liquidity to 1e-7; swap-to-swap L ratio within 1% for 97%+).

## (i) Strand re-pricing (the 6 P14 D1/D2 cells m4 m6 m10 ms30 ms50 ms70; rules, universe, latency 1.9 s, stake 0.25 SOL, guards: unchanged, no re-tuning)
A position is "stranded" exactly as P14 defines it (`ended`: curve completes at or before the slot the sell would land). New rule: it is NOT lost; it continues in the migrated DAMM v2 pool:
- State carried at completion: entry decision spot `ref`, running peak (max of ref and on-curve post-print spot after the buy landing, up to completion), tokens held (P14 buy_fill), time cap = decision time + cell hold_s.
- On DAMM the same exit rule runs on post-print spots of the pool in fetch (execution) order, prints from the migration slot on: tp (price >= ref*tp), sl (<= ref*sl), trail (<= running peak*trail, peak keeps running),
  cap at entry decision + hold_s. If the rule already fired on the curve before completion (the sell was sent and stranded), the exit decision on DAMM is the first print at/after the migration slot, and that
  failed sell is charged as an extra 160,000-lamport send. Cap already passed at migration: decision at the first DAMM print.
- Price units: DBC and DAMM spots are both quote atoms per base atom ((S/2^64)^2) so ratios carry over; the DAMM price jump from the last curve price to the first DAMM print is reported, not corrected.
- Landing: k = round(1.9 s / slot_s of that hour) slots after the decision slot (P14 slot_s). Exit state = after the last DAMM print with slot <= landing slot. Sell: constant-L (L from the carried implied L of the
  pool, last trade with quote leg >= 0.01 SOL, init liquidity before the first), fee = pool's last observed sell-side rate (fee/(out+fee)); own impact via sell_fill; 160,000 lamports send; rent 0 (as P14).
- Pools with no usable DAMM data (not fetched because the cap was reached, truncated before the exit, WSOL on side A, decode error): keep the P14 full-loss rule and are COUNTED and reported; a bracket
  (all stranded = full loss, as P14) is printed beside the re-priced line. Non-stranded attempts keep their P14 numbers exactly (reproduce screens_a2fix.json first; mismatch > 1e-6 SOL stops the run).
- Fail models: flat 15% and pressure (slope scale 1, intercept refit on the cell's fills), tools.arena_stack.fail_legs with the P14 entry features (ssb, nearby). Labels as P14 (MARGINAL = both means > 0, ex-top-3 > 0, ex-best-day > 0).

## (ii) D5 post-graduation hold (new cells; each migrated pool gets at most ONE attempt)
Universe U5: migrated pool with WSOL as token B, decoded history present, EvtInitializePool state known, collect_fee_mode known, a 0.25 SOL buy at the entry state moves the spot <= 2% (own fee included),
entry state has L finite, no failed-decode flag. Entry: decision at the migration slot (the MigrationDammV2 tx slot); the buy lands k = round(1.9 s / slot_s) slots later on the state after the last print with slot <= that
(the pool's initial state from EvtInitializePool if there is none). Reference price for tp = decision spot (state at the decision slot). Buy: stake 0.25 SOL, per-trade pool fee = pool's last observed buy-side rate
(first observed rate of any side if none; fallback 1%), fee side by collect_fee_mode (0 output, 1 input), own impact, guard exec/spot > 1.15 = not filled (one send paid), 2 x 160,000 sends, rent 0, own buy re-injected
into the exit state (same pool). Exit lands k slots after the fire slot; sell fee = last observed sell-side rate.
| cell | tp | sl | trail | time cap |
|---|---|---|---|---|
| g1 FLOOR-v2-like | +50% | none | none | 23 h (24 h is not coverable: the history ends at +24 h and the sell must land inside it) |
| g2 | +25% | none | none | 6 h |
| g3 time-only | none | none | none | 1 h |
An attempt whose exit landing would pass min(migration + 24 h, 2026-10-17T00) is refused (`block_edge`/`history_end`, counted; this removes most of 10-16 for g1). Attempts per day below 3 on average: dropped unpriced.
Same fail models, stats, labels and ex-top-3 / ex-best-day as P14. Per day reported (3 days only: screen, never gate). No haircut for sniper competition at migration (none measured): optimistic by an unknown amount.

## Multiplicity
P14's 12 cells + 3 D5 cells = 15 priced cells; the strand re-pricing re-uses 6 of the 12 (a second price of the same cells under the new strand rule, not new cells; the P14 full-loss line and the P14
last-price bracket remain printed). One pricing run after all fetched data exists; no re-tuning; diagnostics added after the first look are labelled as such.
Declared at: 2026-10-24T06:20:15Z

## Erratum 1 (before any pricing)
The fetch order puts the 682 strand pools (pools where a P14 D1/D2 position was open at completion) first, so those pools are a selected, hot subset. D5 PRIMARY sample = migrated pools that are NOT strand
pools (fetched in sha256(pool) order, a uniform subset of non-strand migrations; the share of all non-strand migrations covered is printed). D5 on all fetched pools (strand pools included) is printed beside it as a sensitivity and never changes a label.
Erratum written at: 2026-10-24T06:52:08Z

## Erratum 2 (pricing run #1511 was a loader bug; no P&L was read)
Run #1511 (kit sha in its grad.json) used a sanity check that compared the first swap's implied liquidity with the EvtInitializePool liquidity; the migration adds a second position, so the pool's liquidity at the
first swap is about 2x the init event's, and 152 of 200 sampled strand pools were wrongly set to `no_data` (D5 attempts 4-5). Only the printed counts were looked at; grad.json of #1511 was not opened. Fix: the
sanity is now quote-leg vs base-leg liquidity agreement of the first swap, and the pre-first-swap liquidity is the first swap's implied L. No rule, parameter or universe changed. The second run is the priced run.
Erratum written at: 2026-10-24T07:26:19Z
