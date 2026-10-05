# EXP-012 operating point (V-priced, 9-day exploration pool), paired against the frozen point

**EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE.** N = 36 cells computed and logged (12 primary, rest sensitivity); cumulative tries on these 9 days: 18 existing + 36 new = 54. CI: gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats).

- Thresholds: thresholds below 0.803 already failed DEC-017 (c): pressure -0.00312, CI90 [-0.01022, +0.00426]; 0.90 keeps 4 mints.
- k counts from the first PumpSwap print (entry_land_k); live a25eb17 entries land k(migrate) = 5-6, notebook evidence from jobs #170/#171, not measured here.
- fee 155k is a sensitivity at the same k; fee and k are coupled live (DEC-019 Am.1 stop rule) and the pressure curve is calibrated at 500k.
- no inner OOF scores are stored by the freeze; training-day selection used scores from models that saw the held-out day, so the nested number is an UPPER BOUND.

Caveats:
- exploration pool, the same 9 days the model was frozen on: best-of-N, winner's curse applies; not a promote, not gate evidence
- only the ENTRY slot is delayed per k; the exit delay stays at the frozen k=1 'start' (a live exit lags too)
- a missed fill (no state, slippage cap) costs one per-side fee and stays in n
- V-priced (vault + V) execution; a pool with no V is left unchanged and counted, never V=0 silently
- exit sell fills are quoted by the frozen exec model; the live probe's -11..-16 bps sell shortfall and +-300 bps entry noise are NOT added

## Frozen operating point (reference: threshold 0.8031, k=6, 0.05 SOL, fee 505k)

| thr | k | size | fee | n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.8031 | 6 | 0.05 | 505000 | 881 | 97.89 | 0.00107 | [-0.00004, 0.00221] | 0.1049 | 0.825 | 6/9 | 0.00058 | [-0.00012, 0.00131] | 0.0571 | 0.435 | 7/9 |

## Primary grid (size 0.05 SOL, fee 505k)

| thr | k | size | fee | n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.8031 | 6 | 0.05 | 505000 | 881 | 97.89 | 0.00107 | [-0.00004, 0.00221] | 0.1049 | 0.825 | 6/9 | 0.00058 | [-0.00012, 0.00131] | 0.0571 | 0.435 | 7/9 |
| 0.8200 | 6 | 0.05 | 505000 | 663 | 73.67 | 0.00145 | [0.00028, 0.00265] | 0.1069 | 0.849 | 6/9 | 0.00083 | [0.00006, 0.00158] | 0.0608 | 0.469 | 5/9 |
| 0.8500 | 6 | 0.05 | 505000 | 312 | 34.67 | 0.00297 | [0.00114, 0.00484] | 0.1029 | 0.816 | 8/9 | 0.00179 | [0.00060, 0.00300] | 0.0620 | 0.481 | 8/9 |
| 0.8031 | 8 | 0.05 | 505000 | 881 | 97.89 | 0.00047 | [-0.00065, 0.00153] | 0.0460 | 0.307 | 4/9 | 0.00016 | [-0.00050, 0.00080] | 0.0152 | 0.059 | 5/9 |
| 0.8200 | 8 | 0.05 | 505000 | 663 | 73.67 | 0.00084 | [-0.00034, 0.00201] | 0.0622 | 0.460 | 5/9 | 0.00040 | [-0.00032, 0.00110] | 0.0297 | 0.195 | 5/9 |
| 0.8500 | 8 | 0.05 | 505000 | 312 | 34.67 | 0.00173 | [-0.00010, 0.00353] | 0.0598 | 0.442 | 6/9 | 0.00087 | [-0.00023, 0.00194] | 0.0302 | 0.200 | 8/9 |
| 0.8031 | 10 | 0.05 | 505000 | 881 | 97.89 | 0.00003 | [-0.00105, 0.00110] | 0.0026 | -0.082 | 5/9 | -0.00009 | [-0.00081, 0.00063] | -0.0087 | -0.170 | 6/9 |
| 0.8200 | 10 | 0.05 | 505000 | 663 | 73.67 | 0.00060 | [-0.00052, 0.00178] | 0.0444 | 0.305 | 5/9 | 0.00026 | [-0.00049, 0.00107] | 0.0192 | 0.103 | 5/9 |
| 0.8500 | 10 | 0.05 | 505000 | 312 | 34.67 | 0.00155 | [-0.00017, 0.00324] | 0.0538 | 0.395 | 6/9 | 0.00095 | [-0.00024, 0.00202] | 0.0330 | 0.237 | 6/9 |
| 0.8031 | 12 | 0.05 | 505000 | 881 | 97.89 | -0.00011 | [-0.00119, 0.00094] | -0.0108 | -0.200 | 5/9 | -0.00029 | [-0.00103, 0.00042] | -0.0280 | -0.320 | 4/9 |
| 0.8200 | 12 | 0.05 | 505000 | 663 | 73.67 | 0.00046 | [-0.00073, 0.00161] | 0.0341 | 0.206 | 6/9 | 0.00022 | [-0.00061, 0.00100] | 0.0161 | 0.078 | 5/9 |
| 0.8500 | 12 | 0.05 | 505000 | 312 | 34.67 | 0.00169 | [-0.00005, 0.00349] | 0.0585 | 0.436 | 7/9 | 0.00115 | [0.00001, 0.00230] | 0.0398 | 0.299 | 8/9 |

