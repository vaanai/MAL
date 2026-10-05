# Live probe vs simulator (execution check, not edge evidence), 2026-10-05

**These are execution measurements at 0.05 SOL.** Probe trades never count toward any gate. Source: `tools/probe_sim_calibration.py` (#322, revised in #331 and the sim-cap flag branch), run on all live probe fills against the tip tape on mal-fast-0.
- **Seal:** only mints the probe traded. The paper runner's files were never read.
- **Rounding:** all numbers are copied from job output and not rounded up.

## What the tool does

For every live trade it re-simulates the same mint on the tip tape: V-priced entry at the live landed slot, the executor's exit rule, live fees. It compares the result with the live fill. Variants of the tp/sl reference price (the "mark"):
- `live_correct`: the post-buy state, as #331 now does live from our own buy tx;
- `live_legacy_correct`: the send-state mark, as builds 8a6849b, a25eb17 and 7004b16 did;
- `live_snapshot_correct`: the #330 first-snapshot method, which was never installed;
- `sim_correct`: tape-sim entry.

## Results

**Job #187** (tool at 0ab8ad1 = #331, 28 closed trades). Exit decisions that match the executor's actual exits:

| variant | all | 8a6849b | a25eb17 | 7004b16 |
| --- | --- | --- | --- | --- |
| live_correct (buy-tx mark) | 27/28 | 5/6 | 15/15 | 7/7 |
| live_legacy_correct (send-state) | 26/28 | 5/6 | 14/15 | 7/7 |
| live_snapshot_correct (#330) | 26/28 | 5/6 | 15/15 | 6/7 |
| sim_correct | 27/28 | 5/6 | 15/15 | 7/7 |

The one miss in every variant is 8JqER8jd on 8a6849b. That is the 5 s exit-poll bug fixed in #315: live stopped at −0.7369 where the sim takes profit.

**Job #175** (tool at 0b95671, 20 trades). These are the double-count and parity checks behind #324:
- **Double count:** executor-identical minus correct-book ret at the live trigger row, mean 0.0010595, median 0.0008841, n = 19. #324 fixed this.
- **ATA rent:** charged minus refunded is 0 on all 19.
- **Entry gap:** sim tokens vs live, mean +26.08 bps, median +1.03 bps.

**Job #189** (sim-cap flag, 28 closed trades). The exploration simulator refuses entries more than 15% above the migration-slot price (`latency_curve._try_buy`, `SLIPPAGE_CAP` 0.15). Live has no such cap.
- 5 of 28 live trades fall outside the cap. They made +10,642,436 lamports live.
- The other 23 made −164,004,662.
- Conclusion: no live 15% guard is proposed on this evidence (n = 5).

## Reading

On the fixed builds, the sim reproduces the executor's exit decisions on every trade (a25eb17 15/15, 7004b16 7/7). Taking the mark from our own buy tx (#331) agrees as well as or better than the alternatives. This checks the execution model only. It says nothing about whether EXP-012 has an edge.
