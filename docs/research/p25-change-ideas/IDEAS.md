# P25 change-capture ideas (lane P25, manager15 push, 2026-10-24)

The owner's thesis: edges come from recent changes (regimes, program redeploys, new mechanics) and fade, so be first. This lane lists what changed on Solana meme venues in the last ~4 weeks, who each change forces to trade or leads to misprice, and 8 new strategies built on those changes.

**Method.** Desk work, the public web, and counts only. The lane ran no pricing and read no tape row or outcome. It used 0 Helius credits and ran no MiScusi job. It opened no shadow output, ledger, bots.json, probe output, fwdv1/holdout directory, or gap row.

**Inputs.** CLAUDE.md; LAB_STATE "Now"; docs/HANDOFF.md §1-4; docs/library (all rows); hunt29/p13/CENSUS.md; docs/research/venues-new-tape-2026-10-24.md; dbc-p14, dbc-p17 and p20-ideas; docs/HOLDOUT_LEDGER.md; DEC-031 (Amendment 3 item 1, Amendment 4 item 4, Amendment 10); the A3 program-upgrade reviews of 10-09, 10-13, 10-14 and 10-21.

**The one local count.** It was taken on `docs/research/dbc-p17/data/cfg_summary.json`, which holds DBC config accounts. These are structure, not tape: no price and no outcome. The count is in change C9.

`date -u` read 2026-10-24T17:57:42Z before this file was written.

**Nothing here is a result.** Every capacity figure is a guess, and its arithmetic is shown. No sentence says that any idea makes money, and no book has cleared the promotion gate. Dollar figures assume SOL = $150. That is the same assumption as hunt29/p5/STACK.md and P20, not a market quote. Web facts marked [secondary] come from press or aggregator pages, not from the venue's own docs.

## 1. Change log, 2026-09-26 .. 2026-10-24

