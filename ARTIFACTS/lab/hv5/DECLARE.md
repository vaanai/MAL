# HV5 DECLARE: V5-A (delta-veto) and V5-B (exit manager), cohort counts first

Written 2026-10-21T17:45Z (host clock). Desk only. No API call, no price after a decision, no outcome has been read to write this file. In-sample exploration only. No book has cleared the promotion gate. Nothing here says a book makes money.

Source plan: /data/mal/hunt-1008/push-1016/hunt24/hv5/PLAN.md sections 2 and 3. Rules: hunt23/RULES.md including Amendment 1 and the wave-2 amendment. Lineage: see section 6 (NOT "none").

## 1. What is being declared

V5-A: a small model (Haiku 5.5, k=3 votes, VETO needs 2 of 3, actions KEEP / HALF / VETO) sees the HV4 card plus delta features and may only veto or halve an entry that a code rule book already made. V5-B: the same model manages an OPEN position (HOLD / TIGHTEN / EXIT; stops only tighten; code hard stop and max hold stay). Both run against a code twin with the same entries and no model, plus a numeric twin, a placebo and a random veto at the same take rate (200 seeds).

This DECLARE covers the first build step only: the counts-only cohort (tools/hv5_cohort_counts.py). Pricing and model calls are PHASE 2 and need their own go from the manager after COUNTS.md. The cells below are fixed now so the counts cannot pick them.

## 2. Base entry sources (which entries the model may veto or manage)

### BASE-1: H5 BOOSTFLOOR entries (preferred base; non-negative exploration evidence)
- Entry: the frozen rule /data/mal/hunt-1008/h5-flows/RULE.md (sha256 c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c): canonical PumpSwap pool with V in [17.5e9, 17.7e9], first non-BOOST sell with t in [0, 300] s of the pool's first print, post-trade Q = real quote + V <= 40 SOL, BOOST budget not yet spent. One trade per pool. Code-twin exit: sell at 330 s after the first print.
- Measured exploration mean, with source (/data/mal/hunt-1008/h5-flows/REPORT.md, "Frozen rule, discovery", explore-0814, 2026-08-14T12..08-28T11, h5-flows cost model, NOT the arena stack): binding 1.9 s leg, stake 0.25: n=1,702, 15 days, mean +11.604% flat and +10.609% pressure; total +49.37 / +45.14 SOL; ex-top-3 +38.61 / +36.39 SOL; ex-best-day +39.58 / +35.90 SOL; positive days 13/15 (flat) and 15/15 (pressure). Median per trade -9.75% (right-skewed). The September confirmation (run once, no retune) was +13.2% primary 0.1 flat, n=1,124, 21 days.
- Honest caveats, all of which stay in every report:
  1. In-sample: Q*=40 and the exit were chosen on explore-0814 itself.
  2. Those numbers are the h5-flows cost model, not the arena stack (rent, strand rule, feed delay 7 slots, tools/arena_stack). The arena-stack re-price is not part of this step.
  3. The only forward evidence is the live canary (13 attempts), and EXP-024 ended NOT_DECIDABLE (library lib-c-K1). H5 has not cleared the gate.
  4. The payoff is right-skewed (median negative, mean positive). A veto that removes a few big winners destroys the mean: the kill rules below use ex-top-3 and ex-best-day on the VARIANT arm, not only the delta.
  5. **360 s conflict.** Amendment 1 item 3 says every rule must require pool age >= 360 s at decision. BASE-1 enters before 300 s by construction. On the August exploration block this is not an October Look-2 outcome read (EXP-024 Look-2 seals October pools only until 11-14), but it does break the letter of item 3. I do NOT resolve it. Open question for the manager (cell C1 and C2 below are priced only if the manager rules that item 3 does not apply to an August-only read of a rule that is already frozen). Forward use on October pools is sealed until 2026-11-14 in any case.

