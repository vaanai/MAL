# Haiku v5: a self-improving filter on one structural trigger (design + offline harness + cost-capped run plan)

Written 2026-10-30 from 15:13Z (`date -u`, mal-research-0), lane haiku-trader, branch `claude/haiku-v5-harness`. Paper only.
**No book has cleared the promotion gate. Nothing here says any book or bot makes money.** No API call was made for this lane (0 USD).
The offline result, whatever it is, is a hypothesis on in-sample exploration days, never gate evidence.

**Revision 1 (2026-10-30, from 15:38Z), on QP CHANGES (comment 6526732116) and reviewer CHANGES (comment 6526743241) at a39d2bd7:**
(QP1) the loader refuses any trigger whose `outcome_known_ms` is not exactly decision + 15 min and never uses the file's value;
(QP2) basis el2 (P55 exit lag 2) is required by code for every Haiku run and every score, every outcome row carries its basis, and the
frozen fallback is deleted (manager: not for P1 either); (QP3) coded ceilings: `--budget-usd` <= 16 for any Haiku run, <= 1 with
`--max-days`; (QP4) the scorer refuses unless its harness sha256 equals the run's, and score.json records model, arms, reps, max_days,
basis and both harness sha256 values; (QP5) on capped nights L and P reflect on the same sampled rows; (R1) an exclusive run lock next to
the ledger; (R2) tests at the exact freeze instant, at the numeric arm's fit boundary, and with each leak guard forced to fail (both
boundary tests were mutation-checked: a `<=` at the freeze and a decision-time fit filter each fail them); (R3) section 8 rewritten with
paths, working directory and the same-sha rule. Also taken: `run_start.json` before the first call, symlink-resolved path seals, a
scorer refusal on missing decisions, the model in the verdict line, a ledger-total "run done" line, a structure-only `pilot` read
(latency against the 60 s caller timeout, `max_tokens` stop share, cache-read share, tokens per byte), a report-only count/selection
split of each arm's skips, and a count of P nights with a single feedback row.

## 1. Idea (manager's hypothesis, made testable)

Haiku is weak as a raw ranker (P92: Haiku IC 0.02723 vs kNN 0.296; P93: AUC 0.4476, CI90 [0.3384, 0.5499]). Haiku v3's self-improving veto
did not show learning: the learning arm was within about one replicate sd of the shuffled-outcome placebo, and the gain was a dead-pool
filter a numeric rule reproduces (`/data/mal/hunt-1008/push-1016/hunt8/haiku-v3/QP-REVIEW.md` section 3).

v5 changes the question. The bot never picks winners from a universe. It sits on **one fixed structural trigger** (the H5 BOOST-floor entry)
and learns **when to act on it**: BUY or SKIP each trigger, from a playbook it rewrites every night from its own closed paper triggers.
It is built to fail fast on exactly what sank v3: (i) learning must beat a placebo that gets the same calls with deranged outcomes;
(ii) it must beat a frozen no-learning copy; (iii) it must beat a numeric walk-forward veto at the **same daily skip count** (the
"dead-pool filter needs no LLM" lesson); (iv) the learning gain must also hold on the later half of the days, when memory is longest.

## 2. Data (exploration only)

- Triggers: the 2,826 frozen H5 binding trades (`/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`, sha256
  `ac9b14982448cc860ef51b7779966bacc692ee016697a1fec9cc2d3bb6c22249`), Q* 40, 0.1 SOL, exit at s0 + 330 s. Each row is a trigger.
- Cards: `features_decision_time.parquet` (sha256 `da22723d6e1d2bef60b50db0c05ee94ba194eeb37c6223e03d914cbc0eb9ee45`), causal fields only
  (MAP.md section 5; every `f_*` uses path rows 0..i or raw-tape rows before the trigger). Excluded: `qa_*`, `f_trig_t_frozen_s` and
  `f_boost_spent_frozen_sol` (look-ahead, MAP section 6), `f_mayhem` (no variance), mint, day, block.
