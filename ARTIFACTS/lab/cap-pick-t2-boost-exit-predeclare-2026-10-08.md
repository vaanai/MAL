# CAP-PICK T2: BOOST-progress exit (rule B90), pre-declaration

Declared 2026-10-08 (UTC), owner-approved task T2 of the 10-08 audit (`edge-scan-1008/REPORT.md` section T2). **This file is committed before any scoring run of the new code.** Exploration pools only. Report-only. It does not change [EXP-022](../../EXP/EXP-022-cap-pick-part1-prereg.md) and it never decides a gate on its own. Using it on walk 2 would need a pre-declared report-only amendment before counting starts. Nothing here says any book is positive.

Labels: **[measured]** copied from a cited audit file. **[inferred]** reasoned. **[pinned]** a design value this file fixes.

## 0. What is already known (disclosed before the test)

All from `reports/g_reachable_cap_book_rescore.md` section 4 (G, audit 2026-10-08), exploration blocks that have been read many times. None of it is out-of-sample.

- [measured] BOOST's last buy lands a median 342.8 to 347.8 s after our entry. p10 is 329.5 to 333.0 s.
- [measured] G's `D9_boost90` is a BOOST-90% exit with a **600 s** backup cap, not the rule below. It fires at a median 318.0 s after entry (p10 304.9, p90 333.4). The 300 s cap minus D9, paired per fill, was +0.059 / +0.120 / +0.198 / +0.130 / +0.358 pp of stake in favour of D9 on explore / fresh / exp011 / fast-pool / oracle (sign: D9 is the better exit by 0.06 to 0.36 pp).
- [measured] Holding deadline exits to 420 s instead of 300 s costs -9.337% of stake per position (n 1,349).
- [inferred] Because the 90% trigger fires after 300 s on about 90% of fills, B90 with a 300 s cap will exit at the cap on most attempts. Under today's timing the paired difference should be small. Condition (d)(i) is expected to pass almost trivially. This is a prediction, not a result.

## 1. Rule B90 (a) [pinned]

**Exit at the first print where the BOOST wallet's cumulative buys on this pool since s0 reach 0.9 x 17.585 SOL (15.8265 SOL), or at the 300 s cap, whichever comes first.** tp50 / sl30 stay as in the book. A tp or sl trigger at the same print or earlier wins (G's rule: the boost exit must be strictly earlier).

**Which detector, and why.** The tape rows have a `trader` field, but the keeper address `HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r` is not in it. I grepped the whole explore-0814 `trades-2026-08-15T12` hour file (`w7`, all venues): 0 occurrences. The keeper **signs** the crank transactions. The swap's user account on the tape is the per-pool `boost_vault` PDA (G: PDA match on 21,303 of 21,393 detector hits, 99.58%; 21,392 distinct wallets in 21,393 graduations). So a wallet-list match on the keeper address is impossible on this tape, and the rule uses **G's causal behavioural detector on the `trader` field** (`g_reachable_cap_book_rescore/sim.py`, `boost_cross`):

- a wallet W on the canonical pool's path, over prints with slot <= s0 + 2,500;
- W has made no sell so far, every buy of W so far is <= 2 SOL, W has made >= 8 buys, and W's cumulative buy `sol_lamports` >= 0.9 x 17,585,000,000;
- the trigger is the first print (in path order) at which any wallet satisfies all four.

G found BOOST by this detector in 99.63% of fills.