## Nested leave-one-day-out over the THRESHOLD, per fixed k, paired vs the frozen threshold

Candidates [0.8030766588450794, 0.82, 0.85] (frozen first = reference, paired difference 0), size 0.05, fee 505000. Pick on 8 days by pooled pressure paired mean, score on the 9th; training floor 100 entered trades, a fold with no qualifying candidate is UNAVAILABLE (not defaulted). Common set = the frozen threshold's mints at that k; a candidate that skips a mint scores 0 on it. Upper bound (see above).

### k = 6
- frozen-threshold level: n 881, flat mean 0.00107 [-0.00004, 0.00221], pressure mean 0.00058 [-0.00012, 0.00131]
- nested threshold choice: held-out n 881, unavailable folds 0/9, times chosen {'t0.8031_k6_s0.05_f505000': 2, 't0.8200_k6_s0.05_f505000': 2, 't0.8500_k6_s0.05_f505000': 5}
  - flat: paired mean -0.00072 SOL/mint, CI90 [-0.00137, -0.00007], held-out days positive 0/9
  - pressure: paired mean -0.00035 SOL/mint, CI90 [-0.00076, 0.00007], held-out days positive 1/9
- optimism gap (in-sample best `t0.8500_k6_s0.05_f505000` paired pressure mean 0.00005 minus nested held-out -0.00035): 0.00040 SOL/mint

### k = 8
- frozen-threshold level: n 881, flat mean 0.00047 [-0.00065, 0.00153], pressure mean 0.00016 [-0.00050, 0.00080]
- nested threshold choice: held-out n 881, unavailable folds 0/9, times chosen {'t0.8031_k8_s0.05_f505000': 0, 't0.8200_k8_s0.05_f505000': 4, 't0.8500_k8_s0.05_f505000': 5}
  - flat: paired mean -0.00027 SOL/mint, CI90 [-0.00098, 0.00050], held-out days positive 3/9
  - pressure: paired mean -0.00009 SOL/mint, CI90 [-0.00051, 0.00036], held-out days positive 3/9
- optimism gap (in-sample best `t0.8500_k8_s0.05_f505000` paired pressure mean 0.00015 minus nested held-out -0.00009): 0.00024 SOL/mint

### k = 10
- frozen-threshold level: n 881, flat mean 0.00003 [-0.00105, 0.00110], pressure mean -0.00009 [-0.00081, 0.00063]
- nested threshold choice: held-out n 881, unavailable folds 0/9, times chosen {'t0.8031_k10_s0.05_f505000': 0, 't0.8200_k10_s0.05_f505000': 1, 't0.8500_k10_s0.05_f505000': 8}
  - flat: paired mean 0.00035 SOL/mint, CI90 [-0.00040, 0.00115], held-out days positive 6/9
  - pressure: paired mean 0.00028 SOL/mint, CI90 [-0.00020, 0.00081], held-out days positive 6/9