### BASE-2: HV4 CON PULLBACK entries (contrast base; strongly negative evidence, stated plainly)
- Entry: HV4 setup PULLBACK (persona CON) as implemented in tools/hv4_cards.py (age 2 h to 7 d, so the 360 s rule holds), scanned every 5 min, 30 min cooldown per pool, with the Lane G filters and the WSOL / V-band universe. Code-twin exit: 4 h hold, take-profit +50% (the exit of the nearest priced neighbour, hunt23 a-slow cell A1).
- Measured exploration mean of the nearest priced neighbour (hunt23/a-slow/REPORT.md, cell A1 "age>=2h H4 TP50", explore-0814, arena stack, 1.9 s, 0.25 SOL, rent 0, flat, total): -13.10% per trip (pooled with September -12.29%). A1 is not the PULLBACK entry itself: no priced mean of PULLBACK exists, and none is measured in this step. DESIGN says the family is dead as a rule (library lib-a-A9, lib-b-C4).
- **A veto cannot rescue a strongly negative base.** If the PULLBACK base mean is near A1's, a veto of at most 50% of entries cannot make the kept set positive after 1.8% costs. BASE-2 is declared as the contrast to BASE-1 (is any gain in the veto, or is it only the base?), and it is the only base with enough entries if BASE-1 fails the count bar. It is not a profit candidate on its own.
- BIG BREAKOUT and SECOND_WAVE are counted (they are the V5-C base) but get no cell here.

## 3. Cells (4, at most)

| id | variant | base | rule group | event triggered |
|---|---|---|---|---|
| C1 | V5-A delta-veto | BASE-1 | V5-A | False |
| C2 | V5-B exit manager | BASE-1 | V5-B | True (code-detected events) |
| C3 | V5-A delta-veto | BASE-2 (PULLBACK) | V5-A | False |
| C4 | V5-B exit manager | BASE-2 (PULLBACK) | V5-B | True |

Count bar (decided by COUNTS.md, before any price): a cell whose base has fewer than 20 attempts/day on explore-0814 (mean over the 15 days) is dropped without pricing; so is a cell whose base has fewer than 100 attempts in its worst 7-consecutive-day window (the bar for 100 trades by day 7). Attempts here are entries the code rule book makes, before any model veto. If the model vetoes at most 50%, 100 attempts leave at least 50 kept trades: the forward read needs its own power statement, not this one.

## 4. Pick statistic and survivors (fixed before any price)

- At most ONE survivor per rule group (V5-A, V5-B) and at most TWO overall.
- Survivor = passes every kill rule in section 5 AND beats the numeric twin, the placebo p95 and the random-veto p95 (200 seeds) on ex-top-3 in all four deciding sub-cells.
- Pick statistic inside a group: the larger value of MIN over the four deciding sub-cells of the paired delta (variant arm minus code twin), ex-top-3 mean SOL per entry (the same entries in both arms). Tie: lower API spend. Not the best single sub-cell, not the screen_p of the variant arm.
- Worst-in-slot mean > 0 for the event cells (C2, C4); if it cannot be computed, a survivor is marked "worst-in-slot not measured: PROVISIONAL".

## 5. Kill criteria (hunt23/RULES.md Amendment 1)

Priced with tools/arena_screen.py at the pinned kit sha (section 7), stake 0.25, decision latency 1.9 s (1.3 s reported), feed delay 7 slots, rent 0 deciding and rent-charged reported, WSOL and V in [17.5e9, 17.7e9] enforced at decision.

