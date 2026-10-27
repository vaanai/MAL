# HV8 rated-features: Haiku rubric ratings into a frozen calibrator (design v0, 2026-10-27)

Desk design, lane HV8 (manager17). Records only. **Nothing here says HV8 makes money.** HV8 has 0 trades, no sha, no clock. No book has cleared
the promotion gate. Paper only. No API call, job, tape read, card read, clean-view read, or HV4/Q50/HV6/HV7 output was used. Every rate, token,
sd and cost figure below is an assumption or arithmetic, not a measurement, unless a source is named. Times from `date -u` (2026-10-27T04:30Z).

Source: recommendation 2 of `docs/research/p67/RESEARCH.md` (main at 0317d21a) and the QP rulings on PR #1007 (verdicts at 9310a4ff, 9dfd330d,
cd1b2bf1: "Rec 2 ... Missing: the 2-4 features and the training window must be named before training, and the training labels must come from a
holdout-ledger block"; "pointwise is about 0.105 USD/day with a cache write per call"). Also read: `docs/research/hv7/DESIGN.md` (all),
`docs/HOLDOUT_LEDGER.md` (block table), `tools/hv7_rank.py` / `tools/hv7_runner.py` / `tools/hv7_dump_requests.py` (function names and the
screen path), `tools/hv6_cards.py` header, `tools/hv4_cards.py` field tuples, `tools/arena_clean_adapter.py` header. No card contents were opened.

## 1. Idea and the honest expectation

HV7 asks Haiku for a forced listwise order. HV8 asks a narrower question, the one the lab's own history points at (haiku-v3: learning about equal
to placebo, the gain was a dead-pool filter, i.e. a numeric filter did the work): **does Haiku's reading of the card fields add anything over code
reading the same fields?**

- Haiku gives **pointwise** 0-3 ratings on **3 named rubric items**, one card per call, so a rating never depends on the other cards in the set
  (no position bias, and a within-period permutation is a clean placebo).
- A **frozen** logistic calibrator, trained **once** on a pre-registered exploration window, maps [3 ratings + 1 numeric anchor] to a rank score.
- The book (L8) trades the top-1 score each hour on HV7's screen sets.
- The skill test is paired against a **numeric twin** (same model form, same feature count, the numeric fields the rubric items talk about), a
  **permuted-rating placebo**, R and U.

The pre-registered expectation is the HV7 one: L8's absolute gate read is expected to **FAIL** (the screen loses before selection; HV6 section 1),
and the skill contrast is expected to read **"no skill detected"** (section 5: at IC <= 0.05 its power is about 0.05). The deliverable is a decided
book and a paired number, not a pass.

## 2. Universe, screen, period, exit (all HV7's, reused byte for byte)

- Screen = `Hv7Runner.screen` at `t_d = H:02:00.000Z`: HV6's universe (canonical PumpSwap pools of graduated pump.fun mints, WSOL quote, PRE-trade
  reserves plus V, age 2 h to 7 d with the hard 2 h guard, HV4 filters and hard vetoes 1-7, phantom filter, running-max head slot), top N = 8 by
  `f.vol_60m`, `N_MIN = 3`, HV7's pre-listed code vetoes. A C4 test asserts HV8's screen ids and card shas equal HV7's at the same `t_d`.
