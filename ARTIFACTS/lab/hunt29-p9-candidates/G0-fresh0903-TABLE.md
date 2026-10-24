# arena_screen TABLE (p9_g4_g0_v1): IN-SAMPLE exploration screen, not evidence, no profit claim

Deciding leg: 1.9 s, 0.25 SOL, rent 0 (DEC-031 Am.2 universe enforced), pool age >= 360 s enforced, feed delay 7. KILL (hunt23 Amendment 1) is evaluated on all four sub-cells {flat, pressure} x {sc, total}. Blocks: B1=fresh-0903.

**Author's job, not the kit's.** (1) Drop any cell that matches the C1-NF decision grid and exit (EXP-025 section 4, DEC-031 section 3.7) BEFORE pricing. (2) Drop any cell under 20 attempts/day on explore-0814 WITHOUT pricing (RULES step 2); the kit's explore-0814 attempts/day kill is a backstop, not a substitute. (3) Before ANY registration run `causal_probe` (on a copy of the state) on a seed-1 sample of at least 200 triggers PER BLOCK with 0 failures, and state the count in REPORT; the kit hands a rule the whole hour and cannot see look-ahead. (4) Declare per cell, before pricing, whether it is event-triggered (the default) or grid-triggered; an event cell is KILLED when the lower of its worst-in-slot and no-guard-rescue legs has a mean <= 0 in any of the four sub-cells (R7), and stays PROVISIONAL-BLOCKED if worst-in-slot could not be measured; a grid cell reports it only and needs it measured before any canary. (5) The kit's figures are not the bot's: it resolves exits by slot arithmetic at the hour's slot rate, the arena bot holds in ms on a 1 s tick with the engine's rolling clock, so the frozen bot's md5 decision-equivalence replay is still required at registration. (6) Selection: every figure is in-sample on blocks that were mined before; a survivor is the best of its declared cells, and the chance of at least one false screen pass is up to 1 - 0.95^N (QP ruling item 1). A survivor is a candidate for a forward slot and nothing more.

## 1. Strand rule total (deciding), pooled over all blocks

| cell | att | fills | days(+) | flat mean% | flat CIlo% | flat tot | ex3 | exbest | press mean% | press CIlo% | press tot | ex3 | exbest | explore-0814 alone flat/press mean% | September pooled flat/press mean% | block mean% flat/press | impact .25/1.0 % | 1.0 SOL flat/press mean% | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G0 | 79 | 77 | 5(3) | 0.55 | -3.02 | 0.109 | -0.212 | -0.296 | 0.24 | -2.94 | 0.048 | -0.221 | -0.291 | na/na | na/na | B1:0.55/0.24 | 1.310/5.238 | 0.55/0.25 | KILL EVENT |

## 2. The four deciding sub-cells with the pooled in-sample gate columns

Each entry: mean% / CI90 LB% / dates(+) majority? / ex-top-3 SOL / ex-best-day SOL. Explore-alone and September-pooled entries: mean%.

| cell | total/flat | total/press | sc/flat | sc/press |
|---|---|---|---|---|
| G0 | 0.55 / -3.02 / 3/5 maj / -0.212 / -0.296 [expl na, sept na] | 0.24 / -2.94 / 3/5 maj / -0.221 / -0.291 [expl na, sept na] | 2.03 / -1.08 / 4/5 maj / 0.080 / -0.004 [expl na, sept na] | 1.69 / -0.92 / 4/5 maj / 0.065 / -0.005 [expl na, sept na] |

## 3. Reported legs (not deciding): mean% (total SOL), flat / press

| cell | 1.3 s total rent0 | 1.9 s total rent-charged | 1.3 s sc rent0 | strands 1.9 total | KILL reasons |
|---|---|---|---|---|---|
| G0 | 0.55 (0.109) / 0.25 (0.048) | -0.12 (-0.023) / -0.31 (-0.062) | 2.03 (0.401) / 1.69 (0.334) | 1 | total/flat.ex_top3<=0; total/flat.ex_best_day<=0; total/flat.gate.n 79 < 100; total/flat.gate.ci_lo_trade<=0; total/flat.gate.ci_lo_date_clustered<=0; total/press.ex_top3<=0; total/press.ex_best_day<=0; total/press.gate.n 79 < 100; total/press.gate.ci_lo_trade<=0; total/press.gate.ci_lo_date_clustered<=0; sc/flat.ex_best_day<=0; sc/flat.gate.n 79 < 100; sc/flat.gate.ci_lo_trade<=0; sc/flat.gate.ci_lo_date_clustered<=0; sc/press.ex_best_day<=0; sc/press.gate.n 79 < 100; sc/press.gate.ci_lo_trade<=0; sc/press.gate.ci_lo_date_clustered<=0; blocks_positive 0/4 < 3; explore-0814: not measured (no trips in this group); september: not measured (no trips in this group); excluded share 30.7% > 5%: not decidable in-sample |

## 4. Worst-in-slot (R7): mean SOL per trade at the deciding cell (1.9 s, 0.25 SOL, rent 0), END / worst-in-slot / no guard rescue / **LOWER of the two**

An EVENT cell is KILLED when the LOWER mean is <= 0 in any sub-cell; a GRID cell reports it only. `not measured` = the tape states did not reproduce the kit's END states (no figure is printed and an event cell stays PROVISIONAL-BLOCKED). CIlo = the lower of the two legs' trade-level CI90 bounds (arena_score's bootstrap). In-sample, no profit claim.

| cell | kind | total/flat | total/press | sc/flat | sc/press | sub-cells with lower mean <= 0 | guard flips sc/total |
|---|---|---|---|---|---|---|---|
| G0 | EVENT | 0.0014 / 0.0014 / 0.0014 / **0.0014** (CIlo -0.0068, worst_in_slot) | 0.0006 / 0.0006 / 0.0006 / **0.0006** (CIlo -0.0071, worst_in_slot) | 0.0051 / 0.0051 / 0.0051 / **0.0051** (CIlo -0.0008, worst_in_slot) | 0.0042 / 0.0042 / 0.0042 / **0.0042** (CIlo -0.0007, worst_in_slot) | - | 0/0 |
