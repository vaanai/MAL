# P82 pre-registration: CASHBACK-FLOOR re-cost of the frozen H5 binding book

Written before any pricing (step 1 reads one on-chain flag per pool and nothing else; no P&L is computed before this file is committed). Library row: p81-cashback-floor (docs/research/p81/IDEAS.md on PR #1036). Exploration only; in-sample for at least 14 families; a positive is a lead, never a pass. No book has cleared the promotion gate. Nothing here changes a running book.

## Facts (docs copy under /data/mal/hunt-1008/swarm-1013/w2-pump-idl/raw/docs, PUMP_CASHBACK_README.md, CLAIM_CASHBACK.md; IDL layout lab note ARTIFACTS/lab/pumpswap-v-layout-2026-10-06.md)

- Flag: PumpSwap `Pool.is_cashback_coin: bool` at byte 244 (is_mayhem_mode at 243). Observed `0001` = cashback.
- The refund is NOT paid in the trade transaction. The creator-fee slice accrues in the trader's `UserVolumeAccumulator` WSOL ATA (Pump AMM program) and is paid only by a later `claim_cashback` transaction (permissionless; the user's WSOL ATA must exist). A trade gets it only if the buy/sell appends the cashback remaining accounts; otherwise the creator fee goes to the creator. So a live refund needs the right accounts on every trade plus claim transactions (base fee, 5,000 lamports each, plus ATA rent once).
- New cashback coins are rejected since 2026-09-12; older ones keep accruing.

## Population (frozen, exploration blocks only)

Same 2,826 trades as P55 (1,702 discovery, 1,124 confirmation; `/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`), pool address from `g_reachable_cap_book_rescore/meta/<day>.parquet`. Seals: no October hour, no arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output.

## Step 1: flag read (counts only)

`getMultipleAccounts` (100 pools per call, about 29 calls, max 3,000 calls, max 5 rps) on a PUBLIC RPC (api.mainnet-beta.solana.com); no Helius. Results cached in this folder. Report cashback share of pools and of trades by block and by month. A pool whose account cannot be read counts as unknown, reported separately. If unknown pools are more than 20% of trades, the verdict is NOT_DECIDABLE. If cashback trades are below 100 of 2,826, NOT_DECIDABLE (no power).

## Step 2: the one rule (no grid)

Reprice every trade with the P55 pricing (PumpSwap PRE-trade reserves, V in the quote reserve, `PUMPSWAP_SOL_FEE_TIERS`, 55,000-lamport priority per send, landing 1.9 s after trigger, stake 0.1 SOL, exit trigger s0 + round(330 s / sps)), exit lag 2 slots (primary; the frozen s14 lag ceil(0.55 s / sps) is reported as a check). Entries and exits are unchanged. Baseline must reproduce the stored `pnl` at the frozen lag (report max abs difference in lamports; the code gate).

For a trade on a pool with `is_cashback_coin` true add back, for each leg, the creator slice at the tier of that leg's pre-trade market cap (`creator_ppm` of `PUMPSWAP_SOL_FEE_SPLIT`, market cap = Q/B x 1e6 as p55's `tier`):
- buy leg: `S x creator_ppm / 1e6` (fee is taken on the input),
- sell leg: `gross proceeds x creator_ppm / 1e6`, gross proceeds before the sell fee.
Primary credits the refund in full and at once. Sensitivity (report-only, not selected): one claim transaction of 5,000 lamports charged per trade. Non-cashback pools are unchanged.

Fail models as P55: flat `0.85 x pnl + 0.15 x (-55,000)`; pressure-fail `(1-p) x pnl + p x (-55,000)` with the stored per-trade p. A failed send gets no refund (the fail-model formulas are applied to the refunded pnl, as for any pnl; the -55,000 branch has no refund).

Majority-positive days = days whose total P&L is positive (gate wording; counted of days with trades). Reported for discovery, confirmation and pooled, both models, on all 2,826 trades and on the cashback subset: delta per trade (pp of the 0.1 SOL stake), new mean, total ex-top-3 trades, total ex-best-day, number of days with a majority of trades positive, CI90 lower bound of the mean (1,000 bootstrap draws, seed 1, trade-level, 5th percentile). Also: baseline mean for cashback vs non-cashback pools (is the subset a pure cost effect).

## Kill line

KILL (a cost note, not a book) if the mean delta over ALL trades is below +0.3 pp per trade under either fail model, OR the re-costed book mean over all trades is at or below 0 under either fail model. NOT_DECIDABLE under the Step 1 rules. LEAD only if neither holds, and then only a pre-registered forward test on a new block would follow.

## Capacity line

Refund per cashback trade = stake x (0.60% to 1.25% round trip, by tier). Live-economics view: SOL a day saved = (total refund of the 2,826 trades in SOL) / (number of calendar days with at least one frozen trade), at the frozen stake 0.1 SOL; and the same at 0.25 and 1 SOL stake by repricing the same entries and exits at that stake (curve impact is inside the swap math), reporting base mean pp, delta pp and SOL/day for each. Net edge after the claim cost is shown in the sensitivity. This is arithmetic on frozen trades, not a forecast.
