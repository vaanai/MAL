# P94: what MAL learned on 2026-10-27, and where to aim next

Lane P94 (synthesis-1027), manager17. Desk task: no tape, no pricing, no new number. Every figure is copied from the lane REPORT named in the table. Written 2026-10-27. **No book has cleared the promotion gate. Nothing here says any book makes money.**

## Plain-English summary (for the owner)

1. Today we tested about 20 new trading ideas on past data. None of them made money after fees. No book has passed the promotion gate.
2. The main reason is cost. Each buy-and-sell on a small or mid-size coin costs roughly 2.5 to 4 percent in fees and delay. Most price moves we can catch are smaller than that.
3. Cheaper coins to trade do exist (big, old pools cost about 0.65 percent), but most of those pools barely move, so there is little to earn there.
4. We did find one real tool: a simple "similar past pools" ranker (kNN) that sorts pools by how they will do. It is genuinely better than random (about 6 points per trade). That is not enough: the pools it picks from lose about 15 points per trade on average, because many are dying pools.
5. Its skill is mostly "avoid bad pools", not "find winners".
6. We tested whether the Haiku AI model can rank pools or read coin names. It cannot: on numeric cards it was near random (rank score 0.03 against 0.30 for the simple ranker), and on names it was worse than a coin flip (0.45).
7. So the Haiku-ranking test (HV7, due 11-01) is unlikely to find skill, and it is at risk of not being readable in time.
8. The H5/Q50 book is in its forward test. Two live blockers were fixed today (the Mayhem fee recipient, and the N4 pre-check now passes). Its first possible read is 2026-10-30. HV6 registers for 10-30.
9. We should stop testing fast in-and-out trades on small coins. The five directions to try next are listed in section 4, each with a first test and a stop line.
10. Honest status: money-making is unproven, and the cost problem is the main thing standing in the way.

## 1. Every lane, P70 to P93

Verdicts use the lane's own words. "Dead" means KILL or not decidable; none produced a gate-passing book. All tests are in-sample on dates at least 14 families have read, so even a positive would have been a lead only.

| Lane | Idea | Verdict | Key number | Why it died |
|---|---|---|---|---|
| P70 | Passive PumpSwap LP fees, 24 h hold | KILL | Drift-free view (fees + IL - costs) mean -0.024202 SOL at 0.25 SOL; 9.58% of pool-days positive | LP fees do not cover impermanent loss and costs. |
| P71 | Copy top wallets at MAL latency (1.9 s fill) | KILL | 611 copies, every cell negative; pooled without best wallet -3.44161% flat; rank IC Aug to Sep about -0.17 | Wallet skill does not persist; by the time we land, the edge is gone. |
| P72 | Absorb a forced-sell cluster | KILL | 425,142 priced trades, 0 of 36 days positive; -2.6377% flat | Fee legs and lag, not impact, cause the loss (same loss at 0.25 and 1 SOL). |
| P73 | Mayhem early-collapse fade | NOT_DECIDABLE | Counts step failed its own gate; no pricing run | Mayhem mints break the curve check on the tape. |
| P74 | Bonding-curve completion entry (P=85%) | KILL | Mean <= 0 under both fail models; ex-top-3 <= 0 | Stops outweigh the enriched completions. |
| P75 | Model-selected version of P74 | KILL | Test AUC 0.6196; best cell -0.197% (pressure, no portal fee); CI90 low -0.013387 | Selection lifts the mean from about -6% to about -1.3%/-0.2%, still below zero. |
| P76 | Hour-of-day PUMP drift (hour 16) | KILL | Mean < 0 both models; hour 16 not top 2 | No pattern. Also NOT_DECIDABLE in SOL: tape has no PUMP/SOL pool. |
| P77 | Flow-surge veto on frozen H5 | NOT_DECIDABLE | Veto fires on 89.38% of trades | The veto removes almost the whole book; it is a different book, not a filter. |
| P78 | Cheapest-fee band B7 (0.30%/leg): P72 absorb, P71 copy, momentum | KILL (all three) | Floor falls 3.3989% (B0) to 0.6479% (B7). P71 copy mean positive both models (0.1 SOL: +0.13175 flat) but best wallet share of total 195% | Cheap band is mostly quiet or pegged pools (about 35% of tradeable B7 pool-hours move < 0.001% in 2 slots). P71 copy fails its frozen lines 2 and 3. |
| P79 | Buy after Mayhem agent's sell burst | KILL | -13.738% flat (-0.034346 SOL), CI90 low -0.051181 | Agent's sell carries no information beyond the price drop it caused; 1.25% fee tier both legs. |
| P80 | Learned rug veto on H5 | NOT_DECIDABLE | Independent training sample 0 rows (need 300) | H5 trades are the only pools meeting H5 timing; no clean training set. |
| P81 | Idea sprint: cashback, volume reward, holder carry | Triage | 3 lanes proposed | Became P82 and P83; holder-carry stays untested. |
| P82 | Cashback refund on H5 trades | KILL (a cost note) | +0.2180 pp flat, +0.1750 pp pressure vs +0.3 pp line | Refund is real but about 0.2 pp, too small to change anything. |
| P83 | Volume-reward farming | INACTIVE | Both accumulator accounts all zero | Programme not live; also a possible terms-of-service issue the owner must decide first. |
| P84 | Prior-day graduation count as on/off switch for H5 | KILL | 0 on-days of 21; discovery placebo p 0.47 to 0.51 | The switch variable is not stable day to day; confirmation never tested the idea. |
| P85 | FLOOR survivor, fresh-0808 confirm | NOT_DECIDABLE | n 59 (< 100); mean > 0 both models; 37.9% excluded | Weakly positive but biased: exclusion drops late time-exits and keeps early take-profits. A weak lead only. |
| P86 | Numeric kNN picker, hourly, 285 hours | KILL | IC 0.27814 (t 11.294); book mean -1.128 pp flat, -0.656 pp pressure; beats random by +6.892 / +5.733 pp | Better picker, still below costs. See 2(b). |
| P87 | B7 dip-absorption pullback | KILL | 441 trades; mean -0.43635% flat, -0.3685% pressure; 11/26 and 13/26 days positive | Cheap band, but 52 pools and top 5 carry 68% of trades; no edge. |
| P88 | KNN-C forward paper book design | SHELVED / REDESIGN | 0 trades, not registered | QP and reviewer both asked for redesign: the cost question was not settled. |
| P89 | kNN as veto on H5 | NOT_DECIDABLE | Computable share 0% | kNN is built for hourly established pools; H5 trades are a different domain. |
| P90 | (No lane report on main; the arena feed-delay p90 commits are unrelated.) | Not assessed | n/a | n/a |
| P91 | kNN on low-fee, active universe (B5-B7) | PENDING | Branch `claude/p91-knn-lowfee` has PREREG and scripts only; no report | Result not available at writing. Included in direction 1. |
| P92 | Haiku vs kNN on numeric cards | KILL (as add-on) | Haiku IC 0.027 (t 0.5) vs kNN 0.296; ensemble IC below kNN | Haiku adds nothing over kNN. Haiku top pick worse than random. |
| P93 | Haiku reads token name and symbol | KILL | AUC 0.4476, CI90 [0.3384, 0.5499]; 19 of 400 survivors | No signal; low power (19 positives) but no sign of a large effect. |

