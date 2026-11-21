# FLOOR-CPMM step 2: DECLARE (DRAFT for a QP ruling; written before any pricing)

Status: **DRAFT, 2026-11-21T19:42Z, worker draft for manager24.** Nothing here is registered. No history has been fetched, no price read, no P&L computed. No book has cleared the promotion gate and nothing on this page says FLOOR-CPMM makes money. Every figure the replay later prints is in-sample, a screen, and "not blind" (the step-1 counts were read before this page).

Sources: `new-income-streams.md` sections 0 and A1 and `floor-cpmm-count.md` steps 1 and 1b (host `/data/mal/hunt-1008/ops/reports/2026-11-20/`); FLOOR's live rule `tools/arena_rule_l1.py` (line numbers below are that file's); template for porting FLOOR to a new pool type `tools/arena_rule_l1_oq.py` and `docs/rotation/books/osb-oq-v1.md`; fetcher window mode `tools/launchlab_cpmm_fetch.py` (this PR).

## 1. Seals and data

- Window: **migrations with block_time in [2026-10-24T01:00:00Z, cut)** and CPMM history rows with block_time in [2026-10-24T01:00:00Z, run end). `cut` is fixed at the fetch job's `--run-end`, written in the job record before the fetch. A migration before the window start is refused by the fetcher; rows before it are dropped and counted.
- LaunchLab holdout [2026-10-17T00, 2026-10-24T00) (HOLDOUT_LEDGER row "LaunchLab holdout"): never fetched, read or priced. D2/D3 domain seals and the arena/tip seals do not apply because no tape is read; the manager re-checks the ledger before pricing (this draft opened no ledger).
- Data source: Helius `getTransactionsForAddress` per pool (public chain data), written under a new out dir outside the LaunchLab tape tree. No arena tape, venue tape or tip tape.
- Pool list: the step-1b migration list (242 WSOL-quoted pools as of 2026-11-21T19:38Z). Pools migrated after the list was built are out of this read.

## 2. Universe

A pool is in the universe if all hold, tested once at the pool's first eligible time (never re-tested later):

1. WSOL-quoted LaunchLab -> Raydium CPMM (`initialize_with_permission` from the LaunchLab authority); non-WSOL quotes are out (no conversion leg in this book).
2. LP burned-or-locked >= 95%: LP burned fraction (1 - LP mint supply / PoolState.lp_supply) plus the LP share held by the Raydium LP-lock authority `3f7GcQFG397GAaEnv51zR6tsTVihYRydnydDD1cXekxH` (PDA of the Raydium lock program, step 1b) >= 0.95. That the lock is permanent is Raydium's documentation, not verified from program code.
3. Token mint authority null.
4. **Vesting: not checked. Stated as an unchecked veto**: a pool with a vesting or other pending-unlock structure is not excluded and the result carries that gap.
5. WSOL-side depth: `q_floor = k / S >= 10 SOL` (below).

Reserves are the vault balance minus accrued protocol, fund and creator fees (PoolState), as in step 1; the raw-vault variant is reported beside it.

## 3. Entry (decided on the history, in order, at the first time it holds after migration)

Definitions: `q` = real WSOL reserve, `S` = token mint supply (getTokenSupply, constant while mint authority is null), `k = q * r_token`, `q_floor = k / S`, `excess = q - k / S`. All reserves are the pool's state **after** the print under test, as FLOOR tests its vault after the print (l1_rule.py:102-103; arena_rule_l1.py lines 98-102).

