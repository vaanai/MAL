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

## 11. AMENDMENT 1 (forward run, 60 s pool-state sampling)

Status: **registered by the merge of this section.** The ruling on it is the QP verdict on the merged head, recorded as the QP comment on the PR that merges this section (PR #1487), together with the reviewer verdict there. This amendment adds a forward test beside sections 1 to 10; it does not change them. It says nothing about whether FLOOR-CPMM makes money, and no result is claimed. No forward row has been read: the only forward facts used are the field names of `tools/floor_cpmm_forward_rec.py` (PR #1486, merge daf50a6c) and the QP VERDICT 34856cb3 OK comment on it. No price, reserve or result appears here. Every later reference to "this section" means the blob of this file at the merge commit; the job record pins that blob sha (`POSTX:blob_sha`).

Placeholders: `POSTX:job_number`, `POSTX:first_hour_file` and `POSTX:blob_sha` are filled by the manager on the job start, in the job record and in the HOLDOUT_LEDGER row; nothing else here is open.

**11.1 What the forward run is, and the seal.**
- The data is `/data2/mal/floor-cpmm/forward-1124/` on mal-research-0 (HOLDOUT_LEDGER row "FLOOR-CPMM forward pool-state"): one row per pool per poll, `--interval-s 60`, each row carrying `ms` (the recorder's poll time), `slot` (the context slot of its own getMultipleAccounts call), `base_reserve` and `quote_reserve`, `token_supply` (S of section 3), the decoded owed-fee fields and the veto fields. Hours: from `POSTX:first_hour_file` of job `POSTX:job_number`, up to 800 h in total (11.6).
- Purpose: an out-of-sample FLOOR-on-CPMM paper test. The data is recorded after this section is merged and its blob pinned, and no rule constant below is tuned on it. The constants (TP 1.5, 24 h hold, E1, E2, the carried-over gates) come from FLOOR and from the in-sample replay's registration, so the winner's curse of the header (item 4) still applies to them.
- **The rows stay sealed until the kill read of 11.6.** They are read by exactly one reader: the forward pricer `tools/floor_cpmm_forward_price.py`, named here and written later as its own T1 PR (tested on synthetic rows only, pinned by commit before the read). It is run as a MiScusi job, in two modes only: the counts-only check of 11.6 (prints one integer) and the kill read (once). No other reader exists: no agent, owner or Console look at a row, a pool address, a reserve, a price, a trade outcome, a per-trade table or an interim P&L or trade count before the kill read, other than the counts-only integer of 11.6. A look outside this path is reported as a breach of the seal, and the read is then reported compromised.
- Before the kill read, only the counters of `status.json` may be read: polls, rows, gaps (the list and `gap_ms`), errors, pools and the progress line. These are timing and volume, not outcomes.
- **Coverage gap**: pools migrated between 2026-11-21T08:51:11Z (the pool list's last record) and the recorder's start are in neither the 242-record list nor forward discovery. They are not in the universe. The result text states the start instant.

**11.2 Sample semantics (the 60 s grid replaces swap prints).**
The forward data has pool states, not swap events, so the print-based items of section 3 and the slot arithmetic of section 4 are replaced as follows. All are fixed now.
1. **Sample time** is the row's `ms`. A "sample" of a pool is one of its rows with `err` null and `quote_reserve`, `base_reserve` and `token_supply` all non-null.
2. **Decide on a sample, price at the next.** A decision is made on sample i at time `t_i = ms_i`. With latency `L` (1.9 s or 4.085 s, the two cells of section 7), entry is **priced at the first sample j of that pool with `ms_j >= t_i + L`**, using sample j's reserves. A decision is never priced on the sample that triggered it. On a 60 s grid both latencies land on sample i+1 unless a gap intervenes, so the two latency cells differ only inside a gap; the result text says so. The `slot_s` estimate, the K-slot rounding and the exit-lag slot form of section 4 are not used on forward data.
3. **Exit**: take-profit and time exit are decided on samples. A take-profit decided on sample i is **priced at the first sample with `ms >= t_i + 0.55 s`** (section 4's minimum exit lag, as time), which is sample i+1. The 24 h time exit has decision time `entry decision time + 24 h + 1.9 s` and is priced at the first sample at or after it.
4. **Missed intra-minute take-profits are declared, not repaired.** A take-profit reached and reversed between two samples is not seen; such a position exits later or at the time exit. No intra-minute path is interpolated. The result text states this bias on exits and the unknown direction of the bias on entries (a price move between samples is neither paid for nor seen).
5. **Reserves are corrected for fees owed.** The row carries the owed fees as `protocol_fees_token_0/1`, `fund_fees_token_0/1` and `creator_fees_token_0/1`, by token side. The rows do not carry which side is WSOL: the pricer takes `wsol_is_token0` from the pool's entry in `pools.json` (a pricer-only read, sealed with the rows). **When `wsol_is_token0` is true, the quote-side owed fees are the `_token_0` fields and the base-side owed fees are the `_token_1` fields; otherwise the quote side is `_token_1` and the base side `_token_0`.** Priced `quote` = `quote_reserve` minus the quote-side protocol, fund and creator fees owed; priced `base` = `base_reserve` minus the base-side ones. All come from the same call as the vaults. The raw-vault variant is reported beside it.
6. **Guard on samples** (`GUARD = 1.15`, section 4): the buy reverts and pays one send if `(net / tokens) / spot > 1.15`, where `spot` is the spot of the **decision sample** (corrected reserves) and the fill comes from the landing sample. A guarded buy is `-send`, never stranded.
7. **Haircuts, send fee, stranding, own-buy re-injection and the TP mark (cell stake, no 0.99 / 1.01 factors)** are section 4 unchanged, applied to sample reserves.
8. **Pressure-fail leg: a labelled proxy, in force for the kill read only.** `ssb` and `nearby` of section 6 cannot be computed from pool states. The forward read reports the flat 15% leg and, as the pressure leg, section 6's formula with two proxies: `flow_rise` = max(0, rise of the corrected quote reserve from the decision sample to the landing sample), used for `nearby`, and `buy_flag` = 1 if `flow_rise > 0` else 0, used for `ssb`, intercept refit per cell as section 6. A 60 s net rise is **not** a lower bound of the stack's 2 s gross-buy window (`NEARBY_WINDOW_S = 2.0`) nor of its landing-slot buy count; it is a different quantity. Because the intercept is refit to mean p = 0.289, an all-zero proxy reduces to a uniform 28.9% fail, and the proxy only spreads p across trades. **It never counts as the pressure-fail model for any promotion-gate item.** If the pricer cannot compute it, the pressure leg is NOT_AVAILABLE and the kill read uses the flat leg only.
9. **Entry gates carried over.**
   - G1 (canonical sell print): replaced by a **sample-to-sample drop in the corrected quote reserve** (net sell flow since the previous sample of the pool), evaluated on every sample, no "first time" rule. A sample pair whose corrected `q * b` falls by more than `K_TOL = 1e-3` (relative) is a liquidity event, not a G1 trigger; these are counted.
   - G2 not ported (as section 3). E1 (`excess <= 0.5 SOL`) and E2 (`q_floor >= 10 SOL`) are evaluated on the decision sample with corrected reserves and the sample's own `token_supply`.
   - G3 (pool age in [24 h, 200 h]) with base time M = the pool's `open_time` from the PoolState tail (byte 373), a chain field. It was checked against the migration time on one list pool only; the result text says so.
   - G5 (one open per pool; 60 s cooldown after the exit decision) ported.
   - G6 (previous print within 6 h): replaced by "the previous sample of the pool is within 6 h".
   - **G9 (history window, `HIST_HOURS = 26`, rule:158) is ported** as keeping the last 26 h of samples of each pool for the gates; a sample outside the window is not used.
   - **G7 (24 h volume including this print >= 1 SOL): the deciding form is a lower bound.** The sum of the absolute change in the corrected quote reserve over consecutive non-gap sample pairs of the pool within the last 24 h, ending with the pair that contains the decision sample, excluding liquidity-event pairs, must be >= 1 SOL. Apart from liquidity events this is a lower bound on the WSOL volume, so it never admits a trigger that FLOOR's volume test would reject. A pool with less than 24 h of samples uses the samples it has (still a lower bound). The **no-G7 form** (the same book without this test) is printed beside it as report-only; it is never read as the deciding cell.
   - G8 (post-trade spot over the spot at the last print 6 h back `< 0.80`): ported on samples as the spot of the decision sample over the spot of the latest earlier sample no more than 6 h back, `< 0.80`; with no such sample there is no trigger.
   - G10 (uncapped book; K=4 FIFO report-only leg) ported.
   None is tuned on forward rows.

**11.3 Gaps and missing pool samples.**
1. **A gap** is any interval between two consecutive samples of a pool longer than `2 x 60 s = 120 s` (the recorder's own threshold for the `gaps` list of `status.json`), and any interval covered by a recorder gap entry or a restart.
2. **No entry is decided across a gap.** A sample whose previous sample of the pool is more than 120 s earlier cannot trigger an entry. The first trigger-eligible sample after a gap is the second sample after it. Refused triggers are counted.
3. **An entry decided before a gap and landing in it** is priced at the first sample at or after the landing time (after the gap) and marked "landed after gap". If that sample is more than 300 s after the decision, the entry is **not filled**: it is counted as unfilled, with no send paid. This is a neutral rule for an unknown fill, not a conservative one.
4. **An exit decided in a gap** (a time exit falling inside it) prices at the first sample after the gap. A take-profit is decided only on samples, so none is decided in a gap. The result text prints how many exits were delayed by a gap and the longest delay (the delay has no bound).
5. **A missing per-pool sample** (a failed poll chunk drops its pools for one poll without a whole-poll gap entry and counts `poll_chunk_errors`) is a gap for that pool by rule 1 if it leaves more than 120 s. The result text prints `poll_chunk_errors`, the `gaps` list and `gap_ms` from `status.json`, the number of pools with at least one gap, and the share of pool-days lost to gaps.
6. **A pool that stops being sampled.** A position whose exit decision falls at or before the end of the recording, but whose exit cannot be priced because the pool has no sample at or after the landing time (an `err` row or no row until the end, while the recording continued), **stays in the headline as stranded**: `-stake - send - 2,039,280` (section 4, stranding). These positions are listed, and the last-good-state variant (priced at the pool's last sample) is printed beside the headline. "Open at run end" (section 4, last bullet) is kept only for positions whose exit decision falls after the end of the recording.
7. A pool whose token mint or PoolState read is undecodable at the decision or landing sample counts as a missing sample.

**11.4 Fee tier.** The pool fee is `trade_fee_rate + creator_fee_rate` (ppm of 1e6), where `creator_fee_rate` counts only if the PoolState's `enable_creator_fee` is true. The recorder reads the AmmConfig rates once, when the pool is resolved, and copies them into every row, so the rates are constant for a pool; a pool with a null rate is **excluded and counted**, never priced with an assumed rate. The pool fee replaces `fee_ppm(Q, B)` on entry, TP mark and exit, as section 4. The section 5 cross-check against SwapEvent fees is not possible without swaps. The recorded `creator_fee_on` (which token side the creator fee is taken on) is not used in the pricing: the combined rate is applied to the input side of each leg, as section 4's `fee_ppm` places it. The distribution of `creator_fee_on` over the universe is printed, and the effect of this simplification is declared second order, not measured.

**11.5 Vetoes (universe; tested on the pool's rows; fixed now).** A pool is excluded from the forward universe, and counted by reason, if any of these holds on its **latest row at or before the decision sample**:
1. `lp_locked_frac < 0.95` (null, because the lock read has not completed, counts as `< 0.95`);
2. `mint_authority_set` true (null counts as set);
3. `freeze_authority_set` true (null counts as set);
4. `token_program` null, or a Token-2022 `token_program` with `token_2022_transfer_fee` true or null. Section 2 item 5 already excludes a transfer-fee extension; nothing here prices one.
`mint_authority_set`, `freeze_authority_set`, `token_program` and `token_2022_transfer_fee` are read once, when the pool is resolved, and copied into every row; only `lp_locked_frac` is filled in later. So an authority set after the resolve is not seen (declared, 11.8). The wording "latest row at or before the decision sample" is look-ahead-free. Vesting remains an unchecked veto (section 2, item 6), and Token-2022 extensions other than the transfer fee (permanent delegate, transfer hook) are unchecked. The quote is WSOL (the recorder lists only those). This run has no look-ahead toward eligibility, unlike the replay (header, item 2); the result text says so.

**11.6 The read: when, how often, and what it may print.**
- **Recording length.** The job starts with `--max-hours 336`. The recording is extended by resume with the same out dir only by a ledger edit before the hours it adds, and never beyond a **hard cap of 800 h** from `POSTX:first_hour_file`. If the cap runs past the end of the ledger row's Hours envelope, the extension edit widens the envelope first. The pause between a job reaching its limit and its resume is a restart gap under 11.3 item 1 (it delays exits and refuses entries); the result text prints the pause length. The manager files the next extension edit before the running job reaches its limit, to keep the pause short.
- **Check instants** (hours from `POSTX:first_hour_file`): **336 h, 480 h, 624 h and 800 h**.
- **The counted set (outcome-free).** The **counted set** at a check instant `C` is every **filled entry** of the deciding cell (0.25 SOL, 4.085 s, deciding G7 form) whose entry decision time is at or before `C - (24 h + 1.9 s + 300 s)`, whatever its exit or outcome. A filled entry is a non-guarded buy that landed (a guarded buy and an unfilled entry, 11.3 item 3, are not). Because the cutoff is a fixed time before `C` and does not depend on how a trade ended, the count cannot depend on recent winners. The **kill read scores exactly this set** and no other entry; entries decided after the cutoff are not scored. Exits of the set are priced on rows with `ms < C`; an exit with no sample before `C` follows 11.3 item 6 (stranded in the headline, last-good-state variant beside it). "10 trades" everywhere in this section means the size of the counted set. Residual dependence, declared: G5 re-entry in a pool can come sooner after an early take-profit than after a time exit; this is small, because G8 needs a fall of more than 20% over 6 h.
- **Counts-only check.** At each check instant the pinned pricer is run once in counts-only mode. It prints **one integer** and nothing else (no P&L, no per-trade row, no pool, no day): the size of the counted set at `C`.
- **Pinned requirements for `tools/floor_cpmm_forward_price.py`** (its T1 PR is checked against these; it does not write them):
  - (a) every mode takes `--until <ms>` (the check instant `C` in ms), refuses any row with `ms >= until`, and refuses to run before `C`;
  - (b) in counts mode, stdout carries exactly one integer line and stderr nothing; it writes no file other than the marker of (c); the MiScusi job log is the integer;
  - (c) one counts run per check instant, logged: a marker file for that instant records the run and refuses a second counts run at the same instant;
  - (d) kill mode refuses unless the counts marker for the same instant records an integer >= 10, and writes a one-look marker that blocks any second kill run;
  - (e) its tests use synthetic rows only; no real row, smoke dir or out dir is opened by a test.
- **Which read happens when.**
  1. At the first check instant where the integer is **>= 10**, the **kill read** runs, once, on the rows before that instant, and the recording stops there.
  2. If the integer is < 10 at 336 h, 480 h or 624 h, nothing is read, and the recording is extended to the next check instant.
  3. If the integer is < 10 at **800 h**, the line is **NOT_DECIDABLE and ends**. The rows are never read for outcomes. The recording stops.
- **Kill read.** Cell 0.25 SOL / 4.085 s, deciding G7 form, on the counted set, under the flat 15% leg and the pressure proxy leg of 11.2 item 8 (flat leg only if it is NOT_AVAILABLE). K1 (mean SOL per trade > 0) and K4 (total after dropping the top 3 trades > 0) must hold under each leg that exists. **K2** (trades per day >= 0.3) holds with **D = recorded hours / 24** at the read instant (10 trades inside the 800 h cap is at least 0.3 per day). K3 of section 9 is replaced by the 10-entry floor above.
- Printed beside it: the four cells of section 7 (0.10 and 0.25 SOL, 1.9 s and 4.085 s), the no-G7 form (report-only), the raw-vault variant, the stranded-pool list and last-good-state variant of 11.3 item 6, the gap and veto counts, the refused triggers and liquidity events, trades per day with the D above, the restart pause lengths, and the coverage-gap statement of 11.1.
- **The promotion gate is unchanged and is not cleared by this run alone.** It needs at least 100 out-of-sample trades, at least 5 distinct UTC days with a majority positive, a lower 90% CI bound of mean SOL per trade > 0 (1,000 draws, seed 1, 5th percentile) and a positive total after removing the top 3 trades, under both the flat 15% and the pressure-fail models. QP's guess (new-income-streams section A1, itself a guess) is about 14 trades in 336 h, which supports a kill read and cannot reach 100. The pressure proxy never counts for the gate. A passing kill read earns at most a longer forward recording under a new registration and, separately, a CPMM swap builder and a live-lane registration; it is not a promotion and not "the book makes money".
- Both fail models are PumpSwap-calibrated; no CPMM live fill exists (section 10, item 6).

**11.7 The job (fixed command shape).**
```
python3 -m tools.floor_cpmm_forward_rec --out-dir /data2/mal/floor-cpmm/forward-1124 --max-hours 336
```
- It does **not** pass `--list`, `--list-sha256-prefix` or `--rpc-url` (the pinned list and the public RPC default are the registered ones), nor `--discovery-address`. Other flags stay at their defaults (`--interval-s 60`, `--rps 2`, `--max-failed-polls 60`).
- Run as a manager MiScusi job on mal-research-0 from a clean detached tree at the commit pinned in the job record (PR #1486 merge daf50a6c, or a later merge that leaves the recorder bytes unchanged), with **0.5 CPU**; `cpu.stat` is checked after 1 h (a throttled recorder makes gaps). The out dir is **fresh**: `forward-1124` must not exist before the start, and the hours of the smoke jobs #3308, #3312 and #3313 are spent and not part of this run.
- No Helius key and no credits: public RPC only. The job record carries `POSTX:blob_sha`, the blob sha of this file at the merge commit.

**11.8 Gaps declared in advance (forward).**
1. The G7 deciding form is a lower bound on volume (liquidity events excluded), so it is stricter than FLOOR's volume test; the no-G7 form is report-only.
2. Intra-minute take-profits and intra-minute entry moves are invisible; the direction of the bias on entries is unknown.
3. `flow_rise` and `buy_flag` are proxies (11.2 item 8) and never count for the gate.
4. The two latency cells are nearly identical on a 60 s grid.
5. Vesting and Token-2022 extensions other than the transfer fee are unchecked.
6. At most about 14 trades in 336 h (a guess), so any mean is noisy and the top-3 drop removes a large share; the cap of 800 h can end the line NOT_DECIDABLE.
7. The coverage gap of 11.1.
8. The fee rates and the mint, freeze, token-program and transfer-fee flags are read once at pool setup (11.4, 11.5), so a later change is not seen.
9. An exit delayed by a gap has no bound (11.3 item 4); the count and the longest delay are printed.
10. `creator_fee_on` is recorded and unused (11.4); the `open_time` base time was checked on one pool (11.2 item 9).
11. QP's non-blocking notes on #1486: the URL guard does not catch a token in a URL path (hence no `--rpc-url`); a null lock result counts as 0 locked (the veto then excludes the pool); the lock and resolve steps are not under the discovery time budget.
