# HV8 rated-features: Haiku rubric ratings into a frozen calibrator (design v1, 2026-10-27)

Desk design, lane HV8 (manager17). Records only. **Nothing here says HV8 makes money.** HV8 has 0 trades, no sha, no clock. No book has cleared
the promotion gate. Paper only. No API call, job, tape read, card read, clean-view read, or HV4/Q50/HV6/HV7 output was used. Every rate, token,
sd and cost figure below is an assumption or arithmetic, not a measurement, unless a source is named. Times from `date -u` (v0 04:30Z, v1 04:40Z).

**v1 (2026-10-27, after QP CHANGES on #1010, comment 6433888568, and the manager's decision).** Scope cut: **only the OFFLINE stage is built now**
(C1-C3, the Claude-plan ratings run R, the frozen calibrator with leave-one-day-out IC). The forward book (C4-C6, owner cost line, bridge, clock) is
**DEFERRED** until HV7's window-end read, a pass of the strengthened bar 5, and a registration with D >= 10. Changes A-I: training ends at
08-28T11 with a hard 08-28T12 refusal (A); `created_known` from an observed event (B); manifest pin order, first ratings only, no-lookahead test,
in-sample rubric disclosure (C); T8's claim narrowed and an all-fields twin T8a (D); P8 fully specified as a derangement plus a matching-pick share
(E); bar 5 strengthened with winner's-curse shrinkage and the 96 h option dropped (F); headline power under HV7's convention, 10/day rows (G);
30-min worst case and unverified cacheability (H); call count 1,470 + 67 re-runs in recorded batches (I).

Source: recommendation 2 of `docs/research/p67/RESEARCH.md` (main at 0317d21a) and the QP rulings on PR #1007 (verdicts at 9310a4ff, 9dfd330d,
cd1b2bf1). Also read: `docs/research/hv7/DESIGN.md` (all), `docs/HOLDOUT_LEDGER.md` (block table), `tools/hv7_rank.py` / `tools/hv7_runner.py` /
`tools/hv7_dump_requests.py` (function names and the screen path), `tools/hv6_cards.py` header, `tools/hv4_cards.py` field tuples,
`tools/arena_clean_adapter.py` header. No card contents were opened.

## 1. Idea and the honest expectation

HV7 asks Haiku for a forced listwise order. HV8 asks a narrower question, the one the lab's own history points at (haiku-v3: learning about equal
to placebo, the gain was a dead-pool filter, i.e. a numeric filter did the work): **do Haiku's ratings of a card add rank information over a
small code model on named card fields?**

- Haiku gives **pointwise** 0-3 ratings on **3 named rubric items**, one card per call, so a rating never depends on the other cards in the set.
- A **frozen** logistic calibrator, trained **once** on a pre-registered exploration window, maps [3 ratings + 1 numeric anchor] to a rank score.
- Comparators on the same sets: the **3-field twin** T8 (the IUT leg), the **all-fields twin** T8a (report-only, a bar-5 hurdle), the
  **permuted-rating placebo** P8, and, forward only, R and U.

**What the offline stage can and cannot say.** It is the only part of HV8 with real power (QP: a within-set Spearman IC over about 167 sets of
N <= 8 has SE about 0.38 / sqrt(167), about 0.03 per arm). It runs on reused exploration hours with an in-sample rubric (section 4), so a pass is a
**hypothesis**, never a result (ledger rule 2). A fail stops HV8.

The forward expectation, if it is ever run, is HV7's: L8's absolute gate read **FAIL** (the screen loses before selection), and the skill contrast
**"no skill detected"** (section 5: at IC <= 0.05 its joint power is about the test's own size).

## 2. Universe, screen, period, exit (all HV7's, reused byte for byte)

- Screen = `Hv7Runner.screen` at `t_d = H:02:00.000Z`: HV6's universe (canonical PumpSwap pools of graduated pump.fun mints, WSOL quote, PRE-trade
  reserves plus V, age 2 h to 7 d with the hard 2 h guard, HV4 filters and hard vetoes 1-7, phantom filter, running-max head slot), top N = 8 by
  `f.vol_60m`, `N_MIN = 3`, HV7's pre-listed code vetoes.
- Entry instant `t_e = t_d + 45 s`, 0.25 SOL. Exit = HV6 `X_EXIT` (stop -8%, target +20%, 60 min), code only. Same for every arm and every label.
- **Hourly periods only** for HV8 (CHANGE H). If HV7 registers 30-min periods, HV8 does not inherit them without a new priced design (section 6).
- Pools >= 2 h old only, so HV8 stays outside the EXP-024 Look-2 seal (no V-range pool in its first 360 s).

