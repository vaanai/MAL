# P37 idea sprint: mechanisms that do not race

Lane P37, manager17, desk only, 2026-10-26 (`date -u` 2026-10-26T17:11:19Z before this file was written).

**Method.** The public web, and repo docs on origin/main 5071e29a plus the P32 audit (origin/claude/p32-survivor-audit, docs/research/p32-survivor-audit/AUDIT.md). No tape row, job output, ledger, fill or price was opened. No MiScusi job, no host, 0 Helius credits. No sealed path was touched (HV4, Q50, the DBC forward window and fwdv1/, meta-capture). I did **not** open `docs/research/dbc-p17/data/cfg_summary.json`. Its configs may come from the spent DBC window, where counts are barred, so every DBC config number below is quoted from an earlier lane.

**Nothing here is a result.** Every edge and rate in the capacity column is my guess, and the arithmetic is shown. No sentence says an idea makes money. No book has cleared the promotion gate. Web facts marked [secondary] come from press or aggregators, not from the venue's own docs or code.

**Inputs.** P32 AUDIT.md. All 512 rows of docs/library/ideas.jsonl. P25 IDEAS.md §1 (the change log to 10-24). docs/research/dbc-p16/REPORT.md. docs/HOLDOUT_LEDGER.md (exploration pool rows). The SEALS-BRIEF (sha256 823cc062...).

## 0. What P32 left, and the screen used here

P32's finding is that every event reaction dies at MAL's ~4.4 s effective entry plus ~2.4% round trip. The survivors are slow, bounded structures at 1-15 trades/day: FLOOR, the BOOST schedule (Q50/H5) and DBC m10. This lane looks only for mechanisms whose P&L does not depend on reacting within seconds.

