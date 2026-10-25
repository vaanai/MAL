# P31 RESULTS: forced / mechanical flow census on fresh-0903. COUNTS ONLY. No pricing, no P&L, no edge claim.

Rules frozen in DECLARE.md (committed and pushed before any sample was drawn). Jobs: #1623 (stage S tape pass), #1624 cancelled at 217 extracts (no wire counter), #1629 (stage F resumed with a wire-attempt cap, then stage A). Code: tools/p31_census.py, tools/test_p31_census.py. Raw output: census.json, denominators.json (public addresses and counts only).
Data touched: fresh-0903 trades (exploration pool, 2026-09-03T12 to 09-09T12), public RPC getTransaction. Not touched: HV4, Q50, HV6, DBC forward, arena tape, meta-capture, any October hour. 0 Helius credits.

## Budget as spent
Wire attempts (retries counted): 1,898 getTransaction attempts (including a pessimistic 600 assumed for the first, uncounted run), 0 getSignaturesForAddress. Under the 2,000 cap. 1,021 sampled signatures got a result row: 932 decoded, 89 unreachable (88 HTTP errors after retries, 1 stopped by the cap), so 8.7% unreachable and never dropped silently. Planned 1,500 samples (A 1,000 + B 500); 1,021 were fetched (shuffled order, so every day is represented: stratum A 643 decoded, B 289 decoded).

## Two facts that bound everything below
1. fresh-0903 has 11.8-15.4 million sell rows per full day (93-95% flagged "burst" by the P26 shape, so stratum B (burst) is nearly the same population as A and adds little). The 2026-09-04..08 PumpSwap share of sell rows is about 88-91%.
2. A 643-row sample can only see a flow of at least about 0.3% of sells (about 35,000 rows/day). P29's keeper is about 3,100 tagged sells/day = 0.026% of sells: expected 0.17 hits in the sample, observed 0 memos in 932 decoded txs. So this census cannot discover rare keepers. It finds only DOMINANT mechanical flows. Rare order executors need an address-first search (known executor address, then its signature history), not a sell-row sample.

## Findings by key
- K1 relayer (fee payer != tape trader): 36 of 643 stratum-A txs (5.6%); 94.4% have fee payer == trader (manual, wallet-signed). Payers with >= 5 hits: `Gygj9QQb...` (17 A + 6 B), `AgmLJBMD...` (6 A + 2 B). Both single or dual signer, no memo, no order-like structure.
- K2 fee wallet (recurring small System transfer destination >= 10 txs): NONE. The most frequent small-transfer destination appears in 7 of 932 txs. Bot fee wallets are spread over many addresses per product (or paid by inner program transfers), so no fee-wallet tag can be read from a sample. No Axiom/Photon/BullX/GMGN/Trojan/BonkBot/Maestro wallet could be identified or labeled (desk search also found no official list).
- K3 memo: 0 memo instructions in 932 decoded txs. No `pump-limit:` (as expected, see fact 2) and no other order memo.
- K4 claim/burn in the same tx as a sell: 0 creator-fee claim, 0 Burn. One `ClaimCashback` (pump cashback accumulator close) inside a Jupiter RouteV2 sell (1 of 643). Claim-then-sell by one wallet is a SEQUENCE across txs and is not measurable from a sell-row sample.
- K5 program set (>= 3% of A): the plain PumpSwap/ATA/Token set (78%, ordinary), and `FLASHX8Dr...` as the only top-level program (20 A + 10 B, 3.1%). Below 3%: `MAyhSmzX...` (17 A, the program behind Gygj9), `6EF8rrec...` (22 A, 3.4% as one member of mixed program sets; not identified), `DF1ow4ts...` (8 A, behind AgmLJ), pump app program `6Vo3245e...` (9 A, 1.4%: the keeper's program, but it also carries manual app trades, so the program alone is NOT a forced-flow tag), Jupiter `JUP6Lkb...` (13 A).

## Table (flow, tag rule, confidence, tagged/day, clusters/day, PumpSwap share)
Tagged/day = stratum-A share x median full-day sell rows (12.1M), Wilson 95% interval. Clusters/day not computable inside the budget for any key (all are 100k+ rows/day; their signature history is millions of entries). Confidence is only that the tag exists as described; no key is shown to be order-executing.
| Flow | Tag rule | Confidence (tag exists / order-executing) | Tagged sells/day (95% interval) | Clusters/day | PumpSwap share |
| --- | --- | --- | --- | --- | --- |
| Relayer `Gygj9QQb...` via program `MAyhSmzX...` | K1 fee payer, single signer, trader != payer | MEDIUM / not shown (no memo, no order structure; looks like a bot-owned program wallet) | about 249,000 (201,000-507,000) | not computable | 0% of sampled hits (bonding only) |
| Program `FLASHX8Dr...` only | K5 top-level program | MEDIUM / not shown | about 334,000 (244,000-576,000) | not computable | 23% |
| Relayer `AgmLJBMD...` via `DF1ow4ts...` | K1, 2 signers, volume-accumulator open/close | LOW-MEDIUM / not shown | about 129,000 (52,000-244,000) | not computable | 75% |
| Fee-wallet tag (Axiom, Photon, BullX, GMGN, Trojan, BonkBot, Maestro) | K2 | NONE found | n/a | n/a | n/a |
| Order memos other than `pump-limit:` | K3 | NONE found in 932 txs | n/a (sample power: cannot see flows below about 35,000/day) | n/a | n/a |
| Creator-fee claim or burn in a sell tx | K4 | 0 hits in 643 (Wilson upper bound 0.6%); sequence form unmeasurable | n/a | n/a | n/a |
| `ClaimCashback` sells (pump cashback) | K4 | 1 hit, LOW | not estimable (1 hit) | n/a | n/a |
| Protocol-scheduled flows (boost slices, buybacks, burns) | desk only | n/a | n/a | n/a | n/a (library already holds them: lib-c-K1, web-forced-flows-9/10/13/15, web-crosschain-1..8) |
Rounded from census.json; projections use the sample share x sell rows, not an event count, and "mechanical" is not "uninformed": a bot program that sells for itself is as informed as its owner.

## Which should join P29's absorb family
Honest answer: none is established. No second order executor was found and none can be found by this sampling design (fact 2).
- Best lead 1: the same pump app program `6Vo3245e...` family is already P29 itself; its volume split (keeper fills vs manual app sells) is what P29's signature join resolves. Nothing to add.
- Best lead 2 (screen, not a book): the relayer-program flows `FLASHX8Dr...` and `Gygj9QQb...` (about 3% and 2.6% of all sells). Whether they are mechanical exits or informed bots is untested; a cheap counts-only follow-up is to page `Gygj9QQb...`'s signature history (one address, fewer than 450 calls if it is a low-count wallet; unknown here) and test whether their sells arrive at fixed offsets from a mint's launch or from a price level.
- Next census design that CAN find rare keepers: address-first. Ask Helm (read-only public txs) for the executor addresses of Axiom/Photon/BullX/GMGN/Trojan limit orders, then page each address's history like P29.

## What this does not show
Counts and a 1,021-row sample only. Nothing about price after any tagged sell. No flow here is shown uninformed. 8.7% of sampled transactions were unreachable on public RPC. Projections are intervals around a share, not event counts.
