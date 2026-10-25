# P31 DECLARE: census of other forced / mechanical / uninformed on-chain flows. COUNTS ONLY.

Declared 2026-10-25 (before any sample is drawn or any transaction fetched). Lane P31, follow-up to P29 (PR #931, pump.fun app keeper 64fcNUtA...). Exploration pool only. No price path, no exit, no P&L, no edge claim. 0 Helius credits. Public RPC (api.mainnet-beta.solana.com) only.

## Seals obeyed (hunt30 SEALS-BRIEF)
Only fresh-0903 trades (/data/mal/blocks-clean/fresh-0903, hours [2026-09-03T12, 2026-09-09T12), exploration pool per HOLDOUT_LEDGER). Not touched: HV4, Q50, HV6, DBC forward and fwdv1, arena tape, /data/mal/meta-capture/ (any path), any tape hour >= 2026-10-25T00Z, any October pool. Public chain data is not sealed information.

## Desk read (done before this file)
- One public web search for fee wallets or executor signers of Axiom, Photon, BullX, GMGN, Trojan, BonkBot, Maestro returned no official wallet list (only generic bot descriptions). No wallet is taken from the web. Entities are therefore found EMPIRICALLY from sampled transactions; their names stay "unlabeled" unless a program or memo string names the product.
- One web search for pump creator-fee claims returned the public-docs instruction names (collect_coin_creator_fee, transfer_creator_fees_to_pump_v2, fee sharing config) and nothing on a sell-after-claim pattern. Claim instruction presence is therefore measured from transaction logs.
- Library (docs/library/INDEX.md) already holds, and this lane does NOT repeat: web-forced-flows-1 Jupiter DCA (shelved: orders off-chain), -5 Drift liquidations, -9 Believe buyback, -10 LetsBONK burn, -13 Meteora claims, -14 cook.meme, -15 Virtuals TWAP, web-crosschain-1..8, lib-c-K1 H5 BOOST ride (live canary), l3-boost-* (BOOST variants), p25-pump-order-keeper-absorb (P29 keeper).

## Fact frame (verified in the tape code and one probe)
A fresh-0903 trade row keeps signature, slot, trader (event user, not fee payer), venue, side, sol_lamports, reserves. It has no fee payer, signer list, or outer program, so every tag is made by joining on signature. Public RPC getTransaction serves 2026-09-07 transactions (one probe).

## Method
Stage S (tape pass, no network). Stream all 144 hours of fresh-0903 trades (w1..w3). Per UTC day, fixed seed 31: (a) denominators: sell rows by venue; BURST rows = sell rows in a (mint, 10 s) window holding >= 3 sell rows; (b) stratum A: reservoir sample of 200 sell rows per full day 09-04..09-08 (1,000 total); (c) stratum B: reservoir sample of 100 BURST sell rows per full day 09-04..09-08 (500 total). Dedupe on signature.
Stage F (network). getTransaction (jsonParsed, maxSupportedTransactionVersion 0) for each sampled signature, one call each. HARD CAP 1,500 getTransaction calls plus <= 450 getSignaturesForAddress calls = 1,950 < 2,000. Class-only error handling. A missing tx is counted as "unreachable", never dropped silently.
Stage A (analysis, no network). Per sampled tx extract: fee payer (account 0), signer count, fee payer == tape trader, top-level program ids (excluding ComputeBudget and System), memo strings (digits masked), "Program log: Instruction: X" names, System transfers (destination, lamports).

## Candidate keys (tag rules, frozen now)
K1 RELAYER: fee payer != tape trader. Report each fee payer with >= 5 sampled txs: share in strata A and B, order-like memo presence, tip presence.
K2 FEE-WALLET: a System transfer destination (<= 0.05 SOL, from a signer, not the trader) recurring in >= 10 sampled txs. These are the candidate Axiom/Photon/BullX/GMGN/Trojan/BonkBot/Maestro fee wallets; labeled only if a program id or memo names the product.
K3 MEMO: a normalised memo prefix recurring >= 5 times (P29 `pump-limit:` memos are expected; any OTHER order memo is a new keeper candidate).
K4 CLAIM-IN-TX: a sampled sell tx whose logs also contain a creator-fee claim/transfer instruction (name matches /creator.?fee|claim|collect/i) or a Burn instruction. Same-tx co-occurrence only; a claim-then-sell SEQUENCE by one wallet is not measurable from a sell-row sample (stated limit).
K5 PROGRAM-SET: the set of non-trivial top-level program ids; any set carried by >= 3% of stratum A txs is reported (aggregators, bot routers).

For each key: ORDER-EXECUTING (pre-set SL/TP or limit: relayer fee payer != trader, an order-like memo or dedicated executor program, and a fee transfer) vs MANUAL (fee payer == trader) vs UNKNOWN. Confidence: HIGH only if >= 20 sampled txs share the structure AND >= 90% are consistent; MEDIUM 5..19 or 70..90%; LOW otherwise. Confidence says "this tag exists as described", never "the flow is uninformed".

## Counts to report (per key that clears its threshold)
- tagged sell rows/day: stratum-A share x full-day sell rows, Wilson 95% interval, full days 09-04..09-08, median.
- tagged-in-burst rows/day: stratum-B share x BURST rows/day.
- PumpSwap share: sampled tagged rows on pumpswap / tagged.
- burst clusters/day under the 2% bar at 0.25 SOL: ONLY for keys whose full signature history is pageable inside the 450-call budget (P29 method: signature join, SL60 and ANY10 definitions, vault-only and vault+V); otherwise "not computable inside budget" and the projection stands alone. A projection is a bound with an interval, not a count of events.
Kill rules: a key with a Wilson upper bound below 100 tagged rows per median full day is DEAD for volume. A key not shown to be order-executing is "manual-or-unknown", not a forced flow. Nothing here authorises pricing; a pricing rule needs its own frozen DECLARE and QP.

## Execution
MiScusi jobs (sh, mem_gb <= 6) pinned to machine mal-research-0, git_ref claude/p31-forced-flow: stage S, then stage F (after S), then stage A (after F). Notebook entry before submit. Code: tools/p31_census.py, tests tools/test_p31_census.py. Library rows and rebuilt INDEX.md in the same PR.
