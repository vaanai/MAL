# P39 DECLARE: p37-damm-lp counts (COUNTS ONLY, no P&L), written before any count

Lane P39, manager17. Idea: provide liquidity for hours on DBC-migrated Meteora DAMM v2 pools (spec: docs/research/p37-idea-sprint/IDEAS.md §3.1 on PR #946; kill rules there are fixed and are not changed here).
`date -u` before this file was written: 2026-10-26T17:20:45Z.

## 0. Data and seals
- Source: the P16 DAMM v2 pool histories `/data2/mal/venue-tape-backfill/meteora_damm_v2/pools/pool-<pool>.jsonl.zst` (fetch jobs #1492 / #1506, `tools/damm_v2_fetch.py`) and its `checkpoint.json` (migration list).
  Every migration in them has block_time in [2026-10-14T00, 2026-10-17T00) (the fetch tool refuses others); history rows at or after 2026-10-17T00 were dropped at fetch. The count tool re-checks both and refuses a row or migration outside.
- Strand flag (P16 hot-pool bias): `docs/research/dbc-p16/strand_pools.json` (DBC pool ids only).
- No DBC hour file is read (the migration list comes from the checkpoint). Not opened: `docs/research/dbc-p17/data/cfg_summary.json`, `meteora_dbc_holdout/`, `fwdv1/`, the P16 `probe_raw.json`, the P16 smoke `grad.json`, any P16 `grad.json`. Nothing at or after 2026-10-17T00 is read; nothing in [10-17, 10-24) (SPENT) or [10-25T00Z, 11-01T00:10Z) (SEALED).
- Screen only: 3 UTC days, disclosed exploration, never a gate. No P&L of any kind is computed (no per-pool combination of fee yield and price outcome, no mean of a trade result).

**Known before this file (disclosed):** the P16 report says the fee seen in its 5 probe pools was 10 bps of gross, and its post-hoc D5 diagnostic quotes buy/sell fees of 11-37 bps; the P16 entry-impact distribution of a 0.25 SOL buy (0.5% of pools <= 2%, median 307%); and the P16 note that the median price 1 h after migration was about 2e-5 of the price at +2 s. I opened one pool file (16 trades) to see the row format; no fee, volume or price was computed from it.

## 1. Desk check (spec step 0): PASS
Source: MeteoraAg/damm-v2 at commit 5398b5c7c534de014091132da37c6478cce671a8.
- `create_position`: `payer` is any signer ("Can be anyone"), `owner` unchecked; the only gate is `can_create_position()`. `add_liquidity`: signer must hold the position (or a delegate); gate `can_add_liquidity()`.
  Both are `is_enabled` (pool_status == Enable) in `pool_action_access/permissionless.rs`, the only validator `get_pool_access_validator` returns. Swaps need the same `is_enabled`, so any pool with swaps in the window was open to new positions.
- The swap event carries the fee: `EvtSwap2.swap_result` has `claiming_fee`, `protocol_fee`, `compounding_fee`, `referral_fee` (state/pool.rs `SwapResult2`). Our decoder (tools/damm_decode.py) names the first one `trading_fee`; it is the LP-claimable part.
  Split (state/fee.rs `split_fees`): protocol = fee × protocol_fee_percent / 100; LP part = fee − protocol; of that, compounding_fee_bps go back into reserves (LP benefit), the rest is claiming; referral comes out of the protocol part.

## 2. Definitions (all fixed now)
- Migration time `t_m` = block_time of the pool's first row (the migration tx). Day = UTC date of `t_m`.
- History end `t_cov` = `covered_to_bt` of the pool_meta row (= min(t_m + 24 h, 10-17T00) unless truncated at the page cap; then the last row's block_time).
- Trades: `type == "trade"`, not `failed`, in fetch (execution) order. Pool usable = P16 `Ser` loader accepts it (init event present, WSOL is token B, first-swap leg check).
- Per-trade total fee `F = trading_fee + protocol_fee + compounding_fee + referral_fee` (token atoms); LP fee `F_lp = trading_fee + compounding_fee`. Fee token: quote (WSOL) for a sell, and for a buy when collect_fee_mode != 0; base for a buy when collect_fee_mode == 0.
- Gross fee rate `f = F / gross`, gross = amount_in (input-side fee) or amount_out + F (output-side fee) (the P16 `Ser` rule). Fee in SOL: quote atoms / 1e9; base atoms × post-trade spot `p = (S/2^64)^2` / 1e9.
- Quote volume of a trade = quote leg incl. fee: buy amount_in if the input is quote; sell amount_out + F when the fee is in quote (as P16 `qleg` plus the fee).
- Quote reserve `Q(t)` = `reserve_b` of the last trade with block_time <= t; before the first trade, `reserve_b` of the first trade minus its quote-in (buy) or plus its quote-out (sell). TVL(t) = 2 Q(t) (full range: base side worth the quote side; sqrt_min/max from the init event are reported as a check).
  Cross-check printed: Q from reserves vs L·S/2^128 from the carried implied L.
- Entry `t_e = t_m + 600 s` (spec: +10 min). Hold windows `[t_e, t_e + w)`, w = 1, 4, 6, 12 h, and "24 h" = `[t_e, t_m + 86,400)` (history ends at +24 h). A window is measurable only if its end <= t_cov; others are counted as censored.
- Hourly volume at h = 1, 4, 12, 24: quote volume in `[t_m + (h−1) h, t_m + h h)`; measurable iff `t_m + h·3600 <= t_cov`.
- Fee rate at a time t: f of the first trade at or after t (and before t_cov); reported at t_m (first trade), t_e, t_m+1 h, t_m+6 h; decay = f(t_m+6 h) / f(first trade). Init-event base fee (cliff_fee_numerator / 1e9, base_fee_mode byte, dynamic fee present yes/no, compounding_fee_bps, collect_fee_mode) decoded and counted.
- Fee yield (primary), per unit of resting liquidity: `y_w = Σ_{trades in window} (F_lp_SOL / L_pre) / (2 S_e / 2^128 / 1e9)`, where `L_pre` = the P16 carried implied liquidity before the trade and `S_e` = sqrt price at t_e. This is what a full-range position of fixed liquidity earns per unit of its value at t_e, whatever other LPs do.
  Secondary (count-like): `y_tvl_w = Σ F_lp_SOL / TVL(t_e)`. Total-fee versions (F instead of F_lp) printed beside.
- Turnover_6h = quote volume in `[t_e, t_e + 6 h)` / Q(t_e).
- Price ratio `r_w = p(end) / p(t_e)`, p after the last trade at or before the time (init price if none). A COUNT LABEL only.
- Liquidity events after migration (EvtLiquidityChange rows after the migration tx) counted per pool, as a flag for pulled liquidity.

## 3. Kill rules applied here
**K0 (fixed in IDEAS §3.1):** fewer than 10 pools/day with fee >= 0.25% and 6 h turnover >= 2.
- Operational: fee = gross rate `f` at t_e; 6 h turnover as above; a pool with no trade in `[t_e, t_e + 6 h)` has turnover 0. Only pools with a measurable 6 h window count.
- Per day and stratum s (strand / non-strand): `est_s = q_s × N_s / M_s` (q = qualifying pools, N = migrated pools with WSOL as token B that day, M = fetched usable pools with a measurable 6 h window). Strand pools are the hot ones (an upper bound), so the stratified estimate is the decision metric; raw fetched counts are printed beside.
- K0 KILLS if the mean over the 3 days of `est_strand + est_nonstrand` < 10. (10-16 is mostly censored for 6 h windows after 17:50Z; its estimate scales by M.)

**K-BE (added here, before any count; a stricter pre-pricing screen, never a substitute for K1/K2):** break-even fee yield at w = 6 h.
Stake S = 0.25 SOL; buy base for S/2 = 0.125 SOL at t_e; add both halves; remove at the window end; sell the base. Sends: 4 × 160,000 lamports = 0.00064 SOL (buy, add, remove, sell). Rent 0 (refunded on close).
- `c_buy = 1 − (1 − f_e) · Q_e / (Q_e + 0.125)` (fee + own impact, constant product, valued at the pre-trade spot)
- `V0 = 0.125 + 0.125 (1 − c_buy)`
- position at the end, before fees: `V0 · sqrt(r)` (full range, constant L)
- `c_sell = 1 − (1 − f_x) · Q_x / (Q_x + V0 sqrt(r) / 2)` (selling the base half; our own liquidity ignored, conservative)
- dilution `d = 1 / (1 + V0 / (2 Q_e))` (our liquidity's share of fees per unit, against a pool of TVL 2 Q_e)
- **break-even:** `y_be(r) = [0.25 + 0.00064 − V0 · sqrt(r) · (1 − c_sell / 2)] / (V0 · d)`
- Decomposition printed: vs holding SOL, `1 − sqrt(r)` = directional `(1 − r)/2` + IL `(1 + r)/2 − sqrt(r)`.
- Evaluated ONCE on the K0 universe (fee >= 0.25% and turnover >= 2; if it holds fewer than 10 pools, on all usable pools with a measurable 6 h window, primary sample), with Q_e, f_e, Q_x = Q(t_e+6 h), f_x = f at t_e+6 h each at their p50, and r at p10 / p50 / p90 of r_6h.
- **K-BE KILLS if p90 of y_6h < y_be(p50 of r_6h).** (The top-decile fee pool does not cover the median pool's value loss and costs.) Marginal quantiles only; no per-pool pairing.

**K1, K2** need the one pricing and are NOT applied here (no P&L).

**Verdict:** KILL if K0 or K-BE kills (final: no second fee band or horizon on these hours). Else ADVANCE-TO-PRICING-DECLARE (the spec's one pricing, K1/K2, a separate declare).

## 4. Reporting
- Primary sample: non-strand pools (sha256 order, a uniform subset; coverage share printed). All fetched pools printed beside; strand pools alone printed.
- Distributions p10 / p50 / p90 (numpy linear quantiles), n, n censored, per migration day; all numbers unrounded from the output JSON.
- Every DBC/DAMM day touched listed (expected: 2026-10-14, 10-15, 10-16; the tool records min/max migration and row block_time).
- Tool: `tools/p39_damm_lp_counts.py` with tests; run as a MiScusi job; output `docs/research/p39-damm-lp/counts.json` + sha256.

Declared at: 2026-10-26T17:20:45Z (date -u above; file committed before the tool is written)
