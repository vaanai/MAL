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
  - Pairwise Ranking Prompting: compare pairs, aggregate; insensitive to input order; a 20B model near GPT-4-based ranking on TREC-DL (https://arxiv.org/abs/2306.17563).
  - Position bias is real in listwise and pairwise judging (https://arxiv.org/abs/2406.07791); inconsistency across sorts can be reduced by aggregating runs (https://arxiv.org/abs/2406.00231).
- Debate: https://arxiv.org/abs/2311.17371 (ICML 2024) and https://arxiv.org/abs/2502.08788 find multi-agent debate does not reliably beat self-consistency or single-agent CoT despite more compute (QA benchmarks).
- Calibration: verbalized LLM confidence is overconfident (https://arxiv.org/abs/2306.13063); a 2026 forecasting paper reports near-calibration below 0.5 and overconfidence above it (https://arxiv.org/abs/2607.08046; paper claim, not read). Fix everyone uses is a held-out recalibration map, i.e. a small model on top.
- Finance agent papers (backtest, high leakage risk): CryptoTrade, EMNLP 2024, analyst + news + trader + reflection agents on daily BTC/ETH/SOL (https://arxiv.org/abs/2407.09546); TradingAgents, three stocks over a short 2024 window (https://arxiv.org/abs/2412.20138); FinMem, FinCon (library rows 14-16). StockBench (https://arxiv.org/abs/2510.02209): most agents did not beat buy-and-hold (abstract). Alpha Arena Season 1 (live, press): Qwen3-Max +22.32%, four of six models in the red, organiser called it exploratory with no statistical controls.
- Leakage: https://arxiv.org/abs/2601.13770 (Look-Ahead-Bench), https://arxiv.org/abs/2603.17692 (BlindTrade anonymisation), https://arxiv.org/abs/2607.04958 (look-ahead-freedom as a property). Relevance to us is lower: pump pools are post-cutoff, tape-based, and cards carry no names in HV6/HV7. The leakage that does apply to us is **our own**: reuse of rehearsal days, and prompt edits made after seeing outcomes.
- LLM alpha mining: "Alpha Jungle" LLM+MCTS formula mining, AAAI 2026 (https://arxiv.org/abs/2505.11122); backtest claims; generalisation across regimes untested per a summary. Multiplicity is the unaddressed risk.
- Crypto text features: I found no memecoin or Solana study with an out-of-sample trading result. Evidence for text -> memecoin returns: none found.

## 3. Pattern by pattern

Constants used: HV7 hourly, N up to 8 cards, ~3,000 input tokens per ranking call (assumption), ~400 output tokens, 24 periods/day. One L call costs 24 x (3,000 x 0.10 + 400 x 0.50) / 1e6 = 0.0120 USD/day (arithmetic; with the cache marker on the system block it is lower). Cap 0.30 USD/day.

| # | Pattern | Evidence it works | Fits our constraints | Cost / day (arithmetic) |
|---|---|---|---|---|
| 1 | Listwise vs pairwise / tournament | Text ranking only; listwise is order-sensitive, pairwise is robust but 28 pairs (56 with both orders) at N=8 | Fits if pairs fit ~900 in / 50 out tokens. Latency: 56 parallel calls fit well before t_e (45 s) | Single order 28 x 24 x (900x0.10+50x0.50)/1e6 = 0.0773; both orders 0.1546. Fits cap |
| 2 | Self-consistency / majority vote (permutation SC) | Best-replicated ranking fix above | Yes; K shuffled calls, Borda or Kemeny aggregate. Also gives an outcome-free diagnostic (agreement) | K=3: 0.036; K=5: 0.060 |
| 3 | Rationale-then-score | Mixed in general; for ranking, rationale can anchor. No finance-specific audited evidence found | Costs output tokens; HV7 already caps `why` at 20 words. Score pointwise instead of ordering | Pointwise 8 cards x 24 x (450x0.10+80x0.50)/1e6 = 0.0163 |
| 4 | Calibrated probabilities + EV threshold | Calibration needs held-out labels; verbalised confidence overconfident; Alpha Illusion: LLM confidence is not a tradable probability | A threshold re-creates abstention, the HV4/HV6 failure (n starvation). Only as a *ranking score* via a frozen calibrator, never a trade/no-trade gate in the first test | Same as 3 |
| 5 | kNN retrieval of similar past cases | Found no trading evidence (searched; none). Retrieval-augmented forecasting calibration not found either | Outcome lag: a case enters memory only after its 60 min exit; bandwagon + leakage risk; writes outcomes into prompts (HV7 forbids outcomes in requests) | Longer prompts: +1,500 in tokens per call = +0.0036/call-day at L only |
| 6 | Reflection / memory with outcome lag | CryptoTrade, FinMem, FinCon backtests; lab: haiku-v3 learning ~ placebo. Reflexion/ExpeL gains are benchmark tasks | Already tried here; HV7 L-mem is deferred v7.1 | ~0.005 per night note |
| 7 | LLM as feature extractor feeding a small model | Alpha Illusion recommends "LLM as information interface"; lab found Haiku gain = dead-pool filter, i.e. a numeric filter did the work | Best fit: gives a clean test "does the LLM score add over the numeric model at matched coverage". Cards numeric; text only from metadata (P28) | Pointwise 0.0163 |
| 8 | Multi-agent debate | Does not beat self-consistency (two papers above); finance versions (TradingAgents) have no cost-honest ablation I found | 3-6x calls for no demonstrated edge | 0.04-0.07 |
| 9 | Regime classifier | Library already holds regime signals; one label per hour = tiny effective n; a regime label can also be computed in code | LLM adds nothing code cannot; fails n | ~0.003 |
| 10 | LLM writes rules offline | Alpha Jungle-type backtests; lab row web-llm-traders-7 untested | Best on cost (the live loop is code), but the multiplicity of rules tried is the danger; API is reserved by the owner for the Haiku trader, so rule drafting runs on the Claude plan, not the API | 0 API (plan-side) |

## 4. Ranked recommendations (all paired against random, with a placebo; none claims profit)

### Rank 1: permutation self-consistency as HV7's ranker (v7.1 of L, before the clock)

- What: L's order = Borda aggregate of K = 3 independent calls with different seeded shuffles (the existing perm formula with a K index). Report-only: L1 (first call alone), so the ranking stability benefit is measured, not assumed. Same exit, same arms R/S/U.
- Outcome-free kill test (run on rehearsal prompts, no outcome opened): per period, mean pairwise Kendall tau between the K orders. Placebo comparator: the same statistic when the K calls are given cards whose field values are shuffled across pools (so the cards carry no pool-specific information) and a pure-random permutation baseline (expected tau 0). If real-card tau is not clearly above the shuffled-card tau, the ranker is reading noise or position; do not register an LLM arm. Pre-declare the margin before running.
- Outcome test: the existing L - U contrast (daily sign-flips, MDE rank IC about 0.17 at D = 10). Add one report-only contrast L(K=3) - L1 on the same periods (paired, same cards).
- Fit: changes only the model-call count; fallback rule unchanged (any of the K replies that is not a full permutation is dropped; fewer than 2 valid = FALLBACK to R's pick).
- Cost: about 0.036 USD/day (K=3), 0.060 (K=5); both under 0.30. Latency: calls in parallel, well inside the 45 s gap.
- Honest limit: aggregation lowers noise, it cannot create information. Expected: agreement up, IC not above the MDE.

### Rank 2: pointwise rationale-then-score features with a frozen small calibrator (HV8)

- What: Haiku scores each pool on 3 closed-set 0-3 ratings (per DESIGN's closed vocabulary, `why` first, then scores; reply schema-validated). A 2-4 feature ridge or logistic model, trained **once** on a frozen pre-period and frozen before the clock, maps [numeric card fields + LLM scores] to a rank score; trade the top pick.
- Comparators (all frozen at the same time): (a) numeric-only model with the same form and feature count; (b) LLM scores permuted across pools within period (placebo: same marginal, no information); (c) R and U.
- Test: paired difference (model with LLM scores - numeric-only), daily sign-flips; skill claim only if it beats (a), because lab history is that a numeric filter explains the gain. Gate for any absolute claim is unchanged.
- Fit: no abstention (always ranks), numeric heavy, so cheap; the calibrator absorbs the overconfidence documented above. Ties to P28 narrative scorer (docs/research/p28-haiku-narrative) for text fields; its seal rules apply (October meta-capture is walk-2 sealed; counts only until a QP ruling).
- Cost: about 0.016 USD/day for scoring; training is one-off offline.
- Risks: pre-period reuse (disclose), 2-4 features limit overfit; training on days that overlap HV6/HV7 rehearsals contaminates; use days not used elsewhere and check docs/HOLDOUT_LEDGER.md.

### Rank 3: pairwise tournament versus listwise, decided outcome-blind, then one adopted (HV7.2 or HV8)

- What: run listwise (K=3, rank 1) and pairwise round-robin (28 pairs, both orders, Copeland score) on the same rehearsal prompts. Compare **stability only**: Kendall tau between two independent repeats of each method, transitivity-violation rate for pairwise, and the same on shuffled-card placebo. No outcomes opened. The method with the higher real-minus-placebo stability margin is adopted as the sole L; no arm is added.
- Why outcome-blind: choosing the better method by outcome would be a selection step with multiplicity.
- Cost: pairwise both orders 0.155 USD/day, single order 0.077; fits the 0.30 cap alone but not combined with K=3 listwise at the same time on the live day (0.19 total is fine, the cap is ok; disclose).
- Caveat: pairwise on 8 cards loses the cross-card context that may carry the screen's information; a null here is not evidence of no skill.

### Not recommended now
- kNN case memory and reflection: lab placebo result plus outcome-lag leakage; revive only after rank 2 shows LLM scores add anything (library rows added).
- Debate: no demonstrated edge over self-consistency, 3-6x cost.
- Calibrated EV threshold as a trade gate: reintroduces abstention; use only as a ranking score.
- Offline rule writing: worth a desk lane after HV7 reads, because the live API cost is nil, but every candidate rule counts in multiplicity and the generator will find in-sample winners; must register N rules tried and test on days not used to write them.

## 5. What was not done
No paper was read in full; no GitHub repo was cloned or its code audited; no live results of any Solana LLM bot were found, so none are claimed. Token counts are guesses. This note is not judged by quant-proof.
