# P71 pre-registration: out-of-sample wallet persistence at MAL latency (copy test)

Written before any pricing. Library rows: web-public-pnl-3, web-public-pnl-5. Lane P71, manager17, 2026-10-27. Exploration only; paper only. A pass is a lead, never a promote. No book has cleared the promotion gate. These dates are in-sample lab dates read by at least 14 families (EXP-024 sec. 9, m >= 14), so even a positive here is a lead.

## Data (frozen)

- `/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet` (the exploration paths P55 used). PumpSwap PRE-trade reserves, V-era pools only (`meta.v` in [17.5, 17.7] SOL), each pool's tape covers [mslot, mslot+7300) slots only (about 32 min after migration). The wallet id is the tape `th` (uint64 trader hash).
- Block = `meta.blk`. Ranking block: `explore-0814` (days 2026-08-14..08-28). Copy blocks: `fresh-0903` and `exp011-0909` (reported separately, then pooled). No October hour, no arena, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 data is read.
- Limits that follow from the data and are disclosed, not fixed: (a) only the first ~32 min of each migrated V-pool is visible, so "realized net" and the copy test cover post-migration activity inside that window only; (b) the 60-minute exit is therefore truncated to the last print of the window (priced at the final pool state, lag 2 slots); the share of truncated exits is reported.

## Ranking (Aug, explore-0814 only; one parameter set)

- Per (wallet, pool) with at least one buy and one sell in the window (a round-trip pool): `net = sell_sol - buy_sol * min(1, tok_sold / tok_bought)`, in SOL as it appears on the tape (no extra fee leg).
- Wallet score = sum of `net` over its round-trip pools. Wallets with fewer than 5 round-trip pools are not ranked.
- Bot flags (pnl-5), computed on the August tape only, applied BEFORE ranking: a wallet is a bot if ANY of
  - B1: more than 30 trades in one UTC hour (`bt // 3600`), summed over pools;
  - B2: a buy and a sell by the wallet in the same slot of the same pool;
  - B3: in at least one third of its round-trip pools, first sell slot minus first buy slot is below 2 slots.
- Flagged wallets are excluded. Top 50 of the rest by score = the copy list. Also reported: how many of the unfiltered top 50 are flagged (flagged share).

## Amendment 1 (2026-10-27, before any Sep copy pricing; ranking step only)

The first August ranking run (job log, Aug tape only, no September row read) gave top scores near 8,751 SOL per wallet, which is a unit artifact: a wallet that sells tokens it did not buy inside the visible pool window (bought on the bonding curve or transferred) gets the full sale proceeds against only the pool-buy cost. Fix, applied before any copy pricing: net is on matched quantity, `net = sell_sol * min(1, tok_bought/tok_sold) - buy_sol * min(1, tok_sold/tok_bought)`. Nothing else changes. The pre-fix run's only reported fact is that 43 of its unfiltered top 50 were flagged (B1 43, B2 5, B3 4); the post-fix ranking is reported in REPORT.md.

## Amendment 2 (2026-10-27, before any Sep copy pricing; ranking and attribution only)

After amendment 1 the top August scores were still up to 8,751 SOL per wallet. Inspecting one wallet's rows (August tape only) showed pool-seed rows, not trades: a print with `sol` = 987,654,320 lamports (a placeholder constant, `tok` 2.3767e12) and a print of about 1,777 to 2,960 SOL (more than 20x the pool's quote reserve of about 67 SOL, so not a possible swap) at the pool's first slot. Rule, applied to wallet attribution only (ranking, bot flags, and which prints count as a lead's buy or sell; the price path of every pool is untouched): a print with `sol` > 100 SOL or `sol` = 987,654,320 is not a trade and is dropped from attribution.

## Copy test (Sep, fresh-0903 and exp011-0909)

- Pool filter as P55: `meta.v` in [17.5, 17.7] SOL, seconds-per-slot from block times in (0.15, 0.6).
- Every BUY by a copy-list wallet with `sol >= 0.05 SOL` (dust excluded) is copied: one copy per lead buy, fixed stake S = 0.1 SOL.
- Entry: lands at slot X = lead buy slot + kb, kb = ceil(1.9 s / sps). Buy priced at the state after slot X (pre-trade state of the first print with slot > X), PumpSwap fee tier on both legs, V included (q + V), 55,000 lamport priority per send. This is the p55_price.py / s14_boostdip.py math, reused.
- Exit trigger: the lead's first SELL in the same pool after the lead buy slot, landing kb slots after the lead sell (the copied sell at the same 1.9 s lag, primary); else the time stop 60 min after X, truncated at the window end, landing 2 slots later (the realistic exit lag). If the lead sells so that the landing slot is not after X, the copy is skipped and counted.
- Sensitivity, report-only, never selected among: copied-sell lag 2 slots instead of kb.
- Fail models: flat `0.85*pnl + 0.15*(-55,000)`, and pressure-fail `(1-p)*pnl + p*(-55,000)` with p from `tools/latency_curve` at scale 1 (slopes 0.8 / 0.35, intercept fitted so the mean p over the copied sends is 0.289), pressure = (buys in slot X, buy lamports in the 2 s up to X), as in s14_boostdip.py.

## Metrics

Per block and pooled: n, days, mean per trade (SOL and % of stake) under both fail models, total, total without the top 3 trades, total without the best day, days positive of days, CI90 lower bound (day-cluster bootstrap, 1,000 draws, seed 1, 5th percentile), total without the best wallet and the best wallet's share of total. Persistence check: Spearman rank IC between August net and September copy net over the copy-list wallets with at least one priced copy (n reported), plus a descriptive IC against the wallet's own September realized net. Capacity: copied buys per day (Sep blocks), and trades/day x stake x mean net edge at 0.1, 0.25 and 1.0 SOL (the AMM impact is inside the repricing at each stake; p_fail held at the 0.1 SOL value).

## Kill lines (any one is KILL)

1. Pooled Sep mean per trade <= 0 under the flat model OR the pressure-fail model.
2. Driven by the top 3: total without the top 3 trades <= 0 under either model.
3. Driven by one wallet: total without the best wallet <= 0 under either model.
4. Also KILL if either block alone has a mean <= 0 under either model AND the pooled total without its best day <= 0 (reported as a day-concentration line, not decisive alone).

NOT_DECIDABLE if fewer than 100 priced copies, or fewer than 5 distinct UTC days, or the copy list has fewer than 20 wallets with a priced copy. Otherwise LEAD (never a pass). The promotion-gate items (CI90 lower bound > 0, majority of days positive, 100 out-of-sample trades) are reported for information.
