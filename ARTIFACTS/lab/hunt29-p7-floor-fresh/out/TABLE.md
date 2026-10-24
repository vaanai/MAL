# arena_screen TABLE (l1_floor_option_v2): IN-SAMPLE exploration screen, not evidence, no profit claim

Deciding leg: 1.9 s, 0.25 SOL, rent 0 (DEC-031 Am.2 universe enforced), pool age >= 360 s enforced, feed delay 7. KILL (hunt23 Amendment 1) is evaluated on all four sub-cells {flat, pressure} x {sc, total}. Blocks: B1=fresh-0903.

**Author's job, not the kit's.** (1) Drop any cell that matches the C1-NF decision grid and exit (EXP-025 section 4, DEC-031 section 3.7) BEFORE pricing. (2) Drop any cell under 20 attempts/day on explore-0814 WITHOUT pricing (RULES step 2); the kit's explore-0814 attempts/day kill is a backstop, not a substitute. (3) Before ANY registration run `causal_probe` (on a copy of the state) on a seed-1 sample of at least 200 triggers PER BLOCK with 0 failures, and state the count in REPORT; the kit hands a rule the whole hour and cannot see look-ahead. (4) Declare per cell, before pricing, whether it is event-triggered (the default) or grid-triggered; an event cell is KILLED when the lower of its worst-in-slot and no-guard-rescue legs has a mean <= 0 in any of the four sub-cells (R7), and stays PROVISIONAL-BLOCKED if worst-in-slot could not be measured; a grid cell reports it only and needs it measured before any canary. (5) The kit's figures are not the bot's: it resolves exits by slot arithmetic at the hour's slot rate, the arena bot holds in ms on a 1 s tick with the engine's rolling clock, so the frozen bot's md5 decision-equivalence replay is still required at registration. (6) Selection: every figure is in-sample on blocks that were mined before; a survivor is the best of its declared cells, and the chance of at least one false screen pass is up to 1 - 0.95^N (QP ruling item 1). A survivor is a candidate for a forward slot and nothing more.

## 1. Strand rule total (deciding), pooled over all blocks

| cell | att | fills | days(+) | flat mean% | flat CIlo% | flat tot | ex3 | exbest | press mean% | press CIlo% | press tot | ex3 | exbest | explore-0814 alone flat/press mean% | September pooled flat/press mean% | block mean% flat/press | impact .25/1.0 % | 1.0 SOL flat/press mean% | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| l1 | 62 | 62 | 5(4) | 2.93 | -0.02 | 0.454 | 0.009 | 0.118 | 2.03 | -0.77 | 0.314 | -0.065 | 0.026 | na/na | na/na | B1:2.93/2.03 | 1.368/5.473 | 2.93/2.04 | KILL EVENT |

## 2. The four deciding sub-cells with the pooled in-sample gate columns

Each entry: mean% / CI90 LB% / dates(+) majority? / ex-top-3 SOL / ex-best-day SOL. Explore-alone and September-pooled entries: mean%.

| cell | total/flat | total/press | sc/flat | sc/press |
|---|---|---|---|---|
| l1 | 2.93 / -0.02 / 4/5 maj / 0.009 / 0.118 [expl na, sept na] | 2.03 / -0.77 / 4/5 maj / -0.065 / 0.026 [expl na, sept na] | 4.81 / 2.50 / 5/5 maj / 0.300 / 0.408 [expl na, sept na] | 3.86 / 1.65 / 5/5 maj / 0.220 / 0.311 [expl na, sept na] |

## 3. Reported legs (not deciding): mean% (total SOL), flat / press

| cell | 1.3 s total rent0 | 1.9 s total rent-charged | 1.3 s sc rent0 | strands 1.9 total | KILL reasons |
|---|---|---|---|---|---|
| l1 | 2.94 (0.456) / 1.96 (0.304) | 2.25 (0.348) / 1.45 (0.225) | 4.82 (0.746) / 3.80 (0.589) | 1 | total/flat.gate.n 62 < 100; total/flat.gate.ci_lo_trade<=0; total/flat.gate.ci_lo_date_clustered<=0; total/press.ex_top3<=0; total/press.gate.n 62 < 100; total/press.gate.ci_lo_trade<=0; total/press.gate.ci_lo_date_clustered<=0; sc/flat.gate.n 62 < 100; sc/press.gate.n 62 < 100; blocks_positive 0/4 < 3; explore-0814: not measured (no trips in this group); september: not measured (no trips in this group); excluded share 25.3% > 5%: not decidable in-sample |

## 4. Worst-in-slot (R7): mean SOL per trade at the deciding cell (1.9 s, 0.25 SOL, rent 0), END / worst-in-slot / no guard rescue / **LOWER of the two**

An EVENT cell is KILLED when the LOWER mean is <= 0 in any sub-cell; a GRID cell reports it only. `not measured` = the tape states did not reproduce the kit's END states (no figure is printed and an event cell stays PROVISIONAL-BLOCKED). CIlo = the lower of the two legs' trade-level CI90 bounds (arena_score's bootstrap). In-sample, no profit claim.

| cell | kind | total/flat | total/press | sc/flat | sc/press | sub-cells with lower mean <= 0 | guard flips sc/total |
|---|---|---|---|---|---|---|---|
| l1 | EVENT | 0.0073 / 0.0073 / 0.0073 / **0.0073** (CIlo -0.0032, worst_in_slot) | 0.0051 / 0.0051 / 0.0051 / **0.0051** (CIlo -0.0047, worst_in_slot) | 0.0120 / 0.0120 / 0.0120 / **0.0120** (CIlo 0.0045, worst_in_slot) | 0.0097 / 0.0097 / 0.0097 / **0.0097** (CIlo 0.0034, worst_in_slot) | - | 0/0 |
