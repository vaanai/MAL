# P27 DECLARE: six fast-read ideas, COUNTS ONLY. Written before any count.

Declared at (date -u): 2026-10-25T18:30Z. Lane P27, branch claude/p27-fast-screens. Exploration only. No pricing, no outcome, no P&L, no return. 0 Helius credits. No profit claim; no book has cleared the promotion gate.

## How the six were picked
Inputs: docs/library/ideas.jsonl (all rows with status untested, parked, shelved, survivor), docs/research/p25-change-ideas/IDEAS.md, P26 results (keeper-absorb and redeploy-holiday are already counted and PARKED, so not repeated), hunt2 bc-capacity and library-capacity reports, hunt29 P9 shortlist.
Screening of the 45 untested rows (reason each fell out):
- Needs a trained model I cannot count without (E19, E3 crash-bounce, web-llm-traders-1/5/6/7/8/9/10, haiku-v3-3): no rule to count.
- Owned or sealed: r4b-depth-2, indep-mech-5 (EXP-027 / r4-b), l3-boost-h5q50-1 and l3-boost-offband-1 (Q50 / H5 sealed, off-band is 0-2 pools/day per P9), all DBC rows (m10 forward window sealed, [10-17,10-24) spent for D1; dbc-completion-hold has no post-migration tape), LaunchLab rows (holdout).
- Not trades or not a book: scheduled-flows-*, w2-pump-idl-*, speed-route-*, web-landing-*, census-*, p20-incentive-farming, p20-launch-allocation, web-capacity-5/6.
- Frequency known too low or October-only data: p25 quote-drift / t22 clock / prestock / bridge / app-fee tag (need October tape or tx reads that are sealed or unavailable), web-competitors-6, p20-xstock-offhours.
- Wallet copy (hunt23-c-1 failed all 16 sub-tests; c-8 has no stricter tier) and exit variants (no new entry): skipped.
Six that have a countable entry rule on permitted exploration tape and can reach 100 trades in about a week if the count is high enough:

| # | Library id | Short name |
|---|---|---|
| 1 | mayhem-flow-2 | MAYHEM-COLLAPSE: enter after a Mayhem mint halves between +1 min and +5 min |
| 2 | w2-mayhem-grads-1 | MAYHEM-GRAD6: Mayhem graduates, first PumpSwap print at or after grad + 6 min |
| 3 | web-oct-mechanics-1 | BC-MID: bonding curve crosses 50% progress at age >= 60 s |
| 4 | bc-capacity-1 | BC-LATE: bonding curve crosses 75% progress at age >= 60 s (deepest curve, lowest impact) |
| 5 | dev-events-7 | SERIAL-CREATOR: new mint by a creator with >= 2 graduated mints in the prior 48 h, first slow curve print |
| 6 | web-oct-mechanics-1 + bc-capacity-1 (slow-completer universe) | BC-SLOW-GRAD6: non-Mayhem graduates, first PumpSwap print at or after grad + 6 min (the old migrate family under its own count, as a universe base rate for 2 and 5) |

Idea 6 is a base-rate control and is listed only because it is the denominator the dead migrate-direct family sat on; it carries the DEAD-FAMILY label (EXP-012 / migrate-direct closed) and has no revive trigger from a mechanic change. It is counted so ideas 2 and 5 can be compared with it. Expect PARK unless a recent mechanic gives a trigger (none found).

## Window and ledger check (what hours, why permitted)
- Hours: fresh-0903 `[2026-09-03T12, 2026-09-09T12)`, clean copy `/data/mal/blocks-clean/fresh-0903/w{1,2,3}/{creates,trades,migrations}`. docs/HOLDOUT_LEDGER.md row "Fresh confirmation block": owner exploration pool, spent by EXP-012's one read (job #32) and moved to the exploration pool; never a confirmation holdout again. Allowed for exploration counts.
- Counted days (decisions per day reported): full UTC days 2026-09-05, 09-06, 09-07, 09-08 (4 days). 2026-09-03T12 to 09-05T00 is warm-up for creator history and open mints only. 09-04 reported as a fifth day where noted.
- Not touched: any October hour, arena tape (/data/mal/arena/tape), forward-1002, fresh-0828 / fresh-0808 / fresh-0802 (EXP-028 reserved / held-back), explore-0814 (not needed), DBC, LaunchLab, HV4 / Q50 / DBC-forward / EXP-024 Look-2 outputs, shadows, ledgers, bots.json. No join to any seal domain.
- Active seals (copied from /data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md): HV4 and Q50 outputs never opened; EXP-024 Look-2 seal covers October hours only; DBC forward and [10-17,10-24) not touched; arena tape counts only under QP rulings (none made); ledger D2/D3 apply to October pools (none touched).

