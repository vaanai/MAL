# EXP-016 plan (exploration stage): a rug-risk entry veto layered on the frozen EXP-012 book

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan, written before any EXP-016 code, label, feature or result exists.** It is not a pre-registration. A pre-registration (Part 1, which names the confirmation block's owner) and a freeze record (Part 2) follow only if the screen in §7 passes. **Pin:** the PR head sha the manager records in a PR comment before any EXP-016 code reads an outcome row is the pinned version. Every later edit is listed in a dated "post-pin edits" section. |
| **Date** | 2026-10-06 |
| **Owner request (2026-10-06)** | Live probe trades lost −76.5% and −95.5%. Each was a one-step gap past the −30% stop. A +90.5% winner gapped past the take-profit. A stop cannot fill inside a gap, so the defense is not entering. |
| **Why a new family** | The rug screen #191 ([note](../ARTIFACTS/lab/exp012-rug-risk-2026-10-06.md), job #191, PR #336) found that none of 6 concentration vetoes helped. Its "rug" label had a base rate of 0.5664: it measured migration volatility, not rugs. Its features were only bonding-curve holder shares, buyer count and creator prior mints. §1 says what changes. |
| **Hypothesis** | A strict, outcome-defined rug event (§2) is predictable at the EXP-012 decision time from launch-bundle, creator-behaviour and wallet-cluster information on the tape (§3). Vetoing the predicted rugs improves the frozen EXP-012 book, paired on the same mints, at realistic costs, and does more than remove winners. |
| **Frozen book it is layered on** | EXP-012 as frozen: model md5 `a1810d219ed61db64a396f40dc302ce5`, threshold 0.8030766588450794, `migrate` trigger, `tp50_sl30`. Nothing frozen is edited. The veto can only remove entries. |
| **Target confirmation block** | `[2026-08-02T12, 2026-08-08T12)`, ledger row "Third backup confirmation block" (reserved in #380, being walked now). It is claimed only by a merged EXP-016 Part 1. **EXP-016 has priority on it** (manager decision 2026-10-06, because the owner asked for this study). §8 says what that means for EXP-014. |
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
  - A mint whose migration row has no `pool` is **excluded from both books** (the frozen book and the kept book) and from training. Such mints are counted before `started`.
- **Excluded rows.** Rows whose `trader` is our own probe wallet are dropped from the label and from every feature. The tool pins the probe wallet's public key. No key material is used. The exploration pools end before the probe started, so this guards only the later blocks. Simulated fills are never written to the tape.
- **V rule (pinned; the same for the label and the simulator).** `Vp(p_m)` = the **stored V** from the pinned fixed-parser map (§11, P1), which is the value `pumpswap_virtual_adapter.correct_print` uses, with **v ≤ 0 → vault-only** (`Vp = 0`).
  - The label and the frozen simulator therefore price every print identically, which is P4's test.
  - **Disclosed:** stored V = V0 − A − B, where A and B are pending fee and cashback counters. In the V investigation (2026-10-06) they were at most 0.002 SOL (about 1 bp at E ≈ 17.6 SOL, and far less on deeper pools), and V0 was constant on 200 of 200 sampled pools.
  - Stored V is one snapshot, so it differs from the V at trade time by at most those counters, subject to P1's constancy check.
- **Effective quote.** Before print i: `E_i = quote_reserve_i + Vp(p_m)`. After print i: `E⁺_i` = the post-trade vault from `tools.paper_price_path.pumpswap_post_trade_reserves`, plus `Vp`.
  - **Fee fallback.** v2 rows drop the fee fields, so the helper always takes its `fee_ppm` branch, with `fee_ppm = paper_curve_math.venue_fee_ppm("pumpswap", mcap)`. `mcap` is that print's market cap on vault + Vp, as the V adapter's `mcap_mode="v"` computes it. The error is about one trade's fee, around 1% of that trade's quote, which is small next to a 36.75% threshold.
  - **When the helper returns None.** For a sell whose pool delta is at least the vault, `E⁺_i` = `Vp` (the vault is drained; this can only make A or B fire). In every other None case (zero or malformed amounts), `E⁺_i` = `E_i`, and the print is treated as a no-op. Both cases are counted and reported.
- **Hold window `H_m`.** This is defined only for a FILLED frozen-cell trade. It runs from the entry-state print the frozen simulator fills against at the deciding cell (k = 6, §6) to the print its `tp50_sl30` exit fills against, with exit lag 2, inclusive. **Both endpoints are canonical-pool prints** (§11, P2). A MISS has no hold window and no label.
- **Censored cells (EXP-015's rule, kept).** A cell is censored when the tape ends before its exit deadline. That is a block-edge effect, not an outcome.
  - Censored cells are **dropped from both books** and get no label. Their count is taken before `started`, outcome-blind (status counts only, no nets).
  - Only that block-edge case is censored (as in `tools/latency_curve.py`: `deadline > tape_through_ms`). A **time-cap exit** is priced, as the code does, at the last print at or before the deadline and stays in both books. Silent-pool cells are defined from pool print timestamps alone, with no exit status: the cell's own pool has no print in the last 60 s before its deadline. They are counted **after** the `started` line, printed with the G1/G2 guards before any candidate is scored (a count before `started` would read outcome-derived status, as EXP-015 §11 item 9 forbids), and they enter the total-loss sensitivity of S6 and §8 (g).
- **Drains at the end of the window.** A drain that is itself the dump step is caught through `E⁺` of the print that does it, even when that print is the last in `H_m`: event A uses `E⁺`, not a later print. An **untaped** drain needs a later print (event B). Without one it is invisible (§10).

**RUG(m) = 1 iff event A or event B occurs inside `H_m`:**
- **A, a flow step.** For some slot s that has a print in `H_m`, let `A_before(s)` be E of the first print at s, and `A_after(s)` be E⁺ of the last print in `H_m` with slot ≤ s + 2. A fires if `A_after(s) / A_before(s) ≤ 0.6325` (= √0.40). Under constant product this is a spot-price fall of at least 60% within 3 slots. Three slots is the stop's reaction horizon: the trigger slot plus exit lag 2.
- **B, an untaped drain.** For consecutive prints i, i+1 in `H_m`, B fires if `E_{i+1} / E⁺_i ≤ 0.6325`. The pool lost at least 36.75% of its effective SOL with no taped trade in between. That covers a liquidity withdrawal, an undecoded instruction or a missing row. In every case the reserves are real on-chain state, and the next sell gaps.

**Properties, by construction.** A RUG trade cannot exit at the tp after the step. The step starts below 1.5·P0, or the tp would already have fired, and it ends below 0.40 of its start, so below 0.6·P0. Its stop fires inside the step or right after it, and the exit lands near or below 0.6·P0 unless the price recovers within the 2-slot exit lag. RUG is therefore, up to that lag, a subset of "stop exits with a gap of at least 10 points past the stop". A dump **after** a tp exit is RUG = 0, because it costs the book nothing.

**Reported, never selected on:** (i) **RUG70-1**: the owner's example, one slot, price ratio ≤ 0.30 (E ratio ≤ 0.5477); (ii) #191's label, so the two are compared on the same rows; (iii) A-only and B-only counts, with **B split by source** (P1 A, B, C; P2, P3, P4). A B event on the Oracle tape may be a missed row rather than a drain. (iv) the count of prints from other pools seen inside hold windows. Those prints are reported and never filled (§11, P2).

**Primary label pinned as written** (manager, 2026-10-06): A or B, −60% within 3 slots. RUG70-1 and #191's label are report-only.

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

**Decision time.** The live executor decides when it sees the migration, and k = 6 is landing latency. Every feature uses only events **strictly before the migration slot**. That includes c1 and e2, which EXP-012 cuts by receive time: here they are **recomputed at the slot cutoff**, so that every feature passes the same shuffle test (§11, P4). c1's 3-second window is still measured on `block_time`. Nothing from the 6 landing slots is used. The manager confirmed this cutoff on 2026-10-06. Bonding-curve trades all precede the `complete` event, so curve features are complete at migration.

**Definitions.**
- **Supply** = 1e15 raw units. **Launch slots** = `[create_slot, create_slot + 2]`; N = 2 matches `funding_graph.SNIPER_SLOT_DELTA`.
- **Creator set** = {create row `creator`, create row `trader`}.
- **Held** = curve buys minus curve sells by that wallet up to the cutoff, floored at 0 per wallet. This is a trade-flow balance only: **SPL token transfers are not on the tape** (§10).
- **Block history** = the same block's own hours only, trailing 24 h, every event's slot strictly before this mint's migration slot. Each block is left-censored for about its first 24 h, as `creator_prior_mints_24h` already is.
- **Dump step** (for d2 and d3, on another mint n, on any venue): event A's arithmetic over **n's own prints**, using **n's own `Vp`** on n's canonical pool (same rule as §2.2). On the bonding curve, n's post-trade virtual SOL stands in for E. A step at slot s counts only if **s + 2 < this mint's migration slot**, so the whole window, and every print in it, precedes this mint's cutoff.

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
| c1 | `sniper_buy_share` | EXP-012's definition (share of curve buy SOL within 3 s of create; getBlock time is 1 s resolution), recomputed at the slot cutoff | tape | covariate (not new) |
| c2 | `launch_sol_share` | share of curve buy SOL in the launch slots (slot-based sniper share) | tape | new |
| d1 | `serial_launch_held` | held balance at cutoff of this mint's launch buyers that were launch buyers on ≥ 3 other mints in block history / supply | tape, needs a causal per-block pass | new |
| d2 | `creator_prior_dumps` | creator-set's earlier mints in block history with a dump step | tape, causal pass | new |
| d3 | `prior_dumper_held` | held balance at cutoff of wallets that sold inside a dump step on another mint in block history / supply | tape, causal pass | new |
| d4 | `creator_buyer_recurrence` | this mint's launch buyers that were launch buyers on any of the creator-set's earlier mints in block history | tape, causal pass | new |
| e1 | `top3_share` | #191's | tape | covariate (not new) |
| e2 | `n_buyers` | EXP-012's definition, recomputed at the slot cutoff | tape | covariate (not new) |
| f1 | creator funding source | funder of the creator wallet before the create: exchange / fresh / shared with early buyers | **extra RPC**: `getSignaturesForAddress(creator, before = create signature)` plus up to 5 `getTransaction` (`tools/funding_graph.py` limits) | **phase 2, optional, not in this plan** |

**(e) What existing code computes.** `tools/exploration_entry_model.compute_features` has EXP-012's 18 frozen features. `tools/exp012_rug_risk.rug_features` has #191's 6. Neither records slots for curve events: `_Feat.record` stores receive time only. So a1-a5, b1-b4, c2 and d1-d4 need a new accumulator that keeps `slot`, `signature` and `trader`, and d1-d4 need a time-ordered per-block pass over every mint. The EXP-012 18 are not model inputs here (only c1 and e2), because the veto should not re-learn EXP-012's own selection.

**f1 cost estimate (phase 2).** This is unmeasured; a probe comes first. Each creator needs at most 3 signature pages plus 5 transactions, so at most 8 calls. At about 1 credit per call (the Developer-plan getBlock figure; not confirmed for these two methods):
- creators of the frozen-selected mints only (about 4k over the pools and the confirmation block): about **32k credits**;
- creators of all migrations (about 35k): about **0.3M credits**, about 8 h at 10 rps;
- adding about 5 launch buyers per mint: about **1.5M credits**.

If these methods bill at 10 credits a call, multiply by 10. Causality holds because `before = create signature` returns only earlier history. **Phase 1 is tape-only** (manager, 2026-10-06). Phase 2 is a **separate, later family with its own plan, tries and confirmation block**, not a rescue of a phase-1 FAIL.

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
- **No pool is ever removed. No removal depends on outcomes.** EXP-015 §11 item 11's 0.5% removal rule is **not** used.
  - The V investigation showed that `pool_v_0909.json`'s 321 null pools are V0 = 0 pools carrying small negative pending counters: vault pricing fits them at 0.000 bps. They are not closed accounts. The fixed-parser map reads a stored V ≤ 0 for them, so they are priced vault-only.
  - **Truly closed pools** (no account to read) are priced by a pinned rule: V implied by the pool's own historical swaps (`tools.pumpswap_virtual_history`, from BuyEvents), or vault-only if there is none.
    - **Disclosed:** that estimate uses a later observation of a quantity that is treated as constant. That is acceptable only because V0 is constant (P1's check). It is not a look-ahead on outcomes.
  - **Parse-fail pools** (an account that the fixed parser still cannot decode) get the same rule.
  - **Before `started`**, the number of closed pools and of parse-fail pools among frozen-selected mints is counted and written out, as pool ids only.
  - The kept book is **gated** on a total-loss sensitivity for these mints (§7 S6, §8 (g)).
- **Disclosed frozen-book change.** The fixed-parser map prices the 321 V ≤ 0 pools (vault-only), and any pool whose V EXP-015 read differently, unlike EXP-015's pinned `pool_v_0909.json` (sha `70914a16…5b42e`). So the frozen EXP-012 book here is **not** byte-comparable with EXP-015's frozen side. Both EXP-016 arms use the same map, so the paired comparison is unaffected.

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
- **Veto threshold for Lf:** the (1 − f) quantile of the inner out-of-fold probabilities over the inner dates' **frozen-selected FILLED** migrations (MISSes excluded, because a vetoed MISS gains nothing). Non-interpolating, `index = round((1 − f)·(n − 1))`. The veto is then applied to every frozen-selected migration whose probability is at or above that threshold, MISSes included (they score x = 0).
- No hyperparameter search.

### 5.3 Nested leave-one-day-out (the threshold is never chosen on the confirmation block)

For each outer date d, using only the other dates (35-minute purge around d, as EXP-015 §3):
1. **Inner LODO.** Fit the logistic once per inner date to get inner OOF probabilities. This sets L5's and L10's thresholds.
2. **Score each candidate on the inner dates** by the pooled **pressure** paired mean over frozen-selected migrations (§7, x_m). A candidate is eligible only if it vetoes ≥ 30 filled trades and ≤ 20% of the frozen-selected filled trades on the inner dates.
3. **Pick the best eligible candidate.** It must have an inner paired mean > 0. **"No veto" (frozen) is always a candidate and wins ties.**
4. **Apply it to d.** A rule is applied directly. For the logistic, a model fit on all inner dates is applied with step 1's threshold.

The bars in §7 are computed on the **outer** results of this procedure, not on the best single candidate. Per-candidate tables are report-only.

**Disclosed: the inner pick uses the pressure leg only.** The flat leg never influences which candidate is chosen. It is tested only through the outer bars, which require both legs. A candidate that is best under pressure but weak under flat can therefore be picked and then fail.

**Freeze, if §7 passes:** run steps 1-3 once over all pool dates, with one LODO level. The chosen candidate, and for a logistic the model fit on all dates and its pooled-OOF quantile threshold, go into Part 2.

### 5.4 Tries

- **Cap:** 6 candidates, logged before any fit. One `started` line goes to `data/tries.jsonl` (keys `exp016_r1..r4`, `exp016_l5`, `exp016_l10`) with the row-universe and feature-table sha256. Result lines follow.
- The run refuses if any `exp016_*` line already exists. EXP-015 §11 item 9's try-spend and resume rules apply: nothing outcome-derived is printed before `started`.
- **Each candidate counts on every pool it touches**, so +6 per pool.
- **Prior tries:** P1 has at least 74 (the count in `data/tries.jsonl` at f4daaa0); `explore-0814` has at least 6 in the file, plus DEC-017 (a), which is not in the file; P3 also carries EXP-012's one confirmation read. **The exact counts are read from `data/tries.jsonl` when the `started` line is written**, and recorded in that line and in the report. Nothing here assumes other families' lines (EXP-015 or any other) exist or don't.

## 6. Costs in every deciding cell

These are EXP-015 §4, unchanged, and the costs `tools/exp015_screen.py` already pins:
- V pricing: vault + stored V from the pinned fixed-parser map, with v ≤ 0 meaning vault-only (§2.2), for the label and the simulator alike. Closed and parse-fail pools follow §4's rule;
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
- Censored cells and mints with no migration `pool` are not in either book (§2.2).

**Date sets (pinned).** All pool dates; the non-P1 dates; and each pool on its own (P1, P2, P3, P4).

**The screen passes only if all of S1-S6 hold under both fail models.**

- **S1, paired improvement.**
  - On the 27 non-P1 dates: mean x > 0, with CI90 lower bound > 0 under **both** the `book_stats` resampler and the date-cluster resampler.
  - On all 36 dates: mean x > 0.
- **S2, days.** On the non-P1 dates, more than half of the dates have a positive paired sum. A date with no vetoed filled trade counts as **not** positive.
- **S3, not just removing winners.**
  - The RUG share among vetoed filled trades is at least **2×** the RUG base rate among frozen-selected filled trades (lift ≥ 2), on the non-P1 dates.
  - **Always reported:** the number of vetoed trades that exited at the tp, their total SOL, the vetoed trades' mean and CI90, and RUG precision and recall.
- **S4, concentration.** The non-P1 paired total stays > 0 after removing the **3 vetoed trades with the largest avoided losses**, and again after removing the best date. A veto whose gain is three rugs is not reliable.
- **S5, veto size.** The outer veto removes at most 20% of frozen-selected filled trades on every pinned date set.
- **S6, the kept book is not a loser.** On the non-P1 dates, the kept book's mean SOL per trade is > 0. It must also stay > 0 when every kept trade on a closed or parse-fail pool (§4), or in a silent-pool cell (§2.2), is scored at total loss, −(size + both fees) "Kept trade" here means a **filled** trade; a miss opens no position and keeps its miss cost.

**Report-only, never gating:**
- the kept book's full gate shape (n, days, CI, ex-top-3) on all dates and on the non-P1 dates;
- per-candidate and per-source tables;
- k = 4 and k = 8, lag 0, the raw simulator;
- RUG70-1 and #191's label;
- the S1 paired mean, recomputed with closed and parse-fail pool trades and silent-pool cells (§2.2) scored at total loss;
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
  - a V map covering **every** PumpSwap pool traded in the block, built with the fixed V parser (§11, P1), with its sha pinned. Closed and parse-fail pools are priced by §4's rule and counted, as pool ids.
- **Ownership and priority.** The ledger names an owner only through a merged pre-registration. EXP-016 has priority on this block (manager decision, 2026-10-06, because the owner asked for this study). **The manager will not merge an EXP-014 pre-registration that claims `[2026-08-02T12, 2026-08-08T12)` while EXP-016's Part 1 is pending. EXP-014 waits for the next block.** There is no race. If EXP-016 fails its screen, the block is released by ledger edit, unread, as §7 says.
- **Buffer.** Block history (d1-d4, `creator_prior_mints_24h`) is built from the block's own hours. fresh-0808, which follows it, stays unread, so the first ~24 h is left-censored, as in training.

**Part 1 and Part 2, after the screen passes and before the read.**
- Part 1 names the owner and the exact frozen veto.
- Part 2 records: candidate id, model and feature-list md5, threshold, feature-code commit, the row-universe and feature-table sha256 values, the view pins, and `FROZEN.md5`.
- **The scorer refuses unless all of it matches.** It uses EXP-012's refusal list as the template: code commit match, a clean tree, an O_EXCL read-once lock at `/data/mal/exp016/HOLDOUT_READ.lock` taken before the first row, a view re-hash before the lock, 144/144 hours sealed, and the V0-map sha asserted.
- **Two more checks run before the lock**, outcome-blind, from pool ids and frozen scores only:
  - **V coverage** counts only pools with a readable stored V (a value ≤ 0 is readable and means vault-only). It must be over 99% of the block's traded canonical pools.
  - **The gating-cell no-V check**: every frozen-selected mint's canonical pool has a readable stored V or a §4 rule price, and the count of closed and parse-fail pools is written out.
- A refusal before the lock does not spend the block. Any failure after the lock does.

**The single gating cell.** Frozen EXP-012 selects. The frozen EXP-016 veto removes. The costs are §6. **The kept book** is the frozen-selected entries minus the vetoed ones, and a vetoed MISS stays in the kept book with its fee.

**PASS iff, under the flat 15% model and the pressure model at scale 1:**
- (a) kept book n entered ≥ 100 (MISSes count as entered);
- (b) trades on ≥ 5 distinct UTC dates, with a majority of those dates positive;
- (c) the kept book's mean SOL per trade has a CI90 lower bound > 0 under both resamplers (1,000 draws, seed 1);
- (d) the kept book's total SOL is > 0 after removing its top 3 trades;
- (e) the **paired** improvement mean x > 0, with a CI90 lower bound > 0 under both resamplers;
- (f) the one-sided bootstrap p-values of the kept-book mean > 0 and of the paired mean > 0 are each ≤ **0.0125**, 10,000 draws, seed 1, both resamplers;
- (g) (a)-(d) still hold when every kept trade on a closed or parse-fail pool (§4), or in a silent-pool cell (§2.2), is scored at total loss, −(size + both fees) "Kept trade" here means a **filled** trade; a miss opens no position and keeps its miss cost.

**Multiplicity: lab-wide Bonferroni.** α = 0.05 is split equally across the four confirmation families EXP-013, EXP-014, EXP-015 and EXP-016: **α/4 = 0.0125 per family**. The other three were tightened to 0.0125 by pre-read amendments in #382 (merged). (f) only tightens the CLAUDE.md gate.

**Few dates.** The block has 6-7 UTC migration dates. A date-cluster bootstrap over so few clusters **understates** the variance, so its CI and p-value are optimistic. The token-level `book_stats` resampler is required as well, but it ignores day effects. Neither is exact, and both must pass.

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
- **EXP-016 targets a later walk, not walk 2.** Walk 2's registrations must merge before 2026-10-16T01, and EXP-016 will not be ready by then. DEC-021 covers at most 2 walks (§8), so the later walk is a **third walk**. It needs **a new DEC with its own α**, merged before that walk's first counted hour.
- **Expected effect against DEC-021's δmin.** This is arithmetic, not a measurement. A veto of about 5% of selected trades that avoids about −0.01 SOL on each improves the book by about **+0.0005 SOL per frozen-selected trade** at 0.05 SOL. Over every migration in the window (DEC-021's paired unit, with about 10% of migrations selected) that is about +0.00005. Both are far below DEC-021's δmin of **+0.003 per trade**.
  - At ρ ≈ 0.9, DEC-021's table needs about 838 trades for 80% power at +0.001. Scaling by 1/δ², that is about 3,300 trades at +0.0005, many weeks of forward data.
  - **A forward paired test of this veto is therefore badly underpowered at any realistic window.** The new DEC must state its power at the expected effect, even if that is far below 0.5.
- **Live change follows the new DEC (a third walk), modelled on DEC-021 §6-§7.** A passing challenger switches under that DEC's switch rule, on the manager's decision, with the notebook decision and Console entry that DEC-021 §6(f) requires. Helm does the re-pin. **Size and funding stay owner decisions** (DEC-020): a switch never changes size or wallet. The veto does not change k or the fee either. Computing the features live (curve history and 24 h block state on the fast box) is part of the DEC-021 §7 pre-live checks.
- **No effect on EXP-012's 10-16 read.** EXP-016 opens no hour at or after 2026-10-02T00 and changes nothing frozen.

## 10. What it cannot show, and the honest prior

**Cannot show:**
- **Token transfers.** Creators who split supply to fresh wallets look dispersed. Every "held" figure is a trade-flow lower bound on what a group controls.
- **Funding links.** Without f1, a ring funded from one wallet looks like strangers.
- **Rugs after the hold window, or before the entry.** They are not in the label, by design.
- **Pools without a print after a drain.** A taped drain is caught from that print's `E⁺`. An **untaped** drain with no later print is invisible. A time-cap exit after it is priced at the last (pre-drain) print, which flatters both books and gives the veto no credit for avoiding it; the silent-pool count and the total-loss sensitivity (§2.2) bound this. If the tape itself ends before the deadline, the cell is censored and dropped (§2.2).
- **Pending V counters.** Stored V carries them, by up to 0.002 SOL per pool (§2.2). A long-unclaimed cashback pool could carry more. That was not observed, but it is not ruled out.
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

## 11. Manager decisions and preconditions

**Decisions (manager, 2026-10-06, before the pin):**
1. **Decision cutoff:** strictly before the migration slot (§3). This is conservative and matches when the executor decides.
2. **Label:** A or B, −60% within 3 slots, is primary and is pinned as written (§2.2). RUG70-1 and #191's label are report-only.
3. **Creator funding (f1):** phase 2, a separate later family. Phase 1 is tape-only (§3).
4. **The 0802 block:** EXP-016 has priority. The manager will not merge an EXP-014 pre-registration claiming it while EXP-016's Part 1 is pending, and EXP-014 waits for the next block (§8).
5. **Live:** follows the new DEC (a third walk), modelled on DEC-021 §6-§7. Size and funding stay owner decisions (§9).
6. **Tries:** at least 74 on P1. Exact counts are read from `data/tries.jsonl` when the `started` line is written (§5.4).
7. **Timing:** EXP-016 targets a later walk, not walk 2 (§9).

**Preconditions. No EXP-016 row (label, feature or table) is built before all of these hold:**
- **P1, the V parser fix and the V map.**
  - The fix PR `claude/pumpswap-v-signed-base` decodes V as signed, records the pending counters and `v_base` (V0), and checks constancy on V0. It must merge first.
  - **The merge sha is recorded in a dated post-pin edit of this file before any row is built.**
  - A V map covering every PumpSwap pool traded in every pool view (P1-P4) is then built with the fixed parser, and its sha256 is pinned in the tool by a reviewed commit. The confirmation block gets its own map (§8).
  - The pricing rule is fixed in §2.2: stored V from the pinned map, the value `correct_print` uses, with v ≤ 0 meaning vault-only, for the label and the simulator alike. Stored V differs from V0 by pending counters of ≤ 0.002 SOL (disclosed). Closed and parse-fail pools follow §4.
  - V coverage counts only pools with a readable stored V.
  - **Constancy check (outcome-blind, before `started` and before the confirmation lock).** On a sample of P2 pools and of 0802 pools (the sample size is pinned in the tool), the V implied at trade time by each pool's own swaps (`tools.pumpswap_virtual_history`) is compared with the map's stored V. **Timing on 0802:** the 0802 part runs only after Part 1 merges and names the block, and before the lock. It prints pool ids and differences only. A refusal there does not spend the block.
    - A pool **disagrees** if the difference exceeds **max(1 bp of that print's quote reserve, 0.002 SOL)**.
    - If **more than 1%** of sampled pools disagree, the run **refuses** and reports the pools, by id and difference only.
    - The check reads pool fields and swap amounts, not trade outcomes.
- **P2, pool attribution.**
  - The builder verifies, with a unit test and a count on the real pools, that an EXP-012 fill (entry and exit) is never priced from a pool other than the mint's migration pool. The count is taken before `started` and is outcome-blind: pool ids only. A print from another pool inside a hold window is reported and never filled.
  - **This check is likely to refuse.** `latency_curve` builds a mint's print list without reading `pool`. The expected fix restricts the frozen simulator's prints to the migration pool. That is a **disclosed frozen-book change**: both EXP-016 arms use it, the PR that makes it says so, and the frozen book's numbers here may differ from earlier EXP-012 runs.
  - If the check refuses, nothing is spent. The manager approves the fix before any try is spent.
  - The same pre-`started` pass counts the mints whose migration row has no `pool`. They are excluded from both books (§2.2).
- **P3, the pin.** The manager records this plan's head sha in a PR comment, and `quant-proof` reviews the bars.
- **P4, the tool PR with tests:**
  - **Label:** synthetic prints for:
    - a 3-slot dump (fires) and a slow decline (does not fire);
    - an untaped drain (fires), and a dump step made by the last print in the window (fires);
    - a censored cell (dropped from both books, no label);
    - a dump after the tp exit (does not fire), and a probe-wallet row (ignored);
    - the `post_trade_reserves` None cases (§2.2);
    - stored V > 0, stored V = 0 and stored V < 0 (vault-only) pools.
  - **Adapter path.** The test covers the path `latency_curve` actually prices through: `pumpswap_virtual_adapter.correct_print`, including `v ≤ 0 → vault`. The label and the simulator must price the same print identically.
  - **Features:** causality checks. Shuffled future events, including events in the migration slot itself, must not change any feature, as `tools/test_exploration_entry_model.py` checks today. This covers c1 and e2 at the slot cutoff. The per-block cluster pass must never use another mint's event at or after this mint's migration slot. In particular, a d2/d3 dump step counts only if s + 2 < this mint's migration slot.
- **P5, data.** EXP-015's P4 preconditions are recorded (or the run goes without P4 and says so). The build runs as a MiScusi job, one heavy job at a time.

## 12. Revisions before the pin

All made 2026-10-06, before any pin and before any EXP-016 code exists:
1. Manager decisions applied (§11) (7b3d766).
2. After `quant-proof` returned CHANGES, and after the V investigation:
   - the V rule is pinned to V0 per pool, with pending ignored (§2.2); superseded by item 3;
   - no outcome-linked removal; closed and parse-fail pools are priced by a rule and gated on a total-loss sensitivity (§4, S6, §8 (g));
   - the P1 and P4 adapter path; disclosure of the change to the frozen book;
   - lab-wide Bonferroni at 0.0125 (§8); the inner pick is pressure-only; the L5/L10 quantile is over filled trades;
   - S6 added and S3's redundant bullet dropped;
   - DEC-021: the later walk is a third walk, plus the expected effect against δmin;
   - label edge cases (§2.2): own wallet, endpoints, fee fallback, None cases, B by source, censored rows;
   - feature cutoff details (§3): d2/d3 windows and V0, c1 and e2 at the slot cutoff;
   - S5 date sets, the pre-lock checks, P2's likely refusal, no-pool mints, and the few-dates bootstrap caveat.

3. Quant-proof round 2:
   - **Pricing field:** stored V with v ≤ 0 meaning vault-only, for the label and the simulator; V0 dropped as the pricing field (§2.2).
   - **Censored cells:** dropped from both books (EXP-015's rule, block edge only). Round 3: a time-cap exit is priced at the last print before the deadline, as the code does; silent-pool cells are counted and enter the total-loss sensitivity.
   - **Live wording:** the new DEC for a third walk.
   - **P1:** a constancy check.
   - **No-pool mints:** excluded from both books.
   - **Multiplicity:** #382 cited.

## 13. Post-pin edits (dated; none touches a bar, label threshold or try count)

1. 2026-10-06, before any row is built. **P1 done:** the V parser fix merged as #383 (merge `6300915f45a9`): signed V decode, `parse_virtual_detail` (pending, v_base), base-V constancy at merge, zero-V report counts V ≤ 0. The fixed-parser maps for P1–P4 and 0802 are still to be built and pinned by a reviewed commit before PR 2 reads any row.
2. 2026-10-06. **Pending counters are larger than §2.2 and §10 say.** Forward snapshot #2 (job #259, 52,697 pools): on V0 ≈ 17.58 pools, 18,846 have pending < 1e5 lamports, 1,476 under ~1 bp, 115 up to 0.01 SOL, 9 above 0.01 SOL, max 0.1319 SOL (about 0.75% of V). Read "up to 0.002 SOL" in §2.2 and §10 as "typically under ~1 bp; at most about 0.13 SOL on a handful of canonical pools". Pricing stays on stored V (§2.2); the error is at most about 0.1% of price on those pools. P1's constancy check keeps its pinned tolerance; a pool whose pending moved more than it disagrees, and the 1% refusal rule decides.
3. 2026-10-06. **PR 1 merged** as #392 (merge `66d6ecb`), after 3 quant-proof rounds: the §2.2 label, the §3 features, the P2 pool filter and the pre-`started` counters. Judgment calls recorded in #392's body, all plan-consistent; one pinned here: the d-group trailing 24 h window is counted from each other mint's `create_slot`, not from this mint's decision slot (causal either way; this mint's cutoff still applies to every event).
4. 2026-10-06. **P2 confirmed and its fix disclosed:** `latency_curve` never reads `pool`, so the frozen simulator can fill (and time the migration clock) from a foreign PumpSwap pool. EXP-016 feeds the simulator migration-pool rows only; this changes fills **and** `mig_slot`/entry timing for mints whose first PumpSwap print is foreign (`count_foreign_first_mints`, reported before `started`). Frozen-book change for EXP-016 only; EXP-012's own reads are unchanged.
5. 2026-10-06. **Oracle intra-slot order** follows the simulator (receive time, then first-read order). Cross-slot receive inversions are counted (`count_slot_inversions`) and reported before `started`.
6. 2026-10-06 (manager decision). **P1B pool.** The Oracle live tape has no `migrations/` dir, so for P1B mints the migration pool is `tools.pumpswap_tx.canonical_pool(Pubkey.from_string(mint))`, deterministic from the mint alone, outcome-blind, and the same pool the live probe executor trades (`tools/probe_executor.py`). The P1B migration slot is the first tape print on that pool. A P1B mint whose canonical pool has zero prints on the tape is a no-pool mint (counted, by mint id, before `started`) and is in neither book, like other no-pool mints. Other sources keep the migration-row pool; before `started` the run also reports, counts only, how many migration-row pools equal `canonical_pool(mint)` and how many do not. This touches no bar, label threshold or try count.
   **Disclosure.** P1B's migration slot (the first canonical-pool print) can be later than the real CreatePool slot. Curve features are unaffected (every bonding trade precedes `complete`), but the d-group block-history cutoff can include other mints' events in that gap: a small look-ahead past decision time, not an outcome leak. Before `started` the run reports, for P1B mints, the number of slots between the mint's last bonding-curve print on the tape (at or before its first canonical-pool print) and that first canonical-pool print: p50, p90, max, in slots only, plus the count of mints with no bonding print on the tape.
7. 2026-10-06. **Censoring and the constancy sample.** (a) A cell is censored iff `landing_ms + cap_ms + EXIT_LAG * SLOT_MS > tape_through_ms` (the cap deadline plus the two-slot exit lag is past the block edge), applied to every cell whether or not tp/sl would hit and **including MISS cells**: a MISS near a block edge is censored and dropped from both books too (the earlier `count_censored` helper skipped MISS; the screen does not). (b) The constancy sample is drawn (seeded `sample_pools()`) only from P2 pools with a readable stored V, after the no-create-row and no-migration-slot exclusions; `--emit-constancy-sample OUT.json` writes exactly that set of pool ids (primary; see item 8(c) for the reserve) for the separate constancy job (guards plus the P2 source only; no outcome, no tries line). (c) Refusal messages before `started` name the mint id, the endpoint and a reason only. None of this touches a bar, threshold or try count.
8. 2026-10-06. **Constancy input tool.** The `--v-constancy-json` file is built by `tools/exp016_constancy.py`: for each sampled pool, the earliest successful PumpSwap buy print on the P2 view tape (slot, then signature), up to 5 fallbacks to the next print, implied V from that transaction's BuyEvent (`round(implied_virtual)`), rate limited to at most 5 rps. A pool whose transaction cannot be read is written with `v_implied: null` and a reason; `check_v_constancy` treats such rows as NOT CHECKED (counted, never indexed), and the pinned floor of 200 checked pools is unchanged. Under (c) a null primary is replaced from the reserve; only an irreplaceable null refuses on the floor. Pre-declared before any fetch: (a) a failed RPC read (HTTP error, JSON-RPC error, null result) retries the SAME signature up to 3 further times with bounded backoff, then ends the pool as `rpc_error`; the next print is tried only for decode reasons (no event, pool mismatch, zero base out, failed tx, dust). (b) Dust: a print whose BuyEvent has `base_amount_out` < 1,000,000 base units or net quote (`quote_amount_in_with_lp_fee - lp_fee`) < 5,000,000 lamports (0.005 SOL) is skipped. Disclosed limit: `v_implied` is `round(implied_virtual)` of integer event fields, so rounding error in V grows as the net input shrinks; the dust floor bounds it but does not remove it. (c) `--emit-constancy-sample` writes `{primary, reserve}`: `primary` is the unchanged `sample_pools()` 200; `reserve` is an ordered seeded draw of 20 (seed SEED+1) from the readable P2 pools minus the primary. Each null primary row is replaced by the next reserve pool in order; `check_constancy_sample` accepts exactly primary plus the first k reserve pools, k = the number of null primary rows (all rows present, nulls included). A null reserve row is not replaced; it leaves n_checked < 200 and the floor refuses. Replacements do not cascade. The floor of 200 checked pools and the 1% rule are unchanged. (d) Within a slot, the earliest print is ordered by signature string, not tx_index. This touches no bar, threshold or try count.
9. 2026-10-06, before any row is read (manager rulings; none touches a bar, threshold or try count). **P1B creates, the create-slot rule, `--precount` and the refusal limits.**
   - **P1B creates and rows.** `oracle_live_adapter._hour_info_b` returns no `create` key, so P1B creates are loaded from `oracle_live_adapter.iter_adapted_creates` (its day files and hard cutoff, as EXP-015's P1B path does). The Oracle trade rows have no `block_time` and its PumpSwap rows no `quote_is_wsol`: P1B rows go through `adapt_trade_row` (as EXP-015's P1B path does), otherwise admission would drop every row. P1B migration candidates are the pump.fun-created mints (a create row) that have a PumpSwap print; a PumpSwap token with no create is not a migration.
   - **Create slot (pre-declared).** Adapter creates carry `slot = 0` (a placeholder). A create whose slot is not a positive int gets `slot :=` the slot of the mint's FIRST `pump_bonding` print on the tape (outcome-blind; bonding prints precede migration). A placeholder create with no bonding print on the tape is excluded from features, training and both books and is counted. A create with a real slot (P1A; P1C if its slots are real, which the count `create_slot_placeholders` shows) is kept as is, and the same rule applies to any placeholder found there. **Disclosed:** a mint created before the first tape hour has its first tape bonding print later than its real creation, so its create slot (and launch-window features) is later than the real one; the reported gap (first bonding print `block_time` minus create `block_time`, seconds, p50/p90/max) shows how large that is.
   - **Counts before `started`** (P1B): creates loaded, creates with a bonding print, creates excluded for none, and the gap in seconds.
   - **`--precount`.** A full tape pass on every source as given, printing ONLY outcome-blind counts per source: creates, migrations, cells, no-create-row, no-pool, no-migration-slot, no-bonding exclusions, foreign-first, P1B gap (slots and seconds), censored (the §13 item 7 deadline rule), NO_SIM by reason, V coverage and pool-vs-canonical, plus the item 10(c) and item 11 counts. No lock, no tries line, no label, no P&L, no id lists. It runs with the V map unpinned and says so.
   - **Refusal limits (checked before `started` in the real run; reported as would-refuse in `--precount`).** The run refuses if any source has 0 cells; if a source's no-create-row share of its migrated mints exceeds 2%; if its no-pool share exceeds 2%; or if P1B mints excluded for no bonding print exceed 5% of P1B creates that have a migration.
10. 2026-10-06. **V0 is not constant on LP-active pools (#400's LP law: `V0 <- floor(V0 * S_after / S_before)` on a PumpSwap Deposit or Withdraw).** §2.2's "V0 constant per pool" is false for those pools.
   - (a) The closed-pool fallback (V implied by the pool's own historical swaps, §4) is a value at one trade time and can differ at other times on an LP-active pool. P1's constancy check fails closed on that: more than 1% of sampled pools disagreeing refuses.
   - (b) **What label B does on a liquidity pull.** Only PumpSwap swap rows are priced; Deposit and Withdraw are not prints. A Withdraw shows up only through the next swap's pre-state: B compares `E_{i+1} = quote_reserve_{i+1} + Vp` with `E+_i`, where `Vp` is the one stored V (constant in the label). A proportional Withdraw of a fraction w of the pool shrinks the quote vault by w, leaves the price unchanged, and the label sees `E` fall from `Q + Vp` to `Q(1-w) + Vp`. B fires iff that ratio is at most 0.6325, that is `w >= 0.3675 (Q + Vp) / Q`: at the seed state (Q = 67.41 SOL, Vp = 17.58 SOL) a pull of about 46% of the vault or more. So B **does** fire on a large liquidity removal even though the price is unchanged, and a smaller pull does not (the constant `Vp` makes the threshold a larger share of the vault than the 36.75% of E §2.2 states). A liquidity pull is a rug, so this is the intended meaning and is kept. A Withdraw that is also a V0 change is read through a stale `Vp`; the item 10(c) count says how many selected mints are on LP-active pools. A Deposit raises E and cannot fire B.
   - (c) **Count before `started`** (counts only, per source): frozen-selected mints whose migration pool's `v_base` (from the map's `.detail.json`) lies more than 1,000 lamports outside the canonical creation cluster [17,584,505,200, 17,584,505,699], an LP-active proxy. Mints with no `v_base` are counted separately; a missing detail file is reported as unavailable.
11. 2026-10-06 (manager rulings; no bar, threshold or try count changes). **Ties and the P1 selection clock.**
   - **Ties.** The veto set of the logistic candidates (L5, L10) is the rows whose probability is STRICTLY GREATER than the (1 - f) quantile threshold. Rows tied at the threshold are not vetoed (deterministic and outcome-blind; fewer vetoes). If all probabilities tie, nothing is vetoed, the candidate has 0 vetoed fills and is ineligible by the plan's 30-vetoed-fills eligibility rule.
   - **P1 selection clock.** P1's frozen selection uses EXP-012's stored OOF scores, built on the old migration clock, while the fills here use only the migration pool. The stored frozen scores are kept (no re-score). **Disclosed:** a P1 mint whose first PumpSwap print was on a foreign pool has a different migration clock now. Before `started` the run counts the P1 frozen-selected mints that are foreign-first (old clock differs from new), per source, count only.

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