## 3. Rating request (frozen in C1, not edited after the offline run)

**Card view (tape-only).** The training cards are built on August tape (section 4), where the as-of chain facts (RPC mint facts, largest holders)
cannot be reconstructed. So the card shown to Haiku, in training and in any later live use, is the HV6 card with every field that needs RPC
removed. Kept: `tools/hv4_cards.FEATURE_FIELDS` (tape-derived), the risk fields the HV7 runner computes from tape (`holder_count` from the pool's
net map, `quote_chg_15m`, `quote_dd_peak`) and setup tags. C1 classifies each field from code, and a C1 test asserts the view built with HV7's
`FakeChain` equals the view built with a real chain dict on every kept field.

**System block.** HV7's RANK_v0 header facts that are true for every arm: UNTRUSTED TEXT, CARDS, HONESTY, SCREEN NOT REASON, BASE RATE and MEASURED
DIRECTIONS byte-equal to `tools/hv7_playbooks/RANK_v0.txt` where they do not mention ranking (C1 byte-compare test), then:

> RATING TASK. You see ONE pool that passed the lab's code screen. Rate it on three items, each 0, 1, 2 or 3, from its card fields alone. Write
> `why` first (at most 25 words, naming at least one card field), then the three integers. There is no buy, skip or rank decision here; a frozen
> code model turns the ratings into a score. Where the lab has no measured direction, your rating is a hypothesis, not a lab result.

**Rubric items (named now; anchors frozen in C1).** Each item has a numeric **mirror field** that T8 uses (section 4).

| Item | What it rates (0 = worst, 3 = best for the next 60 min) | Mirror field (T8) |
|---|---|---|
| `flow` | breadth and balance of recent buying: many distinct buyers, net inflow, not one wallet | `f.net_flow_15m` |
| `dump_risk` | (3 = lowest risk) concentration of selling power: top signers' share, creator selling, sellers outnumbering buyers | `f.top3_signer_share_60m` |
| `trend` | price position: recovering or holding near the 6 h high (3) versus still falling from it (0) | `f.close_vs_hi_6h` |

Numeric anchor (in L8, T8, T8a and P8): `f.vol_60m`, the screen key, as signed log1p.

**Disclosure (CHANGE C): the rubric is in-sample.** The 3 items and their mirror fields were chosen with the lab's exploration findings in view,
and some of those findings came from the training block itself. That is in-sample selection of the hypothesis. It is acceptable only because the
deciding test is a forward one, and it is one more reason a bar-5 pass is a hypothesis.

**Reply.** JSON object `{"why": str, "flow": int, "dump_risk": int, "trend": int}`, ints in {0,1,2,3}. Model `claude-haiku-5-5` (offline: the
Claude-plan Haiku alias; forward: the API), thinking disabled, no temperature, max_tokens 300, one 5-min cache marker on the system block.
Validation (`validate_rate`): not JSON, a missing or out-of-range item, a refusal, a timeout, or no reply makes that card **unrated**; a long or
field-less `why` is accepted and logged (`why_defect`), as in HV7.

**Unrated cards.** Offline: an unrated card may be re-run once inside the re-run allowance (section 4); still unrated, it is dropped from
training and counted. Forward (DEFERRED): imputation of training medians, FALLBACK to R's pick when more than N/2 cards are unrated, and the two
10% caps (FALLBACK periods, imputed cards) that make every L8 contrast NOT EVALUABLE, checked at E1, B and window end.

## 4. Offline stage: training data, labels, calibrator, comparators (BUILT NOW)

**Training window (ledger exploration pool; CHANGE A).** Decision instants `t_d = H:02:00Z` for H in **[2026-08-21T12, 2026-08-28T11)** = **167
instants**, inside the `Exploration expansion` block `[2026-08-14T12, 2026-08-28T12)` (Owner: exploration pool;
`/data/mal/clean-view/explore-0814/w1..w7`). The last `t_d` is 2026-08-28T10:02:00Z, so its `t_e` is 10:02:45Z and its 60-min exit plus the 1.9 s
leg ends by about 11:02:47Z, inside the block. The window ended at 08-28T12 in v0, which let the last labels run into `[2026-08-28T12, ...)`,
`fresh-0828`, reserved for EXP-028; that is fixed here.

