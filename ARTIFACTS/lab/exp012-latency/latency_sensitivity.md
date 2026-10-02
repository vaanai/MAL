# EXP-012 entry-latency sensitivity

**EXPLORATION, NOT EVIDENCE.** Selection: OUT-OF-FOLD (stored 9-fold LODO scores, ARTIFACTS/exp012/oof_scores.json); enter iff score >= frozen threshold. Threshold 0.8030766588450794.

Caveats:
- only the ENTRY slot is delayed; the exit delay stays at the frozen k=1 'start', so live decay is >= this
- selection score is the stored OOF score (computed at k=1 timing), not recomputed per k
- exploration pool, same 9 days the model was frozen on; a sensitivity curve, never a promote

Checks: {"n_oof_stored": 8801, "n_rows_at_smallest_k": 8801, "n_selected_stored": 881, "n_selected_at_smallest_k": 881}

Mean SOL per trade (0.5 SOL entries), 90% CI, ex-top-3 total SOL.

| k | book | n | fill | flat mean | flat CI90 | flat ex-top3 | press mean | press CI90 | press ex-top3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | OOF-selected | 881 | 0.978 | 0.03707 | [0.02577, 0.04845] | 30.690 | 0.02315 | [0.01543, 0.03088] | 19.039 |
| 1 | unfiltered | 8801 | 0.347 | 0.00067 | [-0.00159, 0.00281] | 3.821 | 0.00009 | [-0.00140, 0.00156] | -0.599 |
| 2 | OOF-selected | 881 | 0.958 | 0.03773 | [0.02642, 0.04920] | 31.366 | 0.02378 | [0.01684, 0.03096] | 19.843 |
| 2 | unfiltered | 8801 | 0.345 | 0.00105 | [-0.00112, 0.00327] | 7.317 | 0.00061 | [-0.00084, 0.00195] | 4.097 |
| 4 | OOF-selected | 881 | 0.922 | 0.03068 | [0.01997, 0.04221] | 25.598 | 0.01875 | [0.01182, 0.02587] | 15.549 |
| 4 | unfiltered | 8801 | 0.334 | 0.00197 | [-0.00004, 0.00404] | 15.506 | 0.00110 | [-0.00014, 0.00240] | 8.324 |
| 6 | OOF-selected | 881 | 0.910 | 0.02833 | [0.01726, 0.03909] | 23.645 | 0.01817 | [0.01132, 0.02503] | 15.113 |
| 6 | unfiltered | 8801 | 0.333 | 0.00212 | [0.00001, 0.00414] | 16.640 | 0.00105 | [-0.00029, 0.00227] | 7.876 |
| 8 | OOF-selected | 881 | 0.867 | 0.02322 | [0.01234, 0.03440] | 19.339 | 0.01387 | [0.00737, 0.02064] | 11.426 |
| 8 | unfiltered | 8801 | 0.326 | -0.00029 | [-0.00233, 0.00165] | -4.717 | -0.00030 | [-0.00152, 0.00087] | -3.911 |
| 12 | OOF-selected | 881 | 0.826 | 0.02030 | [0.01005, 0.03046] | 16.611 | 0.01327 | [0.00614, 0.02034] | 10.897 |
| 12 | unfiltered | 8801 | 0.322 | -0.00049 | [-0.00249, 0.00146] | -8.028 | -0.00093 | [-0.00230, 0.00040] | -10.959 |

Pressure mean crosses 0: {"crosses": false, "note": "pressure mean stays > 0 through k=12 (no extrapolation)"}
