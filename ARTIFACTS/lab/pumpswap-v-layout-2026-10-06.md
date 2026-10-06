**Lab note, 2026-10-06 (manager7).** This investigation used exploration data only and about 45 RPC calls. It opened no P&L, outcome or forward-pool files. The trigger was jobs #245, #254 and #255, which found that the "null V" pools are live canonical pools, not closed accounts; the earlier docs that said "closed accounts" were wrong. The scratch scripts and dumps it refers to (`scratchpad/nv/`) are not committed. The fix is in branch `claude/pumpswap-v-signed-base`.

# Negative V on PumpSwap pools: findings (2026-10-06, ~06:30–07:05Z)

Scope: pricing and layout only. No P&L, outcome or score files were opened. The forward null pools were not opened.
The analysis used the 321 exploration null pools (`/data/mal/ops/still-no-v-pools.json`).
About 45 RPC calls in total, at ≤ 4 rps. Scripts and raw dumps are in `scratchpad/nv/`.

## 1. Layout

**Known:**
- The on-chain Anchor IDL (account `5fLnXNNoZcZt9Qku6HARM3un3Ttm2cGsR7gN9Zp1R7h3`, saved as `nv/pump_amm_idl.json`) ends `Pool` at
  `coin_creator` (211..243), `is_mayhem_mode: bool` (243) and `is_cashback_coin: bool` (244). The "2 flag bytes" are these two bools. Observed values: `0100` = mayhem, `0001` = cashback.
- That IDL is stale. Accounts are 300 or 301 bytes, and every field after byte 245 is undocumented. The repo has no IDL or layout note beyond `parse_pool_account`'s comment.

**Observed** (321 null, 200 normal and 40 other pools fetched):

| bytes | content |
|---|---|
| 245..261 | V, a signed 16-byte value. On negative pools, bytes 253..261 are `ff`×8 (316/321). Today, i64(245..253) == i128(245..261) on all 561 accounts. |
| 261..270 | always 0 |
| 270 | a 0/1 byte (a bool or flag) |
| 271..279 | u64 accumulator A |
| 279..287 | u64 accumulator B |
| 287..end | always 0 |

- **Exact identity on all 321 null pools: V = −(A + B).** The "later field equal to |V|" is B when A = 0 (227 pools). 87 pools have A > 0.
- On normal pools with nonzero accumulators, V + A + B ≈ 17,584,505,3xx–6xx. Examples: `EFZVJys8` 17,584,441,196 + 3,146 + 61,346; `7qveSqke` 17,584,466,402 + 38,927.
  So **V (stored) = V0 − A − B**, with V0 ≈ 17.5845053 SOL on V-era pools and V0 = 0 on these 321 pools.
- **Guessed (not proven):** A and B are pending quote amounts held in the quote vault that do not belong to LP reserves (cashback or creator-fee style accruals). Claiming them resets both to 0 and puts V back to V0. The IDL has `claim_cashback`, `TokensInVaultLessThanCashbackEarned` and `transfer_creator_fees_to_pump(_v2)`, which fit this guess. Field names and their order are not known.
- Whether V is i128 or i64 plus padding cannot be told apart from current data. A signed read gives identical values either way.

## 2. Does V change over a pool's life?

- 311 of 321 null pools trade on the exploration tape: 1,364,090 PumpSwap rows from 2026-08-14 to 2026-09-22. They are mostly old, large pools: vault Q at first sight has a median of 27.6 SOL, p90 267 SOL and a maximum of 15.5k SOL. These look like pre-V-era migrations.
- I inferred V from sells: tier fee, then V = gross·(B+t)/t − Q, on sells > 0.005 SOL.
  - Median over pools of the first-5-sells V: **−1.2e-6 SOL**. Last 5 sells: −1.6e-6 SOL.
  - 281/282 pools have |V_first| < 0.5 SOL, and **0 pools are near 17.58**.
  - V was never ≈17.58 on these pools. It is about 0 for the whole tape life. It is not a drift down from 17.58.
- Live checks:
  - Two snapshots ~10 min apart: **2/321 null pools changed**. `4W4G9TDr` went from (V −172,362; A 4,659; B 167,703) to (0, 0, 0), which looks like a claim reset. `8KCYetq9` went from 0 to −21,259 (B 21,259), which looks like an accrual.
  - The negative part is time-varying. It is small: max |V| among the 321 is 2,018,703 lamports (0.0020 SOL), and the median is 42,845 lamports.

## 3. Pricing check

**Live BuyEvents** (`pumpswap_virtual_history.analyze`; implied V is rounded to 1e-4 SOL), recent buys on 6 null pools:

