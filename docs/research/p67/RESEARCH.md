# P67: how others build LLM trading agents, and what HV7 / HV8 can test honestly

Desk research, lane P67 (manager17), 2026-10-27. Web and repo docs only: no tape, data dir, card, ledger or HV4/HV6/HV7/Q50 output was opened; 0 Helius credits; 0 API calls.
**Nothing here says any book makes money.** No book has cleared the promotion gate. Paper only.

Reading depth, stated plainly: web search results (snippets and abstracts) plus one abstract page fetch (The Alpha Illusion). I did **not** read full papers, so numbers below are the ones the search results printed, and are marked "paper claim". Cost figures are arithmetic from `tools/haiku_caller.py` PRICES (claude-haiku-5-5: input 0.10, cache_write 0.125, cache_read 0.01, output 0.50 USD per million tokens) and my token guesses; they are assumptions, not measurements.

Already covered, not repeated: library rows `web-llm-traders-1..21` (Reflexion, ExpeL, ACE, FinMem, FinCon, TradingAgents, StockBench, Alpha Arena, LiveTradeBench, BlindTrade, look-ahead audits) and `haiku-v3-*`.

## 0. Verdict up front

1. The public evidence that an LLM adds trading skill is **weak and mostly unaudited**. The strongest independent statement found is "The Alpha Illusion" (https://arxiv.org/abs/2605.16895): reported Sharpe ratios of FinCon, FinMem, TradingAgents, FinAgent and others "should not be treated as deployment evidence"; temporal contamination, frictions, short windows and parametric priors are not separated from skill; "language-model confidence is not a tradable probability"; and a conservative design is the LLM as an auditable information interface feeding separate calibration, risk and execution modules (paper claim). That is the same shape as the lab's own measured finding (haiku-v3: learning about equal to placebo, gain = dead-pool filter).
2. I found **no audited live P&L for any open-source Solana memecoin LLM bot.** The repos are real and star-counted but those are popularity, not performance (listed in section 1).
3. HV7's forced rank with paired nulls (R, S, U) is already a better test design than anything in the public repos. The most useful additions are cheap, outcome-blind variance reducers on the ranker (permutation self-consistency), and moving the LLM from "decider" to "feature extractor with a frozen calibrator" for HV8.
4. Everything below keeps the HV7 stance: expected result is "no skill detected" unless rank IC is above the registered MDE (about 0.17, DESIGN section 4). A pattern that cannot beat that bar cannot be told apart from noise at 10 days.

## 1. What practitioners ship (repos; popularity is not evidence)

Aggregator listings from one search (stars and dates are third-party and unverified):
- https://github.com/solagent99/solana_aiagent_trading (memecoin AI agent tool, about 79 stars, listing says no recent pushes)
- https://github.com/affaan-m/stoictradingAI (TypeScript autonomous Solana bot, about 95 stars, last push 2026-05-18 per aggregator)
- https://github.com/sendaifun/solana-agent-kit (a toolkit for agent actions, not a strategy)
- https://github.com/moondevonyt/moon-dev-ai-agents (about 3,704 stars per aggregator; general algo-trading agents, not Solana-specific)
- https://madeonsol.com/blog/ai-trading-agent-solana-data-api (LangChain ReAct + GPT-4o over KOL win-rate filters; a tutorial)
- https://github.com/TauricResearch/TradingAgents (about 69k stars per a mirror, stocks) and https://github.com/Xtra-Computing/CryptoTrade (BTC/ETH/SOL daily)
Common shape: LLM reads a text bundle (price, news/KOL feed), emits BUY/SELL/HOLD in free text, a tool layer executes. None of the pages reviewed published a paired-null, cost-honest, out-of-sample test. Some skill-marketplace pages carry low safety scores (a wallet-key risk; the lab holds no keys, so irrelevant but a reason not to copy code).

## 2. Evidence base, ranked by how far to trust it

- Ranking and aggregation methods (text retrieval benchmarks, not finance): moderate evidence, replicated, but a different task.
  - Permutation self-consistency: shuffle the list, rank repeatedly, take the central ranking by Kendall tau. Paper claim: up to 7-18% improvement for GPT-3.5, 8-16% for LLaMA2-70B over single-pass listwise on five list-ranking datasets (https://arxiv.org/abs/2310.07712, NAACL 2024; code https://github.com/castorini/perm-sc).
  - Pairwise Ranking Prompting: compare pairs, aggregate; insensitive to input order; a 20B model near GPT-4-based ranking on TREC-DL (https://arxiv.org/abs/2306.17563; paper claim).
  - Position bias is real in listwise and pairwise judging (https://arxiv.org/abs/2406.07791); inconsistency across sorts can be reduced by aggregating runs (https://arxiv.org/abs/2406.00231).
- Debate: https://arxiv.org/abs/2311.17371 (ICML 2024) and https://arxiv.org/abs/2502.08788 find multi-agent debate does not reliably beat self-consistency or single-agent CoT despite more compute (QA benchmarks; paper claims, 2311.17371 and 2502.08788).
- Calibration: verbalized LLM confidence is overconfident (https://arxiv.org/abs/2306.13063); a 2026 forecasting paper reports near-calibration below 0.5 and overconfidence above it (https://arxiv.org/abs/2607.08046; paper claim, not read). Fix everyone uses is a held-out recalibration map, i.e. a small model on top.
- Finance agent papers (backtest, high leakage risk): CryptoTrade, EMNLP 2024, analyst + news + trader + reflection agents on daily BTC/ETH/SOL (https://arxiv.org/abs/2407.09546); TradingAgents, three stocks over a short 2024 window (https://arxiv.org/abs/2412.20138); FinMem, FinCon (library rows 14-16). StockBench (https://arxiv.org/abs/2510.02209): most agents did not beat buy-and-hold (abstract). Alpha Arena Season 1 (live, press): Qwen3-Max +22.32% (press figure), four of six models in the red, organiser called it exploratory with no statistical controls.
- Leakage: https://arxiv.org/abs/2601.13770 (Look-Ahead-Bench), https://arxiv.org/abs/2603.17692 (BlindTrade anonymisation), https://arxiv.org/abs/2607.04958 (look-ahead-freedom as a property). Relevance to us is lower: pump pools are post-cutoff, tape-based, and cards carry no names in HV6/HV7. The leakage that does apply to us is **our own**: reuse of rehearsal days, and prompt edits made after seeing outcomes.
- LLM alpha mining: "Alpha Jungle" LLM+MCTS formula mining, AAAI 2026 (https://arxiv.org/abs/2505.11122); backtest claims; generalisation across regimes untested per a summary. Multiplicity is the unaddressed risk.
- Crypto text features: I found no memecoin or Solana study with an out-of-sample trading result. Evidence for text -> memecoin returns: none found.

## 3. Pattern by pattern

Constants used (QP-corrected, from DESIGN section 5): one rank call = 2,874 cache-write + 9,056 new input + 400 output tokens = 0.00146485 USD/call. Hourly calls always pay cache-write (the cache lasts 5 min). Single L = 24 calls = 0.0352 USD/day. Cap 0.30 USD/day. My first draft used 3,000 input tokens and a cache discount and understated every cost by about 3x; those figures are withdrawn.

| # | Pattern | Evidence it works | Fits our constraints | Cost / day (arithmetic) |
|---|---|---|---|---|
| 1 | Listwise vs pairwise / tournament | Text ranking only (paper claims); listwise is order-sensitive, pairwise is robust but 28 pairs (56 with both orders) at N=8 | Latency fine in parallel, but cost is the limit | QP figures: single order 0.4305, both orders 0.8610 USD/day. ABOVE the 0.30 cap |
| 2 | Self-consistency / majority vote (permutation SC) | Best-replicated ranking fix above | Yes; K shuffled calls, Borda aggregate. Also gives an outcome-free agreement statistic (report-only) | K=3 about 0.1055 expected, 0.338 worst case with retries (ABOVE the cap); needs the owner cost line raised first |
| 3 | Rationale-then-score | Mixed in general; for ranking, rationale can anchor. No finance-specific audited evidence found | Costs output tokens; HV7 already caps `why` at 20 words. Score pointwise instead of ordering | Pointwise about 0.105 (QP) |
| 4 | Calibrated probabilities + EV threshold | Calibration needs held-out labels; verbalised confidence overconfident; Alpha Illusion: LLM confidence is not a tradable probability | A threshold re-creates abstention, the HV4/HV6 failure (n starvation). Only as a *ranking score* via a frozen calibrator, never a trade/no-trade gate in the first test | Same as 3 |
| 5 | kNN retrieval of similar past cases | Found no trading evidence (searched; none). Retrieval-augmented forecasting calibration not found either | Outcome lag: a case enters memory only after its 60 min exit; bandwagon + leakage risk; writes outcomes into prompts (HV7 forbids outcomes in requests) | Longer prompts: +1,500 in tokens per call = +0.0036/call-day at L only |
| 6 | Reflection / memory with outcome lag | CryptoTrade, FinMem, FinCon backtests; lab: haiku-v3 learning ~ placebo. Reflexion/ExpeL gains are benchmark tasks | Already tried here; HV7 L-mem is deferred v7.1 | ~0.005 per night note |
| 7 | LLM as feature extractor feeding a small model | Alpha Illusion recommends "LLM as information interface"; lab found Haiku gain = dead-pool filter, i.e. a numeric filter did the work | Best fit: gives a clean test "does the LLM score add over the numeric model at matched coverage". Cards numeric; text only from metadata (P28) | Pointwise about 0.105 |
| 8 | Multi-agent debate | Does not beat self-consistency (two papers above, paper claims); finance versions (TradingAgents) have no cost-honest ablation I found | 3-6x calls for no demonstrated edge | Roughly 0.1-0.2 (3-6x the single 0.0352; arithmetic) |
| 9 | Regime classifier | Library already holds regime signals; one label per hour = tiny effective n; a regime label can also be computed in code | LLM adds nothing code cannot; fails n | ~0.003 |
| 10 | LLM writes rules offline | Alpha Jungle-type backtests; lab row web-llm-traders-7 untested | Best on cost (the live loop is code), but the multiplicity of rules tried is the danger; API is reserved by the owner for the Haiku trader, so rule drafting runs on the Claude plan, not the API | 0 API (plan-side) |

## 4. Ranked recommendations (all paired against random, with a placebo; none claims profit)

### Rank 1: permutation self-consistency as HV7's ranker (K=3 Borda goes to v7.1, not v7.0)

Manager decision recorded: K=3 Borda is **v7.1**, not v7.0, because of the cost (below) and the rehearsal size (432 calls against a 288-call cap). v7.0 stays a single-call L.

- What: L's order = Borda aggregate of K = 3 independent calls with different seeded shuffles (the existing perm formula with a K index).
- QP rules for v7.1:
  - k=0 is byte-identical to v7.0 (K index 0 reproduces the v7.0 request and perm).
  - Borda runs over valid replies only, with an outcome-free tie-break: the order of the lowest-index valid call.
  - Fewer than 2 valid replies is FALLBACK to R's pick (counts in n).
  - L1 (the first call alone) is report-only.
  - The owner cost line is raised first, before any K>1 live run.
- Further v7.1 rules (QP and reviewer):
  - A reply after t_e is invalid; the number of valid replies per period is logged.
  - A C2 test shows haiku_caller handles 3 concurrent calls safely on one ledger; alternatively the 3 calls run one after another inside the t_e window.
  - The md5 decision-equivalence proof and the real-layout replay are rerun at the K=3 sha.
  - DESIGN section 7 bar 4 (agreement vs chance) remains the kill floor for v7.1.
- Agreement statistic (mean pairwise Kendall tau among the K orders) is **report-only**. The earlier draft's kill rule "real-card agreement must exceed shuffled-field agreement" is **dropped**: a consistent field-rule ranker would fail it (it is stable on shuffled fields too), so it kills good rankers.
- Outcome test: the existing L - U contrast (daily sign-flips, MDE rank IC about 0.17 at D = 10) plus report-only L(K=3) - L1, paired on the same periods.
- Cost (QP): single 0.0352 USD/day; K=3 about 0.1055 expected, **0.338 worst case with retries, above the 0.30 cap**. K=5 is not affordable. So K=3 needs the cap raised, or a retry limit that keeps the worst case under 0.30.
- Honest limit: aggregation lowers noise, it cannot create information. Expected: IC not above the MDE.

### Rank 2: pointwise rationale-then-score features with a frozen small calibrator (HV8)

- What: Haiku scores each pool on 3 closed-set 0-3 ratings (per DESIGN's closed vocabulary, `why` first, then scores; reply schema-validated). A 2-4 feature ridge or logistic model, trained **once** on a frozen pre-period and frozen before the clock, maps [numeric card fields + LLM scores] to a rank score; trade the top pick.
- Comparators (all frozen at the same time): (a) numeric-only model with the same form and feature count; (b) LLM scores permuted across pools within period (placebo: same marginal, no information); (c) R and U.
- Test: paired difference (model with LLM scores - numeric-only), daily sign-flips; skill claim only if it beats (a), because lab history is that a numeric filter explains the gain. Gate for any absolute claim is unchanged.
- Fit: no abstention (always ranks), numeric heavy, so cheap; the calibrator absorbs the overconfidence documented above. Ties to P28 narrative scorer (docs/research/p28-haiku-narrative) for text fields; its seal rules apply (October meta-capture is walk-2 sealed; counts only until a QP ruling).
- Cost: pointwise scoring about 0.105 USD/day (QP); training is one-off offline.
- Pre-declaration: the 2-4 features and the training window are named **before training**. Training outcomes come from a ledger block (docs/HOLDOUT_LEDGER.md), **never the 10-21..23 rehearsal sets**.
- Risks: pre-period reuse (disclose); 2-4 features limit overfit.

### Rank 3: pairwise tournament versus listwise, decided outcome-blind, then one adopted (HV7.2 or HV8)

- What: run listwise (K=3, rank 1) and pairwise round-robin (28 pairs, both orders, Copeland score) on the same rehearsal prompts. Compare **stability only**: Kendall tau between two independent repeats of each method, transitivity-violation rate for pairwise. The method is chosen by stability against the random/position baseline (DESIGN section 7 bar 4); the same statistic on shuffled cards is report-only. No outcomes opened. The method with the larger margin over that baseline is adopted as the sole L; no arm is added.
- Why outcome-blind: choosing the better method by outcome would be a selection step with multiplicity.
- Cost (QP): pairwise single order 0.4305, both orders 0.8610 USD/day, both above the 0.30 cap. The rehearsal runs on Claude-plan subagents, so there is no API bill. Pairwise in both orders is 56 calls per set, 4,032 calls over 72 sets, against the 288-call rehearsal cap, so it needs its own DESIGN section 7 amendment before any run. Adopting pairwise live also needs the 0.30 USD/day cap raised; otherwise it is not adoptable.
- Caveat: pairwise on 8 cards loses the cross-card context that may carry the screen's information; a null here is not evidence of no skill.

### Not recommended now
- kNN case memory and reflection: lab placebo result plus outcome-lag leakage; revive only after rank 2 shows LLM scores add anything (library rows added).
- Debate: no demonstrated edge over self-consistency, 3-6x cost.
- Calibrated EV threshold as a trade gate: reintroduces abstention; use only as a ranking score.
- Offline rule writing: worth a desk lane after HV7 reads, because the live API cost is nil, but every candidate rule counts in multiplicity and the generator will find in-sample winners; must register N rules tried and test on days not used to write them.

## 5. What was not done
No paper was read in full; no GitHub repo was cloned or its code audited; no live results of any Solana LLM bot were found, so none are claimed. Token counts are guesses. This note is not judged by quant-proof.