| # | When (UTC) | What changed | Source |
|---|---|---|---|
| C0 | 2026-09-02 (announced); EVM extension 10-22 | Pump app limit orders on Solana: take-profit and stop-loss sells at user price levels. The execution mechanism is not public. | [secondary] phemex (Sept), cryptonomist 10-22, chaincatcher 10-23 |
| C1 | 2026-10-02 (pump deploy, signer `6W6qsDbo…`) | Program write. Content not reviewed separately; the 10-09 review names it only as the previous deploy by the same signer. | ARTIFACTS/lab/a3-repin-2026-10-09-program-upgrade-review.md §1 |
| C2 | 2026-10-08T16:20:17Z (pump slot 454596459, PumpSwap 454596406) | New `MultiHopCurveSwap` / `MultiHopSwap`. Pump-coin quote mints (custom pairs quoted in a pump coin). `Global.max_curve_depth` = 1. **Synthetic migrations** (a PostCompleteBuyEvent in the curve-completing tx) went from 0/61 graduations before to 12/61 after. Completions moved to BuyV3 / BuyExactQuoteInV3. | same review §1, §5 |
| C3 | 2026-10-09 ~14:35Z (epoch 1053) | Slot time 200 ms, the last of four SIMD-0525 steps (250 ms from 09-18). The per-block limit is 50M CU (was 62.5M at 250 ms). First full epoch averaged 218.3 ms. An epoch is now ~1.08 days (epoch 1056 started 10-12T21:18Z, epoch 1064 at 10-21T13:36Z). | everstake 2026-10-12; solana.com/upgrades/alpenglow; solanacompass |
| C4 | 2026-10-12T17:40:24Z (fees program slot 456132171) | Fees program redeployed (new signer `5sFJMw5d…`). Fee configs unchanged. Content of the change: not established. | a3-repin-2026-10-13-fees-program-upgrade-review.md §1 |
| C5 | 2026-10-13T15:40:41Z (pump slot 456494698) | `max_curve_depth` 1 → 2: depth-2 pump-coin quote chains (`create_v2`). No IDL layout change. 0 of 60 sampled graduates were depth 2. | a3-repin-2026-10-14-pump-program-upgrade-review.md |
| C6 | 2026-10-15 (DAMM v2 0.2.4 → 0.2.5, slot 457,080,014) | No IDL item changed. | [secondary] GitHub FunnyCircuitBoard/Vicinity-Launchpad PR #78 |
| C7 | 2026-10-16 (DBC 0.2.1 → 0.2.2, slot 457,502,600); EvtSwap3 from ~10-16T04Z | New `create_config2` for a Token-2022 base mint with a transfer fee. `PoolConfig.version` is now 1, and padding became four transfer-fee fields. Every swap emits **EvtSwap3**; Swap2 events stop. Swap3 adds `fee_on_base_token`, `swap_mode` and transfer-fee amounts. About +3,500 CU per swap. MAL's own P14 tool mislabelled every Swap3 row as a hook pool. | PR #78 [secondary]; docs/research/dbc-p17/REPORT.md §1 |
| C8 | 2026-10-16/17 | Pump.fun perpetuals via Hyperliquid: BTC/ETH, up to 40x, builder fee up to 0.10%. No meme-coin perp found. | [secondary] cryptobriefing, kucoin |
| C9 | 2026-10-17 | Pump app/web: the zero-fee period (since 08-07) ended. A **0.45% platform fee** applies (0.40% with a referral), paid out to referrers and callout rewards. Bonding fee 1.25% and PumpSwap fees unchanged. | [secondary] cryptobriefing 2026-10-16 |
| C9b | 10-17 .. 10-23 | DBC configs with a Token-2022 transfer fee: **42 of 7,399** (P17 config summary; lane count): 25 bps ×1, 100 ×9, 150 ×2, 200 ×7, 250 ×8, 267 ×1, 300 ×2, 350 ×1, 400 ×1, 450 ×1, 500 ×3, 1000 ×6. 290 configs are `ConfigWithTransferHook`. Quote mints: WSOL 6,117, USDC 297, MET 90, JUP 51, and xStock-like mints (`Xs…`) among the top 8. | local count on cfg_summary.json (structure) |
| C10 | 2026-10-19T15:46:16Z (pump slot 458888140) and 17:47:02Z / 17:47:20Z (pump 458921440, PumpSwap 458921517) | New Token-2022 **transfer-fee quote** support (new error `QuoteMintNotWritable`, `transfer_fee.rs`). pump TradeEvent and PumpSwap Buy/Sell events gained **8 trailing bytes**. They are 0 until 17:47Z, then non-zero on 131 of 15,572 sampled events (0.84%); their meaning is not public. Not flagged separately by A3. The frozen decoder is unaffected. | a3-repin-2026-10-21-program-upgrade-review.md §2, §5 |
| C11 | 2026-10-19 (announced; press 10-20T00:09Z) | **PreStocks custom pairs** on pump.fun: eight pre-IPO tracking tokens usable as quote assets (OpenAI, Anthropic, Polymarket, Kalshi, Figure AI, Neuralink, Anduril, Oura). Restricted for US persons. PreStocks reportedly trials a 0.5% transfer fee; users report 3-5% fees on the tokens. In May 2026, Anthropic and OpenAI called the SPV transfers behind the tokens invalid [secondary]. Matches C10's code change in time; the link is the lane's inference. | [secondary] coinpedia 2026-10-20, The Defiant, TradingView/CoinMarketCal 19 Oct |
| C12 | 10-20 .. 10-22 | Synthetic share on A3 runs: 2/19 (10-20) → 7/20 = 0.350 (10-21) → 8/19 = 0.421 (10-22, the H5 a2 halt). Pooled post-redeploy rate 75/339 = 0.2212. A3 rule replaced by DEC-031 Am.10. | LAB_STATE Now; DEC-031 Am.10; 10-21 review §1 |
| C13 | 2026-10-21 13:36Z (epoch 1064); Alpenglow consensus from slot 459,653,000 (~14:00Z) | **Alpenglow Votor live.** Finality target ~150 ms (was ~12.8 s); one dashboard reading was 202 ms. `confirmed` = `finalized`. Vote txs leave blocks (about 63% of txs before). Validators pay a 0.8 SOL/epoch admission ticket. Fee mechanics for users unchanged. Rotor (propagation) not scheduled. | solana.com/upgrades/alpenglow; solanacompass 2026-10-13 |
| C14 | 10-21 .. 10-22 | **Pump Bridge** becomes the default cross-chain route (mobile), replacing Relay. Company figures: ~2x faster, buy errors 0.9% vs 11.2%. Competitor FOMO launched its own bridge and router on 10-23. | [secondary] crypto.news, coinalertnews 10-22, CMC-AI page |
| C15 | 2026-10-22 | **Meteora DBC transfer fees live** (announcement). Builders may route the fees to holders and "keep or revoke fees after DAMM v2 migration". PreStocks and Tessera tokens usable as DBC quotes. | [secondary] TradingView/CoinMarketCal 22 Oct |
| — | Through 10-21 | **BOOST unchanged** across all redeploys: median budget 17.586 SOL (pin 17.585), slices 30-30.5 (pin 29), last slice 340.5-342 s; boost_enabled = 1. | 10-21 review §1 |
| — | Window | Launch counts: Jumpbit's September ~36,100/day vs MemeFees' 7-day average of 17,726 (week to 10-19, a lower bound). Different methods, so a fall is **not established**. Bitquery: 79% of pump tokens trade only on launch day. | [secondary] crypto.news 2026-10-20 |
| — | Not found | No public change found for Raydium LaunchLab (CPMM-only since 08-17 is older), LetsBonk fees, Jupiter routing, or Jito tips/bundles in the window. The Jito ShredStream shutdown notice (09-05) is older and already in the library. | searches listed in Sources |

**Change cadence.** Pump, PumpSwap or the fees program were written on 5 distinct days in 18 (10-02, 10-08, 10-12, 10-13, 10-19), about 0.28 per day. If that rate holds (a Poisson guess), P(no deploy in a 14-day window) = exp(-0.28 × 14) ≈ 0.02. A book whose counted window ends on any `pins_changed` flag is therefore unlikely to run 14 days unbroken. H5 a1 ended on the 10-19 redeploy, and H5 a2 on the synthetic halt (C12). Whether pins_changed still ends Q50's, HV4's and FLOOR's windows is for the manager to check. This lane did not open their rules.

