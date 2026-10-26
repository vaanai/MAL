# P32 survivor audit: what ever looked positive, what is not latency-bound, three next lanes

Desk only, 2026-10-26. Read from records on origin/main 646f61a6 (docs/library/ideas.jsonl and INDEX.md, LAB_STATE.md, HANDOFF.md, EXP/, ARTIFACTS/lab/, docs/research/). No tape, no pricing, no job, no host. No sealed output was opened (HV4, Q50, DBC forward, `/data/mal/meta-capture/`). **Every number below is copied from a record and is exploration or in-sample unless it says forward. No book has cleared the promotion gate. Nothing here says a book makes money.**

Limits: the library rows are a conversion with a known error rate (INDEX.md "Read this first"). P27, P28, P29 and P30 results are quoted from the task brief; their commits are on other branches, not in the tree I read. SOL/USD is assumed $150 (the figure STACK.md uses, not a quote).

## 1. Every line that ever showed positive evidence

"Positive" means a positive mean on data that was out-of-fold, held out, or forward, even if small. Nothing is positive on a clean forward read: the forward reads that finished are NOT_DECIDABLE (EXP-011, EXP-024 Look 1, H5 arena a2) or withdrawn (EXP-022).

### 1a. Killed by latency or cost (the edge was real in the tape, not reachable or not net of fees)

| Line | Positive evidence | Why it stopped |
| --- | --- | --- |
| EXP-012 migrate-entry one-shot PASS (10-01) | 451 holdout trades, +6.973% at k1 START, no V | Operating-point artifact: +0.145% flat at k6 END with PumpSwap virtual reserve V, lag 2, haircut. RETIRED. |
| Live probe | Price +0.496%/trip over 55 trips | Fees 4.486%, realized -3.990%. Priority fee about 10x what early landers pay. |
| CAP-PICK (EXP-012 picks, seed x 1.15 guard, 300 s cap) | Aug-Sep about +1.5%/attempt; Oct +0.3 to +0.5% (judge) | Judge HOLDS_WEAKER; per-block top-day share 63-78%; P(walk-2 pass) about 5-10%. Withdrawn before counting 10-15 (missed P2 run: process). Mostly cost. |
| Fast-entry retail thesis | Hindsight tape shows large moves | At 1.3-1.6 s event-to-landing: 0 of 3,168 bonding cells positive, 36/36 KOL-copier cells negative, retail arrives late (0.8% of buy SOL by 10 s). |
| P22 resting-bid wick, latency 0 | Upper bound on 28 days | The latency-0 bound itself is negative (-0.021 SOL/trade): a big sell is followed by more decline. Dead even with a perfect fill. |
| P29 keeper-absorb (brief) | 227 SL60 clusters/day on fresh-0903 | Priced -3.3%/trade, 0/11 OOS days; dumps keep falling inside the ~4.4 s effective entry. |
| P30 lane-G grid, P28 CALL-SYNC/SIZE-SKEW, P27 bonding (brief) | Cells screened | All about -3%; killed. Latency plus about 2.4% round trip. |
| LaunchLab | Curve reproduced to the atom | 12 cells FAIL at 1.9 s / 160k; fee 1.25% per side; only 7% of graduations are WSOL-quoted. |
| H5 cadence exit C10 (M1) | Robust survivor, discovery +0.11 pp | Fails the pressure model; exists only under the BOOST-shift assumption. Parked. |
| Dump-absorb D8/D15, deep-pool MR/BO, second-wave, hunt23 slow/flow/c | Isolated positive blocks | Median -8%, 0-9 of 35 days positive. Hunt23 speed audit: NO-GO on a paid speed track. |

### 1b. Killed by sample, regime or process (the edge was not disproved, or was unreadable)