- Blocks, all in the ledger exploration pool (`docs/HOLDOUT_LEDGER.md`): explore-0814, fresh-0903 (spent by EXP-012, moved to exploration),
  exp011-0909 (moved to exploration 10-06), fast-pool-0918, oracle-insample-0922. QP ruled on #1087 that fresh-0903 and exp011-0909
  stay in (both are exploration pool in the ledger, the controlling record; v5 reads only the derived h5-lossfilter parquet and P55 rows).
  Neither block can serve as a confirmation holdout, and v5 results on them are in-sample.
- Real-layout precount (2026-10-30T15:23Z, outcome-free: the export read only `day, mint, blk, mbt, mbt_utc` of the trades file):
  **2,825 triggers** of 2,826 (1 `qa_exit_truncated`, the 08-28 boundary trade MAP section 2 flags), 0 outside a block window, 36 UTC days,
  4 to 250 triggers per day. Per block: explore-0814 1,701; fresh-0903 307; exp011-0909 291; fast-pool-0918 233; oracle-insample-0922 293.
  `triggers.jsonl` sha256 `0154ee5b1898a091c6efd00085541a7df0d2d07f2377cd20b191039ab6641174`.

## 3. The bot

**Sees** (decision call): a static system block (the rule in words, a 35-field glossary, the reply format), its current PLAYBOOK, and one
CARD of 35 decision-time fields (depth before/after the trigger sell, sell size and share, time since first print, depth path and drawdown,
causal BOOST state, flow and seller concentration, 15 s and 60 s flows, creator and holder remainders, bonding duration, market activity,
hour). No mint, date, block or arm label: the card reference is an opaque 12-hex nonce. No outcome statistic of these days is in any prompt.

**Decides**: `{"act": "BUY"|"SKIP", "size": 0.5|1|2, "why": ...}`. BUY/SKIP is deciding; size is report-only (linear scaling, impact at
other stakes not repriced). An unparseable reply falls back to the base rule (BUY, size 1) and is counted. Thinking disabled, max_tokens 200.

**Improves** (reflection call, once per night per learning arm): it gets its playbook and every trigger whose outcome was known strictly
before the next trading day's 00:00Z (card values, its decision, and `ret_pct` = the base trade's return in percent of stake, bought or
skipped), at most 200 rows (deterministic sha-ordered sample above that, seeded `v5|rep|day` so L and P see the same rows; counted), and writes a new playbook of at most 12 rules and
2,500 characters. Thinking omitted (Haiku 5.5 adaptive, default effort), max_tokens 6,000. An unparseable reply keeps the old playbook
(counted). The playbook is the only memory.

## 4. Arms (same triggers, same days)

| Arm | What | API |
|---|---|---|
| L | learner: reflection on true outcomes | yes |
| P | placebo: same calls, the night's `ret_pct` column deranged across rows (Sattolo cycle, seeded `v5|P|rep|day|placebo`); cards and decisions real | yes |
| F | frozen: playbook v0 forever, no reflection | yes |
| A | base book: BUY every trigger | no |
| N | numeric: walk-forward L2 logistic (as P96, lambda 1, IRLS) on the 35 card fields, label = flat return at or below the fit set's 25th percentile, refit each day on outcomes known before 00:00Z, vetoes the top k_D where k_D = L's skip count that day (mean over reps) | no |
| R | random: k_D triggers vetoed at random per day, 1,000 draws, seed 1 | no |

L, P and F run 3 replicates each (API sampling noise; Haiku 5.5 rejects non-default temperature). Replicates differ only in the nonce.

## 5. Walk-forward and leak guards (in code, tested)

- Day D's decisions use the playbook frozen at D 00:00Z. The harness refuses if a playbook was built from an outcome known at or after the
  day start. Reflection rows must have decision and `outcome_known_ms` < freeze; later rows wait for the next night (test: a 23:55Z trigger
  is fed one night later; a row known exactly at the freeze waits, one known 1 ms before is fed). `outcome_known_ms` = decision + 15 min
  (the exit is at most about 331 s after the trigger); the loader refuses any other value and never uses the file's field.
- N fits only on rows known before D 00:00Z (tests: flipping day D's outcomes cannot change D's veto; flipping 150 D-1 rows known after
  D 00:00Z cannot change it either, while the same flip on rows known 1 ms before D 00:00Z does).
