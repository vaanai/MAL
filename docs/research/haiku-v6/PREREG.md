# Haiku v6 TAIL SPOTTER: pre-registration of the offline read (O1)

Written 2026-10-31 (`date -u` 21:2xZ, mal-research-0), lane B, branch `claude/haiku-v6-tail-spotter`. Paper only.
**No book has cleared the promotion gate. Nothing here says a bot or book makes money.** No API call was made for this lane (0 USD). Every
number the offline read will produce is IN-SAMPLE exploration (H5 was chosen on explore-0814; 14+ families have read these days; v5 read
them too). A PASS here is a hypothesis that licenses drafting a forward registration, never gate evidence. Design, cost and run plan:
[DESIGN.md](DESIGN.md). Code: `tools/haiku_v6_harness.py`, tests `tools/test_haiku_v6_harness.py`.

**Revision 1 (2026-10-31, after QP CHANGES comment 6549473252 and reviewer REQUEST CHANGES at head feb88e37, before any paid call):**
(C1) B5 and B6 now also hold on day-demeaned outcomes (a larger boost count alone passed B5 by leverage in QP's probe); (C2) B3, B4, B7, B8 now
also hold on outcomes net of the extra slice's impact; (C3) new B9, L beats its own placebo in at least 2 of 3 replicates; (C4, reviewer R1)
B1 fails closed when a decided L replicate has no reflection count or zero reflections; (C5) multiplicity, timing and winner's-curse
disclosures in section 8 and an explicit verdict cap; (R2) one test per deciding clause. 16 booleans now, not 14.

## 1. What is frozen

- The registration is the code at the merged sha. `run_start.json` records `harness_sha256` (v6), `v5_harness_sha256` (the v5 file v6
  imports) and `prompt_sha256` before the first API call; `score` refuses unless both file hashes equal the run's. Any change after the
  first paid call is a new registration (a new prompt hash also misses the replay store and pays again).
- Inputs, the same as v5 (QP-RULING-1031-haiku-v5-o1): `/data2/mal/scratch/haiku-v5/export-o1/triggers.jsonl` sha256
  `0154ee5b1898a091c6efd00085541a7df0d2d07f2377cd20b191039ab6641174` (2,825 triggers, 36 UTC days) and `outcomes.jsonl` sha256
  `5f5b9b7bde0450d2e6aae6ff9829ec8c859688f316c1fe561b03f4f0cddd49a2`, basis el2 (P55 exit lag 2), SOL at the 0.1 SOL stake, flat 15% and
  pressure-fail. A run pins both with `--expect-sha256`.
- Day split, the same as v5: a burn-in of the first 3 UTC days (206 triggers), then **33 test days with 2,619 test triggers**.
- Seals: the v5 ones, inherited by import (blocks in the ledger exploration pool, nothing at or after 2026-10-01T00Z, forbidden path
  fragments, `outcome_known_ms` exactly decision + 15 min). No tape, no October hour, no sealed path is opened by this lane.

## 2. Arms (all decide on every trigger of the same 36 days; replicates r = 1, 2, 3)

Every arm trades every trigger. An arm's size on a trigger is 1 (normal) or 2 (boost: the standard 0.1 SOL slice plus one extra slice).
There is no skip anywhere in v6; an unparseable Haiku reply falls back to normal (never to a skip) and is counted.

| Arm | What | API |
|---|---|---|
| A | take-all, flat 1x | no |
| L | learner: Haiku answers `boost` or `normal` per trigger from a PLAYBOOK it rewrites every night from the closed triggers of earlier days (true outcomes). The playbook is cut by code at 2,000 characters | yes |
| F | frozen: Haiku with a frozen copy of L's night-1 playbook (same replicate), taken at the start of the day after L's first successful reflection (night 1 unless that reflection fails); v0 before; never reflects | yes |
| P | placebo: random boost at L's daily boost rate. For each replicate r and test day D, L_r's boost count k on D is placed on D's triggers at random, 1,000 draws, numpy `default_rng` seeded by sha256 of `v6|P|r|D`; the seed set never sees an outcome | no |
| N | numeric tilt matched to L's boost count: Lane A's declared model (see section 7), top-k triggers by score per replicate and day, k = L_r's count | no |

Pre-trade fields are the same 35 card fields as v5 (no outcome, mint, date or block in any prompt). L and F see identical prompt text apart
from the playbook and the opaque card nonce.

## 3. Bars (all must hold, under BOTH fail models: flat 15% and pressure-fail)

Notation. For an arm X, m_X(t) is its expected size multiplier on trigger t (A: 1; L and F: 1 plus the share of replicates that boosted t;
P: 1 plus the share of draws and replicates that boosted t; N: 1 plus the share of replicates whose top-k holds t). Contrast X-Y has daily
value d_D = the sum over day D of (m_X - m_Y) x v_t, where v_t is one of three views of the outcome in SOL at the 0.1 SOL stake:
**raw** o_t; **haircut** o_t - h_t, with h_t = 4 s^2 / q_post_trig (s = 0.1 SOL; Lane A DECLARE section 5, the extra round-trip impact of
doubling the stake in a constant-product pool; it is charged on the difference in extra slices; first order, and it understates the exit leg
on winners, which grows about as x^1.5 for a gross return multiple x, per QP on #1163); **day-demeaned** o_t - mean of all of day D's
triggers (removes the leverage of a larger boost count on a positive-mean base). Over the 33 test days: `total` = sum of d_D; `boot_lo5` =
5th percentile of the mean of 1,000 date-cluster bootstrap resamples of the 33 d_D (1,000 draws, seed 1); `ex_best_day` = total minus the
largest d_D; `ex_top3` = total minus the 3 largest per-trigger contributions (m_X - m_Y) x v_t; `late_half` = sum of d_D over the later 17
of the 33 days.

- **B1 format, fails closed.** Parse rate >= 0.98 for each of L1-L3 and F1-F3. Reflection success >= 0.95 for each of L1-L3, and a count with
  at least one reflection must exist for every decided L replicate (a missing replicate or one with zero reflections fails B1; so do counts
  for the wrong replicates). A reflection succeeds if its reply holds a closed, non-empty `<playbook>` block (the last closed block is used). A
  playbook over 2,000 characters is truncated by code (to the last whole line within the cap) and is NOT a failure; an unclosed, empty or
  nested block is a failure and keeps the old playbook.
- **B2 boost share.** On test days, each L replicate boosts at most 0.30 of the triggers (the worst replicate, not the mean).
- **B3 L beats base.** L-A: total > 0 and boot_lo5 > 0, on raw AND haircut views.
- **B4 L beats random.** L-P: total > 0 and boot_lo5 > 0, on raw AND haircut views. Both arms use the same boost count per replicate and
  day, so this is the leverage-neutral selection test (extra size on a positive-mean base adds SOL whatever is picked).
- **B5 L beats the frozen night-1 playbook.** L-F: total > 0 on raw AND day-demeaned views. The demeaned view makes it exposure-neutral. B5
  therefore tests "continued nightly reflection beats a night-1 playbook"; it does **not** show that the bot learned from true outcomes (there
  is no shuffled-outcome LLM arm), and no PASS text may say so.
- **B6 L is not below the numeric tilt.** L-N: total >= 0 on raw AND day-demeaned views (N is off on the first test days, where raw L-N is leverage).
- **B7 not a few trades or a day.** For both L-A and L-P, on raw AND haircut views: ex_best_day > 0 and ex_top3 > 0.
- **B8 late-half gain.** For both L-A and L-P, on raw AND haircut views: late_half > 0.
- **B9 per-replicate consistency.** L-P total > 0 in at least 2 of the 3 replicates (each replicate against its own placebo draws), on raw AND
  haircut views.

B3 to B9 are evaluated once per fail model; with B1 and B2 that is **16 booleans**. **PASS_OFFLINE only if all 16 hold; otherwise
KILL_OFFLINE.**
**NOT_DECIDABLE** if there are fewer than 20 test days, fewer than 1,000 test triggers, L or F decisions are missing, or L boosts fewer than 50
test triggers on average over its replicates (a decidability floor I added; it makes a near-empty learner undecidable instead of a PASS or KILL).
A NOT_DECIDABLE caused by that floor ends v6 as frozen, the same as a KILL: no re-run and no prompt change on these days. Only a technical
NOT_DECIDABLE (missing decisions) allows a re-run, at the same sha, with saved calls replayed.

## 4. What a PASS and a KILL mean

- **Verdict cap.** At best a PASS_OFFLINE is a candidate for a fresh-data pre-registration under DEC-031 (own clock, frozen code and prompts,
  N + 1, P0, gate read). It is never a candidate book, never edge evidence, and no sentence may say the bot learned from outcomes. The same
  cap is in the `license` string of `score()`.
  The library revive trigger for this family (owner-1012-haiku-trader, manager decision 2026-10-31) asks for an LLM role that adds size, with a
  new mechanism, **in a new out-of-sample window**. This read re-uses the same 36 in-sample days, so it is a kill test before that window, not
  the window. EXP-024 Look-2 keeps H5 outcomes on any October hour sealed until at least 2026-11-14T00:00Z (DESIGN section 9).
- KILL_OFFLINE ends v6 as frozen. No further paid trial (thinking-on, 5 replicates, a second trigger) without a new row and registration.
- c + 1 when O1 runs, whatever its verdict. The pilot spends no c (no score).

## 5. Spend (coded in `check_budget`; the ledger is cumulative and separate from v5's)

- Pilot: first 3 days, L and F x 1 replicate (412 decisions, 2 reflections), no score: ceiling **1 USD** (coded, enforced with `--max-days`).
  Expected 0.0552 USD at v5 O1's measured per-call rates, 0.0652 USD at v5 pilot-P1 rates.
- O1: L and F x 3 replicates, 36 days (16,950 decisions, 105 reflections): ceiling **8 USD** on the ledger (coded), cumulative with the pilot.
  Expected 2.3323 USD at v5 O1's measured rates, 2.9373 USD at pilot-P1 rates, 3.0948 USD with every reflection at its output cap. For scale,
  v5 O1 (three LLM arms, 25,425 decisions and 210 reflections) cost 3.7286 USD on its ledger.
- Conditions (QP on #1163): a new ledger (`$S/ledger.jsonl`); the approval notebook entry quotes the owner's 2026-10-28 API approval and names both
  caps; the ledger total is recorded after the pilot and after O1.
- Pilot go/no-go (structure only, `pilot` command, no outcome): parse >= 0.98; both reflections succeed; no reflection over 55 s and no
  caller timeout; `max_tokens` stops under 10% of reflections and under 1% of decisions; the ledger match is true; the projected O1 cost stays
  under 8 USD. Boost share per replicate is printed (decisions only); the manager asks QP before O1 if any replicate boosts more than 60% of
  pilot triggers.

## 6. Report-only (never deciding, not selected among)

Per-replicate L-A; L-P by replicate and by block (explore-0814 against the others); the placebo p-value (share of the 1,000 placebo draws at or
above L's extra); L's boosted-minus-day-mean per boost; top-decile recall and lift for L, F, N and P; the break-even extra cost per boosted
slice; the haircut contrasts for L-F and L-N and the demeaned contrasts for L-A and L-P; extra SOL per test day after the haircut at the 0.1 SOL
stake (the haircut grows with the square of the stake; no forward stake is assumed); L rep 1's sized book and A's book under the gate's
arithmetic (labelled in-sample); F-A, N-A, P-A. Sized P&L is linear in size. Not built: an x-aware exit-leg haircut sensitivity (QP's optional item).

## 7. Numeric comparator N and what is known before the read

- N is the model Lane A declared (`/data/mal/hunt-1008/ops/reports/2026-10-31/laneA-size-tilt.md`, DECLARE sha256
  `00481f11c1164120e71fc234f483043c68f5e4aa9a06e4c3b8d64fb99ee40318`, sections 2 to 4): the 35 card fields each turned into a mid-rank
  percentile of the fit set minus 0.5 (a missing value maps to 0), an L2 logistic with C = 1, label = outcome at or above the fit set's 90th
  percentile (top decile), fitted each test day on triggers whose outcome was known before that day's 00:00Z, none below 300 fit rows. It is
  reimplemented in numpy (a test checks it against sklearn `LogisticRegression(C=1.0)`). Differences from Lane A: N boosts L's count (top k)
  instead of a fixed 80th-percentile threshold, the test days are v5's 33, and the fit set is outcomes known strictly before the day starts
  (Lane A's script uses at or before; 0 of 2,825 triggers have an outcome-known instant exactly at a UTC midnight, per the reviewer, so the
  fit sets are identical on this export).
- Lane A killed that tilt in-sample on the same triggers: its test AUC against the top-10% label was 0.5483676402129253 and its matched-count
  random-placebo p was 0.2517482517482518 (flat) and 0.2087912087912088 (press), bar <= 0.10. So the comparator is weak, B6 is not a hard bar by
  itself, and B4 (random placebo) is the one that matters.
- This design is informed by in-sample reading. I read QP-RULING-1031-haiku-v5-o1 (skips removed right-tail winners on these same 33 days)
  before choosing an add-only action, and the reflection prompt says in words that the target is the right tail. No number from either is in
  any prompt. A PASS is therefore more likely to be a forking-paths result than a clean one, which is why the read is only a kill test.
- Not measured: any outcome of v6, the real parse rate, the real boost share, impact at 0.2 SOL. Nothing is priced by this document.

## 8. Multiplicity, timing and winner's curse (QP C5)

- **Looks already taken on this export before v6** (the same 2,825 triggers, 36 days): v5 O1 (9 LLM arm-replicates, plus the numeric N and the
  random R arm); Lane A (1 deciding cell and 6 report-only sensitivities, of which `label_top20` passed all 8 of Lane A's bars, report-only,
  7 looks in all); Lane C (4 exit variants, per QP on #1163); earlier H5 filter and veto work on overlapping days (library rows lib-c-N1,
  lib-a-A39 and the h5-lossfilter MAP). v6 adds one deciding cell. No multiplicity correction is applied; a PASS is read with this count in mind.
- **H5 was chosen on explore-0814**, which holds 1,701 of the 2,825 triggers and 12 of the 33 test days (counted here from trigger instants and
  block names, no outcome). The late half (2026-09-07 to 2026-09-25) is 17 days and 982 triggers, none in explore-0814, so B8 is also the check
  that a result is not just the block H5 was chosen on.
- **Timing.** This document was written after Lane A's results were available. The reflection prompt's wording ("right tail", "about one row in
  four") is in commit 20a681c2 (2026-10-31T21:17:32Z), written before I read Lane A's report (I first saw that it existed at 21:22:59Z; the file
  dated from about 21:17Z). The reflect prompt sha256 `825347b3...` is unchanged since the first dry run. That wording came from reading
  QP-RULING-1031-haiku-v5-o1 (skips removed tail winners), not from Lane A. I did know Lane A's `label_top20` pass when writing section 3; the
  deciding label for N is its top-10% primary, not that sensitivity.
- **Lane A's contaminated rows.** Lane A printed 5 raw outcome rows (`head`) before its DECLARE. N inherits that; it is negligible and stated.
- **Winner's curse.** A PASS effect size is an upper estimate (the base, the add-only action and the card were all chosen after reading these
  days). A forward registration must not power its read on the O1 point estimate unshrunk; at most half the O1 L-P per boost.
- **Verdict cap and B5 meaning:** see section 4 and B5.