**Hard refusal (CHANGE A).** The dump and label tools refuse (raise, exit non-zero, write nothing) on: any input hour file at or after
`2026-08-28T12`; any row whose `event_ts`, `t_recv_ms` or slot time is at or after `2026-08-28T12:00:00Z`; any path under `fresh-08*`,
`fresh-0828`, `/data/mal/arena`, `/data/mal/meta-capture`. The engine's tape end for labels is passed as `2026-08-28T12:00:00Z` exclusive, and a
label whose exit would need a row at or after it is **censored and counted**, never extended. C3 tests: a synthetic tape with a planted row at
08-28T12:00:00.000Z raises; a planted hour file `tape-2026-08-28T12.jsonl.zst` raises; a `t_d` of 08-28T11:02 raises.

**Warm-up.** `[2026-08-14T12, 2026-08-21T12)` is replayed for state only (no card, no label). `fresh-0808`, the lookback block before 08-14T12,
is reserved (Second backup confirmation block) and is **not** read.

**`created_known` (CHANGE B).** v0 argued that every eligible pool would have `created_known` from 08-21T12 on. That is wrong: an old pool that was
dormant across 08-14T12 has a late first in-block print, so its age counted from first print is under 7 d. v1: a training card requires a
**graduation (`complete`) or pool-create event observed in the block** for its mint/pool, at or after 2026-08-14T12 and strictly before `t_d`. First
print alone never sets it. Pools without such an event are dropped from the training screen before the top-8 cut; the drop count is reported and
is **expected non-zero**. Disclosed train/live shift: the live HV6/HV7 screen falls back to age since first seen, so the training screen is stricter.

