# CAP-PICK T2: result of the full run (rule B90 vs the 300 s cap)

Written 2026-10-09 (UTC), after reading the outputs of MiScusi job #391 (code `f9bdfbb`, PR #471). **Report-only exploration. Not gate evidence. It does not change EXP-022 and uses none of its counted data.** The rule, the kill rule and the replays are the ones in [the pre-declaration](cap-pick-t2-boost-exit-predeclare-2026-10-08.md) (sections 1 to 4, amended by section 7, erratum in section 6). Nothing was added, retuned or re-scoped after the read. No new cuts.

Outputs: `/data/mal/cap-pick-score/t2-full/{none,shift-0.2,shift-0.4,cut-0.8,cut-0.6}/{summary.json,rows.csv,run.log}`. Every config ran 36 UTC days and 24,272 attempts, with 3,055 picks (d08 `cache_table.csv`, threshold 0.8030766588450794); P2-P4 is 2,340 pick attempts. Numbers below are copied from `summary.json` (`paired.books`, `books`, `t2.counts`) and, for the date-concentration line, from `rows.csv`.

## Verdict per the pre-declaration: KEEP B90 (not killed)

Kill rule (sections 4 and 7), picks, P2-P4, on both binding legs (flat 15%, pressure at slope scale 1):

| Condition | Required | Measured | Met |
| --- | --- | --- | --- |
| (i) today's timing: paired date-cluster CI90 lower bound | > -0.25 pp | flat -0.0280, press -0.0221 | yes |
| (ii) shift 0.2: paired mean | > 0 | flat +0.3627, press +0.3185 | yes |
| (ii) shift 0.4: paired mean | > 0 | flat +1.6586, press +1.4462 | yes |

So the pre-declared rule does not close B90. That is all it says. B90 stays a **report-only** candidate exit. It never decides a gate on its own, and using it on walk 2 would need a pre-declared report-only amendment before counting starts. The budget cuts are report-only (section 7) and do not enter the rule.

## Key table: B90 minus cap, pp of stake per attempt, picks, P2-P4 (n 2,340 attempts)

Date-cluster CI90: 1,000 UTC-date resamples, seed 1, 5th and 95th percentile. Dates = B90 better / worse / total (27 dates).

| Config | Leg | Mean | CI90 | Dates | B90 fires | Attempts that differ |
| --- | --- | ---: | --- | --- | ---: | ---: |
| today (none) | flat | +0.0271 | [-0.0280, +0.1034] | 5 / 4 / 27 | 13 | 12 |
| today (none) | press | +0.0213 | [-0.0221, +0.0783] | 5 / 4 / 27 | 13 | 12 |
| shift 0.2 | flat | +0.3627 | [+0.1580, +0.5874] | 19 / 8 / 27 | 398 | 398 |
| shift 0.2 | press | +0.3185 | [+0.1259, +0.5224] | 18 / 9 / 27 | 398 | 398 |
| shift 0.4 | flat | +1.6586 | [+1.2483, +2.1340] | 26 / 1 / 27 | 495 | 495 |
| shift 0.4 | press | +1.4462 | [+1.0687, +1.8828] | 26 / 1 / 27 | 495 | 495 |
| cut 0.8 (report-only) | flat, press | +0.0000 | [+0.0000, +0.0000] | 0 / 0 / 27 | 0 | 0 |
| cut 0.6 (report-only) | flat, press | +0.0000 | [+0.0000, +0.0000] | 0 / 0 / 27 | 0 | 0 |

By block, flat / press mean (CI90 lower bound is in `summary.json`):

| Config | explore-0814 | fresh-0903 | exp011-0909 |
| --- | --- | --- | --- |
| today | -0.0222 / -0.0166 | +0.0000 / +0.0000 | +0.2345 / +0.1812 |
| shift 0.2 | +0.2578 / +0.2107 | +0.3454 / +0.2841 | +0.7601 / +0.7450 |
| shift 0.4 | +1.4193 / +1.2522 | +1.5067 / +1.3016 | +2.6891 / +2.3054 |

Full book (every attempt), flat / press mean [CI90]:

| Config | P2-P4 (n 19,234) | P1 (n 5,038) |
| --- | --- | --- |
| today | +0.0156 [+0.0030, +0.0313] / +0.0135 [+0.0020, +0.0282] | +0.0468 [-0.0069, +0.1069] / +0.0395 [-0.0065, +0.0924] |
| shift 0.2 | +0.1362 [+0.0825, +0.1940] / +0.1257 [+0.0782, +0.1758] | +0.1478 [+0.1189, +0.1826] / +0.1402 [+0.1143, +0.1713] |
| shift 0.4 | +0.5212 [+0.4282, +0.6163] / +0.4692 [+0.3872, +0.5516] | +0.4701 [+0.3679, +0.5780] / +0.4534 [+0.3688, +0.5470] |