Closed today in the library without a P-number: web-oct-mechanics-1 (graduation play), mech-buyer-scout-1/-2, w4s-short-hl-funding-cost, all `explored-no-survivor`. FLOOR is CLOSED on both clocks (A2 rate 6.0/day under 12.5; N not spent), per docs/HANDOFF.md.

## 2. Three cross-cutting findings

**(a) The taker cost floor kills almost every signal.** A round trip pays two fee legs, two priority fees and about two slots of drift. P78 maps this by market-cap band: round-trip floor at 0.25 SOL is 3.3989% (B0) falling to 0.6479% (B7). Lanes P70 to P76 lived in the expensive bands. The cheap band is not a free lunch: P78 found about 35% of tradeable B7 pool-hours (42% of prints) have a 2-slot move under 0.001%, and 211 pools (6.6% of B7 pools, 28% of B7 volume) sit flat for 24 h or more. So the 0.648% floor mostly describes quiet or pegged pools, not organic memecoins. P87 then tried B7 with a real rule and lost 0.43635% a trade. Fees are not the only cost: P72 lost the same percentage at 0.25 and 1 SOL, so the legs themselves, not size, do the damage.

**(b) A real ranker exists, but not enough.** P86's kNN (10 causal tape features, k = 25) has rank IC 0.27814 (t 11.294, day-block t 11.893) and the IC is unchanged when the memory never contains the same pool (0.27586). It beats a random pick by +6.892 pp (flat) and +5.733 pp (pressure) and 10 of 13 days. Its strongest single features are volume (negative) and top-3 concentration, and its signal is mostly "avoid pools that are about to die". But the universe it picks from loses: random pick about -7.5 pp flat, and the kNN book itself -1.128 pp flat, -0.656 pp pressure. The 240 s-window sensitivity flips sign under pressure only because dead pools drop out. A +6 pp selection gain cannot repay a -7.5 pp base. Caveat: dropped, unpriced dead pools bias the means upward, so these are generous.

**(c) Haiku showed no signal.** P92: on the same numeric cards Haiku's IC was 0.027 against 0.296 for kNN, and Haiku was better on only 2 of 13 days. P93: on name and symbol alone, AUC 0.4476, below a trivial name-length baseline (0.5184). Both used 0 API calls (Claude-plan Haiku). The honest scope: P92 is one card design and P93 has 19 positives, so a small effect cannot be excluded, but a large one is excluded.

