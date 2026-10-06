# EXP-016 plan (exploration stage): a rug-risk entry veto layered on the frozen EXP-012 book

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan, written before any EXP-016 code, label, feature or result exists.** It is not a pre-registration. A pre-registration (Part 1, which names the confirmation block's owner) and a freeze record (Part 2) follow only if the screen in §7 passes. **Pin:** the PR head sha the manager records in a PR comment before any EXP-016 code reads an outcome row is the pinned version. Every later edit is listed in a dated "post-pin edits" section. |
| **Date** | 2026-10-06 |
| **Owner request (2026-10-06)** | Live probe trades lost −76.5% and −95.5%. Each was a one-step gap past the −30% stop. A +90.5% winner gapped past the take-profit. A stop cannot fill inside a gap, so the defense is not entering. |
| **Why a new family** | The rug screen #191 ([note](../ARTIFACTS/lab/exp012-rug-risk-2026-10-06.md), job #191, PR #336) found that none of 6 concentration vetoes helped. Its "rug" label had a base rate of 0.5664: it measured migration volatility, not rugs. Its features were only bonding-curve holder shares, buyer count and creator prior mints. §1 says what changes. |
| **Hypothesis** | A strict, outcome-defined rug event (§2) is predictable at the EXP-012 decision time from launch-bundle, creator-behaviour and wallet-cluster information on the tape (§3). Vetoing the predicted rugs improves the frozen EXP-012 book, paired on the same mints, at realistic costs, and does more than remove winners. |
| **Frozen book it is layered on** | EXP-012 as frozen: model md5 `a1810d219ed61db64a396f40dc302ce5`, threshold 0.8030766588450794, `migrate` trigger, `tp50_sl30`. Nothing frozen is edited. The veto can only remove entries. |
| **Target confirmation block** | `[2026-08-02T12, 2026-08-08T12)`, ledger row "Third backup confirmation block" (reserved in #380, being walked now). It is claimed only by a merged EXP-016 Part 1, and only if no other pre-registration claimed it first (§8). |
| **Prior (my honest estimate)** | Low. Screen pass about 15%. A confirmation PASS, which needs the kept book to clear the full gate as well, about 5%. §10 gives the reasons. This is an estimate, not a measurement. |
| **Measured by this file** | Nothing. No row under `/data/mal` was read. Only repo code and docs were read. |

## 1. What #191 got wrong, and the answer here

| #191 | EXP-016 |
| --- | --- |
| Label `one_step` OR `crash50`: any sell print ≥ 30% below the previous print, or a print below 0.5·P0 before the tp, within 15 min. Base rate 0.5664. | One strict label (§2): the pool's effective SOL side falls ≥ 36.75% within 3 slots (a −60% price step), or an untaped drain of the same size between two prints, **inside the trade's own hold window**. Expected base rate in the low single digits (§2.4), with a guard that stops the family if it is above 15%. |
| Features: holder shares at migration, buyer count, top-wallet volume share, creator prior mints. | New information (§3): who bought in the launch slots, and how much they still hold; the creator's buys and sells on the curve; slot-based sniper share; wallets seen as serial launch buyers or as dumpers on earlier mints in the same block (causal). The #191 features enter only as covariates. |
| Rule thresholds were round numbers. | Rule thresholds come from pool arithmetic, before any data (§5.1): 12% of supply sold into a seed pool is exactly a label-sized step. |
| Nested selection over 6 rules on the 9 hammered days. | Nested leave-one-day-out over 6 candidates (4 rules plus a regularised logistic at 2 veto fractions) on up to 36 dates. The deciding bars use the 27 non-P1 dates. |

#191's descriptive finding stands, and this plan takes it as a warning: concentrated pools were **not** worse on its 9 days (top1_share ≥ 0.43 and top3_share ≥ 0.70 quintiles had positive pressure means). Concentration alone is common, at least 304 of 802 filled entries had a single non-creator holder at ≥ 10% of supply. EXP-016 therefore bets on *who* holds it, not *how much* is held.

## 2. The rug label (fixed now, before any data is read)

### 2.1 What the tape has

From the code, not from data:

- **PumpSwap rows** (`observe/trade_decode._decode_buy/_decode_sell`, slimmed by `observe/trade_store.stored_trade` to v2): `venue`, `mint`, `trader`, `side`, `sol_lamports` (user gross), `token_raw`, `quote_reserve`, `base_reserve`, `price_sol`, `pool`, `slot`, `signature`, `event_index`, `event_ts`, `quote_mint`. The backfill adds `tx_index`, `block_time` and `source` (`tools/pump_history_backfill.rows_from_block`). **`quote_reserve`/`base_reserve` are the pool vaults before that trade** (`tools/paper_price_path.pumpswap_post_trade_reserves` docstring; `tools/pumpswap_virtual_adapter`). The v2 row drops `pool_quote_amount`, `lp_fee`, `protocol_fee` and `creator_fee`.
- **Bonding rows** (v1): the same trade fields with `pool = None`, `quote_reserve`/`base_reserve` = the curve's virtual reserves **after** the trade.
- **Lifecycle rows:** `create` (`mint`, `creator`, `trader`, `slot`, `signature`, `tx_index`, initial virtual reserves, supply), `complete`, and `migration` (`mint`, `pool`, `sol_lamports`, `token_raw`, `slot`).
- **The Oracle live tape (P1 sources B and C) has no explicit `tx_index`.** Order inside a slot there is first-read order (`paper_price_path.TxOrder`). The getBlock sources (P1 A, P2-P4 and the confirmation block) have the true `tx_index`.

**Liquidity deposit and withdraw events are not on the tape.** The decoder knows four discriminators only: bonding `TradeEvent`, PumpSwap `BuyEvent`, `SellEvent` and `CreatePoolEvent`. The walker stores decoded rows, not raw logs, so adding them would need a re-walk (about 1.3M credits per 6-day block). The label does not need them: every print carries the pool's vault reserves, so an untaped withdrawal shows up as a reserve gap at the next print (event B below). Whether migrated pump.fun pools can be withdrawn at all (LP burnt at migration) is not checked in this repo, and the label does not rely on it.

### 2.2 Definition

For a migrated mint m:
- **Canonical pool** `p_m` = the `pool` field of m's `migration` row. Only prints with `pool == p_m` are used. They are ordered by `(slot, tx_index, event_index)`.
- **Effective quote.** Before print i: `E_i = quote_reserve_i + V(p_m)`, where V comes from the pinned pool-to-V map (`pool_v_0909.json`, sha `70914a16…5b42e` for the exploration pools; a new map for the confirmation block, §8). After print i: `E⁺_i` = the post-trade vault from `tools.paper_price_path.pumpswap_post_trade_reserves` (with its `fee_ppm` fallback, because v2 rows drop the fee fields) plus V. The fallback's error is about the fee on one trade, around 1% of that trade's quote, which is small next to a 36.75% threshold.
- **Hold window `H_m`.** This is defined only for a FILLED frozen-cell trade. It runs from the entry-state print the frozen simulator fills against at the deciding cell (k = 6, §6) to the print its `tp50_sl30` exit fills against, with exit lag 2, inclusive. A MISS has no hold window and no label.

**RUG(m) = 1 iff event A or event B occurs inside `H_m`:**
- **A, a flow step.** For some slot s that has a print in `H_m`, let `A_before(s)` be E of the first print at s, and `A_after(s)` be E⁺ of the last print in `H_m` with slot ≤ s + 2. A fires if `A_after(s) / A_before(s) ≤ 0.6325` (= √0.40). Under constant product this is a spot-price fall of at least 60% within 3 slots. Three slots is the stop's reaction horizon: the trigger slot plus exit lag 2.
- **B, an untaped drain.** For consecutive prints i, i+1 in `H_m`, B fires if `E_{i+1} / E⁺_i ≤ 0.6325`. The pool lost at least 36.75% of its effective SOL with no taped trade in between. That covers a liquidity withdrawal, an undecoded instruction or a missing row. In every case the reserves are real on-chain state, and the next sell gaps.

**Properties, by construction.** A RUG trade cannot exit at the tp after the step. The step starts below 1.5·P0, or the tp would already have fired, and it ends below 0.40 of its start, so below 0.6·P0. Its stop fires inside the step or right after it, and the exit lands near or below 0.6·P0 unless the price recovers within the 2-slot exit lag. RUG is therefore, up to that lag, a subset of "stop exits with a gap of at least 10 points past the stop", plus censored rows. A dump **after** a tp exit is RUG = 0, because it costs the book nothing.

**Reported, never selected on:** (i) **RUG70-1**: the owner's example, one slot, price ratio ≤ 0.30 (E ratio ≤ 0.5477); (ii) #191's label, so the two are compared on the same rows; (iii) A-only and B-only counts; (iv) the count of hold windows that the frozen simulator fills from a non-canonical pool. The simulator's print list is per mint (`latency_curve._Mint.fillable`), so this is a check on the label's pool choice (§11, Q6).

### 2.3 Pool arithmetic (the scale of a RUG)

This is arithmetic, not a measurement. A migrated pool is seeded at vault 67.41 SOL + V 17.58 SOL = E₀ 84.99 SOL over 206.9M tokens (`tools/probe_executor.py`).
- Event A from the seed state needs about **31.2 SOL out within 3 slots**. That is about **120M tokens, 12.0% of supply**, sold into the pool.
- The owner's −70% in one slot needs 38.4 SOL out, which is 171M tokens or 17.1% of supply.
- If the whole circulating supply (about 793M tokens) were sold into a seed pool, E would fall to about V, and the price to (V/E₀)² = 4.28% of the seed price (−95.7%). The live −95.5% loss matches a near-total dump.

### 2.4 Expected base rate, and the guards

- **Live:** about 3 gap losses in about 38 closed probe trades: C71Lk8Ko (from above −30% to −69% between two 400 ms polls, sold at −89.6%, −47,168,631 lamports; `tools/exp012_rug_risk.py` docstring) and the owner-reported −76.5% and −95.5%. 3/38 = 7.9%, exact 95% binomial interval about 1.7% to 21.4%. To gap from just above −30% to −76.5% takes a step of at least 66%, and to −95.5% a step of at least 94%. The −60% label threshold catches both with slack. The −70% single-slot variant may miss the first, so it is reported, not primary.
- **Structure:** RUG needs a label-sized sale (≥ 12% of supply near seed) concentrated in 3 slots, or a drain of the same size. #191's `one_step` needed only a 30% fall in one print, about 16% of E.
- **Expected:** a few percent of frozen-selected filled trades, against #191's 56.6%.

**Guards** are computed first, after the `started` tries line, and printed before any candidate is scored. They are outcome-level but stop-only: they can end the family, never change it.
- **G1, too broad.** If RUG's rate among frozen-selected filled trades on all pool dates is above **0.15**, the outcome is `LABEL_TOO_BROAD` = FAIL.
- **G2, too rare.** If there are fewer than **30** RUG trades among frozen-selected filled trades on the non-P1 dates, the outcome is `LABEL_TOO_RARE` = FAIL. A paired gain would not be visible.
- No second label, no threshold change and no new window follow either outcome.

## 3. Candidate features

**Decision time.** The live executor decides when it sees the migration, and k = 6 is landing latency. Every feature uses only events **strictly before the migration slot** (slot-based features) or EXP-012's `causal_events` receive-time cutoff (its existing features). Nothing from the 6 landing slots is used (§11, Q1). Bonding-curve trades all precede the `complete` event, so curve features are complete at migration.

**Definitions.**
- **Supply** = 1e15 raw units. **Launch slots** = `[create_slot, create_slot + 2]`; N = 2 matches `funding_graph.SNIPER_SLOT_DELTA`.
- **Creator set** = {create row `creator`, create row `trader`}.
- **Held** = curve buys minus curve sells by that wallet up to the cutoff, floored at 0 per wallet. This is a trade-flow balance only: **SPL token transfers are not on the tape** (§10).
- **Block history** = the same block's own hours only, trailing 24 h, every event's slot strictly before this mint's migration slot. Each block is left-censored for about its first 24 h, as `creator_prior_mints_24h` already is.
- **Dump step** (for d2 and d3, on any mint and any venue): event A's arithmetic, with the curve's post-trade virtual SOL standing in for E on the bonding curve. It is used only when the whole 3-slot window ends before this mint's migration slot.

| id | feature | definition | availability | new vs #191 |
| --- | --- | --- | --- | --- |
| a1 | `n_launch_buyers` | distinct non-creator-set wallets with a curve buy in the launch slots | tape (slot, trader) | new |
| a2 | `launch_supply_bought` | tokens those wallets bought in the launch slots / supply | tape | new |
| a3 | `launch_supply_held` | those wallets' summed held balance at cutoff / supply | tape | new |
| a4 | `n_create_slot_buyers` | distinct non-creator-set wallets buying in `create_slot` itself (bundle) | tape | new |
| a5 | `max_slot_cohort_held` | max over slots s of the held balance of wallets whose **first** curve buy was at s, cohorts of ≥ 2 wallets / supply | tape | new |
| b1 | `creator_launch_share` | creator-set tokens bought in the create transaction (same `signature`) / supply | tape | new |
| b2 | `creator_n_buys_after` | creator-set curve buys after the create transaction | tape | new |
| b3 | `creator_n_sells` | creator-set curve sells | tape | new |
| b4 | `creator_sold_frac` | creator-set tokens sold / tokens bought (0 if none bought) | tape | new |
| b5 | `creator_share` | #191's: creator held balance / supply | tape | covariate (not new) |
| c1 | `sniper_buy_share` | EXP-012's: share of curve buy SOL within 3 s of create (time-based; getBlock time is 1 s resolution) | tape | covariate (not new) |
| c2 | `launch_sol_share` | share of curve buy SOL in the launch slots (slot-based sniper share) | tape | new |
| d1 | `serial_launch_held` | held balance at cutoff of this mint's launch buyers that were launch buyers on ≥ 3 other mints in block history / supply | tape, needs a causal per-block pass | new |
| d2 | `creator_prior_dumps` | creator-set's earlier mints in block history with a dump step | tape, causal pass | new |
| d3 | `prior_dumper_held` | held balance at cutoff of wallets that sold inside a dump step on another mint in block history / supply | tape, causal pass | new |
| d4 | `creator_buyer_recurrence` | this mint's launch buyers that were launch buyers on any of the creator-set's earlier mints in block history | tape, causal pass | new |
| e1 | `top3_share` | #191's | tape | covariate (not new) |
| e2 | `n_buyers` | EXP-012's | tape | covariate (not new) |
| f1 | creator funding source | funder of the creator wallet before the create: exchange / fresh / shared with early buyers | **extra RPC**: `getSignaturesForAddress(creator, before = create signature)` plus up to 5 `getTransaction` (`tools/funding_graph.py` limits) | **phase 2, optional, not in this plan** |

**(e) What existing code computes.** `tools/exploration_entry_model.compute_features` has EXP-012's 18 frozen features. `tools/exp012_rug_risk.rug_features` has #191's 6. Neither records slots for curve events: `_Feat.record` stores receive time only. So a1-a5, b1-b4, c2 and d1-d4 need a new accumulator that keeps `slot`, `signature` and `trader`, and d1-d4 need a time-ordered per-block pass over every mint. The EXP-012 18 are not model inputs here (only c1 and e2), because the veto should not re-learn EXP-012's own selection.

**f1 cost estimate (phase 2).** This is unmeasured; a probe comes first. Each creator needs at most 3 signature pages plus 5 transactions, so at most 8 calls. At about 1 credit per call (the Developer-plan getBlock figure; not confirmed for these two methods):
- creators of the frozen-selected mints only (about 4k over the pools and the confirmation block): about **32k credits**;
- creators of all migrations (about 35k): about **0.3M credits**, about 8 h at 10 rps;
- adding about 5 launch buyers per mint: about **1.5M credits**.

If these methods bill at 10 credits a call, multiply by 10. Causality holds because `before = create signature` returns only earlier history. Phase 2 would be a **separate family with its own plan and tries**, not a rescue of a phase-1 FAIL (§11, Q3).

## 4. Pools, dates and universe

- **Pools:** EXP-015's P1-P4 by reference ([EXP-015 plan v2 §2](EXP-015-pooled-retrain-plan-v2.md)): P1, the 9 days; P2 `explore-0814`; P3 the spent fresh-0903 block; P4 the EXP-011 block. P4 is used **only** after its ledger follow-up records EXP-015's four preconditions. Without P4 the run says so in its first line.
- **Dates:** 36 UTC migration dates (27 non-P1), or 30 (21 non-P1) without P4, as in EXP-015 §3.
- **Never read:**
  - the EXP-009 block;
  - fresh-0828 (EXP-013's target) and fresh-0808 (EXP-015's target);
  - the confirmation block before Part 1 and its lock;
  - any hour at or after 2026-10-02T00 (the forward walk, `/data/mal/blocks/forward-1002`, and the forward-paper runner's files).
- **Frozen selection.** On P1, EXP-012's stored OOF scores (as #191). On P2-P4, the frozen model's scores. The selected set is every migration with score ≥ 0.8030766588450794.
- **Training universe for the logistic:** every FILLED migration on the training dates at the deciding cell, labelled by §2. That is about ten times the selected set, so positives are not scarce. MISS rows are excluded (no hold window) and counted.
- **Unpriceable pools:** EXP-015 §11 item 11 applies unchanged (pool-based removal before `started`, 0.5% cap, bias statement, report-only total-loss sensitivity).

## 5. Candidates, model and tries cap

### 5.1 The 6 candidates (the whole try budget)

Each candidate vetoes a frozen-selected entry.

| id | rule or model | why this threshold |
| --- | --- | --- |
| R1 | `launch_supply_held ≥ 0.12` | 12% of supply is a label-sized sale into a seed pool (§2.3) |
| R2 | `serial_launch_held ≥ 0.12` | same arithmetic, with the holder group identified by repeat launch-buying |
| R3 | `prior_dumper_held ≥ 0.12` | same arithmetic, with the holder group identified by past dump steps |
| R4 | `creator_prior_dumps ≥ 1` | the creator-set already dumped once in block history |
| L5 | logistic, veto the top 5% | §5.2 |
| L10 | logistic, veto the top 10% | §5.2 |

The thresholds were fixed before data from the pool arithmetic. #191's prevalence figure (single holders ≥ 10% in about 38% of filled entries) warns that R1 may veto too much. That is what the 20% cap in §5.3 is for. The thresholds are not moved.

### 5.2 Model

- `logreg_l2` from `tools.exploration_entry_model.SETTINGS`, imported unchanged: C = 0.5, L2, `class_weight="balanced"`, seed 1.
- **18 inputs:** a1-a5, b1-b5, c1, c2, d1-d4, e1, e2. Counts are `log1p`. Every input is standardised on the training fold only.
- **Veto threshold for Lf:** the (1 − f) quantile of the inner out-of-fold probabilities among the inner dates' frozen-selected migrations. Non-interpolating, `index = round((1 − f)·(n − 1))`.
- No hyperparameter search.

### 5.3 Nested leave-one-day-out (the threshold is never chosen on the confirmation block)

For each outer date d, using only the other dates (35-minute purge around d, as EXP-015 §3):
1. **Inner LODO.** Fit the logistic once per inner date to get inner OOF probabilities. This sets L5's and L10's thresholds.
2. **Score each candidate on the inner dates** by the pooled **pressure** paired mean over frozen-selected migrations (§7, x_m). A candidate is eligible only if it vetoes ≥ 30 filled trades and ≤ 20% of the frozen-selected filled trades on the inner dates.
3. **Pick the best eligible candidate.** It must have an inner paired mean > 0. **"No veto" (frozen) is always a candidate and wins ties.**
4. **Apply it to d.** A rule is applied directly. For the logistic, a model fit on all inner dates is applied with step 1's threshold.

The bars in §7 are computed on the **outer** results of this procedure, not on the best single candidate. Per-candidate tables are report-only.

**Freeze, if §7 passes:** run steps 1-3 once over all pool dates, with one LODO level. The chosen candidate, and for a logistic the model fit on all dates and its pooled-OOF quantile threshold, go into Part 2.

### 5.4 Tries

- **Cap:** 6 candidates, logged before any fit. One `started` line goes to `data/tries.jsonl` (keys `exp016_r1..r4`, `exp016_l5`, `exp016_l10`) with the row-universe and feature-table sha256. Result lines follow.
- The run refuses if any `exp016_*` line already exists. EXP-015 §11 item 9's try-spend and resume rules apply: nothing outcome-derived is printed before `started`.
- **Each candidate counts on every pool it touches**, so +6 per pool. The run reads the file and records the actual counts in its report.

| pool | in `data/tries.jsonl` at f4daaa0 | after EXP-015's 3 (its §7) | after EXP-016 |
| --- | ---: | ---: | ---: |
| P1 (9 days) | 74 | 77 | 83 |
| P2 `explore-0814` | 6 (+1 DEC-017 (a), not in the file) | 10 | 16 |
| P3 fresh-0903 | 0 (+ EXP-012's one read) | 3 | 9 |
| P4 EXP-011 block | 0 | 3 | 9 |

## 6. Costs in every deciding cell

These are EXP-015 §4, unchanged, and the costs `tools/exp015_screen.py` already pins:
- V pricing (vault + V);
- k = 6;
- 0.05 SOL;
- 505,000 lamports per side (a MISS pays the fee);
- `tp50_sl30`, 30-minute cap, exit lag 2;
- the live haircut: `net0' = net0 − max(net0 + size, 0) · 0.0042038` (entry +26.08 bps, sell −16 bps);
- flat 15% and pressure scale 1, both gating;
- `book_stats` and the date-cluster resampler, 1,000 draws, seed 1.

Raw simulator rows, k = 4/8 and lag 0 are report-only.

## 7. Screen bars (exploration, stated before any computation)

**Paired unit.** Over every frozen-selected migration m on the scored dates: **x_m = −v_m · net_m**, where v_m = 1 if the outer-selected veto removes m and net_m is m's haircut net under the leg.
- **A vetoed MISS scores x_m = 0.** It keeps its fee in both arms. Simulator misses never count as gains (the #181 lesson: 40 of 46 vetoes there were misses).
- Censored rows are scored as in the frozen book.

**The screen passes only if all of S1-S5 hold under both fail models.**

- **S1, paired improvement.**
  - On the 27 non-P1 dates: mean x > 0, with CI90 lower bound > 0 under **both** the `book_stats` resampler and the date-cluster resampler.
  - On all 36 dates: mean x > 0.
- **S2, days.** On the non-P1 dates, more than half of the dates have a positive paired sum. A date with no vetoed filled trade counts as **not** positive.
- **S3, not just removing winners.**
  - The RUG share among vetoed filled trades is at least **2×** the RUG base rate among frozen-selected filled trades (lift ≥ 2), on the non-P1 dates.
  - The mean net of the vetoed filled trades is < 0.
  - **Always reported:** the number of vetoed trades that exited at the tp, their total SOL, the vetoed trades' mean and CI90, and RUG precision and recall.
- **S4, concentration.** The non-P1 paired total stays > 0 after removing the **3 vetoed trades with the largest avoided losses**, and again after removing the best date. A veto whose gain is three rugs is not reliable.
- **S5, veto size.** The outer veto removes at most 20% of frozen-selected filled trades on every date set reported.

**Report-only, never gating:**
- the kept book's gate shape (n, days, CI, ex-top-3) on all dates and on the non-P1 dates;
- per-candidate and per-source tables;
- k = 4 and k = 8, lag 0, the raw simulator;
- RUG70-1 and #191's label;
- the unpriceable-mint sensitivity;
- first-half vs second-half signs;
- which candidate each outer fold chose.

**Passes:** Part 1 and Part 2 are written, and the confirmation in §8 follows. **Fails** (including G1 or G2):
- the family is closed: no new label, rule, threshold, fraction or feature set on these pools;
- the confirmation block is released, unread, by ledger edit;
- the PR says EXP-016 failed its screen.

## 8. Confirmation: one read of `[2026-08-02T12, 2026-08-08T12)`

**Block.** The ledger row "Third backup confirmation block": three 48 h walkers into `/data/mal/blocks/fresh-0802/w{1,2,3}`.
- **Before Part 1** (outcome-blind, counts and hashes only):
  - `backfill_verify` in both modes (`--content --min-slots-per-hour 8000`);
  - dedupe, sha256 manifest, and a clean view with `VIEW.sha256`;
  - a V map built from **every** PumpSwap pool traded in the block, with its sha pinned.
- **Ownership.** The ledger names an owner only through a merged pre-registration. EXP-014 is the other named candidate, and the first one merged claims the block (§11, Q4).
- **Buffer.** Block history (d1-d4, `creator_prior_mints_24h`) is built from the block's own hours. fresh-0808, which follows it, stays unread, so the first ~24 h is left-censored, as in training.

**Part 1 and Part 2, after the screen passes and before the read.**
- Part 1 names the owner and the exact frozen veto.
- Part 2 records: candidate id, model and feature-list md5, threshold, feature-code commit, the row-universe and feature-table sha256 values, the view pins, and `FROZEN.md5`.
- **The scorer refuses unless all of it matches.** It uses EXP-012's refusal list as the template: code commit match, a clean tree, an O_EXCL read-once lock at `/data/mal/exp016/HOLDOUT_READ.lock` taken before the first row, a view re-hash before the lock, 144/144 hours sealed, the V-map sha asserted, and V coverage over 99% before the lock.
- A refusal before the lock does not spend the block. Any failure after the lock does.

**The single gating cell.** Frozen EXP-012 selects. The frozen EXP-016 veto removes. The costs are §6. **The kept book** is the frozen-selected entries minus the vetoed ones, and a vetoed MISS stays in the kept book with its fee.

**PASS iff, under the flat 15% model and the pressure model at scale 1:**
- (a) kept book n entered ≥ 100 (MISSes count as entered);
- (b) trades on ≥ 5 distinct UTC dates, with a majority of those dates positive;
- (c) the kept book's mean SOL per trade has a CI90 lower bound > 0 under both resamplers (1,000 draws, seed 1);
- (d) the kept book's total SOL is > 0 after removing its top 3 trades;
- (e) the **paired** improvement mean x > 0, with a CI90 lower bound > 0 under both resamplers;
- (f) the one-sided bootstrap p-values of the kept-book mean > 0 and of the paired mean > 0 are each ≤ **0.0125** (= 0.05/4, Holm rank 1 for the four unread families EXP-013, EXP-014, EXP-015 and EXP-016), 10,000 draws, seed 1, both resamplers.

(f) only tightens the CLAUDE.md gate. EXP-015's pinned 0.0167 (k = 3) is not edited by this plan. Whether EXP-016 is counted there is the manager's call.

**FAIL** if any of these fails under either model. The veto is retired. There is no second read, no re-tune and no second cell, and the block moves to exploration. **NOT_DECIDABLE** is a failure after the lock, with the same consequence.

**Power, rough arithmetic only.**
- August days are thinner. EXP-012's fresh-0903 read had n = 451. Expect about 400-600 frozen entries, and so about 20-60 vetoed fills.
- With a per-trade SD of about 0.02 SOL (DEC-021) and about 25 vetoed fills, (e) at p ≤ 0.0125 needs the vetoed trades to average about **−0.009 SOL** (about −18% of size).
- A RUG at 0.05 SOL loses roughly 0.03-0.048 SOL. Non-rug vetoes average about the book mean. So about a quarter or more of the vetoed trades must be RUGs, a lift of about 5 at a 5% base rate. S3's lift ≥ 2 is the screen's floor, not what the read needs.

**Reported with the verdict, not gating:**
- the frozen book alone on the block. It is the paired reference and is **never cited as an EXP-012 confirmation** (DEC-014 (b), DEC-017 §1);
- vetoed trades and RUG precision and recall;
- the per-date and per-walker tables.

## 9. What a PASS earns

- **What it can claim:** "one gate clearance for frozen EXP-012 plus the veto, at k = 1, on a getBlock-only August block, at modelled costs whose exit lag 2 is optimistic." It is not a profit result and not live evidence.
- **It earns a DEC-021 challenger, and only that.** The variant is "frozen EXP-012 plus the frozen EXP-016 veto". It is registered by its own pre-registration in a DEC-021 walk family, paired against the champion sim arm on the same mints, under both fail models and the §6 costs.
- **Walk 2 timing.** Walk 2's registrations must merge before 2026-10-16T01. That needs EXP-016's code, screen, Part 1, Part 2 and read to finish within about nine days, behind EXP-015's screen on the one-heavy-job rule. That is unlikely, and if it slips EXP-016 waits for a later walk. DEC-021 covers at most 2 walks, so a third walk needs a new DEC.
- **Never a live change without the owner.** This plan asks for the owner's explicit yes before any live veto, even though DEC-021 §6(f) delegates the switch to the manager (§11, Q5). The veto does not change k, the fee, size or wallet. Live feature computation (curve history and 24 h block state on the fast box) is a DEC-021 §7 pre-live check.
- **No effect on EXP-012's 10-16 read.** EXP-016 opens no hour at or after 2026-10-02T00 and changes nothing frozen.

## 10. What it cannot show, and the honest prior

**Cannot show:**
- **Token transfers.** Creators who split supply to fresh wallets look dispersed. Every "held" figure is a trade-flow lower bound on what a group controls.
- **Funding links.** Without f1, a ring funded from one wallet looks like strangers.
- **Rugs after the hold window, or before the entry.** They are not in the label, by design.
- **Pools without a print after a drain.** If no print follows inside the window, B cannot fire. The trade's simulated exit is then censored.
- **Within-slot order on P1's Oracle sources.** It is receive order (§2.1), so A's slot edges may be slightly off there. The deciding bars use getBlock dates.
- **Live gap size.** Live exits leak beyond lag 2 (stops fired at −31% to −74% in early builds). A real rug costs more than the simulator charges, so the veto's value may be understated, while its false positives cost exactly what the simulator says.
- **Drift.** The confirmation block is August, two months before live, and it backcasts. LODO trains on both sides of each date.
- **Why rugs happen.** At best this is a predictive association, not a cause.

**Honest prior (my estimate):**
- **Screen, about 15%.** Concentration did not separate losers in #191. Rugs are rare, so S1 and S4 rest on a few dozen events. The candidates bet that identity (serial launch buyers, past dumpers) carries what amount did not. That is plausible and untested.
- **Confirmation, about 5% overall.** Even a good veto must leave a kept book that clears the full gate on fresh-0802 at realistic costs. That is a property of EXP-012, not of the veto, and nothing since the V correction shows it:
  - #191's frozen pressure CI90 on the 9 days is [−0.00012, 0.00131];
  - the 36 closed live trades total −174,249,232 lamports, against a simulator prediction of +0.00058 per trade (job #208, #327).
- **A veto cannot make a non-edge into an edge.** At best it removes a fat left tail.

## 11. Open questions for the manager

1. **Decision cutoff.** Features stop strictly before the migration slot, because the executor sends on the migration and k = 6 is latency. Use migration + 6 only if the executor can wait. That would be a different k and a DEC-019 matter.
2. **Label strength.** Primary is −60% within 3 slots, or an untaped 36.75% drain. The owner's −70% in one slot is reported only. Choose before the pin. After the pin it cannot change.
3. **Phase 2 funding (f1).** Creator-only lookup is about 0.03-0.3M credits by the estimate in §3. Fold it into phase 1, which must be decided before the pin and adds a probe job, or keep it as a later, separate family.
4. **The 0802 block.** It goes to whichever of EXP-014 and EXP-016 merges a pre-registration first. Is that the intended race, or should the manager assign it?
5. **Live wording.** This plan requires the owner's explicit yes for any live veto, which is stricter than DEC-021 §6(f). Keep it, or defer to DEC-021?
6. **Pool attribution.** The label uses the migration pool only, but the frozen simulator's print list is per mint. Confirm the simulator never fills an EXP-012 trade from a non-canonical pool, or accept the report-only count in §2.2 (iv).
7. **Tries count.** The brief said 77 on P1. The file at f4daaa0 shows 74, and 77 assumes EXP-015's three are logged first. The run records whatever the file says.
8. **Ordering.** EXP-015's screen is queued on research-0 under the one-heavy-job rule. EXP-016's table build (a new causal per-block pass over every pool) is a second heavy job.

## Sources

- **Lab notes and plans:**
  - [exp012-rug-risk-2026-10-06.md](../ARTIFACTS/lab/exp012-rug-risk-2026-10-06.md) (#191);
  - [exp012-exit-veto-2026-10-05.md](../ARTIFACTS/lab/exp012-exit-veto-2026-10-05.md) (#181);
  - [probe-calibration-2026-10-06.md](../ARTIFACTS/lab/probe-calibration-2026-10-06.md);
  - [funding-graph.md](../ARTIFACTS/lab/funding-graph.md);
  - [EXP-015 plan v2](EXP-015-pooled-retrain-plan-v2.md) §2-§4, §7, §11;
  - [EXP-013 plan](EXP-013-graduation-classifier-plan.md);
  - [EXP-012 pre-registration](EXP-012-migrate-entry-model-refreeze-prereg.md).
- **Ledger and decisions:**
  - [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) (Third backup confirmation block);
  - [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md);
  - [DEC-021](../DEC/DEC-021-champion-challenger.md).
- **Code, read only:**
  - `tools/pump_history_backfill.py`, `observe/trade_decode.py`, `observe/trade_store.py`;
  - `tools/paper_price_path.py`, `tools/pumpswap_virtual_adapter.py`, `tools/probe_executor.py`;
  - `tools/exploration_entry_model.py`, `tools/exp012_rug_risk.py`, `tools/latency_curve.py`;
  - `tools/funding_graph.py`, `tools/exp015_screen.py`.
- `data/tries.jsonl` (80 lines at f4daaa0).
