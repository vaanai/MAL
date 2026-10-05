# EXP-012 entry-veto rules at the frozen point, paired vs frozen

**EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE.** N = 7 rules; cumulative tries on these 9 days: 54 existing + 7 new = 61. CI: gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats).

Frozen point: {'threshold': 0.8030766588450794, 'k': 6, 'size_sol': 0.05, 'fee': 505000}. Windows: tape rules ['mig+1', 'mig+1']; drift rules last print with slot <= mig+3 (executor snapshot, RPC processed).

Caveats:
- exploration pool, the same 9 days the model was frozen on: best-of-N, winner's curse applies; not a promote, not gate evidence
- tape rules use slot mig+1 only (confirmed tape lags ~1.8 s, ~4 slots); drift uses the last print with slot <= mig+3 (executor snapshot, RPC processed); the lags are not measured here
- a positive result for the five tape-derived rules is NOT deployable as-is: the live executor would need the processed grad stream or extra RPC to see more than the first post-migration slot
- a vetoed entry is assumed to cost nothing (no tx sent); a window with no print gives no veto
- the nested number is an UPPER BOUND: stored OOF scores saw the other days, and the rule family was written after the live stop-loss pattern was known
- only the ENTRY slot is delayed (k=6); the exit delay stays at the frozen k=1 'start'; the live sell shortfall and entry noise are not added
- V-priced execution; exit sells quoted by the frozen exec model

Frozen: n 881, flat mean 0.00107 [-0.00004, 0.00221], pressure mean 0.00058 [-0.00012, 0.00131]

## Per rule, paired against frozen (a vetoed trade scores 0; difference = rule - frozen, SOL per frozen mint)

| rule | n vetoed | n kept | vetoed press mean | kept press mean | paired press | press CI90 | press days+ | paired flat | flat CI90 | flat days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| drift_gt_25 | 46 | 835 | -0.00120 | 0.00068 | 0.00006 | [0.00003, 0.00011] | 9/9 | 0.00010 | [0.00003, 0.00020] | 9/9 |
| drift_gt_50 | 20 | 861 | -0.00074 | 0.00061 | 0.00002 | [0.00001, 0.00003] | 8/9 | 0.00003 | [0.00001, 0.00008] | 8/9 |
| netflow_neg | 241 | 640 | 0.00103 | 0.00042 | -0.00028 | [-0.00061, 0.00007] | 3/9 | -0.00059 | [-0.00114, -0.00003] | 2/9 |
| maxsell_ge_2sol | 92 | 789 | 0.00031 | 0.00061 | -0.00003 | [-0.00023, 0.00018] | 3/9 | -0.00012 | [-0.00046, 0.00025] | 4/9 |
| maxsell_ge_5sol | 17 | 864 | -0.00059 | 0.00061 | 0.00001 | [-0.00007, 0.00010] | 3/9 | 0.00001 | [-0.00015, 0.00017] | 3/9 |
| creator_sold | 1 | 880 | 0.01273 | 0.00057 | -0.00001 | [-0.00004, 0.00000] | 0/9 | -0.00002 | [-0.00006, 0.00000] | 0/9 |
| sells_ge_buys | 198 | 683 | 0.00075 | 0.00053 | -0.00017 | [-0.00047, 0.00014] | 2/9 | -0.00031 | [-0.00078, 0.00019] | 2/9 |

## Nested leave-one-day-out over the rule family (frozen always a candidate), paired vs frozen

- held-out n 881, unavailable folds 0/9, training floor 100, times chosen {'frozen': 0, 'drift_gt_25': 9, 'drift_gt_50': 0, 'netflow_neg': 0, 'maxsell_ge_2sol': 0, 'maxsell_ge_5sol': 0, 'creator_sold': 0, 'sells_ge_buys': 0}
  - press: paired mean 0.00006 SOL/mint, CI90 [0.00003, 0.00011], held-out days positive 9/9
  - flat: paired mean 0.00010 SOL/mint, CI90 [0.00003, 0.00020], held-out days positive 9/9
- optimism gap (in-sample best `drift_gt_25` paired pressure mean 0.00006 minus nested held-out): 0.00000 SOL/mint