| pool | V decoded now | implied V |
|---|---|---|
| H5gzcMCC | −1,729,488 | −1.7e6 (×3) |
| AFaYrFH7 | −2,018,703 | −2.0e6 (×2) |
| CqC3Hz1V | −1,619,446 | −1.6e6 (×2), −0.8e6 |
| 4utojFKP | −985,211 | −1.0e6 (×3) |
| EE3zk9Fx | −1,333,741 | −1.4e6, 0 |

- **The program prices on vault + signed V.** Vault-only residuals are only 0.01–0.04 bps on these deep pools (306–1,565 SOL).
- `EE3zk9Fx`'s "0" fits an accrual or claim between trades.

**Tape sells** (last ≤2,000 sells per pool, `quote_sell`, portal fee 0). Median residual in bps, and mean |r|:

| pool | Q med (SOL) | V=0 | V=decoded-now | V=17.58 |
|---|---|---|---|---|
| EE3zk9Fx | 611 | 0.000 (0.000) | −0.022 (0.023) | +288 |
| H5gzcMCC | 599 | 0.000 (0.000) | −0.029 (0.028) | +294 |
| FnzKY6x7 | 11,289 | 0.000 | 0.000 | +15.6 |
| 4w2cysot | 15,744 | 0.000 | −0.000 | +11.2 |
| 5wNu5Qhd | 8,152 | 0.000 | −0.001 | +21.6 |
| FFcYgSSg | 4,168 | 0.000 | 0.000 | +42.2 |
| EXfi7U4L | 38 | 0.000 | −0.035 | +4,601 |
| 14LhivTS | 53 | 0.000 | −0.301 (0.331) | +3,311 |
| 8KCYetq9 | 38 | 0.000 | 0.000 | +4,676 |
| CEVXMsLC | 38 | 0.000 | −0.032 | +4,634 |

- 100% of V=0 sells are within 1 bp.
- On the historical tape, V=0 fits better than today's decoded V. Today's accrual is not the accrual at trade time.
- Both are < 0.35 bps. Treating these pools as V=17.58 would be wrong by 11–4,676 bps.

## 4. Scale and constancy

- I sampled 200 exploration pools with V in [17.584e9, 17.590e9) from `pool_v_0909.json` (fetched 05:27:32Z) and re-fetched them twice (~06:3xZ and ~06:58Z). **200/200 are unchanged**, byte-exact V.
- In that sample, 2/200 had nonzero accumulators. The largest was 64,492 lamports.
- V0 looks constant per pool. The stored V moves only by the pending accumulators, which are small (≤ 0.002 SOL seen) and reset on claim.
- Map V values on V-era pools spread over 17,584,505,288–17,584,505,565. That spread is a few hundred lamports, which is < 0.0002 bps of pricing.

## 5. Recommendation

1. **`parse_virtual`:** decode signed. Read i128 from bytes 245..261 and require that it fits i64; else return None. That is equivalent to i64 from 245..253 today. Stop treating "≥ 2^63" as unreadable. The 321 pools are readable V≈0 pools, not missing data. They should leave `no_v` and stop being excluded or flagged.
2. **Adapter:** the on-chain math is vault + V with V signed (live events confirm it). But:
   - for snapshot-based historical rescoring, |V| ≤ 0.002 SOL and the snapshot's negative value is not the value at trade time;
   - so vault-only (V=0) is the better historical estimate (0.000 bps vs 0.02–0.33 bps);
   - `correct_print`'s `v ≤ 0 → unchanged` is therefore acceptable for these pools, once `parse_virtual` returns the negative int instead of None.
   - For live or forward pricing from a fresh account read, use vault + V (signed). The difference is sub-bp except on tiny vaults.
   - Either choice is immaterial next to the 17.58 question.
3. **Snapshot fill (V constant):** safe at bp level. V0 showed no change (200/200), and pending accruals move V by ≤ ~0.002 SOL. That is ≤ ~1.1 bps at Q+V ≈ 17.6 SOL and far less on deeper pools.
   - Caveat: the accumulators' meaning is guessed. A high-volume cashback or mayhem pool with long-unclaimed accruals could carry a larger pending amount. That was not observed here, but it is not ruled out.
   - A cheap guard: record A+B at fetch time and flag pools with A+B > 0.01 SOL.

Not done: decoding A and B's names, finding V0's origin (17.5845053 SOL), and explaining the 2,516,200,564 V class (636 pools). The 609 forward pools were counted by the caller only and not opened here.
