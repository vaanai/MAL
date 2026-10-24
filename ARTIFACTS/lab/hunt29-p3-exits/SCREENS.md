# P3 EXITS screens (hunt29, 2026-10-24)

Exploration screen numbers on mined, in-sample data (553 h, the four blocks in BLOCKS.md). Not gate evidence; no sentence here says a book makes money. Declaration: DECLARE.md (sha256 in DECLARE.sha256, first taken 2026-10-24T00:20:17Z, retaken 00:20:24Z after a count-sentence wording fix, before any code ran on data). Kit tools/arena_screen.py (byte-identical to 2835eb45), send = 160,000 lamports through run_p3.py, exit dispatcher p3exits.py (unit-tested on synthetic tapes: tp equals tpfix, time-only and sl/trail equal the kit, truncation does not move an exit). Jobs on mal-research-0: smoke #1423 (count-only, 54 h), priced #1426 (34 cells, 20 min, exit 0). Deciding leg 1.9 s, 0.25 SOL, rent 0, strand rule total, flat 15% and pressure slope 1, trade-level CI90 lo (1,000 draws, seed 1). The control F0 reproduces hunt28 l3 `l1` to the digit (+0.01605 flat / +0.01285 press), so the wrapper is faithful.

Multiplicity: 27 declared new variants (12 FLOOR, 3 G4, 12 REVIVE), 34 kit cells, all priced, none dropped or re-tuned. No counts-only gate step was applied (the priced run reports attempts; every cell has >= 2 attempts/day).

