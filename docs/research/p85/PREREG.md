# P85 pre-registration: survivor confirmation, one read (FLOOR-OPTION v2 on fresh-0808)

Written 2026-10-27T14:23Z (`date -u`), before any count or price of this rule on fresh-0808. Paper only. Not a registered forward read: nothing here is gate evidence, no book has cleared the promotion gate, and no sentence says FLOOR makes money. A positive result is a CONFIRMED LEAD for the manager and quant-proof, never a pass, and changes no running book.

## Step 1: pick (from docs/library/ideas.jsonl, status `explored-survivor-unconfirmed`, 25 rows)

Dropped, with the reason (family or data):
- H5 / Q50 / BOOST family, already in forward test or sealed: cap-impact-1, boost-universe-1, boost-universe-2, boost-custom-v2-1, haiku-v3-1 (r4-b overlay, outcome memory; QP NOT CLEARED).
- r4-b family (EXP-027 forward book): lib-b-D5, lib-c-O2, cap-impact-2, second-book-5, r4b-depth-1 (xs10 also leans on mint 16894 and is "not reproduced").
- Confirmation needs October, DBC, forward or arena data: dbc-early-momentum, dbc-strand-damm-continue, lib-a-A38 (needs the Q50 book).
- Needs owner action or an unavailable input: speed-route-2 (landing test at lean fee), lib-b-B2 (single wallet carries 79%, frozen wallet list needs discovery days), lib-b-C2 (idea only, no rule).
- Overlays with no entry rule of their own: lib-a-A40 (300 s cap), lib-a-A41 (seed x 1.15 guard).
- Conditional version dead or model is not a frozen rule: lib-a-A13, lib-b-E3, cap-impact-3 (crash-bounce walk-forward: a refit model, 73% of the SOL from one block, next_step is a depth proxy not a confirmation block).

Shortlist (top 3 by plausible net edge x capacity where a free unread block exists):
1. **reprice-160k-l1 = lib-c-L1 (FLOOR-OPTION v2, kit form `l1`, 160,000 lamports per send)**. Exploration result at 160k: flat +0.01605 / pressure +0.01285 SOL per trade, n 332, 13.3 trades/day at 0.25 SOL, ex-top-3 +4.405 / +3.490 SOL, 18/19 days positive, CI90 lower bound +0.01118 / +0.00861 (four Aug/Sep blocks, in-sample, best of declared cells). Capacity about 0.2 SOL/day flat before impact (1.37% round trip at 0.25 SOL, 5.48% at 1 SOL). Blocks already read: explore-0814, fast-pool-2026-09-18T23_2026-09-22T00, oracle-insample-2026-09-22_25, oracle-live-2026-09-25_27 (its four), plus fresh-0903 (P7: flat +0.00733 / pressure +0.00506, ex-top-3 +0.009 / -0.065, CI90 lo negative). Free block: fresh-0808.
2. lib-b-C1 / lib-a-A6 (G4-FLOORDIP v1, same family and same free block). Source says the four blocks are spent, power 0.002, capped book fails the gate; the capped successor is FLOOR v2 above. Same rule family, so not a second independent pick.
3. lib-b-E3 / cap-impact-3 (crash-bounce walk-forward): +2.131% flat / +1.121% worst-in-slot on 1,620 trades, but the walk-forward refit has no frozen rule text to run unchanged, and the Aug blocks' day-blocked retrain is a design step, not a confirmation.

