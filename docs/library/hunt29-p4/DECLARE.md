# P4 NEW SCREENS pre-declaration (push 10-24, manager15). Written before any count or price.

Kit: tools/arena_screen.py at origin/main 91544819 (worktree /data2/mal/wt/p4-screens), wrapped only to pass send = 160,000 (DEC-031 Am.8) to screen_cfg (run_kit.py). Deciding leg 1.9 s, 0.25 SOL, rent 0, feed delay 7, flat 15% and pressure fail models, strand rule total (deciding) and sc (reported), 1.0 SOL priced beside. Pool age >= 360 s and the V band [17.5e9, 17.7e9] enforced by the kit. Exploration only; screen numbers, not evidence.

## Blocks (re-checked against docs/HOLDOUT_LEDGER.md at origin/main 91544819, 2026-10-24)
The four canonical kit blocks under /data/mal/clean-view, all "exploration pool" rows: explore-0814 [08-14T12, 08-28T12) 336 h ("Exploration expansion"); fast-pool-2026-09-18T23_2026-09-22T00 73 h ("Fast pre-cut", frozen OOS book now closed); oracle-insample-2026-09-22_25 79 h; oracle-live-2026-09-25_27 65 h ("not a confirmation holdout", reservation withdrawn by #148). 553 h, same as hunt23/24/26/28. Not touched: fresh-08* (reserved), forward walks, October tape, anything the kit forbids. No ledger edit (exploration reads).

## Common
- Rows used by a decision have slot <= the decision slot; windows by block_time use cumulative sums up to the decision row inclusive. State carried across hours inside a block only (RunState.scratch, reset per block).
- ok(row) = V in band AND finite post-trade spot AND the idea's depth condition below. Post-trade spot = (q_post + V) / b_post with q_post = q + sol*(1-0.0125) (buy) or q - sol/(1-0.0125) (sell), as in hunt28 l4rules.
- Rate bar (this lane): a cell with < 10 attempts/day averaged over the four blocks is dropped unpriced and said so. (--min-attempts-per-day 10.)
- 4 ideas x 3 variants = 12 cells. Nothing else is tried; no parameter changes after seeing a count. A bug fix (code that does not match this text) is logged as an AMEND with its reason and the counts re-run; thresholds are never moved.
- Nested variants share triggers (a stricter variant is a subset of the looser one), as BREADTH did; the multiplicity is 12 cells regardless.

## I1 FLUSH (cells f2, f3, f5). Depth: post-trade real quote q_post >= 10e9 at the decision print.
Pair state (pool, trader), from all earlier prints in the block, any side, any V: Sb = sum of buy SOL (lamports), Tb = sum of buy tokens (raw). Dropped when untouched for 48 h.
Loss-cut sell: a sell print with Tb_before > 0, sell SOL >= 1e8 lamports, and sell price (sol/tok) <= 0.80 x (Sb_before/Tb_before). Buys before it in print order only.
Trigger on a BUY print j (trader u) of pool P with: pool age (bt_j - P's first print in the block) >= 3600 s; buy SOL >= 1e8; ok; among loss-cut sells of P with block_time in [bt_j - 90, bt_j - 10]: distinct sellers >= N and their summed SOL >= S; no loss-cut sell of P with block_time in (bt_j - 10, bt_j]; u is not one of those sellers.
f2: N=2, S=0.5 SOL. f3: N=3, S=1.0 SOL. f5: N=5, S=2.0 SOL.
Exit: take profit 1.12, stop 0.92, time 900 s. Cell cooldown 300 s; at most one trigger per (cell, pool) per 300 s.

## I2 WAKE (cells w1, w6, w24). Depth: PRE-trade real quote q_j <= 2e9 (shallow universe), no minimum.
Trigger on a BUY print j of pool P with: V in band, finite spot, buy SOL >= 5e7 lamports (0.05 SOL), P has an earlier print in the block and the previous print of P (any side, any hour of the block) is >= G seconds before bt_j.
w1: G = 3600 s. w6: G = 21600 s. w24: G = 86400 s.
Exit: take profit 1.50, no stop, time 21600 s (6 h). Cooldown 21600 s.

## I3 RANKUP (cells k10, k25, k50). Depth: post-trade real quote q_post >= 30e9 at the decision print.
Per hour (start T), minute bins m = (bt - T)//60 of total traded SOL (buys + sells) per in-universe pool (hour.in_universe: V of the pool's last row in band). R(P, m) = sum of bins m-9..m. Decisions only for m = 10..58, so the 10-minute window never reaches into the previous hour (conservative). Rank pools by R descending (ties by pool string). Boundary B_m = T + 60(m+1).
Event for K: rank(P, m) <= K and rank(P, m-1) > K (or P absent), and P not in the top K at any minute-boundary of the previous 120 min (last-top time kept across hours), and window buy SOL >= 3 SOL, window net flow (buy SOL - sell SOL) > 0, and spot move from the window's first pre-trade spot to the entry print's pre-trade spot <= +30%.
Decision at the first BUY print of P with B_m <= bt < B_m + 60 that is ok (nothing is used past that print and the volume used ended before B_m).
k10: K=10. k25: K=25. k50: K=50.
Exit: stop 0.90, trail 0.88 on the running peak, time 1800 s. Cooldown 1800 s.

## I4 DROUGHT (cells d30, d60, d120). Depth: post-trade real quote q_post >= 10e9 at the decision print.
Per pool, carried across hours: bt of its last SELL print (any V), and buys count and buy SOL since that sell, counting prints strictly before the decision; if the pool has no sell yet in the block, the clock starts at its first print in the block and counts run from there.
Trigger on a BUY print j of pool P, ok, with (bt_j - clock start) >= Dmin, buys since >= n, buy SOL since >= S.
d30: Dmin 1800 s, n 10, S 3 SOL. d60: 3600 s, 20, 6 SOL. d120: 7200 s, 40, 12 SOL.
Exit: stop 0.90, time 1800 s (no take profit, no trail). Cooldown 3600 s.

## Labels (applied after pricing, not tuned; same as hunt28 DECLARE)
SCREEN-PASS-CANDIDATE: all four sub-cells {flat, pressure} x {total, sc} have mean > 0, ex-top-3 > 0, ex-best-day > 0, trade-level and date CI90 lower bound > 0, n >= 100, >= 5 dates with a majority positive, kit KILL absent (incl. worst-in-slot), explore-0814 alone and the September blocks pooled both positive. MARGINAL: positive mean and ex-top-3 on the deciding sub-cells but one of n / CI / days / blocks short. Otherwise FAIL. Thin = < 2 attempts/day (not expected). Any cell positive under both fail models gets the kit's causal_probe (>= 200 sampled triggers per block, seed 1) and the result is reported.
Best stake is read between 0.25 and 1.0 SOL as priced; 0.5 SOL impact is the linear interpolation (labelled).

## Not run, with reasons
DIAMOND hands (repeat of hunt25 A-HC-h60, re-priced -8.3% at 160k); Mayhem-graduate universe (kit V band has 0 such pools); creator-fee / unlock flows (no tape row); hold-time stratification (descriptive). See IDEAS.md.
