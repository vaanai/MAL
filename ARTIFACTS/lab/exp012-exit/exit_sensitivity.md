# EXP-012 exit-variant sensitivity

**EXPLORATION, NOT EVIDENCE.** Entry: frozen: k=1, bound 'start', direct, 0.5 SOL. Selection: OUT-OF-FOLD (stored 9-fold LODO scores, ARTIFACTS/exp012/oof_scores.json); enter iff score >= frozen threshold; same entered set for every variant. Threshold 0.8030766588450794.

**Winner's curse.** 17 exit variants were tried on the same 9 days the model was frozen on. Picking the best of them is a selection: its mean is biased upward and the rank below is not evidence. Compare variants on day stability and CI width, not on the top row. Variants tried: 17.

Caveats:
- only the exit rule changes; entry is the frozen k=1 'start'
- exploration pool, same 9 days the model was frozen on; OOF removes the model's own-day fit, not the winner's curse of the variant choice
- per-day means are small-n cells (about 100 entered trades per day on average); a positive-day count is a coarse stability read

Mean SOL per trade (0.5 SOL entries), 90% CI, ex-top-3 total SOL. REF = the frozen tp50_sl30. Rank is by pressure mean among the variants tried.

| rank | variant | n | fill | flat mean | flat CI90 | flat ex-top3 | press mean | press CI90 | press ex-top3 | press days+ | unfiltered press mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 of 17 | `tpsl_tp50_sl20` | 881 | 0.978 | 0.03118 | [0.02077, 0.04137] | 25.503 | 0.01986 | [0.01298, 0.02691] | 16.135 | 9/9 | -0.00084 |
| 2 of 17 | `tpsl_tp50_sl30` **REF** | 881 | 0.978 | 0.03707 | [0.02577, 0.04845] | 30.690 | 0.02315 | [0.01543, 0.03088] | 19.039 | 9/9 | 0.00009 |
| 4 of 17 | `tpsl_tp50_sl40` | 881 | 0.978 | 0.03782 | [0.02564, 0.05061] | 31.347 | 0.02298 | [0.01488, 0.03152] | 18.886 | 9/9 | -0.00005 |
| 9 of 17 | `tpsl_tp75_sl20` | 881 | 0.978 | 0.03128 | [0.01878, 0.04405] | 25.591 | 0.01964 | [0.01094, 0.02849] | 15.945 | 9/9 | -0.00155 |
| 6 of 17 | `tpsl_tp75_sl30` | 881 | 0.978 | 0.03398 | [0.02040, 0.04827] | 27.970 | 0.02064 | [0.01134, 0.03014] | 16.822 | 9/9 | -0.00131 |
| 10 of 17 | `tpsl_tp75_sl40` | 881 | 0.978 | 0.03128 | [0.01549, 0.04678] | 25.587 | 0.01806 | [0.00801, 0.02859] | 14.557 | 9/9 | -0.00168 |
| 11 of 17 | `tpsl_tp100_sl20` | 881 | 0.978 | 0.03034 | [0.01602, 0.04487] | 24.545 | 0.01796 | [0.00847, 0.02799] | 14.301 | 7/9 | -0.00162 |
| 5 of 17 | `tpsl_tp100_sl30` | 881 | 0.978 | 0.03779 | [0.02212, 0.05429] | 31.112 | 0.02221 | [0.01155, 0.03321] | 17.979 | 9/9 | -0.00150 |
| 7 of 17 | `tpsl_tp100_sl40` | 881 | 0.978 | 0.03593 | [0.01793, 0.05434] | 29.470 | 0.01992 | [0.00821, 0.03250] | 15.962 | 9/9 | -0.00216 |
| 1 of 17 | `timecap_10m_tp50_sl30` | 881 | 0.978 | 0.03837 | [0.02706, 0.04980] | 31.831 | 0.02409 | [0.01662, 0.03196] | 19.868 | 9/9 | 0.00013 |
| 3 of 17 | `timecap_60m_tp50_sl30` | 881 | 0.978 | 0.03674 | [0.02557, 0.04819] | 30.395 | 0.02308 | [0.01543, 0.03098] | 18.973 | 9/9 | 0.00008 |
| 17 of 17 | `trail_20` | 881 | 0.978 | 0.01241 | [0.00296, 0.02197] | 6.978 | 0.00803 | [0.00153, 0.01433] | 4.385 | 8/9 | -0.00170 |
| 16 of 17 | `trail_30` | 881 | 0.978 | 0.01085 | [-0.00218, 0.02440] | 3.461 | 0.00812 | [-0.00080, 0.01788] | 2.169 | 5/9 | -0.00085 |
| 12 of 17 | `ladder_take50_trail20` | 881 | 0.978 | 0.02115 | [0.01112, 0.03145] | 16.329 | 0.01365 | [0.00668, 0.02100] | 10.370 | 8/9 | -0.00153 |
| 13 of 17 | `ladder_take100_trail20` | 881 | 0.978 | 0.01975 | [0.00532, 0.03344] | 14.369 | 0.01156 | [0.00227, 0.02137] | 7.760 | 7/9 | -0.00224 |
| 14 of 17 | `ladder_take150_trail20` | 881 | 0.978 | 0.01798 | [0.00115, 0.03532] | 12.468 | 0.01001 | [-0.00099, 0.02172] | 6.094 | 7/9 | -0.00201 |
| 15 of 17 | `ladder_take200_trail20` | 881 | 0.978 | 0.01749 | [-0.00176, 0.03739] | 11.530 | 0.00839 | [-0.00400, 0.02150] | 4.244 | 7/9 | -0.00111 |