1. A cell is KILLED if, for the VARIANT arm, any of mean, ex-top-3 or ex-best-day is <= 0 in any of the four deciding sub-cells {flat 15%, pressure slope 1} x {strand rule sc, strand rule total} (arena_score.py:33-35).
2. KILLED if the paired delta (variant minus code twin) mean is <= 0 in any of the four sub-cells.
3. V5-A specific: KILLED if the veto-set mean is not below the kept-set mean, or the cell is no better than the numeric twin and the placebo p95 on ex-top-3 in all four sub-cells. Forward-style validity: KILLED if the invalid-output rate exceeds 10%, or the veto rate is above 50% or below 5%.
4. V5-B specific: KILLED if the gain sits only on trips whose stop distance is below the latency-equivalent move (1.9 s plus model latency), if a numeric trailing-stop twin matches it, or if the delta against the code exit is not positive on both legs.
5. Survivor must also be: mean > 0 under both fail models on explore-0814 ALONE and again on the three September blocks POOLED (one period); the gate itself in-sample pooled over all four blocks on all four sub-cells (CI90 lower bound > 0 with 1,000 draws seed 1 5th percentile, a majority of UTC dates positive, ex-top-3 > 0, ex-best-day > 0).
6. Wallet and creator history is built only from rows of the same block that come BEFORE the decision (Amendment 1 item 3). No r4b, l3 or LAYA model or feature, no 02_build feature (DEC-031 3.1). A "recent buyers round-trip within 2 min" share counts a round trip only if the sell is before the decision.
7. All of it is in-sample exploration. A survivor is a candidate for a forward arena book, never a profit claim, and cannot claim a holdout bank (lineage).

## 6. Lineage and seals

- BASE-1 descends from lib-c-K1 (H5 BOOSTFLOOR v1) and lib-c-M1 (parked exit). BASE-2 descends from the HV4 setups (lib-a-A9, lib-b-C4, hunt23 a-slow). Lineage is NOT "none"; no cell here can claim the fresh-0828 bank (DEC-014 rule 2). Wave-3 reasoning applies: this tests a new counterparty (a model veto), not a new price rule.
- C1-NF check: BASE-1 and BASE-2 are not the C1-NF decision grid and exit (EXP-025 section 4, DEC-031 3.7). To be verified by the reviewer.
- Blocks: explore-0814 for the counts and the first screen; the three September exploration blocks for item 5(b) only. No October tape, no fresh-08, no forward-*, truth-*, exp011-*, tip tape, no arena ledger.
- D2/D3 hours [10-02T10, 10-16T01) and the EXP-024 Look-2 seal are not touched (no October data at all).

## 7. Pins

- tools/arena_screen.py at main 9ed4c5f: sha256 e6f1e50abba284b25532a5993ca4eed033836b3976f82d94d9e497dc05bb6fa4. Verify before use.
- tools/hv4_cards.py at 9ed4c5f: sha256 fb33f750d2ba270711aa37b86348505d93946b974f071b765ff317e36f64ce52 (thresholds and setup predicates are imported, not copied).
- tools/hv4_memreplay.py at 9ed4c5f: sha256 57ad389cdcf7cd2589d94811c4ecc51ce54ab72921b459805d8f43bf248c3903.
- RULE.md of BASE-1: c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c.
- tools/hv5_cohort_counts.py: its sha256 is written into COUNTS.md (the counts tool is not a pricing tool and reads no price after a decision).

## 8. Model and API cost caps (PHASE 2 only; this step spends $0)

- Model: claude-haiku-5-5, thinking off, $0.10 in / $0.01 cache / $0.50 out per MTok. Token counts are assumptions until the HV4 shakedown measures them.
- Offline hard caps, abort on first breach, per-variant ledger with replay cache: V5-A $8 (C1 and C3 together), V5-B $6 (C2 and C4 together). Offline total for this DECLARE $14 of the plan's $40 envelope. Stratified sample of at most 6,000 cards. Smoke first: 200 cards with a perturbation-invariance test and a planted future print.
- Forward (not before a survivor, a QP pass and the owner's budget confirmation): at most $1.50/day for HV5, own ledger root /data/mal/arena/haiku-v5. Expected V5-A load $0.09/day, V5-B $0.03-0.10/day (plan estimates, not measurements).
- No Helius credits are used in this step. No API call is made in this step.

## 9. Counts-only cohort tool (what step 2 does)

tools/hv5_cohort_counts.py on explore-0814: for each base entry source, attempts/day, days, distinct pools; plus the share of entries where each delta feature is available as-of strict (holder deltas over 4-12 min, creator history from same-block prior rows, buyer cohort, BOOST state). It reads no price after a decision and calls no API. Where a feature needs per-trader detail that bars do not carry, the tool states the proxy it used and which way the proxy errs.
