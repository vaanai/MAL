# Push 2026-10-16, evening (manager12)

Records only. No sealed, forward, walk, shadow, canary or October outcome file was opened to write this note. Every figure is copied from the source named in its section. Exploration evidence is not gate evidence. No book has cleared the promotion gate.

## (a) h5-tail: as-of features do not predict H5's worst trades

Source: `/data/mal/hunt-1008/push-1016/hunt5/h5-tail/REPORT.md` (exploration tape, entries of H5 v1, cell F 0.25 SOL at the RULES floor).

- Of 177 TRAIN trades with success-case net <= -50% (10.4% of 1,699), 12 coincide with a row-level pool drain in the hold. TEST: 83 tail trades (7.4% of 1,119), 2 with a drain.
- The 17 as-of features give TEST AUC 0.584 (TRAIN in-sample 0.731).
- The frozen filter (q = 30%, the edge of the grid) lowers per-trade SD from 100.7 to 91.8 pp and moves the mean from +10.59% to +11.49%. The n for CI90 lo > 0 falls from 244.7 to 172.9 trades (1.42x); cells C 0.10 and C 0.30: 1.45x and 1.48x.
- Days needed barely move: F 4.59 to 4.64 tape-days at 53.3 trades/day, because the filter also removes 30% of trades. A random skip of the same share gives 245.6 against 244.7 trades.
- Per block at q = 0, F: +25.35%, +8.13%, +2.18%, +4.13%. The mean falls block by block.
- Not checked: no second feature set, no tree model, no q above 30, no October data.

## (b) h5-exit-sd: no exit structure shortens days-to-gate by 2x

Source: `/data/mal/hunt-1008/push-1016/hunt5/h5-exit-sd/REPORT.md` (same entries; variants frozen before any return).

- Best is V4 (sell half at +30%): TEST days-to-gate 12.09 against 13.68 for v1 (1.13x), mean +7.47% against +10.47%. Per-trade SD falls 100.7 to 59.4 pp.
- V2 (hard exit 90 s): mean +6.81%, days-to-gate 19.54 (0.70x). V3 (+30% / -20%): mean +1.92%, 20.76 days (0.66x). V1 (BOOST-slice exit): +10.73%, 13.52 days (1.01x). V5 equals v1.
- The day-level t binds, not the trade-level CI: R0 needs 4.69 days for the CI form and 13.68 days for t > 2.24. sigma_d is 20.35 pp (R0), of which about 15.0 pp is between-day dispersion that cutting trade SD cannot remove.
- Test blocks run +25.35% down to +3.71% (R0). October's mean is below the September average on this tape, so real days-to-gate are longer than any figure here.
- Not checked: one threshold value per variant (no-retune rule), no TP/SL grid, no October data.

## (c) Haiku trader, September walk of frozen v1: FAIL

Source: `/data/mal/hunt-1008/haiku-trader-v2/stage1/sep/RESULT.md`. The walk ran once under its DECLARE; it was not re-run.

- Scope: fresh-0903 and exp011-0909 only (9 UTC days, 162-card e60 subsample, 118 pools). The other two blocks have no print files and are not covered.
- Total without top 3, SOL (flat / pressure): R1 +0.2726 / +0.2172; v1 r1 +0.2946 / +0.2358; r2 +0.2505 / +0.1988; r3 (median) +0.2539 / +0.2018; random p80 +0.2438 / +0.1964. The median replicate is above 0 and above random p80 on both legs, and BELOW R1 on both legs. Declared rule FAILS.
- September margin of v1 over R1: -0.0187, against +0.0958 on the seen August data (v1 was the best of 2 tuned August walks). Uncapped buy-all beats every ranked arm (ex-top-3 +0.4727 flat).
- Null scores 11 / 8 / 7 of 162. Cost 0.0324203 USD. Verdict: FAIL. No Stage 2 on this basis. No library row was supplied for this walk.

## (d) launchpad-landscape: web only

Source: `/data/mal/hunt-1008/push-1016/hunt5/launchpad-landscape/REPORT.md` (read 2026-10-16; mostly secondary sources, not verified on chain).