Per-day pressure mean SOL/trade, OOF-selected (n in parentheses).

| variant | 2026-09-19 | 2026-09-20 | 2026-09-21 | 2026-09-22 | 2026-09-23 | 2026-09-24 | 2026-09-25 | 2026-09-26 | 2026-09-27 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tpsl_tp50_sl20` | +0.0123 (87) | +0.0048 (112) | +0.0151 (91) | +0.0335 (119) | +0.0120 (135) | +0.0199 (101) | +0.0167 (84) | +0.0328 (82) | +0.0402 (70) |
| `tpsl_tp50_sl30` **REF** | +0.0203 (87) | +0.0137 (112) | +0.0172 (91) | +0.0274 (119) | +0.0098 (135) | +0.0241 (101) | +0.0244 (84) | +0.0369 (82) | +0.0492 (70) |
| `tpsl_tp50_sl40` | +0.0351 (87) | +0.0146 (112) | +0.0197 (91) | +0.0320 (119) | +0.0009 (135) | +0.0227 (101) | +0.0148 (84) | +0.0312 (82) | +0.0533 (70) |
| `tpsl_tp75_sl20` | +0.0082 (87) | +0.0107 (112) | +0.0134 (91) | +0.0321 (119) | +0.0117 (135) | +0.0179 (101) | +0.0262 (84) | +0.0339 (82) | +0.0281 (70) |
| `tpsl_tp75_sl30` | +0.0105 (87) | +0.0117 (112) | +0.0172 (91) | +0.0242 (119) | +0.0115 (135) | +0.0255 (101) | +0.0324 (84) | +0.0268 (82) | +0.0351 (70) |
| `tpsl_tp75_sl40` | +0.0194 (87) | +0.0025 (112) | +0.0120 (91) | +0.0255 (119) | +0.0039 (135) | +0.0285 (101) | +0.0217 (84) | +0.0236 (82) | +0.0381 (70) |
| `tpsl_tp100_sl20` | -0.0141 (87) | +0.0182 (112) | -0.0006 (91) | +0.0314 (119) | +0.0168 (135) | +0.0206 (101) | +0.0183 (84) | +0.0538 (82) | +0.0148 (70) |
| `tpsl_tp100_sl30` | +0.0011 (87) | +0.0247 (112) | +0.0035 (91) | +0.0265 (119) | +0.0177 (135) | +0.0299 (101) | +0.0304 (84) | +0.0498 (82) | +0.0171 (70) |
| `tpsl_tp100_sl40` | +0.0026 (87) | +0.0143 (112) | +0.0016 (91) | +0.0328 (119) | +0.0115 (135) | +0.0367 (101) | +0.0220 (84) | +0.0476 (82) | +0.0096 (70) |
| `timecap_10m_tp50_sl30` | +0.0201 (87) | +0.0137 (112) | +0.0177 (91) | +0.0292 (119) | +0.0106 (135) | +0.0238 (101) | +0.0270 (84) | +0.0420 (82) | +0.0473 (70) |
| `timecap_60m_tp50_sl30` | +0.0203 (87) | +0.0137 (112) | +0.0176 (91) | +0.0274 (119) | +0.0098 (135) | +0.0241 (101) | +0.0231 (84) | +0.0369 (82) | +0.0492 (70) |
| `trail_20` | -0.0127 (87) | +0.0029 (112) | +0.0044 (91) | +0.0071 (119) | +0.0119 (135) | +0.0063 (101) | +0.0250 (84) | +0.0122 (82) | +0.0181 (70) |
| `trail_30` | -0.0277 (87) | -0.0007 (112) | +0.0057 (91) | +0.0103 (119) | -0.0019 (135) | -0.0014 (101) | +0.0320 (84) | +0.0479 (82) | +0.0240 (70) |
| `ladder_take50_trail20` | -0.0003 (87) | +0.0025 (112) | +0.0047 (91) | +0.0245 (119) | +0.0097 (135) | +0.0132 (101) | +0.0195 (84) | +0.0263 (82) | +0.0286 (70) |
| `ladder_take100_trail20` | -0.0265 (87) | +0.0118 (112) | -0.0152 (91) | +0.0230 (119) | +0.0139 (135) | +0.0139 (101) | +0.0269 (84) | +0.0491 (82) | +0.0037 (70) |
| `ladder_take150_trail20` | -0.0428 (87) | +0.0182 (112) | -0.0168 (91) | +0.0260 (119) | +0.0048 (135) | +0.0127 (101) | +0.0340 (84) | +0.0491 (82) | +0.0018 (70) |
| `ladder_take200_trail20` | -0.0585 (87) | +0.0205 (112) | -0.0123 (91) | +0.0189 (119) | +0.0076 (135) | +0.0000 (101) | +0.0333 (84) | +0.0504 (82) | +0.0157 (70) |

## Paired increment vs the reference

Per-mint difference (variant net minus tp50_sl30 net) on the same OOF-selected mints, mean SOL/trade with the gate's 90% CI (1,000 draws, seed 1) and the share of days whose mean difference is positive, under both fail models.

| variant | paired n (common with ref) | lost to censoring | flat diff | flat CI90 | flat days+ | press diff | press CI90 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tpsl_tp50_sl20` | 881 | 0 | -0.00589 | [-0.01137, -0.00066] | 2/9 (0.22) | -0.00330 | [-0.00681, 0.00004] | 2/9 (0.22) |
| `tpsl_tp50_sl30` **REF** | | | 0 by construction | | | 0 by construction | | |
| `tpsl_tp50_sl40` | 881 | 0 | 0.00074 | [-0.00423, 0.00608] | 6/9 (0.67) | -0.00017 | [-0.00339, 0.00347] | 5/9 (0.56) |
| `tpsl_tp75_sl20` | 881 | 0 | -0.00579 | [-0.01505, 0.00314] | 3/9 (0.33) | -0.00351 | [-0.00970, 0.00265] | 3/9 (0.33) |
| `tpsl_tp75_sl30` | 881 | 0 | -0.00309 | [-0.01151, 0.00561] | 3/9 (0.33) | -0.00252 | [-0.00817, 0.00313] | 4/9 (0.44) |
| `tpsl_tp75_sl40` | 881 | 0 | -0.00579 | [-0.01612, 0.00502] | 2/9 (0.22) | -0.00509 | [-0.01186, 0.00188] | 1/9 (0.11) |
| `tpsl_tp100_sl20` | 881 | 0 | -0.00673 | [-0.01806, 0.00462] | 4/9 (0.44) | -0.00519 | [-0.01294, 0.00275] | 4/9 (0.44) |
| `tpsl_tp100_sl30` | 881 | 0 | 0.00072 | [-0.01062, 0.01239] | 5/9 (0.56) | -0.00094 | [-0.00864, 0.00668] | 5/9 (0.56) |
| `tpsl_tp100_sl40` | 881 | 0 | -0.00114 | [-0.01432, 0.01242] | 5/9 (0.56) | -0.00323 | [-0.01222, 0.00604] | 5/9 (0.56) |
| `timecap_10m_tp50_sl30` | 881 | 0 | 0.00129 | [-0.00061, 0.00330] | 5/9 (0.56) | 0.00094 | [-0.00013, 0.00213] | 5/9 (0.56) |
| `timecap_60m_tp50_sl30` | 881 | 0 | -0.00034 | [-0.00150, 0.00036] | 2/9 (0.22) | -0.00008 | [-0.00048, 0.00016] | 2/9 (0.22) |
| `trail_20` | 881 | 0 | -0.02467 | [-0.03504, -0.01445] | 0/9 (0.00) | -0.01512 | [-0.02234, -0.00820] | 2/9 (0.22) |
| `trail_30` | 881 | 0 | -0.02623 | [-0.03795, -0.01420] | 2/9 (0.22) | -0.01503 | [-0.02345, -0.00654] | 2/9 (0.22) |
| `ladder_take50_trail20` | 881 | 0 | -0.01593 | [-0.02265, -0.00928] | 0/9 (0.00) | -0.00950 | [-0.01408, -0.00530] | 0/9 (0.00) |
| `ladder_take100_trail20` | 881 | 0 | -0.01733 | [-0.02895, -0.00587] | 4/9 (0.44) | -0.01159 | [-0.01919, -0.00346] | 3/9 (0.33) |
| `ladder_take150_trail20` | 881 | 0 | -0.01909 | [-0.03431, -0.00520] | 4/9 (0.44) | -0.01314 | [-0.02336, -0.00322] | 3/9 (0.33) |
| `ladder_take200_trail20` | 881 | 0 | -0.01959 | [-0.03812, -0.00203] | 3/9 (0.33) | -0.01476 | [-0.02666, -0.00224] | 3/9 (0.33) |