- Entry for every arm at `t_e = H:02:45.000Z`, 0.25 SOL. Exit = HV6 `X_EXIT` (stop -8%, target +20%, 60 min), code only, identical for all arms.
- Hourly periods; the 30-min lever of HV7 section 2 applies only if HV7 adopts it (HV8 follows HV7's registered period so the sets stay equal).
- Pools >= 2 h old only, so HV8 stays outside the EXP-024 Look-2 seal (no V-range pool in its first 360 s).

## 3. Rating request (frozen in C1, not edited after the offline run)

**Card view (tape-only).** The training cards are built on August tape (section 4), where the as-of chain facts (RPC mint facts, largest holders)
cannot be reconstructed. So the card shown to Haiku, in training and live alike, is the HV6 card with every field that needs RPC removed. Kept:
`tools/hv4_cards.FEATURE_FIELDS` (tape-derived) and the risk fields the HV7 runner computes from tape (`holder_count` from the pool's net map,
`quote_chg_15m`, `quote_dd_peak`) plus setup tags. C1 classifies each field from code, and a C1 test asserts the view built with HV7's `FakeChain`
equals the view built with a real chain dict on every kept field. Train and live requests therefore carry the same keys.

**System block.** HV7's RANK_v0 header facts that are true for every arm: UNTRUSTED TEXT, CARDS, HONESTY, SCREEN NOT REASON, BASE RATE and MEASURED
DIRECTIONS byte-equal to `tools/hv7_playbooks/RANK_v0.txt` where they do not mention ranking (C1 byte-compare test), then:

> RATING TASK. You see ONE pool that passed the lab's code screen. Rate it on three items, each 0, 1, 2 or 3, from its card fields alone. Write
> `why` first (at most 25 words, naming at least one card field), then the three integers. There is no buy, skip or rank decision here; a frozen
> code model turns the ratings into a score. Where the lab has no measured direction, your rating is a hypothesis, not a lab result.

**Rubric items (named now; anchors frozen in C1).** Each item has a numeric **mirror field** that the twin uses (section 4).

| Item | What it rates (0 = worst, 3 = best for the next 60 min) | Mirror field (twin) |
|---|---|---|
| `flow` | breadth and balance of recent buying: many distinct buyers, net inflow, not one wallet | `f.net_flow_15m` |
| `dump_risk` | (3 = lowest risk) concentration of selling power: top signers' share, creator selling, sellers outnumbering buyers | `f.top3_signer_share_60m` |
| `trend` | price position: recovering or holding near the 6 h high (3) versus still falling from it (0) | `f.close_vs_hi_6h` |

Numeric anchor (in both L8 and the twin): `f.vol_60m`, the screen key, as signed log1p.

**Reply.** JSON object `{"why": str, "flow": int, "dump_risk": int, "trend": int}`, ints in {0,1,2,3}. `claude-haiku-5-5`, thinking disabled, no
temperature, max_tokens 300, one 5-min cache marker on the system block. Validation (`validate_rate`): not JSON, a missing or out-of-range item, a
refusal, a timeout, a budget stop, or no reply by `t_e` makes that card **unrated**; a long or field-less `why` is accepted and logged
(`why_defect`), as in HV7.

**Unrated cards and FALLBACK.** An unrated card gets the training median of each missing item (logged `imputed`). A period with more than N/2
unrated cards is an L8 **FALLBACK**: L8 trades R8's pick (HV7's `r_pick` formula with prefix `hv8-R|`), counted in n. Two caps, each over all
counted periods of the window and checked by the outcome-blind count tool at E1, B and window end: FALLBACK periods / periods > 0.10, or imputed
cards / shown cards > 0.10, makes every L8 contrast NOT EVALUABLE.

**Calls per period.** N calls (one per card) between `t_d` and `t_e` (45 s). Default: in parallel (the cost line in section 6 already prices every
call as a cache write). C4 must show `haiku_caller`'s reserve/settle is safe with N concurrent calls on one ledger (the same item QP raised for HV7
v7.1 on #1007); if it cannot, the calls run in sequence and any reply after `t_e` is unrated.

## 4. Training and the calibrator (named before training)

