# P82 report: CASHBACK-FLOOR (re-cost of the frozen H5 binding book)

Verdict: **KILL** (a cost note, not a book). Pre-registered kill line: mean delta over all trades below +0.3 pp under either model. Measured +0.2180 pp (flat 15%) and +0.1750 pp (pressure-fail slope 1). Nothing here changes a running book; it is in-sample on exploration blocks that at least 14 families have read, and no book has cleared the promotion gate. This note does not say H5 makes money.

Files: PREREG.md (committed before any pricing), p82_pools.py, p82_flags.py, p82_flags.json (cached RPC result), p82_price.py, p82_analyze.py, p82_analyze_output.txt (all numbers below).

## Facts (docs copy, not tx-verified)
- Flag: PumpSwap `Pool.is_cashback_coin` at byte 244 (lab note pumpswap-v-layout-2026-10-06). Read for all 2,826 pools with 29 public `getMultipleAccounts` calls (api.mainnet-beta.solana.com, 1 rps, no Helius). All 2,826 accounts exist, owner pAMM...EfXEA, so the flag is readable for historical pools; `is_mayhem_mode` is 0 on all. Not done: the 20-transaction `getTransaction` check that the refund is paid as documented.
- The refund is not paid in the trade tx. It accrues in the trader's `UserVolumeAccumulator` WSOL ATA and is paid by a later `claim_cashback` tx (permissionless; user WSOL ATA must exist). It is paid only if the swap appends the cashback remaining accounts; otherwise the creator fee goes to the creator. A claim costs a base fee (5,000 lamports); sensitivity below charges it per trade.
- Cashback coins can no longer be created (create_v2 rejects since 2026-09-12).

## Step 1: share
1,000 of 2,826 pools (35.39%) are cashback; 0 unknown. By block: explore-0814 911 of 1,702 (53.5%), fresh-0903 66 of 307 (21.5%), exp011-0909 22 of 291 (7.6%), fast-pool-0918 0 of 233, oracle-insample-0922 1 of 293 (0.3%). By ISO week the share falls 52-54% (W33-W35), 34.5% (W36), 10.3% (W37), 0% (W38), 0.26% (W39). So the share decays as new cashback coins stop; it is about zero in the late-September blocks.

## Step 2: re-cost (P55 pricing, entries and exits unchanged, exit lag 2 slots; baseline reproduces stored pnl at the frozen lag, max abs diff 0.0 lamports, stored flat/press also 0.0)
Refund full, primary. Means in pp of the 0.1 SOL stake. Day count = days with positive total.

| set | model | n | base mean | new mean | delta | ex-top-3 SOL (new) | ex-best-day SOL (new) | positive days | CI90 lower (new) |
|---|---|---|---|---|---|---|---|---|---|
| disc | flat | 1702 | 11.5930 | 11.9234 | +0.3304 | 15.9442 | 16.3168 | 13/15 | 8.1382 |
| disc | press | 1702 | 10.5955 | 10.8614 | +0.2659 | 14.9613 | 14.7361 | 15/15 | 7.7408 |
| conf | flat | 1124 | 12.5381 | 12.5858 | +0.0476 | 10.1488 | 11.3132 | 18/21 | 8.0305 |
| conf | press | 1124 | 10.9866 | 11.0240 | +0.0374 | 8.9937 | 9.7273 | 17/21 | 7.2743 |
| all | flat | 2826 | 11.9689 | 12.1869 | +0.2180 | 29.8683 | 30.4632 | 31/36 | 9.2326 |
| all | press | 2826 | 10.7511 | 10.9261 | +0.1750 | 27.0459 | 27.1270 | 32/36 | 8.4621 |
| cashback only | flat | 1000 | 7.8841 | 8.5000 | +0.6160 | 4.7962 | 5.8193 | 14/26 | 3.3049 |
| cashback only | press | 1000 | 7.5445 | 8.0391 | +0.4946 | 5.0776 | 5.5882 | 15/26 | 3.8965 |

The re-costed book is not negative (so the second kill clause does not fire); the first does, on both models, and also on the confirmation set alone (+0.0476, +0.0374 pp). Discovery alone passes 0.3 only under flat (+0.3304), because the cashback share there is 53.5%. The refund is worth about 0.72% of stake per cashback trade (median 0.5573%, max 19.16%; the cause was not examined), against about 0.60 to 1.25% estimated in the idea note. Charging a 5,000-lamport claim per trade changes the all-trade delta to +0.2165 / +0.1738 pp (same verdict). Frozen-lag exit gives the same delta (+0.2180 / +0.1750). Primary and frozen-lag rows in the full output.

Subset check (pre-refund, flat, primary): cashback pools 7.8841 pp against non-cashback 14.2059 pp. The cashback subset is worse before the refund, so the cashback and non-cashback books differ in quality and the split is not a pure cost effect (this was a pre-registered lane-level caution).

## Live-economics view (arithmetic on frozen trades; not a forecast)
Total refund over the 2,826 trades at 0.1 SOL: 0.7247 SOL, 36 trade-days (78.50 trades/day on trade days; 65.72 on the 43-day calendar span): 0.02013 SOL per trade-day, 0.01685 per calendar day. At 0.25 SOL stake (same entries and exits, impact inside the swap math): 1.8107 SOL total, 0.05030 per trade-day; at 1 SOL: 7.2239 SOL total, 0.20066 per trade-day, delta 0.2173 pp of stake. These are the Aug/Sep rates; the cashback share in the last two blocks is 0.3% and 0%, and no new cashback coins are created, so for H5/Q50 on current pools the expected saving is about zero. Q50's own rate was not read (sealed).

## Not measured
Tx-level confirmation that the refund is paid on PumpSwap buys and sells; whether the lab's send path appends the cashback remaining accounts (without them the refund is not paid); WSOL ATA creation cost; anything on October pools (sealed); the pool-flag state at the time of each trade (the flag is set at pool creation, read now).