- Both leak guards are tested on failing inputs (a feedback row decided after the freeze; a playbook built from a row known at the freeze).
- Seal checks before any row is used: block in `ALLOWED_BLOCKS` and instants inside its ledger window; nothing at or after 2026-10-01T00Z;
  no input path with a forbidden fragment (meta-capture, arena, forward-, fresh-08, shadow, canary, probe, kalshi, sealed, fwdv1, ...).
  Paths are checked as given and symlink-resolved. The dry mode never opens an outcomes file (test). Outputs are write-once (directory,
  run_start.json, manifest.json, score file).

## 6. Offline verdict (kill-only; both fail models; burn-in 3 days, so 33 test days)

PASS_OFFLINE only if every bar holds, under **flat 15%** and **pressure-fail**:
- **B1** parse rate >= 95% for every arm-replicate; reflection failures <= 10%.
- **B2 learning > placebo**: mean daily (L - P) > 0, its date-cluster bootstrap 5th percentile > 0 (1,000 draws, seed 1), (L - P) total ex its best day > 0, and L > P in at least 2 of 3 replicate pairs.
- **B3 learning > frozen**: mean daily (L - F) > 0 and bootstrap 5th percentile > 0.
- **B4 LLM > numeric at matched share**: (L - N) total > 0 and bootstrap 5th percentile > 0.
- **B5 filter > base and random**: (L - A) total > 0, ex best day > 0, random matched-share p <= 0.10.
- **B6** L's skip share on test days <= 50% (P77 lesson).
- **B7** (L - P) on the later half of test days > 0 (v3 lesson: learning must not fade as memory grows).
NOT_DECIDABLE below 20 test days or 1,000 test triggers. Report-only: P - F, per-replicate totals, L rep 1's kept book under the gate's
arithmetic (labelled in-sample), sized P&L. PASS_OFFLINE licenses only drafting a forward registration (section 9). KILL ends v5 as frozen.

## 7. Cost (Haiku 5.5; no guessing)

Prices per MTok (prompts up to 100K tokens): input $0.10, output $0.50, cache read $0.01, cache write (5 min) $0.125, Batch API half
(sources: claude-api skill model table cached 2026-10-06 and its prompt-caching economics; `tools/haiku_caller.PRICES`; memory anthropic-key).
Token rate: v3's real ledger (Haiku 5.5, numeric cards) shows 19,801,144 input and 271,697 output tokens over 4,812 calls, 0 cache reads,
2.116 USD, i.e. 4,115 input / 56 output tokens and $0.00044 per call; v3 estimated 11.75M input tokens at 3.0 chars/token, so the harness
assumes 0.56 tokens per request byte.

