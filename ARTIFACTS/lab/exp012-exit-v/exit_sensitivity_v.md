# **EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE: BEST OF 7; cumulative tries on these 9 days: 54 existing + 7 = 61**

**EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE.** Entry: k=6 (bound 'start', direct), V pricing, exit_land_k=2, size 0.05 SOL, fee 505000 lamports/side. Selection: OUT-OF-FOLD (stored 9-fold LODO scores, ARTIFACTS/exp012/oof_scores.json); enter iff score >= frozen threshold; same entered set for every variant. Threshold 0.8030766588450794.

**Winner's curse.** 7 exit variants were tried on the same 9 days the model was frozen on, on top of 54 earlier tries (cumulative 61). The best of them is a selection, biased upward; the nested estimate is the honest number. Variants tried: 7.

Caveats:
- cell: k=6 (bound 'start', direct), V pricing, exit_land_k=2, size 0.05 SOL, fee 505000 lamports/side; only the exit rule changes between variants
- exploration pool, same 9 days the model was frozen on; OOF removes the model's own-day fit, not the winner's curse of the variant choice
- per-day means are small-n cells; a positive-day count is a coarse stability read
- costs still missing, as in the operating-point note: sell shortfall beyond the modeled lag, MEV, size-proportional costs

Mean SOL per trade (0.05 SOL entries), 90% CI, ex-top-3 total SOL. REF = the frozen tp50_sl30. Rank is by pressure mean among the variants tried.

| rank | variant | n | fill | flat mean | flat CI90 | flat ex-top3 | press mean | press CI90 | press ex-top3 | press days+ | unfiltered press mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 of 7 | `tpsl_tp50_sl30` **REF** | 881 | 0.910 | 0.00068 | [-0.00046, 0.00186] | 0.470 | 0.00035 | [-0.00037, 0.00108] | 0.218 | 5/9 | -0.00044 |
| 6 of 7 | `tpsl_tp50_sl20` | 881 | 0.910 | 0.00049 | [-0.00044, 0.00151] | 0.303 | 0.00019 | [-0.00042, 0.00084] | 0.079 | 5/9 | -0.00042 |
| 2 of 7 | `tpsl_tp50_sl40` | 881 | 0.910 | 0.00093 | [-0.00028, 0.00214] | 0.685 | 0.00049 | [-0.00029, 0.00124] | 0.343 | 6/9 | -0.00048 |
| 3 of 7 | `tpsl_tp40_sl30` | 881 | 0.910 | 0.00085 | [-0.00014, 0.00184] | 0.621 | 0.00039 | [-0.00022, 0.00101] | 0.258 | 6/9 | -0.00042 |
| 7 of 7 | `tpsl_tp75_sl30` | 881 | 0.910 | -0.00004 | [-0.00144, 0.00131] | -0.193 | -0.00007 | [-0.00097, 0.00077] | -0.174 | 4/9 | -0.00052 |
| 4 of 7 | `timecap_10m_tp50_sl30` | 881 | 0.910 | 0.00071 | [-0.00039, 0.00183] | 0.490 | 0.00038 | [-0.00033, 0.00109] | 0.249 | 5/9 | -0.00043 |
| 1 of 7 | `timecap_5m_tp50_sl30` | 881 | 0.910 | 0.00106 | [0.00006, 0.00213] | 0.803 | 0.00058 | [-0.00006, 0.00124] | 0.425 | 6/9 | -0.00036 |

Per-day pressure mean SOL/trade, OOF-selected (n in parentheses).

| variant | 2026-09-19 | 2026-09-20 | 2026-09-21 | 2026-09-22 | 2026-09-23 | 2026-09-24 | 2026-09-25 | 2026-09-26 | 2026-09-27 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tpsl_tp50_sl30` **REF** | -0.0004 (87) | +0.0013 (112) | -0.0003 (91) | -0.0007 (119) | -0.0016 (135) | +0.0001 (101) | +0.0021 (84) | +0.0019 (82) | +0.0027 (70) |
| `tpsl_tp50_sl20` | -0.0022 (87) | +0.0008 (112) | -0.0003 (91) | -0.0003 (119) | -0.0011 (135) | +0.0005 (101) | +0.0021 (84) | +0.0018 (82) | +0.0017 (70) |
| `tpsl_tp50_sl40` | +0.0003 (87) | +0.0020 (112) | -0.0005 (91) | -0.0006 (119) | -0.0017 (135) | +0.0004 (101) | +0.0018 (84) | +0.0013 (82) | +0.0034 (70) |
| `tpsl_tp40_sl30` | +0.0006 (87) | +0.0010 (112) | +0.0003 (91) | -0.0008 (119) | -0.0016 (135) | -0.0002 (101) | +0.0018 (84) | +0.0015 (82) | +0.0031 (70) |
| `tpsl_tp75_sl30` | -0.0009 (87) | +0.0019 (112) | -0.0006 (91) | -0.0006 (119) | -0.0025 (135) | -0.0003 (101) | +0.0011 (84) | +0.0019 (82) | +0.0006 (70) |
| `timecap_10m_tp50_sl30` | -0.0008 (87) | +0.0012 (112) | -0.0002 (91) | -0.0006 (119) | -0.0015 (135) | +0.0001 (101) | +0.0022 (84) | +0.0027 (82) | +0.0023 (70) |
| `timecap_5m_tp50_sl30` | +0.0004 (87) | +0.0014 (112) | +0.0006 (91) | -0.0006 (119) | -0.0013 (135) | -0.0002 (101) | +0.0019 (84) | +0.0026 (82) | +0.0022 (70) |

## Paired increment vs the reference

Per-mint difference (variant net minus tp50_sl30 net) on the same OOF-selected mints, mean SOL/trade with the gate's 90% CI (1,000 draws, seed 1) and the share of days whose mean difference is positive, under both fail models.

| variant | paired n (common with ref) | lost to censoring | flat diff | flat CI90 | flat days+ | press diff | press CI90 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tpsl_tp50_sl30` **REF** | | | 0 by construction | | | 0 by construction | | |
| `tpsl_tp50_sl20` | 881 | 0 | -0.00019 | [-0.00073, 0.00032] | 4/9 (0.44) | -0.00016 | [-0.00049, 0.00018] | 5/9 (0.56) |
| `tpsl_tp50_sl40` | 881 | 0 | 0.00024 | [-0.00018, 0.00069] | 6/9 (0.67) | 0.00014 | [-0.00013, 0.00042] | 5/9 (0.56) |
| `tpsl_tp40_sl30` | 881 | 0 | 0.00017 | [-0.00033, 0.00073] | 4/9 (0.44) | 0.00004 | [-0.00026, 0.00040] | 4/9 (0.44) |
| `tpsl_tp75_sl30` | 881 | 0 | -0.00073 | [-0.00160, 0.00008] | 2/9 (0.22) | -0.00041 | [-0.00097, 0.00012] | 2/9 (0.22) |
| `timecap_10m_tp50_sl30` | 881 | 0 | 0.00002 | [-0.00017, 0.00023] | 6/9 (0.67) | 0.00004 | [-0.00008, 0.00017] | 6/9 (0.67) |
| `timecap_5m_tp50_sl30` | 881 | 0 | 0.00038 | [-0.00005, 0.00084] | 6/9 (0.67) | 0.00024 | [-0.00005, 0.00054] | 6/9 (0.67) |

