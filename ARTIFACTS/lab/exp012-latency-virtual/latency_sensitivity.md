# EXP-012 entry-latency sensitivity

**EXPLORATION, NOT EVIDENCE.** Selection: OUT-OF-FOLD (stored 9-fold LODO scores, ARTIFACTS/exp012/oof_scores.json); enter iff score >= frozen threshold. Threshold 0.8030766588450794.

Caveats:
- only the ENTRY slot is delayed; the exit delay stays at the frozen k=1 'start', so live decay is >= this
- selection score is the stored OOF score (computed at k=1 timing), not recomputed per k
- exploration pool, same 9 days the model was frozen on; a sensitivity curve, never a promote

Checks: {"n_oof_stored": 8801, "n_rows_at_smallest_k": 8801, "n_selected_stored": 881, "n_selected_at_smallest_k": 881}

Mean SOL per trade (0.5 SOL entries), 90% CI, ex-top-3 total SOL.

| k | n_rows | book | n | fill | flat mean | flat CI90 | flat ex-top3 | press mean | press CI90 | press ex-top3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 8801 | OOF-selected | 881 | 0.967 | 0.03021 | [0.01854, 0.04172] | 24.951 | 0.01867 | [0.01114, 0.02631] | 15.297 |
| 1 | 8801 | unfiltered | 8801 | 0.333 | -0.00072 | [-0.00292, 0.00137] | -8.145 | -0.00098 | [-0.00249, 0.00039] | -9.864 |
| 2 | 8801 | OOF-selected | 881 | 0.948 | 0.02601 | [0.01465, 0.03722] | 21.328 | 0.01620 | [0.00886, 0.02364] | 13.337 |
| 2 | 8801 | unfiltered | 8801 | 0.333 | -0.00077 | [-0.00283, 0.00132] | -8.490 | -0.00075 | [-0.00212, 0.00051] | -7.802 |
| 4 | 8801 | OOF-selected | 881 | 0.915 | 0.02105 | [0.00996, 0.03170] | 17.298 | 0.01332 | [0.00653, 0.02005] | 10.898 |
| 4 | 8801 | unfiltered | 8801 | 0.327 | -0.00020 | [-0.00213, 0.00178] | -3.365 | -0.00035 | [-0.00160, 0.00095] | -4.410 |
| 6 | 8801 | OOF-selected | 881 | 0.907 | 0.01880 | [0.00760, 0.02999] | 15.347 | 0.01246 | [0.00541, 0.01969] | 10.168 |
| 6 | 8801 | unfiltered | 8801 | 0.328 | -0.00045 | [-0.00237, 0.00137] | -5.839 | -0.00066 | [-0.00192, 0.00050] | -7.032 |
| 8 | 8801 | OOF-selected | 881 | 0.865 | 0.01177 | [0.00065, 0.02226] | 9.276 | 0.00769 | [0.00118, 0.01418] | 5.977 |
| 8 | 8801 | unfiltered | 8801 | 0.320 | -0.00164 | [-0.00365, 0.00030] | -16.253 | -0.00118 | [-0.00235, -0.00009] | -11.499 |
| 12 | 8801 | OOF-selected | 881 | 0.825 | 0.00581 | [-0.00479, 0.01652] | 4.077 | 0.00335 | [-0.00412, 0.01051] | 2.250 |
| 12 | 8801 | unfiltered | 8801 | 0.317 | -0.00229 | [-0.00417, -0.00046] | -23.206 | -0.00224 | [-0.00360, -0.00101] | -21.883 |
| 16 | 8801 | OOF-selected | 881 | 0.812 | 0.00285 | [-0.00755, 0.01304] | 1.477 | 0.00327 | [-0.00375, 0.01012] | 2.028 |
| 16 | 8801 | unfiltered | 8801 | 0.313 | -0.00251 | [-0.00450, -0.00062] | -25.498 | -0.00190 | [-0.00327, -0.00064] | -19.479 |
| 24 | 8801 | OOF-selected | 881 | 0.793 | 0.00369 | [-0.00635, 0.01388] | 2.127 | 0.00282 | [-0.00455, 0.01033] | 1.691 |
| 24 | 8801 | unfiltered | 8801 | 0.309 | -0.00294 | [-0.00486, -0.00102] | -28.754 | -0.00245 | [-0.00384, -0.00112] | -23.443 |

Pressure mean crosses 0: {"crosses": false, "note": "pressure mean stays > 0 through k=24 (no extrapolation)"}