| Line | Number and data | Why it stopped |
| --- | --- | --- |
| H5 BOOST-floor v1 (K1; Q50 sibling) | Exploration +12.9 to +13.2%/trade (1,124 Sep 3-25 trades); STACK latest regime (09-18 on) flat/pressure about +4.2%/+3.8% at 0.25 SOL | Decayed: Sept 53/day, Oct 3-16/day. EXP-024 NOT_DECIDABLE (A3 pins_changed, 10-13); arena a2 NOT_DECIDABLE (A3 synthetic_share_high 8/19, ended 10-22). Q50 is a discarded cell with no edge figure of its own. |
| FLOOR-OPTION v2 (L1) / G4-FLOORDIP (C1) | 4 blocks, 332 trades, 13.3/day, 18 of 19 days, flat +0.01605 / pressure +0.01285 SOL/trade, CI90 lo +0.011 | Neighbour block fresh-0903: n 62, flat +0.0073 / pressure +0.0051, CI90 lo -0.0032 / -0.0047, ex-top-3 +0.009 / -0.065, 4 of 5 days, 25% block-edge exclusion. Stacked stress fails. CLOSED 10-25 on rate: A2 6.0/day < 12.5. |
| DBC m10 | 369 attempts, 3 Swap2 days, flat +0.0218, pressure +0.0169, CI90 lo +0.0117, best of 12 (23 later screened cells, m10 the only positive). Rehearsal 589 attempts flat +0.0136; Swap3 day about one-fifth | One look: confirmation window [10-25, 11-01) is running; 10-17..10-23 spent (tool defect, NOT_DECIDABLE). |
| DBC strand continuation | m4/m6 flat +0.0224/+0.0233, CI90 lo +0.0143 | 3 Swap2 days, best of 26, never priced on a Swap3 pool. Dies with m10 if m10 fails (last attempt). |
| C1-NF (EXP-025) | SURVIVES-VERIFY on exploration | Shelved 10-12: October about 1 trade/day (0.9% kept vs 10.4% in Sept). Regime. |
| Single-wallet copy (B2) | 79% of pressure SOL from one wallet | Does not generalize; too few trades. |
| LAYA r4-a | +0.145%/trade after haircut, 24/29 days | P(trade CI90 lo > 0) = 0.078. Shelved. |
| RULE B / l3 xs10 | +1.8 to +2.5% on fresh | xs10 not reproducible by recipe (null tx_index ordering). |
| Haiku veto v3 | "PASS on paper" | 0 OOS trades; learning is placebo; gain is a dead-pool filter. Not cleared. |
| H5 on custom-pair pools | 124 trades / 36 days (3.4/day); 47 tradeable (1.31/day) | Latest block negative; rate far below any gate. |
| Kalshi weather maker pool (M1) | +0.67c/contract, CI90 lo +0.50c, 40/46 days (descriptive) | Not tradeable: join-best-bid M2 makes -5.08c (adverse selection); LAX concentration. Paused by owner until about 11-02; not pump. |

Pattern: nothing positive survives the move from "tape" to "reachable fill, net of fees, on a day it has not seen". Every positive lives in a narrow structure (a scheduled buyer, a bounded mark, a venue with few competitors), and each has a trade rate of 1-15/day.

## 2. Edges that are not latency-bound

Latency-bound means the signal is an event reaction (the price has moved within seconds of the trigger). These do not depend on being first.

| Candidate | Status and evidence | Honest read |
| --- | --- | --- |
| Multi-hour / day hold on a bounded mark (FLOOR) | 24 h hold, real quote <= 0.5 SOL, entry after a long drawdown; the 1.9 s leg does not matter. In-sample 18/19 days; fresh +0.0073 flat. | Smallest, most credible. Rate 6/day and a CI that crosses zero on the neighbour block. Capital tied about 6 SOL at 0.5 SOL stake, about 3 SOL at 0.25 (STACK.md). |
| BOOST schedule (Q50/H5) | The scheduled buyer is deterministic; the edge is gone for entry k >= 12 slots under V (about 9 slots at 1.9 s). | Running. Protocol-timed, but the Sept-to-Oct decay shows regime risk. |
| DBC fee-expiry, start-price floor, segment convexity | Fee clock and floor (P21) both FAIL. Convexity untested (guess 0.1 SOL/day, directional). | Two of three dead. Convexity is not distinct from the dead DBC D2-D6 family unless configs show thin segments. |
| Venue: DBC (m10) | The only line with raw breadth: 170-200 attempts/day on 10-14..16. | Confirmation running; expectations already lowered (Swap3 about one-fifth). |
| Venue: LaunchLab, Orca, Heaven/Boop/Moonit | LaunchLab FAIL; others census only, no recorder. | Dead or no data. |
| Fee tier (A27: PumpSwap fee falls with market cap) | Untested. Deep pools already have gross -5 to -18% (deep-pools-1..6). | A cost cut cannot rescue a negative gross. Low value. |
| Creator rewards / cashback / volume incentives | p20-incentive-farming untested; placeholder 0.07 SOL/day; terms never read; possible anti-wash/policy issue. | Unknown. Desk list of official terms costs 0 credits. Owner policy check first. |
| Size / capacity | STACK.md headline (impact-adjusted, in-sample best-of-N): FLOOR 0.25 SOL/day at 0.5 SOL stake (13/day); H5 about 0.18 SOL/day (13/day). Fixed send cost shrinks with stake; H5 impact is 5.9% at 1 SOL. | A few tenths of a SOL per day at most, and only if edge per trade survives. |
| Filters on existing books | Left-tail rug veto DEAD; funding-graph rug veto prep-ready; Haiku veto placebo; N3 exit-on-BOOST-end untested (detector disagrees with the frozen BOOST wallet on 37% of discovery trades). | The one reliable filter is dead-pool removal. Gains small; any rule change is a new book. |
| Stacking weak survivors (A37 / L4.6) | Untested. Needs frozen scores; the survivors are rule triggers, not scores. H5 and Q50 share pools: a correlated copy. | Adds nothing until each part is positive alone. A risk tool (A38), not an edge. Not a lane; run as a read-time report. |
| Event-reaction (everything else) | Dead under latency plus about 2.4% round trip. | Do not repeat. |

