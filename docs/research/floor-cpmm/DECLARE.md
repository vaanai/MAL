# FLOOR-CPMM step 2: DECLARE (DRAFT v2 for a QP ruling; written before any pricing)

Status: **DRAFT v2, 2026-11-21T19:50Z, worker draft for manager24, after QP and reviewer CHANGES at eeb77191 (PR #1480).** Nothing here is registered. No history has been fetched, no price read, no P&L computed. No book has cleared the promotion gate and nothing on this page says FLOOR-CPMM makes money.

**What this replay is, stated plainly (binding on every result text).**
1. **In-sample, not blind.** The window [2026-10-24T01, cut) is the window whose step-1/1b counts were read before this page, and they were the reason to proceed (bar MET at 53 on the loose LP reading, NOT MET at 5 on the strict one). Nothing is held out. Every figure is an in-sample screen.
2. **Survivorship and look-ahead.** The step-1 stock is current state (pools drained on 2026-11-21), not a history of when pools became eligible. The replay universe is all WSOL-quoted LaunchLab -> CPMM pools of the window, not only pools drained today, which avoids filtering on the outcome. But the universe vetoes are today's reads applied backwards: LP lock share, mint authority, freeze authority, transfer-fee extension and `S` (current supply; a later burn raises `k / S`, so `excess` can pass earlier than it could have been known). These are look-ahead toward eligibility and are declared as such.
3. **It cannot clear any gate item.** The promotion gate needs out-of-sample trades. The replay prints in-sample analogues of the four items (trade count, distinct and positive UTC days, 90% CI lower bound, total after dropping the top 3) and none of them counts toward the gate. Both fail models are PumpSwap-calibrated; no CPMM live fill exists. A pass of K1-K4 earns only a forward open simulation book (which needs a CPMM recorder first).
4. **Winner's curse.** The rule constants are l1-floor-v2's, a variant selected on PumpSwap data, carried over unchanged. E1 and E2 are new and fixed here before pricing; 0.5 SOL is not swapped for the 2.0 SOL of step 1. Four cells are priced (2 stakes x 2 latencies); the kill line reads one fixed cell.

Sources: `new-income-streams.md` sections 0 and A1 and `floor-cpmm-count.md` steps 1 and 1b (host `/data/mal/hunt-1008/ops/reports/2026-11-20/`); FLOOR's live rule `tools/arena_rule_l1.py` (line numbers "rule:" below); FLOOR's deciding round trip `tools/arena_stack.py` (line numbers "stack:" below); template `tools/arena_rule_l1_oq.py` and `docs/rotation/books/osb-oq-v1.md`; fetcher window mode `tools/launchlab_cpmm_fetch.py` (this PR).

## 1. Seals and data

- Window: **migrations with block_time in [2026-10-24T01:00:00Z, cut)**; history rows with block_time in [2026-10-24T01:00:00Z, min(migration + 225 h, cut)). `cut` is the fetch job's `--run-end`, which is **required** (no default) and is written in the job record, together with this file's blob sha, before the fetch starts.
- 225 h = 200 h (G3 age limit) + 24 h (hold) + 1 h; nothing later is used. (This replaces the 21 d first proposed; it cuts the fetch by about 55%.)
- LaunchLab holdout [2026-10-17T00, 2026-10-24T00) (HOLDOUT_LEDGER row "LaunchLab holdout"): never fetched, read or priced. The fetcher refuses a migration before 10-24T01:00:00Z, drops and counts rows before it, and refuses (code, with tests) an `--out-dir` at or under `/data2/mal/venue-tape-backfill` (job #1474's `launchlab_cpmm` dir included), `/data2/mal/venue-tape`, the arena, forward-block, meta-capture, tip-tape and other roots listed in the code, or holding another run's `checkpoint.json`. Window mode uses its own `window-checkpoint.json` and never loads another run's checkpoint. `--out-dir` is required.
- Data: Helius `getTransactionsForAddress` per pool (public chain data). No arena, venue or tip tape. The manager re-checks the ledger (D2/D3) before pricing; this draft opened no ledger.
- Pool list: the step-1b list (242 WSOL-quoted pools as of 2026-11-21T19:38Z); pools migrated after it are out of this read.

## 2. Universe

A pool is in the universe if all hold (current-state reads, tested once, never re-tested; look-ahead declared above):

1. WSOL-quoted LaunchLab -> Raydium CPMM; other quotes are out (no conversion leg).
2. LP burned-or-locked >= 95%: burned fraction (1 - LP mint supply / PoolState.lp_supply) plus the LP share held by the Raydium LP-lock authority `3f7GcQFG397GAaEnv51zR6tsTVihYRydnydDD1cXekxH` >= 0.95. Permanence of the lock is Raydium's documentation, not verified from code.
3. Token mint authority null.
4. **Token mint freeze authority null.** A mint with a freeze authority is excluded and counted (arena_stack treats freeze authority as stranded: stack:163 docstring).
5. **No Token-2022 transfer-fee extension** (and the token program is the classic one or Token-2022 without that extension). A mint with a transfer-fee extension is excluded and counted; none is priced.
6. **Vesting: not checked. Stated as an unchecked veto.**
7. Depth `q_floor = k / S >= 10 SOL` is an entry condition (E2), not a universe filter.

Reserves are vault balances minus accrued protocol, fund and creator fees (PoolState); the raw-vault variant is reported beside it.

## 3. Entry gates (rule: = arena_rule_l1.py), re-entry allowed

Definitions: `q` real WSOL reserve, `S` token supply, `k = q * r_token`, `q_floor = k / S`, `excess = q - q_floor`; all reserves after the print under test (as FLOOR tests its vault after the print, rule:98-102).

**Re-entry: a pool may re-enter**, as FLOOR does: one open position per pool, the next entry no earlier than 60 s after the exit decision (rule:51, 86, 104-106). The trigger is evaluated on every sell print (no "first time" rule).

| # | FLOOR gate | Lines | CPMM port |
| --- | --- | --- | --- |
| G1 | canonical sell print of a WSOL pool | rule:72-77, 86 | A SwapEvent with `input_mint` = token, `output_mint` = WSOL. |
| G2 | V in [17.5e9, 17.7e9] | rule:88-90, 36 | **Not ported** (CPMM has no virtual reserve); replaced by E1 and E2, which are new, not FLOOR's. The book is a sibling, not a twin. |
| E1 | (CPMM) `excess <= 0.5 SOL` | analogue of rule:44, 98-102 | New. |
| E2 | (CPMM) `q_floor >= 10 SOL` | none | New (step-1 E0 depth). |
| G3 | pool age in [24 h, 200 h] | rule:42, 94-97 | Ported, base time M = migration block_time. Nothing trades in the first 24 h. |
| G5 | one open per pool; 60 s cooldown after the exit decision | rule:51, 86, 104-106 | Ported. |
| G6 | previous print within 6 h | rule:43, 163 | Ported (previous CPMM swap of the pool). |
| G7 | 24 h volume including this print >= 1 SOL | rule:45, 165-168 | Ported (WSOL leg of swaps, SwapEvent amounts). |
| G8 | post-trade spot / spot at the last print <= 6 h back `< 0.80` | rule:46, 169-174 | Ported; spot = q / r_token after the print (real reserves, no V). |
| G9 | prints of the last 26 h | rule:158 (`HIST_HOURS`) | Ported. |
| G10 | uncapped book | rule docstring | Ported; a K=4 FIFO report-only leg beside it (rule:187-200). |

## 4. The round trip, pinned to arena_stack (FLOOR's deciding stack)

Every item is arena_stack's unless a named CPMM replacement is given. None is chosen after pricing.

- **Latency in slots** (stack:55-63, 85-93, 102-151; landing state at 138-142): `k = round(lat_s / slot_s)` (`K_ROUNDING = "round"`, stack:26), landing slot `x = decision slot + k`; entry state = reserves **after all swaps with slot <= x** (stack:138-142: `QX[je]`, `je` = first swap with slot >= x + 1). Latency cells: **1.9 s** (`LAT_DECIDING`, rule:56) and **4.085 s** (osb-oq-v1). `slot_s` per UTC hour is measured on the fetched data, pooled over all fetched pools: `slot_s = (bt_b - bt_a) / (slot_b - slot_a)`, where swap a is the first pooled swap with block_time >= the hour start and swap b the first pooled swap with block_time >= the next hour start (the same two swaps give the numerator and the denominator). If `bt_b - bt_a < 1,800 s`, or there is no such pair, `slot_s` is the fixed **0.4 s**. (Whole-second block_time makes a single hour noisy; the pooled form is declared now.)
- **Exit lag** (stack:66-75, 96-99, 145-149): **exit lag = `max(2, ceil(0.55 / slot_s - 1e-9))` slots** (`MIN_EXIT_LAG_SLOTS = 2`, stack:25; a declared replacement: stack:69/98 assert >= 2 rather than clamp), replacing the time form; exit state = reserves after all swaps with slot <= (exit decision slot + lag). **Decision slot of an exit:** for a take-profit exit it is the slot of the swap whose post-trade spot hit 1.5 x mark. For the 24 h time exit, whose decision instant T = entry decision time + 24 h + 1.9 s has no clock slot in the history, the single pinned form is `clock_slot(T) = s_a + round((T - bt_a) / slot_s(hour of T))`, where `(s_a, bt_a)` is the latest pooled fetched swap with block_time <= T and `slot_s` is the estimate above for the UTC hour containing T (analogue of stack:145-149 `clock_slot(t_marks)`); the exit lag is then applied to that slot. No other mapping is used.
- **Guard** `GUARD = 1.15` (stack:27, 181): the buy reverts and pays one send if `(net / tokens) / spot > 1.15`, `spot` = decision-time spot (post-trade spot of the deciding print). A guarded buy is `-send`, not filled, never stranded.
- **Haircuts** `HAIRCUT_ENTRY = 0.002608` (tokens received / (1 + h)), `HAIRCUT_SELL = 0.0016` (proceeds x (1 - h)) (stack:32-33, 185, 194), unchanged.
- **Own-buy re-injection at exit** (stack:186-188): the exit pool is `(Qx + net, Bx - tokens)`, as FLOOR's. At 0.25 SOL into a pool of about 18 SOL this is about 1.4 pp of impact per round trip; it is in the sim, not left out.
- **Send fee** `SEND_FEE_DECIDING = 505,000` lamports per send (stack:28), **2 sends** per filled trip (buy, sell); a guarded buy pays one; `RENT_DECIDING = 0` (stack:29); rent is report-only (stack:30).
- **Stranding** (stack:31, 163, 198-200): a filled trip is stranded (`-stake - send - 2,039,280`, no sell send) when its proceeds are below one send (`auto_stranded`), or its entry or exit state is invalid (C2 state, C7 real-quote bound, C8 k-fall, stack:184, 189-190), or the mint has a freeze authority (excluded earlier, so n/a). The V_LO (17.5e9) checks of stack are replaced by `Qe > 0`, `Be > 0`, finite, as CPMM has no V; C7 uses the pool's real quote (`Qa`), C8 the constant-product k, tolerance `K_TOL = 1e-3`.
- **Fee tier**: `fee_ppm(Q, B)` (stack:78) is PumpSwap's and is **replaced** by the pool's own CPMM rate (section 5) on the entry, the TP mark, and the exit.
- **TP mark and its stake** (rule:115-151, 137-140): `mark = (Qe + net) / (Be - tokens)` at the landing state with `net = stake x (1 - pool fee)`. **The mark uses the cell's own stake** (0.10 in the 0.10 cell, 0.25 in the 0.25 cell); FLOOR fixes 0.25 (rule:54, 137). This is a declared deviation. TP hit when the post-trade spot with our position kept `(q_post + net) / (b_post - tokens)` is `>= 1.5 x mark` (`TP = 1.5`, rule:47). The rule:148 factors 0.99 / 1.01 are FLOOR's approximation of a print's post-trade quote; CPMM post-trade reserves are exact, so they are not used. Checks start at the landing slot.
- **Time exit** at decision + 24 h + 1.9 s (rule:48-50, 178-179). **No stop** (rule docstring).
- **No later swap (never dropped).** CPMM reserves are constant between swaps, so an entry or exit whose landing slot has no later swap is **priced at the pool's last known state** (the state after its last swap at or before the landing slot, derived from that swap's amounts as stack:121-122 does for the final print). It is never excluded. A position whose exit decision falls after `cut` is "open at run end": priced at the last known state at `cut`, reported separately, and excluded from the headline totals but counted and printed (never silently). "Unfilled" means only a guarded buy (-send) or an entry whose landing slot falls after `cut`; both are counted and scored as stated.

## 5. Costs read from chain

- **Pool fee**: from the pool's own `AmmConfig` (`amm_config` in the pool meta), `trade_fee_rate` (ppm of 1e6) plus `creator_fee_rate` where the pool charges it, read by `getAccountInfo` once per distinct config; the address and rates are recorded. Cross-check: SwapEvent `trade_fee` + `creator_fee` over `input_amount` on the pool's swaps must equal the config rate; a pool that does not match is excluded and counted, never priced with an assumed rate.
- Send fees as stack (section 4). Price impact from the pool's real reserves at the stake (no V).

## 6. Fail legs (both) and ssb / nearby on CPMM

`arena_stack.fail_legs` (stack:231-244) unchanged: flat 15% `(1-f)P - f*send` and pressure slope scale 1 (`0.8 ln(1+ssb) + 0.35 ln(1 + nearby/1e9)`, stack:35-36, 210-211), the intercept refit **per cell** on its filled rows to mean p = 0.289 (`press_intercept`, stack:37, 214-228).
- `ssb` = number of CPMM **buy swaps** (WSOL in, token out) of the pool **in the landing slot x** (stack:143: `cib[je] - cib[ix]`).
- `nearby` = WSOL lamports bought (SwapEvent `input_amount` of buy swaps) over slots `[x - round(2 / slot_s), x]` (stack:38, 140, 144).
Both are computed from the pool's own fetched swaps; no other pool's flow enters.

## 7. Stakes and outputs

Stakes **0.10 and 0.25 SOL**, each at **1.9 s and 4.085 s**: 4 cells. Printed, per cell and under both fail legs:
- trades (filled), unfilled (guarded or after cut), open at run end, universe size, pools that ever triggered, exclusions with reasons (freeze, transfer fee, fee mismatch);
- trades per day = trades / D, with **D = (cut - 2026-10-24T01:00:00Z) / 86400** fixed now (not data-dependent), and per migration day;
- mean SOL per trade after the fail leg, and `filled_no_fail` beside it, labelled as having no fail blend;
- total SOL, total after dropping the top 3 trades, per-UTC-day totals (day of the entry decision) with the **number of distinct UTC days and the number of positive days**, total after dropping the top pool and after dropping the top 3 migration days;
- bootstrap lower 90% bound of the mean (1,000 draws, seed 1, 5th percentile; `boot_lo`, stack:247) as an in-sample read;
- fee audit by AmmConfig and actual credits used.

## 8. Fetch plan, credits, and what a stop means

- Window mode: one Helius flock slot, rps <= 8 (refused above), key never printed, hard credit cap, oldest migration first, `getTransactionsForAddress` 100 txs per page at the fetcher's assumed 50 credits per call; no `getTransaction` (pool, mints, vaults come from the list). No per-pool error retry: an RPC error ends the run and a re-run resumes from `window-checkpoint.json` (this was optional and is skipped).
- **Estimate.** The plan (new-income-streams A1 step 2) put the cost at 50-150 credits per pool-day. With the 225 h clip that is 242 pools x 9.375 days = 2,269 pool-days, about **113k to 340k credits**; the earlier 12k-121k page estimate was a guess and is withdrawn. **Cap proposed: 400,000 credits** (under 2% of the 24.1M cycle). Actual credits are reported.
- **What a stop means.** A pool the cap stops, or any pool truncated by `--max-pages` (not used), is missing or partial. **Any missing or truncated pool makes the read NOT_DECIDABLE and nothing is priced** until the fetch is completed under a raised cap; there is no pricing on a partial set, because oldest-first ordering would keep the 10-27..10-29 cluster (19 of 53 loose E0.5) and drop the newest pools, a selection by age. A raise of the cap is a manager step recorded before the re-run.

## 9. Kill line (written now; a result needs BOTH fail legs)

Read at the fixed cell **0.25 SOL, 4.085 s**. Each of K1 and K4 must hold under the flat 15% leg **and** under the pressure leg at slope scale 1 (CLAUDE.md promotion gate; osb-oq-v1 card), else the line ends:
- **K1**: mean SOL per trade > 0 under both legs;
- **K2**: trades per day >= 0.3 with D of section 7 (about 28.8 days at cut = 2026-11-21T20:00Z, so about 9 trades);
- **K3**: at least 20 filled trades, else NOT_DECIDABLE (a stop, not a pass);
- **K4**: total after dropping the top 3 trades > 0 under both legs.

A pass of all four is **not** a promotion and not "the book makes money". The gate (100 out-of-sample trades, 5 distinct UTC days with a majority positive, 90% CI lower bound > 0, positive after dropping the top 3, under both fail models) is unchanged and cannot be met by this in-sample replay; with K3 at 20 trades, the 100-trade item is far off.

## 10. Gaps declared in advance

1. Vesting unchecked. 2. G2 has no CPMM counterpart; E1 and E2 are untested. 3. `getTransactionsForAddress` on the pool sees transactions touching the pool account; a swap that does not list it is missed (vault-delta cross-check where possible). 4. Any non-swap CPMM instruction (liquidity add or remove) is counted and reported. 5. The step-1 stock of 53 is current state; the replay's trigger count may be far smaller. 6. No live CPMM fill exists; any live step needs a CPMM swap builder (T1 code PR with an md5 decision-equivalence proof for the PumpSwap path). 7. `slot_s` is estimated from whole-second block times (pooled, section 4). 8. The pool list's migration time is not re-verified on chain (the fetch filter still bounds rows to >= 10-24T01).

QP ruling requested before the fetch job: universe, E1/E2, G3, round trip (section 4), kill line, cap. Before the job starts the job record pins this file's blob sha and `--run-end`.
