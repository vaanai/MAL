# P96 pre-registration: an "avoid bad pools" veto fit on Q50's forward trades and tested on the successor s1's trades

Written 2026-10-27 from 20:31Z (`date -u` on mal-research-0), lane P96, branch `claude/p96-q50-veto-prereg`. Direction 2 of `docs/research/p94/SYNTHESIS.md` section 4. Paper only. **No book has cleared the promotion gate. Nothing here says any book makes money.** Nothing here changes Q50's read, s1's read, or any registered book.

**Written before any input exists or was opened.** To write this file and the tool the author read code and text only: `tools/h5_shadow.py` (at origin/main 7ebddb71; its blob equals the Q50 pin a5f41370's, and S\* 5c2ab7c6 differs only in the `SUCC_END_MS` seal constant, checked with `git diff`), `observe/trade_decode.py`, `tools/arena_h5_read.py`, `tools/arena_h5_legs.py`, `tools/arena_h5_score.py`, `tools/arena_h5_score_q50.py`, `tools/arena_h5_q50_instant.py`, `tools/arena_h5_books.py`, `tools/arena_score.py`, EXP-024 (Amendment 10 and the s1 note, blob 7015abc9), DEC-031 (Amendments 11, 17 and 18 as cited in docs/HANDOFF.md and `docs/research/q50-successor/S1-1106-PLAN.md`), and the P77, P80 and P89 lane files. Not opened: any Q50 or s1 shadow output (#1419/#1421 included), ledger, score, count, refit or read-instant file, decision row, `bots.json`, executor output, any HV4 output, the DBC forward window or fwdv1/, the LaunchLab holdout, /data/mal/meta-capture/, fresh-0802, fresh-0828, any arena tape row, any HV replay output, and no September exploration file (no schema check was needed: every feature is a field the shadow code writes, read from the code). No value of any tape was seen. 0 Helius credits, 0 API calls.

## 1. Why this sample

P80 (rug veto) and P89 (kNN veto) found no independent sample for an H5 filter: on the exploration tape the H5 trade set is exactly the set of pools that meet the H5 trigger (P80: 0 of 9,470 other pools trigger), and P86's kNN features cannot be computed at H5's timing (P89: 0 of 2,826). The only clean domain is H5's own forward trades: Q50 (`h5v1q50-boostfloor-a1`, clock 2026-10-25T00Z, end-seal 2026-11-06T00Z) and its successor s1 (`h5v1q50-boostfloor-s1`, clock 2026-11-06T00Z, end E_s1 = min(clock + 14 d, 2026-12-01T00Z)). Both run the same rule (`H5-BOOSTFLOOR-Q50 v1`, block sha256 `b3f8ecbf…`) and the same shadow code apart from one end constant.

## 2. Split (chosen: fit on Q50, test on s1)

- **Fit set:** every counted trade of Q50 decided in [2026-10-25T00:00Z, 2026-11-06T00:00Z) at the deciding stake (0.30 SOL), one row per pool.
- **Test set:** every counted trade of s1 decided in [s1 clock, E_s1), one row per pool.

**Why not first half / second half of Q50.** Q50's read set has at least 100 counted trades, and the whole Q50 window perhaps not many more. A second half of about 50 to 100 trades, minus the vetoed trades, is below the gate's 100 trades, so kill line K3 would fire on the count every time, whatever the veto does: the test would be uninformative by construction. Fitting on Q50 and testing on s1 gives (a) a test set from a later, disjoint calendar window (pools with s0 at or after 11-06; out-of-sample in time for the veto), (b) all of Q50 for the fit, and (c) a test set that can reach 125 trades in 14 days. The cost: the result waits for s1's end and reads (earliest run about 2026-11-20T01Z), and the lane ends NOT_DECIDABLE if s1 never registers, never reaches its read, or ends with too few trades.

## 3. Features (6, all decision-time, all from Q50's own shadow records; frozen)

Every feature is computed from the **pv trigger record** of the pool (the record the scorer prices; `tools/arena_h5_score.py` ShadowDir keeps the first pv trigger per pool, dirs in name order) and, for F6 only, the pool's `strip.buys` and `boost_slice` records restricted to slots **strictly before** the trigger slot. No outcome, outcome_bx10, exit ladder, `strip.rows`, `post_last`, pool-close record, `min_q` field or synthetic class is read. Line numbers are `tools/h5_shadow.py` at origin/main 7ebddb71 (blob identical to a5f41370). "Orientation" is what a larger value means, checked in the code. "Prior" is the author's judgment of the direction of risk, report-only: the model learns its own weights and a learned sign opposite to the prior is reported, never decisive.

| # | Name | Exact formula | Source field and code | Orientation (checked in code) | Prior risk direction (judgment) |
|---|---|---|---|---|---|
| F1 | `t_trig_s` | `trigger.t_since_s0_s` | `t = (pr.slot - p.s0) * sps` (l.1905), written as `t_since_s0_s` (l.1996); only t in [0, 300] fires (l.1906) | larger = the first qualifying sell came later after the pool's first print (seconds, pool slot clock) | + weak: later trigger, less BOOST support left before the 330 s exit |
| F2 | `q_post_sol` | `trigger.q_pv_post_sol` | `pr.q_post("pv", p.v0) / 1e9` (l.1999); `q_post` = quote_reserve + per-print V + dq (l.515-519): POST-trade Q incl. V of the trigger sell; <= 50 by the rule | larger = shallower drain, further above the V floor (V about 17.58 SOL) | + : RULE.md discovery "monotone: smaller Q\* better" |
| F3 | `sell_frac` | `trigger.sell_user_out / (trigger.q_pv_pre_sol * 1e9)` | `sell_user_out` = `pr.sol` = `row["sol_lamports"]` (l.1409, l.2002), the sell's `user_quote_amount_out` (`observe/trade_decode.py` l.435); `q_pv_pre_sol` = `pr.q_pre("pv")` = quote_reserve + V PRE-trade (l.515-516, PumpSwap pre-trade convention) | larger = the trigger sell took a larger share of the pool's pre-trade depth | + : a large single dump looks like a rug |
| F4 | `boost_left_sol` | `trigger.boost_remaining_sol` | `(BOOST_BUDGET - spent) / 1e9` (l.2003), `spent` = the BOOST signer's cumulative buys through the trigger print (`boost_spent`, l.1873; PDA or event authority, else the behavioural detector); spent 0 when no signer is known | larger = more BOOST buying still to come (max 17.585 when no BOOST signer was seen) | - : remaining BOOST supports the price |
| F5 | `log_prints` | `ln(1 + trigger.prints_seen)` | `len(p.prints)` at `_fire` (l.2007); the trigger print is appended (l.1525) before `_eval` runs (l.1532), so it counts the trigger print | larger = more prints of the pool seen up to and including the trigger | - weak: a thin, quiet pool is easier to drain |
| F6 | `nb_pre_frac` | `max(0, Σ strip.buys lamports with trigger.slot - 50 <= slot < trigger.slot - Σ boost_slice.lamports of the same pool with slot in the same range) / (trigger.q_pv_pre_sol * 1e9)` | `strip.buys` = `[slot, int(r.sol)]` of every buy from 50 slots before the first trigger print (l.2236-2238; fresh-forward mode only, which Q50 and s1 run); a buy's `sol` is `user_quote_amount_in` (trade_decode l.404); `boost_slice.lamports` = `int(pr.sol)` of vault-signed BOOST buys (l.1949) | larger = more non-BOOST buy SOL arrived in the 50 slots (about 20 s) before the trigger, relative to depth | - : organic dip buying before the trigger supports the price |

Matching and dates: the scored row and its trigger are joined on `pool`, and the trigger's `t_detect_ms` must equal the row's `decision_ms` (the scorer's `decision_ms`, `tools/arena_h5_score.py` l.178), or the tool refuses. The trigger's start record must carry `q_star_sol` 50.0 and rule `H5-BOOSTFLOOR-Q50 v1`. A slot range is in slots, not seconds, so it is exact on the record.

**Unscorable trades.** A trade is unscorable when a feature is missing or not finite (for example no strip record, a strip without `buys`, `buys_truncated` true, or `q_pv_pre_sol` <= 0). In the fit set it is dropped from the fit and from the threshold. In the test set it is never vetoed (it stays in the book). If more than 5% of either set is unscorable, the lane ends NOT_DECIDABLE before any test value is used.

## 4. Model (one fit, no tuning)

- **Label (fit set only):** y = 1 if the trade's `flat_1p9_total` value (flat 15% fail, 1.9 s leg, 0.30 SOL, rent-charged, the read's own figure) is at or below the 25th percentile of the fit set's `flat_1p9_total` values (`np.percentile`, linear). A bottom-quartile label is used because the size of a "big loser" on this book has not been seen by anyone writing this; the quartile gives at least 25 positives at n >= 100.
- **Score:** logistic regression on the 6 features standardised with the fit set's mean and standard deviation (ddof 0), L2 penalty lambda = 1.0 on the 6 weights (intercept unpenalised), fit by Newton/IRLS from zero, at most 50 iterations, converged when the largest step is below 1e-10. numpy only. score = the linear predictor (logit).
- **Threshold:** the 80th percentile of the fit set's scores (`np.percentile`, linear). **Veto = score > threshold.** About 20% of the fit set is vetoed by construction. The same weights, standardisation and threshold are applied once to the test set.
- No feature selection, no lambda search, no class weights, no second threshold, no refit after anything on the test set is seen. One cell.

## 5. Procedure (the tool `tools/p96_veto.py` does exactly this)

1. **Seal checks (REFUSED, exit 3, before any row or record is opened):** `--after-read` set; the wall clock at or after 2026-11-14T00:00Z (EXP-024 section 3 end state, first listed) and at or after E_s1 + 1 h; a sealed-read marker for both books (section 7); for a book whose screen read is PASS, its confirmation read marker too.
2. **Read status.** If either book's read is NOT_DECIDABLE, the lane ends **NOT_DECIDABLE** (no priced read to anchor the rows); no row value is opened.
3. **Parity (REFUSED on a miss):** for each book, the rows decided before its read instant reproduce the read's four sub-cells exactly (n equal; total within 1e-9 SOL), and the rows' header intercept equals the read's refit intercept. Every row is inside its book's window and is one pool, one row.
4. **Counts (NOT_DECIDABLE on a miss; no test value used):** fit set >= 100 scorable trades on >= 5 UTC dates; test set >= 125 trades on >= 5 UTC dates; unscorable <= 5% of each set; the fit converges and no feature is constant on the fit set.
5. **Fit** on the fit set (section 4). Report the weights (standardised units) beside the prior signs, fit AUC, fit veto share.
6. **Veto share on the test set** (a count). Above 50%: **KILL** (K4), no test value is used.
7. **Test.** For each of the four deciding sub-cells (`flat_1p9_total`, `press_1p9_total`, `flat_1p9_sc`, `press_1p9_sc`, each book's values at its own read's refit intercept): the un-vetoed book, the vetoed (kept) book, and the kill lines below.
8. **Write once.** The result goes to `--out` with O_CREAT | O_EXCL; an existing file is never rewritten. The lane runs once; the first completed run is the result, and it is notebooked.

## 6. Kill lines (test set; any one, in any of the four sub-cells, kills)

Δ = kept total - un-vetoed total = minus the sum of the vetoed trades. Placebo: the same number k of test trades removed uniformly at random without replacement, 1,000 draws from `numpy.random.default_rng(1)` (drawn once, applied to all four sub-cells).

- **K1 (placebo).** KILL if Δ - mean(placebo Δ) <= 0.
- **K1b (placebo p).** KILL if p > 0.10, p = (1 + #{placebo kept total >= kept total}) / 1,001. Added beside the brief's K1 because a veto with no skill clears "Δ above the placebo mean" about half the time; P77 and P80 used the same 0.10 line.
- **K2 (drop the best day).** KILL if the kept book's total without its best day (the UTC date with the largest kept daily total; `arena_score.cell_stats` `ex_best_day_sol`) is <= 0, or if Δ without the date that contributes the most to Δ is <= 0.
- **K3 (gate).** KILL if the kept book fails any gate element (`tools.arena_score.cell_verdict`: n >= 100, >= 5 UTC days with a strict majority positive, CI90 lower bound of mean SOL per trade > 0 from 1,000 trade-level bootstrap draws with seed 1 at the 5th percentile, total ex top 3 trades > 0) in any of the four sub-cells, so under both fail models and both strand rules.
- **K4 (P77 lesson).** KILL if the veto removes more than 50% of the test trades. A filter that fires that often is a different book.

Otherwise **PASS**. Report-only, never deciding: test AUC (label = `flat_1p9_total` at or below the fit set's 25th-percentile cut), the un-vetoed test book's gate statistics, Δ ex the 3 trades whose removal helps most, kept trades per day and SOL per day at 0.30 SOL. Impact at other stakes is not repriced.

## 7. The sealed-read marker (from `tools/arena_h5_read.py`)

`tools/arena_h5_read.py` writes its verdict with `--json OUT` (atomic, `h5s._atomic_write_json`) as `{"books": {<bot_id>: {...}}, "refused": {...}, ...}`. For a book read under the books file, `books[bot]` carries `status` (PASS / FAIL / NOT_DECIDABLE), `read_instant_ms`, `refit.intercept`, the four `subcells` with `n` and `total_sol`, and `provenance` with the sha256 of the book's instant file (Q50: `q50-read-instant.json`, DEC-031 Amendment 11; s1: its own instant file, named by the successor's C3 PR). The tool accepts a read JSON for a book only if the book is in `books` and not in `refused`, `status` is one of the three, `final` is not false, and a provenance key ending in `read-instant.json` exists. For PASS or FAIL it also needs `read_instant_ms`, `refit.intercept` and all four sub-cells. A PASS needs that book's confirmation read too (DEC-031 Amendment 11 item 3), passed with `--confirm-read-json`, in the same format, with a `read_instant_ms` after the screen read's, because a screen PASS's later trades are its confirmation sample. The tool records the sha256 of every marker it used.

## 8. Minimum counts (summary)

Fit: >= 100 scorable Q50 trades, >= 5 dates, >= 25 label positives (by construction). Test: >= 125 s1 trades (so a 20% veto still leaves 100), >= 5 dates. Unscorable <= 5% per set. Below any of these: NOT_DECIDABLE, never KILL. A test set of 125 to about 200 trades makes K3 the likely binding line; that is disclosed now, not discovered later.

## 9. What a result licenses

- **PASS** licenses only a **new forward book**: a vetoed successor (the s1 rule plus this frozen veto, weights and threshold copied into its registration), on **new days** after the test window, with its own registration, P0, clock, N + 1, and its own gate read. It is a lead, not a promotion and not a profit claim.
- A PASS never changes Q50's read, s1's read, their verdicts, ledgers, scorers or any confirmation, and is never pooled with them. Q50's and s1's outcomes are not re-read as "vetoed" for any gate purpose.
- **KILL** ends this veto (this feature set, this model, this threshold) for the Q50 rule; a new veto is a new pre-registration with its own c.
- **NOT_DECIDABLE** spends nothing beyond c (below) and licenses nothing.

## 10. Multiplicity and disclosures

- **c + 1.** This test adds 1 to c when it runs, whatever its result (PASS, KILL or NOT_DECIDABLE after the seal checks). A vetoed successor adds 1 to N at its clock.
- The test reads s1's trades a second time: s1's own read used them first. The two are not independent, and a PASS here after an s1 FAIL is a filter chosen on Q50 and checked on s1, not new evidence about s1.
- Q50's and s1's outcomes are observable in real time under EXP-024 Amendment 10 (a) and the s1 note. This file was written before Q50's earliest read boundary (2026-10-30T00Z) and before s1 exists; the author saw no Q50 outcome, count or record.
- Features are decision-time only. F4 depends on which BOOST signer was known at the trigger (`boost_src`); F6 depends on the shadow seeing the buys in the 50 slots before the trigger (a dropped print lowers it).
- Synthetic class: not read and nothing is split by class (EXP-024 Amendment 10 (f)); the books exclude such pools before any outcome exists.

## 11. The exact tool interface

```
python -m tools.p96_veto --after-read \
    --scored-rows ROWS.jsonl \
    --trigger-dir h5v1q50-boostfloor-a1=DIR [--trigger-dir h5v1q50-boostfloor-a1=DIR2 ...] \
    --trigger-dir h5v1q50-boostfloor-s1=DIR [...] \
    --read-json h5v1q50-boostfloor-a1=Q50_READ.json --read-json h5v1q50-boostfloor-s1=S1_READ.json \
    [--confirm-read-json BOT=CONFIRM_READ.json ...] \
    --s1-clock-ms MS --out RESULT.json [--now-ms MS]
```

- `--now-ms` is clamped to the wall clock (it can only tighten the seal, as `arena_h5_read`).
- `--s1-clock-ms` must be a 00:00Z instant at or after 2026-11-06T00:00Z (the slip rule moves it by whole days); E_s1 = min(clock + 14 d, 2026-12-01T00:00Z).
- **ROWS.jsonl** (one JSON object per line), per book: one header line `{"type": "p96_rows_header", "bot_id", "read_instant_ms", "intercept", "stake_sol": "0.30", "leg": "1p9", "rent": "charged"}`, then one line per counted trade at the deciding stake `{"type": "p96_row", "bot_id", "trade_id", "pool", "decision_ms", "v": {"flat_1p9_total", "press_1p9_total", "flat_1p9_sc", "press_1p9_sc"}}`, values in lamports, as `tools/arena_h5_legs.cell_values(items, rule, intercept)` gives them for that book's rows at its read's refit intercept, rows decided after the read instant included. The export that writes this file from the ledger is **owed**, its own small PR before the run; the tool's parity check (step 3) ties it to the sealed read either way.
- Exit 0 with a verdict (PASS, KILL or NOT_DECIDABLE); exit 3 REFUSED (nothing decided, no c spent).
- Tests: `tools/test_p96_veto.py`, synthetic fixtures only (`/data/mal/venv/bin/python3 -m pytest -q tools/test_p96_veto.py`).

## 12. Seals (stated for this lane)

Q50 (all outputs, ledger, shadow dirs #1419/#1421, score, count and read-instant files, decision rows, `bots.json`) and s1 are opened only by the tool, after its seal checks pass. EXP-024 Look-2 seal on H5-family pools until 2026-11-14T00Z: the tool refuses before it. The s1 note (blob 7015abc9) covers s1 pools with s0 at or after 2026-11-06. Not touched by this lane: HV4 outputs; DBC forward window [10-25T00Z, 11-01T00:10Z) and fwdv1/; LaunchLab holdout [10-17, 10-24) (spent); /data/mal/meta-capture/; fresh-0802 (last reserve) and fresh-0828 (EXP-028's); arena tape; HV replay outputs.