**Capacity screen.** trades/day × 0.25 SOL × plausible net edge after the ~2.4% round trip (or the venue's own fee) and impact. A mechanism is dropped below 0.02 SOL/day, and also when its mechanism, its data or a policy line rules it out first. To reach 0.02 SOL/day at 0.25 SOL, trades/day × edge must be at least 0.08: for example 8/day at +1%, or 4/day at +2%.

**Data limit that shapes the top 3.** The exploration pool is all pump/PumpSwap August-September hours (explore-0814, fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922), plus DBC [2026-10-14T00, 10-17T00) and the DAMM v2 histories clipped at 10-17T00. A mechanic that shipped after 2026-10-08 (pump-coin quotes, depth 2, PreStocks, the app fee, Pump Bridge) has **no exploration block**. It can only be counted on arena tape, and any book built on it is an arena book forever. I ranked such ideas down for that reason alone.

## 1. Changes checked on the web, 10-24 .. 10-26 (adds to P25 §1)

| When | What | Source | Used in |
|---|---|---|---|
| 2026-09-30 | PumpSwap `virtual_quote_reserves` may be negative: fees kept in the pool are subtracted from V so the price does not count them. MAL measured max \|V\| of 0.0020 SOL on 321 negative pools (10-06). | pump-public-docs README; ARTIFACTS/lab/pumpswap-v-layout-2026-10-06.md | #10 |
| 10-13 batch | `sweep_protocol_fee` / `sweep_creator_fee` are permissionless; creators, CTOs and fee sharing must sweep the creator fee first. | pump-public-docs README | #10 |
| 2026-10-04 / 10-10 | Callout Rewards (about $15M budget, daily USDC payouts): high-frequency posting cut 10-04; from 10-10 rewards follow followers' profit and low-cap calls earn less. | [secondary] cryptobriefing | #19 |
| 10-16 / 10-17 | Pump app referral programme: a $50 match, a 4x commission boost, and the 0.40% referred fee. Terms last updated 10-07. | [secondary] Medium/coinmonks, airdropalert | none (MAL does not trade through the app) |
| 10-23 | Pump app TP/SL limit orders extended to six EVM networks. | [secondary] tokenpost | #13 |
| 10-25 | Pump app buys Monad tokens with a USDC balance. | [secondary] chaincatcher | none |
| DBC CHANGELOG (no dates in file) | 0.1.1 added 4% and 6% migration fee options. 0.1.8 requires at least 10% locked liquidity for at least one day after migration. 0.1.9 added a market-cap fee scheduler for DAMM v2 migration. 0.1.10 added a DAMM v2 compounding fee mode. 0.2.2 added transfer fees (P25 C7). | github.com/MeteoraAg/dynamic-bonding-curve CHANGELOG.md | #1, #2, #11 |
| DAMM v2 fee docs | The protocol takes 20% of the trading fee by default and **LPs get the rest**. The scheduler is time- or market-cap-based; the UI default decays from 50% to 0.25% over 120 min, exponentially. Collect modes: BothToken; OnlyB (fee always in the quote token); Compounding (fee in the quote token, a `compounding_fee_bps` share "added back to reserves"). | docs.meteora.ag core-products/damm-v2/fees/overview | #1, #2 |

I found no new pump/PumpSwap program write dated after 10-19 in public sources. A3 owns that question, not this lane.

## 2. Candidates (20), with the capacity screen

Edge guesses are net of the round trip. "Closest row" names the library row; "new" says what that row did not test.

| # | id | Mechanism (one line) | Why it does not race | Closest row and what is new | Capacity screen (my guess) | Verdict |
|---|---|---|---|---|---|---|
| 1 | p37-damm-lp | Provide LP on DBC-migrated DAMM v2 pools for hours, earning 80% of a 0.25-6% fee | Fees accrue to a resting position; entering minutes late costs nothing | lp-fees-1 (PumpSwap LP, -0.264 per SOL-day; the LP share is **2 bps** below 420 SOL mcap), lib-a-A34 (DLMM LP, cause not reported). New: the LP fee rate on these pools is 10-240x PumpSwap's on pools at 0.25% or more, on 6,541 completions in 3 days (P16) | 20 pools/day × 0.25 × +2% = **0.10 SOL/day** if the sign is right | **TOP 3 (#1)** |
| 2 | p37-damm-compound-hold | Hold base on Compounding-mode pools: every trade's quote fee is added to reserves, so the price ratchets up with volume | Accrual, not reaction | lib-a-A36 (holder yield, dead), dbc-completion-hold. New: the fee is paid through the price, with no payout | Break-even needs fee × share × turnover above the collapse. At 1% × 50% × 10x/day = +5%/day, against a 1 h time-only mean of -0.204 on migrated pools (dbc-d5) | Drop: kept only as a count field inside #1 |
| 3 | p37-curve-floor | Buy pump curves that reached at least 30% progress and fell back to at most 1% (price within 1.0575x of the curve start); hold 24 h, tp +100% | The curve cannot trade below its start price, so the mark is bounded; entry is minutes after the fall | p20-dbc-start-floor (same shape on DBC: FAIL, -0.0070/trade, 97-100% end at the time cap), lib-b-E14, hunt29-wake-1. New: pump curves, which have far more traffic than DBC pools; reaching back to curve start is a stricter floor than the V-band zombie pools | 50/day × 0.25 × +0.7% = **0.0875 SOL/day** if positive; the DBC analogue says it is not | **TOP 3 (#3)** |
| 4 | p37-stall-to-complete | Buy a clean curve stalled at 90% progress or more for at least 20 min; hold to completion; exit at the first PumpSwap print at or after s0+360 s, else sell on the curve at 6 h | Graduation pays BOOST (about 17.6 SOL of protocol buys) to whoever holds supply, so large holders have a reason to complete; entry is 20+ min ahead, not a race | lib-b-B5 (H1 pre-grad, died on the race: 35-74% completed before a 1.3 s landing), lib-b-E4 (P(graduate) × EV, no survivor), bc-runup-1. New: a stall condition removes the race by construction, and the exit is after BOOST, not before complete | 15/day × 0.25 × +1% = **0.0375 SOL/day** | **TOP 3 (#2)** |
| 5 | p37-floor-lowfee-custom | Run the frozen FLOOR v2 rule on PUMP-quoted custom pools (fee 0.32-0.42%) | A 24 h hold | lib-c-L1, boost-custom-v2-3. New: FLOOR on custom pairs | The fee saving is about 1.7 pp (2.4% → 0.7%), but conversion legs cost 1.5 pp (boost-custom-v2-2), so about +0.2 pp net. At 1-3/day (custom BOOST rates), 3 × 0.25 × 0.002 = 0.0015 | Drop: capacity |
| 6 | p37-dbc-stall-migrate | Buy DBC curves stalled at p 0.6-0.85; hold into the DAMM v2 opening step (first print a median 11% above the decision spot, P16) | Known migration threshold | p20-dbc-completion-step, dbc-milestone. New: a stall entry inside the p ≤ 0.85 universe rule | Already priced in effect: ms50/ms70 re-priced with DAMM continuation = 0.000 / -0.006 per trip (P16) | Drop: dominated |
| 7 | p37-qsink-lock-24h | 24 h hold on a parent pump coin ranked by the share of its float locked in children's curves | Slow accumulation | hunt23-w3-qsink-1 (counting), lib-c-J3 (killed). New: a day-hold horizon and a float-share rank | J3's own text: "most of the parent buying reverses when children die", so a day-hold rides the reversal. Oct-only mechanic: no exploration block | Drop: mechanism and data |
| 8 | p37-cto-hold | Hold 24 h after an admin-signed creator-fee redirect (a community takeover, CTO) | The approval is a slow public process; the signal is hours old | web-competitors-3 (creator fee-recipient events, desk-read). New: admin-signed CTO events, not creator-signed ones | 3/day × 0.25 × +3% = 0.0225 (borderline); tape coverage of the instruction unknown | Near-miss (4th) |
| 9 | p37-mayhem-end | Trade the 24 h Mayhem end, when unsold agent tokens are burned | Known 24 h ahead | scheduled-flows-2, mayhem-flow-1. New: the end-of-window event | The burn is of tokens held off-pool, so it does not change reserves or price. The agent's flow is a coin flip (P(buy) 0.499) | Drop: no mechanical effect |
| 10 | p37-fee-sweep | Trade around permissionless creator/protocol fee sweeps | Sweeps are visible and slow | web-pump-changes-3. New: price effect of a sweep | Price-neutral by design: kept fees are subtracted from V (09-30 note), max \|V\| 0.0020 SOL | Drop: no mechanical effect |
| 11 | p37-dbc-lock-expiry | Use the known instant at which DBC's minimum 1-day LP lock (0.1.8) and LP vesting expire | Known a day ahead | web-forced-flows-3 (Jupiter Lock / LaunchLab cliffs). New: the DBC minimum-lock clock | Sell-side only. MAL cannot short new coins (w4s-short-borrow-short), so this is only a veto or exit timer on DBC holds. About 0 SOL/day on its own | Drop: no long side |
| 12 | p37-exit-hour | Time the exits of 24 h holds to the UTC hour with the most buy SOL | Hour-scale | lib-a-A25, tod-regime-4, exit-upgrade-1. New: hour applied to exits, not to entry gating | FLOOR 6/day × 0.25 × +0.5 pp = 0.0075 | Drop: capacity |
| 13 | p37-tpsl-map | Read pump app TP/SL order levels ahead of the trigger; veto entries above large stop clusters | Levels are known before price arrives | p25-pump-order-keeper-absorb (P29). New: reading levels ahead instead of reacting to fills | Veto only; readability of open orders on chain not established (P29 found only the keeper's fills) | Drop: capacity and data |
| 14 | p37-listing-hold-open | After a CEX listing notice, enter 30-60 min later and exit at the scheduled trading open | The open time is published hours ahead | p20-listing-announce, w4s-short-cex-listing-watch. New: a late entry and a scheduled exit | 0.13-0.5/day (W4-S counts) × 0.25 × +5% = 0.006 | Drop: capacity |
| 15 | p37-scheduled-catalyst | Haiku extracts stated-time catalysts (AMA, listing date, reveal) from public coin pages; hold into the stated time | Hours of lead | lib-a-A19 (parked), lib-c-J2. New: date extraction, not the narrative scoring P28-H tested (AUC 0.53) | Unknown rate. No X keys (hard rule) and no archive, so it cannot be tested on any exploration block | Drop: data (park) |
| 16 | p37-launch-house | MAL launches coins and earns the creator fee from sniper bots that buy every create | Being the house | lib-c-K3 (create-time scripted buyers, as a trade). New: the creator side | Creator fee share × bot volume per create (unknown, perhaps 1-3 SOL) against create cost (about 0.02 SOL [unverified]); likely negative. It also puts junk coins in front of retail | Drop: owner policy and ethics first |
| 17 | p37-cex-dex-gap | Trade minutes-long gaps between MEXC/Gate spot and PumpSwap on listed pump-origin coins during deposit suspensions | Gaps persist while transfers are shut | lib-a-A26, web-capacity-4. New: the CEX-suspension window | Needs a custodial CEX account (policy); about 0.13 listed coins/day | Drop: policy and rate |
| 18 | p37-meme-index | Front-run scheduled rebalances of on-chain meme index baskets | Known rebalance times | web-forced-flows (none). New: index products | No Solana meme index with a rebalance schedule was found (Solindex claims hourly rebalances, not memes) | Drop: no product |
| 19 | p37-callout-follow | Follow pump app callouts on an hours hold, now that callers are paid on followers' profit (from 10-10) | The incentive change might make calls slower and better | lib-a-A10 (KOL copy: 36/36 cells negative). New: the incentive change | The followers react in seconds; the feed is off chain. Latency-bound in practice | Drop: latency and data |
| 20 | p37-pred-market | Use MAL's on-chain counts as an edge on prediction markets that resolve on pump metrics (launch counts, PUMP price) | Resolves over days | kalshi-* rows. New: pump-metric markets | Few markets; Kalshi paused by the owner to about 11-02; not a meme trade | Drop: capacity and policy |

Four of the 20 clear 0.02 SOL/day on paper (#1, #3, #4, #8), and only because of my guessed edges. **None clears it on evidence.** The nearest evidence, for each, points the wrong way (lp-fees-1, p20-dbc-start-floor, lib-b-E4).

## 3. Top 3

Ranking score = (SOL/day if it survives) × P(the first screen survives) / days to that screen. All inputs are guesses.

| Rank | id | SOL/day if it survives | P(screen survives) | Days to screen | Days to a forward clock |
|---|---|---|---|---|---|
| 1 | p37-damm-lp | 0.10 | 0.06 | 3-4 | 8-10 (DBC/DAMM sealed to 11-01T00:10Z; needs an LP executor) |
| 2 | p37-stall-to-complete | 0.0375 | 0.10 | 2-3 | 6-8 if QP rules no first-360 s contact; else after 2026-11-14 |
| 3 | p37-curve-floor | 0.0875 | 0.05 | 2-3 | 5-7 |

### 3.1 p37-damm-lp: passive LP on DBC-migrated DAMM v2 pools

**Mechanism.** Below 420 SOL mcap a PumpSwap LP earns 2 bps of volume, and lp-fees-1 found fee minus IL at -0.264 per SOL-day. A DAMM v2 LP earns the trading fee less the protocol's 20%. On DBC-migrated pools that fee is set by the launchpad's config: flat options up to 6% (CHANGELOG 0.1.1), and since 0.1.9 a market-cap scheduler. So the LP fee per unit of volume is 10-240x PumpSwap's (0.8 × 0.25% = 20 bps up to 0.8 × 6% = 480 bps, against 2 bps). Fees accrue to a resting position; arriving late only loses the fees already paid. The counterparty is everyone who trades the pool, snipers above all.

**Main risk.** IL. A full-range LP worth V0 is worth V0 × sqrt(r) in SOL when the price ratio is r: -29% at r 0.5, -55% at r 0.2. dbc-d5 records a 1 h time-only mean of -0.204 on migrated pools. Fees must beat that. Entry also needs base tokens: a 0.125 SOL buy in a ~10 SOL pool costs about 2.5% impact plus the pool fee. dbc-d5 found that only 0.5% of pools let a 0.25 SOL buy move the spot by 2% or less.

**Decisive cheap test** (exploration: DBC [2026-10-14T00, 10-17T00) and the P16 DAMM v2 histories at `/data2/mal/venue-tape-backfill/meteora_damm_v2/pools/`, clipped at 10-17T00; disclosed exploration, never a gate):
0. Desk, before any count. Confirm from the DAMM v2 IDL that `create_position` / `add_liquidity` are permissionless on migrated pools, and that the swap event carries the fee amount. If either fails, kill.
1. Counts, before any price. Per fetched pool: the effective fee rate (fee / input amount) at migration+10 min, the collect mode, and turnover (6 h volume / quote reserve at +10 min). Report the coverage bias: 2,288 of 6,458 migrated pools were fetched, and strand (hot) pools were fetched first, so counts are an upper bound.
2. One pricing, declared before it runs. LP 0.25 SOL total. Buy base for half on the pool, landing at migration+10 min+1.9 s, at fee and impact. Add liquidity, remove at +6 h (or earlier, at the 10-17T00 clip, disclosed), sell the base. Charge two extra sends (add and remove) at 160,000 lamports. Apply the flat-15% and pressure-fail models to the buy and the add. Fees are credited pro rata to our share of liquidity, print by print.

**Data.** MAL has the 2,288 pool histories (P16), a DAMM v2 decoder (tools/test_damm_decode.py) and the migration-to-pool link. Whether the fee field and liquidity are in the venue_tape rows is not established here. There is no LP executor, and no DAMM v2 sell path either (QP-RULING-1024-p20 (a)2).

**Kill rules (fixed now).** K0: fewer than 10 pools/day with fee ≥ 0.25% and 6 h turnover ≥ 2. K1: at the one pricing, mean ≤ 0 under either fail model, or ex-top-3 ≤ 0, or fewer than 2 of 3 days positive. K2: if 95% or more of the P&L comes from pools whose price rose (that is directional exposure, not fee capture). Any kill is final; no second fee band or horizon on these hours.

**Days to a forward clock.** Screen 3-4 days. No DBC/DAMM hour at or after 2026-10-25T00Z is touched before 11-01T00:10Z. A paper LP executor is a new build. Earliest clock about 11-03 to 11-05 (8-10 days). With 20 pools/day, the gate's 100 trades on 5 days comes about 5-7 days after the clock.

### 3.2 p37-stall-to-complete: stalled near-complete curves held into graduation

**Mechanism.** Completing a pump curve triggers BOOST, about 17.6 SOL of protocol buys over about 340 s. That benefits whoever holds supply, so a curve stalled near the threshold has holders with a reason to finish it. Buy a clean curve that has sat at 90% progress or more for at least 20 min, moving less than 2 pp. On the pump curve the price is ∝ vSOL², with vSOL going from 30 to 115. So completion from p 0.9 adds (115/106.5)² = +16.6% before any post-migration move. A 0.25 SOL buy at p 0.9 moves the curve by (106.75/106.5)² - 1 = 0.47%. Failure is costly: a curve sliding back to p 0.5 is at (72.5/106.5)² = 0.46, or -54%. With a typical failure loss of -30% and fees of about 2.5%, break-even needs P(complete) of about 0.65 or more.

**Why it is not the dead H1 (lib-b-B5).** H1 died on the race: 35-74% of triggers completed before a 1.3 s landing. A 20-min stall removes the race by construction, and the race share is a kill rule below.

**Decisive cheap test** (exploration: explore-0814, fresh-0903, exp011-0909; pump bonding rows):
0. Data quality first, as in bc-bigwin-2. Keep only clean curves (bonding reserve starts near 30 SOL and stays in band). Measure late-curve print coverage: bc-runup-1 found it thin (3.3% usable on minute bars). If fewer than 50% of triggers have print-level coverage over the 20-min stall window, stop and report "no data".
1. Counts. Triggers/day per block. The share of triggers completed within 1.9 s of the decision (the race share). P(complete within 6 h | trigger). Completion is a D1 outcome on Aug/Sep exploration hours, which is allowed. It is declared here as a count gate, not a price.
2. One pricing. 0.25 SOL. Entry at decision + 1.9 s. Exit, if completed, at the first PumpSwap print at or after s0 + 360 s, with V applied to the sell. Otherwise sell on the curve at 6 h. 160,000 lamports per send, both fail models.

**Data.** MAL has the pump bonding rows and PumpSwap prints on all three blocks. The print-level layer for the 30 min before completion is not built (bc-runup-1 next step). That is the main build risk.

**Kill rules (fixed now).** K0: median triggers/day < 8. K0b: race share > 10%. K0c: P(complete ≤ 6 h | trigger) < 0.5. K1: at the pricing, mean ≤ 0 under either model, CI90 lower bound ≤ 0, ex-top-3 ≤ 0, or not a majority of the 26 days positive. The rule is not re-tuned on these blocks after any count.

**Regime caveat.** Synthetic migrations since 10-08 (P25 C2) changed how curves complete. An Aug/Sep pass may not carry forward; a forward book must show its own day-1 dry count.

**Days to a forward clock.** Screen 2-3 days. Forward: arena book only, clock at merge + 75 h. The exit sits after the first 360 s of a V-range pool, but the position is open through that window. QP must rule whether that touches the EXP-024 Look-2 seal (to 2026-11-14T00:00Z). If it does, the clock waits until 11-14. Otherwise 6-8 days.

### 3.3 p37-curve-floor: the pump curve's start price as a bounded mark

**Mechanism.** A pump curve cannot trade below its start price. Buy a clean curve that once reached peak progress of 30% or more and is now back at 1% or less, which puts the spot at most (30.85/30)² = 1.0575x the start. Require at least 30 min since the peak and at least 10 min of quiet. Hold 24 h, tp +100%, no stop. Worst mark loss is about -5.4% plus about 2.5% fees, 1.6% impact at 0.25 SOL, and 0.13% sends, so about -9.6%. A revival to p 0.3 is (55.5/30.85)² = 3.24x. Break-even needs P(tp) of roughly 3-7%. It is FLOOR's shape on the curve's own floor.

**Why it might not repeat p20-dbc-start-floor.** That screen failed: n 150, flat -0.0070/trade, 1 of 10 days positive, 97-100% of trips ending at the time cap. Pump curves have far more traffic than DBC pools. That is the only reason to test this; it is a weak one.

**Decisive cheap test** (explore-0814, fresh-0903, exp011-0909):
1. Counts. Triggers/day after the bc-bigwin-2 clean-curve filter, one entry per mint. The share of triggers that later reach graduation (descriptive, D1 exploration).
2. One pricing as declared above, both fail models, 160,000 lamports per send.

**Data.** MAL has it (the bonding rows on the blocks that bc-bigwin-1 used). Bonding stops are not needed (no stop).

**Kill rules (fixed now).** K0: median triggers/day < 20. K1: 95% or more of trips end at the time cap (the dead-pool pattern of p20-dbc-start-floor and lib-b-E14), checked before the mean is read. K2: at the pricing, mean ≤ 0 under either model, CI90 lower bound ≤ 0, ex-top-3 ≤ 0, or not a majority of days positive. No band or cap retune on these hours.

**Days to a forward clock.** Screen 2-3 days. Arena book at merge + 75 h. Pre-graduation (D1) rows only, so there is no D3 contact; 5-7 days.

## 4. Honest assessment

- Nothing in this sprint clears the capacity screen on evidence. Three ideas clear it on my guessed edges, and each has its nearest evidence against it:
  - DAMM LP: lp-fees-1 (IL beat fees on PumpSwap) and the -0.204 1 h collapse on migrated pools.
  - Stall-to-complete: lib-b-E4 and bc-runup-1.
  - Curve floor: p20-dbc-start-floor.
- My probability that at least one of the three survives its first screen: about 20%. My probability that any reaches a gate PASS: under 5%.
- The one genuinely new economic fact is the fee level. A DAMM v2 LP on migrated DBC pools earns 20-480 bps of volume, against 2 bps on PumpSwap below 420 SOL mcap. That makes DAMM LP the only idea here where the passive side, not the taker side, collects the fee.
- What would change the ranking:
  - A gate PASS on DBC m10 (11-01) raises #1, because the same DBC/DAMM tooling and executor work serves both.
  - A FAIL lowers DBC-venue priority overall.
- Not done here: no count, no price, no config read, no QP ruling. The QP questions are the first-360 s question for #2, and whether #1's E1 use adds to the DBC multiplicity count m.

## Sources

- [pump-public-docs (README, change notes)](https://github.com/pump-fun/pump-public-docs)
- [Meteora DBC CHANGELOG](https://github.com/MeteoraAg/dynamic-bonding-curve/blob/main/CHANGELOG.md)
- [Meteora DAMM v2 fees overview](https://docs.meteora.ag/core-products/damm-v2/fees/overview)
- [Meteora anti-sniper suite for DBC](https://docs.meteora.ag/meteoras-anti-sniper-suite-a.s.s./meteoras-anti-sniper-suite/a.s.s.-for-dbc)
- [cryptobriefing: callout rewards tied to follower profits](https://cryptobriefing.com/pump-fun-callout-rewards-follower-profits/) [secondary]
- [Medium/coinmonks: pump.fun referral programme fine print](https://medium.com/coinmonks/pump-funs-new-50-referral-program-what-the-fine-print-actually-says-62e4658a9700) [secondary]
- [tokenpost: EVM limit orders](https://www.tokenpost.com/news/investing/39181) [secondary]
- [chaincatcher: Monad token purchases](https://www.chaincatcher.com/en/article/2298724) [secondary]
- [crypto.news: Pump Bridge](https://crypto.news/pump-fun-launches-pump-bridge-with-faster-crosschain-trades-and-fewer-errors/) [secondary]
- [Solana Compass: Solindex](https://lang.solanacompass.com/projects/solindex) [secondary]