- optimism gap (in-sample best `t0.8500_k10_s0.05_f505000` paired pressure mean 0.00043 minus nested held-out 0.00028): 0.00014 SOL/mint

### k = 12
- frozen-threshold level: n 881, flat mean -0.00011 [-0.00119, 0.00094], pressure mean -0.00029 [-0.00103, 0.00042]
- nested threshold choice: held-out n 881, unavailable folds 0/9, times chosen {'t0.8031_k12_s0.05_f505000': 0, 't0.8200_k12_s0.05_f505000': 0, 't0.8500_k12_s0.05_f505000': 9}
  - flat: paired mean 0.00071 SOL/mint, CI90 [-0.00005, 0.00156], held-out days positive 7/9
  - pressure: paired mean 0.00069 SOL/mint, CI90 [0.00018, 0.00128], held-out days positive 8/9
- optimism gap (in-sample best `t0.8500_k12_s0.05_f505000` paired pressure mean 0.00069 minus nested held-out 0.00069): -0.00000 SOL/mint

## Fee sensitivity: 155000 per side, same cells (not a recommendation; fee and k are coupled live)

| thr | k | size | fee | n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.8031 | 6 | 0.05 | 155000 | 881 | 97.89 | 0.00169 | [0.00059, 0.00283] | 0.1657 | 1.370 | 8/9 | 0.00110 | [0.00040, 0.00182] | 0.1075 | 0.887 | 8/9 |
| 0.8200 | 6 | 0.05 | 155000 | 663 | 73.67 | 0.00207 | [0.00089, 0.00327] | 0.1523 | 1.256 | 7/9 | 0.00134 | [0.00057, 0.00210] | 0.0986 | 0.807 | 8/9 |
| 0.8500 | 6 | 0.05 | 155000 | 312 | 34.67 | 0.00359 | [0.00176, 0.00546] | 0.1243 | 1.006 | 8/9 | 0.00230 | [0.00112, 0.00351] | 0.0798 | 0.640 | 8/9 |
| 0.8031 | 8 | 0.05 | 155000 | 881 | 97.89 | 0.00108 | [-0.00003, 0.00214] | 0.1056 | 0.841 | 6/9 | 0.00066 | [0.00000, 0.00131] | 0.0643 | 0.499 | 6/9 |
| 0.8200 | 8 | 0.05 | 155000 | 663 | 73.67 | 0.00145 | [0.00026, 0.00262] | 0.1067 | 0.858 | 7/9 | 0.00090 | [0.00018, 0.00160] | 0.0664 | 0.523 | 6/9 |
| 0.8500 | 8 | 0.05 | 155000 | 312 | 34.67 | 0.00233 | [0.00050, 0.00413] | 0.0806 | 0.627 | 6/9 | 0.00137 | [0.00027, 0.00243] | 0.0474 | 0.353 | 8/9 |
| 0.8031 | 10 | 0.05 | 155000 | 881 | 97.89 | 0.00063 | [-0.00045, 0.00170] | 0.0617 | 0.447 | 6/9 | 0.00042 | [-0.00030, 0.00114] | 0.0413 | 0.278 | 6/9 |
| 0.8200 | 10 | 0.05 | 155000 | 663 | 73.67 | 0.00120 | [0.00007, 0.00238] | 0.0885 | 0.700 | 6/9 | 0.00077 | [0.00001, 0.00157] | 0.0566 | 0.438 | 6/9 |
| 0.8500 | 10 | 0.05 | 155000 | 312 | 34.67 | 0.00215 | [0.00043, 0.00383] | 0.0745 | 0.579 | 7/9 | 0.00146 | [0.00027, 0.00255] | 0.0506 | 0.394 | 8/9 |
| 0.8031 | 12 | 0.05 | 155000 | 881 | 97.89 | 0.00049 | [-0.00059, 0.00155] | 0.0476 | 0.325 | 5/9 | 0.00023 | [-0.00052, 0.00094] | 0.0223 | 0.130 | 6/9 |
| 0.8200 | 12 | 0.05 | 155000 | 663 | 73.67 | 0.00106 | [-0.00013, 0.00221] | 0.0780 | 0.599 | 6/9 | 0.00073 | [-0.00010, 0.00152] | 0.0539 | 0.417 | 8/9 |
| 0.8500 | 12 | 0.05 | 155000 | 312 | 34.67 | 0.00228 | [0.00055, 0.00409] | 0.0791 | 0.620 | 9/9 | 0.00166 | [0.00052, 0.00282] | 0.0576 | 0.458 | 8/9 |