**Tape path.** `tools/arena_clean_adapter.py --block /data/mal/clean-view/explore-0814/w1 ... --out <scratch>` writes arena-format hours
(synthetic `t_recv_ms`, V from `arena_screen.VMAP_DEFAULT`, null V = outside the band). The screen is `Hv7Runner.screen` over that tape with HV7's
`FakeChain` (the dump tool's path). So the training screen has no RPC-based veto. Disclosed: it can cost power in a later forward test, it cannot
bias paired contrasts, because every arm is compared on the same sets.

**No-lookahead (CHANGE C).** C3 test: for each of several `t_d`, the card bytes (and `card_sha256`) built from the full tape equal those built from
the same tape truncated at `t_d` (no row at or after `t_d`). Run on synthetic tape in CI, and on 3 real-layout instants inside the count-only pass
(card sha256 only; no outcome).

**Size.** At most 8 x 167 = **1,336 cards** (realised N unknown). A count-only full pass on the real layout runs first and prints sets, cards,
`created_known` drops and screen counters before any rating call (real-layout precount rule).

**The 96 h alternative is dropped (CHANGE F).** At most 95 x 8 = 760 cards before median ties and censoring; with -8% stop exits tying often,
bar 4 (>= 600 labelled rows) is probably out of reach. There is one registered window.

**Ratings run R (Claude plan, owner rule: offline tests on Claude-plan subagents, not the API; CHANGE I).**
- One fresh Haiku subagent per card (Agent tool, default agent type with `model: haiku`, the 10-12 alias to Haiku 5.5), given exactly one prompt
  file and a forced output path, writing exactly one reply file. Typed agents (reviewer/quant-proof) are not used: they return empty inside
  workflows (lab note). One card per subagent keeps each rating independent of other cards; a subagent rating several cards in one context is not
  pointwise and is refused by design.
- Retest: a seeded 10% of the cards (`sha256("hv8-retest|" + card_sha256)` lowest decile by integer value, `ceil(0.10 x cards)`) rated a second time
  by a fresh subagent.
- **Training input = first ratings only.** Retest ratings feed bar 2 only and never enter the calibrator.
- **Calls: 1,336 first + 134 retest = 1,470, plus a re-run allowance of at most 5% of cards (bar 1) = 67, cap 1,537.** The validator refuses a
  run whose reply count exceeds the cap or whose re-runs exceed 67.
- **Batches across sessions.** The 1,470 prompts are split now, by a seeded rule in the dump manifest, into pre-listed batches of at most 64:
  24 batches, the 1,336 first-rating prompts in batches 1-21 and the 134 retest prompts in batches 22-24 (64 + 64 + 6), so a first rating always
  precedes its retest; re-runs (at most 67) go in one extra batch 25. Each batch is launched from a session
  that holds only its batch list; one parent session never launches all 1,470 (context overflow). Per-batch counts (prompts, replies, unrated,
  re-runs, session id, start and end `date -u`) are appended to `R-BATCHES.jsonl` beside the manifest and committed after each batch. Counts only;
  no rating content is printed into a session.
- **Plan usage.** Guess, not measured: about 15k tokens per subagent including harness, about 22M Haiku tokens in all. Measured after batch 1 (from
  the session usage readout, recorded in `R-BATCHES.jsonl`). **Stop if batch 1's measured use per call exceeds 2x the guess (30k)**, and report
  before any further batch.

**Pin order (CHANGE C).** After the last batch and the validator (bars 1-3): the ratings directory is made read-only, its manifest sha256 is (1)
written in a MiScusi notebook entry (kind decision, refs to the dump job) and (2) committed to `docs/research/hv8/RATINGS-PIN.md` on a pushed branch,
both **before** the label job is submitted. The label job takes the sha as an argument and refuses unless it equals the sha in `RATINGS-PIN.md` at
the job's pinned git head and the ratings dir is read-only. So no rating can be made after any outcome exists, and the order is auditable.

**Labels (outcome).** Each training card is priced as U prices it: entry at `t_e`, `arena_paper.ArenaEngine`, 0.25 SOL, 1.9 s leg, X exit, both fail
models, tape end 2026-08-28T12:00:00Z exclusive. Label `y = 1` if the card's net return under the flat 15% model is above its set's median, `y = 0`
if below; ties and censored cards dropped and counted (relative label: the hour's market move cancels). Pressure-model labels are report-only.

**Calibrator (fixed now).** Logistic regression, L2, C = 1.0 (penalty 0.5 x ||w||^2 on standardised features, intercept unpenalised), solved by
Newton/IRLS in numpy for 50 fixed iterations from zero (deterministic, no sklearn version risk). Preprocessing frozen from training rows only:
numeric fields winsorised at the training 1st/99th percentiles, heavy-tailed SOL fields (`f.vol_*`, `f.net_flow_*`, `*_sol*`) as signed log1p, a
null numeric replaced by the training median, then all features z-scored by training mean/sd; ratings enter as integers 0-3, z-scored. The model
file (weights, means, sds, cut points, training manifest sha) gets one sha256.

| Model | Features | Role |
|---|---|---|
| L8 | `flow`, `dump_risk`, `trend`, anchor (4) | the candidate |
| T8 | `f.net_flow_15m`, `f.top3_signer_share_60m`, `f.close_vs_hi_6h`, anchor (4) | 3-field twin; the IUT leg of any forward read |
| T8a | every numeric field of the tape-only card view, plus each setup tag as 0/1 (count fixed by C1 from code) | all-fields twin; report-only; bar-5 hurdle; offline only, no forward arm |
| P8 | L8's frozen weights on permuted ratings (below) | placebo |

**T8's claim is narrow (CHANGE D).** Haiku sees the whole tape-only card, and each rubric item is a composite, while T8 sees 3 single fields plus
the anchor. So L8 > T8 can only mean **"the ratings add over the 3 named mirror fields + anchor"**, not "LLM reading beats code on the same fields".
T8a is the fair version of the broader sentence; bar 5 requires L8 to beat it too, and any write-up that says more than the narrow claim must
quote L8 vs T8a.

**P8, fully specified (CHANGE E).** Per set, cards in screen order `c_0..c_{N-1}` with card shas `h_0..h_{N-1}`. Seed string
`seed = sha256("hv8-P|" + t_d_iso + "|" + ",".join(h_0..h_{N-1})).hexdigest()`. Sattolo's algorithm (a uniform random N-cycle, which is always a
derangement): `pi = [0..N-1]`; for `i = N-1` down to `1`: `j = int(sha256(seed + "|" + str(i)).hexdigest(), 16) mod i`; swap `pi[i]` and `pi[j]`.
Card `c_k` receives the whole rating triple of card `c_{pi[k]}` (one permutation for all 3 items). No card keeps its own triple. P8's pick can
still equal L8's pick (another card's triple can lift the same pool), so the **matching-pick share** (P8 pick = L8 pick) is reported beside every
P8 number. A C1 test asserts determinism, no fixed point for N = 3..8, and uniformity over N-cycles on synthetic seeds.

