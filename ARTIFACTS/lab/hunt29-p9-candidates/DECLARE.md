# P9 FORWARD-CANDIDATES pre-declaration (hunt29, push 10-24). Written before any count or price of the one cell below.

Exploration only. Screen numbers, never gate evidence. No sentence here says a strategy makes money.

## Audit scope (desk, no pricing)
Every docs/library/ideas.jsonl row with status explored-survivor-unconfirmed or parked, plus the named long-horizon rows. For each: forward-test status (EXP/, DEC-031, docs/HANDOFF.md), engine (arena kit or other), last known 160,000-per-send numbers (hunt28 L3/L4, hunt29 P3/P4/P7), robustness score. Source files only; no shadow/ledger/bots.json/canary/probe output opened.

## Re-pricing rule
A row is re-priced ONLY if it has a kit-form rule AND no 160,000-per-send number. Result of the audit step: no row qualifies on the four exploration blocks (FLOOR v2 l1, G4 v1 G0, REVIVE, SELLABS, BREADTH, ACCUM, w2 family, hc_h60 all already priced at 160k in hunt28 L3/L4 and hunt29 P3/P4). H2-PS has no kit-form rule. r4-b / l3 xs10 / O2 / second-book-5 / r4b-depth-1 / haiku-v3 / boost-universe are older-engine or walk-2 / H5-arena lines, not kit-form.

## The one cell priced (stand-alone held-out read, labelled)
G4 v1 kit form, cell G0 (24 h hold, take profit +50%, no stop), exactly as hunt29 P3 (p3rules.py `_g4`, G0 spec), on **fresh-0903** [2026-09-03T12, 2026-09-09T12), 144 h, priced alone and whole, via the kit PR #848 branch (claude/kit-add-fresh-0903, head 6a94572b, UNREVIEWED). Reason: G4 is the only other kit-form line with a positive in-sample flat mean and a registration queue place ("after FLOOR"); FLOOR's own fresh-0903 read is hunt29 P7. Label: HELD-OUT-NEIGHBOUR (fresh-0903 lies in time between the four blocks, many prior readers listed in #848; the G4 rule family was found on neighbouring data). Not an independent test.
- Rule: p9rules.py = p3rules.P3Rule with the l1 universe module swapped for hunt29 p7 l1_rule_p7.py (the only change: fresh-0903 aux block key), cells() restricted to G0, trigger list filtered to G0. Entry, universe, exit, cooldown (60 s), as P3 G0. Aux table: hunt29/p7/work/aux_fresh0903.json (reused, built by P7 with the same reader code).
- Costs: 160,000 per send (DEC-031 Am.8), 1.9 s deciding leg, 0.25 SOL, rent 0 (Am.2), strand rule total, V band, E1 floor, 360 s pool-age seal, flat 15% and pressure fail models, kit defaults. Wrapper run_p3.py of P3 (send 160000 + p3exits dispatcher).
- Disclosed difference: the kit-form G4 is NOT the registered streaming port (g4v1-rule worktree); entry equivalence not measured.
- Output read, nothing else: trades/day, flat and pressure mean SOL/trade, ex-top-3 total, days+/days, drop-best-day total, CI90 lo (1,000 draws, seed 1), impact at 0.25 SOL, block-edge exclusion share. Next to P3 in-sample G0 (19.1/day, flat +0.00448, pressure +0.00334, ex-top-3 -0.43/-0.56, 12/20 days, CI lo -0.0019/-0.0022).
- Multiplicity: 1 new cell. In-sample G4 family was 5 cells (P3), G0 is the control not a pick. No variant is tried after a count.
- Counts-only first; if G0 is under 2 attempts/day it is reported thin and not priced.
