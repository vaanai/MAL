# P6 LATENCY pre-declaration (hunt29, push 10-24). Written before any count, price or smoke run.

Question: is buying speed worth money? Every kit screen so far is priced with a 1.9 s deciding leg. Exploration screen numbers only; in-sample; never gate evidence. No sentence here says a strategy makes money.

## What changes and what is held fixed
- Changed: ONLY the deciding ENTRY leg (arena_screen.LAT_DECIDING and LATS), via a wrapper that sets both to the leg L. Entry lands at decision slot + feed delay D + k(L), k = ceil(L / slot_s). The kit's sell floor (exit decided no earlier than the entry landing + 1 slot) follows the same L, as the kit defines it.
- Held fixed: send 160,000 lamports per send (DEC-031 Am.8), V band and V map, feed delay D = 7 slots (about 1.5 s; NOT varied here, it is a feed property and listed as open), exit lag 0.55 s (3 slots), haircuts, E1 floor, 1.15 guard, impact (fee tiers, pool state at the landing slot), rent 0 deciding (sell closes the ATA, Am.2), strand rule total deciding, both fail models (flat 15%, pressure slope scale 1, intercept refit per cell), stake 0.25 SOL, pool age >= 360 s, cooldowns and exits as each rule defines them.
- Kit: tools/arena_screen.py at origin/main 91544819 (tools/ identical to 2835eb45 used by hunt28 L3 and L4; checked with git diff, empty).
- Blocks (docs/HOLDOUT_LEDGER.md at origin/main, last change 549e40f6, re-read 2026-10-24: all four are "exploration pool"): explore-0814 [2026-08-14T12, 2026-08-28T12), fast-pool-2026-09-18T23_2026-09-22T00, oracle-insample-2026-09-22_25, oracle-live-2026-09-25_27, all under /data/mal/clean-view. Not touched: fresh-08*, forward walks, October tape, the arena paths. Prior reads of these blocks are many (hunt23 to hunt28).

## Legs (4)
L in {1.9 (control, must reproduce the archived numbers), 1.0, 0.6, 0.3} seconds. At about 0.21 s per slot k = 10, 5, 3, 2 slots.

## Cells (9 lines; each priced at all 4 legs = 36 cell-leg prices, 27 of them new)
| id | line | rule file : cell | archived control |
|---|---|---|---|
| 1 | FLOOR v2 (kit form of lib-c-L1, tp-fix rule; tools/arena_rule_l1.py is its streaming port) | hunt24/l4-audit/l1_rule_tpfix.py:L1Rule cell l1, kwargs aux_dir=hunt24/l4-audit/work | hunt28/l3/out/l1-160000 |
| 2-4 | REVIVE r2h, r6h, r12h | hunt28/l4/l4rules.py:L4Rule cells r2h r6h r12h | hunt28/l4/out/priced |
| 5 | w2-quiet R4 | hunt23/w2-quiet/w2e_rule.py:Rule cell R4 | hunt28/l3/out/w2quiet-160000 |
| 6 | w2-quiet R9 | same rule, cell R9 | same |
| 7 | w2b-strand DS-C1-g50 | hunt23/w2b-strand/rule.py:StrandTable cell DS-C1-g50 (table npz as in l3) | hunt28/l3/out/w2bstrand-160000 |
| 8 | BREADTH b5 | hunt28/l4/l4rules.py:L4Rule cell b5 | hunt28/l4/out/priced |
| 9 | SELLABS s1 | hunt28/l4/l4rules.py:L4Rule cell s1 | hunt28/l4/out/priced |
The rule runs compute all of each rule's cells (the kit prices every cell of a rule); only the 9 above are reported and counted. No other cell, leg, stake, parameter or block is tried; nothing is changed after a result is seen.

## Multiplicity
9 lines x 3 new legs = 27 new cell-leg prices, plus 9 control prices. Each line also appears in earlier lanes (hunt23, hunt24, hunt28 L3/L4) as the survivor or near-survivor of a larger cell family, so every line is already a selected, in-sample cell; winner's curse applies on top. The question asked is a monotone trend in L, not a new survivor; no line is promoted from this table.

## Outputs per cell and leg (deciding sub-cell: strand rule total, rent 0)
trades/day; flat and pressure mean SOL per trade at 0.25 SOL; total ex-top-3 (SOL) both models; positive days / days both models; trade-level bootstrap CI90 lower bound (kit: 1,000 draws, seed 1) both models; entry impact at 0.25 SOL.
Zero crossing: for each line and each fail model, the largest L in the grid at which the mean is > 0 and the next-slower leg's mean is <= 0; both-model crossing = the slowest leg at which BOTH means are > 0 (reported as a bracket between grid points; linear interpolation in L is labelled as such). A line with mean <= 0 at 0.3 s under either model is "no crossing in grid". A line positive at 1.9 s is "positive at control".
Control check: the 1.9 s run must reproduce the archived mean, n, ex-top-3 for every cell to the printed digit; if not, STOP and say so.
