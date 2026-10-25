# P26 DECLARE: p25-pump-order-keeper-absorb. COUNTS ONLY. Written before any count.

Declared at (date -u): 2026-10-25T13:17Z. Lane P26, manager15. Exploration, no pricing, no P&L, no returns. 0 Helius credits.

## What was done before this file
- Desk search (one public web query): no source describes how pump app stop-loss/take-profit orders execute (escrow, off-chain watcher, signer, memo, fee transfer). **No tag rule is known.** No getTransaction read was made (no known app-order signature to read).
- One schema peek: first 3 rows of trades-2026-09-03T12 (w3), to learn field names. No count.

## Window and ledger check
- fresh-0903 `[2026-09-03T12, 2026-09-09T12)`, clean copy /data/mal/blocks-clean/fresh-0903/w{1,2,3}/trades. docs/HOLDOUT_LEDGER.md row "Fresh confirmation block": status **exploration pool**, spent by EXP-012's one read and moved to the exploration pool; "never a confirmation holdout again". Allowed for exploration. It begins after the 2026-09-02 app launch. No October hour. No arena tape. No sealed path opened.

## Rule (tag-free proxy, because no tag exists)
A tagged-sell cluster is a subset of all sell clusters, so an untagged count is an UPPER BOUND of the tagged count.
- Sell = trade row with side == sell, venues pump_bonding and pumpswap.
- Cluster = >= 3 sell rows in one mint within a rolling 10 s (block_time seconds). One cluster event per mint, then a 30 s refractory period for that mint.
- Impact bar: pre-entry impact of a 0.25 SOL buy approximated as 2 x 0.25 / quote_reserve_SOL (constant-product small-trade form, reserve from the last sell of the cluster) <= 2%. This is a capacity screen on reserves, not pricing or a return.

## Counts to make
Per UTC day and per hour: all trades, sells, sells/day, share of sells, distinct mints with sells, cluster events, clusters passing the impact bar, distinct mints with a cluster, split by venue. Not made: any price path after the cluster, any exit, any P&L.

## Kill rules (fixed now)
1. Untagged clusters passing the bar < 20/day (median day): DEAD outright (the tagged subset is smaller still).
2. < 5 cluster events/day in any of the 6 days: dead (brief rule).
3. If the untagged bound passes, the idea is NOT cleared: the tag does not exist yet, so "tagged sells >= 100/day" is UNTESTABLE and the status is BLOCKED-ON-TAG (not "price next"). Revive only if a tag rule is found from public docs or public getTransaction reads (cap 2,000 calls, none used here).
4. A big untagged cluster count is not evidence for the idea: dump-absorb-1..6 and hunt29-flush-1 already tested untagged cascades and failed.

## Capacity screen (guess, from IDEAS.md)
50 entries/day x 0.25 SOL x +1% net = 0.125 SOL/day (about $18.75 at SOL = $150). Impact at size: on bonding reserves of ~30-80 SOL a 0.25 SOL buy costs about 0.6-1.7% by itself, the same size as the guessed +1% net edge. After counts, the reserve split of passing clusters is reported. No profit claim; no book has cleared the promotion gate.
