# P20 fresh ideas (lane P20, manager15 push, 2026-10-24)

Desk research and cheap public facts only. The lane ran no pricing, used no Helius credits, opened no sealed, forward, holdout, shadow, ledger, bots.json or probe data, and read no tape row. Inputs: CLAUDE.md, LAB_STATE "Now", docs/library (all 439 non-duplicate rows), hunt29/p13/CENSUS.md, docs/research/dbc-p14..p18, docs/research/dbc-m10-forward/REGISTRATION.md, docs/HOLDOUT_LEDGER.md, QP-RULING-1024-dbc-m10-swap3.md, hunt29/PUSH-PLAN-1024.md and hunt29/p5/STACK.md. The web facts are in "Sources" at the end. `date -u` read 2026-10-24T13:34:25Z before this file was written.

**Nothing here is a result.** Every capacity figure is a guess, with the arithmetic shown so it can be checked. No sentence says any idea makes money, and no book has cleared the promotion gate. Dollar figures assume SOL = $150, the same assumption as hunt29/p5/STACK.md; this is not a market quote.

## What the library already teaches (how the ideas were chosen)

- Directional entries at MAL's latency, made after an observed print, fail on every venue tried: pump curve, PumpSwap, LaunchLab, and DBC D2/D3/D4/D6. The fee plus the 1.1-1.9 s leg uses up the move (library families bc-*, hunt23-*, hunt28-*, hunt29 P4, ll-*, dbc-*).
- What still stands, all on exploration data only, has one of two shapes:
  - a **forced or scheduled flow** (H5/BOOST);
  - a **bounded-loss option** (FLOOR v2: buy near the V floor, take profit at +50%).
  
  DBC m10 is the one directional survivor. It is on a venue whose median fee is 25 bps.
- So the ideas below look for one of these:
  - **(a)** a trigger known *in advance*, so detection latency drops out: you send to land on a known slot;
  - **(b)** a hard price floor that bounds the loss;
  - **(c)** an execution in which a miss costs nothing;
  - **(d)** a signal whose move lasts minutes, not slots.
- Each idea was checked against the library by id, name and mechanism. Near neighbours are named in each entry.

## Seal rules every test path below obeys

- **PumpSwap / pump tape:** only the exploration-pool blocks of docs/HOLDOUT_LEDGER.md: explore-0814 `[08-14T12, 08-28T12)`, fresh-0903, the fast 09-09..09-15 block, fast pre-cut and Oracle in-sample. Never fresh-0828 (reserved for EXP-028), fresh-0808, fresh-0802, Fast EXP-009, the Forward walk, or Forward walk 2 / October domains D2-D3 (check ledger D2/D3 before pricing any October pool).
- **Meteora DBC:**
  - Exploration hours are `[2026-10-14T00, 2026-10-17T00)`.
  - `[10-17T00, 10-24T00)` is spent for every D1 variant (QP-RULING-1024-dbc-m10-swap3 (1)). P17 used it as *disclosed exploration* for non-D1 cells. The DBC ideas below are not D1 (D1 = early momentum, p 0.02-0.35, 60 s buys and net SOL). They may use those hours only as disclosed exploration, with QP's OK first, and never as confirmation.
  - **No DBC hour in `[2026-10-25T00, 2026-11-01T00)` may be opened, counted or fetched by these ideas before the m10 read (refuses before 2026-11-01T00:10Z).** No cell may be registered on that window: REGISTRATION R12 would move m10's CI percentile.
  - A confirmation for any DBC idea needs a new registration on hours after the m10 window, or a P18-style sealed forward paper book. If P18's forward book moves its clock to 10-30, those hours are claimed too. The manager decides.