## Result in words
- NO cell is BEATS-BASELINE (mean AND ex-top-3 above the family control under both fail models) and none is a SCREEN-LEAD.
- FLOOR v2: the armed trails TR50 / TR100 / TR30 have a slightly higher mean than F0 under both fail models (TR50 +0.01675 / +0.01347 vs +0.01605 / +0.01285 SOL per trade, +0.0007 and +0.0006), but a LOWER ex-top-3 (+4.12 / +3.26 vs +4.40 / +3.49) and a CI90 lo that overlaps F0's. That is noise-sized and not a beat under the declared label. Every other exit is worse than the plain TP +50% / 24 h: shorter caps (TPH6, TPH12, T6, T12), time-only (T24), burst sells (B2, B5, BR) and the partial take-profit P50 (+0.01283 / +0.01026) all lose mean and ex-top-3. Selling into bursts cuts the 24 h tail that carries the line. Time-only 24 h (PB) is still positive (+0.01125 / +0.00905): the entry, not the TP, carries the FLOOR line.
- P50 (X3) caveat: 0.5 x PA + 0.5 x PB minus one extra send on TP hits, inner join (325 of 329 / 325 trades); the second leg is priced against the pool without our first sell (slightly optimistic); it still loses to F0. exit-upgrade-3 is therefore run for FLOOR and G4 (kit form), not for the original hunt3 entry list.
- G4 v1 kit form (new entry code, see DECLARE for the disclosed differences from the registered port; entry equivalence NOT measured): baseline G0 has a positive mean (+0.00448 / +0.00334) but ex-top-3 negative (-0.43 / -0.56 SOL), CI90 lo negative, 12 of 20 days positive. GA (+0.00433), GP50, GB, GB2, GBR (X4, -0.00404 / -0.00497) are all worse. None improves the baseline.
- REVIVE: every time-only hold (30 min, 1 h, 4 h) is more negative than the L4 exit under both fail models. The wide stop W (sl 0.70, trail 0.65) improves the mean (r12h +0.00470 / +0.00405 vs -0.00276 / -0.00236; r6h about 0; r2h -0.00224 vs -0.00291) but ex-top-3 stays negative (-2.65 / -2.21 for r12h, worse than RC's -2.25 / -1.92), 5 of 23 days positive: a few trades, not a line. Dead as an exit change.
- H2-PS: NOT RUN. Only the streaming arena rule tools/arena_rule_h2ps.py exists; no kit-form rule, and its exit is a followed wallet's sell, which the kit's ExitView (no trader column) cannot express.
- Kit KILL is true on every cell (FLOOR excluded block-edge share 22% > 5%, worst-in-slot for event entries); P50 / GP50 are derived and carry no kit verdict.

## Ranked table (sorted by pressure mean; 0.25 SOL; trades/day over the days of the run; SOL totals over the pooled four blocks; impact in % at 0.25 / 1.0 SOL, the 0.5 SOL value is the linear interpolation)
| cell | trades/day | flat SOL/tr | press SOL/tr | flat/day | press/day | ex-top3 f/p | drop-best-day f/p | days+ f/p | CI90 lo f/p | beats base | lead | impact .25/1.0 | kill |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TR50 | 13.2 | +0.01675 | +0.01347 | +0.220 | +0.177 | +4.12 / +3.26 | +4.41 / +3.51 | 16/17 / 16/17 | +0.01105 / +0.00863 | False | False | 1.37 / 5.48 | True |
| TR100 | 13.0 | +0.01637 | +0.01315 | +0.214 | +0.171 | +3.94 / +3.11 | +4.24 / +3.36 | 16/17 / 16/17 | +0.01069 / +0.00825 | False | False | 1.37 / 5.48 | True |
| TR30 | 13.4 | +0.01610 | +0.01295 | +0.216 | +0.174 | +4.01 / +3.18 | +4.31 / +3.43 | 17/19 / 17/19 | +0.01072 / +0.00833 | False | False | 1.37 / 5.48 | True |
| F0 | 13.3 | +0.01605 | +0.01285 | +0.213 | +0.171 | +4.40 / +3.49 | +4.41 / +3.49 | 18/19 / 18/19 | +0.01099 / +0.00851 | False | False | 1.37 / 5.48 | True |
| PA | 13.2 | +0.01579 | +0.01263 | +0.208 | +0.166 | +4.27 / +3.38 | +4.42 / +3.50 | 18/19 / 18/19 | +0.01129 / +0.00861 | False | False | 1.37 / 5.47 | True |
| P50 | 13.0 | +0.01283 | +0.01026 | +0.167 | +0.133 | +3.58 / +2.83 | +3.38 / +2.67 | 16/17 / 16/17 | +0.00881 / +0.00677 | False | False | nan / nan | None |
| BR | 14.4 | +0.01251 | +0.00944 | +0.181 | +0.136 | +3.59 / +2.63 | +3.70 / +2.72 | 17/21 / 17/21 | +0.00723 / +0.00464 | False | False | 1.37 / 5.47 | True |
| T24 | 13.0 | +0.01125 | +0.00905 | +0.146 | +0.118 | +2.91 / +2.31 | +2.86 / +2.27 | 16/17 / 15/17 | +0.00705 / +0.00536 | False | False | 1.37 / 5.48 | True |
| PB | 13.0 | +0.01125 | +0.00905 | +0.146 | +0.118 | +2.91 / +2.31 | +2.86 / +2.27 | 16/17 / 15/17 | +0.00705 / +0.00536 | False | False | 1.37 / 5.48 | True |
| TPH12 | 15.1 | +0.01141 | +0.00891 | +0.172 | +0.134 | +3.58 / +2.76 | +3.39 / +2.59 | 16/20 / 15/20 | +0.00736 / +0.00532 | False | False | 1.37 / 5.48 | True |
| T12 | 15.0 | +0.01082 | +0.00865 | +0.162 | +0.129 | +2.67 / +2.07 | +3.14 / +2.47 | 16/19 / 16/19 | +0.00583 / +0.00431 | False | False | 1.37 / 5.48 | True |
| B5 | 14.3 | +0.00933 | +0.00743 | +0.133 | +0.106 | +2.50 / +1.96 | +2.67 / +2.10 | 14/20 / 14/20 | +0.00537 / +0.00386 | False | False | 1.37 / 5.47 | True |
| TPH6 | 16.3 | +0.00954 | +0.00738 | +0.155 | +0.120 | +3.16 / +2.41 | +2.98 / +2.25 | 15/20 / 15/20 | +0.00584 / +0.00406 | False | False | 1.37 / 5.48 | True |
| T6 | 16.2 | +0.00862 | +0.00672 | +0.140 | +0.109 | +2.15 / +1.59 | +2.33 / +1.75 | 14/20 / 14/20 | +0.00388 / +0.00259 | False | False | 1.37 / 5.48 | True |
| B2 | 15.3 | +0.00626 | +0.00442 | +0.096 | +0.068 | +1.74 / +1.14 | +1.67 / +1.08 | 13/20 / 12/20 | +0.00186 / +0.00039 | False | False | 1.37 / 5.48 | True |
| W_r12h | 15.8 | +0.00470 | +0.00405 | +0.074 | +0.064 | -2.65 / -2.21 | -2.23 / -1.87 | 5/23 / 5/23 | -0.00666 / -0.00555 | False | False | 0.67 / 2.69 | True |
| G0 | 19.1 | +0.00448 | +0.00334 | +0.086 | +0.064 | -0.43 / -0.56 | +0.37 / +0.15 | 12/20 / 12/20 | -0.00193 / -0.00221 | False | False | 1.29 / 5.16 | True |
| GA | 18.9 | +0.00433 | +0.00321 | +0.082 | +0.061 | -0.52 / -0.64 | +0.30 / +0.09 | 11/20 / 11/20 | -0.00225 / -0.00239 | False | False | 1.29 / 5.17 | True |
| GP50 | 18.8 | +0.00184 | +0.00116 | +0.034 | +0.022 | -0.77 / -0.83 | +0.02 / -0.12 | 5/17 / 5/17 | -0.00286 / -0.00294 | False | False | nan / nan | None |
| W_r6h | 32.4 | +0.00005 | +0.00010 | +0.001 | +0.003 | -4.65 / -3.87 | -3.79 / -3.15 | 7/23 / 7/23 | -0.00606 / -0.00505 | False | False | 0.64 / 2.57 | True |
| GB | 18.8 | +0.00022 | -0.00015 | +0.004 | -0.003 | -1.34 / -1.28 | -0.65 / -0.70 | 6/17 / 5/17 | -0.00416 / -0.00395 | False | False | 1.29 / 5.17 | True |
| H240_r12h | 15.5 | -0.00163 | -0.00125 | -0.025 | -0.019 | -4.58 / -3.82 | -3.37 / -2.82 | 6/22 / 6/22 | -0.01204 / -0.00997 | False | False | 0.67 / 2.70 | True |
| GB2 | 20.0 | -0.00039 | -0.00146 | -0.008 | -0.029 | -2.77 / -2.89 | -2.24 / -2.40 | 10/21 / 10/21 | -0.00713 / -0.00740 | False | False | 1.29 / 5.16 | True |
| W_r2h | 97.0 | -0.00224 | -0.00189 | -0.217 | -0.183 | -10.25 / -8.65 | -9.10 / -7.69 | 6/24 / 6/24 | -0.00468 / -0.00394 | False | False | 0.60 / 2.41 | True |
| H30_r12h | 16.0 | -0.00251 | -0.00199 | -0.040 | -0.032 | -3.65 / -3.04 | -2.69 / -2.23 | 5/23 / 5/23 | -0.00906 / -0.00750 | False | False | 0.67 / 2.69 | True |
| RC_r12h | 15.9 | -0.00276 | -0.00236 | -0.044 | -0.037 | -2.25 / -1.92 | -1.64 / -1.40 | 6/23 / 6/23 | -0.00629 / -0.00534 | False | False | 0.67 / 2.69 | True |
| RC_r2h | 97.3 | -0.00291 | -0.00250 | -0.283 | -0.243 | -8.99 / -7.69 | -8.00 / -6.86 | 6/24 / 6/24 | -0.00427 / -0.00364 | False | False | 0.60 / 2.41 | True |
| RC_r6h | 32.6 | -0.00367 | -0.00311 | -0.120 | -0.101 | -4.54 / -3.84 | -3.57 / -3.01 | 6/23 / 6/23 | -0.00608 / -0.00510 | False | False | 0.64 / 2.57 | True |
| H30_r6h | 32.7 | -0.00426 | -0.00351 | -0.139 | -0.115 | -6.45 / -5.37 | -4.92 / -4.09 | 5/23 / 5/23 | -0.00791 / -0.00658 | False | False | 0.64 / 2.57 | True |
| H30_r2h | 97.5 | -0.00515 | -0.00435 | -0.502 | -0.424 | -15.51 / -13.11 | -13.28 / -11.24 | 3/24 / 3/24 | -0.00669 / -0.00565 | False | False | 0.60 / 2.41 | True |
| GBR | 20.8 | -0.00404 | -0.00497 | -0.084 | -0.103 | -4.52 / -4.61 | -3.88 / -3.99 | 6/21 / 6/21 | -0.01052 / -0.01095 | False | False | 1.29 / 5.16 | True |
| H60_r2h | 96.9 | -0.00597 | -0.00505 | -0.578 | -0.489 | -16.71 / -14.12 | -15.06 / -12.72 | 4/24 / 3/24 | -0.00752 / -0.00636 | False | False | 0.60 / 2.41 | True |
| H60_r12h | 15.8 | -0.00631 | -0.00521 | -0.100 | -0.082 | -4.15 / -3.46 | -3.03 / -2.52 | 5/23 / 5/23 | -0.01083 / -0.00900 | False | False | 0.67 / 2.69 | True |
| H240_r6h | 31.7 | -0.00667 | -0.00559 | -0.212 | -0.177 | -9.70 / -8.15 | -7.67 / -6.45 | 7/23 / 7/23 | -0.01254 / -0.01056 | False | False | 0.64 / 2.57 | True |
| H240_r2h | 93.2 | -0.00695 | -0.00582 | -0.647 | -0.542 | -21.54 / -18.06 | -19.27 / -16.14 | 4/24 / 4/24 | -0.00967 / -0.00810 | False | False | 0.60 / 2.41 | True |
| H60_r6h | 32.4 | -0.00692 | -0.00582 | -0.224 | -0.188 | -7.56 / -6.36 | -6.23 / -5.24 | 5/23 / 5/23 | -0.00965 / -0.00812 | False | False | 0.64 / 2.57 | True |


Files: out/priced/results.json (kit), rows.json, trades/*.npz (per-trade legs), ANALYSIS.md.
