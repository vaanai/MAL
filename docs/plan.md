# MAL Plan

## Where we are (2026-09-25)
- Live: free PumpPortal new-token and migration capture on mal-core (~1.2k events/h). Host healthy and nearly idle.
- Not live: trade tape, tracked wallets, wallet graph, LAYA, execution. Postgres tables empty.
- Evidence: two scored days, outcomes for <1% of creates. Rules v1 and v2 lose to random. L3 wallet watch is n≈11–22 and unproven.
- Recent repo work (#49–#72) was schema and receipt locking that cannot produce a return. Stop that.

## Principle
- Measure before building. Every task has to move us toward a yes/no on SOL-after-fees expectancy.

## Phase 1 — Get the data that answers the question (now)
- Full pump.fun trade tape on the host: outcomes for every create, plus a wallet tape for layers 2 and 3.
- Honest re-score of existing experiments (median, SOL after fees, rugs counted).
- Smart-wallet and copy-trade research: how FOMO/GMGN rank wallets and how big our window is.

## Phase 2 — Find a signal (next)
- Daily forward scoreboard from the tape: every create, fixed entry, SOL after fees, several horizons.
- Derive our own smart-wallet list from the tape. Backtest "buy when wallet X buys" against the follower pile-in window.
- Creator/insider/bundle features from the graph as filters, mostly for rug avoidance.

## Direction update (2026-09-25 evening)
- Early bonding-curve trading (seconds after create) looks like a bot-dominated fee-loss game under honest fills. Kept running in background only.
- Main hypothesis now: graduated tokens on PumpSwap (lower fees, deeper pools, minutes-to-hours moves driven by human attention).

## Phase 3 — Decide fast (after a signal survives)
- LAYA on the host, built on LightGBM (entry model + exit model behind the rules risk gate), trained and fed on real tape data. Latency budget measured end to end. See [LAYA engine options](research/laya-engine-options.md).
- Forward paper trading with a risk gate for N days against the owner's live bar.

## Phase 4 — Controlled live (owner approval required)
- Small fixed size, hard daily loss cap, kill switch.

## Parked
- Paid gRPC, colocation, Jito races (that's the MEV game, not ours). X data until a spine filter has a number.