- **E1. Excess**: `excess <= 0.5 SOL` (the CPMM analogue of FLOOR's `qpost <= Q_STAR = 0.5e9`, line 102).
- **E2. Depth**: `q_floor >= 10 SOL` (new in CPMM; replaces nothing in FLOOR; it is the step-1 E0 filter).
- **First time it holds after migration**: the first SELL print (token in, WSOL out) at or after the migration at which E1 and E2 hold after the print. Same shape as FLOOR's trigger: a sell print (line 86: `row.get("side") != "sell"` returns).

FLOOR entry gates (arena_rule_l1.py), ported or not, one by one:

| # | FLOOR gate | Lines | CPMM port |
| --- | --- | --- | --- |
| G1 | canonical sell print of a WSOL pool, quote tested first | 72-77, 86 | Ported: a SwapEvent with `input_mint` = token, `output_mint` = WSOL. Quote WSOL is the universe test. |
| G2 | V in [17.5e9, 17.7e9] on the deciding row | 88-90, constants 36 | **Not ported**: CPMM has no virtual reserve. It is replaced by E1 and E2, which are new, not FLOOR's. Disclosed: this makes the book a sibling, not a twin, of FLOOR. |
| G3 | pool age print time minus base time M in [24 h, 200 h] | 42, 94-97 | Ported with M = migration block_time: age in [24 h, 200 h]. Consequence: nothing trades in the first 24 h; the 21 d history fetch covers the 200 h. |
| G4 | quote vault after the print <= 0.5 SOL | 44, 98-102 | Ported as E1 (excess form). |
| G5 | cooldown 60 s after the last exit decision of the pool, one open position per pool | 80, 86, 104-106 | Ported unchanged: one open position per pool, `COOLDOWN_MS = 60_000` after the exit decision. |
| G6 | previous print within 6 h (`ALIVE_S`) | 45, 163 | Ported: previous CPMM swap of the pool within 6 h. |
| G7 | volume over the last 24 h including this print >= 1 SOL | 46, 165-168 | Ported: sum of the pool's WSOL leg over swaps in the last 24 h, from `SwapEvent` amounts, >= 1 SOL. |
| G8 | post-trade spot / post-trade spot of the last print at or before 6 h back < 0.80 | 47, 169-174 | Ported: spot = q / r_token after the print (pool's real reserves, no V); `r6 < 0.80`. |
| G9 | history window of the prints: the last 26 arrival hours | 158 (`HIST_HOURS`) | Ported: the fetched swaps of the last 26 h. |
| G10 | uncapped book, no attempt key | docstring line 15 | Ported: no cap. A K=4 FIFO report-only leg is printed beside it, as FLOOR's `fifo_cap` (lines 187-200). |

Entry pricing: stake `s` WSOL into the pool's constant product with the pool's own trade fee, filled at the first swap at or after decision time + **1.9 s** (`LAT_DECIDING`, line 56). The 4.085 s pricing latency of osb-oq-v1 is the second, harsher cell, reported beside it. Entry reserves are the pre-trade state of the landing swap.

## 4. Exit (FLOOR's kit legs, ported 1:1)

- **Take profit** at 1.5 x the **post-buy mark** (`TP = 1.5`, line 47; `_tp_hit`, lines 115-151): `mark = (Q + net) / (B - tokens)` at the landing state, hit when the post-trade spot **with our position kept** `(q_post + net) / (b_post - tokens)` is `>= 1.5 x mark`. The line-148 constants 0.99 / 1.01 are FLOOR's own sim approximations for a print's post-trade quote; on CPMM the post-trade reserves are exact (SwapEvent plus the vault balances), so they are replaced by the exact post-trade reserve, and the rest of the formula is unchanged. TP checks start at the landing swap (line 126 form: `slot >= signal_slot + FEED_D + k + 1` is not ported; on history the landing swap is the first check).
- **Time exit** at decision + 24 h + 1.9 s (`HOLD_S = 86400`, `LEG_MS = 1_900`, lines 48-50, 178-179).
- **No stop** (docstring line 16).
- Exit pricing: first swap at or after the exit decision + 0.55 s (osb-oq-v1 `EXIT_LAG_MS`, reused), against the pre-trade reserves of that swap. A position still open at run end is marked "open" and reported separately, never as a win or a loss in the headline.

## 5. Costs (read from chain, not assumed)

- **Pool fee**: per pool, from the pool's own `AmmConfig` account (`amm_config` in the pool meta): `trade_fee_rate` (ppm of 1e6) and, where the config enables it, `creator_fee_rate`. Read by `getAccountInfo` at the pool's AmmConfig address, once per distinct config, recorded in the report with its address. Cross-check: the history's `SwapEvent` `trade_fee` / `creator_fee` over `input_amount` must equal the config rate on the sampled swaps; a pool whose observed fee differs from its config is excluded and counted, never priced with an assumed rate.
- **Priority and tx fees**: FLOOR's sim stack as the osb-oq-v1 card quotes it: `send_lamports` 505,000 per send (`SEND_FEE_DECIDING`), **2 sends per round trip** (buy, sell; no conversion leg), i.e. 1,010,000 lamports; FLOOR's live figure is 160,000 per send and is not used for the decision. The worker did not open `tools/arena_stack.py` for this draft: QP to confirm the constant and the 2-send count against the code before pricing.
- **Rent**: report-only, as in osb-oq-v1 (2,039,280 per ATA, deciding stack treats it as refunded).
- **Fail legs**: both, by `tools/arena_oq_score.fail_legs` (calls `arena_stack.fail_legs` unchanged): flat 15% `(1-f)P - f*send` and pressure slope scale 1. **The flat leg is the gate leg**; a result positive only under pressure is never quoted. The CPMM pressure inputs are the pool's own (price impact is in the constant product, no V).
- Price impact: from the pool's real reserves, at the stake. Reported at both stakes.

## 6. Stakes and outputs

Stakes **0.10 SOL and 0.25 SOL**, each at both entry latencies (1.9 s, 4.085 s): 4 cells, one trial per cell, no parameter picked after pricing. Rule constants are FLOOR's (winner's curse of the l1-floor-v2 variant search carries over). The count of rows watched is printed on every figure ("best of 4").

Printed, per cell and under both fail legs (counts and sums only until QP releases per-pool lines):

- trades (filled), unfilled, open at run end, universe size and pools that ever triggered;
- trades per day (trades / days from the first eligible time to run end) and per migration day;
- mean SOL per trade (net of pool fee, sends and fail blend) and `filled_no_fail` beside it, labelled as having no fail blend;
- total SOL, total after dropping the top 3 trades, and per-UTC-day totals (day of the entry decision);
- total after dropping the top pool, and after dropping the top 3 migration days (the 10-27..10-29 stock is 19 of 53 loose E0.5 pools);
- the bootstrap lower 90% bound of mean SOL per trade (1,000 draws, seed 1, 5th percentile) as a read, not a gate claim at this size;
- fee audit: pools by AmmConfig fee, config-vs-observed mismatches excluded;
- exclusions with reasons.

## 7. Kill line (written now)

Any one of these ends the line (no re-tuning on the same hours):

- **K1**: mean SOL per trade <= 0 at 0.25 SOL under the realistic cost (4.085 s entry latency, flat 15% fail leg), or
- **K2**: fewer than 0.3 trades per day (as defined in section 6) at 0.25 SOL, or
- **K3**: fewer than 20 filled trades in the cell (NOT_DECIDABLE, recorded as a stop, not a pass), or
- **K4**: total after dropping the top 3 trades <= 0 at 0.25 SOL under the same cost.

A pass of all four is **not** a promotion and is not "the book makes money": it is a reason to register a forward open simulation book on arriving CPMM data (no CPMM tape exists after 10-17 in our recorders, so a forward book needs a CPMM recorder first). The promotion gate (100 out-of-sample trades, 5 distinct UTC days, lower 90% CI bound > 0, positive after removing the top 3, under both fail models) stays unchanged and is not met by an in-sample replay.

## 8. Fetch plan and credit estimate (nothing run)

- Window mode, one Helius flock slot, rps <= 8, hard credit cap, key never printed. Per pool: `getTransactionsForAddress` pages of 100 txs at an assumed 50 credits per call (the fetcher's `COST_GTFA`); no `getTransaction` calls (pool and mints come from the list).
- Estimate: 242 pools x 21 days of history x pages per pool. Pages per pool are unknown before the fetch: at 1-10 pages per pool (100-1,000 transactions in 21 days) the fetch is 242 x (1..10) x 50 = **about 12k to 121k credits**; a pool with 3,000 transactions costs 1,500 credits and 30 such pools add 45k. The proposed cap is **250,000** credits; a pool the cap stops is marked missing, not partial.
- Pool order: oldest migration first, so a cap stop leaves the newest pools out and is declared.

## 9. Gaps declared in advance

1. Vesting is unchecked.
2. G2 (the V band) has no CPMM counterpart; E1 and E2 are not FLOOR's and are tested here for the first time.
3. History comes from `getTransactionsForAddress` on the pool, which sees transactions touching the pool account; a swap routed in a way that does not list the pool is missed (counted by comparing vault balance deltas to decoded swaps where possible).
4. A liquidity add or remove on a lock-held pool is not expected, but any non-swap CPMM instruction in the history is counted and reported.
5. The step-1 stock of 53 loose E0.5 pools is a current-state stock; history will show when each pool was eligible, and the replay's trigger count may be far smaller (pools drained at the time of the count may never have passed G3 to G8 in order).
6. Fill, latency and cost bias: no live CPMM fill exists. Any live step needs a CPMM swap builder in the executor (T1 code PR with an md5 decision-equivalence proof for the PumpSwap path).

QP ruling requested before the fetch job starts: universe, E1/E2, G3 (migration as base time), cost constants, kill line, cap.
