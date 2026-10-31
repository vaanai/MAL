# Haiku v6 TAIL SPOTTER: an add-only Haiku sizer (design, harness, cost, run plan)

Written 2026-10-31 (`date -u` 21:2xZ, mal-research-0), lane B, branch `claude/haiku-v6-tail-spotter`. Paper only.
**No book has cleared the promotion gate. Nothing here says any bot or book makes money.** No API call was made for this lane (0 USD). The
bars are in [PREREG.md](PREREG.md); this file is the idea, the build, the cost and the run plan.

**Revision 1 (2026-10-31, after QP and reviewer on #1163 at feb88e37, before any paid call):** bars changed as listed at the top of PREREG.md
(B5/B6 day-demeaned, impact haircut in B3/B4/B7/B8, new B9 per-replicate, B1 fails closed, disclosures and the verdict cap in PREREG section 8).
Section 8 item 6 below is struck accordingly. The harness code, tests and PREREG move together; the cost table in section 5 is unchanged
(no prompt changed: prompt sha256 as in the dry runs).

## 1. Idea, and what is new against the dead rows

Rows read: `haiku-v3-2` (nightly playbook reflection with a shuffled-outcome placebo: learning about equal to placebo; the gain was a numeric
dead-pool filter), `haiku-v5-o1` (BUY/SKIP filter on this trigger: 11 of 12 bars failed; every filter below the unfiltered base; skipped trades
averaged above the day mean, so vetoes removed right-tail winners; 59 of 210 playbooks over the 2,500-character cap), `owner-1012-haiku-trader`
(revive trigger, manager decision 2026-10-31: an LLM role that adds trades or size rather than skipping them, with a new mechanism, in a new
out-of-sample window), and Lane A `laneA-size-tilt` (a numeric add-size tilt on the same triggers, killed in-sample: AUC 0.5483676402129253, matched
random-placebo p 0.2517482517482518 flat).

**What is new.** The action space is `boost` or `normal` and nothing else. Every trigger of the base book is still traded at 1x or more, so
the failure that killed v3 and v5 (a veto removing a tail winner) cannot occur: the only thing the bot can change is how much extra it puts on
a trigger whose base outcome is whatever it is. The bot is asked to spot the right tail, not to avoid the left.

**Why that is not enough to read a win.** The in-sample base outcome is positive on average (v5 O1: A = 33.639782713204504 flat and
29.95236332195579 press over the 33 test days), so 2x on any subset adds SOL on average. Lane A measured it: a matched-count random 2x
reached 5.553028762055533 of the numeric tilt's 6.814292036936234 SOL (flat, 32 days). The bars that count are therefore the selection ones
(L-P, L-F, L-N), not L-A.

**What it cannot do.** It cannot reduce a loss. A boosted loser costs twice the base loss (a base trade loses at most about 0.1 SOL).
A prior for a PASS is low: the card has shown weak information about the right tail (Lane A AUC 0.548; v5's numeric veto was no better than base).
The experiment costs about 2.3 USD and answers whether Haiku reads something in the card that a rank-transformed logistic does not.

## 2. Data

Identical to v5 DESIGN section 2: the 2,825 frozen H5 BOOST-floor triggers on five exploration blocks, el2 outcomes, burn-in 3 days, 33 test
days, 2,619 test triggers; shas in PREREG section 1. Nothing is read from October, the tape or any sealed path.

## 3. The bot (what is different from v5)

| | v5 | v6 |
|---|---|---|
| Reply | `{"act": BUY or SKIP, "size": 0.5, 1, 2, "why"}` | `{"call": "boost" or "normal", "why"}` |
| Effect | SKIP removes the trade; size is report-only | boost = 2x (one extra standard slice); normal = 1x; a skip cannot be expressed |
| Parse failure | falls back to BUY | falls back to normal, counted |
| Playbook cap | 2,500 characters, told to the model only | **2,000 characters, cut by code** (`cap_playbook`: back to the last whole line); a re-check before every day refuses a playbook over the cap; truncations are counted, not failures |
| Rule vocabulary | SKIP, or size 0.5 or 2; up to 12 rules | `BOOST if <numeric condition on named card fields>`; up to 8 rules (told); told to aim at the right tail and to boost about one row in four at most |
| Reflection feedback | cards, decision, base `ret_pct`, bought or skipped | cards, call, base `ret_pct`, boosted or normal; every row is a traded row |
| Arms | L, P (shuffled-outcome reflection), F (empty playbook), A, N, R | A, L, F (frozen copy of L's night-1 playbook), P (random boost at L's rate), N (Lane A's logistic) |
| API arms | L, P, F | L and F only; P, N and A are code |

The card (35 decision-time fields), the glossary, the sampling cap on feedback rows (200 a night, counted), the model, the output limits (decide 400,
reflect 10,000 tokens, thinking disabled for decisions and adaptive for reflections) and the 60 s caller timeout margin are v5's, reused by
import. The word "boost" in the reply means the extra slice; the prompt says it has nothing to do with the BOOST wallet the rule watches.

## 4. Walk-forward and leak guards (in code, tested)

All v5 guards, plus: (i) F copies L's playbook at the START of the day after L's first successful reflection, never in the day L wrote it (test:
F's decisions on day 1 use v0; the playbook file sha of F from day 2 equals L's night-1 playbook; a failed night 1 moves the copy to the first success);
(ii) the cap guard fails closed if the cap function is bypassed (test); (iii) the placebo draw function takes no outcome argument and its calls
are identical when the outcomes are changed (test); (iv) a perturbation test flips every outcome from day D* on and shows that every request up to and
including D*'s decisions is byte-identical and that the first differing request is L's reflection after D*; (v) N is fitted only on outcomes
known before the day starts (past-only and exact-boundary tests, both non-vacuous: the fit set has at least 300 rows); (vi) the scorer needs
the v6 and v5 file hashes to equal the run's.

## 5. Cost (no guessing; `dry` on the real trigger layout, no outcome read, no API)

| Run | Decide calls | Reflect calls | Max body | Expected, v5 O1 measured rates | Expected, v5 P1 rates | Every reflection at cap | Reserve-rule bound |
|---|---|---|---|---|---|---|---|
| Pilot: first 3 days, L and F x 1 rep | 412 | 2 | 19,442 B | 0.0552 USD | 0.0652 USD | 0.0682 USD | 0.4383 USD |
| O1: 36 days, L and F x 3 reps | 16,950 | 105 | 43,567 B | 2.3323 USD | 2.9373 USD | 3.0948 USD | 19.4317 USD |

`dry.json` sha256 `650b26dc8cf7a49d64d3846b6bf323dbb4f4d2c53b2d52372fc42a0345635603` (O1) and `18385819f97283e2d1f4d79428eb2fc1f3d96f0738483a4eda6e46c2e93cff97`
(pilot), under `/data2/mal/scratch/haiku-v6/dry-full` and `dry-pilot` (regenerate with the `dry` command; prompt sha256 in the file).
"v5 O1 measured rates" are 3.06505256 USD / 25,425 decisions and 0.57796121 USD / 210 reflections from the QP ruling's ledger split. v5 O1 cost
3.7286 USD on its ledger for three LLM arms (that total includes the 0.0856299 USD pilot P1; the O1 calls alone were 3.64301377 USD); v6 has two and half the reflections. The reserve-rule bound is the caller's worst case (every byte a token, cache-write rate), not a forecast.

**Caps (coded, tested):** pilot **1 USD** (`--max-days` set), O1 **8 USD** on the ledger (cumulative with the pilot; the v6 ledger is new and
separate from v5's). `tools/haiku_caller` additionally stops any call whose worst case would cross the ledger budget. The 8 USD ceiling is about 2.7x
the pilot-P1-rate expectation. Time: v5 O1 took 9 h 41 min for 25,635 calls (22:01:59Z to 07:43:21Z), so O1 here is about 6 to 7 h (serial calls).

## 6. Run plan (the manager; each step a MiScusi job under sh; QP reads the PR first; nothing below has been run)

Working directory: a clean checkout at the merged sha (`git -C /home/claude/.miscusi/repos/vaanai/MAL worktree add --detach /data2/mal/wt/v6-run <sha>`).
`S=/data2/mal/scratch/haiku-v6`, `E=/data2/mal/scratch/haiku-v5/export-o1` (read only; pinned by sha). Pilot, O1 and every rerun run at the same sha.

1. (no API) `sha256sum $E/triggers.jsonl $E/outcomes.jsonl` must print the two shas in PREREG section 1.
2. (API, ceiling 1 USD) Pilot, `<notebook id>`, `<T sha>`, `<O sha>` filled by the manager:
   `set -a; . /var/lib/mal/anthropic/anthropic.env; set +a; /data/mal/venv/bin/python -m tools.haiku_v6_harness run --triggers $E/triggers.jsonl --outcomes $E/outcomes.jsonl --out-dir $S/run-p1 --model haiku --live --approval <notebook id> --budget-usd 1 --ledger $S/ledger.jsonl --save-dir $S/store --reps 1 --max-days 3 --expect-sha256 triggers=<T sha>,outcomes=<O sha>`
   then, only if it exited 0 (chain with `&&`): `/data/mal/venv/bin/python -m tools.haiku_v6_harness pilot --run-dir $S/run-p1 --ledger $S/ledger.jsonl --save-dir $S/store`.
   Read the go/no-go list in PREREG section 5.
3. (API, ceiling 8 USD cumulative) O1: the same `run` with `--reps 3`, no `--max-days`, `--budget-usd 8`, `--out-dir $S/run-o1`. A caller stop (exit 4) is rerun
   into a NEW `--out-dir` with the same ledger and store (saved calls replay at no cost). The pilot's requests are identical to O1's replicate 1 on days 1 to 3
   and replay (disclosed). One run per ledger (`ledger.jsonl.v6-run.lock`).
   Spend conditions (QP): the approval note quotes the owner's 2026-10-28 API approval and both caps; record `ledger_summary` after the pilot and after O1.
4. (no API) `/data/mal/venv/bin/python -m tools.haiku_v6_harness score --run-dir $S/run-o1 --triggers $E/triggers.jsonl --outcomes $E/outcomes.jsonl --out $S/score-o1.json`.
   Only a score with `o1_verdict_eligible` true (haiku, arms L and F, 3 replicates, no `--max-days`, el2) is the O1 verdict. Quant-proof reads it before any sentence about it.

## 7. Forward path and clock

Only after PASS_OFFLINE and QP, as v5 DESIGN section 9: a new arena book under DEC-031 with its own registration, frozen code and prompts, md5
decision-equivalence replay, P0 and N + 1; F and P run beside it as shadows. The learner needs H5 outcomes within minutes of each exit, which EXP-024
Look-2 seals for October hours of a V-range pool in the first 360 s until at least 2026-11-14T00:00Z. Sizing at 0.2 SOL in the pool is not priced
here; the Lane A haircut formula is reported as a sensitivity only.

## 8. Choices I made that the manager may strike or change before the pilot

1. F = a copy of L's night-1 playbook (the brief's wording). v5's F was an empty playbook, which here would equal A (it never boosts).
2. B2 is the worst replicate's boost share, not the mean. B7 and B8 apply to L-A and L-P, not to L-F.
3. The 50-boost decidability floor (PREREG section 3). Without it a learner that boosts 5 triggers could pass or fail on noise.
4. P is code (random at L's rate), not an LLM arm. Dropping v5's shuffled-outcome LLM placebo saves about 1.2 USD (a third of v5's 3.7286 USD); L-F now carries the "does it learn" question.
5. N = Lane A's model with the top-k match; it needs at least 300 fit rows, so N does nothing on the first few test days (L-N equals L-A on those days).
6. ~~Sized P&L is linear; no impact is applied in the bars.~~ Struck by QP on #1163: the impact haircut (4 s^2 / q_post_trig on the extra slice) is now in B3, B4, B7, B8 and B9, and the raw and haircut views must both hold. Break-even stays report-only.
7. (added after review) B5 and B6 are exposure-neutral (raw and day-demeaned), B9 asks for 2 of 3 replicates, B1 fails closed, and a PASS is capped at a candidate for a fresh-data pre-registration (PREREG sections 3, 4, 8).

## 9. Disclosures

- In-sample on every number; v5 and Lane A read the same days, and the add-only action was chosen after reading v5's O1 result on them (PREREG section 7).
- The reflection prompt names the right tail as the target and asks for a minority of boosts; both are design choices, stated, and shared by L and F.
- Not measured: tokens per byte of the v6 prompts, cache-read share, parse rate, boost share, any v6 outcome. Costs use v5's measured rates.
- The library row for this idea is not added by this PR (the brief names no library edit); the proposed row text is in the lane report.