**Choice: row `reprice-160k-l1` (rule of `lib-c-L1`).** Its `next_step` names no confirmation block, so the block comes from the ledger. The library row says FLOOR keeps a registered forward route (records #810/#811); this read does not touch that route.

## Block and ledger lines relied on (docs/HOLDOUT_LEDGER.md at origin/main 1ad5fce5)

- Table row "Second backup confirmation block" `[2026-08-08T12, 2026-08-14T12)` (6 days, 144h): Owner "reserved: next confirmation test (no owner; EXP-015 failed its screen 2026-10-06 and released it unread)". Status: "Sealed and verified 2026-10-04, never read: ... `hours_flagged` 0, 144 `VIEW.sha256` lines each". Changelog 2026-10-06: "EXP-015 failed its screen ... `[2026-08-08T12, 2026-08-14T12)` goes back to reserved, no owner, unread."
- Not usable and not used: "Backup confirmation block" fresh-0828 (EXP-028 Part 1 is its only eligible claimant); "Third backup confirmation block" fresh-0802 (kept as the one unspent block under DEC-014 block-budget rule 3); the exploration pool blocks FLOOR already read; every October, DBC, LaunchLab, arena, meta-capture, FLOOR G1, HV4/HV6/HV7/Q50 item.
- EXP-028's text names fresh-0808 as "the next block, for a later cohort" and claims no read of it. This lane's disclosed read is the first read of the block, so it spends fresh-0808 as a confirmation block for whatever uses it later (rule 3: disclosure). The manager decides whether that cost is acceptable; the lane's instruction authorises it because the ledger shows no owner and no read.
- fresh-0808 ends at 2026-08-14T12, where explore-0814 starts: a near-adjacent block to FLOOR's selection data (backcasting risk, not independence). Say so in the report.
- The ledger entry for this read is in the same PR, committed before pricing.

## Rule (frozen EXACTLY as the evidence file states it; one cell, no grid, no variant)

Rule = FLOOR v2 `l1`, kit form `l1_rule_tpfix.py:L1Rule` of hunt24 l4-audit (sha256 fba5dcc1e96f938af24af224f47c5174e71b84388d0708927aab5c6c12b623cc, imports `l1_rule.py` sha256 cefdc4c676242d79ee667e32f82ee7c303532ada14ff81fd19d5c69b747daf58), as run by P7 on fresh-0903 and by hunt28 l3 (REPRICE.md) on the four blocks. Library mechanism text: "Buys pools under a q0.5 cap at 0.1 SOL, takes profit at +50% (1.5 times the post-buy mark), exits at decision plus 1.9 s plus 24 hours, with no stop." Entry, universe, exit and cooldown are the rule files' own; they are not retyped or changed here.

The only edits to the rule files are the two P7 made, for a new block name: the block-key map gains `fresh-0808 -> aux_fresh0808.json` (otherwise the name falls to the `explore` aux table), and the tpfix module imports the copy. Diff recorded in the PR.

Kit and cost: `tools/arena_screen.py` at origin/main (fresh-0903 solo support from #848), through a wrapper that changes ONLY: `screen_cfg` send 505,000 -> 160,000 (DEC-031 Am.8; same as hunt28 and P7), `EXTRA_SOLO_BLOCK_ROOTS` -> fresh-0808, and removes the substring `fresh-08` from the kit's FORBIDDEN words (needed to open this path at all). Deciding leg 1.9 s, stake 0.25 SOL, rent 0 (Am.2), strand rule `total`, E1 floor, 360 s pool-age seal, both fail models (flat 15% and pressure, slope scale 1), kit defaults elsewhere. Priced ALONE and whole. V map: kit default (pool_v_0814, then pool_v_0909); V coverage of the block's pools is reported in the counts step. PumpSwap tape reserves are PRE-trade, V in the kit is the pool's virtual quote reserve (about 17.58 SOL); this is the kit's own pricing (the same engine as p55's), reused unchanged. Exit lag: the kit's exit is the rule's own 24 h clock; the deciding leg includes the kit's 1.9 s landing (decision plus 1.9 s) on both buy and sell legs, which is the kit's version of lag 2 slots. The brief's "exit lag 2" is not changed to something else.

## Counts step first (counts-only, no price), then one priced pass

Counts: triggers, attempts after overlap, per UTC day; excluded block-edge share; V coverage of pools. Refusal before pricing (NOT_DECIDABLE, nothing priced): a kit refusal; V coverage of the attempted pools under 95%; fewer than 3 attempts/day on average.

## Reported (all from the kit's results.json, no metric added after seeing numbers)

n, trades/day, mean SOL per trade (flat and pressure), total, total without the top 3, total without the best UTC day, UTC days positive / days with a trade (majority-positive), CI90 lower bound of the mean per trade (1,000 bootstrap draws, seed 1, 5th percentile), capacity line (trades/day x 0.25 SOL x net mean, net of the kit's impact at 0.25 and 1.0 SOL), kit flags (event-triggered, worst-in-slot, block-edge exclusions). Promotion-gate arithmetic, REPORT-ONLY (this is not a registered forward read): n >= 100; >= 5 UTC days with a majority positive; CI90 lower bound > 0; total still positive without the top 3; each under both fail models.

## Kill line

KILL if mean <= 0 under either fail model, or the total without the top 3 is <= 0 under either model (result driven by the top 3). Otherwise a positive result is reported as CONFIRMED LEAD for the manager and quant-proof (never a pass), with NOT_DECIDABLE if n < 100 or fewer than 5 UTC days. In-sample caveats stated in the report: FLOOR was selected on neighbouring blocks (fresh-0808 abuts explore-0814), winner's curse and multiplicity apply, the neighbour screen on fresh-0903 did not reproduce the breadth, and the kit PR for the solo block was unreviewed at P7's time.