Dry run on the real layout at Revision 1 (no outcome read, no API; `dry.json` sha256
`60fdcc5ccb5468c7aa3513511ec27a4af894db7ca19928ca4735cfa177c487ac` full, `ec90c2fb0fa3e66e9554c5816623a66563d821045da62b5b674e3a9d60aaa75f`
pilot; the first head's files were `aee96d55…a4630` and `02584530…3edb2`, same call counts and costs):

| Run | Decide calls | Reflect calls | Max body | Expected (cached system block) | Worst-case bound |
|---|---|---|---|---|---|
| Pilot P1: first 3 days, L/P/F x 1 rep | 618 | 4 | 19,002 B | $0.0823 | $0.5532 |
| Full O1: 36 days, L/P/F x 3 reps | 25,425 | 210 | 42,682 B | $3.6083 | $24.581 |

Worst case = haiku_caller's reserve rule (every byte a token, +256, at the cache-write rate, plus max_tokens of output). Without any cache
hit, O1's expected cost is about $10 (6,303 B mean decision body x 0.56 x $0.10/M + 60 x $0.50/M, x 25,425, plus about $0.6 of reflections).
Per decision: about $0.00012 cached ((3.6083 - 0.56) / 25,425), $0.00038 uncached. Per reflection: about $0.003.

**Forward cost per day** (arms L, P, F x 1 rep, 2 reflections): $0.03 to $0.07 at 54 triggers/day (September exploration mean, 1,124/21),
$0.05 to $0.13 at 113/day (August mean, 1,702/15), $0.10 to $0.29 at 250/day (the busiest exploration day). October trigger rates were
not counted here (arena tape not opened).

## 8. Run plan (each step a MiScusi job under sh; the manager approves step 2 after QP; one ledger for all v5 runs)

Working directory for every step: a clean checkout at the merged sha of this PR, for example
`git -C /home/claude/.miscusi/repos/vaanai/MAL worktree add --detach /data2/mal/wt/v5-run <merged sha>` and `cd /data2/mal/wt/v5-run`.
**P1, O1 and every rerun after an exit 4 run at that same sha.** The prompts are code constants: a changed prompt misses the replay store
on every call and pays again inside the same $16 ledger ceiling, so O1 could no longer finish under it. Each run writes
`run_start.json` (harness and prompt sha256, args) before its first call; check it against the previous run before a rerun.
Fixed paths: `S=/data2/mal/scratch/haiku-v5`, export `E=$S/export-o1`, ledger `$S/ledger.jsonl`, store `$S/store`.

0. (no API) Realistic-primary outcomes, P55 pricer unchanged (exit lag 2):
   `cd docs/research/p55 && /data/mal/audit-1008/venv/bin/python p55_price.py /data2/mal/scratch/haiku-v5/p55-rows.parquet; cd ../../..`
1. (no API) Export with outcomes, basis el2 (the only basis a Haiku run or a score accepts; there is no frozen fallback):
   `/data/mal/c1nf-parity-venv/bin/python -m tools.haiku_v5_export --out-dir /data2/mal/scratch/haiku-v5/export-o1 --with-outcomes --basis el2 --el2-rows /data2/mal/scratch/haiku-v5/p55-rows.parquet`.
   It must print `parity OK (el2)` (totals of the full book equal P84's published el2 totals to 1e-4 SOL) or it writes nothing.
   Notebook the sha256 of `export-o1/triggers.jsonl` and `export-o1/outcomes.jsonl` (`export.json` lists both).
2. (API; coded ceiling $1 because `--max-days` is set) Pilot P1. `<notebook id>`, `<T sha>` and `<O sha>` are filled by the manager at
   submission from step 1's notebook entry:
   `set -a; . /var/lib/mal/anthropic/anthropic.env; set +a; /data/mal/venv/bin/python -m tools.haiku_v5_harness run --triggers /data2/mal/scratch/haiku-v5/export-o1/triggers.jsonl --outcomes /data2/mal/scratch/haiku-v5/export-o1/outcomes.jsonl --out-dir /data2/mal/scratch/haiku-v5/run-p1 --model haiku --live --approval <notebook id> --budget-usd 1 --ledger /data2/mal/scratch/haiku-v5/ledger.jsonl --save-dir /data2/mal/scratch/haiku-v5/store --reps 1 --max-days 3 --expect-sha256 triggers=<T sha>,outcomes=<O sha>`.
   Then the structure-only read (no outcome, no score):
   `/data/mal/venv/bin/python -m tools.haiku_v5_harness pilot --run-dir /data2/mal/scratch/haiku-v5/run-p1 --ledger /data2/mal/scratch/haiku-v5/ledger.jsonl --save-dir /data2/mal/scratch/haiku-v5/store`
   -> parse rate, reflection success, per call kind: tokens, cache-read share, input tokens per byte (the 0.56 assumption), latency max and
   count over 45 s (haiku_caller times out at 60 s, non-streaming), `max_tokens` stop share, USD. Go/no-go for O1: parse >= 95%, reflection
   success >= 90%, no reflection over 45 s, `max_tokens` stops under 10% of reflections, and a USD per call that keeps O1 under $15. The
   owner compares the console with the ledger.
3. (API; the coded ceiling is $16 on the cumulative ledger, so about $15 for this step) Full O1: the same `run` command with `--reps 3`,
   no `--max-days`, `--budget-usd 16`, `--out-dir /data2/mal/scratch/haiku-v5/run-o1`. Serial calls (haiku_caller holds its ledger lock
   across a call): about 5 to 11 h. The run lock (`ledger.jsonl.v5-run.lock`) refuses a second process on the same ledger. A run stopped
   by the caller (budget, retries, API error) exits 4 and is rerun, at the same sha, into a NEW `--out-dir` with the same ledger and store;
   saved calls replay at no cost. P1's rep-1 calls on days 1-3 are identical requests and replay (disclosed).
4. (no API) `/data/mal/venv/bin/python -m tools.haiku_v5_harness score --run-dir /data2/mal/scratch/haiku-v5/run-o1 --triggers /data2/mal/scratch/haiku-v5/export-o1/triggers.jsonl --outcomes /data2/mal/scratch/haiku-v5/export-o1/outcomes.jsonl --out /data2/mal/scratch/haiku-v5/score-o1.json`.
   The scorer refuses unless its harness sha256 equals the run's and the basis is el2. **Only a score.json with `o1_verdict_eligible`
   true (model haiku, arms L,P,F, reps 3, no `--max-days`, basis el2) is the O1 verdict.** The verdict line goes to the manager;
   quant-proof reads score.json before any sentence about it.

c + 1 when O1 runs, whatever its verdict. P1 spends no c (no score).

## 9. $192 and the gate path

- v5 total: P1 about $0.08 (cap $1), O1 $3.6 to $10 expected (cap $15), forward 14 to 30 days at $0.03 to $0.29/day, so about $5 to $25.
  More spend does not buy more evidence for v5. Further paid trials, each pre-declared and each c + 1, only after O1 reads: (a) a
  thinking-on decision arm (adaptive, effort low), several times the output cost; (b) v5 on a second frozen structural trigger with an
  exploration set; (c) 5 replicates instead of 3.
- Forward (only after PASS_OFFLINE and QP): a new arena book under DEC-031 with its own registration, frozen code sha, frozen prompts and
  reflection procedure (the playbook evolves; the procedure does not), md5 decision-equivalence replay on the stub, P0, clock and N + 1.
  The book is L rep 1 starting from playbook v0 (August/September Q thresholds do not port 1:1: PumpSwap v2 moves V per print since
  2026-09-30, MAP section 5); P and F run beside it as shadows for the learning contrast. It needs its own trigger process and scorer: no
  join to Q50, s1 or HV4 outputs before their reads.
- **Clock.** The learning loop needs H5 outcomes within minutes of each exit. EXP-024 section 3 Look-2 stays in force until EXP-024's end
  state, first 2026-11-14T00:00Z, and covers "H5 outcomes on any October hour of a V-range pool in its first 360 s, from any source, RPC
  and paper runners included" (DEC-031 section 3 item 5, quoted in docs/rules/q50-a1.md section 8); Q50's declared exception (EXP-024
  Note under Amendment 10 (a)) covers only the Q50 process. So the seal-clean clock is **2026-11-14T00:00Z** at the earliest. Whether
  November hours before 11-14 are outside that text ("October hour") is a QP question, not assumed here.
- Gate read as CLAUDE.md: at least 100 out-of-sample trades, at least 5 UTC days with a majority positive, CI90 lower bound of mean SOL per
  trade > 0 (1,000 draws, seed 1), total ex top 3 > 0, under flat 15% and pressure-fail at slope 1. The earliest read is clock + 5 full
  days if the count is met; the registration fixes the read instant.

## 10. Disclosures

- **In-sample.** H5 was chosen on explore-0814; all 36 dates were read by at least 14 families (MAP section 1) and by at least 5 Haiku
  designs (v3 QP section 2), v3 included. The author read MAP.md (with its in-sample big-loser statistics) and v3's QP review before
  writing the prompts; no number from them is in any prompt, and every arm shares the same prompts, so the contrasts are not biased by
  it, but the choice of card fields is informed by that reading.
- Deciding outcomes are P55 el2 (exit lag 2) at the 0.1 SOL stake; impact at larger stakes is not repriced; the feedback shows the flat
  fail model's expected value per trade, which is smoother than realised fills (v3 QP section 1).
- The Q* 40 trigger set differs from Q50/s1's Q* 50; the forward book would learn on its own trigger set from v0.
- Not measured: real tokens per byte for these prompts, cache-hit share, parse rate, any outcome, any score. Nothing priced in this lane.