**Leave-one-day-out (C2).** 7 folds of 24 h starting at 12:00Z: fold k = `t_d` in [2026-08-21T12 + 24k h, +24 h), k = 0..6 (fold 6 has 23
instants). For each fold, every model is refit on the other 6 folds with the same fixed hyperparameters (P8 uses that fold's L8 weights) and scored
on the held-out fold. Fold IC = mean over the fold's sets (>= 3 labelled cards) of the within-set Spearman of model score vs realised net return
(flat 15%). The final frozen model is fit once on all 7 folds after the LODO numbers are written.

## 5. Pre-clock bars (kill-only, pre-declared now; a failure stops HV8, never triggers a variant)

Offline (built now):
1. **Parse**: valid first ratings >= ceil(0.95 x first-rating calls), after the re-run allowance.
2. **Reliability**: quadratic-weighted kappa between first and retest ratings >= 0.40 on at least 2 of the 3 items (each item reported).
3. **Non-degenerate**: at most 1 item with more than 90% of first ratings on one value.
4. **Training rows**: >= 600 labelled, rated cards (ties and censored dropped), else NOT_DECIDABLE.
5. **LODO screen (strengthened; CHANGE F).** All of:
   - (a) mean over the 7 folds of (IC_L8 - IC_T8) >= **0.10**;
   - (b) IC_L8 - IC_T8 > 0 in **>= 6 of 7** folds;
   - (c) mean IC_L8 > mean IC_T8a;
   - (d) mean IC_L8 > mean IC_P8 (P8's matching-pick share reported).
   Kill-only: passing licenses only the decision to consider a forward test. **Winner's curse:** a passing gap is the maximum of one trial on reused
   exploration hours with an in-sample rubric, so any forward power line uses the shrunk gap
   `g_s = 0.5 x max(0, g - 1.645 x sd_folds(g_k) / sqrt(7))` (g = mean fold gap, g_k the fold gaps), never `g`. The null pass rate of bar 5 is not
   simulated here.

Forward (DEFERRED):
6. **Bridge** (stub day, needs the owner line): 48 stub cards rated on both the plan path and the API path; quadratic-weighted kappa >= 0.60 on at
   least 2 of 3 items (noisy at 48; kill-only).
7. **Rate**: HV7's P0 (same screen) gives >= 10.0 attempts/day and no day under 5.
8. md5 decision-equivalence proof, two seed-1 replays byte-identical, E1 restart drill, no open seal question.

## 6. Forward book (DEFERRED: reads, power, cost, clock)

**Gating (QP ruling on #1010, adopted by the manager).** The forward stage is designed here but not built. It happens only if (a) bar 5 passes,
(b) the registration carries **D >= 10** through an owner-approved horizon extension written before the clock, and (c) the manager decides after
HV7's window-end read. HV7's result informs the forward clock only, never the offline stage. If HV7's L - U reads "no skill detected" on the same
screen, the prior for HV8's forward test falls further.

**Arms and bot ids (forward).** `hv8.0-L8` (promotable), `hv8.0-T8`, `hv8.0-P8`, `hv8.0-R8`, `hv8.0-U8` (or U1..U8 shadow ids, HV7's C2 rule): 5 bot
ids, 1 promotable cell, stake 0.25. T8a is not a forward arm (report-only offline). Cross-arm pricing independence = HV7's C2 hard test at HV8's sha.

**Gate read (L8 only).** As HV7 section 4; CLAUDE.md gate unchanged; expected FAIL.

**Skill claim (read ONCE at window end).** Intersection-union: **L8 - T8 > 0 AND L8 - U8 > 0**, each an exact one-sided sign-flip on daily sums,
p <= 0.025 in all four sub-cells, >= 8 non-zero days each. Size <= 0.025. At D = 8 the minimum p is 1/256 = 0.0039 but ">= 8 non-zero days" then
needs every day (one lost day is NOT_DECIDABLE), which is a reason D >= 10 is required. The claim is the narrow one of section 4 (over the 3 mirror
fields + anchor). Report-only, Holm-ranked: L8 - P8 (with matching-pick share), T8 - U8, P8 - U8, R8 - U8 (fault check), within-set IC of L8 and T8.
A skill pass is not a promotion.

**Power: headline under HV7's QP convention (CHANGE G).** Per-trade d(L - U) ~ N(IC_L x 1.4264 x s, (1.06 s)^2), d(L - T) ~ N((IC_L - IC_T) x
1.4264 x s, (1.41 s)^2) with the L noise shared by both contrasts, no pick coincidence; exact one-sided sign-flip, p <= 0.025, 1,500 draws, seed 1
(`docs/research/hv8/power_hv8_v1.py`, part 1; arithmetic). The sign-flip is scale-free, so s drops out in IC terms. P(both pass), with P(L - U
alone) in brackets:

| D | trades/day | twin IC | L8 IC 0 | 0.05 | 0.10 | 0.17 | 0.25 | 0.30 |
|---|---|---|---|---|---|---|---|---|
| 10 | 20 | 0.00 | 0.005 (0.024) | 0.040 (0.127) | 0.173 (0.381) | 0.515 (0.802) | 0.863 (0.983) | 0.963 (0.999) |
| 10 | 20 | 0.10 | 0.000 | 0.003 | 0.021 | 0.129 | 0.457 | 0.697 |
| 10 | 10 | 0.00 | 0.009 (0.024) | 0.037 (0.094) | 0.094 (0.228) | 0.273 (0.521) | 0.585 (0.831) | 0.745 (0.941) |
| 10 | 10 | 0.10 | 0.002 | 0.011 | 0.025 | 0.091 | 0.279 | 0.451 |

- **L - U alone: MDE IC about 0.17 at 20/day** (power 0.802), matching QP's HV7 figure (per-trade lift about 1.9% at s = 8%, 3.0% at s = 12%);
  about 0.24 at 10/day.
- **Both contrasts (the HV8 claim), D = 10: L8 IC about 0.23 (20/day) or about 0.32 (10/day) if the twin has no skill; above 0.30 at 20/day if the
  twin has IC 0.10.** That is an IC gap over the twin of roughly 0.2 or more.
- At IC <= 0.05 joint power is 0.037-0.040, about the test's size. Expected: "no skill detected"; a non-pass is not evidence of no skill below the
  MDE. No arm or cell is added to raise power.

**Optimistic bound (v0's order-statistic score model, `power_hv8.py` and part 2 of `power_hv8_v1.py`).** Scores = IC x z + noise, picked pool's
return taken from the order statistic, L8/T8 noise correlation kappa. At matched IC it gives smaller variance than the HV7 convention (L - U alone
0.905 vs 0.802 at IC 0.17, D = 10, 20/day). Its MDEs are **lower bounds**, not the headline. P(both):

| D | trades/day | twin IC | kappa | L8 IC 0 | 0.05 | 0.10 | 0.17 | 0.25 | 0.30 |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 20 | 0.00 | 0.5 | 0.003 | 0.053 | 0.250 | 0.715 | 0.975 | 0.997 |
| 10 | 20 | 0.00 | 0.8 | 0.003 | 0.053 | 0.291 | 0.818 | 0.993 | 1.000 |
| 10 | 20 | 0.10 | 0.5 | 0.000 | 0.003 | 0.021 | 0.192 | 0.666 | 0.887 |
| 10 | 20 | 0.10 | 0.8 | 0.000 | 0.001 | 0.029 | 0.258 | 0.807 | 0.972 |
| 10 | 10 | 0.00 | 0.5 | 0.001 | 0.037 | 0.109 | 0.361 | 0.749 | 0.900 |
| 10 | 10 | 0.00 | 0.8 | 0.005 | 0.036 | 0.132 | 0.437 | 0.849 | 0.959 |
| 10 | 10 | 0.10 | 0.5 | 0.000 | 0.003 | 0.021 | 0.106 | 0.365 | 0.599 |
| 10 | 10 | 0.10 | 0.8 | 0.000 | 0.003 | 0.013 | 0.123 | 0.488 | 0.755 |
| 8 | 20 | 0.00 | 0.5 | 0.004 | 0.033 | 0.142 | 0.521 | 0.883 | 0.969 |
| 8 | 20 | 0.10 | 0.5 | 0.000 | 0.002 | 0.017 | 0.143 | 0.498 | 0.746 |

(v0's D = 8 kappa 0.8 rows stay in `power_hv8.py`'s output. QP reran `power_hv8.py`, md5 6d8d48e4513e4574c5604b0083d47d15, about 7 s, and matched
v0's table cell for cell. `power_hv8_v1.py` md5 245793a482f201200711ce3e33f696df.) In these draws L8 and T8 picked the same pool in about 32%
(kappa 0.5) or 54% (kappa 0.8) of periods. Any forward power line uses bar 5's shrunk gap `g_s`, not the LODO gap.

**API cost (forward only; offline spends 0 API).** Prices `tools/haiku_caller.py` `PRICES["claude-haiku-5-5"]` = input 0.10, cache_write 0.125,
cache_read 0.01, output 0.50 USD per million tokens. Tokens from HV7 DESIGN section 5: system 2,874, card about 1,082, task text about 400, output
assumed 120, max_tokens 300.

| Call | Input | Output | USD/call | Calls/day | USD/day |
|---|---|---|---|---|---|
| rate, 1 card, cache write | 2,874 cache-write + 1,482 new | 120 | 0.00056745 | 192 (8 x 24) | 0.1089504 |
| rate, worst (max_tokens 300) | same | 300 | 0.00065745 | 192 | 0.1262304 |
| worst x MAX_ATTEMPTS 2 (HV8 setting) | | | | | 0.2524608 |
| worst x MAX_ATTEMPTS 3 | | | | | 0.3786912 |
| **30-min periods, worst x 2 (CHANGE H)** | same | 300 | 0.00065745 | 384 | **0.5049216, above a 0.30 cap** |
| sequential, calls 2-8 read the cache (report-only) | 2,874 read + 1,482 new | 120 | 0.00023694 | 168 + 24 writes | 0.05342472 |

- Check, row 1: (2,874 x 0.125 + 1,482 x 0.10 + 120 x 0.50) / 1e6 = 0.00056745; x 192 = 0.1089504.
- **30-min periods (CHANGE H):** HV8 is fixed to hourly periods (section 2). A 30-min HV8 needs its own priced design and owner line first.
- **Cacheability not verified (CHANGE H):** whether a 2,874-token system block meets Haiku 5.5's minimum cacheable prompt length was not checked.
  If it does not, every call is plain input ((4,356 x 0.10 + 120 x 0.50) / 1e6 = 0.0004956 per call, cheaper), so the cache-write rows are upper
  bounds, and the sequential cache-read row is wrong.
- **Owner cost line (DEFERRED; not requested now).** HV8 would need its own line, e.g. "HV8 <= 0.30 USD/day, hourly periods, from its stub day to its
  window end, inside the 3.00 USD/day and the 200 USD total", MAX_ATTEMPTS 2. Cap table at that point: HV6 2.20 + HV6 v6.1 0.00 + HV7 0.30 (about 0.34
  with v7.1 K = 3) + HV8 0.30 = 2.80 (2.84) <= 3.00, enforced by HV7's CI sum test extended to HV8. Bridge 48 x 0.00056745 = 0.0272376 USD. D >= 10
  past 11-11 needs the owner's horizon extension (D = 10 to 11-13 from an 11-03 clock would add 0.60 USD at cap; QP: it moves joint power at IC 0.05
  only from 0.033 to 0.053, so ask only after bar 5 passes).
- Helius (forward only): the live screen's chain vetoes need HV8's own RPC client (HV6 pattern, `MAL_HV4_RPC` pinned); credits counted and reported.

**Forward clock: DEFERRED.** Not before HV7's window-end read (HV7 window end 2026-11-11T00Z at an 11-01 clock) and a new API horizon with D >= 10.
No 11-03 registration at D = 8.

## 7. Code: reuse and builder chunks (each <= 40 turns, WIP commit by turn 28, draft PR, no merge)

**Reused as is (imported, not edited).** `tools/hv7_runner.py` (`Hv7Runner.screen`, `_card`, `check_card_keys`, decision log, `FakeChain` /
`NullEngine` patterns from `hv7_dump_requests`), `tools/hv7_rank.py` (`screen_set`, `r_pick`, `u_set`, `iso_from_ms`, `forbidden_keys`),
`tools/hv6_cards.py` (scan, build_card, age guard), `tools/hv6_phantom.py`, `tools/hv4_prompts.py` (`Request`, `card_sha256`, `CACHE_MARKER`),
`tools/arena_state.py`, `tools/arena_paper.py`, `tools/arena_clean_adapter.py`.

| Chunk | Branch | Scope | Tests |
|---|---|---|---|
| C1 | `claude/hv8-rate-core` | `tools/hv8_rate.py` (pure): tape-only card view, T8a field list from code, `build_rate_request` (1 card), `validate_rate`, rubric constants, `placebo_perm` (Sattolo, section 4), retest selector, batch splitter; `tools/hv8_playbooks/RATE_v0.txt`, `tools/hv8_templates/rate.txt` | view equal across FakeChain/real chain on kept keys; no RPC field in a request; byte-compare with RANK_v0 shared sections; validator (missing item, 4, -1, float, string, refusal, empty -> unrated; long `why` -> `why_defect`); request bytes stable; forbidden-key guard; placebo determinism, no fixed point for N = 3..8, whole-triple rule; retest decile; batches <= 64 with retests last |
| C2 | `claude/hv8-calibrator` | `tools/hv8_calib.py`: frozen preprocessing, fixed-iteration L2 IRLS, model JSON + sha256, `score`, L8/T8/T8a/P8 picks, LODO folds, bars 2-5 incl. shrunk gap `g_s`, matching-pick share | known weights recovered on synthetic data; byte-identical model from two runs; refuses features other than the registered sets; ties by screen order; LODO on a planted signal passes and on noise mostly fails; kappa and Spearman arithmetic; trains on first ratings only (a retest row in the input raises) |
| C3 | `claude/hv8-train-dump` | `tools/hv8_train_dump.py` (adapter output -> `Hv7Runner.screen` with FakeChain at the 167 `t_d`, warm-up from 08-14T12, event-based `created_known` with drop count, `--count-only`, prompts + manifest + batch lists, cap 1,537); `tools/hv8_ratings_validate.py` (bars 1-3, `R-BATCHES.jsonl` counts); `tools/hv8_train_label.py` (prices every card, tape end 08-28T12 exclusive, censoring count, refuses unless the sha equals `RATINGS-PIN.md` and the ratings dir is read-only); sh job wrapper | **08-28T12 refusal (row, hour file, `t_d`)**; path refusals; **no-lookahead truncation test**; `created_known` from complete/pool-create only (a dormant pool with a late first print is dropped); label tool refuses without the pin; relative-label ties; wrapper runs under sh |
| R | (no PR; `/data2/mal/hv8-train/`) | count-only pass (MiScusi job), dump job, 24 Haiku batches across sessions, validator, pin, label job, C2 training | bars 1-5 |
| C4-C6 | DEFERRED | forward runner, registration, read tool (v0's scope: HV7 screen equality, N rate calls in `[t_d, t_e)`, concurrent reserve/settle test, pricing independence, stub + bridge, cap-sum CI test, IUT read) | DEFERRED |

## 8. Offline clock (earliest realistic)

One heavy job at a time on research-0. HV6's v6.0 registration holds the 16 GB replay slot to its 2026-10-29T12:00Z fallback deadline; HV7's
replays (real-layout, md5) queue behind it (HV7 freeze about 10-30). HV8's heavy jobs (count-only pass, dump, label pricing) queue behind HV7's.
The ratings run is plan-side, not a research-0 heavy job.

| Step | Earliest |
|---|---|
| This DESIGN v1: QP + reviewer | 10-27 |
| C1, C2 (pure code, parallel) | 10-28 |
| C3 | 10-28..29 |
| Count-only pass, then dump job (after HV7's replays) | 10-30 |
| R: 24 batches across sessions (stop rule after batch 1) | 10-30..11-01 |
| Validator, pin (notebook + commit), label job, LODO, bars 4-5 | 11-01..11-02 |

The forward stage has no date (section 6).

## 9. Seals (from SEALS-BRIEF.md, 2026-10-25) and what HV8 reads

Offline reads: `/data/mal/clean-view/explore-0814/w1..w7` for `[2026-08-14T12, 2026-08-28T12)` only (exploration pool), with the hard 08-28T12
refusal. Never: HV4/Q50/HV6/HV7 outputs or the status/logs of #1561/#1562/#1419/#1421; `/data/mal/meta-capture/` (no listing); any October hour
for pricing (EXP-024 Look-2 seal to 11-14); FLOOR G1 forward/arena outputs; the LaunchLab holdout; the DBC windows (D1 spent, forward window
sealed); the reserved blocks `fresh-0802`, `fresh-0808`, `fresh-0828`. The card view takes field names from code, not from inspecting cards.
Disclosures: explore-0814 is heavily reused exploration (EXP-012 backcheck job #207 computed outcomes there and refused; other screens used it),
so it yields a hypothesis only (ledger rule 2), never a confirmation; the rubric is in-sample (section 3). A forward stage, if run, would be on
walk-2 hours (DEC-030 section 2 R1) and disclosed as such.

## 10. Biggest risks

1. **Offline pass on reused, in-sample hours**: bar 5 is one trial, but the block and the rubric are not fresh; hence the shrinkage.
2. **The screen loses** and selection cannot cover it: expected forward gate FAIL.
3. **Under-powered forward contrast**: under HV7's convention the joint MDE is an IC of about 0.23 at D = 10 and 20/day (about 0.32 at 10/day).
4. **August to November drift**: pump redeploys and fee changes since August can break a calibrator trained there.
5. **Train/live shifts**: no RPC vetoes and stricter `created_known` in training; plan vs API rater (bar 6).
6. **Twin strength**: T8 is narrow by construction; the broad claim needs T8a, which is report-only.