**Training window (ledger exploration pool).** Decision hours `t_d` in **[2026-08-21T12, 2026-08-28T12)** = 168 instants, inside the
`Exploration expansion` block `[2026-08-14T12, 2026-08-28T12)` (Owner: exploration pool; `/data/mal/clean-view/explore-0814/w1..w7`). The first
7 days `[2026-08-14T12, 2026-08-21T12)` are replayed for state only (no card, no label): the age filter is "since first PumpSwap print", and the
lookback block before 08-14T12 is `fresh-0808`, which is reserved (Second backup confirmation block) and is **not** read. From 08-21T12 on, every
pool with true age <= 7 d graduated inside the block, so `created_known` holds for every eligible pool; the training screen additionally requires
`created_known` and logs the count it drops (expected 0 by this argument; a non-zero count is reported). Disclosures: this block is heavily reused
exploration (EXP-012 backcheck job #207 computed outcomes there and refused; other screens used it), so it yields a hypothesis only (ledger rule 2).
It is never a confirmation holdout. Not used: `fresh-0828` (reserved for EXP-028), `fresh-0808`, `fresh-0802` (reserved), any October hour.

**Tape path.** `tools/arena_clean_adapter.py --block /data/mal/clean-view/explore-0814/w1 ... --out <scratch>` writes arena-format hours
(synthetic `t_recv_ms`, V from `arena_screen.VMAP_DEFAULT`, null V = outside the band). The screen is `Hv7Runner.screen` over that tape with HV7's
`FakeChain` (the dump tool's path). So the training screen has no RPC-based veto; the live screen has them. That is a disclosed train/live shift: it
can cost power, it cannot bias the paired contrasts, because every arm is compared on the same live sets.

**Size.** At most 8 x 168 = **1,344 cards** (realised N unknown; a count-only full pass on the real layout runs first and prints sets, cards and
drop counters before any rating call, per the real-layout precount rule).

**Labels (outcome).** Each training card is priced as U prices it: entry at `t_d + 45 s`, `arena_paper.ArenaEngine`, 0.25 SOL, 1.9 s leg, X exit,
both fail models. Label `y = 1` if the card's net return under the flat 15% model is above its set's median, `y = 0` if below, ties dropped
(relative label: the hour's market move cancels). Pressure-model labels are report-only. The label job refuses to start unless the ratings
manifest sha256 is pinned in its arguments and the ratings directory is read-only, so no rating is made after any outcome exists.

**Calibrator (fixed now).** Logistic regression, L2, C = 1.0 (penalty 0.5 x ||w||^2 on standardised features, intercept unpenalised), solved by
Newton/IRLS in numpy for 50 fixed iterations from zero (deterministic, no sklearn version risk). Preprocessing frozen from training rows only:
mirror and anchor fields winsorised at the training 1st/99th percentiles, `f.vol_60m` and `f.net_flow_15m` as signed log1p, then all features
z-scored by training mean/sd; ratings enter as integers 0-3, z-scored. The model file (weights, means, sds, cut points, training manifest sha)
gets one sha256, pinned in the registration.

| Model | Features (4) | Arm |
|---|---|---|
| L8 | `flow`, `dump_risk`, `trend`, anchor | L8 (promotable) |
| twin | `f.net_flow_15m`, `f.top3_signer_share_60m`, `f.close_vs_hi_6h`, anchor | T8 (comparator) |
| placebo | L8's frozen weights; the 3 ratings permuted across the period's cards by `sha256("hv8-P|" + t_d_iso + ...)` (one perm for all 3 items, so a card's rating triple stays whole) | P8 (comparator) |

Pick = highest score; ties by screen order. **No feature is added, dropped or swapped after the offline run**; a pre-clock bar that fails
stops HV8, it does not trigger a new variant.

**Offline sizing on the Claude plan (owner rule: offline tests on Claude-plan subagents, not the API).**
- One fresh Haiku subagent per card (Agent tool, `model: haiku`, the 10-12 alias to Haiku 5.5), given exactly one prompt file and writing exactly
  one reply file. One card per subagent keeps each rating independent of other cards, as live calls are; a subagent rating several cards in one
  context is not pointwise and is refused by design.
- Retest: a seeded 10% of the cards (`sha256("hv8-retest|" + card_sha)` lowest decile) rated a second time by a fresh subagent, for test-retest
  reliability.
- **Calls: at most 1,344 + 135 = 1,479 subagent calls** (about 9x HV7's 144-call rehearsal; HV7's 288-call cap does not cover it; HV8's own cap
  is 1,479 including one format-only re-run of failed files only).
- Plan load (guess, not measured): about 15k tokens per subagent including harness, so about 22M Haiku tokens; at 24 concurrent subagents about
  62 waves, about 1-2 h wall. Registered smaller alternative, chosen by the manager **before any call** if plan usage is tight:
  `t_d` in [2026-08-24T12, 2026-08-28T12) = 96 instants, at most 768 + 77 = 845 calls.
- Plan-vs-API serving difference (the Claude Code harness wraps the plan model): measured by the bridge bar (section 7, bar 6) on the stub day.

## 5. Reads (pre-registration sketch; quoted exactly from DEC-031 in the registration)

**Arms and bot ids.** `hv8.0-L8` (promotable), `hv8.0-T8`, `hv8.0-P8`, `hv8.0-R8`, `hv8.0-U8` (or U1..U8 shadow ids, HV7's C2 rule): 5 bot ids,
1 promotable cell, one deciding stake (0.25). T8, P8, R8, U8 are comparators, never promoted. Cross-arm pricing independence is the HV7 C2 hard test,
rerun at HV8's sha.

**Gate read (absolute, L8 only).** As HV7 section 4: B = first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates by the outcome-blind
count tool, 1.9 s leg, four sub-cells, CLAUDE.md gate unchanged, day-block bootstrap report-only. Expected FAIL. If B has not come by window end,
NOT_DECIDABLE.

**Skill claim (read ONCE at window end, after the last counted attempt has exited).** Both, each an exact one-sided sign-flip on daily sums of
per-period SOL differences, p <= 0.025 in each of the four sub-cells, and >= 8 non-zero days for each:
1. **L8 - T8 > 0** (ratings add over code reading the same fields), and
2. **L8 - U8 > 0** (L8 selects better than the whole set).

This is an intersection-union test (both must pass), so no multiplicity correction between the two is needed and the size is at most 0.025.
Report-only, Holm-ranked: L8 - P8 (does the rating-to-pool alignment matter, calibrator held fixed), T8 - U8, P8 - U8, R8 - U8 (pipeline fault check,
must be mean zero), within-set Spearman IC of L8's and T8's scores against U8-priced returns. **A skill pass is not a promotion**: with L8's gate
FAIL it says "the ratings select better than code on these fields, and the screen still loses"; it licenses a v8.1, never a canary.

**Power (QP's HV7 simulation approach, arithmetic, not a measurement).** Exact one-sided sign-flip at p <= 0.025 on daily sums, 20 trades/day,
N = 8, normal returns, zero cross-pool correlation, 1,500 draws per cell, seed 1 (script:
`docs/research/hv8/power_hv8.py`, run with `/data/mal/venv/bin/python -I`, about 3 min). Score model: each
arm's score = IC x z + noise, where z is the pool's standardised 60-min return; the L8 and twin noises correlate kappa (they share the anchor and
read the same fields). The sign-flip is scale-free, so s drops out in IC terms. Power that BOTH contrasts pass:

| D | twin IC | kappa | L8 IC 0 | 0.05 | 0.10 | 0.17 | 0.25 | 0.30 |
|---|---|---|---|---|---|---|---|---|
| 10 | 0.00 | 0.5 | 0.003 | 0.053 | 0.250 | 0.715 | 0.975 | 0.997 |
| 10 | 0.00 | 0.8 | 0.003 | 0.053 | 0.291 | 0.818 | 0.993 | 1.000 |
| 10 | 0.10 | 0.5 | 0.000 | 0.003 | 0.021 | 0.192 | 0.666 | 0.887 |
| 10 | 0.10 | 0.8 | 0.000 | 0.001 | 0.029 | 0.258 | 0.807 | 0.972 |
| 8 | 0.00 | 0.5 | 0.004 | 0.033 | 0.142 | 0.521 | 0.883 | 0.969 |
| 8 | 0.00 | 0.8 | 0.003 | 0.033 | 0.173 | 0.636 | 0.945 | 0.992 |
| 8 | 0.10 | 0.5 | 0.000 | 0.002 | 0.017 | 0.143 | 0.498 | 0.746 |
| 8 | 0.10 | 0.8 | 0.000 | 0.003 | 0.021 | 0.177 | 0.652 | 0.879 |

L8 and T8 picked the same pool in about 32% (kappa 0.5) or 54% (kappa 0.8) of periods in these draws.

- **MDE (80% power, both contrasts, D = 10): L8 IC about 0.17-0.19 if the twin has no skill; about 0.25-0.28 if the twin has IC 0.10.** So L8 must
  beat the twin by an IC gap of roughly 0.15-0.19. At D = 8 the MDE rises to about 0.20-0.22 (twin 0) and 0.28-0.31 (twin 0.10).
- In per-trade lift over U (normal approximation, lift = IC x s x 1.4264, E[max of 8 N(0,1)] = 1.4264): IC 0.18 is 2.05% at s = 8% and 3.08% at
  s = 12%.
- This score model gives the picked pool its order-statistic variance, which is smaller than QP's HV7 convention sd(L - U) = 1.06 s. At matched IC
  it is more optimistic: L8 - U8 alone at IC 0.17, D = 10 has power 0.905 here, against about 0.8 from QP's HV7 table (interpolated between 0.617 at
  1.5% and 0.835 at 2.0% lift, s = 8%). Read these MDEs as lower bounds.
- At IC <= 0.05, the range the lab's earlier Haiku work points to, the power of the skill claim is about 0.03-0.05. **The likely result is "no
  skill detected", and a non-pass is not evidence of no skill below the MDE.** No arm or cell is added to raise power.

**E1 (outcome-blind, host-side MiScusi job scheduled before T0).** At T0 + 2 d, HV7's E1 rules with HV8 names: A_L8 < 20 makes every HV8 read
NOT_DECIDABLE (`E1_entry_rate`) with an entries halt at the next 00:00Z; the two 10% caps of section 3 are checked; output sha256 notebooked.

**Multiplicity (DEC-031 Am.3 item 3).** 5 bot ids, 1 promotable. Each read carries N = every book with a clock before it (HV4's 9, Q50, H5 books,
hunt23/cohort books, HV6's 5, HV7's 4, HV8's 5, stopped and NOT_DECIDABLE included), the line 1 - 0.95^N and the report-only p with its Holm rank.

**Siblings (Am.3 8(e)).** HV8 shares HV7's screen sets, cards (minus RPC fields), model, `t_e` and exit on the same days. HV8 L8 and HV7 L are
never independent confirmations; the better of them at any read is in-sample. HV8's U8 equals HV7's U pool for pool (same sets, same `t_e`, pricing
independence), which is disclosed, not used as a second sample.

## 6. API cost (repo price table) and the owner cost line

Prices: `tools/haiku_caller.py` `PRICES["claude-haiku-5-5"]` = input 0.10, cache_write 0.125, cache_read 0.01, output 0.50 USD per million tokens.
Token sizes from HV7 DESIGN section 5: system 2,874 tokens, about 1,082 tokens per card, about 400 tokens of task text; output assumed 120 tokens
(`why` + three ints), max_tokens 300. One card per call, every call priced as a cache **write** (hourly; parallel calls cannot read each other's
cache).

| Call | Input | Output | USD/call | Calls/day | USD/day |
|---|---|---|---|---|---|
| rate, 1 card | 2,874 cache-write + 1,482 new | 120 | 0.00056745 | 192 (8 x 24) | 0.1089504 |
| rate, worst (max_tokens 300) | same | 300 | 0.00065745 | 192 | 0.1262304 |
| worst x MAX_ATTEMPTS 3 | | | | | 0.3786912 |
| worst x MAX_ATTEMPTS 2 (HV8 setting) | | | | | 0.2524608 |
| report-only: sequential, calls 2-8 read the cache | 2,874 read + 1,482 new | 120 | 0.00023694 | 168 + 24 writes | 0.05342472 |

Check, row 1: (2,874 x 0.125 + 1,482 x 0.10 + 120 x 0.50) / 1e6 = (359.25 + 148.2 + 60) / 1e6 = 0.00056745; x 192 = 0.1089504 (QP's "about 0.105").

**HV8 needs its own owner cost line.** K1 ("HV6 <= 3.00 USD/day 10-30..11-11") covers HV6 only, and HV7's draft line covers HV7 only. Proposed,
to be quoted verbatim in the registration: **"HV8 <= 0.30 USD/day, from its stub day to its window end, inside the 3.00 USD/day and the 200 USD
total"**, with MAX_ATTEMPTS = 2 so the worst case (0.2525) stays under the cap. No HV8 API call (stub, bridge or smoke) before that line exists.

| Book | Cap USD/day | Source |
|---|---|---|
| HV6 v6.0 | 2.20 | HV6 DESIGN section 6 |
| HV6 v6.1 | 0.00 | parked |
| HV7 v7.0 | 0.30 | HV7 DESIGN section 5 (v7.1 K = 3 would need about 0.34) |
| HV8 v8.0 | 0.30 | this design |
| **Sum** | **2.80** (2.84 with HV7 v7.1) | <= 3.00 |

Enforcement: HV7's option (a), a CI sum test over the pinned cap constants (`hv6_agents.caps_total_usd()` + HV7 + HV8 <= 3.00), extended in C5.
Window cost: at cap 10 d x 0.30 = 3.00 USD; expected about 10 x 0.1089504 = 1.089504 USD, plus the bridge (48 x 0.00056745 = 0.0272376 USD).
Offline training ratings: 0 API (Claude plan). Helius: the live screen's chain vetoes need HV8's own RPC client (HV6 pattern, `MAL_HV4_RPC`
pinned); if the provider is helius, credits are counted and reported per the CLAUDE.md credits rule. Training spends 0 credits.

## 7. Pre-clock bars (kill-only, pre-declared now; a failure stops HV8, never triggers a variant)

1. **Parse** (offline run): valid ratings >= ceil(0.95 x calls).
2. **Reliability**: quadratic-weighted kappa between first and retest ratings >= 0.40 on at least 2 of the 3 items (each item reported).
3. **Non-degenerate**: at most 1 item with more than 90% of cards on one value.
4. **Training rows**: >= 600 labelled, rated cards (ties dropped), else NOT_DECIDABLE pre-clock.
5. **LODO screen (exploration, outcome-bearing, kill-only)**: leave-one-day-out over the 7 training days, fixed hyperparameters; mean within-set
   Spearman IC of the L8 score must exceed the twin's. If not, HV8 is not registered. Passing licenses nothing beyond running the forward test; one
   model form and one feature set, so one trial (disclosed). P8's LODO IC is reported beside both.
6. **Bridge (stub day, needs the owner line)**: 48 stub cards rated on both the plan path and the API path; quadratic-weighted kappa >= 0.60 on at
   least 2 of 3 items, else the calibrator was trained on a different rater and HV8 is not registered.
7. **Rate**: HV7's P0 (same screen) gives >= 10.0 attempts/day and no day under 5.
8. md5 decision-equivalence proof, two seed-1 replays byte-identical, E1 restart drill, no open seal question.

## 8. Code: reuse and new builder chunks (each <= 40 turns, WIP commit by turn 28, draft PR, no merge)

**Reused as is (imported, not edited).** `tools/hv7_runner.py` (`Hv7Runner.screen`, `_card`, `check_card_keys`, the resume rules of HV7 section 8a,
decision log, `FakeChain`/`NullEngine` patterns from `hv7_dump_requests`), `tools/hv7_rank.py` (`screen_set`, `r_pick`, `u_set`, `fallback_pick`,
`iso_from_ms`, `forbidden_keys`), `tools/hv6_cards.py` (scan, build_card, age guard), `tools/hv6_phantom.py`, `tools/hv4_prompts.py` (`Request`,
`card_sha256`, `CACHE_MARKER`), `tools/haiku_caller.py`, `tools/arena_state.py`, `tools/arena_paper.py`, `tools/arena_clean_adapter.py`,
`tools/hv7_count.py` pattern.

| Chunk | Branch | Scope | Tests |
|---|---|---|---|
| C1 | `claude/hv8-rate-core` | `tools/hv8_rate.py` (pure): tape-only card view, `build_rate_request` (1 card), `validate_rate`, rubric constants, `placebo_perm`, imputation; `tools/hv8_playbooks/RATE_v0.txt`, `tools/hv8_templates/rate.txt` | view equals across FakeChain/real chain on kept keys; no RPC field in a request; byte-compare with RANK_v0 shared sections; validator (missing item, 4, -1, float, string, refusal, empty -> unrated; long `why` -> `why_defect`); request bytes stable; forbidden-key guard; placebo perm determinism and whole-triple rule; section 6 price arithmetic |
| C2 | `claude/hv8-calibrator` | `tools/hv8_calib.py`: frozen preprocessing, fixed-iteration L2 IRLS, model JSON + sha256, `score`, L8/T8/P8 picks, LODO report, bars 2-5 | synthetic data with known weights recovered; byte-identical model from two runs; refuses features other than the registered 4+4; ties by screen order; LODO on a planted signal; kappa arithmetic |
| C3 | `claude/hv8-train-dump` | `tools/hv8_train_dump.py` (adapter output -> `Hv7Runner.screen` with FakeChain at the 168 `t_d`, warm-up from 08-14T12, `created_known` drop count, `--count-only`, prompts + manifest, cap 1,479, refuses non-empty out / dirty tree / any hour outside the window / any path under fresh-08*, fresh-0828, /data/mal/arena, /data/mal/meta-capture); `tools/hv8_ratings_validate.py` (bars 1-3); `tools/hv8_train_label.py` (prices every card, refuses unless the ratings manifest sha is pinned and the ratings dir is read-only) | synthetic adapted tape: sets equal `hv7_dump_requests` logic; window refusals; label tool refusal; relative-label ties; job wrapper runs under sh |
| R | (no PR; `/data2/mal/hv8-train/`) | count-only pass (MiScusi job), dump job, 1,479 Haiku subagents, validator, label job, C2 training | bars 1-5 |
| C4 | `claude/hv8-runner` | `tools/hv8_runner.py`: HV7 screen, N rate calls in `[t_d, t_e)`, own budget ledger (MAX_ATTEMPTS 2), arms L8/T8/P8/R8/U8 at `t_e`, X exit, fallback and imputation caps, resume; `tools/hv8_count.py` | screen ids and card shas equal HV7's at the same `t_d`; pricing independence (HV7 C2 test at HV8's sha); concurrent reserve/settle on one ledger (or sequential with deadline); StubModel drill: one entry per arm per period; late reply -> unrated; two runs byte-identical |
| C5 | `claude/hv8-registration` | `hv8_register.py`, drills, md5 proof, stub + bridge, cap-sum CI test, DEC-031 amendment, records | QP OK on exact head + reviewer verdict |
| C6 | `claude/hv8-read` | `hv8_read.py`: gate at B, IUT skill read at window end, secondaries, Holm, MDE line | synthetic books with known answers; refuses early reads; D = 8 rule |

## 9. Clock (earliest realistic)

Resources: one heavy job at a time on research-0. HV6's v6.0 registration holds the 16 GB replay slot to its 2026-10-29T12:00Z fallback deadline,
and HV7's replays (real-layout, md5) queue behind it (HV7 freeze about 10-30). HV8's heavy jobs (count-only pass + dump over 14 August days, then
the label pricing) queue behind HV7's. The offline ratings are plan-side, not research-0 heavy.

| Step | Earliest |
|---|---|
| This DESIGN: QP + reviewer | 10-27 |
| C1, C2 (pure code, parallel) | 10-28 |
| C3 | 10-28..29 |
| Count-only pass, then dump job (after HV7's replays) | 10-30 |
| 1,479 Haiku subagents + validator (bars 1-3) | 10-30..31 |
| Label job, training, LODO (bars 4-5) | 10-31 |
| C4 runner, drills, md5 proof | 10-31..11-01 |
| 24 h stub at the candidate sha, bridge (bar 6; owner line needed) | 11-01..11-02 |
| Records PR (QP OK on exact head + reviewer) -> registration | 11-02 |
| **Clock T0** | **2026-11-03T00:00:00Z** (fallback 11-04T00Z) |

- **Window problem.** The owner's API horizon is 2026-11-11T00Z (200 USD cap to 11-11). From an 11-03 clock that is D = 8: the ">= 8 non-zero days"
  rule then needs every day, and the MDE rises (section 5). **Ask with the cost line: extend HV8's line to 2026-11-13T00:00Z (D = 10).** Cost of the
  2 extra days at cap: 0.60 USD. The extension goes in the registration before the clock, never after.
- Kill-review week: the manager's HV7 confirmation (LAB_STATE and docs/HANDOFF.md list none after 2026-10-05) is repeated on the registration date.
- HV8 does not touch HV7's resources: its own bot ids, ledger, RPC client and runner process; it reads only the arena tape (counts-only rules do not
  apply to a registered book's own forward hours) and its own outputs.

## 10. Seals (from SEALS-BRIEF.md, 2026-10-25) and what HV8 reads

Reads: `/data/mal/clean-view/explore-0814/w1..w7` (exploration pool) for training; the forward arena tape from its own T0 for its own book; its own
outputs. Never: HV4/Q50/HV6/HV7 outputs or the status/logs of #1561/#1562/#1419/#1421; `/data/mal/meta-capture/` (no listing); any October hour for
pricing (EXP-024 Look-2 seal to 11-14; HV8's forward hours are November); FLOOR G1 forward/arena outputs; the LaunchLab holdout; the DBC windows
(D1 spent, forward window sealed); the reserved blocks `fresh-0802`, `fresh-0808`, `fresh-0828`. The C1 card view takes field names from code, not
from inspecting cards. Walk-2 disclosure: HV8's forward hours are walk-2-hour information (DEC-030 section 2 R1) and are disclosed as such.

## 11. Biggest risks

1. **The screen loses** and selection cannot cover it: expected gate FAIL.
2. **Under-powered contrast**: MDE IC gap about 0.15-0.19 at D = 10, worse at D = 8; expected "no skill detected".
3. **August to November drift**: pump redeploys and fee changes since August can break a calibrator trained there; the twin is trained on the same
   window, so drift hurts both, but not necessarily equally.
4. **Train/live screen shift** (no RPC vetoes in training) and **plan/API rater shift** (bar 6).
5. **Exploration reuse**: explore-0814 has been screened by many lanes; bar 5 is one trial, but the block is not fresh.
6. **Twin strength**: if the twin already carries the information, L8 - T8 cannot pass even when Haiku reads well; that is the intended question.
