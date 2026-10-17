# Push 2026-10-16, night (manager12)

Records only. No sealed, forward, walk, shadow, canary, probe or October outcome file was opened to write this note. Every figure is copied from the source named in its section. Exploration evidence is not gate evidence. No book has cleared the promotion gate.

## (a) r4-b forward read tool: four PRs merged

Source: PRs #681, #683, #684, #685 on `vaanai/MAL`. Reviewer and quant-proof verdicts are comments on each PR.

- #681 (64231f4), E-d md5 decision-equivalence replay on explore-0814 (adapter plus features against l3): scoped PASS. MiScusi job #1082 at e504803; report `/data/mal/hunt-1008/push-1016/hunt6/r4b-equiv-d/full3/report.json`, sha256 `70ded6de33fa96a3ca55410d9b1ccd7567627eb80a006a5bd87bd2f0745c8898`. Non-tie rows: 833,253 of 950,729. The raw comparison FAILS only on the tie mints with a null `tx_index`, which is why the PASS is scoped to non-tie rows.
- #683 (ff2e398), Token-2022 extension allowlist `{metadataPointer, tokenMetadata}` from the 300-mint census: 295 Token-2022 mints with exactly that pair, 5 SPL, 0 with a freeze authority.
- #684 (aef7bea), stranded and report-only rent priced by the mint's token program: 2,074,080 lamports for Token-2022 or unknown, 2,039,280 for SPL. The chain rent-exempt minimum on 2026-10-16 is 1,513,840 for 170 bytes and 1,488,440 for 165 bytes, so both constants are conservative and were kept. 6 Helius RPC calls.
- #685 (65d95c4), EXP-027 window-record tamper guard, exact tries key, repo-root pins, and a `--check-window` dry run.

## (b) EXP-027 (#675, draft): status

Source: PR #675, the EXP-027 file, `QP-RULING-1016.md`.

- Text quant-proof verdict "TEXT READY" at 3d3406a. Still pending: the owner's E1 answer (MiScusi question q_Z3dGvhO1wJ87wg), the DEC-030 owner line (q__jVaqpxB937kEg), and the sha256 pin of `r4b_read.py`.
- Merge window 2026-10-18T02:00Z to 2026-10-26T23:59Z.
- QP power, synthetic: P(full pass) .2535 for k = 1, .1660 for k = 2, at the 37.9% effect, N = 20, pinned stack (no rent, auto-stranded, 505k sends).

## (c) Walk 2: job #1036 extended

Source: MiScusi job #1036 (extension on 2026-10-16 at about 22:59Z).

- Extended to the 14-day maximum; it ends about 2026-10-29T19:20Z.
- Helius credits: 217,548 of 11,825,000 through 2026-10-16T21.
- Successor job(s) are needed for anything past the maximum. None is submitted yet.

## (d) Dead hunts

### drain-escape (hunt4)

Source: `/data/mal/hunt-1008/push-1016/hunt4/drain-escape/REPORT.md` (exploration tape, frozen DECLARE, one return run plus one logged diagnostic).

- Universe: 24,007 mints, 23,903 entered, 36 UTC days, 5 blocks. Six exit-on-drain rules, each on a primary (+5 slots) and a fast (+1 slot) leg, flat-15% fail model only; pressure-fail not run.
- Baseline (no trigger): -24.09% per attempt at 0.25 SOL, total -1,439.7 SOL, ex-top-3 -1,503.3, 0 positive days.
- Best cell R4 fast: -5.11%, total -305.7 SOL, positive on 3 of 36 days. R4 primary: -7.91%, total -472.7, ex-top-3 -494.3, ex-best-day -476.5. All 12 rule/leg cells are negative in total, ex-top-3 and ex-best-day.
- Per block, R4 fast: positive only in oracle-insample-0922 (+3.0%, n 2,636); the other four blocks are -4.7% to -7.1%. This is the best of 12 cells after the fact, one block of five, flat-15 only; it is not evidence of an edge.
- Drain trades (hindsight label) are 9,809 (41.0%), average -93.3% held. Rules fire on 7.5-42.6% of non-drain trades (R3 7.5%; the other five 20.3-42.6%); non-drain trades average +16.9% held (success-only, hindsight label; median -0.70%); a false exit costs 19.5 to 29.9 points.
- Ceiling with perfect foresight (hindsight drain label; not a tradable rule): +7.66% per attempt. Cost floor alone: about -2.4%.
- Not checked: pressure-fail, failed exit sells, entry latency, LP events, tuning.

### day-sigma (hunt7)

Source: `/data/mal/hunt-1008/push-1016/hunt7/day-sigma/REPORT.md` (exploration tape, frozen DECLARE, 35 UTC days, 5 blocks, 157,011 universe decisions).

- Six rules, flat-15 mean at 0.25 SOL (n; sigma_d; day t; days positive; total SOL; ex-top-3): R1a -7.413% (7,414; 3.048 pp; -14.32; 1/35; -137.399; -143.776), R1b -11.976% (7,374; 5.799; -11.72; 2/35; -220.779; -226.395), R1c -12.873% (5,357; 8.334; -8.63; 1/35; -172.396; -184.737), R2a -5.390% (7,410; 1.618; -19.88; 0/35; -99.844; -102.351), R2b -13.079% (7,188; 5.129; -14.71; 0/35; -235.035; -239.990), R2c -16.025% (7,333; 6.535; -13.87; 1/35; -293.780; -309.723).
- Control (random 20% of eligible pool-hours, 29,730 trades): -5.187% at 0.25 SOL, SD 99.23, sigma_d 4.291, 1/35 days positive. R2a is 0.2 pp below it (-5.390% vs -5.187%); no selector beats the control.
- All 30 rule-block cells are negative. The best sensitivity cell for any rule is R2a at -3.904% (flat 0.5% round trip); none crosses zero.
- Reading: low between-day dispersion appeared only on losing rules; the mean binds, not sigma_d. No second mechanism for the gate.
- Not checked: other depth, hold or top-N (frozen), non-canonical pools, cheap-fee universe, intra-hour exits, October data, sealed blocks.

Library rows: day-sigma-1, -2, -3 added; drain-escape-1 to -3 were already present.

## (e) Fee-arm probe watch

Source: read-only MiScusi job #1083, 2026-10-16 23:16Z.

- FEEARM_WATCH OK. Control: 6 buys landed of 6. Test: 7 of 7. 0 expired, 0 failed, 0 sells stuck. No alerts.
- No P&L or cost figure is reported here. This is a landing and health check only.

No book has cleared the promotion gate.