## 3. What this means for the live candidates

- **H5/Q50 (`h5v1q50-boostfloor-a1`).** In forward test, clock 2026-10-25T00Z, earliest read boundary 2026-10-30T00Z, end-seal 11-06. Its live blockers were fixed today (mayhem fee recipient, N4 pre-check PASS). Nothing today changes its odds either way; a passing read would still need the gate and the owner's word. The kNN veto (P89) and rug veto (P80) could not be tested on it, so any filter has to wait for forward trades.
- **HV6.** Registers for 10-30 (P3 work; per the manager brief). Nothing in today's lanes bears on it.
- **HV7 (Haiku ranking).** At risk for 11-01. Its minimum detectable effect is a rank IC of about 0.17, and P92 measured Haiku at 0.027 on a comparable card, so the prior for a pass is low. The risk is also procedural: the read tool now treats missing closing rows as final NOT_DECIDABLE and refuses rows without an entry stamp. The #1018 equivalence proofs and rehearsal PASS are recorded in the Console/notebook; they were not re-read for this note.
- **FLOOR.** Closed on both clocks. P85 (fresh-0808) was weakly positive (mean > 0 both models, signs match in-sample) but n 59 and 37.9% non-random exclusion, so it is a weak lead and not a revival. A revival needs a new amendment (f1 to f5) and clock.
- **DBC m10.** Forward window to 11-01T00:10Z; a confirmation only, never a gate read.

## 4. Next five directions, ranked

Scores are judgments, not measurements: probability it works (P) x SOL/day if it works. Low-edge short-hold taker work is deliberately absent.

| # | Direction | P | If it works | First test | Kill line |
|---|---|---|---|---|---|
| 1 | **Cost-gated kNN (redesign of P88, P91 first).** Use the ranker only where the base is not -7.5 pp: low-fee band and active pools. | low (about 10%) | Small: capacity is the limit (about 22 trades/day at 0.25 SOL) | Read P91 when it lands: top pick mean vs random, both fail models, at least 100 test hours with 3 or more candidates. | Top-pick mean <= 0 under either model, or fewer than 100 qualifying hours. Then close the kNN-as-book line. |
| 2 | **Let Q50 finish, then fit a veto on its forward trades.** The only clean domain for "avoid bad pools" is H5's own pool set, and no independent sample exists today (P80, P89). | low-moderate (about 15%) | Moderate: lifts an existing book | After the Q50 read, with new pre-registration: frozen 6-feature rug score fit on first half of forward trades, tested on the second. | Delta vs placebo not positive, or drop-best-day total <= 0. Never touch Q50 outputs before the read. |
| 3 | **Low-turnover carry (p81-holder-carry, untested).** Multi-day holds amortize one fee over many days; the carry is paid to holders. | low (about 8%) | Moderate if turnover is high | Counts only: how many holder-rewards coins, daily turnover and reward vs round-trip fee. | Median reward-minus-fee day <= 0, or fewer than 5 candidate coins per day. |
| 4 | **DBC m10 forward confirmation (11-01) and, if it passes, P18 forward paper.** Already registered. | low-moderate (about 10%) | Unknown | Do nothing to the window; run the one pre-registered read at or after 2026-11-01T00:10Z. | Any non-PASS; remember a PASS licenses paper only. |
| 5 | **Mechanism-change watch.** Redeploys and fee changes created our biggest known edges (pins, tier tables). Check daily for new fee programmes, cashback or reward switches turning on (P83 showed one inactive today). | very low per week | Large when it hits | Daily job: diff program pins and accumulators; flag any change. Costs nothing. | No actionable change in 4 weeks: reduce to weekly. |

Ranking logic: 2 and 4 depend on events already running; 1 is cheapest to learn from; 3 and 5 are cheap, low-odds options.

### Stop

- **Stop more short-hold taker signals in small-cap or mid-cap bands** (P70 to P79, P84, P87 all killed). The floor is 1% to 3.4% and the best honest cells are -0.2% to -13.7%.
- **Stop Haiku-as-ranker work** on numeric cards or on names (P92, P93). Revisit only if a new input type appears that kNN lacks.
- **Stop LP and wallet-copy lines** (P70, P71; P71's only positive cell, P78 B7, hinges on one wallet).
- **Stop re-pricing the same in-sample days with new rules.** Every lane above used dates read by 14 or more families; another positive would again be a lead, not a pass.
- Do not build KNN-C K1 to K5 until a cost-gated redesign (direction 1) shows a positive top-pick mean.

## Not measured

Live fills, any October out-of-sample hour, impact at size beyond the 1 SOL checks, the P91 result, and anything inside the HV4/Q50/DBC seals. P90 had no report to cite.
