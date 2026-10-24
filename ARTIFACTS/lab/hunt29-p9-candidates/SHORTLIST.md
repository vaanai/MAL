# P9 FORWARD-CANDIDATES: SHORTLIST (hunt29, 2026-10-24)

Desk audit of docs/library/ideas.jsonl plus ONE new stand-alone held-out screen read (G4 v1 G0 on fresh-0903, job #1459, DECLARE sha256 74e884ae..., taken 2026-10-24T01:45Z before any count). Every figure is an in-sample exploration screen number except where labelled HELD-OUT-NEIGHBOUR; none is gate evidence. No sentence here says a strategy makes money; quant-proof decides. Kit PR #848 (head 6a94572b) is unreviewed.

## 1. New number: G4 v1 (kit-form G0: 24 h hold, TP +50%, no stop) on fresh-0903, 160,000/send, 1.9 s, 0.25 SOL, rent 0
| | n | /day | flat SOL/tr | press SOL/tr | ex-top-3 f/p (SOL) | days+ | CI90 lo f/p (SOL/tr) | drop-best-day f/p | block-edge excl. |
|---|---|---|---|---|---|---|---|---|---|
| in-sample 4 blocks (P3 G0) | ~380 | 19.1 | +0.00448 | +0.00334 | -0.43 / -0.56 | 12/20 | -0.0019 / -0.0022 | +0.37 / +0.15 | n/a |
| fresh-0903 (this read) | 79 | 11.3 | +0.00138 | +0.00061 | -0.21 / -0.22 | 3/5 | -0.0068 / -0.0071 | -0.30 / -0.29 | 30.7% |
Reading: mean positive but about 30% (flat) and 18% (pressure) of in-sample; ex-top-3 and drop-best-day negative; one day carries the total (top-day share 3.7x); n below 100; KILL true (worst-in-slot lower leg +0.0014 / +0.0006, CI lo negative; excluded share 30.7% over 5%, not decidable). The kit's printed "1.3 s" row key is the kit's label for the deciding leg here (section 1 deciding = same numbers). The kit-form G4 differs from the registered port (tools/arena_rule_g4v1.py, worktree g4v1-rule, PR #806); entry equivalence not measured. Files: out/priced/{TABLE.md,results.json}.

## 2. Ranked table (all audited rows; robustness score R = count of 6 checks met: [in-sample mean>0 both models at 160k] [ex-top-3>0 both] [held-out sign same] [days+ majority] [>=10 trades/day] [kit-form or arena-form rule exists])
| rank | row(s) | forward status | engine | last numbers at current cost | R | capital per trade (stake x hold) | verdict |
|---|---|---|---|---|---|---|---|
| 1 | FLOOR v2 (lib-c-L1, lib-b-C2/K8-R1, reprice-160k-l1) | REGISTERED, clock 10-26T00Z (slip 10-27), H=12 d | arena (arena_rule_l1) | in-sample 13.3/d, +0.0161/+0.0129, ex3 +4.41/+3.49, 18/19 d; fresh-0903 (P7) 62 tr, +0.0073/+0.0051, ex3 +0.009/-0.065, 4/5 d | 5 | 0.25 SOL x <=24 h (3.3 SOL open at 13/d) | already forward; no action |
| 2 | G4 v1 (lib-b-C1, lib-a-A6, G4.x) | NOT registered; queue "after FLOOR" (port PR #806, equivalence 12/13 on w7) | arena-form port exists; kit form priced | in-sample +0.0045/+0.0033, ex3 -0.43/-0.56, 12/20 d; fresh-0903 +0.0014/+0.0006, ex3 -0.21/-0.22, 3/5 d (section 1) | 3 | 0.25 SOL x ~24 h (73 of 79 exits time): ~2.8-4.8 SOL open at 11-19/d | CANDIDATE 1 (weak; see section 3) |
| 3 | H2-PS / H2 Book A (lib-b-B2) | not registered; arena rule file tools/arena_rule_h2ps.py exists | streaming arena rule; no kit form (exit = followed wallet's sell) | desk, 35k stack only: +0.89..+1.27 %/tr, ex-top-3 negative, 4XWC 79% of SOL; no 160k number | 2 | 0.25 SOL x <=600 s | CANDIDATE 2, conditional on a P0 count (single-wallet rate likely < bar) |
| 4 | r4-b / LAYA O2, l3 xs10 (lib-c-O2, lib-b-D5) | FORWARD: EXP-027 on walk 2 (W0 >= 10-18T03) | xs engine, not kit | desk 35k-C +0.94 / +2.06 %/tr; 16/28 d+, P(CI lo>0)=0.165; xs10 one runner 40% | 2 | 0.25 SOL x 1 h x 10/h | already forward |
| 5 | second-book-5, r4b-depth-1/2 (0.5-2 SOL stake, depth skip) | not separable: needs r4-b to pass EXP-027 first | xs engine | in-sample only; +100 bps drift -> ex3 -1.22 SOL | 1 | 0.5-2 SOL x 1 h | wait for EXP-027 |
| 6 | haiku-v3-1/2/3/4 | HV4 (9 bots) clock 10-25T00Z covers Haiku; QP: v3 NOT CLEARED, learning ~ placebo | LLM overlay | gain = dead-pool filter | 1 | n/a | defer (QP-REVIEW 5(d)) |
| 7 | lib-a-A13 / lib-b-E3 uninformed-sell reversion; I4 SELLABS | none | E3 = LightGBM walk-forward (not kit); SELLABS kit | SELLABS s1/s2/s4 at 160k: flat -2.7% / -2.8%, 0/25 d, FAIL; E3: 2.52 of 3.45 SOL in fresh-0903 | 0 | 0.1-0.25 SOL x 10 min | do not register |
| 8 | lib-a-A40 (300 s cap), A41 (seed x1.15 guard) | not books: exit/entry components | kit-native (guard 1.15 already in the kit) | POS-EXPL on g_reachable only | n/a | n/a | component of other books |
| 9 | H5 family: M1 C10 cadence exit; l3-boost-h5q50-1; boost-universe-1/2 | Q50 registered 10-25T00Z; M1 parked (pressure +2.94 pp < +3.0 floor, regime); boost-universe custom-pair 3.4/d, off-band 0-2/d | H5 arena executor | not kit-form; H5 cost module (rent charged) | 1 | 0.25 SOL x 330 s | M1 needs a DEC-027 amendment; custom-pair needs an engine + P0 bar: no |
| 10 | REVIVE r2h/r6h/r12h; w2-quiet R4; w2b-strand DS-C1-g50; hc_h60 | none | kit | all ex-top-3 negative at 160k (r12h +W exit mean +0.0047 but ex3 -2.65, 5/23 d) | 0-1 | 0.25 SOL x 1 h | do not register |
| 11 | boost-universe-3/4, A19/J2 attention, speed-route-1..5, G4.1/G4.2, L4.5, lib-c-L4.5 | none | landing/feed tests or too few pools or 11-06 clean tape | no pricing | 0 | n/a | not forward candidates now |

## 3. Top candidates to register as NEW arena books, clock on or after 2026-10-27
Honest framing: only one row beyond the already-forward books has a kit-form or arena-form rule AND a positive mean under both fail models at 160k; it shrank on held-out and is concentrated. A forward book is the only way to get more than a screen; registration here is a cheap bet on a 24 h line, not a claim.

### C1. G4 v1 (`g4v1`, kit cell G0) - register after FLOOR starts, clock 10-27T00Z or later
Registration pack needs:
- Rule file + frozen sha: tools/arena_rule_g4v1.py from PR #806 (worktree /data2/mal/wt/g4v1-rule), merged and frozen_sha pinned; md5 decision-equivalence replay (12/13 on w7 is not equal: the 13th must be explained or fixed first); fresh-0903 aux/universe via the same reader.
- P0 count definition (structure only, fixed first in a notebook entry, whole closed arena-tape days >= 3, tool = rule module at the candidate sha, counts-only): entry predicate at each UTC hour mark - pool age (H - migration time) in [24 h, 200 h], a print in [H-6 h, H), 24 h volume >= 1 SOL, pre-trade price x 1e6 < 25, price at H / price 6 h earlier < 0.80, V in [17.5e9, 17.7e9], WSOL; decision at the pool's first print >= H. Rate evidence: 19.1/day in-sample, 11.3/day fresh-0903 (79 attempts after cooldown). Suggested bar (QP to set): >= 10/day on 3 days, no day under 5 (FLOOR used 12.5 of 13.3 = 94% of its in-sample rate; 94% of 11.3 is 10.6). Hard-gap rule as Am.3.
- H: 100 attempts in about 6-9 days at 11-19/day plus a 24 h drain; propose H = 12 days like FLOOR Am.9 (one <=14 d MiScusi job), same END_H logic and gap/DR1 hour-health checks as the FLOOR pack (hunt24/floor-1026).
- Feed needs: continuous PumpSwap prints for pools aged 24-200 h; the rule needs per-pool 26 h history, so the streaming port must warm up (first signal not before clock + 24 h of tape, or a seeded history; declare which). Pool-age and feed-gap exclusions per Am.6; block-edge exclusion was 22-31% in kit screens, so decidable share must be declared.
- Executor availability: NONE for a 24 h hold. Same shape as FLOOR (24 h hold, TP +50%, sell + close ATA): reuse P2 FLOOR-EXEC design (hunt29/p2). Cost basis: FLOOR is priced at 505,000/send (not covered by Am.8); G4 registered after Am.8 would be covered at 160,000 only if its registration follows the Am.8 rules; at 505k the kit numbers fall by about 0.28 pp per trade (2 x 345k lamports / 0.25 SOL) - fresh-0903 flat +0.55% becomes about +0.27%.
- Capacity: ~2.8-4.8 SOL open at 0.25 SOL stake (11-19/day x ~24 h), more than a 1 SOL bankroll; stacked with FLOOR (3.3 SOL) this is the P5 question.
- Per-book process (one process per frozen_sha); memory check as FLOOR (floor-v2-memory).

### C2. H2-PS (`h2ps`) - only if its P0 count passes
- Rule file: tools/arena_rule_h2ps.py (+ test_arena_rules_h2ps.py) exists; needs frozen sha and the frozen wallet list (Book A list, 50 wallets) pinned by sha.
- P0 count: would-be follow-buys per UTC day of the listed wallets on whole closed arena-tape days (structure only). The library's own withdrawal rule (4XWC649w... < 30 distinct-mint buys in a week, about 4/day) says the rate is likely far below a 10-20/day bar; the count decides, not this note. Concentration: 4XWC carries 79% of pressure-leg SOL; ex-4XWC desk mean negative at 160k-ish stacks.
- H: depends on the count (if 3/day, 100 attempts takes more than 30 days: not a fast book). No kit-form 160k number exists; cannot be priced in the kit (ExitView has no trader column). Executor: none (copy-trade of a wallet sell needs a streaming trigger on the executor).
- Rank honestly: register only if P0 >= bar; otherwise it is a slow book beside FLOOR/G4.

### C3. none
No third row meets "kit/arena-form rule + positive both-model mean at 160k + rate >= 10/day". Rows that could become a book only after other results: r4-b stake scale-up (after EXP-027), M1 (needs DEC-027 amendment), boost custom-pair (needs engine). Recommendation: spend the third slot on a NEW screen from P4-style lanes, not on a repeat of a library row.

## 4. Counts and multiplicity
Audited rows: every explored-survivor-unconfirmed or parked row plus the named long-horizon rows (about 40 of 467). New pricing: 1 cell (G4 G0, fresh-0903). Rows excluded from pricing by rule: all others already had 160k numbers (hunt28 L3/L4, hunt29 P3/P4/P7) or no kit-form rule.