## Size sensitivity at the frozen threshold, fee 505000 (NOT a selection)

**missing size-proportional costs (exit lag, sell shortfall, MEV); does not support any live size above 0.05 SOL.**

| thr | k | size | fee | n | trades/day | flat mean | flat CI90 | flat SOL/day | flat ex-top3 | flat days+ | press mean | press CI90 | press SOL/day | press ex-top3 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.8031 | 6 | 0.1 | 505000 | 881 | 97.89 | 0.00304 | [0.00082, 0.00531] | 0.2972 | 2.433 | 8/9 | 0.00191 | [0.00050, 0.00336] | 0.1868 | 1.520 | 7/9 |
| 0.8031 | 6 | 0.25 | 505000 | 881 | 97.89 | 0.00899 | [0.00343, 0.01466] | 0.8802 | 7.315 | 8/9 | 0.00590 | [0.00239, 0.00951] | 0.5775 | 4.791 | 8/9 |
| 0.8031 | 6 | 0.5 | 505000 | 881 | 97.89 | 0.01879 | [0.00759, 0.02998] | 1.8393 | 15.339 | 8/9 | 0.01246 | [0.00540, 0.01969] | 1.2194 | 10.161 | 8/9 |
| 0.8031 | 8 | 0.1 | 505000 | 881 | 97.89 | 0.00181 | [-0.00041, 0.00394] | 0.1777 | 1.383 | 5/9 | 0.00103 | [-0.00027, 0.00233] | 0.1011 | 0.751 | 6/9 |
| 0.8031 | 8 | 0.25 | 505000 | 881 | 97.89 | 0.00561 | [-0.00000, 0.01093] | 0.5487 | 4.394 | 6/9 | 0.00357 | [0.00031, 0.00676] | 0.3493 | 2.744 | 6/9 |
| 0.8031 | 8 | 0.5 | 505000 | 881 | 97.89 | 0.01176 | [0.00064, 0.02225] | 1.1509 | 9.269 | 7/9 | 0.00769 | [0.00117, 0.01418] | 0.7523 | 5.971 | 6/9 |
| 0.8031 | 10 | 0.1 | 505000 | 881 | 97.89 | 0.00091 | [-0.00123, 0.00306] | 0.0886 | 0.584 | 6/9 | 0.00054 | [-0.00090, 0.00195] | 0.0530 | 0.291 | 6/9 |
| 0.8031 | 10 | 0.25 | 505000 | 881 | 97.89 | 0.00353 | [-0.00186, 0.00901] | 0.3459 | 2.575 | 6/9 | 0.00248 | [-0.00110, 0.00608] | 0.2427 | 1.717 | 7/9 |
| 0.8031 | 10 | 0.5 | 505000 | 881 | 97.89 | 0.00714 | [-0.00364, 0.01791] | 0.6994 | 5.309 | 6/9 | 0.00521 | [-0.00191, 0.01236] | 0.5101 | 3.773 | 7/9 |
| 0.8031 | 12 | 0.1 | 505000 | 881 | 97.89 | 0.00064 | [-0.00151, 0.00276] | 0.0625 | 0.355 | 5/9 | 0.00017 | [-0.00132, 0.00159] | 0.0164 | 0.009 | 5/9 |
| 0.8031 | 12 | 0.25 | 505000 | 881 | 97.89 | 0.00289 | [-0.00255, 0.00818] | 0.2829 | 2.024 | 5/9 | 0.00155 | [-0.00218, 0.00514] | 0.1520 | 1.017 | 6/9 |
| 0.8031 | 12 | 0.5 | 505000 | 881 | 97.89 | 0.00580 | [-0.00480, 0.01651] | 0.5682 | 4.070 | 5/9 | 0.00335 | [-0.00413, 0.01050] | 0.3275 | 2.243 | 6/9 |