## Nested leave-one-day-out selection of the exit

Rule: per held-out day: variant with the best pooled pressure paired mean on the other days (reference = 0 is a candidate); scored on the held-out day. Held-out days: 9.

Common mint set (a row in every candidate variant and the reference, used for the choice and the held-out score): 881 of 881 OOF-selected mints. Candidates: `tpsl_tp50_sl30`, `tpsl_tp50_sl20`, `tpsl_tp50_sl40`, `tpsl_tp75_sl20`, `tpsl_tp75_sl30`, `tpsl_tp75_sl40`, `tpsl_tp100_sl20`, `tpsl_tp100_sl30`, `tpsl_tp100_sl40`, `timecap_10m_tp50_sl30`, `timecap_60m_tp50_sl30`, `trail_20`, `trail_30`, `ladder_take50_trail20`, `ladder_take100_trail20`, `ladder_take150_trail20`, `ladder_take200_trail20`.

No variant was excluded as censored.

Advantage of the selection procedure over tp50_sl30, held-out days only (mean SOL/trade of chosen minus reference).

| scope | paired n | flat adv | flat CI90 | flat days+ | press adv | press CI90 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all held-out days | 881 | -0.00640 | [-0.01103, -0.00185] | 3/9 (0.33) | -0.00412 | [-0.00739, -0.00102] | 3/9 (0.33) |
| all-days selection rule, scored on fast days (about 3-4 days, coarse) | 290 | 0.00025 | [-0.00247, 0.00275] | 1/3 (0.33) | 0.00011 | [-0.00095, 0.00115] | 1/3 (0.33) |