## 3. Three next lanes

Rules for all three: one pre-registered pricing, counts first, no cell added after a count. Forward books are arena paper books; post-10-25 archive rows are in-clock, so a rule priced on them is in-sample for every book. Ledger (re-read docs/HOLDOUT_LEDGER.md before any use; as of the 10-08 audit): exploration pools are explore-0814, fresh-0903 (since the 10-06 ledger move), exp011-0909, fast-pool-0918, oracle-insample-0922. Reserved or sealed: fresh-0802/0808/0828, forward walk 2 [10-16T01, 11-16T01), the DBC window [10-25T00Z, 11-01T00:10Z) and fwdv1/, DBC [10-17, 10-24) for m10 and all D1 variants, the EXP-024 Look-2 seal to 11-14, and meta-capture.

### Lane 1: FLOOR-SLOW (re-register the frozen FLOOR v2 with a window sized to its real rate)

- **Idea.** FLOOR closed because the clock needed N inside a short window and the A2 rate was 6.0/day against a 12.5 bar. The rule was not refuted. Register the same frozen rule (`l1_rule_tpfix`, 160k per send, 0.25 SOL, 1.9 s leg, rent charged, 24 h hold) with a window of about 28 days and the gate's own counts (100 trades, 5 days, majority positive, CI lo, ex-top-3).
- **Decisive test.** Counts first: arena-tape triggers per day for the frozen rule over the last 7 closed days (counts and creates only, as QP-RULING-1025 allows; no price). Under 4/day: kill the lane. Then one pricing: the forward read at the first 00:00Z boundary with at least 100 counted trades on at least 5 dates, as Q50's Am.11. No early look, no extension, no variant.
- **Data allowed.** Rule frozen on its four spent exploration blocks; fresh-0903 is the neighbour check already done. Forward arena tape from a clock at least merge + 75 h (HANDOFF: revival needs f1-f5, a new amendment). No walk-2 hour used for design.
- **Expected trades/day.** About 6; 100 trades in about 17 days, read at about day 17-20. The 25% block-edge exclusion on fresh is a historical-window artifact and should not apply forward; confirm in the counts.
- **Capacity line.** 6/day x 0.25 SOL x edge. Fresh was 40-46% of the four-block mean, about +0.5% per trade on pressure: 6 x 0.25 x 0.005 = about 0.008 SOL/day at 0.25, about 0.02 at 0.5 SOL (impact 1.4% at 0.25 SOL, growing). Roughly $1-3/day. Capital tied about 1.5 SOL average open at 0.25.
- **Kill rules.** (a) Trigger rate under 4/day. (b) Any A3 or gap halt, as for Q50. (c) At the read: any gate item fails under either fail model, or CI lo <= 0; NOT_DECIDABLE is final. (d) Futility fixed in advance: at day 10 with at least 50 trades and pressure mean <= -0.01 SOL/trade.
- **Why not a repeat.** Not an event reaction and not a race: the mark is bounded by a real quote at or below 0.5 SOL and exits after 24 h. It is a survivor with positive evidence on four blocks and the same sign on a fifth. It repeats FLOOR only in re-running one frozen rule, on clean forward hours, which has never been done.
- **Honest risk.** The fresh-0903 CI lower bound is already negative and ex-top-3 pressure is -0.065 SOL. My estimate: 15-25% PASS; NOT_DECIDABLE or FAIL otherwise.

### Lane 2: DBC-P18 staging and strand measurement, conditional on the m10 read

- **Idea.** m10 is the only line with real breadth. Its read is a confirmation only (a PASS licenses a forward paper book, P18, and nothing more). Stage the book now so a PASS becomes a clock in days, and measure the one assumption that decides the DBC family: strand exits.
- **Decisive test.** Counts first, decode only, no price: (1) detection-to-landing and first-DAMM-print sell latency on the 10-14..10-16 exploration blocks (INDEX `dbc-strand-damm-continue` next step); (2) the share of Swap3-era pools open at curve completion. One pricing: the registered forward m10 read (one cell). On PASS, register the paper book with its own day-1 dry score and the strand rule fixed by (1). On FAIL or NOT_DECIDABLE the lane and every D1 variant end; create no sibling.
- **Data allowed.** DBC [10-14, 10-17) exploration. [10-17, 10-24) is spent for m10 and D1 variants (decode-only counts allowed per read-hardening item 6; no screen, engine or price). The forward window is sealed until 11-01T00:10Z.
- **Expected trades/day.** Raw 170-200 attempts/day at Swap2; 223 attempts on 10-16 (Swap3) at about one-fifth of the mean.
- **Capacity line.** Stake per attempt is not stated in the rows I read, so I do not infer a SOL figure. In-sample arithmetic of 200 attempts/day at +0.004 per attempt would be 0.8 SOL/day if the stake is 1 SOL-scale; that is best of 12 (later 26), three days, one regime. Plan on 0 to 0.3 SOL/day with 0 as the base case.
- **Kill rules.** m10 read FAIL or NOT_DECIDABLE kills the lane. A P18 day-1 dry score below a pre-written bar kills the book before the clock. A strand rule that relies on the >11% jump above the decision spot is rejected.
- **Why not a repeat.** A different venue and tape, not an event reaction on pump.fun/PumpSwap, and the one venue where the early-seat competition on strands was never measured. It is not independent of the m10 decision, which is why it ranks second.
- **Timing.** Earliest registration 11-01, about 6 days away; the read comes about a week after.

