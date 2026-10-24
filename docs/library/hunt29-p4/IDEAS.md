# P4 NEW SCREENS desk note (push 10-24, manager15), written BEFORE any count or price

Exploration only. Screen numbers on spent exploration blocks; nothing here is gate evidence, no sentence says a strategy makes money.
Each idea: WHY (who is forced to trade against us, or what slow information we exploit at 1.9 s), why it is not a repeat, expected rate (a guess, to be replaced by the counts-only job).

Facts that bind (memory + library): PumpSwap tape reserves PRE-trade; V ~ 17.58 SOL in the pricing; MAL entry 1.3-1.9 s (+7 feed slots); fresh-pool longs fail; deep aged pools were gross-negative (survivors-*, deep-pools-*, hunt23 a-slow, day-sigma: -3% to -20% per trade); hunt28 L4 (BREADTH, ACCUM, REVIVE, SELLABS) all FAIL at 160,000/send.
The fee floor is ~2.5% round trip plus impact, so a long needs a selection that is worth more than ~3% gross over its hold.

## Considered and NOT picked (checked against docs/library)
- DIAMOND hands at pool age 2 h (hunt23-c-7): the same rule was priced as hunt25 A-HC-h60 and re-priced at 160k (reprice-160k-hc-h60: flat deciding mean about -8.3%, ex-top-3 negative). The library row hunt23-c-7 still says "untested"; that row is stale. Repeat, dropped.
- Mayhem graduates as a universe (w2-mayhem-grads-1): the kit's V band (17.5e9-17.7e9) has 0 in-band Mayhem pools and the price convention was not trusted (step 2 STOP). Cannot be priced by the kit. Not run.
- Creator-fee claims, unlock releases, Graduation-85 flows: not observable in the PumpSwap trade rows (no claim/unlock row), or already inside H5 / FLOOR (BOOST window is age < 360 s, refused by the kit seal). Not run.
- Hold-time stratification of round trips (web-public-pnl-4): descriptive, selects no rule; it cannot produce trades. Its useful part (loss-cut timing, web-public-pnl-2) is used as the trigger of FLUSH.
- Regular-interval equal-size buyer chain (TWAP/DCA-like): overlaps ACCUM (hunt28, FAIL) and the wash-volume ride (lib-b-E7, dead). Not run.
- Dump-absorb by price drop alone, single big sell absorbed, exhaustion run: dead (dump-absorb-1..6, hunt23-b-flow-C/D, hunt28-sellabs-1).

## I1 FLUSH: realized-loss stop-out cascade, bought after it exhausts  (library: web-public-pnl-2 loss-cut timing, new trigger)
- WHY (forced counterparties): holders who bought higher and sell at a >= 20% loss inside a short window are stop-loss / capitulation sellers. Their supply is forced by their own exit rules, not by information. When several distinct such sellers have gone and the selling stops, the overhang of the weakest hands is gone and the next print is a buyer. Slow information: the cost basis of each seller (from the pool's own tape) is not on any screen the crowd watches; price drop alone is.
- Not a repeat: A13/E3, dump-absorb, SELLABS and b-flow-C/D trigger on PRICE DROP or on one big sell. FLUSH conditions on the sellers' own realized loss versus their volume-weighted buy price in the pool (state carried across hours) and on distinct-seller count plus a quiet period. No wallet-skill ledger (A20/A23 are skill classes; this is realized loss of the seller on this pool).
- Expected rate: tens to hundreds of events a day in V-band pools with real quote >= 10 SOL (guess; counts-only decides; drop if < 10/day).
- Risk named in advance: after a cascade the pool may simply be dying (survivors/dead-pool drift); the label tells.

## I2 WAKE: a zombie pool is bid again  (library: new universe, convexity of FLOOR-OPTION v2 / G4-FLOORDIP; REVIVE excluded real quote < 10 SOL, so this universe was never screened by L4)
- WHY: in a pool with real quote <= 2 SOL the price is held up by V (pricing law), the stake is a small fraction of the 17.6 SOL effective depth, and the exit is an option: take profit +50% with no stop (the shape of the only positive L1/G4 lines). A wake-up buy after >= 1 h / 6 h / 24 h of silence is someone with new information or a bot restarting a promotion; the cost of the option is the 2.5% fee plus whatever the pool does when nobody follows. Nobody is forced; the edge, if any, is convex payoff on a free signal.
- Not a repeat: REVIVE (hunt28) was q >= 10 SOL and a 2-buyer / 1 SOL confirmation with sl/trail; indep-mech-4 D1 needed >= 2 SOL of buys from 6 buyer-minutes. FLOOR/G4 trigger on SELL prints into a falling pool at age >= 24 h. WAKE triggers on the first BUY after silence in the shallow universe, one print, no confirmation.
- Expected rate: 20-200/day (guess).
- Risk named in advance: shallow pools strand; the kit counts strands under rule total; impact column will be small because of V, read it with care.

## I3 RANKUP: a pool enters the platform's top-K by 10-minute SOL volume for the first time in two hours  (library: related to the l3 xs10 hourly top-10 lead, which was model-based; this is a minute-grid volume rank, no model)
- WHY (who is forced): attention surfaces (trending lists, screeners) rank pools by short-window volume; followers buy what the list shows with a delay of tens of seconds to minutes. A first-time entry into the top K is the moment the list starts to show it. Slow information is the cross-sectional rank, which a single-pool view does not give.
- Not a repeat: BREADTH/b-flow-A use absolute per-pool thresholds; this ranks pools against each other each minute. day-sigma-1 and survivors-* are hourly or daily rank on returns of deep pools; this is volume rank on a minute grid, short hold.
- Expected rate: K=10 about 10-30/day, K=25/50 more (guess).

## I4 DROUGHT: a pool whose holders do not sell  (library: new; related to diamond hands but measured on the sell side of the pool itself, not wallets)
- WHY (forced buyers?): a pool with many buys and no sell print for D minutes is either (a) a mechanical buyer, e.g. an agent / creator buy-and-burn or fee-funded buyback (mech-buyer-scout, w3-burn-1: the scouts found such buyers exist but sized them too small on their own) running into held supply, or (b) a honeypot where sells fail. (a) is a forced flow we can ride, (b) is a strand/total loss. The screen separates them by price: the strand rule (total) and no-fail mean show (b) as losses. Slow information: the absence of sell prints is visible only on the tape.
- Not a repeat: HC-h60 conditions on the first 20 buyers' sells; REVIVE and BREADTH require sells <= 25-30% of buys, not zero for a long time; w3-burn-1 looked for named buyer wallets and found 25 qualifying events, this counts any pool.
- Expected rate: unknown, possibly under 10/day at the longer drought; counts-only decides.

## Multiplicity
4 ideas x 3 variants = 12 cells declared; all will be priced unless dropped by the counts-only rate bar (< 10 attempts/day on average over the four blocks; drops reported). Best-of-12 winner's curse and the prior reads of the same four blocks by hunt23/24/26/28 apply. A cell that clears everything is a LEAD for quant-proof, not a result.
