# 1. How MAL finds an edge

> **In short**
> MAL replays real Solana trades through an honest simulator that charges real fees and lets some orders miss, under two different "what if sending is slow" assumptions. A trading idea only counts once it clears a fixed checklist — the promotion gate — on data it has never seen before.

This is the pipeline end to end: **trade tape → honest simulator → signals → forward paper → gated live**. Nothing skips a step, and nothing goes live until a book clears the gate below and you approve it.

## The trade tape

Everything starts with a record of what actually happened on chain: every pump.fun bonding-curve trade and every PumpSwap trade after a token graduates ("migrates"), with the wallet, the side, the SOL and token amounts, the slot, and the time MAL's own listener received it (`t_recv_ms`).

MAL captures this for free, from the public Solana RPC `logsSubscribe` feed, not a paid vendor. The first real measurement window (31.4 minutes, 2026-09-25) logged 496,286 trades, matched 90.9% of new token creates to a first bonding trade, and had zero dropped connections (`ARTIFACTS/lab/trade-tape.md`). A same-window shadow test against Helius's paid feed found the two nearly identical — Helius matched 99.0% of the public feed's trades — so MAL stayed on the free tape (same file). This tape is the "ground truth" every later number here is computed from: if it were wrong or incomplete, everything downstream would be wrong too.

The tape is sealed hour by hour into read-only files. Once an hour is sealed, nobody edits it — a strategy is tested by replaying those exact sealed hours, never by re-running against a live, moving feed.

## The honest simulator

A backtest that assumes every order fills instantly at the quoted price is not a backtest of a real trading bot — it is a backtest of a fantasy. MAL's simulator tries to close that gap in three ways:

**Fees.** On the frozen "migrate" trade (buy right after a token graduates to PumpSwap, sell on a fixed rule), the realized round trip pays **2.511%** of size in venue fees alone — the exchange's own cut on both the buy and the sell leg (`ARTIFACTS/lab/fee-audit-2026-09-27.md`). That number was checked against the actual on-chain fee accounts, not just the code's assumption: a buy at the graduation market cap (410.88 SOL) pays 1.25%, a sell at +50% (616.32 SOL) pays 1.20%, and both match what the simulator already charges to the decimal point (0.00 points of difference, same file). Fees are the largest cost on this trade — a cost audit found venue fees are roughly 85–90% of total round-trip cost (PR #142).

**Slippage.** Orders are capped at a 15% slippage tolerance (`DEFAULT_SLIPPAGE_CAP = 0.15`, `ARTIFACTS/lab/migrate-direct-prereg.md`). If the price has moved further than that between decision and execution, the simulator does not pretend the order filled anyway — it records a miss.

**Fills.** The simulator models exactly when in the block sequence an order would land — for the current frozen cell, "slot+1 start" (the market state as of the first print with `slot < migration_slot + 1`, priced with the priority fee that would typically win the slot, 0.0005 SOL per side). Not every attempt lands: on the frozen migrate cell's holdout read, only about a third of attempts filled (32–40% depending on priority, `ARTIFACTS/lab/fee-audit-2026-09-27.md`, `migrate-direct-oos.md`). A model that only reports the trades that happened to fill, and quietly drops the misses, would look far better than a real bot ever would. MAL's constitution requires every reject and every runner to stay in the book (rule 9, "full detect book," `CONSTITUTION.md`) — misses are counted, not deleted.

## Two fail models, and why both

Even a "would it have filled" answer is not the whole story: the real risk is that *sending* the order competes with other bots for the same block, and that competition gets worse the more the trade is worth sending. MAL scores every candidate under two different assumptions about that risk, and a trade only counts if it works under **both**:

- **Flat 15%.** A simple, size-blind assumption: 15% of sends fail outright, no matter the trade's size or urgency. Easy to reason about, but it doesn't capture that a juicier trade attracts more competition.
- **Pressure, scale 1.** A fitted curve (`mixed_net` in `tools/latency_curve.py`, intercept −1.4548727851312098, slot slope 0.8, SOL slope 0.35) that makes the failure probability rise with slot delay and position size — a bigger, more urgent order is more likely to get crowded out. This was fit once, on real landed-vs-missed data, and then frozen — it is never refit on the data a candidate is being tested against.

**Why both, not just one:** they can disagree, and disagreement is exactly what you want to see before trusting a number. On the frozen migrate cell's own selection grid, the closest a single cell got to clearing the bar was flat net **+0.47%** against pressure net **−0.013%** at the same settings (`ARTIFACTS/lab/latency-curve-2026-09-27.md`) — positive on one model, negative on the other. A model that only wins under the flat model's simple, size-blind assumption is quietly betting your size doesn't matter to the market — checking both is the cheapest way to catch that bet before it costs real SOL.

## What "SOL per trade after fees" means

This is the single number every gate check is built from: for one round-trip trade (a buy and its matching sell, or a "miss" that pays only the priority fee and nothing else), how much SOL is left after subtracting venue fees on both legs, the priority fee paid to try to land the order, and — under the pressure model — the chance the send itself failed. It is reported per trade (mean and median), and summed across trades as a total. A book's "mean SOL per trade" is the average of that after-fees number across every out-of-sample trade it took, wins and losses and misses together — not just the winners.

## The promotion gate, item by item

A book is not called a result — let alone something worth watching, let alone something worth trading — until it clears every one of these, under **both** fail models (`CONSTITUTION.md` rule 8, "kill-attempt before promote"; `LAB_STATE.md` "Promotion gate"):

**At least 100 out-of-sample trades.** A handful of trades can look like anything. 100 is the floor before the average stops being mostly noise. *Why this rule exists:* MAL's frozen migrate-direct cell's very first out-of-sample read had only 60 trades pooled across two days (`ARTIFACTS/lab/migrate-direct-oos.md`) — far short of both the trade-count and day-count floors, and it still would have been tempting to read something into +0.51% flat net if the gate didn't exist to say "not enough data yet."

**At least 5 distinct UTC days, with a majority positive.** One good day is a good day, not an edge. Requiring 5 days, most of them positive, is a cheap check against a single lucky afternoon. *Why:* that same early migrate-direct read had exactly 2 days, one positive — the gate would reject it on day-count alone even before looking at the CI.

**Lower 90% CI bound of mean SOL/trade > 0** (1,000 bootstrap draws, seed 1, 5th percentile). This resamples the actual trades 1,000 times (same random seed every time, so it's reproducible) and asks: even in an unlucky 5% of those resamples, is the average still above zero? A positive raw mean with a negative CI lower bound means the result could easily have been luck. *Why:* the formally-failed migrate-direct cell posted flat net **−0.0906%** and pressure net **−0.1682%** on its 2026-09-28T21:00Z one-shot out-of-sample read, with "every CI lower bound < 0" (`LAB_STATE.md`) — the CI check is what turns "the average looks bad" into a documented, reproducible fail rather than an argument about one number.

**Total SOL still positive after removing the top 3 trades (ex-top-3).** If three trades are propping up an otherwise flat or losing book, it is not a repeatable edge — it's a few lucky moonshots. *Why:* the trailing-stop exit looked good pooled — pressure total **+7.217 SOL** — but with its top 3 trades removed (one alone worth +16.58 SOL flat) it fell to **−8.048 SOL** (`ARTIFACTS/lab/exploration-exits-2026-09-28.md`, detailed in [doc 2](2-how-we-avoid-fooling-ourselves.md)) — exactly the failure mode ex-top-3 exists to catch.

Clearing all four, under both fail models, still isn't a promote by itself if the read shares a sitting with other candidate books — see the multiplicity section in [doc 2](2-how-we-avoid-fooling-ourselves.md). And clearing the gate on backward historical data earns a **forward-paper book**, not live trading (see below).

## The path: tape → simulator → signals → forward paper → gated live

1. **Trade tape** — the sealed, real record of what happened on chain (above).
2. **Honest simulator** — replays a candidate rule (an entry filter, an exit rule, a size) against sealed tape with real fees, real slippage, real fills, both fail models.
3. **Signals** — a candidate entry or exit rule, screened on exploration data, then **pre-registered** (see doc 2) before it may touch any fresh, never-read block.
4. **Forward paper** — a pre-registered book that clears the gate on backward data gets a paper book on the forward-paper runner: still no real money, scored the same way, on new data as it arrives.
5. **Gated live** — only after a forward-paper book independently clears the same gate (about 7 days of forward paper is the current live bar, `LAB_STATE.md`) does live trading become a question — and it still needs the owner's explicit yes. The owner's profit target is not evidence for any of these steps; it's a target, not a measurement (`CLAUDE.md`).

As of this writing, the frozen migrate-direct cell is dead (it failed its one-shot OOS read on both models), and the current candidate, **EXP-011**, is a pre-registered entry-selection model whose one holdout read is scheduled once its backfill finishes (`LAB_STATE.md`, `EXP/EXP-011-migrate-entry-model-prereg.md`). It has not been read yet as of 2026-09-30.

## Glossary

- **Trade tape** — the sealed, timestamped record of real on-chain trades that every backtest replays.
- **Sealed hour** — an hour of tape that is finalized and read-only; nothing is edited after sealing.
- **Honest simulator** — a backtest that charges real venue fees and priority fees, respects a slippage cap, and can miss a fill — it does not assume every order lands at the quoted price.
- **Slippage cap** — the maximum price movement (15% here) an order will tolerate before the simulator records a miss instead of a fill.
- **Fill / miss** — a fill is an order that lands within the slippage cap; a miss is one that doesn't and pays only the priority fee.
- **Flat 15% fail model** — every send has a flat 15% chance of failing to land, independent of size or timing.
- **Pressure fail model (scale 1)** — a fitted curve where the chance of a send failing rises with slot delay and position size; frozen, never refit on the data being tested.
- **Round trip** — one buy and its matching sell (or a miss, which pays only the priority fee on one side).
- **SOL per trade after fees** — the net SOL left on one round trip after venue fees, priority fees, and the fail model's penalty; the base unit of every gate check.
- **Promotion gate** — the fixed checklist (≥100 trades, ≥5 UTC days majority positive, 90% CI lower bound > 0, ex-top-3 total > 0, under both fail models) a book must clear before it counts as anything more than a hypothesis.
- **Out-of-sample (OOS)** — data a candidate was not tuned on; the only kind of data the gate checks against.
- **Ex-top-3** — the book's total after removing its 3 best trades; catches results that are really just a few lucky moonshots.
- **90% CI lower bound** — the 5th percentile of 1,000 seed-1 bootstrap resamples of the mean; must be above zero.
- **Forward paper** — a book that cleared the historical gate, now run live against new incoming tape, still with no real money.
- **Gated live** — real-money trading, which requires a forward-paper book to independently clear the same gate and the owner's explicit approval.