### Lane 3: COST-OFFSET desk audit, then a cost overlay on the survivors

- **Idea.** Every survivor sits within 1-2 pp of zero after about 2.4% round trip. The lab priced sends, protocol fees and impact but never enumerated offsets: creator-fee share, cashback or volume rewards, PumpSwap fee tiers, priority-fee amortization. A 0.5-1.0 pp cut is as large as most survivors' edge.
- **Decisive test.** Desk first (0 credits): official terms of every program the books touch (pump.fun, PumpSwap, DBC/DAMM v2) for fee tiers, creator-fee rules, trader rebates and cashback. Counts second: the share of FLOOR/H5/DBC triggers on pools with a lower tier or a rebate (arena creates and counts only; public GeckoTerminal for tier). One pricing: apply the actual per-pool fee to the frozen FLOOR rule on fresh-0903 (already screened once; label it in-sample) against the flat 160k assumption. No new rule.
- **Data allowed.** Public docs, `getRecentPrioritizationFees` (p25-alpenglow-send-cost, 24 h forward, 0 Helius), arena creates/counts, fresh-0903. No hour >= 10-25 priced for a rule; no sealed block. Owner policy check before any incentive-seeking test (anti-wash terms).
- **Expected trades/day.** None new; it changes the cost of lanes 1 and 2 and the running Q50.
- **Capacity line.** 0.5 pp x 6/day x 0.25 SOL = 0.0075 SOL/day on FLOOR alone. Small; its value is as a bridge across zero.
- **Kill rules.** No program offers a rebate or lower tier reachable by a non-custodial paper key, or the net offset is under 0.3 pp after fixed costs: close the same day. If the offset needs wash-like volume: stop and ask the owner.
- **Why not a repeat.** It tests cost, not a signal; A27 and incentive-farming are untested rows.
- **No book.** It registers no forward book; it changes inputs to lanes 1 and 2.

### Considered and not ranked

- A37/L4.6 stacking: nothing positive enough to stack; H5 and Q50 are correlated copies. Run as a pairwise daily-P&L report after the 10-30 and 11-01 reads.
- H5 on custom pairs or off-band WSOL: 1.3-3.4/day, below the gate for months.
- Haiku arms (HV4, HV6, P28-H): LLM arms abstained day 1; learning measured as placebo.
- N3 exit-on-BOOST-end: edits Q50 (a new book on the same pools); 37% detector disagreement.
- Kalshi weather maker: owner-paused until about 11-02; not pump.
- p20 atomic cross-pool arbitrage, launch allocation, listing announcement, xStocks: guesses of 0.02-0.06 SOL/day each, under a trade a day or an owner conflict.

## 4. Honest assessment

The evidence says the remaining reachable edge is small. The fast-entry thesis is refuted at MAL's latency, and every event-reaction family has died on cost and latency (about 2.4% round trip against a mean that is at best +1-4% in sample and shrank by 55-60% on a neighbouring block). The survivors are three narrow, slow structures (FLOOR, the BOOST schedule, DBC m10) with 1-15 trades/day each (DBC aside), none confirmed on clean forward hours.

Realistic SOL/day if all three hold, taking the lab's in-sample capacity figures and cutting them by what fresh data showed (about 40-50%): FLOOR about 0.02-0.05, Q50 about 0.04-0.08, DBC 0-0.3 with a base case of 0. Sum about 0.06-0.15 SOL/day, or $9-22/day at $150; probability-weighted, closer to $2-8/day over the next month. The $20-50/day target (0.13-0.33 SOL/day) needs DBC m10 to confirm at a meaningful stake and Q50 to pass. I put that at 10-15%, not before mid-November, with the gated steps (reads at 10-30 and 11-01, then a DEC-032 canary) coming first. Capital binds too: the owner has about 1.29 SOL, while FLOOR alone ties 1.5-6 SOL.