- 2026 launch and graduation counts were found only for Pump.fun. No 2026 share or count was found for LetsBonk, LaunchLab, Meteora DBC, Believe, Moonshot, Boop, Bags, Jupiter Studio or Heaven.
- Pump.fun is the only venue with a 2026 protocol-funded buy found (BOOST, July 2026; size and start date conflict between sources) and a public IDL and event set.
- Raydium LaunchLab changed on 2026-08-17 (CPMM-only graduation, platform and creator LP locked in a Fee Key NFT, the rest burned; official docs). Meteora DBC has official thresholds (10 SOL for wSOL) and an IDL; its 2026 share was not found.
- The ranking in the report is about data access and latency fit. No venue is shown to make money.

## (e) EXP-026 power desk (quant-proof's synthetic simulation)

Quant-proof's analysis, copied from the MiScusi notebook text. It is a synthetic simulation, not a measurement on any tape.

EXP-026's stop E = first hour with ≥100 trades on ≥5 dates (336 h is only the cap, EXP-026:106), so it reads at ~n 100; P(pass) ~.03 at +4%, ~.05–.06 at +8%, .07–.10 at +11%, .14–.18 at +16% (upper bounds, SD 100 pp); fixed 28 d .05–.09/.13–.24/.21–.42/.42–.74; stop at ≥250 trades on ≥7 dates (cap 35 d) .05–.06/.13–.15/.21–.25/.40–.49; days matter more than trades (day-level t binds); plan on +2 to +5%; a re-file needs a new file and hours, a new slot (DEC-029 amendment with the owner's line, or a fifth-slot DEC) and a new seal exception; QP: propose only if G0 shows unsealed pools and E1 is redefined in the same filing.

## (f) Fee-arm probe: live, but no buy is possible

- The probe has been live at db4af222 since about 18:20Z. It can make 0 buys, because the intents carry no `migration_slot` (read-only jobs #1072 and #1073: 1,388 of 1,388 rows).
- The fix, PR #669, is merged as d29ae53 (Reviewer OK 6222376763, QP OK 6222380881). The entry-k guard is off for the feearm profile.
- A reinstall request to the owner is open (question q_RA-Txtw4ncevOA). Nothing is reinstalled until the owner answers.
- QP's wording nit: "about 12 slots" is measured from the intent write, not from the migration.

## (g) October D1 layer

- Job #1067 refused on C2. Root cause and fix are in PR #670, with QP's ruling in its comment 6222506181: the old C2 (completes with no migrate position at most 0.5% of `mints_with_complete`) fails by construction on this layout (about 16,379 completes against 52 migration rows). QP retired it, on a structure count and not on an outcome, and declared C2' and C2'' in its place (integer arithmetic, 200x, computed before R4).
- Blocking items B1 (C2' and C2'' with refusal tests) and B2 (a complete row without an integer `event_ts` must refuse) must be fixed and QP must OK the new head before the precount reruns. QP re-checked at 1764657 (comment 6222603050) and found one more blocking item: C2'' missed a complete row that sits EARLIER than the trade-detected complete (the harmful case), so precount #1074 (started at that head) was cancelled; the fix is with the builder, and the precount reruns only after QP OK on the fixed head. The manager then checks C1, C2', C2'', C3 and gates (a) to (c) in PRECOUNT.json before any full run. D1 is exploration only.
- The same ruling records a low-severity breach: a diagnostic scan read row types and mint counts from two out-of-window walk-2 hours. Disclosure: HOLDOUT_LEDGER changelog of 2026-10-16, the Forward walk 2 Status cell, and the EXP-025 "Breach disclosure (2026-10-16T18:56Z)" amendment. No read is compromised.

## What it means (manager's reading)

- No exit or tail filter makes H5 certifiable fast.
- The day-level t's between-day dispersion sets about 13–14 days even at September's mean and rate.
- The Haiku ranker shows no lift on September.
- There is no case for a second venue's tape.

No book has cleared the promotion gate.