## Conventions verified in the code
- PumpSwap rows carry PRE-trade reserves, plus V (about 17.58 SOL, 17.5e9 to 17.7e9 lamports) added on the quote side for BOOSTed pools (tools/test_cap_pick_score.py:229-230, tools/test_c1nf_features.py:39). Capacity lines use quote_reserve alone (lower bound on depth) and quote_reserve + 17.58 SOL (BOOST pools only; Mayhem V is unconfirmed, hunt-shared step2b).
- Bonding rows carry POST-trade virtual reserves (hunt2 bc-capacity REPORT 2.2, k = 3.219e25, progress = (vSOL - 30) / 85.005359). Mayhem bonding reserves do not follow the curve (3.0% k-consistent), so idea 1 uses price_sol only, never the curve progress.
- Bonding fee 1.25% per side assumed (no fee column), PumpSwap cost floor about 2.4% round trip.
- Capacity = decisions/day x stake x net edge. Net edge is NOT measured here (no pricing); so the capacity line states only decisions/day and round-trip impact at 0.25 / 0.5 / 1 SOL from the state reserve: impact(S) = S/(Q+S) + S/(Q+2S)-style small form, with Q = reserve at the decision row (same formula as hunt2 bc-capacity).

## Rules (decision = the row that would trigger an order; no look past it)
1. MAYHEM-COLLAPSE. Universe: bonding mints with create is_mayhem_mode = true. p1 = price_sol of the last trade with block_time <= create_time + 60 s (needs a trade >= 15 s after create); p5 = price_sol of the first trade with block_time >= create_time + 300 s and < create + 360 s. Decision at that p5 row if p5 < 0.5 x p1 and the row is a bonding trade. One per mint. Uses prices before the decision only.
2. MAYHEM-GRAD6. Universe: mints created in the window with is_mayhem_mode = true that have a complete event. Decision = first PumpSwap trade of the mint with block_time >= complete_time + 360 s and < complete_time + 24 h. One per mint. (Mints created before 09-03T12 are not seen; the count is a lower bound.)
3. BC-MID. Non-Mayhem bonding mint; decision = first bonding trade with post-trade progress p >= 0.50 and p < 0.75 where trade age (block_time - create_time) >= 60 s, one per mint. Entry state also reports the share with age >= 300 s.
4. BC-LATE. Same but p >= 0.75 and p < 0.90 (leaves at least 10 points for the exit), age >= 60 s, one per mint.
5. SERIAL-CREATOR. A create whose creator had >= 2 mints with a complete event in the prior 48 h of the window (warm-up 09-03T12 to 09-05T00; counted days start 09-05). Decision = first bonding trade of that mint with age >= 30 s and non-Mayhem progress < 0.25. One per mint.
6. BC-SLOW-GRAD6. Like 2 for non-Mayhem mints (base rate / control). Also report the subset whose PumpSwap quote_reserve at the decision row is in the V band, i.e. 17.5e9 to 17.7e9 lamports is NOT checkable from the reserve alone, so only report quote_reserve percentiles.

## Counts to make
Per UTC day: decisions, distinct mints, share of decisions by hour (to check top-hour concentration), median / p10 reserve at the decision row (SOL), impact at 0.25 / 0.5 / 1 SOL (median), share with impact(0.25) <= 2%. Not made: any price after the decision, any exit, any return, any P&L, any hit rate.

## Kill rules (fixed now)
1. Median counted day with fewer than 10 would-be decisions = PARK.
2. Any counted full day with fewer than 5 = PARK.
3. Median impact(0.25 SOL) round-trip above 5% at the decision row = PARK on capacity (cannot pay 2.4%+ costs).
4. Decisions concentrated: top hour carries more than 25% of a day's decisions on 2 or more days = flag, not kill.
5. A count that passes is NOT evidence of edge. ADVANCE means "worth one pre-registered pricing step", nothing more. Pricing is a separate, pre-registered step; this PR does none.
6. Idea 6 carries the DEAD-FAMILY label and cannot ADVANCE (migrate-direct closed; no mechanic revive trigger).