## 2. Who is forced or misprices, whether MAL can act, capacity guess

Latency means MAL's 1.1-1.3 s detection-to-landing (P15; about 5.5-6.5 slots at 200 ms). Capacity = trades/day × stake × net edge after fees and impact. Every number is a guess.

| Change | Forced or mispricing party | Can MAL act at 1.1-1.3 s? | Capacity guess (arithmetic) |
|---|---|---|---|
| C2/C5 multi-hop, pump-coin quotes | Buying a child forces a same-tx buy of the parent (QSINK). Depth-2 forces the grandparent as well. | Same-tx legs: no lead. Only the slow parent drift could be traded. Already hunt23-w3-qsink-1 (untested, counting). | Not re-estimated (neighbour row owns it). |
| C2/C12 synthetic migrations | Completer bundles buy at s0 and hold inventory into BOOST. | D3 sealed; K4 / boost-universe-4 dead or no survivor. The binding effect is on MAL's own A3 decidability. | 0 (no new trade). |
| C3 200 ms slots | Hot-pool trades queue against a halved per-block CU. The fill price moves more per k slots. | It changes the cost of k, not the sign (SB-H8). | Cost model only. |
| C7 Swap3 event switch; C10 +8 trailing bytes | Third-party bots whose decoders expect EvtSwap2 or exact event lengths (strict Borsh) go blind or misparse until patched. | Yes, if MAL's parity check passes first. MAL's own tools broke too (P14 hook mislabel; A3 halt). | Idea 3 (p25-redeploy-holiday): ~0.029 SOL/day. |
| C0 pump TP/SL orders | Stop-loss holders: price-triggered sells clustered at levels. | Plausible if the cascade lasts ≥10 s and the sells can be tagged. | Idea 2 (p25-pump-order-keeper-absorb): ~0.125 SOL/day. |
| C3/C13 slot time + Alpenglow | Every fee payer. Freed block space may lower the priority fee needed to land within 6 slots. | Not a signal: a cost lever on every line. | Idea 1 (p25-alpenglow-send-cost): ≤ 0.036 SOL/day saved. |
| C9 app fee 0.45% | App retail pays more and may move to terminals. App trades may become taggable by the fee transfer. | Only as a label or veto on existing lines. | Idea 4 (p25-app-fee-retail-tag): ~0.075 SOL/day. |
| C14 Pump Bridge | Cross-chain buyers: the intent is visible on the origin chain before the Solana fill. | Only if the lead is ≥ ~1.5 s. The solver may fill first. | Idea 5 (p25-pump-bridge-lead): ~0.05 SOL/day. |
| C7/C9b/C15 DBC transfer fees | Bots that skip or misprice Token-2022 fee mints. Holders when the fee is cut. Token-2022 fee changes bind about 2 epochs after they are set (lane's reading of spl-token-2022; verify), so a cut is public days ahead. | Yes: the trigger is known in advance. | Idea 7 (p25-t22-fee-epoch-clock): ~0.0225 SOL/day. |
| C10/C11/C15 PreStock quotes | Child buys force PreStock buys in thin pools. A 0.5% transfer fee widens the no-arbitrage band, so dislocations may last longer. | Slow reversion only. Needs quote inventory (boost-custom-v2-2 found no survivor). US-person restriction. | Idea 8 (p25-prestock-quote-band): ~0.025 SOL/day. |
| C2 custom-pair quotes (Sep 9 non-SOL quotes; 10-08 pump-coin quotes) | The SOL cost to complete a child curve moves with the parent's price. Displayed market cap moves too. | Pre-graduation only (D1), on minutes. | Idea 6 (p25-quote-drift-completion): ~0.025 SOL/day. |
| C8 perps, C6 DAMM 0.2.5, C4 fees program | None identified. | n/a | 0 |

## 3. Strategies (all NEW; neighbours named)

### Seal rules that apply to every idea

- **PumpSwap and pump: Aug/Sep.** Exploration-pool blocks only: explore-0814, fresh-0903 (exploration pool), fast `[09-09T12, 09-15T12)`, fast pre-cut, Oracle in-sample. Never fresh-0828, fresh-0808, fresh-0802, Fast EXP-009 or Forward walk 2.
- **PumpSwap and pump: October.**
  - Only the Forward walk row's released domains **D0** (structure counts, slot timing, distinct-trader counts) and **D1** (pre-graduation outcomes), on `[10-02T10, 10-16T01)`, through the catalog roles oct-structure-d0 / oct-pregrad-d1 as MiScusi jobs. Exploration only; released hours never confirm. D2/D3 stay closed.
  - **October arena-tape hours are walk-2-hour information (DEC-030/DEC-031).** A book whose design touches them is an arena book only, forever.
- **DBC.**
  - `[10-14T00, 10-17T00)` is exploration.
  - `[10-17T00, 10-24T00)` is spent for every D1 variant and usable only as disclosed exploration for non-D1 cells with QP's OK.
  - **No DBC or DAMM hour ≥ 2026-10-25T00Z** is opened, counted or fetched before the m10 read (it refuses before 2026-11-01T00:10Z). No cell is registered on that window.
- **DAMM v2.** The 2,288 fetched pools are clipped at 10-17T00.
- **LaunchLab.** `[10-17, 10-24)` is a reserved holdout.
- **Off-venue public data** (EVM chains, account state read today, fee percentiles sampled forward from a declared start): no ledger block. Any forward recorder starts after its pre-registration.

### Ranking

Score = (E $/day × P(first screen survives)) / days to first screen. All inputs are the lane's guesses. P is low on purpose: the library's base rate is about 1 unconfirmed survivor per 15-20 ideas. The gate also needs ≥ 5 UTC days and ≥ 100 trades.

| Rank | Id | E $/day | P | Days | Score | Days to 100 trades at the guessed rate |
|---|---|---|---|---|---|---|
| 1 | p25-alpenglow-send-cost (cost overlay, not a book) | 5.5 | 0.20 | 1.5 | 0.73 | n/a (overlay) |
| 2 | p25-pump-order-keeper-absorb | 18.75 | 0.04 | 1.5 | 0.50 | ~2 |
| 3 | p25-redeploy-holiday | 4.3 | 0.10 | 1.0 | 0.43 | ~18 (40 trades/week, windows only) |
| 4 | p25-app-fee-retail-tag | 11.25 | 0.03 | 2.0 | 0.17 | ~4 |
| 5 | p25-pump-bridge-lead | 7.5 | 0.04 | 2.0 | 0.15 | ~2 |
| 6 | p25-quote-drift-completion | 3.75 | 0.04 | 1.0 | 0.15 | ~20 |
| 7 | p25-t22-fee-epoch-clock | 3.4 | 0.05 | 1.5 | 0.11 | ~35 |
| 8 | p25-prestock-quote-band | 3.75 | 0.05 | 2.0 | 0.09 | ~10 |

---

### 1. p25-alpenglow-send-cost: re-cost the shelf after 200 ms slots and Alpenglow (rank 1; cost overlay)

- **Change.**
  - C3: 200 ms slots since 10-09.
  - C13: Votor since 10-21. Votes leave blocks (about 63% of txs before), and `confirmed` = `finalized` at a ~150 ms target.
- **Mechanism.**
  - If freed block space lowers the priority fee needed to land within k ≤ 6 slots, every line's per-send cost falls. The arena prices 505,000 lamports per send (Amendment 8); exploration prices 160,000.
  - Detection at `confirmed` may also arrive earlier.
  - This is not a P&L source. p6-latency-reprice shows a shorter leg moves the means by ≤ 0.0008 SOL/trade. reprice-160k-l1 moved FLOOR's in-sample flat mean only from 6.17% to 6.42%.
- **Who pays.** Nobody. It is a cost the lab may be overstating, or understating, after the change.
- **Neighbours.** p6-latency-reprice, p6-latency-desk, reprice-160k-*, lib-c-R2.SB-H8, speed-route-4/5, web-landing-1..3.
- **Capacity (guess).** Saving ≤ 2 × (505,000 − 100,000) lamports = 0.00081 SOL per round trip, if 100k lands. A guessed stack of 45 trips/day (FLOOR ~13/day in its screen + Q50 P0 25-46/day) gives 0.036 SOL/day = **$5.5**.
- **Data MAL has.** None needed from the tape.
- **Data needed.**
  - Public `getRecentPrioritizationFees` on pump, PumpSwap and DBC writable accounts, hourly, forward from a declared start: 0 Helius credits.
  - Optionally the public-RPC fee field of sampled landed txs, but only for blocks after the declared start.
- **Seal-safe path.** Forward sampling only. No arena-tape hour before the start; no walk-2 tape. A new stack figure applies to new books only (DEC-031 Amendment 8 item 3: never retroactive).
- **Counts-only first step.** 24 h of hourly samples: p50/p75/p90 fee per CU on the venues' hot accounts, with 160k and 505k shown as a fraction of p75 × MAL's CU. No landing claim without live sends, which need the owner.
- **Kill risk.**
  - Bots take the freed space and fees do not fall.
  - Landing within 6 slots still needs Sender/Jito tips.
  - Even a large saving cannot turn a negative line positive (p6).

### 2. p25-pump-order-keeper-absorb: absorb tagged stop-loss sells (rank 2)

- **Change.** C0: pump app take-profit/stop-loss orders on Solana since 2026-09-02; C9: app fee since 10-17.
- **Mechanism.**
  - A stop-loss sell is triggered by price, not by information, and stops cluster at levels a fixed percentage below entries. A cascade of them can overshoot.
  - If triggered sells can be tagged on chain (an executor signer, a program, a fee transfer or a memo), buy after a cluster of tagged sells in one pool and exit on partial reversion or at a time cap.
- **Who pays.** The stop-loss sellers, who pay impact on a price-triggered sale.
- **Neighbours.** lib-a-A13 / lib-b-E3 (uninformed-sell reversion; walk-forward survivor unconfirmed), dump-absorb-1..6 and hunt29-flush-1 (untagged big-sell and realized-loss cascades, all fail), web-forced-flows-11 (desk-read, execution unknown). New here: a hard label from the order mechanism instead of a modelled one.
- **Capacity (guess).** 50 entries/day × 0.25 SOL × +1% net = 0.125 SOL/day = **$18.75**.
- **Data MAL has.** Sept exploration blocks after the 09-02 launch:
  - fresh-0903 `[09-03T12, 09-09T12)`;
  - fast `[09-09T12, 09-15T12)`;
  - fast pre-cut `[09-19T01, 09-22T00)`;
  - Oracle in-sample `[09-22T00, 09-25T07)`.

  The dump-absorb kit is in tools/arena_screen.py.
- **Data needed.** The tag. Desk work on pump's app docs plus a few public `getTransaction` reads of txs known to come from app orders: 0 Helius credits.
- **Seal-safe path.** Exploration blocks only. No October pump hour. If it later needs October, it is an arena book.
- **Counts-only first step.**
  1. Write the tag rule first.
  2. On fresh-0903, count tagged sells/day, their share of all sells, and clusters (≥ 3 tagged sells in one pool within 10 s).
  3. Drop the idea if there are fewer than 100 tagged sells/day, or fewer than 20 clusters/day that pass a ≤ 2% impact bar at 0.25 SOL.
- **Kill risk.**
  - Orders sign from the user's own embedded wallet with no mark, so there is no tag.
  - Cascades keep going (the dump-absorb result).
  - Fixed-percentage clustering may not exist.

### 3. p25-redeploy-holiday: trade in the competition gap after a layout-changing redeploy (rank 3)

- **Change.**
  - C7: DBC Swap2 → EvtSwap3 at ~10-16T04Z.
  - C10: pump/PumpSwap +8 trailing event bytes at 15:46Z on 10-19.
  - C2: v3 instructions and multi-hop at 10-08T16:20Z.
- **Mechanism.**
  - Bots that parse events strictly go blind, or misparse, until they are patched. Examples: Borsh `try_from_slice` rejects trailing bytes, and listeners keyed to old event names miss new ones.
  - For some hours the fast competition may be thinner. A lab that passes its own decode-parity check first trades with less competition.
  - Today MAL does the opposite: A3 `pins_changed` ends counted windows exactly then, and MAL's own P14 tool broke on Swap3.
- **Who pays.** The early buyers and bots that keep trading on stale or partial data, and the flow that broken snipers would have taken.
- **Neighbours.** lib-a-A17 (change watch, protection only), lib-c-R2.SB-H5 (depth tripwire), launchpad-landscape-4 and speed-route-4 (sniper timing), hunt23-speed-1. None treats a redeploy as a window to trade in.
- **Capacity (guess).** 2 layout-changing upgrades/week × 6 h windows; ~20 trades of an existing kit line per window at +2% better fills × 0.25 SOL = 0.1 SOL/window. That is 0.2 SOL/week ≈ 0.029 SOL/day = **$4.3**.
- **Data MAL has.**
  - DBC exploration `[10-14T00, 10-17T00)`: about 52 h before and 20 h after the switch (/data2/mal/venue-tape-backfill/meteora_dbc/; tools/dbc_decode.py).
  - forward-1002 D0 hours spanning 10-08T16:20Z and 10-13T15:40Z, through oct-structure-d0.
- **Data needed.** None. 0 credits.
- **Seal-safe path.**
  - D0 counts only on pump October hours: distinct-trader counts and slot timing, which the ruling allows; no post-s0 price.
  - DBC exploration hours only.
  - The 10-19 pump change lies in walk-2 time, so it is arena-only and is not used here.
- **Counts-only first step.**
  1. Declare the rule first.
  2. Per new curve (pump `create` / DBC InitializeVirtualPool*), count distinct signers and trades in slots 0-2, 0-5 and 0-10.
  3. Compare the 24 h before with 0-6 h and 6-24 h after each change, with the same hours of a no-change day as control.
  4. Holiday = a ≥ 25% drop in median first-5-slot distinct signers for ≥ 3 h, on ≥ 2 of the 3 changes.
- **Kill risk.**
  - Snipers key on instructions or accounts, not events, and vendor SDKs patch within minutes.
  - Trading in A3-halted windows needs a new DEC.
  - Windows are rare and short (most of the 5 deploy days changed no layout).

### 4. p25-app-fee-retail-tag: app-flow label after the 10-17 fee (rank 4)

- **Change.** C9: the app's 0.45% platform fee from 10-17. Before that, app trades were zero-fee and may carry no mark.
- **Mechanism.**
  - If the platform fee is a transfer to a fixed account inside the trade tx, every app trade after 10-17 is tagged.
  - App users are the least-informed flow. Two uses follow: a veto on kit-line entries when the recent sell flow is app-tagged panic, and an exit into app-tagged buy bursts.
  - The fee may also push app volume to terminals, so the regime shift itself is countable.
- **Who pays.** App retail, now 0.45% poorer per trade.
- **Neighbours.** lib-a-A10 (KOL / app-flow momentum, dead), exit-upgrade-4 (sell into a burst, untested), hunt23-b-flow-V1 (flow-surge veto), hunt23-w2f-* (sell into a surge, no survivor). New here: an exact tag from the fee route.
- **Capacity (guess).** 30 tagged decisions/day × 0.25 SOL × +1% = 0.075 SOL/day = **$11.25**.
- **Data MAL has.** The arena tape after 10-17 (arena-only), which decodes events and probably not system transfers.
- **Data needed.** The fee account, from desk work and a few public `getTransaction` reads; then per-tx fee-transfer presence. Public RPC: about 1 call per sampled tx, 0 Helius.
- **Seal-safe path.** Post-10-17 pump hours are walk-2 information, so any book is an **arena book only**, under DEC-031 Amendment 4 item 4 with a declared cohort window. The tag study alone is a structure count.
- **Counts-only first step.** On one declared closed day of new recorder data, sample 2,000 pump trade txs by public RPC. Count the share carrying the fee transfer. Drop the idea if it is under 5%.
- **Kill risk.**
  - The fee is taken off-chain or inside a router, so there is no tag.
  - App share is small.
  - The arena-only status limits the path.

### 5. p25-pump-bridge-lead: cross-chain intent seen before the Solana fill (rank 5)

- **Change.** C14: Pump Bridge is the default cross-chain route (10-21/22); FOMO's bridge followed on 10-23.
- **Mechanism.**
  - A buy from Base, BSC or another EVM chain is visible as an origin-chain deposit before the Solana fill.
  - If the target mint and size can be read from the deposit and the lead is ≥ ~1.5 s, buy first and sell into the fill.
  - **Policy flag:** this anticipates identifiable user orders. The owner should say whether that is acceptable before any build.
- **Who pays.** The bridged buyer, through a slightly worse fill.
- **Neighbours.** pump-buyback-1 (front-run a protocol buyer; no window found), lib-c-K1 / BOOST (anticipated protocol flow), lib-a-A10 (app flow, dead). No cross-chain row.
- **Capacity (guess).** 50 qualifying bridge buys/day × 0.1 SOL × +1% = 0.05 SOL/day = **$7.5**.
- **Data MAL has.** None.
- **Data needed.**
  - Bridge contracts and the Solana solver: desk work plus public EVM and Solana RPC, 0 Helius.
  - One declared day of forward logging of deposits and fills.
- **Seal-safe path.** The EVM side is public and outside any ledger block. The Solana side of pump trades after 10-16 is walk-2 information, so any book is arena-only. Logging starts after a declared instant.
- **Counts-only first step.** Fills/day; lag from origin inclusion to Solana fill (p10/p50/p90); share of fills ≥ 0.5 SOL. Drop the idea if the p50 lag is under 1.5 s or there are fewer than 20 qualifying fills/day.
- **Kill risk.**
  - The solver fronts liquidity and fills before origin finality (no lead). "2x faster" points that way.
  - Cross-chain volume into pump coins is small.
  - Policy.

### 6. p25-quote-drift-completion: custom-pair curves whose SOL cost to complete falls (rank 6)

- **Change.** C2 and C5: pump-coin quotes (10-08) and depth-2 (10-13), on top of non-SOL custom-pair quotes (tokenized stocks, metals, wrapped BTC/ETH; from 09-09 per web-competitors-6). C11: PreStock quotes (10-19).
- **Mechanism.**
  - A custom-pair curve completes at a threshold in quote units. When the quote asset's SOL price falls, the SOL needed to complete falls. The displayed market cap falls too, which may draw buyers looking for something "cheap".
  - Buy children above a high progress level when their quote fell ≥ X% in the last hour. Exit on the curve at or before `complete` (the D1 rule).
- **Who pays.** Later curve buyers.
- **Neighbours.** web-oct-mechanics-1 (graduation play, untested), lib-c-I3 / G1 (pump-curve pre-graduation runners, dead), hunt23-w3-qsink-1, web-competitors-6. New here: the quote-price trigger.
- **Capacity (guess).** 5 triggers/day × 0.1 SOL × +5% = 0.025 SOL/day = **$3.75**.
- **Data MAL has.** Sept exploration blocks (non-SOL-quote children from 09-09). For external quote assets (xStocks, wBTC), the quote price comes from public price data, outside any ledger block. For pump-coin quotes, the D1 role on `[10-08T16, 10-16T01)` covers only parents still on their curve. A graduated parent's pool price is D2/D3 and sealed.
- **Data needed.** Public minute prices for the external quote assets: 0 Helius.
- **Seal-safe path.** As above. No walk-2 hours.
- **Counts-only first step.** On fast `[09-09T12, 09-15T12)`, count custom-pair creates/day, their completions, and the decisions/day a declared trigger (quote −10% in 60 min, progress ≥ 0.6) would make. Drop the idea under 2 per day.
- **Kill risk.**
  - Too few custom-pair completions.
  - Buyers think in quote units, not SOL.
  - The pre-graduation curve domain is shelved at MAL latency (push round 2).

### 7. p25-t22-fee-epoch-clock: a Token-2022 fee cut announced epochs ahead (rank 7)

- **Change.** C7 and C15: DBC transfer-fee base mints (`create_config2`, 10-16; live 10-22). Builders may revoke after DAMM v2 migration. C9b counts 42 such configs. C3: one epoch is about 1.08 days.
- **Mechanism.**
  - Token-2022 `SetTransferFee` sets a `newer_transfer_fee` that binds about 2 epochs later (lane's reading of spl-token-2022; verify before use). A cut is therefore public about 2 days before it binds.
  - Bots that skip or misprice transfer-fee mints may return once the fee is 0. Holders gain from the cheaper round trip.
  - Buy after the cut is set and before it binds; exit after the binding epoch into returning flow. The trigger is known in advance, so detection latency drops out.
- **Who pays.** Sellers before the cut, who exit while the round trip is still expensive.
- **Neighbours.** p20-dbc-fee-floor-clock (DBC/DAMM fee-scheduler expiry; FAIL in P21), lib-a-A36 (holder-reward basket, dead), web-forced-flows-3 (cliff releases). New here: a Token-2022 fee schedule that is public epochs ahead.
- **Capacity (guess).** 3 cuts/day × 0.25 SOL × +3% = 0.0225 SOL/day = **$3.4**.
- **Data MAL has.** docs/research/dbc-p17/data/cfg_summary.json (configs with `transfer_fee_bps`); tools/dbc_config.py; tools/dbc_cfg_fetch.py.
- **Data needed.** Mint `TransferFeeConfig` for those configs' pools (public `getMultipleAccounts`, 0 Helius) and `SetTransferFee` history per mint (public `getSignaturesForAddress`).
- **Seal-safe path.**
  - Account state read today is structure.
  - Any priced look at DBC `[10-17, 10-24)` is disclosed exploration with QP's OK (not D1).
  - Nothing ≥ 10-25T00Z for DBC/DAMM before the m10 read refuses.
  - A forward recorder starts after 11-01T00:10Z.
- **Counts-only first step.** Count pools on the 42 configs, mints with a pending `newer_transfer_fee` lower than the current fee, and cuts/day. Drop the idea under 1 cut/day.
- **Kill risk.**
  - Cuts are rare.
  - The extension stays after a cut to 0, so extension-filtering bots still skip.
  - Pools are tiny.

### 8. p25-prestock-quote-band: forced PreStock-leg buys against an external anchor (rank 8)

- **Change.** C11: PreStocks quotes on pump (10-19). C15: PreStocks/Tessera as DBC quotes (10-22). C10: Token-2022 transfer-fee quote support. C2: multi-hop routing.
- **Mechanism.**
  - Buying a PreStock-quoted child routes SOL → PreStock → child, forcing PreStock buys in its pump-routed pool.
  - PreStocks also have their own liquidity (an external anchor). A transfer fee of about 0.5% widens the no-arbitrage band, so dislocations may last longer than on fee-less quotes.
  - Sell a PreStock inventory into launch-wave dislocations above the band and rebuy at the anchor.
- **Who pays.** Child buyers, through the parent leg.
- **Neighbours.** hunt23-w3-qsink-1 and hunt23-w3-pump-sink-1 (ride the forced parent buy), boost-custom-v2-2 (standing quote inventory; no survivor), p20-atomic-xpool-bundle, w4-gap-xpool-1 (dead), p20-xstock-offhours. New here: a fee-widened band with an external anchor.
- **Capacity (guess).** 10/day × 0.5 SOL × +0.5% = 0.025 SOL/day = **$3.75**.
- **Data MAL has.** None that is seal-safe for pump after 10-16.
- **Data needed.**
  - PreStock mint list and pools (public RPC);
  - anchor prices (PreStocks / GeckoTerminal public API);
  - a forward logger of pool-vs-anchor gaps from a declared start.

  0 Helius credits.
- **Seal-safe path.** Forward only. Any pump-side book is arena-only. The idea needs the owner's say: PreStocks are restricted for US persons, and the issuers of the underlying shares dispute the SPV structure.
- **Counts-only first step.** One declared day: PreStock-quoted child creates/day and minutes with a pool-anchor gap above band + fees. Drop the idea under 10 such minutes/day.
- **Kill risk.**
  - Child launch volume is tiny.
  - Searchers close gaps in-slot.
  - Holding inventory carries issuer and legal risk.
  - Owner policy.

## 4. Considered and not added

| Idea | Why not |
|---|---|
| H5 or BOOST on synthetic pools | lib-c-R2.SB-H3 and boost-universe-4 (D3 sealed / no survivor). |
| Depth-2 grandparent forced buy | Same mechanism as hunt23-w3-qsink-1; a depth variant, not a new idea. |
| Alpenglow detection-leg shortening as a strategy | p6-latency-reprice: 1.9 → 0.3 s moves no line across zero. Kept only inside idea 1 as a re-measure. |
| Pump perps (C8) | BTC/ETH only; no pump-coin perp. |
| Launch-count fall as a regime gate | Not established (different methods); regime gates are dead (lib-a-A25). |
| Holder-redistribution transfer-fee tokens as yield | lib-a-A36 (holder-reward basket, dead). |
| A bug in transfer-fee quote accounting | Not pursued: that would exploit a defect, not a market. |

## 5. A process note for the manager (not a strategy, no row)

- **Change radar.** The owner's "be first" needs a daily radar over pump, PumpSwap, fees, DBC, DAMM v2, LaunchLab and CPMM. It would cover:
  - programdata slot and sha;
  - event-length histograms (the 10-19 +8 bytes was found 2 days late);
  - config-account version fields;
  - the venues' announcement feeds.

  Each detected change would get a written "who is forced" note and a counts-only template within 24 h.
- **The lab already has the parts:** tools/pump_structure_monitor.py, the program-upgrade-1021 scripts, and venue_tape.
- **Decidability.** By the cadence arithmetic in section 1, books on 14-day windows that end on pins_changed will rarely finish. A DEC that separates a layout-preserving redeploy (decode parity passes) from a mechanic change would keep books decidable through routine deploys. That is the manager's and QP's call.

## Credits and jobs

- Helius credits: 0. MiScusi jobs: none. Local reads: repo docs, plus one structure count on cfg_summary.json.
- Web: 23 searches and 13 page fetches (sources below).

## Sources (public, read 2026-10-24)

- **Solana**
  - [solana.com/upgrades/alpenglow](https://solana.com/upgrades/alpenglow)
  - [Solana Compass: Alpenglow date](https://solanacompass.com/news/anza-sets-alpenglow-mainnet-date-solanas-consensus-switch-is-scheduled-for-october-21-at-about-1450-utc)
  - [Everstake: 200 ms slots](https://everstake.com/resources/blog/solana-block-time-200ms-slots)
  - [Solana Compass: 350 ms step](https://solanacompass.com/news/solana-cuts-slot-time-to-350ms-at-epoch-1020-first-reduction-since-network-launch)
  - [Phemex: Alpenglow live](https://phemex.com/academy/solana-alpenglow-live-finality-tps-impact)
- **Pump**
  - [Coinpedia: pre-IPO pairs (2026-10-20)](https://coinpedia.org/news/pump-fun-spikes-8-after-pre-ipo-token-launch-for-openai-polymarket/)
  - [The Defiant: pre-IPO pairings](https://thedefiant.io/news/defi/pump-fun-adds-pre-ipo-tokens-to-its-launch-pairings)
  - [TradingView/CoinMarketCal 19 Oct](https://www.tradingview.com/news/coinmarketcal:a0b206cba094b:0-pump-fun-prestocks-pairing-launches-with-direct-pre-ipo-token-trading-19-oct-2026/)
  - [Crypto Briefing: 0.45% app fee (2026-10-16)](https://cryptobriefing.com/pumpfun-zero-fee-solana-app-referral/)
  - [Crypto Briefing: perps](https://cryptobriefing.com/pumpfun-launches-perpetual-futures-hyperliquid/)
  - [crypto.news: Pump Bridge](https://crypto.news/pump-fun-launches-pump-bridge-with-faster-crosschain-trades-and-fewer-errors/)
  - [coinalertnews: Pump Bridge 10-22](https://coinalertnews.com/news/2026/10/22/pump-fun-pump-bridge-launch)
  - [Phemex: Solana limit orders](https://phemex.com/news/article/pumpfun-launches-solana-limit-orders-with-takeprofit-and-stoploss-tools-95256)
  - [Cryptonomist: EVM TP/SL 10-22](https://en.cryptonomist.ch/2026/10/22/pumpfun-evm-trading-orders/)
  - [ChainCatcher](https://www.chaincatcher.com/en/article/2298308)
  - [crypto.news: survival and launch counts (2026-10-20)](https://crypto.news/pump-fun-memecoin-trader-fatigue-token-survival/)
  - [CMC-AI updates page (AI-generated, used only as a pointer)](https://coinmarketcap.com/cmc-ai/pump-fun/latest-updates/)
- **Meteora**
  - [Vicinity-Launchpad PR #78 (DBC 0.2.2, DAMM v2 0.2.5)](https://github.com/FunnyCircuitBoard/Vicinity-Launchpad/pull/78)
  - [TradingView/CoinMarketCal: DBC transfer fees 22 Oct](https://www.tradingview.com/news/coinmarketcal:fd5ecbefe094b:0-meteora-dbc-activates-transfer-fees-22-oct-2026/)
- **Lab records**
  - ARTIFACTS/lab/a3-repin-2026-10-09-program-upgrade-review.md
  - a3-repin-2026-10-13-fees-program-upgrade-review.md
  - a3-repin-2026-10-14-pump-program-upgrade-review.md
  - a3-repin-2026-10-21-program-upgrade-review.md
  - docs/research/dbc-p17/REPORT.md
  - DEC/DEC-031-forward-paper-arena.md (Amendment 10)
  - hunt29/p13/CENSUS.md