**Everything else is the P_primary default** [pinned], so the only difference between the two arms is the exit: k = 1.3 s on the day table (`round`), END bound, exit lag 2 slots on every sell (the B90 sell included), guard `min_out` at seed x 1.15 (net basis), 0.5 SOL, 55,000 lamports per send, 300 s day-mean cap, flat 15% and pressure (slope scale 1, intercept refit on the run's guard-passed sends) as the binding legs, live 1/62 and no-fail report-only. The 600 s backup cap of G's D9 is **not** used.

**Not pre-declared, so not built:** any threshold other than 0.9, any cap other than 300 s, any other k or lag. The threshold and the budget are module constants, not flags.

## 2. Comparison (b) [pinned]

Paired per attempt, B90 minus the 300 s cap, same (UTC date, mint), same entry and guard result, same price path (so also the same cut path in section 3).

- **Books:** the EXP-012 pick book (`--picks` = d08 `cache_table.csv`, pick = score >= 0.8030766588450794) and the full book (every attempt).
- **Scopes:** P2-P4 (explore-0814, fresh-0903, exp011-0909) pooled, for both books; P1 (fast-pool-0918, oracle-insample-0922) pooled, for the full book. cache_table also has P1 rows, but those scores come from a model trained on P1 (in-sample; G used the LODO out-of-fold scores for P1, which the scorer does not read). So the pick book is reported on P2-P4 only, and a pick subset on P1 is not reported. Each block is also reported.
- **Legs:** flat 15% and pressure at slope scale 1 (both binding for the kill rule), live 1/62 and no-fail (report-only).
- **Statistic:** mean paired difference in pp of stake per attempt, the date-cluster CI90 (1,000 UTC-date resamples, seed 1, 5th and 95th percentile), dates on which B90 is better, and the number of attempts on which B90 fires before the cap.

## 3. Stress replays (c) [pinned]

Pump.fun shortens BOOST: the keeper's buys stop after a fraction f of the 17.585 SOL budget, f = **0.8** and f = **0.6**.

- **Keeper for the cut:** the wallet the section 1 detector finds on the original (uncut) path. This uses the whole path, which is allowed because the replay builds a counterfactual and is not a trading rule. A mint with no detected wallet is left unchanged.
- **Cut:** keep the keeper's buys while its cumulative buy SOL, including that buy, is <= f x 17.585 SOL. Remove every later buy of the keeper. Every other print is kept, at the same slot and in the same order.
- **Re-simulation (constant product on quote vault + V and base, PRE-trade convention).** The state before the first removed print is the tape's. After it, each kept print is applied in order to the running state (X = quote + V, B = base), with the fee tier from `PUMPSWAP_SOL_FEE_TIERS` on the state's market cap, as the scorer does for our own buy:
  - buy keeps its SOL in: net = S x (1 - fee), tokens out = B x net / (X + net), X += net, B -= tokens out;
  - sell keeps its tokens in: gross = X x T / (B + T), SOL out = gross x (1 - fee), X -= gross, B += T.
  The re-simulated state before each kept print is that print's PRE-trade reserve, and the state after the last print is the final entry.
- **Approximations, stated:** (1) other traders do not react to the missing flow; their amounts and timing are the tape's. (2) the fee is the lab's whole-fee-leaves-the-pool approximation on buys, as in `pumpswap_pool_quote_delta`; the LP share that stays in a real pool is ignored. (3) no integer rounding and no slippage limits. (4) after the first removal the path drifts from what the chain would have done by an unknown amount. (5) the entry state is unchanged, because the keeper's first slice lands about 7 to 8 slots after s0 and at least the first slices are kept for f >= 0.6.

**Property of the rule, declared before the run.** For f < 0.9 the keeper's cumulative buys can never reach 0.9 x 17.585 SOL on a cut path, so B90 never fires there. B90 then equals the cap on every attempt and the paired difference is exactly 0. The fixture tests assert this. The replays still show how much the cap itself loses when BOOST is shortened (cap book under f against the cap book under today's timing).

## 4. Kill rule (d), as given by the manager [pinned]

Keep B90 only if (i) it is not worse than the cap under today's timing (paired date-cluster CI90 lower bound > -0.25 pp on the picks, P2-P4, on **both** binding legs) AND (ii) it beats the cap in both stress replays (paired mean > 0 on the picks, P2-P4). Otherwise close with no retune. B90 never decides a gate on its own.

**Consequence of section 3, declared now.** Condition (ii) cannot be met by B90 as defined: the paired mean is exactly 0 in both replays, which is not > 0. Read literally, B90 is closed. I do not change the rule. If the manager wants (ii) to be non-vacuous, `REPORT.md` section T2 pre-specified a different replay, BOOST slice times shifted -20% and -40% (same budget, earlier schedule), under which a 90% trigger does fire earlier. That replay is **not built or run here**. It would need its own pre-declared amendment and a builder step before any full run.

## 5. Limits

Exploration pools only. The scorer's refusals are unchanged and not loosened. No sealed block, no forward or walk-2 data, no hour at or after 2026-10-02T10Z, no network, no key. The smoke run is one day (explore-0814, 2026-08-15). The full run (all exploration blocks, three configs) is the manager's, as a MiScusi job. Every cell in sections 2 and 3 is reported whatever the outcome.
