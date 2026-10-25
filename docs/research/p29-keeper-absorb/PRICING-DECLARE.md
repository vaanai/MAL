# P29 PRICING-DECLARE: keeper stop-loss absorb. FROZEN BEFORE ANY PRICE PATH IS READ. NOT PRICED.

Declared 2026-10-25 (lane P29, branch claude/p29-keeper-absorb, follows RESULTS.md: revive trigger met on counts). Exploration, paper only, 0 Helius. Awaits a quant-proof ruling before any run. Nothing here is an edge claim; no book has cleared the promotion gate.

Not read before this file: any price, exit, return or P&L of any tagged or untagged cluster; no `price_sol`, `quote_reserve` path after a cluster has been looked at for this rule. Counts in RESULTS.md used `quote_reserve` of the last tagged sell only for the 2% bar.

## 1. Event (frozen)
- Tag: RESULTS.md rule (keeper fill signature from the keeper's public history). Kind SL only; TP and `mixed` slots excluded (TP mirror is a separate family).
- Event: >= 3 DISTINCT tagged SL fill signatures with sell rows in one mint (PumpSwap or pump_bonding) within 60 s (block_time); event closes at the 3rd distinct signature; then a 60 s refractory per mint. The "last tagged sell" is the row of that 3rd distinct signature (later tagged SL rows of the mint before the entry print are ignored for timing; the entry is not delayed by them).
- Pre-drop price P_pre = `price_sol` of the last print of the mint at or before (first tagged sell of the event minus 60 s). P_last = `price_sol` at the last tagged sell. Drop d = (P_pre - P_last) / P_pre. **Skip the event if d < 3%** (frozen).
- Skip unless the 2% impact bar at 0.25 SOL passes on the reserves at the entry print (PRE-trade reserves for PumpSwap with V per pool added, POST-trade for bonding; V from the pool V map, a pool with no V is skipped and counted, never V = 0).

## 2. Timing (corrected per coordinator)
### 2.1 Why not the earlier "L = 2 slots"
MAL's measured entry latency is 1.9 s and the lab prices deciding cells at realistic cost. A keeper fill is visible to MAL only after the fact (same-slot Jito bundle), so the entry cannot be 2 slots after the sell.
### 2.2 Decision instant
- D = t(last tagged sell print) + T_tag. **T_tag = 1.0 s (declared).** Basis, from tools/arena_feed.py: the live feed is public logsSubscribe of the two pump programs, decoded from logs only; it carries the signature but NOT the fee payer or signer, and its current subscriptions do not include the keeper. Live tagging therefore needs one extra logsSubscribe with `mentions: [keeper]` (the keeper is fee payer of both txs, so both appear), delivered on the same confirmed-commitment path as the pump logs, no getTransaction; the create tx's memo line in its logs gives SL/TP. The tag arrives in the same notification wave as the sell; T_tag covers pairing the keeper-mention notification with the pump log row and the memo (the two txs are separate notifications) and the 1 s block_time quantisation. This is an estimate, not a measurement: the keeper subscription does not exist in the feed and no tag latency has been measured. Stress leg uses T_tag = 3.0 s.
- Feed delay of the tape (confirmed commitment lag) is separate and already part of the 1.9 s landing figure MAL uses; not added twice. QP to rule whether this is right.
### 2.3 Legs
- **PRIMARY (deciding): entry at the first print of the mint whose time is >= D + 1.9 s** (time from the slot clock: each day's seconds per slot = the median of 1-s block_time differences over that day's slots, computed from the tape, fixed by rule; entry slot = last tagged slot + ceil((1.0 + 1.9) / s_day)). Exit legs use the same 1.9 s lag after the exit trigger is seen.
- **STRESS (deciding for kill only, not for pass): entry at the first print >= D + 12 slots**, with T_tag = 3.0 s on the exit side. Read as a robustness report; a PASS requires the primary; a stress-leg sign flip is reported.
- **Report-only best case:** entry 2 slots after the last tagged sell. Never used to decide.

## 3. Trade (frozen)
- Entry: buy 0.25 SOL at the entry print, priced by the repository's realistic-cost paper pricing at the 1.9 s leg (curve math, PumpSwap virtual-reserve adapter, fee tiers, creator fee, tx fee; the same pricing module the H5 arena read uses, tools/arena_h5_cost.py / tools/paper_curve_math.py, pinned at QP time by commit; its outputs were not read).
- Exit, first of: (a) reversion: price >= P_last + 0.5 x (P_pre - P_last); (b) time cap 120 s after the entry print; (c) stop: price <= 0.90 x entry fill price. Exit fill at the first print >= trigger time + 1.9 s, priced with full sell costs and impact.
- No re-entry on the same mint inside the refractory. One position per mint. No parameter other than those written here.

## 4. Days (fixed before any return)
Ledger check (docs/HOLDOUT_LEDGER.md): September exploration-pool blocks, all with data under /data/mal/blocks-clean/. The app and the keeper exist from about 2026-09-02, so explore-0814 has none.

| Block | Hours | Ledger owner | Use here |
| --- | --- | --- | --- |
| fresh-0903 | [09-03T12, 09-09T12) | exploration pool (spent by EXP-012) | **In-sample, report only:** full days 09-04..09-08 (5). Counts already taken here. No retune is planned, so this is a sanity run; its numbers cannot rescue a failed OOS. |
| exp011-0909 | [09-09T12, 09-15T12) | exploration pool since 2026-10-06 (EXP-011 closed NOT_DECIDABLE, never scored) | **OOS look:** full days 09-10..09-14 (5) |
| fast-pool-0918 | [09-18T23, 09-22T00) | exploration pool (pre-cut + EXP-009 exclusion hours) | **OOS look:** full days 09-19, 09-20, 09-21 (3) |
| oracle-insample-0922 | [09-22T00, 09-25T06) | exploration pool ("seen" for the migrate-direct 972-cell grid) | **OOS look:** full days 09-22, 09-23, 09-24 (3) |
| oracle-live-0925 | [09-25T07, 09-28T00) | exploration pool, not a holdout; contains the void window 09-25T19 to 09-27T06:58 of forward-paper rows | **Excluded** (void-window adjacency, partial days). Reserve for a later confirmation. |
| Excluded | Fast EXP-009 block [09-15T12, 09-19T01) (owned by EXP-009); fresh-0828 (reserved: bank-cohort confirmation, EXP-028); everything >= 2026-10-04 (forward walks, kill review, HV4/Q50/DBC seals, arena, meta-capture) | | Not read. |

- **Single OOS look:** the 11 full days 09-10..09-14, 09-19..09-21, 09-22..09-24 are read once, together, after this file is merged and QP-ruled. No interim peeks, no per-block reads before the pooled one. Expected trades: >= 100 (counts: about 100-350 passing clusters a day on fresh-0903 alone; QP to confirm that the d >= 3% and distinct-signature filters leave >= 100 over 11 days).
- Caveat stated now: these are exploration-pool days, not sealed holdouts. They are out-of-sample only for THIS rule (never priced here; the 09-04..09-08 counts used no returns). A PASS is "exploration-OOS PASS", not a promotion: C0/live also needs the forward confirmation on the reserved Forward walk 2 block and DEC-032 preconditions. No D2/D3 domain seal covers September hours (those cover October graduation pools); checked against the ledger text, to be confirmed by QP.
- Multiplicity: one primary cell (k = 1: primary leg, this event, this exit), so the single bootstrap applies (1,000 draws, seed 1). The stress and best-case legs and the placebo are not in k.

## 5. Kill set (all frozen), under BOTH flat 15% fail and pressure-fail slope scale 1
The book is KILLED (not promoted) if, on the OOS look at the primary leg, under either fail model, ANY of:
1. mean SOL per trade <= 0;
2. CI90 lower bound of mean SOL per trade <= 0 (1,000 bootstrap draws, seed 1, 5th percentile);
3. total SOL <= 0 after removing the top 3 trades;
4. total SOL <= 0 after removing the best UTC day;
5. fewer than half of the OOS UTC days positive (needs >= 5 days; here 11);
6. fewer than 100 OOS trades;
7. median gross edge per trade (before fees and tx cost, from fill to exit fill) <= 1% at the primary leg (the earlier "median-gross-edge kill").
PASS = none of 1-7 fires under both fail models AND the placebo test (section 6) passes. Winner's-curse note: there is no grid, so no selection correction is needed beyond k = 1; the stress leg flipping sign is reported and weakens the reading but is not a kill.

## 6. Placebo (frozen)
Question: does the keeper tag carry information beyond "a sell burst"?
- Control events: for each tagged event, the matched UNTAGGED burst = >= 3 sell rows in one mint within 60 s, none of whose signatures is in the fill set, distinct signatures, d >= 3%, same 2% bar, that (i) occur in the SAME pool/mint at a different time (>= 10 min from any tagged event of that mint), else (ii) the same venue and the same UTC day with the nearest quote-reserve decile, nearest total sold SOL in the burst (within +-25%), nearest drop d (within +-2 pts). One control per tagged event, no reuse; unmatched tagged events are dropped from the paired test and counted.
- Control events run through the identical entry, exit, cost and fail models.
- Test: paired difference (tagged minus control) of net SOL per trade, bootstrap 1,000 draws seed 1, under both fail models. The tag carries information only if the paired mean difference > 0 AND its CI90 lower bound > 0. If the untagged control is itself positive with a similar mean, the finding is "a sell-burst effect", not a keeper effect, and the book is reported as such (the idea family dump-absorb-1..6 / hunt29-flush-1 already failed on untagged cascades).

## 7. Capacity line (arithmetic, not a result)
Trades per day N <= about 225 (median full-day SL60 clusters passing the 2% bar, before the distinct-signature and d >= 3% filters; realistic N is lower, plan on 100). Daily SOL = N x 0.25 SOL x net edge per trade e.
| e (net, after all costs) | N = 100/day | N = 225/day |
| --- | --- | --- |
| 0.5% | 0.125 SOL | 0.28 SOL |
| 1% | 0.25 SOL | 0.56 SOL |
| 2% | 0.50 SOL | 1.1 SOL |
Against the owner's cash-flow ask ($25-40/day, about 0.17-0.27 SOL at $150) the book needs about e >= 1% on >= 100 trades/day at 0.25 SOL. A 0.25 SOL buy costs roughly 0.6-1.7% of impact on bonding reserves before exit costs, so a net 1% needs a gross reversion well above that; the median-gross-edge kill (7) is set at 1% for that reason. Impact also halves edges at larger stake (capacity-first rule): a size ladder is not part of this declaration.

## 8. Process
1. QP ruling on this file (timing T_tag, the control construction, the day split, the cost module pin).
2. Merge this DECLARE; build the runner as a MiScusi job (sh, mem <= 6 GB, <= 2 CPU, notebook entry first, 0 Helius, one heavy job at a time), with a counts-only dry pass on the real layout first (events, matched controls, skips, refusals), no prices.
3. One OOS look, then in-sample report. Seals as in hunt30 SEALS-BRIEF: no HV4/Q50/DBC forward/arena/meta-capture contact; no hour >= 2026-10-25T00Z.
4. Result is written to a lab note with every kill line; no sentence that a book made money before quant-proof on the exact head.