- **Meteora DAMM v2:** the 2,288 fetched pools are clipped at 10-17T00, so they are exploration only. P16's disclosed `probe_raw.json` holds 2 txs after 10-17T00; do not use them.
- **LaunchLab:** `[10-17T00, 10-24T00)` is a reserved holdout (HANDOFF line 33). Exploration is `[10-03, 10-17)` only.
- **Off-venue data** (exchange notices, GeckoTerminal OHLCV, program accounts read at today's state): no ledger block is involved. A forward test needs its own recorder from a pre-registered start, and no October pump tape.
- Multiplicity: each new DBC cell adds to the 26 DBC/D5 cells already screened (P14 12, P17 11, P16 D5 3). Disclose that in each DECLARE.

## Status after QP-RULING-1024-p20 and P21 (#876)

Added 2026-10-24 after the merge of #876. The ranking table below is the original desk ranking and is left as written.

| Rank | Idea | QP domain | Status now |
|---|---|---|---|
| 1 | p20-dbc-start-floor | D7 | Screened by P21 (#876, job #1527, exploration only). All 4 D7 cells FAIL. Primary d7_s105_h6: flat -0.0070 / pressure -0.0058 SOL per attempt, 1/10 days positive. 97-100% of trips end at the time cap. About 15 attempts/day, not the guessed 100. Not eligible for a registration. |
| 2 | p20-dbc-completion-step | D8 | Not screened. QP-RULING-1024-p20 (a)2: a screen is allowed, but there is **no path to a book**. No DAMM v2 sell path exists, and the entry at p >= 0.95 breaks the p <= 0.85 universe rule and QP item 3. The P16 strand-pool subset may not be its primary sample. The library row was updated to match. |
| 3 | p20-dbc-fee-floor-clock | D9 | Screened by P21 (#876). Both D9 cells FAIL. Primary d9_t60: flat -0.0058 / pressure -0.0050. It is negative even at the optimistic expiry + 0 landing. Not eligible for a registration. |
| 4-10 | the remaining ideas | — | Ranks unchanged. P22 is running rank 4's latency-0 kill test (p20-wick-bid-zero-latency). |

The multiplicity on DBC is now m = 32 (P21). The library rows for the D7 and D9 ideas are main's rows from #876.

## Ranking

Score = (expected $/day × probability that a first screen survives) / days to the first screen. All three inputs are the lane's guesses. "Days to 100 trades" is the gate's minimum count at the guessed rate; the gate also needs at least 5 UTC days, so no row can be read in under 5 days.

| Rank | Id | Idea | E $/day (guess) | P(survive screen) | Days to first screen | Score | Days to 100 trades at guessed rate |
|---|---|---|---|---|---|---|---|
| 1 | p20-dbc-start-floor | DBC curve start-price floor option | 45 | 0.08 | 1.5 | 2.40 | ~1 |
| 2 | p20-dbc-completion-step | DBC completing buy into the migration price step | 30 | 0.05 | 1.0 | 1.50 | ~1 |
| 3 | p20-dbc-fee-floor-clock | DBC/DAMM v2 anti-sniper fee-expiry clock (pre-signed entry) | 30 | 0.06 | 1.5 | 1.20 | ~1-2 |
| 4 | p20-wick-bid-zero-latency | Resting-bid wick capture (0-latency bound first) | 6 | 0.05 | 0.5 | 0.60 | ~5 |
| 5 | p20-dbc-segment-convexity | DBC multi-segment curve: enter before a thin segment | 15 | 0.03 | 1.5 | 0.30 | ~2 |
| 6 | p20-atomic-xpool-bundle | Atomic two-pool arb in a revert-unless-surplus bundle (free option) | 9 | 0.10 | 3.0 | 0.30 | ~5 |
| 7 | p20-incentive-farming | Trading-incentive farming vs round-trip cost | 10 | 0.03 | 1.0 | 0.30 | n/a |
| 8 | p20-launch-allocation | Launch-allocation underpricing (Alpha Vault / Genesis) | 7.5 | 0.10 | 3.0 | 0.25 | ~100 |
| 9 | p20-listing-announce | Exchange-listing announcement long (off-chain signal, on-chain buy) | 4.5 | 0.15 | 3.0 | 0.23 | ~200 |
| 10 | p20-xstock-offhours | Tokenized-stock off-hours fair-value reversion (not meme) | 4.5 | 0.10 | 4.0 | 0.11 | ~10 |

E $/day is the "capacity guess" line of each entry × $150. P is low on purpose: base rate of the library is roughly 1 survivor-unconfirmed per 15-20 ideas, and a survivor is not a gate pass.

---

## 1. p20-dbc-start-floor: DBC curve start-price floor option (rank 1)

- **Mechanism.**
  - A Meteora DBC curve cannot trade below its configured `sqrt_start_price` (a config field, tools/data/meteora_dbc_idl_v0.2.2.json `PoolConfig.sqrt_start_price`). At zero quote reserve the price is exactly the start price.
  - A pool that pumped and then dumped back to near zero quote sits on a hard floor. A buy at s × start price can lose at most (s - 1) plus fees (25 bps a side on 89% of rows, P14) plus sends, whatever happens next. Nothing can strand it: completion is far away.
  - The upside is open: revivals (an operator re-pump, a second wave or a narrative) move the price up the curve. This is FLOOR v2's shape (bounded loss, take profit at +50%) on a venue with a hard floor instead of a V floor, and about 10x lower fees than pump.
- **Who pays.** Later buyers of a revival; on a bounded loss the cost of the misses is the small loss itself.
- **Neighbours.**
  - lib-c-L1 FLOOR-OPTION v2 and lib-b-C1 G4-FLOORDIP: same shape on PumpSwap.
  - lib-b-E14 tail-harvest basket and indep-mech-4 dead-pool revival: dead on PumpSwap, 0/13 days positive for E14. That is the main warning.
  - No DBC floor row exists.
- **Capacity (guess).** About 2,680 new SOL pools/day (P17: 18,754 pools over 7 days). Suppose 10% fall back within 5% of start while still on the curve, and half of those pass a ≤2% impact bar at 0.1 SOL: about 130 candidates/day; take 100/day.
  - 100/day × 0.1 SOL × +2% net (guess) = **0.2 SOL/day**; at 0.25 SOL with a lower take rate, about 0.3 SOL/day (**$45**).
  - Capital: a 24 h hold × 100/day × 0.1 SOL = 10 SOL tied up, so on 1.29 SOL the binding limit is capital, as for FLOOR. A shorter cap (2-6 h) is a declared cell, not a tune.
- **Data MAL has.**
  - DBC tape: /data2/mal/venue-tape-backfill/meteora_dbc/ (10-14..10-16, 72 h) and p17_union (240 h hardlinks; 10-17..10-23 disclosed exploration).
  - Pricer: tools/dbc_price.py, dbc_screen.py (--swap3), dbc_config.py.
  - Config summary: docs/research/dbc-p17/data/cfg_summary.json. It has no `sqrt_start_price` or curve segments.
- **Data needed.** Raw config bytes for `sqrt_start_price`/curve: re-decode a cached P17 fetch if one exists, or refetch about 7,400 configs with getMultipleAccounts (74 calls, under 1,000 credits).
- **Seal-safe test path.**
  - Counts, then one priced screen, on `[10-14T00, 10-17T00)`, plus `[10-17T00, 10-24T00)` as disclosed exploration only with QP's OK (not D1).
  - No hour ≥ 10-25T00.
  - Declare before the counts: floor band s ∈ {1.02, 1.05, 1.10}; exits tp +50% / 6 h / 24 h; stake 0.1 and 0.25; strand impossible but checked.
  - Report ex-top-3 and ex-best-day.
- **Time to first screen.** About 1.5 days: 0.5 for config decode and counts, 1 for the screen with the existing pricer.
- **Main kill risk.**
  - Floor-sitting DBC pools stay dead (as E14 did on PumpSwap). The loss per trip is small, but most trips lose and fees plus sends eat the rest.
  - Second risk: spam pools with tiny thresholds dominate the candidate set.

## 2. p20-dbc-completion-step: DBC completing buy into the migration price step (rank 2)

- **Mechanism.**
  - When a DBC curve reaches `migration_quote_threshold`, the pool migrates to DAMM v2 at a price set by the config (`migration_sqrt_price`).
  - P16 measured that the first DAMM print sits a median 11% above the decision spot of strand positions (8% ms50, 13.5% ms70). That says nothing yet about the gap between the *last curve print* and the first DAMM print.
  - If the DAMM opening price is above the curve's terminal price by a config-determined step, a buy that lands in the last few percent of the curve (or completes it) holds a near-deterministic step, sold into the first DAMM prints.
- **Who pays.** The first DAMM buyers, mostly migration snipers who pay above the terminal curve price.
- **Neighbours.**
  - dbc-milestone ms70: entry at 70%, tp 1.15 on the curve. Re-priced strand trips were mean +0.00004 SOL, so riding from 70% is about zero; this idea enters at ≥ 95% and exits on DAMM.
  - dbc-strand-damm-continue: D1 positions carried through.
  - dbc-completion-hold: entry after completion.
  - speed-route-3: pump migrate+buy, parked.
  - web-oct-mechanics-1: pump graduation play.
- **Capacity (guess).** About 2,180 completions/day (P14: 6,541 in 3 days). Suppose 5% let a 0.25 SOL buy land at p ≥ 0.95 within 1.1-1.3 s of a trigger with a step ≥ 3% after fees: about 100/day.
  - 100 × 0.25 SOL × +1% net (guess) = **0.25 SOL/day**; rounded to 0.2 for competition (**$30**).
- **Data MAL has.**
  - DBC tape (as idea 1).
  - DAMM v2 histories for 2,288 migrated pools: /data2/mal/venue-tape-backfill/meteora_damm_v2/pools/; decoder tools/damm_decode.py, tools/damm_grad_price.py; docs/research/dbc-p16/grad.json.
- **Data needed.** `migration_sqrt_price` from the same config refetch as idea 1. 0 tape credits: the first screen fits inside the fetched pools.
- **Seal-safe test path.**
  - Step 1 is desk arithmetic: config terminal curve price vs `migration_sqrt_price`, no tape.
  - Step 2: the distribution of (first k DAMM prints / last curve print) on the 2,288 pools, all clipped before 10-17T00.
  - Step 3: a priced cell with entry triggered at p ≥ 0.95 and a 1.9 s leg, on `[10-14, 10-17)`, after a DECLARE. No hour ≥ 10-25T00.
- **Time to first screen.** About 1 day.
- **Main kill risk.**
  - The completing buy is a race MAL cannot win at 5-6 slots (the curve is completed by a sniper in-slot).
  - The DAMM step is captured by migration snipers in slot 0-2, before MAL's sell lands. P16: 82% of sampled migrated pools fall by more than half within 1 h (a flag, not validated).
  - Needs a DAMM v2 sell path that does not exist (QP item 3).

## 3. p20-dbc-fee-floor-clock: anti-sniper fee-expiry clock (rank 3)

- **Mechanism.**
  - Meteora's anti-sniper suite gives DBC and DAMM v2 a **fee scheduler**: the fee starts high and decays linearly or exponentially to a base fee. The DAMM v2 frontend default is 50% falling to 0.25% over 120 min.
  - It also gives a **rate limiter**: the fee rises with trade size during a window.
  - The schedule is in the config (`BaseFeeConfig`: cliff_fee_numerator, factors, base_fee_mode; `MigratedPoolMarketCapFeeSchedulerParams`), so its end time is known when the pool is created.
  - Size buyers and snipers have a reason to wait for the floor. If deferred demand bunches at the expiry, a buy pre-signed to land at the expiry slot has **no detection leg**. MAL's 1.1-1.3 s matters only for when to send, which is known in advance.
- **Who pays.** The deferred buyers who arrive after expiry. Exit after 30-300 s into that flow.
- **Neighbours.** None on fee schedules. P14 noted the decaying-fee approximation only as a pricing caveat.
- **Capacity (guess).** About 2,680 new pools/day. Suppose 5% have a scheduler or rate limiter that ends while the pool is alive, on curve, and passes the impact bar: about 130/day; take 100.
  - 100 × 0.25 SOL × +0.8% net (guess) = **0.2 SOL/day** (**$30**).
  - A DAMM v2 variant adds migrated pools with market-cap fee schedulers. P16 saw 10 bps in its probe pools, so many may have none.
- **Data MAL has.** DBC tape. Per-trade fee bps is on every row, so the realized fee path per pool can be seen directly, without the config.
- **Data needed.** Config fee params (same 74-call refetch as idea 1) to know the expiry *in advance*: a causal trigger must not use the realized fee path.
- **Seal-safe test path.**
  - Counts and one priced screen on `[10-14, 10-17)` (plus 10-17..10-23 as disclosed exploration with QP's OK).
  - Trigger = config-computed expiry; entry lands at expiry + {0, 2} slots.
  - Declared control: the same pools entered at a random time.
  - No hour ≥ 10-25T00.
- **Time to first screen.** About 1.5 days.
- **Main kill risk.**
  - Schedules on DBC are short or rare and most pools are dead by expiry.
  - At a public, known slot there is a priority-fee race: every bot can schedule the same send.

## 4. p20-wick-bid-zero-latency: resting-bid wick capture (rank 4)

- **Mechanism.** A resting bid fills *at the price of the sell that hits it*, with zero latency. Examples: a single-sided bid bin in a Meteora DLMM pool, or any in-pool resting liquidity. A reactive buy pays the 1.9 s leg after the print.
  - If large single sells (panic, liquidations, operator dumps) overshoot and part-revert within seconds, the reversion goes to the resting bid, not to the reactive buyer.
- **Who pays.** The impatient seller, who pays impact.
- **Neighbours.** dump-absorb-1..6 (buy the next print ≥ 2 s later: median -8.40% at 0.25 SOL, n 444,006), crash-bounce-v2, hunt28-sellabs, hunt23-b-flow-C, and lib-a-A34 DLMM LP (dead as LP; this is a one-sided bid, not two-sided LP).
- **Capacity (guess).** 20 fills/day × 0.1 SOL × +2% = **0.04 SOL/day** (**$6**). It also needs the token to have a DLMM (or similar) pool; most pump tokens do not.
- **Data MAL has.**
  - PumpSwap tape on the exploration blocks (/data2/mal/blocks-clean/explore-0814, fresh-0903, exp011-0909).
  - The dump-absorb rule code and kit tools/arena_screen.py, where entry at the sell's own post-trade state (latency 0) is one parameter.
- **Data needed.** For a real venue: DLMM pool lists for pump mints. getProgramAccounts with a mint filter costs about 10 credits a call (assumed); about 20k credits for 2,000 mints. Not needed for the bound.
- **Seal-safe test path.** The **kill test first**: re-run the dump-absorb D8/D15 triggers on the exploration blocks with entry at latency 0, at the sell's post-trade price, gross and net of one 25 bps fee. If even this upper bound is negative, the idea dies with 0 credits.
- **Time to first screen.** About 0.5 day.
- **Main kill risk.**
  - Adverse selection: resting bids fill mostly on sells that keep going (rugs, drains: 21.1% of generic entries hit a >20x down jump, round 3).
  - The latency-0 bound is not a fill model: a real resting bid also gets picked off.

## 5. p20-dbc-segment-convexity: multi-segment curve geometry (rank 5)

- **Mechanism.**
  - DBC curves have 1-16 segments, each with its own liquidity L (config `curve`; web-cross-venue-4). Where L drops at a segment boundary, the price moves faster per SOL bought.
  - A position bought just before a thin segment gets more price per SOL of the next buyers' flow. The geometry is known from the config, not learned from prints.
- **Who pays.** Buyers inside the thin segment, who pay more price per SOL.
- **Neighbours.** dbc-milestone (fixed 30/50/70% crossings, not config-specific) and lib-c-I3 pregrad runners (pump curve, single segment).
- **Capacity (guess).** If 10% of pools have a sharp L drop below p 0.85: 268/day; take 50 at 0.25 SOL × +0.8% = **0.1 SOL/day** (**$15**).
- **Data MAL has.** DBC tape and the pricer (segments are already handled by the config-curve path in dbc_config.py).
- **Data needed.** Curve segments per config (same refetch).
- **Seal-safe test path.** As idea 1: `[10-14, 10-17)` first, 10-17..10-23 as disclosed exploration with QP's OK, nothing ≥ 10-25T00. Declare boundary distance bands {1%, 3%} of quote before the boundary.
- **Time to first screen.** About 1.5 days.
- **Main kill risk.**
  - Directional at heart: it still needs flow to continue, and directional DBC cells D2-D6 all failed.
  - Most configs may be single-segment.

## 6. p20-atomic-xpool-bundle: atomic two-pool arbitrage as a free option (rank 6)

- **Mechanism.**
  - When one mint trades in two pools (for example a PumpSwap canonical pool and a Meteora DAMM v2/DLMM or Raydium CPMM pool), a large trade in one leaves a gap.
  - Buy-in-A and sell-in-B in **one atomic bundle**, with a min-out that requires a surplus ≥ fees + tip. If the gap is gone, the bundle does not land and costs nothing: Jito bundles are all-or-nothing, and the tip is a transfer inside the bundle. Latency then costs only missed chances, not losses.
- **Who pays.** The trader who moved pool A, through the stale price left in pool B's liquidity.
- **Neighbours.** w4-gap-xpool-1 (directional long of the cheaper PumpSwap sibling; killed: 0 of 173 sibling pairs ≥ 10 SOL), web-capacity-4 (cross-venue gap, untested), lib-a-A26 (venue arb, dead, cause not reported). New here: the cross-*program* pairs and the zero-cost-miss execution. The idea's first step is exactly xpool's revive trigger ("a cross-venue feed ... with a measured same-slot gap").
- **Capacity (guess).** 20 captures/day × 0.003 SOL = **0.06 SOL/day** (**$9**). Size is capped by the shallower pool.
- **Data MAL has.** PumpSwap mint list on the exploration blocks, and the DAMM v2 / CPMM decoders.
- **Data needed.**
  - Second-venue pools for pump mints: getProgramAccounts with mint filters on DAMM v2, DLMM and CPMM, about 30-60k credits for 2,000 mints (assumed 10 per call). Public RPC may refuse gPA.
  - Then a forward gap logger: public `logsSubscribe` mentions on the found pools, 0 credits.
  - The current state of Jito's block engine needs checking (the library notes Jito ShredStream's shutdown notice of 2026-09-05).
- **Seal-safe test path.** Structure count at today's account state (no tape). Then a pre-registered forward logger of gaps that persist ≥ k = 5-6 slots, from a start after registration. No October pump tape is read (D2/D3).
- **Time to first screen.** About 3 days, 2 of them logger time.
- **Main kill risk.**
  - Searchers close cross-pool gaps in the same slot, so nothing persists 5 slots.
  - Second-venue pools for pump mints are dust, as the PumpSwap siblings were.
  - Live needs a bundle path and an on-chain min-out check: new executor work.

## 7. p20-incentive-farming: trading incentives vs round-trip cost (rank 7)

- **Mechanism.** Platforms and terminals sometimes pay per-volume rewards: points, token airdrops, fee cashback. Library row scheduled-flows-4 notes pump cashback is closed to new launches.
  - If a live programme's reward per SOL traded is larger than the round trip on its cheapest eligible pool (DAMM v2 at 10 bps, or 25 bps DBC), round trips are positive carry paid by the platform's marketing budget.
  - Latency does not matter.
- **Who pays.** The platform.
- **Neighbours.** kalshi-lip-1 (Kalshi liquidity incentives, forward test; the same idea on another venue). No Solana row.
- **Capacity (guess).** Unknown until programmes are listed. Placeholder 0.07 SOL/day (**$10**): 50 SOL volume/day × 0.15% net (guess).
- **Data MAL has.** None.
- **Data needed.** A desk list of live programmes with official terms (0 credits).
- **Seal-safe test path.** Desk only. No tape.
- **Time to first screen.** About 1 day (desk).
- **Main kill risk.**
  - Anti-wash and anti-sybil rules void the rewards, and the reward token's value is uncertain.
  - Most terminals need their own custodial wallets, which conflicts with MAL's key rules.
  - This may be an owner-policy question (terms of service) before a data one.

## 8. p20-launch-allocation: launch-allocation underpricing (rank 8)

- **Mechanism.**
  - Pre-launch allocation products let depositors buy at the launch or pro-rata price *before* public trading: Meteora **Alpha Vault** (pro-rata or FCFS, buys in the pool's protected pre-activation window, unused quote returned) and Metaplex **Genesis** launch pools (price set after the deposit window, pro-rata).
  - If first-hours public prices sit above the allocation price on average (IPO-style underpricing), depositors hold the gap.
  - Deposit windows run for hours, so latency does not matter.
- **Who pays.** Public buyers in the first hours after activation.
- **Neighbours.** None. A variant (b) is a pre-signed buy landing at a DLMM/DAMM launch pool's `activation_point` (scheduled start, no detection leg); this is a known-slot race like idea 3.
- **Capacity (guess).** 1 launch/day × 0.5 SOL filled (after pro-rata) × +10% net = **0.05 SOL/day** (**$7.5**). The gate's 100 trades would take about 100 days.
- **Data MAL has.** None.
- **Data needed.** Alpha Vault and Genesis program accounts (vault configs, caps, vesting points) via getProgramAccounts, under 10k credits. First-hours prices from the public GeckoTerminal API (0 Helius).
- **Seal-safe test path.** An event study on all past vaults found on chain (no ledger block). Then a forward paper record from a pre-registered start.
- **Time to first screen.** About 3 days.
- **Main kill risk.**
  - Rock's winner's curse in allocation: good launches are oversubscribed (tiny fills) and bad ones fill in full.
  - Vesting (`start_vesting_point`..`end_vesting_point`) can lock tokens through the pop.
  - Launches may be too rare for the gate.

## 9. p20-listing-announce: exchange-listing announcement long (rank 9)

- **Mechanism.**
  - A major-exchange listing announcement for a Solana token widens its buyer base. CEX trading opens hours later, so the on-chain pool is where pre-listing demand lands first.
  - Reported cases: BOME about +300% on the day after Binance's 2024 announcement; MEW +14% and BONK +7% on Upbit announcements; Upbit restricts buy orders for about 5 minutes at open.
  - MAL would poll the notice endpoints (Binance, Upbit, Bithumb, Coinbase, Robinhood, Bybit, OKX) and buy on chain within about 2-4 s. Variant: on-chain inflow of the token to a known CEX hot wallet as a pre-announcement signal.
  - The signal is off-chain (exchange websites); the trade is on Solana.
- **Who pays.** Later on-chain buyers, and the CEX-side buyers whom arbitrageurs serve from on-chain inventory.
- **Neighbours.**
  - w4s-short-cex-listing-watch (*short* on perps after listing; about 0.13 pump-origin perp listings/day).
  - lib-c-J2 outside attention (DexScreener, livestreams, X; parked).
  - No long-on-announcement row.
- **Capacity (guess).** 0.5 announcements/day × 1.5 SOL (deep pools, under 1% impact) × +3% net = **0.0225 SOL/day** (**$3.4**, rounded to $4.5 for the fat right tail). The gate's 100 trades would take about 200 days.
- **Data MAL has.** PumpSwap tape for pump-origin tokens on exploration blocks only.
- **Data needed.** 12 months of announcement archives (public pages, 0 credits) and minute OHLCV from the GeckoTerminal public API (rate-limited, 0 Helius).
- **Seal-safe test path.** An event study on public data; no ledger block. Entry at +2 s / +10 s / +60 s after the announcement timestamp, exits at 15 min / 1 h / 6 h, declared first.
- **Time to first screen.** About 3 days.
- **Main kill risk.**
  - News bots take the move in the first second.
  - Announcement timestamps on archive pages are not the instant the notice became visible.
  - The rate is far too low for the gate in weeks.

## 10. p20-xstock-offhours: tokenized-stock off-hours fair value (rank 10; not a meme coin)

- **Mechanism.** Tokenized US stocks (xStocks) trade 24/7 in Solana AMM pools. Outside US hours, thin pools drift with retail flow while a fair value is visible in index futures (nearly 24/5). Buy below fair value minus costs, and exit on reversion or at the open.
- **Who pays.** Off-hours on-chain retail, and stale LPs.
- **Neighbours.** web-competitors-6 (pump coins *quoted in* tokenized stocks: a different thing) and lib-a-A26 (Robinhood chain venue arb, dead).
- **Conflict with direction.** The owner moved everything to meme coins on 10-19. This row is here only because the brief asked for non-pump markets. It needs the owner's say before any work.
- **Capacity (guess).** 10 trades/day × 1 SOL × +0.3% net = **0.03 SOL/day** (**$4.5**). This scales with capital up to pool depth.
- **Data MAL has.** None.
- **Data needed.** xStock pool histories, 10 pools × about 30 days of gTFA pages at about 50 credits a page: about 50-150k credits. Minute futures data (free sources are short-history).
- **Seal-safe test path.** No ledger block.
- **Time to first screen.** About 4 days.
- **Main kill risk.**
  - The issuers' market makers already arbitrage it.
  - Weekend gaps have no futures anchor.
  - It is not a meme coin.

---

## Considered and not added (duplicates or capacity-dead)

| Idea | Why not added |
|---|---|
| DBC locked-vesting unlock clock (`LockedVestingConfig.cliff_duration_from_migration_time`) as a sell-pressure event | Same family as web-forced-flows-3 (cliff releases). As a long trade it is directional; it may be useful as a veto layer inside idea 2's DAMM exit. |
| LP on young DAMM v2 pools during high-fee scheduler windows | LP family is dead (lib-a-A33, A34). P16: 82% of sampled migrated pools fall by more than half within 1 h (impermanent loss), and the probe pools charged 10 bps. |
| LST epoch-boundary rate arbitrage (SOL vs LST pools) | Capacity: about 0.03% × 1 SOL ≈ 0.0003 SOL per epoch (about 2 days). Useless at MAL's capital. |
| Telegram caller copy | lib-a-A10 (KOL copy) dead-as-frozen. |
| Bags verified-creator launches (DBC) | Outside-attention family lib-c-J2 (parked); sniper-competed at launch. |
| Polymarket meme or launchpad markets | Off-Solana; Kalshi is paused by the owner (10-19); no row added. |
| DBC sniper-cohort exit then enter | Same as lib-b-E13 (overhang exhaustion) and E9 (insider distribution); dead on pump. |
| Mayhem 24 h unsold-token burn | The burn changes no pool reserve; not a price event (scheduled-flows-2 covers Mayhem). |

## Credits and jobs

- Helius credits used by this lane: 0.
- MiScusi jobs: none.
- Web: 5 searches (below).

## Sources (public, read 2026-10-24)
- [Meteora anti-sniper suite](https://docs.meteora.ag/meteoras-anti-sniper-suite-a.s.s.), [fee scheduler](https://docs.meteora.ag/meteoras-anti-sniper-suite-a.s.s./meteoras-anti-sniper-suite/fee-scheduler), [A.S.S. for DBC](https://docs.meteora.ag/meteoras-anti-sniper-suite-a.s.s./meteoras-anti-sniper-suite/a.s.s.-for-dbc)
- [Meteora Alpha Vault modes](https://docs.meteora.ag/helper-products/alpha-vault/vault-modes), [what is Alpha Vault](https://docs.meteora.ag/helper-products/alpha-vault/what-is-alpha-vault)
- [Metaplex Genesis](https://developers.metaplex.com/smart-contracts/genesis), [launch pool](https://www.metaplex.com/docs/smart-contracts/genesis/launch-pool)
- [Jito low-latency send / bundles](https://docs.jito.wtf/lowlatencytxnsend/), [GetBlock Jito bundles guide](https://docs.getblock.io/guides/how-to-optimize-solana-transactions-with-jito-bundles)
- Listing cases: [BOME (FXStreet)](https://www.fxstreet.com/cryptocurrencies/news/solana-meme-coin-bome-leads-meme-rally-with-300-daily-gains-and-nearly-1-billion-market-cap-202403161208), [MEW Upbit (crypto.news)](https://crypto.news/upbit-listing-triggered-bullish-momentum-for-mew/), [BONK Upbit (CoinGape)](https://coingape.com/bonk-price-may-rally-2x-after-major-listing-on-upbit/)