## Nested leave-one-day-out selection of the exit

Rule: per held-out day: variant with the best pooled pressure paired mean on the other days (reference = 0 is a candidate); scored on the held-out day. Held-out days: 9.

Common mint set (a row in every candidate variant and the reference, used for the choice and the held-out score): 881 of 881 OOF-selected mints. Candidates: `tpsl_tp50_sl30`, `tpsl_tp50_sl20`, `tpsl_tp50_sl40`, `tpsl_tp40_sl30`, `tpsl_tp75_sl30`, `timecap_10m_tp50_sl30`, `timecap_5m_tp50_sl30`.

No variant was excluded as censored.

Advantage of the selection procedure over tp50_sl30, held-out days only (mean SOL/trade of chosen minus reference).

| scope | paired n | flat adv | flat CI90 | flat days+ | press adv | press CI90 | press days+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all held-out days | 881 | 0.00008 | [-0.00032, 0.00052] | 5/9 (0.56) | 0.00000 | [-0.00026, 0.00029] | 4/9 (0.44) |
| all-days selection rule, scored on fast days (about 3-4 days, coarse) | 290 | 0.00053 | [-0.00037, 0.00147] | 3/3 (1.00) | 0.00025 | [-0.00033, 0.00083] | 2/3 (0.67) |

Times chosen (of the held-out days): `tpsl_tp50_sl40` 2, `timecap_5m_tp50_sl30` 7.

| held-out day | chosen |
| --- | --- |
| 2026-09-19 | `timecap_5m_tp50_sl30` |
| 2026-09-20 | `timecap_5m_tp50_sl30` |
| 2026-09-21 | `tpsl_tp50_sl40` |
| 2026-09-22 | `timecap_5m_tp50_sl30` |
| 2026-09-23 | `timecap_5m_tp50_sl30` |
| 2026-09-24 | `timecap_5m_tp50_sl30` |
| 2026-09-25 | `timecap_5m_tp50_sl30` |
| 2026-09-26 | `tpsl_tp50_sl40` |
| 2026-09-27 | `timecap_5m_tp50_sl30` |

## Exit-side latency

modeled: exit_land_k=2. An event exit (tp/sl) fills at the state of the last print before slot trigger+2; a time-cap exit fires at its deadline and lands 2 slots (800 ms) later. The 400 ms poll is not modeled beyond that fixed lag: the trigger is read from every print, so a real poll adds up to one more slot of delay on average.

## Tries

Variants tried: 7 (of 8 requested; 1 skipped, listed below). Also run: nested-LODO selection over the same variants (one selection procedure, not a variant). Tries-log lines written this run: 7.

Skipped requested variants:
- `breakeven_after_tp25` (breakeven): not implemented in tools/exploration_exits.py

## Optimism gap

Best variant by pooled pressure increment: `timecap_5m_tp50_sl30`.

| | flat | pressure |
| --- | --- | --- |
| best variant, pooled paired increment vs tp50_sl30 | 0.00038 | 0.00024 |
| nested leave-one-day-out estimate | 0.00008 | 0.00000 |
| optimism gap (pooled minus nested) | 0.00029 | 0.00023 |

## Rows and censoring

n_oof 8801, OOF-selected expected 881.

| variant | rows | selected |
| --- | --- | --- |
| `timecap_10m_tp50_sl30` | 8801 | 881 |
| `timecap_5m_tp50_sl30` | 8801 | 881 |
| `tpsl_tp40_sl30` | 8801 | 881 |
| `tpsl_tp50_sl20` | 8801 | 881 |
| `tpsl_tp50_sl30` | 8801 | 881 |
| `tpsl_tp50_sl40` | 8801 | 881 |
| `tpsl_tp75_sl30` | 8801 | 881 |