No P1 pick cell is written (the cache scores on P1 are in-sample).

## How to read it, and what it does not show

- **Today's timing is almost vacuous.** B90 fires on 13 of 2,340 pick attempts (56 of 24,272 in the full book), as the pre-declaration expected: the 90% trigger comes after the 300 s cap on most fills. On the picks the flat total is +0.31662 SOL, and one date (2026-09-13) carries +0.32346 of it; without that date the total is -0.00683 SOL. Condition (i) passes because the difference is small, not because B90 adds anything today.
- **Shifted replays are not concentrated in one date.** Flat total, picks, P2-P4: shift 0.2 is +4.24352 SOL (best date 2026-09-14, +0.68278; without it +3.56074; best three dates are 0.452 of the total). Shift 0.4 is +19.40567 SOL (best date 2026-08-25, +2.51910; without it +16.88657; best three are 0.325). Positive dates: 19 of 27 and 26 of 27. All three blocks are positive on the mean in both shifts. At shift 0.2 the block CI90 lower bound is below 0 on fresh-0903 (flat -0.1553, press -0.1335), and press on explore-0814 (-0.0150); the rule reads P2-P4 only.
- **The shifted worlds are counterfactual re-simulations, not tape.** Same keeper amounts and budget, prints moved earlier (707,751 prints over 24,217 mints). Other traders keep their slots and do not react. The shift touches the entry on 9,846 attempts at 0.2 and 14,407 at 0.4 (of 24,272), so the entry is re-simulated there; fills are 10,255 and 10,191 against 10,370 today. The cap book itself moves between worlds (picks P2-P4 flat mean per attempt: +3.298 today, +3.262 at 0.2, +3.488 at 0.4), so read the paired columns, which are on the same shifted path. The approximations are listed in pre-declaration sections 3 and 7.
- The result says B90 is not worse than the cap today and is better than the cap if BOOST's schedule runs 20% or 40% faster, in this simulation. It does not say pump.fun will do that, and it does not measure live exit lag, fills or slippage beyond the scorer's P_primary defaults.
- Disclosed in section 7 and unchanged: the A3 halt `boost_last_slice_early` would catch a -20% shift at the next daily run, so the insurance covers at most the gap of about 24 h; REPORT section T2 values it at about 0.15 SOL [inferred].

## Did the cut configs behave as designed? Yes

The same fill count as `none` (10,370) is expected, and the cut did bite.

- **Why fills do not move.** The guard and the entry are decided at the landing state, s0 + k with k of 3 to 6 slots. The cut removes only keeper buys past f x 17.585 SOL, which are hundreds of slots later. The entry state is untouched. Against `none`, for all 24,272 attempts: `status`, `exec_ratio` and `landing_slot` are identical in both cut runs. (The shifts, by contrast, move prints to before the landing slot, so their fills do change.)
- **That it bit.** The detected keeper was cut on 24,217 mints in both runs: 147,172 prints removed at f = 0.8 and 287,873 at f = 0.6. The no-fail P&L differs from `none` on 1,307 attempts (f = 0.8) and 1,915 (f = 0.6); the exit type changes on 15 and 138.
- **B90 equals the cap exactly there.** Zero B90 fires and an exactly zero paired difference in every scope, as pre-declaration section 3 says for f < 0.9. The detector found some wallet on 415 cut paths (erratum, section 6); none fired before the cap.
- **Exposure of the cap book to a shorter BOOST** (mean per attempt, % of stake, flat / press; re-simulation, no reaction by other traders):

| Config | picks P2-P4 | all P2-P4 | all P1 |
| --- | --- | --- | --- |
| today | +3.298 / +2.820 | +0.786 / +0.730 | -0.264 / -0.276 |
| cut 0.8 | +2.910 / +2.468 | +0.656 / +0.610 | -0.359 / -0.364 |
| cut 0.6 | +1.990 / +1.642 | +0.263 / +0.251 | -0.663 / -0.640 |

B90 does nothing for a budget cut, because its trigger is an absolute 90% of the budget. Only a shift (a faster schedule) makes it fire earlier.

## Not measured

Live behaviour; exit lag above 2 slots; a real change in BOOST; any day after 2026-09-25 or before 2026-08-14; the sensitivity of the shifted replays to the other-traders-do-not-react approximation.
