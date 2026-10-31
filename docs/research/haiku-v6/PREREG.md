# Haiku v6 TAIL SPOTTER: pre-registration of the offline read (O1)

Written 2026-10-31 (`date -u` 21:2xZ, mal-research-0), lane B, branch `claude/haiku-v6-tail-spotter`. Paper only.
**No book has cleared the promotion gate. Nothing here says a bot or book makes money.** No API call was made for this lane (0 USD). Every
number the offline read will produce is IN-SAMPLE exploration (H5 was chosen on explore-0814; 14+ families have read these days; v5 read
them too). A PASS here is a hypothesis that licenses drafting a forward registration, never gate evidence. Design, cost and run plan:
[DESIGN.md](DESIGN.md). Code: `tools/haiku_v6_harness.py`, tests `tools/test_haiku_v6_harness.py`.

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
P: 1 plus the share of draws and replicates that boosted t; N: 1 plus the share of replicates whose top-k holds t). o_t is the outcome in SOL at
the 0.1 SOL stake under the fail model. Contrast X-Y has daily value d_D = the sum over day D of (m_X - m_Y) x o_t. Over the 33 test days:
`total` = sum of d_D; `boot_lo5` = 5th percentile of the mean of 1,000 date-cluster bootstrap resamples of the 33 d_D (1,000 draws, seed 1);
`ex_best_day` = total minus the largest d_D; `ex_top3` = total minus the 3 largest per-trigger contributions (m_X - m_Y) x o_t;
`late_half` = sum of d_D over the later 17 of the 33 days.

- **B1 format.** Parse rate >= 0.98 for each of L1-L3 and F1-F3. Reflection success >= 0.95 for each of L1-L3. A reflection succeeds if its
  reply holds one closed, non-empty `<playbook>` block. A playbook over 2,000 characters is truncated by code (to the last whole line within
  the cap) and is NOT a failure; an unclosed, empty or nested block is a failure and keeps the old playbook. Missing counts fail closed.
- **B2 boost share.** On test days, each L replicate boosts at most 0.30 of the triggers (a limit on the worst replicate, not the mean).
- **B3 L beats base.** L-A: total > 0 and boot_lo5 > 0.
- **B4 L beats random.** L-P: total > 0 and boot_lo5 > 0. Both arms use the same boost count per replicate and day, so this is the
  leverage-neutral selection test (extra size on a positive-mean base adds SOL whatever is picked).
- **B5 L beats the frozen night-1 playbook.** L-F: total > 0.
- **B6 L is not below the numeric tilt.** L-N: total >= 0.
- **B7 not a few trades or a day.** For both L-A and L-P: ex_best_day > 0 and ex_top3 > 0.
- **B8 late-half gain.** For both L-A and L-P: late_half > 0.

B3 to B8 are evaluated once per fail model; with B1 and B2 that is 14 booleans. **PASS_OFFLINE only if all 14 hold; otherwise KILL_OFFLINE.**
**NOT_DECIDABLE** if there are fewer than 20 test days, fewer than 1,000 test triggers, L or F decisions are missing, or L boosts fewer than 50
test triggers on average over its replicates (a decidability floor I added; it makes a near-empty learner undecidable instead of a PASS or KILL).

## 4. What a PASS and a KILL mean

- PASS_OFFLINE licenses only drafting a forward paper registration under DEC-031 (own clock, frozen code and prompts, N + 1, P0, gate read).
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
- Pilot go/no-go (structure only, `pilot` command, no outcome): parse >= 0.98; both reflections succeed; no reflection over 55 s and no
  caller timeout; `max_tokens` stops under 10% of reflections and under 1% of decisions; the ledger match is true; the projected O1 cost stays
  under 8 USD. Boost share per replicate is printed (decisions only); the manager asks QP before O1 if any replicate boosts more than 60% of
  pilot triggers.

## 6. Report-only (never deciding, not selected among)

Per-replicate L-A; the placebo p-value (share of the 1,000 placebo draws at or above L's extra); L's boosted-minus-day-mean per boost;
top-decile recall and lift for L, F, N and P; the break-even extra cost per boosted slice; the Lane A haircut formula (4 s^2 / q_post_trig,
Lane A DECLARE section 5, not independently verified) applied to L-A and L-P; L rep 1's sized book and A's book under the gate's arithmetic
(labelled in-sample); F-A, N-A, P-A. Sized P&L is linear in size: impact at 0.2 SOL is not repriced.

## 7. Numeric comparator N and what is known before the read

- N is the model Lane A declared (`/data/mal/hunt-1008/ops/reports/2026-10-31/laneA-size-tilt.md`, DECLARE sha256
  `00481f11c1164120e71fc234f483043c68f5e4aa9a06e4c3b8d64fb99ee40318`, sections 2 to 4): the 35 card fields each turned into a mid-rank
  percentile of the fit set minus 0.5 (a missing value maps to 0), an L2 logistic with C = 1, label = outcome at or above the fit set's 90th
  percentile (top decile), fitted each test day on triggers whose outcome was known before that day's 00:00Z, none below 300 fit rows. It is
  reimplemented in numpy (a test checks it against sklearn `LogisticRegression(C=1.0)`). Differences from Lane A: N boosts L's count (top k)
  instead of a fixed 80th-percentile threshold, and the test days are v5's 33.
- Lane A killed that tilt in-sample on the same triggers: its test AUC against the top-10% label was 0.5483676402129253 and its matched-count
  random-placebo p was 0.2517482517482518 (flat) and 0.2087912087912088 (press), bar <= 0.10. So the comparator is weak, B6 is not a hard bar by
  itself, and B4 (random placebo) is the one that matters.
- This design is informed by in-sample reading. I read QP-RULING-1031-haiku-v5-o1 (skips removed right-tail winners on these same 33 days)
  before choosing an add-only action, and the reflection prompt says in words that the target is the right tail. No number from either is in
  any prompt. A PASS is therefore more likely to be a forking-paths result than a clean one, which is why the read is only a kill test.
- Not measured: any outcome of v6, the real parse rate, the real boost share, impact at 0.2 SOL. Nothing is priced by this document.
