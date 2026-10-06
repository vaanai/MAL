# Probe calibration update, 2026-10-06 (job #209): 36 closed live trades

**Execution check, not edge evidence.** Job #209 ran `tools/probe_sim_calibration.py` on mal-fast-0 at main 57c96a0, against `probe-fills.jsonl` read at about 01:22Z, with the tip tape. It is sealed to probe-traded mints only. Builds are never pooled for P&L.

Probe status at 01:21:38Z (job #208): 37/90 attempts, realized −0.174754 SOL, 1 open, build `faa319227eee420319eed06e85774f64dd2273b1`.

## Exit decisions (primary variant)

| build | closed | sim reproduces executor exit | tp-vs-sl disagree |
| --- | ---: | --- | ---: |
| 8a6849b | 6 | 5/6 | 1 (the old 5 s-poll bug) |
| a25eb17 | 15 | 15/15 | 0 |
| 7004b16 | 7 | 7/7 | 0 |
| faa3192 | 8 | 8/8 | 0 |
| all | 36 | 35/36 | 1 |

## faa3192, per trade (live pnl vs tape-sim pnl, lamports)

| mint | entry gap bps | exit (live/sim) | live pnl | sim pnl | gap |
| --- | ---: | --- | ---: | ---: | ---: |
| FKcuvH3E | −2.519 | sl/sl | −16,486,700 | −15,496,327 | −990,373 |
| 55ApVwM9 | 189.3 | tp/tp | 23,335,197 | 24,634,884 | −1,299,687 |
| 4u4v866X | −1.43 | tp/tp | 24,224,603 | 24,128,023 | 96,580 |
| HJek92mM | −198.9 | sl/sl | −20,981,498 | −21,388,682 | 407,184 |
| BpTuijKr | −19.89 | tp/tp | 27,281,949 | 27,208,791 | 73,158 |
| 2kFLTpHg | 0.8064 | sl/sl | −19,661,320 | −18,619,844 | −1,041,476 |
| D5X4EM9n | 74.31 | sl/sl | −18,025,842 | −18,200,775 | 174,933 |
| 3WaeS9Ce | 161.7 | sl/sl | −20,573,395 | −20,080,897 | −492,498 |

faa3192 total live P&L on the 8 closed trades: −20,887,006 lamports (job #208); 3 tp, 5 sl.

## Reading

- The tape sim reproduces the live exit decision on every closed trade since the 5 s-poll fix (30/30).
- The per-trade P&L gap on faa3192 runs from −1,299,687 to +407,184 lamports. It leans negative: 4 of 8 are below −490,000.
- The calibration starts from the live landed slot and fill. It says nothing about how the sim models landing (k) or fees at other settings.
- The losses on faa3192 are the strategy's outcomes at this operating point (3 of 8 hit tp), not a simulator error.
- n is small: 8 trades on this build.