Times chosen (of the held-out days): `tpsl_tp50_sl40` 2, `tpsl_tp100_sl30` 1, `timecap_10m_tp50_sl30` 6.

| held-out day | chosen |
| --- | --- |
| 2026-09-19 | `timecap_10m_tp50_sl30` |
| 2026-09-20 | `timecap_10m_tp50_sl30` |
| 2026-09-21 | `timecap_10m_tp50_sl30` |
| 2026-09-22 | `timecap_10m_tp50_sl30` |
| 2026-09-23 | `tpsl_tp50_sl40` |
| 2026-09-24 | `timecap_10m_tp50_sl30` |
| 2026-09-25 | `tpsl_tp50_sl40` |
| 2026-09-26 | `timecap_10m_tp50_sl30` |
| 2026-09-27 | `tpsl_tp100_sl30` |

## Exit-side latency

not implemented: tools/exploration_exits.py fixes the exit's landing delay to its entry constants (_delayed(..., ENTRY_LAND_K, ENTRY_BOUND, ENTRY_BOUND)) and a time-cap exit has no delay at all; there is no parameter for a delayed exit fill, and this PR does not add exit logic. The k in {1, 4, 8} exit-side check for the reference and the top-3 variants is not run.

## Tries

Variants tried: 17 (of 22 requested; 5 skipped, listed below). Also run: nested-LODO selection over the same variants (one selection procedure, not a variant). Tries-log lines written this run: 17.

Skipped requested variants:
- `tpsl_tp30_sl20` (tp_sl_grid): not implemented in tools/exploration_exits.py
- `tpsl_tp30_sl30` (tp_sl_grid): not implemented in tools/exploration_exits.py
- `tpsl_tp30_sl40` (tp_sl_grid): not implemented in tools/exploration_exits.py
- `timecap_30m_tp50_sl30` (time_cap): identical exit to the reference tpsl_tp50_sl30
- `trail_40` (trailing_stop): not implemented in tools/exploration_exits.py
