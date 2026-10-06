**Lab note, 2026-10-06 (manager8).** Pool fields and PumpSwap transaction logs only. No P&L, outcome, decision or forward-paper file was opened. Jobs #282, #283 and #284 on mal-research-0, about 400 RPC calls in total. Raw outputs: `/data/mal/ops/vfield-20261006T144947Z/` (`accounts.json`, `lp_*.json`, `full_*.json`, `*.log`). Follows job #278 and the [V layout note](pumpswap-v-layout-2026-10-06.md).

# PumpSwap V0 changes only on LP deposits and withdrawals, by an exact rule

## Question

Job #278 re-fetched the 52,697 forward pools of snapshot #2 six hours later. `v_base` = V + A + B (called V0 below) differed on 6 pools (4 canonical, 2 non-canonical). The merged #383 rule refuses on any V0 difference, so as written book (B) would be NOT_DECIDABLE on 10-16. The owner asked us to rule out an unread pool field before changing any rule.

## 1. Unread pool bytes: ruled out

Job #282 read raw accounts for the 6 moved pools plus 34 controls (12 with a constant V0 well above 17.5845 SOL, 8 canonical with pending > 0.001 SOL, 8 canonical with pending 0). All of them are 301 bytes long.

- Bytes 287..301 are zero on all 40 pools.
- Bytes 261..270 are zero on every canonical pool. On 4 pools (the 2 moved non-canonical pools and 2 unmoved controls) they hold a small value (`2c01` = 300 or `c8` = 200, probably a config value). The value is the same on moved and unmoved pools.
- The flag at byte 270 is 1 on 3 moved pools and 2 of the 28 controls, and 0 on the rest. It is not linked to the moves.

No unread field explains the moves.

## 2. Cause: liquidity Deposit and Withdraw

Job #284 read each moved pool's LP-mint history. LP mint and supply come from `parse_pool_account`. Every one of the 6 pools had a PumpSwap `Deposit` or `Withdraw` in the window `[07:49:58Z, 13:53:11Z]`. A full instruction scan of the two quiet pools found no other instruction that could change V. The others were trades (`Buy`, `Sell`, `BuyExactQuoteIn`, the `…V2` variants), `SweepProtocolFee`, `SweepCreatorFee` and `CloseUserVolumeAccumulator`.

`DepositEvent` and `WithdrawEvent` (IDL layout: `lp_mint_supply` is the supply **before** the operation, followed by the LP amount out or in) give S_before and S_after. The rule

    V0_after = floor(V0_before × S_after / S_before)        (S = LP mint supply)

applied event by event reproduces every move **to the lamport**:

| pool | LP ops in window | V0 snapshot #2 | V0 #278 | predicted | residual |
|---|---|---:|---:|---:|---:|
| 8ewuF2o8 | 1 deposit | 17,584,505,649 | 17,584,847,247 | 17,584,847,247 | 0 |
| AgcjmdfX | 1 withdraw | 17,607,267,834 | 17,584,505,306 | 17,584,505,306 | 0 |
| BaiHzFqn | 1 deposit | 17,584,505,443 | 17,618,246,195 | 17,618,246,195 | 0 |
| CyJwKnLi | 7 deposits, 1 withdraw | 17,670,729,486 | 17,800,041,063 | 17,800,041,063 | 0 |
| 6X2DJ4sA (non-canonical) | 1 withdraw | 438,152,246,087 | 434,791,942,471 | 434,791,942,471 | 0 |
| 6ejg4aYJ (non-canonical) | 4 deposits | 219,722,151 | 219,980,561 | 219,980,561 | 0 |

Out of sample: BaiHzFqn deposited again after #278 (at 1791295118), and the #282 read (17,759,713,075) matches the rule exactly.

So V0 scales with LP supply, which keeps the price unchanged across a proportional deposit or withdraw. That is what a virtual reserve on a constant-product pool has to do. The pending counters A and B are not scaled: V0 (= V + A + B) is the scaled quantity. It matched exactly on 8ewuF2o8 while its pending rose from 35.0M to 47.4M lamports.

## 3. What this means

- **"V0 is constant per pool" is false. "V0 / LP supply is constant per pool" is true**, up to 1 lamport of floor rounding per LP operation.
  - V0 is piecewise constant and changes only at LP events.
  - Given a pool's LP events, V at any slot can be computed exactly.
  - A V0 difference between two fetches is either explained by the rule or is a genuine anomaly. No tolerance guess is needed.
- **Rate.**
  - In the 6 h #278 interval, 6 of 52,643 pools moved, 0.011%.
  - In the #278 fetch, 137 of the 20,441 pools with V0 in 17.5–17.9 SOL sit more than 1,000 lamports from the canonical creation cluster (17,584,505,2xx–6xx). That fits past LP activity, but it was not checked pool by pool. That is about 0.7%, and the upper end is 17.876 SOL (+1.7%).
- **Price effect.** A V0 off by 1.7% of 17.58 SOL (0.3 SOL) moves price by about 0.3% at Q + V ≈ 100 SOL, and by less on deeper pools. It is small, but not zero, and it is systematic for the affected pools.
- **Historical maps** (pool_v_0909 and others, fetched weeks after the trades) price an LP-active pool at its V0 at fetch time, not at trade time.
  - For EXP-012-style entries a few minutes after migration, the trade-time V0 is almost always the creation value. The map value differs only on pools that saw LP activity between the trade and the fetch.
  - Exposure is around 1% of pools. This is a disclosed limit of every V-priced historical re-score, not a reason to reopen one.
  - EXP-016's P1 constancy check (implied V at trade time vs map) will see these pools as disagreeing; its 1% refusal rule governs.
- **Runner paper (fast-0).** `fast_tip_follower` reads V once per pool and caches it (`v_cache`), so rows on a pool with a later LP op carry a stale V. This affects the paper runner only. The live probe executor reads the pool account at trade time.
- **Live.** Unaffected. The executor's quote uses the fresh pool state.

## 4. Fix for the 10-16 (B) read

The fix is pre-declared in DEC-016 Amendment 5 §7, in the same PR as this note. It replaces "any V0 difference refuses" with the exact rule plus a refusal ceiling for anything the rule does not explain:
- a V0 move explained by the pool's LP events passes;
- (B) prices an entered pool at its V0 at the entry slot;
- unexplained moves are null-V for (B) and are capped;
- the (B) report carries a sensitivity line.

Not done here:
- the meaning of bytes 261..270 on non-canonical pools;
- a count of how often LP ops fall inside an EXP-012 hold window (that needs entered mints and is done at the read);
- a re-count of LP-active pools in pool_v_0909.
